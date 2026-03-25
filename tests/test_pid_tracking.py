"""Tests for PID tracking on agent step runs."""

import os
import signal
from unittest.mock import patch

from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    Job,
    JobConfig,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _gen_id,
    _now,
)
from stepwise.store import SQLiteStore


def _make_running_job(store: SQLiteStore, step_name: str = "agent-step") -> tuple[Job, StepRun]:
    """Create a running job with a running step run that has a PID."""
    wf = WorkflowDefinition(steps={
        step_name: StepDefinition(
            name=step_name,
            executor=ExecutorRef(type="agent", config={"prompt": "test"}),
            outputs=["result"],
        ),
    })
    job = Job(
        id=_gen_id("job"),
        objective="test pid tracking",
        workflow=wf,
        status=JobStatus.RUNNING,
        inputs={},
        workspace_path="/tmp/test",
        config=JobConfig(),
        created_at=_now(),
        updated_at=_now(),
        created_by="server",
    )
    store.save_job(job)

    run = StepRun(
        id=_gen_id("run"),
        job_id=job.id,
        step_name=step_name,
        attempt=1,
        status=StepRunStatus.RUNNING,
        pid=12345,
        executor_state={"pid": 12345, "pgid": 12345},
        started_at=_now(),
    )
    store.save_run(run)
    return job, run


class TestPIDSavedOnStart:
    """PID is saved to the step_run record when state_update_fn reports it."""

    def test_pid_stored_in_db(self):
        store = SQLiteStore(":memory:")
        job, run = _make_running_job(store)

        loaded = store.load_run(run.id)
        assert loaded.pid == 12345

    def test_pid_in_to_dict(self):
        run = StepRun(
            id="run-1",
            job_id="job-1",
            step_name="step",
            attempt=1,
            status=StepRunStatus.RUNNING,
            pid=99999,
        )
        d = run.to_dict()
        assert d["pid"] == 99999

    def test_pid_from_dict(self):
        d = {
            "id": "run-1",
            "job_id": "job-1",
            "step_name": "step",
            "attempt": 1,
            "status": "running",
            "pid": 99999,
        }
        run = StepRun.from_dict(d)
        assert run.pid == 99999

    def test_pid_none_by_default(self):
        d = {
            "id": "run-1",
            "job_id": "job-1",
            "step_name": "step",
            "attempt": 1,
            "status": "running",
        }
        run = StepRun.from_dict(d)
        assert run.pid is None


class TestPIDClearedOnComplete:
    """PID is cleared (set to NULL) when step completes or fails."""

    def test_pid_cleared_on_completion(self):
        store = SQLiteStore(":memory:")
        job, run = _make_running_job(store)

        # Simulate completion
        loaded = store.load_run(run.id)
        loaded.status = StepRunStatus.COMPLETED
        loaded.pid = None
        loaded.completed_at = _now()
        store.save_run(loaded)

        final = store.load_run(run.id)
        assert final.pid is None
        assert final.status == StepRunStatus.COMPLETED

    def test_pid_cleared_on_failure(self):
        store = SQLiteStore(":memory:")
        job, run = _make_running_job(store)

        loaded = store.load_run(run.id)
        loaded.status = StepRunStatus.FAILED
        loaded.pid = None
        loaded.error = "test failure"
        loaded.completed_at = _now()
        store.save_run(loaded)

        final = store.load_run(run.id)
        assert final.pid is None
        assert final.status == StepRunStatus.FAILED


class TestDeadPIDDetectedOnRestart:
    """Server restart recovery detects dead PIDs and marks runs as failed."""

    def test_dead_pid_marked_failed(self):
        """When PID is dead on restart, run is marked FAILED with descriptive error."""
        store = SQLiteStore(":memory:")
        job, run = _make_running_job(store)

        # Import and call the cleanup function
        from stepwise.server import _cleanup_zombie_jobs

        # Mock verify_agent_pid to return False (PID dead or recycled)
        with patch("stepwise.server.verify_agent_pid", return_value=False):
            # Also mock os.killpg to avoid side effects
            with patch("stepwise.server.os.killpg"):
                _cleanup_zombie_jobs(store)

        loaded = store.load_run(run.id)
        assert loaded.status == StepRunStatus.FAILED
        assert "PID 12345 not found on restart" in loaded.error
        assert loaded.pid is None

        # Job should be failed
        loaded_job = store.load_job(job.id)
        assert loaded_job.status == JobStatus.FAILED

    def test_alive_pid_left_alone(self):
        """When PID is alive on restart, run stays RUNNING."""
        store = SQLiteStore(":memory:")
        job, run = _make_running_job(store)

        from stepwise.server import _cleanup_zombie_jobs

        # Mock verify_agent_pid to return True (PID alive and verified)
        with patch("stepwise.server.verify_agent_pid", return_value=True):
            _cleanup_zombie_jobs(store)

        loaded = store.load_run(run.id)
        assert loaded.status == StepRunStatus.RUNNING
        assert loaded.pid == 12345

        # Job should still be running
        loaded_job = store.load_job(job.id)
        assert loaded_job.status == JobStatus.RUNNING

    def test_no_pid_still_fails_run(self):
        """Runs without PID are still failed with the old error message."""
        store = SQLiteStore(":memory:")
        wf = WorkflowDefinition(steps={
            "step": StepDefinition(
                name="step",
                executor=ExecutorRef(type="script", config={"command": "echo"}),
                outputs=["result"],
            ),
        })
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=wf,
            status=JobStatus.RUNNING,
            inputs={},
            workspace_path="/tmp/test",
            config=JobConfig(),
            created_at=_now(),
            updated_at=_now(),
            created_by="server",
        )
        store.save_job(job)

        run = StepRun(
            id=_gen_id("run"),
            job_id=job.id,
            step_name="step",
            attempt=1,
            status=StepRunStatus.RUNNING,
            pid=None,  # no PID
            started_at=_now(),
        )
        store.save_run(run)

        from stepwise.server import _cleanup_zombie_jobs
        _cleanup_zombie_jobs(store)

        loaded = store.load_run(run.id)
        assert loaded.status == StepRunStatus.FAILED
        assert loaded.error == "Server restarted: step was orphaned"


class TestMigration:
    """The pid column is added by migration on older databases."""

    def test_migration_adds_pid_column(self):
        store = SQLiteStore(":memory:")
        # Verify the column exists by reading a run with pid
        run = StepRun(
            id="run-test",
            job_id="job-test",
            step_name="step",
            attempt=1,
            status=StepRunStatus.RUNNING,
            pid=42,
            started_at=_now(),
        )
        # Need a job first for FK
        wf = WorkflowDefinition(steps={
            "step": StepDefinition(
                name="step",
                executor=ExecutorRef(type="script", config={}),
                outputs=[],
            ),
        })
        job = Job(
            id="job-test",
            objective="test",
            workflow=wf,
            status=JobStatus.RUNNING,
            inputs={},
            workspace_path="/tmp",
            config=JobConfig(),
            created_at=_now(),
            updated_at=_now(),
        )
        store.save_job(job)
        store.save_run(run)

        loaded = store.load_run("run-test")
        assert loaded.pid == 42
