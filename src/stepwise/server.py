"""FastAPI server wrapping the Stepwise engine with REST + WebSocket API."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from contextlib import asynccontextmanager
from datetime import datetime, timezone as _tz
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from stepwise.engine import AsyncEngine, Engine, _adopt_stale_cli_job, _auto_adopt_stale_cli_jobs
from stepwise.config import (
    load_config, load_config_with_sources, save_config,
    save_project_config, save_project_local_config,
    save_agents_to_local_config,
    StepwiseConfig, ModelEntry,
    DEFAULT_LABEL_NAMES,
    validate_label_name, label_model_id,
)
from stepwise.openrouter_models import enrich_registry
from stepwise.models import (
    Job,
    JobConfig,
    JobStatus,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _gen_id,
    _now,
    Schedule,
    ScheduleStatus,
    ScheduleType,
    OverlapPolicy,
    RecoveryPolicy,
)
from stepwise.store import SQLiteStore
from stepwise.events import JOB_AWAITING_APPROVAL
from stepwise.hooks import build_event_envelope

logger = logging.getLogger("stepwise.server")


class _ThreadLocalConnProxy:
    """Per-thread sqlite3.Connection proxy.

    sqlite3 Connection objects and their Cursors cannot safely be shared
    across threads even with `check_same_thread=False` — the global
    connection state advances on every operation, and a cursor opened in
    thread A becomes invalid the moment thread B issues a new query on
    the same connection. The symptom is the classic
    `sqlite3.InterfaceError: bad parameter or other API misuse` when
    thread A tries to fetch from its now-stale cursor.

    The canonical sqlite3 multi-threading pattern is "one connection per
    thread," which WAL mode supports with no writer contention for
    readers (each connection sees a consistent MVCC snapshot). This
    proxy forwards every attribute access to a `threading.local`
    connection, lazily opening a new one on first use per thread and
    applying the same PRAGMAs as the primary connection.

    For `:memory:` databases the proxy switches to a shared-cache URI
    (`file:mem-<id>?mode=memory&cache=shared`) so every thread opens
    the SAME in-memory DB instead of getting its own empty one. This
    keeps test fixtures that rely on `:memory:` working.
    """

    def __init__(self, db_path: str) -> None:
        import sqlite3
        import threading
        # Rewrite :memory: to a shared-cache URI unique per proxy so
        # test stores don't collide and each test gets its own fresh
        # in-memory database shared across its threads. File-backed
        # paths pass through unchanged.
        if db_path == ":memory:":
            self._connect_uri = f"file:swstore-{id(self)}?mode=memory&cache=shared"
            self._use_uri = True
            # Keep a "keepalive" connection open for the lifetime of
            # the proxy — shared-cache memory DBs vanish the moment
            # their last connection closes, which could otherwise
            # happen between lazy per-thread conns.
            self._keepalive = sqlite3.connect(
                self._connect_uri, uri=True, check_same_thread=False,
            )
        else:
            self._connect_uri = db_path
            self._use_uri = False
            self._keepalive = None
        self._db_path = db_path
        self._tls = threading.local()
        # Track every live connection so the owning store can close
        # them on shutdown. Access to the registry is serialized by
        # the registry_lock; individual connections are never shared
        # across threads.
        self._all_conns: list[sqlite3.Connection] = []
        self._registry_lock = threading.Lock()

    def _make_conn(self):
        import sqlite3
        if self._use_uri:
            conn = sqlite3.connect(
                self._connect_uri, uri=True, check_same_thread=False,
            )
        else:
            conn = sqlite3.connect(self._connect_uri, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # WAL isn't meaningful for shared-cache memory DBs — skip it
        # there to avoid the "cannot change into wal mode from within
        # a transaction" gotcha on in-memory stores.
        if not self._use_uri:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        with self._registry_lock:
            self._all_conns.append(conn)
        return conn

    def _current(self):
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            conn = self._make_conn()
            self._tls.conn = conn
        return conn

    def close_all(self) -> None:
        """Close every thread's connection. Called at server shutdown."""
        with self._registry_lock:
            for conn in self._all_conns:
                try:
                    conn.close()
                except Exception:
                    pass
            self._all_conns.clear()

    def __getattr__(self, name):
        # Forward attribute access (execute, commit, executemany,
        # executescript, cursor, rollback, in_transaction, ...) to
        # the current thread's connection. Called only when `name`
        # isn't found via normal attribute lookup, so internal
        # attributes (_db_path, _tls, _all_conns) are handled first.
        return getattr(self._current(), name)

    def __enter__(self):
        return self._current().__enter__()

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._current().__exit__(exc_type, exc_val, exc_tb)

    @property
    def row_factory(self):
        return self._current().row_factory

    @row_factory.setter
    def row_factory(self, value):
        self._current().row_factory = value


