"""Tests for SQLite persistence layer."""

from datetime import datetime, timezone

import pytest

from stepwise.models import (
    Event,
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
    WatchSpec,
    WorkflowDefinition,
    _gen_id,
    _now,
)
from stepwise.store import SQLiteStore


@pytest.fixture
def store():
    s = SQLiteStore(":memory:")
    yield s
    s.close()


class TestJobPersistence:
    def test_save_and_load_job(self, store):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(name="a", outputs=["r"], executor=ExecutorRef("script", {})),
        })
        job = Job(
            id="job-test1",
            objective="Test persistence",
            workflow=w,
            status=JobStatus.RUNNING,
            inputs={"key": "value"},
            config=JobConfig(max_sub_job_depth=3),
        )
        store.save_job(job)
        loaded = store.load_job("job-test1")
        assert loaded.id == "job-test1"
        assert loaded.objective == "Test persistence"
        assert loaded.status == JobStatus.RUNNING
        assert loaded.inputs["key"] == "value"
        assert loaded.config.max_sub_job_depth == 3

    def test_update_job(self, store):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(name="a", outputs=["r"], executor=ExecutorRef("script", {})),
        })
        job = Job(id="job-update", objective="Test", workflow=w)
        store.save_job(job)

        job.status = JobStatus.COMPLETED
        job.updated_at = _now()
        store.save_job(job)

        loaded = store.load_job("job-update")
        assert loaded.status == JobStatus.COMPLETED

    def test_active_jobs(self, store):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(name="a", outputs=["r"], executor=ExecutorRef("script", {})),
        })
        job1 = Job(id="job-1", objective="Running", workflow=w, status=JobStatus.RUNNING)
        job2 = Job(id="job-2", objective="Pending", workflow=w, status=JobStatus.PENDING)
        job3 = Job(id="job-3", objective="Completed", workflow=w, status=JobStatus.COMPLETED)
        store.save_job(job1)
        store.save_job(job2)
        store.save_job(job3)

        active = store.active_jobs()
        assert len(active) == 1
        assert active[0].id == "job-1"

    def test_load_missing_job(self, store):
        with pytest.raises(KeyError):
            store.load_job("nonexistent")


