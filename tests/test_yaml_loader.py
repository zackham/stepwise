"""Tests for YAML workflow loader."""

import pytest

from stepwise.yaml_loader import (
    YAMLLoadError,
    _DotDict,
    evaluate_exit_condition,
    evaluate_when_condition,
    evaluate_derived_outputs,
    load_workflow_string,
)


# ── Expression Evaluation ────────────────────────────────────────────


class TestEvaluateExitCondition:
    def test_simple_comparison(self):
        assert evaluate_exit_condition("outputs.score >= 0.8", {"score": 0.9}, attempt=1)
        assert not evaluate_exit_condition("outputs.score >= 0.8", {"score": 0.5}, attempt=1)

    def test_string_equality(self):
        assert evaluate_exit_condition(
            "outputs.decision == 'approve'", {"decision": "approve"}, attempt=1
        )
        assert not evaluate_exit_condition(
            "outputs.decision == 'approve'", {"decision": "revise"}, attempt=1
        )

    def test_attempt_variable(self):
        assert evaluate_exit_condition("attempt >= 5", {}, attempt=5)
        assert not evaluate_exit_condition("attempt >= 5", {}, attempt=3)

    def test_compound_expression(self):
        assert evaluate_exit_condition(
            "outputs.decision == 'revise' and attempt < 5",
            {"decision": "revise"},
            attempt=3,
        )
        assert not evaluate_exit_condition(
            "outputs.decision == 'revise' and attempt < 5",
            {"decision": "revise"},
            attempt=5,
        )

    def test_any_all(self):
        assert evaluate_exit_condition(
            "any(s < 0.5 for s in outputs.scores)",
            {"scores": [0.8, 0.3, 0.9]},
            attempt=1,
        )
        assert not evaluate_exit_condition(
            "any(s < 0.5 for s in outputs.scores)",
            {"scores": [0.8, 0.7, 0.9]},
            attempt=1,
        )
        assert evaluate_exit_condition(
            "all(s > 0.5 for s in outputs.scores)",
            {"scores": [0.8, 0.7, 0.9]},
            attempt=1,
        )

    def test_sorted_indexing(self):
        assert evaluate_exit_condition(
            "sorted(outputs.scores)[1] > 0.7",
            {"scores": [0.9, 0.3, 0.8]},
            attempt=1,
        )

    def test_len(self):
        assert evaluate_exit_condition(
            "len(outputs.errors) == 0",
            {"errors": []},
            attempt=1,
        )

    def test_nested_dict_access(self):
        assert evaluate_exit_condition(
            "outputs.rubric.clarity > 0.7",
            {"rubric": {"clarity": 0.8, "depth": 0.6}},
            attempt=1,
        )

    def test_always_true(self):
        assert evaluate_exit_condition("True", {}, attempt=1)

    def test_max_attempts(self):
        assert evaluate_exit_condition(
            "max_attempts is not None and attempt >= max_attempts",
            {},
            attempt=5,
            max_attempts=5,
        )
        assert not evaluate_exit_condition(
            "max_attempts is not None and attempt >= max_attempts",
            {},
            attempt=3,
            max_attempts=5,
        )

    def test_invalid_expression_raises(self):
        with pytest.raises(ValueError, match="failed"):
            evaluate_exit_condition("import os", {}, attempt=1)

    def test_no_dangerous_builtins(self):
        with pytest.raises(ValueError, match="failed"):
            evaluate_exit_condition("__import__('os')", {}, attempt=1)


class TestDotDict:
    def test_attribute_access(self):
        d = _DotDict({"x": 1, "y": {"z": 2}})
        assert d.x == 1
        assert d.y.z == 2

    def test_missing_attribute(self):
        d = _DotDict({"x": 1})
        with pytest.raises(AttributeError):
            _ = d.missing


