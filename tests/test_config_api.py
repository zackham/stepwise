"""Tests for the /api/config/* endpoints added in the 1.0 settings surface.

Covers the fields that existed in StepwiseConfig but had no REST surface
before the 2026-04-15 settings overhaul: notify_url/notify_context,
agent_permissions, agent_process_ttl, max_concurrent_jobs. The
pre-existing per-agent concurrency + containment endpoints have their
own tests elsewhere; this file fills the gaps.
"""

import os
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

import stepwise.server as srv
from stepwise.server import app


@pytest.fixture
def project_dir(tmp_path):
    (tmp_path / ".stepwise").mkdir()
    return tmp_path


@pytest.fixture
def client(project_dir):
    old_env = os.environ.copy()
    os.environ["STEPWISE_PROJECT_DIR"] = str(project_dir)
    os.environ["STEPWISE_DB"] = ":memory:"
    os.environ["STEPWISE_TEMPLATES"] = str(project_dir / "_templates")
    os.environ["STEPWISE_JOBS_DIR"] = str(project_dir / "_jobs")

    with TestClient(app, raise_server_exceptions=False) as c:
        assert srv._project_dir == project_dir.resolve()
        yield c

    os.environ.clear()
    os.environ.update(old_env)


def _local_yaml(project_dir: Path) -> dict:
    path = project_dir / ".stepwise" / "config.local.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


# ── GET /api/config surfaces the new fields ──────────────────────────


class TestConfigResponseShape:
    def test_new_fields_present_with_defaults(self, client):
        resp = client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        # All five new fields MUST appear in the response, even at default
        assert "agent_permissions" in data
        assert data["agent_permissions"] == "approve_all"
        assert "agent_process_ttl" in data
        assert data["agent_process_ttl"] == 0
        assert "max_concurrent_jobs" in data
        assert data["max_concurrent_jobs"] == 10
        assert "notify_url" in data
        assert data["notify_url"] is None
        assert "notify_context" in data
        assert data["notify_context"] == {}


# ── PUT /api/config/max-concurrent-jobs ──────────────────────────────


class TestMaxConcurrentJobs:
    def test_set_and_persist(self, client, project_dir):
        resp = client.put(
            "/api/config/max-concurrent-jobs", json={"limit": 25}
        )
        assert resp.status_code == 200
        assert resp.json()["max_concurrent_jobs"] == 25

        # Persisted to local config
        assert _local_yaml(project_dir)["max_concurrent_jobs"] == 25

        # Visible via GET
        assert client.get("/api/config").json()["max_concurrent_jobs"] == 25

    def test_zero_removes_from_file(self, client, project_dir):
        client.put("/api/config/max-concurrent-jobs", json={"limit": 50})
        assert _local_yaml(project_dir).get("max_concurrent_jobs") == 50

        resp = client.put(
            "/api/config/max-concurrent-jobs", json={"limit": 0}
        )
        assert resp.status_code == 200
        # 0 means "use default" — field removed from file, reverts to 10
        assert "max_concurrent_jobs" not in _local_yaml(project_dir)
        assert resp.json()["max_concurrent_jobs"] == 10

    def test_negative_rejected(self, client):
        resp = client.put(
            "/api/config/max-concurrent-jobs", json={"limit": -1}
        )
        assert resp.status_code == 400

    def test_engine_picks_up_new_limit(self, client):
        client.put("/api/config/max-concurrent-jobs", json={"limit": 7})
        # Live engine should reflect the new cap without a restart
        assert srv._engine is not None
        assert srv._engine.max_concurrent_jobs == 7


# ── PUT /api/config/agent-process-ttl ────────────────────────────────


class TestAgentProcessTtl:
    def test_set_and_persist(self, client, project_dir):
        resp = client.put(
            "/api/config/agent-process-ttl", json={"ttl_seconds": 3600}
        )
        assert resp.status_code == 200
        assert resp.json()["agent_process_ttl"] == 3600
        assert _local_yaml(project_dir)["agent_process_ttl"] == 3600

    def test_zero_removes(self, client, project_dir):
        client.put("/api/config/agent-process-ttl", json={"ttl_seconds": 1200})
        assert _local_yaml(project_dir).get("agent_process_ttl") == 1200

        resp = client.put(
            "/api/config/agent-process-ttl", json={"ttl_seconds": 0}
        )
        assert resp.status_code == 200
        assert "agent_process_ttl" not in _local_yaml(project_dir)

    def test_negative_rejected(self, client):
        resp = client.put(
            "/api/config/agent-process-ttl", json={"ttl_seconds": -5}
        )
        assert resp.status_code == 400


# ── PUT /api/config/agent-permissions ────────────────────────────────


class TestAgentPermissions:
    @pytest.mark.parametrize("mode", ["approve_all", "prompt", "deny"])
    def test_valid_modes(self, client, project_dir, mode):
        resp = client.put(
            "/api/config/agent-permissions", json={"permissions": mode}
        )
        assert resp.status_code == 200
        assert resp.json()["agent_permissions"] == mode

    def test_approve_all_removes_from_file(self, client, project_dir):
        # First set to non-default
        client.put("/api/config/agent-permissions", json={"permissions": "deny"})
        assert _local_yaml(project_dir).get("agent_permissions") == "deny"

        # Reset to default — should remove from file
        client.put(
            "/api/config/agent-permissions", json={"permissions": "approve_all"}
        )
        assert "agent_permissions" not in _local_yaml(project_dir)

    def test_invalid_mode_rejected(self, client):
        resp = client.put(
            "/api/config/agent-permissions", json={"permissions": "yolo"}
        )
        assert resp.status_code == 400


# ── PUT /api/config/notify-webhook ───────────────────────────────────


class TestNotifyWebhook:
    def test_set_url_and_context(self, client, project_dir):
        resp = client.put(
            "/api/config/notify-webhook",
            json={
                "url": "https://hooks.slack.com/services/T/B/xxx",
                "context": {"channel": "#stepwise-alerts"},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["notify_url"] == "https://hooks.slack.com/services/T/B/xxx"
        assert data["notify_context"] == {"channel": "#stepwise-alerts"}

        local = _local_yaml(project_dir)
        assert local["notify_url"] == "https://hooks.slack.com/services/T/B/xxx"
        assert local["notify_context"] == {"channel": "#stepwise-alerts"}

    def test_clear_url_removes_both_fields(self, client, project_dir):
        client.put(
            "/api/config/notify-webhook",
            json={"url": "https://example.com/hook", "context": {"k": "v"}},
        )

        resp = client.put(
            "/api/config/notify-webhook", json={"url": "", "context": {}}
        )
        assert resp.status_code == 200
        local = _local_yaml(project_dir)
        assert "notify_url" not in local
        assert "notify_context" not in local

    def test_url_scheme_validated(self, client):
        resp = client.put(
            "/api/config/notify-webhook",
            json={"url": "ftp://bad.example.com", "context": {}},
        )
        assert resp.status_code == 400

    def test_null_url_clears(self, client, project_dir):
        client.put(
            "/api/config/notify-webhook",
            json={"url": "https://example.com/hook", "context": {}},
        )
        resp = client.put(
            "/api/config/notify-webhook", json={"url": None, "context": None}
        )
        assert resp.status_code == 200
        assert resp.json()["notify_url"] is None