class TestStepRunPersistence:
    def test_save_and_load_run(self, store):
        # Need a job first
        w = WorkflowDefinition(steps={
            "a": StepDefinition(name="a", outputs=["r"], executor=ExecutorRef("script", {})),
        })
        job = Job(id="job-sr1", objective="Test", workflow=w)
        store.save_job(job)

        run = StepRun(
            id="run-001",
            job_id="job-sr1",
            step_name="a",
            attempt=1,
            status=StepRunStatus.COMPLETED,
            inputs={"data": "hello"},
            dep_run_ids={"$job": "$job"},
            result=HandoffEnvelope(
                artifact={"r": "result"},
                sidecar=Sidecar(decisions_made=["chose A"]),
            ),
            started_at=_now(),
            completed_at=_now(),
        )
        store.save_run(run)

        loaded = store.load_run("run-001")
        assert loaded.id == "run-001"
        assert loaded.status == StepRunStatus.COMPLETED
        assert loaded.inputs["data"] == "hello"
        assert loaded.dep_run_ids["$job"] == "$job"
        assert loaded.result.artifact["r"] == "result"
        assert loaded.result.sidecar.decisions_made == ["chose A"]

    def test_save_run_with_watch(self, store):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(name="a", outputs=["r"], executor=ExecutorRef("script", {})),
        })
        job = Job(id="job-sr2", objective="Test", workflow=w)
        store.save_job(job)

        run = StepRun(
            id="run-002",
            job_id="job-sr2",
            step_name="a",
            attempt=1,
            status=StepRunStatus.SUSPENDED,
            watch=WatchSpec("poll", {"check_command": "check.py", "interval_seconds": 30},
                           fulfillment_outputs=["status"]),
            executor_state={"_watch": {"check_count": 3}},
        )
        store.save_run(run)

        loaded = store.load_run("run-002")
        assert loaded.status == StepRunStatus.SUSPENDED
        assert loaded.watch.mode == "poll"
        assert loaded.watch.config["interval_seconds"] == 30
        assert loaded.executor_state["_watch"]["check_count"] == 3

    def test_latest_run(self, store):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(name="a", outputs=["r"], executor=ExecutorRef("script", {})),
        })
        job = Job(id="job-sr3", objective="Test", workflow=w)
        store.save_job(job)

        for i in range(3):
            run = StepRun(
                id=f"run-{i}",
                job_id="job-sr3",
                step_name="a",
                attempt=i + 1,
                status=StepRunStatus.COMPLETED if i < 2 else StepRunStatus.RUNNING,
            )
            store.save_run(run)

        latest = store.latest_run("job-sr3", "a")
        assert latest.id == "run-2"
        assert latest.attempt == 3

    def test_latest_completed_run(self, store):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(name="a", outputs=["r"], executor=ExecutorRef("script", {})),
        })
        job = Job(id="job-sr4", objective="Test", workflow=w)
        store.save_job(job)

        run1 = StepRun(id="run-c1", job_id="job-sr4", step_name="a",
                       attempt=1, status=StepRunStatus.COMPLETED)
        run2 = StepRun(id="run-c2", job_id="job-sr4", step_name="a",
                       attempt=2, status=StepRunStatus.FAILED)
        run3 = StepRun(id="run-c3", job_id="job-sr4", step_name="a",
                       attempt=3, status=StepRunStatus.RUNNING)
        store.save_run(run1)
        store.save_run(run2)
        store.save_run(run3)

        latest_completed = store.latest_completed_run("job-sr4", "a")
        assert latest_completed.id == "run-c1"
        assert latest_completed.attempt == 1

    def test_completed_run_count(self, store):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(name="a", outputs=["r"], executor=ExecutorRef("script", {})),
        })
        job = Job(id="job-sr5", objective="Test", workflow=w)
        store.save_job(job)

        for i, status in enumerate([
            StepRunStatus.COMPLETED,
            StepRunStatus.FAILED,
            StepRunStatus.COMPLETED,
            StepRunStatus.RUNNING,
        ]):
            run = StepRun(id=f"run-cnt{i}", job_id="job-sr5", step_name="a",
                          attempt=i + 1, status=status)
            store.save_run(run)

        assert store.completed_run_count("job-sr5", "a") == 2

    def test_runs_by_status(self, store):
        w = WorkflowDefinition(steps={
            "a": StepDefinition(name="a", outputs=["r"], executor=ExecutorRef("script", {})),
        })
        job = Job(id="job-sr6", objective="Test", workflow=w, status=JobStatus.RUNNING)
        store.save_job(job)

        store.save_run(StepRun(id="r1", job_id="job-sr6", step_name="a",
                               attempt=1, status=StepRunStatus.RUNNING))
        store.save_run(StepRun(id="r2", job_id="job-sr6", step_name="a",
                               attempt=2, status=StepRunStatus.SUSPENDED,
                               watch=WatchSpec("external", {}, [])))
        store.save_run(StepRun(id="r3", job_id="job-sr6", step_name="a",
                               attempt=3, status=StepRunStatus.DELEGATED,
                               sub_job_id="sub-1"))

        assert len(store.running_runs("job-sr6")) == 1
        assert len(store.suspended_runs("job-sr6")) == 1
        assert len(store.delegated_runs("job-sr6")) == 1


def _make_job(store, job_id):
    """Create a minimal job so foreign key constraints are satisfied."""
    w = WorkflowDefinition(steps={
        "a": StepDefinition(name="a", outputs=["r"], executor=ExecutorRef("script", {})),
    })
    job = Job(id=job_id, objective="Test", workflow=w)
    store.save_job(job)


class TestEventPersistence:
    def test_save_and_load_events(self, store):
        _make_job(store, "job-evt1")
        event = Event(
            id="evt-001",
            job_id="job-evt1",
            timestamp=_now(),
            type="step.completed",
            data={"step": "a", "attempt": 1},
        )
        store.save_event(event)

        events = store.load_events("job-evt1")
        assert len(events) == 1
        assert events[0].type == "step.completed"
        assert events[0].data["step"] == "a"

    def test_effector_flag(self, store):
        _make_job(store, "job-evt2")
        event = Event(
            id="evt-002",
            job_id="job-evt2",
            timestamp=_now(),
            type="step.completed",
            data={},
            is_effector=True,
        )
        store.save_event(event)

        events = store.load_events("job-evt2")
        assert events[0].is_effector is True

    def test_events_since(self, store):
        _make_job(store, "j1")
        t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2026, 6, 1, tzinfo=timezone.utc)
        t3 = datetime(2026, 12, 1, tzinfo=timezone.utc)

        store.save_event(Event(id="e1", job_id="j1", timestamp=t1, type="a", data={}))
        store.save_event(Event(id="e2", job_id="j1", timestamp=t2, type="b", data={}))
        store.save_event(Event(id="e3", job_id="j1", timestamp=t3, type="c", data={}))

        since = datetime(2026, 5, 1, tzinfo=timezone.utc)
        events = store.load_events("j1", since=since)
        assert len(events) == 2
        assert events[0].type == "b"
        assert events[1].type == "c"
