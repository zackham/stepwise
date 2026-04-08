"""Top-level validate(flow) integration pass.

Orchestrates step 1 (predicate-form parser) and step 2 (mhb / mhb_strict /
mutex_when_proved / inherited_mutex) plus the §7.4 auxiliary rules into a
single accept/reject pass over a WorkflowDefinition. Pure analysis: no
mutation, no engine/runner imports, returns a ValidationResult.

Step 3 ships this function and the test suite covers it directly. Step 5
will wire it into the YAML load pipeline and run it against meeting-ingest.
"""

from __future__ import annotations

from stepwise.models import ExecutorRef, StepDefinition, WorkflowDefinition
from stepwise.validator.back_edges import compute_back_edges, find_cycle_nodes
from stepwise.validator.errors import (
    PairVerdict,
    ValidationError,
    ValidationResult,
)
from stepwise.validator.mhb import (
    compute_mhb_ancestors,
    compute_mhb_strict_ancestors,
    inherited_mutex,
    mutex_when_proved,
)
from stepwise.validator.mutex import predicates_mutex


# Production convention: agent executors have type "agent" (verified via
# grep across src/stepwise/{engine,yaml_loader,cli}.py).
_AGENT_EXECUTOR_TYPES = {"agent"}


def _is_agent_executor(executor: ExecutorRef) -> bool:
    """True if this is an agent-type executor (has a session to fork)."""
    return executor.type in _AGENT_EXECUTOR_TYPES


# ─── pair_safe per §7.3 ───────────────────────────────────────────────────


def pair_safe(
    flow: WorkflowDefinition,
    x_name: str,
    y_name: str,
    mhb: dict[str, set[str]],
    strict: dict[str, set[str]],
) -> PairVerdict:
    """Per §7.3 — is the pair (X, Y) provably non-concurrent?

    Clauses:
      1. mhb(X, Y) — X reaches a terminal state before Y starts.
      2. mhb(Y, X) — symmetric.
      3. mutex_when_proved(X, Y) — predicate-form when: clauses are mutex
         on a shared upstream input.
      4. inherited_mutex(X, Y) — some pair of strict ancestors is mutex.
    """
    x_step = flow.steps[x_name]
    y_step = flow.steps[y_name]

    if x_name in mhb.get(y_name, set()):
        return PairVerdict(x_name, y_name, True, "mhb(X, Y)")
    if y_name in mhb.get(x_name, set()):
        return PairVerdict(x_name, y_name, True, "mhb(Y, X)")
    if mutex_when_proved(flow, x_step, y_step, predicates_mutex):
        return PairVerdict(x_name, y_name, True, "mutex_when_proved")
    if inherited_mutex(flow, x_step, y_step, strict, predicates_mutex):
        return PairVerdict(x_name, y_name, True, "inherited_mutex")

    return PairVerdict(
        x_name,
        y_name,
        False,
        "no proof: no mhb path either direction, no direct mutex, no inherited mutex",
    )


# ─── Per-rule check helpers ───────────────────────────────────────────────


def _emit_back_edge_errors(
    flow: WorkflowDefinition,
    cycle_nodes: set[str],
    result: ValidationResult,
) -> None:
    """Emit back_edge_unsupported (simple 2-node cycle) or cyclic_dependency."""
    if len(cycle_nodes) == 2:
        # Try to identify the simple consumer→producer back-edge.
        nodes = sorted(cycle_nodes)
        a, b = nodes[0], nodes[1]
        # Either a depends on b and b depends on a — both are back-edges
        # but we surface the first one we see.
        consumer, producer = a, b
        result.errors.append(
            ValidationError(
                rule_id="back_edge_unsupported",
                message=(
                    f"step {consumer!r}: loop-back binding to {producer!r} is not "
                    f"yet supported — loop-back binding runtime not yet implemented; "
                    f"use a linear after chain or restructure the flow"
                ),
                step_names=(consumer, producer),
                fix_suggestion=(
                    f"Remove the dependency from {consumer!r} to {producer!r}, "
                    f"or restructure the flow as a linear chain."
                ),
            )
        )
    else:
        result.errors.append(
            ValidationError(
                rule_id="cyclic_dependency",
                message=(
                    f"cyclic dependency detected involving steps: "
                    f"{sorted(cycle_nodes)}"
                ),
                step_names=tuple(sorted(cycle_nodes)),
                fix_suggestion=(
                    "Break the cycle by removing one of the dependencies "
                    "(after entry, after_any_of group member, or input binding)."
                ),
            )
        )


