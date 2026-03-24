---
title: "Implementation Plan: Language Consolidation for Stepwise 1.0 (Chunk 1)"
date: "2026-03-23T12:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Language Consolidation for Stepwise 1.0 — Chunk 1

## Overview

Three language changes to align Stepwise's YAML and CLI surface with 1.0 conventions: rename `sequencing:` → `after:` in flow YAML, rename `--var`/`--var-file` → `--input`/`--input X=@file` in CLI, and remove the `stepwise chain` command entirely. All changes include backward compatibility where specified, full test updates, and documentation sweeps.

---

## Requirements

### R1: `sequencing:` → `after:` YAML field rename

**Acceptance criteria:**
- AC1.1: `after:` is the primary field name parsed by `yaml_loader.py` in all three parsing paths (sub-flow at line 727, for_each at line 757, normal at line 781)
- AC1.2: `sequencing:` is accepted as a deprecated alias — silent accept, no warning emitted
- AC1.3: The `StepDefinition` dataclass field is renamed from `sequencing` to `after` (`models.py:436`)
- AC1.4: `to_dict()` serializes as `"after"` (`models.py:459`); `from_dict()` reads `"after"` first, falls back to `"sequencing"` (`models.py:496`)
- AC1.5: All engine readiness checks use `.after` (`engine.py:919`, `engine.py:1033`, `engine.py:1088`, `engine.py:1654`, `engine.py:2063`)
- AC1.6: Validation errors and warnings reference `"after"` not `"sequencing"` (`models.py:609-614`, `models.py:827-837`)
- AC1.7: Web frontend `StepDefinition` interface uses `after: string[]` (`web/src/lib/types.ts:74`)
- AC1.8: All built-in flows (`flows/welcome/FLOW.yaml`, `flows/research-proposal/FLOW.yaml`, `flows/eval-1-0/FLOW.yaml`), examples, and templates use `after:`
- AC1.9: All documentation uses `after:` exclusively
- AC1.10: `grep -rn "sequencing:" src/ tests/ flows/ docs/ examples/ --include="*.py" --include="*.yaml" --include="*.md" --include="*.ts" --include="*.tsx" --include="*.json"` → zero hits except: (a) `yaml_loader.py` deprecated-alias parsing, (b) one backward-compat test

### R2: `--var`/`--var-file` → `--input`/`--input X=@file` CLI rename

**Acceptance criteria:**
- AC2.1: `--input KEY=VALUE` replaces `--var KEY=VALUE` (repeatable) across `run`, `preflight`, and `cache debug` commands
- AC2.2: `--input KEY=@PATH` replaces `--var-file KEY=PATH` — `@` prefix triggers file read inside `parse_inputs()`
- AC2.3: `--var` kept as hidden alias (`help=argparse.SUPPRESS`) mapping to same `dest` as `--input`
- AC2.4: `--var-file` kept as hidden alias mapping to same `dest` as `--input`
- AC2.5: `--inputs-file PATH` replaces `--vars-file PATH`; `--vars-file` kept as hidden alias
- AC2.6: `parse_vars()` renamed to `parse_inputs()` in `runner.py:44`; `load_vars_file()` renamed to `load_inputs_file()` in `runner.py:57`
- AC2.7: `agent_help.py` generates `--input` in all hint strings (lines 54, 60, 116, 253, 267, 373, 400, 514)
- AC2.8: All test files use `--input` in subprocess calls
- AC2.9: `grep -rn "\-\-var " src/ docs/ --include="*.py" --include="*.md"` → zero hits except hidden alias definitions

### R3: Remove `stepwise chain` command

**Acceptance criteria:**
- AC3.1: `src/stepwise/chain.py` deleted (236 lines)
- AC3.2: `cmd_chain()` function removed from `cli.py` (lines 2044-2143)
- AC3.3: Chain subparser removed from `cli.py` (lines 3980-4009)
- AC3.4: `"chain": cmd_chain` removed from handlers dict (`cli.py:4441`)
- AC3.5: `tests/test_chain.py` deleted (410 lines)
- AC3.6: Chain section removed from `docs/cli.md` (lines 162-199, line 11 table)
- AC3.7: Context chains (M7a: `context.py`, `ChainConfig` in `models.py:445-446`, `_compile_chain_context()` in `engine.py:2072`, `test_context_chains.py`) are **completely untouched**
- AC3.8: Tests using "chain" in the English sense (`test_async_engine.py:94` `test_linear_chain_completes`, `test_async_engine.py:181` `test_chain_completes_fast`, `test_concurrency.py:53` `test_chain_reaction_fast`, `test_async_engine.py:44` `linear_chain_wf` helper) are **untouched**

### R4: Documentation sweep

**Acceptance criteria:**
- AC4.1: `CLAUDE.md` — all `sequencing:` → `after:`, all `--var` → `--input`, chain CLI removed
- AC4.2: `README.md` — `--var` → `--input` (lines 40, 47)
- AC4.3: `docs/cli.md` — chain section removed, all `--var` → `--input` (20+ occurrences), `sequencing` → `after` (line 805)
- AC4.4: `docs/yaml-format.md` — all `sequencing:` → `after:` (lines 83, 133, 512, 542, 633)
- AC4.5: `docs/concepts.md` — `sequencing:` → `after:` (lines 131, 265), `--var` → `--input` (lines 23, 335, 363, 366)
- AC4.6: `docs/patterns.md` — all `sequencing:` → `after:` (lines 52, 93, 251, 264, 537)
- AC4.7: `docs/quickstart.md` — `sequencing:` → `after:` (lines 70, 78), `--var` → `--input` (lines 164, 171)
- AC4.8: `docs/agent-integration.md` — all `--var` → `--input` (30+ occurrences)
- AC4.9: `docs/use-cases.md` — `sequencing:` → `after:` (lines 90, 114)
- AC4.10: `docs/how-to/*.md` — all `--var` → `--input`
- AC4.11: `src/stepwise/flow-reference.md` and `src/stepwise/_templates/agent-skill/FLOW_REFERENCE.md` — `sequencing:` → `after:`, `--var` → `--input`
- AC4.12: `src/stepwise/_templates/streaming-demo.json` — `"sequencing"` → `"after"` (6 occurrences at lines 18, 34, 54, 102, 121, 164)

---

## Assumptions

### A1: Persisted JSON blobs in SQLite contain `"sequencing"` key — backward compat needed

