"""Tests for §6.2 mutex_when_proved (validator/mhb.py).

Verifies the same-input + same-producer + non-any_of + predicates_mutex
guard chain.
"""

from __future__ import annotations

from stepwise.models import (
    ExecutorRef,
    InputBinding,
    StepDefinition,
    WhenPredicate,
    WorkflowDefinition,
)
from stepwise.validator.mhb import mutex_when_proved


def _step(name: str, **kwargs) -> StepDefinition:
    return StepDefinition(
        name=name,
        outputs=kwargs.pop("outputs", [name + "_out"]),
        executor=kwargs.pop("executor", ExecutorRef("script", {})),
        **kwargs,
    )


def _flow(*steps: StepDefinition) -> WorkflowDefinition:
    return WorkflowDefinition(steps={s.name: s for s in steps})


def test_same_ancestor_eq_disjoint_proves_mutex():
    """Both M1 and M2 reference R.route via local input 'route', with
    disjoint eq predicates → mutex.
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
    assert mutex_when_proved(flow, M1, M2) is True


def test_same_input_in_disjoint_proves_mutex():
    """eq vs in (disjoint) → mutex."""
    M1 = _step(
        "M1",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when=WhenPredicate(input="route", op="eq", value="a"),
    )
    M2 = _step(
        "M2",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when=WhenPredicate(input="route", op="in", value=("b", "c")),
    )
    flow = _flow(_step("R"), M1, M2)
    assert mutex_when_proved(flow, M1, M2) is True


def test_predicates_not_mutex_returns_false():
    """Same input, same producer, eq:'a' × eq:'a' → predicates not mutex."""
    pred = WhenPredicate(input="route", op="eq", value="a")
    M1 = _step(
        "M1",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when=pred,
    )
    M2 = _step(
        "M2",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when=WhenPredicate(input="route", op="eq", value="a"),
    )
    flow = _flow(_step("R"), M1, M2)
    assert mutex_when_proved(flow, M1, M2) is False


def test_different_input_names_rejected():
    """Different local input names → False even if both are eq with disjoint values."""
    M1 = _step(
        "M1",
        inputs=[InputBinding(local_name="x", source_step="R", source_field="x_out")],
        when=WhenPredicate(input="x", op="eq", value="a"),
    )
    M2 = _step(
        "M2",
        inputs=[InputBinding(local_name="y", source_step="R", source_field="y_out")],
        when=WhenPredicate(input="y", op="eq", value="b"),
    )
    flow = _flow(_step("R"), M1, M2)
    assert mutex_when_proved(flow, M1, M2) is False


def test_different_producer_rejected():
    """Same local input name but resolves to different producers → False."""
    M1 = _step(
        "M1",
        inputs=[InputBinding(local_name="route", source_step="R1", source_field="route_out")],
        when=WhenPredicate(input="route", op="eq", value="a"),
    )
    M2 = _step(
        "M2",
        inputs=[InputBinding(local_name="route", source_step="R2", source_field="route_out")],
        when=WhenPredicate(input="route", op="eq", value="b"),
    )
    flow = _flow(_step("R1"), _step("R2"), M1, M2)
    assert mutex_when_proved(flow, M1, M2) is False


def test_different_producer_field_rejected():
    """Same producer but different field → False."""
    M1 = _step(
        "M1",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="field_a")],
        when=WhenPredicate(input="route", op="eq", value="a"),
    )
    M2 = _step(
        "M2",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="field_b")],
        when=WhenPredicate(input="route", op="eq", value="b"),
    )
    flow = _flow(_step("R"), M1, M2)
    assert mutex_when_proved(flow, M1, M2) is False


def test_any_of_input_excluded():
    """If the witness binding is an any_of, mutex_when_proved → False (§6.2)."""
    M1 = _step(
        "M1",
        inputs=[InputBinding(
            local_name="route",
            source_step="",
            source_field="",
            any_of_sources=[("R1", "x"), ("R2", "x")],
        )],
        when=WhenPredicate(input="route", op="eq", value="a"),
    )
    M2 = _step(
        "M2",
        inputs=[InputBinding(local_name="route", source_step="R1", source_field="x")],
        when=WhenPredicate(input="route", op="eq", value="b"),
    )
    flow = _flow(_step("R1"), _step("R2"), M1, M2)
    assert mutex_when_proved(flow, M1, M2) is False


def test_string_form_when_rejected():
    """Legacy string-form when: → False (only predicate-form is comparable)."""
    M1 = _step(
        "M1",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when="route == 'a'",
    )
    M2 = _step(
        "M2",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when=WhenPredicate(input="route", op="eq", value="b"),
    )
    flow = _flow(_step("R"), M1, M2)
    assert mutex_when_proved(flow, M1, M2) is False
    assert mutex_when_proved(flow, M2, M1) is False


def test_none_when_rejected():
    """when=None → False."""
    M1 = _step(
        "M1",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
    )
    M2 = _step(
        "M2",
        inputs=[InputBinding(local_name="route", source_step="R", source_field="route_out")],
        when=WhenPredicate(input="route", op="eq", value="b"),
    )
    flow = _flow(_step("R"), M1, M2)
    assert mutex_when_proved(flow, M1, M2) is False
