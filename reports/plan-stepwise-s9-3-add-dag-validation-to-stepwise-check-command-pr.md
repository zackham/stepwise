# S9-3: Add DAG Validation to `stepwise check`

## Overview

`stepwise check` currently only validates model resolution and provider API keys for LLM steps. It parses YAML with `yaml.safe_load()` (raw dict), never builds a `WorkflowDefinition`, and always returns `EXIT_SUCCESS`. A flow with a 3-step cycle passes with zero warnings.

Meanwhile, `stepwise validate` already runs comprehensive DAG validation via `WorkflowDefinition.validate()` — including cycle detection, missing step references, loop target connectivity, and entry/terminal existence. The fix is straightforward: **make `check` run the same structural validation that `validate` already does**, then continue with its model-resolution pass.

This is not a "build cycle detection from scratch" task. The validation logic exists. The gap is that `check` doesn't call it.

## Requirements

### R1: Run structural validation in `check`
**What:** `cmd_check` calls `load_workflow_yaml()` → `wf.validate()` → `wf.warnings()` before model resolution.
**Acceptance:** `stepwise check cycle-flow.yaml` reports cycle errors. `stepwise check valid-flow.yaml` shows structural OK then model table.

### R2: Detect cycles with edge-level detail
**What:** Improve `_detect_cycles()` error message to show the specific edges forming the cycle, not just the step names.
**Acceptance:** Error reads like `"Cycle: step-a → step-b (input: data) → step-c (input: result) → step-a (input: output)"` instead of `"Cycle detected involving steps: step-a, step-b, step-c"`.

### R3: Validate loop targets (already done — verify coverage)
**What:** `validate()` already checks that loop targets exist and are DAG-connected. Confirm this works through `check`.
**Acceptance:** `stepwise check` reports error for `action: loop, target: nonexistent-step` and for loop targets not connected in the dependency graph.

### R4: Report unreachable steps
**What:** After cycle detection, identify steps with no path from any entry step (roots). These steps can never execute.
**Acceptance:** A flow with step-x that depends on step-y (which doesn't exist as a real dep target from roots) reports `"Step 'step-x' is unreachable — no path from any entry step"`.

### R5: Missing dependency references (already done — verify coverage)
**What:** `validate()` already checks input bindings and `after` clauses for nonexistent step names. Confirm this surfaces through `check`.
**Acceptance:** `stepwise check` reports `"Step 'X': input binding references unknown step 'Y'"`.

### R6: Actionable error messages
**What:** Where the existing cycle message just lists step names, enhance to show which edges to consider removing.
**Acceptance:** Cycle errors include the edge path and a suggestion like `"Consider adding 'optional: true' to break the cycle or removing a dependency"`.

### R7: Non-zero exit code on failure
**What:** `cmd_check` returns `EXIT_JOB_FAILED` (1) when structural validation finds errors.
**Acceptance:** `stepwise check bad-flow.yaml; echo $?` prints `1`. CI scripts can `stepwise check flow.yaml || exit 1`.

## Assumptions (verified against code)

1. **`validate()` already covers R3 and R5.** Verified at `models.py:680-707` (exit rule targets, loop connectivity) and `models.py:612-653` (input binding sources, `after` references). No new validation logic needed for these.

2. **`_detect_cycles()` uses Kahn's algorithm** (`models.py:1145-1209`). It correctly excludes loop back-edges and optional edges. The remaining steps after topological sort are the cycle members. Currently reports just step names — enhancement needed for edge-level detail.

3. **`cmd_check` uses raw `yaml.safe_load()`** (`cli.py:1635-1636`). Must switch to `load_workflow_yaml()` to get a `WorkflowDefinition` for validation. The model-resolution pass can then use `wf.steps` instead of raw dict traversal.

4. **Unreachable step detection doesn't exist yet.** `entry_steps()` identifies roots and `_get_ancestors()` does BFS, but no check validates that every step is reachable from at least one root. This is new logic.

5. **`warnings()` returns advisory strings** (`models.py:798+`). These should surface through `check` as they do through `validate` — informational, not blocking.

6. **Exit codes are defined** at `cli.py:57-62`. `EXIT_JOB_FAILED = 1` is the right code for validation failures.

## Implementation Steps

### Step 1: Add unreachable step detection to `WorkflowDefinition.validate()`
**File:** `src/stepwise/models.py`

Add after the entry/terminal checks (line ~778), before `return errors`:

```python
# Check for unreachable steps (no path from any entry step)
if not errors:
    reachable: set[str] = set(entry)
    frontier = set(entry)
    # BFS forward through the DAG (follow dependents, not dependencies)
    fwd_adj: dict[str, set[str]] = {n: set() for n in self.steps}
    for name, step in self.steps.items():
        for dep in self._get_step_deps(name):
            if dep in fwd_adj:
                fwd_adj[dep].add(name)
    while frontier:
        node = frontier.pop()
        for child in fwd_adj.get(node, set()):
            if child not in reachable:
                reachable.add(child)
                frontier.add(child)
    unreachable = set(self.steps.keys()) - reachable
    for name in sorted(unreachable):
        errors.append(
            f"Step '{name}' is unreachable — no path from any entry step"
        )
```

This BFS walks forward from entry steps through the forward adjacency graph (dep → dependent). Any step not visited is unreachable.

**Note:** Must handle loop back-edges and optional edges the same way `_detect_cycles` does — exclude them from the forward adjacency so we don't mark loop targets as "reachable only via back-edge". Actually, loop targets ARE reachable via back-edges (they're re-entered), so the forward BFS should use the same edge set as `_detect_cycles` (excluding loop back-edges) but entry steps that are loop targets are already in the `entry` set. Need to think carefully: a step that is ONLY reachable via a loop back-edge is actually reachable at runtime (the loop will re-launch it). So the forward adjacency should include ALL hard edges (including loop back-edges) — just not optional edges. Actually, simplest: use full dependency graph forward edges. If a step has deps and none of those deps are reachable from roots, it's unreachable.