class ThreadSafeStore(SQLiteStore):
    """SQLiteStore variant that uses one connection per thread.

    Safe for cross-thread access because each thread gets its own
    sqlite3.Connection via a `threading.local` proxy. WAL mode means
    readers don't block each other (MVCC snapshot per transaction),
    and writes are still serialized by SQLite at the file level.

    Pre-2026-04-15 used a single shared connection with a lock around
    each execute() call — that serialized reads fine but let cursors
    from different threads collide mid-fetch, producing
    sqlite3.InterfaceError under /api/jobs load. See roadmap entry
    "Jobs list API N+1 query + SQLite threading crash" for the
    diagnosis.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn = _ThreadLocalConnProxy(db_path)
        self._create_tables()

    def close(self) -> None:
        """Close all per-thread connections (server shutdown)."""
        if isinstance(self._conn, _ThreadLocalConnProxy):
            self._conn.close_all()


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


class CreateScheduleRequest(BaseModel):
    name: str
    type: str  # "cron" or "poll"
    flow_path: str
    cron_expr: str
    poll_command: str | None = None
    poll_timeout_seconds: int = 30
    cooldown_seconds: int | None = None
    job_inputs: dict | None = None
    job_name_template: str | None = None
    overlap_policy: str = "skip"
    recovery_policy: str = "skip"
    timezone: str = "America/Los_Angeles"
    max_consecutive_errors: int = 10
    metadata: dict | None = None


class UpdateScheduleRequest(BaseModel):
    name: str | None = None
    cron_expr: str | None = None
    poll_command: str | None = None
    poll_timeout_seconds: int | None = None
    cooldown_seconds: int | None = None
    job_inputs: dict | None = None
    job_name_template: str | None = None
    overlap_policy: str | None = None
    recovery_policy: str | None = None
    timezone: str | None = None
    max_consecutive_errors: int | None = None
    metadata: dict | None = None


class PauseScheduleRequest(BaseModel):
    reason: str | None = None


# ── Global state ──────────────────────────────────────────────────────

_engine: AsyncEngine | None = None
_ws_clients: set[WebSocket] = set()
_engine_task: asyncio.Task | None = None
_event_loop: asyncio.AbstractEventLoop | None = None
_templates_dir: Path = Path("templates")
_project_dir: Path = Path(".")
_stream_tasks: dict[str, asyncio.Task] = {}
_script_stream_tasks: dict[str, asyncio.Task] = {}
_script_monitor_task: asyncio.Task | None = None
_flow_mtimes: dict[str, float] = {}  # flow source_path → last known mtime

# Scheduler service (initialized in lifespan)
_scheduler = None  # SchedulerService | None


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
        if hasattr(_engine, "_agent_limits"):
            _engine._agent_limits = {
                k: v for k, v in cfg.max_concurrent_by_agent.items() if v > 0
            }
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

            # Parse user prompts (session/prompt method, different structure)
            if data.get("method") == "session/prompt":
                prompt_parts = data.get("params", {}).get("prompt", [])
                text_parts = [p.get("text", "") for p in prompt_parts
                              if isinstance(p, dict) and p.get("type") == "text"]
                if text_parts:
                    events.append({"t": "prompt", "text": "\n".join(text_parts)})
                continue

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
            elif su == "tool_call_update":
                status = update.get("status")
                title = update.get("title", "")
                tool_id = update.get("toolCallId", "")
                if status in ("completed", "failed"):
                    ev: dict = {"t": "tool_end", "id": tool_id}
                    if title:
                        ev["output"] = title
                    if status == "failed":
                        ev["error"] = True
                    events.append(ev)
                elif title and tool_id:
                    # Intermediate update with real title (e.g., file path)
                    events.append({"t": "tool_title", "id": tool_id, "title": title})
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


async def _tail_script_output(run_id: str, stdout_path: str, stderr_path: str | None) -> None:
    """Tail script stdout/stderr files and broadcast via WebSocket with byte offsets."""
    stdout_offset = 0
    stderr_offset = 0
    try:
        while True:
            stdout_new = ""
            stderr_new = ""
            prev_stdout_offset = stdout_offset
            prev_stderr_offset = stderr_offset

            try:
                with open(stdout_path, "rb") as f:
                    f.seek(stdout_offset)
                    data = f.read()
                    if data:
                        stdout_offset = f.tell()
                        stdout_new = data.decode("utf-8", errors="replace")
            except FileNotFoundError:
                pass

            if stderr_path:
                try:
                    with open(stderr_path, "rb") as f:
                        f.seek(stderr_offset)
                        data = f.read()
                        if data:
                            stderr_offset = f.tell()
                            stderr_new = data.decode("utf-8", errors="replace")
                except FileNotFoundError:
                    pass

            if stdout_new or stderr_new:
                await _broadcast({
                    "type": "script_output",
                    "run_id": run_id,
                    "stdout": stdout_new,
                    "stderr": stderr_new,
                    "stdout_offset": prev_stdout_offset,
                    "stderr_offset": prev_stderr_offset,
                })
            await asyncio.sleep(0.3)
    except asyncio.CancelledError:
        pass


def _get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Engine not initialized")
    return _engine


def _serialize_job(
    job: Job,
    summary: bool = False,
    cost_usd: float | None = None,
    *,
    # Precomputed per-job lookups — callers that serialize a LIST of
    # jobs (e.g. /api/jobs) should batch these upfront via
    # _build_summary_lookups() to avoid N+1 query storms. When None,
    # _serialize_job falls back to per-job queries for the single-job
    # callers that already hit hot code paths elsewhere.
    suspended_ids: set[str] | None = None,
    running_run_map: dict[str, StepRun] | None = None,
    last_terminal_map: dict[str, StepRun] | None = None,
    completed_step_counts: dict[str, int] | None = None,
) -> dict:
    if summary:
        engine = _get_engine()
        if suspended_ids is not None:
            has_suspended = job.id in suspended_ids
        else:
            has_suspended = bool(engine.store.suspended_runs(job.id))
        # Include current/last step info for list view context
        current_step = None
        if job.status == JobStatus.RUNNING:
            if running_run_map is not None:
                r = running_run_map.get(job.id)
            else:
                running = engine.store.running_runs(job.id)
                r = running[0] if running else None
            if r is not None:
                current_step = {
                    "name": r.step_name,
                    "status": r.status.value,
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                }
        elif job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.PAUSED, JobStatus.ARCHIVED):
            if last_terminal_map is not None:
                last = last_terminal_map.get(job.id)
            else:
                runs = engine.store.runs_for_job(job.id)
                terminal = [r for r in runs if r.completed_at]
                last = max(terminal, key=lambda r: r.completed_at) if terminal else None
            if last is not None:
                current_step = {
                    "name": last.step_name,
                    "status": last.status.value,
                    "started_at": last.started_at.isoformat() if last.started_at else None,
                    "completed_at": last.completed_at.isoformat() if last.completed_at else None,
                }
        # Lightweight workflow: step names (empty dicts) + metadata only, no full step definitions
        step_names = list(job.workflow.steps.keys()) if job.workflow else []
        wf_meta = job.workflow.metadata.to_dict() if job.workflow and job.workflow.metadata else None
        lightweight_workflow = {"steps": {name: {} for name in step_names}}
        if wf_meta:
            lightweight_workflow["metadata"] = wf_meta
        if completed_step_counts is not None:
            completed_steps = completed_step_counts.get(job.id, 0)
        else:
            completed_steps = engine.store.completed_step_count(job.id)
        result = {
            "id": job.id,
            "name": job.name,
            "objective": job.objective,
            "status": job.status.value,
            "created_at": job.created_at.isoformat(),
            "updated_at": job.updated_at.isoformat(),
            "parent_job_id": job.parent_job_id,
            "created_by": job.created_by,
            "flow_file": getattr(job.workflow, "source_dir", None),
            "flow_source_path": getattr(job.workflow, "source_path", None),
            "metadata": job.metadata,
            "has_suspended_steps": has_suspended,
            "current_step": current_step,
            "workflow": lightweight_workflow,
            "step_count": len(step_names),
            "completed_steps": completed_steps,
            "job_group": job.job_group,
            "depends_on": job.depends_on,
        }
        if cost_usd is not None:
            result["cost_usd"] = round(cost_usd, 4)
        return result
    return job.to_dict()


def _build_summary_lookups(store, jobs: list[Job]) -> dict:
    """Precompute all per-job lookups needed by _serialize_job(summary=True)
    in a constant number of batch queries. Eliminates the 4×N N+1 storm
    from the /api/jobs serializer loop.
    """
    if not jobs:
        return {
            "suspended_ids": set(),
            "running_run_map": {},
            "last_terminal_map": {},
            "completed_step_counts": {},
        }
    job_ids = [j.id for j in jobs]
    return {
        "suspended_ids": store.batch_job_ids_with_suspended_runs(job_ids),
        "running_run_map": store.batch_first_running_run(job_ids),
        "last_terminal_map": store.batch_last_terminal_run(job_ids),
        "completed_step_counts": store.batch_completed_step_counts(job_ids),
    }


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


async def _script_stream_monitor() -> None:
    """Periodically check for running script steps to tail."""
    engine = _get_engine()
    while True:
        try:
            for job in engine.store.active_jobs():
                for run in engine.store.running_runs(job.id):
                    state = run.executor_state or {}
                    stdout_path = state.get("stdout_path")
                    if stdout_path and run.id not in _script_stream_tasks:
                        task = asyncio.create_task(
                            _tail_script_output(
                                run.id,
                                stdout_path,
                                state.get("stderr_path"),
                            )
                        )
                        _script_stream_tasks[run.id] = task

            active_run_ids = set()
            for job in engine.store.active_jobs():
                for run in engine.store.running_runs(job.id):
                    active_run_ids.add(run.id)
            stale = [rid for rid in _script_stream_tasks if rid not in active_run_ids]
            for rid in stale:
                _script_stream_tasks[rid].cancel()
                del _script_stream_tasks[rid]

            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(5.0)


async def _process_health_check() -> None:
    """Periodic health check: detect dead runner processes and enforce TTL.

    Runs every 60s. Detects:
    - RUNNING step runs whose PID is no longer alive (clean up state)
    - RUNNING step runs that have exceeded the agent TTL (SIGTERM + cleanup)

    Logs all process lifecycle events for debugging.
    """
    from stepwise.process_lifecycle import (
        HEALTH_CHECK_INTERVAL_SECONDS,
        run_health_check,
    )

    engine = _get_engine()
    # Config > env var > default (0 = disabled)
    config_ttl = engine.config.agent_process_ttl if engine.config else 0
    ttl = int(os.environ.get("STEPWISE_AGENT_TTL", str(config_ttl)))
    interval = int(os.environ.get("STEPWISE_HEALTH_CHECK_INTERVAL", str(HEALTH_CHECK_INTERVAL_SECONDS)))
    logger.info("Process health check started (interval=%ds, agent_ttl=%ds)", interval, ttl)

    while True:
        try:
            await asyncio.sleep(interval)
            result = run_health_check(engine.store, ttl_seconds=ttl)

            # Re-evaluate affected jobs so exit rules / settlement can proceed
            affected_job_ids: set[str] = set()
            for run_id in result.dead_cleaned + result.expired_killed:
                try:
                    run = engine.store.load_run(run_id)
                    affected_job_ids.add(run.job_id)
                except KeyError:
                    pass

            for job_id in affected_job_ids:
                engine._dispatch_ready(job_id)
                engine._check_job_terminal(job_id)
                _notify_change(job_id)

        except asyncio.CancelledError:
            break
        except Exception:
            logger.error("Error in process health check", exc_info=True)
            await asyncio.sleep(interval)


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


async def _flow_source_watcher() -> None:
    """Poll flow source files for active jobs and broadcast changes via WebSocket."""
    engine = _get_engine()
    while True:
        try:
            changed_job_ids: list[str] = []
            active = engine.store.active_jobs()
            tracked_paths: set[str] = set()
            for job in active:
                sp = getattr(job.workflow, "source_path", None)
                if not sp:
                    continue
                tracked_paths.add(sp)
                try:
                    mtime = os.path.getmtime(sp)
                except OSError:
                    continue
                prev = _flow_mtimes.get(sp)
                if prev is None:
                    _flow_mtimes[sp] = mtime
                    continue
                if mtime != prev:
                    _flow_mtimes[sp] = mtime
                    changed_job_ids.append(job.id)

            if changed_job_ids:
                await _broadcast({
                    "type": "flow_source_changed",
                    "job_ids": changed_job_ids,
                    "timestamp": _now().isoformat(),
                })

            # Clean up tracking for paths no longer active
            for sp in list(_flow_mtimes):
                if sp not in tracked_paths:
                    del _flow_mtimes[sp]

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
            # Containment-VM runs: the stored pid is a guest pid; we
            # can't probe it via os.kill on the host. vmmd is a
            # separate daemon that survives server restarts, so the
            # VM itself may still be running. Leave these alone for
            # the engine's reattach_surviving_runs logic to handle.
            if run.executor_state and run.executor_state.get("in_vm"):
                logger.info(
                    "Skipping in-VM run %s (job %s step %s) — containment VM survives server restart",
                    run.id, job.id, run.step_name,
                )
                continue
            # Check if the agent process is still alive
            pid = run.pid or (run.executor_state or {}).get("pid")
            if pid:
                try:
                    os.kill(pid, 0)
                    pid_alive = True
                except (ProcessLookupError, PermissionError):
                    pid_alive = False
                if pid_alive:
                    logger.info(
                        "Step run %s (job %s step %s) PID %d verified alive, leaving for reattach",
                        run.id, job.id, run.step_name, pid,
                    )
                    continue
                else:
                    logger.info(
                        "Step run %s (job %s step %s) PID %d dead or recycled, marking failed",
                        run.id, job.id, run.step_name, pid,
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
            run.error = f"Agent process died (PID {pid} not found on restart)" if pid else "Server restarted: step was orphaned"
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

    # ── PID-file guard: prevent duplicate server processes ──
    dot_dir = _project_dir / ".stepwise"
    dot_dir.mkdir(parents=True, exist_ok=True)
    _port = int(os.environ.get("STEPWISE_PORT", "8341"))
    from stepwise.server_detect import acquire_pidfile_guard, ServerAlreadyRunning
    try:
        acquire_pidfile_guard(dot_dir, _port)
    except ServerAlreadyRunning as e:
        logger.error("Cannot start: %s", e)
        raise SystemExit(1) from e

    store = ThreadSafeStore(db_path)

    from stepwise.registry_factory import create_default_registry
    config = load_config(_project_dir)
    registry = create_default_registry(config)

    # Inject config API keys into process environment so agent subprocesses
    # (spawned via _expand_env_refs in agent_registry) can resolve ${VAR} refs.
    if config.openrouter_api_key and not os.environ.get("OPENROUTER_API_KEY"):
        os.environ["OPENROUTER_API_KEY"] = config.openrouter_api_key
    if config.anthropic_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = config.anthropic_api_key

    # Ensure server.log exists regardless of how the server was started
    # (foreground, systemd, etc.).  server_bg.py already sets up a handler
    # for --detach mode, so skip if one is already present.
    _setup_file_logging(dot_dir)

    _engine = AsyncEngine(store, registry, jobs_dir=jobs_dir, project_dir=dot_dir if dot_dir.is_dir() else None, billing_mode=config.billing, config=config, max_concurrent_jobs=config.max_concurrent_jobs)

    # Fail zombie jobs: server-owned jobs left in running/pending from a dead process
    _cleanup_zombie_jobs(store)
    # Auto-adopt CLI-owned jobs with stale heartbeats (>120s) — their runner is gone
    adopted = _auto_adopt_stale_cli_jobs(_engine, max_age_seconds=120)
    # Drain dead-process reaping synchronously before HTTP starts accepting.
    # The periodic _process_health_check task runs reap_dead_processes every 15s,
    # which can race with the first inbound create_job for the SQLite write lock
    # and wedge the request. Running it eagerly here ensures the cold-start backlog
    # is cleared before any client request arrives. Catches dead PIDs that
    # _cleanup_zombie_jobs skipped (jobs with suspended steps) and dead PIDs in
    # newly-adopted CLI jobs.
    from stepwise.process_lifecycle import reap_dead_processes as _reap
    try:
        _reaped = _reap(store, engine=None)
        if _reaped:
            logger.info("Startup reap: cleaned %d dead-PID run(s)", len(_reaped))
    except Exception:
        logger.error("Startup reap_dead_processes failed", exc_info=True)
    # Re-evaluate surviving RUNNING jobs (settle any that completed pre-crash).
    # This also covers newly adopted jobs since they're now server-owned.
    _engine.recover_jobs()

    # Reattach monitoring for agent steps that survived the restart
    reattached = await _engine.reattach_surviving_runs()
    if reattached:
        logger.info("Reattached %d surviving step run(s) from previous server", reattached)

    # Kick PENDING jobs that are ready to start. recover_jobs() already calls
    # _start_queued_jobs(), but by that point reattach_surviving_runs() hasn't
    # registered surviving executor tasks yet. Re-evaluating here ensures PENDING
    # jobs are dispatched with full knowledge of the engine's actual state.
    _engine._start_queued_jobs()

    # Register in global server registry
    from stepwise.server_detect import register_server, unregister_server
    register_server(
        project_path=str(_project_dir),
        pid=os.getpid(),
        port=_port,
        url=f"http://localhost:{_port}",
    )

    _engine.on_broadcast = _schedule_broadcast
    _engine.on_event = _schedule_event_stream
    # Set event loop reference BEFORE starting the engine task — _launch()
    # may be called from request handlers before run() gets its first iteration.
    _engine._loop = asyncio.get_running_loop()
    _engine_task = asyncio.create_task(_engine.run())
    _stream_monitor = asyncio.create_task(_agent_stream_monitor())
    global _script_monitor_task
    _script_monitor_task = asyncio.create_task(_script_stream_monitor())
    _observer = asyncio.create_task(_observe_external_jobs())
    _source_watcher = asyncio.create_task(_flow_source_watcher())
    _health_checker = asyncio.create_task(_process_health_check())

    # ── Scheduler Service ──
    global _scheduler
    from stepwise.scheduler import SchedulerService

    async def _create_and_start_scheduled_job(
        flow_path: str = None,
        inputs: dict = None,
        name: str = None,
        metadata: dict = None,
        staged: bool = False,
        job_id: str = None,
        start_only: bool = False,
    ) -> str:
        """Bridge between scheduler and engine for job creation."""
        if start_only and job_id:
            _engine.start_job(job_id)
            return job_id

        from stepwise.flow_resolution import resolve_flow
        flow_file = resolve_flow(flow_path, _project_dir)

        from stepwise.yaml_loader import load_workflow_yaml
        workflow = load_workflow_yaml(flow_file)

        job = _engine.create_job(
            objective=name or f"Scheduled: {flow_path}",
            workflow=workflow,
            inputs=inputs,
            name=name,
            metadata=metadata,
        )
        if not staged:
            _engine.start_job(job.id)
        return job.id

    _scheduler = SchedulerService(store=store, project_dir=str(_project_dir))
    await _scheduler.start(_create_and_start_scheduled_job)

    # Wire engine → scheduler completion hook (for queue overlap policy)
    def _on_job_completed(job_id: str) -> None:
        if _scheduler and _event_loop:
            _event_loop.call_soon_threadsafe(
                _event_loop.create_task,
                _scheduler.on_job_completed(job_id),
            )
    _engine.on_job_completed = _on_job_completed

    yield

    # Stop scheduler
    if _scheduler:
        await _scheduler.stop()

    # Cancel all stream tailer tasks
    for task in _stream_tasks.values():
        task.cancel()
    _stream_tasks.clear()

    # Cancel script stream tailers
    for task in _script_stream_tasks.values():
        task.cancel()
    _script_stream_tasks.clear()

    # Cancel script stream monitor
    if _script_monitor_task:
        _script_monitor_task.cancel()
        try:
            await _script_monitor_task
        except asyncio.CancelledError:
            pass

    _health_checker.cancel()
    try:
        await _health_checker
    except asyncio.CancelledError:
        pass

    _source_watcher.cancel()
    try:
        await _source_watcher
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
    from stepwise.server_detect import remove_pidfile
    remove_pidfile(dot_dir)


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
def list_jobs(request: Request, status: str | None = None, top_level: bool = False, limit: int = 50, include_archived: bool = False, include_total: bool = False, group: str | None = None):
    engine = _get_engine()
    # If filtering by group, use the dedicated method
    if group:
        jobs = engine.store.jobs_in_group(group)
        if status:
            try:
                job_status = JobStatus(status)
            except ValueError:
                valid = [s.value for s in JobStatus]
                raise HTTPException(status_code=400, detail=f"Invalid status '{status}'. Valid: {valid}")
            jobs = [j for j in jobs if j.status == job_status]
        cost_map = engine.store.batch_job_costs([j.id for j in jobs])
        lookups = _build_summary_lookups(engine.store, jobs)
        serialized = [
            _serialize_job(j, summary=True, cost_usd=cost_map.get(j.id, 0.0), **lookups)
            for j in jobs
        ]
        if include_total:
            return {"jobs": serialized, "total": len(serialized)}
        return serialized
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
        include_archived=include_archived,
    )
    cost_map = engine.store.batch_job_costs([j.id for j in jobs])
    lookups = _build_summary_lookups(engine.store, jobs)
    serialized = [
        _serialize_job(j, summary=True, cost_usd=cost_map.get(j.id, 0.0), **lookups)
        for j in jobs
    ]
    if include_total:
        # COUNT(*) instead of loading every matching row — the old
        # `len(all_jobs(limit=0))` triggered a SECOND full N+1 just
        # to compute the number.
        total = engine.store.count_jobs(
            job_status, top_level_only=top_level,
            meta_filters=meta_filters or None,
            include_archived=include_archived,
        )
        return {"jobs": serialized, "total": total}
    return serialized


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
                wf = load_workflow_yaml(abs_path)
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


@app.get("/api/jobs/{job_id}/live-source")
def get_live_source(job_id: str):
    """Re-read the flow YAML from disk and return current step definitions."""
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    engine = _get_engine()
    job = engine.store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    source_path = getattr(job.workflow, "source_path", None)
    if not source_path:
        raise HTTPException(status_code=404, detail="No source path for this job")
    p = Path(source_path)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="Flow file no longer exists on disk")
    try:
        wf = load_workflow_yaml(p)
    except (YAMLLoadError, Exception) as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse flow file: {e}")
    return {
        "steps": {name: step.to_dict() for name, step in wf.steps.items()},
        "mtime": os.path.getmtime(source_path),
    }


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
def get_job(job_id: str, summary: bool = False):
    """Return a single job. With `?summary=true` the response shape
    matches the list endpoint (current_step, has_suspended_steps,
    completed_steps, lightweight workflow, cost_usd) so the web UI's
    React Query cache for `["jobs", ...]` lists can splice this
    response in directly without a full list refetch when a tick
    arrives. Default `summary=false` keeps the legacy full-job
    response for callers that want the verbose to_dict() shape.
    """
    engine = _get_engine()
    try:
        job = engine.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if summary:
        # Compute the same summary fields the list endpoint uses,
        # using the per-job (non-batched) fallbacks since this is a
        # single-job call. cost_usd via batch helper for one id.
        cost = engine.store.batch_job_costs([job.id]).get(job.id, 0.0)
        return _serialize_job(job, summary=True, cost_usd=cost)
    return _serialize_job(job)


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


@app.post("/api/jobs/{job_id}/retry-failed")
def retry_failed_steps(job_id: str):
    """Retry every failed/cancelled step in this job and its
    descendants. Sets touched jobs back to RUNNING and re-launches
    failed step runs via rerun_step semantics. For delegated for_each
    parents whose sub-jobs failed, recurses into the sub-jobs and
    resets the parent's failed delegated run to DELEGATED so the
    completion handler re-evaluates when sub-jobs settle.

    Returns counts: {jobs_resumed, steps_rerun, delegated_reset}.
    """
    engine = _get_engine()
    try:
        counts = engine.retry_failed_steps(job_id)
        _notify_change(job_id)
        return {"status": "retrying", **counts}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
    jobs = engine.store.all_jobs(include_archived=True)
    for job in jobs:
        engine.store.delete_job(job.id)
    if _event_loop is not None:
        _event_loop.call_soon_threadsafe(
            _event_loop.create_task,
            _broadcast({"type": "jobs_changed"}),
        )
    return {"status": "deleted", "count": len(jobs)}


# ── Archive & bulk operations ───────────────────────────────────────


class ArchiveRequest(BaseModel):
    job_ids: list[str] | None = None
    status: str | None = None
    group: str | None = None


class UnarchiveRequest(BaseModel):
    job_ids: list[str]


class BulkDeleteRequest(BaseModel):
    job_ids: list[str] | None = None
    status: str | None = None
    group: str | None = None
    archived: bool = False


@app.post("/api/jobs/{job_id}/archive")
def archive_single_job(job_id: str):
    engine = _get_engine()
    try:
        job = engine.store.load_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    terminal = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
    if job.status not in terminal:
        raise HTTPException(status_code=400, detail=f"Can only archive terminal jobs (job is {job.status.value})")
    engine.store.archive_job(job_id)
    _notify_change(job_id)
    return {"status": "archived"}


@app.post("/api/jobs/{job_id}/unarchive")
def unarchive_single_job(job_id: str):
    engine = _get_engine()
    try:
        job = engine.store.load_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if job.status != JobStatus.ARCHIVED:
        raise HTTPException(status_code=400, detail=f"Job is not archived (status: {job.status.value})")
    engine.store.unarchive_job(job_id)
    _notify_change(job_id)
    return {"status": "unarchived"}


@app.post("/api/jobs/archive")
def archive_jobs(req: ArchiveRequest):
    engine = _get_engine()
    terminal = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
    to_archive = []

    if req.job_ids:
        for jid in req.job_ids:
            try:
                job = engine.store.load_job(jid)
            except KeyError:
                raise HTTPException(status_code=404, detail=f"Job not found: {jid}")
            if job.status not in terminal:
                raise HTTPException(status_code=400, detail=f"Can only archive terminal jobs (job {jid} is {job.status.value})")
            to_archive.append(job)
    elif req.status:
        try:
            status = JobStatus(req.status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {req.status}")
        if status not in terminal:
            raise HTTPException(status_code=400, detail="Can only archive terminal statuses")
        to_archive = engine.store.all_jobs(status=status, top_level_only=True)
    elif req.group:
        all_jobs = engine.store.all_jobs(top_level_only=True)
        to_archive = [j for j in all_jobs if j.job_group == req.group and j.status in terminal]
    else:
        raise HTTPException(status_code=400, detail="Specify job_ids, status, or group")

    for job in to_archive:
        engine.store.archive_job(job.id)

    if to_archive and _event_loop is not None:
        _event_loop.call_soon_threadsafe(
            _event_loop.create_task,
            _broadcast({"type": "jobs_changed"}),
        )
    return {"count": len(to_archive), "archived": [j.id for j in to_archive]}


@app.post("/api/jobs/unarchive")
def unarchive_jobs(req: UnarchiveRequest):
    engine = _get_engine()
    restored = []
    for jid in req.job_ids:
        try:
            job = engine.store.load_job(jid)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Job not found: {jid}")
        if job.status != JobStatus.ARCHIVED:
            raise HTTPException(status_code=400, detail=f"Job {jid} is not archived")
        engine.store.unarchive_job(jid)
        restored.append(jid)

    if restored and _event_loop is not None:
        _event_loop.call_soon_threadsafe(
            _event_loop.create_task,
            _broadcast({"type": "jobs_changed"}),
        )
    return {"count": len(restored), "unarchived": restored}


@app.post("/api/jobs/bulk-delete")
def bulk_delete_jobs(req: BulkDeleteRequest):
    engine = _get_engine()
    active = {JobStatus.RUNNING, JobStatus.PENDING}
    to_delete = []

    if req.job_ids:
        for jid in req.job_ids:
            try:
                job = engine.store.load_job(jid)
            except KeyError:
                raise HTTPException(status_code=404, detail=f"Job not found: {jid}")
            if job.status in active:
                raise HTTPException(status_code=400, detail=f"Cannot delete active job {jid}. Cancel it first.")
            to_delete.append(job)
    elif req.archived:
        to_delete = engine.store.all_jobs(status=JobStatus.ARCHIVED, top_level_only=True)
    elif req.status:
        try:
            status = JobStatus(req.status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {req.status}")
        if status in active:
            raise HTTPException(status_code=400, detail="Cannot bulk-delete active jobs")
        to_delete = engine.store.all_jobs(status=status, top_level_only=True)
    elif req.group:
        all_jobs = engine.store.all_jobs(top_level_only=True, include_archived=True)
        to_delete = [j for j in all_jobs if j.job_group == req.group and j.status not in active]
    else:
        raise HTTPException(status_code=400, detail="Specify job_ids, status, group, or archived")

    for job in to_delete:
        engine.store.delete_job(job.id)

    if to_delete and _event_loop is not None:
        _event_loop.call_soon_threadsafe(
            _event_loop.create_task,
            _broadcast({"type": "jobs_changed"}),
        )
    return {"count": len(to_delete), "deleted": [j.id for j in to_delete]}


# ── Job staging & dependency endpoints ──────────────────────────────


class StageJobRequest(BaseModel):
    job_group: str | None = None


class RunGroupRequest(BaseModel):
    group: str
    max_concurrent: int | None = None


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


class PatchJobRequest(BaseModel):
    notify_url: str | None = None
    notify_context: dict | None = None


@app.patch("/api/jobs/{job_id}")
def patch_job(job_id: str, req: PatchJobRequest):
    """Update mutable fields on an existing job (notify_url, notify_context)."""
    engine = _get_engine()
    try:
        job = engine.store.load_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if req.notify_url is not None:
        job.notify_url = req.notify_url
    if req.notify_context is not None:
        job.notify_context = req.notify_context
    job.updated_at = _now()
    engine.store.save_job(job)
    return {"status": "updated", "job_id": job_id}


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
    if req.max_concurrent is not None:
        engine.store.set_group_max_concurrent(req.group, req.max_concurrent)
    job_ids = engine.store.transition_group_to_pending(req.group)
    engine._start_queued_jobs()
    for jid in job_ids:
        _notify_change(jid)
    return {"status": "pending", "group": req.group, "count": len(job_ids), "job_ids": job_ids}


# ── Group settings endpoints ─────────────────────────────────────────


class UpdateGroupRequest(BaseModel):
    max_concurrent: int


@app.get("/api/groups")
def list_groups():
    """List all known groups with settings and job counts.

    Pre-fix: called `all_jobs()` TWICE, each time loading 2500+ rows
    with full dependency hydration — just to build a group→count
    dict. Now reads job_group + status + parent_job_id directly in
    one query and aggregates in Python.
    """
    engine = _get_engine()
    settings = engine.store.list_group_settings()
    all_groups: dict[str, dict] = {}
    # Single SQL query, only the columns we actually need.
    rows = engine.store._conn.execute(
        "SELECT job_group, status, parent_job_id FROM jobs "
        "WHERE job_group IS NOT NULL"
    ).fetchall()
    for row in rows:
        grp = row["job_group"]
        if not grp:
            continue
        g = all_groups.setdefault(
            grp,
            {
                "group": grp,
                "max_concurrent": 0,
                "active_count": 0,
                "pending_count": 0,
                "total_count": 0,
            },
        )
        g["total_count"] += 1
        st = row["status"]
        if st == JobStatus.RUNNING.value and not row["parent_job_id"]:
            g["active_count"] += 1
        elif st == JobStatus.PENDING.value:
            g["pending_count"] += 1
    for grp, limit in settings.items():
        g = all_groups.setdefault(
            grp,
            {
                "group": grp,
                "max_concurrent": 0,
                "active_count": 0,
                "pending_count": 0,
                "total_count": 0,
            },
        )
        g["max_concurrent"] = limit
    return list(all_groups.values())


@app.get("/api/groups/{group}")
def get_group(group: str):
    """Get settings and counts for a single group."""
    engine = _get_engine()
    max_concurrent = engine.store.get_group_max_concurrent(group)
    jobs = engine.store.jobs_in_group(group)
    active = sum(1 for j in jobs if j.status == JobStatus.RUNNING and not j.parent_job_id)
    pending = sum(1 for j in jobs if j.status == JobStatus.PENDING)
    return {"group": group, "max_concurrent": max_concurrent,
            "active_count": active, "pending_count": pending, "total_count": len(jobs)}


@app.patch("/api/groups/{group}")
def update_group(group: str, req: UpdateGroupRequest):
    """Update group concurrency limit. Re-evaluates queued jobs immediately."""
    engine = _get_engine()
    engine.store.set_group_max_concurrent(group, req.max_concurrent)
    engine._start_queued_jobs()
    if _event_loop is not None:
        _event_loop.call_soon_threadsafe(
            _event_loop.create_task,
            _broadcast({"type": "group_changed", "group": group}),
        )
    return {"group": group, "max_concurrent": req.max_concurrent}


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


@app.get("/api/jobs/{job_id}/children")
def get_children(job_id: str):
    engine = _get_engine()
    try:
        children = engine.store.child_jobs(job_id)
        return [j.to_dict() for j in children]
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")


# ── Workspace browser ────────────────────────────────────────────────
# Browse files the job wrote to its workspace. Used by the Workspace
# tab in the job view so users can see what ended up on disk.

_WORKSPACE_MAX_LIST_ENTRIES = 2000
_WORKSPACE_MAX_READ_BYTES = 512 * 1024  # 512 KB cap per read


def _job_workspace_root(job_id: str) -> Path:
    engine = _get_engine()
    try:
        job = engine.store.load_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    ws = job.workspace_path or str(
        Path(engine.jobs_dir) / job_id / "workspace"
    )
    root = Path(ws).resolve()
    return root


def _resolve_workspace_path(root: Path, rel: str) -> Path:
    """Resolve a user-provided relative path against the workspace root,
    rejecting traversal (..) and absolute paths."""
    if not rel:
        return root
    # Reject absolute paths and explicit '..' segments outright.
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="path escapes workspace")
    return candidate


@app.get("/api/jobs/{job_id}/workspace")
def get_job_workspace_listing(job_id: str, path: str = ""):
    """Return a flat listing of the job's workspace under `path`.

    Entries are sorted dir-first, then by name. Each entry has:
      - name, path (relative to workspace root), is_dir, size (bytes)
    Hidden dotfiles and __pycache__ are omitted by default.
    """
    root = _job_workspace_root(job_id)
    if not root.exists():
        return {"root": str(root), "exists": False, "entries": []}
    target = _resolve_workspace_path(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"path not found: {path}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"path is a file, use /file endpoint: {path}")

    entries: list[dict[str, Any]] = []
    try:
        for entry in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name)):
            name = entry.name
            # Hide Python/OS noise by default.
            if name in ("__pycache__", ".DS_Store"):
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            rel = str(entry.resolve().relative_to(root))
            entries.append({
                "name": name,
                "path": rel,
                "is_dir": entry.is_dir(),
                "size": stat.st_size if entry.is_file() else None,
                "modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=_tz.utc,
                ).isoformat(),
            })
            if len(entries) >= _WORKSPACE_MAX_LIST_ENTRIES:
                break
    except PermissionError:
        raise HTTPException(status_code=403, detail=f"permission denied: {path}")

    return {
        "root": str(root),
        "exists": True,
        "path": str(target.resolve().relative_to(root)) if target != root else "",
        "entries": entries,
        "truncated": len(entries) >= _WORKSPACE_MAX_LIST_ENTRIES,
    }


@app.get("/api/jobs/{job_id}/workspace/file")
def get_job_workspace_file(job_id: str, path: str):
    """Return the contents of a single workspace file.

    Caps at _WORKSPACE_MAX_READ_BYTES; binary files get a truncation
    marker instead of decoded text. Clients can use this for previewing
    artifacts agents produced during the run.
    """
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    root = _job_workspace_root(job_id)
    target = _resolve_workspace_path(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"file not found: {path}")
    if target.is_dir():
        raise HTTPException(status_code=400, detail=f"path is a directory, use listing endpoint: {path}")

    size = target.stat().st_size
    truncated = size > _WORKSPACE_MAX_READ_BYTES
    raw = target.read_bytes()[:_WORKSPACE_MAX_READ_BYTES]
    try:
        content = raw.decode("utf-8")
        is_binary = False
    except UnicodeDecodeError:
        content = None
        is_binary = True

    return {
        "path": str(target.resolve().relative_to(root)),
        "size": size,
        "truncated": truncated,
        "is_binary": is_binary,
        "content": content,
    }


@app.post("/api/jobs/{job_id}/steps/{step_name}/rerun")
def rerun_step(job_id: str, step_name: str):
    engine = _get_engine()
    try:
        run = engine.rerun_step(job_id, step_name)
        _notify_change(job_id)
        return run.to_dict()
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/runs/{run_id}/poll-now")
def trigger_poll_now(run_id: str):
    """Reset next_check_at so the engine's next poll cycle picks up this step immediately."""
    from datetime import timezone
    engine = _get_engine()
    run = engine.store.load_run(run_id)
    if run.status != StepRunStatus.SUSPENDED:
        raise HTTPException(status_code=400, detail="Run is not suspended")
    if not run.watch or run.watch.mode != "poll":
        raise HTTPException(status_code=400, detail="Run is not a poll step")
    # Reset next_check_at to trigger immediate poll
    es = run.executor_state or {}
    watch_state = es.get("_watch", {})
    watch_state["next_check_at"] = datetime.now(timezone.utc).isoformat()
    es["_watch"] = watch_state
    run.executor_state = es
    engine.store.save_run(run)
    _notify_change(run.job_id)
    return {"status": "triggered", "run_id": run_id}


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
    """Get accumulated cost for a run from step events or executor meta."""
    engine = _get_engine()
    try:
        run = engine.store.load_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    cost = engine._run_cost(run)
    return {"run_id": run_id, "cost_usd": cost, "billing_mode": engine.billing_mode or "api_key"}


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


