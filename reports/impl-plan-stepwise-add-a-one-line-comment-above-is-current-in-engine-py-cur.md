---
title: "Implementation Plan: Add comment above _is_current in engine.py"
date: "2026-03-24"
project: stepwise
tags: [implementation, plan]
status: active
---

## Overview

Add a one-line comment `# Currentness with cycle detection` above the `_is_current` method definition at line 1034 of `src/stepwise/engine.py`, replacing the blank line at line 1033.

## Requirements

1. **Add comment**: Insert `# Currentness with cycle detection` on the line immediately before `def _is_current(...)` in `src/stepwise/engine.py`.
   - **Acceptance criteria**: The comment appears at the correct indentation (4 spaces, matching class method level) on line 1034, pushing `def _is_current` to line 1035. The section separator `# ── Currentness ──…` at line 1032 remains untouched. All 8 call sites of `_is_current` (lines 918, 959, 1024, 1091, 1106, 1171 in `engine.py`) continue to resolve correctly. All existing tests pass.

## Assumptions

1. **Method location**: `_is_current` is defined at line 1034 of `src/stepwise/engine.py`, preceded by a blank line (1033) and a section separator comment (1032). **Verified**: `Read` of lines 1028–1047 confirms this exact layout.
2. **No other `_is_current` definitions**: There is exactly one `def _is_current` in the codebase, in `engine.py`. **Verified**: `Grep` for `def _is_current` across the repo returned only line 1034 of `engine.py`.
3. **Comment-only change**: The `_is_current` method already implements cycle detection via the `_checking_steps` parameter (lines 1040–1043 docstring, lines 1045–1047 implementation). The comment describes existing behavior — no logic change needed. **Verified**: `Read` of lines 1034–1047 confirms the cycle-detection guard.

## Out of Scope

- **Logic changes**: No modifications to `_is_current` behavior or any other method. The spec is comment-only.
- **Test changes**: No new tests needed — comment additions have no behavioral effect.
- **Documentation**: No README, CHANGELOG, or docstring updates.

## Architecture

`engine.py` uses section separator comments (e.g., `# ── Currentness ──…` at line 1032, similar separators for Readiness, Settlement, etc.) to delineate method groups. The new comment sits between the section separator and the method definition, adding a concise label that highlights the cycle-detection aspect. This follows the same pattern seen elsewhere in the file where inline comments annotate method purpose above the `def` line.

The 8 internal call sites of `_is_current` (at lines 918, 959, 1024, 1091, 1106, 1171 in `engine.py`) and the test file (`tests/test_engine.py`, class `TestTransitiveCurrentness` at line 246) are unaffected since only a comment line is inserted.

## Implementation Steps

1. **Edit `src/stepwise/engine.py` line 1033** — Replace the blank line between the section separator (line 1032) and `def _is_current` (line 1034) with `    # Currentness with cycle detection` (4-space indent to match class method level).
   - **Depends on**: Nothing — this is the sole step.
   - **Rationale for being step 1**: There is only one change to make; no prep or follow-up steps are needed.

## Dependencies

This is a single-step plan with no inter-step dependencies. The only external dependency is that the file `src/stepwise/engine.py` exists and has the expected content at lines 1032–1034, which has been verified via `Read`.

## Testing Strategy

1. **Verify comment placement** (manual):
   ```
   uv run python -c "import inspect, stepwise.engine; src = inspect.getsource(stepwise.engine.EngineBase._is_current); assert src.strip().startswith('def _is_current')"
   ```
   Confirms the method is still importable and parseable after the edit.

2. **Run currentness-specific tests**:
   ```
   uv run pytest tests/test_engine.py::TestTransitiveCurrentness -v
   ```
   This class (line 246 of `tests/test_engine.py`) directly exercises `_is_current` via transitive currentness scenarios. Passing confirms no accidental damage.

3. **Run full test suite**:
   ```
   uv run pytest tests/
   ```
   All ~40 test files must pass, confirming the comment insertion didn't introduce a syntax error or break imports.

## Risks & Mitigations

1. **Risk**: Indentation mismatch causes `IndentationError` at import time.
   **Mitigation**: Use 4-space indent matching the surrounding method definitions (verified by reading lines 1032–1047). Test step 1 (`python -c "import ..."`) catches this immediately.

2. **Risk**: Accidental deletion of adjacent lines during edit.
   **Mitigation**: The `Edit` tool targets only the blank line at 1033 with an exact-match `old_string`. Any mismatch fails the edit rather than silently corrupting.