**Verification:** `models.py:459` — `to_dict()` writes `"sequencing": self.sequencing`. `models.py:496` — `from_dict()` reads `d.get("sequencing", [])`. Existing DB rows will have `"sequencing"` in their serialized JSON.

**Decision:** `from_dict()` must read `"after"` first, fall back to `"sequencing"`. `to_dict()` writes `"after"` only. This ensures new jobs use the new key while old persisted jobs still deserialize correctly.

### A2: Context chains (M7a) are completely separate from `stepwise chain` CLI

**Verification:** `chain.py` imports only `ConfigVar` and `WorkflowDefinition` from `models.py`. It has zero references to `context.py`, `ChainConfig`, or `_compile_chain_context()`. Conversely, `context.py` has zero references to `chain.py` or `compile_chain()`. The only shared word is "chain" — they are architecturally independent features.

**Files for M7a (DO NOT TOUCH):** `context.py`, `models.py:445-446` (`chain`/`chain_label` fields), `engine.py:2070-2120` (`_compile_chain_context`), `yaml_loader.py:872` (`_parse_chains`), `events.py:37` (`CHAIN_CONTEXT_COMPILED`), `report.py:759-760,953-957,1023-1027`, `cache.py:26`, `tests/test_context_chains.py`.

### A3: `--var` is used in four CLI commands, but chain is being deleted

**Verification:** `cli.py:3958` (run), `cli.py:3989` (chain), `cli.py:4022` (preflight), `cli.py:4177` (cache debug). Since chain is deleted in R3, only `run`, `preflight`, and `cache debug` need the rename.

**Note:** Preflight uses `dest="var"` (default argparse dest) accessed as `args.var` at `cli.py:1681` via `getattr(args, "var", None)`. The other commands use `dest="vars"`. The rename unifies all to `dest="inputs_cli"`.

### A4: The `@file` syntax doesn't conflict with existing value patterns

**Verification:** Searched all test files and example flows — no existing `--var` value starts with `@`. This matches conventions used by `curl -d @file` and `gh issue create --body-file @`. The only edge case is a literal value starting with `@`, which users can work around via `--inputs-file`.

### A5: Server builds DAG JSON from raw YAML dicts AND from `to_dict()` output

**Verification:** `server.py:1596` reads `step_def.get("sequencing", [])` from raw parsed YAML (editor endpoints). The job detail API serializes via `StepDefinition.to_dict()`. Both paths need updating: raw YAML access needs `"after"` with `"sequencing"` fallback; `to_dict()` output changes automatically when the field is renamed.

### A6: The web frontend reads the field name from the JSON API response

**Verification:** `types.ts:74` defines `sequencing: string[]`. `dag-layout.ts:163` iterates `step.sequencing`. `StepDetailPanel.tsx:157` checks `stepDef.sequencing.length`. All derive their data from the server's JSON, which comes from `to_dict()`. When `to_dict()` outputs `"after"`, the frontend must read `"after"`.

---

## Out of Scope

- **M7a context chains** — `ChainConfig`, `context.py`, `test_context_chains.py`, `chain`/`chain_label` fields on `StepDefinition`. Separate feature, untouched. (See A2 for boundary verification.)
- **Job staging with `--after` + `--input`** — the replacement for chain, but that's a separate chunk of the design.
- **Deprecation warnings** — spec says "keep `sequencing:` as deprecated alias for now." No runtime warnings. Same for `--var` — hidden but silent.
- **CHANGELOG.md / version bump** — release-time concern, not in this chunk.
- **`examples/self_analysis.py`** — uses `"sequencing"` in programmatic dict construction (`examples/self_analysis.py:66,113,152,202`). This IS in scope for the rename since it's user-facing example code.

---

## >>>ESCALATE: `--vars-file` rename target

The spec says `--var / --var-file → --input / --input X=@file`. It does not explicitly mention `--vars-file PATH` (the bulk YAML/JSON loader). Two options:

1. Rename to `--inputs-file PATH` (consistent with `--input`)
2. Leave as `--vars-file` (different concept — loading a file of vars, not a single var from a file)

**Recommendation:** Rename to `--inputs-file PATH` for consistency. Keep `--vars-file` as hidden deprecated alias. Proceeding with this assumption unless corrected.

---

## Architecture

### How `after:` fits existing patterns

The rename is purely cosmetic at the language level. The field semantics are identical to `sequencing:` — `after: [step-a]` means "wait for step-a to complete before starting." The engine readiness check at `engine.py:919` adds these to `regular_deps` alongside input binding sources and `for_each` source. The DAG layout at `dag-layout.ts:163` renders them as edges without data labels. Nothing changes except the name.

**Backward-compat pattern:** `from_dict()` already uses `d.get("key", default)` for deserialization (`models.py:490-511`). Adding a fallback `d.get("after") or d.get("sequencing") or []` follows the same pattern used elsewhere (e.g., `d.get("on_error", "fail")` at line 510).

**YAML loader pattern:** Each parsing path uses `step_data.get("sequencing", [])` (`yaml_loader.py:727,757,781`). Adding `step_data.get("after") or step_data.get("sequencing") or []` keeps the same structure. The `or` chain handles the case where `"after"` is present but set to `null` or empty.

### How `--input` fits existing patterns

`parse_vars()` at `runner.py:44-54` splits `KEY=VALUE` on first `=`. Adding `@file` detection after the split is a 4-line addition — if `value.startswith("@")`, read the file. This eliminates the need for the separate `--var-file` processing block in `cmd_run()` (lines 1941-1962), which currently does the same split-then-read-file logic but outside the parser.

The three separate argparse flags (`--var`, `--var-file`, `--vars-file`) become two (`--input`, `--inputs-file`) with old names as hidden aliases via `help=argparse.SUPPRESS`. Argparse allows multiple flags to share the same `dest`, so `--var` and `--input` can both append to `dest="inputs_cli"`.

### Chain removal scope

Surgical deletion: `chain.py` (236 lines), `cmd_chain()` in `cli.py` (lines 2044-2143), chain subparser (lines 3980-4009), handler registration (line 4441), `tests/test_chain.py` (410 lines), and one docs section (`docs/cli.md:162-199`). Zero model, engine, or context changes.

---

## Implementation Steps

### Step 1: Rename `sequencing` field in `StepDefinition` dataclass

**Why first:** All subsequent steps (engine, yaml_loader, server, tests) depend on the field name being `after` on the dataclass. If we update engine.py to reference `.after` before renaming the field, nothing compiles. This is the atomic foundation change.

