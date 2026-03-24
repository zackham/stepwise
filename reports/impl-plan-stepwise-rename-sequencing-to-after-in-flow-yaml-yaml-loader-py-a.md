---
title: "Implementation Plan: Rename sequencing: to after: in flow YAML"
date: "2026-03-23T21:30:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Rename `sequencing:` to `after:` in flow YAML

## Overview

Rename the `sequencing` field to `after` across the entire Stepwise codebase — YAML surface, Python dataclass, engine internals, CLI, web frontend, tests, docs, and built-in flows. The old `sequencing:` key is retained as a silent deprecated alias in YAML parsing and `from_dict()` deserialization for backward compatibility with existing flows and persisted SQLite data.

## Requirements

### R1: `after:` as primary YAML field
- **AC1.1:** `after: [step-a]` and `after: step-a` (string shorthand) are accepted by `yaml_loader.py` in all three step-type parsing paths: sub-flow (line 727), for-each (line 757), normal (line 781)
- **AC1.2:** `sequencing:` is accepted as a deprecated alias — silent accept, no warning emitted
- **AC1.3:** If both `after:` and `sequencing:` are present on the same step, raise a `ValueError` with message `"cannot use both 'after' and 'sequencing'"`
- **AC1.4:** `after: null` and `after: []` both resolve to empty list (no ordering deps)

### R2: Python field rename
- **AC2.1:** `StepDefinition.sequencing` field (line 436) renamed to `StepDefinition.after`
- **AC2.2:** `to_dict()` serializes as `"after"` key (line 459)
- **AC2.3:** `from_dict()` reads `"after"` first, falls back to `"sequencing"` for backward compat with persisted data (line 496)
- **AC2.4:** All 14 `.sequencing` attribute accesses across `models.py` (5), `engine.py` (6), `cli.py` (2), `context.py` (1), `report.py` (2) updated to `.after`
- **AC2.5:** Comment in `chain.py:115-119` updated to reference "after" instead of "sequencing"

### R3: Validation messages updated
- **AC3.1:** `models.py:613` error message: `"after references unknown step"` (was `"sequencing references"`)
- **AC3.2:** `models.py:834` warning message: `"has 'after' on looping step"` (was `"has sequencing on"`)

### R4: Server and web frontend updated
- **AC4.1:** `server.py:1599-1602` DAG edge builder reads `"after"` from raw YAML with `"sequencing"` fallback
- **AC4.2:** `server.py:2267-2285` step deletion cascade cleans both `"after"` and `"sequencing"` keys
- **AC4.3:** `web/src/lib/types.ts:74` field renamed from `sequencing: string[]` to `after: string[]`
- **AC4.4:** `dag-layout.ts` lines 136, 163, 497, 530 — all `step.sequencing` → `step.after`
- **AC4.5:** `StepDefinitionPanel.tsx` lines 244, 592, 594, 596 — property + UI label "Sequencing" → "After"
- **AC4.6:** `StepDetailPanel.tsx` lines 157, 159, 161 — property + UI label "Sequencing" → "After"
- **AC4.7:** All 4 web test files updated: `dag-layout.test.ts` (lines 9, 18, 155, 163, 201, 205, 251, 255, 269), `StepNode.test.tsx:13`, `StepDefinitionPanel.test.tsx:29`, `FlowDagView.touch.test.tsx:14`

### R5: Tests updated
- **AC5.1:** All Python tests use `after=` kwarg and `after:` in inline YAML — 8 test files, ~20 occurrences total
- **AC5.2:** New backward-compat test: YAML with `sequencing: [a]` parses to `StepDefinition.after == ["a"]`
- **AC5.3:** New conflict test: YAML with both `after:` and `sequencing:` raises `ValueError`
- **AC5.4:** New `from_dict` fallback test: old serialized dict with `"sequencing"` key deserializes to `.after`

### R6: Docs, flows, examples, templates updated
- **AC6.1:** 4 flow YAML files use `after:`: `flows/research-proposal/FLOW.yaml:42`, `flows/welcome/FLOW.yaml:131,169`, `flows/eval-1-0/FLOW.yaml:39,146,164,180`, `examples/generate-homepage.flow.yaml:925,1039`
- **AC6.2:** 10 doc/reference files updated: `CLAUDE.md`, `README.md:100`, `docs/yaml-format.md:83,133,512,542,633`, `docs/concepts.md:131,265`, `docs/quickstart.md:70,78`, `docs/patterns.md:52,93,251,264,537`, `docs/cli.md:805`, `docs/use-cases.md:90,114`, `src/stepwise/flow-reference.md:46,311,312,432,567,668`, `src/stepwise/_templates/agent-skill/FLOW_REFERENCE.md:46,311,312,432,567,668`
- **AC6.3:** `src/stepwise/_templates/streaming-demo.json` — 6 occurrences at lines 18, 34, 54, 102, 121, 164
- **AC6.4:** `examples/self_analysis.py` — 4 occurrences at lines 66, 113, 152, 202

