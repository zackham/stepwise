"""Tests for §7.2 compute_mhb_ancestors (validator/mhb.py).

Covers all five clauses (a)/(b)/(c)/(d)/(e), back-edge exclusion via the
side-table parameter, and the council pass 5 optional-binding case.
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


# ─── Clause (a): direct edges ─────────────────────────────────────────────


def test_mhb_direct_after_edge():
    """A → B via B.after = ['A']."""
    flow = _flow(_step("A"), _step("B", after=["A"]))
    anc = compute_mhb_ancestors(flow)
    assert anc["A"] == set()
    assert anc["B"] == {"A"}


def test_mhb_direct_input_edge():
    """A → B via B.inputs binding to A.result."""
    flow = _flow(
        _step("A"),
        _step("B", inputs=[InputBinding(local_name="x", source_step="A", source_field="A_out")]),
    )
    anc = compute_mhb_ancestors(flow)
    assert anc["B"] == {"A"}


def test_mhb_input_from_job_not_a_dep():
    """An input from $job is not a step dep."""
    flow = _flow(
        _step("A", inputs=[InputBinding(local_name="x", source_step="$job", source_field="param")]),
    )
    anc = compute_mhb_ancestors(flow)
    assert anc["A"] == set()


# ─── Clause (c): transitivity ─────────────────────────────────────────────


def test_mhb_transitivity():
    """A → B → C: C must have both A and B as ancestors."""
    flow = _flow(
        _step("A"),
        _step("B", after=["A"]),
        _step("C", after=["B"]),
    )
    anc = compute_mhb_ancestors(flow)
    assert anc["C"] == {"A", "B"}


def test_mhb_transitivity_long_chain():
    flow = _flow(
        _step("A"),
        _step("B", after=["A"]),
        _step("C", after=["B"]),
        _step("D", after=["C"]),
        _step("E", after=["D"]),
    )
    anc = compute_mhb_ancestors(flow)
    assert anc["E"] == {"A", "B", "C", "D"}


# ─── Back-edge exclusion ──────────────────────────────────────────────────


def test_mhb_back_edge_excluded():
    """Back-edge from B to A: A should NOT appear in B's ancestors."""
    flow = _flow(_step("A"), _step("B", after=["A"]))
    anc = compute_mhb_ancestors(flow, back_edges={("B", "A")})
    assert anc["B"] == set()


def test_mhb_back_edge_input_excluded():
    """Back-edge input binding excluded."""
    flow = _flow(
        _step("A"),
        _step("B", inputs=[InputBinding(local_name="x", source_step="A", source_field="A_out")]),
    )
    anc = compute_mhb_ancestors(flow, back_edges={("B", "A")})
    assert anc["B"] == set()


# ─── Clause (d): universal-prefix input any_of ────────────────────────────


def test_mhb_universal_prefix_input_any_of():
    """A is a common ancestor of M1 and M2; C consumes any_of(M1, M2).
    A must be in mhb(C) by clause (d), but M1/M2 are not (clause (e) doesn't
    fire because M1/M2 are not pairwise mutex).
    """
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
    anc = compute_mhb_ancestors(flow)
    assert "A" in anc["C"]
    # M1, M2 are not mutex (no when:) → clause (e) doesn't fire → not in mhb
    assert "M1" not in anc["C"]
    assert "M2" not in anc["C"]


# ─── Clause (e): mutex-complete fan-in (input any_of) ─────────────────────


def test_mhb_mutex_complete_fanin_input_any_of():
    """M1 and M2 mutex via predicate-form when: on a shared input from R.
    C consumes any_of(M1, M2). Clause (e) → both M1 and M2 in mhb(C).
    Clause (d) → R in mhb(C).
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
    anc = compute_mhb_ancestors(flow)
    assert "M1" in anc["C"]
    assert "M2" in anc["C"]
    assert "R" in anc["C"]


# ─── Clause (b): universal-prefix after.any_of ────────────────────────────


def test_mhb_universal_prefix_after_any_of():
    """A → M1, A → M2; C has after_any_of=[[M1,M2]]. A must be in mhb(C)."""
    flow = _flow(
        _step("A"),
        _step("M1", after=["A"]),
        _step("M2", after=["A"]),
        _step("C", after_any_of=[["M1", "M2"]]),
    )
    anc = compute_mhb_ancestors(flow)
    assert "A" in anc["C"]
    # M1, M2 not mutex → not in mhb(C) via clause (e)
    assert "M1" not in anc["C"]
    assert "M2" not in anc["C"]


# ─── Clause (e): mutex-complete fan-in (after.any_of) ─────────────────────


def test_mhb_mutex_complete_fanin_after_any_of():
    """M1 mutex M2 via shared input from R; C has after_any_of=[[M1,M2]].
    Clause (e) → both M1 and M2 in mhb(C).
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
        _step("C", after_any_of=[["M1", "M2"]]),
    )
    anc = compute_mhb_ancestors(flow)
    assert "M1" in anc["C"]
    assert "M2" in anc["C"]


# ─── Council pass 5 bug 1: optional input ─────────────────────────────────


def test_mhb_optional_input_included_in_mhb_but_excluded_from_strict():
    """Optional input bindings DO contribute to mhb but NOT to mhb_strict."""
    flow = _flow(
        _step("A"),
        _step(
            "B",
            inputs=[InputBinding(
                local_name="x",
                source_step="A",
                source_field="A_out",
                optional=True,
            )],
        ),
    )
    anc = compute_mhb_ancestors(flow)
    strict = compute_mhb_strict_ancestors(flow)
    assert "A" in anc["B"]
    assert "A" not in strict["B"]
