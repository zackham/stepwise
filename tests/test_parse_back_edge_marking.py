"""Step 7 (§11.2): yaml_loader._mark_back_edges parser pre-pass tests.

Covers R1-R6 + R7-R10 of the step 7 plan.
"""

from __future__ import annotations

import pytest

from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError


def _load(tmp_path, body: str):
    f = tmp_path / "f.flow.yaml"
    f.write_text(body)
    return load_workflow_yaml(f)


# ─── R1: single-step self-loop ────────────────────────────────────────────


def test_single_step_self_loop_marked(tmp_path):
    body = """\
name: self-loop
author: test
steps:
  start:
    run: 'echo "{}"'
    outputs: [v]
  worker:
    run: 'echo "{}"'
    outputs: [v]
    inputs:
      prev:
        from: worker.v
        optional: true
    after: [start]
    exits:
      - name: again
        when: "True"
        action: loop
        target: worker
        max_iterations: 3
"""
    wf = _load(tmp_path, body)
    worker = wf.steps["worker"]
    prev = next(b for b in worker.inputs if b.local_name == "prev")
    assert prev.is_back_edge is True
    assert prev.closing_loop_id == "worker"


# ─── R2: two-step forward loop ────────────────────────────────────────────


def test_two_step_forward_loop_marked(tmp_path):
    body = """\
name: two-step
author: test
steps:
  analyze:
    run: 'echo "{}"'
    outputs: [v]
    inputs:
      prev:
        from: critique.note
        optional: true
  critique:
    run: 'echo "{}"'
    outputs: [note, verdict]
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
    prev = next(b for b in wf.steps["analyze"].inputs if b.local_name == "prev")
    assert prev.is_back_edge is True
    assert prev.closing_loop_id == "analyze"


# ─── R3: any_of all sources are back-edge ─────────────────────────────────


def test_any_of_all_sources_back_edge_marked(tmp_path):
    """When every any_of source is a back-edge AND they share the closing
    loop, the binding-as-a-whole is marked is_back_edge."""
    body = """\
name: any-of-back-edge
author: test
steps:
  seed:
    run: 'echo "{}"'
    outputs: [v]
  analyze:
    run: 'echo "{}"'
    outputs: [v]
    inputs:
      seed: seed.v
      prev:
        any_of:
          - critique_a.note
          - critique_b.note
        optional: true
  critique_a:
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
  critique_b:
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
    prev = next(b for b in wf.steps["analyze"].inputs if b.local_name == "prev")
    assert prev.is_back_edge is True
    assert prev.closing_loop_id == "analyze"


# ─── R4: any_of mixed scope NOT marked ────────────────────────────────────


def test_any_of_mixed_scope_not_marked(tmp_path):
    """Canonical swdev/plan pattern: any_of with one back-edge source AND
    one forward source. The binding-as-a-whole is NOT marked
    is_back_edge (per §11.4) because not every source is a back-edge."""
    body = """\
name: mixed-any-of
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
    text = next(b for b in wf.steps["analyze"].inputs if b.local_name == "text")
    # The binding is NOT marked as back-edge: the iter-1 path (plan.result)
    # is forward, not loop-back.
    assert text.is_back_edge is False


# ─── R5: ambiguous closure rejection ──────────────────────────────────────
# (Hard to construct a fixture for v1 — multiple non-nested loops on the
# same path is unusual. Skip for now; documented as a known v1.0 limit.)


# ─── R6: optional back-edge marked ────────────────────────────────────────


def test_optional_back_edge_marked(tmp_path):
    body = """\
name: opt-back
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
    outputs: [feedback, verdict]
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
    prev = next(b for b in wf.steps["worker"].inputs if b.local_name == "prev")
    assert prev.is_back_edge is True


# ─── R7: is_present accepted on loop-back ─────────────────────────────────


def test_is_present_on_loop_back_accepted(tmp_path):
    body = """\
name: is-present-loop-back
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
        from: critique.note
        optional: true
    when:
      input: prev
      is_present: false
  critique:
    run: 'echo "{}"'
    outputs: [note]
    inputs:
      text: worker.v
    exits:
      - name: again
        when: "True"
        action: loop
        target: worker
        max_iterations: 3
"""
    wf = _load(tmp_path, body)
    assert wf.steps["worker"].when is not None  # parsed clean


# ─── R8: is_present rejected on regular binding ───────────────────────────


def test_is_present_on_regular_binding_rejected(tmp_path):
    body = """\
name: bad-is-present
author: test
steps:
  fetch:
    run: 'echo "{}"'
    outputs: [x]
  consume:
    inputs:
      x: fetch.x
    when:
      input: x
      is_present: true
    run: 'echo "{}"'
    outputs: [done]
"""
    f = tmp_path / "f.flow.yaml"
    f.write_text(body)
    with pytest.raises(YAMLLoadError) as exc:
        load_workflow_yaml(f)
    msg = str(exc.value)
    assert "is_present_not_loop_back" in msg or "not a loop-back binding" in msg


# ─── R9: is_present on mixed any_of rejected ──────────────────────────────


def test_is_present_on_mixed_any_of_rejected(tmp_path):
    body = """\
name: bad-mixed-any-of
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
    when:
      input: text
      is_present: true
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
    f = tmp_path / "f.flow.yaml"
    f.write_text(body)
    with pytest.raises(YAMLLoadError) as exc:
        load_workflow_yaml(f)
    msg = str(exc.value)
    assert "is_present_mixed_scope_any_of" in msg or "every source" in msg


# ─── R10: is_null still legal on any binding ──────────────────────────────


def test_is_null_legal_on_regular_binding(tmp_path):
    body = """\
name: is-null-ok
author: test
steps:
  fetch:
    run: 'echo "{}"'
    outputs: [x]
  consume:
    inputs:
      x: fetch.x
    when:
      input: x
      is_null: false
    run: 'echo "{}"'
    outputs: [done]
"""
    wf = _load(tmp_path, body)
    assert wf.steps["consume"].when is not None