### R7: Grep verification
- **AC7.1:** Final grep: `grep -rn "sequencing" src/ tests/ flows/ docs/ examples/ web/ --include="*.py" --include="*.yaml" --include="*.md" --include="*.ts" --include="*.tsx" --include="*.json"` returns zero hits except these allowed exceptions:
  - `yaml_loader.py`: 3× `step_data.get("sequencing")` in deprecated-alias parsing
  - `models.py:from_dict()`: 1× `d.get("sequencing")` fallback
  - `server.py`: 2× `other_step.get("sequencing")` / `step_def.get("sequencing")` in raw YAML fallback
  - `test_yaml_loader.py`: 1× `test_sequencing_deprecated_alias` test with `sequencing:` in inline YAML

## Assumptions

### A1: Persisted SQLite data uses `"sequencing"` key in serialized JSON
**Verification:** Read `models.py:459` — `to_dict()` writes `"sequencing": self.sequencing`. Read `models.py:496` — `from_dict()` reads `d.get("sequencing", [])`. The `step_runs` table stores serialized `WorkflowDefinition` JSON which embeds `StepDefinition.to_dict()` output. Existing DB rows will have `"sequencing"` key.
**Decision:** `from_dict()` reads `"after"` first, falls back to `"sequencing"`. No DB migration needed — old data loads correctly, new data writes `"after"`.

### A2: Server raw YAML endpoints operate on unparsed YAML dicts, not StepDefinition objects
**Verification:** Read `server.py:1599` — `step_def.get("sequencing", [])` where `step_def` is a raw dict from `ryaml.load()`. Read `server.py:2281` — `other_step.get("sequencing")` also on raw YAML dict. These never go through `yaml_loader.py` parsing.
**Decision:** Both must do dual-key lookup (`"after"` primary, `"sequencing"` fallback) since user flow files may use either key.

### A3: Frontend types derive solely from `to_dict()` output via server JSON API
**Verification:** Read `types.ts:74` — `sequencing: string[]` matches `to_dict()` output key. The `/api/jobs/{id}` endpoint serializes `Job.workflow.steps[*].to_dict()`. When `to_dict()` changes from `"sequencing"` to `"after"`, all frontend code reading this field must update.
**Decision:** Rename TS field to `after: string[]`. Since frontend is bundled into the Python package at `src/stepwise/_web/`, both sides always deploy atomically.

### A4: No deprecation warning for `sequencing:` in YAML — silent alias only
**Verification:** Spec says "keep sequencing: as deprecated alias." No mention of log warnings. Existing codebase precedent: `prompt_file` vs `prompt` in `yaml_loader.py:181-217` uses silent conflict detection (`ValueError` if both present), not deprecation warnings.
**Decision:** Silent accept. Deprecation warnings can be added in a future release if needed.

### A5: `"after" in step_data` check preferred over truthy `or` chain for dual-key YAML lookup
**Verification:** If a user writes `after: []` (explicit empty), `step_data.get("after")` returns `[]` which is falsy. An `or` chain (`step_data.get("after") or step_data.get("sequencing") or []`) would incorrectly fall through to `"sequencing"`. While semantically equivalent (both mean "no deps"), the key-presence check is more correct.
**Decision:** Use `"after" in step_data` / `"sequencing" in step_data` pattern:
```python
if "after" in step_data:
    after = step_data["after"] or []
elif "sequencing" in step_data:
    after = step_data["sequencing"] or []
else:
    after = []
```
This correctly handles `after: []`, `after: null`, and absent key. Same pattern for `from_dict()`.

### A6: `chain.py` sequencing references are comments only, not runtime code
**Verification:** Read `chain.py:115-119` — two comment lines (`# Add sequencing for ordering` and `# sequencing is implicit via input binding, skip explicit`). The commented-out code never sets a `"sequencing"` key on the step dict. No functional change needed, only comment text update.
**Decision:** Update comments to say "after" instead of "sequencing". No logic change.

## Out of Scope

- **Runtime deprecation warnings** — silent alias only; no `logging.warning()` on `sequencing:` usage. Rationale: avoid log noise for all existing users until a future explicit deprecation cycle.
- **DB migration** — `from_dict()` fallback handles old data transparently; no schema changes needed. SQLite `step_runs.result` and `jobs.workflow` columns contain serialized JSON — migrating every row would be high-risk for zero functional benefit.
- **CHANGELOG entry** — this rename will be part of the 1.0 release notes, not a standalone version bump.
- **`--var` → `--input` CLI rename** — separate chunk of the language consolidation plan (`reports/impl-plan-stepwise-language-consolidation-for-stepwise-1-0-chunk-1-of-the-job.md`).
- **`stepwise chain` command removal** — separate chunk of the same consolidation plan.
- **Existing consolidation plan report** — the file `reports/impl-plan-stepwise-language-consolidation-for-stepwise-1-0-chunk-1-of-the-job.md` covers this rename as one of three tasks; references to "sequencing" in that report are not updated (it documents the before/after state).

