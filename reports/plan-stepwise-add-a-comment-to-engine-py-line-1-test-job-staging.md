# Plan: Add a comment to engine.py line 1 — "test job staging"

## Overview

Add the comment `# test job staging` to line 1 of `src/stepwise/engine.py`, above the existing module docstring.

## Requirements

| # | Requirement | Acceptance Criteria |
|---|---|---|
| R1 | Add comment `# test job staging` as the first line of `engine.py` | Line 1 of `src/stepwise/engine.py` is `# test job staging` |
| R2 | Existing code is unaffected | All existing imports, classes, and logic remain intact; test suite passes |

## Assumptions

| # | Assumption | Verified Against |
|---|---|---|
| A1 | `engine.py` currently starts with a module docstring on line 1 | Read `src/stepwise/engine.py:1` — confirmed: `"""Tick-based workflow engine: readiness, currentness, launching, exit resolution."""` |
| A2 | No linting or formatting rules prohibit comments before the module docstring | `pyproject.toml` and project conventions — no such restriction found |

## Implementation Steps

| Order | File | Change |
|---|---|---|
| 1 | `src/stepwise/engine.py` | Insert `# test job staging` as a new line 1, pushing the existing docstring to line 2 |

## Testing Strategy

```bash
# Verify the comment is present
head -1 src/stepwise/engine.py
# Expected: # test job staging

# Run the full test suite to confirm no regressions
uv run pytest tests/
```
