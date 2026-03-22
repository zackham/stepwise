---
title: "Implementation Plan: P6 — stepwise chain — Ephemeral Flow Composition"
date: "2026-03-21T22:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: P6 — stepwise chain — Ephemeral Flow Composition

## Overview

Add a `stepwise chain` CLI command that compiles a linear sequence of flows into an ephemeral workflow using the existing `flow:` sub-flow step mechanism, writes it to a temp file, and runs it through the normal engine — getting full DAG visualization, observability, and event stream with zero changes to the runner or engine.

## Requirements

### R1: CLI Command Registration
**`stepwise chain <flow1> <flow2> [flow3...] [--var key=val] [--watch] [--async] [--wait]`**

- Accepts 2+ flow names as positional arguments
- Supports all execution mode flags from `stepwise run`: `--watch`, `--async`, `--wait`, `--local`, `--output json`, `--timeout`, `--report`, `--var`, `--var-file`, `--vars-file`, `--objective`, `--name`, `--workspace`, `--meta`, `--no-open`, `--notify`, `--notify-context`, `--rerun`
- **AC1**: `stepwise chain --help` prints usage with all flags listed
- **AC2**: `stepwise chain flow1` (single flow) exits with error: "chain requires at least 2 flows"
- **AC3**: `stepwise chain flow1 flow2 --var x=1` parses both flow names and the var

### R2: Flow Resolution
Each flow name in the chain is resolved using the same logic as `stepwise run` (`cli.py:1767-1784`):
- Local `flows/` directory via `resolve_flow()` (`flow_resolution.py:26-92`)
- Registry `@author:name` via `parse_registry_ref()` + `resolve_registry_flow()`
- Direct file paths (absolute or relative)

- **AC1**: `stepwise chain research-proposal plan-and-build` resolves both flows from `flows/` directory
- **AC2**: `stepwise chain nonexistent foo` exits with error naming the unresolved flow
- **AC3**: `stepwise chain ./custom.flow.yaml plan-and-build` resolves the first as a direct path

### R3: Ephemeral Workflow Compilation
Build a YAML string where each flow becomes a sub-flow step using `flow: <absolute-path>`:
- Stage naming: `stage-1`, `stage-2`, ..., `stage-N`
- Auto-wire `result` from stage N to the appropriate input on stage N+1 (see R4)
- All `--var` flags become job-level variables, wired as `$job.<name>` to each stage that expects them
- Generated flow name: `chain-{flow1}-{flow2}-...` truncated to 80 chars

- **AC1**: `compile_chain([path_a, path_b], ["topic"])` returns valid YAML parseable by `load_workflow_yaml()`
- **AC2**: Stage-1 has no upstream result wiring; stage-2+ wire `result` from previous stage
- **AC3**: Generated YAML round-trips: `load_workflow_yaml(compile_chain(...))` succeeds and `validate()` returns no errors

### R4: Auto-Wiring Logic
Wire `result` output from stage N to stage N+1's input using priority-ordered config var matching:
1. If flow has `config_vars`: match against priority list `spec` > `topic` > `prompt` > `question` > first required config var
2. If flow has no `config_vars`: scan step `$job.*` bindings (`models.py:836-841` pattern) and match against same priority list
3. If neither yields a match: wire as `result`

Additionally, all `--var` names that match a flow's config vars (or scanned `$job.*` refs) are wired as `$job.<name>` passthrough to that stage.

- **AC1**: Flow with `config: {spec: {type: str}}` receives upstream `result` as `spec`
- **AC2**: Flow with `config: {topic: ..., spec: ...}` receives upstream `result` as `spec` (highest priority)
- **AC3**: Flow with `config: {custom: {type: str}}` (no priority match) receives upstream `result` as `custom`
- **AC4**: Flow with no `config:` block but steps using `$job.spec` receives upstream `result` as `spec`
- **AC5**: Flow with no `config:` and no `$job.*` refs receives upstream `result` as `result`
- **AC6**: `--var project=stepwise` appears as `project: $job.project` on every stage whose flow expects `$job.project`

### R5: Execution Modes
The chain command supports all `stepwise run` execution modes by delegating to the existing `cmd_run` dispatch logic (`cli.py:1844-1918`):
- Default (headless terminal progress) → `run_flow()` (`runner.py:384`)
- `--watch` (web UI) → `_run_watch()` (`cli.py:1921`)
- `--wait` (blocking JSON) → `run_wait()` (`runner.py:803`)
- `--async` (fire-and-forget) → `run_async()` (`runner.py:1438`)