class TestExpressionSecurity:
    """AST validator blocks dunder traversal while preserving safe patterns."""

    # ── Blocked patterns ────────────────────────────────────────

    def test_class_traversal_blocked(self):
        with pytest.raises(ValueError, match="__class__"):
            evaluate_exit_condition(
                "().__class__.__bases__[0].__subclasses__()", {}, attempt=1
            )

    def test_dunder_on_outputs(self):
        with pytest.raises(ValueError, match="__class__"):
            evaluate_exit_condition("outputs.__class__", {"x": 1}, attempt=1)

    def test_dunder_globals(self):
        with pytest.raises(ValueError, match="__globals__"):
            evaluate_exit_condition("float.__globals__", {}, attempt=1)

    def test_fstring_blocked(self):
        with pytest.raises(ValueError, match="f-string"):
            evaluate_exit_condition("f'{True}'", {}, attempt=1)

    def test_lambda_blocked(self):
        with pytest.raises(ValueError, match="Lambda"):
            evaluate_exit_condition("(lambda: 1)()", {}, attempt=1)

    def test_import_blocked(self):
        """__import__ is not in SAFE_BUILTINS — eval raises NameError → ValueError."""
        with pytest.raises(ValueError):
            evaluate_exit_condition("__import__('os')", {}, attempt=1)

    def test_derived_blocks_dunder(self):
        with pytest.raises(ValueError, match="__name__"):
            evaluate_derived_outputs(
                {"evil": "val.__class__.__name__"}, {"val": "hello"}
            )

    # ── when collapses to False (not raise) ─────────────────────

    def test_when_dunder_returns_false(self):
        result = evaluate_when_condition("x.__class__.__bases__", {"x": "hi"})
        assert result is False

    def test_when_fstring_returns_false(self):
        result = evaluate_when_condition("f'{x}'", {"x": "hi"})
        assert result is False

    def test_when_missing_var_returns_false(self):
        result = evaluate_when_condition("undefined > 5", {})
        assert result is False

    # ── Backward-compatible patterns that MUST still work ───────

    def test_generator_with_any(self):
        assert evaluate_exit_condition(
            "any(s < 0.5 for s in outputs.scores)",
            {"scores": [0.3, 0.7]}, attempt=1,
        )

    def test_generator_with_all(self):
        assert evaluate_exit_condition(
            "all(s > 0.1 for s in outputs.scores)",
            {"scores": [0.3, 0.7]}, attempt=1,
        )

    def test_dict_get_method(self):
        assert evaluate_exit_condition(
            "outputs.get('missing', 'fallback') == 'fallback'",
            {}, attempt=1,
        )

    def test_dict_get_with_float_cast(self):
        """Real pattern from examples/report-test-loop.flow.yaml:18."""
        assert evaluate_exit_condition(
            "float(outputs.get('quality_score', 0)) >= 0.8",
            {"quality_score": "0.9"}, attempt=1,
        )

    def test_delegated_get_pattern(self):
        """Real pattern from CLAUDE.md:343 — dict .get() with underscore key."""
        assert evaluate_exit_condition(
            "outputs.get('_delegated', False)",
            {"_delegated": True}, attempt=1,
        )

    def test_is_comparison(self):
        """Real pattern from test_yaml_loader.py:88."""
        assert evaluate_exit_condition(
            "max_attempts is not None and attempt >= max_attempts",
            {}, attempt=5, max_attempts=5,
        )

    def test_sorted_indexing(self):
        assert evaluate_exit_condition(
            "sorted(outputs.scores)[0] == 1",
            {"scores": [3, 1, 2]}, attempt=1,
        )


class TestInputNameValidation:
    def test_valid_identifier_accepted(self):
        wf = load_workflow_string("""
steps:
  s:
    run: echo ok
    outputs: [x]
    inputs:
      my_url: $job.url
""")
        assert wf.steps["s"].inputs[0].local_name == "my_url"

    def test_hyphenated_name_rejected(self):
        with pytest.raises(Exception, match="not a valid identifier"):
            load_workflow_string("""
steps:
  s:
    run: echo ok
    outputs: [x]
    inputs:
      foo-bar: $job.val
""")

    def test_numeric_prefix_rejected(self):
        with pytest.raises(Exception, match="not a valid identifier"):
            load_workflow_string("""
steps:
  s:
    run: echo ok
    outputs: [x]
    inputs:
      123abc: $job.val
""")


# ── YAML Loading ─────────────────────────────────────────────────────


