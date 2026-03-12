"""Generate agent-readable instructions for Stepwise flows.

Used by `stepwise agent-help` to produce a flow catalog that tells agents
what flows are available and how to call them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from stepwise.flow_resolution import FlowInfo
from stepwise.flow_resolution import discover_flows as _discover_flows
from stepwise.schema import generate_schema
from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError


def _build_flow_entries(
    flows: list[FlowInfo | Path], project_dir: Path
) -> list[dict]:
    """Parse flows and return structured entries."""
    entries = []
    for item in flows:
        # Accept both FlowInfo objects and raw Path objects
        if isinstance(item, FlowInfo):
            flow_path = item.path
            flow_name = item.name
        else:
            flow_path = item
            flow_name = None

        try:
            wf = load_workflow_yaml(str(flow_path))
            schema = generate_schema(wf)
        except (YAMLLoadError, Exception):
            continue

        name = schema["name"] or flow_name or flow_path.stem.replace(".flow", "")
        inputs = schema.get("inputs", [])
        outputs = schema.get("outputs", [])
        human_steps = schema.get("humanSteps", [])
        desc = schema.get("description", "")

        # Build run command — prefer flow name for clean invocation
        if flow_name:
            run_ref = flow_name
        else:
            try:
                run_ref = str(flow_path.relative_to(project_dir))
            except ValueError:
                run_ref = flow_path.name
        var_args = " ".join(f'--var {inp}="..."' for inp in inputs)
        cmd = f"stepwise run {run_ref} --wait"
        if var_args:
            cmd += f" {var_args}"

        # rel_path for file field
        try:
            rel_path = str(flow_path.relative_to(project_dir))
        except ValueError:
            rel_path = flow_path.name

        entry: dict = {
            "name": name,
            "file": rel_path,
            "inputs": inputs,
            "outputs": outputs,
            "run": cmd,
        }
        if desc:
            entry["description"] = desc
        if human_steps:
            entry["human_steps"] = [
                {"step": hs["step"], "fields": hs["fields"]}
                for hs in human_steps
            ]

        entries.append(entry)

    return entries


def generate_agent_help(
    project_dir: Path,
    flows_dir: Path | None = None,
    fmt: str = "compact",
) -> str:
    """Generate agent instructions for all flows in a project.

    Args:
        project_dir: Project root directory.
        flows_dir: Override flow discovery directory.
        fmt: Output format — "compact" (default, tight markdown),
             "json" (machine-readable), or "full" (legacy verbose).
    """
    if flows_dir:
        # Legacy: raw path glob for --flows-dir override
        flow_paths = sorted(flows_dir.rglob("*.flow.yaml"))
        flows: list[FlowInfo | Path] = list(flow_paths)
    else:
        flows = _discover_flows(project_dir)

    if not flows:
        if fmt == "json":
            return json.dumps({"flows": [], "count": 0})
        return "No flows found. Create a .flow.yaml file to get started."

    entries = _build_flow_entries(flows, project_dir)

    if fmt == "json":
        return json.dumps({"flows": entries, "count": len(entries)}, indent=2)

    if fmt == "full":
        return _format_full(entries)

    return _format_compact(entries)


def _format_compact(entries: list[dict]) -> str:
    """Tight, self-sufficient output for agent consumption.

    Includes flow catalog + CLI reference so an agent can run and manage
    jobs with no other context needed.
    """
    lines: list[str] = []

    # Flow catalog
    for entry in entries:
        name = entry["name"]
        desc = entry.get("description", "")

        header = f"**{name}**"
        if desc:
            header += f" — {desc}"
        lines.append(header)

        lines.append(f"  `{entry['run']}`")

        parts = []
        if entry["inputs"]:
            parts.append(f"in: {', '.join(entry['inputs'])}")
        if entry["outputs"]:
            parts.append(f"out: {', '.join(entry['outputs'])}")
        if entry.get("human_steps"):
            step_names = [hs["step"] for hs in entry["human_steps"]]
            parts.append(f"human: {', '.join(step_names)}")
        if parts:
            lines.append(f"  {' | '.join(parts)}")

        lines.append("")

    # CLI reference — everything an agent needs to run and manage flows
    lines.extend([
        "---",
        "CLI: `stepwise run <flow> --wait --var k=v` returns JSON to stdout.",
        "`--wait` blocks until done. `--async` returns job_id immediately.",
        "`stepwise status <job-id>` — check progress.",
        "`stepwise output <job-id>` — retrieve outputs after completion.",
        "`stepwise fulfill <run-id> '{\"field\": \"value\"}'` — satisfy a human step.",
        "`stepwise schema <flow>` — JSON schema (inputs, outputs, human steps).",
        "`stepwise validate <flow>` — syntax check a .flow.yaml file.",
        "",
        "Exit codes: 0=success, 1=failed, 2=input error, 3=timeout, 4=cancelled.",
        "--wait JSON: `{\"status\": \"completed\", \"job_id\": \"...\", \"outputs\": [...], \"cost_usd\": N}`",
        "On failure: `{\"status\": \"failed\", \"error\": \"...\", \"failed_step\": \"...\"}`",
        "",
    ])

    return "\n".join(lines)


def _format_full(entries: list[dict]) -> str:
    """Verbose markdown with headers — legacy format for --update use."""
    lines = [
        "# Stepwise Flows",
        "",
        "## Available Flows",
        "",
    ]

    for entry in entries:
        name = entry["name"]
        desc = entry.get("description", "")

        lines.append(f"### {name}")
        if desc:
            lines.append(desc)

        if entry["inputs"]:
            lines.append(f"- Inputs: {', '.join(entry['inputs'])}")
        else:
            lines.append("- Inputs: none")

        if entry.get("human_steps"):
            parts = []
            for hs in entry["human_steps"]:
                fields_str = ", ".join(hs["fields"])
                parts.append(f"{hs['step']} (→ {fields_str})")
            lines.append(f"- Human steps: {'; '.join(parts)}")
        else:
            lines.append("- Human steps: none")

        lines.append(f"- Run: `{entry['run']}`")

        if entry["outputs"]:
            lines.append(f"- Output fields: {', '.join(entry['outputs'])}")

        lines.append("")

    lines.extend([
        "## Quick Reference",
        "",
        "```",
        "stepwise run <flow> --wait --var k=v     # run, block, get JSON",
        "stepwise run <flow> --async               # fire-and-forget",
        "stepwise output <job-id>                  # retrieve outputs",
        "stepwise fulfill <run-id> '{...}'         # satisfy human step",
        "stepwise status <job-id>                  # check progress",
        "```",
        "",
        "Exit codes: 0=success, 1=failed, 2=input error, 3=timeout, 4=cancelled",
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
