"""Core data structures for Stepwise workflow engine.

Job, WorkflowDefinition, StepDefinition, StepRun, InputBinding,
ExecutorRef, DecoratorRef, ExitRule, HandoffEnvelope, Sidecar,
SubJobDefinition, WatchSpec, and all enums.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _gen_id(prefix: str = "id") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Enums ──────────────────────────────────────────────────────────────


class JobStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepRunStatus(Enum):
    RUNNING = "running"
    SUSPENDED = "suspended"
    DELEGATED = "delegated"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── Step Limits ───────────────────────────────────────────────────────


# ── Chain Config ─────────────────────────────────────────────────────


@dataclass
class ChainConfig:
    """Configuration for a named context chain."""
    max_tokens: int = 80000
    overflow: str = "drop_oldest"  # "drop_oldest" | "drop_middle"
    include_thinking: bool = False
    accumulation: str = "full"  # "full" | "latest"

    def to_dict(self) -> dict:
        return {
            "max_tokens": self.max_tokens,
            "overflow": self.overflow,
            "include_thinking": self.include_thinking,
            "accumulation": self.accumulation,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ChainConfig:
        return cls(
            max_tokens=d.get("max_tokens", 80000),
            overflow=d.get("overflow", "drop_oldest"),
            include_thinking=d.get("include_thinking", False),
            accumulation=d.get("accumulation", "full"),
        )


@dataclass
class StepLimits:
    """Cost and time limits enforced by the engine."""
    max_cost_usd: float | None = None
    max_duration_minutes: float | None = None
    max_iterations: int | None = None  # loop bound (cheap steps can iterate fast)

    def to_dict(self) -> dict:
        d: dict = {}
        if self.max_cost_usd is not None:
            d["max_cost_usd"] = self.max_cost_usd
        if self.max_duration_minutes is not None:
            d["max_duration_minutes"] = self.max_duration_minutes
        if self.max_iterations is not None:
            d["max_iterations"] = self.max_iterations
        return d

    @classmethod
    def from_dict(cls, d: dict) -> StepLimits:
        return cls(
            max_cost_usd=d.get("max_cost_usd"),
            max_duration_minutes=d.get("max_duration_minutes"),
            max_iterations=d.get("max_iterations"),
        )


# ── Serializable References ────────────────────────────────────────────


@dataclass
class DecoratorRef:
    type: str  # "timeout", "retry", "fallback"
    config: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"type": self.type, "config": self.config}

    @classmethod
    def from_dict(cls, d: dict) -> DecoratorRef:
        return cls(type=d["type"], config=d.get("config", {}))


@dataclass
class ExecutorRef:
    type: str  # registered name: "script", "mock_llm", "human", etc.
    config: dict = field(default_factory=dict)
    decorators: list[DecoratorRef] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "config": self.config,
            "decorators": [d.to_dict() for d in self.decorators],
        }

    def with_config(self, extra: dict) -> ExecutorRef:
        """Return a copy with additional config keys merged in."""
        return ExecutorRef(
            type=self.type,
            config={**self.config, **extra},
            decorators=self.decorators,
        )

    @classmethod
    def from_dict(cls, d: dict) -> ExecutorRef:
        return cls(
            type=d["type"],
            config=d.get("config", {}),
            decorators=[DecoratorRef.from_dict(dr) for dr in d.get("decorators", [])],
        )


@dataclass
class ExitRule:
    name: str
    type: str  # "field_match", "always"
    config: dict = field(default_factory=dict)
    priority: int = 0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "config": self.config,
            "priority": self.priority,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ExitRule:
        return cls(
            name=d["name"],
            type=d["type"],
            config=d.get("config", {}),
            priority=d.get("priority", 0),
        )


# ── Input Binding ──────────────────────────────────────────────────────


@dataclass
class InputBinding:
    local_name: str  # what the executor sees
    source_step: str  # which predecessor, or "$job"; empty string for any_of
    source_field: str  # which output field; empty string for any_of
    any_of_sources: list[tuple[str, str]] | None = None  # [(step, field), ...]

    def to_dict(self) -> dict:
        d: dict = {
            "local_name": self.local_name,
            "source_step": self.source_step,
            "source_field": self.source_field,
        }
        if self.any_of_sources is not None:
            d["any_of_sources"] = [{"step": s, "field": f} for s, f in self.any_of_sources]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> InputBinding:
        any_of = None
        if d.get("any_of_sources"):
            any_of = [(e["step"], e["field"]) for e in d["any_of_sources"]]
        return cls(
            local_name=d["local_name"],
            source_step=d["source_step"],
            source_field=d["source_field"],
            any_of_sources=any_of,
        )


# ── For-Each Spec ─────────────────────────────────────────────────────


@dataclass
class ForEachSpec:
    """Specifies iteration over a list output from an upstream step."""
    source_step: str  # which step produces the list
    source_field: str  # which output field is the list
    item_var: str = "item"  # variable name for current element (default: "item")
    on_error: str = "fail_fast"  # "fail_fast" | "continue"

    def to_dict(self) -> dict:
        return {
            "source_step": self.source_step,
            "source_field": self.source_field,
            "item_var": self.item_var,
            "on_error": self.on_error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ForEachSpec:
        return cls(
            source_step=d["source_step"],
            source_field=d["source_field"],
            item_var=d.get("item_var", "item"),
            on_error=d.get("on_error", "fail_fast"),
        )


# ── Route Spec ────────────────────────────────────────────────────────


@dataclass
class RouteSpec:
    """A single conditional route within a route step."""
    name: str                           # route key (e.g. "trivial", "default")
    when: str | None                    # expression string (None for default)
    flow: WorkflowDefinition | None     # inline sub-flow (parsed)
    flow_ref: str | None                # @author:name registry ref (preserved)

    def to_dict(self) -> dict:
        d: dict = {"name": self.name}
        if self.when is not None:
            d["when"] = self.when
        if self.flow is not None:
            d["flow"] = self.flow.to_dict()
        if self.flow_ref is not None:
            d["flow_ref"] = self.flow_ref
        return d

    @classmethod
    def from_dict(cls, d: dict) -> RouteSpec:
        return cls(
            name=d["name"],
            when=d.get("when"),
            flow=WorkflowDefinition.from_dict(d["flow"]) if d.get("flow") else None,
            flow_ref=d.get("flow_ref"),
        )


@dataclass
class RouteDefinition:
    """Conditional sub-flow dispatch for a route step."""
    routes: list[RouteSpec]  # ordered list of routes (default last)

    def to_dict(self) -> dict:
        return {"routes": [r.to_dict() for r in self.routes]}

    @classmethod
    def from_dict(cls, d: dict) -> RouteDefinition:
        return cls(
            routes=[RouteSpec.from_dict(r) for r in d.get("routes", [])],
        )


# ── Output Field Spec ─────────────────────────────────────────────────


VALID_FIELD_TYPES = {"str", "text", "number", "bool", "choice"}


@dataclass
class OutputFieldSpec:
    """Typed output field specification for human steps."""
    type: str = "str"        # str, text, number, bool, choice
    required: bool = True
    default: Any = None
    description: str = ""
    options: list[str] | None = None   # choice
    multiple: bool = False             # choice: multi-select
    min: float | None = None           # number
    max: float | None = None           # number

    def to_dict(self) -> dict:
        d: dict = {}
        if self.type != "str":
            d["type"] = self.type
        if not self.required:
            d["required"] = False
        if self.default is not None:
            d["default"] = self.default
        if self.description:
            d["description"] = self.description
        if self.options is not None:
            d["options"] = self.options
        if self.multiple:
            d["multiple"] = True
        if self.min is not None:
            d["min"] = self.min
        if self.max is not None:
            d["max"] = self.max
        return d

    @classmethod
    def from_dict(cls, d: dict) -> OutputFieldSpec:
        return cls(
            type=d.get("type", "str"),
            required=d.get("required", True),
            default=d.get("default"),
            description=d.get("description", ""),
            options=d.get("options"),
            multiple=d.get("multiple", False),
            min=d.get("min"),
            max=d.get("max"),
        )


# ── Step Definition ────────────────────────────────────────────────────


@dataclass
class StepDefinition:
    name: str
    outputs: list[str]  # declared output field names
    executor: ExecutorRef
    inputs: list[InputBinding] = field(default_factory=list)
    sequencing: list[str] = field(default_factory=list)  # wait-for-completion deps
    exit_rules: list[ExitRule] = field(default_factory=list)
    idempotency: str = "idempotent"  # "idempotent" | "retriable_with_guard" | "non_retriable"
    description: str = ""  # optional human-readable description
    limits: StepLimits | None = None  # M4: cost/time/iteration limits
    for_each: ForEachSpec | None = None  # iteration over upstream list
    sub_flow: WorkflowDefinition | None = None  # embedded flow for for_each
    output_schema: dict[str, OutputFieldSpec] = field(default_factory=dict)
    chain: str | None = None  # M7a: context chain membership
    chain_label: str | None = None  # M7a: label shown in chain prefix
    route_def: RouteDefinition | None = None  # M8: conditional sub-flow dispatch

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "outputs": self.outputs,
            "executor": self.executor.to_dict(),
            "inputs": [b.to_dict() for b in self.inputs],
            "sequencing": self.sequencing,
            "exit_rules": [r.to_dict() for r in self.exit_rules],
            "idempotency": self.idempotency,
        }
        if self.output_schema:
            d["output_schema"] = {k: v.to_dict() for k, v in self.output_schema.items()}
        if self.limits:
            d["limits"] = self.limits.to_dict()
        if self.for_each:
            d["for_each"] = self.for_each.to_dict()
        if self.sub_flow:
            d["sub_flow"] = self.sub_flow.to_dict()
        if self.chain:
            d["chain"] = self.chain
        if self.chain_label:
            d["chain_label"] = self.chain_label
        if self.route_def:
            d["route_def"] = self.route_def.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> StepDefinition:
        return cls(
            name=d["name"],
            outputs=d["outputs"],
            executor=ExecutorRef.from_dict(d["executor"]),
            inputs=[InputBinding.from_dict(b) for b in d.get("inputs", [])],
            sequencing=d.get("sequencing", []),
            exit_rules=[ExitRule.from_dict(r) for r in d.get("exit_rules", [])],
            idempotency=d.get("idempotency", "idempotent"),
            output_schema={k: OutputFieldSpec.from_dict(v) for k, v in d.get("output_schema", {}).items()},
            limits=StepLimits.from_dict(d["limits"]) if d.get("limits") else None,
            for_each=ForEachSpec.from_dict(d["for_each"]) if d.get("for_each") else None,
            sub_flow=WorkflowDefinition.from_dict(d["sub_flow"]) if d.get("sub_flow") else None,
            chain=d.get("chain"),
            chain_label=d.get("chain_label"),
            route_def=RouteDefinition.from_dict(d["route_def"]) if d.get("route_def") else None,
        )


# ── Workflow Definition ────────────────────────────────────────────────


@dataclass
class FlowMetadata:
    """Optional metadata parsed from flow YAML header."""
    name: str = ""
    description: str = ""
    author: str = ""
    version: str = ""
    tags: list[str] = field(default_factory=list)
    forked_from: str = ""  # e.g. "@bob:code-review" — provenance for forked flows

    def to_dict(self) -> dict:
        d: dict = {}
        if self.name:
            d["name"] = self.name
        if self.description:
            d["description"] = self.description
        if self.author:
            d["author"] = self.author
        if self.version:
            d["version"] = self.version
        if self.tags:
            d["tags"] = self.tags
        if self.forked_from:
            d["forked_from"] = self.forked_from
        return d

    @classmethod
    def from_dict(cls, d: dict) -> FlowMetadata:
        return cls(
            name=d.get("name", ""),
            description=d.get("description", ""),
            author=d.get("author", ""),
            version=d.get("version", ""),
            tags=d.get("tags", []),
            forked_from=d.get("forked_from", ""),
        )


@dataclass
class WorkflowDefinition:
    steps: dict[str, StepDefinition] = field(default_factory=dict)
    metadata: FlowMetadata = field(default_factory=FlowMetadata)
    chains: dict[str, ChainConfig] = field(default_factory=dict)  # M7a
    source_dir: str | None = None  # M10: directory containing the flow file

    def validate(self) -> list[str]:
        """Validate the workflow definition. Returns list of errors."""
        errors: list[str] = []
        step_names = set(self.steps.keys())

        if not step_names:
            errors.append("Workflow has no steps")
            return errors

        # Check input binding sources
        for name, step in self.steps.items():
            for binding in step.inputs:
                if binding.any_of_sources is not None:
                    # Validate any_of bindings
                    if len(binding.any_of_sources) < 2:
                        errors.append(
                            f"Step '{name}': input '{binding.local_name}' any_of must have >= 2 sources"
                        )
                    for src_step, src_field in binding.any_of_sources:
                        if src_step not in step_names:
                            errors.append(
                                f"Step '{name}': input '{binding.local_name}' any_of references "
                                f"unknown step '{src_step}'"
                            )
                        elif src_field.split(".")[0] not in self.steps[src_step].outputs:
                            errors.append(
                                f"Step '{name}': input '{binding.local_name}' any_of references "
                                f"unknown field '{src_field}' on step '{src_step}'"
                            )
                elif binding.source_step != "$job" and binding.source_step not in step_names:
                    errors.append(
                        f"Step '{name}': input binding references unknown step '{binding.source_step}'"
                    )
                elif binding.source_step != "$job":
                    source_step = self.steps[binding.source_step]
                    # Support nested field references: "hero.headline" checks "hero" exists
                    field_root = binding.source_field.split(".")[0]
                    if field_root not in source_step.outputs:
                        errors.append(
                            f"Step '{name}': input binding references unknown field "
                            f"'{binding.source_field}' on step '{binding.source_step}'"
                        )

            # Check sequencing references
            for seq_step in step.sequencing:
                if seq_step not in step_names:
                    errors.append(
                        f"Step '{name}': sequencing references unknown step '{seq_step}'"
                    )

            # Check duplicate local names
            local_names = [b.local_name for b in step.inputs]
            seen_locals: set[str] = set()
            for ln in local_names:
                if ln in seen_locals:
                    errors.append(
                        f"Step '{name}': duplicate local_name '{ln}' in inputs"
                    )
                seen_locals.add(ln)

            # Check duplicate outputs
            seen_outputs: set[str] = set()
            for out in step.outputs:
                if out in seen_outputs:
                    errors.append(
                        f"Step '{name}': duplicate output '{out}'"
                    )
                seen_outputs.add(out)

            # Check exit rule targets
            for rule in step.exit_rules:
                target = rule.config.get("target")
                action = rule.config.get("action")
                if action == "loop" and target:
                    if target not in step_names:
                        errors.append(
                            f"Step '{name}': exit rule '{rule.name}' loop target "
                            f"'{target}' is not a valid step"
                        )
                if action == "advance" and target:
                    if target not in step_names:
                        errors.append(
                            f"Step '{name}': exit rule '{rule.name}' advance target "
                            f"'{target}' is not a valid step"
                        )

            # Check for_each steps
            if step.for_each:
                fe = step.for_each
                if fe.source_step not in step_names:
                    errors.append(
                        f"Step '{name}': for_each references unknown step '{fe.source_step}'"
                    )
                elif fe.source_field.split(".")[0] not in self.steps[fe.source_step].outputs:
                    errors.append(
                        f"Step '{name}': for_each references unknown field "
                        f"'{fe.source_field}' on step '{fe.source_step}'"
                    )
                if not step.sub_flow:
                    errors.append(
                        f"Step '{name}': for_each requires a 'flow' block"
                    )
                elif step.sub_flow:
                    sub_errors = step.sub_flow.validate()
                    for se in sub_errors:
                        errors.append(f"Step '{name}' sub-flow: {se}")
                if fe.on_error not in ("fail_fast", "continue"):
                    errors.append(
                        f"Step '{name}': for_each on_error must be 'fail_fast' or 'continue', "
                        f"got '{fe.on_error}'"
                    )

            # Check route steps
            if step.route_def:
                rd = step.route_def
                if not rd.routes:
                    errors.append(f"Step '{name}': routes must have at least one entry")
                default_count = sum(1 for r in rd.routes if r.when is None)
                if default_count > 1:
                    errors.append(f"Step '{name}': multiple default routes")
                for route in rd.routes:
                    if route.when is None and route.name != "default":
                        errors.append(
                            f"Step '{name}': route '{route.name}' must have a 'when' expression "
                            f"(only 'default' can omit it)"
                        )
                    if route.flow is None and route.flow_ref is None:
                        errors.append(
                            f"Step '{name}': route '{route.name}' missing 'flow'"
                        )
                    if route.flow:
                        sub_errors = route.flow.validate()
                        for se in sub_errors:
                            errors.append(f"Step '{name}' route '{route.name}': {se}")
                        terms = route.flow.terminal_steps()
                        if not terms and step.outputs:
                            errors.append(
                                f"Step '{name}' route '{route.name}': sub-flow has no terminal "
                                f"steps but route requires outputs"
                            )
                        for tname in terms:
                            term_outputs = set(route.flow.steps[tname].outputs)
                            missing = set(step.outputs) - term_outputs
                            if missing:
                                errors.append(
                                    f"Step '{name}' route '{route.name}': terminal step "
                                    f"'{tname}' missing outputs {sorted(missing)}"
                                )
                if step.for_each:
                    errors.append(
                        f"Step '{name}': cannot combine for_each and routes"
                    )
                if not step.outputs:
                    errors.append(
                        f"Step '{name}': route steps must declare outputs"
                    )
                for inp in step.inputs:
                    if inp.local_name == "attempt":
                        errors.append(
                            f"Step '{name}': 'attempt' is a reserved name and cannot "
                            f"be used as an input binding"
                        )

        # Check chain definitions
        for chain_name, chain_config in self.chains.items():
            members = [n for n, s in self.steps.items() if s.chain == chain_name]
            if len(members) < 2:
                errors.append(
                    f"Chain '{chain_name}': must have at least 2 members, "
                    f"found {len(members)}"
                )
            if chain_config.overflow not in ("drop_oldest", "drop_middle"):
                errors.append(
                    f"Chain '{chain_name}': overflow must be 'drop_oldest' or "
                    f"'drop_middle', got '{chain_config.overflow}'"
                )
            if chain_config.accumulation not in ("full", "latest"):
                errors.append(
                    f"Chain '{chain_name}': accumulation must be 'full' or "
                    f"'latest', got '{chain_config.accumulation}'"
                )
            if chain_config.max_tokens < 1:
                errors.append(
                    f"Chain '{chain_name}': max_tokens must be positive"
                )

        # Check step chain references are valid
        for name, step in self.steps.items():
            if step.chain and step.chain not in self.chains:
                errors.append(
                    f"Step '{name}': references undefined chain '{step.chain}'"
                )

        # Check for cycles
        if not errors:
            cycle_errors = self._detect_cycles()
            errors.extend(cycle_errors)

        # Check at least one entry step
        entry = self.entry_steps()
        if not entry and not errors:
            errors.append("Workflow has no entry steps (all steps have dependencies)")

        # Check at least one terminal step
        terminal = self.terminal_steps()
        if not terminal and not errors:
            errors.append("Workflow has no terminal steps")

        return errors

    def entry_steps(self) -> list[str]:
        """Steps with no dependencies (no inputs, sequencing, or for_each source)."""
        result = []
        for name, step in self.steps.items():
            has_step_deps = any(
                b.source_step != "$job" for b in step.inputs if not b.any_of_sources
            )
            has_any_of_deps = any(b.any_of_sources for b in step.inputs)
            has_for_each_dep = step.for_each is not None
            if not has_step_deps and not has_any_of_deps and not step.sequencing and not has_for_each_dep:
                result.append(name)
        return result

    def terminal_steps(self) -> list[str]:
        """Steps that nothing else depends on.

        Excludes loop-internal steps: steps that loop back to one of their
        own dependencies are intermediate loop participants, not terminals.
        """
        depended_on: set[str] = set()
        for step in self.steps.values():
            for binding in step.inputs:
                if binding.any_of_sources:
                    for src_step, _ in binding.any_of_sources:
                        depended_on.add(src_step)
                elif binding.source_step != "$job":
                    depended_on.add(binding.source_step)
            for seq in step.sequencing:
                depended_on.add(seq)
            if step.for_each:
                depended_on.add(step.for_each.source_step)

        terminals = []
        for name in self.steps:
            if name in depended_on:
                continue
            # Exclude loop-internal steps (loop back to own dep)
            step_def = self.steps[name]
            own_deps: set[str] = set()
            for b in step_def.inputs:
                if b.any_of_sources:
                    for src, _ in b.any_of_sources:
                        own_deps.add(src)
                elif b.source_step != "$job":
                    own_deps.add(b.source_step)
            own_deps.update(step_def.sequencing)
            is_loop_internal = any(
                rule.config.get("action") == "loop"
                and rule.type == "always"  # Only unconditional loops
                and rule.config.get("target", name) in own_deps
                for rule in step_def.exit_rules
            )
            if not is_loop_internal:
                terminals.append(name)
        return terminals

    def _detect_cycles(self) -> list[str]:
        """Detect cycles using Kahn's algorithm."""
        adj: dict[str, list[str]] = {name: [] for name in self.steps}
        in_degree: dict[str, int] = {name: 0 for name in self.steps}

        for name, step in self.steps.items():
            deps: set[str] = set()
            for binding in step.inputs:
                if binding.any_of_sources:
                    for src_step, _ in binding.any_of_sources:
                        deps.add(src_step)
                elif binding.source_step != "$job":
                    deps.add(binding.source_step)
            for seq in step.sequencing:
                deps.add(seq)
            if step.for_each:
                deps.add(step.for_each.source_step)
            for dep in deps:
                if dep in adj:
                    adj[dep].append(name)
                    in_degree[name] += 1

        queue = deque(n for n, d in in_degree.items() if d == 0)
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited != len(self.steps):
            remaining = [n for n, d in in_degree.items() if d > 0]
            return [f"Cycle detected involving steps: {', '.join(sorted(remaining))}"]
        return []

    def to_dict(self) -> dict:
        d: dict = {
            "steps": {name: step.to_dict() for name, step in self.steps.items()},
        }
        meta = self.metadata.to_dict()
        if meta:
            d["metadata"] = meta
        if self.chains:
            d["chains"] = {n: c.to_dict() for n, c in self.chains.items()}
        if self.source_dir is not None:
            d["source_dir"] = self.source_dir
        return d

    @classmethod
    def from_dict(cls, d: dict) -> WorkflowDefinition:
        steps = {}
        for name, step_d in d.get("steps", {}).items():
            steps[name] = StepDefinition.from_dict(step_d)
        metadata = FlowMetadata.from_dict(d.get("metadata", {}))
        chains = {}
        for name, chain_d in d.get("chains", {}).items():
            chains[name] = ChainConfig.from_dict(chain_d)
        return cls(
            steps=steps,
            metadata=metadata,
            chains=chains,
            source_dir=d.get("source_dir"),
        )