## Architecture

The rename is purely cosmetic at the language level. The field semantics are identical — `after: [step-a]` means "wait for step-a to complete before starting." Engine behavior at `engine.py:919` (readiness check adds to `regular_deps`) and `engine.py:2063` (recording deps in `StepRun.dep_run_ids`) is unchanged.

### Backward-compat patterns (citing existing codebase precedents)

**`from_dict()` key fallback** — `models.py:490-511` already uses `d.get("key", default)` for all optional fields. Adding a two-key fallback follows the same shape:
```python
# Existing pattern (models.py:510):
on_error=d.get("on_error", "fail"),

# New pattern for "after":
after=d.get("after") if "after" in d else d.get("sequencing", []),
```

**YAML dual-key with conflict detection** — `yaml_loader.py:181-217` handles `prompt` vs `prompt_file` with an explicit mutual-exclusivity check (`raise ValueError` if both present). The `after`/`sequencing` conflict check follows this same pattern.

**Server raw YAML dual-key** — `server.py:1599-1602` and `server.py:2280-2285` both operate on raw YAML dicts (not StepDefinition objects). Both must handle either key since users may have old or new flow files.

### Complete file inventory (29 files across 7 layers)

| Layer | File | Occurrences | Change type |
|---|---|---|---|
| **Model** | `src/stepwise/models.py` | 10 | Field rename, serialization, validation messages |
| **YAML** | `src/stepwise/yaml_loader.py` | 12 | 3 parsing paths × (conflict check + dual-key + kwarg) |
| **Engine** | `src/stepwise/engine.py` | 6 | `.sequencing` → `.after` attribute accesses |
| **CLI** | `src/stepwise/cli.py` | 2 | Lines 1106, 1197: `.sequencing` → `.after` |
| **Chain** | `src/stepwise/chain.py` | 2 | Lines 115, 119: comment text only |
| **Context** | `src/stepwise/context.py` | 1 | Line 74: `.sequencing` → `.after` |
| **Report** | `src/stepwise/report.py` | 2 | Lines 120, 840: `.sequencing` → `.after` |
| **Server** | `src/stepwise/server.py` | 6 | Lines 1599-1602, 2267-2285: raw YAML dual-key |
| **TS types** | `web/src/lib/types.ts` | 1 | Line 74: interface field rename |
| **DAG layout** | `web/src/lib/dag-layout.ts` | 4 | Lines 136, 163, 497, 530 |
| **Editor UI** | `web/src/components/editor/StepDefinitionPanel.tsx` | 4 | Lines 244, 592, 594, 596 |
| **Job UI** | `web/src/components/jobs/StepDetailPanel.tsx` | 3 | Lines 157, 159, 161 |
| **Web tests** | `web/src/lib/dag-layout.test.ts` | 9 | Factory + test bodies |
| **Web tests** | `web/src/components/dag/StepNode.test.tsx` | 1 | Line 13 |
| **Web tests** | `web/src/components/editor/__tests__/StepDefinitionPanel.test.tsx` | 1 | Line 29 |
| **Web tests** | `web/src/components/dag/__tests__/FlowDagView.touch.test.tsx` | 1 | Line 14 |
| **Py tests** | `tests/test_yaml_loader.py` | 8 | Lines 241-268, 515-523, 562 |
| **Py tests** | `tests/test_validation.py` | 8 | Lines 230-331 (4 test methods) |
| **Py tests** | `tests/test_models.py` | 4 | Lines 160-219 |
| **Py tests** | `tests/test_engine.py` | 3 | Lines 433-462 |
| **Py tests** | `tests/test_m4_async.py` | 1 | Line 635 |
| **Py tests** | `tests/test_poll_executor.py` | 1 | Line 222 |
| **Py tests** | `tests/test_research_proposal_flow.py` | 1 | Line 159 |
| **Py tests** | `tests/test_runner.py` | 1 | Line 336 |
| **Flows** | `flows/research-proposal/FLOW.yaml` | 1 | Line 42 |
| **Flows** | `flows/welcome/FLOW.yaml` | 2 | Lines 131, 169 |
| **Flows** | `flows/eval-1-0/FLOW.yaml` | 4 | Lines 39, 146, 164, 180 |
| **Flows** | `examples/generate-homepage.flow.yaml` | 2 | Lines 925, 1039 |
| **Example** | `examples/self_analysis.py` | 4 | Lines 66, 113, 152, 202 |
| **Template** | `src/stepwise/_templates/streaming-demo.json` | 6 | Lines 18, 34, 54, 102, 121, 164 |
| **Docs** | `CLAUDE.md` | ~15 | Multiple sections |
| **Docs** | `README.md` | 1 | Line 100 |
| **Docs** | `docs/yaml-format.md` | 6 | Lines 83, 133, 512, 542, 633 |
| **Docs** | `docs/concepts.md` | 2 | Lines 131, 265 |
| **Docs** | `docs/quickstart.md` | 2 | Lines 70, 78 |
| **Docs** | `docs/patterns.md` | 6 | Lines 52, 93, 251, 264, 537 |
| **Docs** | `docs/cli.md` | 1 | Line 805 |
| **Docs** | `docs/use-cases.md` | 2 | Lines 90, 114 |
| **Docs** | `src/stepwise/flow-reference.md` | 6 | Lines 46, 311, 312, 432, 567, 668 |
| **Docs** | `src/stepwise/_templates/agent-skill/FLOW_REFERENCE.md` | 6 | Lines 46, 311, 312, 432, 567, 668 |

