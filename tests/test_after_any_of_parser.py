"""Tests for _parse_after (yaml_loader) and the YAML surface for after.any_of."""

from __future__ import annotations

import pytest

from stepwise.yaml_loader import _parse_after, load_workflow_yaml


# ─── _parse_after positive cases ──────────────────────────────────────────


def test_parse_empty_returns_empty():
    assert _parse_after(None, "x") == ([], [])
    assert _parse_after([], "x") == ([], [])


def test_parse_string_singleton():
    assert _parse_after("A", "x") == (["A"], [])


def test_parse_list_of_strings_no_groups():
    assert _parse_after(["A", "B"], "x") == (["A", "B"], [])


def test_parse_list_with_single_any_of_group():
    assert _parse_after([{"any_of": ["A", "B"]}], "x") == ([], [["A", "B"]])


def test_parse_mixed_string_and_any_of():
    """R3: mixed list form ['X', {any_of: ['A', 'B']}]."""
    assert _parse_after(["X", {"any_of": ["A", "B"]}], "x") == (
        ["X"],
        [["A", "B"]],
    )


def test_parse_multiple_any_of_groups():
    assert _parse_after(
        [{"any_of": ["A", "B"]}, {"any_of": ["C", "D"]}], "x"
    ) == ([], [["A", "B"], ["C", "D"]])


def test_parse_complex_mixed():
    """A regular dep, two any_of groups, another regular dep."""
    assert _parse_after(
        ["X", {"any_of": ["A", "B"]}, {"any_of": ["C", "D"]}, "Y"],
        "x",
    ) == (["X", "Y"], [["A", "B"], ["C", "D"]])


# ─── _parse_after negative cases ──────────────────────────────────────────


def test_parse_pure_any_of_dict_rejected():
    """R-pure-dict: `after: {any_of: [...]}` is rejected; must use list form."""
    with pytest.raises(ValueError, match="must be a string, list"):
        _parse_after({"any_of": ["A", "B"]}, "x")


def test_parse_empty_any_of_rejected():
    """R-empty: `after: [{any_of: []}]` rejected."""
    with pytest.raises(ValueError, match="non-empty"):
        _parse_after([{"any_of": []}], "x")


def test_parse_single_member_any_of_rejected():
    """R-single: `after: [{any_of: [X]}]` rejected with 'use plain' hint."""
    with pytest.raises(ValueError, match="single member.*use plain") as exc_info:
        _parse_after([{"any_of": ["X"]}], "step1")
    assert "step1" in str(exc_info.value)
    assert "X" in str(exc_info.value)


def test_parse_self_reference_rejected():
    """R-self: cannot reference self in any_of."""
    with pytest.raises(ValueError, match="cannot reference self"):
        _parse_after([{"any_of": ["step1", "B"]}], "step1")


def test_parse_non_string_member_rejected():
    """R-string: each any_of member must be a string."""
    with pytest.raises(ValueError, match="must be strings"):
        _parse_after([{"any_of": ["A", 42]}], "x")


def test_parse_unknown_dict_key_rejected():
    """Only `any_of` is supported as a dict key."""
    with pytest.raises(ValueError, match="unsupported keys"):
        _parse_after([{"any_of": ["A", "B"], "extra": "stuff"}], "x")


def test_parse_dict_without_any_of_rejected():
    """A dict without any_of is also rejected (only any_of dicts allowed)."""
    with pytest.raises(ValueError, match="unsupported keys"):
        _parse_after([{"foo": "bar"}], "x")


def test_parse_int_member_rejected():
    """A non-str/dict element in the list is rejected."""
    with pytest.raises(ValueError, match="must be a string or"):
        _parse_after(["A", 42], "x")


def test_parse_any_of_not_a_list_rejected():
    """The any_of value must itself be a list."""
    with pytest.raises(ValueError, match="must be a list"):
        _parse_after([{"any_of": "not-a-list"}], "x")


# ─── YAML surface integration ─────────────────────────────────────────────


def test_load_yaml_flow_with_after_any_of(tmp_path):
    """Full YAML load round-trip: a flow with mixed after.any_of parses correctly."""
    flow_yaml = """\
name: test-after-any-of
author: test
steps:
  a:
    run: |
      echo '{"value": 1}'
    outputs: [value]
  b:
    run: |
      echo '{"value": 2}'
    outputs: [value]
  c:
    run: |
      echo '{"result": "done"}'
    outputs: [result]
    after:
      - any_of: [a, b]
"""
    f = tmp_path / "any_of.flow.yaml"
    f.write_text(flow_yaml)
    wf = load_workflow_yaml(f)
    assert wf.steps["c"].after == []
    assert wf.steps["c"].after_any_of == [["a", "b"]]


def test_load_yaml_flow_with_mixed_after(tmp_path):
    """Mixed list form: a regular dep + an any_of group."""
    flow_yaml = """\
name: test-mixed
author: test
steps:
  x:
    run: |
      echo '{"v": 0}'
    outputs: [v]
  a:
    run: |
      echo '{"v": 1}'
    outputs: [v]
    after: [x]
  b:
    run: |
      echo '{"v": 2}'
    outputs: [v]
    after: [x]
  c:
    run: |
      echo '{"r": "ok"}'
    outputs: [r]
    after:
      - x
      - any_of: [a, b]
"""
    f = tmp_path / "mixed.flow.yaml"
    f.write_text(flow_yaml)
    wf = load_workflow_yaml(f)
    assert wf.steps["c"].after == ["x"]
    assert wf.steps["c"].after_any_of == [["a", "b"]]


def test_load_yaml_flow_rejects_empty_any_of(tmp_path):
    """Parse-time validation propagates from YAML load."""
    flow_yaml = """\
name: test-empty-any-of
author: test
steps:
  a:
    run: 'echo "{}"'
    outputs: [v]
  c:
    run: 'echo "{}"'
    outputs: [r]
    after:
      - any_of: []
"""
    f = tmp_path / "empty.flow.yaml"
    f.write_text(flow_yaml)
    with pytest.raises(Exception, match="non-empty"):
        load_workflow_yaml(f)
