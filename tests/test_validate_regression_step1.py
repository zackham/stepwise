"""Tests for R11 + R12: parse-time regressions for is_present: and dynamic session names."""

from __future__ import annotations

import pytest

from stepwise.yaml_loader import load_workflow_yaml


def test_is_present_on_regular_binding_rejected_at_parse_time(tmp_path):
    """Step 7 (§11.3): is_present: is now accepted on loop-back bindings,
    but rejected on regular (non-loop-back) bindings via the second-pass
    _validate_predicate_refs check (rule_id: is_present_not_loop_back).
    """
    flow_yaml = """\
name: test-is-present
steps:
  fetch:
    run: |
      echo '{"x": 1}'
    outputs: [x]
  consume:
    inputs:
      x: fetch.x
    when:
      input: x
      is_present: true
    run: 'echo "{}"'
    outputs: [done]
"""
    f = tmp_path / "is_present.flow.yaml"
    f.write_text(flow_yaml)
    with pytest.raises(Exception) as exc_info:
        load_workflow_yaml(f)
    msg = str(exc_info.value)
    assert "is_present" in msg
    assert "not a loop-back binding" in msg or "is_present_not_loop_back" in msg


def test_dynamic_session_name_rejected_at_parse_time(tmp_path):
    """R12: a templated session name like '${var}' is rejected at parse time."""
    flow_yaml = """\
name: test-dynamic-session
steps:
  writer:
    run: 'echo "{}"'
    outputs: [x]
    session: "${var}"
"""
    f = tmp_path / "dynamic.flow.yaml"
    f.write_text(flow_yaml)
    with pytest.raises(Exception) as exc_info:
        load_workflow_yaml(f)
    msg = str(exc_info.value)
    assert "session" in msg
    assert "${var}" in msg or "invalid" in msg.lower() or "static identifier" in msg


def test_static_session_name_with_dash_rejected(tmp_path):
    """A session name with a dash (not a Python identifier) is rejected."""
    flow_yaml = """\
name: test-dashed
steps:
  writer:
    run: 'echo "{}"'
    outputs: [x]
    session: "my-session"
"""
    f = tmp_path / "dashed.flow.yaml"
    f.write_text(flow_yaml)
    with pytest.raises(Exception) as exc_info:
        load_workflow_yaml(f)
    msg = str(exc_info.value)
    assert "session" in msg
    assert "my-session" in msg or "invalid" in msg.lower()


def test_valid_session_name_accepted(tmp_path):
    """A valid identifier-like session name parses cleanly."""
    flow_yaml = """\
name: test-valid
steps:
  writer:
    executor: agent
    config:
      agent: claude
    prompt: "do work"
    outputs: [x]
    session: "my_session"
"""
    f = tmp_path / "valid.flow.yaml"
    f.write_text(flow_yaml)
    wf = load_workflow_yaml(f)
    assert wf.steps["writer"].session == "my_session"
