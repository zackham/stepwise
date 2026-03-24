"""Tests for stall detection warnings in WorkflowDefinition.warnings().

Covers R2 (stall detection) and R3 (unbounded loop detection).
"""

import pytest

from stepwise.models import (
    ExitRule,
    ExecutorRef,
    InputBinding,
    OutputFieldSpec,
    StepDefinition,
    WorkflowDefinition,
)


def _external_step(name, outputs, exit_rules, output_schema=None):
    """Helper: create an external step with exit rules."""
    return StepDefinition(
        name=name,
        outputs=outputs,
        executor=ExecutorRef("external", {"prompt": "test"}),
        exit_rules=exit_rules,
        output_schema=output_schema or {},
    )


def _callable_step(name, outputs, exit_rules):
    """Helper: create a callable step with exit rules."""
    return StepDefinition(
        name=name,
        outputs=outputs,
        executor=ExecutorRef("callable", {"fn_name": "noop"}),
        exit_rules=exit_rules,
    )


class TestUnboundedLoopWarning:
    def test_unbounded_loop_warning(self):
        """Loop exit rule without max_iterations produces a warning."""
        wf = WorkflowDefinition(steps={
            "step_a": _callable_step("step_a", ["result"], [
                ExitRule("loop_back", "expression", {
                    "condition": "not outputs.get('done', False)",
                    "action": "loop", "target": "step_a",
                    # no max_iterations
                }, priority=5),
                ExitRule("done", "expression", {
                    "condition": "outputs.get('done', False)",
                    "action": "advance",
                }, priority=10),
            ]),
        })
        warns = wf.warnings()
        loop_warns = [w for w in warns if "max_iterations" in w]
        assert len(loop_warns) >= 1
        assert "step_a" in loop_warns[0]
        assert "loop_back" in loop_warns[0]


class TestNoCatchAllWarning:
    def test_no_catch_all_warning(self):
        """Step with exit rules but no catch-all produces a warning."""
        wf = WorkflowDefinition(steps={
            "check": _callable_step("check", ["status"], [
                ExitRule("pass", "expression", {
                    "condition": "outputs.get('status') == 'pass'",
                    "action": "advance",
                }, priority=10),
                ExitRule("fail", "expression", {
                    "condition": "outputs.get('status') == 'fail'",
                    "action": "loop", "target": "check", "max_iterations": 3,
                }, priority=5),
            ]),
        })
        warns = wf.warnings()
        catch_all_warns = [w for w in warns if "catch-all" in w]
        assert len(catch_all_warns) >= 1
        assert "check" in catch_all_warns[0]


class TestExternalStepStallDetection:
    def test_external_step_stall_detection(self):
        """External step with output gap produces a stall warning."""
        wf = WorkflowDefinition(steps={
            "approve": _external_step(
                "approve", ["approved", "feedback"],
                exit_rules=[
                    ExitRule("approved", "expression", {
                        "condition": "outputs.get('approved') == True",
                        "action": "advance",
                    }, priority=10),
                    ExitRule("rejected_with_feedback", "expression", {
                        "condition": (
                            "outputs.get('approved') == False "
                            "and outputs.get('feedback') is not None"
                        ),
                        "action": "loop", "target": "approve", "max_iterations": 5,
                    }, priority=5),
                ],
                output_schema={
                    "approved": OutputFieldSpec(type="bool", required=True),
                    "feedback": OutputFieldSpec(type="str", required=False),
                },
            ),
        })
        warns = wf.warnings()
        stall_warns = [w for w in warns if "uncovered" in w]
        assert len(stall_warns) >= 1
        # The gap is {approved: False, feedback: None}
        gap_warn = [w for w in stall_warns if "False" in w and "None" in w]
        assert len(gap_warn) >= 1, f"Expected gap for approved=False/feedback=None, got: {stall_warns}"

    def test_external_step_full_coverage_no_warning(self):
        """External step with catch-all has no stall warning."""
        wf = WorkflowDefinition(steps={
            "approve": _external_step(
                "approve", ["approved", "feedback"],
                exit_rules=[
                    ExitRule("approved", "expression", {
                        "condition": "outputs.get('approved') == True",
                        "action": "advance",
                    }, priority=10),
                    ExitRule("rejected_with_feedback", "expression", {
                        "condition": (
                            "outputs.get('approved') == False "
                            "and outputs.get('feedback') is not None"
                        ),
                        "action": "loop", "target": "approve", "max_iterations": 5,
                    }, priority=5),
                    ExitRule("fallback", "expression", {
                        "condition": "True",
                        "action": "loop", "target": "approve", "max_iterations": 5,
                    }, priority=1),
                ],
                output_schema={
                    "approved": OutputFieldSpec(type="bool", required=True),
                    "feedback": OutputFieldSpec(type="str", required=False),
                },
            ),
        })
        warns = wf.warnings()
        stall_warns = [w for w in warns if "uncovered" in w]
        assert len(stall_warns) == 0, f"Expected no stall warnings with catch-all, got: {stall_warns}"


class TestCoercionSafetyNote:
    def test_coercion_safety_note(self):
        """Exit rule using float() produces an info note."""
        wf = WorkflowDefinition(steps={
            "scorer": _callable_step("scorer", ["score"], [
                ExitRule("high", "expression", {
                    "condition": "float(outputs.get('score', 0)) >= 4.0",
                    "action": "advance",
                }, priority=10),
                ExitRule("fallback", "expression", {
                    "condition": "True",
                    "action": "loop", "target": "scorer", "max_iterations": 3,
                }, priority=1),
            ]),
        })
        warns = wf.warnings()
        coercion_warns = [w for w in warns if "coercion" in w]
        assert len(coercion_warns) >= 1
        assert "high" in coercion_warns[0]


