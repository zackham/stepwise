"""Tests for SQLiteStore.similar_failed_runs() query."""

import pytest

from stepwise.models import (
    HandoffEnvelope,
    Job,
    JobStatus,
    Sidecar,
    StepRun,
    StepRunStatus,
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


def _make_job(store: SQLiteStore, name: str = "test-job") -> Job:
    job = Job(
        id=_gen_id("job"),
        objective="test",
        name=name,
        workflow=WorkflowDefinition(),
        status=JobStatus.RUNNING,
        inputs={},
        created_at=_now(),
        updated_at=_now(),
    )
    store.save_job(job)
    return job


def _make_failed_run(
    store: SQLiteStore,
    job_id: str,
    step_name: str = "my-step",
    error_category: str = "timeout",
    error: str = "timed out",
    attempt: int = 1,
) -> StepRun:
    run = StepRun(
        id=_gen_id("run"),
        job_id=job_id,
        step_name=step_name,
        attempt=attempt,
        status=StepRunStatus.FAILED,
        error=error,
        error_category=error_category,
        started_at=_now(),
        completed_at=_now(),
    )
    store.save_run(run)
    return run


def test_similar_failed_runs_returns_matches(store):
    """Multiple failed runs with same category are returned."""
    job1 = _make_job(store, "job-one")
    job2 = _make_job(store, "job-two")
    run1 = _make_failed_run(store, job1.id, error_category="timeout")
    run2 = _make_failed_run(store, job2.id, error_category="timeout")
    # Different category — should not appear
    _make_failed_run(store, job1.id, step_name="other", error_category="auth_error")

    results = store.similar_failed_runs("timeout")
    assert len(results) == 2
    run_ids = {r["run_id"] for r in results}
    assert run1.id in run_ids
    assert run2.id in run_ids
    # Check fields
    assert results[0]["error_category"] == "timeout"
    assert results[0]["job_name"] in ("job-one", "job-two")


def test_similar_failed_runs_excludes_current(store):
    """exclude_run_id filters out the specified run."""
    job = _make_job(store)
    run1 = _make_failed_run(store, job.id, error_category="timeout", attempt=1)
    run2 = _make_failed_run(store, job.id, error_category="timeout", attempt=2)

    results = store.similar_failed_runs("timeout", exclude_run_id=run1.id)
    assert len(results) == 1
    assert results[0]["run_id"] == run2.id


def test_similar_failed_runs_filters_by_step_name(store):
    """step_name narrows results to matching step."""
    job = _make_job(store)
    _make_failed_run(store, job.id, step_name="fetch", error_category="timeout")
    run2 = _make_failed_run(store, job.id, step_name="analyze", error_category="timeout", attempt=2)

    results = store.similar_failed_runs("timeout", step_name="analyze")
    assert len(results) == 1
    assert results[0]["step_name"] == "analyze"


def test_similar_failed_runs_empty_when_no_matches(store):
    """Returns empty list when no matching category exists."""
    job = _make_job(store)
    _make_failed_run(store, job.id, error_category="auth_error")

    results = store.similar_failed_runs("timeout")
    assert results == []


def test_similar_failed_runs_respects_limit(store):
    """Limit parameter caps results."""
    job = _make_job(store)
    for i in range(10):
        _make_failed_run(store, job.id, error_category="timeout", attempt=i + 1)

    results = store.similar_failed_runs("timeout", limit=3)
    assert len(results) == 3


def test_similar_failed_runs_uses_job_name_fallback(store):
    """Falls back to objective when job name is null."""
    job = Job(
        id=_gen_id("job"),
        objective="my long objective text for fallback",
        name=None,
        workflow=WorkflowDefinition(),
        status=JobStatus.RUNNING,
        inputs={},
        created_at=_now(),
        updated_at=_now(),
    )
    store.save_job(job)
    _make_failed_run(store, job.id, error_category="timeout")

    results = store.similar_failed_runs("timeout")
    assert len(results) == 1
    assert "my long objective" in results[0]["job_name"]
