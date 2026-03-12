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
_project_dir: Path = Path(".")
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
    global _engine, _tick_task, _templates_dir, _project_dir

    db_path = os.environ.get("STEPWISE_DB", "stepwise.db")
    tmpl_dir = os.environ.get("STEPWISE_TEMPLATES", "templates")
    jobs_dir = os.environ.get("STEPWISE_JOBS_DIR", "jobs")
    _templates_dir = Path(tmpl_dir)
    _templates_dir.mkdir(parents=True, exist_ok=True)
    _project_dir = Path(os.environ.get("STEPWISE_PROJECT_DIR", ".")).resolve()

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


# ── Editor (flow listing / loading / saving) ─────────────────────────


class ParseYAMLRequest(BaseModel):
    yaml: str


class SaveYAMLRequest(BaseModel):
    yaml: str


def _build_flow_graph(yaml_content: str) -> dict:
    """Build a graph structure from YAML for DAG visualization.

    Returns {nodes: [...], edges: [...]}.
    """
    import yaml as yaml_lib

    try:
        data = yaml_lib.safe_load(yaml_content)
    except Exception:
        return {"nodes": [], "edges": []}

    if not isinstance(data, dict):
        return {"nodes": [], "edges": []}

    steps = data.get("steps", {})
    nodes = []
    edges = []
    seen_edges: set[tuple[str, str, bool]] = set()

    for step_name, step_def in steps.items():
        if not isinstance(step_def, dict):
            continue

        # Determine executor type
        if step_def.get("for_each"):
            executor = "for_each"
        elif step_def.get("routes"):
            executor = "route"
        elif step_def.get("run"):
            executor = "script"
        elif step_def.get("executor"):
            executor = step_def["executor"]
        else:
            executor = "unknown"

        outputs = step_def.get("outputs", [])

        # Check for exit rules / loops
        exits = step_def.get("exits", [])
        loop_targets = []
        for ex in exits:
            if isinstance(ex, dict) and ex.get("action") == "loop" and ex.get("target"):
                loop_targets.append(ex["target"])

        # Build detail fields
        detail_fields: dict = {}
        for key in ("model", "system", "prompt", "temperature", "max_tokens",
                     "run", "for_each", "as", "on_error", "limits"):
            if key in step_def:
                detail_fields[key] = step_def[key]
        if step_def.get("inputs"):
            detail_fields["inputs"] = step_def["inputs"]
        if exits:
            detail_fields["exits"] = exits

        node: dict = {
            "id": step_name,
            "label": step_name.replace("-", " ").replace("_", " ").title(),
            "executor_type": executor,
            "outputs": outputs,
            "details": detail_fields,
        }
        nodes.append(node)

        # Input binding edges
        inputs = step_def.get("inputs", {})
        for _key, value in inputs.items():
            if isinstance(value, str) and "." in value and not value.startswith("$job"):
                dep_step = value.split(".")[0]
                if dep_step in steps:
                    edge_key = (dep_step, step_name, False)
                    if edge_key not in seen_edges:
                        field_name = value.split(".", 1)[1] if "." in value else None
                        edge: dict = {"source": dep_step, "target": step_name}
                        if field_name:
                            edge["label"] = field_name
                        edges.append(edge)
                        seen_edges.add(edge_key)

        # Sequencing edges
        sequencing = step_def.get("sequencing", [])
        if isinstance(sequencing, str):
            sequencing = [sequencing]
        for seq_dep in sequencing:
            if seq_dep in steps:
                edge_key = (seq_dep, step_name, False)
                if edge_key not in seen_edges:
                    edges.append({"source": seq_dep, "target": step_name})
                    seen_edges.add(edge_key)

        # For-each source edge
        fe_source = step_def.get("for_each")
        if isinstance(fe_source, str) and "." in fe_source:
            dep_step = fe_source.split(".")[0]
            if dep_step in steps:
                edge_key = (dep_step, step_name, False)
                if edge_key not in seen_edges:
                    edges.append({"source": dep_step, "target": step_name})
                    seen_edges.add(edge_key)

        # Loop back-edges
        for target in loop_targets:
            if target in steps:
                edges.append({
                    "source": step_name,
                    "target": target,
                    "is_loop": True,
                })

    return {"nodes": nodes, "edges": edges}


