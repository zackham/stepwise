"""Tests for boomerang step detection, terminal output merging,
when-condition parsing for sub-flow/for-each steps, and premature
launch warning suppression.

These pin down engine semantics introduced in session 20 (March 2026).
"""

import pytest
from stepwise.models import (
    ExitRule,
    ExecutorRef,
    InputBinding,
    StepDefinition,
    WorkflowDefinition,
)
from stepwise.yaml_loader import load_workflow_string


# ── Helpers ──────────────────────────────────────────────────────────

def _script(name, outputs=None, inputs=None, after=None, exit_rules=None, when=None):
    return StepDefinition(
        name=name,
        outputs=outputs or [],
        executor=ExecutorRef("script", {}),
        inputs=inputs or [],
        after=after or [],
        exit_rules=exit_rules or [],
        when=when,
    )


def _loop_rule(target, name="loop", when="True", max_iter=5):
    return ExitRule(
        name=name,
        type="always" if when == "True" else "field_match",
        config={"condition": when, "action": "loop", "target": target, "max_iterations": max_iter},
        priority=0,
    )


def _advance_rule(name="advance", when="True"):
    return ExitRule(
        name=name,
        type="always" if when == "True" else "field_match",
        config={"condition": when, "action": "advance"},
        priority=10,
    )


def _escalate_rule(name="escalate", when="attempt >= 4"):
    return ExitRule(
        name=name,
        type="field_match",
        config={"condition": when, "action": "escalate"},
        priority=5,
    )


# ── Boomerang terminal detection ─────────────────────────────────────


class TestBoomerangTerminalDetection:
    """Steps with no advance exits (only loop/escalate) are boomerangs
    and should not appear as terminal steps."""

    def test_pure_loop_step_excluded(self):
        """A step with only loop exits is not terminal."""
        w = WorkflowDefinition(steps={
            "run-tests": _script("run-tests", outputs=["status"]),
            "fix-tests": _script(
                "fix-tests", outputs=["fixes"],
                inputs=[InputBinding("status", "run-tests", "status")],
                exit_rules=[_loop_rule("run-tests")],
            ),
        })
        assert w.terminal_steps() == ["run-tests"]

    def test_loop_plus_escalate_excluded(self):
        """A step with loop + escalate (no advance) is still a boomerang."""
        w = WorkflowDefinition(steps={
            "review": _script("review", outputs=["result"]),
            "revise": _script(
                "revise", outputs=["changes"],
                inputs=[InputBinding("result", "review", "result")],
            ),
            "push-fix": _script(
                "push-fix", outputs=["pushed"],
                inputs=[InputBinding("changes", "revise", "changes")],
                exit_rules=[
                    _loop_rule("review"),
                    _escalate_rule(),
                ],
            ),
            "done": _script(
                "done", outputs=["final"],
                inputs=[InputBinding("result", "review", "result")],
                when="result == 'approved'",
            ),
        })
        terminals = w.terminal_steps()
        assert "push-fix" not in terminals
        assert "revise" not in terminals  # only consumer is boomerang
        assert "review" not in terminals  # depended on by done
        assert "done" in terminals

    def test_advance_plus_loop_is_terminal_candidate(self):
        """A step with both advance and loop exits is NOT a boomerang."""
        w = WorkflowDefinition(steps={
            "check": _script(
                "check", outputs=["status"],
                exit_rules=[
                    _advance_rule("pass", when="outputs.status == 'pass'"),
                    _loop_rule("check", name="retry"),
                ],
            ),
        })
        assert "check" in w.terminal_steps()

    def test_no_exit_rules_is_terminal(self):
        """Steps with no exit rules (implicit advance) are terminal candidates."""
        w = WorkflowDefinition(steps={
            "a": _script("a", outputs=["x"]),
            "b": _script("b", outputs=["y"],
                         inputs=[InputBinding("x", "a", "x")]),
        })
        assert w.terminal_steps() == ["b"]

    def test_step_feeding_only_boomerangs_not_terminal(self):
        """If a step's only consumers are boomerangs, it's depended-on
        and not terminal. Add a real terminal to complete the flow."""
        w = WorkflowDefinition(steps={
            "source": _script("source", outputs=["data"]),
            "process": _script(
                "process", outputs=["result"],
                inputs=[InputBinding("data", "source", "data")],
            ),
            "fix": _script(
                "fix", outputs=["fixed"],
                inputs=[InputBinding("result", "process", "result")],
                exit_rules=[_loop_rule("process")],
            ),
            "deploy": _script(
                "deploy", outputs=["done"],
                inputs=[InputBinding("result", "process", "result")],
                when="result == 'pass'",
            ),
        })
        terminals = w.terminal_steps()
        assert "fix" not in terminals     # boomerang
        assert "source" not in terminals  # depended on by process
        assert "process" not in terminals # depended on by fix and deploy
        assert "deploy" in terminals

    def test_single_step_loop_fallback(self):
        """A single-step workflow with only loop exits still has a terminal
        (fallback prevents zero-terminal validation failure)."""
        w = WorkflowDefinition(steps={
            "poll": _script(
                "poll", outputs=["data"],
                exit_rules=[_loop_rule("poll")],
            ),
        })
        assert w.terminal_steps() == ["poll"]

    def test_single_boomerang_fallback_picks_progression_step(self):
        """In a 2-step loop (run + fix), the progression step (run) is
        terminal even though it's depended on by the boomerang (fix)."""
        w = WorkflowDefinition(steps={
            "run": _script("run", outputs=["status"]),
            "fix": _script(
                "fix", outputs=["fixes"],
                inputs=[InputBinding("status", "run", "status")],
                exit_rules=[_loop_rule("run")],
            ),
        })
        terminals = w.terminal_steps()
        assert "run" in terminals
        assert "fix" not in terminals

    def test_demo_flow_terminals(self):
        """The demo flow should have exactly deploy-prod and close-pr as terminals."""
        import os
        flow_path = os.path.join(os.path.dirname(__file__), "..", "flows", "demo", "FLOW.yaml")
        if not os.path.exists(flow_path):
            pytest.skip("Demo flow not found")
        from stepwise.yaml_loader import load_workflow_yaml
        wf = load_workflow_yaml(flow_path)
        terminals = wf.terminal_steps()
        assert sorted(terminals) == ["close-pr", "deploy-prod"]


