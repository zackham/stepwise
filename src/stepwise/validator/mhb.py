"""§7.2 mhb / §7.2.1 mhb_strict / §6.2 mutex_when_proved / §7.3 inherited_mutex.

Pure analysis functions over WorkflowDefinition. No engine, runner, or
yaml_loader imports — only stepwise.models and stepwise.validator.mutex.

Direct port of scripts/stepwise_fuzzer/spec.py:106-301 from the vita repo,
adapted to production data structures:
  - WorkflowDefinition.steps is dict[str, StepDefinition] (not list)
  - StepDefinition.after is list[str] (not list[AfterDep]); back-edges
    handled by a side-table parameter
  - InputBinding uses local_name (not name) and any_of_sources (list of
    (step, field) tuples) instead of separate inputs_any_of lists
  - StepDefinition.after_any_of is list[list[str]] (groups of step names)

Step 2 deliberately leaves these algebra functions decoupled from
back-edge detection: callers pass back_edges as a side-table. Step 3 (or
step 7) computes the back-edge set structurally.

CRITICAL (council pass 5, gpt-5.4): mhb_strict MUST be computed by
positive enumeration of clauses (a')/(b')/(c')/(d'), NOT by post-filtering
mhb. An implementation that filters mhb is wrong because clause (e)'s
mutex-complete fan-in contributions cannot be distinguished from clause
(a) contributions in the result.
"""

from __future__ import annotations

from typing import Callable

from stepwise.models import (
    InputBinding,
    StepDefinition,
    WhenPredicate,
    WorkflowDefinition,
)
from stepwise.validator.mutex import predicates_mutex as _default_predicates_mutex


# ─── Internal helpers ─────────────────────────────────────────────────────


def _get_step(flow: WorkflowDefinition, name: str) -> StepDefinition | None:
    """Look up a step by name. WorkflowDefinition.steps is dict[str, StepDef]."""
    return flow.steps.get(name)


def _iter_steps(flow: WorkflowDefinition):
    """Iterate StepDefinitions in insertion order."""
    return flow.steps.values()


# ─── §6.2 mutex_when_proved ───────────────────────────────────────────────


def _resolve_input_source(
    step: StepDefinition, input_name: str
) -> tuple[str, str] | None:
    """Find the (producer_step, field) tuple for a step's local input.

    Returns None if:
      - the input binding is not present
      - the binding is an any_of (excluded from mutex proofs by §6.2)
    """
    for ib in step.inputs:
        if ib.local_name == input_name:
            if ib.any_of_sources is not None:
                return None  # any_of excluded by §6.2
            return (ib.source_step, ib.source_field)
    return None


def mutex_when_proved(
    flow: WorkflowDefinition,
    x_step: StepDefinition,
    y_step: StepDefinition,
    predicates_mutex_fn: Callable[[WhenPredicate, WhenPredicate], bool] | None = None,
) -> bool:
    """Per §6.2: are two steps' predicate-form when: clauses provably mutex?

    Both steps must:
      1. Have predicate-form when: (WhenPredicate, not str/None).
      2. Reference the same local input name.
      3. Resolve that input to the same (producer_step, producer_field).
      4. Not use an any_of binding (§6.2 explicit exclusion).
      5. The two predicates must satisfy predicates_mutex().
    """
    if predicates_mutex_fn is None:
        predicates_mutex_fn = _default_predicates_mutex

    x_when = x_step.when
    y_when = y_step.when
    if not isinstance(x_when, WhenPredicate):
        return False
    if not isinstance(y_when, WhenPredicate):
        return False
    if x_when.input != y_when.input:
        return False

    x_src = _resolve_input_source(x_step, x_when.input)
    y_src = _resolve_input_source(y_step, y_when.input)
    if x_src is None or y_src is None:
        return False
    if x_src != y_src:
        return False

    return predicates_mutex_fn(x_when, y_when)


def _members_pairwise_mutex(
    flow: WorkflowDefinition,
    members: tuple[str, ...] | list[str],
    predicates_mutex_fn: Callable[[WhenPredicate, WhenPredicate], bool] | None = None,
) -> bool:
    """Are all members of an any_of group pairwise mutex (per §6.2)?"""
    if len(members) < 2:
        return False
    for i in range(len(members)):
        x = _get_step(flow, members[i])
        if x is None:
            return False
        for j in range(i + 1, len(members)):
            y = _get_step(flow, members[j])
            if y is None:
                return False
            if not mutex_when_proved(flow, x, y, predicates_mutex_fn):
                return False
    return True


