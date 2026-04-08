"""Step 7 (§11): end-to-end loop-back binding runtime tests.

Covers R19-R22 + the unified `when: derived` pattern variant per Q3.
"""

from __future__ import annotations

import pytest

from tests.conftest import register_step_fn, CallableExecutor
from stepwise.engine import Engine
from stepwise.executors import ExecutorRegistry
from stepwise.models import (
    ExecutorRef,
    ExitRule,
    HandoffEnvelope,
    InputBinding,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRunStatus,
    WhenPredicate,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore


def make_engine() -> Engine:
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    reg.register("callable", lambda config: CallableExecutor(fn_name=config.get("fn_name", "default")))
    return Engine(store=store, registry=reg)


def _step(name: str, fn: str, **kwargs) -> StepDefinition:
    return StepDefinition(
        name=name,
        outputs=kwargs.pop("outputs", ["result"]),
        executor=ExecutorRef("callable", {"fn_name": fn}),
        **kwargs,
    )


def _loop_field(
    target: str, field: str, value, name: str = "back", max_iterations: int = 10,
) -> ExitRule:
    """Loop exit rule using field_match: relaunch target while
    output[field] == value."""
    return ExitRule(
        name=name, type="field_match",
        config={
            "field": field, "value": value,
            "action": "loop", "target": target,
            "max_iterations": max_iterations,
        },
    )


# ─── R19: step with back-edge-only dep is ready on iter-1 ─────────────────


def test_step_with_back_edge_ready_on_iter1():
    """A consumer whose only inputs are back-edges should be ready
    immediately (no producer runs needed)."""
    engine = make_engine()
    wf = WorkflowDefinition(steps={
        "consumer": _step("consumer", "noop", inputs=[
            InputBinding(
                local_name="prev",
                source_step="producer",
                source_field="result",
                is_back_edge=True,
                closing_loop_id="consumer",
                optional=True,
            ),
        ]),
        "producer": _step("producer", "noop", inputs=[
            InputBinding(local_name="x", source_step="consumer", source_field="result"),
        ]),
    })
    job = engine.create_job("t", wf)
    assert engine._is_step_ready(job, "consumer", wf.steps["consumer"]) is True


# ─── R20: score → refine → score loop completes ──────────────────────────


def test_score_refine_score_loop_completes():
    """Canonical analyze → critique → loop-back-to-analyze runs 3 iterations
    and terminates. Built programmatically (skipping yaml_loader) so we
    can mark is_back_edge directly."""

    def analyze_fn(inputs):
        # On iter-1: prev_round absent (back-edge missing). On iter-N>1
        # the back-edge resolves to the prior critique.note value.
        prev_round = inputs.get("prev_round", 0) or 0
        return {"text": f"round{prev_round + 1}", "round": prev_round + 1}

    def critique_fn(inputs):
        round_n = inputs["round"]
        verdict = "done" if round_n >= 3 else "rework"
        return {"note": round_n, "verdict": verdict}

    register_step_fn("analyze", analyze_fn)
    register_step_fn("critique", critique_fn)

    engine = make_engine()
    analyze_step = _step("analyze", "analyze", outputs=["text", "round"], inputs=[
        InputBinding(
            local_name="prev_round",
            source_step="critique",
            source_field="note",
            is_back_edge=True,
            closing_loop_id="analyze",
            optional=True,
        ),
    ])
    critique_step = _step("critique", "critique", outputs=["note", "verdict"], inputs=[
        InputBinding(local_name="text", source_step="analyze", source_field="text"),
        InputBinding(local_name="round", source_step="analyze", source_field="round"),
    ], exit_rules=[
        _loop_field(target="analyze", field="verdict", value="rework", max_iterations=10),
    ])
    wf = WorkflowDefinition(steps={"analyze": analyze_step, "critique": critique_step})

    job = engine.create_job("t", wf)
    engine.start_job(job.id)
    job = engine.get_job(job.id)
    assert job.status == JobStatus.COMPLETED
    # Verify the loop ran 3 iterations
    completed = engine.store.completed_run_count(job.id, "analyze")
    assert completed == 3


# ─── R21: is_present false on iter-1, true on iter-N>1 ───────────────────


def test_is_present_false_on_iter1_true_on_iter2():
    """A step with `when: { is_present: false }` runs only on iter-1.
    Verified at the unit level: presence map and predicate evaluation."""
    engine = make_engine()
    pred = WhenPredicate(input="prev", op="is_present", value=False)
    consumer = _step("consumer", "noop", when=pred, inputs=[
        InputBinding(
            local_name="prev",
            source_step="producer",
            source_field="result",
            is_back_edge=True,
            closing_loop_id="consumer",
            optional=True,
        ),
    ])
    producer = _step("producer", "noop", inputs=[
        InputBinding(local_name="x", source_step="consumer", source_field="result"),
    ])
    wf = WorkflowDefinition(steps={"consumer": consumer, "producer": producer})
    job = engine.create_job("t", wf)

    # Iter-1: producer has no run → presence False → is_present:false matches
    inputs, _, presence = engine._resolve_inputs(job, consumer)
    assert presence == {"prev": False}
    from stepwise.validator.mutex import evaluate_when_predicate
    assert evaluate_when_predicate(pred, inputs, presence) is True

    # Synthesize a producer run AND advance the loop frame to iter > 0.
    from stepwise.models import StepRun
    engine.store.save_run(StepRun(
        id="r1", job_id=job.id, step_name="producer", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        result=HandoffEnvelope(artifact={"result": "value"}, sidecar=Sidecar(),
                                workspace=None, timestamp=_now()),
    ))
    engine._get_or_create_loop_frame(job, "consumer").iteration_index = 1

    inputs2, _, presence2 = engine._resolve_inputs(job, consumer)
    assert presence2 == {"prev": True}
    assert evaluate_when_predicate(pred, inputs2, presence2) is False  # is_present:false no longer matches


# ─── R22: consumer stays current after producer next iter ────────────────


def test_consumer_stays_current_with_back_edge_dep():
    """`_is_current` skips back-edge bindings — a new producer run does
    NOT invalidate the consumer's previous run."""
    engine = make_engine()
    consumer = _step("consumer", "noop", inputs=[
        InputBinding(
            local_name="prev",
            source_step="producer",
            source_field="result",
            is_back_edge=True,
            closing_loop_id="consumer",
            optional=True,
        ),
    ])
    producer = _step("producer", "noop", inputs=[
        InputBinding(local_name="x", source_step="consumer", source_field="result"),
    ])
    wf = WorkflowDefinition(steps={"consumer": consumer, "producer": producer})
    job = engine.create_job("t", wf)

    # Save a consumer run (iter 1) and a producer run (post-loop)
    from stepwise.models import StepRun
    consumer_run = StepRun(
        id="r-c-1", job_id=job.id, step_name="consumer", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        dep_run_ids={},
        result=HandoffEnvelope(artifact={"result": "v1"}, sidecar=Sidecar(),
                                workspace=None, timestamp=_now()),
    )
    engine.store.save_run(consumer_run)
    producer_run = StepRun(
        id="r-p-1", job_id=job.id, step_name="producer", attempt=1,
        status=StepRunStatus.COMPLETED, started_at=_now(), completed_at=_now(),
        result=HandoffEnvelope(artifact={"result": "from p"}, sidecar=Sidecar(),
                                workspace=None, timestamp=_now()),
    )
    engine.store.save_run(producer_run)

    # The consumer run is still "current" — its back-edge dep is excluded
    # from the dep-provenance walk.
    assert engine._is_current(job, consumer_run) is True


# ─── Unified pattern (Q3 alternative): when: derived routing ─────────────


def test_unified_when_derived_pattern_runs():
    """A single step with a self-loop: increments v each iteration via the
    back-edge to its own previous run. Tests that:
      - the back-edge resolves to the prior iteration's output
      - iter-1 has no prior (presence False, key absent → defaults)
      - the loop terminates via field_match exit rule on a derived value
    """
    def worker_fn(inputs):
        # iter-1: prev absent → start at 1; iter-N: prev=N-1 → N
        prev = inputs.get("prev", 0) or 0
        return {"v": prev + 1, "verdict": "rework" if prev + 1 < 3 else "done"}

    register_step_fn("worker", worker_fn)
    engine = make_engine()
    worker = _step("worker", "worker", outputs=["v", "verdict"], inputs=[
        InputBinding(
            local_name="prev",
            source_step="worker",
            source_field="v",
            is_back_edge=True,
            closing_loop_id="worker",
            optional=True,
        ),
    ], exit_rules=[
        _loop_field(target="worker", field="verdict", value="rework", max_iterations=10),
    ])
    wf = WorkflowDefinition(steps={"worker": worker})
    job = engine.create_job("t", wf)
    engine.start_job(job.id)
    job = engine.get_job(job.id)
    assert job.status == JobStatus.COMPLETED
    assert engine.store.completed_run_count(job.id, "worker") == 3
