"""Tests for after_resolved: dependency semantic.

after_resolved allows a step to wait for dependencies to reach a terminal state
(COMPLETED or SKIPPED), rather than requiring COMPLETED. This enables
reconvergence after conditional branches where some paths are skipped.
"""

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
from stepwise.yaml_loader import load_workflow_string, YAMLLoadError
from tests.conftest import register_step_fn, run_job_sync


# ── Helpers ──────────────────────────────────────────────────────────


def _callable_ref(fn_name: str) -> ExecutorRef:
    return ExecutorRef(type="callable", config={"fn_name": fn_name})


# ── YAML parsing tests ──────────────────────────────────────────────


class TestAfterResolvedParsing:

    def test_after_resolved_string(self):
        wf = load_workflow_string("""
steps:
  step_a:
    executor: external
    outputs: [result]
  step_b:
    executor: external
    outputs: [result]
    after_resolved: step_a
""")
        assert wf.steps["step_b"].after_resolved == ["step_a"]

    def test_after_resolved_list(self):
        wf = load_workflow_string("""
steps:
  step_a:
    executor: external
    outputs: [result]
  step_b:
    executor: external
    outputs: [result]
  step_c:
    executor: external
    outputs: [result]
    after_resolved: [step_a, step_b]
""")
        assert wf.steps["step_c"].after_resolved == ["step_a", "step_b"]

    def test_after_resolved_empty(self):
        wf = load_workflow_string("""
steps:
  step_a:
    executor: external
    outputs: [result]
""")
        assert wf.steps["step_a"].after_resolved == []

    def test_after_resolved_coexists_with_after(self):
        wf = load_workflow_string("""
steps:
  step_a:
    executor: external
    outputs: [x]
  step_b:
    executor: external
    outputs: [y]
  step_c:
    executor: external
    outputs: [result]
    after: [step_a]
    after_resolved: [step_b]
""")
        assert wf.steps["step_c"].after == ["step_a"]
        assert wf.steps["step_c"].after_resolved == ["step_b"]


# ── Serialization round-trip ────────────────────────────────────────


class TestAfterResolvedSerialization:

    def test_round_trip(self):
        step = StepDefinition(
            name="test",
            outputs=["result"],
            executor=ExecutorRef("external", {}),
            after_resolved=["dep_a", "dep_b"],
        )
        d = step.to_dict()
        assert d["after_resolved"] == ["dep_a", "dep_b"]
        restored = StepDefinition.from_dict(d)
        assert restored.after_resolved == ["dep_a", "dep_b"]

    def test_empty_not_serialized(self):
        step = StepDefinition(
            name="test",
            outputs=["result"],
            executor=ExecutorRef("external", {}),
        )
        d = step.to_dict()
        assert "after_resolved" not in d


# ── Engine behavior tests ───────────────────────────────────────────