@app.get("/api/local-flows")
def list_local_flows():
    """List all flows discoverable in the project directory."""
    from stepwise.flow_resolution import discover_flows
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    flows = discover_flows(_project_dir)
    result = []
    for flow_info in flows:
        # Get file mtime
        stat_path = flow_info.path
        if flow_info.is_directory:
            # For directory flows, use the FLOW.yaml mtime
            stat_path = flow_info.path
        try:
            mtime = stat_path.stat().st_mtime
            modified_at = datetime.fromtimestamp(mtime).isoformat()
        except OSError:
            modified_at = ""

        # Parse lightly to get step count
        steps_count = 0
        try:
            wf = load_workflow_yaml(flow_info.path)
            steps_count = len(wf.steps)
        except (YAMLLoadError, Exception):
            pass

        # Compute relative path from project dir
        try:
            rel_path = str(flow_info.path.relative_to(_project_dir))
        except ValueError:
            rel_path = str(flow_info.path)

        result.append({
            "path": rel_path,
            "name": flow_info.name,
            "steps_count": steps_count,
            "modified_at": modified_at,
            "is_directory": flow_info.is_directory,
        })

    return result


@app.get("/api/flows/local/{path:path}")
def load_local_flow(path: str):
    """Load a specific flow by its relative path."""
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    abs_path = (_project_dir / path).resolve()

    # Security: ensure the resolved path is within the project directory
    try:
        abs_path.relative_to(_project_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path escapes project directory")

    # If path is a directory, look for FLOW.yaml inside it
    if abs_path.is_dir():
        abs_path = abs_path / "FLOW.yaml"

    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"Flow not found: {path}")

    raw_yaml = abs_path.read_text()

    try:
        workflow = load_workflow_yaml(abs_path)
    except YAMLLoadError as e:
        raise HTTPException(status_code=400, detail=f"Invalid flow YAML: {'; '.join(e.errors)}")

    graph = _build_flow_graph(raw_yaml)

    # Determine if this is a directory flow
    is_directory = abs_path.name == "FLOW.yaml"
    flow_dir = str(abs_path.parent)

    try:
        rel_path = str(abs_path.relative_to(_project_dir))
    except ValueError:
        rel_path = path

    return {
        "path": rel_path,
        "name": workflow.metadata.name or abs_path.stem,
        "raw_yaml": raw_yaml,
        "flow": workflow.to_dict(),
        "graph": graph,
        "is_directory": is_directory,
        "flow_dir": flow_dir,
    }


@app.post("/api/flows/parse")
def parse_flow_yaml(req: ParseYAMLRequest):
    """Parse a YAML string and return the flow + graph structure."""
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    if not req.yaml or not req.yaml.strip():
        return {"flow": None, "graph": None, "errors": ["Empty YAML input"]}

    try:
        workflow = load_workflow_yaml(req.yaml)
    except YAMLLoadError as e:
        return {"flow": None, "graph": None, "errors": e.errors}
    except Exception as e:
        return {"flow": None, "graph": None, "errors": [str(e)]}

    graph = _build_flow_graph(req.yaml)
    return {
        "flow": workflow.to_dict(),
        "graph": graph,
        "errors": [],
    }


