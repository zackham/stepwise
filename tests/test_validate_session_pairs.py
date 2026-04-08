"""Tests for R2 + R10: per-session pair_safe check + pair_verdicts always populated."""

from __future__ import annotations

from stepwise.models import (
    ExecutorRef,
    InputBinding,
    StepDefinition,
    WhenPredicate,
    WorkflowDefinition,
)
from stepwise.validator import validate
from stepwise.validator.errors import PairVerdict


def _step(name: str, **kwargs) -> StepDefinition:
    return StepDefinition(
        name=name,
        outputs=kwargs.pop("outputs", [name + "_out"]),
        executor=kwargs.pop("executor", ExecutorRef("agent", {})),
        **kwargs,
    )


def _flow(*steps: StepDefinition) -> WorkflowDefinition:
    return WorkflowDefinition(steps={s.name: s for s in steps})


def test_meeting_ingest_unordered_writers_rejected():
    """Four session writers with NO ordering — 6 unsafe pair errors."""
    flow = _flow(
        _step("extract_commits", session="meeting"),
        _step("extract_calendar", session="meeting"),
        _step("extract_email", session="meeting"),
        _step("extract_slack", session="meeting"),
    )
    r = validate(flow)
    assert r.accepted is False
    pair_unsafe_errors = [e for e in r.errors if e.rule_id == "pair_unsafe"]
    assert len(pair_unsafe_errors) == 6  # C(4, 2) = 6
    for err in pair_unsafe_errors:
        assert err.session == "meeting"
        assert len(err.step_names) == 2
        assert err.fix_suggestion is not None


def test_meeting_ingest_linear_chain_accepted():
    """Same four writers but chained linearly via after — accept."""
    flow = _flow(
        _step("extract_commits", session="meeting"),
        _step("extract_calendar", session="meeting", after=["extract_commits"]),
        _step("extract_email", session="meeting", after=["extract_calendar"]),
        _step("extract_slack", session="meeting", after=["extract_email"]),
    )
    r = validate(flow)
    assert r.accepted is True
    assert all(pv.safe for pv in r.pair_verdicts)


def test_diamond_with_shared_session_accepted():
    """A → {B, C} → D, where A and D share a session via linear ordering."""
    flow = _flow(
        _step("A", session="s1"),
        _step("B", after=["A"]),
        _step("C", after=["A"]),
        _step("D", session="s1", after=["B", "C"]),
    )
    r = validate(flow)
    assert r.accepted is True


def test_three_way_mutex_accepted():
    """Three pairwise-mutex branches into a shared session."""
    flow = _flow(
        _step("R", outputs=["route"]),
        _step(
            "branch_a",
            session="s1",
            inputs=[InputBinding(local_name="route", source_step="R", source_field="route")],
            when=WhenPredicate(input="route", op="eq", value="a"),
        ),
        _step(
            "branch_b",
            session="s1",
            inputs=[InputBinding(local_name="route", source_step="R", source_field="route")],
            when=WhenPredicate(input="route", op="eq", value="b"),
        ),
        _step(
            "branch_c",
            session="s1",
            inputs=[InputBinding(local_name="route", source_step="R", source_field="route")],
            when=WhenPredicate(input="route", op="eq", value="c"),
        ),
    )
    r = validate(flow)
    assert r.accepted is True


def test_accepted_flow_has_pair_verdicts():
    """R10: even on accepted flows, pair_verdicts is populated for inspection."""
    flow = _flow(
        _step("A", session="s1"),
        _step("B", session="s1", after=["A"]),
    )
    r = validate(flow)
    assert r.accepted is True
    assert len(r.pair_verdicts) == 1
    assert r.pair_verdicts[0].safe is True
    assert isinstance(r.pair_verdicts[0], PairVerdict)


def test_single_writer_no_pair_check_needed():
    """A session with one writer needs no pair check."""
    flow = _flow(_step("solo", session="s1"))
    r = validate(flow)
    assert r.accepted is True
    assert r.pair_verdicts == []
