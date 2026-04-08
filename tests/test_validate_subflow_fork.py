"""Tests for R5: subflow / for_each fork restrictions."""

from __future__ import annotations

from stepwise.models import (
    ExecutorRef,
    ForEachSpec,
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


def test_R5b_fork_from_on_for_each_rejected():
    """A step with for_each: cannot also declare fork_from:."""
    flow = _flow(
        _step("anchor", session="parent"),
        _step(
            "looper",
            after=["anchor"],
            session="forked",
            fork_from="anchor",
            for_each=ForEachSpec(source_step="anchor", source_field="anchor_out"),
        ),
    )
    r = validate(flow)
    assert r.accepted is False
    errors = [e for e in r.errors if e.rule_id == "fork_from_on_for_each"]
    assert len(errors) == 1
    assert errors[0].step_names == ("looper",)


def test_R5b_fork_from_on_sub_flow_rejected():
    """A step with sub_flow: cannot also declare fork_from:."""
    inner = WorkflowDefinition(steps={
        "inner_step": _step("inner_step"),
    })
    flow = _flow(
        _step("anchor", session="parent"),
        _step(
            "subflow_step",
            after=["anchor"],
            session="forked",
            fork_from="anchor",
            sub_flow=inner,
        ),
    )
    r = validate(flow)
    assert r.accepted is False
    errors = [e for e in r.errors if e.rule_id == "fork_from_on_for_each"]
    assert len(errors) == 1
    assert errors[0].step_names == ("subflow_step",)


def test_R5a_fork_from_inside_subflow_referencing_parent_step_rejected():
    """A step inside a sub_flow body cannot fork_from a parent-flow step."""
    inner = WorkflowDefinition(steps={
        "inner_step": _step(
            "inner_step",
            session="forked",
            fork_from="parent_anchor",  # references a parent-flow step name
        ),
    })
    flow = _flow(
        _step("parent_anchor", session="parent"),
        _step("wrapper", after=["parent_anchor"], sub_flow=inner),
    )
    r = validate(flow)
    assert r.accepted is False
    errors = [e for e in r.errors if e.rule_id == "fork_from_in_subflow"]
    assert len(errors) == 1
    assert errors[0].step_names == ("inner_step", "parent_anchor")
    assert "sub_flow" in errors[0].message
    assert errors[0].fix_suggestion is not None


def test_R5a_fork_from_inside_subflow_referencing_sibling_accepted():
    """fork_from to a sibling step within the same sub_flow body is OK at this rule."""
    inner = WorkflowDefinition(steps={
        "sibling_anchor": _step("sibling_anchor", session="inner_parent"),
        "inner_step": _step(
            "inner_step",
            after=["sibling_anchor"],
            session="forked",
            fork_from="sibling_anchor",
        ),
    })
    flow = _flow(
        _step("wrapper", sub_flow=inner),
    )
    r = validate(flow)
    # The R5a rule should NOT fire (sibling reference); other rules in the
    # outer flow may or may not pass — we only check the R5a rule.
    errors = [e for e in r.errors if e.rule_id == "fork_from_in_subflow"]
    assert errors == []
