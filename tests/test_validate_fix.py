"""Tests for stepwise validate --fix functionality."""

import tempfile
from pathlib import Path

from stepwise.yaml_loader import apply_fixes, load_workflow_yaml


YAML_WITH_UNBOUNDED_LOOP = """\
name: test-fix
steps:
  generate:
    # This step generates text
    run: |
      echo '{"text": "hello"}'
    outputs: [text]
    exits:
      - name: good
        when: "outputs.text == 'done'"
        action: advance
      - name: retry
        when: "True"
        action: loop
        target: generate
"""

YAML_MULTIPLE_UNBOUNDED = """\
name: multi-fix
steps:
  step-a:
    run: |
      echo '{"x": 1}'
    outputs: [x]
    exits:
      - name: done
        when: "outputs.x == 10"
        action: advance
      - name: redo-a
        when: "True"
        action: loop
        target: step-a

  step-b:
    run: |
      echo '{"y": 2}'
    outputs: [y]
    after: [step-a]
    exits:
      - name: done
        when: "outputs.y == 20"
        action: advance
      - name: redo-b
        when: "True"
        action: loop
        target: step-b

  step-c:
    run: |
      echo '{"z": 3}'
    outputs: [z]
    after: [step-b]
    exits:
      - name: done
        when: "outputs.z == 30"
        action: advance
      - name: redo-c
        when: "True"
        action: loop
        target: step-c
"""

YAML_ALREADY_BOUNDED = """\
name: clean
steps:
  generate:
    run: |
      echo '{"text": "hello"}'
    outputs: [text]
    exits:
      - name: good
        when: "outputs.text == 'done'"
        action: advance
      - name: retry
        when: "True"
        action: loop
        target: generate
        max_iterations: 5
"""


def _write_temp_yaml(content: str) -> Path:
    """Write YAML to a temp file and return the path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".flow.yaml", mode="w", delete=False)
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)


def test_fixable_warnings_detects_unbounded_loops():
    """fixable_warnings() returns descriptors for loop rules without max_iterations."""
    wf = load_workflow_yaml(YAML_WITH_UNBOUNDED_LOOP)
    fixes = wf.fixable_warnings()
    assert len(fixes) == 1
    assert fixes[0]["step"] == "generate"
    assert fixes[0]["rule_name"] == "retry"
    assert fixes[0]["fix"] == "add_max_iterations"
    assert fixes[0]["value"] == 10


def test_fixable_warnings_empty_when_bounded():
    """fixable_warnings() returns empty for flows with max_iterations set."""
    wf = load_workflow_yaml(YAML_ALREADY_BOUNDED)
    fixes = wf.fixable_warnings()
    assert fixes == []


def test_apply_fixes_adds_max_iterations():
    """apply_fixes inserts max_iterations into loop exit rules."""
    path = _write_temp_yaml(YAML_WITH_UNBOUNDED_LOOP)
    try:
        wf = load_workflow_yaml(str(path))
        fixes = wf.fixable_warnings()
        updated = apply_fixes(str(path), fixes)

        # Write back and re-parse
        path.write_text(updated)
        wf2 = load_workflow_yaml(str(path))
        assert wf2.fixable_warnings() == []

        # Verify the value was set correctly
        retry_rule = None
        for rule in wf2.steps["generate"].exit_rules:
            if rule.name == "retry":
                retry_rule = rule
        assert retry_rule is not None
        assert retry_rule.config["max_iterations"] == 10
    finally:
        path.unlink(missing_ok=True)


def test_apply_fixes_preserves_comments():
    """Comments in the YAML survive the round-trip."""
    path = _write_temp_yaml(YAML_WITH_UNBOUNDED_LOOP)
    try:
        wf = load_workflow_yaml(str(path))
        fixes = wf.fixable_warnings()
        updated = apply_fixes(str(path), fixes)
        assert "# This step generates text" in updated
    finally:
        path.unlink(missing_ok=True)


def test_apply_fixes_idempotent():
    """Running apply_fixes twice produces identical output."""
    path = _write_temp_yaml(YAML_WITH_UNBOUNDED_LOOP)
    try:
        wf = load_workflow_yaml(str(path))
        fixes = wf.fixable_warnings()
        first = apply_fixes(str(path), fixes)
        path.write_text(first)

        wf2 = load_workflow_yaml(str(path))
        fixes2 = wf2.fixable_warnings()
        assert fixes2 == []

        # Second apply with no fixes should be no-op
        second = apply_fixes(str(path), fixes2)
        assert first == second
    finally:
        path.unlink(missing_ok=True)


def test_multiple_fixes_in_one_pass():
    """Multiple unbounded loops in different steps all get fixed."""
    path = _write_temp_yaml(YAML_MULTIPLE_UNBOUNDED)
    try:
        wf = load_workflow_yaml(str(path))
        fixes = wf.fixable_warnings()
        assert len(fixes) == 3

        updated = apply_fixes(str(path), fixes)
        path.write_text(updated)
        wf2 = load_workflow_yaml(str(path))
        assert wf2.fixable_warnings() == []

        # Verify all three got max_iterations
        for step_name in ["step-a", "step-b", "step-c"]:
            loop_rules = [
                r for r in wf2.steps[step_name].exit_rules
                if r.config.get("action") == "loop"
            ]
            assert len(loop_rules) == 1
            assert loop_rules[0].config["max_iterations"] == 10
    finally:
        path.unlink(missing_ok=True)


def test_no_fixes_on_clean_flow():
    """apply_fixes with empty fix list returns unchanged YAML."""
    path = _write_temp_yaml(YAML_ALREADY_BOUNDED)
    try:
        original = path.read_text()
        result = apply_fixes(str(path), [])
        # Content should be equivalent (ruamel may normalize whitespace)
        wf = load_workflow_yaml(result)
        assert len(wf.steps) == 1
    finally:
        path.unlink(missing_ok=True)