class TestLoadWorkflowString:
    def test_minimal_workflow(self):
        wf = load_workflow_string("""
steps:
  greet:
    run: scripts/greet.py
    outputs: [message]
""")
        assert len(wf.steps) == 1
        assert "greet" in wf.steps
        assert wf.steps["greet"].outputs == ["message"]
        assert wf.steps["greet"].executor.type == "script"
        assert "python3 scripts/greet.py" in wf.steps["greet"].executor.config["command"]

    def test_linear_pipeline(self):
        wf = load_workflow_string("""
steps:
  ingest:
    run: scripts/ingest.sh
    outputs: [records]

  validate:
    run: scripts/validate.py
    outputs: [valid_records]
    inputs:
      records: ingest.records
""")
        assert len(wf.steps) == 2
        assert wf.steps["validate"].inputs[0].local_name == "records"
        assert wf.steps["validate"].inputs[0].source_step == "ingest"
        assert wf.steps["validate"].inputs[0].source_field == "records"

    def test_job_level_inputs(self):
        wf = load_workflow_string("""
steps:
  start:
    run: scripts/start.py
    outputs: [result]
    inputs:
      topic: $job.topic
""")
        binding = wf.steps["start"].inputs[0]
        assert binding.local_name == "topic"
        assert binding.source_step == "$job"
        assert binding.source_field == "topic"

    def test_external_executor(self):
        wf = load_workflow_string("""
steps:
  approve:
    executor: external
    prompt: "Approve this?"
    outputs: [approved]
""")
        step = wf.steps["approve"]
        assert step.executor.type == "external"
        assert step.executor.config["prompt"] == "Approve this?"

    def test_fan_out(self):
        wf = load_workflow_string("""
steps:
  source:
    run: scripts/source.py
    outputs: [data]

  branch_a:
    run: scripts/branch_a.py
    outputs: [result]
    inputs:
      data: source.data

  branch_b:
    run: scripts/branch_b.py
    outputs: [result]
    inputs:
      data: source.data

  merge:
    run: scripts/merge.py
    outputs: [final]
    inputs:
      a: branch_a.result
      b: branch_b.result
""")
        assert len(wf.steps) == 4
        assert len(wf.steps["merge"].inputs) == 2

    def test_exit_rules(self):
        wf = load_workflow_string("""
steps:
  check:
    run: scripts/check.py
    outputs: [score, passed]

    exits:
      - name: passed
        when: "outputs.passed == True"
        action: advance

      - name: retry
        when: "outputs.passed == False and attempt < 3"
        action: loop
        target: check

      - name: give_up
        when: "attempt >= 3"
        action: escalate
""")
        step = wf.steps["check"]
        assert len(step.exit_rules) == 3
        assert step.exit_rules[0].name == "passed"
        assert step.exit_rules[0].type == "expression"
        assert step.exit_rules[0].config["action"] == "advance"
        assert step.exit_rules[1].config["action"] == "loop"
        assert step.exit_rules[1].config["target"] == "check"
        assert step.exit_rules[2].config["action"] == "escalate"

    def test_after(self):
        wf = load_workflow_string("""
steps:
  a:
    run: scripts/a.py
    outputs: [result]

  b:
    run: scripts/b.py
    outputs: [result]
    after: [a]
""")
        assert wf.steps["b"].after == ["a"]

    def test_after_string(self):
        """After as a single string (not list) should be accepted."""
        wf = load_workflow_string("""
steps:
  a:
    run: scripts/a.py
    outputs: [result]

  b:
    run: scripts/b.py
    outputs: [result]
    after: a
""")
        assert wf.steps["b"].after == ["a"]

    def test_decorators(self):
        wf = load_workflow_string("""
steps:
  build:
    run: scripts/build.sh
    outputs: [artifact]
    decorators:
      - type: timeout
        config: { seconds: 300 }
      - type: retry
        config: { max_retries: 2 }
""")
        step = wf.steps["build"]
        assert len(step.executor.decorators) == 2
        assert step.executor.decorators[0].type == "timeout"
        assert step.executor.decorators[1].type == "retry"

    def test_py_file_gets_python3_prefix(self):
        wf = load_workflow_string("""
steps:
  run_it:
    run: scripts/process.py
    outputs: [result]
""")
        assert wf.steps["run_it"].executor.config["command"] == "python3 scripts/process.py"

    def test_non_py_file_no_prefix(self):
        wf = load_workflow_string("""
steps:
  run_it:
    run: scripts/build.sh
    outputs: [result]
""")
        assert wf.steps["run_it"].executor.config["command"] == "scripts/build.sh"

    def test_mock_llm_executor(self):
        wf = load_workflow_string("""
steps:
  test:
    executor: mock_llm
    outputs: [result]
""")
        assert wf.steps["test"].executor.type == "mock_llm"

    def test_llm_executor(self):
        wf = load_workflow_string("""
steps:
  classify:
    executor: llm
    prompt: "Classify this text: $text"
    model: anthropic/claude-sonnet-4
    system: "You are a text classifier."
    temperature: 0.2
    outputs: [label, confidence]
""")
        step = wf.steps["classify"]
        assert step.executor.type == "llm"
        assert step.executor.config["prompt"] == "Classify this text: $text"
        assert step.executor.config["model"] == "anthropic/claude-sonnet-4"
        assert step.executor.config["system"] == "You are a text classifier."
        assert step.executor.config["temperature"] == 0.2

    def test_llm_executor_minimal(self):
        wf = load_workflow_string("""
steps:
  ask:
    executor: llm
    prompt: "What is $thing?"
    outputs: [answer]
""")
        step = wf.steps["ask"]
        assert step.executor.type == "llm"
        assert "model" not in step.executor.config

    def test_llm_executor_missing_prompt(self):
        with pytest.raises(YAMLLoadError, match="requires 'prompt'"):
            load_workflow_string("""
steps:
  bad:
    executor: llm
    outputs: [answer]
""")

    def test_exit_rule_priority_order(self):
        """First rule in YAML should have highest priority."""
        wf = load_workflow_string("""
steps:
  check:
    run: scripts/check.py
    outputs: [value]
    exits:
      - name: first
        when: "True"
        action: advance
      - name: second
        when: "True"
        action: escalate
""")
        rules = wf.steps["check"].exit_rules
        assert rules[0].priority > rules[1].priority

    def test_sequencing_deprecated_alias(self):
        """The old 'sequencing' key still works as a deprecated alias."""
        wf = load_workflow_string("""
steps:
  a:
    run: echo ok
    outputs: [x]
  b:
    run: echo ok
    outputs: [y]
    sequencing: [a]
""")
        assert wf.steps["b"].after == ["a"]

    def test_after_and_sequencing_conflict(self):
        with pytest.raises(YAMLLoadError, match="cannot use both"):
            load_workflow_string("""
steps:
  a:
    run: echo ok
    outputs: [x]
  b:
    run: echo ok
    outputs: [y]
    after: [a]
    sequencing: [a]
""")


