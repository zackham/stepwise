"""Tests for R7: retries and cache prohibited on session writers and fork sources."""

from __future__ import annotations

from stepwise.models import (
    CacheConfig,
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


def test_retry_on_session_writer_rejected():
    flow = _flow(
        _step("writer", session="s1", max_continuous_attempts=3),
    )
    r = validate(flow)
    assert r.accepted is False
    errors = [e for e in r.errors if e.rule_id == "retry_on_session_writer"]
    assert len(errors) == 1
    assert errors[0].session == "s1"
    assert errors[0].step_names == ("writer",)


def test_cache_on_session_writer_rejected():
    flow = _flow(
        _step("writer", session="s1", cache=CacheConfig(enabled=True)),
    )
    r = validate(flow)
    assert r.accepted is False
    errors = [e for e in r.errors if e.rule_id == "cache_on_session_writer"]
    assert len(errors) == 1


def test_retry_on_fork_source_rejected():
    """The target of a fork_from is a fork-source — also banned from retries."""
    flow = _flow(
        _step("anchor", session="parent", max_continuous_attempts=2),
        _step("child", session="forked", fork_from="anchor", after=["anchor"]),
    )
    r = validate(flow)
    assert r.accepted is False
    errors = [e for e in r.errors if e.rule_id == "retry_on_session_writer"
              or e.rule_id == "retry_on_fork_source"]
    # 'anchor' is BOTH a session writer AND a fork source. The validator
    # emits the session-writer rule first (it checks session_writer first).
    assert any("anchor" in e.step_names for e in errors)


def test_cache_on_fork_source_rejected():
    """A non-session step that is the target of a fork_from is a pure fork source."""
    # In production semantics, fork_from targets must have a session (R4c).
    # But for the cache rule, the target could in principle be tested with
    # session=None to isolate the fork_source path. However R4c will reject
    # the fork-from on the no-session target. So instead we test the
    # session-writer + cache combination, which is the primary R7 case.
    flow = _flow(
        _step("anchor", session="parent", cache=CacheConfig(enabled=True)),
        _step("child", session="forked", fork_from="anchor", after=["anchor"]),
    )
    r = validate(flow)
    assert r.accepted is False
    cache_errors = [e for e in r.errors if e.rule_id == "cache_on_session_writer"
                    or e.rule_id == "cache_on_fork_source"]
    assert any("anchor" in e.step_names for e in cache_errors)


def test_session_writer_no_retry_no_cache_accepted():
    """Positive case: a session writer with no retry/cache → accept."""
    flow = _flow(
        _step("writer", session="s1"),
    )
    r = validate(flow)
    retry_cache_errors = [
        e for e in r.errors
        if e.rule_id in (
            "retry_on_session_writer",
            "cache_on_session_writer",
            "retry_on_fork_source",
            "cache_on_fork_source",
        )
    ]
    assert retry_cache_errors == []