- **AC1**: `stepwise chain flow1 flow2 --watch` opens web UI showing `stage-1` and `stage-2` as expandable sub-flow nodes
- **AC2**: `stepwise chain flow1 flow2 --wait` blocks, prints JSON result on stdout
- **AC3**: `stepwise chain flow1 flow2 --async` prints `{"job_id": "..."}` and exits immediately
- **AC4**: Default mode prints terminal progress with step completion events

### R6: No File Persistence
The ephemeral YAML exists only as a temp file outside `flows/`. Cleanup behavior:
- Sync modes (default, `--wait`, `--watch`): delete temp file in `finally` block after run completes
- `--async` mode: write to `.stepwise/tmp/` (project-scoped, gitignored) and skip cleanup since the subprocess reads job from DB, not from file (`runner.py:1523-1538` passes `--job-id`, not file path)

- **AC1**: After a completed default-mode chain run, `ls /tmp/stepwise-chain-*` finds no leftover files
- **AC2**: No new files exist in `flows/` after any chain run
- **AC3**: `--async` chain writes temp to `.stepwise/tmp/`, not `/tmp/`

## Assumptions

### A1: Existing `flow:` step mechanism handles all sub-flow execution
**Verified**: `yaml_loader.py:695-740` parses `flow: <path>` into a `StepDefinition` with `executor=ExecutorRef("sub_flow", {"flow_ref": flow_ref})` (line 736). Engine detects `executor.type == "sub_flow"` at `engine.py:1088-1089` and calls `_launch_sub_flow()` at `engine.py:1812-1848`, which creates a `SubJobDefinition` (line 1828) and delegates via `_create_sub_job()` (line 1833). Sub-job inputs come from `parent_run.inputs` at `engine.py:2298`: `inputs=parent_run.inputs or {}`. This means step-level input bindings become sub-job-level `$job.*` variables.

### A2: Flow resolution functions are reusable from outside cmd_run
**Verified**: `resolve_flow(name_or_path, project_dir)` at `flow_resolution.py:26` returns a `Path`. `parse_registry_ref(flow_ref)` at `flow_resolution.py` returns `(author, slug)` or `None`. Both are pure functions with no side effects. `cmd_run` imports and calls them at `cli.py:1758-1760`.

### A3: Config vars describe a flow's expected inputs (when declared)
**Verified**: `yaml_loader.py:899-940` parses `config:` blocks into `ConfigVar` objects stored on `WorkflowDefinition.config_vars` (list). Each `ConfigVar` has `name`, `required`, `default`, `type`, `description` fields (`models.py:298-340`). Steps reference these via `$job.<name>` input bindings.

### A4: Flows can use $job.* without declaring config vars
**Verified**: `generate-homepage.flow.yaml` uses `$job.repo_path`, `$job.spec_path`, `$job.output_dir` without a `config:` block. The validator at `models.py:860-863` only emits an info warning: `"$job.{name} is used but no config: block is declared"`. This means `compile_chain()` must also scan step input bindings for `$job.*` refs, not rely solely on `config_vars`.

### A5: Terminal step outputs are discoverable via WorkflowDefinition.terminal_steps()
**Verified**: `models.py:918-977` implements `terminal_steps()` — returns step names that nothing else depends on (excluding loop-internal steps). `_terminal_output()` at `engine.py:2319-2327` uses `terminal[0]` (first terminal step by insertion order) to extract sub-job results. **Caveat**: only the first terminal step's output is used; if a flow has multiple terminals with different outputs, only the first is propagated.

### A6: Absolute paths in `flow:` refs resolve correctly from temp files
**Verified**: `_load_flow_from_file` at `yaml_loader.py:389` does `(base_dir / file_ref).resolve()`. Python's `Path.__truediv__` discards the left operand when the right is absolute, so `Path("/tmp") / "/home/zack/flows/foo.flow.yaml"` → `Path("/home/zack/flows/foo.flow.yaml")`. The `base_dir` (derived from temp file location at `yaml_loader.py:1047`) is irrelevant for absolute paths. However, `base_dir` must not be `None` (checked at line 384); `load_workflow_yaml` guarantees this by defaulting to `source_path.parent` at line 1047.

