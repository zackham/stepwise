"""Tests for job ownership, heartbeat, stale detection, and adoption."""

import os
import time
from datetime import datetime, timedelta, timezone

from stepwise.models import (
    Job,
    JobConfig,
    JobStatus,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    ExecutorRef,
    InputBinding,
    _now,
)
from stepwise.store import SQLiteStore

from tests.conftest import register_step_fn, run_job_sync


def _simple_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(steps={
        "step-a": StepDefinition(
            name="step-a",
            executor=ExecutorRef(type="callable", config={"fn_name": "pass_through"}),
            outputs=["x"],
        ),
    })


class TestJobOwnership:
    """Tests for created_by, runner_pid, heartbeat_at fields."""

    def test_job_defaults_to_server_owner(self, store):
        """Jobs default to created_by='server' with no PID."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        wf = _simple_workflow()
        job = engine.create_job(objective="test", workflow=wf)
        loaded = store.load_job(job.id)
        assert loaded.created_by == "server"
        assert loaded.runner_pid is None
        assert loaded.heartbeat_at is None

    def test_job_stores_cli_owner(self, store):
        """Jobs store created_by and runner_pid when set."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        wf = _simple_workflow()
        job = engine.create_job(objective="test", workflow=wf)
        job.created_by = f"cli:{os.getpid()}"
        job.runner_pid = os.getpid()
        store.save_job(job)

        loaded = store.load_job(job.id)
        assert loaded.created_by == f"cli:{os.getpid()}"
        assert loaded.runner_pid == os.getpid()

    def test_heartbeat_updates_timestamp(self, store):
        """store.heartbeat() updates heartbeat_at."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        wf = _simple_workflow()
        job = engine.create_job(objective="test", workflow=wf)

        assert store.load_job(job.id).heartbeat_at is None
        store.heartbeat(job.id)
        loaded = store.load_job(job.id)
        assert loaded.heartbeat_at is not None
        assert (datetime.now(timezone.utc) - loaded.heartbeat_at).total_seconds() < 5

    def test_heartbeat_updates_on_repeated_calls(self, store):
        """Multiple heartbeat calls update the timestamp."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        wf = _simple_workflow()
        job = engine.create_job(objective="test", workflow=wf)

        store.heartbeat(job.id)
        first = store.load_job(job.id).heartbeat_at

        time.sleep(0.01)
        store.heartbeat(job.id)
        second = store.load_job(job.id).heartbeat_at

        assert second >= first

    def test_ownership_persists_through_serialization(self, store):
        """to_dict/from_dict round-trips ownership fields."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        wf = _simple_workflow()
        job = engine.create_job(objective="test", workflow=wf)
        job.created_by = "cli:12345"
        job.runner_pid = 12345
        job.heartbeat_at = _now()
        store.save_job(job)

        loaded = store.load_job(job.id)
        d = loaded.to_dict()
        assert d["created_by"] == "cli:12345"
        assert d["runner_pid"] == 12345
        assert d["heartbeat_at"] is not None

        restored = Job.from_dict(d)
        assert restored.created_by == "cli:12345"
        assert restored.runner_pid == 12345


class TestStaleDetection:
    """Tests for stale job detection."""

    def _make_running_cli_job(self, store, engine, pid=99999):
        wf = _simple_workflow()
        job = engine.create_job(objective="test-stale", workflow=wf)
        job.status = JobStatus.RUNNING
        job.created_by = f"cli:{pid}"
        job.runner_pid = pid
        store.save_job(job)
        return job

    def test_stale_job_detected_when_no_heartbeat(self, store):
        """RUNNING CLI job with no heartbeat is stale."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        job = self._make_running_cli_job(store, engine)

        stale = store.stale_jobs(max_age_seconds=60)
        assert any(j.id == job.id for j in stale)

    def test_stale_job_detected_when_heartbeat_old(self, store):
        """RUNNING CLI job with old heartbeat is stale."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        job = self._make_running_cli_job(store, engine)

        # Set heartbeat to 5 minutes ago
        old_time = (_now() - timedelta(minutes=5)).isoformat()
        store._conn.execute(
            "UPDATE jobs SET heartbeat_at=? WHERE id=?", (old_time, job.id)
        )
        store._conn.commit()

        stale = store.stale_jobs(max_age_seconds=60)
        assert any(j.id == job.id for j in stale)

    def test_fresh_heartbeat_not_stale(self, store):
        """RUNNING CLI job with fresh heartbeat is not stale."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        job = self._make_running_cli_job(store, engine)
        store.heartbeat(job.id)

        stale = store.stale_jobs(max_age_seconds=60)
        assert not any(j.id == job.id for j in stale)

    def test_server_owned_never_stale(self, store):
        """RUNNING server-owned jobs are never stale."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        wf = _simple_workflow()
        job = engine.create_job(objective="test-server", workflow=wf)
        job.status = JobStatus.RUNNING
        job.created_by = "server"
        store.save_job(job)

        stale = store.stale_jobs(max_age_seconds=0)  # Even with 0s threshold
        assert not any(j.id == job.id for j in stale)

    def test_completed_job_not_stale(self, store):
        """Completed jobs are not stale regardless of heartbeat."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        wf = _simple_workflow()
        job = engine.create_job(objective="test-done", workflow=wf)
        job.status = JobStatus.COMPLETED
        job.created_by = "cli:99999"
        store.save_job(job)

        stale = store.stale_jobs(max_age_seconds=0)
        assert not any(j.id == job.id for j in stale)


