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
    ConfigVar,
    DecoratorRef,
    ExecutorRef,
    ExitRule,
    FlowMetadata,
    FlowRequirement,
    ForEachSpec,
    InputBinding,
    OutputFieldSpec,
    StepDefinition,
    StepLimits,
    VALID_FIELD_TYPES,
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
    # JavaScript/YAML-friendly aliases
    "true": True,
    "false": False,
    "null": None,
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


def evaluate_when_condition(condition: str, inputs: dict) -> bool:
    """Evaluate a step-level `when` condition against resolved inputs.

    Input names are directly available in the namespace (e.g., `status == 'pass'`).
    Returns False on NameError/AttributeError/TypeError (missing input).
    """
    namespace: dict = {"__builtins__": SAFE_BUILTINS}
    for k, v in inputs.items():
        namespace[k] = _DotDict(v) if isinstance(v, dict) else v
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
    if executor_type == "human":
        prompt = prompt_from_file or step_data.get("prompt", "")
        if prompt:
            config["prompt"] = prompt
    elif executor_type == "mock_llm":
        # Pass through any config
        for k in ("failure_rate", "partial_rate", "latency_range", "responses"):
            if k in step_data:
                config[k] = step_data[k]
    elif executor_type == "agent":
        for k in ("prompt", "output_mode", "output_path", "emit_flow", "working_dir"):
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
        return WorkflowDefinition(steps=flow_steps), None

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

        sequencing = step_data.get("sequencing", [])
        if isinstance(sequencing, str):
            sequencing = [sequencing]

        return StepDefinition(
            name=step_name,
            description=step_data.get("description", ""),
            outputs=outputs,
            output_schema=output_schema,
            executor=ExecutorRef("sub_flow", {"flow_ref": flow_ref} if flow_ref else {}),
            inputs=input_bindings,
            sequencing=sequencing,
            sub_flow=sub_flow,
        )

    # Check for for_each (changes how the step is parsed)
    for_each_spec, sub_flow = _parse_for_each(
        step_data, step_name, base_dir, loading_files, project_dir
    )

    if for_each_spec:
        # For-each step: executor is a no-op placeholder, the engine handles it
        executor = ExecutorRef("for_each", {})

        input_bindings = _parse_inputs(step_data.get("inputs", {}), step_name)

        # Outputs default to ["results"] for for_each steps
        if not outputs:
            outputs = ["results"]

        sequencing = step_data.get("sequencing", [])
        if isinstance(sequencing, str):
            sequencing = [sequencing]

        return StepDefinition(
            name=step_name,
            description=step_data.get("description", ""),
            outputs=outputs,
            output_schema=output_schema,
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
    input_bindings = _parse_inputs(step_data.get("inputs", {}), step_name)

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

    # Step-level when condition (pure-pull branching)
    when_condition = step_data.get("when")

    # Chain membership (M7a)
    chain = step_data.get("chain")
    chain_label = step_data.get("chain_label")

    # Session continuity
    continue_session = step_data.get("continue_session", False)
    loop_prompt_raw = step_data.get("loop_prompt")
    loop_prompt = None
    if loop_prompt_raw is not None:
        loop_prompt = str(loop_prompt_raw)
    max_continuous_attempts = step_data.get("max_continuous_attempts")

    return StepDefinition(
        name=step_name,
        description=step_data.get("description", ""),
        outputs=outputs,
        output_schema=output_schema,
        executor=executor,
        inputs=input_bindings,
        sequencing=sequencing,
        exit_rules=exit_rules,
        idempotency=idempotency,
        when=when_condition,
        limits=limits,
        chain=chain,
        chain_label=chain_label,
        continue_session=continue_session,
        loop_prompt=loop_prompt,
        max_continuous_attempts=max_continuous_attempts,
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
        forked_from=data.get("forked_from", ""),
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

    # Parse config variables and requirements
    try:
        config_vars = _parse_config(data)
    except ValueError as e:
        errors.append(str(e))
        config_vars = []

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
    if source_path is not None:
        source_dir_str = str(source_path.parent.resolve())

    workflow = WorkflowDefinition(
        steps=steps, metadata=metadata, chains=chains, source_dir=source_dir_str,
        config_vars=config_vars, requires=requires, readme=readme,
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

    # Parse config variables and requirements
    try:
        config_vars = _parse_config(data)
    except ValueError as e:
        errors.append(str(e))
        config_vars = []

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
        steps=steps, chains=chains,
        config_vars=config_vars, requires=requires, readme=readme,
    )
    validation_errors = workflow.validate()
    if validation_errors:
        raise YAMLLoadError(validation_errors)

    return workflow