### A7: Temp files work for all execution modes
**Verified**: All runner functions load the workflow from `flow_path` once at startup, then operate on the in-memory `WorkflowDefinition` object:
- `run_flow` loads at `runner.py:434`, never re-reads the file
- `run_wait` loads at `runner.py:838`, delegates `workflow.to_dict()` to server at `runner.py:900-921`
- `run_async` loads at `runner.py:1464`, persists job to DB before spawning subprocess; subprocess receives `--job-id` not file path (`runner.py:1523-1538`)
- `_run_watch` loads at `cli.py:1935`, submits `workflow.to_dict()` to server API
No runner function re-reads the file path after initial loading.

### A8: cmd_run can be reused via synthetic args namespace
**Verified**: `cmd_run` accesses args fields via attribute access and `getattr()` with defaults (`cli.py:1757-1918`). The complete field set is: `flow`, `objective`, `vars`, `vars_file`, `var_files`, `meta`, `project_dir`, `workspace`, `quiet`, `report`, `report_output`, `output_format`, `local`, `job_name`, `rerun_steps`, `async_mode`, `wait`, `watch`, `notify`, `notify_context`, `port`, `no_open`, `timeout`, `_adapter`. A synthetic `Namespace` with these fields can be passed to `cmd_run` directly.

## Out of Scope

| Item | Reason |
|------|--------|
| Non-linear composition (DAGs) | Use `.flow.yaml` with explicit `inputs`/`sequencing` for DAGs; chain is strictly linear by design |
| Explicit field mapping / `--pipe` | Requires complex CLI syntax; users needing custom wiring should write YAML directly |
| `result_type` validation between stages | Nice-to-have but not blocking; type mismatches surface as runtime errors in the sub-flow |
| Stage-specific `--var` flags | Adds CLI complexity; all vars pass through to all stages for simplicity |
| Changes to `runner.py`, `engine.py`, `server.py` | Temp file + existing `flow:` step mechanism makes engine changes unnecessary |
| New executor types | Reuses existing `sub_flow` executor registered by `yaml_loader.py` parsing |
| Flow output field remapping | If stage N outputs `[summary]` instead of `[result]`, the chain wires `summary` as-is; no remapping |

## Architecture

### Design: Temp YAML File + Reuse of cmd_run

The chain command builds a YAML string, writes it to a temp file, sets `args.flow` to the temp path, and calls `cmd_run(args)`. This reuses ALL existing execution logic with zero modifications to runner, engine, or server.

```
cmd_chain(args)                          [cli.py — new]
  ├─ validate: len(args.flows) >= 2
  ├─ resolve each flow name              [flow_resolution.py:resolve_flow]
  ├─ compile_chain(paths, var_names)     [chain.py — new]
  │   ├─ load each workflow              [yaml_loader.py:load_workflow_yaml]
  │   ├─ inspect config_vars + $job refs [models.py:836-841 pattern]
  │   ├─ determine result binding        [priority: spec>topic>prompt>question>first>result]
  │   ├─ build YAML string               [stage-N steps with flow: <abs-path>]
  │   └─ return YAML string
  ├─ write YAML to temp file
  ├─ args.flow = str(temp_path)          [rewrite args for cmd_run]
  ├─ cmd_run(args)                       [cli.py:1757 — existing, unchanged]
  └─ finally: cleanup temp file
```

### Generated YAML Structure

For `stepwise chain research-proposal plan-and-build --var topic="event system" --var project=stepwise`:

```yaml
name: chain-research-proposal-plan-and-build
description: "Chain: research-proposal → plan-and-build"
steps:
  stage-1:
    description: "research-proposal"
    flow: /home/zack/work/stepwise/flows/research-proposal/FLOW.yaml
    inputs:
      topic: $job.topic
      project: $job.project
    outputs: [result, result_type]
  stage-2:
    description: "plan-and-build"
    flow: /home/zack/work/stepwise/flows/plan-and-build/FLOW.yaml
    inputs:
      spec: stage-1.result
      project: $job.project
    outputs: [result]
```

### How Sub-flow Input Wiring Works (end-to-end)