@app.put("/api/flows/local/{path:path}")
def save_local_flow(path: str, req: SaveYAMLRequest):
    """Save YAML to a flow file. Validates first, creates .bak backup."""
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    abs_path = (_project_dir / path).resolve()

    # Security: ensure the resolved path is within the project directory
    try:
        abs_path.relative_to(_project_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path escapes project directory")

    # If path is a directory, target FLOW.yaml inside it
    if abs_path.is_dir():
        abs_path = abs_path / "FLOW.yaml"

    # Validate the YAML first
    try:
        workflow = load_workflow_yaml(req.yaml)
    except YAMLLoadError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid YAML: {'; '.join(e.errors)}",
        )

    # Create .bak backup if file already exists
    if abs_path.is_file():
        bak_path = abs_path.with_suffix(abs_path.suffix + ".bak")
        bak_path.write_bytes(abs_path.read_bytes())

    # Atomic write: write to .tmp then rename
    tmp_path = abs_path.with_suffix(abs_path.suffix + ".tmp")
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_text(req.yaml)
    tmp_path.rename(abs_path)

    graph = _build_flow_graph(req.yaml)

    is_directory = abs_path.name == "FLOW.yaml"
    flow_dir = str(abs_path.parent)

    try:
        rel_path = str(abs_path.relative_to(_project_dir))
    except ValueError:
        rel_path = path

    return {
        "path": rel_path,
        "name": workflow.metadata.name or abs_path.stem,
        "raw_yaml": req.yaml,
        "flow": workflow.to_dict(),
        "graph": graph,
        "is_directory": is_directory,
        "flow_dir": flow_dir,
    }


# ── Visual Editing (M12b) ──────────────────────────────────────────────


class StepPatchRequest(BaseModel):
    """Field-level patches to a step's YAML. Keys are YAML field names."""
    flow_path: str
    step_name: str
    changes: dict[str, Any]


class AddStepRequest(BaseModel):
    flow_path: str
    name: str
    executor: str = "script"


class DeleteStepRequest(BaseModel):
    flow_path: str
    step_name: str


def _resolve_flow_path(path: str) -> Path:
    """Resolve a flow path within the project dir, with security check."""
    abs_path = (_project_dir / path).resolve()
    try:
        abs_path.relative_to(_project_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path escapes project directory")
    if abs_path.is_dir():
        abs_path = abs_path / "FLOW.yaml"
    return abs_path


def _ruamel_load_and_patch(
    file_path: Path,
    step_name: str,
    changes: dict[str, Any],
) -> str:
    """Load YAML with ruamel.yaml round-trip, apply patches, return updated YAML."""
    from ruamel.yaml import YAML
    from io import StringIO

    ryaml = YAML()
    ryaml.preserve_quotes = True

    raw = file_path.read_text()
    data = ryaml.load(raw)

    if "steps" not in data or step_name not in data["steps"]:
        raise HTTPException(
            status_code=404, detail=f"Step '{step_name}' not found"
        )

    step = data["steps"][step_name]
    for key, value in changes.items():
        step[key] = value

    buf = StringIO()
    ryaml.dump(data, buf)
    return buf.getvalue()


@app.post("/api/flows/patch-step")
def patch_step(req: StepPatchRequest):
    """Apply field-level edits to a step via ruamel.yaml round-trip."""
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    abs_path = _resolve_flow_path(req.flow_path)
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"Flow not found: {req.flow_path}")

    new_yaml = _ruamel_load_and_patch(abs_path, req.step_name, req.changes)

    # Validate the result
    try:
        workflow = load_workflow_yaml(new_yaml)
    except YAMLLoadError as e:
        raise HTTPException(status_code=400, detail=f"Invalid after patch: {'; '.join(e.errors)}")

    # Write atomically
    tmp_path = abs_path.with_suffix(abs_path.suffix + ".tmp")
    tmp_path.write_text(new_yaml)
    tmp_path.rename(abs_path)

    graph = _build_flow_graph(new_yaml)
    return {
        "raw_yaml": new_yaml,
        "flow": workflow.to_dict(),
        "graph": graph,
        "errors": [],
    }