class TestAfterResolvedEngine:

    def test_reconverge_after_skipped_branch(self, async_engine):
        """Core use case: classify → branch_a (when true) / branch_b (when false)
        → merge (after_resolved: [branch_a, branch_b]).

        branch_b is skipped, but merge should still run because after_resolved
        accepts SKIPPED as resolved."""
        register_step_fn("classify", lambda inputs: {"category": "simple"})
        register_step_fn("branch_a", lambda inputs: {"result": "a done"})
        register_step_fn("branch_b", lambda inputs: {"result": "b done"})
        register_step_fn("merge", lambda inputs: {"final": "merged"})

        wf = WorkflowDefinition(steps={
            "classify": StepDefinition(
                name="classify",
                executor=_callable_ref("classify"),
                outputs=["category"],
            ),
            "branch_a": StepDefinition(
                name="branch_a",
                executor=_callable_ref("branch_a"),
                inputs=[InputBinding("category", "classify", "category")],
                outputs=["result"],
                when="category == 'simple'",
            ),
            "branch_b": StepDefinition(
                name="branch_b",
                executor=_callable_ref("branch_b"),
                inputs=[InputBinding("category", "classify", "category")],
                outputs=["result"],
                when="category != 'simple'",
            ),
            "merge": StepDefinition(
                name="merge",
                executor=_callable_ref("merge"),
                outputs=["final"],
                after_resolved=["branch_a", "branch_b"],
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        run_map = {r.step_name: r for r in runs if r.status != StepRunStatus.SKIPPED or r.step_name == "branch_b"}

        # branch_a completed, branch_b skipped, merge completed
        assert any(r.step_name == "branch_a" and r.status == StepRunStatus.COMPLETED for r in runs)
        assert any(r.step_name == "branch_b" and r.status == StepRunStatus.SKIPPED for r in runs)
        assert any(r.step_name == "merge" and r.status == StepRunStatus.COMPLETED for r in runs)

    def test_after_resolved_all_completed(self, async_engine):
        """after_resolved works when all deps complete (not skipped)."""
        register_step_fn("a", lambda inputs: {"x": 1})
        register_step_fn("b", lambda inputs: {"y": 2})
        register_step_fn("c", lambda inputs: {"result": "done"})

        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a",
                executor=_callable_ref("a"),
                outputs=["x"],
            ),
            "b": StepDefinition(
                name="b",
                executor=_callable_ref("b"),
                outputs=["y"],
            ),
            "c": StepDefinition(
                name="c",
                executor=_callable_ref("c"),
                outputs=["result"],
                after_resolved=["a", "b"],
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        assert any(r.step_name == "c" and r.status == StepRunStatus.COMPLETED for r in runs)

    def test_after_would_cascade_skip(self, async_engine):
        """Verify the problem: using plain after: with a skipped dep causes
        the downstream step to also be skipped (cascade skip)."""
        register_step_fn("start", lambda inputs: {"flag": False})
        register_step_fn("conditional", lambda inputs: {"result": "done"})
        register_step_fn("downstream", lambda inputs: {"final": "done"})

        wf = WorkflowDefinition(steps={
            "start": StepDefinition(
                name="start",
                executor=_callable_ref("start"),
                outputs=["flag"],
            ),
            "conditional": StepDefinition(
                name="conditional",
                executor=_callable_ref("conditional"),
                inputs=[InputBinding("flag", "start", "flag")],
                outputs=["result"],
                when="flag",
            ),
            "downstream": StepDefinition(
                name="downstream",
                executor=_callable_ref("downstream"),
                outputs=["final"],
                after=["conditional"],  # plain after — should cascade skip
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)

        runs = async_engine.store.runs_for_job(job.id)
        downstream_runs = [r for r in runs if r.step_name == "downstream"]
        # downstream should be skipped because conditional was skipped
        assert all(r.status == StepRunStatus.SKIPPED for r in downstream_runs)

    def test_after_resolved_prevents_cascade_skip(self, async_engine):
        """Same flow as above but with after_resolved — downstream should run."""
        register_step_fn("start", lambda inputs: {"flag": False})
        register_step_fn("conditional", lambda inputs: {"result": "done"})
        register_step_fn("downstream", lambda inputs: {"final": "done"})

        wf = WorkflowDefinition(steps={
            "start": StepDefinition(
                name="start",
                executor=_callable_ref("start"),
                outputs=["flag"],
            ),
            "conditional": StepDefinition(
                name="conditional",
                executor=_callable_ref("conditional"),
                inputs=[InputBinding("flag", "start", "flag")],
                outputs=["result"],
                when="flag",
            ),
            "downstream": StepDefinition(
                name="downstream",
                executor=_callable_ref("downstream"),
                outputs=["final"],
                after_resolved=["conditional"],  # after_resolved — should NOT cascade skip
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        assert any(r.step_name == "downstream" and r.status == StepRunStatus.COMPLETED for r in runs)

    def test_after_resolved_loop_invalidation(self, async_engine):
        """after_resolved deps that flip terminal state across loop iterations
        must be re-evaluated so the downstream merge step re-runs.

        Flow:
          dispatcher(attempt→turn) → [branch_a(when turn==1), branch_b(when turn==2)]
                                   → merge(after_resolved: [branch_a, branch_b])
                                   → controller(loop→dispatcher when attempt<2)

        Iteration 1: branch_a runs, branch_b skipped → merge runs
        Iteration 2: branch_a skipped (stale COMPLETED), branch_b runs → merge runs again
        """
        dispatch_count = [0]

        def dispatcher_fn(inputs):
            dispatch_count[0] += 1
            return {"turn": dispatch_count[0]}

        register_step_fn("dispatcher_fn", dispatcher_fn)
        register_step_fn("branch_a_fn", lambda inputs: {"result": "a"})
        register_step_fn("branch_b_fn", lambda inputs: {"result": "b"})
        register_step_fn("merge_fn", lambda inputs: {"merged": True})
        register_step_fn("controller_fn", lambda inputs: {"done": True})

        wf = WorkflowDefinition(steps={
            "dispatcher": StepDefinition(
                name="dispatcher",
                executor=_callable_ref("dispatcher_fn"),
                outputs=["turn"],
            ),
            "branch_a": StepDefinition(
                name="branch_a",
                executor=_callable_ref("branch_a_fn"),
                inputs=[InputBinding("turn", "dispatcher", "turn")],
                outputs=["result"],
                when="turn == 1",
            ),
            "branch_b": StepDefinition(
                name="branch_b",
                executor=_callable_ref("branch_b_fn"),
                inputs=[InputBinding("turn", "dispatcher", "turn")],
                outputs=["result"],
                when="turn == 2",
            ),
            "merge": StepDefinition(
                name="merge",
                executor=_callable_ref("merge_fn"),
                outputs=["merged"],
                after_resolved=["branch_a", "branch_b"],
            ),
            "controller": StepDefinition(
                name="controller",
                executor=_callable_ref("controller_fn"),
                inputs=[InputBinding("merged", "merge", "merged")],
                outputs=["done"],
                exit_rules=[
                    ExitRule("advance", "expression", {
                        "condition": "attempt >= 2",
                        "action": "advance",
                    }, priority=10),
                    ExitRule("loop", "expression", {
                        "condition": "attempt < 2",
                        "action": "loop", "target": "dispatcher",
                        "max_iterations": 5,
                    }, priority=5),
                ],
            ),
        })

        job = async_engine.create_job(objective="test loop invalidation", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)

        # merge must have run exactly 2 times (once per iteration)
        merge_completed = [r for r in runs
                           if r.step_name == "merge" and r.status == StepRunStatus.COMPLETED]
        assert len(merge_completed) == 2

        # branch_a: 1 COMPLETED (iter 1) + at least 1 SKIPPED (iter 2)
        branch_a_completed = [r for r in runs
                              if r.step_name == "branch_a" and r.status == StepRunStatus.COMPLETED]
        branch_a_skipped = [r for r in runs
                            if r.step_name == "branch_a" and r.status == StepRunStatus.SKIPPED]
        assert len(branch_a_completed) == 1
        assert len(branch_a_skipped) >= 1

        # branch_b: at least 1 SKIPPED (iter 1) + 1 COMPLETED (iter 2)
        branch_b_completed = [r for r in runs
                              if r.step_name == "branch_b" and r.status == StepRunStatus.COMPLETED]
        branch_b_skipped = [r for r in runs
                            if r.step_name == "branch_b" and r.status == StepRunStatus.SKIPPED]
        assert len(branch_b_completed) == 1
        assert len(branch_b_skipped) >= 1

        # SKIPPED runs should have dep_run_ids (provenance tracking)
        for skipped in branch_a_skipped + branch_b_skipped:
            assert skipped.dep_run_ids is not None, (
                f"SKIPPED run for {skipped.step_name} missing dep_run_ids"
            )

    def test_mixed_after_and_after_resolved(self, async_engine):
        """A step can have both after: and after_resolved: deps."""
        register_step_fn("start_fn", lambda inputs: {"flag": "no"})
        register_step_fn("required_fn", lambda inputs: {"x": 1})
        register_step_fn("optional_fn", lambda inputs: {"y": 2})
        register_step_fn("final_fn", lambda inputs: {"result": "done"})

        wf = WorkflowDefinition(steps={
            "start": StepDefinition(
                name="start",
                executor=_callable_ref("start_fn"),
                outputs=["flag"],
            ),
            "required": StepDefinition(
                name="required",
                executor=_callable_ref("required_fn"),
                outputs=["x"],
                after=["start"],
            ),
            "optional_branch": StepDefinition(
                name="optional_branch",
                executor=_callable_ref("optional_fn"),
                inputs=[InputBinding("flag", "start", "flag")],
                outputs=["y"],
                when="flag == 'yes'",
            ),
            "final": StepDefinition(
                name="final",
                executor=_callable_ref("final_fn"),
                outputs=["result"],
                after=["required"],  # must complete
                after_resolved=["optional_branch"],  # can be skipped
            ),
        })

        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        runs = async_engine.store.runs_for_job(job.id)
        assert any(r.step_name == "final" and r.status == StepRunStatus.COMPLETED for r in runs)
        assert any(r.step_name == "optional_branch" and r.status == StepRunStatus.SKIPPED for r in runs)


# ── Topology tests ──────────────────────────────────────────────────


class TestAfterResolvedTopology:

    def test_after_resolved_in_entry_steps(self):
        """Steps with after_resolved are NOT entry steps."""
        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["x"],
                executor=ExecutorRef("external", {}),
            ),
            "b": StepDefinition(
                name="b", outputs=["y"],
                executor=ExecutorRef("external", {}),
                after_resolved=["a"],
            ),
        })
        assert wf.entry_steps() == ["a"]

    def test_after_resolved_in_terminal_steps(self):
        """Steps that are after_resolved deps are NOT terminal."""
        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["x"],
                executor=ExecutorRef("external", {}),
            ),
            "b": StepDefinition(
                name="b", outputs=["y"],
                executor=ExecutorRef("external", {}),
                after_resolved=["a"],
            ),
        })
        assert wf.terminal_steps() == ["b"]

    def test_validation_unknown_after_resolved_step(self):
        """after_resolved referencing unknown step should be caught by validation."""
        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["x"],
                executor=ExecutorRef("external", {}),
                after_resolved=["nonexistent"],
            ),
        })
        errors = wf.validate()
        assert any("after_resolved references unknown step" in e for e in errors)