def _prompt_event_for_run(run) -> dict | None:
    """If the run has a persisted interpolated prompt (agent step),
    return a synthesized `{t: "prompt", text: ...}` stream event.

    Rationale: the ACP NDJSON only has the `session/prompt` method
    call when stepwise's adapter drives it — but stepwise writes
    NDJSON by tailing the adapter's stdout, so the outgoing prompt
    (sent via JSON-RPC request) doesn't land in the file.
    `_interpolated_config.prompt` captures the exact text we sent,
    so we inject it into the event stream for the frontend to
    render via PromptSegmentRow (FadedText) at the top of the
    session. Applies to both live and completed runs.
    """
    state = run.executor_state or {}
    ic = state.get("_interpolated_config") or {}
    prompt = ic.get("prompt")
    if isinstance(prompt, str) and prompt:
        return {"t": "prompt", "text": prompt}
    return None


@app.get("/api/runs/{run_id}/agent-output")
def get_agent_output(run_id: str):
    """Get condensed agent output events for a completed run."""
    engine = _get_engine()
    try:
        run = engine.store.load_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    output_path = (run.executor_state or {}).get("output_path")
    events: list[dict] = []
    prompt_event = _prompt_event_for_run(run)
    if prompt_event:
        events.append(prompt_event)
    if not output_path:
        return {"events": events}
    try:
        with open(output_path) as f:
            raw = f.read()
        events.extend(_parse_ndjson_events(raw))
    except FileNotFoundError:
        pass
    return {"events": events}


