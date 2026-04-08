"""Tests for R3: conditional fork rejoin / multi-root mutex rule."""

from __future__ import annotations

from stepwise.models import (
    ExecutorRef,
    InputBinding,
    StepDefinition,
    WhenPredicate,
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


def test_two_mutex_roots_accepted():
    """Two chain roots that are mutex via predicate-form when: → accept."""
    flow = _flow(
        _step("R", outputs=["route"]),
        _step("anchor", session="parent"),
        _step(
            "branch_a",
            session="forked",
            fork_from="anchor",
            after=["anchor"],
            inputs=[InputBinding(local_name="route", source_step="R", source_field="route")],
            when=WhenPredicate(input="route", op="eq", value="a"),
        ),
        _step(
            "branch_b",
            session="forked",
            fork_from="anchor",
            after=["anchor"],
            inputs=[InputBinding(local_name="route", source_step="R", source_field="route")],
            when=WhenPredicate(input="route", op="eq", value="b"),
        ),
    )
    r = validate(flow)
    multi_root_errors = [e for e in r.errors if e.rule_id == "multi_root_not_mutex"]
    assert multi_root_errors == [], f"unexpected errors: {r.errors}"


def test_two_non_mutex_roots_rejected():
    """Two chain roots without mutex when: → reject."""
    flow = _flow(
        _step("anchor", session="parent"),
        _step("branch_a", session="forked", fork_from="anchor", after=["anchor"]),
        _step("branch_b", session="forked", fork_from="anchor", after=["anchor"]),
    )
    r = validate(flow)
    assert r.accepted is False
    multi_root_errors = [e for e in r.errors if e.rule_id == "multi_root_not_mutex"]
    assert len(multi_root_errors) == 1
    err = multi_root_errors[0]
    assert err.session == "forked"
    assert set(err.step_names) == {"branch_a", "branch_b"}
    assert err.fix_suggestion is not None
    assert "mutex" in err.fix_suggestion.lower() or "merge" in err.fix_suggestion.lower()


def test_one_root_accepted_trivial():
    """A session with a single chain root → trivially accepted."""
    flow = _flow(
        _step("anchor", session="parent"),
        _step("branch", session="forked", fork_from="anchor", after=["anchor"]),
    )
    r = validate(flow)
    multi_root_errors = [e for e in r.errors if e.rule_id == "multi_root_not_mutex"]
    assert multi_root_errors == []
