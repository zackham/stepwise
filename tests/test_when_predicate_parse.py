"""Tests for WhenPredicate dataclass + _parse_when dispatcher.

Validates §5.4 ban enforcement, predicate-form parsing, round-trip
serialization, and step-name error wrapping.
"""

from __future__ import annotations

import pytest

from stepwise.models import StepDefinition, WhenPredicate
from stepwise.yaml_loader import _parse_when, load_workflow_yaml


# ─── WhenPredicate dataclass: positive ────────────────────────────────────


class TestWhenPredicatePositive:
    def test_eq_str(self):
        p = WhenPredicate(input="status", op="eq", value="pass")
        assert p.input == "status"
        assert p.op == "eq"
        assert p.value == "pass"

    def test_eq_int(self):
        p = WhenPredicate(input="count", op="eq", value=3)
        assert p.value == 3

    def test_in_tuple(self):
        p = WhenPredicate(input="status", op="in", value=("a", "b"))
        assert p.value == ("a", "b")

    def test_in_int_tuple(self):
        p = WhenPredicate(input="code", op="in", value=(200, 201))
        assert p.value == (200, 201)

    def test_is_null_true(self):
        p = WhenPredicate(input="error", op="is_null", value=True)
        assert p.value is True

    def test_is_null_false(self):
        p = WhenPredicate(input="error", op="is_null", value=False)
        assert p.value is False

    def test_is_present_passes_dataclass(self):
        # Dataclass itself accepts is_present — _parse_when is what rejects.
        p = WhenPredicate(input="x", op="is_present", value=True)
        assert p.op == "is_present"


# ─── WhenPredicate dataclass: negative (§5.4 bans) ────────────────────────