# ─── §7.2 mhb (must-happen-before) ────────────────────────────────────────


def compute_mhb_ancestors(
    flow: WorkflowDefinition,
    predicates_mutex_fn: Callable[[WhenPredicate, WhenPredicate], bool] | None = None,
    back_edges: set[tuple[str, str]] | None = None,
) -> dict[str, set[str]]:
    """Compute mhb_ancestors[Y] = set of steps X such that mhb(X, Y).

    Per §7.2 clauses (a)-(e):
      (a) Direct edges (after, regular inputs) — EXCLUDING back-edges.
      (b) Universal-prefix after.any_of: mhb(Z, Y) iff mhb(Z, Mi) for every Mi.
      (c) Transitivity.
      (d) Universal-prefix input any_of bindings — same as (b).
      (e) Mutex-complete fan-in: mhb(Mi, Y) iff every pair (Mj, Mk) is mutex.

    Computed iteratively until fixed point.

    back_edges is a set of (consumer_step_name, producer_step_name) tuples
    that should be excluded from the mhb relation. The default empty set
    means production code paths get the same behavior as the fuzzer
    (which encodes back-edges per binding); tests pass non-empty sets.
    """
    if back_edges is None:
        back_edges = set()

    ancestors: dict[str, set[str]] = {s.name: set() for s in _iter_steps(flow)}

    # Seed with direct (a) edges
    for step in _iter_steps(flow):
        for dep_name in step.after:
            if (step.name, dep_name) in back_edges:
                continue
            ancestors[step.name].add(dep_name)
        for ib in step.inputs:
            if ib.any_of_sources is not None:
                continue  # handled in (d) below
            if ib.source_step == "$job" or not ib.source_step:
                continue  # job-level input, not a step dep
            if (step.name, ib.source_step) in back_edges:
                continue
            ancestors[step.name].add(ib.source_step)

    changed = True
    while changed:
        changed = False
        for step in _iter_steps(flow):
            new_ancestors: set[str] = set(ancestors[step.name])

            # (c) Transitivity over current ancestors
            for anc in list(new_ancestors):
                if anc in ancestors:
                    new_ancestors |= ancestors[anc]

            # (b) Universal-prefix after.any_of
            for group in step.after_any_of:
                if not group:
                    continue
                # If any member is a back-edge from this step, skip the whole
                # group (TODO: per-member exclusion would need per-binding
                # back-edge metadata; current side-table is per-(consumer,
                # producer) pair).
                if any((step.name, m) in back_edges for m in group):
                    continue
                first = group[0]
                if first not in ancestors:
                    continue
                common = set(ancestors[first]) | {first}
                for m in group[1:]:
                    if m not in ancestors:
                        common = set()
                        break
                    common &= (set(ancestors[m]) | {m})
                new_ancestors |= common - {step.name}

            # (d) Universal-prefix input any_of bindings
            for ib in step.inputs:
                if ib.any_of_sources is None:
                    continue
                if not ib.any_of_sources:
                    continue
                src_steps = [s for s, _ in ib.any_of_sources]
                if any((step.name, s) in back_edges for s in src_steps):
                    continue
                first = src_steps[0]
                if first not in ancestors:
                    continue
                common = set(ancestors[first]) | {first}
                for m in src_steps[1:]:
                    if m not in ancestors:
                        common = set()
                        break
                    common &= (set(ancestors[m]) | {m})
                new_ancestors |= common - {step.name}

            # (e) Mutex-complete fan-in for after.any_of
            for group in step.after_any_of:
                if any((step.name, m) in back_edges for m in group):
                    continue
                if _members_pairwise_mutex(flow, tuple(group), predicates_mutex_fn):
                    for m in group:
                        new_ancestors.add(m)

            # (e) Mutex-complete fan-in for input any_of bindings
            for ib in step.inputs:
                if ib.any_of_sources is None:
                    continue
                members = tuple(s for s, _ in ib.any_of_sources)
                if any((step.name, m) in back_edges for m in members):
                    continue
                if _members_pairwise_mutex(flow, members, predicates_mutex_fn):
                    for m in members:
                        new_ancestors.add(m)

            new_ancestors.discard(step.name)
            if new_ancestors != ancestors[step.name]:
                ancestors[step.name] = new_ancestors
                changed = True

    return ancestors


# ─── §7.2.1 mhb_strict (strict-execution sub-relation) ────────────────────


