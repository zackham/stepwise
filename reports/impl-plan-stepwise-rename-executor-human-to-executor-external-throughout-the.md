---
title: "Implementation Plan: Rename executor: human → executor: external"
date: "2026-03-20T00:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Rename `executor: human` → `executor: external`

## Overview

Complete the rename of the "human" executor type to "external" across the stepwise codebase. The Python source, web frontend, flow YAML files, and tests have already been migrated. What remains is: removing 3 residual `"human"` dual-references in Python source/tests, updating all documentation files (12 docs, README, CLAUDE.md, 3 flow-reference copies, 2 SKILL.md copies, `streaming-demo.json`), and verifying the CHANGELOG entry is accurate.

## Requirements

### R1: Remove residual `"human"` from Python source
**Acceptance criteria:**
- `rg '"human"' src/stepwise/ --type py` returns zero matches
- `cache.py:42` reads `frozenset({"external", "poll", "for_each", "sub_flow"})` (no `"human"`)
- `models.py:869` reads `if step.executor.type == "external":` (no `"human"` tuple)
- `models.py:872` reads `"external steps"` (not `"human/external steps"`)
- `uv run pytest tests/test_cache.py tests/test_validation.py -v` passes

### R2: Remove stale test assertion
**Acceptance criteria:**
- `rg '"human"' tests/ --type py` returns zero matches
- `test_cache.py:461` (`assert "human" in UNCACHEABLE_TYPES`) is deleted
- `test_cache.py:460` (`assert "external" in UNCACHEABLE_TYPES`) remains
- `uv run pytest tests/test_cache.py::TestCacheKey::test_external_steps_never_cached -v` passes

### R3: Documentation uses `executor: external` everywhere
**Acceptance criteria:**
- `rg 'executor: human' docs/` returns zero matches
- `rg 'humanSteps' docs/` returns zero matches
- All 20 `executor: human` instances across 8 doc files are replaced with `executor: external`
- All 5 `humanSteps` instances across `docs/cli.md` and `docs/agent-integration.md` are replaced with `externalSteps`
- `docs/api.md` executor lists show `"external"` not `"human"` (lines 305, 319)
- Conceptual "human" phrasing (judgment, approval, oversight) is preserved

### R4: README.md executor table updated
**Acceptance criteria:**
- `README.md:112` reads `| **external** |` not `| **human** |`
- `README.md:18` reads "**external input**" or similar (not "**human decision**" as executor-type reference)
- `rg 'executor: human' README.md` returns zero matches

### R5: Flow-reference and template files updated
**Acceptance criteria:**
- `rg 'executor: human' src/stepwise/flow-reference.md .claude/skills/ src/stepwise/_templates/` returns zero matches
- `rg '"type": "human"' src/stepwise/_templates/` returns zero matches
- Section headers `### human` changed to `### external` in all 3 FLOW_REFERENCE.md copies
- `streaming-demo.json:26` reads `"type": "external"` not `"type": "human"`

### R6: CHANGELOG entry is accurate
**Acceptance criteria:**
- `CHANGELOG.md` `[Unreleased]` section documents the breaking rename (already present at line 9)
- Entry mentions `executor: human` → `executor: external` (already correct)
- No additional CHANGELOG changes needed — entry was written before the migration began

### R7: All tests pass
**Acceptance criteria:**
- `uv run pytest tests/ -x` exits 0
- `cd web && npm run test -- --run` exits 0
- `cd web && npm run lint` exits 0

## Assumptions