## Implementation Steps

Steps are ordered by dependency: model layer first (everything depends on the field existing), then consumers top-down. Steps within a layer are independent and can be parallelized.

### Step 1: Rename Python field in `StepDefinition` dataclass (~15 min)

**Depends on:** nothing (foundation for all other steps)

**File: `src/stepwise/models.py`** — 3 changes in the dataclass + serialization:

| Line | Old code | New code |
|---|---|---|
| 436 | `sequencing: list[str] = field(default_factory=list)  # wait-for-completion deps` | `after: list[str] = field(default_factory=list)  # wait-for-completion deps` |
| 459 | `"sequencing": self.sequencing,` | `"after": self.after,` |
| 496 | `sequencing=d.get("sequencing", []),` | `after=d.get("after") if "after" in d else d.get("sequencing", []),` |

**Verify:** `uv run python -c "from stepwise.models import StepDefinition, ExecutorRef; s = StepDefinition(name='x', outputs=['y'], executor=ExecutorRef('script', {}), after=['a']); print(s.to_dict()['after'])"` → prints `['a']`

### Step 2: Update validation + graph methods in `models.py` (~15 min)

**Depends on:** Step 1 (field must be renamed first)

**File: `src/stepwise/models.py`** — 7 attribute access sites + 2 message strings:

| Line | Old | New |
|---|---|---|
| 609 | `# Check sequencing references` | `# Check after references` |
| 610 | `for seq_step in step.sequencing:` | `for seq_step in step.after:` |
| 613 | `f"Step '{name}': sequencing references unknown step '{seq_step}'"` | `f"Step '{name}': after references unknown step '{seq_step}'"` |
| 827 | `# Warn if a step has sequencing on a looping step but no when condition` | `# Warn if a step has after on a looping step but no when condition` |
| 831 | `for seq in step.sequencing:` | `for seq in step.after:` |
| 834 | `f"⚠ Step '{name}': has sequencing on looping step "` | `f"⚠ Step '{name}': has 'after' on looping step "` |
| 892 | `"""Steps with no dependencies (no inputs, sequencing, or for_each source).` | `"""Steps with no dependencies (no inputs, after, or for_each source).` |
| 918 | `and not step.sequencing and not has_for_each_dep:` | `and not step.after and not has_for_each_dep:` |
| 943 | `for seq in step.sequencing:` | `for seq in step.after:` |
| 973 | `own_deps.update(step_def.sequencing)` | `own_deps.update(step_def.after)` |
| 1019 | `for seq in step.sequencing:` | `for seq in step.after:` |

**Verify:** `uv run pytest tests/test_models.py -x -q`

### Step 3: Update YAML loader with dual-key + conflict detection (~20 min)

**Depends on:** Step 1 (kwarg name changed to `after=`)

**File: `src/stepwise/yaml_loader.py`** — 3 identical changes at lines 727-729, 757-759, 781-783.

Each block changes from:
```python
sequencing = step_data.get("sequencing", [])
if isinstance(sequencing, str):
    sequencing = [sequencing]
```
To:
```python
if "after" in step_data and "sequencing" in step_data:
    raise ValueError(
        f"Step '{step_name}': cannot use both 'after' and 'sequencing' "
        f"(use 'after' — 'sequencing' is deprecated)"
    )
if "after" in step_data:
    after = step_data["after"] or []
elif "sequencing" in step_data:
    after = step_data["sequencing"] or []
else:
    after = []
if isinstance(after, str):
    after = [after]
```

Plus 3 kwarg updates: `sequencing=sequencing` → `after=after` at lines 738, 768, 857.

Note the `"key" in step_data` pattern instead of truthy `or` chain — handles `after: []` correctly (see Assumption A5).

**Verify:** `uv run pytest tests/test_yaml_loader.py -x -q`

### Step 4: Update engine attribute accesses (~10 min)

**Depends on:** Step 1 (field must be renamed first)