# ── Handoff Envelope ───────────────────────────────────────────────────


@dataclass
class Sidecar:
    decisions_made: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    constraints_discovered: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "decisions_made": self.decisions_made,
            "assumptions": self.assumptions,
            "open_questions": self.open_questions,
            "constraints_discovered": self.constraints_discovered,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Sidecar:
        return cls(
            decisions_made=d.get("decisions_made", []),
            assumptions=d.get("assumptions", []),
            open_questions=d.get("open_questions", []),
            constraints_discovered=d.get("constraints_discovered", []),
        )


@dataclass
class HandoffEnvelope:
    artifact: dict
    sidecar: Sidecar = field(default_factory=Sidecar)
    executor_meta: dict = field(default_factory=dict)
    workspace: str = ""
    timestamp: datetime = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "artifact": self.artifact,
            "sidecar": self.sidecar.to_dict(),
            "executor_meta": self.executor_meta,
            "workspace": self.workspace,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> HandoffEnvelope:
        return cls(
            artifact=d["artifact"],
            sidecar=Sidecar.from_dict(d.get("sidecar", {})),
            executor_meta=d.get("executor_meta", {}),
            workspace=d.get("workspace", ""),
            timestamp=datetime.fromisoformat(d["timestamp"]) if d.get("timestamp") else _now(),
        )


