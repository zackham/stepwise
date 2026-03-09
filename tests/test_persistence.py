"""Tests for SQLite persistence and crash recovery."""

from datetime import datetime, timezone

import pytest

from stepwise.events import Event, EventType
from stepwise.models import (
    Job,
    JobStatus,
    StepDefinition,
    StepRun,
    StepStatus,
    WorkflowDefinition,
)
from stepwise.store import StepwiseStore


@pytest.fixture
def wf():
    return WorkflowDefinition(
        name="persist_test",
        steps=[
            StepDefinition(name="a", executor="script", config={"command": "echo hi"}),
            StepDefinition(name="b", executor="script", depends_on=["a"]),
        ],
    )


# ── Job persistence ──────────────────────────────────────────────────


class TestJobPersistence:
    def test_save_and_load(self, store, wf):
        job = Job.create(wf, inputs={"key": "value"})
        store.save_job(job)
        loaded = store.load_job(job.id)
        assert loaded is not None
        assert loaded.id == job.id
        assert loaded.status == JobStatus.PENDING
        assert loaded.inputs == {"key": "value"}
        assert loaded.workflow.name == "persist_test"

    def test_load_nonexistent(self, store):
        assert store.load_job("nonexistent") is None

    def test_save_with_outputs(self, store, wf):
        job = Job.create(wf)
        job.status = JobStatus.COMPLETED
        job.outputs = {"result": 42, "nested": {"a": [1, 2]}}
        store.save_job(job)
        loaded = store.load_job(job.id)
        assert loaded.outputs == {"result": 42, "nested": {"a": [1, 2]}}

    def test_update_status(self, store, wf):
        job = Job.create(wf)
        store.save_job(job)
        store.update_job_status(job.id, JobStatus.RUNNING)
        loaded = store.load_job(job.id)
        assert loaded.status == JobStatus.RUNNING

    def test_list_jobs(self, store, wf):
        j1 = Job.create(wf)
        j1.status = JobStatus.COMPLETED
        j2 = Job.create(wf)
        j2.status = JobStatus.RUNNING
        j3 = Job.create(wf)
        j3.status = JobStatus.COMPLETED
        store.save_job(j1)
        store.save_job(j2)
        store.save_job(j3)

        all_jobs = store.list_jobs()
        assert len(all_jobs) == 3

        completed = store.list_jobs(status=JobStatus.COMPLETED)
        assert len(completed) == 2
        assert all(j.status == JobStatus.COMPLETED for j in completed)

    def test_delete_job(self, store, wf):
        job = Job.create(wf)
        store.save_job(job)
        assert store.delete_job(job.id)
        assert store.load_job(job.id) is None

    def test_delete_nonexistent(self, store):
        assert not store.delete_job("nonexistent")


# ── Step Run persistence ─────────────────────────────────────────────


class TestStepRunPersistence:
    def test_step_runs_saved_with_job(self, store, wf):
        job = Job.create(wf)
        store.save_job(job)
        loaded = store.load_job(job.id)
        assert "a" in loaded.step_runs
        assert "b" in loaded.step_runs
        assert loaded.step_runs["a"].status == StepStatus.PENDING

    def test_step_run_with_outputs(self, store, wf):
        job = Job.create(wf)
        sr = job.step_runs["a"]
        sr.status = StepStatus.COMPLETED
        sr.outputs = {"stdout": "hello", "return_code": 0}
        sr.started_at = datetime.now(timezone.utc)
        sr.completed_at = datetime.now(timezone.utc)
        sr.input_hash = sr.compute_input_hash()
        store.save_job(job)

        loaded = store.load_job(job.id)
        loaded_sr = loaded.step_runs["a"]
        assert loaded_sr.status == StepStatus.COMPLETED
        assert loaded_sr.outputs == {"stdout": "hello", "return_code": 0}
        assert loaded_sr.started_at is not None
        assert loaded_sr.input_hash is not None

    def test_save_individual_step_run(self, store, wf):
        job = Job.create(wf)
        store.save_job(job)
        sr = job.step_runs["a"]
        sr.status = StepStatus.RUNNING
        sr.started_at = datetime.now(timezone.utc)
        store.save_step_run(sr)

        runs = store.load_step_runs(job.id)
        a_runs = [r for r in runs if r.step_name == "a"]
        assert len(a_runs) == 1
        assert a_runs[0].status == StepStatus.RUNNING

    def test_iteration_fields(self, store, wf):
        job = Job.create(wf)
        store.save_job(job)
        sr = StepRun.create(
            job_id=job.id,
            step_name="a:0",
            iteration_index=0,
            iteration_value="item_zero",
        )
        sr.status = StepStatus.COMPLETED
        sr.outputs = {"val": "processed"}
        store.save_step_run(sr)

        runs = store.load_step_runs(job.id)
        iter_runs = [r for r in runs if r.step_name == "a:0"]
        assert len(iter_runs) == 1
        assert iter_runs[0].iteration_index == 0
        assert iter_runs[0].iteration_value == "item_zero"


