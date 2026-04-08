"""Parse-time tests for §9.7 ephemeral fork_from + session inputs.

R1: fork_from without session accepted (Rule 1 relaxed)
R2: for_each + fork_from without session accepted (Rule 6 relaxed)
R3: for_each + session still rejected (Rule 6 preserved)
R4: type: session accepted in flow input declarations
R5: fork_from: $job.context accepted when input has type: session
R6: fork_from: $job.context rejected when input has type: str
R7: _session binding accepted on session-bearing steps
R8: _session binding rejected on non-session steps
"""

from __future__ import annotations

import pytest

from stepwise.models import (
    ConfigVar,
    ExecutorRef,
    ForEachSpec,
    InputBinding,
    StepDefinition,
    WorkflowDefinition,
)
from stepwise.yaml_loader import _validate_sessions


def _step(name: str, **kwargs) -> StepDefinition:
    return StepDefinition(
        name=name,
        outputs=kwargs.pop("outputs", ["result"]),
        executor=ExecutorRef(kwargs.pop("executor_type", "agent"), kwargs.pop("executor_config", {"agent": "claude"})),
        **kwargs,
    )


# ── R1: fork_from without session accepted (Rule 1 relaxed) ─────────


def test_fork_from_without_session_accepted():
    """Ephemeral one-shot fork: fork_from set, session not set."""
    steps = {
        "parent": _step("parent", session="research"),
        "child": _step("child", fork_from="parent", after=["parent"]),
    }
    errors: list[str] = []
    _validate_sessions(steps, errors)
    assert not any("fork_from requires a session name" in e for e in errors), errors


# ── R2: for_each + fork_from without session accepted ────────────────


def test_for_each_fork_from_without_session_accepted():
    """for_each + fork_from (no session) is now legal."""
    sub_flow = WorkflowDefinition(steps={
        "inner": _step("inner", fork_from="$job.ctx"),
    })
    steps = {
        "parent": _step("parent", session="research"),
        "fan": _step(
            "fan",
            for_each=ForEachSpec(source_step="parent", source_field="items"),
            sub_flow=sub_flow,
            inputs=[InputBinding("ctx", "parent", "_session")],
        ),
    }
    errors: list[str] = []
    _validate_sessions(steps, errors)
    # Should NOT have "session is not compatible with for_each"
    assert not any("not compatible with for_each" in e for e in errors), errors


# ── R3: for_each + session still rejected ────────────────────────────


def test_for_each_with_session_still_rejected():
    """for_each + session: name is still banned."""
    steps = {
        "fan": _step(
            "fan",
            session="shared",
            for_each=ForEachSpec(source_step="src", source_field="items"),
            sub_flow=WorkflowDefinition(),
        ),
    }
    errors: list[str] = []
    _validate_sessions(steps, errors)
    assert any("session is not compatible with for_each" in e for e in errors)


# ── R4: type: session accepted in flow input declarations ────────────


def test_type_session_accepted():
    """ConfigVar with type='session' should parse cleanly."""
    cv = ConfigVar.from_dict({"name": "ctx", "type": "session"})
    assert cv.type == "session"


def test_type_session_invalid_type_rejected():
    """ConfigVar with a truly invalid type should still be rejected."""
    with pytest.raises(ValueError, match="invalid type"):
        ConfigVar.from_dict({"name": "ctx", "type": "invalid_type"})


# ── R5: fork_from: $job.context accepted with type: session ──────────


def test_fork_from_job_input_accepted():
    """fork_from: $job.ctx is valid when ctx has type: session."""
    steps = {
        "worker": _step("worker", fork_from="$job.ctx"),
    }
    input_vars = [ConfigVar(name="ctx", type="session")]
    errors: list[str] = []
    _validate_sessions(steps, errors, input_vars=input_vars)
    # Should not have fork_from validation errors
    assert not any("fork_from" in e.lower() for e in errors), errors


# ── R6: fork_from: $job.context rejected with type: str ──────────────


def test_fork_from_job_input_wrong_type_rejected():
    """fork_from: $job.ctx is rejected when ctx has type: str."""
    steps = {
        "worker": _step("worker", fork_from="$job.ctx"),
    }
    input_vars = [ConfigVar(name="ctx", type="str")]
    errors: list[str] = []
    _validate_sessions(steps, errors, input_vars=input_vars)
    assert any("type: session" in e for e in errors), errors


def test_fork_from_job_input_undeclared_rejected():
    """fork_from: $job.ctx is rejected when ctx is not declared."""
    steps = {
        "worker": _step("worker", fork_from="$job.ctx"),
    }
    errors: list[str] = []
    _validate_sessions(steps, errors, input_vars=[])
    assert any("type: session" in e for e in errors), errors


# ── R7: _session binding accepted on session-bearing steps ───────────


def test_session_binding_on_session_step_accepted():
    """Input binding to step._session is valid when step has session."""
    steps = {
        "parent": _step("parent", session="research"),
        "child": _step(
            "child",
            inputs=[InputBinding("ctx", "parent", "_session")],
            after=["parent"],
        ),
    }
    errors: list[str] = []
    _validate_sessions(steps, errors)
    assert not any("_session" in e for e in errors), errors


# ── R8: _session binding rejected on non-session steps ───────────────


def test_session_binding_on_non_session_step_rejected():
    """Input binding to step._session is rejected when step has no session."""
    steps = {
        "parent": _step("parent"),  # no session
        "child": _step(
            "child",
            inputs=[InputBinding("ctx", "parent", "_session")],
            after=["parent"],
        ),
    }
    errors: list[str] = []
    _validate_sessions(steps, errors)
    assert any("_session" in e and "no session" in e for e in errors), errors