**File: `src/stepwise/models.py`**

| Line | Current | New |
|------|---------|-----|
| 436 | `sequencing: list[str] = field(default_factory=list)` | `after: list[str] = field(default_factory=list)` |
| 459 | `"sequencing": self.sequencing,` | `"after": self.after,` |
| 496 | `sequencing=d.get("sequencing", []),` | `after=d.get("after") or d.get("sequencing") or [],` |

**Validation and warnings (same file):**

| Line | Current | New |
|------|---------|-----|
| 609 | `# Check sequencing references` | `# Check after references` |
| 610 | `for seq_step in step.sequencing:` | `for seq_step in step.after:` |
| 613 | `f"Step '{name}': sequencing references unknown step '{seq_step}'"` | `f"Step '{name}': after references unknown step '{seq_step}'"` |
| 827 | `# Warn if a step has sequencing on a looping step...` | `# Warn if a step has after on a looping step...` |
| 831 | `for seq in step.sequencing:` | `for seq in step.after:` |
| 834 | `f"⚠ Step '{name}': has sequencing on looping step "` | `f"⚠ Step '{name}': has 'after' on looping step "` |

**Entry/terminal step detection (same file):**

| Line | Current | New |
|------|---------|-----|
| 892 | `"""Steps with no dependencies (no inputs, sequencing, or for_each source).` | `"""Steps with no dependencies (no inputs, after, or for_each source).` |
| 918 | `and not step.sequencing and not has_for_each_dep:` | `and not step.after and not has_for_each_dep:` |
| 943-944 | `for seq in step.sequencing: depended_on.add(seq)` | `for seq in step.after: depended_on.add(seq)` |
| 973 | `own_deps.update(step_def.sequencing)` | `own_deps.update(step_def.after)` |
| 1019-1020 | `for seq in step.sequencing: deps.add(seq)` | `for seq in step.after: deps.add(seq)` |

**Verification after this step:**
```bash
uv run python -c "from stepwise.models import StepDefinition; s = StepDefinition(name='x', outputs=['y'], executor=None, after=['a']); print(s.to_dict()['after'])"
# → ['a']
uv run python -c "from stepwise.models import StepDefinition; s = StepDefinition.from_dict({'name':'x','outputs':['y'],'executor':{'type':'script','config':{}},'sequencing':['a']}); print(s.after)"
# → ['a']
```

### Step 2: Update `yaml_loader.py` parser

**Why second:** Depends on Step 1 (field is now `after`). Must happen before Step 6 (tests that load YAML need the parser to produce `.after`). Independent of Steps 3-5.

**File: `src/stepwise/yaml_loader.py`**

Three parsing paths, identical change pattern:

| Line | Current | New |
|------|---------|-----|
| 727 | `sequencing = step_data.get("sequencing", [])` | `after = step_data.get("after") or step_data.get("sequencing") or []` |
| 728 | `if isinstance(sequencing, str):` | `if isinstance(after, str):` |
| 729 | `sequencing = [sequencing]` | `after = [after]` |
| 738 | `sequencing=sequencing,` | `after=after,` |
| 757 | `sequencing = step_data.get("sequencing", [])` | `after = step_data.get("after") or step_data.get("sequencing") or []` |
| 758 | `if isinstance(sequencing, str):` | `if isinstance(after, str):` |
| 759 | `sequencing = [sequencing]` | `after = [after]` |
| 768 | `sequencing=sequencing,` | `after=after,` |
| 780 | `# Sequencing` | `# After (ordering deps)` |
| 781 | `sequencing = step_data.get("sequencing", [])` | `after = step_data.get("after") or step_data.get("sequencing") or []` |
| 782 | `if isinstance(sequencing, str):` | `if isinstance(after, str):` |
| 783 | `sequencing = [sequencing]` | `after = [after]` |
| 857 | `sequencing=sequencing,` | `after=after,` |

**Verification:**
```bash
uv run python -c "
from stepwise.yaml_loader import load_workflow_yaml
import tempfile, os
# Test new syntax
f = tempfile.NamedTemporaryFile(suffix='.flow.yaml', mode='w', delete=False)
f.write('name: test\nsteps:\n  a:\n    run: echo ok\n    outputs: [x]\n  b:\n    run: echo ok\n    outputs: [y]\n    after: [a]\n')
f.close()
wf = load_workflow_yaml(f.name)
print('new syntax:', wf.steps['b'].after)
os.unlink(f.name)
# Test deprecated syntax
f2 = tempfile.NamedTemporaryFile(suffix='.flow.yaml', mode='w', delete=False)
f2.write('name: test\nsteps:\n  a:\n    run: echo ok\n    outputs: [x]\n  b:\n    run: echo ok\n    outputs: [y]\n    sequencing: [a]\n')
f2.close()
wf2 = load_workflow_yaml(f2.name)
print('deprecated syntax:', wf2.steps['b'].after)
os.unlink(f2.name)
"
# → new syntax: ['a']
# → deprecated syntax: ['a']
```

### Step 3: Update `engine.py` references

**Why third:** Depends on Step 1 (field renamed). Independent of Steps 2, 4, 5. Must happen before Step 6 (engine tests reference `step_def.after`).

**File: `src/stepwise/engine.py`**

| Line | Current | New |
|------|---------|-----|
| 919 | `regular_deps.extend(step_def.sequencing)` | `regular_deps.extend(step_def.after)` |
| 1033 | `regular_dep_steps.extend(step_def.sequencing)` | `regular_dep_steps.extend(step_def.after)` |
| 1082 | `"""All dependency steps: input binding sources + sequencing + for_each source."""` | `"""All dependency steps: input binding sources + after + for_each source."""` |
| 1088 | `deps.extend(step_def.sequencing)` | `deps.extend(step_def.after)` |
| 1654 | `for seq in step.sequencing:` | `for seq in step.after:` |
| 2062 | `# Record sequencing deps` | `# Record after deps` |
| 2063 | `for seq_step in step_def.sequencing:` | `for seq_step in step_def.after:` |

### Step 4: Update remaining Python backend files

**Why fourth:** Depends on Step 1 (field renamed). Independent of Steps 2, 3. Groups remaining scattered references.

**`src/stepwise/server.py`** — raw YAML dict access (editor endpoints):

