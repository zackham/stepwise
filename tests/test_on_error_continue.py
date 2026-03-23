"""Tests for step-level on_error: continue support.

N31 — When parallel agent steps have on_error: continue, a single step
failure should not kill the job. Other parallel steps keep running and
downstream synthesize steps proceed with null/error markers for failed inputs.
"""

from __future__ import annotations

import pytest

from stepwise.engine import Engine
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore
from stepwise.yaml_loader import YAMLLoadError, load_workflow_string

from tests.conftest import register_step_fn


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_engine():
    from tests.conftest import registry as _reg_fixture
    from stepwise.executors import (
        ExecutorRegistry, ExecutionContext, Executor, ExecutorResult,
        ExecutorStatus,
    )

    store = SQLiteStore(":memory:")

    reg = ExecutorRegistry()

    # Callable executor that looks up fn by name
    from tests.conftest import CallableExecutor
    reg.register("callable", lambda config: CallableExecutor(
        fn_name=config.get("fn_name", "default"),
    ))

    return Engine(store=store, registry=reg), store


# ── YAML schema tests ─────────────────────────────────────────────────────────


class TestOnErrorYAMLParsing:
    """Test on_error field is parsed from YAML correctly."""

    def test_default_is_fail(self):
        wf = load_workflow_string("""
steps:
  step_a:
    executor: external
    outputs: [result]
""")
        assert wf.steps["step_a"].on_error == "fail"

    def test_on_error_continue_parsed(self):
        wf = load_workflow_string("""
steps:
  step_a:
    executor: external
    outputs: [result]
    on_error: continue
""")
        assert wf.steps["step_a"].on_error == "continue"

    def test_on_error_fail_explicit(self):
        wf = load_workflow_string("""
steps:
  step_a:
    executor: external
    outputs: [result]
    on_error: fail
""")
        assert wf.steps["step_a"].on_error == "fail"

    def test_on_error_invalid_value_raises(self):
        with pytest.raises(YAMLLoadError) as exc_info:
            load_workflow_string("""
steps:
  step_a:
    executor: external
    outputs: [result]
    on_error: ignore
""")
        assert "on_error" in str(exc_info.value)
        assert "ignore" in str(exc_info.value)

    def test_on_error_continue_multi_step(self):
        wf = load_workflow_string("""
steps:
  review_a:
    executor: external
    outputs: [review]
    on_error: continue
  review_b:
    executor: external
    outputs: [review]
    on_error: continue
  synthesize:
    executor: external
    outputs: [summary]
    inputs:
      a: review_a.review
      b: review_b.review
""")
        assert wf.steps["review_a"].on_error == "continue"
        assert wf.steps["review_b"].on_error == "continue"
        assert wf.steps["synthesize"].on_error == "fail"


class TestOnErrorSerialization:
    """Test on_error field serializes correctly."""

    def test_fail_default_not_serialized(self):
        """on_error: fail should not appear in to_dict (omit when default)."""
        step = StepDefinition(
            name="step",
            outputs=["result"],
            executor=ExecutorRef("external", {}),
            on_error="fail",
        )
        d = step.to_dict()
        assert "on_error" not in d

    def test_continue_is_serialized(self):
        step = StepDefinition(
            name="step",
            outputs=["result"],
            executor=ExecutorRef("external", {}),
            on_error="continue",
        )
        d = step.to_dict()
        assert d.get("on_error") == "continue"

    def test_roundtrip_via_dict(self):
        step = StepDefinition(
            name="step",
            outputs=["result"],
            executor=ExecutorRef("external", {}),
            on_error="continue",
        )
        restored = StepDefinition.from_dict(step.to_dict())
        assert restored.on_error == "continue"

    def test_roundtrip_default_via_dict(self):
        step = StepDefinition(
            name="step",
            outputs=["result"],
            executor=ExecutorRef("external", {}),
        )
        restored = StepDefinition.from_dict(step.to_dict())
        assert restored.on_error == "fail"


# ── Engine behavior tests ─────────────────────────────────────────────────────


