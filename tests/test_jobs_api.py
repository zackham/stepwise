"""Tests for /api/jobs endpoint validation."""

import os
import time
import pytest
from starlette.testclient import TestClient

import stepwise.server as srv
from stepwise.server import app
from stepwise.models import (
    ExecutorRef,
    Job,
    JobStatus,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _gen_id,
    _now,
)
from stepwise.store import SQLiteStore


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


class TestListJobsStatusValidation:
    def test_invalid_status_returns_400(self, client):
        resp = client.get("/api/jobs", params={"status": "suspended"})
        assert resp.status_code == 400
        body = resp.json()
        assert "Invalid status" in body["detail"]
        assert "suspended" in body["detail"]

    def test_valid_status_returns_200(self, client):
        resp = client.get("/api/jobs", params={"status": "running"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_no_status_returns_200(self, client):
        resp = client.get("/api/jobs")
        assert resp.status_code == 200


class TestStartupReapBeforeFirstCreateJob:
    """Regression: the first create_job after a fresh server start (with zombie
    runs left from a prior crash) must not race with the periodic health check's
    reap_dead_processes for the SQLite write lock. Reaping must happen
    synchronously before the HTTP listener accepts connections."""

    def test_reap_called_during_lifespan_startup(self, tmp_path, monkeypatch):
        # Spy on reap_dead_processes to verify it runs synchronously inside
        # the lifespan startup phase — i.e. before TestClient yields and any
        # HTTP request can race with it.
        import stepwise.server as srv_mod
        real_reap = srv_mod.reap_dead_processes if hasattr(srv_mod, "reap_dead_processes") else None

        from stepwise import process_lifecycle as plc
        original = plc.reap_dead_processes
        call_log: list[str] = []

        def spy_reap(store, engine):
            call_log.append("called")
            return original(store, engine)

        monkeypatch.setattr(plc, "reap_dead_processes", spy_reap)

        old_env = os.environ.copy()
        os.environ["STEPWISE_PROJECT_DIR"] = str(tmp_path)
        os.environ["STEPWISE_DB"] = str(tmp_path / "stepwise.db")
        os.environ["STEPWISE_TEMPLATES"] = str(tmp_path / "_templates")
        os.environ["STEPWISE_JOBS_DIR"] = str(tmp_path / "_jobs")
        try:
            with TestClient(app, raise_server_exceptions=False) as c:
                # By the time TestClient enters the context, lifespan startup
                # has yielded — meaning the eager reap must already have run.
                assert call_log, (
                    "reap_dead_processes was not invoked during lifespan startup; "
                    "first create_job would race with the periodic health check"
                )
                # First request must not wedge.
                start = time.monotonic()
                resp = c.get("/api/jobs", params={"status": "running"})
                elapsed = time.monotonic() - start
                assert resp.status_code == 200
                assert elapsed < 1.0, f"first request took {elapsed:.2f}s"
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def test_dead_pid_runs_reaped_before_first_request(self, tmp_path):
        db_path = str(tmp_path / "stepwise.db")

        # Pre-seed the DB with a RUNNING job containing zombie runs whose
        # PIDs are guaranteed dead. Use a non-script executor type and a
        # suspended sibling step so neither _cleanup_zombie_jobs (skips
        # jobs with suspended steps) nor _recover_dead_script_runs (script
        # only) touches the dead-PID runs — only the eager reap closes the gap.
        seed = SQLiteStore(db_path)
        wf = WorkflowDefinition(steps={
            "alive-step": StepDefinition(
                name="alive-step",
                executor=ExecutorRef(type="external", config={}),
                outputs=["result"],
            ),
            "dead-step-1": StepDefinition(
                name="dead-step-1",
                executor=ExecutorRef(type="mock_llm", config={}),
                outputs=["result"],
            ),
            "dead-step-2": StepDefinition(
                name="dead-step-2",
                executor=ExecutorRef(type="mock_llm", config={}),
                outputs=["result"],
            ),
        })
        job = Job(
            id=_gen_id("job"),
            objective="zombie",
            workflow=wf,
            status=JobStatus.RUNNING,
            inputs={},
            created_by="server",
        )
        seed.save_job(job)

        # Suspended sibling forces _cleanup_zombie_jobs to skip the job.
        suspended_run = StepRun(
            id=_gen_id("run"),
            job_id=job.id,
            step_name="alive-step",
            attempt=1,
            status=StepRunStatus.SUSPENDED,
            started_at=_now(),
        )
        seed.save_run(suspended_run)

        dead_run_ids = []
        for i, step in enumerate(("dead-step-1", "dead-step-2")):
            run = StepRun(
                id=_gen_id("run"),
                job_id=job.id,
                step_name=step,
                attempt=1,
                status=StepRunStatus.RUNNING,
                pid=99000000 + i,
                started_at=_now(),
            )
            seed.save_run(run)
            dead_run_ids.append(run.id)
        seed.close()

        old_env = os.environ.copy()
        os.environ["STEPWISE_PROJECT_DIR"] = str(tmp_path)
        os.environ["STEPWISE_DB"] = db_path
        os.environ["STEPWISE_TEMPLATES"] = str(tmp_path / "_templates")
        os.environ["STEPWISE_JOBS_DIR"] = str(tmp_path / "_jobs")
        try:
            with TestClient(app, raise_server_exceptions=False) as c:
                # First request after startup — should not wedge.
                start = time.monotonic()
                resp = c.get("/api/jobs", params={"status": "running"})
                elapsed = time.monotonic() - start
                assert resp.status_code == 200
                assert elapsed < 1.0, f"first request took {elapsed:.2f}s"

                # All zombie dead-PID runs must already be FAILED (reaped
                # during lifespan, not deferred to the periodic timer).
                verify = SQLiteStore(db_path)
                try:
                    for rid in dead_run_ids:
                        run = verify.load_run(rid)
                        assert run.status == StepRunStatus.FAILED, (
                            f"run {rid} status={run.status} — not reaped at startup"
                        )
                        assert run.pid is None
                finally:
                    verify.close()
        finally:
            os.environ.clear()
            os.environ.update(old_env)
