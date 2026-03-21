"""Generate JSON tool contracts from Stepwise flow definitions.

Used by `stepwise schema` and `stepwise agent-help` to expose
flow interfaces to external agents.
"""

from __future__ import annotations

from pathlib import Path

from stepwise.models import WorkflowDefinition


def generate_schema(workflow: WorkflowDefinition) -> dict:
    """Generate a JSON tool contract from a WorkflowDefinition.

    Scans steps for $job.* input bindings, terminal step outputs,
    and external steps to produce a machine-readable schema.
    """
    # Collect unique $job.* input field names
    job_inputs: set[str] = set()
    for step in workflow.steps.values():
        for binding in step.inputs:
            if binding.source_step == "$job":
                job_inputs.add(binding.source_field)

    # Collect terminal step output field names
    terminal_names = workflow.terminal_steps()
    terminal_outputs: list[str] = []
    seen_outputs: set[str] = set()
    for name in terminal_names:
        step = workflow.steps[name]
        for out in step.outputs:
            if out not in seen_outputs:
                terminal_outputs.append(out)
                seen_outputs.add(out)

    # Collect external steps
    external_steps: list[dict] = []
    for name, step in workflow.steps.items():
        if step.executor.type in ("external", "human"):
            entry: dict = {
                "step": name,
                "prompt": step.executor.config.get("prompt", ""),
                "fields": step.outputs,
            }
            if step.output_schema:
                entry["schema"] = {k: v.to_dict() for k, v in step.output_schema.items()}
            external_steps.append(entry)

    schema: dict = {
        "name": workflow.metadata.name,
        "description": workflow.metadata.description,
    }
    if workflow.metadata.version:
        schema["version"] = workflow.metadata.version
    schema["inputs"] = sorted(job_inputs)
    schema["outputs"] = terminal_outputs
    schema["externalSteps"] = external_steps

    if workflow.config_vars:
        schema["config"] = {v.name: v.to_dict() for v in workflow.config_vars}
    if workflow.requires:
        schema["requires"] = [r.to_dict() for r in workflow.requires]

    return schema
