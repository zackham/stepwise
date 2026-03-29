"""FastAPI server wrapping the Stepwise engine with REST + WebSocket API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from contextlib import asynccontextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from stepwise.engine import AsyncEngine, Engine, _adopt_stale_cli_job, _auto_adopt_stale_cli_jobs
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
from stepwise.agent import verify_agent_pid
from stepwise.events import JOB_AWAITING_APPROVAL
from stepwise.hooks import build_event_envelope

logger = logging.getLogger("stepwise.server")


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

    def __enter__(self):
        self._lock.acquire()
        return self._conn.__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return self._conn.__exit__(exc_type, exc_val, exc_tb)
        finally:
            self._lock.release()

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
        lock = threading.RLock()
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
    metadata: dict | None = None
    job_group: str | None = None
    status: str | None = None  # "staged" to create in staged state


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


# ── Event stream client registry ─────────────────────────────────────

from dataclasses import dataclass, field


@dataclass
class _StreamClient:
    ws: WebSocket
    queue: asyncio.Queue  # Queue[dict]
    job_ids: set[str] | None = None       # None = no job_id filter
    session_id: str | None = None         # None = no session_id filter
    session_job_ids: set[str] = field(default_factory=set)  # resolved job_ids for session_id


_event_stream_clients: list[_StreamClient] = []


def _matches_stream_filter(client: _StreamClient, envelope: dict) -> bool:
    """Check if an event envelope matches a stream client's filters."""
    job_id = envelope.get("job_id", "")

    # No filters = admin mode, receives everything
    if client.job_ids is None and client.session_id is None:
        return True

    # Check explicit job_id filter
    if client.job_ids is not None and job_id in client.job_ids:
        return True

    # Check session_id filter (resolved to job_ids)
    if client.session_id is not None and job_id in client.session_job_ids:
        return True

    return False


async def _dispatch_to_event_stream(envelope: dict) -> None:
    """Dispatch an event envelope to all matching stream clients."""
    dead: list[_StreamClient] = []
    for client in _event_stream_clients:
        # Dynamic session_id discovery: when a new job starts, check if
        # it belongs to a session-filtered client
        if (
            client.session_id is not None
            and envelope.get("event") == "job.started"
        ):
            meta = envelope.get("metadata", {})
            if meta.get("sys", {}).get("session_id") == client.session_id:
                client.session_job_ids.add(envelope.get("job_id", ""))

        if _matches_stream_filter(client, envelope):
            try:
                client.queue.put_nowait(envelope)
            except asyncio.QueueFull:
                dead.append(client)
    for client in dead:
        try:
            _event_stream_clients.remove(client)
        except ValueError:
            pass
        try:
            await client.ws.close(code=1008, reason="backpressure")
        except Exception:
            pass


def _schedule_event_stream(envelope: dict) -> None:
    """Schedule event stream dispatch from sync context (engine callback). Thread-safe."""
    if _event_loop is not None:
        _event_loop.call_soon_threadsafe(
            _event_loop.create_task,
            _dispatch_to_event_stream(envelope),
        )


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


def _reload_engine_config() -> StepwiseConfig:
    """Reload merged config and refresh the in-memory engine registry."""
    global _engine

    cfg = load_config(_project_dir)
    if _engine is not None:
        from stepwise.registry_factory import create_default_registry

        _engine.config = cfg
        _engine.registry = create_default_registry(cfg)
        _engine.billing_mode = cfg.billing
        _engine.max_concurrent_jobs = cfg.max_concurrent_jobs
        if hasattr(_engine, "_executor_limits"):
            _engine._executor_limits = cfg.resolved_executor_limits()
    return cfg


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


