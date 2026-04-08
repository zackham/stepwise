"""Step 7 (§11.7): three-state _resolve_inputs presence map tests.

Covers R11-R16: presence semantics for regular, loop-back, optional,
any_of, $job inputs.
"""

from __future__ import annotations

import pytest

from stepwise.engine import Engine
from stepwise.executors import ExecutorRegistry
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
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
    return Engine(store=store, registry=ExecutorRegistry())


def _step(name: str, **kwargs) -> StepDefinition:
    return StepDefinition(
        name=name,
        outputs=kwargs.pop("outputs", ["result"]),
        executor=kwargs.pop("executor", ExecutorRef("script", {})),
        **kwargs,
    )


def _completed_run(job_id: str, step_name: str, attempt: int = 1, artifact: dict | None = None) -> StepRun:
    return StepRun(
        id=f"run-{step_name}-{attempt}",
        job_id=job_id,
        step_name=step_name,
        attempt=attempt,
        status=StepRunStatus.COMPLETED,
        started_at=_now(),
        completed_at=_now(),
        result=HandoffEnvelope(
            artifact=artifact or {},
            sidecar=Sidecar(),
            workspace=None,
            timestamp=_now(),
        ),
    )


# ─── R11: regular binding presence True ───────────────────────────────────


def test_regular_binding_presence_true():
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "a": _step("a", outputs=["v"]),
        "b": _step("b", inputs=[
            InputBinding(local_name="x", source_step="a", source_field="v"),
        ]),
    })
    job = engine.create_job("t", wf)
    engine.store.save_run(_completed_run(job.id, "a", artifact={"v": "hello"}))

    inputs, _, presence = engine._resolve_inputs(job, wf.steps["b"])
    assert inputs == {"x": "hello"}
    assert presence == {"x": True}


# ─── R12: loop-back iter-1 presence False, key absent ────────────────────


def test_loop_back_iter1_presence_false():
    engine = make_engine()
    # Construct a loop-back binding manually (skipping yaml_loader so we
    # don't have to satisfy the unguarded back-edge check).
    wf = WorkflowDefinition(steps={
        "consumer": _step("consumer", inputs=[
            InputBinding(
                local_name="prev",
                source_step="producer",
                source_field="result",
                is_back_edge=True,
                closing_loop_id="consumer",
            ),
        ]),
        "producer": _step("producer", outputs=["result"]),
    })
    job = engine.create_job("t", wf)

    inputs, _, presence = engine._resolve_inputs(job, wf.steps["consumer"])
    assert "prev" not in inputs  # key absent
    assert presence == {"prev": False}


# ─── R13: loop-back iter-N>1 presence True ───────────────────────────────


def test_loop_back_iterN_presence_true():
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "consumer": _step("consumer", inputs=[
            InputBinding(
                local_name="prev",
                source_step="producer",
                source_field="result",
                is_back_edge=True,
                closing_loop_id="consumer",
            ),
        ]),
        "producer": _step("producer", outputs=["result"]),
    })
    job = engine.create_job("t", wf)
    engine.store.save_run(_completed_run(job.id, "producer", artifact={"result": "round 1"}))
    # Simulate the loop having fired once: frame exists with iter > 0.
    engine._get_or_create_loop_frame(job, "consumer").iteration_index = 1

    inputs, _, presence = engine._resolve_inputs(job, wf.steps["consumer"])
    assert inputs == {"prev": "round 1"}
    assert presence == {"prev": True}


# ─── R14: any_of all sources back-edge → presence False on iter-1 ────────


def test_any_of_loop_back_iter1_presence_false():
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "consumer": _step("consumer", inputs=[
            InputBinding(
                local_name="prev",
                source_step="",
                source_field="",
                any_of_sources=[("producer1", "result"), ("producer2", "result")],
                is_back_edge=True,
                closing_loop_id="consumer",
            ),
        ]),
        "producer1": _step("producer1", outputs=["result"]),
        "producer2": _step("producer2", outputs=["result"]),
    })
    job = engine.create_job("t", wf)
    inputs, _, presence = engine._resolve_inputs(job, wf.steps["consumer"])
    assert "prev" not in inputs
    assert presence == {"prev": False}


# ─── R15: optional non-loop-back, no producer → presence False ───────────


def test_optional_absent_presence_false():
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "a": _step("a", outputs=["v"]),
        "b": _step("b", inputs=[
            InputBinding(local_name="x", source_step="a", source_field="v", optional=True),
        ]),
    })
    job = engine.create_job("t", wf)
    inputs, _, presence = engine._resolve_inputs(job, wf.steps["b"])
    assert inputs == {"x": None}
    assert presence == {"x": False}


def test_optional_with_completed_producer_presence_true():
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "a": _step("a", outputs=["v"]),
        "b": _step("b", inputs=[
            InputBinding(local_name="x", source_step="a", source_field="v", optional=True),
        ]),
    })
    job = engine.create_job("t", wf)
    engine.store.save_run(_completed_run(job.id, "a", artifact={"v": 42}))
    inputs, _, presence = engine._resolve_inputs(job, wf.steps["b"])
    assert inputs == {"x": 42}
    assert presence == {"x": True}


# ─── R16: $job input always present ───────────────────────────────────────


def test_job_input_always_present():
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "a": _step("a", inputs=[
            InputBinding(local_name="topic", source_step="$job", source_field="topic"),
        ]),
    })
    job = engine.create_job("t", wf, inputs={"topic": "loops"})
    inputs, _, presence = engine._resolve_inputs(job, wf.steps["a"])
    assert inputs == {"topic": "loops"}
    assert presence == {"topic": True}
