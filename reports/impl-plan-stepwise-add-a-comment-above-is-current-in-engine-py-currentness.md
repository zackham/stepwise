---
title: "Implementation Plan: Add comment above _is_current in engine.py"
date: "2026-03-24"
project: stepwise
tags: [implementation, plan]
status: complete
---

## Overview

Add the comment `# Currentness with cycle detection` above the `_is_current` method in `src/stepwise/engine.py`. Investigation reveals the comment already exists at line 1052 — the spec is satisfied with zero code changes. This plan documents the spec analysis, verification, and the contingent implementation path that would apply if the comment were missing.

## Requirements

### R1: Comment text matches spec

The exact string `# Currentness with cycle detection` must exist in `src/stepwise/engine.py`.

- **AC1.1:** `grep -c "# Currentness with cycle detection" src/stepwise/engine.py` outputs `1` (exists exactly once).
- **AC1.2:** The comment is a Python `#` comment (not a docstring, not inside a string literal).

### R2: Comment is positioned directly above `_is_current`

The comment must appear on the line immediately preceding `def _is_current(...)`, with no blank lines or other code between them.

- **AC2.1:** `grep -A1 "# Currentness with cycle detection" src/stepwise/engine.py` outputs two consecutive lines: the comment, then `def _is_current(...)`.
- **AC2.2:** The comment is indented at the same level as `def _is_current` (4 spaces — method-level inside the `Engine` class).

### R3: Comment is semantically accurate

The comment text must reflect the method's actual behavior. `_is_current` does two things: (1) checks currency of a `StepRun`, and (2) uses `_checking_steps` for cycle detection. "Currentness with cycle detection" captures both.

- **AC3.1:** The method signature includes a `_checking_steps` parameter (cycle detection mechanism).
- **AC3.2:** The method body contains a cycle-break guard (`if run.step_name in _checking_steps: return True`).

### R4: No behavioral regression

The change is comment-only; no runtime behavior may change.

- **AC4.1:** `uv run pytest tests/` exits 0 with no failures.
- **AC4.2:** `src/stepwise/engine.py` parses as valid Python (`ast.parse` succeeds).

## Assumptions

1. **The comment already exists at line 1052** — Confirmed by reading `src/stepwise/engine.py:1050-1053`:
   ```
   1050:    # ── Currentness ───────────────────────────────────────────────────────
   1051:
   1052:    # Currentness with cycle detection
   1053:    def _is_current(self, job: Job, run: StepRun, _checking_steps: set | None = None) -> bool:
   ```
   The comment is indented at method level (4 spaces), matching the `def` on the next line.

2. **Single definition of `_is_current`** — Grep for `def _is_current` across the entire codebase returns exactly one match at `src/stepwise/engine.py:1053`. The method belongs to the `Engine` class (legacy tick-based engine, starting at line 55). `AsyncEngine` (line 2773) delegates to the same store/readiness logic and does not redefine `_is_current`.
   - **Verified:** `grep -rn "def _is_current" src/stepwise/` returns one result.

3. **The comment is unique** — `grep -rn "Currentness with cycle detection" src/stepwise/` returns exactly one match at `engine.py:1052`. No duplication risk.

4. **Comment matches method semantics** — The `_is_current` method (lines 1053–1098+) accepts a `_checking_steps: set | None` parameter used for cycle detection (line 1064–1068). Specifically, line 1066 checks `if run.step_name in _checking_steps` and returns `True` to break circular dependency chains (e.g., `score→refine→score`).

## Out of Scope

- **Logic changes to `_is_current`** — The spec requests only a comment. The method body (lines 1054–1098+), its docstring (lines 1054–1063), and the `_checking_steps` cycle-detection parameter are not modified.
- **Changes to `AsyncEngine`** — `AsyncEngine` (line 2773) does not have its own `_is_current`; it inherits currency logic through shared store patterns. No parallel comment needed.
- **New tests** — A comment has zero behavioral impact. Testing comment presence would be testing cosmetics, not behavior.
- **Section divider changes** — The `# ── Currentness ──...` divider at line 1050 is a separate organizational element and stays as-is.