| Line | Current | New |
|------|---------|-----|
| 1595 | `# Sequencing edges` | `# After edges (ordering deps)` |
| 1596 | `sequencing = step_def.get("sequencing", [])` | `after_deps = step_def.get("after") or step_def.get("sequencing") or []` |
| 1597 | `if isinstance(sequencing, str):` | `if isinstance(after_deps, str):` |
| 1598 | `sequencing = [sequencing]` | `after_deps = [after_deps]` |
| 1599 | `for seq_dep in sequencing:` | `for seq_dep in after_deps:` |
| 2264 | `# Cascade: remove input bindings and sequencing refs to the deleted step` | `# Cascade: remove input bindings and after refs to the deleted step` |
| 2277-2282 | `seq = other_step.get("sequencing")` etc. | `seq = other_step.get("after") or other_step.get("sequencing")` + clean both keys |

**`src/stepwise/context.py:74`:**

| Line | Current | New |
|------|---------|-----|
| 74 | `for seq in step.sequencing:` | `for seq in step.after:` |

**`src/stepwise/report.py`:**

| Line | Current | New |
|------|---------|-----|
| 120 | `d.update(step.sequencing)` | `d.update(step.after)` |
| 840 | `deps.update(step.sequencing)` | `deps.update(step.after)` |

**`src/stepwise/cli.py`** (diagram command):

| Line | Current | New |
|------|---------|-----|
| 1106 | `for seq in step.sequencing:` | `for seq in step.after:` |
| 1196 | `# Sequencing edges` | `# After edges` |
| 1197 | `for dep in step.sequencing:` | `for dep in step.after:` |

### Step 5: Update web frontend for `after` field

**Why fifth:** Depends on Steps 1 + 4 (backend now serializes `"after"`). Must happen before web tests in Step 6. Independent of Python tests.

**`web/src/lib/types.ts:74`:** `sequencing: string[]` → `after: string[]`

**`web/src/lib/dag-layout.ts`:**
| Line | Current | New |
|------|---------|-----|
| 136 | `// Add edges from input bindings and sequencing` | `// Add edges from input bindings and after deps` |
| 163 | `for (const seq of step.sequencing) {` | `for (const seq of step.after) {` |
| 497 | `for (const seq of step.sequencing) {` | `for (const seq of step.after) {` |
| 530 | `for (const seq of step.sequencing) referencedAsSource.add(seq);` | `for (const seq of step.after) referencedAsSource.add(seq);` |

**`web/src/components/jobs/StepDetailPanel.tsx`:**
| Line | Current | New |
|------|---------|-----|
| 157 | `{stepDef.sequencing.length > 0 && (` | `{stepDef.after.length > 0 && (` |
| 161 | `{stepDef.sequencing.join(", ")}` | `{stepDef.after.join(", ")}` |

**`web/src/components/editor/StepDefinitionPanel.tsx`:**
| Line | Current | New |
|------|---------|-----|
| 244 | `stepDef.sequencing.length > 0 ||` | `stepDef.after.length > 0 ||` |
| 592 | `{stepDef.sequencing.length > 0 && (` | `{stepDef.after.length > 0 && (` |
| 596 | `{stepDef.sequencing.map((s) => (` | `{stepDef.after.map((s) => (` |

**Test files (4):**
| File:Line | Current | New |
|-----------|---------|-----|
| `dag-layout.test.ts:9` | `sequencing?: string[]` | `after?: string[]` |
| `dag-layout.test.ts:18` | `sequencing: opts.sequencing ?? []` | `after: opts.after ?? []` |
| `dag-layout.test.ts:155` | `"deduplicates edges when same dependency comes from inputs and sequencing"` | `"deduplicates edges when same dependency comes from inputs and after"` |
| `dag-layout.test.ts:163` | `sequencing: ["A"]` | `after: ["A"]` |
| `dag-layout.test.ts:201` | `"uses sequencing-only edges for ordering"` | `"uses after-only edges for ordering"` |
| `dag-layout.test.ts:205` | `sequencing: ["A"]` | `after: ["A"]` |
| `dag-layout.test.ts:251` | `"sequencing-only edges have empty labels"` | `"after-only edges have empty labels"` |
| `dag-layout.test.ts:255,269` | `sequencing: ["A"]` | `after: ["A"]` |
| `StepNode.test.tsx:13` | `sequencing: []` | `after: []` |
| `FlowDagView.touch.test.tsx:14` | `sequencing: []` | `after: []` |
| `StepDefinitionPanel.test.tsx:29` | `sequencing: []` | `after: []` |

**Verification:**
```bash
cd web && npx vitest run --reporter=verbose 2>&1 | tail -20
```

### Step 6: Update Python tests for `after` field

**Why sixth:** Depends on Steps 1-4 (all Python code now uses `.after`). Tests must be updated to match. Must pass before proceeding to the `--input` rename (Steps 7-8 touch `cli.py` too, so a clean test run here establishes a checkpoint).

**8 test files with exact changes:**

1. **`tests/test_engine.py`**
   - Line 434: rename `test_sequencing_dep_must_rerun` → `test_after_dep_must_rerun`
   - Line 459: `sequencing=["implement"]` → `after=["implement"]`

2. **`tests/test_yaml_loader.py`**
   - Line 241: rename `test_sequencing` → `test_after`
   - Line 251: `sequencing: [a]` → `after: [a]` in YAML string
   - Line 253: `assert wf.steps["b"].sequencing == ["a"]` → `assert wf.steps["b"].after == ["a"]`
   - Line 255: rename `test_sequencing_string` → `test_after_string`
   - Line 266: `sequencing: a` → `after: a` in YAML string
   - Line 268: `.sequencing` → `.after`
   - Line 515: rename `test_sequencing_unknown_step` → `test_after_unknown_step`
   - Line 522: `sequencing: [nonexistent]` → `after: [nonexistent]` in YAML string
   - Line 562: `sequencing: [review]` → `after: [review]` in YAML string
   - **Add new test:** `test_sequencing_backward_compat` — load YAML with `sequencing: [a]`, assert `wf.steps["b"].after == ["a"]`

3. **`tests/test_models.py`**
   - Line 160: rename `test_sequencing_missing_step` → `test_after_missing_step`
   - Line 165: `sequencing=["nonexistent"]` → `after=["nonexistent"]`
   - Line 215: `sequencing=["a"]` → `after=["a"]`
   - **Add new test:** `test_from_dict_sequencing_backward_compat` — verify `StepDefinition.from_dict({"sequencing": ["a"], ...}).after == ["a"]`
   - **Add new test:** `test_to_dict_uses_after_key` — verify `"after"` in `step.to_dict()` and `"sequencing"` not in `step.to_dict()`