| # | Assumption | Verification |
|---|---|---|
| A1 | All Python source class/function/variable renames are already done | Verified: `rg 'HumanExecutor' src/ tests/` = 0 matches. `rg 'HUMAN_RERUN' src/` = 0 matches. `rg 'HumanInputAborted' src/ tests/` = 0 matches. `rg 'collect_human_input' src/ tests/` = 0 matches. `rg '_handle_human_input' src/` = 0 matches. Only hits are in `reports/` (the old plan doc). |
| A2 | All web frontend renames are done | Verified: `rg '"human"' web/src/ --type ts` = 0 matches. `rg 'HumanInputPanel' web/src/` = 0 matches. `ExternalInputPanel.tsx` exists in `web/src/components/dag/`. |
| A3 | All flow YAML files already use `executor: external` | Verified: `rg 'executor: human' flows/` = 0 matches. `rg 'executor: external' flows/` returns hits in `flows/welcome/FLOW.yaml`, `flows/eval-1-0/FLOW.yaml`, `flows/research-proposal/FLOW.yaml`. |
| A4 | Registry registers `"external"` not `"human"` | Verified: `registry_factory.py:46` reads `registry.register("external", lambda cfg: ExternalExecutor(...))`; `rg 'register\("human"' src/` = 0 matches. |
| A5 | The 3 residual `"human"` references in Python are defensive dual-checks, not sole references | Verified by reading `cache.py:42`: `frozenset({"human", "external", ...})` — both names present. `models.py:869`: `in ("human", "external")` — both names present. Removing `"human"` is safe because `registry_factory.py` no longer registers it, so no executor will ever have `type="human"`. |
| A6 | `streaming-demo.json` template is a serialized `WorkflowDefinition` with `"type": "human"` | Verified by reading `streaming-demo.json:26`: `"type": "human"` in the `"answer"` step's executor config. This is loaded at runtime and must match the registry. |
| A7 | Conceptual "human" phrasing should be preserved | "Human judgment", "human-in-the-loop" tags, "human approval", "human oversight" describe UX concepts. Registry tags like `human-in-the-loop` in `docs/flow-sharing.md` are user-facing descriptors. These stay. Only the executor type identifier `"human"` and its direct references (executor lists, YAML `executor:` field, schema keys) change. |
| A8 | CHANGELOG entry is already complete | Verified by reading `CHANGELOG.md:9`: the `[Unreleased]` section already documents the full rename including class, event, schema key, and component renames. No modification needed. |

## Out of Scope

- **Python source class/function renames** — already completed (confirmed by A1).
- **Web frontend renames** — already completed (confirmed by A2).
- **Flow YAML file renames** — already completed (confirmed by A3).
- **Deprecation alias / backward compatibility** — spec says pre-1.0, no alias needed.
- **Database migration** — `WatchSpec.mode` in serialized `step_runs.watch` JSON column may contain `"human"` for old suspended runs. Runs are ephemeral and pre-1.0; acceptable.
- **CHANGELOG historical entries** — e.g., "Ctrl+C during human input" at `CHANGELOG.md:25` describes what happened at that version and stays as-is.
- **Conceptual "human" in docs** — ~94 occurrences of "human" in docs refer to human judgment/involvement/oversight, not the executor type. These are preserved. See Assumption A7.

## Architecture

Pure string replacement — no architectural changes. The module DAG (`models → executors → engine → server`), control flow, data structures, and API contracts are untouched.

### What was already renamed (for context)

The codebase follows a pattern where executor types are identified by string keys registered in `registry_factory.py` and matched throughout the system:

```
registry_factory.py:46  →  registry.register("external", ...)     # was "human"
executors.py:362        →  class ExternalExecutor(Executor)        # was HumanExecutor
events.py:20            →  EXTERNAL_RERUN = "external.rerun"       # was HUMAN_RERUN
schema.py:59            →  schema["externalSteps"] = ...           # was "humanSteps"
io.py:17                →  class ExternalInputAborted(Exception)   # was HumanInputAborted
```

These are the source-of-truth definitions. All consumers (`engine.py`, `runner.py`, `server.py`, `cli.py`, `agent_help.py`, web components) have already been updated to use the new names. The remaining work is purely documentary.

### Remaining change inventory (exact lines)

**Python source (3 lines across 2 files):**

| File | Line | Current | Target |
|---|---|---|---|
| `src/stepwise/cache.py` | 42 | `frozenset({"human", "external", "poll", "for_each", "sub_flow"})` | `frozenset({"external", "poll", "for_each", "sub_flow"})` |
| `src/stepwise/models.py` | 869 | `if step.executor.type in ("human", "external"):` | `if step.executor.type == "external":` |
| `src/stepwise/models.py` | 872 | `f"human/external steps"` | `f"external steps"` |

**Test (1 line):**

| File | Line | Current | Target |
|---|---|---|---|
| `tests/test_cache.py` | 461 | `assert "human" in UNCACHEABLE_TYPES` | (delete line) |

**Documentation — executor-type references (40 instances across 21 files):**

