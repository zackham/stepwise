"""Tests for R8: back-edge / cycle detection."""

from __future__ import annotations

from stepwise.models import (
    ExecutorRef,
    InputBinding,
    StepDefinition,
    WorkflowDefinition,
)
from stepwise.validator import (
    compute_back_edges,
    find_cycle_nodes,
    validate,
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


def test_acyclic_flow_no_back_edges():
    """A linear chain has no cycles and compute_back_edges returns empty."""
    flow = _flow(
        _step("A"),
        _step("B", after=["A"]),
        _step("C", after=["B"]),
    )
    assert find_cycle_nodes(flow) == set()
    assert compute_back_edges(flow) == set()
    r = validate(flow)
    back_errors = [e for e in r.errors if e.rule_id in ("back_edge_unsupported", "cyclic_dependency")]
    assert back_errors == []


def test_self_loop_detected_and_rejected():
    """A step that depends on itself via after."""
    flow = _flow(_step("A", after=["A"]))
    cycle = find_cycle_nodes(flow)
    assert "A" in cycle
    r = validate(flow)
    assert r.accepted is False
    errors = [e for e in r.errors if e.rule_id in ("back_edge_unsupported", "cyclic_dependency")]
    assert len(errors) >= 1


def test_two_step_cycle_rejected():
    """A → B → A via after."""
    flow = _flow(
        _step("A", after=["B"]),
        _step("B", after=["A"]),
    )
    cycle = find_cycle_nodes(flow)
    assert cycle == {"A", "B"}
    r = validate(flow)
    assert r.accepted is False
    errors = [e for e in r.errors if e.rule_id == "back_edge_unsupported"]
    assert len(errors) == 1
    assert "loop-back binding" in errors[0].message
    assert "not yet supported" in errors[0].message


def test_three_step_cycle_rejected_as_cyclic_dependency():
    """A → B → C → A: a cycle of 3 nodes."""
    flow = _flow(
        _step("A", after=["C"]),
        _step("B", after=["A"]),
        _step("C", after=["B"]),
    )
    cycle = find_cycle_nodes(flow)
    assert cycle == {"A", "B", "C"}
    r = validate(flow)
    assert r.accepted is False
    errors = [e for e in r.errors if e.rule_id == "cyclic_dependency"]
    assert len(errors) == 1
    assert "cyclic dependency" in errors[0].message


def test_cycle_via_input_binding_rejected():
    """B has an input binding to A's output AND A has an input binding to B's output."""
    flow = _flow(
        _step("A", inputs=[InputBinding(local_name="x", source_step="B", source_field="B_out")]),
        _step("B", inputs=[InputBinding(local_name="y", source_step="A", source_field="A_out")]),
    )
    cycle = find_cycle_nodes(flow)
    assert cycle == {"A", "B"}
    r = validate(flow)
    assert r.accepted is False


def test_cyclic_flow_does_not_compute_mhb():
    """When a cycle is present, validate() returns early — no other errors emitted."""
    flow = _flow(
        _step("A", after=["B"]),
        _step("B", after=["A"]),
    )
    r = validate(flow)
    # Only the cycle/back-edge error, no pair_unsafe etc.
    assert all(
        e.rule_id in ("back_edge_unsupported", "cyclic_dependency") for e in r.errors
    )
