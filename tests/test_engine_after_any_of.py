"""Engine-level tests for after_any_of: eligibility + first-success-wins + no cancellation."""

from __future__ import annotations

from tests.conftest import CallableExecutor, register_step_fn
from stepwise.engine import Engine
from stepwise.executors import (
    ExecutorRegistry,
    ScriptExecutor,
)
from stepwise.models import (
    ExecutorRef,
    InputBinding,
    JobStatus,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
)
from stepwise.store import SQLiteStore


def make_engine() -> Engine:
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    reg.register("callable", lambda config: CallableExecutor(fn_name=config.get("fn_name", "default")))
    reg.register("script", lambda config: ScriptExecutor(command=config.get("command", "echo '{}'")))
    return Engine(store=store, registry=reg)


def _step(name: str, fn_name: str, **kwargs) -> StepDefinition:
    return StepDefinition(
        name=name,
        outputs=kwargs.pop("outputs", ["v"]),
        executor=ExecutorRef("callable", {"fn_name": fn_name}),
        **kwargs,
    )


# ─── R5: eligibility tests ────────────────────────────────────────────────


def test_step_eligible_when_first_any_of_member_completes():
    """A and B in any_of for C; A completes, C runs (B's status doesn't matter)."""
    register_step_fn("a_fn", lambda inputs: {"v": 1})
    register_step_fn("b_fn", lambda inputs: {"v": 2})
    register_step_fn("c_fn", lambda inputs: {"v": "C ran"})

    engine = make_engine()
    w = WorkflowDefinition(steps={
        "a": _step("a", "a_fn"),
        "b": _step("b", "b_fn"),
        "c": _step("c", "c_fn", after_any_of=[["a", "b"]]),
    })
    job = engine.create_job("first-success", w)
    engine.start_job(job.id)
    job = engine.get_job(job.id)
    assert job.status == JobStatus.COMPLETED
    runs = engine.get_runs(job.id)
    c_runs = [r for r in runs if r.step_name == "c" and r.status == StepRunStatus.COMPLETED]
    assert len(c_runs) == 1
    assert c_runs[0].result.artifact["v"] == "C ran"


def test_step_not_eligible_when_no_any_of_member_settled():
    """A and B in any_of for C; both NOT yet run → C is NOT ready (unit-level check).

    Construct a job, do not run it, and call _is_step_ready directly with no
    runs in the store for A or B.
    """
    register_step_fn("a_fn", lambda inputs: {"v": 1})
    register_step_fn("b_fn", lambda inputs: {"v": 2})
    register_step_fn("c_fn", lambda inputs: {"v": "C ran"})

    engine = make_engine()
    w = WorkflowDefinition(steps={
        "a": _step("a", "a_fn"),
        "b": _step("b", "b_fn"),
        "c": _step("c", "c_fn", after_any_of=[["a", "b"]]),
    })
    job = engine.create_job("not-ready", w)
    # Before starting any runs, C is not ready.
    assert engine._is_step_ready(job, "c", w.steps["c"]) is False


def test_step_eligible_with_mixed_required_and_any_of():
    """Required dep X completed AND any_of {A, B} has at least one completed → eligible."""
    register_step_fn("x_fn", lambda inputs: {"v": "x"})
    register_step_fn("a_fn", lambda inputs: {"v": "a"})
    register_step_fn("b_fn", lambda inputs: {"v": "b"})
    register_step_fn("c_fn", lambda inputs: {"v": "c done"})

    engine = make_engine()
    w = WorkflowDefinition(steps={
        "x": _step("x", "x_fn"),
        "a": _step("a", "a_fn"),
        "b": _step("b", "b_fn"),
        "c": _step(
            "c",
            "c_fn",
            after=["x"],
            after_any_of=[["a", "b"]],
        ),
    })
    job = engine.create_job("mixed", w)
    engine.start_job(job.id)
    job = engine.get_job(job.id)
    assert job.status == JobStatus.COMPLETED
    runs = engine.get_runs(job.id)
    c_runs = [r for r in runs if r.step_name == "c" and r.status == StepRunStatus.COMPLETED]
    assert len(c_runs) == 1


def test_step_eligible_with_on_error_continue_failure_in_any_of():
    """A in any_of fails but has on_error: continue; B still pending → C is eligible."""
    def a_fail(_inputs):
        raise RuntimeError("boom")

    register_step_fn("a_fn", a_fail)
    register_step_fn("b_fn", lambda inputs: {"v": "b"})
    register_step_fn("c_fn", lambda inputs: {"v": "c done"})

    engine = make_engine()
    w = WorkflowDefinition(steps={
        "a": _step("a", "a_fn", on_error="continue"),
        "b": _step("b", "b_fn"),
        "c": _step("c", "c_fn", after_any_of=[["a", "b"]]),
    })
    job = engine.create_job("on-error-continue", w)
    engine.start_job(job.id)
    job = engine.get_job(job.id)
    assert job.status == JobStatus.COMPLETED
    runs = engine.get_runs(job.id)
    c_runs = [r for r in runs if r.step_name == "c" and r.status == StepRunStatus.COMPLETED]
    assert len(c_runs) == 1