@app.post("/api/flows/add-step")
def add_step(req: AddStepRequest):
    """Add a minimal new step to the flow."""
    from ruamel.yaml import YAML
    from io import StringIO
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    abs_path = _resolve_flow_path(req.flow_path)
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"Flow not found: {req.flow_path}")

    ryaml = YAML()
    ryaml.preserve_quotes = True
    data = ryaml.load(abs_path.read_text())

    if "steps" not in data:
        raise HTTPException(status_code=400, detail="Flow has no steps mapping")

    if req.name in data["steps"]:
        raise HTTPException(status_code=409, detail=f"Step '{req.name}' already exists")

    # Create minimal step definition based on executor type
    if req.executor == "script":
        new_step = {"run": "echo hello", "outputs": ["result"]}
    elif req.executor in ("llm", "agent"):
        new_step = {"executor": req.executor, "prompt": "TODO", "outputs": ["result"]}
    elif req.executor == "human":
        new_step = {"executor": "human", "prompt": "TODO", "outputs": ["result"]}
    else:
        new_step = {"executor": req.executor, "outputs": ["result"]}

    data["steps"][req.name] = new_step

    buf = StringIO()
    ryaml.dump(data, buf)
    new_yaml = buf.getvalue()

    # Validate
    try:
        workflow = load_workflow_yaml(new_yaml)
    except YAMLLoadError as e:
        raise HTTPException(status_code=400, detail=f"Invalid after adding step: {'; '.join(e.errors)}")

    # Write atomically
    tmp_path = abs_path.with_suffix(abs_path.suffix + ".tmp")
    tmp_path.write_text(new_yaml)
    tmp_path.rename(abs_path)

    graph = _build_flow_graph(new_yaml)
    return {
        "raw_yaml": new_yaml,
        "flow": workflow.to_dict(),
        "graph": graph,
        "errors": [],
    }


@app.post("/api/flows/delete-step")
def delete_step(req: DeleteStepRequest):
    """Delete a step and clean up references from other steps."""
    from ruamel.yaml import YAML
    from io import StringIO
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    abs_path = _resolve_flow_path(req.flow_path)
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"Flow not found: {req.flow_path}")

    ryaml = YAML()
    ryaml.preserve_quotes = True
    data = ryaml.load(abs_path.read_text())

    if "steps" not in data or req.step_name not in data["steps"]:
        raise HTTPException(status_code=404, detail=f"Step '{req.step_name}' not found")

    del data["steps"][req.step_name]

    # Cascade: remove input bindings and sequencing refs to the deleted step
    for other_name, other_step in data["steps"].items():
        if isinstance(other_step, dict):
            # Clean inputs
            inputs = other_step.get("inputs")
            if isinstance(inputs, dict):
                to_remove = [
                    k for k, v in inputs.items()
                    if isinstance(v, str) and v.startswith(f"{req.step_name}.")
                ]
                for k in to_remove:
                    del inputs[k]

            # Clean sequencing
            seq = other_step.get("sequencing")
            if isinstance(seq, list):
                other_step["sequencing"] = [s for s in seq if s != req.step_name]
                if not other_step["sequencing"]:
                    del other_step["sequencing"]

    buf = StringIO()
    ryaml.dump(data, buf)
    new_yaml = buf.getvalue()

    # Validate (may still fail if structure is broken)
    try:
        workflow = load_workflow_yaml(new_yaml)
    except YAMLLoadError as e:
        raise HTTPException(status_code=400, detail=f"Invalid after delete: {'; '.join(e.errors)}")

    # Write atomically
    tmp_path = abs_path.with_suffix(abs_path.suffix + ".tmp")
    tmp_path.write_text(new_yaml)
    tmp_path.rename(abs_path)

    graph = _build_flow_graph(new_yaml)
    return {
        "raw_yaml": new_yaml,
        "flow": workflow.to_dict(),
        "graph": graph,
        "errors": [],
    }


@app.get("/api/flows/mtime")
def get_flow_mtime(flow_path: str = Query(..., alias="path")):
    """Get the file modification time for change detection."""
    abs_path = _resolve_flow_path(flow_path)
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"Flow not found: {flow_path}")
    mtime = abs_path.stat().st_mtime
    return {"mtime": mtime, "modified_at": datetime.fromtimestamp(mtime).isoformat()}


# ── Registry Proxy ────────────────────────────────────────────────────


class InstallRequest(BaseModel):
    slug: str
    target: str = "project"  # "project" only for now