# ── Watch Spec ─────────────────────────────────────────────────────────


@dataclass
class WatchSpec:
    mode: str  # "poll", "human", "timeout"
    config: dict = field(default_factory=dict)
    fulfillment_outputs: list[str] = field(default_factory=list)
    output_schema: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "mode": self.mode,
            "config": self.config,
            "fulfillment_outputs": self.fulfillment_outputs,
        }
        if self.output_schema:
            d["output_schema"] = self.output_schema
        return d

    @classmethod
    def from_dict(cls, d: dict) -> WatchSpec:
        return cls(
            mode=d["mode"],
            config=d.get("config", {}),
            fulfillment_outputs=d.get("fulfillment_outputs", []),
            output_schema=d.get("output_schema", {}),
        )


# ── Sub-Job Definition ─────────────────────────────────────────────────


@dataclass
class SubJobDefinition:
    objective: str
    workflow: WorkflowDefinition
    config: JobConfig | None = None

    def to_dict(self) -> dict:
        return {
            "objective": self.objective,
            "workflow": self.workflow.to_dict(),
            "config": self.config.to_dict() if self.config else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> SubJobDefinition:
        return cls(
            objective=d["objective"],
            workflow=WorkflowDefinition.from_dict(d["workflow"]),
            config=JobConfig.from_dict(d["config"]) if d.get("config") else None,
        )


# ── Step Run ───────────────────────────────────────────────────────────


@dataclass
class StepRun:
    id: str
    job_id: str
    step_name: str
    attempt: int
    status: StepRunStatus
    inputs: dict | None = None
    dep_run_ids: dict[str, str] | None = None  # {dep_step: run_id}
    result: HandoffEnvelope | None = None
    error: str | None = None
    error_category: str | None = None  # M4: typed failure classification
    executor_state: dict | None = None
    watch: WatchSpec | None = None
    sub_job_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "step_name": self.step_name,
            "attempt": self.attempt,
            "status": self.status.value,
            "inputs": self.inputs,
            "dep_run_ids": self.dep_run_ids,
            "result": self.result.to_dict() if self.result else None,
            "error": self.error,
            "error_category": self.error_category,
            "executor_state": self.executor_state,
            "watch": self.watch.to_dict() if self.watch else None,
            "sub_job_id": self.sub_job_id,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> StepRun:
        return cls(
            id=d["id"],
            job_id=d["job_id"],
            step_name=d["step_name"],
            attempt=d["attempt"],
            status=StepRunStatus(d["status"]),
            inputs=d.get("inputs"),
            dep_run_ids=d.get("dep_run_ids"),
            result=HandoffEnvelope.from_dict(d["result"]) if d.get("result") else None,
            error=d.get("error"),
            error_category=d.get("error_category"),
            executor_state=d.get("executor_state"),
            watch=WatchSpec.from_dict(d["watch"]) if d.get("watch") else None,
            sub_job_id=d.get("sub_job_id"),
            started_at=datetime.fromisoformat(d["started_at"]) if d.get("started_at") else None,
            completed_at=datetime.fromisoformat(d["completed_at"]) if d.get("completed_at") else None,
        )


# ── Job Config ─────────────────────────────────────────────────────────


@dataclass
class JobConfig:
    max_sub_job_depth: int = 5
    timeout_minutes: int | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "max_sub_job_depth": self.max_sub_job_depth,
            "timeout_minutes": self.timeout_minutes,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> JobConfig:
        return cls(
            max_sub_job_depth=d.get("max_sub_job_depth", 5),
            timeout_minutes=d.get("timeout_minutes"),
            metadata=d.get("metadata", {}),
        )


# ── Job ────────────────────────────────────────────────────────────────


@dataclass
class Job:
    id: str
    objective: str
    workflow: WorkflowDefinition
    status: JobStatus = JobStatus.PENDING
    inputs: dict = field(default_factory=dict)
    parent_job_id: str | None = None
    parent_step_run_id: str | None = None
    workspace_path: str = ""
    config: JobConfig = field(default_factory=JobConfig)
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    created_by: str = "server"
    runner_pid: int | None = None
    heartbeat_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "objective": self.objective,
            "workflow": self.workflow.to_dict(),
            "status": self.status.value,
            "inputs": self.inputs,
            "parent_job_id": self.parent_job_id,
            "parent_step_run_id": self.parent_step_run_id,
            "workspace_path": self.workspace_path,
            "config": self.config.to_dict(),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "created_by": self.created_by,
            "runner_pid": self.runner_pid,
            "heartbeat_at": self.heartbeat_at.isoformat() if self.heartbeat_at else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Job:
        return cls(
            id=d["id"],
            objective=d["objective"],
            workflow=WorkflowDefinition.from_dict(d["workflow"]),
            status=JobStatus(d["status"]),
            inputs=d.get("inputs", {}),
            parent_job_id=d.get("parent_job_id"),
            parent_step_run_id=d.get("parent_step_run_id"),
            workspace_path=d.get("workspace_path", ""),
            config=JobConfig.from_dict(d["config"]) if d.get("config") else JobConfig(),
            created_at=datetime.fromisoformat(d["created_at"]) if d.get("created_at") else _now(),
            updated_at=datetime.fromisoformat(d["updated_at"]) if d.get("updated_at") else _now(),
            created_by=d.get("created_by", "server"),
            runner_pid=d.get("runner_pid"),
            heartbeat_at=datetime.fromisoformat(d["heartbeat_at"]) if d.get("heartbeat_at") else None,
        )


# ── Event ──────────────────────────────────────────────────────────────


@dataclass
class Event:
    id: str
    job_id: str
    timestamp: datetime
    type: str
    data: dict = field(default_factory=dict)
    is_effector: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "timestamp": self.timestamp.isoformat(),
            "type": self.type,
            "data": self.data,
            "is_effector": self.is_effector,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Event:
        return cls(
            id=d["id"],
            job_id=d["job_id"],
            timestamp=datetime.fromisoformat(d["timestamp"]),
            type=d["type"],
            data=d.get("data", {}),
            is_effector=d.get("is_effector", False),
        )