Revised approach: Build forward adjacency from ALL edges (hard deps, after, for_each). Don't exclude loop back-edges (they represent real runtime paths). Exclude only `$job` references.

### Step 2: Improve cycle detection error messages
**File:** `src/stepwise/models.py`

Enhance `_detect_cycles()` to extract the actual cycle path from the remaining nodes. After Kahn's algorithm identifies the remaining nodes, do a DFS from any remaining node following only edges within the remaining set to find one concrete cycle. Report edges with their type (input binding / after / for_each):

```python
if visited != len(self.steps):
    remaining = {n for n, d in in_degree.items() if d > 0}
    # Find one concrete cycle for actionable reporting
    cycle_path = self._find_cycle_path(remaining, adj)
    if cycle_path:
        edges = []
        for i in range(len(cycle_path)):
            src = cycle_path[i]
            dst = cycle_path[(i + 1) % len(cycle_path)]
            edge_type = self._classify_edge(src, dst)
            edges.append(f"{src} → {dst} ({edge_type})")
        return [
            f"Cycle detected: {' → '.join(edges)}. "
            f"Consider adding 'optional: true' to one input binding to break the cycle."
        ]
    return [f"Cycle detected involving steps: {', '.join(sorted(remaining))}"]
```

Add helper `_find_cycle_path(remaining, adj)` — DFS within remaining nodes to extract one cycle. Add `_classify_edge(src, dst)` — checks if the edge is from input binding, `after`, or `for_each`.

### Step 3: Rewrite `cmd_check` to run structural validation first
**File:** `src/stepwise/cli.py`

Replace `yaml.safe_load()` with `load_workflow_yaml()`. Run `wf.validate()` + `wf.warnings()` first. Then do model resolution from `wf.steps` instead of raw dict.