@app.get("/api/registry/search")
def registry_search(
    q: str = "",
    tag: str | None = None,
    sort: str = "downloads",
    limit: int = 20,
    offset: int = 0,
):
    """Proxy search to stepwise.run registry."""
    from stepwise.registry_client import search_flows, RegistryError

    try:
        result = search_flows(query=q, tag=tag, sort=sort, limit=limit)
        return result
    except RegistryError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Registry unavailable: {e}")


@app.get("/api/registry/flow/{slug}")
def registry_flow_detail(slug: str):
    """Fetch flow detail from stepwise.run registry."""
    from stepwise.registry_client import fetch_flow, RegistryError
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    try:
        data = fetch_flow(slug)
        # Build graph and parse flow from the YAML for DAG preview
        if data.get("yaml"):
            data["graph"] = _build_flow_graph(data["yaml"])
            try:
                workflow = load_workflow_yaml(data["yaml"])
                data["flow"] = workflow.to_dict()
            except (YAMLLoadError, Exception):
                data["flow"] = None
        else:
            data["graph"] = {"nodes": [], "edges": []}
            data["flow"] = None
        return data
    except RegistryError as e:
        status = e.status_code or 502
        raise HTTPException(status_code=status, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Registry unavailable: {e}")


@app.post("/api/registry/install")
def registry_install(req: InstallRequest):
    """Install a flow from the registry into the local project."""
    from stepwise.registry_client import fetch_flow, RegistryError

    try:
        data = fetch_flow(req.slug, use_cache=False)
    except RegistryError as e:
        status = e.status_code or 502
        raise HTTPException(status_code=status, detail=str(e))

    yaml_content = data.get("yaml")
    if not yaml_content:
        raise HTTPException(status_code=502, detail="Registry returned no YAML content")

    # Install as directory flow: flows/<slug>/FLOW.yaml
    flows_dir = _project_dir / "flows"
    flow_dir = flows_dir / req.slug
    if flow_dir.exists():
        raise HTTPException(
            status_code=409,
            detail=f"Flow directory already exists: flows/{req.slug}/",
        )

    flow_dir.mkdir(parents=True)
    flow_file = flow_dir / "FLOW.yaml"
    flow_file.write_text(yaml_content)

    # Write provenance metadata
    origin = {
        "registry": "stepwise.run",
        "slug": data.get("slug", req.slug),
        "author": data.get("author"),
        "version": data.get("version"),
        "installed_at": _now().isoformat(),
    }
    (flow_dir / ".origin.json").write_text(json.dumps(origin, indent=2) + "\n")

    # Parse and return same format as local flow detail
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    try:
        flow_def = load_workflow_yaml(yaml_content)
        flow_dict = flow_def.to_dict()
        graph = _build_flow_graph(yaml_content)
        errors: list[str] = []
    except YAMLLoadError as exc:
        flow_dict = None
        graph = {"nodes": [], "edges": []}
        errors = exc.errors
    except Exception as exc:
        flow_dict = None
        graph = {"nodes": [], "edges": []}
        errors = [str(exc)]

    rel_path = f"flows/{req.slug}/FLOW.yaml"
    return {
        "path": rel_path,
        "name": data.get("name", req.slug),
        "raw_yaml": yaml_content,
        "flow": flow_dict,
        "graph": graph,
        "errors": errors,
        "is_directory": True,
        "flow_dir": f"flows/{req.slug}",
    }


# ── Editor LLM Chat ───────────────────────────────────────────────────


class EditorChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []
    current_yaml: str | None = None
    selected_step: str | None = None


@app.post("/api/editor/chat")
async def editor_chat(req: EditorChatRequest):
    """Stream LLM-assisted flow editing responses as NDJSON."""
    from starlette.responses import StreamingResponse
    from stepwise.editor_llm import chat_stream

    def generate():
        for chunk in chat_stream(
            user_message=req.message,
            history=req.history,
            current_yaml=req.current_yaml,
            selected_step=req.selected_step,
        ):
            yield json.dumps(chunk) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


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
