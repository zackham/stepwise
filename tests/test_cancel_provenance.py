"""Cancellation provenance: WHO cancelled a job and WHY must be recorded.

Regression for a production incident: an external
actor (a SIGTERM-reaped `stepwise run --wait` client) cancelled a running
server-side job, and the only trace was a bare "Job cancelled" on the step
runs — no job.cancelled event, no reason, no source. Root-causing required
correlating three separate logs.

This file covers:
- Engine.cancel_job emits a job.cancelled event carrying reason/source
- run.error includes the reason when one is given
- Defaults ("unspecified"/"unknown") when no provenance is passed
- POST /api/jobs/{id}/cancel forwards an optional {reason, source} body
  (and still works with no body, defaulting source to "api")
"""

from __future__ import annotations

import os
import threading

import pytest
from starlette.testclient import TestClient

import stepwise.server as srv
from stepwise.server import app
from stepwise.engine import AsyncEngine
from stepwise.events import JOB_CANCELLED
from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorRegistry,
    ExecutorResult,
    ExecutorStatus,
)
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore


class _BlockingExecutor(Executor):
    """Executor that blocks until an event is set (simulates a running agent)."""

    def __init__(self, started_event: threading.Event, block_event: threading.Event):
        self._started = started_event
        self._block = block_event

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        self._started.set()
        self._block.wait(timeout=30)
        return ExecutorResult(
            type="data",
            envelope=HandoffEnvelope(
                artifact={"result": "ok"},
                sidecar=Sidecar(),
                workspace="",
                timestamp=_now(),
            ),
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        return ExecutorStatus(state="running")

    def cancel(self, state: dict) -> None:
        pass


def _single_agent_step() -> WorkflowDefinition:
    return WorkflowDefinition(steps={
        "research": StepDefinition(
            name="research",
            executor=ExecutorRef(type="agent", config={}),
            outputs=["result"],
        ),
    })


async def _start_blocked_job(engine, store):
    """Create + start a job and wait until its agent step is running."""
    import asyncio

    started_event = threading.Event()
    block_event = threading.Event()
    registry = engine.registry
    registry.register(
        "agent", lambda cfg: _BlockingExecutor(started_event, block_event)
    )

    job = engine.create_job(objective="cancel-me", workflow=_single_agent_step())
    engine_task = asyncio.create_task(engine.run())
    engine.start_job(job.id)
    await asyncio.get_event_loop().run_in_executor(None, started_event.wait, 5.0)
    assert started_event.is_set(), "agent step did not start"
    block_event.set()  # let the executor thread finish once cancelled
    return job, engine_task


def _cancel_events(store, job_id):
    return [e for e in store.load_events(job_id) if e.type == JOB_CANCELLED]


class TestEngineCancelProvenance:

    @pytest.mark.asyncio
    async def test_cancel_emits_event_with_reason_and_source(self):
        import asyncio

        store = SQLiteStore(":memory:")
        engine = AsyncEngine(store=store, registry=ExecutorRegistry())
        engine._agent_stagger_seconds = 0.0
        job, engine_task = await _start_blocked_job(engine, store)
        try:
            engine.cancel_job(
                job.id,
                reason="interrupted (SIGINT) at attached --wait client",
                source="cli_wait_client_sigint",
            )

            assert store.load_job(job.id).status == JobStatus.CANCELLED

            events = _cancel_events(store, job.id)
            assert len(events) == 1, "cancel_job must emit exactly one job.cancelled event"
            assert events[0].data["reason"] == (
                "interrupted (SIGINT) at attached --wait client"
            )
            assert events[0].data["source"] == "cli_wait_client_sigint"

            runs = store.runs_for_job(job.id)
            cancelled_runs = [r for r in runs if r.status == StepRunStatus.CANCELLED]
            assert cancelled_runs, "expected the running step to be cancelled"
            for run in cancelled_runs:
                assert run.error == (
                    "Job cancelled: interrupted (SIGINT) at attached --wait client"
                )
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_cancel_without_provenance_uses_defaults(self):
        import asyncio

        store = SQLiteStore(":memory:")
        engine = AsyncEngine(store=store, registry=ExecutorRegistry())
        engine._agent_stagger_seconds = 0.0
        job, engine_task = await _start_blocked_job(engine, store)
        try:
            engine.cancel_job(job.id)

            events = _cancel_events(store, job.id)
            assert len(events) == 1
            assert events[0].data["reason"] == "unspecified"
            assert events[0].data["source"] == "unknown"

            runs = store.runs_for_job(job.id)
            cancelled_runs = [r for r in runs if r.status == StepRunStatus.CANCELLED]
            assert cancelled_runs
            for run in cancelled_runs:
                assert run.error == "Job cancelled"
        finally:
            engine_task.cancel()
            try:
                await engine_task
            except asyncio.CancelledError:
                pass


@pytest.fixture
def client(tmp_path):
    """TestClient with lifespan-managed engine on an in-memory DB."""
    old_env = os.environ.copy()
    os.environ["STEPWISE_PROJECT_DIR"] = str(tmp_path)
    os.environ["STEPWISE_DB"] = ":memory:"
    os.environ["STEPWISE_TEMPLATES"] = str(tmp_path / "_templates")
    os.environ["STEPWISE_JOBS_DIR"] = str(tmp_path / "_jobs")

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c

    os.environ.clear()
    os.environ.update(old_env)


class TestCancelEndpointProvenance:

    def _make_running_job(self, engine, job_id: str):
        from stepwise.models import Job

        job = Job(
            id=job_id,
            objective="test",
            workflow=WorkflowDefinition(steps={}),
            status=JobStatus.RUNNING,
            created_at=_now(),
            updated_at=_now(),
        )
        engine.store.save_job(job)
        return job

    def test_cancel_endpoint_forwards_reason_and_source(self, client, monkeypatch):
        engine = srv._engine
        job = self._make_running_job(engine, "job-cancel-prov")

        seen = []

        def recording_cancel(job_id, *, reason=None, source=None):
            seen.append((job_id, reason, source))

        monkeypatch.setattr(engine, "cancel_job", recording_cancel)

        resp = client.post(
            f"/api/jobs/{job.id}/cancel",
            json={"reason": "client interrupt", "source": "cli_wait_client_sigint"},
        )
        assert resp.status_code == 200
        assert seen == [(job.id, "client interrupt", "cli_wait_client_sigint")]

    def test_cancel_endpoint_works_without_body(self, client, monkeypatch):
        engine = srv._engine
        job = self._make_running_job(engine, "job-cancel-nobody")

        seen = []

        def recording_cancel(job_id, *, reason=None, source=None):
            seen.append((job_id, reason, source))

        monkeypatch.setattr(engine, "cancel_job", recording_cancel)

        resp = client.post(f"/api/jobs/{job.id}/cancel")
        assert resp.status_code == 200
        # No body: reason stays None, source defaults to "api" so the
        # event still records that the cancel came through the HTTP API.
        assert seen == [(job.id, None, "api")]


# ── SIGTERM detaches, SIGINT cancels (the actual incident behaviour) ──
# The provenance tests above cover the RECORDING half. This covers the half that
# actually prevented the production incident: a delegated `--wait` client that
# is externally killed must leave server-owned work ALONE. Without this, a parent
# session reaping its background children silently destroys running jobs — which
# is exactly what happened in production (two scheduled jobs cancelled ~30s after
# launch, zero completions, no recorded cause).

import asyncio
import signal as _signal
from unittest.mock import MagicMock, patch

import pytest as _pytest

from stepwise.runner import EXIT_SUCCESS, _delegated_ws_loop


class _SpyClient:
    """Records every POST so we can assert a cancel was or was not issued."""

    def __init__(self):
        self.posts = []

    async def get(self, url, **kw):
        resp = MagicMock()
        # /api/jobs/{id} returns the job; /api/jobs/{id}/runs returns run dicts.
        if url.endswith("/runs"):
            payload = [{"id": "run-1", "step_name": "s1", "status": "running",
                        "started_at": None, "completed_at": None, "attempt": 1}]
        else:
            payload = {"id": "job-test", "status": "running"}
        resp.json = MagicMock(return_value=payload)
        resp.raise_for_status = MagicMock()
        resp.status_code = 200
        return resp

    async def post(self, url, **kw):
        self.posts.append((url, kw.get("json")))
        resp = MagicMock()
        resp.json = MagicMock(return_value={"status": "ok"})
        resp.raise_for_status = MagicMock()
        resp.status_code = 200
        return resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


async def _run_loop_with_signal(sig: int, spy: _SpyClient) -> int:
    """Start the delegated wait loop, deliver `sig` to ourselves, return exit code."""
    adapter = MagicMock()
    adapter.live_flow.return_value.__enter__ = MagicMock(return_value=MagicMock())
    adapter.live_flow.return_value.__exit__ = MagicMock(return_value=False)

    import websockets as _ws
    with patch("stepwise.runner.httpx.AsyncClient", return_value=spy), \
         patch.object(_ws, "connect", side_effect=Exception("no ws")):
        task = asyncio.ensure_future(_delegated_ws_loop(
            server_url="http://localhost:1",
            job_id="job-test",
            adapter=adapter,
            output_stream=None,
            output_json=False,
            report=False,
            report_output=None,
            flow_path=None,
        ))
        await asyncio.sleep(0.05)  # let the signal handlers install
        import os
        os.kill(os.getpid(), sig)
        return await asyncio.wait_for(task, timeout=10)


@_pytest.mark.asyncio
async def test_sigterm_detaches_without_cancelling_the_job():
    """THE REGRESSION. An externally-killed viewer must not destroy server work."""
    spy = _SpyClient()
    code = await _run_loop_with_signal(_signal.SIGTERM, spy)
    assert code == EXIT_SUCCESS, "detach is a clean exit, not a failure"
    cancels = [u for u, _ in spy.posts if "cancel" in u]
    assert cancels == [], f"SIGTERM must NOT cancel; issued {cancels}"


@_pytest.mark.asyncio
async def test_sigint_still_cancels_with_provenance():
    """Interactive Ctrl-C keeps its cancel semantics — and says who did it."""
    spy = _SpyClient()
    code = await _run_loop_with_signal(_signal.SIGINT, spy)
    assert code == 130
    cancels = [(u, b) for u, b in spy.posts if "cancel" in u]
    assert len(cancels) == 1, f"SIGINT must cancel exactly once; got {cancels}"
    body = cancels[0][1]
    assert body["source"] == "cli_client_sigint"
    assert "SIGINT" in body["reason"]
