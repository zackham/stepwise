---
title: "Implementation Plan: Add cycle detection comment to _is_current"
date: "2026-03-24"
project: stepwise
tags: [implementation, plan]
status: completed
---

## Overview

Add the comment `# Currentness with cycle detection` above the `_is_current` method in `src/stepwise/engine.py`. The comment already exists — this plan documents verification that the requirement is met and defines the regression testing strategy.

## Requirements

1. **Comment placement** — The exact text `# Currentness with cycle detection` must appear on the line immediately before `def _is_current(...)` in `src/stepwise/engine.py`.
   - **Acceptance criteria:** `grep -n "Currentness with cycle detection" src/stepwise/engine.py` returns exactly one match, and the following line contains `def _is_current`.
   - **Acceptance criteria:** The existing section divider `# ── Currentness ──...` at line 1032 is preserved unchanged.
   - **Acceptance criteria:** The file parses as valid Python (`ast.parse()` succeeds).
   - **Acceptance criteria:** All existing tests pass without modification.

## Assumptions

1. **The comment already exists** — `src/stepwise/engine.py:1034` contains `# Currentness with cycle detection`, directly above `def _is_current` at line 1035.
   - **Verified:** Read `src/stepwise/engine.py` lines 1032–1045. Lines 1034–1035 show the comment followed by the method definition.

2. **Single definition of `_is_current`** — The method is defined once in the entire codebase, at `src/stepwise/engine.py:1035`, inside the `Engine` class.
   - **Verified:** `grep "def _is_current" src/stepwise/engine.py` returns exactly one match at line 1035. No other file in `src/stepwise/` defines this method.

3. **Section divider pattern** — `engine.py` uses `# ── Section Name ──...` dividers to organize the `Engine` class (24 instances: lines 147, 294, 485, 692, 871, 1032, 1154, 1236, 1642, 1998, 2054, 2132, 2186, 2326, 2440, 2561, 2603, 2703, 2718, 2773, 2853, 2908, 3005, 3156, 3185). The comment at line 1034 is a supplementary annotation within the `# ── Currentness ──` section, not a replacement for the divider.
   - **Verified:** `grep -n "# ──" src/stepwise/engine.py` returns all 24 section dividers.

4. **`_is_current` is called from 6 sites** within `engine.py` (lines 918, 959, 1024, 1092, 1107, 1172) and tested directly at `tests/test_engine.py:427`. The comment does not affect any call site behavior.
   - **Verified:** `grep -n "_is_current" src/stepwise/engine.py` and `grep -rn "_is_current" tests/`.

5. **No dedicated `test_currentness.py` exists** — currentness tests live in `tests/test_engine.py` (e.g., `TestAfterFreshness` at line 433, direct `_is_current` assertion at line 427).
   - **Verified:** `glob tests/test_current*.py` returns no files.

## Out of Scope

- **Logic changes to `_is_current`** — The spec requests only a comment. The method body, its docstring (lines 1036–1045), and the `_checking_steps` cycle-detection parameter are untouched.
- **Changes to call sites** — The 6 internal call sites (lines 918, 959, 1024, 1092, 1107, 1172) and 1 test call site (`tests/test_engine.py:427`) are not modified.
- **New tests** — A comment-only change has zero behavioral impact; adding tests for the presence of a comment would be testing cosmetics, not behavior.
- **Section divider modifications** — The `# ── Currentness ──...` divider at line 1032 stays as-is.

## Architecture

`engine.py` organizes the `Engine` class (lines 55–2771) and `AsyncEngine` class (lines 2773–3224) using `# ── Section Name ──...` dividers. The `_is_current` method belongs to the `# ── Currentness ──` section (line 1032). This section contains one method (`_is_current`, lines 1035–1152) which implements recursive currency checking with cycle detection via the `_checking_steps` parameter.

The comment at line 1034 serves as a quick-scan label that surfaces the cycle-detection aspect — complementing the 10-line docstring (lines 1036–1045) which explains the full algorithm. This pattern of supplementary comments above methods within section blocks is consistent with the file's style.

## Implementation Steps

No code changes are required. The following verification steps confirm the requirement is already met.

### Step 1: Verify comment exists at correct location
- **Action:** Read `src/stepwise/engine.py` lines 1032–1036
- **Expected:** Line 1034 = `    # Currentness with cycle detection`, line 1035 = `    def _is_current(...)`
- **Dependencies:** None
- **Status:** Done — verified via `Read` tool during plan exploration

### Step 2: Verify comment is unique
- **Action:** Run `grep -c "Currentness with cycle detection" src/stepwise/engine.py`
- **Expected:** Output = `1` (exactly one occurrence)
- **Dependencies:** None (can run in parallel with Step 1)
- **Status:** Done — grep returned single match at line 1034

### Step 3: Verify file syntax integrity
- **Action:** Run `uv run python -c "import ast; ast.parse(open('src/stepwise/engine.py').read())"`
- **Expected:** Exits 0, prints no errors
- **Dependencies:** None (can run in parallel with Steps 1–2)
- **Status:** Done — printed `OK`

### Step 4: Run targeted engine tests
- **Action:** Run `uv run pytest tests/test_engine.py -v -k "current or freshness"`
- **Expected:** All currentness-related tests pass (specifically the `_is_current` assertion at `test_engine.py:427` and `TestAfterFreshness` at line 433)
- **Dependencies:** Step 3 (syntax must be valid before running tests)

### Step 5: Run full test suite
- **Action:** Run `uv run pytest tests/`
- **Expected:** All tests pass with 0 failures
- **Dependencies:** Step 4 (targeted tests should pass first to catch issues early)

## Testing Strategy

**No new tests required** — this is a comment-only change with zero behavioral impact.

**Verification commands (ordered by blast radius):**

1. **Syntax check** — confirms the file is valid Python after any edit:
   ```
   uv run python -c "import ast; ast.parse(open('src/stepwise/engine.py').read())"
   ```
   Expected: exits 0.

2. **Comment placement check** — confirms the comment exists exactly once, directly above `def _is_current`:
   ```
   grep -n "Currentness with cycle detection" src/stepwise/engine.py
   ```
   Expected: `1034:    # Currentness with cycle detection` (single match).

3. **Adjacency check** — confirms the comment is immediately followed by the method definition:
   ```
   sed -n '1034,1035p' src/stepwise/engine.py
   ```
   Expected:
   ```
       # Currentness with cycle detection
       def _is_current(self, job: Job, run: StepRun, _checking_steps: set | None = None) -> bool:
   ```

4. **Targeted regression** — runs only the tests that exercise `_is_current`:
   ```
   uv run pytest tests/test_engine.py::TestRerunStep -v
   uv run pytest tests/test_engine.py::TestAfterFreshness -v
   ```
   Expected: all pass. These classes contain the only direct `_is_current` call in tests (line 427) and the freshness logic that depends on currency.

5. **Full regression** — confirms no side effects across the entire suite:
   ```
   uv run pytest tests/
   ```
   Expected: all ~40 test files pass, 0 failures.

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Comment gets deleted by a future refactor | Low | Low — cosmetic only | The comment is visible in the section header area; grep-based CI check could catch removal if desired |
| Line numbers drift as file evolves | Medium | None — the comment is anchored by content, not line number | All verification commands use content matching (`grep`), not line-number assumptions |
| Duplicate comment introduced by parallel edit | Very low | Low — grep count check catches it | Step 2 verifies exactly one occurrence |