def _check_session_pairs(
    flow: WorkflowDefinition,
    mhb: dict[str, set[str]],
    strict: dict[str, set[str]],
    result: ValidationResult,
) -> None:
    """For each session, enumerate writers and check pair_safe (§7.3, R2)."""
    sessions: dict[str, list[str]] = {}
    for name, step in flow.steps.items():
        if step.session:
            sessions.setdefault(step.session, []).append(name)

    for session_name, writer_names in sorted(sessions.items()):
        writer_names = sorted(writer_names)
        for i in range(len(writer_names)):
            for j in range(i + 1, len(writer_names)):
                x_name, y_name = writer_names[i], writer_names[j]
                pv = pair_safe(flow, x_name, y_name, mhb, strict)
                result.pair_verdicts.append(pv)
                if not pv.safe:
                    result.errors.append(
                        ValidationError(
                            rule_id="pair_unsafe",
                            message=(
                                f"Steps {x_name!r} and {y_name!r} both write to "
                                f"session {session_name!r} but the validator cannot "
                                f"prove they will not run concurrently."
                            ),
                            step_names=(x_name, y_name),
                            session=session_name,
                            fix_suggestion=(
                                f"Add 'after: [{x_name}]' to step {y_name!r}, or use "
                                f"'fork_from: <step>' to give one of them an "
                                f"independent forked session."
                            ),
                        )
                    )
                    result.accepted = False


def _check_multi_root_mutex(
    flow: WorkflowDefinition,
    result: ValidationResult,
) -> None:
    """If a session has multiple fork_from chain roots, they must be pairwise mutex (R3)."""
    roots_by_session: dict[str, list[str]] = {}
    for name, step in flow.steps.items():
        if step.session and step.fork_from:
            roots_by_session.setdefault(step.session, []).append(name)

    for session_name, root_names in sorted(roots_by_session.items()):
        if len(root_names) < 2:
            continue
        root_names = sorted(root_names)
        for i in range(len(root_names)):
            for j in range(i + 1, len(root_names)):
                x_step = flow.steps[root_names[i]]
                y_step = flow.steps[root_names[j]]
                if not mutex_when_proved(flow, x_step, y_step, predicates_mutex):
                    result.errors.append(
                        ValidationError(
                            rule_id="multi_root_not_mutex",
                            message=(
                                f"session {session_name!r} has multiple steps "
                                f"declaring fork_from: ({root_names[i]!r} and "
                                f"{root_names[j]!r}) but they are not pairwise "
                                f"mutually exclusive — they could both initialize "
                                f"the session and race."
                            ),
                            step_names=(root_names[i], root_names[j]),
                            session=session_name,
                            fix_suggestion=(
                                "Make their `when:` clauses mutex via predicate "
                                "form, or merge them into a single chain root."
                            ),
                        )
                    )
                    result.accepted = False


