"""FastAPI server wrapping the Stepwise engine with REST + WebSocket API."""

from __future__ import annotations

import asyncio
import json
import os
import signal
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from stepwise.engine import AsyncEngine, Engine
from stepwise.config import (
    load_config, load_config_with_sources, save_config,
    save_project_config, save_project_local_config,
    StepwiseConfig, ModelEntry,
    DEFAULT_LABEL_NAMES,
    validate_label_name, label_model_id,
)
from stepwise.openrouter_models import enrich_registry
from stepwise.models import (
    Job,
    JobConfig,
    JobStatus,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore



class _LockedConnection:
    """Proxy that serializes all sqlite3.Connection method calls via a lock."""

    def __init__(self, conn, lock):
        self._conn = conn
        self._lock = lock

    def execute(self, *args, **kwargs):
        with self._lock:
            return self._conn.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        with self._lock:
            return self._conn.executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        with self._lock:
            return self._conn.executescript(*args, **kwargs)

    def commit(self, *args, **kwargs):
        with self._lock:
            return self._conn.commit(*args, **kwargs)

    def close(self, *args, **kwargs):
        with self._lock:
            return self._conn.close(*args, **kwargs)

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._conn.row_factory = value


class ThreadSafeStore(SQLiteStore):
    """SQLiteStore subclass that allows cross-thread access for the server.

    Wraps the sqlite3 connection with a threading.Lock proxy since API handlers
    run in FastAPI's threadpool while the engine's to_thread() executor calls
    also access the store concurrently.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        import sqlite3
        import threading
        lock = threading.Lock()
        raw_conn = sqlite3.connect(db_path, check_same_thread=False)
        raw_conn.row_factory = sqlite3.Row
        raw_conn.execute("PRAGMA journal_mode=WAL")
        raw_conn.execute("PRAGMA foreign_keys=ON")
        raw_conn.execute("PRAGMA busy_timeout=5000")
        self._db_path = db_path
        self._conn = _LockedConnection(raw_conn, lock)
        self._create_tables()


# ── Pydantic request/response models ─────────────────────────────────


class CreateJobRequest(BaseModel):
    objective: str
    workflow: dict | None = None
    flow_path: str | None = None
    inputs: dict | None = None
    config: dict | None = None
    workspace_path: str | None = None
    notify_url: str | None = None
    notify_context: dict | None = None
    name: str | None = None


class FulfillWatchRequest(BaseModel):
    payload: dict


class InjectContextRequest(BaseModel):
    context: str


class SaveTemplateRequest(BaseModel):
    name: str
    description: str = ""
    workflow: dict


# ── Global state ──────────────────────────────────────────────────────

_engine: AsyncEngine | None = None
_ws_clients: set[WebSocket] = set()
_engine_task: asyncio.Task | None = None
_event_loop: asyncio.AbstractEventLoop | None = None
_templates_dir: Path = Path("templates")
_project_dir: Path = Path(".")
_stream_tasks: dict[str, asyncio.Task] = {}


def _schedule_broadcast(event: dict) -> None:
    """Schedule a WebSocket broadcast from sync context (engine callback). Thread-safe."""
    if _event_loop is not None:
        _event_loop.call_soon_threadsafe(
            _event_loop.create_task,
            _broadcast({"type": "tick", "changed_jobs": [event.get("job_id", "")], "timestamp": _now().isoformat()}),
        )


def _notify_change(job_id: str) -> None:
    """Broadcast a change for endpoints that modify state outside the engine."""
    if _event_loop is not None:
        _event_loop.call_soon_threadsafe(
            _event_loop.create_task,
            _broadcast({"type": "tick", "changed_jobs": [job_id], "timestamp": _now().isoformat()}),
        )


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


def _get_engine() -> AsyncEngine:
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


async def _agent_stream_monitor() -> None:
    """Periodically check for new agent output streams to tail."""
    engine = _get_engine()
    while True:
        try:
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

            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(5.0)


async def _observe_external_jobs() -> None:
    """Poll for state changes in CLI-owned jobs and detect stale heartbeats.

    The server doesn't execute CLI-owned jobs, but needs to broadcast their
    state changes to connected WebSocket clients and detect orphaned jobs.
    """
    engine = _get_engine()
    last_seen: dict[str, str] = {}  # job_id → updated_at
    while True:
        try:
            external = engine.store.running_jobs(exclude_owner="server")
            changed_ids = []
            for job in external:
                updated = job.updated_at.isoformat() if isinstance(job.updated_at, datetime) else str(job.updated_at)
                if updated != last_seen.get(job.id):
                    changed_ids.append(job.id)
                    last_seen[job.id] = updated

            if changed_ids:
                await _broadcast({
                    "type": "tick",
                    "changed_jobs": changed_ids,
                    "timestamp": _now().isoformat(),
                })

            # Detect stale jobs and notify WebSocket clients
            stale = engine.store.stale_jobs(max_age_seconds=60)
            if stale:
                await _broadcast({
                    "type": "stale_jobs",
                    "jobs": [{"id": j.id, "objective": j.objective,
                              "last_heartbeat": j.heartbeat_at.isoformat() if j.heartbeat_at else None}
                             for j in stale],
                })

            # Clean up tracking for jobs no longer running
            active_ids = {j.id for j in external}
            for jid in list(last_seen):
                if jid not in active_ids:
                    del last_seen[jid]

            await asyncio.sleep(2)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(5)


def _cleanup_zombie_jobs(store: ThreadSafeStore) -> None:
    """Fail server-owned jobs stuck in running/pending from a previous crashed server.

    Jobs with suspended human steps are NOT zombies — they're legitimately
    waiting for input. Skip those and let the engine resume them normally.
    """
    import logging
    logger = logging.getLogger("stepwise.server")
    for job in store.active_jobs():
        if job.created_by != "server":
            continue
        # Skip jobs that have suspended steps — they're waiting on humans
        if store.suspended_runs(job.id):
            logger.info("Skipping job %s (%s): has suspended steps waiting for input", job.id, job.objective)
            continue
        # Kill orphaned agent processes and fail running step runs
        for run in store.running_runs(job.id):
            # Kill the actual OS process if we have its pgid
            if run.executor_state:
                pgid = run.executor_state.get("pgid")
                if pgid:
                    try:
                        os.killpg(pgid, signal.SIGTERM)
                        logger.info("Killed orphaned process group %d for job %s step %s", pgid, job.id, run.step_name)
                    except (ProcessLookupError, PermissionError):
                        pass  # already dead
            run.status = StepRunStatus.FAILED
            run.error = "Server restarted: step was orphaned"
            run.completed_at = _now()
            store.save_run(run)
        job.status = JobStatus.FAILED
        job.updated_at = _now()
        store.save_job(job)
        logger.info("Failed zombie job %s (%s)", job.id, job.objective)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine, _engine_task, _event_loop, _templates_dir, _project_dir

    _event_loop = asyncio.get_running_loop()
    db_path = os.environ.get("STEPWISE_DB", "stepwise.db")
    tmpl_dir = os.environ.get("STEPWISE_TEMPLATES", "templates")
    jobs_dir = os.environ.get("STEPWISE_JOBS_DIR", "jobs")
    _templates_dir = Path(tmpl_dir)
    _templates_dir.mkdir(parents=True, exist_ok=True)
    _project_dir = Path(os.environ.get("STEPWISE_PROJECT_DIR", ".")).resolve()

    store = ThreadSafeStore(db_path)

    from stepwise.registry_factory import create_default_registry
    config = load_config(_project_dir)
    registry = create_default_registry(config)

    dot_dir = _project_dir / ".stepwise"
    _engine = AsyncEngine(store, registry, jobs_dir=jobs_dir, project_dir=dot_dir if dot_dir.is_dir() else None, billing_mode=config.billing, config=config)

    # Fail zombie jobs: server-owned jobs left in running/pending from a dead process
    _cleanup_zombie_jobs(store)

    _engine.on_broadcast = _schedule_broadcast
    _engine_task = asyncio.create_task(_engine.run())
    _stream_monitor = asyncio.create_task(_agent_stream_monitor())
    _observer = asyncio.create_task(_observe_external_jobs())

    yield

    # Cancel all stream tailer tasks
    for task in _stream_tasks.values():
        task.cancel()
    _stream_tasks.clear()

    _observer.cancel()
    try:
        await _observer
    except asyncio.CancelledError:
        pass

    _stream_monitor.cancel()
    try:
        await _stream_monitor
    except asyncio.CancelledError:
        pass

    if _engine_task:
        _engine_task.cancel()
        try:
            await _engine_task
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


# ── Dev crash handler: exit on first unhandled exception ──────────────

@app.exception_handler(Exception)
async def _crash_on_error(request, exc):
    import traceback
    import logging as _logging
    traceback.print_exception(type(exc), exc, exc.__traceback__)
    _logging.critical("Unhandled exception — exiting server for debugging")
    os._exit(1)


# ── Jobs ──────────────────────────────────────────────────────────────


@app.get("/api/jobs")
def list_jobs(status: str | None = None, top_level: bool = False):
    engine = _get_engine()
    job_status = JobStatus(status) if status else None
    jobs = engine.store.all_jobs(job_status, top_level_only=top_level)
    return [_serialize_job(j) for j in jobs]


@app.post("/api/jobs")
def create_job(req: CreateJobRequest):
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    engine = _get_engine()
    try:
        if req.workflow:
            wf = WorkflowDefinition.from_dict(req.workflow)
        elif req.flow_path:
            abs_path = (_project_dir / req.flow_path).resolve()
            abs_path.relative_to(_project_dir)
            if not abs_path.is_file():
                raise HTTPException(status_code=404, detail=f"Flow not found: {req.flow_path}")
            try:
                wf = load_workflow_yaml(abs_path.read_text())
            except YAMLLoadError as e:
                raise HTTPException(status_code=400, detail=str(e))
        else:
            raise HTTPException(status_code=400, detail="Either workflow or flow_path is required")
        config = JobConfig.from_dict(req.config) if req.config else None
        job = engine.create_job(
            objective=req.objective,
            workflow=wf,
            inputs=req.inputs,
            config=config,
            workspace_path=req.workspace_path,
            name=req.name,
        )
        if req.notify_url:
            job.notify_url = req.notify_url
            job.notify_context = req.notify_context or {}
            engine.store.save_job(job)
        _notify_change(job.id)
        return _serialize_job(job)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/jobs/suspended")
def list_suspended_jobs_route(
    since: str | None = Query(default=None, description="Duration like '24h', '7d'"),
    flow: str | None = Query(default=None),
):
    """Global suspension inbox across all active jobs."""
    return list_suspended_jobs(since=since, flow=flow)


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
        _notify_change(job_id)
        return {"status": "started"}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/jobs/{job_id}/pause")
def pause_job(job_id: str):
    engine = _get_engine()
    try:
        engine.pause_job(job_id)
        _notify_change(job_id)
        return {"status": "paused"}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/jobs/{job_id}/resume")
def resume_job(job_id: str):
    engine = _get_engine()
    try:
        engine.resume_job(job_id)
        _notify_change(job_id)
        return {"status": "resumed"}
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    engine = _get_engine()
    try:
        engine.cancel_job(job_id)
        _notify_change(job_id)
        return {"status": "cancelled"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")


@app.post("/api/jobs/{job_id}/adopt")
def adopt_job(job_id: str):
    """Take over an orphaned job from a dead CLI process."""
    engine = _get_engine()
    try:
        job = engine.store.load_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    if job.created_by == "server":
        raise HTTPException(status_code=400, detail="Job already owned by server")
    if job.status not in (JobStatus.RUNNING, JobStatus.PENDING):
        raise HTTPException(status_code=400, detail=f"Cannot adopt job in {job.status.value} state")

    # Fail all orphaned RUNNING steps
    for run in engine.store.running_runs(job_id):
        run.status = StepRunStatus.FAILED
        run.error = "Orphaned: owner process died"
        run.completed_at = _now()
        engine.store.save_run(run)

    # Transfer ownership
    job.created_by = "server"
    job.runner_pid = None
    job.updated_at = _now()
    engine.store.save_job(job)

    # Engine re-evaluates — exit rules handle recovery
    engine.tick()
    _notify_change(job_id)
    return {"status": "adopted", "job_id": job_id}


@app.get("/api/jobs/stale")
def get_stale_jobs():
    """Return jobs whose CLI owner hasn't heartbeated recently."""
    engine = _get_engine()
    stale = engine.store.stale_jobs(max_age_seconds=60)
    return [j.to_dict() for j in stale]


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    engine = _get_engine()
    try:
        engine.store.load_job(job_id)  # verify it exists
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    engine.store.delete_job(job_id)
    _notify_change(job_id)
    return {"status": "deleted"}


@app.delete("/api/jobs")
def delete_all_jobs():
    engine = _get_engine()
    jobs = engine.store.all_jobs()
    for job in jobs:
        engine.store.delete_job(job.id)
    if _event_loop is not None:
        _event_loop.call_soon_threadsafe(
            _event_loop.create_task,
            _broadcast({"type": "jobs_changed"}),
        )
    return {"status": "deleted", "count": len(jobs)}


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
        _notify_change(job_id)
        return run.to_dict()
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/runs/{run_id}/fulfill")
def fulfill_watch(run_id: str, req: FulfillWatchRequest):
    engine = _get_engine()
    try:
        result = engine.fulfill_watch(run_id, req.payload)
        run = engine.store.load_run(run_id)
        _notify_change(run.job_id)
        # Idempotent: already fulfilled returns a dict
        if result is not None:
            return result
        return {"status": "fulfilled", "run_id": run_id, "job_id": run.job_id}
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

    run.status = StepRunStatus.FAILED
    run.error = "Cancelled by user"
    run.error_category = "user_cancelled"
    run.completed_at = _now()
    engine.store.save_run(run)
    _notify_change(run.job_id)
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
        _notify_change(job_id)
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


# ── Agent Ergonomics Endpoints ────────────────────────────────────────


@app.get("/api/jobs/{job_id}/status")
def get_job_status(job_id: str):
    """Resolved flow status — full DAG view with per-step costs, statuses, suspension details."""
    engine = _get_engine()
    try:
        return engine.resolved_flow_status(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")


@app.get("/api/jobs/{job_id}/cost")
def get_job_cost(job_id: str):
    """Get total accumulated cost for a job."""
    engine = _get_engine()
    try:
        engine.get_job(job_id)  # validate job exists
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    cost = engine.job_cost(job_id)
    return {"job_id": job_id, "cost_usd": round(cost, 4) if cost else 0}


@app.get("/api/jobs/{job_id}/suspended")
def get_job_suspended(job_id: str):
    """Get details of suspended steps for a job."""
    engine = _get_engine()
    try:
        engine.get_job(job_id)  # validate job exists
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    details = engine.suspended_step_details(job_id)
    return {"job_id": job_id, "suspended_steps": details}


@app.get("/api/jobs/{job_id}/output")
def get_job_output(
    job_id: str,
    step: str | None = Query(default=None, description="Comma-separated step names"),
    inputs: bool = Query(default=False),
):
    """Get job outputs, optionally per-step."""
    engine = _get_engine()
    try:
        job = engine.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    if step:
        step_names = [s.strip() for s in step.split(",")]
        result = {}
        for name in step_names:
            if name not in job.workflow.steps:
                result[name] = {"_error": f"Step '{name}' not in workflow"}
                continue
            if inputs:
                run = engine.store.latest_run(job_id, name)
                result[name] = run.inputs if run else None
            else:
                run = engine.store.latest_completed_run(job_id, name)
                if run and run.result:
                    result[name] = run.result.artifact
                else:
                    result[name] = None
        return result

    # Default: terminal outputs
    outputs = engine.terminal_outputs(job_id)
    return outputs or {}


def list_suspended_jobs(
    since: str | None = None,
    flow: str | None = None,
):
    """Global suspension inbox across all active jobs."""
    from datetime import timezone
    engine = _get_engine()
    now = datetime.now(timezone.utc)

    items = []
    for job in engine.store.active_jobs():
        suspended = engine.store.suspended_runs(job.id)
        for run in suspended:
            step_def = job.workflow.steps.get(run.step_name)
            age = (now - run.started_at).total_seconds() if run.started_at else 0

            if since:
                max_age = _parse_server_duration(since)
                if max_age and age > max_age:
                    continue

            if flow and job.objective != flow:
                continue

            item = {
                "job_id": job.id,
                "flow_name": job.objective,
                "run_id": run.id,
                "step_name": run.step_name,
                "prompt": run.watch.config.get("prompt") if run.watch else None,
                "expected_outputs": run.watch.fulfillment_outputs if run.watch else [],
                "suspended_at": run.started_at.isoformat() if run.started_at else None,
                "age_seconds": round(age, 1),
                "fulfill_command": f"stepwise fulfill {run.id} '<json>'",
            }
            if run.watch and run.watch.output_schema:
                item["output_schema"] = run.watch.output_schema
            items.append(item)

    return {"count": len(items), "suspended_steps": items}


@app.get("/api/health")
def health_check():
    """Health check endpoint for server detection."""
    engine = _get_engine()
    return {
        "status": "ok",
        "active_jobs": len(engine.store.active_jobs()),
    }


def _parse_server_duration(s: str) -> float | None:
    """Parse duration string like '24h', '7d', '30m' to seconds."""
    if not s:
        return None
    unit = s[-1].lower()
    try:
        value = float(s[:-1])
    except ValueError:
        return None
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers.get(unit, 1)


# ── Engine ────────────────────────────────────────────────────────────


@app.get("/api/status")
def engine_status():
    engine = _get_engine()
    active = engine.store.active_jobs()
    all_jobs = engine.store.all_jobs()
    return {
        "active_jobs": len(active),
        "total_jobs": len(all_jobs),
        "registered_executors": list(engine.registry._factories.keys()),
        "cwd": os.getcwd(),
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
    context_length: int | None = None
    max_output_tokens: int | None = None
    prompt_cost: float | None = None
    completion_cost: float | None = None


class UpdateModelsRequest(BaseModel):
    models: list[ModelEntryRequest]
    default_model: str | None = None


class CreateLabelRequest(BaseModel):
    name: str
    model: str


class UpdateLabelRequest(BaseModel):
    model: str


class SetApiKeyRequest(BaseModel):
    key: str  # "openrouter" or "anthropic"
    value: str
    scope: str = "user"  # "user" or "project"


@app.get("/api/config")
def get_config():
    cs = load_config_with_sources(_project_dir)
    cfg = cs.config
    enriched = enrich_registry(cfg.model_registry)
    return {
        "has_api_key": bool(cfg.openrouter_api_key),
        "has_anthropic_key": bool(cfg.anthropic_api_key),
        "api_key_source": cs.api_key_source,
        "model_registry": [m.to_dict() for m in enriched],
        "default_model": cfg.default_model,
        "labels": [li.to_dict() for li in cs.label_info],
        "billing_mode": cfg.billing,
    }


@app.get("/api/config/labels")
def get_labels():
    cs = load_config_with_sources(_project_dir)
    return {
        "labels": [li.to_dict() for li in cs.label_info],
        "default_model": cs.config.default_model,
    }


@app.post("/api/config/labels")
def create_label(req: CreateLabelRequest):
    if not validate_label_name(req.name):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid label name '{req.name}'. Must match: lowercase letters, digits, hyphens, underscores."
        )
    if req.name in DEFAULT_LABEL_NAMES:
        raise HTTPException(status_code=400, detail=f"Cannot create label '{req.name}' — it's a default label. Use PUT to reassign it.")

    import yaml as yaml_lib
    path = _project_dir / ".stepwise" / "config.yaml"
    data: dict = {}
    if path.exists():
        data = yaml_lib.safe_load(path.read_text()) or {}
    labels = data.get("labels", {})
    if req.name in labels:
        raise HTTPException(status_code=409, detail=f"Label '{req.name}' already exists at project level.")
    labels[req.name] = req.model
    save_project_config(_project_dir, labels, data.get("default_model"))
    return {"status": "created", "name": req.name, "model": req.model}


@app.put("/api/config/labels/{name}")
def update_label(name: str, req: UpdateLabelRequest):
    import yaml as yaml_lib
    path = _project_dir / ".stepwise" / "config.yaml"
    data: dict = {}
    if path.exists():
        data = yaml_lib.safe_load(path.read_text()) or {}
    labels = data.get("labels", {})
    labels[name] = req.model
    save_project_config(_project_dir, labels, data.get("default_model"))
    return {"status": "updated", "name": name, "model": req.model}


@app.delete("/api/config/labels/{name}")
def delete_label(name: str):
    if name in DEFAULT_LABEL_NAMES:
        raise HTTPException(status_code=400, detail=f"Cannot delete default label '{name}'.")
    import yaml as yaml_lib
    path = _project_dir / ".stepwise" / "config.yaml"
    data: dict = {}
    if path.exists():
        data = yaml_lib.safe_load(path.read_text()) or {}
    labels = data.get("labels", {})
    if name not in labels:
        raise HTTPException(status_code=404, detail=f"Label '{name}' not found at project level.")
    del labels[name]
    save_project_config(_project_dir, labels, data.get("default_model"))
    return {"status": "deleted", "name": name}


@app.get("/api/config/models/search")
def search_models(q: str = "", limit: int = 30):
    """Search OpenRouter model catalog (cached 24h)."""
    from stepwise.openrouter_models import search_openrouter_models
    try:
        results = search_openrouter_models(q, limit=limit)
        return {"models": [m.to_dict() for m in results]}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch OpenRouter models: {e}")


@app.get("/api/config/models")
def get_models():
    cfg = load_config(_project_dir)
    return {
        "models": [m.to_dict() for m in cfg.model_registry],
        "default_model": cfg.default_model,
    }


@app.put("/api/config/models")
def update_models(req: UpdateModelsRequest):
    cfg = load_config()  # user-level only for registry
    cfg.model_registry = [
        ModelEntry(id=m.id, name=m.name, provider=m.provider)
        for m in req.models
    ]
    if req.default_model is not None:
        cfg.default_model = req.default_model
    save_config(cfg)
    return {"status": "updated", "models": [m.to_dict() for m in cfg.model_registry]}


@app.post("/api/config/models")
def add_model(req: ModelEntryRequest):
    cfg = load_config()  # user-level
    if any(m.id == req.id for m in cfg.model_registry):
        raise HTTPException(status_code=409, detail=f"Model '{req.id}' already in registry.")
    entry = ModelEntry(
        id=req.id, name=req.name, provider=req.provider,
        context_length=req.context_length,
        max_output_tokens=req.max_output_tokens,
        prompt_cost=req.prompt_cost,
        completion_cost=req.completion_cost,
    )
    cfg.model_registry.append(entry)
    save_config(cfg)
    return {"status": "added", "model": entry.to_dict()}


@app.delete("/api/config/models/{model_id:path}")
def delete_model(model_id: str):
    cfg = load_config()  # user-level
    orig_len = len(cfg.model_registry)
    cfg.model_registry = [m for m in cfg.model_registry if m.id != model_id]
    if len(cfg.model_registry) == orig_len:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not in registry.")
    save_config(cfg)
    return {"status": "deleted", "model_id": model_id}


@app.put("/api/config/api-key")
def set_api_key(req: SetApiKeyRequest):
    if req.key not in ("openrouter", "anthropic"):
        raise HTTPException(status_code=400, detail=f"Unknown key type: {req.key}")

    if req.scope == "project":
        kwargs = {f"{req.key}_api_key": req.value}
        save_project_local_config(_project_dir, **kwargs)
    else:
        cfg = load_config()  # user-level
        if req.key == "openrouter":
            cfg.openrouter_api_key = req.value
        else:
            cfg.anthropic_api_key = req.value
        save_config(cfg)
    return {"status": "updated"}


@app.put("/api/config/default-model")
def set_default_model(req: UpdateLabelRequest):
    """Set the default model label/ID."""
    import yaml as yaml_lib
    path = _project_dir / ".stepwise" / "config.yaml"
    data: dict = {}
    if path.exists():
        data = yaml_lib.safe_load(path.read_text()) or {}
    labels = data.get("labels", {})
    save_project_config(_project_dir, labels, req.model)
    return {"status": "updated", "default_model": req.model}


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

        # Parse lightly to get step count, description, and executor types
        steps_count = 0
        description = ""
        executor_types: list[str] = []
        try:
            wf = load_workflow_yaml(flow_info.path)
            steps_count = len(wf.steps)
            description = wf.metadata.description or ""
            executor_types = sorted(
                {s.executor.type for s in wf.steps.values() if s.executor}
            )
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
            "description": description,
            "steps_count": steps_count,
            "modified_at": modified_at,
            "is_directory": flow_info.is_directory,
            "executor_types": executor_types,
        })

    return result