# ── Error Cases ──────────────────────────────────────────────────────


class TestYAMLLoadErrors:
    def test_missing_steps(self):
        with pytest.raises(YAMLLoadError, match="steps"):
            load_workflow_string("name: test")

    def test_empty_steps(self):
        with pytest.raises(YAMLLoadError, match="steps"):
            load_workflow_string("""
steps: {}
""")

    def test_step_not_mapping(self):
        with pytest.raises(YAMLLoadError, match="must be a mapping"):
            load_workflow_string("""
steps:
  bad: "not a mapping"
""")

    def test_missing_executor(self):
        with pytest.raises(YAMLLoadError, match="must have either 'run' or 'executor'"):
            load_workflow_string("""
steps:
  broken:
    outputs: [result]
""")

    def test_invalid_input_source(self):
        with pytest.raises(YAMLLoadError, match="Expected 'step_name.field_name'"):
            load_workflow_string("""
steps:
  broken:
    run: scripts/test.py
    outputs: [result]
    inputs:
      x: no_dot_here
""")

    def test_unknown_step_reference(self):
        with pytest.raises(YAMLLoadError, match="unknown step"):
            load_workflow_string("""
steps:
  a:
    run: scripts/a.py
    outputs: [result]
    inputs:
      x: nonexistent.field
""")

    def test_unknown_field_reference(self):
        with pytest.raises(YAMLLoadError, match="unknown field"):
            load_workflow_string("""
steps:
  a:
    run: scripts/a.py
    outputs: [result]

  b:
    run: scripts/b.py
    outputs: [result]
    inputs:
      x: a.nonexistent_field
""")

    def test_exit_rule_missing_when(self):
        with pytest.raises(YAMLLoadError, match="missing 'when'"):
            load_workflow_string("""
steps:
  a:
    run: scripts/a.py
    outputs: [result]
    exits:
      - name: bad
        action: advance
""")

    def test_exit_rule_invalid_action(self):
        with pytest.raises(YAMLLoadError, match="invalid action"):
            load_workflow_string("""
steps:
  a:
    run: scripts/a.py
    outputs: [result]
    exits:
      - name: bad
        when: "True"
        action: explode
""")

    def test_loop_without_target(self):
        with pytest.raises(YAMLLoadError, match="no 'target'"):
            load_workflow_string("""
steps:
  a:
    run: scripts/a.py
    outputs: [result]
    exits:
      - name: retry
        when: "True"
        action: loop
""")

    def test_loop_target_unknown_step(self):
        with pytest.raises(YAMLLoadError, match="not a valid step"):
            load_workflow_string("""
steps:
  a:
    run: scripts/a.py
    outputs: [result]
    exits:
      - name: retry
        when: "True"
        action: loop
        target: nonexistent
""")

    def test_loop_to_self(self):
        """Loop targeting the step itself is valid."""
        wf = load_workflow_string("""
steps:
  a:
    run: echo ok
    outputs: [result]
    exits:
      - name: retry
        when: "attempt < 3"
        action: loop
        target: a
""")
        assert "a" in wf.steps

    def test_loop_to_ancestor(self):
        """Loop targeting an upstream dependency is valid."""
        wf = load_workflow_string("""
steps:
  a:
    run: echo ok
    outputs: [result]

  b:
    run: echo ok
    inputs:
      x: a.result
    outputs: [result]
    exits:
      - name: retry
        when: "True"
        action: loop
        target: a
""")
        assert "b" in wf.steps

    def test_loop_to_non_ancestor(self):
        """Loop targeting a step that is not connected in the DAG should fail."""
        with pytest.raises(YAMLLoadError, match="not connected"):
            load_workflow_string("""
steps:
  a:
    run: echo ok
    outputs: [result]

  b:
    run: echo ok
    outputs: [result]

  c:
    run: echo ok
    inputs:
      x: a.result
    outputs: [result]
    exits:
      - name: retry
        when: "True"
        action: loop
        target: b
""")

    def test_loop_to_transitive_ancestor(self):
        """Loop targeting a transitive ancestor (grandparent) is valid."""
        wf = load_workflow_string("""
steps:
  a:
    run: echo ok
    outputs: [result]

  b:
    run: echo ok
    inputs:
      x: a.result
    outputs: [result]

  c:
    run: echo ok
    inputs:
      x: b.result
    outputs: [result]
    exits:
      - name: retry
        when: "True"
        action: loop
        target: a
""")
        assert "c" in wf.steps

    def test_cycle_detection(self):
        with pytest.raises(YAMLLoadError, match="Cycle"):
            load_workflow_string("""
steps:
  a:
    run: scripts/a.py
    outputs: [result]
    inputs:
      x: b.result

  b:
    run: scripts/b.py
    outputs: [result]
    inputs:
      x: a.result
""")

    def test_invalid_yaml(self):
        with pytest.raises(YAMLLoadError, match="YAML parse error"):
            load_workflow_string("{{invalid yaml}}")

    def test_yaml_root_not_mapping(self):
        with pytest.raises(YAMLLoadError, match="must be a mapping"):
            load_workflow_string("- just\n- a\n- list")

    def test_after_unknown_step(self):
        with pytest.raises(YAMLLoadError, match="unknown step"):
            load_workflow_string("""
steps:
  a:
    run: scripts/a.py
    outputs: [result]
    after: [nonexistent]
""")


