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
    STAGED = "staged"
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
    type: str  # registered name: "script", "mock_llm", "external", etc.
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
    optional: bool = False  # weak reference — resolves to None if dep unavailable

    def to_dict(self) -> dict:
        d: dict = {
            "local_name": self.local_name,
            "source_step": self.source_step,
            "source_field": self.source_field,
        }
        if self.any_of_sources is not None:
            d["any_of_sources"] = [{"step": s, "field": f} for s, f in self.any_of_sources]
        if self.optional:
            d["optional"] = True
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
            optional=d.get("optional", False),
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


# ── Output Field Spec ─────────────────────────────────────────────────


VALID_FIELD_TYPES = {"str", "text", "number", "bool", "choice"}


@dataclass
class OutputFieldSpec:
    """Typed output field specification for external steps."""
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


# ── Config Variable ───────────────────────────────────────────────────


@dataclass
class ConfigVar:
    """Declared configurable variable for a flow (maps to $job.* inputs)."""
    name: str
    description: str = ""
    type: str = "str"        # str, text, number, bool, choice
    default: Any = None
    required: bool = True    # inferred True when no default provided
    example: str = ""
    options: list[str] | None = None   # choice type
    sensitive: bool = False  # mask value in output, suggest env var

    def to_dict(self) -> dict:
        d: dict = {"name": self.name}
        if self.description:
            d["description"] = self.description
        if self.type != "str":
            d["type"] = self.type
        if self.default is not None:
            d["default"] = self.default
        if not self.required:
            d["required"] = False
        if self.example:
            d["example"] = self.example
        if self.options is not None:
            d["options"] = self.options
        if self.sensitive:
            d["sensitive"] = True
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ConfigVar:
        typ = d.get("type", "str")
        if typ not in VALID_FIELD_TYPES:
            raise ValueError(f"ConfigVar '{d.get('name', '?')}': invalid type '{typ}' "
                             f"(valid: {', '.join(sorted(VALID_FIELD_TYPES))})")
        has_default = "default" in d
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            type=typ,
            default=d.get("default"),
            required=d.get("required", not has_default),
            example=d.get("example", ""),
            options=d.get("options"),
            sensitive=d.get("sensitive", False),
        )


# ── Flow Requirement ──────────────────────────────────────────────────


@dataclass
class FlowRequirement:
    """External tool or capability required by a flow."""
    name: str
    description: str = ""
    check: str = ""          # shell command to verify availability
    install: str = ""        # install command (e.g. "pip install camofox")
    url: str = ""            # link to docs or project page

    def to_dict(self) -> dict:
        d: dict = {"name": self.name}
        if self.description:
            d["description"] = self.description
        if self.check:
            d["check"] = self.check
        if self.install:
            d["install"] = self.install
        if self.url:
            d["url"] = self.url
        return d

    @classmethod
    def from_dict(cls, d: dict) -> FlowRequirement:
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            check=d.get("check", ""),
            install=d.get("install", ""),
            url=d.get("url", ""),
        )


# ── Cache Config ──────────────────────────────────────────────────────


def parse_duration(s: str) -> int | None:
    """Parse a duration string like '24h', '7d', '30m' into seconds."""
    import re
    m = re.match(r'^(\d+)([hdms])$', s)
    if not m:
        return None
    value = int(m.group(1))
    unit = m.group(2)
    if unit == 'h':
        return value * 3600
    elif unit == 'd':
        return value * 86400
    elif unit == 'm':
        return value * 60
    elif unit == 's':
        return value
    return None


@dataclass
class CacheConfig:
    """Per-step result caching configuration."""
    enabled: bool = True
    ttl: int | None = None  # seconds; None = use executor-type default
    key_extra: str | None = None  # extra string to include in cache key

    def to_dict(self) -> dict:
        d: dict = {"enabled": self.enabled}
        if self.ttl is not None:
            d["ttl"] = self.ttl
        if self.key_extra is not None:
            d["key_extra"] = self.key_extra
        return d

    @classmethod
    def from_dict(cls, d: dict) -> CacheConfig:
        return cls(
            enabled=d.get("enabled", True),
            ttl=d.get("ttl"),
            key_extra=d.get("key_extra"),
        )


