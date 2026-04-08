"""Tests for R9 + R13: structured ValidationError fields and §7.5 error wording."""

from __future__ import annotations

from stepwise.models import (
    CacheConfig,
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


def test_pair_unsafe_error_includes_step_pair_and_session_and_fix():
    """R9: pair_unsafe error has structured step_names tuple, session, fix_suggestion."""
    flow = _flow(
        _step("extract_commits", session="meeting"),
        _step("analyze", session="meeting"),
    )
    r = validate(flow)
    err = next(e for e in r.errors if e.rule_id == "pair_unsafe")
    # Structured fields
    assert err.step_names == ("analyze", "extract_commits")  # sorted
    assert err.session == "meeting"
    assert err.fix_suggestion is not None
    # §7.5 wording — actionable, points at concrete fixes
    assert "after:" in err.fix_suggestion
    assert "fork_from:" in err.fix_suggestion
    # Message names both steps and the session
    assert "analyze" in err.message
    assert "extract_commits" in err.message
    assert "meeting" in err.message


def test_section_7_5_pair_unsafe_message_wording():
    """The §7.5 example phrase 'both write to session' appears in pair_unsafe."""
    flow = _flow(
        _step("X", session="S"),
        _step("Y", session="S"),
    )
    r = validate(flow)
    err = next(e for e in r.errors if e.rule_id == "pair_unsafe")
    assert "both write to session" in err.message


def test_section_7_5_multi_root_message_wording():
    """multi_root_not_mutex error mentions 'fork_from' and 'mutually exclusive'."""
    flow = _flow(
        _step("anchor", session="parent"),
        _step("a", session="forked", fork_from="anchor", after=["anchor"]),
        _step("b", session="forked", fork_from="anchor", after=["anchor"]),
    )
    r = validate(flow)
    err = next(e for e in r.errors if e.rule_id == "multi_root_not_mutex")
    assert "fork_from" in err.message
    assert "mutually exclusive" in err.message


def test_section_7_5_fork_target_missing_message_wording():
    """fork_target_missing error names the missing target."""
    flow = _flow(
        _step("child", session="forked", fork_from="ghost"),
    )
    r = validate(flow)
    err = next(e for e in r.errors if e.rule_id == "fork_target_missing")
    assert "ghost" in err.message
    assert "no such step" in err.message


def test_section_7_5_fork_target_no_session_message_wording():
    """fork_target_no_session mentions 'session' and the actionable fix."""
    flow = _flow(
        _step("anchor"),
        _step("child", session="forked", fork_from="anchor", after=["anchor"]),
    )
    r = validate(flow)
    err = next(e for e in r.errors if e.rule_id == "fork_target_no_session")
    assert "session" in err.message
    assert "Add 'session:" in err.fix_suggestion


def test_section_7_5_retry_on_session_writer_message_wording():
    """retry_on_session_writer message references retry policy and v1.0."""
    flow = _flow(
        _step("writer", session="s1", max_continuous_attempts=3),
    )
    r = validate(flow)
    err = next(e for e in r.errors if e.rule_id == "retry_on_session_writer")
    assert "max_continuous_attempts" in err.message
    assert "retry policies" in err.message
    assert "v1.0" in err.message


def test_section_7_5_back_edge_message_wording():
    """back_edge_unsupported uses the locked deferred wording."""
    flow = _flow(
        _step("A", after=["B"]),
        _step("B", after=["A"]),
    )
    r = validate(flow)
    err = next(e for e in r.errors if e.rule_id == "back_edge_unsupported")
    assert "loop-back binding" in err.message
    assert "not yet supported" in err.message
    assert "loop-back binding runtime not yet implemented" in err.message
