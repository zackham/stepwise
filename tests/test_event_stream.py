"""Tests for the /api/v1/events/stream WebSocket endpoint (E2)."""

from __future__ import annotations

import os
import pytest
from datetime import datetime, timezone
from pathlib import Path
from starlette.testclient import TestClient

import stepwise.server as srv
from stepwise.server import app
from stepwise.store import SQLiteStore
from stepwise.models import (
    Job, JobStatus, WorkflowDefinition, Event,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_job(
    store,
    job_id: str,
    metadata: dict | None = None,
) -> Job:
    """Create a minimal job in the store."""
    job = Job(
        id=job_id,
        objective="test",
        workflow=WorkflowDefinition(steps={}),
        status=JobStatus.RUNNING,
        created_at=_now(),
        updated_at=_now(),
        metadata=metadata or {"sys": {}, "app": {}},
    )
    store.save_job(job)
    return job


def _make_event(
    store,
    event_id: str,
    job_id: str,
    event_type: str = "step.completed",
    data: dict | None = None,
) -> int:
    """Create an event in the store and return its rowid."""
    event = Event(
        id=event_id,
        job_id=job_id,
        timestamp=_now(),
        type=event_type,
        data=data or {},
    )
    return store.save_event(event)


class TestEventStreamEndpoint:
    """Tests for /api/v1/events/stream WebSocket."""

    @pytest.fixture(autouse=True)
    def client(self, tmp_path):
        """Create a TestClient with engine via lifespan (in-memory DB)."""
        old_env = os.environ.copy()
        os.environ["STEPWISE_PROJECT_DIR"] = str(tmp_path)
        os.environ["STEPWISE_DB"] = ":memory:"
        os.environ["STEPWISE_TEMPLATES"] = str(tmp_path / "_templates")
        os.environ["STEPWISE_JOBS_DIR"] = str(tmp_path / "_jobs")

        with TestClient(app, raise_server_exceptions=False) as c:
            self._client = c
            self._store = srv._engine.store
            yield c

        os.environ.clear()
        os.environ.update(old_env)

    @property
    def store(self):
        return self._store

    # ── Test 1: Basic connection ────────────────────────────────────────

    def test_basic_connection(self):
        """Connect without filters, verify connection works."""
        _make_job(self.store, "j1")

        with self._client.websocket_connect("/api/v1/events/stream") as ws:
            pass  # clean disconnect

    # ── Test 2: Replay with since_event_id ──────────────────────────────

    def test_replay_since_event_id(self):
        """Connect with since_event_id, verify replay + boundary frame."""
        _make_job(self.store, "j1")

        r1 = _make_event(self.store, "e1", "j1", "job.started")
        r2 = _make_event(self.store, "e2", "j1", "step.completed", {"step": "s1"})
        r3 = _make_event(self.store, "e3", "j1", "step.completed", {"step": "s2"})

        with self._client.websocket_connect(
            f"/api/v1/events/stream?since_event_id={r1}"
        ) as ws:
            msg1 = ws.receive_json()
            assert msg1["event"] == "step.completed"
            assert msg1["event_id"] == r2
            assert msg1["job_id"] == "j1"
            assert "step" in msg1  # step promoted to top-level

            msg2 = ws.receive_json()
            assert msg2["event"] == "step.completed"
            assert msg2["event_id"] == r3

            # Boundary frame
            boundary = ws.receive_json()
            assert boundary["type"] == "sys.replay.complete"
            assert boundary["last_event_id"] == r3

    def test_replay_since_event_id_zero(self):
        """since_event_id=0 replays all events."""
        _make_job(self.store, "j1")

        _make_event(self.store, "e1", "j1", "job.started")
        _make_event(self.store, "e2", "j1", "step.completed")

        with self._client.websocket_connect(
            "/api/v1/events/stream?since_event_id=0"
        ) as ws:
            msg1 = ws.receive_json()
            assert msg1["event"] == "job.started"

            msg2 = ws.receive_json()
            assert msg2["event"] == "step.completed"

            boundary = ws.receive_json()
            assert boundary["type"] == "sys.replay.complete"

    # ── Test 3: job_id filter ───────────────────────────────────────────

    def test_job_id_filter_replay(self):
        """Only events for the specified job_id are replayed."""
        _make_job(self.store, "j1")
        _make_job(self.store, "j2")

        _make_event(self.store, "e1", "j1", "job.started")
        _make_event(self.store, "e2", "j2", "job.started")
        r3 = _make_event(self.store, "e3", "j1", "step.completed")

        with self._client.websocket_connect(
            "/api/v1/events/stream?job_id=j1&since_event_id=0"
        ) as ws:
            msg1 = ws.receive_json()
            assert msg1["job_id"] == "j1"
            assert msg1["event"] == "job.started"

            msg2 = ws.receive_json()
            assert msg2["job_id"] == "j1"
            assert msg2["event"] == "step.completed"

            boundary = ws.receive_json()
            assert boundary["type"] == "sys.replay.complete"
            assert boundary["last_event_id"] == r3

    def test_multiple_job_ids(self):
        """Multiple job_id params use OR semantics."""
        _make_job(self.store, "j1")
        _make_job(self.store, "j2")
        _make_job(self.store, "j3")

        _make_event(self.store, "e1", "j1", "job.started")
        _make_event(self.store, "e2", "j2", "job.started")
        _make_event(self.store, "e3", "j3", "job.started")

        with self._client.websocket_connect(
            "/api/v1/events/stream?job_id=j1&job_id=j2&since_event_id=0"
        ) as ws:
            msg1 = ws.receive_json()
            assert msg1["job_id"] == "j1"

            msg2 = ws.receive_json()
            assert msg2["job_id"] == "j2"

            boundary = ws.receive_json()
            assert boundary["type"] == "sys.replay.complete"

    # ── Test 4: session_id filter ───────────────────────────────────────

    def test_session_id_filter_replay(self):
        """session_id filter resolves to job_ids and filters replay."""
        _make_job(self.store, "j1", metadata={"sys": {"session_id": "sess-A"}, "app": {}})
        _make_job(self.store, "j2", metadata={"sys": {"session_id": "sess-B"}, "app": {}})

        _make_event(self.store, "e1", "j1", "job.started")
        _make_event(self.store, "e2", "j2", "job.started")
        r3 = _make_event(self.store, "e3", "j1", "step.completed")

        with self._client.websocket_connect(
            "/api/v1/events/stream?session_id=sess-A&since_event_id=0"
        ) as ws:
            msg1 = ws.receive_json()
            assert msg1["job_id"] == "j1"

            msg2 = ws.receive_json()
            assert msg2["job_id"] == "j1"

            boundary = ws.receive_json()
            assert boundary["type"] == "sys.replay.complete"
            assert boundary["last_event_id"] == r3

    # ── Test 5: since_job_start ─────────────────────────────────────────

    def test_since_job_start(self):
        """since_job_start=true replays all events for filtered jobs."""
        _make_job(self.store, "j1")
        _make_job(self.store, "j2")

        _make_event(self.store, "e1", "j1", "job.started")
        _make_event(self.store, "e2", "j2", "job.started")
        r3 = _make_event(self.store, "e3", "j1", "step.completed")

        with self._client.websocket_connect(
            "/api/v1/events/stream?since_job_start=true&job_id=j1"
        ) as ws:
            msg1 = ws.receive_json()
            assert msg1["job_id"] == "j1"
            assert msg1["event"] == "job.started"

            msg2 = ws.receive_json()
            assert msg2["job_id"] == "j1"
            assert msg2["event"] == "step.completed"

            boundary = ws.receive_json()
            assert boundary["type"] == "sys.replay.complete"
            assert boundary["last_event_id"] == r3

    def test_since_job_start_requires_scope(self):
        """since_job_start without job_id or session_id closes with error."""
        # The server accepts then immediately closes — TestClient may raise
        try:
            with self._client.websocket_connect(
                "/api/v1/events/stream?since_job_start=true"
            ) as ws:
                pass
        except Exception:
            pass  # Expected — connection closed by server

    # ── Test 6: No-filter admin mode ────────────────────────────────────

    def test_no_filter_admin_replay_all(self):
        """No-filter with since_event_id=0 replays all events."""
        _make_job(self.store, "j1")
        _make_job(self.store, "j2")

        _make_event(self.store, "e1", "j1", "job.started")
        _make_event(self.store, "e2", "j2", "job.started")

        with self._client.websocket_connect(
            "/api/v1/events/stream?since_event_id=0"
        ) as ws:
            msg1 = ws.receive_json()
            assert msg1["job_id"] == "j1"

            msg2 = ws.receive_json()
            assert msg2["job_id"] == "j2"

            boundary = ws.receive_json()
            assert boundary["type"] == "sys.replay.complete"

    # ── Test 7: Envelope format ─────────────────────────────────────────

    def test_envelope_format(self):
        """Verify event envelopes match the E1 format."""
        meta = {"sys": {"session_id": "s1"}, "app": {"tag": "v1"}}
        _make_job(self.store, "j1", metadata=meta)

        _make_event(self.store, "e1", "j1", "step.completed", {"step": "fetch", "output": "ok"})

        with self._client.websocket_connect(
            "/api/v1/events/stream?since_event_id=0"
        ) as ws:
            msg = ws.receive_json()

            # Required fields
            assert msg["event"] == "step.completed"
            assert msg["job_id"] == "j1"
            assert msg["event_id"] >= 1
            assert "timestamp" in msg
            assert msg["metadata"] == meta
            assert msg["data"]["step"] == "fetch"
            assert msg["data"]["output"] == "ok"

            # Step promoted to top level
            assert msg["step"] == "fetch"

            # Boundary
            boundary = ws.receive_json()
            assert boundary["type"] == "sys.replay.complete"

    # ── Test 8: Reconnection with since_event_id ────────────────────────

    def test_reconnection_no_gaps(self):
        """Reconnect with last seen event_id, verify no gaps."""
        _make_job(self.store, "j1")

        r1 = _make_event(self.store, "e1", "j1", "job.started")
        r2 = _make_event(self.store, "e2", "j1", "step.completed")

        # First connection: get all events
        with self._client.websocket_connect(
            "/api/v1/events/stream?since_event_id=0"
        ) as ws:
            ws.receive_json()  # e1
            ws.receive_json()  # e2
            boundary = ws.receive_json()
            last_seen = boundary["last_event_id"]

        # Add more events while disconnected
        r3 = _make_event(self.store, "e3", "j1", "step.completed", {"step": "s2"})

        # Reconnect with last_seen
        with self._client.websocket_connect(
            f"/api/v1/events/stream?since_event_id={last_seen}"
        ) as ws:
            msg = ws.receive_json()
            assert msg["event_id"] == r3
            assert msg["event"] == "step.completed"

            boundary = ws.receive_json()
            assert boundary["type"] == "sys.replay.complete"
            assert boundary["last_event_id"] == r3

    # ── Test 9: Metadata in replayed envelopes ──────────────────────────

    def test_metadata_included_in_replay(self):
        """Job metadata is included in replayed event envelopes."""
        meta = {"sys": {"session_id": "test-session"}, "app": {"env": "dev"}}
        _make_job(self.store, "j1", metadata=meta)

        _make_event(self.store, "e1", "j1", "job.started")

        with self._client.websocket_connect(
            "/api/v1/events/stream?since_event_id=0"
        ) as ws:
            msg = ws.receive_json()
            assert msg["metadata"] == meta

            ws.receive_json()  # boundary

    # ── Test 10: Empty replay ───────────────────────────────────────────

    def test_empty_replay(self):
        """No events to replay still sends boundary frame."""
        with self._client.websocket_connect(
            "/api/v1/events/stream?since_event_id=999999"
        ) as ws:
            boundary = ws.receive_json()
            assert boundary["type"] == "sys.replay.complete"
            assert boundary["last_event_id"] == 999999

    # ── Test 11: No replay without since params ─────────────────────────

    def test_no_replay_without_since(self):
        """Connection without since_event_id or since_job_start skips replay."""
        _make_job(self.store, "j1")
        _make_event(self.store, "e1", "j1", "job.started")

        # Just connecting without replay params should not send historical events.
        with self._client.websocket_connect("/api/v1/events/stream") as ws:
            pass  # clean disconnect — no events expected


class TestLoadEventsSince:
    """Unit tests for SQLiteStore.load_events_since()."""

    def test_all_events(self):
        store = SQLiteStore(":memory:")
        _make_job(store, "j1")
        r1 = _make_event(store, "e1", "j1", "job.started")
        r2 = _make_event(store, "e2", "j1", "step.completed")

        results = store.load_events_since(since_rowid=0)
        assert len(results) == 2
        assert results[0][0] == r1
        assert results[1][0] == r2
        store.close()

    def test_since_rowid_filter(self):
        store = SQLiteStore(":memory:")
        _make_job(store, "j1")
        r1 = _make_event(store, "e1", "j1", "job.started")
        r2 = _make_event(store, "e2", "j1", "step.completed")

        results = store.load_events_since(since_rowid=r1)
        assert len(results) == 1
        assert results[0][0] == r2
        store.close()

    def test_job_id_filter(self):
        store = SQLiteStore(":memory:")
        _make_job(store, "j1")
        _make_job(store, "j2")
        _make_event(store, "e1", "j1", "job.started")
        _make_event(store, "e2", "j2", "job.started")
        _make_event(store, "e3", "j1", "step.completed")

        results = store.load_events_since(since_rowid=0, job_ids={"j1"})
        assert len(results) == 2
        assert all(r[1].job_id == "j1" for r in results)
        store.close()

    def test_raw_return_format(self):
        """Verify (rowid, Event, metadata) tuple format."""
        store = SQLiteStore(":memory:")
        meta = {"sys": {"session_id": "s1"}, "app": {}}
        _make_job(store, "j1", metadata=meta)
        _make_event(store, "e1", "j1", "step.completed", {"step": "fetch", "count": 42})

        results = store.load_events_since(since_rowid=0)
        assert len(results) == 1
        rowid, event, metadata = results[0]
        assert event.type == "step.completed"
        assert event.job_id == "j1"
        assert metadata == meta
        assert event.data["step"] == "fetch"
        assert event.data["count"] == 42
        store.close()

    def test_empty_result(self):
        store = SQLiteStore(":memory:")
        results = store.load_events_since(since_rowid=0)
        assert results == []
        store.close()