**File: `src/stepwise/engine.py`** — 6 sites:

| Line | Old | New |
|---|---|---|
| 919 | `regular_deps.extend(step_def.sequencing)` | `regular_deps.extend(step_def.after)` |
| 1033 | `regular_dep_steps.extend(step_def.sequencing)` | `regular_dep_steps.extend(step_def.after)` |
| 1082 | `"""All dependency steps: input binding sources + sequencing + for_each source."""` | `"""All dependency steps: input binding sources + after + for_each source."""` |
| 1088 | `deps.extend(step_def.sequencing)` | `deps.extend(step_def.after)` |
| 1654 | `for seq in step.sequencing:` | `for seq in step.after:` |
| 2062-2063 | `# Record sequencing deps` / `for seq_step in step_def.sequencing:` | `# Record after deps` / `for seq_step in step_def.after:` |

**Verify:** `uv run pytest tests/test_engine.py -x -q`

### Step 5: Update CLI + support modules (~10 min)

**Depends on:** Step 1 (field must be renamed first)

**File: `src/stepwise/cli.py`** — 2 sites:

| Line | Old | New |
|---|---|---|
| 1106 | `for seq in step.sequencing:` | `for seq in step.after:` |
| 1197 | `for dep in step.sequencing:` | `for dep in step.after:` |

**File: `src/stepwise/context.py`** — 1 site:

| Line | Old | New |
|---|---|---|
| 74 | `for seq in step.sequencing:` | `for seq in step.after:` |

**File: `src/stepwise/report.py`** — 2 sites:

| Line | Old | New |
|---|---|---|
| 120 | `d.update(step.sequencing)` | `d.update(step.after)` |
| 840 | `deps.update(step.sequencing)` | `deps.update(step.after)` |

**File: `src/stepwise/chain.py`** — 2 comment-only changes:

| Line | Old | New |
|---|---|---|
| 115 | `# Add sequencing for ordering (inputs handle data deps, but if stage N` | `# Add after for ordering (inputs handle data deps, but if stage N` |
| 119 | `# sequencing is implicit via input binding, skip explicit` | `# after is implicit via input binding, skip explicit` |

**Verify:** `uv run pytest tests/ -x -q` (CLI + context + report tested indirectly)

### Step 6: Update server raw YAML handling (~15 min)

**Depends on:** Step 1 (to_dict output changed)

**File: `src/stepwise/server.py`** — 2 sites operating on raw YAML dicts:

**Lines 1599-1607** (DAG edge builder) — change from:
```python
sequencing = step_def.get("sequencing", [])
if isinstance(sequencing, str):
    sequencing = [sequencing]
for seq_dep in sequencing:
```
To:
```python
after_deps = step_def.get("after") or step_def.get("sequencing") or []
if isinstance(after_deps, str):
    after_deps = [after_deps]
for seq_dep in after_deps:
```
(Truthy `or` chain is fine here — raw YAML `after: []` and absent key are semantically identical for edge building.)

**Lines 2267-2285** (step deletion cascade) — update comment + clean both keys:
```python
# Clean after (and legacy sequencing)
for key in ("after", "sequencing"):
    seq = other_step.get(key)
    if isinstance(seq, list):
        other_step[key] = [s for s in seq if s != req.step_name]
        if not other_step[key]:
            del other_step[key]
```

**Verify:** Server endpoints are tested via `uv run pytest tests/test_server.py -x -q` (if DAG endpoint has tests) or manual verification.

### Step 7: Update web frontend types + components (~20 min)

**Depends on:** Step 6 (server must output `"after"` key for frontend to consume)

**File: `web/src/lib/types.ts`** — 1 change:

| Line | Old | New |
|---|---|---|
| 74 | `sequencing: string[];` | `after: string[];` |

**File: `web/src/lib/dag-layout.ts`** — 4 changes:

| Line | Old | New |
|---|---|---|
| 136 | `// Add edges from input bindings and sequencing` | `// Add edges from input bindings and after` |
| 163 | `for (const seq of step.sequencing)` | `for (const seq of step.after)` |
| 497 | `for (const seq of step.sequencing)` | `for (const seq of step.after)` |
| 530 | `for (const seq of step.sequencing) referencedAsSource.add(seq);` | `for (const seq of step.after) referencedAsSource.add(seq);` |

**File: `web/src/components/editor/StepDefinitionPanel.tsx`** — 4 changes:

| Line | Old | New |
|---|---|---|
| 244 | `stepDef.sequencing.length > 0 \|\|` | `stepDef.after.length > 0 \|\|` |
| 592 | `{stepDef.sequencing.length > 0 && (` | `{stepDef.after.length > 0 && (` |
| 594 | `<span className="text-xs text-zinc-500">Sequencing</span>` | `<span className="text-xs text-zinc-500">After</span>` |
| 596 | `{stepDef.sequencing.map((s) => (` | `{stepDef.after.map((s) => (` |

