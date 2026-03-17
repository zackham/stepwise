"""Tests for webhook notification (--notify) support."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from stepwise.engine import Engine
from stepwise.hooks import fire_notify_webhook
from stepwise.models import Job, JobStatus, WorkflowDefinition, StepDefinition, ExecutorRef
from stepwise.store import SQLiteStore


# ── Helpers ───────────────────────────────────────────────────────────


def _minimal_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        steps={
            "step1": StepDefinition(
                name="step1",
                outputs=["result"],
                executor=ExecutorRef(type="mock", config={}),
            ),
        },
    )


class WebhookCapture:
    """Run a tiny HTTP server to capture webhook POSTs."""

    def __init__(self):
        self.payloads: list[dict] = []
        self._server = None
        self._thread = None

    def start(self) -> str:
        capture = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                capture.payloads.append(json.loads(body))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *args):
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{port}/webhook"

    def stop(self):
        if self._server:
            self._server.shutdown()

    def wait_for(self, count: int, timeout: float = 5.0):
        import time
        deadline = time.monotonic() + timeout
        while len(self.payloads) < count and time.monotonic() < deadline:
            time.sleep(0.05)


@pytest.fixture
def webhook():
    cap = WebhookCapture()
    yield cap
    cap.stop()


# ── Unit tests: fire_notify_webhook ──────────────────────────────────


class TestFireNotifyWebhook:
    def test_posts_to_url(self, webhook):
        url = webhook.start()
        fire_notify_webhook(
            event_type="step.suspended",
            event_data={"step": "approve", "run_id": "run-abc"},
            job_id="job-123",
            notify_url=url,
        )
        webhook.wait_for(1)
        assert len(webhook.payloads) == 1
        p = webhook.payloads[0]
        assert p["event"] == "step.suspended"
        assert p["job_id"] == "job-123"
        assert p["step"] == "approve"
        assert p["run_id"] == "run-abc"
        assert "timestamp" in p

    def test_includes_notify_context(self, webhook):
        url = webhook.start()
        ctx = {"telegram_thread_id": 12345, "project": "cadence"}
        fire_notify_webhook(
            event_type="job.completed",
            event_data={},
            job_id="job-456",
            notify_url=url,
            notify_context=ctx,
        )
        webhook.wait_for(1)
        assert webhook.payloads[0]["context"] == ctx

    def test_fires_for_any_event(self, webhook):
        url = webhook.start()
        fire_notify_webhook(
            event_type="step.started",
            event_data={},
            job_id="job-789",
            notify_url=url,
        )
        webhook.wait_for(1)
        assert len(webhook.payloads) == 1

    def test_handles_unreachable_url(self):
        """Should log warning but not raise."""
        fire_notify_webhook(
            event_type="job.failed",
            event_data={"error": "boom"},
            job_id="job-err",
            notify_url="http://127.0.0.1:1/nope",
        )


# ── Model tests ──────────────────────────────────────────────────────


class TestJobNotifyFields:
    def test_defaults_to_none(self):
        job = Job(id="j1", objective="test", workflow=_minimal_workflow())
        assert job.notify_url is None
        assert job.notify_context == {}

    def test_to_dict_omits_when_none(self):
        job = Job(id="j1", objective="test", workflow=_minimal_workflow())
        d = job.to_dict()
        assert "notify_url" not in d

    def test_to_dict_includes_when_set(self):
        job = Job(
            id="j1", objective="test", workflow=_minimal_workflow(),
            notify_url="http://example.com/hook",
            notify_context={"key": "value"},
        )
        d = job.to_dict()
        assert d["notify_url"] == "http://example.com/hook"
        assert d["notify_context"] == {"key": "value"}

    def test_roundtrip(self):
        job = Job(
            id="j1", objective="test", workflow=_minimal_workflow(),
            notify_url="http://example.com/hook",
            notify_context={"thread_id": 42},
        )
        d = job.to_dict()
        restored = Job.from_dict(d)
        assert restored.notify_url == "http://example.com/hook"
        assert restored.notify_context == {"thread_id": 42}

    def test_from_dict_without_notify(self):
        d = Job(id="j1", objective="test", workflow=_minimal_workflow()).to_dict()
        restored = Job.from_dict(d)
        assert restored.notify_url is None
        assert restored.notify_context == {}


# ── Store tests ──────────────────────────────────────────────────────


class TestStoreNotify:
    def test_save_and_load_with_notify(self, tmp_path):
        store = SQLiteStore(str(tmp_path / "test.db"))
        job = Job(
            id="j1", objective="test", workflow=_minimal_workflow(),
            notify_url="http://localhost:8080/events",
            notify_context={"telegram_thread_id": 999},
        )
        store.save_job(job)
        loaded = store.load_job("j1")
        assert loaded.notify_url == "http://localhost:8080/events"
        assert loaded.notify_context == {"telegram_thread_id": 999}
        store.close()

    def test_save_and_load_without_notify(self, tmp_path):
        store = SQLiteStore(str(tmp_path / "test.db"))
        job = Job(id="j2", objective="test", workflow=_minimal_workflow())
        store.save_job(job)
        loaded = store.load_job("j2")
        assert loaded.notify_url is None
        assert loaded.notify_context == {}
        store.close()

    def test_update_notify_url(self, tmp_path):
        store = SQLiteStore(str(tmp_path / "test.db"))
        job = Job(id="j3", objective="test", workflow=_minimal_workflow())
        store.save_job(job)
        job.notify_url = "http://localhost:9090/hook"
        job.notify_context = {"key": "val"}
        store.save_job(job)
        loaded = store.load_job("j3")
        assert loaded.notify_url == "http://localhost:9090/hook"
        assert loaded.notify_context == {"key": "val"}
        store.close()

    def test_migration_adds_columns(self, tmp_path):
        import sqlite3
        db_path = str(tmp_path / "old.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY, objective TEXT, workflow TEXT,
                status TEXT, inputs TEXT, parent_job_id TEXT,
                parent_step_run_id TEXT, workspace_path TEXT,
                config TEXT, created_at TEXT, updated_at TEXT,
                created_by TEXT DEFAULT 'server',
                runner_pid INTEGER, heartbeat_at TEXT
            )
        """)
        conn.commit()
        conn.close()
        store = SQLiteStore(db_path)
        job = Job(
            id="j-migrated", objective="test", workflow=_minimal_workflow(),
            notify_url="http://test.com",
        )
        store.save_job(job)
        loaded = store.load_job("j-migrated")
        assert loaded.notify_url == "http://test.com"
        store.close()


