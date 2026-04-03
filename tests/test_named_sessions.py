"""Tests for named sessions and fork_from support (Phase 2)."""

import pytest

from stepwise.models import (
    ExecutorRef,
    InputBinding,
    StepDefinition,
)
from stepwise.yaml_loader import (
    YAMLLoadError,
    _validate_sessions,
    load_workflow_string,
)


# ── Model Round-Trip ────────────────────────────────────────────────


class TestModelRoundTrip:
    def test_session_and_fork_from_round_trip(self):
        """StepDefinition with session/fork_from survives to_dict/from_dict."""
        step = StepDefinition(
            name="review",
            outputs=["issues"],
            executor=ExecutorRef("agent", {"prompt": "Review", "agent": "claude"}),
            session="critic",
            fork_from="planning",
        )
        d = step.to_dict()
        assert d["session"] == "critic"
        assert d["fork_from"] == "planning"

        restored = StepDefinition.from_dict(d)
        assert restored.session == "critic"
        assert restored.fork_from == "planning"

    def test_session_only_round_trip(self):
        """Session without fork_from serializes correctly."""
        step = StepDefinition(
            name="plan",
            outputs=["plan"],
            executor=ExecutorRef("agent", {"prompt": "Plan"}),
            session="planning",
        )
        d = step.to_dict()
        assert d["session"] == "planning"
        assert "fork_from" not in d

        restored = StepDefinition.from_dict(d)
        assert restored.session == "planning"
        assert restored.fork_from is None

    def test_no_session_fields_omitted(self):
        """Steps without session/fork_from omit them from dict."""
        step = StepDefinition(
            name="basic",
            outputs=["out"],
            executor=ExecutorRef("script", {"command": "echo hi"}),
        )
        d = step.to_dict()
        assert "session" not in d
        assert "fork_from" not in d


# ── YAML Parsing ───────────────────────────────────��────────────────


class TestYAMLParsing:
    def test_session_and_fork_from_parsed(self):
        """YAML with session and fork_from populates step fields."""
        wf = load_workflow_string("""
steps:
  plan:
    executor: agent
    agent: claude
    session: planning
    prompt: "Plan."
    outputs: [plan]

  implement:
    executor: agent
    agent: claude
    session: planning
    prompt: "Implement."
    outputs: [code]
    after: [plan]

  review:
    executor: agent
    agent: claude
    session: critic
    fork_from: planning
    prompt: "Review."
    after: [implement]
    outputs: [issues]
""")
        assert wf.steps["plan"].session == "planning"
        assert wf.steps["plan"].fork_from is None
        assert wf.steps["implement"].session == "planning"
        assert wf.steps["review"].session == "critic"
        assert wf.steps["review"].fork_from == "planning"

    def test_no_session_fields_default_none(self):
        """Steps without session/fork_from default to None."""
        wf = load_workflow_string("""
steps:
  basic:
    run: echo hi
    outputs: [msg]
""")
        assert wf.steps["basic"].session is None
        assert wf.steps["basic"].fork_from is None


# ── Validation Rules ────────────────────────────────────────────────


class TestValidationRule1:
    """fork_from without session -> error."""

    def test_fork_from_without_session(self):
        with pytest.raises(YAMLLoadError, match="fork_from requires a session name"):
            load_workflow_string("""
steps:
  plan:
    executor: agent
    agent: claude
    session: planning
    prompt: "Plan."
    outputs: [plan]

  review:
    executor: agent
    agent: claude
    fork_from: planning
    prompt: "Review."
    after: [plan]
    outputs: [issues]
""")


class TestValidationRule2:
    """fork_from references unknown session -> error."""

    def test_fork_from_unknown_session(self):
        with pytest.raises(YAMLLoadError, match="references unknown session"):
            load_workflow_string("""
steps:
  plan:
    executor: agent
    agent: claude
    session: planning
    prompt: "Plan."
    outputs: [plan]

  review:
    executor: agent
    agent: claude
    session: critic
    fork_from: nonexistent
    prompt: "Review."
    after: [plan]
    outputs: [issues]
""")


class TestValidationRule3:
    """Fork without agent: claude -> error."""

    def test_forked_step_not_agent_claude(self):
        """Forked step using a script executor fails validation."""
        with pytest.raises(YAMLLoadError, match="session forking requires.*agent: claude"):
            load_workflow_string("""
steps:
  plan:
    executor: agent
    agent: claude
    session: planning
    prompt: "Plan."
    outputs: [plan]

  review:
    run: echo review
    session: critic
    fork_from: planning
    after: [plan]
    outputs: [issues]
""")

    def test_parent_session_step_not_agent_claude(self):
        """Parent session step using a script executor fails validation."""
        with pytest.raises(YAMLLoadError, match="session forking requires.*agent: claude"):
            load_workflow_string("""
steps:
  plan:
    run: echo plan
    session: planning
    outputs: [plan]

  review:
    executor: agent
    agent: claude
    session: critic
    fork_from: planning
    prompt: "Review."
    after: [plan]
    outputs: [issues]
""")