@app.get("/api/runs/{run_id}/script-output")
def get_script_output(run_id: str, stdout_offset: int = 0, stderr_offset: int = 0):
    """Get script stdout/stderr content, supporting offset-based tailing."""
    engine = _get_engine()
    try:
        run = engine.store.load_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    state = run.executor_state or {}
    stdout_path = state.get("stdout_path")
    stderr_path = state.get("stderr_path")

    def _read_from(path: str | None, offset: int) -> tuple[str, int]:
        if not path:
            return "", offset
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read()
                new_offset = f.tell()
            return data.decode("utf-8", errors="replace"), new_offset
        except FileNotFoundError:
            return "", offset

    stdout, new_stdout_offset = _read_from(stdout_path, stdout_offset)
    stderr, new_stderr_offset = _read_from(stderr_path, stderr_offset)

    return {
        "stdout": stdout,
        "stderr": stderr,
        "stdout_offset": new_stdout_offset,
        "stderr_offset": new_stderr_offset,
    }


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
    return {"job_id": job_id, "cost_usd": round(cost, 4) if cost else 0, "billing_mode": engine.billing_mode or "api_key"}


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


@app.get("/api/jobs/{job_id}/sessions")
def get_job_sessions(job_id: str):
    """List agent sessions for a job, grouped by executor_state.session_name."""
    engine = _get_engine()
    try:
        engine.store.load_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    runs = engine.store.runs_for_job(job_id)
    sessions: dict[str, dict] = {}
    for run in runs:
        sname = (run.executor_state or {}).get("session_name")
        if not sname:
            continue
        if sname not in sessions:
            sessions[sname] = {
                "session_name": sname,
                "run_ids": [],
                "step_names": [],
                "is_active": False,
                "started_at": None,
                "latest_at": None,
                "total_tokens": 0,
            }
        s = sessions[sname]
        s["run_ids"].append(run.id)
        if run.step_name not in s["step_names"]:
            s["step_names"].append(run.step_name)
        if run.status == StepRunStatus.RUNNING:
            s["is_active"] = True
        # Extract token usage from the output NDJSON's last usage_update
        output_path = (run.executor_state or {}).get("output_path")
        if output_path:
            try:
                with open(output_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            update = data.get("params", {}).get("update", {})
                            if update.get("sessionUpdate") == "usage_update":
                                used = update.get("used", 0)
                                if used:
                                    s["total_tokens"] = max(s.get("total_tokens", 0), used)
                        except (json.JSONDecodeError, KeyError):
                            pass
            except (FileNotFoundError, OSError):
                pass
        ts = run.started_at.isoformat() if run.started_at else None
        if ts and (not s["started_at"] or ts < s["started_at"]):
            s["started_at"] = ts
        end_ts = run.completed_at or run.started_at
        end_iso = end_ts.isoformat() if end_ts else None
        if end_iso and (not s["latest_at"] or end_iso > s["latest_at"]):
            s["latest_at"] = end_iso
    return {"sessions": sorted(sessions.values(), key=lambda x: x["started_at"] or "")}


@app.get("/api/jobs/{job_id}/sessions/{session_name:path}/transcript")
def get_session_transcript(job_id: str, session_name: str):
    """Get concatenated agent output events for a session with step boundary markers."""
    engine = _get_engine()
    try:
        engine.store.load_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    runs = engine.store.runs_for_job(job_id)
    session_runs = [
        r for r in runs
        if (r.executor_state or {}).get("session_name") == session_name
    ]
    if not session_runs:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_name}")
    session_runs.sort(key=lambda r: r.started_at or datetime.min)

    all_events: list[dict] = []
    boundaries: list[dict] = []
    prev_session_tokens = 0
    for run in session_runs:
        boundaries.append({
            "event_index": len(all_events),
            "step_name": run.step_name,
            "attempt": run.attempt,
            "run_id": run.id,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "status": run.status.value,
            "tokens_used": 0,
        })
        # Prepend the interpolated prompt as a stream event for each
        # step in the session so the faded-prompt panel renders at
        # the top of every step's output in the Session tab. Without
        # this the transcript shows only the agent's responses.
        prompt_event = _prompt_event_for_run(run)
        if prompt_event:
            all_events.append(prompt_event)
        output_path = (run.executor_state or {}).get("output_path")
        if output_path:
            try:
                with open(output_path) as f:
                    raw = f.read()
                # Extract per-run token count (cumulative in usage events, compute delta)
                run_max_tokens = 0
                for ndjson_line in raw.strip().split("\n"):
                    if not ndjson_line.strip():
                        continue
                    try:
                        d = json.loads(ndjson_line)
                        upd = d.get("params", {}).get("update", {})
                        if upd.get("sessionUpdate") == "usage_update":
                            run_max_tokens = max(run_max_tokens, upd.get("used", 0))
                    except (json.JSONDecodeError, KeyError):
                        pass
                boundaries[-1]["tokens_used"] = max(0, run_max_tokens - prev_session_tokens)
                prev_session_tokens = run_max_tokens
                all_events.extend(_parse_ndjson_events(raw))
            except FileNotFoundError:
                pass
    return {"events": all_events, "boundaries": boundaries}


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


