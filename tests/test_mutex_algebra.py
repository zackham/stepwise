"""Tests for §6 predicate mutex algebra (validator/mutex.py).

Parameterized over all 18 rows of the §6.1 truth table, both directions
for symmetry. Plus type-strictness regression tests.
"""

from __future__ import annotations

import pytest

from stepwise.models import WhenPredicate
from stepwise.validator.mutex import (
    _set_disjoint,
    _strict_neq,
    _value_in_list,
    predicates_mutex,
)


def W(op: str, value, input_name: str = "x") -> WhenPredicate:
    return WhenPredicate(input=input_name, op=op, value=value)


# ─── §6.1 18-row truth table ──────────────────────────────────────────────


# Each row: (predicate_a, predicate_b, expected_mutex)
TRUTH_TABLE = [
    # eq vs eq
    ("eq a == eq a", W("eq", "a"), W("eq", "a"), False),
    ("eq a == eq b", W("eq", "a"), W("eq", "b"), True),
    # eq vs in
    ("eq a in [a,b]", W("eq", "a"), W("in", ("a", "b")), False),
    ("eq c in [a,b]", W("eq", "c"), W("in", ("a", "b")), True),
    # in vs in
    ("in [a,b] disjoint [c,d]", W("in", ("a", "b")), W("in", ("c", "d")), True),
    ("in [a,b] overlap [b,c]", W("in", ("a", "b")), W("in", ("b", "c")), False),
    ("in identical", W("in", ("a", "b")), W("in", ("a", "b")), False),
    # eq vs is_null
    ("eq a × is_null:true", W("eq", "a"), W("is_null", True), True),
    ("eq a × is_null:false", W("eq", "a"), W("is_null", False), False),
    # in vs is_null
    ("in × is_null:true", W("in", ("a", "b")), W("is_null", True), True),
    ("in × is_null:false", W("in", ("a", "b")), W("is_null", False), False),
    # is_null vs is_null
    ("is_null:true × is_null:false", W("is_null", True), W("is_null", False), True),
    ("is_null:true × is_null:true", W("is_null", True), W("is_null", True), False),
    ("is_null:false × is_null:false", W("is_null", False), W("is_null", False), False),
    # eq vs is_present
    ("eq × is_present:false", W("eq", "a"), W("is_present", False), True),
    ("eq × is_present:true", W("eq", "a"), W("is_present", True), False),
    # in vs is_present
    ("in × is_present:false", W("in", ("a",)), W("is_present", False), True),
    ("in × is_present:true", W("in", ("a",)), W("is_present", True), False),
    # is_null vs is_present
    ("is_null:true × is_present:false", W("is_null", True), W("is_present", False), True),
    ("is_null:false × is_present:false", W("is_null", False), W("is_present", False), True),
    ("is_null:true × is_present:true", W("is_null", True), W("is_present", True), False),
    ("is_null:false × is_present:true", W("is_null", False), W("is_present", True), False),
    # is_present vs is_present
    ("is_present:true × is_present:false", W("is_present", True), W("is_present", False), True),
    ("is_present:true × is_present:true", W("is_present", True), W("is_present", True), False),
    ("is_present:false × is_present:false", W("is_present", False), W("is_present", False), False),
]


class TestTruthTable:
    @pytest.mark.parametrize("name,a,b,expected", TRUTH_TABLE,
                             ids=[r[0] for r in TRUTH_TABLE])
    def test_forward(self, name, a, b, expected):
        assert predicates_mutex(a, b) is expected

    @pytest.mark.parametrize("name,a,b,expected", TRUTH_TABLE,
                             ids=[r[0] for r in TRUTH_TABLE])
    def test_symmetry(self, name, a, b, expected):
        # Reversing the argument order must give the same answer.
        assert predicates_mutex(b, a) is expected


# ─── Type-strictness regressions (§5.5) ───────────────────────────────────


class TestTypeStrictness:
    def test_eq_int_vs_eq_str_is_mutex(self):
        # eq: 5 vs eq: '5' — different types under strict-type semantics
        a = W("eq", 5)
        b = W("eq", "5")
        assert predicates_mutex(a, b) is True

    def test_eq_str_vs_eq_int_is_mutex(self):
        a = W("eq", "200")
        b = W("eq", 200)
        assert predicates_mutex(a, b) is True

    def test_eq_int_in_str_list_is_mutex(self):
        # eq: 5 vs in: ['5'] — strict-type → mutex
        a = W("eq", 5)
        b = W("in", ("5",))
        assert predicates_mutex(a, b) is True

    def test_eq_str_in_int_list_is_mutex(self):
        a = W("eq", "200")
        b = W("in", (200,))
        assert predicates_mutex(a, b) is True

    def test_in_int_disjoint_in_str(self):
        a = W("in", (1, 2, 3))
        b = W("in", ("1", "2", "3"))
        assert predicates_mutex(a, b) is True

    def test_in_overlap_same_type(self):
        a = W("in", (1, 2, 3))
        b = W("in", (3, 4, 5))
        assert predicates_mutex(a, b) is False


# ─── Helper unit tests ────────────────────────────────────────────────────


class TestStrictNeq:
    def test_same_type_same_value(self):
        assert _strict_neq("a", "a") is False

    def test_same_type_diff_value(self):
        assert _strict_neq("a", "b") is True

    def test_diff_type(self):
        assert _strict_neq(5, "5") is True

    def test_int_vs_int(self):
        assert _strict_neq(5, 5) is False
        assert _strict_neq(5, 6) is True


class TestValueInList:
    def test_present(self):
        assert _value_in_list("a", ("a", "b")) is True

    def test_absent(self):
        assert _value_in_list("c", ("a", "b")) is False

    def test_strict_type(self):
        assert _value_in_list(5, ("5",)) is False
        assert _value_in_list("5", (5,)) is False


class TestSetDisjoint:
    def test_disjoint(self):
        assert _set_disjoint(("a", "b"), ("c", "d")) is True

    def test_overlap(self):
        assert _set_disjoint(("a", "b"), ("b", "c")) is False

    def test_identical(self):
        assert _set_disjoint(("a",), ("a",)) is False

    def test_empty_left(self):
        # Empty-in is banned at parse time, but defend in depth.
        assert _set_disjoint((), ("a",)) is True

    def test_empty_right(self):
        assert _set_disjoint(("a",), ()) is True

    def test_strict_type_disjoint(self):
        assert _set_disjoint((5,), ("5",)) is True