class TestRunningJobs:
    """Tests for running_jobs() with owner filter."""

    def test_running_jobs_excludes_owner(self, store):
        """running_jobs(exclude_owner='server') excludes server jobs."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        wf = _simple_workflow()

        server_job = engine.create_job(objective="server-job", workflow=wf)
        server_job.status = JobStatus.RUNNING
        server_job.created_by = "server"
        store.save_job(server_job)

        cli_job = engine.create_job(objective="cli-job", workflow=wf)
        cli_job.status = JobStatus.RUNNING
        cli_job.created_by = "cli:1234"
        store.save_job(cli_job)

        external = store.running_jobs(exclude_owner="server")
        ids = [j.id for j in external]
        assert cli_job.id in ids
        assert server_job.id not in ids

    def test_running_jobs_no_filter(self, store):
        """running_jobs() without filter returns all running jobs."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        wf = _simple_workflow()

        j1 = engine.create_job(objective="j1", workflow=wf)
        j1.status = JobStatus.RUNNING
        store.save_job(j1)

        j2 = engine.create_job(objective="j2", workflow=wf)
        j2.status = JobStatus.RUNNING
        j2.created_by = "cli:5678"
        store.save_job(j2)

        all_running = store.running_jobs()
        assert len(all_running) == 2


class TestClaimStep:
    """Tests for atomic step claiming."""

    def test_claim_step_returns_attempt(self, store):
        """First claim returns attempt 1."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        wf = _simple_workflow()
        job = engine.create_job(objective="test", workflow=wf)

        attempt = store.claim_step(job.id, "step-a")
        assert attempt == 1

    def test_claim_step_increments_after_completed_run(self, store):
        """Claim returns attempt 2 when a completed run exists at attempt 1."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        from stepwise.models import StepRun
        engine = AsyncEngine(store, ExecutorRegistry())
        wf = _simple_workflow()
        job = engine.create_job(objective="test", workflow=wf)

        # Insert a completed run at attempt 1
        import uuid
        run = StepRun(
            id=str(uuid.uuid4()),
            job_id=job.id,
            step_name="step-a",
            attempt=1,
            status=StepRunStatus.COMPLETED,
        )
        store.save_run(run)

        a2 = store.claim_step(job.id, "step-a")
        assert a2 == 2

    def test_claim_blocked_when_running(self, store, async_engine):
        """Cannot claim a step that has a RUNNING run."""
        register_step_fn("pass_through", lambda inputs: {"x": 1})
        wf = _simple_workflow()
        job = async_engine.create_job(objective="test", workflow=wf)

        # Create a running step_run manually
        from stepwise.models import StepRun
        import uuid
        run = StepRun(
            id=str(uuid.uuid4()),
            job_id=job.id,
            step_name="step-a",
            attempt=1,
            status=StepRunStatus.RUNNING,
        )
        store.save_run(run)

        result = store.claim_step(job.id, "step-a")
        assert result is None

    def test_claim_allowed_after_completion(self, store, async_engine):
        """Can claim a step after all prior runs are completed."""
        register_step_fn("pass_through", lambda inputs: {"x": 1})
        wf = _simple_workflow()
        job = async_engine.create_job(objective="test", workflow=wf)

        from stepwise.models import StepRun
        import uuid
        run = StepRun(
            id=str(uuid.uuid4()),
            job_id=job.id,
            step_name="step-a",
            attempt=1,
            status=StepRunStatus.COMPLETED,
        )
        store.save_run(run)

        result = store.claim_step(job.id, "step-a")
        assert result == 2
