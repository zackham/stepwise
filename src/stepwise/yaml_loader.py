"""Load Stepwise workflows from YAML files.

Parses YAML workflow definitions into WorkflowDefinition objects.
See docs/yaml-format.md for the format specification.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import yaml

from stepwise.models import (
    CacheConfig,
    ConfigVar,
    DecoratorRef,
    ExecutorRef,
    ExitRule,
    FlowMetadata,
    FlowRequirement,
    ForEachSpec,
    InputBinding,
    KitDefinition,
    OutputFieldSpec,
    StepDefinition,
    StepLimits,
    VALID_FIELD_TYPES,
    VALID_VISIBILITY,
    WhenPredicate,
    WorkflowDefinition,
    parse_duration,
)


_SESSION_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _parse_after(after_data: Any, step_name: str) -> tuple[list[str], list[list[str]]]:
    """Parse the `after:` field into (regular_deps, any_of_groups).

    Accepts:
      - None or empty list → ([], [])
      - String "X" → (["X"], [])  (singleton convenience)
      - List of strings ["X", "Y"] → (["X", "Y"], [])
      - List with mixed elements ["X", {"any_of": ["A", "B"]}] →
            (["X"], [["A", "B"]])
      - List of any_of groups [{"any_of": ["A","B"]}, {"any_of": ["C","D"]}] →
            ([], [["A", "B"], ["C", "D"]])

    Rejects (ValueError with step name):
      - Pure dict form: `after: {any_of: [...]}` (must use list form)
      - Empty any_of: `after: [{any_of: []}]`
      - Single-member any_of: `after: [{any_of: [X]}]`
      - Self-reference: `after: [{any_of: [step_name, ...]}]`
      - Non-string members in any_of
      - Unknown dict keys (only `any_of` is supported)
    """
    if after_data is None or after_data == []:
        return [], []
    if isinstance(after_data, str):
        return [after_data], []
    if not isinstance(after_data, list):
        raise ValueError(
            f"step {step_name!r}: 'after' must be a string, list, or list with "
            f"any_of dicts, got {type(after_data).__name__}"
        )

    regular: list[str] = []
    any_of_groups: list[list[str]] = []
    for item in after_data:
        if isinstance(item, str):
            regular.append(item)
        elif isinstance(item, dict):
            if set(item.keys()) != {"any_of"}:
                extra = sorted(set(item.keys()) - {"any_of"})
                raise ValueError(
                    f"step {step_name!r}: after entry has unsupported keys "
                    f"{extra}; only 'any_of' is allowed"
                )
            members = item["any_of"]
            if not isinstance(members, list):
                raise ValueError(
                    f"step {step_name!r}: after.any_of must be a list of step "
                    f"names, got {type(members).__name__}"
                )
            if len(members) == 0:
                raise ValueError(
                    f"step {step_name!r}: after.any_of must be non-empty"
                )
            if len(members) == 1:
                raise ValueError(
                    f"step {step_name!r}: after.any_of with a single member "
                    f"is not allowed — use plain 'after: [{members[0]!r}]' instead"
                )
            for m in members:
                if not isinstance(m, str):
                    raise ValueError(
                        f"step {step_name!r}: after.any_of members must be "
                        f"strings (step names), got {type(m).__name__}"
                    )
                if m == step_name:
                    raise ValueError(
                        f"step {step_name!r}: cannot reference self in after.any_of"
                    )
            any_of_groups.append(list(members))
        else:
            raise ValueError(
                f"step {step_name!r}: after entry must be a string or "
                f"{{any_of: [...]}} dict, got {type(item).__name__}"
            )
    return regular, any_of_groups


def _parse_after_resolved(data: Any, step_name: str) -> list[str]:
    """Parse the ``after_resolved:`` field — a list of step names whose
    terminal state (COMPLETED or SKIPPED) unblocks the dependent step.

    Accepts:
      - None → []
      - str → [str]
      - list[str] → list[str]
    """
    if data is None or data == []:
        return []
    if isinstance(data, str):
        return [data]
    if isinstance(data, list):
        result: list[str] = []
        for item in data:
            if isinstance(item, str):
                result.append(item)
            else:
                raise ValueError(
                    f"step {step_name!r}: after_resolved entries must be strings, "
                    f"got {type(item).__name__}"
                )
        return result
    raise ValueError(
        f"step {step_name!r}: 'after_resolved' must be a string or list of strings, "
        f"got {type(data).__name__}"
    )


def _parse_when(when_data: Any, step_name: str) -> str | WhenPredicate | None:
    """Dispatch a `when:` field into either legacy string form or predicate form.

    - None → None
    - str → return as-is (legacy expression form, opaque to mutex algebra)
    - dict → parse into WhenPredicate (predicate form, §5)
    - other → ValueError with step name

    Step 7: ``is_present:`` is now accepted at parse time. The structural
    "is_present: only on loop-back bindings" check is performed in a
    second pass after the entire workflow has been parsed and back-edge
    bindings have been marked — see _validate_predicate_refs() below.
    """
    if when_data is None:
        return None
    if isinstance(when_data, str):
        return when_data
    if isinstance(when_data, dict):
        try:
            return WhenPredicate.from_dict(when_data)
        except ValueError as exc:
            raise ValueError(f"step {step_name!r}: {exc}") from exc
    raise ValueError(
        f"step {step_name!r}: when: must be a string or mapping, "
        f"got {type(when_data).__name__}"
    )


# ─── Step 7 (§11): back-edge marking and predicate-reference validation ──


def _mark_back_edges(steps: dict) -> list[str]:
    """Mark loop-back input bindings on each step's InputBindings (§11.2).

    Algorithm per locked decisions 3, 4, 5, 12:
      1. Compute the set of (consumer, producer) pairs that form back-edges
         using models.collect_loop_back_edges() (the canonical helper used
         by both the parser and the cycle detector).
      2. For each step's regular InputBinding b: if (step.name, b.source_step)
         is in the back-edge set, set b.is_back_edge = True and assign
         b.closing_loop_id (= the loop initiator step name = the unique
         loop-exit-rule target).
      3. For each step's any_of InputBinding: only mark the binding as a
         whole if EVERY source is a back-edge AND every source's closing
         loop is the same (§11.4 same-loop-frame check). If mixed, leave
         is_back_edge = False (parse-time `is_present:` use will be
         rejected by _validate_predicate_refs).
      4. For each marked binding, derive `closing_loop_id` by walking the
         steps that have a `loop` exit rule whose target is on the path
         from the consumer back to the producer. The trivial case
         (consumer == loop_target AND producer == loop_owner) covers
         every yellow flow. Self-loops use consumer == producer == owner.

    Returns a list of error strings (e.g., "ambiguous_loop_closure").
    """
    from stepwise.models import collect_loop_back_edges

    errors: list[str] = []

    back_edges = collect_loop_back_edges(steps)
    if not back_edges:
        return errors

    # Build a map from each back-edge (consumer, producer) → closing_loop_id.
    # The closing loop is the unique loop owner whose `loop` exit-rule
    # target equals the consumer (or, for transitive cases, reaches the
    # consumer via the forward graph).
    #
    # Loop owners and their targets:
    loop_owner_targets: list[tuple[str, str]] = []  # (owner_step, target_step)
    for owner_name, owner_def in steps.items():
        for rule in owner_def.exit_rules:
            action = rule.config.get("action")
            if action in ("loop", "escalate"):
                target = rule.config.get("target")
                if target and target in steps:
                    loop_owner_targets.append((owner_name, target))

    def closing_loop_for_edge(consumer: str, producer: str) -> tuple[str | None, str | None]:
        """Return (closing_loop_id, error_msg) for this back-edge.

        closing_loop_id is the loop target step name (= frame_id) that
        owns the loop closing this back-edge. Per §11.5, this is the
        innermost enclosing loop frame whose `loop` exit rule sits on the
        cycle that includes the consumer and producer.

        We use models.collect_loop_back_edges' notion of "loop owner is
        on the cycle": for each loop pair (owner, target), if owner is
        the producer or owner is forward-reachable from the producer
        (modulo the candidate edge), the loop closes the cycle. The
        closing_loop_id is the target of that loop pair.
        """
        from stepwise.models import collect_loop_back_edges  # for the test set
        all_back_edges = collect_loop_back_edges(steps)
        if (consumer, producer) not in all_back_edges and consumer != producer:
            return None, (
                f"step {consumer!r}: input bound to {producer!r} appears to be "
                f"a loop-back binding, but no enclosing loop exit rule was "
                f"found to close it (rule_id: loop_back_binding_ambiguous_closure)"
            )
        # Build forward reachability over the steps so we can match the
        # collect_loop_back_edges criterion: owner is on the cycle iff
        # owner == producer OR forward-reachable from producer.
        fwd: dict[str, set[str]] = {n: set() for n in steps}
        for cn, cd in steps.items():
            for b in cd.inputs:
                if b.any_of_sources:
                    for s, _f in b.any_of_sources:
                        if s in steps and s != cn:
                            fwd[s].add(cn)
                elif b.source_step and b.source_step != "$job" and b.source_step in steps:
                    if b.source_step != cn:
                        fwd[b.source_step].add(cn)
            for s in cd.after:
                if s in steps and s != cn:
                    fwd[s].add(cn)
            for s in cd.after_resolved:
                if s in steps and s != cn:
                    fwd[s].add(cn)

        def reachable(start: str, end: str) -> bool:
            if start == end:
                return True
            seen = {start}
            stack = [start]
            while stack:
                cur = stack.pop()
                for nxt in fwd.get(cur, ()):
                    if (cur, nxt) == (producer, consumer):
                        continue  # exclude the candidate edge
                    if nxt == end:
                        return True
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append(nxt)
            return False

        # Step 1a (Pattern A trivial): canonical loop-carry shape where
        # consumer sits at the top of the loop body (loop target) and
        # producer at the bottom (loop owner). This is the classic
        # `analyze ← refine` / `prev_note ← critique.note` shape.
        for owner, tgt in loop_owner_targets:
            if owner == producer and tgt == consumer:
                return tgt, None

        # Step 1b (Pattern B trivial): "tail reads head" shape where
        # consumer is the loop owner (end of body, where the loop
        # decision is made) and producer is the loop target (first
        # step of the body). Happens when an any_of / optional binding
        # on the owner declares an iter-1 fallback to a non-loop
        # producer and a back-edge to the loop target. This closes the
        # same structural cycle in the opposite direction.
        # Example: hub ← any_of [init.notes, work.result], with
        # hub.loop(target=work).
        for owner, tgt in loop_owner_targets:
            if owner == consumer and tgt == producer:
                return tgt, None

        # Step 2 (fallback): transitive closure search. Used for the
        # multi-step loop body case. Any loop_pair whose body both
        # contains the producer and reaches the consumer qualifies.
        candidates: list[str] = []
        for owner, tgt in loop_owner_targets:
            reach_body = (tgt == producer) or reachable(tgt, producer)
            reach_tail = (consumer == owner) or reachable(consumer, owner)
            if reach_body and reach_tail:
                if tgt not in candidates:
                    candidates.append(tgt)

        if not candidates:
            return None, (
                f"step {consumer!r}: input bound to {producer!r} appears to be "
                f"a loop-back binding, but no enclosing loop exit rule was "
                f"found to close it (rule_id: loop_back_binding_ambiguous_closure)"
            )
        if len(candidates) == 1:
            return candidates[0], None
        # Multiple candidates: pick the first deterministically. In
        # practice this is unreachable for the canary patterns.
        return candidates[0], None

    for step_name, step_def in steps.items():
        for b in step_def.inputs:
            if b.any_of_sources:
                # §11.4 same-loop-frame check: every source must be back-edge
                # AND every source must share the same closing loop.
                source_loops: list[str | None] = []
                source_back_edges: list[bool] = []
                for src_step, _ in b.any_of_sources:
                    is_be = (step_name, src_step) in back_edges
                    source_back_edges.append(is_be)
                    if is_be:
                        cl, err = closing_loop_for_edge(step_name, src_step)
                        if err:
                            errors.append(err)
                            source_loops.append(None)
                        else:
                            source_loops.append(cl)
                    else:
                        source_loops.append(None)
                # Whole-binding back-edge marking: every source is back-edge
                # AND every source has the same closing loop.
                if all(source_back_edges) and len(set(source_loops)) == 1 and source_loops[0]:
                    b.is_back_edge = True
                    b.closing_loop_id = source_loops[0]
                # Else: leave b.is_back_edge False; mixed-scope any_of with
                # is_present: gets caught in _validate_predicate_refs.
            else:
                if not b.source_step or b.source_step == "$job":
                    continue
                if (step_name, b.source_step) in back_edges:
                    cl, err = closing_loop_for_edge(step_name, b.source_step)
                    if err:
                        errors.append(err)
                        continue
                    b.is_back_edge = True
                    b.closing_loop_id = cl
    return errors


def _validate_predicate_refs(steps: dict) -> list[str]:
    """Per §11.3-11.4 + Answer 2: validate when:/is_present: predicate references.

    Rules:
      - is_present: only legal on loop-back bindings.
        Rule_id: is_present_not_loop_back.
      - is_present: on an any_of binding requires the binding-as-a-whole
        to be is_back_edge=True (every source is back-edge from the same
        closing loop). Rule_id: is_present_mixed_scope_any_of.
      - is_null:/eq:/in: are unchanged (legal on any binding).
      - Answer 2 (unguarded back-edge check): if a step has an
        is_back_edge binding with NO `optional: true`, NO any_of fallback,
        AND NO is_present: guard, the step would have an undefined value
        on iter-1. Reject at parse time so errors surface loud.

    Returns a list of error strings.
    """
    from stepwise.models import WhenPredicate

    errors: list[str] = []
    for step_name, step_def in steps.items():
        # Build name → binding lookup
        binding_by_name: dict[str, "InputBinding"] = {
            b.local_name: b for b in step_def.inputs
        }

        when = step_def.when
        is_present_inputs: set[str] = set()
        if isinstance(when, WhenPredicate):
            if when.op == "is_present":
                is_present_inputs.add(when.input)
                target_b = binding_by_name.get(when.input)
                if target_b is None:
                    errors.append(
                        f"step {step_name!r}: when.is_present references "
                        f"unknown input {when.input!r}"
                    )
                elif not target_b.is_back_edge:
                    if target_b.any_of_sources:
                        errors.append(
                            f"step {step_name!r}: when.is_present: on any_of "
                            f"binding {when.input!r} requires every source to "
                            f"be a loop-back binding closed by the same loop "
                            f"(rule_id: is_present_mixed_scope_any_of)"
                        )
                    else:
                        errors.append(
                            f"step {step_name!r}: when.is_present: is only "
                            f"legal on loop-back bindings (input {when.input!r} "
                            f"is not a loop-back binding — it has no producer "
                            f"closed by a loop exit rule) "
                            f"(rule_id: is_present_not_loop_back)"
                        )

        # Answer 2 (Q2): unguarded back-edge check.
        for b in step_def.inputs:
            if not b.is_back_edge:
                continue
            # OK if optional, an any_of with at least one source, or
            # guarded by an is_present: predicate on this binding.
            if b.optional:
                continue
            if b.any_of_sources:
                # any_of bindings inherently provide a fallback path
                # (the any_of resolver picks the first available source).
                continue
            if b.local_name in is_present_inputs:
                continue
            errors.append(
                f"step {step_name!r}: input binding {b.local_name!r} is a "
                f"loop-back binding but has no fallback (add optional: true, "
                f"an any_of: source list, or a when: {{ is_present: }} guard "
                f"on this step)"
            )
    return errors


def _apply_step7_back_edge_pass(
    steps: dict, errors: list[str],
) -> None:
    """Run the step-7 two-pass back-edge marking + predicate validation.

    Mutates each binding's ``is_back_edge`` and ``closing_loop_id`` fields,
    and appends any structural errors to the caller's error list.
    """
    errors.extend(_mark_back_edges(steps))
    errors.extend(_validate_predicate_refs(steps))

# Safe builtins for exit rule expression evaluation


def _regex_extract(pattern: str, text: str, default: str | None = None) -> str | None:
    """Extract the first capture group from text using a regex pattern.

    Returns the first captured group, or *default* if no match.
    """
    m = re.search(pattern, text)
    if m and m.lastindex:
        return m.group(1)
    return default


def _regex_extract_last(pattern: str, text: str, default: str | None = None) -> str | None:
    """Extract the last capture group match from text using a regex pattern.

    Uses a greedy lead-in ([\\s\\S]*) before the pattern to skip past earlier
    occurrences — handles cases where agents reference a tag in prose (e.g.
    backtick-escaped `<tag>`) before using it for real.
    """
    # Wrap pattern with greedy lead-in to match the LAST occurrence
    last_pattern = r"[\s\S]*" + pattern
    m = re.search(last_pattern, text, re.DOTALL)
    if m and m.lastindex:
        return m.group(1)
    return default


SAFE_BUILTINS = {
    "any": any,
    "all": all,
    "len": len,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "sorted": sorted,
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "True": True,
    "False": False,
    "None": None,
    # JavaScript/YAML-friendly aliases
    "true": True,
    "false": False,
    "null": None,
    # String / regex helpers
    "regex_extract": _regex_extract,
    "regex_extract_last": _regex_extract_last,
}


def _validate_expression_ast(expr: str) -> None:
    """Reject dangerous AST patterns before eval().

    Raises ValueError if the expression contains:
    - Attribute access starting with _ (blocks __class__, __bases__, etc.)
    - f-strings (can embed arbitrary expressions)
    - Lambda expressions
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid expression syntax: {e}") from e
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(
                f"Access to '{node.attr}' is not allowed in expressions"
            )
        if isinstance(node, ast.JoinedStr):
            raise ValueError("f-strings are not allowed in expressions")
        if isinstance(node, ast.Lambda):
            raise ValueError("Lambda expressions are not allowed")


