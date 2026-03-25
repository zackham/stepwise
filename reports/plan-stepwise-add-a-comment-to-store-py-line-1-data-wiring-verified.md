# Plan: Add a comment to store.py line 1

## Overview

Add the comment `# Data wiring verified` to line 1 of `src/stepwise/store.py`, above the existing module docstring.

## Requirements

| # | Requirement | Acceptance Criteria |
|---|-------------|-------------------|
| 1 | Comment `# Data wiring verified` appears on line 1 of `store.py` | `head -1 src/stepwise/store.py` outputs `# Data wiring verified` |
| 2 | Existing code is unchanged | All tests pass; no functional diff beyond the added line |

## Assumptions

| # | Assumption | Verified |
|---|-----------|----------|
| 1 | `store.py` currently starts with a docstring on line 1 (`"""SQLite persistence…"""`) | Yes — read file, line 1 is `"""SQLite persistence: jobs, step_runs, events tables."""` |
| 2 | No other file references line numbers in `store.py` that would break | Yes — this is a pure comment addition; Python line references are runtime, not static |

## Implementation Steps

| Order | File | Change |
|-------|------|--------|
| 1 | `src/stepwise/store.py` | Insert `# Data wiring verified` as new line 1 (existing content shifts down by one line) |

## Testing Strategy

```bash
# Verify the comment is present
head -1 src/stepwise/store.py
# Expected: # Data wiring verified

# Verify no regressions
uv run pytest tests/
```
