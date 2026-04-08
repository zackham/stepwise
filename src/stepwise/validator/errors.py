"""Structured ValidationError, PairVerdict, and ValidationResult dataclasses.

These are the public surface for the top-level validate(flow) function.
ValidationError carries enough structured fields (rule_id + step_names +
session + fix_suggestion) that tests assert on structure, not just message
substrings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


RuleId = Literal[
    "pair_unsafe",
    "multi_root_not_mutex",
    "fork_target_missing",
    "fork_target_no_session",
    "fork_target_not_agent",
    "fork_target_not_in_mhb",
    "fork_from_in_subflow",
    "fork_from_on_for_each",
    "fork_from_requires_chain_root",
    "retry_on_session_writer",
    "cache_on_session_writer",
    "retry_on_fork_source",
    "cache_on_fork_source",
    "back_edge_unsupported",
    "cyclic_dependency",
    "is_present_unsupported",
    "dynamic_session_name",
    "static_session_name_invalid",
    # Step 7 (§11): loop-back binding rule_ids
    "loop_back_binding_ambiguous_closure",
    "is_present_not_loop_back",
    "is_present_mixed_scope_any_of",
]


@dataclass(frozen=True)
class ValidationError:
    """A single validator-emitted error.

    rule_id is one of the canonical RuleId literals so tests can assert
    on structure, not just message substrings. step_names, session, and
    fix_suggestion are optional structured fields populated when the
    rule has the relevant context.
    """
    rule_id: RuleId
    message: str
    step_names: tuple[str, ...] = ()
    session: str | None = None
    fix_suggestion: str | None = None


@dataclass(frozen=True)
class PairVerdict:
    """The result of pair_safe(X, Y) for a single unordered pair.

    Mirrors the fuzzer's PairVerdict shape (scripts/stepwise_fuzzer/
    spec.py:23-30) so the differential check has structural parity.
    """
    x: str
    y: str
    safe: bool
    reason: str


@dataclass
class ValidationResult:
    """The verdict of validate(flow): accept or reject + structured errors."""
    accepted: bool
    errors: list[ValidationError] = field(default_factory=list)
    pair_verdicts: list[PairVerdict] = field(default_factory=list)