**File: `web/src/components/jobs/StepDetailPanel.tsx`** — 3 changes:

| Line | Old | New |
|---|---|---|
| 157 | `{stepDef.sequencing.length > 0 && (` | `{stepDef.after.length > 0 && (` |
| 159 | `<div className="text-zinc-500">Sequencing</div>` | `<div className="text-zinc-500">After</div>` |
| 161 | `{stepDef.sequencing.join(", ")}` | `{stepDef.after.join(", ")}` |

**Verify:** `cd web && npm run lint && npm run test`

### Step 8: Update web tests (~10 min)

**Depends on:** Step 7 (types must be updated for tests to compile)

**File: `web/src/lib/dag-layout.test.ts`** — update `makeStep` helper + all test bodies:

| Line | Old | New |
|---|---|---|
| 9 | `sequencing?: string[];` | `after?: string[];` |
| 18 | `sequencing: opts.sequencing ?? [],` | `after: opts.after ?? [],` |
| 155 | `"deduplicates edges when same dependency comes from inputs and sequencing"` | `"deduplicates edges when same dependency comes from inputs and after"` |
| 163 | `sequencing: ["A"],` | `after: ["A"],` |
| 201 | `"uses sequencing-only edges for ordering"` | `"uses after-only edges for ordering"` |
| 205 | `B: makeStep("B", { sequencing: ["A"] }),` | `B: makeStep("B", { after: ["A"] }),` |
| 251 | `"sequencing-only edges have empty labels"` | `"after-only edges have empty labels"` |
| 255 | `B: makeStep("B", { sequencing: ["A"] }),` | `B: makeStep("B", { after: ["A"] }),` |
| 269 | `B: makeStep("B", { sequencing: ["A"] }),` | `B: makeStep("B", { after: ["A"] }),` |

**Files with factory fixture updates:**

| File | Line | Old | New |
|---|---|---|---|
| `StepDefinitionPanel.test.tsx` | 29 | `sequencing: [],` | `after: [],` |
| `StepNode.test.tsx` | 13 | `sequencing: [],` | `after: [],` |
| `FlowDagView.touch.test.tsx` | 14 | `sequencing: [],` | `after: [],` |

**Verify:** `cd web && npm run test`

### Step 9: Update Python tests (~30 min)

**Depends on:** Steps 1-5 (all Python source must be updated first)

8 test files with exact change counts per file:

**`tests/test_yaml_loader.py`** — 8 occurrences across 4 tests + 2 new tests:

| Line | Change |
|---|---|
| 241 | `def test_sequencing` → `def test_after` |
| 251 | `sequencing: [a]` → `after: [a]` in YAML string |
| 253 | `.sequencing == ["a"]` → `.after == ["a"]` |
| 255 | `def test_sequencing_string` → `def test_after_string` |
| 266 | `sequencing: a` → `after: a` in YAML string |
| 268 | `.sequencing == ["a"]` → `.after == ["a"]` |
| 515 | `def test_sequencing_unknown_step` → `def test_after_unknown_step` |
| 522 | `sequencing: [nonexistent]` → `after: [nonexistent]` |
| 562 | `sequencing: [review]` → `after: [review]` |
| NEW | `test_sequencing_deprecated_alias()` — YAML with `sequencing: [a]`, assert `.after == ["a"]` |
| NEW | `test_after_and_sequencing_conflict()` — YAML with both keys, assert `ValueError` |

**`tests/test_validation.py`** — 8 occurrences across 4 tests:

| Line | Change |
|---|---|
| 230 | docstring: "sequencing" → "after" |
| 232 | `def test_ungated_sequencing_on_loop_target` → `def test_ungated_after_on_loop_target` |
| 233 | docstring: "sequencing" → "after" |
| 251 | `sequencing=["review"]` → `after=["review"]` |
| 261 | `def test_gated_sequencing_on_loop_target_no_warning` → `def test_gated_after_on_loop_target_no_warning` |
| 262 | docstring: "sequencing" → "after" |
| 283 | `sequencing=["review"]` → `after=["review"]` |
| 291 | `def test_self_loop_sequencing_warning` → `def test_self_loop_after_warning` |
| 292 | docstring: "sequencing" → "after" |
| 308 | `sequencing=["retry_step"]` → `after=["retry_step"]` |
| 319 | docstring: "sequencing" → "after" |
| 326 | `sequencing=["step_a"]` → `after=["step_a"]` |

**`tests/test_models.py`** — 4 occurrences:

| Line | Change |
|---|---|
| 160 | `def test_sequencing_missing_step` → `def test_after_missing_step` |
| 165 | `sequencing=["nonexistent"]` → `after=["nonexistent"]` |
| 206 | comment: "sequencing" → "after" |
| 215 | `sequencing=["a"]` → `after=["a"]` |
| 218 | comment: "via sequencing" → "via after" |
| NEW | `test_from_dict_sequencing_fallback()` — verify old `"sequencing"` key in dict |