| File | Lines needing change | Change type |
|---|---|---|
| `docs/executors.md` | 170, 196, 251 | `executor: human` → `executor: external`; "Human executor" → "External executor" |
| `docs/quickstart.md` | 55, 77, 114, 147 | `executor: human` → `executor: external`; `` `human` step `` → `` `external` step `` |
| `docs/yaml-format.md` | 57, 118, 230, 310, 525, 628 | `executor: human` → `executor: external`; table entry `ExecutorRef("human"...)` → `ExecutorRef("external"...)` |
| `docs/concepts.md` | 11, 35, 41, 69, 95 | `executor: human` → `executor: external`; executor type lists; table row |
| `docs/patterns.md` | 309 | `executor: human` → `executor: external` |
| `docs/use-cases.md` | 27, 71, 117 | `executor: human` → `executor: external` |
| `docs/why-stepwise.md` | 21, 41 | `executor: human` → `executor: external`; executor list in prose |
| `docs/api.md` | 305, 319 | `"human"` → `"external"` in JSON executor arrays |
| `docs/cli.md` | 573, 581 | `"humanSteps"` → `"externalSteps"` in JSON examples |
| `docs/agent-integration.md` | 75, 82, 341 | `"humanSteps"` → `"externalSteps"` in JSON/shell examples |
| `docs/flow-sharing.md` | 127 | `human` → `external` in executor column |
| `README.md` | 18, 112 | Executor table row; product description executor list |
| `src/stepwise/flow-reference.md` | 64, 137, 143, 476, 730 | `executor: human` → `executor: external`; section header |
| `.claude/skills/stepwise/FLOW_REFERENCE.md` | 64, 137, 143, 476, 730 | Same as above |
| `.claude/skills/stepwise/SKILL.md` | 3, 8, 132 | Executor type lists in description |
| `src/stepwise/_templates/agent-skill/FLOW_REFERENCE.md` | 35, 108, 114, 429 | `executor: human` → `executor: external`; section header |
| `src/stepwise/_templates/agent-skill/SKILL.md` | 15, 117 | Executor type lists |
| `src/stepwise/_templates/streaming-demo.json` | 3, 26 | `"type": "human"` → `"type": "external"`; description text |
| `CLAUDE.md` | 148 | "human input form" → "external input form" (describes component) |

**Documentation — conceptual references (preserved, ~94 instances):**

These are NOT changed. Examples: "human judgment" (`docs/why-stepwise.md:93`), "human-in-the-loop" tags (`docs/flow-sharing.md:96,120,163`), "human inspection" (`docs/concepts.md:177`), "pause for human input" (`docs/cli.md:25,36`), "human decision" (`README.md:18` — keep as product-level concept, only change the executor table), "human approval gate" (`docs/flow-sharing.md:119,162,200,234,338`).

## Implementation Steps

### Ordering rationale

Python source → tests → verify → documentation → templates → CLAUDE.md → final verify. Python changes first because they affect runtime behavior (test assertions). Tests verify the Python changes. Documentation is independent of code but sequenced after code so that any `stepwise validate` or `stepwise schema` smoke tests work. Each step has a single commit for clean `git bisect`.

---

### Step 1: Remove `"human"` from `UNCACHEABLE_TYPES` in `cache.py` (~2 min)

**File:** `src/stepwise/cache.py`

**Line 42 — before:**
```python
UNCACHEABLE_TYPES = frozenset({"human", "external", "poll", "for_each", "sub_flow"})
```

**Line 42 — after:**
```python
UNCACHEABLE_TYPES = frozenset({"external", "poll", "for_each", "sub_flow"})
```

**Why this is safe:** The registry (`registry_factory.py:46`) no longer registers `"human"`, so no step will ever have `executor.type == "human"`. The `"human"` entry in the frozenset was a transitional dual-reference. Removing it has no behavioral impact — it just removes a dead code path from the membership check.

**No commit yet** — bundle with Step 2.

---

### Step 2: Remove `"human"` from validation warning in `models.py` (~2 min)

**File:** `src/stepwise/models.py`

**Line 869 — before:**
```python
if step.executor.type in ("human", "external"):
```

**Line 869 — after:**
```python
if step.executor.type == "external":
```

**Line 872 — before:**
```python
f"human/external steps"
```

**Line 872 — after:**
```python
f"external steps"
```