4. **`tests/test_validation.py`**
   - Lines 230-233: rename `test_ungated_sequencing_on_loop_target` → `test_ungated_after_on_loop_target`; update docstring
   - Line 251: `sequencing=["review"]` → `after=["review"]`
   - Lines 261-262: rename `test_gated_sequencing_on_loop_target_no_warning` → `test_gated_after_on_loop_target_no_warning`
   - Line 283: `sequencing=["review"]` → `after=["review"]`
   - Lines 291-292: rename `test_self_loop_sequencing_warning`
   - Line 308: `sequencing=["retry_step"]` → `after=["retry_step"]`
   - Line 319: update docstring
   - Line 326: `sequencing=["step_a"]` → `after=["step_a"]`

5. **`tests/test_m4_async.py:635`:** `sequencing=["score"]` → `after=["score"]`

6. **`tests/test_poll_executor.py:222`:** `sequencing=["create-pr"]` → `after=["create-pr"]`

7. **`tests/test_runner.py:336`:** `sequencing: [count]` → `after: [count]` in YAML string

8. **`tests/test_research_proposal_flow.py:159`:** `sequencing=["init"]` → `after=["init"]`

**Checkpoint verification:**
```bash
uv run pytest tests/test_models.py tests/test_yaml_loader.py tests/test_validation.py tests/test_engine.py -v
# Then full suite:
uv run pytest tests/ -x
```

### Step 7: Rename `parse_vars` → `parse_inputs` in `runner.py`

**Why seventh:** Independent of Steps 1-6. Must happen before Step 8 (cli.py imports from runner.py). Establishes the new function API.

**File: `src/stepwise/runner.py`**

1. **Line 44-54:** Rename function + add `@file` handling:
   ```python
   def parse_inputs(input_list: list[str] | None) -> dict[str, str]:
       """Parse --input KEY=VALUE flags. KEY=@PATH reads file contents."""
       result: dict[str, str] = {}
       if not input_list:
           return result
       for item in input_list:
           if "=" not in item:
               raise ValueError(f"Invalid --input format: '{item}' (expected KEY=VALUE)")
           key, value = item.split("=", 1)
           if value.startswith("@"):
               fpath = Path(value[1:])
               if not fpath.exists():
                   raise ValueError(f"Input file not found: {fpath}")
               result[key] = fpath.read_text()
           else:
               result[key] = value
       return result
   ```

2. **Line 57-76:** Rename `load_vars_file` → `load_inputs_file`, update error message:
   - Line 62: `f"Inputs file not found: {path}"`

3. **After both functions:** Add backward-compat aliases:
   ```python
   parse_vars = parse_inputs  # deprecated alias
   load_vars_file = load_inputs_file  # deprecated alias
   ```

**Verification:**
```bash
uv run python -c "
from stepwise.runner import parse_inputs
import tempfile
f = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False)
f.write('hello world')
f.close()
print(parse_inputs(['key=value', f'spec=@{f.name}']))
"
# → {'key': 'value', 'spec': 'hello world'}
```

### Step 8: Rename `--var` → `--input` in CLI argparse definitions

**Why eighth:** Depends on Step 7 (function renames). Touches `cli.py` which was also modified in Step 4, but different sections (argparse defs vs diagram code).

**File: `src/stepwise/cli.py`**

**Run command (lines 3958-3962):**
```python
p_run.add_argument("--input", action="append", dest="inputs_cli", metavar="KEY=VALUE",
                   help="Pass input (repeatable; KEY=@FILE reads file contents)")
p_run.add_argument("--var", action="append", dest="inputs_cli", help=argparse.SUPPRESS)
p_run.add_argument("--var-file", action="append", dest="inputs_cli", help=argparse.SUPPRESS)
p_run.add_argument("--inputs-file", dest="inputs_file",
                   help="Load inputs from YAML/JSON file")
p_run.add_argument("--vars-file", dest="inputs_file", help=argparse.SUPPRESS)
```

**Preflight command (line 4022):**
```python
p_preflight.add_argument("--input", action="append", dest="inputs_cli",
                         help="Input override (key=value)")
p_preflight.add_argument("--var", action="append", dest="inputs_cli", help=argparse.SUPPRESS)
```

**Cache debug command (lines 4177-4178):**
```python
p_cache_debug.add_argument("--input", action="append", dest="inputs_cli", metavar="KEY=VALUE",
                           help="Input variable (repeatable)")
p_cache_debug.add_argument("--var", action="append", dest="inputs_cli", help=argparse.SUPPRESS)
```

**Consumption in `cmd_run()` (lines 1920-1962):**
- Line 1920: `if args.vars_file:` → `if args.inputs_file:`
- Line 1922: `load_vars_file(args.vars_file)` → `load_inputs_file(args.inputs_file)`
- Line 1932: `parse_vars(args.vars)` → `parse_inputs(args.inputs_cli)`
- Lines 1941-1962: **Delete entire `--var-file` processing block** — `@file` handling is now in `parse_inputs()`
- Update import: `from stepwise.runner import parse_inputs, load_inputs_file`

**Consumption in `cmd_preflight()` (lines 1680-1686):**
- `getattr(args, "var", None)` → `getattr(args, "inputs_cli", None)`

**Consumption in `cmd_cache_debug()` (line 4322):**
- `parse_vars(getattr(args, "vars", None))` → `parse_inputs(getattr(args, "inputs_cli", None))`

### Step 9: Remove `stepwise chain` command

**Why ninth:** Independent of Steps 1-8 but placed here because it touches `cli.py` which was modified in Steps 4 and 8. Doing it after those changes avoids merge conflicts within the same step.

1. **Delete** `src/stepwise/chain.py` (236 lines)
2. **Delete** `tests/test_chain.py` (410 lines)
3. **`cli.py`:** Remove `cmd_chain()` function (lines 2044-2143)
4. **`cli.py`:** Remove chain subparser block (lines 3980-4009 — but line numbers shifted from Step 8 changes; find by `p_chain = sub.add_parser("chain"`)
5. **`cli.py`:** Remove `"chain": cmd_chain` from handlers dict (line 4441)

**DO NOT TOUCH** (verified in A2):
- `test_async_engine.py:44` (`linear_chain_wf`), `:94` (`test_linear_chain_completes`), `:181` (`test_chain_completes_fast`)
- `test_concurrency.py:53` (`test_chain_reaction_fast`)
- `context.py`, `models.py:445-446`, `engine.py:2070-2120`, `test_context_chains.py`