1. **Chain job** created with `inputs={"topic": "event system", "project": "stepwise"}`
2. **Stage-1** step has input bindings: `{topic: $job.topic, project: $job.project}`
3. Engine resolves stage-1 inputs via `_resolve_inputs()` (`engine.py:1896-1897`): `{"topic": "event system", "project": "stepwise"}`
4. `_launch_sub_flow()` (`engine.py:1812`) creates sub-job via `_create_sub_job()` (`engine.py:2298`): `inputs=parent_run.inputs` = `{"topic": "event system", "project": "stepwise"}`
5. Sub-flow's steps access `$job.topic`, `$job.project` — resolves from sub-job inputs
6. Sub-flow terminal step outputs `{"result": "...", "result_type": "..."}` → propagated to parent via `_terminal_output()` (`engine.py:2319-2327`)
7. **Stage-2** step has input bindings: `{spec: stage-1.result, project: $job.project}`
8. Engine resolves: `{"spec": "<stage-1 result>", "project": "stepwise"}`
9. Sub-job for stage-2 gets `inputs={"spec": "...", "project": "stepwise"}`
10. Sub-flow's steps access `$job.spec`, `$job.project`

Extra inputs passed to a sub-flow are silently ignored (`engine.py:1896-1897` only extracts declared bindings).

### Key Design Decisions

1. **Temp file vs. WorkflowDefinition refactor**: Temp file avoids modifying any runner function signatures. `load_workflow_yaml` handles `flow: <absolute-path>` natively (`yaml_loader.py:507-512`). All runner functions load from path once, then work with the in-memory object (verified in A7).

2. **Reuse cmd_run vs. duplicate dispatch**: Setting `args.flow = temp_path` and calling `cmd_run(args)` reuses the entire mode dispatch (`cli.py:1844-1918`) including `--async`, `--wait`, `--watch`, input parsing, metadata, notify. This avoids duplicating ~80 lines of dispatch logic. Feasible because all required args fields are known (A8).

3. **Config var inspection + $job scanning**: Primary source is `config_vars` list; fallback scans all step input bindings for `$job.*` source refs (same pattern as `models.py:836-841`). This handles flows both with and without `config:` blocks (verified in A3, A4).

4. **Passthrough ALL matching vars**: Each stage receives `$job.<name>` bindings for every `--var` key that the flow references. Non-matching vars are not wired (no error, just unused). This follows the engine's behavior of silently ignoring extra inputs.

### File Locations

| File | Purpose | Depends on |
|------|---------|------------|
| `src/stepwise/chain.py` | **New**: `compile_chain()` + `_determine_result_binding()` + `_scan_job_refs()` | `yaml_loader.py`, `models.py` |
| `src/stepwise/cli.py` | `cmd_chain()` handler + subparser registration | `chain.py`, `flow_resolution.py` |
| `tests/test_chain.py` | **New**: Unit + integration tests | `chain.py`, `conftest.py` fixtures |

## Implementation Steps

### Step 1: Create `_determine_result_binding()` and `_scan_job_refs()` in `src/stepwise/chain.py` (~30 min)

**Why first**: These are pure functions with no dependencies beyond `models.py` types. They form the core wiring logic that everything else builds on. Must be correct before building the YAML generator.

**`_determine_result_binding(config_vars: list[ConfigVar], job_refs: set[str]) -> str`**
- Checks `config_vars` names, then `job_refs`, against priority list: `["spec", "topic", "prompt", "question"]`
- Falls back to first required config var (by list order), then first job ref, then `"result"`
- Returns the variable name to wire `result` into

**`_scan_job_refs(workflow: WorkflowDefinition) -> set[str]`**
- Iterates `workflow.steps.values()`, collects `{b.source_field for b in step.inputs if b.source_step == "$job"}`
- Same pattern as `models.py:836-841` (the validate_warnings `job_fields` extraction)
- Returns set of field names that the flow expects from `$job`

Both functions are < 20 lines each. Unit-testable in isolation.

### Step 2: Create `compile_chain()` in `src/stepwise/chain.py` (~45 min)

**Why second**: Depends on Step 1's wiring functions. This is the main compilation function — it loads flows, inspects them, and generates the YAML string.

**`compile_chain(flow_paths: list[Path], var_names: list[str]) -> str`**

