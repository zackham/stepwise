"""Tests for StepDefinition after_any_of round-trip + WorkflowDefinition.validate."""

from __future__ import annotations

from stepwise.models import (
    ExecutorRef,
    InputBinding,
    StepDefinition,
    WorkflowDefinition,
)


def _step(name: str, **kwargs) -> StepDefinition:
    return StepDefinition(
        name=name,
        outputs=kwargs.pop("outputs", [name + "_out"]),
        executor=kwargs.pop("executor", ExecutorRef("script", {})),
        **kwargs,
    )


# ─── R7: round-trip ───────────────────────────────────────────────────────


def test_round_trip_after_any_of():
    """A step with after_any_of round-trips through to_dict / from_dict."""
    step = StepDefinition(
        name="rejoin",
        outputs=["result"],
        executor=ExecutorRef("script", {}),
        after=["X"],
        after_any_of=[["A", "B"], ["C", "D"]],
    )
    d = step.to_dict()
    # The serialized form is the inline list mixing strings + dicts.
    assert d["after"] == ["X", {"any_of": ["A", "B"]}, {"any_of": ["C", "D"]}]
    # Top-level after_any_of key should NOT be present (inlined).
    assert "after_any_of" not in d
    step2 = StepDefinition.from_dict(d)
    assert step2.after == ["X"]
    assert step2.after_any_of == [["A", "B"], ["C", "D"]]


def test_round_trip_only_after_any_of():
    step = StepDefinition(
        name="c",
        outputs=["r"],
        executor=ExecutorRef("script", {}),
        after_any_of=[["A", "B"]],
    )
    d = step.to_dict()
    assert d["after"] == [{"any_of": ["A", "B"]}]
    step2 = StepDefinition.from_dict(d)
    assert step2.after == []
    assert step2.after_any_of == [["A", "B"]]


def test_round_trip_only_regular_after():
    step = StepDefinition(
        name="c",
        outputs=["r"],
        executor=ExecutorRef("script", {}),
        after=["A", "B"],
    )
    d = step.to_dict()
    assert d["after"] == ["A", "B"]
    step2 = StepDefinition.from_dict(d)
    assert step2.after == ["A", "B"]
    assert step2.after_any_of == []


def test_round_trip_legacy_after_any_of_key():
    """Backwards compat: legacy serialized format with top-level after_any_of."""
    legacy_d = {
        "name": "c",
        "outputs": ["r"],
        "executor": {"type": "script", "config": {}},
        "after": [],
        "exit_rules": [],
        "idempotency": "idempotent",
        "after_any_of": [["A", "B"]],
    }
    step = StepDefinition.from_dict(legacy_d)
    assert step.after == []
    assert step.after_any_of == [["A", "B"]]


# ─── R4: WorkflowDefinition.validate after_any_of member existence ────────


def test_workflow_validate_after_any_of_unknown_member():
    """validate() reports unknown members of after_any_of groups."""
    flow = WorkflowDefinition(steps={
        "a": _step("a"),
        "c": _step("c", after_any_of=[["a", "ghost"]]),
    })
    errors = flow.validate()
    matching = [
        e for e in errors
        if "after.any_of references unknown step 'ghost'" in e
    ]
    assert len(matching) == 1


def test_workflow_validate_after_any_of_known_members_ok():
    """validate() passes when after_any_of members all exist."""
    flow = WorkflowDefinition(steps={
        "a": _step("a"),
        "b": _step("b"),
        "c": _step("c", after_any_of=[["a", "b"]]),
    })
    errors = flow.validate()
    after_errors = [e for e in errors if "after.any_of" in e]
    assert after_errors == []


def test_workflow_validate_after_any_of_multiple_groups_with_unknown():
    """Multiple groups; unknown members in either group are flagged."""
    flow = WorkflowDefinition(steps={
        "a": _step("a"),
        "b": _step("b"),
        "c": _step("c"),
        "d": _step("d", after_any_of=[["a", "b"], ["c", "missing"]]),
    })
    errors = flow.validate()
    matching = [
        e for e in errors
        if "after.any_of references unknown step 'missing'" in e
    ]
    assert len(matching) == 1