## Architecture

`engine.py` organizes the `Engine` class using `# ── Section Name ──...` dividers (24 instances throughout the file). The `_is_current` method lives in the `# ── Currentness ──` section (divider at line 1050). The file uses a two-level comment pattern in this section:

- **Line 1050:** Section divider — `# ── Currentness ──...` (organizational, used for navigation)
- **Line 1052:** Descriptive sub-comment — `# Currentness with cycle detection` (summarizes the method's dual purpose)
- **Line 1053:** Method definition — `def _is_current(...)`

This two-level pattern (divider → descriptive comment → method) appears in other sections of the file as well, e.g., the `# ── Readiness ──` section. The comment serves as a quick-scan label that surfaces the cycle-detection aspect — complementing the 10-line docstring (lines 1054–1063) which explains the full algorithm.

The method is called from 6 sites within `engine.py` (currency checks during readiness evaluation, input resolution, and settlement). It is tested in `tests/test_engine.py` via `TestAfterFreshness` and `TestRerunStep` test classes, which exercise the currency logic including cycle scenarios.

## Implementation Steps

This plan has two paths: the **primary path** (verification-only, since the comment already exists) and the **contingent path** (what would happen if verification fails). Both are decomposed into steps with explicit dependency ordering.

### Dependency graph

```
Step 1 (inspect) ──┬──→ Step 3 (adjacency) ──→ Step 4 (syntax) ──→ Step 5 (targeted tests) ──→ Step 6 (full suite)
                   │                                ↑
Step 2 (uniqueness)┘          Step 2.5 (edit) ──────┘
                              [contingent — only if Step 1 or 3 fails]
```

Steps 1 and 2 are independent and run in parallel (no shared state). Step 3 depends on Step 1 (need to know the comment exists before checking adjacency). Step 4 depends on Step 3 (syntax check validates any edit). Steps 5→6 are sequential by blast radius (targeted before full).

### Primary path (verification-only)

#### Step 1: Inspect comment existence (< 1 min)

- **File:** `src/stepwise/engine.py`
- **Action:** Read lines 1050–1053. Confirm line 1052 = `    # Currentness with cycle detection`.
- **Depends on:** Nothing — entry point.
- **Rationale:** The spec says "add a comment." Before adding, we must check whether it already exists to avoid duplication. This is the gate that determines whether the contingent path activates.
- **Exit criteria:** Line 1052 contains the exact comment text → proceed to Step 3. Otherwise → branch to Step 2.5 (contingent).
- **Result:** Confirmed present.

#### Step 2: Verify uniqueness (< 1 min, parallel with Step 1)

- **File:** `src/stepwise/engine.py`
- **Action:** `grep -c "# Currentness with cycle detection" src/stepwise/engine.py`
- **Depends on:** Nothing — independent of Step 1. Runs in parallel.
- **Rationale:** If count > 1, we need to deduplicate rather than add. If count = 0 and Step 1 also found nothing, that's consistent. If count = 1, we're clean.
- **Exit criteria:** Output = `1`.
- **Result:** Count = 1.

#### Step 3: Verify adjacency to method definition (< 1 min)

- **Action:** `grep -A1 "# Currentness with cycle detection" src/stepwise/engine.py`
- **Depends on:** Step 1 (must confirm comment exists before checking what follows it).
- **Rationale:** The spec says "above `_is_current`." The comment could exist but be in the wrong location (e.g., above a different method, or separated by blank lines). This step verifies placement, not just existence.
- **Exit criteria:** Output shows comment immediately followed by `def _is_current(...)` with no intervening lines.
- **Result:** Adjacency confirmed.

#### Step 4: Validate syntax (< 1 min)

- **Action:** `uv run python -c "import ast; ast.parse(open('src/stepwise/engine.py').read()); print('OK')"`
- **Depends on:** Step 3 (if the contingent edit path ran, this validates it didn't break syntax). In the primary path this is a baseline check.
- **Rationale:** Guards against a corrupted file state that could cause false confidence in later test steps.
- **Exit criteria:** Prints `OK`, exits 0.

#### Step 5: Run targeted regression tests (< 2 min)

- **Action:** `uv run pytest tests/test_engine.py -v -k "current or freshness or rerun"`
- **Depends on:** Step 4 (syntax must be valid before importing the module for tests).
- **Rationale:** Runs only tests that exercise `_is_current` — `TestAfterFreshness` and `TestRerunStep` in `tests/test_engine.py`. Catches regressions fast with a small blast radius.
- **Exit criteria:** All matched tests pass.

#### Step 6: Run full test suite (< 5 min)

- **Action:** `uv run pytest tests/`
- **Depends on:** Step 5 (if targeted tests fail, there's no point running the full suite — fix first).
- **Rationale:** Final confirmation across all ~40 test files. Required by "push to master = release" guardrail (CLAUDE.md).
- **Exit criteria:** All tests pass, 0 failures. Satisfies requirement R4.

### Contingent path (if comment is missing)

#### Step 2.5: Add the comment (< 1 min)

- **File:** `src/stepwise/engine.py`
- **Action:** Insert `    # Currentness with cycle detection` on the blank line immediately above `def _is_current(...)` (currently line 1051). Use the `Edit` tool with `old_string` = `\n    def _is_current(` and `new_string` = `\n    # Currentness with cycle detection\n    def _is_current(`.
- **Depends on:** Step 1 finding the comment absent, AND Step 2 confirming count = 0.
- **Rationale for placement:** The blank line at 1051 (between the section divider and method def) is the natural location, matching the two-level pattern used elsewhere in the file.
- **Exit criteria:** `grep -A1 "# Currentness with cycle detection" src/stepwise/engine.py` shows the comment followed by `def _is_current`. Then proceed to Step 4.
- **Not executed:** Comment already exists (Step 1 passed).

## Testing Strategy

No new tests required — this is a comment-only, zero-behavioral-impact change.

**Verification commands (ordered by specificity, then blast radius):**

1. **Comment existence** — confirms the exact text exists once:
   ```
   grep -c "# Currentness with cycle detection" src/stepwise/engine.py
   ```
   Expected: `1`

2. **Comment adjacency** — confirms the comment is directly above the method:
   ```
   grep -A1 "# Currentness with cycle detection" src/stepwise/engine.py
   ```
   Expected output:
   ```
       # Currentness with cycle detection
       def _is_current(self, job: Job, run: StepRun, _checking_steps: set | None = None) -> bool:
   ```

3. **Syntax validation** — confirms the file parses as valid Python:
   ```
   uv run python -c "import ast; ast.parse(open('src/stepwise/engine.py').read()); print('OK')"
   ```
   Expected: `OK`

4. **Targeted regression** — runs currency-specific tests:
   ```
   uv run pytest tests/test_engine.py -v -k "current or freshness or rerun"
   ```
   Expected: all pass.

5. **Full regression** — confirms no side effects across the entire suite:
   ```
   uv run pytest tests/
   ```
   Expected: all pass, 0 failures.

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Comment is accidentally deleted in a future refactor | Low | Low — cosmetic only | The comment is visible in the section header area; `grep` in CI could catch removal if desired |
| Line numbers drift as `engine.py` evolves | Medium | None — all verification uses content matching (`grep`), not line numbers | Plan documents line numbers for reference only; all acceptance criteria are content-based |
| A parallel edit introduces a duplicate comment | Very low | Low — cosmetic confusion | Step 2 verifies exactly one occurrence; would surface immediately |
| Spec was intended to request something different (e.g., a different comment text) | Low | Low — easy to re-edit | The plan explicitly quotes the spec text and the existing comment text; discrepancy would be caught during review |
