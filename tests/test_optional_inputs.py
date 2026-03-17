"""Tests for optional inputs: weak-reference bindings that resolve to None."""

import pytest

from tests.conftest import register_step_fn, run_job_sync
from stepwise.engine import AsyncEngine
from stepwise.executors import ExecutorRegistry
from stepwise.models import (
    ExitRule,
    ExecutorRef,
    InputBinding,
    JobStatus,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
)
from stepwise.store import SQLiteStore
from stepwise.yaml_loader import load_workflow_string, YAMLLoadError


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    from tests.conftest import CallableExecutor
    reg.register("callable", lambda cfg: CallableExecutor(fn_name=cfg.get("fn_name", "default")))
    return AsyncEngine(store=store, registry=reg)


# ── Tests ────────────────────────────────────────────────────────────


class TestOptionalInputNone:
    def test_optional_input_none_on_first_run(self, engine):
        """Step with optional input from unfinished dep — resolves to None."""
        received = {}

        register_step_fn("step_a_val", lambda i: {"value": "hello"})

        def step_b(inputs):
            received.update(inputs)
            return {"result": "done"}

        register_step_fn("step_b", step_b)

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("callable", {"fn_name": "step_a_val"}),
                inputs=[],
            ),
            "b": StepDefinition(
                name="b", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "step_b"}),
                inputs=[
                    InputBinding("prev", "a", "value", optional=True),
                ],
            ),
        })

        # Both a and b should be entry steps since b's dep on a is optional
        entries = wf.entry_steps()
        assert "b" in entries

        job = engine.create_job("optional test", wf)
        result = run_job_sync(engine, job.id)
        assert result.status == JobStatus.COMPLETED

    def test_optional_input_populated_on_loop(self, engine):
        """After loop, optional dep has completed — input resolves to its value."""
        call_count = {"n": 0}

        def step_a(inputs):
            call_count["n"] += 1
            prev = inputs.get("prev_result")
            if call_count["n"] == 1:
                assert prev is None  # first run — optional dep not available
                return {"done": False, "value": "first"}
            else:
                assert prev == "first"  # second run — optional dep available
                return {"done": True, "value": "second"}

        register_step_fn("loop_step", step_a)

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["done", "value"],
                executor=ExecutorRef("callable", {"fn_name": "loop_step"}),
                inputs=[
                    InputBinding("prev_result", "a", "value", optional=True),
                ],
                exit_rules=[
                    ExitRule("done", "expression", {
                        "condition": "outputs.get('done', False)",
                        "action": "advance",
                    }, priority=10),
                    ExitRule("loop", "expression", {
                        "condition": "not outputs.get('done', False)",
                        "action": "loop", "target": "a", "max_iterations": 5,
                    }, priority=5),
                ],
            ),
        })

        job = engine.create_job("loop optional test", wf)
        result = run_job_sync(engine, job.id)
        assert result.status == JobStatus.COMPLETED
        assert call_count["n"] == 2


class TestOptionalInputCycle:
    def test_optional_input_cycle_allowed(self, engine):
        """A→B→A cycle where A's input from B is optional — validates OK."""
        register_step_fn("cycle_a", lambda i: {"out": "a_out"})
        register_step_fn("cycle_b", lambda i: {"out": "b_out"})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["out"],
                executor=ExecutorRef("callable", {"fn_name": "cycle_a"}),
                inputs=[
                    InputBinding("b_val", "b", "out", optional=True),
                ],
            ),
            "b": StepDefinition(
                name="b", outputs=["out"],
                executor=ExecutorRef("callable", {"fn_name": "cycle_b"}),
                inputs=[
                    InputBinding("a_val", "a", "out"),
                ],
            ),
        })

        # Validation should pass — optional edge breaks the cycle
        errors = wf.validate()
        assert not errors, f"Unexpected errors: {errors}"

        job = engine.create_job("cycle test", wf)
        result = run_job_sync(engine, job.id)
        assert result.status == JobStatus.COMPLETED

    def test_optional_input_required_cycle_rejected(self):
        """Same cycle but non-optional — validation raises cycle error."""
        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["out"],
                executor=ExecutorRef("callable", {"fn_name": "cycle_a"}),
                inputs=[
                    InputBinding("b_val", "b", "out"),  # NOT optional
                ],
            ),
            "b": StepDefinition(
                name="b", outputs=["out"],
                executor=ExecutorRef("callable", {"fn_name": "cycle_b"}),
                inputs=[
                    InputBinding("a_val", "a", "out"),
                ],
            ),
        })

        errors = wf.validate()
        assert any("Cycle" in e or "cycle" in e for e in errors)