**Why this is safe:** Same reasoning as Step 1. This validation warning is in `WorkflowDefinition.validate_warnings()` (line 866-873), which fires during `stepwise validate`. The `"human"` branch was unreachable since the registry no longer registers that type — YAML with `executor: human` would fail at parse time before reaching validation. The warning message pattern follows the same style as the neighboring `poll` warning at line 874-877.

**Commit:** `refactor: remove residual "human" dual-references from cache.py and models.py`

---

### Step 3: Remove stale test assertion in `test_cache.py` (~2 min)

**File:** `tests/test_cache.py`

**Line 461 — delete:**
```python
assert "human" in UNCACHEABLE_TYPES
```

**Line 460 stays:**
```python
assert "external" in UNCACHEABLE_TYPES
```

**Context:** The test `test_external_steps_never_cached` at line 459 verifies that the set contains the correct types. After Step 1 removed `"human"` from the set, this assertion would fail. The `"external"` assertion on line 460 is the canonical check.

**Commit:** `test: remove stale "human" cache assertion from test_cache.py`

---

### Step 4: Run Python tests and verify zero stale references (~5 min)

```bash
uv run pytest tests/ -x -v
```

Then verify no stale references remain:

```bash
rg '"human"' src/stepwise/ tests/ --type py
```

Expected: zero matches.

**No commit.**

---

### Step 5: Update `docs/executors.md` (~5 min)

**Executor-type changes (3 lines):**
- Line 170: `executor: human` → `executor: external`
- Line 196: `executor: human` → `executor: external`
- Line 251: "Human executor" → "External executor" (decision tree answer)

**Section header:** The section around line 166 is titled for this executor type. Update header text (e.g., `## Human` → `## External`) to match the new name. Keep descriptive text like "waits for human input" and "human-in-the-loop" (lines 166, 192, 214) — these describe the concept.

**No commit yet** — bundle with Steps 6-11.

---

### Step 6: Update `docs/quickstart.md` (~5 min)

**Executor-type changes (4 lines):**
- Line 55: `executor: human` → `executor: external`
- Line 77: `` `human` step `` → `` `external` step `` (the backtick-quoted executor name)
- Line 114: `executor: human` → `executor: external`
- Line 147: `executor: human` → `executor: external`

**Preserved:** Lines 21, 35, 98, 110, 141 — conceptual "human" (approval, step pauses for decision, etc.)

---

### Step 7: Update `docs/yaml-format.md` (~5 min)

**Executor-type changes (6 lines):**
- Line 57: `executor: human` → `executor: external`
- Line 118: `executor: human  # human executor` → `executor: external  # external executor`
- Line 230: `executor: human` → `executor: external`
- Line 310: `executor: human` → `executor: external`
- Line 525: `executor: human` → `executor: external`
- Line 628: `` `executor: human` + `prompt:` | `ExecutorRef("human", {"prompt": "..."})` `` → `` `executor: external` + `prompt:` | `ExecutorRef("external", {"prompt": "..."})` ``

**Preserved:** Lines 92 ("human-readable"), 221 ("human inspection"), 432 ("human-readable label") — conceptual.

---

### Step 8: Update `docs/concepts.md` (~5 min)

**Executor-type changes (5 lines):**
- Line 11: table entry `human` → `external` in executor type column
- Line 35: `(script, LLM, agent, or human)` → `(script, LLM, agent, or external)`
- Line 41: `executor: human` → `executor: external`
- Line 69: table row `Human` → `External`
- Line 95: `executor: human` → `executor: external`

**Preserved:** Lines 93 ("Human — waits for human input" comment — update only the executor name part: `# External — waits for external input`), 177, 186 (escalation), 329, 346, 353, 355-357 (conceptual "human steps" in agent integration context).

**Judgment call on lines 346, 353, 355-356:** These lines describe the UX pattern of "flows with human steps" in the agent integration section. Since "human steps" here refers to the executor type (not the concept), update to "external steps" for consistency with the new type name. Line 357 ("human steps" in schema context) also updates.

---

### Step 9: Update `docs/patterns.md`, `docs/use-cases.md`, `docs/why-stepwise.md` (~5 min)

**`docs/patterns.md` (1 line):**
- Line 309: `executor: human` → `executor: external`
- Preserved: Lines 278, 293, 325, 331, 338 — conceptual.