# ── Step Definition ────────────────────────────────────────────────────


@dataclass
class StepDefinition:
    name: str
    outputs: list[str]  # declared output field names
    executor: ExecutorRef
    inputs: list[InputBinding] = field(default_factory=list)
    after: list[str] = field(default_factory=list)  # wait-for-completion deps
    exit_rules: list[ExitRule] = field(default_factory=list)
    idempotency: str = "idempotent"  # "idempotent" | "retriable_with_guard" | "non_retriable"
    description: str = ""  # optional human-readable description
    when: str | None = None  # activation condition evaluated against resolved inputs
    limits: StepLimits | None = None  # M4: cost/time/iteration limits
    for_each: ForEachSpec | None = None  # iteration over upstream list
    sub_flow: WorkflowDefinition | None = None  # embedded flow for for_each
    output_schema: dict[str, OutputFieldSpec] = field(default_factory=dict)
    chain: str | None = None  # M7a: context chain membership
    chain_label: str | None = None  # M7a: label shown in chain prefix
    continue_session: bool = False  # reuse agent session across loop iterations
    loop_prompt: str | None = None  # alternate prompt template on attempt > 1
    max_continuous_attempts: int | None = None  # circuit breaker for session reuse
    cache: CacheConfig | None = None  # opt-in result caching
    on_error: str = "fail"  # "fail" (default) | "continue" — step-level error policy
    derived_outputs: dict[str, str] = field(default_factory=dict)  # computed fields from artifact

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "outputs": self.outputs,
            "executor": self.executor.to_dict(),
            "inputs": [b.to_dict() for b in self.inputs],
            "after": self.after,
            "exit_rules": [r.to_dict() for r in self.exit_rules],
            "idempotency": self.idempotency,
        }
        if self.when is not None:
            d["when"] = self.when
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
        if self.continue_session:
            d["continue_session"] = True
        if self.loop_prompt is not None:
            d["loop_prompt"] = self.loop_prompt
        if self.max_continuous_attempts is not None:
            d["max_continuous_attempts"] = self.max_continuous_attempts
        if self.cache is not None:
            d["cache"] = self.cache.to_dict()
        if self.on_error != "fail":
            d["on_error"] = self.on_error
        if self.derived_outputs:
            d["derived_outputs"] = self.derived_outputs
        return d

    @classmethod
    def from_dict(cls, d: dict) -> StepDefinition:
        return cls(
            name=d["name"],
            outputs=d["outputs"],
            executor=ExecutorRef.from_dict(d["executor"]),
            inputs=[InputBinding.from_dict(b) for b in d.get("inputs", [])],
            after=d.get("after") if "after" in d else d.get("sequencing", []),
            exit_rules=[ExitRule.from_dict(r) for r in d.get("exit_rules", [])],
            idempotency=d.get("idempotency", "idempotent"),
            when=d.get("when"),
            output_schema={k: OutputFieldSpec.from_dict(v) for k, v in d.get("output_schema", {}).items()},
            limits=StepLimits.from_dict(d["limits"]) if d.get("limits") else None,
            for_each=ForEachSpec.from_dict(d["for_each"]) if d.get("for_each") else None,
            sub_flow=WorkflowDefinition.from_dict(d["sub_flow"]) if d.get("sub_flow") else None,
            chain=d.get("chain"),
            chain_label=d.get("chain_label"),
            continue_session=d.get("continue_session", False),
            loop_prompt=d.get("loop_prompt"),
            max_continuous_attempts=d.get("max_continuous_attempts"),
            cache=CacheConfig.from_dict(d["cache"]) if d.get("cache") else None,
            on_error=d.get("on_error", "fail"),
            derived_outputs=d.get("derived_outputs", {}),
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
    config_vars: list[ConfigVar] = field(default_factory=list)
    requires: list[FlowRequirement] = field(default_factory=list)
    readme: str = ""

    def _get_step_deps(self, step_name: str) -> set[str]:
        """Get direct dependencies of a step (via inputs and after)."""
        step = self.steps[step_name]
        deps: set[str] = set()
        for b in step.inputs:
            if b.source_step != "$job" and b.source_step in self.steps:
                deps.add(b.source_step)
            if b.any_of_sources:
                for src_step, _ in b.any_of_sources:
                    if src_step in self.steps:
                        deps.add(src_step)
        for seq_step in step.after:
            if seq_step in self.steps:
                deps.add(seq_step)
        return deps

    def _get_ancestors(self, step_name: str) -> set[str]:
        """Get all transitive ancestors of a step in the dependency graph."""
        ancestors: set[str] = set()
        frontier = set(self._get_step_deps(step_name))
        while frontier:
            dep = frontier.pop()
            if dep in ancestors:
                continue
            ancestors.add(dep)
            frontier.update(self._get_step_deps(dep) - ancestors)
        return ancestors

    def _is_dag_connected(self, step_name: str, target: str) -> bool:
        """Check if target is an ancestor or descendant of step_name."""
        return target in self._get_ancestors(step_name) or step_name in self._get_ancestors(target)

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
                        elif src_field.split(".")[0] not in (set(self.steps[src_step].outputs) | set(self.steps[src_step].derived_outputs)):
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
                    # Skip validation for _-prefixed fields (auto-injected metadata like _session_id)
                    all_outputs = set(source_step.outputs) | set(source_step.derived_outputs)
                    if not field_root.startswith("_") and field_root not in all_outputs:
                        errors.append(
                            f"Step '{name}': input binding references unknown field "
                            f"'{binding.source_field}' on step '{binding.source_step}'"
                        )

            # Check after references
            for seq_step in step.after:
                if seq_step not in step_names:
                    errors.append(
                        f"Step '{name}': after references unknown step '{seq_step}'"
                    )

            # Check duplicate local names and identifier validity
            import re
            _id_re = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
            local_names = [b.local_name for b in step.inputs]
            seen_locals: set[str] = set()
            for ln in local_names:
                if not _id_re.match(ln):
                    errors.append(
                        f"Step '{name}': input name '{ln}' is not a valid identifier"
                    )
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
                    errors.append(
                        f"Step '{name}': exit rule '{rule.name}' has 'advance' with 'target' — "
                        f"use step-level 'when' for conditional branching instead"
                    )

            # Check loop targets are connected in the DAG (ancestor, descendant, or self)
            for rule in step.exit_rules:
                target = rule.config.get("target")
                action = rule.config.get("action")
                if action == "loop" and target and target in step_names and target != name:
                    if not self._is_dag_connected(name, target):
                        errors.append(
                            f"Step '{name}': exit rule '{rule.name}' loops to '{target}' "
                            f"which is not connected in the dependency graph — "
                            f"loop targets must be the step itself or connected "
                            f"via inputs/after dependencies"
                        )

            # Check for_each steps
            if step.for_each:
                fe = step.for_each
                if fe.source_step not in step_names:
                    errors.append(
                        f"Step '{name}': for_each references unknown step '{fe.source_step}'"
                    )
                elif fe.source_field.split(".")[0] not in (set(self.steps[fe.source_step].outputs) | set(self.steps[fe.source_step].derived_outputs)):
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

    def warnings(self) -> list[str]:
        """Generate advisory warnings about potential issues (non-blocking)."""
        from itertools import product as _product
        from stepwise.yaml_loader import evaluate_exit_condition

        warns: list[str] = []

        for name, step in self.steps.items():
            if not step.exit_rules:
                continue

            # Unbounded loop detection
            for rule in step.exit_rules:
                if (rule.config.get("action") == "loop"
                        and not rule.config.get("max_iterations")):
                    target = rule.config.get("target", name)
                    warns.append(
                        f"\u26a0 Step '{name}': loop exit rule '{rule.name}' "
                        f"targeting '{target}' has no max_iterations"
                    )

            # Structural catch-all check
            conditions = [
                r.config.get("condition", "")
                for r in step.exit_rules if r.type == "expression"
            ]
            has_catch_all = any(
                c.strip().lower() == "true"
                for c in conditions
            ) or any(r.type == "always" for r in step.exit_rules)
            if not has_catch_all:
                warns.append(
                    f"\u26a0 Step '{name}': exit rules have no unconditional "
                    f"catch-all (when: 'True')"
                )

            # Output-space coverage for external steps
            if (step.executor.type == "external" and step.output_schema):
                domains: dict[str, list] = {}
                for field_name, spec in step.output_schema.items():
                    vals: list = []
                    if spec.type == "bool":
                        vals = [True, False]
                    elif spec.type == "choice" and spec.options:
                        vals = list(spec.options)
                    elif spec.type == "number":
                        vals = [spec.min if spec.min is not None else 0]
                    else:  # str, text
                        vals = ["sample"]
                    if not spec.required:
                        vals = vals + [None]
                    domains[field_name] = vals

                field_names = list(domains.keys())
                combos = list(_product(*[domains[f] for f in field_names]))

                if len(combos) > 256:
                    warns.append(
                        f"\u2139 Step '{name}': {len(combos)} output "
                        f"combinations — skipping coverage analysis"
                    )
                else:
                    for combo in combos:
                        outputs = dict(zip(field_names, combo))
                        covered = False
                        for rule in step.exit_rules:
                            if rule.type == "always":
                                covered = True
                                break
                            cond = rule.config.get("condition", "False")
                            try:
                                if evaluate_exit_condition(cond, outputs, 1):
                                    covered = True
                                    break
                            except Exception:
                                pass
                        if not covered:
                            warns.append(
                                f"\u26a0 Step '{name}': uncovered output "
                                f"combination {outputs}"
                            )

            # Coercion safety notes
            import re
            for rule in step.exit_rules:
                cond = rule.config.get("condition", "")
                if re.search(r"float\(|int\(|bool\(", cond):
                    warns.append(
                        f"\u2139 Step '{name}': exit rule '{rule.name}' "
                        f"uses type coercion — verify input is always "
                        f"the expected type"
                    )

        # Ungated post-loop step detection
        # Collect all steps involved in loops (both the step with the loop
        # exit rule and the loop target)
        loop_steps: set[str] = set()
        for name, step in self.steps.items():
            for rule in step.exit_rules:
                if rule.config.get("action") == "loop":
                    loop_steps.add(name)
                    loop_steps.add(rule.config.get("target", name))

        # Warn if a step has after on a looping step but no when condition
        for name, step in self.steps.items():
            if step.when:
                continue
            for seq in step.after:
                if seq in loop_steps:
                    warns.append(
                        f"\u26a0 Step '{name}': has 'after' on looping step "
                        f"'{seq}' but no 'when' condition — it will run after "
                        f"the first iteration, not after the loop exits"
                    )

        # Config variable cross-checks
        job_fields = {
            b.source_field
            for s in self.steps.values()
            for b in s.inputs
            if b.source_step == "$job"
        }

        if self.config_vars:
            config_names = {v.name for v in self.config_vars}

            for name in sorted(config_names - job_fields):
                warns.append(
                    f"\u26a0 Config variable '{name}' is declared but never referenced as $job.{name}"
                )
            for name in sorted(job_fields - config_names):
                warns.append(
                    f"\u2139 $job.{name} is not declared in config: block"
                )
            for v in self.config_vars:
                if v.required and v.default is None and not v.example:
                    warns.append(
                        f"\u2139 Config variable '{v.name}' has no default or example "
                        f"— users may not know what to provide"
                    )
        elif job_fields:
            for name in sorted(job_fields):
                warns.append(
                    f"\u2139 $job.{name} is used but no config: block is declared"
                )

        # Cache on uncacheable step types
        for name, step in self.steps.items():
            if step.cache is not None and step.cache.enabled:
                if step.executor.type == "external":
                    warns.append(
                        f"\u26a0 Step '{name}': cache has no effect on "
                        f"external steps"
                    )
                elif step.executor.type == "poll":
                    warns.append(
                        f"\u26a0 Step '{name}': cache has no effect on poll steps"
                    )
                elif (step.executor.type == "agent"
                      and step.executor.config.get("emit_flow")):
                    warns.append(
                        f"\u26a0 Step '{name}': cache has no effect on "
                        f"emit_flow agent steps"
                    )

        return warns

    def entry_steps(self) -> list[str]:
        """Steps with no dependencies (no inputs, after, or for_each source).

        Loop back-edges are excluded: if step X depends on step Y and Y has
        a loop exit targeting X, that dependency doesn't prevent X from being
        an entry step.
        """
        # Collect loop back-edges
        loop_back_edges: set[tuple[str, str]] = set()
        for sname, sdef in self.steps.items():
            for rule in sdef.exit_rules:
                if rule.config.get("action") == "loop" and rule.config.get("target"):
                    loop_back_edges.add((sname, rule.config["target"]))

        result = []
        for name, step in self.steps.items():
            has_step_deps = any(
                b.source_step != "$job"
                and (b.source_step, name) not in loop_back_edges
                and not b.optional
                for b in step.inputs if not b.any_of_sources
            )
            has_any_of_deps = any(
                b.any_of_sources and not b.optional
                for b in step.inputs
            )
            has_for_each_dep = step.for_each is not None
            if not has_step_deps and not has_any_of_deps and not step.after and not has_for_each_dep:
                result.append(name)
        return result

    def terminal_steps(self) -> list[str]:
        """Steps that nothing else depends on.

        Excludes loop-internal steps: steps that loop back to one of their
        own dependencies are intermediate loop participants, not terminals.
        Also excludes steps that are targeted by exit rules (escalation targets,
        loop targets) — these are control flow participants, not outputs.
        Self-deps and optional deps are excluded from the "depended on" set.
        """
        depended_on: set[str] = set()
        for step in self.steps.values():
            for binding in step.inputs:
                if binding.any_of_sources:
                    for src_step, _ in binding.any_of_sources:
                        # Skip self-deps and optional deps
                        if src_step != step.name and not binding.optional:
                            depended_on.add(src_step)
                elif binding.source_step != "$job":
                    # Skip self-deps and optional deps
                    if binding.source_step != step.name and not binding.optional:
                        depended_on.add(binding.source_step)
            for seq in step.after:
                depended_on.add(seq)
            if step.for_each:
                depended_on.add(step.for_each.source_step)

        # Steps targeted by exit rules (loop, escalate) from *other* steps are
        # control flow participants, not terminals. Self-loops don't disqualify
        # a step from being terminal.
        targeted_by_exits: set[str] = set()
        for step in self.steps.values():
            for rule in step.exit_rules:
                target = rule.config.get("target")
                if target and target != step.name and rule.config.get("action") in ("loop", "escalate"):
                    targeted_by_exits.add(target)

        terminals = []
        for name in self.steps:
            if name in depended_on:
                continue
            if name in targeted_by_exits:
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
            own_deps.update(step_def.after)
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
        """Detect cycles using Kahn's algorithm.

        Loop back-edges are excluded: when step X depends on step Y's output
        and Y has a loop exit rule targeting X, that edge is a valid loop
        pattern, not a structural cycle.
        """
        # Collect loop back-edges: (source_step, target_step) pairs where
        # source has a loop exit targeting target
        loop_back_edges: set[tuple[str, str]] = set()
        for name, step in self.steps.items():
            for exit_rule in step.exit_rules:
                action = exit_rule.config.get("action")
                target = exit_rule.config.get("target")
                if action == "loop" and target:
                    loop_back_edges.add((name, target))

        adj: dict[str, list[str]] = {name: [] for name in self.steps}
        in_degree: dict[str, int] = {name: 0 for name in self.steps}

        # Collect optional edges: bindings with optional=True
        optional_edges: set[tuple[str, str]] = set()
        for name, step in self.steps.items():
            for binding in step.inputs:
                if binding.optional and not binding.any_of_sources and binding.source_step != "$job":
                    optional_edges.add((binding.source_step, name))

        for name, step in self.steps.items():
            deps: set[str] = set()
            for binding in step.inputs:
                if binding.any_of_sources:
                    for src_step, _ in binding.any_of_sources:
                        deps.add(src_step)
                elif binding.source_step != "$job":
                    deps.add(binding.source_step)
            for seq in step.after:
                deps.add(seq)
            if step.for_each:
                deps.add(step.for_each.source_step)
            for dep in deps:
                # Skip loop back-edges: dep → name is a back-edge if dep
                # has a loop exit targeting name
                if (dep, name) in loop_back_edges:
                    continue
                # Skip optional edges — they don't create hard dependencies
                if (dep, name) in optional_edges:
                    continue
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
        if self.config_vars:
            d["config_vars"] = [v.to_dict() for v in self.config_vars]
        if self.requires:
            d["requires"] = [r.to_dict() for r in self.requires]
        if self.readme:
            d["readme"] = self.readme
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
        config_vars = [ConfigVar.from_dict(v) for v in d.get("config_vars", [])]
        requires = [FlowRequirement.from_dict(r) for r in d.get("requires", [])]
        return cls(
            steps=steps,
            metadata=metadata,
            chains=chains,
            source_dir=d.get("source_dir"),
            config_vars=config_vars,
            requires=requires,
            readme=d.get("readme", ""),
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
    mode: str  # "poll", "external", "timeout"
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
    pid: int | None = None
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
            "pid": self.pid,
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
            pid=d.get("pid"),
            started_at=datetime.fromisoformat(d["started_at"]) if d.get("started_at") else None,
            completed_at=datetime.fromisoformat(d["completed_at"]) if d.get("completed_at") else None,
        )


# ── Job Metadata ───────────────────────────────────────────────────────

VALID_SYS_KEYS: dict[str, type] = {
    "origin": str,
    "session_id": str,
    "parent_job_id": str,
    "root_job_id": str,
    "depth": int,
    "notify_url": str,
    "created_by": str,
}

METADATA_MAX_BYTES = 8192


def validate_job_metadata(metadata: dict) -> None:
    """Validate job metadata structure: sys/app namespaces, key whitelist, types, size."""
    if not isinstance(metadata, dict):
        raise ValueError(f"metadata must be a dict, got {type(metadata).__name__}")

    metadata.setdefault("sys", {})
    metadata.setdefault("app", {})

    if not isinstance(metadata["sys"], dict):
        raise ValueError("metadata.sys must be a dict")
    if not isinstance(metadata["app"], dict):
        raise ValueError("metadata.app must be a dict")

    import json
    serialized = json.dumps(metadata)
    if len(serialized) > METADATA_MAX_BYTES:
        raise ValueError(
            f"metadata exceeds {METADATA_MAX_BYTES} bytes "
            f"({len(serialized)} bytes serialized)"
        )

    for key, value in metadata["sys"].items():
        if key not in VALID_SYS_KEYS:
            raise ValueError(
                f"unknown sys key: {key!r} "
                f"(valid: {', '.join(sorted(VALID_SYS_KEYS))})"
            )
        expected_type = VALID_SYS_KEYS[key]
        if not isinstance(value, expected_type):
            raise ValueError(
                f"sys.{key} must be {expected_type.__name__}, "
                f"got {type(value).__name__}"
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
    name: str | None = None
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
    notify_url: str | None = None
    notify_context: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=lambda: {"sys": {}, "app": {}})
    job_group: str | None = None
    depends_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "objective": self.objective,
            "name": self.name,
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
            "metadata": self.metadata,
            "job_group": self.job_group,
            "depends_on": self.depends_on,
        }
        if self.notify_url:
            d["notify_url"] = self.notify_url
            d["notify_context"] = self.notify_context
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Job:
        return cls(
            id=d["id"],
            objective=d["objective"],
            name=d.get("name"),
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
            notify_url=d.get("notify_url"),
            notify_context=d.get("notify_context", {}),
            metadata=d.get("metadata", {"sys": {}, "app": {}}),
            job_group=d.get("job_group"),
            depends_on=d.get("depends_on", []),
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