### Step 10: Update `agent_help.py`

**Why tenth:** Depends on Step 8 (CLI flags renamed). Independent of Steps 11-14.

**File: `src/stepwise/agent_help.py`**

| Line | Current | New |
|------|---------|-----|
| 54 | `# Use config var descriptions for --var hints when available` | `# Use config var descriptions for --input hints when available` |
| 60 | `var_parts.append(f'--var {inp}="{hint}"')` | `var_parts.append(f'--input {inp}="{hint}"')` |
| 116 | `var_args = " ".join(f'--var {inp}="..."' for inp in inputs)` | `var_args = " ".join(f'--input {inp}="..."' for inp in inputs)` |
| 253 | `"  \`stepwise run <flow> --wait --var k=v\`",` | `"  \`stepwise run <flow> --wait --input k=v\`",` |
| 267 | `"  \`stepwise run <flow> --async --var k=v\`",` | `"  \`stepwise run <flow> --async --input k=v\`",` |
| 373 | `"\`stepwise run <flow> --wait --var k=v\` — run and block for JSON result.",` | `"\`stepwise run <flow> --wait --input k=v\` — run and block for JSON result.",` |
| 400 | `"result=$(stepwise run meeting-ingest.flow.yaml --wait --var audio=rec.mp3)",` | `"result=$(stepwise run meeting-ingest.flow.yaml --wait --input audio=rec.mp3)",` |
| 514 | `"stepwise run <flow> --wait --var k=v     # run, block, get JSON",` | `"stepwise run <flow> --wait --input k=v     # run, block, get JSON",` |

### Step 11: Update Python tests for `--input` rename

**Why eleventh:** Depends on Steps 7-8 (functions and CLI flags renamed). Must pass before documentation changes.

**`tests/test_runner.py` (lines 236-281):**
- Line 236: `class TestParseVars:` → `class TestParseInputs:`
- Line 237: `"""--var flag parsing."""` → `"""--input flag parsing."""`
- Lines 240,244,248,252,256: `parse_vars(...)` → `parse_inputs(...)`
- Lines 259-261: Update error message match: `"KEY=VALUE"` → `"KEY=VALUE"` (same pattern, but function name changed)
- Line 264: `class TestLoadVarsFile:` → `class TestLoadInputsFile:`
- Lines 270,276: `load_vars_file(...)` → `load_inputs_file(...)`
- Line 280: `load_vars_file(...)` → `load_inputs_file(...)`
- **Add new tests:**
  ```python
  def test_at_file_reads_contents(self, tmp_path):
      f = tmp_path / "spec.md"
      f.write_text("hello world")
      result = parse_inputs([f"spec=@{f}"])
      assert result == {"spec": "hello world"}

  def test_at_file_missing_raises(self):
      with pytest.raises(ValueError, match="Input file not found"):
          parse_inputs(["spec=@/nonexistent/file.md"])

  def test_mixed_inline_and_file(self, tmp_path):
      f = tmp_path / "data.txt"
      f.write_text("file content")
      result = parse_inputs(["name=hello", f"data=@{f}"])
      assert result == {"name": "hello", "data": "file content"}
  ```

**`tests/test_cli_tools.py`** — all `--var` → `--input`, `--var-file` → `--input KEY=@PATH`:
| Line | Current | New |
|------|---------|-----|
| 248 | `"--var", "question=What is 2+2?"` | `"--input", "question=What is 2+2?"` |
| 271 | `assert "--var" in result["error"]` | `assert "--input" in result["error"]` |
| 309 | `"--var", "question=test"` | `"--input", "question=test"` |
| 331 | `"--var", "question=test"` | `"--input", "question=test"` |
| 475 | `assert '--var question="..."' in output` | `assert '--input question="..."' in output` |
| 616 | `"--var", "question=test"` | `"--input", "question=test"` |
| 659 | `"--var", "repo=/tmp/test"` | `"--input", "repo=/tmp/test"` |
| 669 | `class TestVarFile:` → `class TestInputFile:` |
| 671 | `"""--var-file reads file contents as variable value."""` → `"""--input KEY=@FILE reads file contents."""` |
| 679 | `"--var-file", f"question={question_file}"` → `"--input", f"question=@{question_file}"` |
| 690 | `"--var-file", "question=/nonexistent/file.txt"` → `"--input", "question=@/nonexistent/file.txt"` |
| 879 | `"--var", "question=hello"` | `"--input", "question=hello"` |
| 942 | `"--var", "repo=/tmp/test"` | `"--input", "repo=/tmp/test"` |

**`tests/test_agent_ergonomics.py:329`:** `"--var", "repo=test"` → `"--input", "repo=test"`

**Checkpoint verification:**
```bash
uv run pytest tests/test_runner.py tests/test_cli_tools.py tests/test_agent_ergonomics.py -v
# Then full suite:
uv run pytest tests/ -x
```

### Step 12: Update built-in flows, examples, and templates

**Why twelfth:** Independent of code changes. These files are user-facing YAML and reference docs that must use the new syntax.

**Flow YAML files — `sequencing:` → `after:`:**
| File | Lines |
|------|-------|
| `flows/welcome/FLOW.yaml` | 131, 169 |
| `flows/research-proposal/FLOW.yaml` | 42 |
| `flows/eval-1-0/FLOW.yaml` | 39, 146, 164, 180 |
| `examples/generate-homepage.flow.yaml` | 925, 1039 |

**Python example — `"sequencing"` → `"after"` in dict literals:**
| File | Lines |
|------|-------|
| `examples/self_analysis.py` | 66, 113, 152, 202 |

**Templates — `"sequencing"` → `"after"` and `--var` → `--input`:**
| File | Lines | Changes |
|------|-------|---------|
| `src/stepwise/_templates/streaming-demo.json` | 18, 34, 54, 102, 121, 164 | `"sequencing"` → `"after"` |
| `src/stepwise/_templates/agent-skill/FLOW_REFERENCE.md` | 44, 46, 308, 311, 312, 347, 350, 432, 567, 668 | `sequencing:` → `after:`, `--var` → `--input` |
| `src/stepwise/flow-reference.md` | 46, 311, 312, 432, 567, 668 | Same |

### Step 13: Update all documentation

**Why thirteenth:** Depends on code changes being finalized (Steps 1-11). Docs reference exact flag names and field names.

