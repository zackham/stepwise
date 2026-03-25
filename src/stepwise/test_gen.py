"""Generate pytest test fixtures from flow YAML definitions."""

from __future__ import annotations

import re

from stepwise.models import StepDefinition, WorkflowDefinition


def generate_test_fixture(wf: WorkflowDefinition, flow_name: str) -> str:
    """Generate a pytest test file for the given workflow."""
    safe_name = re.sub(r"[^a-zA-Z0-9]", "_", flow_name)
    class_name = "".join(
        part.capitalize() for part in safe_name.split("_") if part
    )
    if not class_name:
        class_name = "GeneratedFlow"

    # Collect job-level inputs
    job_inputs: dict[str, str] = {}
    for step in wf.steps.values():
        for binding in step.inputs:
            if binding.source_step == "$job":
                job_inputs[binding.source_field] = f'"sample_{binding.source_field}"'

    # Build stub registrations and step definitions
    stub_lines: list[str] = []
    step_def_lines: list[str] = []

    for step_name, step_def in wf.steps.items():
        fn_name = re.sub(r"[^a-zA-Z0-9]", "_", step_name)

        # Determine stub outputs
        outputs_dict = _stub_outputs(step_def)
        outputs_repr = _format_dict(outputs_dict)

        stub_lines.append(
            f'        register_step_fn("{fn_name}", lambda inputs: {outputs_repr})'
        )

        # Build StepDefinition constructor
        step_def_lines.append(_generate_step_def(step_name, step_def, fn_name))

    # Build the steps dict literal
    steps_entries = []
    for name, code in zip(wf.steps.keys(), step_def_lines):
        steps_entries.append(f'                "{name}": {code}')
    steps_block = "{\n" + ",\n".join(steps_entries) + ",\n            }"

    # Job inputs block
    if job_inputs:
        job_inputs_repr = _format_dict(job_inputs, raw_values=True)
        create_job_inputs = f", inputs={job_inputs_repr}"
    else:
        create_job_inputs = ""

    lines = [
        f'"""Auto-generated test fixture for {flow_name}."""',
        "from tests.conftest import register_step_fn, run_job_sync",
        "from stepwise.models import (",
        "    ExitRule,",
        "    ExecutorRef,",
        "    InputBinding,",
        "    JobStatus,",
        "    StepDefinition,",
        "    WorkflowDefinition,",
        ")",
        "",
        "",
        f"class Test{class_name}:",
        "    def test_happy_path(self, async_engine):",
        '        """Run the flow to completion with stub executors."""',
        "        # Register step stubs",
        *stub_lines,
        "",
        f"        # Build workflow",
        f"        wf = WorkflowDefinition(steps={steps_block})",
        "",
        "        # Create and run job",
        "        job = async_engine.create_job(",
        f'            objective="test {flow_name}",',
        f"            workflow=wf{create_job_inputs},",
        "        )",
        "        result = run_job_sync(async_engine, job.id)",
        "        assert result.status == JobStatus.COMPLETED",
        "",
        "        # Verify step runs",
        "        runs = async_engine.store.runs_for_job(job.id)",
        "        assert len(runs) >= 1",
    ]

    return "\n".join(lines) + "\n"


def _stub_outputs(step_def: StepDefinition) -> dict[str, str]:
    """Generate placeholder output dict for a step."""
    # If the step has advance exit rules with conditions, try to satisfy them
    advance_values = _values_for_advance(step_def)
    if advance_values:
        return advance_values

    result: dict[str, str] = {}
    for out in step_def.outputs:
        if step_def.output_schema and out in step_def.output_schema:
            spec = step_def.output_schema[out]
            result[out] = _typed_placeholder(out, spec)
        else:
            result[out] = f"stub_{out}"
    return result


def _typed_placeholder(name: str, spec) -> str:
    """Generate a typed placeholder value."""
    if spec.type == "bool":
        return "True"
    if spec.type == "choice" and spec.options:
        return spec.options[0]
    if spec.type == "number":
        return "1"
    if spec.type == "text":
        return f"stub_{name}"
    return f"stub_{name}"


