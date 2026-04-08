"""§6 Mutex algebra + §5.5 strict-type evaluator for predicate-form when:.

Direct port of scripts/stepwise_fuzzer/spec.py:46-100 from the vita repo,
which is the fuzzer-certified reference implementation. Phase 2 verifies
this port against the fuzzer reference via an out-of-tree differential
check. Do NOT re-derive these helpers from §6.1 by hand — keep them in
lockstep with the fuzzer.
"""

from __future__ import annotations

from stepwise.models import WhenPredicate


# ─── §6 Mutex algebra ─────────────────────────────────────────────────────


def predicates_mutex(p1: WhenPredicate, p2: WhenPredicate) -> bool:
    """Are two predicate-form when: clauses provably mutually exclusive?

    Per §6.1. Both clauses must be predicate-form. The caller must verify
    they reference the same input binding (which must resolve to the same
    upstream producer/field) — that's §6.2 common-ancestor and is_not_any_of
    handled in pair_check (step 3), not here.
    """
    # Normalize order so we only handle one direction per pair
    pairs = [(p1, p2), (p2, p1)]
    for a, b in pairs:
        if a.op == "eq" and b.op == "eq":
            return _strict_neq(a.value, b.value)
        if a.op == "eq" and b.op == "in":
            return not _value_in_list(a.value, b.value)  # type: ignore[arg-type]
        if a.op == "in" and b.op == "in":
            return _set_disjoint(a.value, b.value)  # type: ignore[arg-type]
        if a.op == "eq" and b.op == "is_null" and b.value is True:
            return True
        if a.op == "in" and b.op == "is_null" and b.value is True:
            return True
        if a.op == "is_null" and b.op == "is_null":
            return a.value != b.value   # is_null:true vs is_null:false
        if a.op == "eq" and b.op == "is_present" and b.value is False:
            return True
        if a.op == "in" and b.op == "is_present" and b.value is False:
            return True
        if a.op == "is_null" and b.op == "is_present":
            if b.value is False:   # is_null × is_present:false → always
                return True
            if a.value is True:    # is_null:true × is_present:true → never (present-and-null)
                return False
            return False
        if a.op == "is_present" and b.op == "is_present":
            return a.value != b.value
    return False


def _strict_neq(a: object, b: object) -> bool:
    """Strict-type comparison per §5.5: type(a) is type(b) and a == b."""
    return not (type(a) is type(b) and a == b)


def _value_in_list(v: object, lst: tuple) -> bool:
    """Strict-type membership check."""
    return any(type(v) is type(x) and v == x for x in lst)


def _set_disjoint(s1: tuple, s2: tuple) -> bool:
    """Are two literal lists disjoint under strict-type equality?"""
    for a in s1:
        for b in s2:
            if type(a) is type(b) and a == b:
                return False
    return True


# ─── §5.5 strict-type evaluator ───────────────────────────────────────────


_SENTINEL = object()


def evaluate_when_predicate(
    pred: WhenPredicate,
    inputs: dict,
    presence: dict[str, bool] | None = None,
) -> bool:
    """Evaluate a predicate-form when: clause against resolved inputs.

    Strict-type semantics per §5.5 plus §11.3 presence rules (step 7):
      - eq: presence must be True AND runtime value must match by strict
        type and value.
      - in: presence must be True AND runtime value must match at least
        one tuple element by strict-type equality.
      - is_null: true → presence True AND runtime value is None.
      - is_null: false → presence True AND runtime value is not None.
      - is_present: true → presence is True (binding has produced a value
        in the current iteration of its closing loop).
      - is_present: false → presence is False (loop-back binding on iter-1,
        or any other absent state).

    The ``presence`` parameter is a per-binding map produced by
    ``Engine._resolve_inputs``. If the caller doesn't provide it (legacy
    callers, or unit tests), presence defaults to ``key in inputs`` for
    backwards compatibility.
    """
    if presence is None:
        # Backwards-compat fallback: derive presence from key membership.
        presence = {k: True for k in inputs.keys()}

    runtime_value = inputs.get(pred.input, _SENTINEL)
    key_present = runtime_value is not _SENTINEL
    pres = presence.get(pred.input, False)

    if pred.op == "eq":
        if not pres or not key_present:
            return False
        return type(runtime_value) is type(pred.value) and runtime_value == pred.value

    if pred.op == "in":
        if not pres or not key_present:
            return False
        return _value_in_list(runtime_value, pred.value)  # type: ignore[arg-type]

    if pred.op == "is_null":
        if pred.value is True:
            # §11.3: is_null: true requires presence (does NOT match absent).
            return pres and key_present and runtime_value is None
        # is_null: false → presence True AND value is not None.
        return pres and key_present and runtime_value is not None

    if pred.op == "is_present":
        # §11.3: is_present: <bool> reads directly from the presence map.
        return bool(pres) == bool(pred.value)

    raise ValueError(f"unknown when.op: {pred.op!r}")