**`docs/use-cases.md` (3 lines):**
- Lines 27, 71, 117: `executor: human` → `executor: external`
- Preserved: Lines 7, 41, 45, 93, 97, 142 — conceptual.

**`docs/why-stepwise.md` (2 lines):**
- Line 21: `(script, LLM, agent, or human)` → `(script, LLM, agent, or external)`
- Line 41: `executor: human` → `executor: external`
- Preserved: Lines 54, 58, 81, 93 — conceptual.

---

### Step 10: Update `docs/api.md`, `docs/cli.md`, `docs/agent-integration.md`, `docs/flow-sharing.md` (~10 min)

**`docs/api.md` (2 lines):**
- Line 305: `"human"` → `"external"` in `registered_executors` JSON array
- Line 319: `"human"` → `"external"` in `executors` JSON array
- Preserved: Lines 189, 195 — conceptual ("Fulfill a watch", "human response").

**`docs/cli.md` (2 lines — schema key only):**
- Line 573: `"humanSteps": []` → `"externalSteps": []`
- Line 581: `"humanSteps": [` → `"externalSteps": [`
- Preserved: Lines 25, 36, 106, 154, 289, 354, 397, 538, 561, 577, 622, 701 — conceptual "human step" in prose descriptions. These describe the UX pattern and are acceptable to keep as-is.

**`docs/agent-integration.md` (3 lines — schema key only):**
- Line 75: `"humanSteps": []` → `"externalSteps": []`
- Line 82: `**humanSteps**` → `**externalSteps**` (bold key reference + description)
- Line 341: `'.humanSteps | length'` → `'.externalSteps | length'` in shell example
- Preserved: Lines 16, 31, 58, 226, 234, 266, 282, 336, 354 — conceptual.

**`docs/flow-sharing.md` (1 line):**
- Line 127: `approve     human    → decide: ...` → `approve     external    → decide: ...` (executor type column in step table)
- Preserved: Lines 96, 119, 120, 162, 163, 200, 201, 234, 235, 338, 340 — "human-in-the-loop" tags and "human approval gate" descriptions are registry metadata/conceptual.

**Commit:** `docs: update executor examples and schema keys in all documentation for human → external rename`

---

### Step 11: Update `README.md` (~5 min)

**Executor-type changes (2 lines):**
- Line 18: `**human decision**` → `**external input**` (executor type in product intro list)
- Line 112: `| **human** | Pauses the job and waits for input (web UI or stdin) | Approvals, creative judgment, decisions |` → `| **external** | Pauses the job and waits for input (web UI or stdin) | Approvals, creative judgment, decisions |`

**Preserved:** Lines 33, 59, 135, 147 — conceptual ("human steps pause", "human input", "human gates").

**Commit:** `docs: update README.md executor table for human → external rename`

---

### Step 12: Update 3 FLOW_REFERENCE.md copies (~10 min)

These three files have identical structure. Apply the same changes to each:

**`src/stepwise/flow-reference.md` (5 lines):**
- Line 64: `executor: human  # human | llm | agent` → `executor: external  # external | llm | agent`
- Line 137: `### human` → `### external`
- Line 143: `executor: human` → `executor: external`
- Line 476: `executor: human` → `executor: external`
- Line 730: `executor: human` → `executor: external`
- Preserved: Line 411 ("pauses job for human inspection") — conceptual.

**`.claude/skills/stepwise/FLOW_REFERENCE.md` (5 lines):**
- Lines 64, 137, 143, 476, 730 — same changes as above
- Preserved: Line 411 — conceptual.

**`src/stepwise/_templates/agent-skill/FLOW_REFERENCE.md` (4 lines):**
- Line 35: `executor: human  # human | llm | agent` → `executor: external  # external | llm | agent`
- Line 108: `### human` → `### external`
- Line 114: `executor: human` → `executor: external`
- Line 429: `executor: human` → `executor: external`
- Preserved: Line 364 — conceptual.

**Commit:** `docs: update flow-reference files for human → external executor rename`

---

### Step 13: Update 2 SKILL.md copies and `streaming-demo.json` (~10 min)

