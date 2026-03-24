---
title: "Implementation Plan: Add cycle detection comment to _is_current"
date: "2026-03-24T00:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

## Overview

Add the comment `# Currentness with cycle detection` above the `_is_current` method in `src/stepwise/engine.py`, following the codebase's established section-comment conventions.

## Requirements

1. **Add comment** — The line `# Currentness with cycle detection` must appear directly above the `def _is_current(...)` definition.
   - **Acceptance criteria:** `grep -n "Currentness with cycle detection" src/stepwise/engine.py` returns exactly one match, on the line immediately before `def _is_current`.
   - **Acceptance criteria:** The existing section divider `# ── Currentness ──...` (line 1032) is preserved unchanged.
   - **Acceptance criteria:** All existing tests pass without modification.

## Assumptions

1. **Method location** — `_is_current` is defined at `src/stepwise/engine.py:1034`, inside the `Engine` class. The surrounding context is:
   - Line 1032: `# ── Currentness ───────...` (section divider)
   - Line 1033: blank line
   - Line 1034: `def _is_current(self, job: Job, run: StepRun, _checking_steps: set | None = None) -> bool:`
   - **Verified** by reading `src/stepwise/engine.py:1025–1064`.

2. **Section comment pattern** — The file uses `# ── Name ──...` as section dividers throughout (23 instances found via grep). The new comment is a *supplementary* annotation, not a replacement for the section divider. This is analogous to how the method's docstring (lines 1035–1044) already explains cycle detection — the comment serves as a quick scan label.
   - **Verified** by grepping for `# ──` across the file (lines 147, 294, 485, 692, 871, 1032, 1153, 1235, etc.).

3. **No other method named `_is_current`** — The method exists once in the file, so the edit target is unambiguous.
   - **Verified** by grep: `def _is_current` returns exactly one match at line 1034.

## Out of Scope

- **Logic changes** — No modifications to the `_is_current` method body or any other code. The spec requests only a comment.
- **Test changes** — Comment-only change has no behavioral impact; no test updates needed.
- **Refactoring the section divider** — The existing `# ── Currentness ──...` divider stays as-is.

## Architecture

`engine.py` organizes its `Engine` class using `# ── Section Name ──...` dividers (e.g., `# ── Readiness ──` at line 871, `# ── Launching ──` at line 1235). Each section groups related private methods. The `_is_current` method lives under the `# ── Currentness ──` section (line 1032) and already contains a detailed docstring (lines 1035–1044) explaining the cycle-detection guard via `_checking_steps`.

The new comment `# Currentness with cycle detection` will sit between the section divider and the method definition, serving as a quick-scan label that surfaces the cycle-detection aspect without requiring reading the full docstring. This mirrors how comments appear elsewhere in the file as short annotations above methods within their section blocks.

**Placement decision:** Insert on the blank line 1033 (between the section divider and `def`), preserving the divider → comment → def visual structure used in the rest of the file.

## Implementation Steps

1. **Edit `src/stepwise/engine.py:1033`** — Replace the blank line between the section divider (line 1032) and `def _is_current` (line 1034) with the comment `    # Currentness with cycle detection` (4-space indent to match class method indentation level).
   - File: `src/stepwise/engine.py`
   - Old: blank line at 1033
   - New: `    # Currentness with cycle detection`
   - Dependencies: None.

2. **Verify edit** — Run `grep -n "Currentness with cycle detection" src/stepwise/engine.py` to confirm exactly one match on the line before `def _is_current`.
   - Dependencies: Step 1 must complete first.

3. **Run tests** — Execute `uv run pytest tests/` to confirm no regressions.
   - Dependencies: Step 1 must complete first. Can run in parallel with step 2.

## Testing Strategy

**No new tests required** — this is a comment-only change with zero behavioral impact.

**Regression verification:**

1. **Full Python test suite:**
   ```
   uv run pytest tests/
   ```
   Expected: all ~40 test files pass. Specifically, engine tests that exercise `_is_current`:
   - `uv run pytest tests/test_engine.py -v` — covers currentness logic, cycle detection (the `_checking_steps` guard), and settlement.
   - `uv run pytest tests/test_currentness.py -v` (if it exists) — dedicated currentness tests.

2. **Syntactic verification:**
   ```
   python -c "import ast; ast.parse(open('src/stepwise/engine.py').read())"
   ```
   Expected: exits 0 (valid Python after edit).

3. **Comment placement verification:**
   ```
   grep -n "Currentness with cycle detection" src/stepwise/engine.py
   ```
   Expected: exactly one match, on the line immediately before `def _is_current`.

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Edit targets wrong line (file shifted since verification) | Low | Low — caught immediately by grep check in step 2 | Use `old_string`/`new_string` context matching (not line numbers) for the edit |
| Indentation mismatch causes syntax error | Low | Low — caught by AST parse in testing step | Use 4-space indent matching surrounding method definitions (verified at lines 1034, 871, etc.) |