class TestCombinationCap:
    def test_combination_cap(self):
        """Step with >256 output combinations skips analysis with info note."""
        # 9 bool fields = 512 combinations (> 256 cap)
        schema = {
            f"field_{i}": OutputFieldSpec(type="bool", required=True)
            for i in range(9)
        }
        wf = WorkflowDefinition(steps={
            "big_form": _external_step(
                "big_form",
                [f"field_{i}" for i in range(9)],
                exit_rules=[
                    ExitRule("done", "expression", {
                        "condition": "outputs.get('field_0') == True",
                        "action": "advance",
                    }, priority=10),
                ],
                output_schema=schema,
            ),
        })
        warns = wf.warnings()
        cap_warns = [w for w in warns if "skipping" in w.lower()]
        assert len(cap_warns) >= 1
        assert "512" in cap_warns[0]
        # No uncovered combination warnings (analysis was skipped)
        stall_warns = [w for w in warns if "uncovered" in w]
        assert len(stall_warns) == 0


class TestCLIValidateWarnings:
    """Integration test: cmd_validate shows warnings in output."""

    def test_validate_shows_warnings(self, tmp_path):
        """Write a flow YAML with stall risk; verify validate output has warnings."""
        flow_content = """\
steps:
  review:
    executor: external
    prompt: "Review this"
    outputs:
      approved:
        type: bool
    exits:
      - name: approved
        when: "outputs.get('approved') == True"
        action: advance
"""
        flow_file = tmp_path / "test.flow.yaml"
        flow_file.write_text(flow_content)

        from stepwise.yaml_loader import load_workflow_yaml
        wf = load_workflow_yaml(str(flow_file))
        warns = wf.warnings()

        # Should have catch-all warning and uncovered combination warning
        assert any("catch-all" in w for w in warns), f"Expected catch-all warning, got: {warns}"
        assert any("uncovered" in w for w in warns), f"Expected uncovered warning, got: {warns}"


class TestUngatedPostLoopWarning:
    """Warn when a step has after on a looping step but no when condition."""

    def test_ungated_after_on_loop_target(self):
        """Step with after on a loop target and no 'when' produces a warning."""
        wf = WorkflowDefinition(steps={
            "draft": _callable_step("draft", ["content"], []),
            "review": _callable_step("review", ["score"], [
                ExitRule("good", "expression", {
                    "condition": "float(outputs.get('score', 0)) >= 0.8",
                    "action": "advance",
                }, priority=10),
                ExitRule("retry", "expression", {
                    "condition": "attempt < 3",
                    "action": "loop", "target": "draft", "max_iterations": 3,
                }, priority=5),
            ]),
            "publish": StepDefinition(
                name="publish",
                outputs=["url"],
                executor=ExecutorRef("callable", {"fn_name": "noop"}),
                inputs=[InputBinding("content", "draft", "content")],
                after=["review"],
                # no when condition — this is the bug
            ),
        })
        warns = wf.warnings()
        post_loop_warns = [w for w in warns if "looping step" in w]
        assert len(post_loop_warns) == 1
        assert "publish" in post_loop_warns[0]
        assert "review" in post_loop_warns[0]

    def test_gated_after_on_loop_target_no_warning(self):
        """Step with after on a loop target AND a 'when' condition produces no warning."""
        wf = WorkflowDefinition(steps={
            "draft": _callable_step("draft", ["content"], []),
            "review": _callable_step("review", ["score"], [
                ExitRule("good", "expression", {
                    "condition": "float(outputs.get('score', 0)) >= 0.8",
                    "action": "advance",
                }, priority=10),
                ExitRule("retry", "expression", {
                    "condition": "attempt < 3",
                    "action": "loop", "target": "draft", "max_iterations": 3,
                }, priority=5),
            ]),
            "publish": StepDefinition(
                name="publish",
                outputs=["url"],
                executor=ExecutorRef("callable", {"fn_name": "noop"}),
                inputs=[
                    InputBinding("content", "draft", "content"),
                    InputBinding("score", "review", "score"),
                ],
                after=["review"],
                when="float(score) >= 0.8",  # properly gated
            ),
        })
        warns = wf.warnings()
        post_loop_warns = [w for w in warns if "looping step" in w]
        assert len(post_loop_warns) == 0

    def test_self_loop_after_warning(self):
        """Step with after on a self-looping step produces a warning."""
        wf = WorkflowDefinition(steps={
            "retry_step": _callable_step("retry_step", ["result"], [
                ExitRule("done", "expression", {
                    "condition": "outputs.get('done', False)",
                    "action": "advance",
                }, priority=10),
                ExitRule("retry", "expression", {
                    "condition": "True",
                    "action": "loop", "target": "retry_step", "max_iterations": 5,
                }, priority=1),
            ]),
            "next_step": StepDefinition(
                name="next_step",
                outputs=["out"],
                executor=ExecutorRef("callable", {"fn_name": "noop"}),
                after=["retry_step"],
                # no when
            ),
        })
        warns = wf.warnings()
        post_loop_warns = [w for w in warns if "looping step" in w]
        assert len(post_loop_warns) == 1
        assert "next_step" in post_loop_warns[0]
        assert "retry_step" in post_loop_warns[0]

    def test_no_warning_when_no_loop(self):
        """Step with after on a non-looping step produces no warning."""
        wf = WorkflowDefinition(steps={
            "step_a": _callable_step("step_a", ["result"], []),
            "step_b": StepDefinition(
                name="step_b",
                outputs=["out"],
                executor=ExecutorRef("callable", {"fn_name": "noop"}),
                after=["step_a"],
            ),
        })
        warns = wf.warnings()
        post_loop_warns = [w for w in warns if "looping step" in w]
        assert len(post_loop_warns) == 0