**`CLAUDE.md`** — comprehensive sweep:
- Line 44: `--var` → `--input` in cache debug
- Line 239: `sequencing: [step-x]` → `after: [step-x]`
- Line 377: `--var project_path=` → `--input project_path=`
- All YAML examples with `sequencing:` → `after:`
- Chain command references removed from CLI mode table

**`README.md`:**
| Line | Current | New |
|------|---------|-----|
| 40 | `stepwise run council --wait --var question=` | `stepwise run council --wait --input question=` |
| 47 | `which \`--var\` flags to add` | `which \`--input\` flags to add` |

**`docs/cli.md`:**
- Line 11: Remove `chain` from command table
- Lines 90-146: All `--var` → `--input`, `--var-file` → `--input KEY=@FILE`, `--vars-file` → `--inputs-file`
- Lines 162-199: **Delete entire chain section**
- Lines 266, 272: `--var` → `--input`
- Line 805: `"gray dashed for sequencing"` → `"gray dashed for after"` (or `"gray dashed for ordering deps"`)
- Lines 876, 886: `--var` → `--input`

**`docs/yaml-format.md`:**
| Line | Current | New |
|------|---------|-----|
| 83 | `sequencing: [review]` | `after: [review]` |
| 133 | `sequencing: [step_a, step_b]` | `after: [step_a, step_b]` |
| 512 | `sequencing deps` | `after deps` |
| 542 | `sequencing: [review]` | `after: [review]` |
| 595 | `--var` → `--input`, `--vars-file` → `--inputs-file` |
| 597 | `--var` → `--input` |
| 633 | `sequencing: [a, b]` / `StepDefinition.sequencing` | `after: [a, b]` / `StepDefinition.after` |

**`docs/concepts.md`:**
| Line | Current | New |
|------|---------|-----|
| 23 | `--var repo=` | `--input repo=` |
| 131 | `sequencing: [deploy]` | `after: [deploy]` |
| 265 | `sequencing: [step-x]` | `after: [step-x]` |
| 335 | `--var question=` | `--input question=` |
| 363 | `--var question=` | `--input question=` |
| 366 | `--var-file key=path` | `--input key=@path` |

**`docs/patterns.md`:**
| Line | Current | New |
|------|---------|-----|
| 8 | `--var` | `--input` |
| 52, 93, 251 | `sequencing: [...]` | `after: [...]` |
| 264 | `sequencing: [analyze]` and explanation text | `after: [analyze]` + update explanation |
| 537 | `sequencing: [step]` | `after: [step]` |

**`docs/quickstart.md`:**
| Line | Current | New |
|------|---------|-----|
| 70 | `sequencing: [decide]` | `after: [decide]` |
| 78 | `via \`sequencing\`` | `via \`after\`` |
| 164 | `--var repo_path=` | `--input repo_path=` |
| 171 | `--var flags` | `--input flags` |

**`docs/use-cases.md`:**
| Line | Current | New |
|------|---------|-----|
| 90 | `sequencing: [review]` | `after: [review]` |
| 114 | `sequencing: [build]` | `after: [build]` |

**`docs/agent-integration.md`:** 30+ `--var` → `--input` replacements (lines 3, 12, 80, 89, 96, 103, 106, 109, 112, 151, 155, 185, 210, 231, 286, 308, 318, 328, 333, 344, 346, 352). Also `--var-file` → `--input KEY=@FILE` at lines 106, 112, 333.

**`docs/how-to/app-developer.md`:** Lines 43-44, 88, 145, 193, 248, 302, 305, 317 — all `--var` → `--input`, `--var-file` → `--input KEY=@FILE`, `--vars-file` → `--inputs-file`.

**`docs/how-to/codex-opencode.md`:** Lines 49, 74, 82, 101, 139, 178, 201 — all `--var` → `--input`.

**`docs/how-to/claude-code.md`:** Lines 49, 74, 88, 150, 166 — all `--var` → `--input`.

### Step 14: Verification sweep and final test run

**Why last:** Confirms zero regressions and zero leftover references. Depends on all prior steps.

**Grep verification (must produce zero hits outside allowed locations):**

```bash
# sequencing: in code/tests/docs — only allowed in yaml_loader.py fallback and backward-compat test
grep -rn 'sequencing' src/ tests/ flows/ docs/ examples/ \
  --include="*.py" --include="*.yaml" --include="*.md" \
  --include="*.ts" --include="*.tsx" --include="*.json" \
  | grep -v 'yaml_loader.py' \
  | grep -v 'backward_compat' \
  | grep -v 'context.py' \
  | grep -v 'CHANGELOG.md' \
  | grep -v 'node_modules'
# Expected: only M7a-related hits in context.py, and backward-compat aliases in runner.py

# --var as a flag — only allowed in argparse.SUPPRESS lines
grep -rn '\-\-var[ "'"'"']' src/ docs/ \
  --include="*.py" --include="*.md" \
  | grep -v 'SUPPRESS' \
  | grep -v 'CHANGELOG.md'
# Expected: zero hits

# chain command — should be gone from cli.py
grep -n 'cmd_chain\|"chain".*cmd\|p_chain' src/stepwise/cli.py
# Expected: zero hits
```

**Full test suite:**
```bash
# Python — all tests
uv run pytest tests/ -x -v

# Web — all tests
cd web && npx vitest run --reporter=verbose

# Smoke test — end-to-end validation
uv run stepwise validate flows/welcome/FLOW.yaml
uv run stepwise validate flows/eval-1-0/FLOW.yaml
uv run stepwise validate flows/research-proposal/FLOW.yaml
```

---

## Testing Strategy

### Unit tests — new test cases to add

| Test | File | What it verifies |
|------|------|------------------|
| `test_sequencing_backward_compat` | `test_yaml_loader.py` | YAML with `sequencing: [a]` parses to `step.after == ["a"]` |
| `test_from_dict_sequencing_backward_compat` | `test_models.py` | `from_dict({"sequencing": ["a"], ...}).after == ["a"]` |
| `test_to_dict_uses_after_key` | `test_models.py` | `"after" in step.to_dict()` and `"sequencing" not in step.to_dict()` |
| `test_at_file_reads_contents` | `test_runner.py` | `parse_inputs(["spec=@/tmp/file"])` → file contents |
| `test_at_file_missing_raises` | `test_runner.py` | `parse_inputs(["spec=@/nonexistent"])` → `ValueError` |
| `test_mixed_inline_and_file` | `test_runner.py` | Both `KEY=VALUE` and `KEY=@FILE` in same call |

