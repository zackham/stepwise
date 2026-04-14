"""Step 7 (§11): validator integration tests for loop-back binding flows.

Verifies that the 8 yellow vita flows + synthetic shapes that use
loop-back patterns now validate cleanly via the top-level
validator.validate(flow) function.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stepwise.validator.back_edges import (
    compute_back_edges,
    find_cycle_nodes_excluding_back_edges,
)
from stepwise.validator.validate import validate
from stepwise.yaml_loader import load_workflow_yaml


# ─── Synthetic fixtures ───────────────────────────────────────────────────


def _load(tmp_path, body: str):
    f = tmp_path / "f.flow.yaml"
    f.write_text(body)
    return load_workflow_yaml(f)


def test_back_edges_computed_from_bindings(tmp_path):
    body = """\
name: simple-loop
author: test
steps:
  start:
    run: 'echo "{}"'
    outputs: [v]
  worker:
    run: 'echo "{}"'
    outputs: [v]
    inputs:
      seed: start.v
      prev:
        from: validator.feedback
        optional: true
  validator:
    run: 'echo "{}"'
    outputs: [feedback]
    inputs:
      cur: worker.v
    exits:
      - name: again
        when: "True"
        action: loop
        target: worker
        max_iterations: 3
"""
    wf = _load(tmp_path, body)
    edges = compute_back_edges(wf)
    assert ("worker", "validator") in edges


def test_residual_forward_cycle_still_rejected(tmp_path):
    """A flow with a forward cycle (no loop exit rule closing it) is still
    rejected by the residual cycle check."""
    body = """\
name: forward-cycle
author: test
steps:
  a:
    run: 'echo "{}"'
    outputs: [v]
    inputs:
      x: b.v
  b:
    run: 'echo "{}"'
    outputs: [v]
    inputs:
      y: a.v
"""
    f = tmp_path / "f.flow.yaml"
    f.write_text(body)
    # The validator's WorkflowDefinition.validate() catches this as a cycle
    # before even reaching the new validator/validate.py.
    from stepwise.yaml_loader import YAMLLoadError
    with pytest.raises(YAMLLoadError):
        load_workflow_yaml(f)


def test_simple_loop_back_validates_clean(tmp_path):
    body = """\
name: clean-loop
author: test
steps:
  start:
    run: 'echo "{}"'
    outputs: [v]
  analyze:
    run: 'echo "{}"'
    outputs: [v]
    inputs:
      seed: start.v
      prev:
        from: critique.note
        optional: true
  critique:
    run: 'echo "{}"'
    outputs: [note]
    inputs:
      text: analyze.v
    exits:
      - name: again
        when: "True"
        action: loop
        target: analyze
        max_iterations: 3
"""
    wf = _load(tmp_path, body)
    result = validate(wf)
    assert result.accepted, f"unexpected errors: {[e.message for e in result.errors]}"


def test_any_of_swdev_plan_pattern_validates_clean(tmp_path):
    """Canonical swdev/plan shape: any_of [refine.result, plan.result]
    where refine is the back-edge and plan is the iter-1 producer."""
    body = """\
name: swdev-plan
author: test
steps:
  plan:
    run: 'echo "{}"'
    outputs: [result]
  analyze:
    run: 'echo "{}"'
    outputs: [result]
    inputs:
      text:
        any_of:
          - refine.result
          - plan.result
  refine:
    run: 'echo "{}"'
    outputs: [result]
    inputs:
      text: analyze.result
    exits:
      - name: again
        when: "True"
        action: loop
        target: analyze
        max_iterations: 3
"""
    wf = _load(tmp_path, body)
    result = validate(wf)
    assert result.accepted, f"unexpected errors: {[e.message for e in result.errors]}"


def test_find_cycle_nodes_excluding_back_edges_passes_clean_loop(tmp_path):
    body = """\
name: clean-loop
author: test
steps:
  a:
    run: 'echo "{}"'
    outputs: [v]
  b:
    run: 'echo "{}"'
    outputs: [v]
    inputs:
      x: a.v
      prev:
        from: c.v
        optional: true
  c:
    run: 'echo "{}"'
    outputs: [v]
    inputs:
      y: b.v
    exits:
      - name: back
        when: "True"
        action: loop
        target: b
        max_iterations: 3
"""
    wf = _load(tmp_path, body)
    cycles = find_cycle_nodes_excluding_back_edges(wf)
    assert cycles == set()


# ─── 8 yellow vita flow placeholders ──────────────────────────────────────
# The actual yellow flows live in vita/flows; we exercise them via the
# sweep tool (PYTHONPATH=scripts python sweep_vita_flows.py). The test
# below verifies that the swdev/plan pattern (which all 8 yellow flows
# reduce to) validates clean.


def test_research_proposal_pattern_validates_clean(tmp_path):
    """The research-proposal pattern: optional back-edge from external-checkpoint."""
    body = """\
name: research-proposal-min
author: test
steps:
  init:
    run: 'echo "{}"'
    outputs: [report_path]
  revise:
    run: 'echo "{}"'
    outputs: [report]
    inputs:
      report_path: init.report_path
      external_feedback:
        from: external-checkpoint.feedback
        optional: true
  external-checkpoint:
    executor: external
    prompt: "Review the proposal."
    inputs:
      result: revise.report
    outputs:
      choice:
        type: choice
        options: [approved, feedback]
      feedback:
        type: text
    exits:
      - name: approved
        when: "outputs.choice == 'approved'"
        action: advance
      - name: feedback
        when: "True"
        action: loop
        target: revise
        max_iterations: 5
"""
    wf = _load(tmp_path, body)
    result = validate(wf)
    assert result.accepted, f"unexpected errors: {[e.message for e in result.errors]}"