**`tests/test_engine.py`** — 3 occurrences:

| Line | Change |
|---|---|
| 433 | `class TestSequencingFreshness` → `class TestAfterFreshness` |
| 434 | `def test_sequencing_dep_must_rerun` → `def test_after_dep_must_rerun` |
| 459 | `sequencing=["implement"]` → `after=["implement"]` |

**`tests/test_m4_async.py`** — 1 occurrence:

| Line | Change |
|---|---|
| 635 | `sequencing=["score"]` → `after=["score"]` |

**`tests/test_poll_executor.py`** — 1 occurrence:

| Line | Change |
|---|---|
| 222 | `sequencing=["create-pr"]` → `after=["create-pr"]` |

**`tests/test_research_proposal_flow.py`** — 1 occurrence:

| Line | Change |
|---|---|
| 159 | `sequencing=["init"]` → `after=["init"]` |

**`tests/test_runner.py`** — 1 occurrence:

| Line | Change |
|---|---|
| 336 | `sequencing: [count]` → `after: [count]` in inline YAML |

**Verify:** `uv run pytest tests/ -x -q`

### Step 10: Update flows, examples, templates (~10 min)

**Depends on:** nothing (YAML files are leaf artifacts)

**Flow files** — direct `sequencing:` → `after:` replacement:
- `flows/research-proposal/FLOW.yaml:42` — `sequencing: [init]` → `after: [init]`
- `flows/welcome/FLOW.yaml:131` — `sequencing: [task-implement]` → `after: [task-implement]`
- `flows/welcome/FLOW.yaml:169` — `sequencing: [write-code]` → `after: [write-code]`
- `flows/eval-1-0/FLOW.yaml:39,146,164,180` — 4 occurrences
- `examples/generate-homepage.flow.yaml:925,1039` — 2 occurrences

**Templates** — JSON key rename:
- `src/stepwise/_templates/streaming-demo.json` — `"sequencing"` → `"after"` at lines 18, 34, 54, 102, 121, 164

**Example code:**
- `examples/self_analysis.py` — `"sequencing"` → `"after"` at lines 66, 113, 152, 202

**Verify:** `uv run stepwise validate flows/research-proposal/FLOW.yaml && uv run stepwise validate flows/eval-1-0/FLOW.yaml`

### Step 11: Update documentation (~30 min)

**Depends on:** nothing (docs are leaf artifacts)

10 documentation files, with exact line counts per file:

| File | Lines to update | Notes |
|---|---|---|
| `CLAUDE.md` | ~15 occurrences | YAML examples, key distinction, field table, recipes |
| `README.md` | Line 100 | Single YAML example |
| `docs/yaml-format.md` | Lines 83, 133, 512, 542, 633 | Examples + reference table |
| `docs/concepts.md` | Lines 131, 265 | Example + distinction table |
| `docs/quickstart.md` | Lines 70, 78 | Example + explanation |
| `docs/patterns.md` | Lines 52, 93, 251, 264, 537 | Examples + explanation + reference table |
| `docs/cli.md` | Line 805 | Edge color legend: "gray dashed for sequencing" → "gray dashed for after" |
| `docs/use-cases.md` | Lines 90, 114 | YAML examples |
| `src/stepwise/flow-reference.md` | Lines 46, 311, 312, 432, 567, 668 | Full field reference |
| `src/stepwise/_templates/agent-skill/FLOW_REFERENCE.md` | Lines 46, 311, 312, 432, 567, 668 | Agent template copy |

**Verify:** Visual review; no automated test for doc content.

### Step 12: Grep verification + final test run (~10 min)

**Depends on:** Steps 1-11 (all changes complete)

```bash
# Verification grep — should show ONLY allowed exceptions
grep -rn "sequencing" src/ tests/ flows/ docs/ examples/ web/ \
  --include="*.py" --include="*.yaml" --include="*.md" \
  --include="*.ts" --include="*.tsx" --include="*.json" \
  | grep -v "reports/"  # exclude plan reports

# Expected allowed hits:
#   yaml_loader.py:  3× step_data.get("sequencing")  (deprecated alias)
#   models.py:       1× d.get("sequencing")           (from_dict fallback)
#   server.py:       2× .get("sequencing")            (raw YAML fallback)
#   test_yaml_loader: 1× test_sequencing_deprecated_alias (compat test YAML)

# Full test suite
uv run pytest tests/ -x -q
cd web && npm run test && npm run lint
```

## Testing Strategy

### Existing tests (renamed, updated)

