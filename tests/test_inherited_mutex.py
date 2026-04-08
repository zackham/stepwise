"""Tests for §7.3 inherited_mutex (validator/mhb.py).

Verifies the canonical conditional fork-rejoin pattern and self-inclusion
of each step in its own ancestor set.
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
    compute_mhb_strict_ancestors,
    inherited_mutex,
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


def test_conditional_fork_rejoin_canonical():
    """Two mutex chain roots branch_a and branch_b. cont_a follows branch_a
    via after-edge. inherited_mutex(branch_b, cont_a) should be True
    because branch_a is a strict ancestor of cont_a and branch_a × branch_b
    is mutex_when_proved.
    """
    pred_a = WhenPredicate(input="route", op="eq", value="a")
    pred_b = WhenPredicate(input="route", op="eq", value="b")
    branch_a = _step(
        "branch_a",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when=pred_a,
    )
    branch_b = _step(
        "branch_b",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when=pred_b,
    )
    cont_a = _step("cont_a", after=["branch_a"])
    flow = _flow(_step("R"), branch_a, branch_b, cont_a)

    strict = compute_mhb_strict_ancestors(flow)
    assert "branch_a" in strict["cont_a"]

    assert inherited_mutex(flow, branch_b, cont_a, strict) is True
    # Symmetric (cont_a vs branch_b)
    assert inherited_mutex(flow, cont_a, branch_b, strict) is True


def test_unrelated_steps_returns_false():
    """Two unrelated steps, no mutex predicates → False."""
    A = _step("A")
    B = _step("B")
    flow = _flow(A, B)
    strict = compute_mhb_strict_ancestors(flow)
    assert inherited_mutex(flow, A, B, strict) is False


def test_self_inclusion_in_ancestors():
    """Each step is its own mhb_strict-ancestor: inherited_mutex(X, Y) checks
    mutex_when_proved(X, Y) directly even when neither has prior strict
    ancestors.
    """
    pred_a = WhenPredicate(input="route", op="eq", value="a")
    pred_b = WhenPredicate(input="route", op="eq", value="b")
    M1 = _step(
        "M1",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when=pred_a,
    )
    M2 = _step(
        "M2",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when=pred_b,
    )
    flow = _flow(_step("R"), M1, M2)
    strict = compute_mhb_strict_ancestors(flow)
    # Neither M1 nor M2 has the other as a strict ancestor — but each is
    # its own ancestor, so mutex_when_proved(M1, M2) is checked directly.
    assert inherited_mutex(flow, M1, M2, strict) is True


def test_inherited_via_transitive_strict_ancestor():
    """branch_a → mid → cont. inherited_mutex(branch_b, cont) should be
    True because branch_a is a transitive strict ancestor of cont.
    """
    pred_a = WhenPredicate(input="route", op="eq", value="a")
    pred_b = WhenPredicate(input="route", op="eq", value="b")
    branch_a = _step(
        "branch_a",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when=pred_a,
    )
    branch_b = _step(
        "branch_b",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when=pred_b,
    )
    mid = _step("mid", after=["branch_a"])
    cont = _step("cont", after=["mid"])
    flow = _flow(_step("R"), branch_a, branch_b, mid, cont)
    strict = compute_mhb_strict_ancestors(flow)
    assert "branch_a" in strict["cont"]
    assert inherited_mutex(flow, branch_b, cont, strict) is True


def test_no_inheritance_when_strict_excludes_mutex_anchor():
    """If the only path from branch_a to cont is via clause (e) (mutex-complete
    fan-in), branch_a is in mhb but NOT in mhb_strict, so inherited_mutex
    must return False — there's no positively-known executed branch_a in
    cont's strict-ancestor set.
    """
    pred_a = WhenPredicate(input="route", op="eq", value="a")
    pred_b = WhenPredicate(input="route", op="eq", value="b")
    branch_a = _step(
        "branch_a",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when=pred_a,
    )
    branch_b = _step(
        "branch_b",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when=pred_b,
    )
    # cont consumes any_of(branch_a, branch_b) — branch_a is in mhb via
    # clause (e) but not in mhb_strict.
    cont = _step("cont", inputs=[InputBinding(
        local_name="result",
        source_step="",
        source_field="",
        any_of_sources=[("branch_a", "branch_a_out"), ("branch_b", "branch_b_out")],
    )])
    flow = _flow(_step("R"), branch_a, branch_b, cont)
    strict = compute_mhb_strict_ancestors(flow)
    assert "branch_a" not in strict["cont"]
    assert "branch_b" not in strict["cont"]
    # cont itself doesn't have a when:, so cont × branch_b is not mutex
    # via mutex_when_proved(cont, branch_b). The only mutex-prove pair
    # would be branch_a × branch_b, but branch_a is not in cont's strict
    # ancestor set. So inherited_mutex(branch_b, cont) → False.
    assert inherited_mutex(flow, branch_b, cont, strict) is False
