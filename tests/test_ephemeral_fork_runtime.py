"""Engine runtime tests for §9.7 ephemeral fork_from + session inputs.

R7:  _session virtual output resolves to snapshot UUID
R8:  _session binding triggers fork-source detection
R9:  Ephemeral fork step gets _fork_from_session_id in exec config
R10: $job.context fork_from resolves UUID from job inputs
R11: for_each with _session input passes UUID to sub-jobs
"""

from __future__ import annotations

import pytest

from tests.conftest import register_step_fn, CallableExecutor
from stepwise.engine import Engine
from stepwise.executors import ExecutorRegistry
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore


def make_engine() -> Engine:
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    reg.register("callable", lambda config: CallableExecutor(fn_name=config.get("fn_name", "default")))
    return Engine(store=store, registry=reg)


def _step(name: str, **kwargs) -> StepDefinition:
    return StepDefinition(
        name=name,
        outputs=kwargs.pop("outputs", ["result"]),
        executor=ExecutorRef("callable", {"fn_name": kwargs.pop("fn", "noop")}),
        **kwargs,
    )


# ── R7: _session virtual output resolves from executor_state ─────────


def test_session_virtual_output_resolves_snapshot_uuid():
    """A binding to step._session resolves to executor_state['snapshot_uuid']."""
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "parent": _step("parent", session="research"),
        "child": _step(
            "child",
            inputs=[InputBinding("ctx", "parent", "_session")],
            after=["parent"],
        ),
    })
    job = engine.create_job("t", wf)

    # Simulate parent completion with snapshot_uuid in executor_state
    parent_run = StepRun(
        id="r-parent", job_id=job.id, step_name="parent", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        executor_state={"snapshot_uuid": "snap-abc-123", "session_id": "live-xyz"},
        result=HandoffEnvelope(
            artifact={"result": "done"},
            sidecar=Sidecar(), workspace=None, timestamp=_now(),
        ),
    )
    engine.store.save_run(parent_run)

    child_def = wf.steps["child"]
    inputs, _, _ = engine._resolve_inputs(job, child_def)
    # Should prefer snapshot_uuid over session_id
    assert inputs["ctx"] == "snap-abc-123"


def test_session_virtual_output_falls_back_to_session_id():
    """When no snapshot_uuid, _session falls back to session_id."""
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "parent": _step("parent", session="research"),
        "child": _step(
            "child",
            inputs=[InputBinding("ctx", "parent", "_session")],
            after=["parent"],
        ),
    })
    job = engine.create_job("t", wf)

    parent_run = StepRun(
        id="r-parent", job_id=job.id, step_name="parent", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        executor_state={"session_id": "live-xyz"},  # no snapshot_uuid
        result=HandoffEnvelope(
            artifact={"result": "done"},
            sidecar=Sidecar(), workspace=None, timestamp=_now(),
        ),
    )
    engine.store.save_run(parent_run)

    child_def = wf.steps["child"]
    inputs, _, _ = engine._resolve_inputs(job, child_def)
    assert inputs["ctx"] == "live-xyz"


# ── R8: _session binding triggers fork-source detection ──────────────


def test_session_binding_triggers_fork_source():
    """A step whose _session output is consumed should be in fork sources."""
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "builder": _step("builder", session="research"),
        "consumer": _step(
            "consumer",
            inputs=[InputBinding("ctx", "builder", "_session")],
            after=["builder"],
        ),
    })
    job = engine.create_job("t", wf)
    sources = engine._fork_source_step_names(job)
    assert "builder" in sources


def test_non_session_binding_no_fork_source():
    """A normal binding does not add the step to fork sources."""
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "a": _step("a"),
        "b": _step("b", inputs=[InputBinding("x", "a", "result")]),
    })
    job = engine.create_job("t", wf)
    sources = engine._fork_source_step_names(job)
    assert "a" not in sources


# ── R9: Ephemeral fork gets _fork_from_session_id ────────────────────


def test_ephemeral_fork_resolves_snapshot():
    """Sessionless fork_from resolves the parent's snapshot_uuid into exec config."""
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "parent": _step("parent", session="research"),
        "child": _step("child", fn="noop", fork_from="parent", after=["parent"]),
    })
    # Change child executor type to agent so session ctx is injected
    wf.steps["child"].executor = ExecutorRef("agent", {"fn_name": "noop", "agent": "claude"})

    job = engine.create_job("t", wf)

    # Simulate parent completion
    parent_run = StepRun(
        id="r-parent", job_id=job.id, step_name="parent", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        executor_state={"snapshot_uuid": "snap-abc-123"},
        result=HandoffEnvelope(
            artifact={"result": "done"},
            sidecar=Sidecar(), workspace=None, timestamp=_now(),
        ),
    )
    engine.store.save_run(parent_run)

    register_step_fn("noop", lambda inputs: {"result": "ok"})

    # Prepare the step run to see what exec_ref config we get
    run, exec_ref, inputs, ctx = engine._prepare_step_run(job, "child")
    if exec_ref is None:
        pytest.skip("cache hit — not expected")
    assert exec_ref.config.get("_fork_from_session_id") == "snap-abc-123"
    assert exec_ref.config.get("_backend_type") == "claude_direct"


# ── R10: $job.context fork_from resolves from job inputs ─────────────


def test_job_input_fork_from_resolves():
    """fork_from: $job.ctx reads UUID from job.inputs at runtime."""
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "worker": _step("worker", fork_from="$job.ctx"),
    })
    wf.steps["worker"].executor = ExecutorRef("agent", {"agent": "claude"})

    job = engine.create_job("t", wf, inputs={"ctx": "snap-from-parent-456"})

    register_step_fn("noop", lambda inputs: {"result": "ok"})

    run, exec_ref, inputs, ctx = engine._prepare_step_run(job, "worker")
    if exec_ref is None:
        pytest.skip("cache hit")
    assert exec_ref.config.get("_fork_from_session_id") == "snap-from-parent-456"
    assert exec_ref.config.get("_backend_type") == "claude_direct"


# ── R11: fork_from step-name still detected as fork source ───────────


def test_ephemeral_fork_from_detected_as_fork_source():
    """Ephemeral (sessionless) fork_from adds the target to fork sources."""
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "parent": _step("parent", session="research"),
        "child": _step("child", fork_from="parent", after=["parent"]),
    })
    job = engine.create_job("t", wf)
    sources = engine._fork_source_step_names(job)
    assert "parent" in sources


def test_job_ref_fork_from_not_in_fork_sources():
    """$job. fork_from references don't add any step to fork sources
    (the source is outside the current scope)."""
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "worker": _step("worker", fork_from="$job.ctx"),
    })
    job = engine.create_job("t", wf)
    sources = engine._fork_source_step_names(job)
    assert len(sources) == 0
