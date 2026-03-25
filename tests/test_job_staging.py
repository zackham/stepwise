"""Tests for job staging store methods: create, dependencies, cycle detection, pending resolution, group transition."""

from __future__ import annotations

import pytest

from stepwise.models import (
    Job,
    JobStatus,
    WorkflowDefinition,
    StepDefinition,
    ExecutorRef,
    _now,
)
from stepwise.store import SQLiteStore


def _wf() -> WorkflowDefinition:
    return WorkflowDefinition(steps={
        "a": StepDefinition(
            name="a",
            outputs=["result"],
            executor=ExecutorRef(type="script", config={"command": "echo ok"}),
        ),
    })


def _make_job(store: SQLiteStore, objective: str, status: JobStatus = JobStatus.STAGED,
              group: str | None = None) -> Job:
    import uuid
    job = Job(
        id=f"job-{uuid.uuid4().hex[:8]}",
        objective=objective,
        workflow=_wf(),
        status=status,
        job_group=group,
        created_at=_now(),
        updated_at=_now(),
    )
    store.save_job(job)
    return job


@pytest.fixture
def store():
    s = SQLiteStore(":memory:")
    yield s
    s.close()


class TestCreateStagedJob:
    def test_create_with_staged_status(self, store):
        job = _make_job(store, "staged-test", status=JobStatus.STAGED, group="g1")
        loaded = store.load_job(job.id)
        assert loaded.status == JobStatus.STAGED
        assert loaded.job_group == "g1"
        assert loaded.objective == "staged-test"


class TestAddDependency:
    def test_add_and_get(self, store):
        j1 = _make_job(store, "a")
        j2 = _make_job(store, "b")
        store.add_job_dependency(j2.id, j1.id)
        deps = store.get_job_dependencies(j2.id)
        assert deps == [j1.id]

    def test_idempotent(self, store):
        j1 = _make_job(store, "a")
        j2 = _make_job(store, "b")
        store.add_job_dependency(j2.id, j1.id)
        store.add_job_dependency(j2.id, j1.id)  # duplicate — no error
        assert store.get_job_dependencies(j2.id) == [j1.id]

    def test_get_dependents(self, store):
        j1 = _make_job(store, "a")
        j2 = _make_job(store, "b")
        store.add_job_dependency(j2.id, j1.id)
        assert store.get_job_dependents(j1.id) == [j2.id]


class TestCycleDetection:
    def test_direct_cycle_rejected(self, store):
        j1 = _make_job(store, "a")
        j2 = _make_job(store, "b")
        store.add_job_dependency(j2.id, j1.id)  # B depends on A
        assert store.would_create_cycle(j1.id, j2.id) is True  # A depends on B would cycle

    def test_self_cycle_rejected(self, store):
        j1 = _make_job(store, "a")
        assert store.would_create_cycle(j1.id, j1.id) is True

    def test_transitive_cycle_rejected(self, store):
        j1 = _make_job(store, "a")
        j2 = _make_job(store, "b")
        j3 = _make_job(store, "c")
        store.add_job_dependency(j2.id, j1.id)  # B depends on A
        store.add_job_dependency(j3.id, j2.id)  # C depends on B
        assert store.would_create_cycle(j1.id, j3.id) is True  # A depends on C would cycle

    def test_no_cycle_allowed(self, store):
        j1 = _make_job(store, "a")
        j2 = _make_job(store, "b")
        j3 = _make_job(store, "c")
        store.add_job_dependency(j2.id, j1.id)  # B depends on A
        # C depends on A is fine (no cycle)
        assert store.would_create_cycle(j3.id, j1.id) is False


class TestPendingJobsWithDepsMet:
    def test_no_deps_returns_pending(self, store):
        j1 = _make_job(store, "a", status=JobStatus.PENDING)
        jobs = store.pending_jobs_with_deps_met()
        assert [j.id for j in jobs] == [j1.id]

    def test_unmet_dep_excluded(self, store):
        j1 = _make_job(store, "a", status=JobStatus.PENDING)
        j2 = _make_job(store, "b", status=JobStatus.PENDING)
        store.add_job_dependency(j2.id, j1.id)  # j2 waits for j1
        jobs = store.pending_jobs_with_deps_met()
        # Only j1 is ready (j2 depends on j1 which is PENDING, not COMPLETED)
        assert [j.id for j in jobs] == [j1.id]

    def test_met_dep_included(self, store):
        j1 = _make_job(store, "a", status=JobStatus.COMPLETED)
        j2 = _make_job(store, "b", status=JobStatus.PENDING)
        store.add_job_dependency(j2.id, j1.id)
        jobs = store.pending_jobs_with_deps_met()
        assert [j.id for j in jobs] == [j2.id]

    def test_staged_not_returned(self, store):
        _make_job(store, "a", status=JobStatus.STAGED)
        jobs = store.pending_jobs_with_deps_met()
        assert jobs == []


class TestJobsInGroup:
    def test_returns_jobs_in_group(self, store):
        j1 = _make_job(store, "a", group="g1")
        j2 = _make_job(store, "b", group="g1")
        _make_job(store, "c", group="g2")
        jobs = store.jobs_in_group("g1")
        assert {j.id for j in jobs} == {j1.id, j2.id}

    def test_empty_group(self, store):
        assert store.jobs_in_group("nonexistent") == []


class TestJobDependents:
    def test_returns_dependent_jobs(self, store):
        j1 = _make_job(store, "a")
        j2 = _make_job(store, "b")
        j3 = _make_job(store, "c")
        store.add_job_dependency(j2.id, j1.id)
        store.add_job_dependency(j3.id, j1.id)
        dependents = store.job_dependents(j1.id)
        assert {j.id for j in dependents} == {j2.id, j3.id}

    def test_no_dependents(self, store):
        j1 = _make_job(store, "a")
        assert store.job_dependents(j1.id) == []


class TestTransitionGroup:
    def test_transitions_staged_to_pending(self, store):
        j1 = _make_job(store, "a", status=JobStatus.STAGED, group="g1")
        j2 = _make_job(store, "b", status=JobStatus.STAGED, group="g1")
        ids = store.transition_group_to_pending("g1")
        assert set(ids) == {j1.id, j2.id}
        for jid in ids:
            assert store.load_job(jid).status == JobStatus.PENDING

    def test_only_affects_target_group(self, store):
        j1 = _make_job(store, "a", status=JobStatus.STAGED, group="g1")
        j2 = _make_job(store, "b", status=JobStatus.STAGED, group="g2")
        ids = store.transition_group_to_pending("g1")
        assert ids == [j1.id]
        assert store.load_job(j2.id).status == JobStatus.STAGED

    def test_skips_non_staged(self, store):
        j1 = _make_job(store, "a", status=JobStatus.STAGED, group="g1")
        j2 = _make_job(store, "b", status=JobStatus.PENDING, group="g1")
        ids = store.transition_group_to_pending("g1")
        assert ids == [j1.id]
        # j2 was already PENDING, not touched
        assert store.load_job(j2.id).status == JobStatus.PENDING

    def test_empty_group(self, store):
        ids = store.transition_group_to_pending("nonexistent")
        assert ids == []