# ── Integration: YAML → Engine ───────────────────────────────────────


class TestYAMLToEngine:
    """Test that YAML-loaded workflows produce correct WorkflowDefinitions."""

    def test_iterative_review_structure(self):
        wf = load_workflow_string("""
steps:
  draft:
    run: scripts/draft.py
    outputs: [content]

  review:
    executor: external
    prompt: "Review this"
    outputs: [decision, feedback]
    inputs:
      content: draft.content
    exits:
      - name: approve
        when: "outputs.decision == 'approve'"
        action: advance
      - name: revise
        when: "outputs.decision == 'revise' and attempt < 5"
        action: loop
        target: draft
      - name: max_revisions
        when: "attempt >= 5"
        action: escalate

  publish:
    run: scripts/publish.py
    outputs: [url]
    inputs:
      content: draft.content
    after: [review]
""")
        # Structure checks
        assert len(wf.steps) == 3
        assert wf.entry_steps() == ["draft"]
        assert wf.terminal_steps() == ["publish"]

        # Exit rule checks
        review = wf.steps["review"]
        assert len(review.exit_rules) == 3

        # Executor checks
        assert wf.steps["draft"].executor.type == "script"
        assert wf.steps["review"].executor.type == "external"
        assert wf.steps["publish"].executor.type == "script"

    def test_workflow_validates_cleanly(self):
        wf = load_workflow_string("""
steps:
  a:
    run: scripts/a.py
    outputs: [x]
  b:
    run: scripts/b.py
    outputs: [y]
    inputs:
      x: a.x
  c:
    run: scripts/c.py
    outputs: [z]
    inputs:
      y: b.y
""")
        errors = wf.validate()
        assert errors == []