Logic:
1. For each flow path: call `load_workflow_yaml(str(path))` to get `WorkflowDefinition`
2. For each flow: collect `config_vars` and `_scan_job_refs(wf)` to determine expected inputs
3. For each flow: call `terminal_steps()`, get first terminal's `outputs` list. Error if no terminal steps.
4. For each flow (N > 1): call `_determine_result_binding()` to find where to wire upstream `result`
5. For each flow: compute passthrough vars — intersection of `var_names` with flow's expected inputs (config var names ∪ job refs)
6. Build YAML dict with:
   - `name`: `chain-{stem1}-{stem2}-...` truncated at 80 chars
   - `description`: `"Chain: {stem1} → {stem2} → ..."`
   - `steps`: dict of `stage-N` entries, each with `flow`, `inputs`, `outputs`, `description`
7. Serialize via `yaml.dump()` and return

Edge case handling:
- Terminal step outputs `[summary, score]` (no `result`): use `summary` (first output) as the forward-wired field. Log warning to stderr.
- No terminal steps: raise `ValueError` with flow name and guidance
- Single terminal step vs. multiple: always use `terminal_steps()[0]` (matches engine behavior at `engine.py:2324`)

**Depends on**: Step 1 (wiring functions). Cannot be parallelized with Step 1.

### Step 3: Unit tests for `compile_chain()` in `tests/test_chain.py` (~45 min)

**Why third**: Validates Steps 1-2 before wiring into the CLI. Tests catch wiring bugs early without needing engine integration.

Write temp flow YAML files to `tempfile.mkdtemp()`, call `compile_chain()`, parse output with `yaml.safe_load()` and assert structure.

**Test cases** (each is a separate test function):

1. **`test_two_flow_chain_spec_binding`**: Flow-A outputs `[result]`, flow-B has `config: {spec: {type: str}}`. Assert stage-2 input `spec: stage-1.result`.

2. **`test_priority_order_spec_over_topic`**: Flow with `config: {topic: ..., spec: ...}`. Assert `spec` wins.

3. **`test_priority_order_topic`**: Flow with `config: {topic: {type: str}}` only. Assert `topic`.

4. **`test_priority_order_prompt`**: Flow with `config: {prompt: {type: text}}` only. Assert `prompt`.

5. **`test_fallback_first_required_config_var`**: Flow with `config: {custom_input: {type: str}}`. Assert `custom_input`.

6. **`test_no_config_vars_with_job_refs`**: Flow with no `config:` but steps using `$job.spec`. Assert `spec` binding.

7. **`test_no_config_vars_no_job_refs`**: Flow with no `config:` and no `$job.*` refs. Assert `result` fallback.

8. **`test_var_passthrough`**: Both flows have `project` config var. Chain with `var_names=["project", "extra"]`. Assert both stages get `project: $job.project`; `extra` only appears on stages whose flow references it.

9. **`test_three_flow_chain`**: Three flows chained. Assert stage-2 wires from `stage-1.result`, stage-3 from `stage-2.result`.

10. **`test_output_discovery`**: Flow with terminal step outputs `[result, summary]`. Assert stage declares `outputs: [result, summary]`.

11. **`test_non_result_output_forward`**: Terminal step outputs `[summary, score]` (no `result`). Assert stage uses `summary` as the forward-wired output for the next stage.

12. **`test_flow_name_truncation`**: Five flows with long names. Assert generated name ≤ 80 chars.

13. **`test_compile_roundtrip`**: Output of `compile_chain()` loads via `load_workflow_yaml()` and `validate()` returns no errors.

14. **`test_error_no_terminal_steps`**: Flow with circular deps (no terminals). Assert `ValueError`.

**Depends on**: Steps 1-2 (the functions being tested). Cannot be parallelized with Steps 1-2.

### Step 4: Register `chain` subparser in `cli.py` (~20 min)

**Why fourth**: The subparser is mechanical boilerplate. It must mirror `p_run`'s flags exactly (verified complete list in A8). Doing this separately from the handler keeps the diff small and reviewable.

Add after `p_run` registration (around `cli.py:3680`):

```python
p_chain = sub.add_parser("chain", help="Chain multiple flows into a linear pipeline")
p_chain.add_argument("flows", nargs="+", help="Flow names or paths (executed in order)")
# Copy ALL flags from p_run (verified list from A8):
p_chain.add_argument("--watch", ...)
p_chain.add_argument("--wait", ...)
p_chain.add_argument("--async", dest="async_mode", ...)
# ... (all 18 shared flags)
p_chain.set_defaults(handler=cmd_chain)
```