@app.get("/api/servers")
def list_servers():
    """List all running Stepwise servers across all projects."""
    from stepwise.server_detect import list_active_servers, read_pidfile
    servers = list_active_servers()
    pid_data = read_pidfile(_project_dir / ".stepwise") if _project_dir else {}
    return {
        "servers": servers,
        "current": pid_data.get("url"),
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
    # Count instead of loading. The web UI polls /api/status; loading
    # every row (with its workflow JSON + batched dependencies) just
    # to call len() on the list was a multi-second operation on live
    # DBs and blocked the same SQLite connection other endpoints need.
    total_jobs = engine.store.count_jobs(include_archived=True)
    active = engine.store.active_jobs()
    from importlib.metadata import version
    try:
        ver = version("stepwise-run")
    except Exception:
        ver = "unknown"
    return {
        "active_jobs": len(active),
        "total_jobs": total_jobs,
        "registered_executors": list(engine.registry._factories.keys()),
        "cwd": os.getcwd(),
        "version": ver,
    }


@app.get("/api/changelog")
def get_changelog():
    """Return the CHANGELOG.md content as plain text."""
    from fastapi.responses import PlainTextResponse

    # Try bundled changelog first (installed package)
    bundled = Path(__file__).parent / "_changelog.md"
    if bundled.is_file():
        return PlainTextResponse(bundled.read_text(encoding="utf-8"))

    # Try source tree (editable installs / development)
    source_changelog = Path(__file__).parent.parent.parent / "CHANGELOG.md"
    if source_changelog.is_file():
        return PlainTextResponse(source_changelog.read_text(encoding="utf-8"))

    # Fallback: try to find via dist-info
    try:
        from importlib.metadata import distribution
        import json as _json
        dist = distribution("stepwise-run")
        for f in dist.files or []:
            if f.name == "direct_url.json":
                url_path = Path(dist.locate_file(f))
                data = _json.loads(url_path.read_text())
                url = data.get("url", "")
                if url.startswith("file://"):
                    candidate = Path(url[7:]) / "CHANGELOG.md"
                    if candidate.is_file():
                        return PlainTextResponse(candidate.read_text(encoding="utf-8"))
                break
    except Exception:
        pass

    raise HTTPException(status_code=404, detail="CHANGELOG.md not found")


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


class UpdateContainmentRequest(BaseModel):
    # Project-wide default containment for agent steps. None disables
    # containment by default; "cloud-hypervisor" makes every agent step
    # contained unless overridden at agent / flow / step / CLI level.
    containment: str | None = None


class UpdateMaxConcurrentJobsRequest(BaseModel):
    # Global job-queue cap. AsyncEngine.max_concurrent_jobs — how many
    # jobs can be RUNNING at once across the whole engine. Distinct
    # from max_concurrent_by_executor (per-step-type) and
    # max_concurrent_by_agent (per-agent-name). 0 = use default (10).
    limit: int


class UpdateAgentProcessTtlRequest(BaseModel):
    # Safety net for zombie agent subprocesses. 0 = no timeout.
    # Positive values kill agent subprocesses older than this many
    # seconds during the reap sweep.
    ttl_seconds: int


class UpdateAgentPermissionsRequest(BaseModel):
    # Project-wide agent approval policy. "approve_all" (default) lets
    # every tool call through without prompting. "prompt" and "deny"
    # are defined in the config model but not yet fully enforced in
    # the engine — see docs/executors.md for the current behavior.
    permissions: str  # "approve_all" | "prompt" | "deny"


class UpdateNotifyWebhookRequest(BaseModel):
    # Webhook URL called on job lifecycle events. None / empty clears.
    # notify_context is sent as a constant payload prefix — useful for
    # routing (Slack channel name, target team, etc.). Arbitrary JSON.
    url: str | None = None
    context: dict | None = None


@app.get("/api/config")
def get_config():
    cs = load_config_with_sources(_project_dir)
    cfg = cs.config
    enriched = enrich_registry(cfg.model_registry)
    return {
        "has_api_key": bool(cfg.openrouter_api_key),
        "has_anthropic_key": bool(cfg.anthropic_api_key),
        "openrouter_api_key": cfg.openrouter_api_key or "",
        "anthropic_api_key": cfg.anthropic_api_key or "",
        "api_key_source": cs.api_key_source,
        "model_registry": [m.to_dict() for m in enriched],
        "default_model": cfg.default_model,
        "default_agent": cfg.default_agent,
        "labels": [li.to_dict() for li in cs.label_info],
        "billing_mode": cfg.billing,
        "agent_containment": cfg.agent_containment,
        "agent_permissions": cfg.agent_permissions,
        "agent_process_ttl": cfg.agent_process_ttl,
        "max_concurrent_jobs": cfg.max_concurrent_jobs,
        "notify_url": cfg.notify_url,
        "notify_context": cfg.notify_context,
        "concurrency_limits": cfg.resolved_executor_limits(),
        "concurrency_running": {
            t: sum(1 for v in _engine._task_exec_types.values() if v == t)
            for t in set(_engine._task_exec_types.values())
        } if _engine and hasattr(_engine, "_task_exec_types") else {},
        "agent_concurrency_limits": dict(cfg.max_concurrent_by_agent),
        "agent_concurrency_running": {
            n: sum(1 for v in _engine._task_agent_names.values() if v == n)
            for n in set(_engine._task_agent_names.values())
        } if _engine and hasattr(_engine, "_task_agent_names") else {},
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


class UpdateAgentConcurrencyRequest(BaseModel):
    agent: str  # agent NAME (claude, codex, aloop, ...)
    limit: int  # 0 = remove per-agent cap (fall back to type cap only)


@app.put("/api/config/agent-concurrency")
def update_agent_concurrency_limit(req: UpdateAgentConcurrencyRequest):
    """Set max concurrent steps for a specific agent NAME.

    Per-agent caps are checked in ADDITION to the executor-type cap.
    A step is throttled if either cap is hit. Set 0 to remove the
    per-agent cap (the executor-type cap still applies).
    """
    if req.limit < 0:
        raise HTTPException(
            status_code=400, detail="Limit must be non-negative (0 = remove cap)",
        )
    if not req.agent:
        raise HTTPException(status_code=400, detail="agent name required")

    import yaml as yaml_lib
    path = _project_dir / ".stepwise" / "config.local.yaml"
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml_lib.safe_load(path.read_text()) or {}
    by_agent = data.get("max_concurrent_by_agent", {})
    if not isinstance(by_agent, dict):
        by_agent = {}
    if req.limit == 0:
        by_agent.pop(req.agent, None)
    else:
        by_agent[req.agent] = req.limit
    if by_agent:
        data["max_concurrent_by_agent"] = by_agent
    else:
        data.pop("max_concurrent_by_agent", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_lib.dump(data, default_flow_style=False, sort_keys=False))

    new_cfg = _reload_engine_config()
    return {
        "status": "updated",
        "agent_concurrency_limits": dict(new_cfg.max_concurrent_by_agent),
    }


@app.post("/api/config/reload")
def reload_config_endpoint():
    """Reload config from disk. Use after manual YAML edits."""
    cfg = _reload_engine_config()
    return {"status": "reloaded", "limits": cfg.resolved_executor_limits()}


_VALID_CONTAINMENT = {None, "cloud-hypervisor"}


@app.put("/api/config/containment")
def update_agent_containment_default(req: UpdateContainmentRequest):
    """Set the project-wide default containment for agent steps.

    Writes to .stepwise/config.local.yaml. Setting None clears the
    default (agent steps run unisolated unless flow/step/agent
    override). Per-agent overrides via PUT /api/agents/{name} take
    precedence over this default.
    """
    value = req.containment
    if value not in _VALID_CONTAINMENT:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid containment mode {value!r}. Allowed: null, 'cloud-hypervisor'.",
        )

    import yaml as yaml_lib
    path = _project_dir / ".stepwise" / "config.local.yaml"
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml_lib.safe_load(path.read_text()) or {}
    if value is None:
        data.pop("agent_containment", None)
    else:
        data["agent_containment"] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_lib.dump(data, default_flow_style=False, sort_keys=False))

    new_cfg = _reload_engine_config()
    return {"status": "updated", "agent_containment": new_cfg.agent_containment}


_VALID_AGENT_PERMISSIONS = {"approve_all", "prompt", "deny"}


def _update_local_config_field(
    field_name: str,
    value: Any,
    remove_when: Callable[[Any], bool] = lambda v: v is None,
) -> None:
    """Helper: set/unset a top-level field in .stepwise/config.local.yaml.

    When ``remove_when(value)`` is truthy, the key is removed entirely
    rather than written as its sentinel value (None / 0 / "").
    """
    import yaml as yaml_lib
    path = _project_dir / ".stepwise" / "config.local.yaml"
    data: dict[str, Any] = {}
    if path.exists():
        data = yaml_lib.safe_load(path.read_text()) or {}
    if remove_when(value):
        data.pop(field_name, None)
    else:
        data[field_name] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_lib.dump(data, default_flow_style=False, sort_keys=False))


@app.put("/api/config/max-concurrent-jobs")
def update_max_concurrent_jobs(req: UpdateMaxConcurrentJobsRequest):
    """Set the global job-queue cap. 0 restores the default (10)."""
    if req.limit < 0:
        raise HTTPException(
            status_code=400, detail="Limit must be non-negative (0 = default)",
        )
    _update_local_config_field(
        "max_concurrent_jobs",
        req.limit,
        remove_when=lambda v: v == 0,
    )
    new_cfg = _reload_engine_config()
    # Propagate into the live engine immediately so new jobs pick up
    # the change without a restart.
    if _engine is not None:
        _engine.max_concurrent_jobs = new_cfg.max_concurrent_jobs
    return {
        "status": "updated",
        "max_concurrent_jobs": new_cfg.max_concurrent_jobs,
    }


@app.put("/api/config/agent-process-ttl")
def update_agent_process_ttl(req: UpdateAgentProcessTtlRequest):
    """Set the agent-subprocess zombie reap TTL. 0 disables."""
    if req.ttl_seconds < 0:
        raise HTTPException(
            status_code=400, detail="ttl_seconds must be non-negative (0 = disabled)",
        )
    _update_local_config_field(
        "agent_process_ttl",
        req.ttl_seconds,
        remove_when=lambda v: v == 0,
    )
    new_cfg = _reload_engine_config()
    return {
        "status": "updated",
        "agent_process_ttl": new_cfg.agent_process_ttl,
    }


@app.put("/api/config/agent-permissions")
def update_agent_permissions(req: UpdateAgentPermissionsRequest):
    """Set the project-wide agent approval policy."""
    if req.permissions not in _VALID_AGENT_PERMISSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid permissions {req.permissions!r}. "
                f"Allowed: {sorted(_VALID_AGENT_PERMISSIONS)}"
            ),
        )
    _update_local_config_field(
        "agent_permissions",
        req.permissions,
        remove_when=lambda v: v == "approve_all",
    )
    new_cfg = _reload_engine_config()
    return {
        "status": "updated",
        "agent_permissions": new_cfg.agent_permissions,
    }


@app.put("/api/config/notify-webhook")
def update_notify_webhook(req: UpdateNotifyWebhookRequest):
    """Set the job-lifecycle webhook URL + context payload prefix.

    Pass url=null or url="" to clear. notify_context is a free-form
    dict stored as-is and sent alongside every webhook call.
    """
    url = (req.url or "").strip() or None
    if url is not None and not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(
            status_code=400,
            detail="notify_url must start with http:// or https://",
        )

    _update_local_config_field(
        "notify_url",
        url,
        remove_when=lambda v: v is None,
    )
    _update_local_config_field(
        "notify_context",
        req.context or {},
        remove_when=lambda v: not v,
    )
    new_cfg = _reload_engine_config()
    return {
        "status": "updated",
        "notify_url": new_cfg.notify_url,
        "notify_context": new_cfg.notify_context,
    }


# ── Agent settings endpoints ──────────────────────────────────────────

import re as _re

_AGENT_NAME_RE = _re.compile(r"^[a-z][a-z0-9_-]{0,62}$")


class CreateAgentRequest(BaseModel):
    name: str
    command: list[str]
    config: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None
    containment: str | None = None
    disabled: bool = False


class UpdateAgentRequest(BaseModel):
    command: list[str] | None = None
    config: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None
    containment: str | None = None
    disabled: bool | None = None


def _read_local_agents_raw() -> dict:
    """Read the raw agents dict from config.local.yaml."""
    import yaml as yaml_lib
    path = _project_dir / ".stepwise" / "config.local.yaml"
    if not path.exists():
        return {}
    data = yaml_lib.safe_load(path.read_text()) or {}
    agents = data.get("agents", {})
    return agents if isinstance(agents, dict) else {}


def _reload_and_inject() -> StepwiseConfig:
    """Reload config and re-inject API keys into os.environ."""
    cfg = _reload_engine_config()
    if cfg.openrouter_api_key:
        os.environ["OPENROUTER_API_KEY"] = cfg.openrouter_api_key
    if cfg.anthropic_api_key:
        os.environ["ANTHROPIC_API_KEY"] = cfg.anthropic_api_key
    return cfg


@app.get("/api/agents")
def get_agents():
    """List all agents with metadata for the settings UI."""
    from stepwise.agent_registry import get_all_agents_with_metadata
    return {"agents": get_all_agents_with_metadata()}


@app.put("/api/agents/{name}")
def update_agent(name: str, req: UpdateAgentRequest):
    """Update an agent's config. For builtins, creates/updates an override."""
    from stepwise.agent_registry import BUILTIN_AGENTS

    is_builtin = name in BUILTIN_AGENTS
    local_agents = _read_local_agents_raw()

    if is_builtin:
        # Reject command changes for builtins
        if req.command is not None:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot change command for builtin agent '{name}'. Create a custom agent instead.",
            )
        # Build partial override
        override = local_agents.get(name, {})
        if not isinstance(override, dict):
            override = {}
        if req.disabled is not None:
            override["disabled"] = req.disabled
        if req.containment is not None:
            override["containment"] = req.containment
        if req.capabilities is not None:
            existing_caps = override.get("capabilities", {})
            if not isinstance(existing_caps, dict):
                existing_caps = {}
            existing_caps.update(req.capabilities)
            override["capabilities"] = existing_caps
        if req.config is not None:
            existing_config = override.get("config", {})
            if not isinstance(existing_config, dict):
                existing_config = {}
            for key_name, key_data in req.config.items():
                existing_config[key_name] = key_data
            override["config"] = existing_config
        local_agents[name] = override
    else:
        # Custom agent — must already exist in local config
        if name not in local_agents:
            raise HTTPException(status_code=404, detail=f"Agent '{name}' not found.")
        agent_data = local_agents[name]
        if not isinstance(agent_data, dict):
            agent_data = {}
        if req.command is not None:
            agent_data["command"] = req.command
        if req.disabled is not None:
            agent_data["disabled"] = req.disabled
        if req.containment is not None:
            agent_data["containment"] = req.containment
        if req.capabilities is not None:
            existing_caps = agent_data.get("capabilities", {})
            if not isinstance(existing_caps, dict):
                existing_caps = {}
            existing_caps.update(req.capabilities)
            agent_data["capabilities"] = existing_caps
        if req.config is not None:
            existing_config = agent_data.get("config", {})
            if not isinstance(existing_config, dict):
                existing_config = {}
            for key_name, key_data in req.config.items():
                existing_config[key_name] = key_data
            agent_data["config"] = existing_config
        local_agents[name] = agent_data

    save_agents_to_local_config(_project_dir, local_agents)
    _reload_and_inject()
    return {"status": "updated", "name": name}


@app.put("/api/agents/{name}/containment")
def update_agent_containment(name: str, req: UpdateContainmentRequest):
    """Set or clear a per-agent containment override.

    Distinct from PUT /api/agents/{name} because that endpoint treats
    `containment: null` as "don't touch", which makes it impossible to
    clear an existing override. Here `null` explicitly removes the
    override (agent falls back to the project-wide `agent_containment`
    default, which itself defaults to no containment).
    """
    from stepwise.agent_registry import BUILTIN_AGENTS

    if req.containment not in _VALID_CONTAINMENT:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid containment mode {req.containment!r}. Allowed: null, 'cloud-hypervisor'.",
        )

    is_builtin = name in BUILTIN_AGENTS
    local_agents = _read_local_agents_raw()
    entry = local_agents.get(name, {})
    if not isinstance(entry, dict):
        entry = {}

    if not is_builtin and name not in local_agents:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found.")

    if req.containment is None:
        entry.pop("containment", None)
    else:
        entry["containment"] = req.containment

    # If the override entry is now empty AND the agent is a builtin,
    # drop the entry entirely so the builtin defaults apply cleanly.
    if is_builtin and not entry:
        local_agents.pop(name, None)
    else:
        local_agents[name] = entry

    save_agents_to_local_config(_project_dir, local_agents)
    _reload_and_inject()
    return {"status": "updated", "name": name, "containment": req.containment}