class CreateFlowRequest(BaseModel):
    name: str


@app.post("/api/local-flows")
def create_local_flow(req: CreateFlowRequest):
    """Create a new flow file with a minimal template."""
    import re
    from stepwise.flow_resolution import FLOW_NAME_PATTERN

    name = req.name.strip()
    if not name or not FLOW_NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid flow name: '{name}'. Must match [a-zA-Z0-9_-]+",
        )

    flows_dir = _project_dir / "flows"
    flow_dir = flows_dir / name
    flow_file = flow_dir / "FLOW.yaml"

    if flow_dir.exists() or (flows_dir / f"{name}.flow.yaml").exists():
        raise HTTPException(status_code=409, detail=f"Flow '{name}' already exists")

    flow_dir.mkdir(parents=True, exist_ok=True)

    template = (
        f"name: {name}\n"
        f'description: ""\n'
        f"\n"
        f"steps:\n"
        f"  hello:\n"
        f"    run: 'echo \"{{\\\"message\\\": \\\"hello from {name}\\\"}}\"'\n"
        f"    outputs: [message]\n"
    )
    flow_file.write_text(template)

    return {
        "path": str(flow_dir.relative_to(_project_dir)),
        "name": name,
    }


# ── Flow Directory Files ─────────────────────────────────────────────
# NOTE: These routes MUST be registered before the catch-all /api/flows/local/{path:path}
# or FastAPI's path parameter matching will swallow the /files suffix.