```python
def cmd_check(args: argparse.Namespace) -> int:
    """Verify flow structure and model resolution."""
    from stepwise.config import load_config_with_sources
    from stepwise.flow_resolution import FlowResolutionError, resolve_flow
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    project_dir = Path(args.project_dir).resolve() if args.project_dir else Path.cwd().resolve()
    try:
        flow_path = resolve_flow(args.flow, project_dir)
    except (FlowResolutionError, Exception) as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE_ERROR

    io = _io(args)

    # Phase 1: Structural validation
    try:
        wf = load_workflow_yaml(str(flow_path))
        errors = wf.validate()
        if errors:
            io.log("error", f"Validation failed: {flow_path.name}")
            for err in errors:
                io.log("info", f"  ✗ {err}")
            return EXIT_JOB_FAILED

        step_count = len(wf.steps)
        loop_count = sum(
            1 for s in wf.steps.values()
            for r in s.exit_rules
            if r.config.get("action") == "loop"
        )
        parts = [f"{step_count} steps"]
        if loop_count:
            parts.append(f"{loop_count} loops")
        io.log("success", f"Structure OK ({', '.join(parts)})")

        flow_warnings = wf.warnings()
        for w in flow_warnings:
            if w.startswith("ℹ"):
                io.log("info", f"  {w}")
            else:
                io.log("warn", f"  {w}")
    except YAMLLoadError as e:
        io.log("error", f"{flow_path.name}:")
        for err in e.errors:
            io.log("info", f"  - {err}")
        return EXIT_JOB_FAILED
    except Exception as e:
        io.log("error", f"{flow_path}: {e}")
        return EXIT_JOB_FAILED

    # Phase 2: Model resolution (existing logic, adapted to use wf.steps)
    cws = load_config_with_sources(project_dir)
    cfg = cws.config
    # ... (existing model resolution table logic, iterate wf.steps
    #      instead of raw dict, check executor.type for "llm"/"agent")

    return EXIT_SUCCESS
```

Key changes:
- Import `load_workflow_yaml` instead of `yaml`
- Phase 1: structural validation with early return on errors
- Phase 2: model resolution from `wf.steps` (adapt field access from raw dict to `StepDefinition` attributes)
- Return `EXIT_JOB_FAILED` on structural errors

### Step 4: Update model-resolution loop to use `StepDefinition`
**File:** `src/stepwise/cli.py`

The current model resolution iterates raw dicts. After switching to `load_workflow_yaml()`, iterate `wf.steps` and access `step.executor.type`, `step.executor.config.get("model")`, etc. The `ExecutorRef` dataclass has `.type` and `.config` attributes.

### Step 5: Add tests
**File:** `tests/test_validation.py` (extend existing)

```python
class TestDagValidationInCheck:
    """Tests for DAG validation that surfaces through check/validate."""

    def test_cycle_detected_with_edges(self):
        """Cycle error message includes specific edges."""
        wf = WorkflowDefinition(steps={
            "a": StepDefinition(name="a", inputs=[InputBinding("x", "c", "out")], outputs=["out"]),
            "b": StepDefinition(name="b", inputs=[InputBinding("x", "a", "out")], outputs=["out"]),
            "c": StepDefinition(name="c", inputs=[InputBinding("x", "b", "out")], outputs=["out"]),
        })
        errors = wf.validate()
        assert any("→" in e for e in errors)  # Edge-level detail
        assert any("a" in e and "b" in e and "c" in e for e in errors)

    def test_unreachable_step_detected(self):
        """Steps with no path from any root are flagged."""
        wf = WorkflowDefinition(steps={
            "root": StepDefinition(name="root", outputs=["out"]),
            "connected": StepDefinition(name="connected", inputs=[InputBinding("x", "root", "out")], outputs=["y"]),
            "island": StepDefinition(name="island", inputs=[InputBinding("x", "phantom", "out")], outputs=["z"]),
        })
        errors = wf.validate()
        # "island" references unknown step "phantom" — caught by existing validation
        assert any("unknown step 'phantom'" in e for e in errors)

    def test_unreachable_step_valid_but_disconnected(self):
        """Step with valid deps but no path from root."""
        # Create two disconnected subgraphs
        wf = WorkflowDefinition(steps={
            "root": StepDefinition(name="root", outputs=["out"]),
            "mid": StepDefinition(name="mid", inputs=[InputBinding("x", "root", "out")], outputs=["y"]),
            # island-a and island-b form a valid subgraph but disconnected from root
            "island-a": StepDefinition(name="island-a", outputs=["out"]),
            "island-b": StepDefinition(name="island-b", inputs=[InputBinding("x", "island-a", "out")], outputs=["z"]),
        })
        errors = wf.validate()
        # island-a is an entry step too, so island-a → island-b is reachable
        # This is actually valid (multiple entry points). Need a better test case.
        assert not errors  # Both subgraphs are independently valid

    def test_unreachable_via_only_optional_edge(self):
        """Step reachable only via optional edge from root is still reachable
        (entry_steps includes it since optional deps don't block entry status)."""
        # Better test: step with all hard deps from a cycle that was pruned
        pass  # Design concrete case during implementation

    def test_loop_back_edge_not_false_positive(self):
        """Valid loop patterns are not flagged as cycles."""
        wf = WorkflowDefinition(steps={
            "generate": StepDefinition(name="generate", outputs=["content"]),
            "review": StepDefinition(
                name="review",
                inputs=[InputBinding("content", "generate", "content")],
                outputs=["score"],
                exit_rules=[ExitRule(name="retry", type="conditional",
                    condition="attempt < 3",
                    config={"action": "loop", "target": "generate"})],
            ),
        })
        errors = wf.validate()
        assert not errors
```