# ── Event persistence ────────────────────────────────────────────────


class TestEventPersistence:
    def test_save_and_load(self, store, wf):
        job = Job.create(wf)
        store.save_job(job)

        event = Event.create(
            job.id,
            EventType.STEP_STARTED,
            step_name="a",
            data={"attempt": 1},
        )
        store.save_event(event)

        events = store.load_events(job.id)
        assert len(events) == 1
        assert events[0].event_type == EventType.STEP_STARTED
        assert events[0].step_name == "a"
        assert events[0].data["attempt"] == 1

    def test_filter_by_type(self, store, wf):
        job = Job.create(wf)
        store.save_job(job)

        store.save_event(Event.create(job.id, EventType.STEP_STARTED, "a"))
        store.save_event(Event.create(job.id, EventType.STEP_COMPLETED, "a"))
        store.save_event(Event.create(job.id, EventType.STEP_STARTED, "b"))

        started = store.load_events(job.id, EventType.STEP_STARTED)
        assert len(started) == 2
        completed = store.load_events(job.id, EventType.STEP_COMPLETED)
        assert len(completed) == 1

    def test_events_ordered_by_timestamp(self, store, wf):
        job = Job.create(wf)
        store.save_job(job)

        for etype in [EventType.JOB_STARTED, EventType.STEP_STARTED, EventType.STEP_COMPLETED]:
            store.save_event(Event.create(job.id, etype))

        events = store.load_events(job.id)
        for i in range(len(events) - 1):
            assert events[i].timestamp <= events[i + 1].timestamp


# ── Crash Recovery ───────────────────────────────────────────────────


class TestCrashRecovery:
    def test_running_steps_reset_to_pending(self, store, wf):
        job = Job.create(wf)
        job.status = JobStatus.RUNNING
        job.step_runs["a"].status = StepStatus.RUNNING
        job.step_runs["a"].started_at = datetime.now(timezone.utc)
        store.save_job(job)

        recovered = store.recover_job(job.id)
        assert recovered is not None
        assert recovered.status == JobStatus.PENDING
        assert recovered.step_runs["a"].status == StepStatus.PENDING
        assert recovered.step_runs["a"].started_at is None

    def test_completed_steps_preserved(self, store, wf):
        job = Job.create(wf)
        job.status = JobStatus.RUNNING
        job.step_runs["a"].status = StepStatus.COMPLETED
        job.step_runs["a"].outputs = {"result": "done"}
        job.step_runs["b"].status = StepStatus.RUNNING
        store.save_job(job)

        recovered = store.recover_job(job.id)
        assert recovered.step_runs["a"].status == StepStatus.COMPLETED
        assert recovered.step_runs["a"].outputs == {"result": "done"}
        assert recovered.step_runs["b"].status == StepStatus.PENDING

    def test_recover_persists_changes(self, store, wf):
        job = Job.create(wf)
        job.status = JobStatus.RUNNING
        job.step_runs["a"].status = StepStatus.RUNNING
        store.save_job(job)

        store.recover_job(job.id)

        # Load again to verify persistence
        reloaded = store.load_job(job.id)
        assert reloaded.status == JobStatus.PENDING
        assert reloaded.step_runs["a"].status == StepStatus.PENDING

    def test_recover_nonexistent(self, store):
        assert store.recover_job("nonexistent") is None

    def test_no_recovery_needed(self, store, wf):
        job = Job.create(wf)
        job.status = JobStatus.COMPLETED
        job.step_runs["a"].status = StepStatus.COMPLETED
        job.step_runs["b"].status = StepStatus.COMPLETED
        store.save_job(job)

        recovered = store.recover_job(job.id)
        assert recovered.status == JobStatus.COMPLETED  # Not changed


# ── File-based store ─────────────────────────────────────────────────


class TestFileStore:
    def test_file_persistence(self, tmp_path, wf):
        db_path = str(tmp_path / "test.db")
        store1 = StepwiseStore(db_path)
        job = Job.create(wf, inputs={"x": 1})
        store1.save_job(job)
        store1.close()

        store2 = StepwiseStore(db_path)
        loaded = store2.load_job(job.id)
        assert loaded is not None
        assert loaded.inputs == {"x": 1}
        store2.close()