@app.post("/api/agents")
def create_agent(req: CreateAgentRequest):
    """Create a custom agent."""
    from stepwise.agent_registry import BUILTIN_AGENTS

    if not _AGENT_NAME_RE.match(req.name):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid agent name '{req.name}'. Must match: ^[a-z][a-z0-9_-]{{0,62}}$",
        )
    if req.name in BUILTIN_AGENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot create agent '{req.name}' — it's a builtin agent name. Use PUT to override settings.",
        )
    if not req.command:
        raise HTTPException(status_code=400, detail="Agent command must be non-empty.")

    local_agents = _read_local_agents_raw()
    if req.name in local_agents:
        raise HTTPException(status_code=409, detail=f"Agent '{req.name}' already exists.")

    agent_data: dict[str, Any] = {"command": req.command}
    if req.config:
        agent_data["config"] = req.config
    if req.capabilities:
        agent_data["capabilities"] = req.capabilities
    if req.containment:
        agent_data["containment"] = req.containment
    if req.disabled:
        agent_data["disabled"] = req.disabled

    local_agents[req.name] = agent_data
    save_agents_to_local_config(_project_dir, local_agents)
    _reload_and_inject()
    return {"status": "created", "name": req.name}


@app.delete("/api/agents/{name}")
def delete_agent(name: str):
    """Delete a custom agent. Builtins cannot be deleted (use disable)."""
    from stepwise.agent_registry import BUILTIN_AGENTS

    if name in BUILTIN_AGENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete builtin agent '{name}'. Use POST /api/agents/{name}/disable to disable it, "
                   f"or POST /api/agents/{name}/reset to remove overrides.",
        )

    local_agents = _read_local_agents_raw()
    if name not in local_agents:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found.")

    del local_agents[name]
    save_agents_to_local_config(_project_dir, local_agents)
    _reload_and_inject()
    return {"status": "deleted", "name": name}


@app.post("/api/agents/{name}/disable")
def disable_agent(name: str):
    """Disable an agent."""
    from stepwise.agent_registry import BUILTIN_AGENTS, _get_all_agents

    all_agents = _get_all_agents()
    if name not in all_agents:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found.")

    local_agents = _read_local_agents_raw()
    is_builtin = name in BUILTIN_AGENTS

    if is_builtin:
        override = local_agents.get(name, {})
        if not isinstance(override, dict):
            override = {}
        override["disabled"] = True
        local_agents[name] = override
    else:
        agent_data = local_agents.get(name, {})
        if not isinstance(agent_data, dict):
            agent_data = {}
        agent_data["disabled"] = True
        local_agents[name] = agent_data

    save_agents_to_local_config(_project_dir, local_agents)
    _reload_and_inject()
    return {"status": "disabled", "name": name}


@app.post("/api/agents/{name}/enable")
def enable_agent(name: str):
    """Enable a previously disabled agent."""
    from stepwise.agent_registry import BUILTIN_AGENTS, _get_all_agents

    all_agents = _get_all_agents()
    if name not in all_agents:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found.")

    local_agents = _read_local_agents_raw()
    is_builtin = name in BUILTIN_AGENTS

    if is_builtin:
        override = local_agents.get(name, {})
        if not isinstance(override, dict):
            override = {}
        override.pop("disabled", None)
        # Clean up empty override
        if not override:
            local_agents.pop(name, None)
        else:
            local_agents[name] = override
    else:
        agent_data = local_agents.get(name, {})
        if not isinstance(agent_data, dict):
            agent_data = {}
        agent_data.pop("disabled", None)
        local_agents[name] = agent_data

    save_agents_to_local_config(_project_dir, local_agents)
    _reload_and_inject()
    return {"status": "enabled", "name": name}


@app.post("/api/agents/{name}/reset")
def reset_agent(name: str):
    """Reset a builtin agent to defaults by removing overrides."""
    from stepwise.agent_registry import BUILTIN_AGENTS

    if name not in BUILTIN_AGENTS:
        raise HTTPException(
            status_code=400,
            detail=f"Agent '{name}' is not a builtin. Only builtins can be reset.",
        )

    local_agents = _read_local_agents_raw()
    if name not in local_agents:
        return {"status": "already_default", "name": name}

    del local_agents[name]
    save_agents_to_local_config(_project_dir, local_agents)
    _reload_and_inject()
    return {"status": "reset", "name": name}


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
    """List all flows discoverable in the project directory (local + registry)."""
    from stepwise.flow_resolution import discover_flows, discover_registry_flows
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

        # Parse lightly to get step count, description, executor types, and graph
        steps_count = 0
        description = ""
        executor_types: list[str] = []
        visibility = "interactive"
        graph = None
        try:
            wf = load_workflow_yaml(flow_info.path)
            steps_count = len(wf.steps)
            description = wf.metadata.description or ""
            executor_types = sorted(
                {s.executor.type for s in wf.steps.values() if s.executor}
            )
            visibility = wf.metadata.visibility or "interactive"
            raw = flow_info.path.read_text()
            graph = _build_flow_graph(raw)
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
            "visibility": visibility,
            "source": "local",
            "graph": graph,
            "kit_name": flow_info.kit_name,
        })

    # Also include cached registry flows
    seen_names = {r["name"] for r in result}
    registry_flows = discover_registry_flows(_project_dir)
    for reg_flow in registry_flows:
        # Skip if a local flow with the same name already exists
        if reg_flow.slug in seen_names:
            continue

        steps_count = 0
        description = ""
        executor_types_reg: list[str] = []
        visibility_reg = "interactive"
        graph_reg = None
        try:
            wf = load_workflow_yaml(reg_flow.path)
            steps_count = len(wf.steps)
            description = wf.metadata.description or ""
            executor_types_reg = sorted(
                {s.executor.type for s in wf.steps.values() if s.executor}
            )
            visibility_reg = wf.metadata.visibility or "interactive"
            raw = reg_flow.path.read_text()
            graph_reg = _build_flow_graph(raw)
        except (YAMLLoadError, Exception):
            pass

        try:
            mtime = reg_flow.path.stat().st_mtime
            modified_at = datetime.fromtimestamp(mtime).isoformat()
        except OSError:
            modified_at = ""

        try:
            rel_path = str(reg_flow.path.relative_to(_project_dir))
        except ValueError:
            rel_path = str(reg_flow.path)

        result.append({
            "path": rel_path,
            "name": reg_flow.slug,
            "description": description,
            "steps_count": steps_count,
            "modified_at": modified_at,
            "is_directory": True,
            "executor_types": executor_types_reg,
            "visibility": visibility_reg,
            "source": "registry",
            "registry_ref": reg_flow.ref,
            "graph": graph_reg,
        })

    return result


def _build_flow_info_dict(flow_path: Path, flow_name: str, kit_name: str | None = None) -> dict:
    """Build the standard flow info dict used by /api/local-flows and /api/kits/{name}."""
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    try:
        mtime = flow_path.stat().st_mtime
        modified_at = datetime.fromtimestamp(mtime).isoformat()
    except OSError:
        modified_at = ""

    steps_count = 0
    description = ""
    executor_types: list[str] = []
    visibility = "interactive"
    graph = None
    try:
        wf = load_workflow_yaml(flow_path)
        steps_count = len(wf.steps)
        description = wf.metadata.description or ""
        executor_types = sorted(
            {s.executor.type for s in wf.steps.values() if s.executor}
        )
        visibility = wf.metadata.visibility or "interactive"
        raw = flow_path.read_text()
        graph = _build_flow_graph(raw)
    except (YAMLLoadError, Exception):
        pass

    try:
        rel_path = str(flow_path.relative_to(_project_dir))
    except ValueError:
        rel_path = str(flow_path)

    return {
        "path": rel_path,
        "name": flow_name,
        "description": description,
        "steps_count": steps_count,
        "modified_at": modified_at,
        "is_directory": True,
        "executor_types": executor_types,
        "visibility": visibility,
        "source": "local",
        "graph": graph,
        "kit_name": kit_name,
    }


@app.get("/api/kits")
def list_kits():
    """List all discovered kits with metadata."""
    from stepwise.flow_resolution import discover_kits
    from stepwise.yaml_loader import load_kit_yaml, KitLoadError

    kits = discover_kits(_project_dir)
    result = []
    for kit_info in kits:
        kit_def = None
        try:
            kit_def = load_kit_yaml(kit_info.path)
        except (KitLoadError, Exception):
            pass
        raw_yaml = ""
        try:
            raw_yaml = kit_info.path.read_text()
        except Exception:
            pass
        included = [
            {"name": f.name, "source_ref": f.source_ref, "source_type": f.source_type}
            for f in kit_info.included_flows
        ]
        result.append({
            "name": kit_info.name,
            "description": kit_def.description if kit_def else "",
            "author": kit_def.author if kit_def else "",
            "category": kit_def.category if kit_def else "",
            "usage": kit_def.usage if kit_def else "",
            "tags": kit_def.tags if kit_def else [],
            "flow_count": len(kit_info.all_flow_names),
            "flow_names": kit_info.all_flow_names,
            "included_flows": included,
            "raw_yaml": raw_yaml,
        })
    return result


@app.get("/api/kits/{kit_name}")
def get_kit_detail(kit_name: str):
    """Get full kit detail including member flows."""
    from stepwise.flow_resolution import discover_kits
    from stepwise.yaml_loader import load_kit_yaml, KitLoadError

    kits = discover_kits(_project_dir)
    kit = next((k for k in kits if k.name == kit_name), None)
    if not kit:
        raise HTTPException(status_code=404, detail=f"Kit '{kit_name}' not found")

    try:
        kit_def = load_kit_yaml(kit.path)
    except KitLoadError as e:
        raise HTTPException(status_code=500, detail=f"Error parsing KIT.yaml: {e}")

    flows = []
    for flow_name, flow_path in zip(kit.flow_names, kit.flow_paths):
        flows.append(_build_flow_info_dict(flow_path, flow_name, kit_name=kit_name))

    raw_yaml = ""
    try:
        raw_yaml = kit.path.read_text()
    except Exception:
        pass

    return {
        "name": kit_def.name,
        "description": kit_def.description,
        "author": kit_def.author,
        "category": kit_def.category,
        "usage": kit_def.usage,
        "tags": kit_def.tags,
        "include": kit_def.include,
        "flows": flows,
        "raw_yaml": raw_yaml,
    }


class CreateFlowRequest(BaseModel):
    name: str
    template: str = "blank"


class ForkFlowRequest(BaseModel):
    source_path: str
    name: str


@app.post("/api/local-flows/fork")
def fork_flow(req: ForkFlowRequest):
    """Fork a registry flow into the local flows/ directory."""
    import re
    import shutil

    from stepwise.flow_resolution import FLOW_NAME_PATTERN

    if not FLOW_NAME_PATTERN.match(req.name):
        raise HTTPException(status_code=400, detail=f"Invalid flow name: '{req.name}'")

    source = _project_dir / req.source_path
    if not source.is_file():
        raise HTTPException(status_code=404, detail=f"Source flow not found: {req.source_path}")

    dest_dir = _project_dir / "flows" / req.name
    if dest_dir.exists():
        raise HTTPException(status_code=409, detail=f"Flow '{req.name}' already exists")

    # Copy the entire flow directory (co-located files: prompts, scripts, etc.)
    source_dir = source.parent
    shutil.copytree(source_dir, dest_dir)

    # Update the name field in the FLOW.yaml
    flow_yaml_path = dest_dir / "FLOW.yaml"
    if flow_yaml_path.is_file():
        content = flow_yaml_path.read_text()
        # Replace the first name: line
        content = re.sub(r"^name:\s*.*$", f"name: {req.name}", content, count=1, flags=re.MULTILINE)
        flow_yaml_path.write_text(content)

    # Return flow info
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    steps_count = 0
    description = ""
    executor_types: list[str] = []
    try:
        wf = load_workflow_yaml(flow_yaml_path)
        steps_count = len(wf.steps)
        description = wf.metadata.description or ""
        executor_types = sorted({s.executor.type for s in wf.steps.values() if s.executor})
    except (YAMLLoadError, Exception):
        pass

    rel_path = str(flow_yaml_path.relative_to(_project_dir))
    return {
        "path": rel_path,
        "name": req.name,
        "description": description,
        "steps_count": steps_count,
        "is_directory": True,
        "executor_types": executor_types,
        "source": "local",
    }


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
    input_vars = [v.to_dict() for v in workflow.input_vars]

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
        "input_vars": input_vars,
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
    for field_name in ("description", "author", "version"):
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
    sort: str = "downloads",
    limit: int = 20,
    offset: int = 0,
):
    """Proxy search to stepwise.run registry."""
    from stepwise.registry_client import search_flows, RegistryError

    try:
        result = search_flows(query=q, sort=sort, limit=limit)
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
        data = fetch_flow(slug, count_download=False)
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