| Test (new name) | File:Line | What it validates | Expected result |
|---|---|---|---|
| `test_after` | `test_yaml_loader.py:241` | `after: [a]` in YAML → `.after == ["a"]` | Pass |
| `test_after_string` | `test_yaml_loader.py:255` | `after: a` → `.after == ["a"]` | Pass |
| `test_after_unknown_step` | `test_yaml_loader.py:515` | `after: [nonexistent]` → `YAMLLoadError` matching `"unknown step"` | Pass |
| `test_iterative_review_structure` | `test_yaml_loader.py:532` | Complex flow with `after: [review]` → correct entry/terminal steps | Pass |
| `test_after_missing_step` | `test_models.py:160` | `StepDefinition(after=["nonexistent"])` → validation error `"after references unknown step"` | Pass |
| `test_terminal_with_after` | `test_models.py:206` | `StepDefinition(after=["a"])` → `terminal_steps() == ["b"]` | Pass |
| `TestAfterFreshness.test_after_dep_must_rerun` | `test_engine.py:433` | Engine re-runs step when after dep is re-run | Pass |
| `TestUngatedPostLoopWarning` (4 tests) | `test_validation.py:229` | Warning text contains `"has 'after' on looping step"` | Pass |

### New tests (3 total)

**1. `test_sequencing_deprecated_alias`** in `test_yaml_loader.py`:
```python
def test_sequencing_deprecated_alias(self):
    """The old 'sequencing' key still works as a deprecated alias."""
    wf = load_workflow_string("""
steps:
  a:
    run: echo ok
    outputs: [x]
  b:
    run: echo ok
    outputs: [y]
    sequencing: [a]
""")
    assert wf.steps["b"].after == ["a"]
```

**2. `test_after_and_sequencing_conflict`** in `test_yaml_loader.py`:
```python
def test_after_and_sequencing_conflict(self):
    with pytest.raises(YAMLLoadError, match="cannot use both"):
        load_workflow_string("""
steps:
  a:
    run: echo ok
    outputs: [x]
  b:
    run: echo ok
    outputs: [y]
    after: [a]
    sequencing: [a]
""")
```

**3. `test_from_dict_sequencing_fallback`** in `test_models.py`:
```python
def test_from_dict_sequencing_fallback(self):
    """Old serialized data with 'sequencing' key deserializes to .after."""
    d = {
        "name": "x", "outputs": ["y"],
        "executor": {"type": "script", "config": {}},
        "sequencing": ["a"],
    }
    step = StepDefinition.from_dict(d)
    assert step.after == ["a"]
    # New serialization uses "after"
    assert "after" in step.to_dict()
    assert "sequencing" not in step.to_dict()
```

### Test commands (in execution order)

```bash
# 1. Core model tests (Steps 1-2)
uv run pytest tests/test_models.py -x -v

# 2. YAML loader tests (Step 3)
uv run pytest tests/test_yaml_loader.py -x -v

# 3. Engine tests (Step 4)
uv run pytest tests/test_engine.py -x -v

# 4. Validation tests (Step 9)
uv run pytest tests/test_validation.py -x -v

# 5. Full Python suite (after all Python changes)
uv run pytest tests/ -x -q

# 6. Web tests (after frontend changes)
cd web && npm run test

# 7. TypeScript type check
cd web && npm run lint

# 8. Flow validation (after flow file updates)
uv run stepwise validate flows/research-proposal/FLOW.yaml
uv run stepwise validate flows/eval-1-0/FLOW.yaml
uv run stepwise validate flows/welcome/FLOW.yaml
```

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Existing user `.flow.yaml` files with `sequencing:` break | High — users can't run their flows | Low (mitigated) | Silent deprecated alias in `yaml_loader.py` ensures old flows parse without changes. Tested by `test_sequencing_deprecated_alias`. |
| Persisted SQLite data with `"sequencing"` key fails to deserialize | High — existing jobs unreadable | Low (mitigated) | `from_dict()` fallback reads both keys. Tested by `test_from_dict_sequencing_fallback`. |
| Missed `.sequencing` attribute access causes `AttributeError` at runtime | Medium — specific code path crashes | Low (mitigated) | Comprehensive grep in Step 12 (AC7.1) catches all occurrences. Full test suite exercises all code paths. |
| Server raw YAML endpoints miss old-format flows | Medium — DAG view or step deletion breaks | Low (mitigated) | Both `server.py` endpoints updated with dual-key lookup. |
| Frontend/backend field name mismatch after deploy | Medium — JS errors, empty DAG edges | None | Frontend bundled into Python package at `src/stepwise/_web/`; both sides always deploy atomically via `make build-web`. |
| `after` is a Python reserved word | Blocker — syntax error | None | Verified: `python -c "import keyword; print('after' in keyword.kwlist)"` → `False`. `after` is not a keyword, soft keyword, or builtin. |
| `after: []` in YAML incorrectly falls through to `sequencing:` key | Low — semantic no-op but logically incorrect | Low (mitigated) | Using `"after" in step_data` key-presence check instead of truthy `or` chain (Assumption A5). |
