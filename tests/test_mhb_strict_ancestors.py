"""Tests for §7.2.1 compute_mhb_strict_ancestors (validator/mhb.py).

Verifies positive-enumeration semantics: clauses (a')/(b')/(c')/(d')
contribute, clause (e') (mutex-complete fan-in) is intentionally
excluded, optional bindings are excluded, back-edges are excluded.
"""

from __future__ import annotations

from stepwise.models import (
    ExecutorRef,
    InputBinding,
    StepDefinition,
    WhenPredicate,
    WorkflowDefinition,
)
from stepwise.validator.mhb import (
    compute_mhb_ancestors,
    compute_mhb_strict_ancestors,
)


def _step(name: str, **kwargs) -> StepDefinition:
    return StepDefinition(
        name=name,
        outputs=kwargs.pop("outputs", [name + "_out"]),
        executor=kwargs.pop("executor", ExecutorRef("script", {})),
        **kwargs,
    )


def _flow(*steps: StepDefinition) -> WorkflowDefinition:
    return WorkflowDefinition(steps={s.name: s for s in steps})


# ─── (a') direct edges ────────────────────────────────────────────────────


def test_strict_direct_after_edge():
    flow = _flow(_step("A"), _step("B", after=["A"]))
    strict = compute_mhb_strict_ancestors(flow)
    assert strict["B"] == {"A"}


def test_strict_direct_required_input():
    flow = _flow(
        _step("A"),
        _step("B", inputs=[InputBinding(local_name="x", source_step="A", source_field="A_out")]),
    )
    strict = compute_mhb_strict_ancestors(flow)
    assert strict["B"] == {"A"}


def test_strict_optional_input_excluded():
    """Optional inputs are NOT in mhb_strict (only in mhb)."""
    flow = _flow(
        _step("A"),
        _step("B", inputs=[InputBinding(
            local_name="x",
            source_step="A",
            source_field="A_out",
            optional=True,
        )]),
    )
    strict = compute_mhb_strict_ancestors(flow)
    assert "A" not in strict["B"]


def test_strict_back_edge_excluded():
    flow = _flow(_step("A"), _step("B", after=["A"]))
    strict = compute_mhb_strict_ancestors(flow, back_edges={("B", "A")})
    assert strict["B"] == set()


def test_strict_input_back_edge_excluded():
    flow = _flow(
        _step("A"),
        _step("B", inputs=[InputBinding(local_name="x", source_step="A", source_field="A_out")]),
    )
    strict = compute_mhb_strict_ancestors(flow, back_edges={("B", "A")})
    assert strict["B"] == set()


# ─── (c') transitivity ────────────────────────────────────────────────────


def test_strict_transitivity():
    flow = _flow(
        _step("A"),
        _step("B", after=["A"]),
        _step("C", after=["B"]),
    )
    strict = compute_mhb_strict_ancestors(flow)
    assert strict["C"] == {"A", "B"}


# ─── (b') universal-prefix after.any_of ───────────────────────────────────


def test_strict_universal_prefix_after_any_of():
    """A is the strict ancestor of M1 and M2; C has after_any_of=[[M1,M2]]."""
    flow = _flow(
        _step("A"),
        _step("M1", after=["A"]),
        _step("M2", after=["A"]),
        _step("C", after_any_of=[["M1", "M2"]]),
    )
    strict = compute_mhb_strict_ancestors(flow)
    assert "A" in strict["C"]


# ─── (d') universal-prefix input any_of ───────────────────────────────────


def test_strict_universal_prefix_input_any_of():
    flow = _flow(
        _step("A"),
        _step("M1", after=["A"]),
        _step("M2", after=["A"]),
        _step("C", inputs=[InputBinding(
            local_name="x",
            source_step="",
            source_field="",
            any_of_sources=[("M1", "M1_out"), ("M2", "M2_out")],
        )]),
    )
    strict = compute_mhb_strict_ancestors(flow)
    assert "A" in strict["C"]


# ─── (e') NOT included — council pass 4 soundness case ───────────────────


def test_strict_mutex_complete_fanin_NOT_included_input_any_of():
    """M1 mutex M2 via predicates on a shared input from R.
    C consumes any_of(M1, M2). M1 and M2 ARE in mhb(C) (clause (e)) but
    are NOT in mhb_strict(C) (clause (e') excluded).
    """
    pred_a = WhenPredicate(input="route", op="eq", value="a")
    pred_b = WhenPredicate(input="route", op="eq", value="b")
    flow = _flow(
        _step("R"),
        _step(
            "M1",
            inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
            when=pred_a,
        ),
        _step(
            "M2",
            inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
            when=pred_b,
        ),
        _step("C", inputs=[InputBinding(
            local_name="result",
            source_step="",
            source_field="",
            any_of_sources=[("M1", "M1_out"), ("M2", "M2_out")],
        )]),
    )
    mhb = compute_mhb_ancestors(flow)
    strict = compute_mhb_strict_ancestors(flow)
    # mhb (clause e) includes M1 and M2
    assert "M1" in mhb["C"]
    assert "M2" in mhb["C"]
    # mhb_strict (clause e' excluded) does NOT
    assert "M1" not in strict["C"]
    assert "M2" not in strict["C"]


def test_strict_mutex_complete_fanin_NOT_included_after_any_of():
    """Same as above but via after_any_of instead of input any_of."""
    pred_a = WhenPredicate(input="route", op="eq", value="a")
    pred_b = WhenPredicate(input="route", op="eq", value="b")
    flow = _flow(
        _step("R"),
        _step(
            "M1",
            inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
            when=pred_a,
        ),
        _step(
            "M2",
            inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
            when=pred_b,
        ),
        _step("C", after_any_of=[["M1", "M2"]]),
    )
    mhb = compute_mhb_ancestors(flow)
    strict = compute_mhb_strict_ancestors(flow)
    assert "M1" in mhb["C"]
    assert "M2" in mhb["C"]
    assert "M1" not in strict["C"]
    assert "M2" not in strict["C"]