### Integration tests — existing tests updated

| Test class | File | What changes |
|------------|------|-------------|
| `TestParseVars` → `TestParseInputs` | `test_runner.py:236` | Function calls, class name |
| `TestLoadVarsFile` → `TestLoadInputsFile` | `test_runner.py:264` | Function calls, class name |
| `TestVarFile` → `TestInputFile` | `test_cli_tools.py:669` | `--var-file KEY=PATH` → `--input KEY=@PATH` |
| `TestWaitBasic` | `test_cli_tools.py:248,264` | `--var` → `--input` in subprocess args |
| `TestRerun` | `test_cli_tools.py:309` | `--var` → `--input` |
| `TestAgentHelp` | `test_cli_tools.py:475` | Assert `--input` in output instead of `--var` |
| All sequencing tests | 8 test files | `sequencing=` → `after=` in constructors |

### Test execution commands

```bash
# Step 6 checkpoint — after field rename
uv run pytest tests/test_models.py tests/test_yaml_loader.py tests/test_validation.py tests/test_engine.py tests/test_m4_async.py tests/test_poll_executor.py tests/test_runner.py tests/test_research_proposal_flow.py -v

# Step 11 checkpoint — --input rename
uv run pytest tests/test_runner.py::TestParseInputs tests/test_runner.py::TestLoadInputsFile -v
uv run pytest tests/test_cli_tools.py -v
uv run pytest tests/test_agent_ergonomics.py -v

# Step 14 — full suite
uv run pytest tests/ -x -v
cd web && npx vitest run --reporter=verbose

# Smoke tests
uv run stepwise validate flows/welcome/FLOW.yaml
uv run stepwise validate flows/eval-1-0/FLOW.yaml
```

---

## Dependencies — Step Ordering Rationale

```
Step 1 (models.py field rename)
├── Step 2 (yaml_loader.py) — needs field named `after`
├── Step 3 (engine.py) — needs `.after` attribute
├── Step 4 (server.py, context.py, report.py, cli.py diagram) — needs `.after` attribute
└── Step 5 (web frontend) — needs `"after"` in JSON
    └── Step 6 (Python tests for `after`) — needs all code updated before tests can pass
        └── CHECKPOINT: `uv run pytest tests/ -x`

Step 7 (runner.py function rename) — independent of Steps 1-6
└── Step 8 (cli.py argparse rename) — imports from runner.py
    └── Step 9 (delete chain) — touches cli.py after Step 8
    └── Step 10 (agent_help.py) — uses same flag names as Step 8
        └── Step 11 (tests for --input) — needs all code updated before tests can pass
            └── CHECKPOINT: `uv run pytest tests/ -x`

Step 12 (flows, examples, templates) — independent of code changes, but logically after code is stable
Step 13 (docs) — independent, but logically last before verification
Step 14 (verification sweep) — must be last, confirms everything
```

**Two parallel tracks:** Steps 1-6 (field rename) and Steps 7-11 (CLI rename) are independent and could run in parallel, though sequential execution is simpler and avoids conflicts in `cli.py`. Step 9 (chain removal) is placed after Step 8 to avoid line-number conflicts in `cli.py` but is otherwise independent.

---

## Risks & Mitigations

### Risk 1: Persisted jobs in SQLite have `"sequencing"` in JSON blobs
- **Impact:** Existing jobs loaded from DB would have `step.after = []` if `from_dict()` only reads `"after"`.
- **Mitigation:** `from_dict()` falls back: `d.get("after") or d.get("sequencing") or []` (Step 1).
- **Verification:** New test `test_from_dict_sequencing_backward_compat` in `test_models.py`.

### Risk 2: External tools parsing `to_dict()` JSON expecting `"sequencing"`
- **Impact:** Any external consumer reading `step["sequencing"]` from the API will get `KeyError`.
- **Mitigation:** This is a 1.0 language change — breaking API changes are expected. The web frontend is updated in the same PR. No known external consumers.

### Risk 3: `--var` used in user scripts and CI pipelines
- **Impact:** Users calling `stepwise run --var` in scripts would break.
- **Mitigation:** `--var` is kept as a hidden deprecated alias with `help=argparse.SUPPRESS`, mapping to the same `dest` as `--input`. **Zero breakage.** Aliases verified in Step 8.

### Risk 4: `@` at start of a value interpreted as file reference unintentionally
- **Impact:** `--input key=@literal` would try to read a file instead of using the literal value.
- **Mitigation:** Matches established conventions (`curl -d @file`). Users with literal `@` values use `--inputs-file` or pipe via stdin. Documented in help text.

### Risk 5: Removing `stepwise chain` breaks existing users
- **Impact:** `stepwise chain flow1 flow2` returns "unknown command."
- **Mitigation:** Chain was added recently. Low adoption. Job staging with `--after` + `--input` is the replacement (separate chunk). CHANGELOG notes the removal at release.

### Risk 6: Accidentally modifying M7a context chain code
- **Impact:** Breaking session continuity across agent steps.
- **Mitigation:** Explicit boundary documented in A2 with exhaustive file list. The grep verification in Step 14 confirms no M7a files were touched. Step 9 explicitly lists "DO NOT TOUCH" files.

### Risk 7: Line number drift from sequential edits to `cli.py`
- **Impact:** Steps 4, 8, and 9 all modify `cli.py`. Line numbers cited may drift.
- **Mitigation:** Each step identifies changes by content pattern (e.g., `p_chain = sub.add_parser("chain")`), not just line number. Changes are in non-overlapping sections: Step 4 touches lines ~1106/1197 (diagram), Step 8 touches lines ~1920/3958/4022/4177 (argparse + consumption), Step 9 touches lines ~2044-2143/3980-4009/4441 (chain).

---

## Commit Sequence

1. `refactor: rename sequencing → after in models, engine, and backend` (Steps 1-4)
2. `refactor: rename sequencing → after in web frontend` (Step 5)
3. `test: update all tests for after field rename` (Step 6) — checkpoint: `uv run pytest tests/ -x`
4. `refactor: rename --var → --input in CLI and runner` (Steps 7-8, 10)
5. `test: update all tests for --input rename` (Step 11) — checkpoint: `uv run pytest tests/ -x`
6. `remove: delete stepwise chain command` (Step 9)
7. `docs: update all flows, examples, templates, and documentation` (Steps 12-13)
8. `chore: verification sweep — grep clean + full test pass` (Step 14)
