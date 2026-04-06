"""Tests for loop-back to external steps.

When a step loops back to an external step, and that external step is
fulfilled (advances), the originating step should re-run. This tests
the fix for a bug where the engine's cycle guard blocked re-execution
of non-external steps participating in cycles that include an external
step (which naturally gates infinite relaunch via suspension).
"""

import pytest

from tests.conftest import register_step_fn
from stepwise.engine import Engine
from stepwise.executors import (
    ExecutorRegistry,
    ExternalExecutor,
    ScriptExecutor,
)
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
from tests.conftest import CallableExecutor


def make_engine():
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    reg.register("callable", lambda config: CallableExecutor(fn_name=config.get("fn_name", "default")))
    reg.register("external", lambda config: ExternalExecutor(prompt=config.get("prompt", "")))
    return Engine(store=store, registry=reg)


class TestLoopBackToExternalStep:
    """Reproduce the bug: revise_decide loops back to human_review (external),
    human_review is fulfilled, but revise_decide never re-runs."""

    def test_loop_to_external_reruns_originator(self):
        """
        Flow:
          decide → human_review (external, suspends)
          human_review → revise_decide (loops back to human_review on escalation)
          revise_decide → act (when no escalation)

        Scenario:
          1. decide produces escalate_reason
          2. human_review suspends, is fulfilled with guidance
          3. revise_decide runs, escalates again → loops to human_review
          4. human_review suspends again, is fulfilled
          5. revise_decide runs again (2nd iteration), does NOT escalate
          6. act runs (escalate_reason is None from 2nd revise_decide)
        """
        revise_count = {"n": 0}

        def decide_fn(inputs):
            return {"escalate_reason": "needs review", "result": None}

        def revise_decide_fn(inputs):
            revise_count["n"] += 1
            if revise_count["n"] == 1:
                # First pass: still needs escalation
                return {"escalate_reason": "still uncertain", "result": None}
            else:
                # Second pass: resolved, no escalation
                return {"escalate_reason": None, "result": "final_decision"}

        def act_fn(inputs):
            return {"done": True}

        register_step_fn("decide_fn", decide_fn)
        register_step_fn("revise_decide_fn", revise_decide_fn)
        register_step_fn("act_fn", act_fn)

        engine = make_engine()

        wf = WorkflowDefinition(steps={
            "decide": StepDefinition(
                name="decide",
                outputs=["escalate_reason", "result"],
                executor=ExecutorRef("callable", {"fn_name": "decide_fn"}),
            ),
            "human_review": StepDefinition(
                name="human_review",
                executor=ExecutorRef("external", {"prompt": "Review needed"}),
                when="escalate_reason",
                inputs=[
                    InputBinding(
                        "escalate_reason", "", "",
                        any_of_sources=[
                            ("revise_decide", "escalate_reason"),
                            ("decide", "escalate_reason"),
                        ],
                    ),
                ],
                outputs=["guidance"],
            ),
            "revise_decide": StepDefinition(
                name="revise_decide",
                outputs=["escalate_reason", "result"],
                executor=ExecutorRef("callable", {"fn_name": "revise_decide_fn"}),
                inputs=[InputBinding("guidance", "human_review", "guidance")],
                exit_rules=[
                    ExitRule(
                        name="escalate_again",
                        type="expression",
                        config={
                            "condition": "outputs.get('escalate_reason')",
                            "action": "loop",
                            "target": "human_review",
                            "max_iterations": 3,
                        },
                        priority=5,
                    ),
                ],
            ),
            "act": StepDefinition(
                name="act",
                outputs=["done"],
                executor=ExecutorRef("callable", {"fn_name": "act_fn"}),
                inputs=[
                    InputBinding(
                        "escalate_reason", "", "",
                        any_of_sources=[
                            ("revise_decide", "escalate_reason"),
                            ("decide", "escalate_reason"),
                        ],
                    ),
                ],
                when="not escalate_reason",
            ),
        })

        job = engine.create_job("Loop-external test", wf)
        engine.start_job(job.id)

        # After start: decide runs (callable, sync), produces escalate_reason.
        # human_review should be launched and suspended.
        engine.tick()

        # Verify human_review is suspended
        hr_runs = engine.get_runs(job.id, "human_review")
        assert len(hr_runs) == 1
        assert hr_runs[0].status == StepRunStatus.SUSPENDED

        # Fulfill human_review (1st time)
        engine.fulfill_watch(hr_runs[0].id, {"guidance": "try approach B"})
        engine.tick()

        # revise_decide should have run (1st iteration) and escalated
        rd_runs = engine.get_runs(job.id, "revise_decide")
        assert len(rd_runs) == 1
        assert rd_runs[0].status == StepRunStatus.COMPLETED
        assert revise_count["n"] == 1

        # human_review should be suspended again (loop target)
        hr_runs = engine.get_runs(job.id, "human_review")
        assert len(hr_runs) == 2
        assert hr_runs[-1].status == StepRunStatus.SUSPENDED

        # Fulfill human_review (2nd time)
        engine.fulfill_watch(hr_runs[-1].id, {"guidance": "approach C"})
        engine.tick()

        # THE BUG: revise_decide should run again (2nd iteration)
        rd_runs = engine.get_runs(job.id, "revise_decide")
        assert len(rd_runs) == 2, (
            f"revise_decide should have run twice but ran {len(rd_runs)} time(s). "
            f"Statuses: {[r.status.value for r in rd_runs]}"
        )
        assert rd_runs[-1].status == StepRunStatus.COMPLETED
        assert revise_count["n"] == 2

        # After 2nd revise_decide: no escalation, act should run
        # Run ticks until job completes
        for _ in range(10):
            job = engine.get_job(job.id)
            if job.status != JobStatus.RUNNING:
                break
            engine.tick()

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED, (
            f"Job should have completed but status is {job.status.value}"
        )

        # act should have run
        act_runs = engine.get_runs(job.id, "act")
        assert len(act_runs) == 1
        assert act_runs[0].status == StepRunStatus.COMPLETED

    def test_pure_callable_cycle_still_blocked(self):
        """Cycles with no external step should still be blocked by the guard.

        score → refine → score (both callable) would infinite-loop without
        the cycle guard. Verify that the fix doesn't break this protection.

        Uses the same pattern as existing TestLoopBasic but verifies that
        after the loop's explicit _launch of score, refine does NOT
        auto-relaunch from the cycle (it is blocked by the cycle guard).
        The loop only progresses via the explicit _launch in exit resolution.
        """
        score_count = {"n": 0}

        def score_fn(inputs):
            score_count["n"] += 1
            return {"quality": "bad" if score_count["n"] < 5 else "good"}

        def refine_fn(inputs):
            return {"refined": True}

        register_step_fn("score_fn", score_fn)
        register_step_fn("refine_fn", refine_fn)

        engine = make_engine()

        # score → refine (refine loops back to score).
        # refine depends on score (regular dep), score has optional dep on refine
        # via after. The cycle: score → refine → (loop to score).
        wf = WorkflowDefinition(steps={
            "score": StepDefinition(
                name="score",
                outputs=["quality"],
                executor=ExecutorRef("callable", {"fn_name": "score_fn"}),
            ),
            "refine": StepDefinition(
                name="refine",
                outputs=["refined"],
                executor=ExecutorRef("callable", {"fn_name": "refine_fn"}),
                inputs=[InputBinding("quality", "score", "quality")],
                exit_rules=[
                    ExitRule(
                        name="retry",
                        type="always",
                        config={
                            "action": "loop",
                            "target": "score",
                            "max_iterations": 10,
                        },
                        priority=5,
                    ),
                ],
            ),
        })

        job = engine.create_job("Pure callable cycle", wf)
        engine.start_job(job.id)

        # Run a bounded number of ticks
        for _ in range(30):
            job = engine.get_job(job.id)
            if job.status != JobStatus.RUNNING:
                break
            engine.tick()

        # The loop from refine explicitly launches score (that works).
        # But after score runs the 2nd time, refine has a stale dep_run_id
        # for score. The cycle guard blocks refine from re-running because
        # there's no external gate. So refine runs once, score runs twice
        # (initial + one loop), then the job settles.
        assert score_count["n"] <= 3, (
            f"Pure callable cycle should be bounded but score ran {score_count['n']} times"
        )