**`.claude/skills/stepwise/SKILL.md` (3 lines):**
- Line 3: `agent/human/LLM workflows` → `agent/external/LLM workflows`
- Line 8: `LLM, agent, human, and script executors` → `LLM, agent, external, and script executors`
- Line 132: `(script, human, poll, llm, agent)` → `(script, external, poll, llm, agent)`
- Preserved: Lines 68, 91 — conceptual ("satisfy human step", "human steps").

**`src/stepwise/_templates/agent-skill/SKILL.md` (2 lines):**
- Line 15: `LLM, agent, human, poll, and script executors` → `LLM, agent, external, poll, and script executors`
- Line 117: `(script, human, poll, llm, agent)` → `(script, external, poll, llm, agent)`
- Preserved: Lines 11, 53, 76 — conceptual ("human steps", "satisfy human step").

**`src/stepwise/_templates/streaming-demo.json` (2 lines):**
- Line 3: `"human answer"` → `"external answer"` in description string
- Line 26: `"type": "human"` → `"type": "external"` (executor type in step definition)

**Commit:** `docs: update skill templates and streaming-demo for human → external rename`

---

### Step 14: Update `CLAUDE.md` (~3 min)

**`CLAUDE.md` (1 line):**
- Line 148: `FulfillWatchDialog` description `(schema-driven human input form)` → `(schema-driven external input form)`

**Preserved:** Line 3 ("agents and humans" — product description), lines 381, 397 ("human inspection", "human triage" — escalation concept).

**Why only 1 line:** CLAUDE.md was largely updated as part of the source code migration. The executor table (around line 86) already shows `external`. The component listing at line 145 already shows `ExternalInputPanel`. Only line 148's parenthetical description remains.

**Commit:** `docs: update CLAUDE.md component description for human → external rename`

---

### Step 15: Verify CHANGELOG entry (~2 min)

**`CHANGELOG.md:9`** already reads:
```
- **Rename `executor: human` → `executor: external`** — The "human" executor type is now called "external" across the entire codebase. This is a breaking change: update `executor: human` to `executor: external` in all `.flow.yaml` files. The underlying suspend/fulfill mechanism is unchanged. Class `HumanExecutor` → `ExternalExecutor`, event `human.rerun` → `external.rerun`, schema key `humanSteps` → `externalSteps`, web component `HumanInputPanel` → `ExternalInputPanel`.
```

This is accurate and complete. **No changes needed.** The entry was written before the migration began and correctly describes the full scope.

**Historical entries preserved:** `CHANGELOG.md:25` ("Ctrl+C during human input") stays — it describes the v0.6.0 state.

**No commit.**

---

### Step 16: Final verification (~10 min)

**Full test suite:**
```bash
uv run pytest tests/ -x -v
cd web && npm run test -- --run
cd web && npm run lint
```

**Comprehensive grep — all should return zero matches:**
```bash
# Python source and tests
rg '"human"' src/stepwise/ tests/ --type py

# YAML examples in docs
rg 'executor: human' docs/ README.md

# Schema key in docs
rg 'humanSteps' docs/ src/ web/src/

# Reference and template files
rg 'executor: human' src/stepwise/flow-reference.md .claude/skills/ src/stepwise/_templates/
rg '"type": "human"' src/stepwise/_templates/

# Executor type in SKILL descriptions
rg 'human, and script\|human, poll' .claude/skills/ src/stepwise/_templates/
```

**Allowed residual "human" (conceptual — not executor-type):**
- "human judgment", "human oversight", "human decision" in conceptual descriptions
- "human-in-the-loop" as registry tags in `docs/flow-sharing.md`
- "human-readable" in comments
- "human input"/"human steps" in prose describing the UX pattern (not the type string)
- CHANGELOG historical entries
- The `reports/` plan document itself

**Manual smoke test:**
```bash
uv run stepwise validate flows/welcome/FLOW.yaml
uv run stepwise schema flows/welcome/FLOW.yaml | python3 -c "
import json, sys
d = json.load(sys.stdin)
assert 'externalSteps' in d
assert 'humanSteps' not in d
print('OK: externalSteps present, humanSteps absent')
"
```

**No commit unless fixes needed.**

## Testing Strategy

### Automated test commands

```bash
# Full Python suite (primary gate — covers engine, executors, cache, validation)
uv run pytest tests/ -x -v

# Targeted tests for the 2 changed Python files
uv run pytest tests/test_cache.py -v -k "external_steps_never_cached"
uv run pytest tests/test_validation.py -v

# Web tests (no web changes, but verify no regressions)
cd web && npm run test -- --run
cd web && npm run lint
```