# ─── R6: all members fail/skip → consumer skipped ────────────────────────


def test_consumer_skipped_when_all_any_of_members_fail():
    """A and B both fail with default on_error (stop); C never becomes ready;
    settlement marks C as SKIPPED at job exit.
    """
    def a_fail(_inputs):
        raise RuntimeError("a boom")

    def b_fail(_inputs):
        raise RuntimeError("b boom")

    register_step_fn("a_fn", a_fail)
    register_step_fn("b_fn", b_fail)
    register_step_fn("c_fn", lambda inputs: {"v": "should not run"})

    engine = make_engine()
    w = WorkflowDefinition(steps={
        "a": _step("a", "a_fn"),
        "b": _step("b", "b_fn"),
        "c": _step("c", "c_fn", after_any_of=[["a", "b"]]),
    })
    job = engine.create_job("all-fail", w)
    engine.start_job(job.id)
    job = engine.get_job(job.id)
    # Job fails because both a and b fail; C never runs.
    assert job.status == JobStatus.FAILED
    runs = engine.get_runs(job.id)
    c_runs = [r for r in runs if r.step_name == "c"]
    # C either has no run (never started) or a SKIPPED run (settlement).
    if c_runs:
        assert all(r.status in (StepRunStatus.SKIPPED,) for r in c_runs)
    else:
        assert c_runs == []


# ─── R9: no cancellation ──────────────────────────────────────────────────


def test_losing_any_of_branches_continue_running():
    """A and B in any_of; both succeed; B is NOT marked CANCELLED.

    The legacy Engine is synchronous so both A and B complete before C runs.
    The point of this test is to assert that NEITHER branch gets cancelled —
    losing branches reach normal terminal states.
    """
    register_step_fn("a_fn", lambda inputs: {"v": "a-done"})
    register_step_fn("b_fn", lambda inputs: {"v": "b-done"})
    register_step_fn("c_fn", lambda inputs: {"v": "c-done"})

    engine = make_engine()
    w = WorkflowDefinition(steps={
        "a": _step("a", "a_fn"),
        "b": _step("b", "b_fn"),
        "c": _step("c", "c_fn", after_any_of=[["a", "b"]]),
    })
    job = engine.create_job("no-cancel", w)
    engine.start_job(job.id)
    job = engine.get_job(job.id)
    assert job.status == JobStatus.COMPLETED
    runs = engine.get_runs(job.id)
    a_runs = [r for r in runs if r.step_name == "a"]
    b_runs = [r for r in runs if r.step_name == "b"]
    # Neither branch is cancelled — both reach COMPLETED.
    assert all(r.status == StepRunStatus.COMPLETED for r in a_runs)
    assert all(r.status == StepRunStatus.COMPLETED for r in b_runs)
    # And neither has any kind of "cancelled" marker in metadata.
    for r in a_runs + b_runs:
        assert r.status != StepRunStatus.SUSPENDED  # not suspended either


def test_after_any_of_with_three_members_one_success():
    """A, B, C in any_of for D; only one needs to complete."""
    register_step_fn("a_fn", lambda inputs: {"v": "a"})
    register_step_fn("b_fn", lambda inputs: {"v": "b"})
    register_step_fn("c_fn", lambda inputs: {"v": "c"})
    register_step_fn("d_fn", lambda inputs: {"v": "d ran"})

    engine = make_engine()
    w = WorkflowDefinition(steps={
        "a": _step("a", "a_fn"),
        "b": _step("b", "b_fn"),
        "c": _step("c", "c_fn"),
        "d": _step("d", "d_fn", after_any_of=[["a", "b", "c"]]),
    })
    job = engine.create_job("three-way", w)
    engine.start_job(job.id)
    job = engine.get_job(job.id)
    assert job.status == JobStatus.COMPLETED
    runs = engine.get_runs(job.id)
    d_runs = [r for r in runs if r.step_name == "d" and r.status == StepRunStatus.COMPLETED]
    assert len(d_runs) == 1


def test_multiple_after_any_of_groups_all_must_have_one_settled():
    """Two any_of groups; D needs at least one from each group settled."""
    register_step_fn("a_fn", lambda inputs: {"v": "a"})
    register_step_fn("b_fn", lambda inputs: {"v": "b"})
    register_step_fn("c_fn", lambda inputs: {"v": "c"})
    register_step_fn("d_fn", lambda inputs: {"v": "d"})
    register_step_fn("e_fn", lambda inputs: {"v": "e"})

    engine = make_engine()
    w = WorkflowDefinition(steps={
        "a": _step("a", "a_fn"),
        "b": _step("b", "b_fn"),
        "c": _step("c", "c_fn"),
        "d": _step("d", "d_fn"),
        "e": _step(
            "e",
            "e_fn",
            after_any_of=[["a", "b"], ["c", "d"]],
        ),
    })
    job = engine.create_job("two-groups", w)
    engine.start_job(job.id)
    job = engine.get_job(job.id)
    assert job.status == JobStatus.COMPLETED
    runs = engine.get_runs(job.id)
    e_runs = [r for r in runs if r.step_name == "e" and r.status == StepRunStatus.COMPLETED]
    assert len(e_runs) == 1
