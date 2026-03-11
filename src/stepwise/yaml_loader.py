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
    DecoratorRef,
    ExecutorRef,
    ExitRule,
    FlowMetadata,
    InputBinding,
    StepDefinition,
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


def _parse_executor(step_data: dict, step_name: str) -> ExecutorRef:
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

    config: dict[str, Any] = {}
    if executor_type == "human":
        prompt = step_data.get("prompt", "")
        if prompt:
            config["prompt"] = prompt
    elif executor_type == "mock_llm":
        # Pass through any config
        for k in ("failure_rate", "partial_rate", "garbage_rate"):
            if k in step_data:
                config[k] = step_data[k]
    elif executor_type == "llm":
        for k in ("prompt", "model", "system", "temperature", "max_tokens"):
            if k in step_data:
                config[k] = step_data[k]
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


def _parse_step(step_name: str, step_data: dict) -> StepDefinition:
    """Parse a single step from YAML data."""
    # Outputs
    outputs = step_data.get("outputs", [])
    if not isinstance(outputs, list):
        raise ValueError(f"Step '{step_name}': 'outputs' must be a list")

    # Executor
    executor = _parse_executor(step_data, step_name)

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

    return StepDefinition(
        name=step_name,
        outputs=outputs,
        executor=executor,
        inputs=input_bindings,
        sequencing=sequencing,
        exit_rules=exit_rules,
        idempotency=idempotency,
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


def load_workflow_yaml(source: str | Path) -> WorkflowDefinition:
    """Load a WorkflowDefinition from a YAML file or string.

    Args:
        source: Path to a YAML file, or a YAML string.

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
            steps[step_name] = _parse_step(step_name, step_data)
        except ValueError as e:
            errors.append(str(e))

    if errors:
        raise YAMLLoadError(errors)

    # Parse metadata from top-level fields
    metadata = _parse_metadata(data, source_path)

    workflow = WorkflowDefinition(steps=steps, metadata=metadata)

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

    if errors:
        raise YAMLLoadError(errors)

    workflow = WorkflowDefinition(steps=steps)
    validation_errors = workflow.validate()
    if validation_errors:
        raise YAMLLoadError(validation_errors)

    return workflow