WS_HEARTBEAT_INTERVAL_SECONDS = 30
WS_RECEIVE_TIMEOUT_SECONDS = 90


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """Job watcher stream.

    Why heartbeats: long-running jobs can go quiet for minutes (a single agent
    step working, a paused job, external approval waits). Cloud proxies / load
    balancers / firewalls typically kill idle WebSocket connections after
    ~30-60 seconds, silently — no close frame reaches the client. Without a
    heartbeat the client thinks the stream is still live, React Query's cached
    state stays stale, and the UI freezes on whatever was last rendered.

    The server emits `{"type":"heartbeat"}` every 30s so the connection is
    never idle, and wraps `receive_text()` in a 90s timeout so a wedged reader
    task doesn't pin a dead socket indefinitely. Clients that don't recognise
    the heartbeat type silently ignore it (safe by construction in
    `useStepwiseWebSocket`).
    """
    await ws.accept()
    _ws_clients.add(ws)

    async def _send_heartbeats() -> None:
        try:
            while True:
                await asyncio.sleep(WS_HEARTBEAT_INTERVAL_SECONDS)
                await ws.send_json({"type": "heartbeat"})
        except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            pass

    heartbeat_task = asyncio.create_task(_send_heartbeats())
    try:
        while True:
            try:
                await asyncio.wait_for(
                    ws.receive_text(), timeout=WS_RECEIVE_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                # No message from client in WS_RECEIVE_TIMEOUT_SECONDS.
                # Treat as dead connection and let the finally block clean up.
                break
    except WebSocketDisconnect:
        pass
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except (asyncio.CancelledError, Exception):
            pass
        _ws_clients.discard(ws)


# ── Schedules ────────────────────────────────────────────────────────


def _cron_description(expr: str) -> str:
    """Human-readable cron description, with graceful fallback."""
    try:
        from cron_descriptor import get_description
        return get_description(expr)
    except Exception:
        return expr


def _serialize_schedule(sched: Schedule, stats: dict | None = None) -> dict:
    """Convert a Schedule to a JSON-serializable dict with optional stats."""
    engine = _get_engine()
    d = sched.to_dict()
    d["cron_description"] = _cron_description(sched.cron_expr)
    if stats:
        d["stats"] = stats
    # Include last fired job status
    last_tick = engine.store.last_fired_tick(sched.id)
    if last_tick and last_tick.job_id:
        try:
            job = engine.store.load_job(last_tick.job_id)
            d["last_job_status"] = job.status.value
        except (KeyError, Exception):
            d["last_job_status"] = None
    else:
        d["last_job_status"] = None
    return d


def _resolve_schedule(schedule_id: str) -> Schedule:
    """Look up a schedule by ID or name. Raises HTTPException on not found."""
    engine = _get_engine()
    sched = engine.store.get_schedule(schedule_id)
    if not sched:
        sched = engine.store.get_schedule_by_name(schedule_id)
    if not sched:
        raise HTTPException(status_code=404, detail=f"Schedule not found: {schedule_id}")
    return sched


@app.get("/api/schedules")
def list_schedules(status: str | None = None, type: str | None = None):
    engine = _get_engine()
    schedules = engine.store.list_schedules(status=status, schedule_type=type)
    return [_serialize_schedule(s) for s in schedules]


@app.post("/api/schedules")
def create_schedule(req: CreateScheduleRequest):
    engine = _get_engine()

    # Validate type
    try:
        sched_type = ScheduleType(req.type)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid type '{req.type}'. Valid: cron, poll")

    # Validate overlap_policy
    try:
        overlap = OverlapPolicy(req.overlap_policy)
    except ValueError:
        valid = [p.value for p in OverlapPolicy]
        raise HTTPException(status_code=400, detail=f"Invalid overlap_policy '{req.overlap_policy}'. Valid: {valid}")

    # Validate recovery_policy
    try:
        recovery = RecoveryPolicy(req.recovery_policy)
    except ValueError:
        valid = [p.value for p in RecoveryPolicy]
        raise HTTPException(status_code=400, detail=f"Invalid recovery_policy '{req.recovery_policy}'. Valid: {valid}")

    # Validate cron expression
    try:
        from croniter import croniter
        croniter(req.cron_expr)
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid cron expression: {e}")

    # Validate poll type has poll_command
    if sched_type == ScheduleType.POLL and not req.poll_command:
        raise HTTPException(status_code=400, detail="poll_command is required for poll-type schedules")

    # Check for duplicate name
    existing = engine.store.get_schedule_by_name(req.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Schedule with name '{req.name}' already exists")

    # Validate flow exists
    from stepwise.flow_resolution import resolve_flow, FlowResolutionError
    try:
        resolve_flow(req.flow_path, _project_dir)
    except FlowResolutionError:
        raise HTTPException(status_code=400, detail=f"Flow not found: {req.flow_path}")

    sched = Schedule(
        id=_gen_id("sched"),
        name=req.name,
        type=sched_type,
        flow_path=req.flow_path,
        cron_expr=req.cron_expr,
        poll_command=req.poll_command,
        poll_timeout_seconds=req.poll_timeout_seconds,
        cooldown_seconds=req.cooldown_seconds,
        job_inputs=req.job_inputs or {},
        job_name_template=req.job_name_template,
        overlap_policy=overlap,
        recovery_policy=recovery,
        timezone=req.timezone,
        max_consecutive_errors=req.max_consecutive_errors,
        metadata=req.metadata or {},
    )
    engine.store.save_schedule(sched)

    # Notify scheduler to pick up the new schedule
    if _scheduler:
        _scheduler.reload_schedule(sched.id)

    return _serialize_schedule(sched)


@app.get("/api/schedules/{schedule_id}")
def get_schedule(schedule_id: str):
    sched = _resolve_schedule(schedule_id)
    engine = _get_engine()
    stats = engine.store.tick_stats(sched.id)
    return _serialize_schedule(sched, stats=stats)


@app.patch("/api/schedules/{schedule_id}")
def update_schedule(schedule_id: str, req: UpdateScheduleRequest):
    sched = _resolve_schedule(schedule_id)
    engine = _get_engine()

    updates = {}
    if req.name is not None:
        # Check for duplicate name
        existing = engine.store.get_schedule_by_name(req.name)
        if existing and existing.id != sched.id:
            raise HTTPException(status_code=409, detail=f"Schedule with name '{req.name}' already exists")
        updates["name"] = req.name
    if req.cron_expr is not None:
        try:
            from croniter import croniter
            croniter(req.cron_expr)
        except (ValueError, KeyError) as e:
            raise HTTPException(status_code=400, detail=f"Invalid cron expression: {e}")
        updates["cron_expr"] = req.cron_expr
    if req.poll_command is not None:
        updates["poll_command"] = req.poll_command
    if req.poll_timeout_seconds is not None:
        updates["poll_timeout_seconds"] = req.poll_timeout_seconds
    if req.cooldown_seconds is not None:
        updates["cooldown_seconds"] = req.cooldown_seconds
    if req.job_inputs is not None:
        updates["job_inputs"] = req.job_inputs
    if req.job_name_template is not None:
        updates["job_name_template"] = req.job_name_template
    if req.overlap_policy is not None:
        try:
            OverlapPolicy(req.overlap_policy)
        except ValueError:
            valid = [p.value for p in OverlapPolicy]
            raise HTTPException(status_code=400, detail=f"Invalid overlap_policy. Valid: {valid}")
        updates["overlap_policy"] = req.overlap_policy
    if req.recovery_policy is not None:
        try:
            RecoveryPolicy(req.recovery_policy)
        except ValueError:
            valid = [p.value for p in RecoveryPolicy]
            raise HTTPException(status_code=400, detail=f"Invalid recovery_policy. Valid: {valid}")
        updates["recovery_policy"] = req.recovery_policy
    if req.timezone is not None:
        updates["timezone"] = req.timezone
    if req.max_consecutive_errors is not None:
        updates["max_consecutive_errors"] = req.max_consecutive_errors
    if req.metadata is not None:
        updates["metadata"] = req.metadata

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    engine.store.update_schedule(sched.id, **updates)

    # Reload in scheduler
    if _scheduler:
        _scheduler.reload_schedule(sched.id)

    updated = engine.store.get_schedule(sched.id)
    return _serialize_schedule(updated)


@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: str):
    sched = _resolve_schedule(schedule_id)
    engine = _get_engine()
    engine.store.delete_schedule(sched.id)

    # Remove from scheduler
    if _scheduler:
        _scheduler.reload_schedule(sched.id)

    return {"status": "deleted", "schedule_id": sched.id}


@app.post("/api/schedules/{schedule_id}/pause")
def pause_schedule(schedule_id: str, req: PauseScheduleRequest | None = None):
    sched = _resolve_schedule(schedule_id)
    engine = _get_engine()

    if sched.status == ScheduleStatus.PAUSED:
        return {"status": "already_paused", "schedule_id": sched.id}

    updates = {
        "status": ScheduleStatus.PAUSED.value,
        "paused_at": _now(),
    }
    if req and req.reason:
        meta = dict(sched.metadata)
        meta["pause_reason"] = req.reason
        updates["metadata"] = meta

    engine.store.update_schedule(sched.id, **updates)

    if _scheduler:
        _scheduler.reload_schedule(sched.id)

    return {"status": "paused", "schedule_id": sched.id}


@app.post("/api/schedules/{schedule_id}/resume")
def resume_schedule(schedule_id: str):
    sched = _resolve_schedule(schedule_id)
    engine = _get_engine()

    if sched.status == ScheduleStatus.ACTIVE:
        return {"status": "already_active", "schedule_id": sched.id}

    updates = {
        "status": ScheduleStatus.ACTIVE.value,
        "paused_at": None,
    }
    # Clear pause reason from metadata
    if sched.metadata.get("pause_reason"):
        meta = dict(sched.metadata)
        meta.pop("pause_reason", None)
        updates["metadata"] = meta

    engine.store.update_schedule(sched.id, **updates)

    if _scheduler:
        _scheduler.reload_schedule(sched.id)

    return {"status": "resumed", "schedule_id": sched.id}


@app.post("/api/schedules/{schedule_id}/trigger")
async def trigger_schedule(schedule_id: str):
    """Manually fire a schedule now, bypassing cron timing."""
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError
    from stepwise.models import OverlapPolicy

    sched = _resolve_schedule(schedule_id)
    engine = _get_engine()

    # Overlap check: respect the schedule's overlap policy
    if sched.overlap_policy == OverlapPolicy.SKIP:
        running = engine.store._conn.execute(
            """SELECT id FROM jobs
               WHERE json_extract(metadata, '$.sys.schedule_id') = ?
               AND status IN ('running', 'pending', 'paused')
               LIMIT 1""",
            (sched.id,),
        ).fetchone()
        if running:
            return {"status": "skipped", "schedule_id": sched.id, "reason": f"overlap: job {running['id']} is still running"}

    from stepwise.flow_resolution import resolve_flow, FlowResolutionError
    try:
        flow_abs = resolve_flow(sched.flow_path, _project_dir)
    except FlowResolutionError:
        raise HTTPException(status_code=400, detail=f"Flow not found: {sched.flow_path}")

    if sched.type == ScheduleType.POLL and sched.poll_command:
        # For poll type, run the poll command first
        from stepwise.poll_eval import evaluate_poll_command
        result = await evaluate_poll_command(
            command=sched.poll_command,
            cwd=str(_project_dir),
            timeout_seconds=sched.poll_timeout_seconds,
        )
        if result.error:
            raise HTTPException(status_code=500, detail=f"Poll command failed: {result.error}")
        if not result.ready:
            return {"status": "not_ready", "schedule_id": sched.id, "message": "Poll command returned not-ready"}
        poll_output = result.output
    else:
        poll_output = None

    # Build inputs
    inputs = {**sched.job_inputs}
    if poll_output:
        inputs.update(poll_output)

    # Render job name
    job_name = f"sched: {sched.name} (manual)"
    if sched.job_name_template and poll_output:
        try:
            job_name = sched.job_name_template.format(**poll_output)
        except (KeyError, ValueError):
            pass

    metadata = {
        "sys": {
            "schedule_id": sched.id,
            "schedule_name": sched.name,
            "trigger": "manual",
        }
    }

    try:
        wf = load_workflow_yaml(flow_abs)
    except YAMLLoadError as e:
        raise HTTPException(status_code=400, detail=f"Failed to load flow: {e}")

    job = engine.create_job(
        objective=f"Scheduled: {sched.name}",
        workflow=wf,
        inputs=inputs if inputs else None,
        name=job_name,
        metadata=metadata,
    )
    try:
        engine.start_job(job.id)
    except (KeyError, ValueError):
        pass

    # Update last_fired_at
    engine.store.update_schedule(sched.id, last_fired_at=_now())

    _notify_change(job.id)
    return {"status": "triggered", "schedule_id": sched.id, "job_id": job.id}


@app.get("/api/schedules/{schedule_id}/ticks")
def list_schedule_ticks(
    schedule_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    outcome: str | None = None,
):
    sched = _resolve_schedule(schedule_id)
    engine = _get_engine()
    ticks = engine.store.list_ticks(sched.id, limit=limit, offset=offset, outcome=outcome)
    return [t.to_dict() for t in ticks]


@app.get("/api/schedules/{schedule_id}/stats")
def get_schedule_stats(schedule_id: str):
    sched = _resolve_schedule(schedule_id)
    engine = _get_engine()
    stats = engine.store.tick_stats(sched.id)
    stats["consecutive_errors"] = engine.store.consecutive_errors(sched.id)
    stats["consecutive_skips"] = engine.store.consecutive_skips(sched.id)
    stats["queue_depth"] = engine.store.schedule_queue_depth(sched.id)
    return stats


@app.get("/api/schedules/{schedule_id}/jobs")
def list_schedule_jobs(
    schedule_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    status: str | None = None,
):
    sched = _resolve_schedule(schedule_id)
    engine = _get_engine()

    # Query jobs where metadata contains schedule_id
    query = "SELECT * FROM jobs WHERE json_extract(metadata, '$.sys.schedule_id') = ?"
    params: list = [sched.id]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = engine.store._conn.execute(query, params).fetchall()
    jobs = [engine.store._row_to_job(r) for r in rows]
    return [_serialize_job(j, summary=True) for j in jobs]


# ── Schedule Chat (LLM-assisted schedule management) ──────────────────


class SchedulesChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = []


_SCHEDULE_CHAT_SYSTEM = """\
You are a helpful schedule management assistant for Stepwise. You help users \
create, modify, pause, resume, and delete schedules.

You have tools to manage schedules. When the user asks you to do something, \
use the appropriate tool. After taking an action, briefly confirm what you did.

Be concise and conversational. Use short sentences.

## Current schedules
{schedules_json}

## Available flows
{flows_json}

## Schedule fields reference
- name: unique identifier (kebab-case recommended)
- type: "cron" or "poll"
- flow_path: path to the flow YAML file
- cron_expr: cron expression (e.g. "*/5 * * * *")
- overlap_policy: "skip" (default), "queue", or "allow"
- recovery_policy: "skip" (default) or "catch_up_once"
- timezone: IANA timezone (default "America/Los_Angeles")
- job_name_template: template for job names, can include variables like {number} from poll output
- job_inputs: key-value pairs passed to the flow
"""

_SCHEDULE_CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_schedule",
            "description": "Create a new schedule",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Schedule name"},
                    "type": {"type": "string", "enum": ["cron", "poll"], "description": "Schedule type"},
                    "flow_path": {"type": "string", "description": "Path to the flow YAML"},
                    "cron_expr": {"type": "string", "description": "Cron expression"},
                    "overlap_policy": {"type": "string", "enum": ["skip", "queue", "allow"]},
                    "recovery_policy": {"type": "string", "enum": ["skip", "catch_up_once"]},
                    "timezone": {"type": "string"},
                    "job_name_template": {"type": "string"},
                    "job_inputs": {"type": "object"},
                },
                "required": ["name", "type", "flow_path", "cron_expr"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_schedule",
            "description": "Update an existing schedule",
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule_id": {"type": "string", "description": "Schedule ID or name"},
                    "name": {"type": "string"},
                    "cron_expr": {"type": "string"},
                    "overlap_policy": {"type": "string", "enum": ["skip", "queue", "allow"]},
                    "recovery_policy": {"type": "string", "enum": ["skip", "catch_up_once"]},
                    "timezone": {"type": "string"},
                    "job_name_template": {"type": "string"},
                    "job_inputs": {"type": "object"},
                },
                "required": ["schedule_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_schedule",
            "description": "Delete a schedule",
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule_id": {"type": "string", "description": "Schedule ID or name"},
                },
                "required": ["schedule_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pause_schedule",
            "description": "Pause an active schedule",
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule_id": {"type": "string", "description": "Schedule ID or name"},
                },
                "required": ["schedule_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "resume_schedule",
            "description": "Resume a paused schedule",
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule_id": {"type": "string", "description": "Schedule ID or name"},
                },
                "required": ["schedule_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_schedules",
            "description": "List all schedules with their current status",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
]


