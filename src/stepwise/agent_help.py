"""Generate agent-readable instructions for Stepwise flows.

Used by `stepwise agent-help` to produce a markdown block that tells
agents how to use available flows. Paste into CLAUDE.md or similar.
"""

from __future__ import annotations

import re
from pathlib import Path

from stepwise.schema import generate_schema
from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError


def discover_flows(project_dir: Path) -> list[Path]:
    """Find all .flow.yaml files in a project directory."""
    flows: list[Path] = []

    # Check project root
    for f in sorted(project_dir.glob("*.flow.yaml")):
        flows.append(f)

    # Check .stepwise/flows/ if it exists
    stepwise_flows = project_dir / ".stepwise" / "flows"
    if stepwise_flows.is_dir():
        for f in sorted(stepwise_flows.rglob("*.flow.yaml")):
            flows.append(f)

    # Check flows/ directory
    flows_dir = project_dir / "flows"
    if flows_dir.is_dir():
        for f in sorted(flows_dir.rglob("*.flow.yaml")):
            if f not in flows:
                flows.append(f)

    return flows


def generate_agent_help(project_dir: Path, flows_dir: Path | None = None) -> str:
    """Generate markdown agent instructions for all flows in a project.

    Args:
        project_dir: Project root directory.
        flows_dir: Override flow discovery directory (if given, only scans this dir).
    """
    if flows_dir:
        flows = sorted(flows_dir.rglob("*.flow.yaml"))
    else:
        flows = discover_flows(project_dir)

    if not flows:
        return (
            "# Stepwise Flows\n\n"
            "No flows found in this project. "
            "Create a .flow.yaml file to get started.\n"
        )

    lines = [
        "# Stepwise Flows",
        "",
        "This project has Stepwise workflows available via CLI.",
        "",
        "## Available Flows",
        "",
    ]

    for flow_path in flows:
        try:
            wf = load_workflow_yaml(str(flow_path))
            schema = generate_schema(wf)
        except (YAMLLoadError, Exception):
            continue

        name = schema["name"] or flow_path.stem.replace(".flow", "")
        desc = schema.get("description", "")

        lines.append(f"### {name}")
        if desc:
            lines.append(f"{desc}")

        # Inputs
        inputs = schema.get("inputs", [])
        if inputs:
            lines.append(f"- Inputs: {', '.join(inputs)}")
        else:
            lines.append("- Inputs: none")

        # Human steps
        human_steps = schema.get("humanSteps", [])
        if human_steps:
            parts = []
            for hs in human_steps:
                fields_str = ", ".join(hs["fields"])
                prompt_preview = hs["prompt"][:60] + "..." if len(hs["prompt"]) > 60 else hs["prompt"]
                parts.append(f"{hs['step']} ({prompt_preview} → {fields_str})")
            lines.append(f"- Human steps: {'; '.join(parts)}")
        else:
            lines.append("- Human steps: none")

        # Run command
        rel_path = flow_path.name
        var_args = " ".join(f'--var {inp}="..."' for inp in inputs)
        cmd = f"stepwise run {rel_path} --wait"
        if var_args:
            cmd += f" {var_args}"
        lines.append(f"- Run: `{cmd}`")

        # Output shape
        outputs = schema.get("outputs", [])
        if outputs:
            lines.append(f"- Output fields: {', '.join(outputs)}")

        lines.append("")

    lines.extend([
        "## Output Shapes",
        "",
        "Success (exit code 0):",
        "```json",
        '{',
        '  "status": "completed",',
        '  "job_id": "job-...",',
        '  "outputs": [{...}],',
        '  "cost_usd": 0.052,',
        '  "duration_seconds": 45.2',
        '}',
        "```",
        "",
        "Failure (exit code 1):",
        "```json",
        '{',
        '  "status": "failed",',
        '  "job_id": "job-...",',
        '  "error": "Step \'test\' failed: exit code 1",',
        '  "failed_step": "test",',
        '  "completed_outputs": [{...}],',
        '  "cost_usd": 0.012,',
        '  "duration_seconds": 12.8',
        '}',
        "```",
        "",
        "Timeout (exit code 3):",
        "```json",
        '{',
        '  "status": "timeout",',
        '  "job_id": "job-...",',
        '  "timeout_seconds": 300,',
        '  "suspended_at_step": "approve"',
        '}',
        "```",
        "",
        "## CLI Quick Reference",
        "",
        "```",
        "stepwise run <flow> --wait --var k=v                 # run, block, get JSON",
        "stepwise run <flow> --async                          # fire-and-forget, returns job_id",
        "stepwise output <job-id>                             # retrieve outputs",
        "stepwise fulfill <run-id> '{\"field\": \"value\"}'       # satisfy human step",
        "stepwise status <job-id>                             # check progress",
        "stepwise schema <flow>                               # input/output schema",
        "```",
        "",
        "## Exit Codes",
        "",
        "- 0: completed successfully",
        "- 1: flow execution failed",
        "- 2: input validation error",
        "- 3: timeout",
        "- 4: cancelled",
        "",
    ])

    return "\n".join(lines)


def update_file(target: Path, content: str) -> bool:
    """Update a section in target file between markers, or append.

    Returns True if markers were found and replaced, False if appended.
    """
    START_MARKER = "<!-- stepwise-agent-help -->"
    END_MARKER = "<!-- /stepwise-agent-help -->"

    if target.exists():
        text = target.read_text()
        pattern = re.compile(
            re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
            re.DOTALL,
        )
        replacement = f"{START_MARKER}\n{content}\n{END_MARKER}"

        if START_MARKER in text:
            new_text = pattern.sub(replacement, text)
            target.write_text(new_text)
            return True
        else:
            target.write_text(text.rstrip() + "\n\n" + replacement + "\n")
            return False
    else:
        target.write_text(f"{START_MARKER}\n{content}\n{END_MARKER}\n")
        return False