class TestOptionalAnyOf:
    def test_optional_any_of(self, engine):
        """any_of with optional: true — if no sources completed, resolves to None.

        Steps x and y exist but are gated by 'when: "false"', so they never run.
        Step c with optional any_of should still proceed with val=None.
        """
        received = {}

        def step_c(inputs):
            received.update(inputs)
            return {"result": "done"}

        register_step_fn("any_of_step", step_c)
        register_step_fn("never_fn", lambda i: {"out": "x"})

        wf = WorkflowDefinition(steps={
            "x": StepDefinition(
                name="x", outputs=["out"],
                executor=ExecutorRef("callable", {"fn_name": "never_fn"}),
                when="False",  # never activates
            ),
            "y": StepDefinition(
                name="y", outputs=["out"],
                executor=ExecutorRef("callable", {"fn_name": "never_fn"}),
                when="False",  # never activates
            ),
            "c": StepDefinition(
                name="c", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "any_of_step"}),
                inputs=[
                    InputBinding(
                        "val", "", "",
                        any_of_sources=[("x", "out"), ("y", "out")],
                        optional=True,
                    ),
                ],
            ),
        })

        job = engine.create_job("any_of optional test", wf)
        result = run_job_sync(engine, job.id)
        assert result.status == JobStatus.COMPLETED
        assert received["val"] is None


class TestOptionalInputScript:
    def test_optional_input_none_in_script(self, engine):
        """Script step with optional None input — resolves to None when dep hasn't run."""
        register_step_fn("script_like", lambda i: {
            "got_val": i.get("opt_val") is None,
            "result": "ok",
        })
        register_step_fn("blocked_fn", lambda i: {"field": "data"})

        wf = WorkflowDefinition(steps={
            "blocker": StepDefinition(
                name="blocker", outputs=["field"],
                executor=ExecutorRef("callable", {"fn_name": "blocked_fn"}),
                when="False",  # never runs
            ),
            "s": StepDefinition(
                name="s", outputs=["got_val", "result"],
                executor=ExecutorRef("callable", {"fn_name": "script_like"}),
                inputs=[
                    InputBinding("opt_val", "blocker", "field", optional=True),
                ],
            ),
        })

        job = engine.create_job("script optional test", wf)
        result = run_job_sync(engine, job.id)
        assert result.status == JobStatus.COMPLETED
        runs = [r for r in engine.store.runs_for_job(job.id) if r.step_name == "s"]
        assert runs[0].result.artifact["got_val"] is True


class TestOptionalCurrentness:
    def test_currentness_skips_optional_deps(self, engine):
        """Step with optional dep — currentness not invalidated when optional dep re-runs."""
        register_step_fn("curr_a", lambda i: {"out": "a"})
        register_step_fn("curr_b", lambda i: {"out": "b"})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["out"],
                executor=ExecutorRef("callable", {"fn_name": "curr_a"}),
            ),
            "b": StepDefinition(
                name="b", outputs=["out"],
                executor=ExecutorRef("callable", {"fn_name": "curr_b"}),
                inputs=[
                    InputBinding("a_val", "a", "out", optional=True),
                ],
            ),
        })

        job = engine.create_job("currentness test", wf)
        result = run_job_sync(engine, job.id)
        assert result.status == JobStatus.COMPLETED


class TestOptionalYAMLParsing:
    def test_yaml_parse_optional_input(self):
        """Parse {from: 'step.field', optional: true} → correct InputBinding."""
        wf = load_workflow_string("""
steps:
  fetch:
    run: 'echo "{}"'
    outputs: [data]
  process:
    run: 'echo "{}"'
    inputs:
      prev: {from: "fetch.data", optional: true}
    outputs: [result]
""")
        binding = wf.steps["process"].inputs[0]
        assert binding.local_name == "prev"
        assert binding.source_step == "fetch"
        assert binding.source_field == "data"
        assert binding.optional is True

    def test_yaml_parse_any_of_optional(self):
        """Parse any_of with optional: true → correct binding."""
        wf = load_workflow_string("""
steps:
  x:
    run: 'echo "{}"'
    outputs: [out]
  y:
    run: 'echo "{}"'
    outputs: [out]
  z:
    run: 'echo "{}"'
    inputs:
      val: {any_of: ["x.out", "y.out"], optional: true}
    outputs: [result]
""")
        binding = wf.steps["z"].inputs[0]
        assert binding.any_of_sources is not None
        assert binding.optional is True


class TestOptionalSerialization:
    def test_optional_input_serialization(self):
        """InputBinding with optional=True round-trips through to_dict/from_dict."""
        binding = InputBinding("prev", "step_a", "value", optional=True)
        d = binding.to_dict()
        assert d["optional"] is True

        restored = InputBinding.from_dict(d)
        assert restored.optional is True
        assert restored.local_name == "prev"
        assert restored.source_step == "step_a"
        assert restored.source_field == "value"

    def test_optional_false_not_serialized(self):
        """InputBinding with optional=False doesn't include optional in dict."""
        binding = InputBinding("x", "y", "z", optional=False)
        d = binding.to_dict()
        assert "optional" not in d

    def test_optional_missing_defaults_false(self):
        """from_dict without optional key defaults to False."""
        d = {"local_name": "x", "source_step": "y", "source_field": "z"}
        binding = InputBinding.from_dict(d)
        assert binding.optional is False