def _execute_schedule_tool(name: str, args: dict) -> tuple[str, dict | None]:
    """Execute a schedule management tool call. Returns (result_text, action_data)."""
    engine = _get_engine()

    if name == "list_schedules":
        schedules = engine.store.list_schedules()
        if not schedules:
            return "No schedules found.", None
        lines = []
        for s in schedules:
            lines.append(f"- **{s.name}** ({s.id}): {s.status.value}, type={s.type.value}, cron=`{s.cron_expr}`")
        return "\n".join(lines), None

    if name == "create_schedule":
        from stepwise.flow_resolution import resolve_flow, FlowResolutionError
        try:
            sched_type = ScheduleType(args.get("type", "cron"))
            overlap = OverlapPolicy(args.get("overlap_policy", "skip"))
            recovery = RecoveryPolicy(args.get("recovery_policy", "skip"))
        except ValueError as e:
            return f"Invalid value: {e}", None

        try:
            resolve_flow(args["flow_path"], _project_dir)
        except FlowResolutionError:
            return f"Flow not found: {args['flow_path']}", None

        existing = engine.store.get_schedule_by_name(args["name"])
        if existing:
            return f"Schedule with name '{args['name']}' already exists.", None

        sched = Schedule(
            id=_gen_id("sched"),
            name=args["name"],
            type=sched_type,
            flow_path=args["flow_path"],
            cron_expr=args.get("cron_expr", ""),
            overlap_policy=overlap,
            recovery_policy=recovery,
            timezone=args.get("timezone", "America/Los_Angeles"),
            job_name_template=args.get("job_name_template"),
            job_inputs=args.get("job_inputs", {}),
        )
        engine.store.save_schedule(sched)
        if _scheduler:
            _scheduler.reload_schedule(sched.id)
        return f"Created schedule **{sched.name}** ({sched.id}).", {"action": "created", "schedule_id": sched.id}

    if name == "update_schedule":
        sid = args.pop("schedule_id", "")
        sched = engine.store.get_schedule(sid) or engine.store.get_schedule_by_name(sid)
        if not sched:
            return f"Schedule not found: {sid}", None
        updates = {k: v for k, v in args.items() if v is not None}
        if not updates:
            return "No fields to update.", None
        engine.store.update_schedule(sched.id, **updates)
        if _scheduler:
            _scheduler.reload_schedule(sched.id)
        return f"Updated schedule **{sched.name}**.", {"action": "updated", "schedule_id": sched.id}

    if name == "delete_schedule":
        sid = args.get("schedule_id", "")
        sched = engine.store.get_schedule(sid) or engine.store.get_schedule_by_name(sid)
        if not sched:
            return f"Schedule not found: {sid}", None
        engine.store.delete_schedule(sched.id)
        if _scheduler:
            _scheduler.reload_schedule(sched.id)
        return f"Deleted schedule **{sched.name}**.", {"action": "deleted", "schedule_id": sched.id}

    if name == "pause_schedule":
        sid = args.get("schedule_id", "")
        sched = engine.store.get_schedule(sid) or engine.store.get_schedule_by_name(sid)
        if not sched:
            return f"Schedule not found: {sid}", None
        if sched.status == ScheduleStatus.PAUSED:
            return f"Schedule **{sched.name}** is already paused.", None
        engine.store.update_schedule(sched.id, status=ScheduleStatus.PAUSED.value, paused_at=_now())
        if _scheduler:
            _scheduler.reload_schedule(sched.id)
        return f"Paused schedule **{sched.name}**.", {"action": "paused", "schedule_id": sched.id}

    if name == "resume_schedule":
        sid = args.get("schedule_id", "")
        sched = engine.store.get_schedule(sid) or engine.store.get_schedule_by_name(sid)
        if not sched:
            return f"Schedule not found: {sid}", None
        if sched.status == ScheduleStatus.ACTIVE:
            return f"Schedule **{sched.name}** is already active.", None
        engine.store.update_schedule(sched.id, status=ScheduleStatus.ACTIVE.value, paused_at=None)
        if _scheduler:
            _scheduler.reload_schedule(sched.id)
        return f"Resumed schedule **{sched.name}**.", {"action": "resumed", "schedule_id": sched.id}

    return f"Unknown tool: {name}", None


@app.post("/api/schedules/chat")
async def schedules_chat(req: SchedulesChatRequest):
    """Chat with an LLM about schedule management. Streams NDJSON."""
    from starlette.responses import StreamingResponse

    config = load_config(_project_dir)
    api_key = config.openrouter_api_key
    if not api_key:
        def error_gen():
            yield json.dumps({"type": "error", "content": "OpenRouter API key not configured. Set it in .stepwise/config.yaml."}) + "\n"
            yield json.dumps({"type": "done"}) + "\n"
        return StreamingResponse(error_gen(), media_type="application/x-ndjson")

    # Build context: current schedules + available flows
    engine = _get_engine()
    schedules = engine.store.list_schedules()
    schedules_json = json.dumps([_serialize_schedule(s) for s in schedules], indent=2) if schedules else "No schedules configured yet."

    # Get available flows
    try:
        from stepwise.flow_resolution import discover_flows
        local_flows = discover_flows(_project_dir)
        flows_json = json.dumps([{"name": f.name, "path": str(f.path)} for f in local_flows], indent=2)
    except Exception:
        flows_json = "Could not load flows."

    system_prompt = _SCHEDULE_CHAT_SYSTEM.replace(
        "{schedules_json}", schedules_json
    ).replace(
        "{flows_json}", flows_json
    )

    # Build messages
    messages = [{"role": "system", "content": system_prompt}]
    for h in req.history:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": req.message})

    def generate():
        import httpx as _httpx

        model = "anthropic/claude-sonnet-4-6"
        max_turns = 5  # Max tool-use turns

        current_messages = list(messages)

        for _turn in range(max_turns):
            payload = {
                "model": model,
                "messages": current_messages,
                "tools": _SCHEDULE_CHAT_TOOLS,
                "temperature": 0.3,
                "max_tokens": 2048,
                "stream": True,
            }

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://stepwise.local",
                "X-Title": "Stepwise Schedule Chat",
            }

            try:
                with _httpx.stream(
                    "POST",
                    "https://openrouter.ai/api/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=120.0,
                ) as resp:
                    if resp.status_code >= 400:
                        error_body = resp.read().decode()
                        yield json.dumps({"type": "error", "content": f"LLM API error ({resp.status_code}): {error_body[:200]}"}) + "\n"
                        yield json.dumps({"type": "done"}) + "\n"
                        return

                    # Accumulate SSE chunks to build the full response
                    full_content = ""
                    tool_calls_accum: dict[int, dict] = {}  # index -> {name, arguments_str}

                    for line in resp.iter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk_data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        choices = chunk_data.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})

                        # Stream text content
                        if delta.get("content"):
                            full_content += delta["content"]
                            yield json.dumps({"type": "text", "content": delta["content"]}) + "\n"

                        # Accumulate tool calls
                        if delta.get("tool_calls"):
                            for tc in delta["tool_calls"]:
                                idx = tc.get("index", 0)
                                if idx not in tool_calls_accum:
                                    tool_calls_accum[idx] = {"name": "", "arguments_str": ""}
                                if tc.get("function", {}).get("name"):
                                    tool_calls_accum[idx]["name"] = tc["function"]["name"]
                                if tc.get("function", {}).get("arguments"):
                                    tool_calls_accum[idx]["arguments_str"] += tc["function"]["arguments"]

                    # If there were tool calls, execute them and loop
                    if tool_calls_accum:
                        # Add the assistant message with tool calls to conversation
                        assistant_msg: dict = {"role": "assistant"}
                        if full_content:
                            assistant_msg["content"] = full_content
                        tc_list = []
                        for idx in sorted(tool_calls_accum.keys()):
                            tc = tool_calls_accum[idx]
                            tc_list.append({
                                "id": f"call_{idx}",
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": tc["arguments_str"],
                                },
                            })
                        assistant_msg["tool_calls"] = tc_list
                        current_messages.append(assistant_msg)

                        # Execute each tool call
                        for tc_item in tc_list:
                            func_name = tc_item["function"]["name"]
                            try:
                                func_args = json.loads(tc_item["function"]["arguments"])
                            except json.JSONDecodeError:
                                func_args = {}

                            result_text, action_data = _execute_schedule_tool(func_name, func_args)

                            # Emit action event if schedule was modified
                            if action_data:
                                yield json.dumps({"type": "action", **action_data}) + "\n"

                            # Add tool result to conversation
                            current_messages.append({
                                "role": "tool",
                                "tool_call_id": tc_item["id"],
                                "content": result_text,
                            })

                        # Continue the loop to get the LLM's response after tool execution
                        continue
                    else:
                        # No tool calls — we're done
                        break

            except _httpx.TimeoutException:
                yield json.dumps({"type": "error", "content": "Request timed out."}) + "\n"
                break
            except Exception as e:
                yield json.dumps({"type": "error", "content": str(e)}) + "\n"
                break

        yield json.dumps({"type": "done"}) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


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