Also add a CLI integration test:

```python
def test_check_returns_nonzero_on_cycle(tmp_path):
    """stepwise check exits 1 on structural errors."""
    flow = tmp_path / "bad.flow.yaml"
    flow.write_text("""
name: bad-cycle
steps:
  a:
    run: echo hi
    inputs: { x: "c.out" }
    outputs: [out]
  b:
    run: echo hi
    inputs: { x: "a.out" }
    outputs: [out]
  c:
    run: echo hi
    inputs: { x: "b.out" }
    outputs: [out]
""")
    result = subprocess.run(
        ["uv", "run", "stepwise", "check", str(flow)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "ycle" in result.stderr or "ycle" in result.stdout
```

### Step 6: Update docstring and help text
**File:** `src/stepwise/cli.py`

Update the `check` subparser help from `"Verify model resolution for every LLM step in a flow"` to `"Validate flow structure and model resolution"`. Update `cmd_check` docstring similarly.

## Testing Strategy

```bash
# Unit tests — existing + new
uv run pytest tests/test_validation.py -v        # Existing warnings tests still pass
uv run pytest tests/test_models.py -v             # Existing cycle/entry/terminal tests pass

# New tests
uv run pytest tests/test_validation.py::TestDagValidationInCheck -v

# Integration — manual
# Create a cycle flow and verify check catches it:
cat > /tmp/cycle.flow.yaml <<'EOF'
name: cycle-test
steps:
  a:
    run: echo hi
    inputs: { x: "c.out" }
    outputs: [out]
  b:
    run: echo hi
    inputs: { x: "a.out" }
    outputs: [out]
  c:
    run: echo hi
    inputs: { x: "b.out" }
    outputs: [out]
EOF
uv run stepwise check /tmp/cycle.flow.yaml
echo "Exit code: $?"  # Should be 1

# Verify valid flows still pass:
uv run stepwise check examples/hello.flow.yaml  # Should be 0

# Full suite
uv run pytest tests/ -x
```

## Scope & Non-Goals

- **In scope:** Wire existing validation into `check`, improve cycle error messages, add unreachable step detection, non-zero exit codes.
- **Out of scope:** New validation rules beyond what's specified. The `validate` command's `--fix` flag is not being added to `check`. No changes to `validate` command behavior (it already works correctly). No changes to runtime engine validation.

## Risk

**Low.** The validation logic already exists and is well-tested. The primary change is calling it from `cmd_check`. The new unreachable-step detection is a simple BFS. The cycle message improvement is a presentation change to existing detection. Existing tests cover the validation foundations; new tests cover the integration and message format.