class TestValidationRule4:
    """Conflicting fork_from on same session -> error."""

    def test_conflicting_fork_from(self):
        with pytest.raises(YAMLLoadError, match="conflicting fork_from values"):
            load_workflow_string("""
steps:
  plan:
    executor: agent
    agent: claude
    session: planning
    prompt: "Plan."
    outputs: [plan]

  code:
    executor: agent
    agent: claude
    session: coding
    prompt: "Code."
    outputs: [code]

  review_a:
    executor: agent
    agent: claude
    session: critic
    fork_from: planning
    prompt: "Review A."
    after: [plan]
    outputs: [issues_a]

  review_b:
    executor: agent
    agent: claude
    session: critic
    fork_from: coding
    prompt: "Review B."
    after: [code]
    outputs: [issues_b]
""")


class TestValidationRule5:
    """Forked session with no dependency on parent -> error."""

    def test_forked_session_no_dep_on_parent(self):
        with pytest.raises(
            YAMLLoadError, match="has no dependency on parent session"
        ):
            load_workflow_string("""
steps:
  plan:
    executor: agent
    agent: claude
    session: planning
    prompt: "Plan."
    outputs: [plan]

  review:
    executor: agent
    agent: claude
    session: critic
    fork_from: planning
    prompt: "Review."
    outputs: [issues]
""")


class TestValidationRule6:
    """for_each + session -> error."""

    def test_for_each_with_session(self):
        # for_each steps get a special executor type, so we test via
        # direct validation rather than full YAML parse (for_each parsing
        # returns early before session is set on the StepDefinition).
        from stepwise.models import ForEachSpec

        steps = {
            "gen": StepDefinition(
                name="gen",
                outputs=["items"],
                executor=ExecutorRef("agent", {"prompt": "Generate list"}),
            ),
            "process": StepDefinition(
                name="process",
                outputs=["results"],
                executor=ExecutorRef("for_each", {}),
                session="batch",
                for_each=ForEachSpec(
                    source_step="gen", source_field="items"
                ),
                after=["gen"],
            ),
        }
        errors: list[str] = []
        _validate_sessions(steps, errors)
        assert any("session is not compatible with for_each" in e for e in errors)


class TestValidationRule7:
    """Old syntax detection: continue_session + session -> error."""

    def test_continue_session_with_session(self):
        steps = {
            "step1": StepDefinition(
                name="step1",
                outputs=["out"],
                executor=ExecutorRef("agent", {"prompt": "Do it"}),
                continue_session=True,
                session="mysession",
            ),
        }
        errors: list[str] = []
        _validate_sessions(steps, errors)
        assert any("continue_session is deprecated" in e for e in errors)

    def test_session_id_input_with_session(self):
        steps = {
            "step1": StepDefinition(
                name="step1",
                outputs=["out"],
                executor=ExecutorRef("agent", {"prompt": "Do it"}),
                session="mysession",
                inputs=[
                    InputBinding("_session_id", "other", "session_id"),
                ],
            ),
        }
        errors: list[str] = []
        _validate_sessions(steps, errors)
        assert any("_session_id input is deprecated" in e for e in errors)


# ── Valid Flows ───────────────────────���──────────────────────���──────


class TestValidFlows:
    def test_sessions_with_fork_valid(self):
        """Full valid flow: sessions + fork_from with correct deps."""
        wf = load_workflow_string("""
steps:
  plan:
    executor: agent
    agent: claude
    session: planning
    prompt: "Plan."
    outputs: [plan]

  implement:
    executor: agent
    agent: claude
    session: planning
    prompt: "Implement."
    outputs: [code]
    after: [plan]

  review:
    executor: agent
    agent: claude
    session: critic
    fork_from: planning
    prompt: "Review."
    after: [implement]
    outputs: [issues]

  fix:
    executor: agent
    agent: claude
    session: planning
    prompt: "Fix: $issues"
    inputs:
      issues: review.issues
    outputs: [fixed_code]
""")
        assert len(wf.steps) == 4
        assert wf.steps["plan"].session == "planning"
        assert wf.steps["review"].fork_from == "planning"
        assert wf.steps["fix"].session == "planning"
        assert wf.steps["fix"].fork_from is None

    def test_sessions_without_fork_valid(self):
        """Simple session sharing (no fork) parses without errors."""
        wf = load_workflow_string("""
steps:
  plan:
    executor: agent
    agent: claude
    session: planning
    prompt: "Plan."
    outputs: [plan]

  implement:
    executor: agent
    agent: claude
    session: planning
    prompt: "Implement."
    outputs: [code]
    after: [plan]
""")
        assert wf.steps["plan"].session == "planning"
        assert wf.steps["implement"].session == "planning"
        assert wf.steps["plan"].fork_from is None
        assert wf.steps["implement"].fork_from is None

    def test_no_sessions_backward_compat(self):
        """Flows without any session fields still parse fine."""
        wf = load_workflow_string("""
steps:
  a:
    run: echo a
    outputs: [x]
  b:
    run: echo b
    outputs: [y]
    after: [a]
""")
        assert len(wf.steps) == 2
        assert wf.steps["a"].session is None
        assert wf.steps["b"].session is None

    def test_fork_with_input_dep_on_parent(self):
        """Fork step depends on parent via input binding (not just after)."""
        wf = load_workflow_string("""
steps:
  plan:
    executor: agent
    agent: claude
    session: planning
    prompt: "Plan."
    outputs: [plan]

  review:
    executor: agent
    agent: claude
    session: critic
    fork_from: planning
    prompt: "Review: $plan_text"
    inputs:
      plan_text: plan.plan
    outputs: [issues]
""")
        assert wf.steps["review"].fork_from == "planning"