def _check_fork_targets(
    flow: WorkflowDefinition,
    mhb: dict[str, set[str]],
    result: ValidationResult,
) -> None:
    """Per §7.4 fork target validation rules (R4 + R5)."""
    for name, step in flow.steps.items():
        if not step.fork_from:
            continue

        # R5b: cannot fork on for_each / sub_flow steps
        if step.for_each is not None or step.sub_flow is not None:
            result.errors.append(
                ValidationError(
                    rule_id="fork_from_on_for_each",
                    message=(
                        f"step {name!r} declares fork_from: but is itself a "
                        f"for_each or sub_flow step. fork_from is undefined for "
                        f"these step types."
                    ),
                    step_names=(name,),
                    fix_suggestion="Remove fork_from from this step.",
                )
            )
            result.accepted = False
            continue

        # R4a: target exists
        target_name = step.fork_from
        if target_name not in flow.steps:
            result.errors.append(
                ValidationError(
                    rule_id="fork_target_missing",
                    message=(
                        f"step {name!r} declares fork_from: {target_name!r} but "
                        f"no such step exists in the flow."
                    ),
                    step_names=(name,),
                    fix_suggestion=(
                        f"Check the spelling of {target_name!r} or add the step."
                    ),
                )
            )
            result.accepted = False
            continue
        target = flow.steps[target_name]

        # R4b: target has agent executor
        if not _is_agent_executor(target.executor):
            result.errors.append(
                ValidationError(
                    rule_id="fork_target_not_agent",
                    message=(
                        f"step {name!r} declares fork_from: {target_name!r} but "
                        f"the target's executor type is {target.executor.type!r}, "
                        f"not an agent executor. Only agent executors have "
                        f"sessions to fork."
                    ),
                    step_names=(name, target_name),
                    fix_suggestion=(
                        "Change the target's executor to an agent type, or "
                        "remove fork_from."
                    ),
                )
            )
            result.accepted = False
            continue

        # R4c: target declares a session
        if not target.session:
            result.errors.append(
                ValidationError(
                    rule_id="fork_target_no_session",
                    message=(
                        f"step {name!r} declares fork_from: {target_name!r} but "
                        f"the target has no session: declared. You cannot fork "
                        f"from an ephemeral one-shot agent step — there's no "
                        f"session to clone."
                    ),
                    step_names=(name, target_name),
                    fix_suggestion=(
                        f"Add 'session: <name>' to step {target_name!r}."
                    ),
                )
            )
            result.accepted = False
            continue

        # R4d: target is in mhb predecessors of the forking step
        if target_name not in mhb.get(name, set()):
            result.errors.append(
                ValidationError(
                    rule_id="fork_target_not_in_mhb",
                    message=(
                        f"step {name!r} declares fork_from: {target_name!r} but "
                        f"the target is not provably ordered before the forking "
                        f"step (no mhb path). fork_from requires a "
                        f"must-happen-before relationship."
                    ),
                    step_names=(name, target_name),
                    fix_suggestion=(
                        f"Add 'after: [{target_name}]' to step {name!r} to make "
                        f"the ordering explicit."
                    ),
                )
            )
            result.accepted = False
            continue


def _check_subflow_fork_from(
    flow: WorkflowDefinition,
    result: ValidationResult,
) -> None:
    """R5a: a step inside a sub_flow body cannot fork_from a parent-flow step.

    Walks each top-level step's sub_flow (if any). For each inner step that
    declares fork_from, if the target name is NOT a step within the same
    sub_flow body, emit fork_from_in_subflow.
    """
    for parent_name, parent_step in flow.steps.items():
        sub = parent_step.sub_flow
        if sub is None:
            continue
        inner_step_names = set(sub.steps.keys())
        for inner_name, inner_step in sub.steps.items():
            if not inner_step.fork_from:
                continue
            if inner_step.fork_from not in inner_step_names:
                result.errors.append(
                    ValidationError(
                        rule_id="fork_from_in_subflow",
                        message=(
                            f"step {inner_name!r} (inside sub_flow of "
                            f"{parent_name!r}) declares "
                            f"fork_from: {inner_step.fork_from!r}, but the "
                            f"target is not a sibling step within the same "
                            f"sub_flow body. fork_from cannot reference parent-flow "
                            f"steps from inside a sub_flow."
                        ),
                        step_names=(inner_name, inner_step.fork_from),
                        fix_suggestion=(
                            "Move the fork_from declaration to the parent flow, "
                            "or restructure so the fork target lives inside the "
                            "same sub_flow body."
                        ),
                    )
                )
                result.accepted = False