ALLOWED_EXTENSIONS = {".yaml", ".yml", ".py", ".sh", ".txt", ".md", ".j2", ".json", ".toml"}
MAX_FILE_SIZE = 100 * 1024  # 100KB


def _resolve_flow_dir(flow_path: str) -> Path:
    """Resolve the flow directory from a flow path. Raises HTTPException on errors."""
    full = (_project_dir / flow_path).resolve()
    # If path points to FLOW.yaml, use parent dir
    if full.name == "FLOW.yaml":
        flow_dir = full.parent
    elif full.is_dir():
        flow_dir = full
    else:
        # Single-file flow — use parent dir
        flow_dir = full.parent

    # Security: must be within project
    try:
        flow_dir.resolve().relative_to(_project_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Path escapes project directory")

    if not flow_dir.is_dir():
        raise HTTPException(status_code=404, detail="Flow directory not found")

    return flow_dir


def _resolve_file_in_flow(flow_dir: Path, file_path: str) -> Path:
    """Resolve a file path within a flow directory. Raises HTTPException on escape."""
    full = (flow_dir / file_path).resolve()

    # Security: resolve symlinks and check containment
    try:
        full.relative_to(flow_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Path escapes flow directory")

    return full


@app.get("/api/flows/local/{flow_path:path}/files")
async def list_flow_files(flow_path: str):
    """List all files in a flow directory (recursive tree)."""
    flow_dir = _resolve_flow_dir(flow_path)
    skip = {".git", "__pycache__", ".venv", "node_modules", ".stepwise"}

    files = []
    for item in sorted(flow_dir.rglob("*")):
        if any(part in skip for part in item.parts):
            continue
        if item.name.startswith("."):
            continue
        if not item.is_file():
            continue
        rel = str(item.relative_to(flow_dir))
        files.append({
            "path": rel,
            "size": item.stat().st_size,
            "is_yaml": item.suffix in {".yaml", ".yml"},
        })

    return {"flow_dir": str(flow_dir.relative_to(_project_dir)), "files": files}


@app.get("/api/flows/local/{flow_path:path}/files/{file_path:path}")
async def read_flow_file(flow_path: str, file_path: str):
    """Read a file from within a flow directory."""
    flow_dir = _resolve_flow_dir(flow_path)
    full = _resolve_file_in_flow(flow_dir, file_path)

    if not full.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    try:
        content = full.read_text(errors="replace")
    except Exception:
        raise HTTPException(status_code=500, detail="Could not read file")

    if len(content) > MAX_FILE_SIZE:
        content = content[:MAX_FILE_SIZE] + f"\n\n[truncated — {len(content)} bytes total]"

    return {"path": file_path, "content": content}


class FlowFileWriteRequest(BaseModel):
    content: str


@app.post("/api/flows/local/{flow_path:path}/files/{file_path:path}")
async def write_flow_file(flow_path: str, file_path: str, req: FlowFileWriteRequest):
    """Create or update a file within a flow directory."""
    flow_dir = _resolve_flow_dir(flow_path)
    full = _resolve_file_in_flow(flow_dir, file_path)

    # Extension whitelist
    suffix = Path(file_path).suffix.lower()
    if suffix and suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"File extension '{suffix}' not allowed. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    # Size limit
    if len(req.content.encode("utf-8")) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large (max {MAX_FILE_SIZE // 1024}KB)")

    # Create parent directories
    full.parent.mkdir(parents=True, exist_ok=True)

    existed = full.exists()
    full.write_text(req.content)

    return {
        "path": file_path,
        "created": not existed,
        "size": len(req.content.encode("utf-8")),
    }


@app.delete("/api/flows/local/{flow_path:path}/files/{file_path:path}")
async def delete_flow_file(flow_path: str, file_path: str):
    """Delete a file from a flow directory."""
    flow_dir = _resolve_flow_dir(flow_path)
    full = _resolve_file_in_flow(flow_dir, file_path)

    if not full.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")

    # Don't allow deleting FLOW.yaml
    if full.name == "FLOW.yaml":
        raise HTTPException(status_code=400, detail="Cannot delete FLOW.yaml")

    full.unlink()
    return {"status": "deleted", "path": file_path}


@app.delete("/api/flows/local/{path:path}")
async def delete_local_flow(path: str):
    """Delete an entire flow (file or directory)."""
    import shutil

    abs_path = (_project_dir / path).resolve()

    # Security: ensure the resolved path is within the project directory
    try:
        abs_path.relative_to(_project_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path escapes project directory")

    # Determine what to delete
    if abs_path.is_dir():
        # Directory flow — delete the whole folder
        shutil.rmtree(abs_path)
    elif abs_path.is_file():
        # Single .flow.yaml file
        abs_path.unlink()
    elif abs_path.name == "FLOW.yaml" and abs_path.parent.is_dir():
        # Path was dir/FLOW.yaml — delete the directory
        shutil.rmtree(abs_path.parent)
    else:
        raise HTTPException(status_code=404, detail=f"Flow not found: {path}")

    return {"status": "deleted", "path": path}


# ── Load Local Flow (catch-all — must be AFTER /files and DELETE routes) ──

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


# ── Flow Metadata Patch ────────────────────────────────────────────────


class FlowMetadataPatch(BaseModel):
    description: str | None = None
    author: str | None = None
    version: str | None = None
    tags: list[str] | None = None


@app.patch("/api/flows/local/{path:path}")
def patch_flow_metadata(path: str, req: FlowMetadataPatch):
    """Update top-level metadata fields in a flow YAML (round-trip safe)."""
    from ruamel.yaml import YAML
    from io import StringIO
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    abs_path = (_project_dir / path).resolve()
    try:
        abs_path.relative_to(_project_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path escapes project directory")
    if abs_path.is_dir():
        abs_path = abs_path / "FLOW.yaml"
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail="Flow file not found")

    ryaml = YAML()
    ryaml.preserve_quotes = True
    raw = abs_path.read_text()
    data = ryaml.load(raw)

    # Apply only the fields that were provided
    for field_name in ("description", "author", "version", "tags"):
        value = getattr(req, field_name)
        if value is not None:
            data[field_name] = value

    buf = StringIO()
    ryaml.dump(data, buf)
    updated_yaml = buf.getvalue()

    # Atomic write
    tmp_path = abs_path.with_suffix(abs_path.suffix + ".tmp")
    tmp_path.write_text(updated_yaml)
    tmp_path.rename(abs_path)

    # Re-parse and return full detail
    workflow = load_workflow_yaml(abs_path)
    graph = _build_flow_graph(updated_yaml)
    is_directory = abs_path.name == "FLOW.yaml"
    flow_dir = str(abs_path.parent)
    try:
        rel_path = str(abs_path.relative_to(_project_dir))
    except ValueError:
        rel_path = path

    return {
        "path": rel_path,
        "name": workflow.metadata.name or abs_path.stem,
        "raw_yaml": updated_yaml,
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
    agent: str = "claude"
    session_id: str | None = None
    flow_path: str | None = None


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
            project_dir=_project_dir,
            agent=req.agent,
            session_id=req.session_id,
            flow_path=req.flow_path,
        ):
            yield json.dumps(chunk) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.post("/api/editor/clear-session")
async def editor_clear_session(data: dict):
    """Clear a chat session."""
    from stepwise.editor_llm import clear_session
    sid = data.get("session_id", "")
    if sid:
        clear_session(sid)
    return {"status": "ok"}


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

from stepwise.project import get_web_dir

_web_dist = get_web_dir()
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