class YAMLLoadError(Exception):
    """Raised when a YAML workflow file is invalid."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"YAML workflow errors: {'; '.join(errors)}")


class KitLoadError(Exception):
    """Error loading a KIT.yaml file."""
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def load_kit_yaml(path: str | Path) -> KitDefinition:
    """Parse a KIT.yaml file into a KitDefinition.

    Validates: name required, description required, name matches dir name,
    name matches FLOW_NAME_PATTERN.
    """
    path = Path(path)
    from stepwise.flow_resolution import FLOW_NAME_PATTERN

    errors: list[str] = []
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception as e:
        raise KitLoadError([f"Failed to parse {path}: {e}"])

    if not isinstance(data, dict):
        raise KitLoadError([f"{path}: expected a YAML mapping, got {type(data).__name__}"])

    name = data.get("name", "")
    if not name:
        errors.append("'name' is required")
    elif not FLOW_NAME_PATTERN.match(name):
        errors.append(f"Invalid kit name '{name}': must match [a-zA-Z0-9_.+-]+")

    if not data.get("description"):
        errors.append("'description' is required")

    # Name must match directory name
    if name and path.parent.name != name:
        errors.append(
            f"Kit name '{name}' does not match directory name '{path.parent.name}'"
        )

    if errors:
        raise KitLoadError(errors)

    # Validate types for optional fields
    include = data.get("include", [])
    if not isinstance(include, list):
        raise KitLoadError(["'include' must be a list"])

    tags = data.get("tags", [])
    if not isinstance(tags, list):
        raise KitLoadError(["'tags' must be a list"])

    return KitDefinition(
        name=name,
        description=data.get("description", ""),
        author=data.get("author", ""),
        category=data.get("category", ""),
        usage=data.get("usage", ""),
        include=[str(i) for i in include],
        tags=[str(t) for t in tags],
    )


class _DotDict(dict):
    """Dict that supports attribute access for exit rule expressions."""

    def __getattr__(self, name: str) -> Any:
        try:
            val = self[name]
        except KeyError:
            raise AttributeError(f"No field '{name}'")
        if isinstance(val, dict) and not isinstance(val, _DotDict):
            return _DotDict(val)
        return val


def evaluate_exit_condition(condition: str, outputs: dict, attempt: int,
                            max_attempts: int | None = None) -> bool:
    """Evaluate an exit rule condition expression.

    Uses eval() with a restricted namespace. Only safe builtins are available.
    """
    namespace = {
        "__builtins__": SAFE_BUILTINS,
        "outputs": _DotDict(outputs),
        "attempt": attempt,
        "max_attempts": max_attempts,
    }
    try:
        _validate_expression_ast(condition)
        return bool(eval(condition, namespace))
    except Exception as e:
        raise ValueError(f"Exit condition '{condition}' failed: {e}") from e


def evaluate_when_condition(condition: str, inputs: dict) -> bool:
    """Evaluate a step-level `when` condition against resolved inputs.

    Input names are directly available in the namespace (e.g., `status == 'pass'`).
    Returns False on NameError/AttributeError/TypeError (missing input).
    """
    namespace: dict = {"__builtins__": SAFE_BUILTINS}
    for k, v in inputs.items():
        namespace[k] = _DotDict(v) if isinstance(v, dict) else v
    try:
        _validate_expression_ast(condition)
    except ValueError:
        import logging
        logging.getLogger("stepwise.engine").warning(
            "when condition %r rejected by AST validator", condition
        )
        return False
    try:
        return bool(eval(condition, namespace))
    except (NameError, AttributeError, TypeError):
        return False
    except Exception:
        import logging
        logging.getLogger("stepwise.engine").warning(
            "when condition %r failed", condition, exc_info=True
        )
        return False


def evaluate_derived_outputs(
    derived: dict[str, str], artifact: dict
) -> dict[str, Any]:
    """Evaluate derived output expressions against a step's artifact.

    Each expression can reference artifact fields by name and use
    SAFE_BUILTINS (including ``regex_extract``).

    Returns a dict of computed field name → value.
    Raises ``ValueError`` if any expression fails.
    """
    namespace: dict = {"__builtins__": SAFE_BUILTINS}
    for k, v in artifact.items():
        namespace[k] = _DotDict(v) if isinstance(v, dict) else v
    results: dict[str, Any] = {}
    for field_name, expr in derived.items():
        try:
            _validate_expression_ast(expr)
            results[field_name] = eval(expr, namespace)
        except Exception as e:
            raise ValueError(
                f"Derived output '{field_name}' expression failed: {e}"
            ) from e
    return results


_IDENTIFIER_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _parse_input_binding(local_name: str, source: str) -> InputBinding:
    """Parse 'step.field' or '$job.field' into an InputBinding."""
    if source.startswith("$job."):
        return InputBinding(local_name, "$job", source[5:])
    parts = source.split(".", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Invalid input source '{source}' for '{local_name}'. "
            f"Expected 'step_name.field_name' or '$job.field_name'"
        )
    return InputBinding(local_name, parts[0], parts[1])


def _parse_inputs(inputs_data: Any, step_name: str) -> list[InputBinding]:
    """Parse inputs from YAML, supporting both string and any_of dict syntax."""
    bindings: list[InputBinding] = []
    if not isinstance(inputs_data, dict):
        return bindings
    for local_name, source in inputs_data.items():
        if not _IDENTIFIER_RE.match(local_name):
            raise ValueError(
                f"Step '{step_name}': input name '{local_name}' is not a valid "
                f"identifier (must match [A-Za-z_][A-Za-z0-9_]*)"
            )
        if isinstance(source, str):
            try:
                bindings.append(_parse_input_binding(local_name, source))
            except ValueError as e:
                raise ValueError(f"Step '{step_name}': {e}") from e
        elif isinstance(source, dict) and "from" in source:
            # {from: "step.field", optional: true} dict form
            try:
                binding = _parse_input_binding(local_name, source["from"])
            except ValueError as e:
                raise ValueError(f"Step '{step_name}': {e}") from e
            binding.optional = source.get("optional", False)
            bindings.append(binding)
        elif isinstance(source, dict) and "any_of" in source:
            sources_list = source["any_of"]
            if not isinstance(sources_list, list) or len(sources_list) < 2:
                raise ValueError(
                    f"Step '{step_name}': input '{local_name}' any_of must be a list with >= 2 entries"
                )
            any_of_pairs: list[tuple[str, str]] = []
            for src in sources_list:
                if not isinstance(src, str) or "." not in src:
                    raise ValueError(
                        f"Step '{step_name}': input '{local_name}' any_of entry '{src}' "
                        f"must be 'step_name.field_name'"
                    )
                parts = src.split(".", 1)
                any_of_pairs.append((parts[0], parts[1]))
            bindings.append(InputBinding(
                local_name=local_name,
                source_step="",
                source_field="",
                any_of_sources=any_of_pairs,
                optional=source.get("optional", False),
            ))
        else:
            raise ValueError(
                f"Step '{step_name}': input '{local_name}' must be a string, "
                f"{{from: ..., optional: true}}, or {{any_of: [...]}} dict"
            )
    return bindings


def _resolve_prompt_file(
    step_data: dict,
    step_name: str,
    base_dir: Path | None,
) -> str | None:
    """Resolve prompt_file to its content if present.

    Returns the file content as a string, or None if no prompt_file specified.
    Raises ValueError if both prompt and prompt_file are specified,
    or if the file cannot be read.
    """
    prompt_file = step_data.get("prompt_file")
    if prompt_file is None:
        return None

    if "prompt" in step_data:
        raise ValueError(
            f"Step '{step_name}': cannot specify both 'prompt' and 'prompt_file'"
        )

    if base_dir is None:
        raise ValueError(
            f"Step '{step_name}': 'prompt_file' cannot be resolved without a base directory"
        )

    prompt_path = (base_dir / prompt_file).resolve()
    if not prompt_path.exists():
        raise ValueError(
            f"Step '{step_name}': prompt file not found: {prompt_path}"
        )

    try:
        return prompt_path.read_text()
    except Exception as e:
        raise ValueError(
            f"Step '{step_name}': error reading prompt file '{prompt_path}': {e}"
        ) from e


def _parse_executor(
    step_data: dict,
    step_name: str,
    base_dir: Path | None = None,
) -> ExecutorRef:
    """Parse executor from step YAML data."""
    if "run" in step_data:
        command = step_data["run"]
        # If it's a .py file, prepend python3
        if command.endswith(".py"):
            command = f"python3 {command}"
        return ExecutorRef("script", {"command": command})

    executor_type = step_data.get("executor")
    if not executor_type:
        raise ValueError(
            f"Step '{step_name}': must have either 'run' or 'executor'"
        )

    # M10: Resolve prompt_file if present (consumed at parse time)
    prompt_from_file = _resolve_prompt_file(step_data, step_name, base_dir)

    config: dict[str, Any] = {}
    if executor_type == "external":
        prompt = prompt_from_file or step_data.get("prompt", "")
        if prompt:
            config["prompt"] = prompt
    elif executor_type == "mock_llm":
        # Pass through any config
        for k in ("failure_rate", "partial_rate", "latency_range", "responses"):
            if k in step_data:
                config[k] = step_data[k]
    elif executor_type == "agent":
        for k in ("prompt", "output_mode", "output_path", "emit_flow", "working_dir", "permissions", "agent", "containment"):
            if k in step_data:
                config[k] = step_data[k]
        if prompt_from_file:
            config["prompt"] = prompt_from_file
        if "prompt" not in config:
            raise ValueError(
                f"Step '{step_name}': Agent executor requires 'prompt'"
            )
    elif executor_type == "poll":
        check_command = step_data.get("check_command")
        if not check_command:
            raw_config = step_data.get("config", {})
            check_command = raw_config.get("check_command")
        if not check_command:
            raise ValueError(
                f"Step '{step_name}': Poll executor requires 'check_command'"
            )
        config["check_command"] = check_command
        if "interval_seconds" in step_data:
            config["interval_seconds"] = step_data["interval_seconds"]
        elif "config" in step_data and "interval_seconds" in step_data["config"]:
            config["interval_seconds"] = step_data["config"]["interval_seconds"]
        prompt = prompt_from_file or step_data.get("prompt", "")
        if prompt:
            config["prompt"] = prompt
    elif executor_type == "llm":
        for k in ("prompt", "model", "system", "temperature", "max_tokens"):
            if k in step_data:
                config[k] = step_data[k]
        if prompt_from_file:
            config["prompt"] = prompt_from_file
        if "prompt" not in config:
            raise ValueError(
                f"Step '{step_name}': LLM executor requires 'prompt'"
            )

    return ExecutorRef(executor_type, config)


def _parse_exit_rules(exits_data: list[dict], step_name: str) -> list[ExitRule]:
    """Parse exit rules from YAML."""
    rules: list[ExitRule] = []
    for i, rule_data in enumerate(exits_data):
        name = rule_data.get("name", f"rule_{i}")
        condition = rule_data.get("when")
        action = rule_data.get("action", "advance")
        target = rule_data.get("target")

        if condition is None:
            raise ValueError(
                f"Step '{step_name}': exit rule '{name}' missing 'when' condition"
            )

        if action not in ("advance", "loop", "escalate", "abandon"):
            raise ValueError(
                f"Step '{step_name}': exit rule '{name}' invalid action '{action}'. "
                f"Must be advance, loop, escalate, or abandon"
            )

        if action == "loop" and not target:
            raise ValueError(
                f"Step '{step_name}': exit rule '{name}' has action 'loop' but no 'target'"
            )

        if action == "advance" and target:
            raise ValueError(
                f"Step '{step_name}': exit rule '{name}' has 'advance' with 'target' — "
                f"use step-level 'when' for conditional branching instead"
            )

        config: dict[str, Any] = {
            "condition": condition,
            "action": action,
        }
        if target:
            config["target"] = target
        max_iter = rule_data.get("max_iterations")
        if max_iter is not None:
            config["max_iterations"] = max_iter

        # Map priority from position (first rule = highest priority)
        priority = len(exits_data) - i

        rules.append(ExitRule(
            name=name,
            type="expression",
            config=config,
            priority=priority,
        ))

    return rules


def _parse_decorators(dec_data: list[dict], step_name: str) -> list[DecoratorRef]:
    """Parse decorators from YAML."""
    decorators: list[DecoratorRef] = []
    for d in dec_data:
        dtype = d.get("type")
        if not dtype:
            raise ValueError(f"Step '{step_name}': decorator missing 'type'")
        decorators.append(DecoratorRef(dtype, d.get("config", {})))
    return decorators


def _find_project_dir(start: Path) -> Path:
    """Walk up from start to find project root (has .stepwise/ or flows/)."""
    current = start.resolve()
    for _ in range(20):
        if (current / ".stepwise").is_dir() or (current / "flows").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return start


def _load_flow_from_file(
    file_ref: str,
    step_name: str,
    context: str,
    base_dir: Path | None,
    loading_files: frozenset[Path] | None,
    project_dir: Path | None = None,
) -> WorkflowDefinition:
    """Load a sub-flow from a file path reference.

    Resolves relative to base_dir, checks for circular references.
    Returns the parsed WorkflowDefinition (baked inline).
    """
    if base_dir is None:
        raise ValueError(
            f"Step '{step_name}' {context}: file ref '{file_ref}' "
            f"cannot be resolved without a base directory"
        )
    abs_path = (base_dir / file_ref).resolve()
    if not abs_path.exists():
        raise ValueError(
            f"Step '{step_name}' {context}: flow file not found: {abs_path}"
        )
    if loading_files and abs_path in loading_files:
        raise ValueError(
            f"Step '{step_name}' {context}: circular flow reference: "
            f"{abs_path} is already being loaded"
        )
    # Immutable set copy per branch — sibling flows can share files
    branch_files = (loading_files or frozenset()) | {abs_path}
    return load_workflow_yaml(
        str(abs_path),
        base_dir=abs_path.parent,
        loading_files=branch_files,
        project_dir=project_dir,
    )


def _resolve_flow_name(
    name: str,
    step_name: str,
    context: str,
    project_dir: Path | None,
    loading_files: frozenset[Path] | None,
) -> WorkflowDefinition:
    """Resolve a bare flow name via project discovery.

    Uses resolve_flow() from flow_resolution.py, checks for circular refs.
    """
    from stepwise.flow_resolution import resolve_flow, FlowResolutionError

    try:
        flow_path = resolve_flow(name, project_dir=project_dir)
    except FlowResolutionError as e:
        raise ValueError(f"Step '{step_name}' {context}: {e}") from e

    abs_path = flow_path.resolve()
    if loading_files and abs_path in loading_files:
        raise ValueError(
            f"Step '{step_name}' {context}: circular flow reference: "
            f"{abs_path} is already being loaded"
        )
    branch_files = (loading_files or frozenset()) | {abs_path}
    return load_workflow_yaml(
        str(abs_path),
        base_dir=abs_path.parent,
        loading_files=branch_files,
        project_dir=project_dir,
    )


def _load_flow_from_registry(
    ref: str,
    step_name: str,
    context: str,
) -> WorkflowDefinition:
    """Resolve an @author:name registry ref to a WorkflowDefinition.

    Fetches the flow YAML from the registry, parses it, and returns
    the baked WorkflowDefinition. Uses disk cache when available.
    """
    from stepwise.registry_client import fetch_flow_yaml, RegistryError

    # Parse ref: @author:name or just @name
    ref_body = ref.lstrip("@")
    if ":" in ref_body:
        author, name = ref_body.split(":", 1)
    else:
        name = ref_body
        author = None

    slug = name  # Registry lookup is by slug

    try:
        yaml_content = fetch_flow_yaml(slug)
    except RegistryError as e:
        raise ValueError(
            f"Step '{step_name}' {context}: failed to resolve registry ref '{ref}': {e}"
        ) from e

    # Parse the fetched YAML into a WorkflowDefinition
    try:
        flow = load_workflow_yaml(yaml_content)
    except YAMLLoadError as e:
        raise ValueError(
            f"Step '{step_name}' {context}: registry flow '{ref}' has errors: {e}"
        ) from e

    # Verify author matches if specified
    if author and flow.metadata and flow.metadata.author:
        if flow.metadata.author != author:
            raise ValueError(
                f"Step '{step_name}' {context}: registry ref '{ref}' specifies author "
                f"'{author}' but flow is by '{flow.metadata.author}'"
            )

    return flow


def _resolve_flow_source(
    flow_data: Any,
    step_name: str,
    context: str,
    base_dir: Path | None = None,
    loading_files: frozenset[Path] | None = None,
    project_dir: Path | None = None,
) -> tuple[WorkflowDefinition, str | None]:
    """Resolve a flow source (string ref or inline dict) to a WorkflowDefinition.

    Returns (workflow, flow_ref) where flow_ref is the original ref string
    for provenance (None for inline dicts).
    """
    if isinstance(flow_data, str):
        if flow_data.startswith("@"):
            flow = _load_flow_from_registry(flow_data, step_name, context)
            return flow, flow_data
        if flow_data.endswith((".yaml", ".yml")):
            flow = _load_flow_from_file(
                flow_data, step_name, context, base_dir, loading_files,
                project_dir=project_dir,
            )
            return flow, flow_data
        from stepwise.flow_resolution import FLOW_NAME_PATTERN
        if FLOW_NAME_PATTERN.match(flow_data):
            flow = _resolve_flow_name(
                flow_data, step_name, context, project_dir, loading_files
            )
            return flow, flow_data
        raise ValueError(
            f"Step '{step_name}' {context}: flow must be a .yaml/.yml file path, "
            f"a bare flow name, or an @author:name registry ref"
        )

    if isinstance(flow_data, dict):
        flow_steps_data = flow_data.get("steps")
        if not flow_steps_data or not isinstance(flow_steps_data, dict):
            raise ValueError(
                f"Step '{step_name}' {context}: inline flow must have a 'steps' mapping"
            )
        flow_steps: dict[str, StepDefinition] = {}
        for sub_name, sub_data in flow_steps_data.items():
            if not isinstance(sub_data, dict):
                raise ValueError(
                    f"Step '{step_name}' {context} step '{sub_name}': must be a mapping"
                )
            flow_steps[sub_name] = _parse_step(
                sub_name, sub_data, base_dir=base_dir, loading_files=loading_files,
                project_dir=project_dir,
            )
        # Parse inline flow's input declarations (if any) so §9.7.5
        # inference 3 can detect explicit vs inferred inputs.
        input_vars: list = []
        if flow_data.get("inputs"):
            try:
                input_vars = _parse_input_vars(flow_data)
            except ValueError:
                pass  # validation errors handled elsewhere
        # Inherit source_dir from the lexically enclosing flow file so that
        # `run: scripts/foo.py` and `prompt_file: prompts/foo.md` inside an
        # inline sub-flow continue to reference the parent flow's assets
        # (matching authoring intent for inline for_each blocks). Only set
        # this when the parent context is itself file-backed — for string-
        # loaded parents (no source_path) source_dir stays None.
        inline_source_dir = (
            str(base_dir.resolve())
            if base_dir is not None and loading_files
            else None
        )
        return (
            WorkflowDefinition(
                steps=flow_steps,
                input_vars=input_vars,
                source_dir=inline_source_dir,
            ),
            None,
        )

    raise ValueError(
        f"Step '{step_name}' {context}: 'flow' must be a string or mapping"
    )


def _parse_for_each(
    step_data: dict,
    step_name: str,
    base_dir: Path | None = None,
    loading_files: frozenset[Path] | None = None,
    project_dir: Path | None = None,
) -> tuple[ForEachSpec | None, WorkflowDefinition | None]:
    """Parse for_each and flow blocks from step YAML data."""
    for_each_source = step_data.get("for_each")
    if not for_each_source:
        return None, None

    if not isinstance(for_each_source, str):
        raise ValueError(
            f"Step '{step_name}': 'for_each' must be a string like 'step.field'"
        )

    # Parse source: "step_name.field" or "step_name.field.nested"
    parts = for_each_source.split(".", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Step '{step_name}': 'for_each' must be 'step_name.field_name', "
            f"got '{for_each_source}'"
        )
    source_step, source_field = parts

    # Item variable name
    item_var = step_data.get("as", "item")
    if not isinstance(item_var, str) or not item_var.isidentifier():
        raise ValueError(
            f"Step '{step_name}': 'as' must be a valid identifier, got '{item_var}'"
        )

    # Error policy
    on_error = step_data.get("on_error", "fail_fast")

    # Watchdog timeout for stale pending sub-jobs (seconds)
    stale_pending_timeout = step_data.get("stale_pending_timeout", 60)
    if not isinstance(stale_pending_timeout, int) or stale_pending_timeout <= 0:
        raise ValueError(
            f"Step '{step_name}': 'stale_pending_timeout' must be a positive integer (seconds), "
            f"got {stale_pending_timeout!r}"
        )

    for_each_spec = ForEachSpec(
        source_step=source_step,
        source_field=source_field,
        item_var=item_var,
        on_error=on_error,
        stale_pending_timeout_seconds=stale_pending_timeout,
    )

    # Parse embedded flow (can be dict or file ref string)
    flow_data = step_data.get("flow")
    if not flow_data:
        raise ValueError(
            f"Step '{step_name}': for_each requires a 'flow' block"
        )

    sub_flow, _ = _resolve_flow_source(
        flow_data, step_name, "for_each flow",
        base_dir=base_dir, loading_files=loading_files, project_dir=project_dir,
    )
    return for_each_spec, sub_flow


def _parse_output_field_spec(
    field_name: str, spec_data: Any, step_name: str,
) -> OutputFieldSpec:
    """Parse a single output field spec from YAML data."""
    if spec_data is None:
        return OutputFieldSpec()
    if not isinstance(spec_data, dict):
        raise ValueError(
            f"Step '{step_name}': output field '{field_name}' spec must be a mapping or null"
        )

    field_type = spec_data.get("type", "str")
    if field_type not in VALID_FIELD_TYPES:
        raise ValueError(
            f"Step '{step_name}': output field '{field_name}' has invalid type '{field_type}'. "
            f"Must be one of: {', '.join(sorted(VALID_FIELD_TYPES))}"
        )

    options = spec_data.get("options")
    multiple = spec_data.get("multiple", False)
    min_val = spec_data.get("min")
    max_val = spec_data.get("max")

    # Validate type-specific constraints
    if field_type == "choice":
        if options is None or not isinstance(options, list) or len(options) == 0:
            raise ValueError(
                f"Step '{step_name}': output field '{field_name}' (type=choice) requires non-empty 'options' list"
            )
    else:
        if options is not None:
            raise ValueError(
                f"Step '{step_name}': output field '{field_name}' (type={field_type}) cannot have 'options' — only choice fields can"
            )
        if multiple:
            raise ValueError(
                f"Step '{step_name}': output field '{field_name}' (type={field_type}) cannot have 'multiple' — only choice fields can"
            )

    if field_type != "number":
        if min_val is not None or max_val is not None:
            raise ValueError(
                f"Step '{step_name}': output field '{field_name}' (type={field_type}) cannot have 'min'/'max' — only number fields can"
            )

    return OutputFieldSpec(
        type=field_type,
        required=spec_data.get("required", True),
        default=spec_data.get("default"),
        description=spec_data.get("description", ""),
        options=options,
        multiple=multiple,
        min=min_val,
        max=max_val,
    )


def _parse_outputs(
    step_data: dict, step_name: str,
) -> tuple[list[str], dict[str, OutputFieldSpec]]:
    """Parse outputs from step YAML data.

    Returns (outputs_list, output_schema).
    Supports list format (backward compat) and dict format (typed).
    """
    raw = step_data.get("outputs", [])

    if isinstance(raw, list):
        return raw, {}

    if isinstance(raw, dict):
        outputs: list[str] = list(raw.keys())
        schema: dict[str, OutputFieldSpec] = {}
        for field_name, spec_data in raw.items():
            schema[field_name] = _parse_output_field_spec(field_name, spec_data, step_name)
        return outputs, schema

    raise ValueError(f"Step '{step_name}': 'outputs' must be a list or mapping")


def _parse_step(
    step_name: str,
    step_data: dict,
    base_dir: Path | None = None,
    loading_files: frozenset[Path] | None = None,
    project_dir: Path | None = None,
) -> StepDefinition:
    """Parse a single step from YAML data."""
    # Outputs
    outputs, output_schema = _parse_outputs(step_data, step_name)

    # Check for direct flow step (sub-flow invocation without for_each)
    flow_data = step_data.get("flow")
    if flow_data and not step_data.get("for_each"):
        if step_data.get("run") or step_data.get("executor"):
            raise ValueError(f"Step '{step_name}': cannot combine flow with run/executor")

        sub_flow, flow_ref = _resolve_flow_source(
            flow_data, step_name, "flow",
            base_dir=base_dir, loading_files=loading_files, project_dir=project_dir,
        )

        if not outputs:
            raise ValueError(f"Step '{step_name}': flow steps must declare outputs")

        # Validate output contract: terminal steps must cover declared outputs
        terms = sub_flow.terminal_steps()
        if not terms:
            raise ValueError(
                f"Step '{step_name}': sub-flow has no terminal steps "
                f"but flow step requires outputs {sorted(outputs)}"
            )
        for term_name in terms:
            term_outputs = set(sub_flow.steps[term_name].outputs)
            missing = set(outputs) - term_outputs
            if missing:
                raise ValueError(
                    f"Step '{step_name}': sub-flow terminal step '{term_name}' "
                    f"missing outputs {sorted(missing)}"
                )

        input_bindings = _parse_inputs(step_data.get("inputs", {}), step_name)

        if "after" in step_data and "sequencing" in step_data:
            raise ValueError(
                f"Step '{step_name}': cannot use both 'after' and 'sequencing' "
                f"(use 'after' — 'sequencing' is deprecated)"
            )
        if "after" in step_data:
            after, after_any_of = _parse_after(step_data["after"], step_name)
        elif "sequencing" in step_data:
            after, after_any_of = _parse_after(step_data["sequencing"], step_name)
        else:
            after, after_any_of = [], []

        # after_resolved: deps that accept SKIPPED as settled
        after_resolved = _parse_after_resolved(step_data.get("after_resolved"), step_name)

        return StepDefinition(
            name=step_name,
            description=step_data.get("description", ""),
            outputs=outputs,
            output_schema=output_schema,
            executor=ExecutorRef("sub_flow", {"flow_ref": flow_ref} if flow_ref else {}),
            inputs=input_bindings,
            after=after,
            after_resolved=after_resolved,
            after_any_of=after_any_of,
            sub_flow=sub_flow,
            when=_parse_when(step_data.get("when"), step_name),
        )

    # Check for for_each (changes how the step is parsed)
    for_each_spec, sub_flow = _parse_for_each(
        step_data, step_name, base_dir, loading_files, project_dir
    )

    if for_each_spec:
        # For-each step: executor is a no-op placeholder, the engine handles it
        executor = ExecutorRef("for_each", {})

        input_bindings = _parse_inputs(step_data.get("inputs", {}), step_name)

        # §9.7.5 Inference 3: infer sub_flow input schema from parent
        # bindings + item_var when the embedded flow has no inputs: block.
        # Only applies to inline flow: blocks (dicts), not file references.
        flow_data = step_data.get("flow")
        if (
            sub_flow is not None
            and isinstance(flow_data, dict)
            and not sub_flow.input_vars
            and not flow_data.get("inputs")
        ):
            from stepwise.models import ConfigVar
            inferred_inputs: list[ConfigVar] = []
            for b in input_bindings:
                iv_type = "str"
                if b.source_field == "_session":
                    iv_type = "session"
                inferred_inputs.append(ConfigVar(
                    name=b.local_name,
                    type=iv_type,
                    required=not b.optional,
                ))
            # Add item_var
            inferred_inputs.append(ConfigVar(
                name=for_each_spec.item_var,
                type="str",
                required=True,
            ))
            sub_flow.input_vars = inferred_inputs

        # Outputs default to ["results"] for for_each steps
        if not outputs:
            outputs = ["results"]

        if "after" in step_data and "sequencing" in step_data:
            raise ValueError(
                f"Step '{step_name}': cannot use both 'after' and 'sequencing' "
                f"(use 'after' — 'sequencing' is deprecated)"
            )
        if "after" in step_data:
            after, after_any_of = _parse_after(step_data["after"], step_name)
        elif "sequencing" in step_data:
            after, after_any_of = _parse_after(step_data["sequencing"], step_name)
        else:
            after, after_any_of = [], []

        # after_resolved: deps that accept SKIPPED as settled
        after_resolved = _parse_after_resolved(step_data.get("after_resolved"), step_name)

        return StepDefinition(
            name=step_name,
            description=step_data.get("description", ""),
            outputs=outputs,
            output_schema=output_schema,
            executor=executor,
            inputs=input_bindings,
            after=after,
            after_resolved=after_resolved,
            after_any_of=after_any_of,
            for_each=for_each_spec,
            sub_flow=sub_flow,
            when=_parse_when(step_data.get("when"), step_name),
        )

    # Normal step parsing
    # Executor
    executor = _parse_executor(step_data, step_name, base_dir=base_dir)

    # Inputs
    input_bindings = _parse_inputs(step_data.get("inputs", {}), step_name)

    # After (ordering deps)
    if "after" in step_data and "sequencing" in step_data:
        raise ValueError(
            f"Step '{step_name}': cannot use both 'after' and 'sequencing' "
            f"(use 'after' — 'sequencing' is deprecated)"
        )
    if "after" in step_data:
        after, after_any_of = _parse_after(step_data["after"], step_name)
    elif "sequencing" in step_data:
        after, after_any_of = _parse_after(step_data["sequencing"], step_name)
    else:
        after, after_any_of = [], []

    # Exit rules
    exits_data = step_data.get("exits", [])
    exit_rules = _parse_exit_rules(exits_data, step_name) if exits_data else []

    # Decorators
    dec_data = step_data.get("decorators", [])
    decorators = _parse_decorators(dec_data, step_name) if dec_data else []
    if decorators:
        executor = ExecutorRef(executor.type, executor.config, decorators)

    # Idempotency
    idempotency = step_data.get("idempotency", "idempotent")

    # Limits
    limits = None
    limits_data = step_data.get("limits")
    if isinstance(limits_data, dict):
        limits = StepLimits.from_dict(limits_data)

    # Step-level when condition (pure-pull branching)
    when_condition = _parse_when(step_data.get("when"), step_name)

    # Named sessions
    session = step_data.get("session")
    if session is not None:
        if not isinstance(session, str):
            raise ValueError(
                f"Step '{step_name}': session must be a string, "
                f"got {type(session).__name__}"
            )
        if not _SESSION_NAME_PATTERN.match(session):
            raise ValueError(
                f"Step '{step_name}': session name '{session}' is invalid — "
                f"must match [A-Za-z_][A-Za-z0-9_]* (static identifier, "
                f"no templating like ${{var}})"
            )
    fork_from = step_data.get("fork_from")

    # §9.7.5 Inference 1: agent: claude inferred from fork_from.
    # The fork mechanism is inherently claude (--resume <uuid> --fork-session).
    # If fork_from is set and executor is agent but no explicit agent config,
    # auto-set agent to claude.
    if fork_from and executor.type == "agent" and not executor.config.get("agent"):
        executor = executor.with_config({"agent": "claude"})

    # Session continuity (legacy)
    continue_session = step_data.get("continue_session", False)
    loop_prompt_raw = step_data.get("loop_prompt")
    loop_prompt = None
    if loop_prompt_raw is not None:
        loop_prompt = str(loop_prompt_raw)
    max_continuous_attempts = step_data.get("max_continuous_attempts")

    # Cache config
    cache_config = None
    cache_raw = step_data.get("cache")
    if cache_raw is True:
        cache_config = CacheConfig()
    elif cache_raw is False:
        cache_config = None
    elif isinstance(cache_raw, dict):
        ttl = None
        ttl_raw = cache_raw.get("ttl")
        if ttl_raw is not None:
            if isinstance(ttl_raw, int):
                ttl = ttl_raw
            elif isinstance(ttl_raw, str):
                ttl = parse_duration(ttl_raw)
                if ttl is None:
                    errors.append(f"Step '{step_name}': invalid cache ttl '{ttl_raw}'")
        cache_config = CacheConfig(
            enabled=cache_raw.get("enabled", True),
            ttl=ttl,
            key_extra=cache_raw.get("key_extra"),
        )

    # Step-level error policy
    on_error_raw = step_data.get("on_error", "fail")
    if on_error_raw not in ("fail", "continue"):
        raise ValueError(
            f"Step '{step_name}': invalid on_error '{on_error_raw}' "
            f"(valid: 'fail', 'continue')"
        )

    # Derived outputs
    derived_outputs_raw = step_data.get("derived_outputs", {})
    if derived_outputs_raw and not isinstance(derived_outputs_raw, dict):
        raise ValueError(
            f"Step '{step_name}': derived_outputs must be a mapping"
        )

    # after_resolved: deps that accept SKIPPED as settled
    after_resolved = _parse_after_resolved(step_data.get("after_resolved"), step_name)

    return StepDefinition(
        name=step_name,
        description=step_data.get("description", ""),
        outputs=outputs,
        output_schema=output_schema,
        executor=executor,
        inputs=input_bindings,
        after=after,
        after_resolved=after_resolved,
        after_any_of=after_any_of,
        exit_rules=exit_rules,
        idempotency=idempotency,
        when=when_condition,
        limits=limits,
        session=session,
        fork_from=fork_from,
        continue_session=continue_session,
        loop_prompt=loop_prompt,
        max_continuous_attempts=max_continuous_attempts,
        cache=cache_config,
        on_error=on_error_raw,
        derived_outputs=derived_outputs_raw if derived_outputs_raw else {},
    )


def _agent_name(sd: StepDefinition) -> str | None:
    """Return the explicit ``agent`` config value on a step (or None)."""
    if sd.executor and sd.executor.config:
        return sd.executor.config.get("agent")
    return None


def _validate_sessions(
    steps: dict[str, StepDefinition],
    errors: list[str],
    input_vars: list | None = None,
) -> None:
    """Validate named session and fork_from constraints across all steps.

    Under step-name fork_from semantics (design doc §8.2), ``fork_from``
    references a STEP NAME, not a session name. The forking step is the
    chain root for a new session and inherits the parent step's
    completion-tail snapshot.

    Per §9.7, ``fork_from`` also accepts ``$job.<input>`` references
    where the input has ``type: session``, and ``_session`` is a virtual
    output on any session-bearing step.
    """
    # Build session → step mapping
    session_steps: dict[str, list[str]] = {}

    for step_name, step_def in steps.items():
        # Rule 1 (relaxed per §9.7.1): fork_from WITHOUT session is now
        # allowed — ephemeral one-shot forks need no session name.
        # fork_from requires session ONLY IF another step in the flow
        # also writes to that session (chain continuation). That check
        # is deferred to Rule 3/4 which already handle chain roots.

        if step_def.session:
            session_steps.setdefault(step_def.session, []).append(step_name)

        # Rule 6 (relaxed per §9.7.1): for_each + session is still
        # banned, but for_each + fork_from WITHOUT session is now legal
        # (ephemeral forking in sub_flows).
        if step_def.session and step_def.for_each:
            errors.append(
                f"Step '{step_name}': session is not compatible with for_each"
            )

        # Rule 7: old syntax detection
        if step_def.session and step_def.continue_session:
            errors.append(
                f"Step '{step_name}': continue_session is deprecated, use session: <name>"
            )
        if step_def.session:
            for inp in step_def.inputs:
                if inp.local_name == "_session_id":
                    errors.append(
                        f"Step '{step_name}': _session_id input is deprecated, use session: <name>"
                    )

    # Rule 1b (NEW): fork_from must reference a known step OR $job.<input>.
    # Per §9.7.3, fork_from: $job.<input> is legal when the referenced
    # input has type: session.
    for step_name, step_def in steps.items():
        if not step_def.fork_from:
            continue
        if step_def.fork_from.startswith("$job."):
            # $job. references are validated separately below
            continue
        if step_def.fork_from not in steps:
            errors.append(
                f"Step '{step_name}': fork_from references unknown step "
                f"'{step_def.fork_from}' (in v1.0, fork_from takes a step "
                f"name, not a session name — see §8.2 of the coordination doc)"
            )

    # Rule 2 (rewritten): fork_from target step must declare session.
    # Skipped for $job. references (those resolve at runtime).
    for step_name, step_def in steps.items():
        if not step_def.fork_from or step_def.fork_from not in steps:
            continue
        target = steps[step_def.fork_from]
        if not target.session:
            errors.append(
                f"Step '{step_name}': fork_from target "
                f"'{step_def.fork_from}' has no session: declared "
                f"(cannot fork from an ephemeral one-shot agent step)"
            )

    # Rule 3 (rewritten): explicit agent: claude on (a) all writers of the
    # forked session AND (b) all writers of the parent session (the session
    # of the fork_from target step). Skipped for $job. fork_from references.
    # §9.7.5 Inference 1: steps with fork_from: already have agent: claude
    # inferred at parse time, so the check on part (a) skips them.
    for step_name, step_def in steps.items():
        if not step_def.fork_from or step_def.fork_from not in steps:
            continue
        target = steps[step_def.fork_from]
        if not target.session:
            continue
        # (a) Forked session writers — skip agent steps with fork_from
        # (inference 1 ensures they have agent: claude via fork_from).
        forked_session = step_def.session
        if forked_session:
            for sn in session_steps.get(forked_session, []):
                sd = steps[sn]
                if sd.fork_from and sd.executor.type == "agent":
                    continue  # §9.7.5: claude inferred from fork_from
                if sd.executor.type != "agent" or _agent_name(sd) != "claude":
                    errors.append(
                        f"Step '{sn}': session forking requires explicit agent: claude"
                    )
        # (b) Parent session writers — these are NOT forking, they
        # must still declare agent: claude explicitly.
        parent_session = target.session
        for sn in session_steps.get(parent_session, []):
            sd = steps[sn]
            if sd.executor.type != "agent" or _agent_name(sd) != "claude":
                errors.append(
                    f"Step '{sn}': session forking requires explicit agent: claude"
                )

    # Rule 4 (rewritten — single-chain rule per §8.1):
    # all chain roots on a forked session must point at steps writing
    # to the same parent session. The conditional-rejoin pattern
    # (multiple chain roots into the same parent session, gated by mutex)
    # is permitted at this rule level — the mutex check belongs to the
    # step 3 coordination validator.
    chain_roots_by_session: dict[str, list[tuple[str, str]]] = {}
    for step_name, step_def in steps.items():
        if not (step_def.fork_from and step_def.session):
            continue
        target = steps.get(step_def.fork_from)
        if target and target.session:
            chain_roots_by_session.setdefault(
                step_def.session, []
            ).append((step_name, target.session))
    for sess, roots in chain_roots_by_session.items():
        parent_sessions = {ps for _, ps in roots}
        if len(parent_sessions) > 1:
            roots_str = ", ".join(f"{r}->{ps}" for r, ps in sorted(roots))
            errors.append(
                f"Session '{sess}' has chain roots forking from "
                f"different parent sessions: {roots_str}"
            )

    # Rule 5 (rewritten): each chain root must declare a dependency on its
    # fork target step (in `after:` or as an input source). Stronger
    # transitive checks are the step 3 validator's job.
    # Skipped for $job. fork_from references (no same-scope step to depend on).
    for step_name, step_def in steps.items():
        if not step_def.fork_from:
            continue
        if step_def.fork_from not in steps:
            continue
        deps = set(step_def.after) | set(step_def.after_resolved)
        for inp in step_def.inputs:
            if inp.source_step and inp.source_step != "$job":
                deps.add(inp.source_step)
        if step_def.fork_from not in deps:
            errors.append(
                f"Step '{step_name}': fork target "
                f"'{step_def.fork_from}' must appear in 'after:' or as "
                f"an input source"
            )

    # §9.7.3: fork_from: $job.<input> requires the input to have type: session.
    input_var_types: dict[str, str] = {}
    if input_vars:
        for iv in input_vars:
            input_var_types[iv.name] = iv.type
    for step_name, step_def in steps.items():
        if not step_def.fork_from:
            continue
        if not step_def.fork_from.startswith("$job."):
            continue
        input_name = step_def.fork_from[len("$job."):]
        iv_type = input_var_types.get(input_name)
        if iv_type != "session":
            errors.append(
                f"Step '{step_name}': fork_from: {step_def.fork_from} "
                f"requires the input '{input_name}' to have type: session"
                + (f" (got type: {iv_type})" if iv_type else
                   " (input not declared)")
            )

    # §9.7.3: _session virtual output is only valid on session-bearing steps.
    for step_name, step_def in steps.items():
        for inp in step_def.inputs:
            src_field = inp.source_field
            if inp.any_of_sources:
                for src_step, sf in inp.any_of_sources:
                    if sf == "_session" and src_step in steps:
                        if not steps[src_step].session:
                            errors.append(
                                f"Step '{step_name}': input '{inp.local_name}' "
                                f"references '{src_step}._session' but step "
                                f"'{src_step}' has no session: declared"
                            )
            elif src_field == "_session" and inp.source_step and inp.source_step != "$job":
                if inp.source_step in steps and not steps[inp.source_step].session:
                    errors.append(
                        f"Step '{step_name}': input '{inp.local_name}' "
                        f"references '{inp.source_step}._session' but step "
                        f"'{inp.source_step}' has no session: declared"
                    )


def _parse_metadata(data: dict, source_path: Path | None = None) -> FlowMetadata:
    """Extract FlowMetadata from YAML top-level fields."""
    name = data.get("name", "")
    if not name and source_path:
        # Default name from filename: "my-flow.flow.yaml" → "my-flow"
        stem = source_path.stem
        if stem.endswith(".flow"):
            stem = stem[:-5]
        name = stem

    author = data.get("author", "")

    visibility = data.get("visibility", "interactive")
    if visibility not in VALID_VISIBILITY:
        raise YAMLLoadError([
            f"Invalid visibility '{visibility}' "
            f"(valid: {', '.join(sorted(VALID_VISIBILITY))})"
        ])

    return FlowMetadata(
        name=name,
        description=data.get("description", ""),
        author=author,
        version=data.get("version", ""),
        forked_from=data.get("forked_from", ""),
        visibility=visibility,
    )


def _parse_config(data: dict) -> list[ConfigVar]:
    """Parse config variable declarations from top-level 'config' block."""
    config_data = data.get("config")
    if not config_data:
        return []
    if not isinstance(config_data, dict):
        raise ValueError("'config' must be a mapping")

    config_vars: list[ConfigVar] = []
    for name, spec in config_data.items():
        if not str(name).isidentifier():
            raise ValueError(f"Config variable '{name}': not a valid identifier")

        if spec is None:
            spec = {}
        if not isinstance(spec, dict):
            raise ValueError(f"Config variable '{name}': spec must be a mapping")

        typ = spec.get("type", "str")
        if typ not in VALID_FIELD_TYPES:
            raise ValueError(
                f"Config variable '{name}': invalid type '{typ}' "
                f"(valid: {', '.join(sorted(VALID_FIELD_TYPES))})"
            )
        if typ == "choice" and not spec.get("options"):
            raise ValueError(
                f"Config variable '{name}': type 'choice' requires non-empty 'options' list"
            )

        has_default = "default" in spec
        config_vars.append(ConfigVar(
            name=str(name),
            description=spec.get("description", ""),
            type=typ,
            default=spec.get("default"),
            required=spec.get("required", not has_default),
            example=str(spec["example"]) if "example" in spec else "",
            options=spec.get("options"),
            sensitive=bool(spec.get("sensitive", False)),
        ))

    return config_vars


def _parse_input_vars(data: dict) -> list[ConfigVar]:
    """Parse input variable declarations from top-level 'inputs' block."""
    input_data = data.get("inputs")
    if not input_data:
        return []
    if not isinstance(input_data, dict):
        raise ValueError("'inputs' must be a mapping")

    input_vars: list[ConfigVar] = []
    for name, spec in input_data.items():
        if not str(name).isidentifier():
            raise ValueError(f"Input variable '{name}': not a valid identifier")

        if spec is None:
            spec = {}
        if not isinstance(spec, dict):
            raise ValueError(f"Input variable '{name}': spec must be a mapping")

        typ = spec.get("type", "str")
        if typ not in VALID_FIELD_TYPES:
            raise ValueError(
                f"Input variable '{name}': invalid type '{typ}' "
                f"(valid: {', '.join(sorted(VALID_FIELD_TYPES))})"
            )
        if typ == "choice" and not spec.get("options"):
            raise ValueError(
                f"Input variable '{name}': type 'choice' requires non-empty 'options' list"
            )

        has_default = "default" in spec
        input_vars.append(ConfigVar(
            name=str(name),
            description=spec.get("description", ""),
            type=typ,
            default=spec.get("default"),
            required=spec.get("required", not has_default),
            example=str(spec["example"]) if "example" in spec else "",
            options=spec.get("options"),
            sensitive=bool(spec.get("sensitive", False)),
        ))

    return input_vars


def _parse_requires(data: dict) -> list[FlowRequirement]:
    """Parse requirement declarations from top-level 'requires' block."""
    requires_data = data.get("requires")
    if not requires_data:
        return []
    if not isinstance(requires_data, list):
        raise ValueError("'requires' must be a list")

    requires: list[FlowRequirement] = []
    for item in requires_data:
        if isinstance(item, str):
            # Shorthand: just a name
            if not item:
                raise ValueError("Requirement name cannot be empty")
            requires.append(FlowRequirement(name=item))
        elif isinstance(item, dict):
            name = item.get("name", "")
            if not name:
                raise ValueError("Requirement must have a 'name' field")
            requires.append(FlowRequirement(
                name=name,
                description=item.get("description", ""),
                check=item.get("check", ""),
                install=item.get("install", ""),
                url=item.get("url", ""),
            ))
        else:
            raise ValueError(f"Requirement entry must be a string or mapping, got {type(item).__name__}")

    return requires


def _load_readme(base_dir: Path | None, data: dict) -> str:
    """Load readme content from inline YAML or README.md file."""
    # Inline readme takes priority
    if "readme" in data:
        return str(data["readme"])

    # For directory flows, try loading README.md
    if base_dir and base_dir.is_dir():
        readme_path = base_dir / "README.md"
        if readme_path.is_file():
            return readme_path.read_text()

    return ""


def get_author() -> str:
    """Get author name: git config → $USER → 'anonymous'."""
    import os
    import subprocess
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return os.environ.get("USER", os.environ.get("USERNAME", "anonymous"))


def load_workflow_yaml(
    source: str | Path,
    base_dir: Path | None = None,
    loading_files: frozenset[Path] | None = None,
    project_dir: Path | None = None,
) -> WorkflowDefinition:
    """Load a WorkflowDefinition from a YAML file or string.

    Args:
        source: Path to a YAML file, or a YAML string.
        base_dir: Base directory for resolving relative file refs. Defaults to
            the parent of the source file, or "." for strings.
        loading_files: Set of absolute file paths currently being loaded
            (for cycle detection). Uses frozenset for immutable branching.
        project_dir: Project root for bare flow name resolution. Auto-derived
            from base_dir by walking up to find .stepwise/ or flows/.

    Returns:
        A validated WorkflowDefinition.

    Raises:
        YAMLLoadError: If the YAML is malformed or the workflow is invalid.
    """
    # Parse YAML
    source_path: Path | None = None
    if isinstance(source, Path) or (isinstance(source, str) and not source.strip().startswith(("name:", "steps:"))):
        # Try as file path
        path = Path(source)
        if path.exists():
            raw = path.read_text()
            source_path = path
        elif isinstance(source, str) and ("\n" in source or ":" in source):
            # Might be inline YAML that doesn't start with name/steps
            raw = source
        else:
            raise YAMLLoadError([f"File not found: {source}"])
    else:
        raw = source

    # Determine base_dir, loading_files, and project_dir defaults
    if base_dir is None:
        base_dir = source_path.parent if source_path else Path(".")
    if loading_files is None:
        loading_files = frozenset({source_path.resolve()}) if source_path else frozenset()
    if project_dir is None:
        project_dir = _find_project_dir(base_dir)

    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise YAMLLoadError([f"YAML parse error: {e}"]) from e

    if not isinstance(data, dict):
        raise YAMLLoadError(["YAML root must be a mapping"])

    # Parse steps
    steps_data = data.get("steps")
    if not steps_data or not isinstance(steps_data, dict):
        raise YAMLLoadError(["Workflow must have a 'steps' mapping"])

    errors: list[str] = []
    steps: dict[str, StepDefinition] = {}

    for step_name, step_data in steps_data.items():
        if not isinstance(step_data, dict):
            errors.append(f"Step '{step_name}': must be a mapping")
            continue
        try:
            steps[step_name] = _parse_step(
                step_name, step_data, base_dir=base_dir, loading_files=loading_files,
                project_dir=project_dir,
            )
        except ValueError as e:
            errors.append(str(e))

    if errors:
        raise YAMLLoadError(errors)

    if errors:
        raise YAMLLoadError(errors)

    # Propagate flow-level containment to agent steps that don't override it
    flow_containment = data.get("containment")
    if flow_containment:
        for step in steps.values():
            executor_type = step.executor.type if step.executor else "script"
            if executor_type == "agent" and "containment" not in step.executor.config:
                step.executor.config["containment"] = flow_containment

    # Parse metadata from top-level fields
    metadata = _parse_metadata(data, source_path)

    # Require author when loading from a file
    if source_path is not None and not metadata.author:
        raise YAMLLoadError(["'author' is required in flow metadata"])

    # Parse config variables, input variables, and requirements
    try:
        config_vars = _parse_config(data)
    except ValueError as e:
        errors.append(str(e))
        config_vars = []

    try:
        input_vars = _parse_input_vars(data)
    except ValueError as e:
        errors.append(str(e))
        input_vars = []

    try:
        requires = _parse_requires(data)
    except ValueError as e:
        errors.append(str(e))
        requires = []

    if errors:
        raise YAMLLoadError(errors)

    # Load readme (inline or from README.md)
    flow_base_dir = source_path.parent if source_path else None
    readme = _load_readme(flow_base_dir, data)

    # M10: Record the source directory for script path resolution
    source_dir_str: str | None = None
    source_path_str: str | None = None
    if source_path is not None:
        source_dir_str = str(source_path.parent.resolve())
        source_path_str = str(source_path.resolve())

    workflow = WorkflowDefinition(
        steps=steps, metadata=metadata, source_dir=source_dir_str,
        source_path=source_path_str,
        config_vars=config_vars, input_vars=input_vars, requires=requires, readme=readme,
    )

    # Step 7 (§11): mark loop-back bindings + validate is_present: predicate refs.
    # Runs BEFORE workflow.validate() so the cycle detector and downstream
    # validators can read InputBinding.is_back_edge directly.
    step7_errors: list[str] = []
    _apply_step7_back_edge_pass(steps, step7_errors)
    if step7_errors:
        raise YAMLLoadError(step7_errors)

    # Run the standard workflow validation
    validation_errors = workflow.validate()
    if validation_errors:
        raise YAMLLoadError(validation_errors)

    # Validate named sessions and fork constraints
    session_errors: list[str] = []
    _validate_sessions(steps, session_errors, input_vars=input_vars)
    if session_errors:
        raise YAMLLoadError(session_errors)

    return workflow


def apply_fixes(file_path: str, fixes: list[dict]) -> str:
    """Apply auto-fixes to a flow YAML file using ruamel.yaml round-trip.

    Returns the updated YAML string. Does NOT write to disk (caller decides).
    """
    from io import StringIO

    from ruamel.yaml import YAML

    ryaml = YAML()
    ryaml.preserve_quotes = True

    with open(file_path) as f:
        data = ryaml.load(f)

    for fix in fixes:
        if fix["fix"] == "add_max_iterations":
            step = data["steps"][fix["step"]]
            exits = step.get("exits", [])
            if fix["rule_index"] < len(exits):
                exits[fix["rule_index"]]["max_iterations"] = fix["value"]

    buf = StringIO()
    ryaml.dump(data, buf)
    return buf.getvalue()


def load_workflow_string(yaml_str: str) -> WorkflowDefinition:
    """Convenience: load a workflow from a YAML string."""
    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as e:
        raise YAMLLoadError([f"YAML parse error: {e}"]) from e
    if not isinstance(data, dict):
        raise YAMLLoadError(["YAML root must be a mapping"])

    steps_data = data.get("steps")
    if not steps_data or not isinstance(steps_data, dict):
        raise YAMLLoadError(["Workflow must have a 'steps' mapping"])

    errors: list[str] = []
    steps: dict[str, StepDefinition] = {}

    for step_name, step_data in steps_data.items():
        if not isinstance(step_data, dict):
            errors.append(f"Step '{step_name}': must be a mapping")
            continue
        try:
            steps[step_name] = _parse_step(step_name, step_data)
        except ValueError as e:
            errors.append(str(e))

    # Parse config variables, input variables, and requirements
    try:
        config_vars = _parse_config(data)
    except ValueError as e:
        errors.append(str(e))
        config_vars = []

    try:
        input_vars = _parse_input_vars(data)
    except ValueError as e:
        errors.append(str(e))
        input_vars = []

    try:
        requires = _parse_requires(data)
    except ValueError as e:
        errors.append(str(e))
        requires = []

    if errors:
        raise YAMLLoadError(errors)

    # Load readme (inline only for string-based loading)
    readme = _load_readme(None, data)

    workflow = WorkflowDefinition(
        steps=steps,
        config_vars=config_vars, input_vars=input_vars, requires=requires, readme=readme,
    )

    # Step 7 (§11): back-edge marking + predicate validation (string-based loader path).
    step7_errors: list[str] = []
    _apply_step7_back_edge_pass(steps, step7_errors)
    if step7_errors:
        raise YAMLLoadError(step7_errors)

    validation_errors = workflow.validate()
    if validation_errors:
        raise YAMLLoadError(validation_errors)

    # Validate named sessions and fork constraints
    session_errors: list[str] = []
    _validate_sessions(steps, session_errors, input_vars=input_vars)
    if session_errors:
        raise YAMLLoadError(session_errors)

    return workflow
