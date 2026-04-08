"""Tests for R4: fork target validation (R4a-R4d)."""

from __future__ import annotations

from stepwise.models import (
    ExecutorRef,
    StepDefinition,
    WorkflowDefinition,
)
from stepwise.validator import validate


def _step(name: str, **kwargs) -> StepDefinition:
    return StepDefinition(
        name=name,
        outputs=kwargs.pop("outputs", [name + "_out"]),
        executor=kwargs.pop("executor", ExecutorRef("agent", {})),
        **kwargs,
    )


def _flow(*steps: StepDefinition) -> WorkflowDefinition:
    return WorkflowDefinition(steps={s.name: s for s in steps})


def test_valid_fork_target_accepted():
    """Valid fork: target exists, is agent, has session, in mhb predecessors."""
    flow = _flow(
        _step("anchor", session="parent"),
        _step(
            "child",
            session="forked",
            fork_from="anchor",
            after=["anchor"],
        ),
    )
    r = validate(flow)
    fork_errors = [e for e in r.errors if e.rule_id.startswith("fork_target_")]
    assert fork_errors == [], f"unexpected fork errors: {fork_errors}"


def test_R4a_target_missing():
    """fork_from references a non-existent step."""
    flow = _flow(
        _step("child", session="forked", fork_from="ghost"),
    )
    r = validate(flow)
    assert r.accepted is False
    errors = [e for e in r.errors if e.rule_id == "fork_target_missing"]
    assert len(errors) == 1
    assert errors[0].step_names == ("child",)
    assert "ghost" in errors[0].message
    assert errors[0].fix_suggestion is not None


def test_R4b_target_not_agent():
    """fork_from target has a non-agent executor."""
    flow = _flow(
        _step("anchor", session="parent", executor=ExecutorRef("script", {})),
        _step("child", session="forked", fork_from="anchor", after=["anchor"]),
    )
    r = validate(flow)
    assert r.accepted is False
    errors = [e for e in r.errors if e.rule_id == "fork_target_not_agent"]
    assert len(errors) == 1
    assert "anchor" in errors[0].message
    assert "script" in errors[0].message


def test_R4c_target_no_session():
    """fork_from target is an agent step but has no session declared."""
    flow = _flow(
        _step("anchor"),  # agent executor by default, no session
        _step("child", session="forked", fork_from="anchor", after=["anchor"]),
    )
    r = validate(flow)
    assert r.accepted is False
    errors = [e for e in r.errors if e.rule_id == "fork_target_no_session"]
    assert len(errors) == 1
    assert errors[0].step_names == ("child", "anchor")


def test_R4d_target_not_in_mhb():
    """fork_from target exists/has session but is not in the forking step's mhb."""
    flow = _flow(
        _step("anchor", session="parent"),
        _step("unrelated"),
        # 'child' has no after / inputs referencing 'anchor' — not in mhb.
        _step("child", session="forked", fork_from="anchor"),
    )
    r = validate(flow)
    assert r.accepted is False
    errors = [e for e in r.errors if e.rule_id == "fork_target_not_in_mhb"]
    assert len(errors) == 1
    assert "after:" in errors[0].fix_suggestion