**Depends on**: Nothing (pure argparse registration). CAN be done in parallel with Steps 1-3 but kept sequential for clean commits.

### Step 5: Implement `cmd_chain()` handler in `cli.py` (~45 min)

**Why fifth**: Depends on Steps 1-2 (compile_chain) and Step 4 (subparser registration). This is the glue that connects flow resolution → compilation → execution.

Handler logic:
1. Validate `len(args.flows) >= 2` — exit with error message if not
2. Resolve each flow name using `resolve_flow()` / `parse_registry_ref()` + `resolve_registry_flow()` — same pattern as `cmd_run` (`cli.py:1767-1784`). Exit on first resolution failure.
3. Parse `--var` flags to extract var names: `[k for k, v in (item.split("=", 1) for item in (args.vars or []))]`
4. Call `compile_chain(flow_paths, var_names)` to get YAML string
5. Write YAML to temp file:
   - Sync modes: `tempfile.NamedTemporaryFile(suffix='.flow.yaml', prefix='stepwise-chain-', delete=False)`
   - `--async` mode: write to `project.dot_dir / "tmp" / f"chain-{uuid4().hex[:8]}.flow.yaml"` (ensure dir exists)
6. Set `args.flow = str(temp_path)` — rewrite the args namespace for `cmd_run`
7. If no `args.objective`: set `args.objective` to the chain description (e.g., `"chain: research-proposal → plan-and-build"`)
8. Call `cmd_run(args)` in a `try` block
9. `finally`: `os.unlink(temp_path)` for sync modes; skip for `--async`

Return `cmd_run`'s return code.

**Depends on**: Steps 1-2 (compile_chain function), Step 4 (subparser exists so handler can be set). Must come after Step 4.

### Step 6: Integration tests in `tests/test_chain.py` (~45 min)

**Why sixth**: Validates the full pipeline — compilation + engine execution. Requires Steps 1-2 (compile_chain) and working engine fixtures from `conftest.py`.

**Test cases:**

15. **`test_integration_two_flow_chain`**: Create two script flows in temp dir:
    - Flow-A: `run: 'echo "{\"result\": \"hello\"}"'`, outputs: `[result]`
    - Flow-B: `run: 'echo "{\"result\": \"$spec world\"}"'`, config: `{spec: {type: str}}`, outputs: `[result]`
    Compile chain → write temp file → load via `load_workflow_yaml()` → create job with `async_engine` → `run_job_sync()` → assert final result contains `"hello world"`.

16. **`test_integration_var_passthrough`**: Chain of two flows, both expecting `$job.project`. Pass `inputs={"project": "test-proj"}`. Assert both sub-jobs receive the project variable.

17. **`test_integration_three_stage_chain`**: Three flows: A outputs `{result: "a"}`, B transforms to `{result: "a+b"}`, C to `{result: "a+b+c"}`. Assert final result.

18. **`test_no_files_in_flows_dir`**: Create a temp flows dir, run a chain, assert no new files in the flows dir.

19. **`test_chain_with_no_config_flow`**: Flow without `config:` block but steps using `$job.spec`. Verify chain correctly wires upstream result to `spec`.

**Depends on**: Steps 1-5 (full implementation). Must come after all implementation steps.

### Step 7: Manual E2E smoke test (~15 min)

**Why last**: Final validation against real flows in the repo. Catches issues that unit/integration tests miss (real YAML complexity, server delegation, web UI rendering).

```bash
# Headless
stepwise chain research-proposal plan-and-build \
  --var topic="event system" --var project=stepwise

# Web UI
stepwise chain research-proposal plan-and-build \
  --var topic="event system" --var project=stepwise \
  --watch

# Blocking JSON
stepwise chain research-proposal plan-and-build \
  --var topic="event system" --var project=stepwise \
  --wait
```

Verify: DAG shows stage-1/stage-2 as expandable sub-flows, result propagates between stages, no files in `flows/`.

**Depends on**: Steps 1-6.

### Step Dependency Graph

```
Step 1 (wiring functions)
  └→ Step 2 (compile_chain)
       └→ Step 3 (unit tests) ─────────┐
       └→ Step 5 (cmd_chain handler) ──┤
            ↑                          │
Step 4 (subparser registration) ───────┤
                                       └→ Step 6 (integration tests)
                                            └→ Step 7 (manual E2E)
```