### Concrete test cases and what they verify

| Test | File:Line | Verifies | Expected |
|---|---|---|---|
| `test_external_steps_never_cached` | `tests/test_cache.py:459` | `"external" in UNCACHEABLE_TYPES` | Pass (line 460 asserts `"external"`) |
| `test_cache_warning_on_external_step` | `tests/test_validation.py` (search for cache warning test) | Validation warning fires for `type="external"` with cache enabled | Pass — `models.py:869` now checks `== "external"` |
| `TestExternalExecutor::test_start_returns_watch` | `tests/test_executors.py` | `ExternalExecutor.start()` returns `WatchSpec(mode="external")` | Pass (unchanged) |
| `TestCreateDefaultRegistry` | `tests/test_registry_factory.py` | `registry.create(ExecutorRef(type="external"))` works | Pass (unchanged) |
| Full pytest suite | `tests/` | No regressions from removing `"human"` dual-checks | Pass — no code path ever reaches `type="human"` since the registry doesn't register it |

### Grep verification matrix

| Pattern | Scope | Expected matches | Purpose |
|---|---|---|---|
| `rg '"human"' src/stepwise/ tests/ --type py` | Python source + tests | 0 | No executor-type refs |
| `rg 'executor: human' docs/ README.md` | Docs | 0 | No YAML examples |
| `rg 'humanSteps' docs/ src/ web/src/` | All code + docs | 0 | No schema key refs |
| `rg 'executor: human' src/stepwise/flow-reference.md .claude/skills/ src/stepwise/_templates/` | References + templates | 0 | No template examples |
| `rg '"type": "human"' src/stepwise/_templates/` | JSON templates | 0 | streaming-demo updated |
| `rg 'HumanExecutor\|HUMAN_RERUN\|HumanInputAborted' src/ tests/ web/` | Everywhere | 0 (except `reports/`) | No class/constant refs |

### Manual smoke tests

```bash
# Flow validation still works after removing "human" from UNCACHEABLE_TYPES
uv run stepwise validate flows/welcome/FLOW.yaml

# Schema generates correct key
uv run stepwise schema flows/welcome/FLOW.yaml | python3 -c "
import json, sys; d = json.load(sys.stdin)
assert 'externalSteps' in d and 'humanSteps' not in d
print('OK')
"

# Agent help references "external" not "human"
uv run stepwise agent-help --flows-dir flows/ 2>/dev/null | grep -q 'external' && echo 'OK: external in agent-help'
```

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| Missing a doc reference to `executor: human` | Low — docs are wrong but code works | Medium — 21 files to update | Comprehensive grep matrix in Step 16 covers all directories. Each file has exact line numbers listed in steps. |
| Over-replacing conceptual "human" (e.g., "human judgment" → "external judgment") | Medium — makes docs read unnaturally | Medium — ~94 conceptual occurrences mixed with ~40 executor-type references | Each step explicitly lists which lines to change and which to preserve. Decision rule: only change "human" when it appears as an executor type identifier (in YAML `executor:`, in type lists, in schema keys, in backtick-quoted references). |
| Three FLOW_REFERENCE.md copies updated inconsistently | Low — skill/template gives wrong examples | Medium — easy to miss one copy | Step 12 explicitly names all 3 paths with per-file line numbers. Grep verification covers all 3 directories. |
| `streaming-demo.json` has `"type": "human"` buried in nested JSON | Medium — demo flow would fail at runtime (`KeyError: 'human'` from registry) | Low — line 26 explicitly identified | Step 13 includes the exact line. Grep verification pattern `'"type": "human"'` catches it. |
| `CLAUDE.md` change affects all future Claude Code conversations | Low — the change is correct and consistent | Low — only 1 line changes | Only updates the component description parenthetical. Executor table and component listing were already migrated. |
| Conceptual "human steps" in `docs/cli.md` and `docs/agent-integration.md` prose may confuse readers who search for the executor type | Low — mild inconsistency between type name and prose | Medium | Acceptable tradeoff: these 94 occurrences describe the UX pattern ("a step that waits for a human"), not the type string. Updating them all would make the docs read awkwardly ("external steps pause for your input"). |
