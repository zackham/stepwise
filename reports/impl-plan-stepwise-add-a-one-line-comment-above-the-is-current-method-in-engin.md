---
title: "Implementation Plan: Add comment above _is_current method"
date: "2026-03-24"
project: stepwise
tags: [implementation, plan]
status: active
---

## Overview

Add a single-line comment above the `_is_current` method in `src/stepwise/engine.py` to document its cycle-detection behavior for circular dependency chains.

## Requirements

1. **Add comment**: Insert `# Currentness check with cycle detection for circular dep chains` on the blank line immediately above `def _is_current(...)` at line 1027 of `src/stepwise/engine.py`.
   - **Acceptance criteria**:
     - The comment appears on the line directly preceding `def _is_current(self, job: Job, run: StepRun, _checking_steps: set | None = None) -> bool:`.
     - The section header `# ── Currentness ───...` (line 1025) remains unchanged above.
     - A blank line separates the section header from the new comment.
     - No other lines in the file are modified.
     - All existing tests pass.

## Assumptions

1. **Method location**: `_is_current` is defined at `src/stepwise/engine.py:1027`, preceded by a blank line at 1026 and section header at 1025. **Verified**: Read of lines 1025–1039 confirms the exact sequence: section header → blank line → `def _is_current(self, job: Job, run: StepRun, _checking_steps: set | None = None) -> bool:`.
2. **Comment indentation**: The method is inside the `_EngineBase` class body (4-space indent). The new comment must also use 4-space indent. **Verified**: Line 1025 reads `    # ── Currentness ───...` and line 1027 reads `    def _is_current(...)` — both 4-space indent.
3. **Cycle detection is the `_checking_steps` parameter**: The method's docstring (lines 1033–1036) states: "_checking_steps tracks which step names are being checked up the call stack. If we encounter a step already being checked, we've hit a circular dep chain (score→refine→score). Treat it as current to break the cycle." The comment accurately summarizes this. **Verified**: Read of lines 1033–1036.

## Out of Scope

- **Method logic or docstring changes** — the spec requests only a comment above the method, not changes to its documentation or behavior.
- **Comments on other methods** — scoped to `_is_current` only.
- **Refactoring** — no changes to the `_checking_steps` mechanism or any other code.

## Architecture

`engine.py` uses `# ── Section Name ───` dividers to organize the `_EngineBase` class into logical sections. There are 22 such sections (verified via grep: `# ── ` matches 22 times). The universal pattern is:

```
    # ── Section Name ──────────────────────────
                                                    ← blank line
    def first_method_in_section(...):
```

No existing section has an intermediate comment between the divider and the first `def`. This change introduces a method-level annotation as a new sub-pattern within the Currentness section at `engine.py:1025`. This is a deliberate, spec-driven deviation — the comment documents non-obvious recursive cycle-breaking behavior that warrants calling out above the signature, complementing the docstring inside.

## Implementation Steps

1. **Edit `src/stepwise/engine.py`** — Single edit operation. Replace the blank line between the section header and `def _is_current` with the comment line (preserving a blank line above):

   **old_string** (lines 1025–1027):
   ```
       # ── Currentness ───...

       def _is_current(self, job: Job, run: StepRun, _checking_steps: set | None = None) -> bool:
   ```

   **new_string**:
   ```
       # ── Currentness ───...

       # Currentness check with cycle detection for circular dep chains
       def _is_current(self, job: Job, run: StepRun, _checking_steps: set | None = None) -> bool:
   ```

   - **Ordering rationale**: This is the only implementation step. No prerequisites — the file content was verified during planning. No follow-up steps depend on it other than testing.

## Testing Strategy

All commands run from the repo root (`/home/zack/work/stepwise/`).

1. **Syntax check** (fast, <1s):
   ```
   uv run python -c "import stepwise.engine"
   ```
   Expected: exit code 0, no output. Catches `IndentationError` or `SyntaxError` from bad edit.

2. **Engine test suite** (targeted, ~10s):
   ```
   uv run pytest tests/test_engine.py -x -q
   ```
   Expected: all tests pass, 0 failures. Confirms no behavioral regression from the comment insertion.

3. **Content verification** (manual sanity check):
   ```
   grep -n "Currentness check with cycle detection" src/stepwise/engine.py
   ```
   Expected: exactly one match, on line 1027 (the old blank line, now the comment). Confirms correct placement.

4. **Full test suite** (comprehensive, optional):
   ```
   uv run pytest tests/ -x -q
   ```
   Expected: all ~40 test files pass. Only needed if the engine tests surface something unexpected.

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Indentation mismatch | Low | Low — `IndentationError` | Verified indent is 4 spaces from reading lines 1025–1027; syntax check in testing step 1 catches immediately |
| Edit matches wrong location | Very low | Low — comment in wrong place | old_string includes the full section header + method signature for unique match (verified unique via grep) |
| Line numbers drifted | Low | None | Edit tool matches on string content, not line numbers; content verified during planning |

## Dependencies

**Step ordering**: There is only one implementation step (the edit), so there are no inter-step dependencies. Testing follows the edit sequentially — syntax check first (fast feedback), then targeted engine tests, then optional full suite. Each test step is independent of the others but all depend on the edit being complete.

**External dependencies**: None. No new packages, config changes, or migrations. The only artifact is a single inserted line in `src/stepwise/engine.py`.