def _serialize_job(job: Job, summary: bool = False) -> dict:
    if summary:
        engine = _get_engine()
        has_suspended = bool(engine.store.suspended_runs(job.id))
        # Include current/last step info for list view context
        current_step = None
        if job.status == JobStatus.RUNNING:
            running = engine.store.running_runs(job.id)
            if running:
                r = running[0]
                current_step = {
                    "name": r.step_name,
                    "status": r.status.value,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                }
        elif job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.PAUSED):
            runs = engine.store.runs_for_job(job.id)
            terminal = [r for r in runs if r.completed_at]
            if terminal:
                last = max(terminal, key=lambda r: r.completed_at)
                current_step = {
                    "name": last.step_name,
                    "status": last.status.value,
                    "started_at": last.started_at.isoformat() if last.started_at else None,
                    "completed_at": last.completed_at.isoformat() if last.completed_at else None,
                }
        return {
            "id": job.id,
            "name": job.name,
            "objective": job.objective,
            "status": job.status.value,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "parent_job_id": job.parent_job_id,
            "created_by": job.created_by,
            "flow_file": getattr(job.workflow, "source_dir", None),
            "metadata": job.metadata,
            "has_suspended_steps": has_suspended,
            "current_step": current_step,
            "workflow": job.workflow.to_dict() if job.workflow else None,
            "job_group": job.job_group,
            "depends_on": job.depends_on,
        }
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

            # Auto-adopt CLI jobs stale >5 minutes — their runner is definitely gone
            very_stale = engine.store.stale_jobs(max_age_seconds=300)
            for job in very_stale:
                _adopt_stale_cli_job(engine, job)
                # Re-evaluate: fail dead steps, dispatch ready ones, check terminal
                engine._recover_dead_script_runs(job)
                engine._dispatch_ready(job.id)
                engine._check_job_terminal(job.id)
                _notify_change(job.id)

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

    Jobs with suspended external steps are NOT zombies — they're legitimately
    waiting for input. Skip those and let the engine resume them normally.

    If a job has no running steps but all terminal steps completed (server
    crashed between step completion and job settlement), mark it COMPLETED
    instead of FAILED.
    """
    import logging
    logger = logging.getLogger("stepwise.server")
    for job in store.active_jobs():
        if job.created_by != "server":
            continue
        # Skip jobs that have suspended steps — they're waiting on external input
        if store.suspended_runs(job.id):
            logger.info("Skipping job %s (%s): has suspended steps waiting for input", job.id, job.objective)
            continue
        # Kill orphaned agent processes and fail running step runs
        running_runs = store.running_runs(job.id)
        for run in running_runs:
            # Check if the agent process is still alive and belongs to an agent
            if run.pid:
                expected_pgid = (run.executor_state or {}).get("pgid")
                if verify_agent_pid(run.pid, expected_pgid=expected_pgid):
                    logger.info(
                        "Step run %s (job %s step %s) PID %d verified alive, leaving for reattach",
                        run.id, job.id, run.step_name, run.pid,
                    )
                    continue
                else:
                    logger.info(
                        "Step run %s (job %s step %s) PID %d dead or recycled, marking failed",
                        run.id, job.id, run.step_name, run.pid,
                    )

            # Kill the actual OS process group if we have its pgid
            if run.executor_state:
                pgid = run.executor_state.get("pgid")
                if pgid:
                    try:
                        os.killpg(pgid, signal.SIGTERM)
                        logger.info("Killed orphaned process group %d for job %s step %s", pgid, job.id, run.step_name)
                    except (ProcessLookupError, PermissionError):
                        pass  # already dead
            run.status = StepRunStatus.FAILED
            run.error = f"Agent process died (PID {run.pid} not found on restart)" if run.pid else "Server restarted: step was orphaned"
            run.pid = None
            run.completed_at = _now()
            store.save_run(run)

        # Check if any runs are still alive (skipped above)
        still_running = store.running_runs(job.id)
        if still_running:
            # Some agent processes survived — leave job running for engine to manage
            logger.info("Job %s (%s) has %d surviving step(s), leaving running", job.id, job.objective, len(still_running))
        elif not running_runs and _job_looks_complete(store, job):
            # No running steps and all terminal steps completed — settle as COMPLETED
            job.status = JobStatus.COMPLETED
            job.updated_at = _now()
            store.save_job(job)
            logger.info("Recovered completed job %s (%s) — settled after restart", job.id, job.objective)
        else:
            job.status = JobStatus.FAILED
            job.updated_at = _now()
            store.save_job(job)
            logger.info("Failed zombie job %s (%s)", job.id, job.objective)


def _job_looks_complete(store: ThreadSafeStore, job: Job) -> bool:
    """Check if a RUNNING job actually completed (all terminal steps have completed runs).

    Used during restart cleanup to avoid failing jobs that were done but
    never settled because the server crashed.
    """
    terminal_steps = job.workflow.terminal_steps()
    if not terminal_steps:
        return False
    for step_name in terminal_steps:
        latest = store.latest_completed_run(job.id, step_name)
        if not latest:
            return False
    return True


def _cleanup_stale_queue_owners(store: ThreadSafeStore) -> None:
    """Terminate acpx queue owner processes not associated with any running step.

    Collects ACP session IDs from all currently running step runs, then kills
    any queue owner process whose session is not in that active set.
    """
    import logging
    logger = logging.getLogger("stepwise.server")

    from stepwise.agent import cleanup_orphaned_queue_owners

    active_ids, active_pids = _collect_active_agent_info(store)

    orphaned = cleanup_orphaned_queue_owners(active_ids, active_pids, kill=True)
    if orphaned:
        logger.info(
            "Cleaned up %d orphaned acpx queue owner(s) on startup", len(orphaned),
        )

    # Also scan process table for zombie queue owners without lock files
    from stepwise.agent import find_zombie_queue_owners
    zombies = find_zombie_queue_owners(active_ids, active_pids)
    for pid, cmdline in zombies:
        logger.info("Zombie acpx queue owner (no lock file): pid=%d cmd=%s", pid, cmdline[:120])
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info("Terminated zombie queue owner pid=%d", pid)
        except (ProcessLookupError, PermissionError):
            pass
    if zombies:
        logger.info("Cleaned up %d zombie acpx queue owner(s) via process scan on startup", len(zombies))


def _cleanup_orphaned_acpx_processes(store: ThreadSafeStore) -> None:
    """Kill acpx/claude processes not belonging to any running step.

    Collects PIDs from all currently running step runs, then scans the process
    table for claude-agent-acp and acpx __queue-owner processes. Any process
    not in the process group of a running step is terminated.
    """
    import logging
    logger = logging.getLogger("stepwise.server")

    from stepwise.agent import cleanup_orphaned_acpx

    # Collect PIDs from running steps (executor_state.pid)
    active_pids: set[int] = set()
    for job in store.active_jobs():
        for run in store.running_runs(job.id):
            if run.executor_state and run.executor_state.get("pid"):
                active_pids.add(run.executor_state["pid"])

    killed = cleanup_orphaned_acpx(active_pids)
    if killed:
        logger.info(
            "Cleaned up %d orphaned acpx/claude process(es) on startup", len(killed),
        )


def _collect_active_agent_info(store: ThreadSafeStore) -> tuple[set[str], set[int]]:
    """Collect ACP session IDs/names AND PIDs from running AND recently completed step runs.

    Returns (active_session_ids, active_pids) — both are used to protect
    running agents from the periodic cleanup.

    Protects:
    - Running step runs (regardless of parent job status)
    - Recently completed step runs with continue_session=True (their queue
      owners may still be needed by downstream steps in the chain)
    """
    from datetime import datetime, timezone, timedelta
    active_ids: set[str] = set()
    active_pids: set[int] = set()

    # Protect running steps
    for run in store.all_running_runs():
        if run.executor_state:
            if run.executor_state.get("session_id"):
                active_ids.add(run.executor_state["session_id"])
            if run.executor_state.get("session_name"):
                active_ids.add(run.executor_state["session_name"])
            if run.executor_state.get("pid"):
                active_pids.add(run.executor_state["pid"])
            if run.executor_state.get("pgid"):
                active_pids.add(run.executor_state["pgid"])

    # Protect recently completed continue_session steps (within 5 min)
    # Their queue owners may still be needed by downstream steps
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    for job in store.active_jobs():
        for run in store.completed_runs(job.id):
            if (run.executor_state
                    and run.executor_state.get("capture_transcript") is not None  # was an agent step
                    and run.completed_at
                    and run.completed_at > cutoff):
                if run.executor_state.get("session_name"):
                    active_ids.add(run.executor_state["session_name"])
                if run.executor_state.get("session_id"):
                    active_ids.add(run.executor_state["session_id"])

    return active_ids, active_pids


async def _periodic_queue_owner_cleanup() -> None:
    """Periodically scan for and clean up orphaned acpx queue owner processes.

    Runs every 60 seconds. Uses both lock-file and process-table scanning
    to detect zombie queue owners not associated with any running step.
    """
    import logging
    logger = logging.getLogger("stepwise.server")
    engine = _get_engine()

    while True:
        try:
            await asyncio.sleep(60)

            active_ids, active_pids = _collect_active_agent_info(engine.store)

            # Lock-file based cleanup — skip queue owners whose session ID
            # OR PID matches a running step
            from stepwise.agent import cleanup_orphaned_queue_owners
            lock_orphans = cleanup_orphaned_queue_owners(active_ids, active_pids, kill=True)
            if lock_orphans:
                logger.info("Periodic cleanup: terminated %d orphaned queue owner(s) via lock files", len(lock_orphans))

            # Process-table based cleanup
            from stepwise.agent import find_zombie_queue_owners
            zombies = find_zombie_queue_owners(active_ids, active_pids)
            for pid, cmdline in zombies:
                logger.info("Periodic cleanup: zombie queue owner pid=%d cmd=%s", pid, cmdline[:120])
                try:
                    os.kill(pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            if zombies:
                logger.info("Periodic cleanup: terminated %d zombie queue owner(s) via process scan", len(zombies))

        except asyncio.CancelledError:
            break
        except Exception:
            logger.debug("Periodic queue owner cleanup error", exc_info=True)
            await asyncio.sleep(60)


def _setup_file_logging(dot_dir: Path) -> None:
    """Add a RotatingFileHandler to the root logger for server.log.

    Skips if a file handler is already attached (e.g. server_bg.py --detach).
    """
    root = logging.getLogger()
    if any(isinstance(h, (logging.FileHandler, RotatingFileHandler)) for h in root.handlers):
        return

    log_dir = dot_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "server.log"

    handler = RotatingFileHandler(
        str(log_path), maxBytes=5 * 1024 * 1024, backupCount=3
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(name)s %(levelname)s: %(message)s"
    ))
    root.addHandler(handler)
    if root.level == logging.NOTSET or root.level > logging.WARNING:
        root.setLevel(logging.WARNING)


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

    # Ensure server.log exists regardless of how the server was started
    # (foreground, systemd, etc.).  server_bg.py already sets up a handler
    # for --detach mode, so skip if one is already present.
    _setup_file_logging(dot_dir)

    _engine = AsyncEngine(store, registry, jobs_dir=jobs_dir, project_dir=dot_dir if dot_dir.is_dir() else None, billing_mode=config.billing, config=config, max_concurrent_jobs=config.max_concurrent_jobs)

    # Fail zombie jobs: server-owned jobs left in running/pending from a dead process
    _cleanup_zombie_jobs(store)
    # Auto-adopt CLI-owned jobs with stale heartbeats (>120s) — their runner is gone
    adopted = _auto_adopt_stale_cli_jobs(_engine, max_age_seconds=120)
    # Re-evaluate surviving RUNNING jobs (settle any that completed pre-crash).
    # This also covers newly adopted jobs since they're now server-owned.
    _engine.recover_jobs()

    # Reattach monitoring for agent steps that survived the restart
    reattached = await _engine.reattach_surviving_runs()
    if reattached:
        logger.info("Reattached %d surviving step run(s) from previous server", reattached)

    # NOTE: Queue owner cleanup disabled. Queue owners manage their own lifecycle
    # via TTL (--ttl 0 = stay alive forever). Stepwise's cleanup routines were
    # killing queue owners that belonged to running steps, causing
    # "Queue owner disconnected" failures. The cleanup code had multiple bugs:
    # session-ID mismatches, PGID mismatches for setsid processes, and race
    # conditions with concurrent jobs. Rather than maintain fragile heuristics,
    # let acpx manage its own processes. Stale queue owners are harmless (they
    # idle and eventually exit when their TTL expires on non-zero TTL sessions).

    # Register in global server registry
    from stepwise.server_detect import register_server, unregister_server
    _port = int(os.environ.get("STEPWISE_PORT", "8340"))
    register_server(
        project_path=str(_project_dir),
        pid=os.getpid(),
        port=_port,
        url=f"http://localhost:{_port}",
    )

    _engine.on_broadcast = _schedule_broadcast
    _engine.on_event = _schedule_event_stream
    _engine_task = asyncio.create_task(_engine.run())
    _stream_monitor = asyncio.create_task(_agent_stream_monitor())
    _observer = asyncio.create_task(_observe_external_jobs())
    # Periodic queue owner cleanup disabled — see note above
    _queue_cleanup = None

    yield

    # Cancel all stream tailer tasks
    for task in _stream_tasks.values():
        task.cancel()
    _stream_tasks.clear()

    if _queue_cleanup:
        _queue_cleanup.cancel()
        try:
            await _queue_cleanup
        except asyncio.CancelledError:
            pass

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
    if _engine and hasattr(_engine, "shutdown"):
        await _engine.shutdown()
    store.close()
    unregister_server(str(_project_dir))


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

# Log unhandled exceptions but keep the server running.
# Crashing on API errors kills running agent jobs.
@app.exception_handler(Exception)
async def _log_unhandled_error(request, exc):
    import traceback
    import logging as _logging
    traceback.print_exception(type(exc), exc, exc.__traceback__)
    _logging.error("Unhandled exception in API handler (server continues)")
    from starlette.responses import JSONResponse
    return JSONResponse({"error": str(exc)}, status_code=500)


# ── Jobs ──────────────────────────────────────────────────────────────


@app.get("/api/jobs")
def list_jobs(request: Request, status: str | None = None, top_level: bool = False, limit: int = 50):
    engine = _get_engine()
    if status:
        try:
            job_status = JobStatus(status)
        except ValueError:
            valid = [s.value for s in JobStatus]
            raise HTTPException(status_code=400, detail=f"Invalid status '{status}'. Valid: {valid}")
    else:
        job_status = None
    meta_filters = {
        k[5:]: v for k, v in request.query_params.items() if k.startswith("meta.")
    }
    jobs = engine.store.all_jobs(
        job_status, top_level_only=top_level, limit=limit,
        meta_filters=meta_filters or None,
    )
    return [_serialize_job(j, summary=True) for j in jobs]


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
            metadata=req.metadata,
        )
        needs_save = False
        if req.status == "awaiting_approval":
            job.status = JobStatus.AWAITING_APPROVAL
            needs_save = True
        elif req.status == "staged":
            job.status = JobStatus.STAGED
            needs_save = True
        if req.job_group:
            job.job_group = req.job_group
            needs_save = True
        if req.notify_url:
            job.notify_url = req.notify_url
            job.notify_context = req.notify_context or {}
            needs_save = True
        if needs_save:
            engine.store.save_job(job)
        if req.status == "awaiting_approval":
            engine._emit(job.id, JOB_AWAITING_APPROVAL)
        # Auto-start unless deferred (staged/awaiting_approval).
        # If at the concurrency limit, start_job queues the job as PENDING
        # and it will be dispatched when a slot opens.
        if req.status not in ("staged", "awaiting_approval"):
            try:
                engine.start_job(job.id)
            except (KeyError, ValueError):
                pass  # job status changed or already started
        _notify_change(job.id)
        return _serialize_job(job)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/jobs/recent-flows")
def recent_flows(limit: int = 5):
    engine = _get_engine()
    jobs = engine.store.recent_flows(limit=limit)
    return [
        {
            "flow_name": (job.workflow.metadata.name if job.workflow.metadata else None) or job.name or job.objective,
            "flow_path": getattr(job.workflow, "source_dir", None),
            "last_inputs": job.inputs,
            "last_job_id": job.id,
            "last_job_name": job.name,
            "last_run_at": job.updated_at.isoformat(),
            "last_status": job.status.value,
            "workflow": job.workflow.to_dict(),
        }
        for job in jobs
    ]


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
        job = engine.store.load_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    # Idempotent: already running is a no-op (not an error)
    if job.status == JobStatus.RUNNING:
        return {"status": "started"}
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


@app.post("/api/jobs/{job_id}/reset")
def reset_job(job_id: str):
    engine = _get_engine()
    try:
        engine.reset_job(job_id)
        _notify_change(job_id)
        return {"status": "reset"}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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

    _adopt_stale_cli_job(engine, job)

    # Engine re-evaluates — exit rules handle recovery
    engine._recover_dead_script_runs(job)
    engine._dispatch_ready(job_id)
    engine._check_job_terminal(job_id)
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


# ── Job staging & dependency endpoints ──────────────────────────────


class StageJobRequest(BaseModel):
    job_group: str | None = None


class RunGroupRequest(BaseModel):
    group: str


class AddDepRequest(BaseModel):
    depends_on_job_id: str


@app.post("/api/jobs/{job_id}/stage")
def stage_job(job_id: str, req: StageJobRequest):
    """Set a job to STAGED status and optionally assign a group."""
    engine = _get_engine()
    try:
        job = engine.store.load_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    job.status = JobStatus.STAGED
    if req.job_group is not None:
        job.job_group = req.job_group
    job.updated_at = _now()
    engine.store.save_job(job)
    _notify_change(job_id)
    return {"status": "staged", "job_id": job_id}


@app.post("/api/jobs/{job_id}/run")
def run_staged_job(job_id: str):
    """Transition a single STAGED job to PENDING."""
    engine = _get_engine()
    try:
        engine.store.transition_job_to_pending(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    engine._start_queued_jobs()
    _notify_change(job_id)
    return {"status": "pending", "job_id": job_id}


@app.post("/api/jobs/{job_id}/approve")
def approve_job_route(job_id: str):
    """Approve a job awaiting approval → PENDING."""
    engine = _get_engine()
    try:
        engine.approve_job(job_id)
        _notify_change(job_id)
        return {"status": "approved", "job_id": job_id}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/jobs/run-group")
def run_group(req: RunGroupRequest):
    """Transition all staged jobs in a group to PENDING."""
    engine = _get_engine()
    job_ids = engine.store.transition_group_to_pending(req.group)
    engine._start_queued_jobs()
    for jid in job_ids:
        _notify_change(jid)
    return {"status": "pending", "group": req.group, "count": len(job_ids), "job_ids": job_ids}


@app.post("/api/jobs/{job_id}/deps")
def add_dependency(job_id: str, req: AddDepRequest):
    """Add a dependency edge. Validates STAGED status and cycle-free."""
    engine = _get_engine()
    try:
        job = engine.store.load_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    try:
        engine.store.load_job(req.depends_on_job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Dependency target not found: {req.depends_on_job_id}")
    if job.status not in (JobStatus.STAGED, JobStatus.AWAITING_APPROVAL):
        raise HTTPException(status_code=400, detail=f"Can only add deps to STAGED/AWAITING_APPROVAL jobs (job is {job.status.value})")
    if engine.store.would_create_cycle(job_id, req.depends_on_job_id):
        raise HTTPException(status_code=409, detail="Cannot add dependency: would create a cycle")
    engine.store.add_job_dependency(job_id, req.depends_on_job_id)
    _notify_change(job_id)
    return {"job_id": job_id, "depends_on": req.depends_on_job_id, "action": "added"}


@app.delete("/api/jobs/{job_id}/deps/{dep_job_id}")
def remove_dependency(job_id: str, dep_job_id: str):
    """Remove a dependency edge. Validates STAGED status."""
    engine = _get_engine()
    try:
        job = engine.store.load_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if job.status not in (JobStatus.STAGED, JobStatus.AWAITING_APPROVAL):
        raise HTTPException(status_code=400, detail=f"Can only remove deps from STAGED/AWAITING_APPROVAL jobs (job is {job.status.value})")
    engine.store.remove_job_dependency(job_id, dep_job_id)
    _notify_change(job_id)
    return {"job_id": job_id, "depends_on": dep_job_id, "action": "removed"}


@app.get("/api/jobs/{job_id}/deps")
def get_dependencies(job_id: str):
    """Return dependency list for a job."""
    engine = _get_engine()
    try:
        engine.store.load_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    deps = engine.store.get_job_dependencies(job_id)
    return {"job_id": job_id, "depends_on": deps}


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
    if engine.billing_mode == "subscription":
        return {"run_id": run_id, "cost_usd": 0, "billing_mode": "subscription"}
    cost = engine.store.accumulated_cost(run_id)
    return {"run_id": run_id, "cost_usd": cost, "billing_mode": "api_key"}


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
    if engine.billing_mode == "subscription":
        return {"job_id": job_id, "cost_usd": 0, "billing_mode": "subscription"}
    cost = engine.job_cost(job_id)
    return {"job_id": job_id, "cost_usd": round(cost, 4) if cost else 0, "billing_mode": "api_key"}


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


@app.get("/api/jobs/{job_id}/outputs")
def get_job_outputs_alias(
    job_id: str,
    step: str | None = Query(default=None, description="Comma-separated step names"),
    inputs: bool = Query(default=False),
):
    """Alias for /api/jobs/{job_id}/output (plural form)."""
    return get_job_output(job_id, step=step, inputs=inputs)


@app.get("/api/errors/similar")
def similar_errors(
    error_category: str,
    exclude_run_id: str | None = None,
    step_name: str | None = None,
    limit: int = 5,
):
    """Find similar past failures by error category."""
    engine = _get_engine()
    results = engine.store.similar_failed_runs(
        error_category=error_category,
        exclude_run_id=exclude_run_id,
        step_name=step_name,
        limit=min(limit, 20),
    )
    return {"results": results}


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
    """Health check endpoint for server detection and identity verification."""
    from importlib.metadata import version
    engine = _get_engine()
    try:
        ver = version("stepwise-run")
    except Exception:
        ver = "unknown"
    return {
        "status": "ok",
        "version": ver,
        "active_jobs": len(engine.store.active_jobs()),
        "project_path": str(_project_dir) if _project_dir else None,
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
    from importlib.metadata import version
    try:
        ver = version("stepwise-run")
    except Exception:
        ver = "unknown"
    return {
        "active_jobs": len(active),
        "total_jobs": len(all_jobs),
        "registered_executors": list(engine.registry._factories.keys()),
        "cwd": os.getcwd(),
        "version": ver,
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
            except Exception as e:
                logger.warning("Failed to load template: %s", e)
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


class UpdateDefaultAgentRequest(BaseModel):
    agent: str


class SetApiKeyRequest(BaseModel):
    key: str  # "openrouter" or "anthropic"
    value: str
    scope: str = "user"  # "user" or "project"


class UpdateConcurrencyRequest(BaseModel):
    executor_type: str
    limit: int  # 0 = remove limit (unlimited)


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
        "default_agent": cfg.default_agent,
        "labels": [li.to_dict() for li in cs.label_info],
        "billing_mode": cfg.billing,
        "concurrency_limits": cfg.resolved_executor_limits(),
        "concurrency_running": {
            t: sum(1 for v in _engine._task_exec_types.values() if v == t)
            for t in set(_engine._task_exec_types.values())
        } if _engine and hasattr(_engine, "_task_exec_types") else {},
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
    save_project_config(
        _project_dir,
        labels,
        data.get("default_model"),
        data.get("default_agent"),
    )
    _reload_engine_config()
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
    save_project_config(
        _project_dir,
        labels,
        data.get("default_model"),
        data.get("default_agent"),
    )
    _reload_engine_config()
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
    save_project_config(
        _project_dir,
        labels,
        data.get("default_model"),
        data.get("default_agent"),
    )
    _reload_engine_config()
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
    _reload_engine_config()
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
    _reload_engine_config()
    return {"status": "added", "model": entry.to_dict()}


@app.delete("/api/config/models/{model_id:path}")
def delete_model(model_id: str):
    cfg = load_config()  # user-level
    orig_len = len(cfg.model_registry)
    cfg.model_registry = [m for m in cfg.model_registry if m.id != model_id]
    if len(cfg.model_registry) == orig_len:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not in registry.")
    save_config(cfg)
    _reload_engine_config()
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
    _reload_engine_config()
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
    save_project_config(
        _project_dir,
        labels,
        req.model,
        data.get("default_agent"),
    )
    cfg = _reload_engine_config()
    return {"status": "updated", "default_model": cfg.default_model}


@app.put("/api/config/default-agent")
def set_default_agent(req: UpdateDefaultAgentRequest):
    """Set the default ACP agent backend for agent executor steps."""
    import yaml as yaml_lib
    path = _project_dir / ".stepwise" / "config.yaml"
    data: dict = {}
    if path.exists():
        data = yaml_lib.safe_load(path.read_text()) or {}
    labels = data.get("labels", {})
    save_project_config(
        _project_dir,
        labels,
        data.get("default_model"),
        req.agent,
    )
    cfg = _reload_engine_config()
    return {"status": "updated", "default_agent": cfg.default_agent}


@app.put("/api/config/concurrency")
def update_concurrency_limit(req: UpdateConcurrencyRequest):
    """Set max concurrent steps for an executor type. 0 = unlimited."""
    if req.limit < 0:
        raise HTTPException(status_code=400, detail="Limit must be non-negative (0 = unlimited)")
    cfg = load_config(_project_dir)
    limits = dict(cfg.max_concurrent_by_executor)
    if req.limit == 0:
        limits.pop(req.executor_type, None)
    else:
        limits[req.executor_type] = req.limit
    save_project_local_config(_project_dir, max_concurrent_by_executor=limits)
    new_cfg = _reload_engine_config()
    return {"status": "updated", "limits": new_cfg.resolved_executor_limits()}


@app.post("/api/config/reload")
def reload_config_endpoint():
    """Reload config from disk. Use after manual YAML edits."""
    cfg = _reload_engine_config()
    return {"status": "reloaded", "limits": cfg.resolved_executor_limits()}


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

        # After edges
        after_deps = step_def.get("after") or step_def.get("sequencing") or []
        if isinstance(after_deps, str):
            after_deps = [after_deps]
        for seq_dep in after_deps:
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


@app.get("/api/flow-stats")
def get_flow_stats():
    """Return job_count and last_run_at per flow source_dir."""
    engine = _get_engine()
    rows = engine.store._conn.execute(
        """SELECT json_extract(workflow, '$.source_dir') as source_dir,
                  COUNT(*) as job_count,
                  MAX(updated_at) as last_run_at
           FROM jobs
           WHERE json_extract(workflow, '$.source_dir') IS NOT NULL
           GROUP BY source_dir"""
    ).fetchall()
    result = []
    project_prefix = str(_project_dir) + "/"
    for row in rows:
        sd = row["source_dir"]
        rel = sd[len(project_prefix):] if sd.startswith(project_prefix) else sd
        result.append({
            "flow_dir": rel,
            "job_count": row["job_count"],
            "last_run_at": row["last_run_at"],
        })
    return result


@app.get("/api/flow-jobs")
def get_flow_jobs(flow_dir: str = Query(...), limit: int = Query(10, ge=1, le=50)):
    """Return recent jobs created from a specific flow directory."""
    engine = _get_engine()
    abs_dir = str((_project_dir / flow_dir).resolve())
    rows = engine.store._conn.execute(
        """SELECT id, name, objective, status, created_at, updated_at
           FROM jobs
           WHERE json_extract(workflow, '$.source_dir') = ?
           ORDER BY created_at DESC
           LIMIT ?""",
        (abs_dir, limit),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "objective": row["objective"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


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
    template: str = "blank"


def _flow_template_yaml(name: str, template: str) -> str:
    """Generate FLOW.yaml content for a given template type."""
    if template == "simple-llm":
        return (
            f"name: {name}\n"
            f"description: Single LLM step\n"
            f"\n"
            f"steps:\n"
            f"  generate:\n"
            f"    executor: llm\n"
            f"    config:\n"
            f"      prompt: \"$prompt\"\n"
            f"    inputs:\n"
            f"      prompt: $job.prompt\n"
            f"    outputs: [response]\n"
        )
    elif template == "agent-task":
        return (
            f"name: {name}\n"
            f"description: Agent task with validation\n"
            f"\n"
            f"steps:\n"
            f"  implement:\n"
            f"    executor: agent\n"
            f"    prompt: \"Implement: $spec\"\n"
            f"    inputs:\n"
            f"      spec: $job.spec\n"
            f"    outputs: [result]\n"
            f"\n"
            f"  validate:\n"
            f"    run: |\n"
            f"      echo '{{\"status\": \"pass\"}}'\n"
            f"    inputs:\n"
            f"      result: implement.result\n"
            f"    outputs: [status]\n"
            f"    exits:\n"
            f"      - name: success\n"
            f"        when: \"outputs.status == 'pass'\"\n"
            f"        action: advance\n"
            f"      - name: retry\n"
            f"        when: \"attempt < 3\"\n"
            f"        action: loop\n"
            f"        target: implement\n"
        )
    elif template == "external-approval":
        return (
            f"name: {name}\n"
            f"description: Agent task with external approval loop\n"
            f"\n"
            f"steps:\n"
            f"  draft:\n"
            f"    executor: agent\n"
            f"    prompt: \"Draft: $request\"\n"
            f"    inputs:\n"
            f"      request: $job.request\n"
            f"      feedback:\n"
            f"        from: approve.feedback\n"
            f"        optional: true\n"
            f"    outputs: [result]\n"
            f"\n"
            f"  approve:\n"
            f"    executor: external\n"
            f"    prompt: \"Review the draft and approve or request changes\"\n"
            f"    inputs:\n"
            f"      result: draft.result\n"
            f"    outputs: [decision, feedback]\n"
            f"    exits:\n"
            f"      - name: approved\n"
            f"        when: \"outputs.decision == 'approve'\"\n"
            f"        action: advance\n"
            f"      - name: revise\n"
            f"        when: \"attempt < 5\"\n"
            f"        action: loop\n"
            f"        target: draft\n"
        )
    elif template == "research-pipeline":
        return (
            f"name: {name}\n"
            f"description: Multi-step research pipeline\n"
            f"\n"
            f"steps:\n"
            f"  gather:\n"
            f"    executor: agent\n"
            f"    prompt: \"Research the following topic and gather key findings: $topic\"\n"
            f"    inputs:\n"
            f"      topic: $job.topic\n"
            f"    outputs: [findings]\n"
            f"\n"
            f"  analyze:\n"
            f"    executor: llm\n"
            f"    config:\n"
            f"      prompt: \"Analyze and synthesize these findings into a structured report:\\n$findings\"\n"
            f"    inputs:\n"
            f"      findings: gather.findings\n"
            f"    outputs: [analysis]\n"
            f"\n"
            f"  review:\n"
            f"    executor: external\n"
            f"    prompt: \"Review the research analysis and approve or request deeper investigation\"\n"
            f"    inputs:\n"
            f"      analysis: analyze.analysis\n"
            f"    outputs: [decision, notes]\n"
            f"    exits:\n"
            f"      - name: approved\n"
            f"        when: \"outputs.decision == 'approve'\"\n"
            f"        action: advance\n"
            f"      - name: dig-deeper\n"
            f"        when: \"attempt < 3\"\n"
            f"        action: loop\n"
            f"        target: gather\n"
        )
    else:
        # blank / default
        return (
            f"name: {name}\n"
            f'description: ""\n'
            f"\n"
            f"steps:\n"
            f"  hello:\n"
            f"    run: 'echo \"{{\\\"message\\\": \\\"hello from {name}\\\"}}\"'\n"
            f"    outputs: [message]\n"
        )


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
    flow_file.write_text(_flow_template_yaml(name, req.template))

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


# ── Flow Config (must be BEFORE the catch-all GET route) ─────────────


def _config_file_path(flow_path: Path) -> Path:
    """Determine the config.local.yaml path for a flow file."""
    if flow_path.name == "FLOW.yaml":
        return flow_path.parent / "config.local.yaml"
    # Single-file: my-flow.flow.yaml → my-flow.config.local.yaml
    stem = flow_path.stem
    if stem.endswith(".flow"):
        stem = stem[:-5]
    return flow_path.parent / f"{stem}.config.local.yaml"


@app.get("/api/flows/local/{path:path}/config")
def get_flow_config(path: str):
    """Read flow config: declared config_vars schema + current values from config.local.yaml."""
    import yaml as _yaml
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    abs_path = _resolve_flow_path(path)
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"Flow not found: {path}")

    try:
        workflow = load_workflow_yaml(abs_path)
    except YAMLLoadError as e:
        raise HTTPException(status_code=400, detail=f"Invalid flow YAML: {'; '.join(e.errors)}")

    config_vars = [v.to_dict() for v in workflow.config_vars]

    # Read config.local.yaml if present
    cfg_path = _config_file_path(abs_path)
    values: dict = {}
    raw_yaml = ""
    if cfg_path.is_file():
        raw_yaml = cfg_path.read_text()
        loaded = _yaml.safe_load(raw_yaml)
        if isinstance(loaded, dict):
            values = loaded

    return {
        "config_vars": config_vars,
        "values": values,
        "raw_yaml": raw_yaml,
        "config_path": cfg_path.name,
    }


class SaveFlowConfigRequest(BaseModel):
    values: dict | None = None
    raw_yaml: str | None = None


@app.put("/api/flows/local/{path:path}/config")
def save_flow_config(path: str, req: SaveFlowConfigRequest):
    """Save flow config values to config.local.yaml.

    Accepts either structured values (dict) or raw YAML string.
    """
    import yaml as _yaml

    abs_path = _resolve_flow_path(path)
    if not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"Flow not found: {path}")

    cfg_path = _config_file_path(abs_path)

    if req.raw_yaml is not None:
        content = req.raw_yaml
        # Validate it's parseable YAML
        try:
            parsed = _yaml.safe_load(content)
        except _yaml.YAMLError as e:
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")
        if content.strip() and not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="Config YAML must be a mapping (key: value)")
    elif req.values is not None:
        # Filter out None values (treat as "unset")
        clean = {k: v for k, v in req.values.items() if v is not None}
        content = _yaml.dump(clean, default_flow_style=False, allow_unicode=True) if clean else ""
    else:
        raise HTTPException(status_code=400, detail="Provide either 'values' or 'raw_yaml'")

    # Handle empty config: delete the file rather than write empty
    if not content.strip():
        if cfg_path.is_file():
            cfg_path.unlink()
        return {"config_path": cfg_path.name, "values": {}, "raw_yaml": ""}

    # Atomic write
    tmp = cfg_path.with_suffix(".tmp")
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(content)
    tmp.rename(cfg_path)

    # Re-read to return canonical state
    loaded = _yaml.safe_load(content)
    values = loaded if isinstance(loaded, dict) else {}

    return {"config_path": cfg_path.name, "values": values, "raw_yaml": content}


# ── Load Local Flow (catch-all — must be AFTER /files, /config, and DELETE routes) ──

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
    elif req.executor == "external":
        new_step = {"executor": "external", "prompt": "TODO", "outputs": ["result"]}
    elif req.executor == "poll":
        new_step = {
            "executor": "poll",
            "check_command": "exit 1  # replace with your check command",
            "interval_seconds": 30,
            "prompt": "Waiting for condition...",
            "outputs": ["result"],
        }
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

    # Cascade: remove input bindings and after/sequencing refs to the deleted step
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

            # Clean after (and legacy sequencing)
            for key in ("after", "sequencing"):
                seq = other_step.get(key)
                if isinstance(seq, list):
                    other_step[key] = [s for s in seq if s != req.step_name]
                    if not other_step[key]:
                        del other_step[key]

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


# ── Event Stream WebSocket ────────────────────────────────────────────


@app.websocket("/api/v1/events/stream")
async def event_stream(ws: WebSocket):
    """General-purpose event stream with filtering and replay.

    Query params:
      job_id     — filter by job ID (repeatable, OR semantics)
      session_id — filter by metadata.sys.session_id
      since_event_id — replay events with rowid > N, then switch to live
      since_job_start — replay all events for filtered jobs from creation
    """
    engine = _get_engine()

    # Parse filters from query params
    raw_job_ids = ws.query_params.getlist("job_id")
    job_ids: set[str] | None = set(raw_job_ids) if raw_job_ids else None
    session_id: str | None = ws.query_params.get("session_id")
    since_event_id_raw = ws.query_params.get("since_event_id")
    since_job_start = ws.query_params.get("since_job_start", "").lower() in ("true", "1", "yes")

    # Validate: since_job_start requires job_id or session_id
    if since_job_start and job_ids is None and session_id is None:
        await ws.accept()
        await ws.close(code=1008, reason="since_job_start requires job_id or session_id")
        return

    await ws.accept()

    # Resolve session_id → job_ids
    session_job_ids: set[str] = set()
    if session_id is not None:
        matching_jobs = engine.store.all_jobs(meta_filters={"sys.session_id": session_id})
        session_job_ids = {j.id for j in matching_jobs}

    # Build the combined set of job_ids for replay queries
    replay_job_ids: set[str] | None = None
    if job_ids is not None or session_id is not None:
        replay_job_ids = set()
        if job_ids:
            replay_job_ids.update(job_ids)
        if session_job_ids:
            replay_job_ids.update(session_job_ids)

    # Determine replay start rowid
    since_rowid = 0
    if since_event_id_raw is not None:
        try:
            since_rowid = int(since_event_id_raw)
        except ValueError:
            await ws.close(code=1008, reason="since_event_id must be an integer")
            return
    elif not since_job_start:
        # No replay requested — skip replay phase
        since_rowid = -1  # sentinel: no replay

    # Replay phase
    last_replayed_rowid = since_rowid if since_rowid >= 0 else 0
    if since_rowid >= 0:
        replay_results = engine.store.load_events_since(
            since_rowid=since_rowid,
            job_ids=replay_job_ids if replay_job_ids else None,
        )
        for rowid, event, metadata in replay_results:
            envelope = build_event_envelope(
                event.type, event.data, event.job_id, rowid,
                metadata, event.timestamp.isoformat(),
            )
            try:
                await ws.send_json(envelope)
            except Exception:
                return
            last_replayed_rowid = rowid

        # Send replay boundary frame
        try:
            await ws.send_json({
                "type": "sys.replay.complete",
                "last_event_id": last_replayed_rowid,
            })
        except Exception:
            return

    # Register for live events
    client = _StreamClient(
        ws=ws,
        queue=asyncio.Queue(maxsize=1000),
        job_ids=job_ids,
        session_id=session_id,
        session_job_ids=session_job_ids,
    )
    _event_stream_clients.append(client)

    try:
        # Catch-up: query events between replay and registration to close the gap
        if since_rowid >= 0:
            catchup = engine.store.load_events_since(
                since_rowid=last_replayed_rowid,
                job_ids=replay_job_ids if replay_job_ids else None,
            )
            for rowid, event, metadata in catchup:
                envelope = build_event_envelope(
                    event.type, event.data, event.job_id, rowid,
                    metadata, event.timestamp.isoformat(),
                )
                await ws.send_json(envelope)

        # Live send loop
        while True:
            envelope = await client.queue.get()
            await ws.send_json(envelope)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        try:
            _event_stream_clients.remove(client)
        except ValueError:
            pass


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
    from fastapi.responses import FileResponse, JSONResponse

    # Serve static assets directly
    app.mount("/assets", StaticFiles(directory=str(_web_dist / "assets")), name="assets")

    # SPA fallback: any non-API route serves index.html
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
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
