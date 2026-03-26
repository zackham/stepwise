"""Tests for the quick-launch recent_flows store method."""

import pytest
from stepwise.models import (
    Job,
    JobStatus,
    WorkflowDefinition,
    StepDefinition,
    ExecutorRef,
    FlowMetadata,
    _gen_id,
    _now,
)
from stepwise.store import SQLiteStore


def _make_job(
    store: SQLiteStore,
    flow_name: str | None = None,
    source_dir: str | None = None,
    objective: str = "test",
    inputs: dict | None = None,
    parent_job_id: str | None = None,
) -> Job:
    wf = WorkflowDefinition(
        steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="script", config={"command": "echo ok"}),
                outputs=["result"],
            )
        },
        metadata=FlowMetadata(name=flow_name or ""),
        source_dir=source_dir,
    )
    job = Job(
        id=_gen_id("job"),
        objective=objective,
        workflow=wf,
        status=JobStatus.COMPLETED,
        inputs=inputs or {},
        parent_job_id=parent_job_id,
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


def test_recent_flows_empty(store):
    """Empty store returns empty list."""
    assert store.recent_flows() == []


def test_recent_flows_groups_by_flow_name(store):
    """Multiple jobs with same flow name -> only most recent returned."""
    _make_job(store, flow_name="my-flow", inputs={"x": "old"})
    j2 = _make_job(store, flow_name="my-flow", inputs={"x": "new"})

    result = store.recent_flows()
    assert len(result) == 1
    assert result[0].id == j2.id
    assert result[0].inputs == {"x": "new"}


def test_recent_flows_different_flows(store):
    """Different flow names produce separate entries."""
    _make_job(store, flow_name="flow-a")
    _make_job(store, flow_name="flow-b")

    result = store.recent_flows()
    assert len(result) == 2


def test_recent_flows_excludes_sub_jobs(store):
    """Jobs with parent_job_id are excluded."""
    parent = _make_job(store, flow_name="parent-flow")
    _make_job(store, flow_name="child-flow", parent_job_id=parent.id)

    result = store.recent_flows()
    assert len(result) == 1
    assert result[0].id == parent.id


def test_recent_flows_respects_limit(store):
    """Limit parameter controls max results."""
    for i in range(5):
        _make_job(store, flow_name=f"flow-{i}")

    result = store.recent_flows(limit=3)
    assert len(result) == 3


def test_recent_flows_no_flow_name_falls_back(store):
    """Jobs without flow metadata name group by source_dir or objective."""
    _make_job(store, source_dir="/path/to/flow", objective="run1")
    j2 = _make_job(store, source_dir="/path/to/flow", objective="run2")

    result = store.recent_flows()
    assert len(result) == 1
    assert result[0].id == j2.id


def test_recent_flows_objective_fallback(store):
    """Without name or source_dir, groups by objective."""
    _make_job(store, objective="same-objective")
    j2 = _make_job(store, objective="same-objective")

    result = store.recent_flows()
    assert len(result) == 1
    assert result[0].id == j2.id
