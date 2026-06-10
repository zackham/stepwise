"""Regression tests for 1.0 RC server hardening findings (F9–F20, F64).

Covers src/stepwise/server.py only:
- F9:  SPA catch-all path traversal containment
- F10: DELETE endpoints cancel RUNNING/PENDING jobs before deletion
- F11: /api/jobs/stale registered before /api/jobs/{job_id}
- F12: PUT /api/config/models preserves model metadata fields
- F13: bad input returns 4xx (events ?since, poll-now unknown run)
- F14: flow-file + schedule-trigger handlers run off the event loop
- F16: _broadcast per-client send timeout drops stalled clients
- F17: /api/v1/events/stream notices client disconnects promptly
- F19: NDJSON tailer buffers partial lines instead of dropping events
- F20: stream monitors keep tailing runs of PAUSED jobs
- F64: _latest_pause_cause uses a targeted query (and stays correct)
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import time
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import stepwise.server as srv
from stepwise.server import app
from stepwise.config import StepwiseConfig
from stepwise.events import JOB_PAUSED
from stepwise.models import (
    Event,
    Job,
    JobStatus,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _gen_id,
)
from stepwise.store import SQLiteStore


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_job(store, job_id: str, status: JobStatus = JobStatus.RUNNING) -> Job:
    job = Job(
        id=job_id,
        objective="test",
        workflow=WorkflowDefinition(steps={}),
        status=status,
        created_at=_now(),
        updated_at=_now(),
    )
    store.save_job(job)
    return job


def _make_running_run(store, job_id: str, step: str, executor_state: dict) -> StepRun:
    run = StepRun(
        id=_gen_id("run"),
        job_id=job_id,
        step_name=step,
        attempt=1,
        status=StepRunStatus.RUNNING,
        started_at=_now(),
        executor_state=executor_state,
    )
    store.save_run(run)
    return run


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


# ── F9: SPA catch-all path traversal ──────────────────────────────────


@pytest.mark.skipif(srv._web_dist is None or not srv._web_dist.exists(),
                    reason="web dist not bundled in this checkout")
class TestSpaPathTraversal:
    def _spa_endpoint(self):
        routes = [r for r in app.routes if getattr(r, "path", "") == "/{full_path:path}"]
        assert routes, "SPA catch-all route not registered"
        return routes[0].endpoint

    def test_dotdot_path_serves_index_not_target(self, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP-SECRET-CONTENT")
        rel = os.path.relpath(secret, srv._web_dist)
        assert rel.startswith("..")

        endpoint = self._spa_endpoint()
        resp = asyncio.run(endpoint(rel))
        # Must NOT serve the file outside the web dist — falls back to index.html
        assert str(resp.path).endswith("index.html")

    def test_absolute_path_serves_index_not_target(self):
        endpoint = self._spa_endpoint()
        resp = asyncio.run(endpoint("../../../../../../etc/passwd"))
        assert str(resp.path).endswith("index.html")

    def test_encoded_traversal_over_http(self, client, tmp_path):
        secret = tmp_path / "secret.txt"
        secret.write_text("TOP-SECRET-CONTENT")
        rel = os.path.relpath(secret, srv._web_dist)
        encoded = rel.replace("..", "%2e%2e")
        resp = client.get(f"/{encoded}")
        assert "TOP-SECRET-CONTENT" not in resp.text

    def test_legit_static_file_still_served(self, client):
        index = (srv._web_dist / "index.html").read_text()
        resp = client.get("/index.html")
        assert resp.status_code == 200
        assert resp.text == index


# ── F10: delete cancels active jobs first ─────────────────────────────


class TestDeleteCancelsActiveJobs:
    def test_delete_running_job_cancels_first(self, client, monkeypatch):
        engine = srv._engine
        job = _make_job(engine.store, "job-del-running", JobStatus.RUNNING)

        cancelled = []
        original = engine.cancel_job

        def recording_cancel(job_id):
            cancelled.append(job_id)
            return original(job_id)

        monkeypatch.setattr(engine, "cancel_job", recording_cancel)

        resp = client.delete(f"/api/jobs/{job.id}")
        assert resp.status_code == 200
        assert cancelled == [job.id]
        with pytest.raises(KeyError):
            engine.store.load_job(job.id)

    def test_delete_terminal_job_skips_cancel(self, client, monkeypatch):
        engine = srv._engine
        job = _make_job(engine.store, "job-del-done", JobStatus.COMPLETED)

        cancelled = []
        monkeypatch.setattr(engine, "cancel_job", lambda jid: cancelled.append(jid))

        resp = client.delete(f"/api/jobs/{job.id}")
        assert resp.status_code == 200
        assert cancelled == []

    def test_delete_all_cancels_active_jobs(self, client, monkeypatch):
        engine = srv._engine
        running = _make_job(engine.store, "job-all-running", JobStatus.RUNNING)
        _make_job(engine.store, "job-all-done", JobStatus.COMPLETED)

        cancelled = []
        original = engine.cancel_job

        def recording_cancel(job_id):
            cancelled.append(job_id)
            return original(job_id)

        monkeypatch.setattr(engine, "cancel_job", recording_cancel)

        resp = client.delete("/api/jobs")
        assert resp.status_code == 200
        assert cancelled == [running.id]
        assert engine.store.all_jobs(include_archived=True) == []


# ── F11: /api/jobs/stale not shadowed ─────────────────────────────────


class TestStaleRouteOrdering:
    def test_stale_route_reachable(self, client):
        resp = client.get("/api/jobs/stale")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ── F12: PUT /api/config/models keeps metadata ────────────────────────


class TestUpdateModelsPreservesMetadata:
    def test_put_models_keeps_all_fields(self, client, monkeypatch):
        saved = {}

        def fake_load_config(project_dir=None):
            return StepwiseConfig()

        def fake_save_config(cfg):
            saved["cfg"] = cfg

        monkeypatch.setattr(srv, "load_config", fake_load_config)
        monkeypatch.setattr(srv, "save_config", fake_save_config)

        resp = client.put("/api/config/models", json={
            "models": [{
                "id": "anthropic/claude-sonnet-4",
                "name": "Sonnet 4",
                "provider": "anthropic",
                "context_length": 200000,
                "max_output_tokens": 64000,
                "prompt_cost": 3.0,
                "completion_cost": 15.0,
            }],
        })
        assert resp.status_code == 200

        entry = saved["cfg"].model_registry[0]
        assert entry.context_length == 200000
        assert entry.max_output_tokens == 64000
        assert entry.prompt_cost == 3.0
        assert entry.completion_cost == 15.0

        # Echoed back in the response too
        model = resp.json()["models"][0]
        assert model["context_length"] == 200000
        assert model["max_output_tokens"] == 64000


# ── F13: 4xx instead of 500 on bad input ──────────────────────────────


class TestClientErrorStatusCodes:
    def test_events_bad_since_returns_400(self, client):
        _make_job(srv._engine.store, "job-events", JobStatus.RUNNING)
        resp = client.get("/api/jobs/job-events/events", params={"since": "garbage"})
        assert resp.status_code == 400

    def test_events_valid_since_still_works(self, client):
        _make_job(srv._engine.store, "job-events-2", JobStatus.RUNNING)
        resp = client.get(
            "/api/jobs/job-events-2/events",
            params={"since": _now().isoformat()},
        )
        assert resp.status_code == 200

    def test_poll_now_unknown_run_returns_404(self, client):
        resp = client.post("/api/runs/run-does-not-exist/poll-now")
        assert resp.status_code == 404


# ── F14: blocking handlers run off the event loop ─────────────────────


class TestBlockingHandlersAreSync:
    def test_flow_file_handlers_are_plain_def(self):
        for fn in (
            srv.list_flow_files,
            srv.read_flow_file,
            srv.write_flow_file,
            srv.delete_flow_file,
            srv.delete_local_flow,
            srv.trigger_schedule,
        ):
            assert not inspect.iscoroutinefunction(fn), (
                f"{fn.__name__} must be a plain def so FastAPI runs its "
                "blocking I/O in the threadpool, not on the event loop"
            )


# ── F16: _broadcast timeout drops stalled clients ─────────────────────


class _StalledWS:
    async def send_text(self, payload: str) -> None:
        await asyncio.Event().wait()  # never completes


class _GoodWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)


class TestBroadcastTimeout:
    def test_stalled_client_does_not_block_broadcast(self, monkeypatch):
        monkeypatch.setattr(srv, "_BROADCAST_SEND_TIMEOUT_SECONDS", 0.05)
        stalled = _StalledWS()
        good = _GoodWS()
        monkeypatch.setattr(srv, "_ws_clients", {stalled, good})

        async def run():
            # Overall bound: without the per-send timeout this hangs forever
            await asyncio.wait_for(srv._broadcast({"type": "tick"}), timeout=2.0)

        asyncio.run(run())
        assert len(good.sent) == 1
        assert stalled not in srv._ws_clients
        assert good in srv._ws_clients


# ── F17: events stream notices disconnects ────────────────────────────


class TestEventStreamDisconnect:
    def test_client_removed_after_disconnect_without_new_events(self, client):
        _make_job(srv._engine.store, "job-stream", JobStatus.COMPLETED)

        with client.websocket_connect(
            "/api/v1/events/stream?job_id=job-stream"
        ):
            deadline = time.time() + 2.0
            while time.time() < deadline and not srv._event_stream_clients:
                time.sleep(0.02)
            assert len(srv._event_stream_clients) == 1

        # After disconnect — with NO further events arriving — the
        # handler must notice via the receive channel and deregister.
        deadline = time.time() + 2.0
        while time.time() < deadline and srv._event_stream_clients:
            time.sleep(0.02)
        assert srv._event_stream_clients == []


# ── F19: tailer buffers partial NDJSON lines ──────────────────────────


class TestTailerPartialLines:
    def test_line_split_across_reads_is_not_dropped(self, tmp_path, monkeypatch):
        received: list[dict] = []

        async def fake_broadcast(msg: dict) -> None:
            received.append(msg)

        monkeypatch.setattr(srv, "_broadcast", fake_broadcast)

        line = json.dumps({
            "params": {"update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "hello stepwise"},
            }},
        })
        path = tmp_path / "out.ndjson"

        async def run():
            # Write only the first half of the record — no newline yet
            path.write_text(line[:25])
            task = asyncio.create_task(srv._tail_agent_output("r1", str(path)))
            await asyncio.sleep(0.35)
            assert received == [], "partial line must not be consumed"
            # Complete the record
            with open(path, "a") as f:
                f.write(line[25:] + "\n")
            await asyncio.sleep(0.35)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        texts = [e["text"] for m in received for e in m["events"] if e["t"] == "text"]
        assert texts == ["hello stepwise"]

    def test_multiple_complete_lines_in_one_read(self, tmp_path, monkeypatch):
        received: list[dict] = []

        async def fake_broadcast(msg: dict) -> None:
            received.append(msg)

        monkeypatch.setattr(srv, "_broadcast", fake_broadcast)

        def chunk(text: str) -> str:
            return json.dumps({
                "params": {"update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": text},
                }},
            })

        path = tmp_path / "out.ndjson"
        path.write_text(chunk("one") + "\n" + chunk("two") + "\n")

        async def run():
            task = asyncio.create_task(srv._tail_agent_output("r1", str(path)))
            await asyncio.sleep(0.35)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        asyncio.run(run())
        texts = [e["text"] for m in received for e in m["events"] if e["t"] == "text"]
        assert texts == ["one", "two"]


# ── F20: monitors keep tailing runs of PAUSED jobs ────────────────────


class TestStreamableJobsIncludePaused:
    def test_paused_job_with_running_run_is_streamable(self):
        store = SQLiteStore(":memory:")
        engine = SimpleNamespace(store=store)

        running_job = _make_job(store, "job-running", JobStatus.RUNNING)
        paused_job = _make_job(store, "job-paused", JobStatus.PAUSED)
        _make_job(store, "job-done", JobStatus.COMPLETED)
        _make_running_run(store, paused_job.id, "agent-step",
                          {"output_path": "/tmp/x.ndjson"})

        ids = {j.id for j in srv._jobs_with_streamable_runs(engine)}
        assert running_job.id in ids
        assert paused_job.id in ids, (
            "PAUSED jobs must stay in the tail set — pausing does not stop "
            "an in-flight agent, and cancelling the tailer makes resume "
            "re-broadcast the whole transcript from offset 0"
        )
        assert "job-done" not in ids


# ── F64: _latest_pause_cause targeted query ───────────────────────────


class TestLatestPauseCause:
    def _add_event(self, store, job_id: str, etype: str, data: dict,
                   ts: datetime) -> None:
        store.save_event(Event(
            id=_gen_id("evt"),
            job_id=job_id,
            timestamp=ts,
            type=etype,
            data=data,
        ))

    def test_returns_latest_paused_payload(self):
        store = SQLiteStore(":memory:")
        engine = SimpleNamespace(store=store)
        job = _make_job(store, "job-pc", JobStatus.PAUSED)

        t0 = _now() - timedelta(minutes=10)
        self._add_event(store, job.id, "job.started", {}, t0)
        self._add_event(store, job.id, JOB_PAUSED,
                        {"reason": "first"}, t0 + timedelta(minutes=1))
        self._add_event(store, job.id, "step.completed",
                        {"step": "a"}, t0 + timedelta(minutes=2))
        self._add_event(store, job.id, JOB_PAUSED,
                        {"reason": "second", "step": "b"},
                        t0 + timedelta(minutes=3))

        cause = srv._latest_pause_cause(engine, job.id)
        assert cause is not None
        assert cause["reason"] == "second"
        assert cause["step"] == "b"
        assert cause["at"] is not None
        # `at` must round-trip as an ISO timestamp
        datetime.fromisoformat(cause["at"])

    def test_returns_none_without_paused_event(self):
        store = SQLiteStore(":memory:")
        engine = SimpleNamespace(store=store)
        job = _make_job(store, "job-nc", JobStatus.RUNNING)
        self._add_event(store, job.id, "job.started", {}, _now())

        assert srv._latest_pause_cause(engine, job.id) is None
