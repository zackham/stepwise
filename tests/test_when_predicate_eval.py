"""Tests for §5.5 strict-type evaluator (validator/mutex.py).

Verifies evaluate_when_predicate semantics: type-strict eq, type-strict
in-membership, is_null:true/false with key-missing handling, and
is_present runtime-rejection (parse-time should catch this, but defend
in depth).
"""

from __future__ import annotations

import pytest

from stepwise.models import WhenPredicate
from stepwise.validator.mutex import evaluate_when_predicate


# ─── eq: strict-type ──────────────────────────────────────────────────────


class TestEqStrictType:
    def test_int_5_vs_5(self):
        pred = WhenPredicate(input="x", op="eq", value=5)
        assert evaluate_when_predicate(pred, {"x": 5}) is True

    def test_int_5_vs_5_0_is_false(self):
        pred = WhenPredicate(input="x", op="eq", value=5)
        assert evaluate_when_predicate(pred, {"x": 5.0}) is False

    def test_int_5_vs_str_5_is_false(self):
        pred = WhenPredicate(input="x", op="eq", value=5)
        assert evaluate_when_predicate(pred, {"x": "5"}) is False

    def test_int_1_vs_runtime_true_is_false(self):
        # bool is subclass of int but type() differs.
        pred = WhenPredicate(input="x", op="eq", value=1)
        assert evaluate_when_predicate(pred, {"x": True}) is False

    def test_str_eq_match(self):
        pred = WhenPredicate(input="status", op="eq", value="pass")
        assert evaluate_when_predicate(pred, {"status": "pass"}) is True

    def test_str_eq_mismatch(self):
        pred = WhenPredicate(input="status", op="eq", value="pass")
        assert evaluate_when_predicate(pred, {"status": "fail"}) is False

    def test_eq_missing_key_is_false(self):
        pred = WhenPredicate(input="status", op="eq", value="pass")
        assert evaluate_when_predicate(pred, {}) is False

    def test_eq_none_runtime_value_is_false(self):
        pred = WhenPredicate(input="status", op="eq", value="pass")
        assert evaluate_when_predicate(pred, {"status": None}) is False


# ─── in: strict-type ──────────────────────────────────────────────────────


class TestInStrictType:
    def test_str_in_match(self):
        pred = WhenPredicate(input="status", op="in", value=("a", "b"))
        assert evaluate_when_predicate(pred, {"status": "a"}) is True

    def test_str_in_no_match(self):
        pred = WhenPredicate(input="status", op="in", value=("a", "b"))
        assert evaluate_when_predicate(pred, {"status": "c"}) is False

    def test_int_in_match(self):
        pred = WhenPredicate(input="code", op="in", value=(200, 201))
        assert evaluate_when_predicate(pred, {"code": 200}) is True

    def test_int_in_str_list_no_match(self):
        # Strict type: int 200 not in tuple of strs
        pred = WhenPredicate(input="code", op="in", value=("200", "201"))
        assert evaluate_when_predicate(pred, {"code": 200}) is False

    def test_in_missing_key_is_false(self):
        pred = WhenPredicate(input="x", op="in", value=("a",))
        assert evaluate_when_predicate(pred, {}) is False


# ─── is_null ──────────────────────────────────────────────────────────────


class TestIsNull:
    def test_is_null_true_with_none(self):
        pred = WhenPredicate(input="error", op="is_null", value=True)
        assert evaluate_when_predicate(pred, {"error": None}) is True

    def test_is_null_true_with_missing_key_is_false(self):
        pred = WhenPredicate(input="error", op="is_null", value=True)
        assert evaluate_when_predicate(pred, {}) is False

    def test_is_null_true_with_zero_is_false(self):
        pred = WhenPredicate(input="error", op="is_null", value=True)
        assert evaluate_when_predicate(pred, {"error": 0}) is False

    def test_is_null_true_with_empty_str_is_false(self):
        pred = WhenPredicate(input="error", op="is_null", value=True)
        assert evaluate_when_predicate(pred, {"error": ""}) is False

    def test_is_null_false_with_none_is_false(self):
        pred = WhenPredicate(input="error", op="is_null", value=False)
        assert evaluate_when_predicate(pred, {"error": None}) is False

    def test_is_null_false_with_present_value_is_true(self):
        pred = WhenPredicate(input="error", op="is_null", value=False)
        assert evaluate_when_predicate(pred, {"error": "boom"}) is True

    def test_is_null_false_with_zero_is_true(self):
        pred = WhenPredicate(input="error", op="is_null", value=False)
        assert evaluate_when_predicate(pred, {"error": 0}) is True

    def test_is_null_false_with_missing_key_is_false(self):
        pred = WhenPredicate(input="error", op="is_null", value=False)
        assert evaluate_when_predicate(pred, {}) is False


# ─── Step 7: is_present: now reads from presence map ─────────────────────


class TestIsPresentNowSupported:
    def test_is_present_true_with_presence_true(self):
        pred = WhenPredicate(input="x", op="is_present", value=True)
        assert evaluate_when_predicate(pred, {"x": "v"}, presence={"x": True}) is True

    def test_is_present_true_with_presence_false(self):
        pred = WhenPredicate(input="x", op="is_present", value=True)
        assert evaluate_when_predicate(pred, {}, presence={"x": False}) is False

    def test_is_present_false_with_presence_false(self):
        pred = WhenPredicate(input="x", op="is_present", value=False)
        assert evaluate_when_predicate(pred, {}, presence={"x": False}) is True

    def test_is_present_false_with_presence_true(self):
        pred = WhenPredicate(input="x", op="is_present", value=False)
        assert evaluate_when_predicate(pred, {"x": "v"}, presence={"x": True}) is False

    def test_is_present_without_presence_arg_uses_inputs_keys(self):
        # Backwards-compat: legacy callers without a presence map fall
        # back to "presence = key in inputs".
        pred = WhenPredicate(input="x", op="is_present", value=True)
        assert evaluate_when_predicate(pred, {"x": "v"}) is True
        assert evaluate_when_predicate(pred, {}) is False

    def test_is_null_requires_presence(self):
        # §11.3 truth table: is_null: true does NOT match the absent state.
        pred_t = WhenPredicate(input="x", op="is_null", value=True)
        assert evaluate_when_predicate(pred_t, {}, presence={"x": False}) is False
        # Present-with-None matches
        assert evaluate_when_predicate(pred_t, {"x": None}, presence={"x": True}) is True

    def test_eq_requires_presence(self):
        pred = WhenPredicate(input="x", op="eq", value="a")
        assert evaluate_when_predicate(pred, {}, presence={"x": False}) is False
        assert evaluate_when_predicate(pred, {"x": "a"}, presence={"x": True}) is True