def _values_for_advance(step_def: StepDefinition) -> dict[str, str] | None:
    """Try to infer output values that trigger the first advance exit rule."""
    for rule in step_def.exit_rules:
        if rule.config.get("action") != "advance":
            continue
        condition = rule.config.get("condition", "")
        if not condition:
            continue
        result = _parse_simple_conditions(condition, step_def.outputs)
        if result:
            for out in step_def.outputs:
                if out not in result:
                    result[out] = f"stub_{out}"
            return result
    return None


def _parse_simple_conditions(
    condition: str, outputs: list[str],
) -> dict[str, str] | None:
    """Parse simple exit rule conditions to extract target values.

    Handles patterns like:
      - outputs.field == 'value'
      - outputs.field == "value"
      - float(outputs.field) >= 0.8
      - outputs.field == True/False
    """
    result: dict[str, str] = {}

    for m in re.finditer(
        r"""outputs\.(\w+)\s*==\s*['"]([^'"]+)['"]""", condition
    ):
        field, value = m.group(1), m.group(2)
        if field in outputs:
            result[field] = value

    for m in re.finditer(
        r"""outputs\.(\w+)\s*==\s*(True|False|true|false)""", condition
    ):
        field, value = m.group(1), m.group(2)
        if field in outputs:
            result[field] = value.capitalize()

    for m in re.finditer(
        r"""float\(outputs\.(\w+)\)\s*>=\s*([\d.]+)""", condition
    ):
        field, threshold = m.group(1), m.group(2)
        if field in outputs:
            result[field] = threshold

    for m in re.finditer(
        r"""outputs\.(\w+)\s*==\s*(\d+(?:\.\d+)?)(?!\w)""", condition
    ):
        field, value = m.group(1), m.group(2)
        if field in outputs:
            result[field] = value

    return result if result else None


def _generate_step_def(
    step_name: str, step_def: StepDefinition, fn_name: str,
) -> str:
    """Generate Python code for a StepDefinition constructor."""
    parts: list[str] = []
    parts.append(f'name="{step_name}"')
    parts.append(f'executor=ExecutorRef(type="callable", config={{"fn_name": "{fn_name}"}})')

    if step_def.inputs:
        input_strs = []
        for b in step_def.inputs:
            args = f'"{b.local_name}", "{b.source_step}", "{b.source_field}"'
            if b.optional:
                args += ", optional=True"
            input_strs.append(f"InputBinding({args})")
        parts.append(f'inputs=[{", ".join(input_strs)}]')

    outputs_repr = repr(step_def.outputs)
    parts.append(f"outputs={outputs_repr}")

    if step_def.after:
        parts.append(f"after={repr(step_def.after)}")

    if step_def.when:
        parts.append(f'when="{step_def.when}"')

    if step_def.exit_rules:
        rule_strs = []
        for rule in step_def.exit_rules:
            config_repr = repr(rule.config)
            rule_strs.append(
                f'ExitRule(name="{rule.name}", type="{rule.type}", '
                f'config={config_repr}, priority={rule.priority})'
            )
        parts.append(f'exit_rules=[{", ".join(rule_strs)}]')

    indent = " " * 20
    joined = f",\n{indent}".join(parts)
    return f"StepDefinition(\n{indent}{joined},\n                )"


def _format_dict(d: dict[str, str], raw_values: bool = False) -> str:
    """Format a dict as a Python literal string."""
    if not d:
        return "{}"
    items = []
    for k, v in d.items():
        if raw_values:
            items.append(f'"{k}": {v}')
        elif isinstance(v, str) and v in ("True", "False", "None") or _is_numeric(v):
            items.append(f'"{k}": {v}')
        else:
            items.append(f'"{k}": "{v}"')
    return "{" + ", ".join(items) + "}"


def _is_numeric(s: str) -> bool:
    try:
        float(s)
        return True
    except (ValueError, TypeError):
        return False
