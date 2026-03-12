"""Load Stepwise workflows from YAML files.

Parses YAML workflow definitions into WorkflowDefinition objects.
See docs/yaml-format.md for the format specification.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from stepwise.models import (
    ChainConfig,
    DecoratorRef,
    ExecutorRef,
    ExitRule,
    FlowMetadata,
    ForEachSpec,
    InputBinding,
    RouteDefinition,
    RouteSpec,
    StepDefinition,
    StepLimits,
    WorkflowDefinition,
)

# Safe builtins for exit rule expression evaluation
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
}


class YAMLLoadError(Exception):
    """Raised when a YAML workflow file is invalid."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"YAML workflow errors: {'; '.join(errors)}")


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
        return bool(eval(condition, namespace))
    except Exception as e:
        raise ValueError(f"Exit condition '{condition}' failed: {e}") from e


def _parse_input_binding(local_name: str, source: str) -> InputBinding:
    """Parse 'step.field' or '$job.field' into an InputBinding."""
    if source.startswith("$job."):
        return InputBinding(local_name, "$job", source[5:])
    if source.startswith("$step."):
        # Magic binding — handled specially by the loader
        # For now, skip these as they're not real input bindings
        raise ValueError(f"$step.* bindings are not yet supported: {source}")
    parts = source.split(".", 1)
    if len(parts) != 2:
        raise ValueError(
            f"Invalid input source '{source}' for '{local_name}'. "
            f"Expected 'step_name.field_name' or '$job.field_name'"
        )
    return InputBinding(local_name, parts[0], parts[1])


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
    if executor_type == "human":
        prompt = prompt_from_file or step_data.get("prompt", "")
        if prompt:
            config["prompt"] = prompt
    elif executor_type == "mock_llm":
        # Pass through any config
        for k in ("failure_rate", "partial_rate", "garbage_rate"):
            if k in step_data:
                config[k] = step_data[k]
    elif executor_type == "agent":
        for k in ("prompt", "output_mode", "output_path"):
            if k in step_data:
                config[k] = step_data[k]
        if prompt_from_file:
            config["prompt"] = prompt_from_file
        if "prompt" not in config:
            raise ValueError(
                f"Step '{step_name}': Agent executor requires 'prompt'"
            )
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

        config: dict[str, Any] = {
            "condition": condition,
            "action": action,
        }
        if target:
            config["target"] = target

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


