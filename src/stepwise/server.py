"""FastAPI server wrapping the Stepwise engine with REST + WebSocket API."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from stepwise.engine import Engine
from stepwise.executors import (
    Executor, ExecutionContext, ExecutorResult, ExecutorStatus,
)
from stepwise.config import load_config, save_config, StepwiseConfig, ModelEntry
from stepwise.models import (
    Job,
    JobConfig,
    JobStatus,
    SubJobDefinition,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore


class DelegatingExecutor(Executor):
    """Creates a sub-job from a child workflow definition in config."""

    def __init__(self, objective: str, child_workflow: dict) -> None:
        self.objective = objective
        self.child_workflow = child_workflow

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        wf = WorkflowDefinition.from_dict(self.child_workflow)
        return ExecutorResult(
            type="sub_job",
            sub_job_def=SubJobDefinition(
                objective=self.objective,
                workflow=wf,
            ),
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        return ExecutorStatus(state="running")

    def cancel(self, state: dict) -> None:
        pass


class ThreadSafeStore(SQLiteStore):
    """SQLiteStore subclass that allows cross-thread access for the server."""

    def __init__(self, db_path: str = ":memory:") -> None:
        import sqlite3
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()


# ── Pydantic request/response models ─────────────────────────────────


class CreateJobRequest(BaseModel):
    objective: str
    workflow: dict
    inputs: dict | None = None
    config: dict | None = None
    workspace_path: str | None = None


class FulfillWatchRequest(BaseModel):
    payload: dict


class InjectContextRequest(BaseModel):
    context: str


class SaveTemplateRequest(BaseModel):
    name: str
    description: str = ""
    workflow: dict


# ── Global state ──────────────────────────────────────────────────────

_engine: Engine | None = None
_ws_clients: set[WebSocket] = set()
_tick_task: asyncio.Task | None = None
_templates_dir: Path = Path("templates")
_last_snapshot: dict[str, Any] = {}
_stream_tasks: dict[str, asyncio.Task] = {}


# ── Agent output streaming ───────────────────────────────────────────


def _parse_ndjson_events(raw: str) -> list[dict]:
    """Parse NDJSON lines into condensed streaming events."""
    events = []
    for line in raw.strip().split("\n"):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            update = data.get("params", {}).get("update", {})
            su = update.get("sessionUpdate")

            if su == "agent_message_chunk":
                content = update.get("content", {})
                if content.get("type") == "text" and content.get("text"):
                    events.append({"t": "text", "text": content["text"]})
            elif su == "tool_call":
                events.append({
                    "t": "tool_start",
                    "id": update.get("toolCallId", ""),
                    "title": update.get("title", ""),
                    "kind": update.get("kind", ""),
                })
            elif su == "tool_call_update" and update.get("status") == "completed":
                events.append({
                    "t": "tool_end",
                    "id": update.get("toolCallId", ""),
                })
            elif su == "usage_update":
                events.append({
                    "t": "usage",
                    "used": update.get("used", 0),
                    "size": update.get("size", 0),
                })
        except json.JSONDecodeError:
            continue
    return events


async def _tail_agent_output(run_id: str, output_path: str) -> None:
    """Async task that tails an NDJSON file and broadcasts events via WebSocket."""
    offset = 0
    try:
        while True:
            try:
                with open(output_path) as f:
                    f.seek(offset)
                    new_data = f.read()
                    if new_data:
                        offset = f.tell()
                        events = _parse_ndjson_events(new_data)
                        if events:
                            await _broadcast({
                                "type": "agent_output",
                                "run_id": run_id,
                                "events": events,
                            })
            except FileNotFoundError:
                pass
            await asyncio.sleep(0.1)
    except asyncio.CancelledError:
        pass


def _get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Engine not initialized")
    return _engine


def _serialize_job(job: Job) -> dict:
    return job.to_dict()


async def _broadcast(message: dict) -> None:
    """Send a message to all connected WebSocket clients."""
    dead: list[WebSocket] = []
    payload = json.dumps(message, default=str)
    for ws in _ws_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


def _snapshot() -> dict[str, Any]:
    """Take a snapshot of engine state for change detection."""
    engine = _get_engine()
    jobs = engine.store.all_jobs()
    runs = {}
    for job in jobs:
        runs[job.id] = [r.to_dict() for r in engine.store.runs_for_job(job.id)]
    return {
        "jobs": {j.id: j.to_dict() for j in jobs},
        "runs": runs,
    }


async def _tick_loop() -> None:
    """Background tick loop."""
    global _last_snapshot
    engine = _get_engine()
    while True:
        try:
            active = engine.store.active_jobs()
            interval = 2.0 if active else 10.0

            before = _snapshot()
            engine.tick()
            after = _snapshot()

            if before != after:
                # Something changed — broadcast
                changed_job_ids = set()
                for jid in set(list(after["jobs"].keys()) + list(before.get("jobs", {}).keys())):
                    if after["jobs"].get(jid) != before.get("jobs", {}).get(jid):
                        changed_job_ids.add(jid)
                    if after["runs"].get(jid) != before.get("runs", {}).get(jid):
                        changed_job_ids.add(jid)

                await _broadcast({
                    "type": "tick",
                    "changed_jobs": list(changed_job_ids),
                    "timestamp": _now().isoformat(),
                })

            # Start tailers for new running agent steps
            for job in engine.store.active_jobs():
                for run in engine.store.running_runs(job.id):
                    output_path = (run.executor_state or {}).get("output_path")
                    if output_path and run.id not in _stream_tasks:
                        task = asyncio.create_task(
                            _tail_agent_output(run.id, output_path)
                        )
                        _stream_tasks[run.id] = task

            # Cancel tailers for completed/failed runs
            active_run_ids = set()
            for job in engine.store.active_jobs():
                for run in engine.store.running_runs(job.id):
                    active_run_ids.add(run.id)
            stale = [rid for rid in _stream_tasks if rid not in active_run_ids]
            for rid in stale:
                _stream_tasks[rid].cancel()
                del _stream_tasks[rid]

            _last_snapshot = after
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Tick loop error: {e}")
            await asyncio.sleep(5.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine, _tick_task, _templates_dir

    db_path = os.environ.get("STEPWISE_DB", "stepwise.db")
    tmpl_dir = os.environ.get("STEPWISE_TEMPLATES", "templates")
    jobs_dir = os.environ.get("STEPWISE_JOBS_DIR", "jobs")
    _templates_dir = Path(tmpl_dir)
    _templates_dir.mkdir(parents=True, exist_ok=True)

    store = ThreadSafeStore(db_path)

    from stepwise.registry_factory import create_default_registry
    registry = create_default_registry()

    _engine = Engine(store, registry, jobs_dir=jobs_dir)
    _tick_task = asyncio.create_task(_tick_loop())

    # --watch mode: auto-create and start a job if env var is set
    watch_workflow_json = os.environ.pop("STEPWISE_WATCH_WORKFLOW", None)
    if watch_workflow_json:
        wf = WorkflowDefinition.from_dict(json.loads(watch_workflow_json))
        objective = os.environ.pop("STEPWISE_WATCH_OBJECTIVE", "watch")
        watch_inputs_json = os.environ.pop("STEPWISE_WATCH_INPUTS", None)
        watch_inputs = json.loads(watch_inputs_json) if watch_inputs_json else None
        job = _engine.create_job(objective=objective, workflow=wf, inputs=watch_inputs)
        _engine.start_job(job.id)

    yield

    # Cancel all stream tailer tasks
    for task in _stream_tasks.values():
        task.cancel()
    _stream_tasks.clear()

    if _tick_task:
        _tick_task.cancel()
        try:
            await _tick_task
        except asyncio.CancelledError:
            pass
    store.close()


app = FastAPI(title="Stepwise", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://stepwise.localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Jobs ──────────────────────────────────────────────────────────────


@app.get("/api/jobs")
def list_jobs(status: str | None = None, top_level: bool = False):
    engine = _get_engine()
    job_status = JobStatus(status) if status else None
    jobs = engine.store.all_jobs(job_status, top_level_only=top_level)
    return [_serialize_job(j) for j in jobs]


@app.post("/api/jobs")
def create_job(req: CreateJobRequest):
    engine = _get_engine()
    try:
        wf = WorkflowDefinition.from_dict(req.workflow)
        config = JobConfig.from_dict(req.config) if req.config else None
        job = engine.create_job(
            objective=req.objective,
            workflow=wf,
            inputs=req.inputs,
            config=config,
            workspace_path=req.workspace_path,
        )
        return _serialize_job(job)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    engine = _get_engine()
    try:
        job = engine.get_job(job_id)
        return _serialize_job(job)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")


@app.post("/api/jobs/{job_id}/start")
def start_job(job_id: str):
    engine = _get_engine()
    try:
        engine.start_job(job_id)
        return {"status": "started"}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str):
    engine = _get_engine()
    try:
        engine.pause_job(job_id)
        return {"status": "paused"}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    engine = _get_engine()
    try:
        engine.resume_job(job_id)
        return {"status": "resumed"}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    engine = _get_engine()
    try:
        engine.cancel_job(job_id)
        return {"status": "cancelled"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    engine = _get_engine()
    try:
        engine.store.load_job(job_id)  # verify it exists
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    engine.store.delete_job(job_id)
    return {"status": "deleted"}


@app.get("/api/jobs/{job_id}/tree")
def get_job_tree(job_id: str):
    engine = _get_engine()
    try:
        tree = engine.get_job_tree(job_id)

        def serialize_tree(node: dict) -> dict:
            return {
                "job": _serialize_job(node["job"]),
                "runs": [r.to_dict() for r in node["runs"]],
                "sub_jobs": [serialize_tree(s) for s in node["sub_jobs"]],
            }

        return serialize_tree(tree)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")


@app.get("/api/jobs/{job_id}/runs")
def get_runs(job_id: str, step_name: str | None = None):
    engine = _get_engine()
    try:
        runs = engine.get_runs(job_id, step_name)
        return [r.to_dict() for r in runs]
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")


@app.post("/api/jobs/{job_id}/steps/{step_name}/rerun")
def rerun_step(job_id: str, step_name: str):
    engine = _get_engine()
    try:
        run = engine.rerun_step(job_id, step_name)
        return run.to_dict()
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/runs/{run_id}/fulfill")
def fulfill_watch(run_id: str, req: FulfillWatchRequest):
    engine = _get_engine()
    try:
        engine.fulfill_watch(run_id, req.payload)
        return {"status": "fulfilled"}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/runs/{run_id}/step-events")
def get_step_events(
    run_id: str,
    since: str | None = None,
    limit: int = Query(default=100, le=1000),
):
    """Get fine-grained step events (cost, activity) for a run."""
    engine = _get_engine()
    events = engine.store.load_step_events(run_id, since=since, limit=limit)
    return events


@app.get("/api/runs/{run_id}/cost")
def get_run_cost(run_id: str):
    """Get accumulated cost for a run from step events."""
    engine = _get_engine()
    cost = engine.store.accumulated_cost(run_id)
    return {"run_id": run_id, "cost_usd": cost}


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str):
    """Cancel a running step."""
    engine = _get_engine()
    try:
        run = engine.store.load_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    if run.status.value != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Run is not running (status: {run.status.value})"
        )

    job = engine.store.load_job(run.job_id)
    step_def = job.workflow.steps.get(run.step_name)
    if step_def:
        try:
            executor = engine.registry.create(step_def.executor)
            executor.cancel(run.executor_state or {})
        except Exception:
            pass

    from stepwise.models import StepRunStatus
    run.status = StepRunStatus.FAILED
    run.error = "Cancelled by user"
    run.error_category = "user_cancelled"
    run.completed_at = _now()
    engine.store.save_run(run)
    return {"status": "cancelled", "run_id": run_id}


@app.get("/api/runs/{run_id}/agent-output")
def get_agent_output(run_id: str):
    """Get condensed agent output events for a completed run."""
    engine = _get_engine()
    try:
        run = engine.store.load_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    output_path = (run.executor_state or {}).get("output_path")
    if not output_path:
        return {"events": []}
    try:
        with open(output_path) as f:
            raw = f.read()
        return {"events": _parse_ndjson_events(raw)}
    except FileNotFoundError:
        return {"events": []}


@app.post("/api/jobs/{job_id}/context")
def inject_context(job_id: str, req: InjectContextRequest):
    engine = _get_engine()
    try:
        engine.inject_context(job_id, req.context)
        return {"status": "injected"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")


@app.get("/api/jobs/{job_id}/events")
def get_events(job_id: str, since: str | None = None):
    engine = _get_engine()
    since_dt = datetime.fromisoformat(since) if since else None
    try:
        events = engine.get_events(job_id, since_dt)
        return [e.to_dict() for e in events]
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")


# ── Engine ────────────────────────────────────────────────────────────


@app.post("/api/tick")
def manual_tick():
    engine = _get_engine()
    engine.tick()
    return {"status": "ticked"}


@app.get("/api/status")
def engine_status():
    engine = _get_engine()
    active = engine.store.active_jobs()
    all_jobs = engine.store.all_jobs()
    return {
        "active_jobs": len(active),
        "total_jobs": len(all_jobs),
        "registered_executors": list(engine.registry._factories.keys()),
    }


@app.get("/api/executors")
def list_executors():
    engine = _get_engine()
    return {"executors": list(engine.registry._factories.keys())}


# ── Templates ─────────────────────────────────────────────────────────


@app.post("/api/templates")
def save_template(req: SaveTemplateRequest):
    path = _templates_dir / f"{req.name}.json"
    data = {
        "name": req.name,
        "description": req.description,
        "workflow": req.workflow,
        "created_at": _now().isoformat(),
    }
    path.write_text(json.dumps(data, indent=2))
    return data


@app.get("/api/templates")
def list_templates():
    templates = []
    if _templates_dir.exists():
        for f in sorted(_templates_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text())
                templates.append(data)
            except Exception:
                pass
    return templates


@app.get("/api/templates/{name}")
def get_template(name: str):
    path = _templates_dir / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Template not found: {name}")
    return json.loads(path.read_text())


@app.delete("/api/templates/{name}")
def delete_template(name: str):
    path = _templates_dir / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Template not found: {name}")
    path.unlink()
    return {"status": "deleted"}


# ── Config ────────────────────────────────────────────────────────────


class ModelEntryRequest(BaseModel):
    id: str
    name: str
    provider: str
    tier: str | None = None


class UpdateModelsRequest(BaseModel):
    models: list[ModelEntryRequest]
    default_model: str | None = None


@app.get("/api/config")
def get_config():
    cfg = load_config()
    return {
        "has_api_key": bool(cfg.openrouter_api_key),
        "model_registry": [m.to_dict() for m in cfg.model_registry],
        "default_model": cfg.default_model,
    }


@app.get("/api/config/models")
def get_models():
    cfg = load_config()
    return {
        "models": [m.to_dict() for m in cfg.model_registry],
        "default_model": cfg.default_model,
    }


@app.put("/api/config/models")
def update_models(req: UpdateModelsRequest):
    cfg = load_config()
    cfg.model_registry = [
        ModelEntry(id=m.id, name=m.name, provider=m.provider, tier=m.tier)
        for m in req.models
    ]
    if req.default_model is not None:
        cfg.default_model = req.default_model
    save_config(cfg)
    return {"status": "updated", "models": [m.to_dict() for m in cfg.model_registry]}


class SetApiKeyRequest(BaseModel):
    api_key: str


@app.put("/api/config/api-key")
def set_api_key(req: SetApiKeyRequest):
    cfg = load_config()
    cfg.openrouter_api_key = req.api_key
    save_config(cfg)
    return {"status": "updated"}


# ── WebSocket ─────────────────────────────────────────────────────────


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.add(ws)
    try:
        while True:
            # Keep connection alive, handle client messages if needed
            data = await ws.receive_text()
            # Could handle ping/pong or client commands here
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(ws)


# ── Static files (production) ─────────────────────────────────────────

from stepwise.project import get_bundled_web_dir

# Prefer bundled web assets, fall back to dev-mode web/dist
_web_dist = get_bundled_web_dir()
if not _web_dist.exists():
    _web_dist = Path(__file__).parent.parent.parent / "web" / "dist"
if _web_dist.exists():
    from fastapi.responses import FileResponse

    # Serve static assets directly
    app.mount("/assets", StaticFiles(directory=str(_web_dist / "assets")), name="assets")

    # SPA fallback: any non-API route serves index.html
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Try serving a static file first
        file_path = _web_dist / full_path
        if full_path and file_path.is_file():
            return FileResponse(file_path)
        # Fallback to index.html for client-side routing
        return FileResponse(_web_dist / "index.html")


# ── Legacy CLI entry point (use stepwise.cli:cli_main instead) ────────


def main():
    import uvicorn
    port = int(os.environ.get("STEPWISE_PORT", "8340"))
    uvicorn.run(
        "stepwise.server:app",
        host="0.0.0.0",
        port=port,
        reload=os.environ.get("STEPWISE_RELOAD", "").lower() == "true",
    )


if __name__ == "__main__":
    main()