class TestOnErrorContinueEngine:
    """Test engine behavior when steps fail with on_error: continue."""

    @pytest.fixture
    def engine_store(self):
        engine, store = _make_engine()
        return engine, store

    def test_job_completes_when_on_error_continue_step_fails(self, engine_store):
        """A step with on_error: continue that fails should not halt the job.
        The next step should run and the job should complete.
        """
        engine, store = engine_store

        register_step_fn("fail_step", lambda inputs: (_ for _ in ()).throw(
            RuntimeError("simulated failure")
        ))
        register_step_fn("succeed_step", lambda inputs: {"result": "ok"})
        register_step_fn("synthesize_fn", lambda inputs: {"summary": "done"})

        wf = WorkflowDefinition(steps={
            "reviewer": StepDefinition(
                name="reviewer",
                executor=ExecutorRef("callable", {"fn_name": "fail_step"}),
                outputs=["review"],
                on_error="continue",
            ),
            "synthesize": StepDefinition(
                name="synthesize",
                executor=ExecutorRef("callable", {"fn_name": "synthesize_fn"}),
                inputs=[InputBinding("review_input", "reviewer", "review")],
                outputs=["summary"],
            ),
        })

        job = engine.create_job(objective="test", workflow=wf)
        engine.start_job(job.id)
        engine.tick()

        job = store.load_job(job.id)
        assert job.status == JobStatus.COMPLETED

        # reviewer failed
        reviewer_run = store.latest_run(job.id, "reviewer")
        assert reviewer_run.status == StepRunStatus.FAILED

        # synthesize completed despite reviewer failure
        synth_run = store.latest_run(job.id, "synthesize")
        assert synth_run is not None
        assert synth_run.status == StepRunStatus.COMPLETED

    def test_job_fails_when_on_error_fail_step_fails(self, engine_store):
        """A step with on_error: fail (default) that fails should halt the job immediately."""
        engine, store = engine_store

        register_step_fn("fail_step", lambda inputs: (_ for _ in ()).throw(
            RuntimeError("simulated failure")
        ))
        register_step_fn("synthesize_fn", lambda inputs: {"summary": "done"})

        wf = WorkflowDefinition(steps={
            "reviewer": StepDefinition(
                name="reviewer",
                executor=ExecutorRef("callable", {"fn_name": "fail_step"}),
                outputs=["review"],
                on_error="fail",  # default — halts job
            ),
            "synthesize": StepDefinition(
                name="synthesize",
                executor=ExecutorRef("callable", {"fn_name": "synthesize_fn"}),
                inputs=[InputBinding("review_input", "reviewer", "review")],
                outputs=["summary"],
            ),
        })

        job = engine.create_job(objective="test", workflow=wf)
        engine.start_job(job.id)
        engine.tick()

        job = store.load_job(job.id)
        assert job.status == JobStatus.FAILED

        # synthesize should NOT have run
        synth_run = store.latest_run(job.id, "synthesize")
        assert synth_run is None or synth_run.status == StepRunStatus.SKIPPED

    def test_parallel_steps_with_one_failing_others_complete(self, engine_store):
        """Parallel steps: one fails with on_error: continue, others complete.
        The synthesize step runs and the job completes.
        """
        engine, store = engine_store

        fail_count = [0]

        def selective_fail(inputs):
            if inputs.get("_reviewer") == "b":
                raise RuntimeError("reviewer B failed")
            return {"review": f"review from {inputs.get('_reviewer', '?')}"}

        register_step_fn("review_a", lambda inputs: {"review": "review A"})
        register_step_fn("review_b", lambda inputs: (_ for _ in ()).throw(
            RuntimeError("reviewer B failed")
        ))
        register_step_fn("review_c", lambda inputs: {"review": "review C"})
        register_step_fn("synthesize_fn", lambda inputs: {
            "summary": f"synthesized: a={inputs.get('a')}, b={inputs.get('b')}, c={inputs.get('c')}"
        })

        wf = WorkflowDefinition(steps={
            "review_a": StepDefinition(
                name="review_a",
                executor=ExecutorRef("callable", {"fn_name": "review_a"}),
                outputs=["review"],
                on_error="continue",
            ),
            "review_b": StepDefinition(
                name="review_b",
                executor=ExecutorRef("callable", {"fn_name": "review_b"}),
                outputs=["review"],
                on_error="continue",
            ),
            "review_c": StepDefinition(
                name="review_c",
                executor=ExecutorRef("callable", {"fn_name": "review_c"}),
                outputs=["review"],
                on_error="continue",
            ),
            "synthesize": StepDefinition(
                name="synthesize",
                executor=ExecutorRef("callable", {"fn_name": "synthesize_fn"}),
                inputs=[
                    InputBinding("a", "review_a", "review"),
                    InputBinding("b", "review_b", "review"),
                    InputBinding("c", "review_c", "review"),
                ],
                outputs=["summary"],
            ),
        })

        job = engine.create_job(objective="parallel-on-error-test", workflow=wf)
        engine.start_job(job.id)
        engine.tick()

        job = store.load_job(job.id)
        assert job.status == JobStatus.COMPLETED, f"Job should complete, got {job.status}"

        # review_a and review_c should have completed
        run_a = store.latest_run(job.id, "review_a")
        assert run_a.status == StepRunStatus.COMPLETED

        run_b = store.latest_run(job.id, "review_b")
        assert run_b.status == StepRunStatus.FAILED

        run_c = store.latest_run(job.id, "review_c")
        assert run_c.status == StepRunStatus.COMPLETED

        # synthesize should have run and completed
        synth_run = store.latest_run(job.id, "synthesize")
        assert synth_run is not None
        assert synth_run.status == StepRunStatus.COMPLETED

        # synthesize inputs: b should be None (failed dep)
        assert synth_run.inputs.get("a") == "review A"
        assert synth_run.inputs.get("b") is None  # failed dep → None
        assert synth_run.inputs.get("c") == "review C"

    def test_failed_dep_input_resolves_to_none(self, engine_store):
        """When an on_error: continue dep fails, downstream step receives None for that input."""
        engine, store = engine_store

        register_step_fn("fail_fn", lambda inputs: (_ for _ in ()).throw(RuntimeError("fail")))
        register_step_fn("check_fn", lambda inputs: {"received": inputs.get("upstream_val")})

        wf = WorkflowDefinition(steps={
            "upstream": StepDefinition(
                name="upstream",
                executor=ExecutorRef("callable", {"fn_name": "fail_fn"}),
                outputs=["value"],
                on_error="continue",
            ),
            "downstream": StepDefinition(
                name="downstream",
                executor=ExecutorRef("callable", {"fn_name": "check_fn"}),
                inputs=[InputBinding("upstream_val", "upstream", "value")],
                outputs=["received"],
            ),
        })

        job = engine.create_job(objective="null-input-test", workflow=wf)
        engine.start_job(job.id)
        engine.tick()

        job = store.load_job(job.id)
        assert job.status == JobStatus.COMPLETED

        downstream_run = store.latest_run(job.id, "downstream")
        assert downstream_run.status == StepRunStatus.COMPLETED
        assert downstream_run.inputs.get("upstream_val") is None

    def test_any_of_input_uses_successful_dep(self, engine_store):
        """With any_of inputs, if one source fails (on_error: continue) and another
        succeeds, the successful one is used.
        """
        engine, store = engine_store

        register_step_fn("fail_fn", lambda inputs: (_ for _ in ()).throw(RuntimeError("fail")))
        register_step_fn("succeed_fn", lambda inputs: {"result": "from_b"})
        register_step_fn("consume_fn", lambda inputs: {"output": inputs.get("combined")})

        wf = WorkflowDefinition(steps={
            "source_a": StepDefinition(
                name="source_a",
                executor=ExecutorRef("callable", {"fn_name": "fail_fn"}),
                outputs=["result"],
                on_error="continue",
            ),
            "source_b": StepDefinition(
                name="source_b",
                executor=ExecutorRef("callable", {"fn_name": "succeed_fn"}),
                outputs=["result"],
            ),
            "consumer": StepDefinition(
                name="consumer",
                executor=ExecutorRef("callable", {"fn_name": "consume_fn"}),
                inputs=[
                    InputBinding(
                        local_name="combined",
                        source_step="",
                        source_field="",
                        any_of_sources=[("source_a", "result"), ("source_b", "result")],
                    ),
                ],
                outputs=["output"],
            ),
        })

        job = engine.create_job(objective="any-of-test", workflow=wf)
        engine.start_job(job.id)
        engine.tick()

        job = store.load_job(job.id)
        assert job.status == JobStatus.COMPLETED

        consumer_run = store.latest_run(job.id, "consumer")
        assert consumer_run.status == StepRunStatus.COMPLETED
        # should have used source_b's result (source_a failed)
        assert consumer_run.inputs.get("combined") == "from_b"

    def test_all_parallel_steps_fail_with_on_error_continue_job_fails(self, engine_store):
        """If ALL deps fail (even with on_error: continue), downstream proceeds but
        if the downstream itself also fails, the job fails appropriately.
        In this test, all parallel steps fail but synthesize still runs successfully.
        """
        engine, store = engine_store

        register_step_fn("fail_fn", lambda inputs: (_ for _ in ()).throw(RuntimeError("fail")))
        register_step_fn("synthesize_fn", lambda inputs: {"summary": "partial"})

        wf = WorkflowDefinition(steps={
            "review_a": StepDefinition(
                name="review_a",
                executor=ExecutorRef("callable", {"fn_name": "fail_fn"}),
                outputs=["review"],
                on_error="continue",
            ),
            "review_b": StepDefinition(
                name="review_b",
                executor=ExecutorRef("callable", {"fn_name": "fail_fn"}),
                outputs=["review"],
                on_error="continue",
            ),
            "synthesize": StepDefinition(
                name="synthesize",
                executor=ExecutorRef("callable", {"fn_name": "synthesize_fn"}),
                inputs=[
                    InputBinding("a", "review_a", "review"),
                    InputBinding("b", "review_b", "review"),
                ],
                outputs=["summary"],
            ),
        })

        job = engine.create_job(objective="all-fail-test", workflow=wf)
        engine.start_job(job.id)
        engine.tick()

        job = store.load_job(job.id)
        # synthesize still runs and completes — so job completes
        assert job.status == JobStatus.COMPLETED

        synth_run = store.latest_run(job.id, "synthesize")
        assert synth_run.status == StepRunStatus.COMPLETED
        assert synth_run.inputs.get("a") is None
        assert synth_run.inputs.get("b") is None

    def test_on_error_fail_default_still_halts_job(self, engine_store):
        """Regression: default behavior (on_error: fail) still halts job on step failure."""
        engine, store = engine_store

        register_step_fn("fail_fn", lambda inputs: (_ for _ in ()).throw(RuntimeError("oops")))
        register_step_fn("ok_fn", lambda inputs: {"result": "ok"})

        wf = WorkflowDefinition(steps={
            "step_a": StepDefinition(
                name="step_a",
                executor=ExecutorRef("callable", {"fn_name": "fail_fn"}),
                outputs=["result"],
                # on_error defaults to "fail"
            ),
            "step_b": StepDefinition(
                name="step_b",
                executor=ExecutorRef("callable", {"fn_name": "ok_fn"}),
                inputs=[InputBinding("x", "step_a", "result")],
                outputs=["result"],
            ),
        })

        job = engine.create_job(objective="halt-test", workflow=wf)
        engine.start_job(job.id)
        engine.tick()

        job = store.load_job(job.id)
        assert job.status == JobStatus.FAILED

        step_b_run = store.latest_run(job.id, "step_b")
        # step_b should not have run (or be skipped)
        assert step_b_run is None or step_b_run.status == StepRunStatus.SKIPPED