def _load_flow_from_file(
    file_ref: str,
    step_name: str,
    context: str,
    base_dir: Path | None,
    loading_files: frozenset[Path] | None,
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
    # Immutable set copy per branch — sibling routes can share files
    branch_files = (loading_files or frozenset()) | {abs_path}
    return load_workflow_yaml(
        str(abs_path),
        base_dir=abs_path.parent,
        loading_files=branch_files,
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


def _parse_for_each(
    step_data: dict,
    step_name: str,
    base_dir: Path | None = None,
    loading_files: frozenset[Path] | None = None,
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

    for_each_spec = ForEachSpec(
        source_step=source_step,
        source_field=source_field,
        item_var=item_var,
        on_error=on_error,
    )

    # Parse embedded flow (can be dict or file ref string)
    flow_data = step_data.get("flow")
    if not flow_data:
        raise ValueError(
            f"Step '{step_name}': for_each requires a 'flow' block"
        )

    # String ref support for for_each (file or registry)
    if isinstance(flow_data, str):
        if flow_data.startswith("@"):
            sub_flow = _load_flow_from_registry(
                flow_data, step_name, "for_each flow"
            )
            return for_each_spec, sub_flow
        if flow_data.endswith((".yaml", ".yml")):
            sub_flow = _load_flow_from_file(
                flow_data, step_name, "for_each flow", base_dir, loading_files
            )
            return for_each_spec, sub_flow
        raise ValueError(
            f"Step '{step_name}': for_each flow string must be a .yaml/.yml file path "
            f"or an @author:name registry ref"
        )

    if not isinstance(flow_data, dict):
        raise ValueError(
            f"Step '{step_name}': for_each requires a 'flow' block"
        )

    flow_steps_data = flow_data.get("steps")
    if not flow_steps_data or not isinstance(flow_steps_data, dict):
        raise ValueError(
            f"Step '{step_name}': for_each flow must have a 'steps' mapping"
        )

    flow_steps: dict[str, StepDefinition] = {}
    for sub_step_name, sub_step_data in flow_steps_data.items():
        if not isinstance(sub_step_data, dict):
            raise ValueError(
                f"Step '{step_name}' sub-flow step '{sub_step_name}': must be a mapping"
            )
        flow_steps[sub_step_name] = _parse_step(
            sub_step_name, sub_step_data, base_dir=base_dir, loading_files=loading_files
        )

    sub_flow = WorkflowDefinition(steps=flow_steps)

    return for_each_spec, sub_flow


def _parse_route(
    step_data: dict,
    step_name: str,
    outputs: list[str],
    base_dir: Path | None = None,
    loading_files: frozenset[Path] | None = None,
) -> RouteDefinition | None:
    """Parse route definitions from step YAML data.

    Returns None if no routes: key present.
    """
    routes_data = step_data.get("routes")
    if routes_data is None:
        return None

    if not isinstance(routes_data, dict):
        raise ValueError(f"Step '{step_name}': 'routes' must be a mapping")

    if not routes_data:
        raise ValueError(f"Step '{step_name}': routes must have at least one entry")

    named_routes: list[RouteSpec] = []
    default_route: RouteSpec | None = None

    for route_name, route_data in routes_data.items():
        if not isinstance(route_data, dict):
            raise ValueError(
                f"Step '{step_name}': route '{route_name}' must be a mapping"
            )

        is_default = route_name == "default"

        # Validate when expression
        when_expr = route_data.get("when")
        if is_default:
            if when_expr is not None:
                raise ValueError(
                    f"Step '{step_name}': 'default' route must not have a 'when' expression"
                )
            when_expr = None
        else:
            if when_expr is None or (isinstance(when_expr, str) and not when_expr.strip()):
                raise ValueError(
                    f"Step '{step_name}': route '{route_name}' must have a 'when' expression "
                    f"(only 'default' can omit it)"
                )

        # Parse flow source
        flow_source = route_data.get("flow")
        if flow_source is None:
            raise ValueError(
                f"Step '{step_name}': route '{route_name}' missing 'flow'"
            )

        flow: WorkflowDefinition | None = None
        flow_ref: str | None = None

        if isinstance(flow_source, dict):
            # Inline flow
            flow_steps_data = flow_source.get("steps")
            if not flow_steps_data or not isinstance(flow_steps_data, dict):
                raise ValueError(
                    f"Step '{step_name}' route '{route_name}': inline flow must have a 'steps' mapping"
                )
            flow_steps: dict[str, StepDefinition] = {}
            for sub_name, sub_data in flow_steps_data.items():
                if not isinstance(sub_data, dict):
                    raise ValueError(
                        f"Step '{step_name}' route '{route_name}' step '{sub_name}': must be a mapping"
                    )
                flow_steps[sub_name] = _parse_step(
                    sub_name, sub_data, base_dir=base_dir, loading_files=loading_files
                )
            flow = WorkflowDefinition(steps=flow_steps)
        elif isinstance(flow_source, str):
            if flow_source.startswith("@"):
                # Registry ref — resolve at parse time, bake inline
                flow = _load_flow_from_registry(
                    flow_source, step_name, f"route '{route_name}'"
                )
                flow_ref = flow_source  # Preserve original ref for provenance
            elif flow_source.endswith((".yaml", ".yml")):
                # File ref — load eagerly, bake inline
                flow = _load_flow_from_file(
                    flow_source, step_name, f"route '{route_name}'",
                    base_dir, loading_files,
                )
                flow_ref = flow_source  # Preserve original path for readability
            else:
                raise ValueError(
                    f"Step '{step_name}' route '{route_name}': flow must be an inline block, "
                    f"a .yaml/.yml file path, or an @author:name registry ref"
                )
        else:
            raise ValueError(
                f"Step '{step_name}' route '{route_name}': 'flow' must be a mapping or string"
            )

        route_spec = RouteSpec(
            name=route_name,
            when=when_expr,
            flow=flow,
            flow_ref=flow_ref,
        )

        if is_default:
            default_route = route_spec
        else:
            named_routes.append(route_spec)

    # Assemble: named routes in declaration order, default always last
    all_routes = named_routes
    if default_route:
        all_routes.append(default_route)

    return RouteDefinition(routes=all_routes)


def _parse_step(
    step_name: str,
    step_data: dict,
    base_dir: Path | None = None,
    loading_files: frozenset[Path] | None = None,
) -> StepDefinition:
    """Parse a single step from YAML data."""
    # Outputs
    outputs = step_data.get("outputs", [])
    if not isinstance(outputs, list):
        raise ValueError(f"Step '{step_name}': 'outputs' must be a list")

    # Check for routes (before for_each — mutual exclusivity checked later)
    route_def = _parse_route(step_data, step_name, outputs, base_dir, loading_files)

    if route_def:
        if step_data.get("for_each"):
            raise ValueError(
                f"Step '{step_name}': cannot combine for_each and routes"
            )
        if not outputs:
            raise ValueError(
                f"Step '{step_name}': route steps must declare outputs"
            )

        executor = ExecutorRef("route", {})

        # Parse inputs
        inputs_data = step_data.get("inputs", {})
        input_bindings: list[InputBinding] = []
        if isinstance(inputs_data, dict):
            for local_name, source in inputs_data.items():
                if isinstance(source, str):
                    try:
                        binding = _parse_input_binding(local_name, source)
                        input_bindings.append(binding)
                    except ValueError as e:
                        raise ValueError(f"Step '{step_name}': {e}") from e
                else:
                    raise ValueError(
                        f"Step '{step_name}': input '{local_name}' source must be a string, "
                        f"got {type(source).__name__}"
                    )

        sequencing = step_data.get("sequencing", [])
        if isinstance(sequencing, str):
            sequencing = [sequencing]

        return StepDefinition(
            name=step_name,
            outputs=outputs,
            executor=executor,
            inputs=input_bindings,
            sequencing=sequencing,
            route_def=route_def,
        )

    # Check for for_each (changes how the step is parsed)
    for_each_spec, sub_flow = _parse_for_each(
        step_data, step_name, base_dir, loading_files
    )

    if for_each_spec:
        # For-each step: executor is a no-op placeholder, the engine handles it
        executor = ExecutorRef("for_each", {})

        # Inputs are parent-level bindings passed to every iteration
        inputs_data = step_data.get("inputs", {})
        input_bindings: list[InputBinding] = []
        if isinstance(inputs_data, dict):
            for local_name, source in inputs_data.items():
                if isinstance(source, str):
                    try:
                        binding = _parse_input_binding(local_name, source)
                        input_bindings.append(binding)
                    except ValueError as e:
                        raise ValueError(f"Step '{step_name}': {e}") from e
                else:
                    raise ValueError(
                        f"Step '{step_name}': input '{local_name}' source must be a string, "
                        f"got {type(source).__name__}"
                    )

        # Outputs default to ["results"] for for_each steps
        if not outputs:
            outputs = ["results"]

        sequencing = step_data.get("sequencing", [])
        if isinstance(sequencing, str):
            sequencing = [sequencing]

        return StepDefinition(
            name=step_name,
            outputs=outputs,
            executor=executor,
            inputs=input_bindings,
            sequencing=sequencing,
            for_each=for_each_spec,
            sub_flow=sub_flow,
        )

    # Normal step parsing
    # Executor
    executor = _parse_executor(step_data, step_name, base_dir=base_dir)

    # Inputs
    inputs_data = step_data.get("inputs", {})
    input_bindings: list[InputBinding] = []
    if isinstance(inputs_data, dict):
        for local_name, source in inputs_data.items():
            if isinstance(source, str):
                try:
                    binding = _parse_input_binding(local_name, source)
                    input_bindings.append(binding)
                except ValueError as e:
                    raise ValueError(f"Step '{step_name}': {e}") from e
            else:
                raise ValueError(
                    f"Step '{step_name}': input '{local_name}' source must be a string, "
                    f"got {type(source).__name__}"
                )

    # Sequencing
    sequencing = step_data.get("sequencing", [])
    if isinstance(sequencing, str):
        sequencing = [sequencing]

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

    # Chain membership (M7a)
    chain = step_data.get("chain")
    chain_label = step_data.get("chain_label")

    return StepDefinition(
        name=step_name,
        outputs=outputs,
        executor=executor,
        inputs=input_bindings,
        sequencing=sequencing,
        exit_rules=exit_rules,
        idempotency=idempotency,
        limits=limits,
        chain=chain,
        chain_label=chain_label,
    )


def _parse_chains(data: dict) -> dict[str, ChainConfig]:
    """Parse chain definitions from top-level 'chains' block."""
    chains_data = data.get("chains")
    if not chains_data:
        return {}
    if not isinstance(chains_data, dict):
        raise ValueError("'chains' must be a mapping")

    chains: dict[str, ChainConfig] = {}
    for name, config_data in chains_data.items():
        if not isinstance(config_data, dict):
            raise ValueError(f"Chain '{name}': config must be a mapping")
        chains[name] = ChainConfig.from_dict(config_data)
    return chains


def _parse_metadata(data: dict, source_path: Path | None = None) -> FlowMetadata:
    """Extract FlowMetadata from YAML top-level fields."""
    name = data.get("name", "")
    if not name and source_path:
        # Default name from filename: "my-flow.flow.yaml" → "my-flow"
        stem = source_path.stem
        if stem.endswith(".flow"):
            stem = stem[:-5]
        name = stem

    return FlowMetadata(
        name=name,
        description=data.get("description", ""),
        author=data.get("author", ""),
        version=data.get("version", ""),
        tags=data.get("tags", []) if isinstance(data.get("tags"), list) else [],
    )


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
) -> WorkflowDefinition:
    """Load a WorkflowDefinition from a YAML file or string.

    Args:
        source: Path to a YAML file, or a YAML string.
        base_dir: Base directory for resolving relative file refs. Defaults to
            the parent of the source file, or "." for strings.
        loading_files: Set of absolute file paths currently being loaded
            (for cycle detection). Uses frozenset for immutable branching.

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

    # Determine base_dir and loading_files defaults
    if base_dir is None:
        base_dir = source_path.parent if source_path else Path(".")
    if loading_files is None:
        loading_files = frozenset({source_path.resolve()}) if source_path else frozenset()

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
                step_name, step_data, base_dir=base_dir, loading_files=loading_files
            )
        except ValueError as e:
            errors.append(str(e))

    if errors:
        raise YAMLLoadError(errors)

    # Parse chains (M7a)
    try:
        chains = _parse_chains(data)
    except ValueError as e:
        errors.append(str(e))
        chains = {}

    if errors:
        raise YAMLLoadError(errors)

    # Parse metadata from top-level fields
    metadata = _parse_metadata(data, source_path)

    # M10: Record the source directory for script path resolution
    source_dir_str: str | None = None
    if source_path is not None:
        source_dir_str = str(source_path.parent.resolve())

    workflow = WorkflowDefinition(
        steps=steps, metadata=metadata, chains=chains, source_dir=source_dir_str
    )

    # Run the standard workflow validation
    validation_errors = workflow.validate()
    if validation_errors:
        raise YAMLLoadError(validation_errors)

    return workflow


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

    # Parse chains (M7a)
    try:
        chains = _parse_chains(data)
    except ValueError as e:
        errors.append(str(e))
        chains = {}

    if errors:
        raise YAMLLoadError(errors)

    workflow = WorkflowDefinition(steps=steps, chains=chains)
    validation_errors = workflow.validate()
    if validation_errors:
        raise YAMLLoadError(validation_errors)

    return workflow