def compute_mhb_strict_ancestors(
    flow: WorkflowDefinition,
    back_edges: set[tuple[str, str]] | None = None,
) -> dict[str, set[str]]:
    """Compute mhb_strict_ancestors[Y] per §7.2.1 positive enumeration.

    Clause (a'): direct required forward after edges, OR required
                 non-optional non-any_of non-back-edge input bindings.
    Clause (b'): universal-prefix after.any_of, recursive over mhb_strict.
    Clause (c'): transitivity over mhb_strict.
    Clause (d'): universal-prefix input any_of, recursive over mhb_strict.
    Clause (e'): NOT included (mutex-complete fan-in is intentionally
                 excluded — only positively-known executed ancestors).

    CRITICAL (council pass 5, gpt-5.4): this MUST be computed by positive
    construction. An implementation that computes mhb and then filters is
    WRONG because it cannot distinguish (a)/(b)/(c)/(d) contributions from
    (e) contributions in the resulting set.
    """
    if back_edges is None:
        back_edges = set()

    strict: dict[str, set[str]] = {s.name: set() for s in _iter_steps(flow)}

    # Seed with (a') direct edges — EXCLUDING optional, back-edge, any_of
    for step in _iter_steps(flow):
        for dep_name in step.after:
            if (step.name, dep_name) in back_edges:
                continue
            strict[step.name].add(dep_name)
        for ib in step.inputs:
            if ib.any_of_sources is not None:
                continue
            if ib.optional:
                continue
            if ib.source_step == "$job" or not ib.source_step:
                continue
            if (step.name, ib.source_step) in back_edges:
                continue
            strict[step.name].add(ib.source_step)

    changed = True
    while changed:
        changed = False
        for step in _iter_steps(flow):
            new_strict: set[str] = set(strict[step.name])

            # (c') Transitivity over current strict ancestors
            for anc in list(new_strict):
                if anc in strict:
                    new_strict |= strict[anc]

            # (b') Universal-prefix after.any_of, recursive over mhb_strict
            for group in step.after_any_of:
                if not group:
                    continue
                if any((step.name, m) in back_edges for m in group):
                    continue
                first = group[0]
                if first not in strict:
                    continue
                common = set(strict[first]) | {first}
                for m in group[1:]:
                    if m not in strict:
                        common = set()
                        break
                    common &= (set(strict[m]) | {m})
                new_strict |= common - {step.name}

            # (d') Universal-prefix input any_of bindings, recursive over mhb_strict
            for ib in step.inputs:
                if ib.any_of_sources is None:
                    continue
                if not ib.any_of_sources:
                    continue
                src_steps = [s for s, _ in ib.any_of_sources]
                if any((step.name, s) in back_edges for s in src_steps):
                    continue
                first = src_steps[0]
                if first not in strict:
                    continue
                common = set(strict[first]) | {first}
                for m in src_steps[1:]:
                    if m not in strict:
                        common = set()
                        break
                    common &= (set(strict[m]) | {m})
                new_strict |= common - {step.name}

            # (e') NOT included — mutex-complete fan-in is intentionally
            # excluded from mhb_strict per §7.2.1. See council pass 5.

            new_strict.discard(step.name)
            if new_strict != strict[step.name]:
                strict[step.name] = new_strict
                changed = True

    return strict


# ─── §7.3 inherited_mutex ─────────────────────────────────────────────────


def inherited_mutex(
    flow: WorkflowDefinition,
    x_step: StepDefinition,
    y_step: StepDefinition,
    mhb_strict_ancestors: dict[str, set[str]],
    predicates_mutex_fn: Callable[[WhenPredicate, WhenPredicate], bool] | None = None,
) -> bool:
    """Per §7.3: do X and Y inherit a mutex relation from a strict ancestor pair?

    Returns True iff there exists some pair (x_anc, y_anc) drawn from
    (mhb_strict_ancestors[x] ∪ {x}) × (mhb_strict_ancestors[y] ∪ {y})
    such that mutex_when_proved(x_anc, y_anc) holds.

    Each step is its own mhb_strict-ancestor — we union {x.name} and
    {y.name} into the candidate sets.
    """
    x_set = set(mhb_strict_ancestors.get(x_step.name, set())) | {x_step.name}
    y_set = set(mhb_strict_ancestors.get(y_step.name, set())) | {y_step.name}
    for x_anc_name in x_set:
        x_anc = _get_step(flow, x_anc_name)
        if x_anc is None:
            continue
        for y_anc_name in y_set:
            y_anc = _get_step(flow, y_anc_name)
            if y_anc is None:
                continue
            if mutex_when_proved(flow, x_anc, y_anc, predicates_mutex_fn):
                return True
    return False