# ── When-condition parsing for sub-flow and for-each steps ────────────


class TestWhenParsingSubFlowForEach:
    """When conditions must be parsed for sub-flow and for-each steps,
    not just regular script/agent steps."""

    def test_sub_flow_step_preserves_when(self):
        yaml = (
            "name: test-when-subflow\n"
            "steps:\n"
            "  source:\n"
            "    run: echo ok\n"
            "    outputs: [status]\n"
            "  guarded:\n"
            "    inputs:\n"
            "      status: source.status\n"
            "    when: \"status == 'pass'\"\n"
            "    flow:\n"
            "      steps:\n"
            "        inner:\n"
            "          run: echo ok\n"
            "          outputs: [result]\n"
            "    outputs: [result]\n"
        )
        wf = load_workflow_string(yaml)
        assert wf.steps["guarded"].when == "status == 'pass'"

    def test_for_each_step_preserves_when(self):
        yaml = (
            "name: test-when-foreach\n"
            "steps:\n"
            "  source:\n"
            "    run: echo ok\n"
            "    outputs: [items, go]\n"
            "  guarded:\n"
            "    inputs:\n"
            "      go: source.go\n"
            '    when: "go == true"\n'
            "    for_each: source.items\n"
            "    as: item\n"
            "    flow:\n"
            "      steps:\n"
            "        inner:\n"
            "          run: echo ok\n"
            "          outputs: [result]\n"
            "    outputs: [results]\n"
        )
        wf = load_workflow_string(yaml)
        assert wf.steps["guarded"].when == "go == true"

    def test_regular_step_preserves_when(self):
        yaml = (
            "name: test-when-regular\n"
            "steps:\n"
            "  source:\n"
            "    run: echo ok\n"
            "    outputs: [status]\n"
            "  guarded:\n"
            "    run: echo ok\n"
            "    inputs:\n"
            "      status: source.status\n"
            "    when: \"status == 'pass'\"\n"
            "    outputs: [result]\n"
        )
        wf = load_workflow_string(yaml)
        assert wf.steps["guarded"].when == "status == 'pass'"

    def test_sub_flow_step_without_when(self):
        yaml = (
            "name: test-no-when-subflow\n"
            "steps:\n"
            "  wrapper:\n"
            "    flow:\n"
            "      steps:\n"
            "        inner:\n"
            "          run: echo ok\n"
            "          outputs: [result]\n"
            "    outputs: [result]\n"
        )
        wf = load_workflow_string(yaml)
        assert wf.steps["wrapper"].when is None


# ── Premature launch warning suppression ──────────────────────────────


class TestPrematureLaunchWarning:
    """Premature launch warnings should be suppressed when the downstream
    step has a when-condition that gates execution."""

    def _build_loop_flow(self, downstream_when=None):
        """Build a flow with a loop and a downstream step."""
        steps = {
            "run-tests": _script("run-tests", outputs=["status"]),
            "fix": _script(
                "fix", outputs=["fixes"],
                inputs=[InputBinding("status", "run-tests", "status")],
                when="status == 'fail'",
                exit_rules=[_loop_rule("run-tests")],
            ),
            "deploy": _script(
                "deploy", outputs=["result"],
                inputs=[InputBinding("status", "run-tests", "status")],
                when=downstream_when,
            ),
        }
        return WorkflowDefinition(steps=steps)

    def test_warning_fires_without_when(self):
        """Without a when-condition, the premature launch warning fires."""
        wf = self._build_loop_flow(downstream_when=None)
        warns = wf.warnings()
        premature = [w for w in warns if "may launch before" in w]
        assert len(premature) == 1
        assert "deploy" in premature[0]

    def test_warning_suppressed_with_when(self):
        """With a when-condition, the warning is suppressed."""
        wf = self._build_loop_flow(downstream_when="status == 'pass'")
        warns = wf.warnings()
        premature = [w for w in warns if "may launch before" in w]
        assert len(premature) == 0

    def test_warning_still_fires_for_ungated_step(self):
        """When-condition on one step doesn't suppress warnings for others."""
        steps = {
            "run-tests": _script("run-tests", outputs=["status"]),
            "fix": _script(
                "fix", outputs=["fixes"],
                inputs=[InputBinding("status", "run-tests", "status")],
                when="status == 'fail'",
                exit_rules=[_loop_rule("run-tests")],
            ),
            "gated": _script(
                "gated", outputs=["r1"],
                inputs=[InputBinding("status", "run-tests", "status")],
                when="status == 'pass'",
            ),
            "ungated": _script(
                "ungated", outputs=["r2"],
                inputs=[InputBinding("status", "run-tests", "status")],
            ),
        }
        wf = WorkflowDefinition(steps=steps)
        warns = wf.warnings()
        premature = [w for w in warns if "may launch before" in w]
        assert len(premature) == 1
        assert "ungated" in premature[0]
        assert "'gated'" not in premature[0]  # quoted to avoid substring match with 'ungated'
