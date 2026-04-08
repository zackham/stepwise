"""Stepwise validator subpackage.

Pure-function validator pieces: predicate algebra, mutex checks, and (future)
mhb computation, pair safety, and flow-level validation passes. No imports
from stepwise.engine or stepwise.runner — this package must be testable in
isolation.

Step 1 lands `mutex.py` (predicate mutex algebra + strict-type evaluator).
Steps 2–7 will add `mhb.py`, `pair_check.py`, and `errors.py`.
"""

from stepwise.validator.mutex import (
    evaluate_when_predicate,
    predicates_mutex,
)

__all__ = [
    "evaluate_when_predicate",
    "predicates_mutex",
]