Steps 3 and 4 CAN be parallelized (no dependency between them). Steps 4 and 5 are sequential (handler references subparser). Step 6 requires all implementation steps.

## Testing Strategy

### Unit Tests

```bash
uv run pytest tests/test_chain.py -v -k "not integration"
```

14 test cases covering:
- Config var matching priority (all 5 priority levels + 2 fallbacks = 7 cases)
- Var passthrough filtering (1 case)
- Multi-flow chaining (1 case)
- Output discovery + non-result output (2 cases)
- Name truncation (1 case)
- Round-trip validation (1 case)
- Error case: no terminal steps (1 case)

### Integration Tests

```bash
uv run pytest tests/test_chain.py -v -k "integration"
```

5 test cases covering:
- End-to-end execution through AsyncEngine with script executors
- Result propagation across 2 and 3 stages
- Var passthrough to sub-jobs
- No-config-var flow handling
- No file persistence after run

### Regression

```bash
uv run pytest tests/                    # full Python suite (~40 test files)
cd web && npm run test                   # frontend (unaffected, but verify no regressions)
```

### Specific Commands

```bash
# Run just the new tests
uv run pytest tests/test_chain.py -v

# Run with coverage to verify chain.py is fully covered
uv run pytest tests/test_chain.py --cov=stepwise.chain --cov-report=term-missing

# Run the existing sub-flow tests to verify no regressions
uv run pytest tests/test_flow_step.py -v

# Run the CLI tests to verify no regressions
uv run pytest tests/test_cli.py -v
```

## Risks & Mitigations

### Risk 1: Sub-flow input mapping mismatch — flow expects vars not in chain's wiring
**Risk**: A flow's steps reference `$job.topic` but the chain only wires `result` → `spec`. The sub-job gets `inputs={"spec": "..."}` but not `{"topic": "..."}`, so `$job.topic` resolves to `None`.
**Mitigation**: Passthrough ALL `--var` keys to every stage that references them. `_scan_job_refs()` discovers all `$job.*` references including undeclared ones. If a flow expects `$job.topic` and the user passes `--var topic=X`, it's wired as `topic: $job.topic` on that stage.
**Residual risk**: If a flow expects `$job.topic` but the user doesn't pass `--var topic=...`, the input resolves to `None`. This is existing engine behavior — not specific to chain.

### Risk 2: Terminal step has no `result` output field
**Risk**: Chain expects `result` from stage N to wire into stage N+1, but the flow's terminal step outputs `[summary, score]`.
**Mitigation**: `compile_chain()` checks if `result` is in the terminal step's output list. If not, uses the first output field and logs a warning: `"Warning: flow '{name}' terminal step outputs {outputs}, using '{first}' as chain result"`. The next stage's auto-wiring binding receives this field instead.
**Detection**: Unit test `test_non_result_output_forward` validates this behavior.

### Risk 3: Temp file cleanup in `--async` mode
**Risk**: `--async` returns immediately. If temp file is cleaned up before runner reads it, execution fails.
**Mitigation**: Verified in A7: `run_async` (`runner.py:1464`) loads the workflow and persists the job to DB (`runner.py:1506-1517`) before spawning the background subprocess. The subprocess receives `--job-id`, not a file path (`runner.py:1523-1538`). So temp cleanup after `run_async` returns is safe. For extra safety, `--async` writes to `.stepwise/tmp/` instead of OS temp.

### Risk 4: Config var priority order doesn't match user's flow
**Risk**: User chains flow-A → flow-B, where flow-B has both `spec` and `topic` config vars. Chain wires `result` → `spec` (higher priority), but user intended `topic`.
**Mitigation**: This is a design limitation documented in the spec. Users needing custom wiring should write a `.flow.yaml` file with explicit input bindings. The chain command is a convenience for the common case.

### Risk 5: Flow loading fails during compilation
**Risk**: `compile_chain()` calls `load_workflow_yaml()` for each flow. If a flow has parse errors, the chain errors without a clear message.
**Mitigation**: Wrap each `load_workflow_yaml()` call in a try/except that includes the flow name in the error: `"Error loading flow '{stem}': {error}"`. Resolution errors are already caught in `cmd_chain` before compilation begins.