def _check_retries_and_cache(
    flow: WorkflowDefinition,
    result: ValidationResult,
) -> None:
    """§7.4: retries and cache prohibited on session-writers and fork-source steps (R7)."""
    fork_sources: set[str] = {
        s.fork_from for s in flow.steps.values() if s.fork_from
    }

    for name, step in flow.steps.items():
        is_session_writer = bool(step.session)
        is_fork_source = name in fork_sources
        if not (is_session_writer or is_fork_source):
            continue

        # Retry: max_continuous_attempts > 1
        if step.max_continuous_attempts is not None and step.max_continuous_attempts > 1:
            rule = "retry_on_session_writer" if is_session_writer else "retry_on_fork_source"
            label = "session-writing" if is_session_writer else "fork-source"
            result.errors.append(
                ValidationError(
                    rule_id=rule,
                    message=(
                        f"step {name!r} is a {label} step AND has "
                        f"max_continuous_attempts: {step.max_continuous_attempts}, "
                        f"but {label} steps cannot have retry policies in v1.0. "
                        f"See §7.4 for the rationale."
                    ),
                    step_names=(name,),
                    session=step.session,
                    fix_suggestion=(
                        "To use retries, remove the 'session:' declaration "
                        "(which makes it an ephemeral one-shot step that can "
                        "retry freely). To keep the session, remove "
                        "'max_continuous_attempts'."
                    ),
                )
            )
            result.accepted = False

        # Cache: cache.enabled True
        if step.cache is not None and getattr(step.cache, "enabled", False):
            rule = "cache_on_session_writer" if is_session_writer else "cache_on_fork_source"
            label = "session-writing" if is_session_writer else "fork-source"
            result.errors.append(
                ValidationError(
                    rule_id=rule,
                    message=(
                        f"step {name!r} is a {label} step AND has cache: "
                        f"enabled, but {label} steps cannot use cache in v1.0 "
                        f"(a cached step may lack a corresponding session "
                        f"artifact for downstream forks to clone from)."
                    ),
                    step_names=(name,),
                    session=step.session,
                    fix_suggestion="Remove the cache policy or restructure the fork.",
                )
            )
            result.accepted = False


# ─── Top-level orchestrator ───────────────────────────────────────────────


def validate(flow: WorkflowDefinition) -> ValidationResult:
    """Top-level coordination validator.

    Orchestrates:
      0. Cycle / back-edge detection (rejects all back-edges in step 3).
      1. Per-session pair_safe check (R2).
      2. Conditional fork rejoin / multi-root mutex check (R3).
      3. Fork target validation (R4 + R5).
      4. Retries / cache prohibition on session writers + fork sources (R7).

    Returns a ValidationResult. Pure analysis: does NOT mutate flow.
    """
    result = ValidationResult(accepted=True)

    # 0. Cycle detection — reject all back-edges in step 3.
    cycle_nodes = find_cycle_nodes(flow)
    if cycle_nodes:
        _emit_back_edge_errors(flow, cycle_nodes, result)
        result.accepted = False
        return result  # Don't compute mhb on a cyclic graph.

    back_edges = compute_back_edges(flow)
    mhb = compute_mhb_ancestors(flow, back_edges=back_edges)
    strict = compute_mhb_strict_ancestors(flow, back_edges=back_edges)

    _check_session_pairs(flow, mhb, strict, result)
    _check_multi_root_mutex(flow, result)
    _check_fork_targets(flow, mhb, result)
    _check_subflow_fork_from(flow, result)
    _check_retries_and_cache(flow, result)

    return result