# ── Integration: engine _emit fires webhook ──────────────────────────


class TestEngineNotifyIntegration:
    def test_emit_fires_webhook_for_suspend(self, tmp_path, webhook):
        url = webhook.start()
        store = SQLiteStore(str(tmp_path / "test.db"))
        job = Job(
            id="j-int", objective="test", workflow=_minimal_workflow(),
            status=JobStatus.RUNNING,
            notify_url=url,
            notify_context={"thread": 42},
        )
        store.save_job(job)
        engine = Engine(store)
        engine._emit("j-int", "step.suspended", {"step": "approve", "run_id": "run-1"})
        webhook.wait_for(1)
        assert len(webhook.payloads) == 1
        p = webhook.payloads[0]
        assert p["event"] == "step.suspended"
        assert p["context"]["thread"] == 42
        assert p["run_id"] == "run-1"
        store.close()

    def test_emit_skips_webhook_when_not_configured(self, tmp_path, webhook):
        url = webhook.start()
        store = SQLiteStore(str(tmp_path / "test.db"))
        job = Job(
            id="j-no-notify", objective="test", workflow=_minimal_workflow(),
            status=JobStatus.RUNNING,
        )
        store.save_job(job)
        engine = Engine(store)
        engine._emit("j-no-notify", "step.suspended", {"step": "s1", "run_id": "r1"})
        import time
        time.sleep(0.2)
        assert len(webhook.payloads) == 0
        store.close()

    def test_emit_fires_on_job_completed(self, tmp_path, webhook):
        url = webhook.start()
        store = SQLiteStore(str(tmp_path / "test.db"))
        job = Job(
            id="j-done", objective="test", workflow=_minimal_workflow(),
            status=JobStatus.RUNNING,
            notify_url=url,
        )
        store.save_job(job)
        engine = Engine(store)
        engine._emit("j-done", "job.completed", {"outputs": {"result": "success"}})
        webhook.wait_for(1)
        assert webhook.payloads[0]["event"] == "job.completed"
        store.close()

    def test_emit_fires_on_job_failed(self, tmp_path, webhook):
        url = webhook.start()
        store = SQLiteStore(str(tmp_path / "test.db"))
        job = Job(
            id="j-fail", objective="test", workflow=_minimal_workflow(),
            status=JobStatus.RUNNING,
            notify_url=url,
        )
        store.save_job(job)
        engine = Engine(store)
        engine._emit("j-fail", "job.failed", {"error": "something went wrong"})
        webhook.wait_for(1)
        assert webhook.payloads[0]["event"] == "job.failed"
        assert webhook.payloads[0]["error"] == "something went wrong"
        store.close()
