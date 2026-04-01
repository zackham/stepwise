"""Tests for /api/jobs/{job_id}/sessions endpoints."""

import os
import pytest
from datetime import datetime, timezone
from starlette.testclient import TestClient

import stepwise.server as srv
from stepwise.server import app
from stepwise.models import StepRun, StepRunStatus

NDJSON_SAMPLE = '{"params":{"update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"Hello"}}}}\n'


@pytest.fixture
def client(tmp_path):
    old_env = os.environ.copy()
    os.environ["STEPWISE_PROJECT_DIR"] = str(tmp_path)
    os.environ["STEPWISE_DB"] = ":memory:"
    os.environ["STEPWISE_TEMPLATES"] = str(tmp_path / "_templates")
    os.environ["STEPWISE_JOBS_DIR"] = str(tmp_path / "_jobs")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    os.environ.clear()
    os.environ.update(old_env)


def _create_job_with_session_runs(client, tmp_path, session_configs):
    """Helper: create a job, then insert runs with crafted executor_state."""
    resp = client.post("/api/jobs", json={
        "objective": "test",
        "workflow": {"steps": {"s": {"name": "s", "executor": {"type": "agent"}, "outputs": []}}},
    })
    job_id = resp.json()["id"]

    engine = srv._engine
    for i, cfg in enumerate(session_configs):
        output_path = None
        if cfg.get("output_content"):
            p = tmp_path / f"output-{i}.ndjson"
            p.write_text(cfg["output_content"])
            output_path = str(p)
        run = StepRun(
            id=f"run-{i}",
            job_id=job_id,
            step_name=cfg["step_name"],
            attempt=cfg.get("attempt", 1),
            status=StepRunStatus(cfg.get("status", "completed")),
            executor_state={
                "session_name": cfg["session_name"],
                **({"output_path": output_path} if output_path else {}),
            },
            started_at=datetime(2024, 1, 1, 0, i, tzinfo=timezone.utc),
            completed_at=datetime(2024, 1, 1, 0, i + 1, tzinfo=timezone.utc)
                if cfg.get("status", "completed") == "completed" else None,
        )
        engine.store.save_run(run)
    return job_id


class TestSessionListing:
    def test_no_sessions_returns_empty(self, client, tmp_path):
        resp = client.post("/api/jobs", json={
            "objective": "test",
            "workflow": {"steps": {"s": {"name": "s", "executor": {"type": "script"}, "outputs": []}}},
        })
        job_id = resp.json()["id"]
        resp = client.get(f"/api/jobs/{job_id}/sessions")
        assert resp.status_code == 200
        assert resp.json() == {"sessions": []}

    def test_single_session_two_runs(self, client, tmp_path):
        job_id = _create_job_with_session_runs(client, tmp_path, [
            {"step_name": "plan", "session_name": "sess-1", "status": "completed"},
            {"step_name": "impl", "session_name": "sess-1", "status": "completed"},
        ])
        resp = client.get(f"/api/jobs/{job_id}/sessions")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["session_name"] == "sess-1"
        assert sessions[0]["run_ids"] == ["run-0", "run-1"]
        assert sessions[0]["step_names"] == ["plan", "impl"]
        assert sessions[0]["is_active"] is False

    def test_multiple_sessions(self, client, tmp_path):
        job_id = _create_job_with_session_runs(client, tmp_path, [
            {"step_name": "plan", "session_name": "sess-1", "status": "completed"},
            {"step_name": "impl", "session_name": "sess-1", "status": "completed"},
            {"step_name": "review", "session_name": "sess-2", "status": "completed"},
        ])
        resp = client.get(f"/api/jobs/{job_id}/sessions")
        sessions = resp.json()["sessions"]
        assert len(sessions) == 2
        assert sessions[0]["session_name"] == "sess-1"
        assert sessions[1]["session_name"] == "sess-2"

    def test_active_session_detection(self, client, tmp_path):
        job_id = _create_job_with_session_runs(client, tmp_path, [
            {"step_name": "plan", "session_name": "sess-1", "status": "completed"},
            {"step_name": "impl", "session_name": "sess-1", "status": "running"},
        ])
        resp = client.get(f"/api/jobs/{job_id}/sessions")
        sessions = resp.json()["sessions"]
        assert sessions[0]["is_active"] is True

    def test_nonexistent_job_returns_404(self, client):
        resp = client.get("/api/jobs/nonexistent/sessions")
        assert resp.status_code == 404


class TestSessionTranscript:
    def test_transcript_ordering_and_boundaries(self, client, tmp_path):
        job_id = _create_job_with_session_runs(client, tmp_path, [
            {"step_name": "plan", "session_name": "sess-1",
             "output_content": NDJSON_SAMPLE},
            {"step_name": "impl", "session_name": "sess-1",
             "output_content": NDJSON_SAMPLE + NDJSON_SAMPLE},
        ])
        resp = client.get(f"/api/jobs/{job_id}/sessions/sess-1/transcript")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 3
        assert len(data["boundaries"]) == 2
        assert data["boundaries"][0]["event_index"] == 0
        assert data["boundaries"][0]["step_name"] == "plan"
        assert data["boundaries"][1]["event_index"] == 1
        assert data["boundaries"][1]["step_name"] == "impl"

    def test_missing_output_file(self, client, tmp_path):
        job_id = _create_job_with_session_runs(client, tmp_path, [
            {"step_name": "plan", "session_name": "sess-1",
             "output_content": NDJSON_SAMPLE},
        ])
        runs = srv._engine.store.runs_for_job(job_id)
        session_run = [r for r in runs if (r.executor_state or {}).get("session_name") == "sess-1"][0]
        os.unlink(session_run.executor_state["output_path"])

        resp = client.get(f"/api/jobs/{job_id}/sessions/sess-1/transcript")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 0
        assert len(data["boundaries"]) == 1

    def test_nonexistent_session_returns_404(self, client, tmp_path):
        resp = client.post("/api/jobs", json={
            "objective": "test",
            "workflow": {"steps": {"s": {"name": "s", "executor": {"type": "agent"}, "outputs": []}}},
        })
        job_id = resp.json()["id"]
        resp = client.get(f"/api/jobs/{job_id}/sessions/nonexistent/transcript")
        assert resp.status_code == 404

    def test_url_encoded_session_name(self, client, tmp_path):
        job_id = _create_job_with_session_runs(client, tmp_path, [
            {"step_name": "plan", "session_name": "step-job/123-plan-1",
             "output_content": NDJSON_SAMPLE},
        ])
        resp = client.get(f"/api/jobs/{job_id}/sessions/step-job%2F123-plan-1/transcript")
        assert resp.status_code == 200