class TestWhenPredicateBans:
    def test_eq_null_rejected(self):
        with pytest.raises(ValueError, match="cannot be null.*is_null"):
            WhenPredicate(input="x", op="eq", value=None)

    def test_eq_true_rejected(self):
        with pytest.raises(ValueError, match="cannot be a bool"):
            WhenPredicate(input="x", op="eq", value=True)

    def test_eq_false_rejected(self):
        with pytest.raises(ValueError, match="cannot be a bool"):
            WhenPredicate(input="x", op="eq", value=False)

    def test_eq_float_rejected(self):
        with pytest.raises(ValueError, match="float literals banned"):
            WhenPredicate(input="x", op="eq", value=5.0)

    def test_in_with_float_rejected(self):
        with pytest.raises(ValueError, match="float literals banned"):
            WhenPredicate(input="x", op="in", value=("a", 5.0))

    def test_in_with_bool_rejected(self):
        with pytest.raises(ValueError, match="cannot be bool"):
            WhenPredicate(input="x", op="in", value=("a", True))

    def test_in_with_null_rejected(self):
        with pytest.raises(ValueError, match="cannot be null"):
            WhenPredicate(input="x", op="in", value=("a", None))

    def test_empty_in_rejected(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            WhenPredicate(input="x", op="in", value=())

    def test_in_not_tuple_rejected(self):
        with pytest.raises(ValueError, match="must be a tuple"):
            WhenPredicate(input="x", op="in", value=["a", "b"])

    def test_is_null_with_non_bool_rejected(self):
        with pytest.raises(ValueError, match="must be bool"):
            WhenPredicate(input="x", op="is_null", value="yes")

    def test_is_present_with_non_bool_rejected(self):
        with pytest.raises(ValueError, match="must be bool"):
            WhenPredicate(input="x", op="is_present", value=1)


# ─── Round-trip via to_dict/from_dict ─────────────────────────────────────


class TestWhenPredicateRoundTrip:
    @pytest.mark.parametrize("pred", [
        WhenPredicate(input="status", op="eq", value="pass"),
        WhenPredicate(input="count", op="eq", value=3),
        WhenPredicate(input="status", op="in", value=("a", "b", "c")),
        WhenPredicate(input="code", op="in", value=(200, 201)),
        WhenPredicate(input="error", op="is_null", value=True),
        WhenPredicate(input="error", op="is_null", value=False),
    ])
    def test_round_trip(self, pred):
        d = pred.to_dict()
        pred2 = WhenPredicate.from_dict(d)
        assert pred == pred2

    def test_in_yaml_serializes_as_list(self):
        pred = WhenPredicate(input="status", op="in", value=("a", "b"))
        d = pred.to_dict()
        assert d["in"] == ["a", "b"]
        assert isinstance(d["in"], list)

    def test_step_definition_round_trip(self):
        from stepwise.models import ExecutorRef
        step = StepDefinition(
            name="run-tests",
            outputs=["passed"],
            executor=ExecutorRef("script", {"command": "echo"}),
            when=WhenPredicate(input="mode", op="eq", value="ci"),
        )
        d = step.to_dict()
        assert d["when"] == {"input": "mode", "eq": "ci"}
        step2 = StepDefinition.from_dict(d)
        assert isinstance(step2.when, WhenPredicate)
        assert step2.when == step.when

    def test_step_definition_after_any_of_round_trip(self):
        """after_any_of round-trips through to_dict / from_dict via inline list form."""
        from stepwise.models import ExecutorRef
        step = StepDefinition(
            name="rejoin",
            outputs=["result"],
            executor=ExecutorRef("script", {"command": "echo"}),
            after_any_of=[["branch_a", "branch_b"], ["worker_x", "worker_y"]],
        )
        d = step.to_dict()
        # Step 4 changed the canonical surface to inline list form: any_of
        # groups are emitted as {any_of: [...]} dicts in the after list.
        assert d["after"] == [
            {"any_of": ["branch_a", "branch_b"]},
            {"any_of": ["worker_x", "worker_y"]},
        ]
        assert "after_any_of" not in d
        step2 = StepDefinition.from_dict(d)
        assert step2.after_any_of == step.after_any_of
        assert step2.after == []

    def test_step_definition_empty_after_any_of_omitted(self):
        """Empty after_any_of is omitted from to_dict (default behavior)."""
        from stepwise.models import ExecutorRef
        step = StepDefinition(
            name="x",
            outputs=["o"],
            executor=ExecutorRef("script", {}),
        )
        d = step.to_dict()
        assert "after_any_of" not in d
        step2 = StepDefinition.from_dict(d)
        assert step2.after_any_of == []


# ─── _parse_when dispatcher ───────────────────────────────────────────────


class TestParseWhen:
    def test_none_passes_through(self):
        assert _parse_when(None, "step-x") is None

    def test_string_passes_through(self):
        assert _parse_when("status == 'pass'", "step-x") == "status == 'pass'"

    def test_dict_parses_to_predicate(self):
        result = _parse_when({"input": "status", "eq": "pass"}, "step-x")
        assert isinstance(result, WhenPredicate)
        assert result.input == "status"
        assert result.op == "eq"
        assert result.value == "pass"

    def test_dict_in_to_predicate(self):
        result = _parse_when({"input": "code", "in": [200, 201]}, "step-x")
        assert isinstance(result, WhenPredicate)
        assert result.value == (200, 201)

    def test_other_type_rejected(self):
        with pytest.raises(ValueError, match="step 'foo'.*must be a string or mapping"):
            _parse_when(42, "foo")

    def test_missing_input_rejected_with_step_name(self):
        with pytest.raises(ValueError, match="step 'foo'.*missing required 'input'"):
            _parse_when({"eq": "pass"}, "foo")

    def test_two_op_keys_rejected(self):
        with pytest.raises(ValueError, match="step 'foo'.*multiple operators.*eq, in"):
            _parse_when({"input": "x", "eq": "a", "in": ["a"]}, "foo")

    def test_no_op_keys_rejected(self):
        with pytest.raises(ValueError, match="step 'foo'.*missing operator.*exactly one of"):
            _parse_when({"input": "x"}, "foo")

    def test_is_present_now_accepted_at_parse_dispatch(self):
        """Step 7: is_present: is no longer rejected by _parse_when itself.
        Structural validation (is_present only on loop-back bindings)
        happens in a second pass via _validate_predicate_refs. The
        WhenPredicate is constructed and returned cleanly here.
        """
        result = _parse_when({"input": "x", "is_present": True}, "foo")
        assert isinstance(result, WhenPredicate)
        assert result.op == "is_present"
        assert result.value is True

    def test_eq_null_wrapped_with_step_name(self):
        with pytest.raises(ValueError, match="step 'foo'.*cannot be null.*is_null"):
            _parse_when({"input": "x", "eq": None}, "foo")

    def test_eq_float_wrapped_with_step_name(self):
        with pytest.raises(ValueError, match="step 'foo'.*float literals banned"):
            _parse_when({"input": "x", "eq": 5.0}, "foo")

    def test_eq_bool_wrapped_with_step_name(self):
        with pytest.raises(ValueError, match="step 'foo'.*cannot be a bool"):
            _parse_when({"input": "x", "eq": True}, "foo")


# ─── Mixed flow integration via load_workflow_yaml ────────────────────────


class TestMixedFlow:
    def test_mixed_string_and_predicate_loads_cleanly(self, tmp_path):
        flow_yaml = """\
name: mixed-when
author: test
steps:
  fetch:
    run: |
      echo '{"status": "pass"}'
    outputs: [status]
  open-pr:
    inputs:
      status: fetch.status
    when: "status == 'pass'"
    run: 'echo "{}"'
    outputs: [pr_url]
  fix-it:
    inputs:
      status: fetch.status
    when:
      input: status
      eq: fail
    run: 'echo "{}"'
    outputs: [fix]
"""
        f = tmp_path / "mixed.flow.yaml"
        f.write_text(flow_yaml)
        wf = load_workflow_yaml(f)
        assert isinstance(wf.steps["open-pr"].when, str)
        assert wf.steps["open-pr"].when == "status == 'pass'"
        assert isinstance(wf.steps["fix-it"].when, WhenPredicate)
        assert wf.steps["fix-it"].when.input == "status"
        assert wf.steps["fix-it"].when.op == "eq"
        assert wf.steps["fix-it"].when.value == "fail"

    def test_predicate_in_with_yaml_list(self, tmp_path):
        flow_yaml = """\
name: in-list
author: test
steps:
  fetch:
    run: |
      echo '{"code": 200}'
    outputs: [code]
  ok:
    inputs:
      code: fetch.code
    when:
      input: code
      in: [200, 201, 204]
    run: 'echo "{}"'
    outputs: [done]
"""
        f = tmp_path / "in.flow.yaml"
        f.write_text(flow_yaml)
        wf = load_workflow_yaml(f)
        pred = wf.steps["ok"].when
        assert isinstance(pred, WhenPredicate)
        assert pred.op == "in"
        assert pred.value == (200, 201, 204)
