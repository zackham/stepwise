"""Tests for DAG branching primitives: step-level `when`, any_of, settlement."""

import pytest

from stepwise.models import (
    ExitRule,
    ExecutorRef,
    InputBinding,
    JobStatus,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
)
from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError
from tests.conftest import register_step_fn, run_job_sync


# ── Helpers ──────────────────────────────────────────────────────────


def _callable_ref(fn_name: str) -> ExecutorRef:
    return ExecutorRef(type="callable", config={"fn_name": fn_name})


# ── Test: basic when branching ───────────────────────────────────────


class TestWhenBranch:
    def test_basic_when_branch(self, async_engine):
        """classify outputs data, two downstream steps with `when` conditions,
        only the matching branch runs."""
        register_step_fn("classify", lambda inputs: {"category": "simple"})
        register_step_fn("quick", lambda inputs: {"result": "quick done"})
        register_step_fn("deep", lambda inputs: {"result": "deep done"})

        wf = WorkflowDefinition(steps={
            "classify": StepDefinition(
                name="classify",
                executor=_callable_ref("classify"),
                outputs=["category"],
            ),
            "quick-path": StepDefinition(
                name="quick-path",
                executor=_callable_ref("quick"),
                inputs=[InputBinding("category", "classify", "category")],
                outputs=["result"],
                when="category == 'simple'",
            ),
            "deep-path": StepDefinition(
                name="deep-path",
                executor=_callable_ref("deep"),
                inputs=[InputBinding("category", "classify", "category")],
                outputs=["result"],
                when="category != 'simple'",
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        run_map = {r.step_name: r for r in runs}
        assert run_map["classify"].status == StepRunStatus.COMPLETED
        assert run_map["quick-path"].status == StepRunStatus.COMPLETED
        assert run_map["deep-path"].status == StepRunStatus.SKIPPED

    def test_no_when_both_run(self, async_engine):
        """Steps without `when` activate unconditionally when deps are met."""
        register_step_fn("start", lambda inputs: {"x": 1})
        register_step_fn("a", lambda inputs: {"result": "a"})
        register_step_fn("b", lambda inputs: {"result": "b"})

        wf = WorkflowDefinition(steps={
            "start": StepDefinition(
                name="start",
                executor=_callable_ref("start"),
                outputs=["x"],
            ),
            "a": StepDefinition(
                name="a",
                executor=_callable_ref("a"),
                inputs=[InputBinding("x", "start", "x")],
                outputs=["result"],
            ),
            "b": StepDefinition(
                name="b",
                executor=_callable_ref("b"),
                inputs=[InputBinding("x", "start", "x")],
                outputs=["result"],
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        run_map = {r.step_name: r for r in runs}
        assert run_map["a"].status == StepRunStatus.COMPLETED
        assert run_map["b"].status == StepRunStatus.COMPLETED


# ── Test: settlement ─────────────────────────────────────────────────


class TestSettlement:
    def test_unmet_when_settled_as_skipped(self, async_engine):
        """Branch not taken → step never runs → settled SKIPPED."""
        register_step_fn("classify", lambda inputs: {"category": "simple"})
        register_step_fn("quick", lambda inputs: {"result": "quick done"})
        register_step_fn("deep", lambda inputs: {"result": "deep done"})

        wf = WorkflowDefinition(steps={
            "classify": StepDefinition(
                name="classify",
                executor=_callable_ref("classify"),
                outputs=["category"],
            ),
            "quick-path": StepDefinition(
                name="quick-path",
                executor=_callable_ref("quick"),
                inputs=[InputBinding("category", "classify", "category")],
                outputs=["result"],
                when="category == 'simple'",
            ),
            "deep-path": StepDefinition(
                name="deep-path",
                executor=_callable_ref("deep"),
                inputs=[InputBinding("category", "classify", "category")],
                outputs=["result"],
                when="category != 'simple'",
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        deep_runs = [r for r in runs if r.step_name == "deep-path"]
        assert len(deep_runs) == 1
        assert deep_runs[0].status == StepRunStatus.SKIPPED
        assert deep_runs[0].error == "Not reached"

    def test_transitive_settlement(self, async_engine):
        """A→B→C chain, B gated by false `when` → B and C both settled SKIPPED."""
        register_step_fn("a", lambda inputs: {"x": 1})
        register_step_fn("b", lambda inputs: {"y": 2})
        register_step_fn("c", lambda inputs: {"z": 3})
        register_step_fn("d", lambda inputs: {"result": "done"})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a",
                executor=_callable_ref("a"),
                outputs=["x"],
            ),
            "b": StepDefinition(
                name="b",
                executor=_callable_ref("b"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["y"],
                when="x == 999",  # never true
            ),
            "c": StepDefinition(
                name="c",
                executor=_callable_ref("c"),
                inputs=[InputBinding("y", "b", "y")],
                outputs=["z"],
            ),
            "d": StepDefinition(
                name="d",
                executor=_callable_ref("d"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["result"],
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        run_map = {r.step_name: r for r in runs}
        assert run_map["b"].status == StepRunStatus.SKIPPED
        assert run_map["c"].status == StepRunStatus.SKIPPED
        assert run_map["d"].status == StepRunStatus.COMPLETED


# ── Test: any_of ────────────────────────────────────────────────────


class TestAnyOf:
    def test_merge_with_any_of(self, async_engine):
        """Branch A or B, merge step uses any_of, completes when either arrives."""
        register_step_fn("classify", lambda inputs: {"x": 1})
        register_step_fn("left", lambda inputs: {"result": "from-left"})
        register_step_fn("right", lambda inputs: {"result": "from-right"})
        register_step_fn("merge", lambda inputs: {"out": inputs["r"]})

        wf = WorkflowDefinition(steps={
            "classify": StepDefinition(
                name="classify",
                executor=_callable_ref("classify"),
                outputs=["x"],
            ),
            "left": StepDefinition(
                name="left",
                executor=_callable_ref("left"),
                inputs=[InputBinding("x", "classify", "x")],
                outputs=["result"],
                when="x == 1",
            ),
            "right": StepDefinition(
                name="right",
                executor=_callable_ref("right"),
                inputs=[InputBinding("x", "classify", "x")],
                outputs=["result"],
                when="x == 2",
            ),
            "merge": StepDefinition(
                name="merge",
                executor=_callable_ref("merge"),
                inputs=[InputBinding(
                    local_name="r", source_step="", source_field="",
                    any_of_sources=[("left", "result"), ("right", "result")],
                )],
                outputs=["out"],
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        run_map = {r.step_name: r for r in runs}
        assert run_map["right"].status == StepRunStatus.SKIPPED
        assert run_map["left"].status == StepRunStatus.COMPLETED
        assert run_map["merge"].status == StepRunStatus.COMPLETED
        assert run_map["merge"].result.artifact["out"] == "from-left"

    def test_all_sources_unmet_settled(self, async_engine):
        """If all any_of sources gated by false `when`, merge step settled SKIPPED."""
        register_step_fn("a", lambda inputs: {"x": 1})
        register_step_fn("b", lambda inputs: {"result": "b"})
        register_step_fn("c", lambda inputs: {"result": "c"})
        register_step_fn("d", lambda inputs: {"result": "d"})
        register_step_fn("merge", lambda inputs: {"final": "nope"})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a",
                executor=_callable_ref("a"),
                outputs=["x"],
            ),
            "b": StepDefinition(
                name="b",
                executor=_callable_ref("b"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["result"],
                when="x == 999",  # never true
            ),
            "c": StepDefinition(
                name="c",
                executor=_callable_ref("c"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["result"],
                when="x == 888",  # never true
            ),
            "d": StepDefinition(
                name="d",
                executor=_callable_ref("d"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["result"],
            ),
            "merge": StepDefinition(
                name="merge",
                executor=_callable_ref("merge"),
                inputs=[InputBinding(
                    local_name="r", source_step="", source_field="",
                    any_of_sources=[("b", "result"), ("c", "result")],
                )],
                outputs=["final"],
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        run_map = {r.step_name: r for r in runs}
        assert run_map["b"].status == StepRunStatus.SKIPPED
        assert run_map["c"].status == StepRunStatus.SKIPPED
        assert run_map["merge"].status == StepRunStatus.SKIPPED
        assert run_map["d"].status == StepRunStatus.COMPLETED

    def test_resolves_first_available(self, async_engine):
        """When multiple any_of sources completed, first in list wins."""
        register_step_fn("a", lambda inputs: {"r": "a-result"})
        register_step_fn("b", lambda inputs: {"r": "b-result"})
        register_step_fn("merge", lambda inputs: {"out": inputs["val"]})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a",
                executor=_callable_ref("a"),
                outputs=["r"],
            ),
            "b": StepDefinition(
                name="b",
                executor=_callable_ref("b"),
                outputs=["r"],
            ),
            "merge": StepDefinition(
                name="merge",
                executor=_callable_ref("merge"),
                inputs=[InputBinding(
                    local_name="val", source_step="", source_field="",
                    any_of_sources=[("a", "r"), ("b", "r")],
                )],
                outputs=["out"],
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        run_map = {r.step_name: r for r in runs}
        assert run_map["merge"].result.artifact["out"] == "a-result"

    def test_yaml_roundtrip(self):
        """Parse any_of YAML, to_dict(), from_dict(), verify equality."""
        yaml_str = """\
name: test
steps:
  a:
    run: 'echo ''{"x": 1}'''
    outputs: [x]
  b:
    run: 'echo ''{"r": "b"}'''
    inputs:
      x: a.x
    outputs: [r]
  c:
    run: 'echo ''{"r": "c"}'''
    inputs:
      x: a.x
    outputs: [r]
  merge:
    run: 'echo ''{"out": "done"}'''
    inputs:
      val:
        any_of:
          - b.r
          - c.r
    outputs: [out]
"""
        wf = load_workflow_yaml(yaml_str)
        merge_step = wf.steps["merge"]
        assert len(merge_step.inputs) == 1
        binding = merge_step.inputs[0]
        assert binding.any_of_sources == [("b", "r"), ("c", "r")]

        # Round-trip through dict
        d = wf.to_dict()
        wf2 = WorkflowDefinition.from_dict(d)
        binding2 = wf2.steps["merge"].inputs[0]
        assert binding2.any_of_sources == [("b", "r"), ("c", "r")]

    def test_validation_errors(self):
        """Invalid step/field refs and <2 sources produce validation errors."""
        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a",
                executor=ExecutorRef("script", {"command": "echo"}),
                outputs=["x"],
            ),
            "b": StepDefinition(
                name="b",
                executor=ExecutorRef("script", {"command": "echo"}),
                inputs=[InputBinding(
                    local_name="val", source_step="", source_field="",
                    any_of_sources=[("a", "x")],
                )],
                outputs=["out"],
            ),
        })
        errors = wf.validate()
        assert any("any_of must have >= 2 sources" in e for e in errors)


# ── Test: job completion with when ──────────────────────────────────


class TestJobCompletionWithWhen:
    def test_untaken_branch_doesnt_block_completion(self, async_engine):
        """Terminal on untaken branch settled SKIPPED, job completes via other terminal."""
        register_step_fn("a", lambda inputs: {"x": 1})
        register_step_fn("b", lambda inputs: {"result": "done"})
        register_step_fn("c", lambda inputs: {"result": "nope"})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a",
                executor=_callable_ref("a"),
                outputs=["x"],
            ),
            "b": StepDefinition(
                name="b",
                executor=_callable_ref("b"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["result"],
                when="x == 1",
            ),
            "c": StepDefinition(
                name="c",
                executor=_callable_ref("c"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["result"],
                when="x == 2",
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

    def test_job_fails_when_no_terminal_reached(self, async_engine):
        """All terminals gated by false `when`, job fails."""
        register_step_fn("a", lambda inputs: {"x": 1})
        register_step_fn("b", lambda inputs: {"result": "b"})
        register_step_fn("c", lambda inputs: {"result": "c"})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a",
                executor=_callable_ref("a"),
                outputs=["x"],
            ),
            "b": StepDefinition(
                name="b",
                executor=_callable_ref("b"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["result"],
                when="x == 999",
            ),
            "c": StepDefinition(
                name="c",
                executor=_callable_ref("c"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["result"],
                when="x == 888",
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.FAILED


# ── Test: loop + when interaction ────────────────────────────────────


class TestLoopWithWhen:
    def test_loop_reruns_upstream_when_reevaluates(self, async_engine):
        """check→fix loop: first pass "fail", fix loops, second pass "pass".
        No stale skips — `when` just re-evaluates."""
        call_count = {"n": 0}

        def check_fn(inputs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {"status": "fail"}
            return {"status": "pass"}

        register_step_fn("check", check_fn)
        register_step_fn("fix", lambda inputs: {"patched": True})
        register_step_fn("done", lambda inputs: {"result": "success"})

        wf = WorkflowDefinition(steps={
            "check": StepDefinition(
                name="check",
                executor=_callable_ref("check"),
                outputs=["status"],
            ),
            "fix": StepDefinition(
                name="fix",
                executor=_callable_ref("fix"),
                inputs=[InputBinding("status", "check", "status")],
                outputs=["patched"],
                when="status == 'fail'",
                exit_rules=[
                    ExitRule(
                        name="retry",
                        type="always",
                        config={"action": "loop", "target": "check"},
                    ),
                ],
            ),
            "done": StepDefinition(
                name="done",
                executor=_callable_ref("done"),
                inputs=[InputBinding("status", "check", "status")],
                outputs=["result"],
                when="status == 'pass'",
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)

        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        by_step: dict[str, list] = {}
        for r in runs:
            by_step.setdefault(r.step_name, []).append(r)

        # check ran twice
        assert len(by_step["check"]) == 2

        # done completed
        done_runs = [r for r in by_step["done"] if r.status == StepRunStatus.COMPLETED]
        assert len(done_runs) == 1
        assert done_runs[0].result.artifact["result"] == "success"


# ── Test: failure routing ────────────────────────────────────────────


class TestFailureRouting:
    def test_failed_upstream_settles_dependents(self, async_engine):
        """Upstream step fails, all dependents settled SKIPPED."""
        def fail_fn(inputs):
            raise ValueError("boom")

        register_step_fn("fail_step", fail_fn)
        register_step_fn("downstream", lambda inputs: {"result": "nope"})
        register_step_fn("other", lambda inputs: {"result": "done"})

        wf = WorkflowDefinition(steps={
            "fail-step": StepDefinition(
                name="fail-step",
                executor=_callable_ref("fail_step"),
                outputs=["x"],
            ),
            "downstream": StepDefinition(
                name="downstream",
                executor=_callable_ref("downstream"),
                inputs=[InputBinding("x", "fail-step", "x")],
                outputs=["result"],
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.FAILED

        runs = async_engine.store.runs_for_job(job.id)
        run_map = {r.step_name: r for r in runs}
        assert run_map["fail-step"].status == StepRunStatus.FAILED
        assert run_map["downstream"].status == StepRunStatus.SKIPPED


# ── Test: when-specific behavior ─────────────────────────────────────


class TestWhenSpecific:
    def test_when_true_always_runs(self, async_engine):
        """Step with `when: "True"` activates normally."""
        register_step_fn("a", lambda inputs: {"x": 1})
        register_step_fn("b", lambda inputs: {"result": "done"})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a",
                executor=_callable_ref("a"),
                outputs=["x"],
            ),
            "b": StepDefinition(
                name="b",
                executor=_callable_ref("b"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["result"],
                when="True",
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        run_map = {r.step_name: r for r in runs}
        assert run_map["b"].status == StepRunStatus.COMPLETED

    def test_when_false_never_runs(self, async_engine):
        """Step with `when: "False"`, job settles, step SKIPPED."""
        register_step_fn("a", lambda inputs: {"x": 1})
        register_step_fn("b", lambda inputs: {"result": "done"})
        register_step_fn("c", lambda inputs: {"result": "fallback"})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a",
                executor=_callable_ref("a"),
                outputs=["x"],
            ),
            "b": StepDefinition(
                name="b",
                executor=_callable_ref("b"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["result"],
                when="False",
            ),
            "c": StepDefinition(
                name="c",
                executor=_callable_ref("c"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["result"],
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        run_map = {r.step_name: r for r in runs}
        assert run_map["b"].status == StepRunStatus.SKIPPED
        assert run_map["c"].status == StepRunStatus.COMPLETED

    def test_when_evaluates_inputs(self, async_engine):
        """when: "x > 5" runs when upstream outputs x=10."""
        register_step_fn("source", lambda inputs: {"x": 10})
        register_step_fn("gated", lambda inputs: {"result": "ran"})

        wf = WorkflowDefinition(steps={
            "source": StepDefinition(
                name="source",
                executor=_callable_ref("source"),
                outputs=["x"],
            ),
            "gated": StepDefinition(
                name="gated",
                executor=_callable_ref("gated"),
                inputs=[InputBinding("x", "source", "x")],
                outputs=["result"],
                when="x > 5",
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        run_map = {r.step_name: r for r in runs}
        assert run_map["gated"].status == StepRunStatus.COMPLETED

    def test_when_exception_doesnt_crash(self, async_engine):
        """Bad expression logs warning, step stays not-ready, job settles."""
        register_step_fn("a", lambda inputs: {"x": 1})
        register_step_fn("b", lambda inputs: {"result": "done"})
        register_step_fn("c", lambda inputs: {"result": "ok"})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a",
                executor=_callable_ref("a"),
                outputs=["x"],
            ),
            "b": StepDefinition(
                name="b",
                executor=_callable_ref("b"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["result"],
                when="undefined_var + 1",
            ),
            "c": StepDefinition(
                name="c",
                executor=_callable_ref("c"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["result"],
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        run_map = {r.step_name: r for r in runs}
        assert run_map["b"].status == StepRunStatus.SKIPPED
        assert run_map["c"].status == StepRunStatus.COMPLETED

    def test_settlement_only_at_job_end(self, async_engine):
        """During execution, steps without runs have no SKIPPED status;
        SKIPPED only appears after settlement."""
        # This is implicitly tested by the settlement tests above,
        # but we verify the specific property: settlement creates SKIPPED
        # only for never-started steps.
        register_step_fn("a", lambda inputs: {"x": 1})
        register_step_fn("b", lambda inputs: {"result": "done"})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a",
                executor=_callable_ref("a"),
                outputs=["x"],
            ),
            "b": StepDefinition(
                name="b",
                executor=_callable_ref("b"),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["result"],
                when="x == 999",
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        # Job fails since the only terminal is gated
        assert result.status == JobStatus.FAILED

        runs = async_engine.store.runs_for_job(job.id)
        b_runs = [r for r in runs if r.step_name == "b"]
        assert len(b_runs) == 1
        assert b_runs[0].status == StepRunStatus.SKIPPED
        assert b_runs[0].error == "Not reached"

    def test_when_with_any_of_input(self, async_engine):
        """when condition evaluates against any_of-resolved input value."""
        register_step_fn("a", lambda inputs: {"r": "good"})
        register_step_fn("b", lambda inputs: {"r": "bad"})
        register_step_fn("gate", lambda inputs: {"out": inputs["val"]})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a",
                executor=_callable_ref("a"),
                outputs=["r"],
            ),
            "b": StepDefinition(
                name="b",
                executor=_callable_ref("b"),
                outputs=["r"],
            ),
            "gate": StepDefinition(
                name="gate",
                executor=_callable_ref("gate"),
                inputs=[InputBinding(
                    local_name="val", source_step="", source_field="",
                    any_of_sources=[("a", "r"), ("b", "r")],
                )],
                outputs=["out"],
                when="val == 'good'",
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        run_map = {r.step_name: r for r in runs}
        assert run_map["gate"].status == StepRunStatus.COMPLETED
        assert run_map["gate"].result.artifact["out"] == "good"
