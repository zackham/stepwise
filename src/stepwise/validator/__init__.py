"""Stepwise validator subpackage.

Pure-function validator pieces: predicate algebra, mutex checks, mhb /
mhb_strict traversal, back-edge detection, and the top-level
validate(flow) integration pass. No imports from stepwise.engine or
stepwise.runner — this package must be testable in isolation.

Step 1 lands `mutex.py` (predicate mutex algebra + strict-type evaluator).
Step 2 lands `mhb.py` (mhb / mhb_strict ancestors, mutex_when_proved,
inherited_mutex). Step 3 lands `errors.py`, `back_edges.py`, and
`validate.py` (top-level integration pass).
"""

from stepwise.validator.back_edges import (
    compute_back_edges,
    compute_topological_order,
    find_cycle_nodes,
)
from stepwise.validator.errors import (
    PairVerdict,
    RuleId,
    ValidationError,
    ValidationResult,
)
from stepwise.validator.mhb import (
    compute_mhb_ancestors,
    compute_mhb_strict_ancestors,
    inherited_mutex,
    mutex_when_proved,
)
from stepwise.validator.mutex import (
    evaluate_when_predicate,
    predicates_mutex,
)
from stepwise.validator.validate import (
    pair_safe,
    validate,
)

__all__ = [
    "PairVerdict",
    "RuleId",
    "ValidationError",
    "ValidationResult",
    "compute_back_edges",
    "compute_mhb_ancestors",
    "compute_mhb_strict_ancestors",
    "compute_topological_order",
    "evaluate_when_predicate",
    "find_cycle_nodes",
    "inherited_mutex",
    "mutex_when_proved",
    "pair_safe",
    "predicates_mutex",
    "validate",
]
