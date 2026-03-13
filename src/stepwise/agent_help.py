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

    Includes 5-mode interaction model, flow catalog, and CLI reference
    so an agent can run and manage flows with no other context needed.
    """
    lines: list[str] = []

    # 5-mode interaction model
    lines.extend([
        "# Stepwise Agent Instructions",
        "",
        "## Interaction Modes",
        "",
        "**Automated** — Run a flow end-to-end, get structured output.",
        "  `stepwise run <flow> --wait --var k=v`",
        "",
        "**Mediated** — Run a flow with human steps; fulfill them interactively.",
        "  `stepwise run <flow> --wait` → exit 5 = suspended → read prompt →",
        "  `stepwise fulfill <run-id> '{...}' --wait` → resume until done.",
        "",
        "**Monitoring** — Check job progress and suspension inbox.",
        "  `stepwise status <job-id> --output json` — full DAG view.",
        "  `stepwise list --suspended --output json` — global inbox.",
        "",
        "**Data Grab** — Retrieve specific outputs from completed steps.",
        "  `stepwise output <job-id> --step name1,name2` — per-step outputs.",
        "  `stepwise output <job-id> --step name --inputs` — step inputs.",
        "",
        "**Takeover** — Cancel and inspect a running job.",
        "  `stepwise cancel <job-id> --output json` — cancel with remaining step info.",
        "  `stepwise wait <job-id>` — block on an existing job.",
        "",
    ])

    # Flow catalog
    if entries:
        lines.extend(["## Flows", ""])
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

    # CLI reference
    lines.extend([
        "## CLI Reference",
        "",
        "`stepwise run <flow> --wait --var k=v` — run and block for JSON result.",
        "`stepwise run <flow> --async` — fire-and-forget, returns job_id.",
        "`stepwise status <job-id> --output json` — resolved flow status (DAG view).",
        "`stepwise output <job-id>` — terminal outputs after completion.",
        "`stepwise output <job-id> --step a,b` — per-step outputs.",
        "`stepwise output <job-id> --step a --inputs` — step inputs.",
        "`stepwise output --run <run-id>` — direct run output.",
        "`stepwise fulfill <run-id> '{...}'` — satisfy a human step.",
        "`stepwise fulfill <run-id> '{...}' --wait` — fulfill then block on job.",
        "`stepwise list --suspended --output json` — global suspension inbox.",
        "`stepwise wait <job-id>` — block until completion or suspension.",
        "`stepwise cancel <job-id> --output json` — cancel with step details.",
        "`stepwise schema <flow>` — JSON tool contract (inputs, outputs, humanSteps).",
        "`stepwise validate <flow>` — syntax check a .flow.yaml file.",
        "",
        "Exit codes: 0=success, 1=failed, 2=input error, 3=timeout, 4=cancelled, 5=suspended.",
        "",
        "**--wait JSON responses:**",
        "  Success: `{\"status\": \"completed\", \"job_id\": \"...\", \"outputs\": {...}, \"cost_usd\": N}`",
        "  Failure: `{\"status\": \"failed\", \"error\": \"...\", \"failed_step\": \"...\"}`",
        "  Suspended: `{\"status\": \"suspended\", \"suspended_steps\": [{\"step\": \"...\", \"run_id\": \"...\", \"prompt\": \"...\", \"fields\": [...]}]}`",
        "",
        "**Mediation example:**",
        "```",
        "# 1. Start flow — blocks until suspended",
        "result=$(stepwise run meeting-ingest.flow.yaml --wait --var audio=rec.mp3)",
        "# exit=5, result has suspended_steps with run_id and prompt",
        "",
        "# 2. Read the prompt, prepare your response",
        "run_id=$(echo $result | jq -r '.suspended_steps[0].run_id')",
        "",
        "# 3. Fulfill and wait for completion",
        "stepwise fulfill $run_id '{\"approved\": true, \"notes\": \"looks good\"}' --wait",
        "```",
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


def build_emit_flow_instructions(
    registry: object | None = None,
    config: object | None = None,
    depth_remaining: int | None = None,
    project_dir: Path | None = None,
) -> str:
    """Build dynamic instructions for agents with emit_flow=true.

    Generates context-aware instructions based on:
    - Available executor types (from registry)
    - Model labels (from config)
    - Available flows for composition (from project discovery)
    - Remaining sub-job depth
    """
    lines: list[str] = []

    lines.append("\n## Flow Emission\n")
    lines.append("You can delegate complex multi-step work by writing a flow definition to:")
    lines.append("\n    .stepwise/emit.flow.yaml\n")
    lines.append("(relative to your working directory)\n")
    lines.append(
        "When this file exists at the end of your session, it will be launched as a "
        "sub-workflow. Your current step will wait for the sub-workflow to complete, "
        "and the sub-workflow's final outputs become your step's outputs.\n"
    )

    # When to emit vs direct
    lines.append("**When to emit a flow:**")
    lines.append("- The task decomposes into multiple sequential or parallel steps")
    lines.append("- Different parts need different executors (scripts, LLM calls, human review)")
    lines.append("- You want retry, timeout, or fallback on individual steps\n")
    lines.append("**When NOT to emit (just do the work directly):**")
    lines.append("- The task is straightforward and you can complete it in one session")
    lines.append("- The task is purely exploratory/research\n")

    # Available executor types — dynamic from registry
    lines.append("### Available executor types\n")
    executor_docs = {
        "script": ("script", "`run: |` shorthand", "Shell commands, stdout parsed as JSON"),
        "llm": ("llm", "`executor: llm`", "LLM API call"),
        "human": ("human", "`executor: human`", "Suspends for human input via web UI"),
        "agent": ("agent", "`executor: agent`", "Spawns another agent session"),
    }

    available_types: list[str] = []
    if registry and hasattr(registry, "_factories"):
        available_types = [t for t in ("script", "llm", "human", "agent")
                          if t in registry._factories]
    else:
        available_types = list(executor_docs.keys())

    lines.append("| Type | Usage | Notes |")
    lines.append("|---|---|---|")
    for t in available_types:
        if t in executor_docs:
            name, usage, notes = executor_docs[t]
            lines.append(f"| `{name}` | {usage} | {notes} |")
    lines.append("")

    # Model labels — dynamic from config
    if config and hasattr(config, "labels") and config.labels:
        from stepwise.config import label_model_id
        lines.append("### Model labels\n")
        lines.append("Use labels instead of full model IDs in `model:` fields:\n")
        lines.append("| Label | Model |")
        lines.append("|---|---|")
        for name, value in config.labels.items():
            model_id = label_model_id(value)
            lines.append(f"| `{name}` | `{model_id}` |")
        lines.append("")
        if config.default_model:
            lines.append(f"Default model: `{config.default_model}`\n")

    # Agent executor guidance
    lines.append("### Agent steps in emitted flows\n")
    lines.append("For `executor: agent` steps, do NOT specify `model:`. The agent "
                 "uses the user's configured default (claude, codex, etc.).\n")

    # Flow format
    lines.append("### Flow format\n")
    lines.append("```yaml")
    lines.append("name: descriptive-name")
    lines.append("steps:")
    lines.append("  step-one:")
    lines.append("    run: |")
    lines.append("      echo '{\"key\": \"value\"}'")
    lines.append("    outputs: [key]")
    lines.append("")
    lines.append("  step-two:")
    lines.append("    executor: llm")
    lines.append("    prompt: \"Analyze: $data\"")
    if config and hasattr(config, "default_model") and config.default_model:
        lines.append(f"    model: {config.default_model}")
    lines.append("    inputs:")
    lines.append("      data: step-one.key")
    lines.append("    outputs: [analysis]")
    lines.append("```\n")

    # Rules
    lines.append("### Rules\n")
    lines.append("- Step names: kebab-case. Output fields: underscore_case")
    lines.append("- `outputs` must match JSON keys produced by the step")
    lines.append("- Steps with no `inputs` referencing other steps run first")
    lines.append("- Steps run as soon as all dependencies complete")
    lines.append("- Terminal step outputs become the parent step's outputs")
    lines.append("- `$job.param` references job-level inputs; `source-step.field` for upstream")
    lines.append("- Always include a safety cap (`attempt >= N`) in loop exit rules\n")

    # Available flows for composition
    if project_dir:
        try:
            flows = _discover_flows(project_dir)
            if flows:
                lines.append("### Available flows for composition\n")
                lines.append("You can reference these as sub-flow steps via `flow: name`:\n")
                for flow in flows[:10]:  # cap at 10 to limit token use
                    lines.append(f"- `{flow.name}`")
                lines.append("")
                lines.append("Only compose with these if the task genuinely maps to them.\n")
        except Exception:
            pass

    # Depth remaining
    if depth_remaining is not None:
        lines.append(f"### Depth limit\n")
        lines.append(f"Sub-job depth remaining: **{depth_remaining}**. "
                     f"Emitted flows count as 1 level.\n")

    # Iterative pattern
    lines.append("### Iterative pattern\n")
    lines.append(
        "If this step loops (via exit rules), you can see results from your previous "
        "iteration via `$prev_result`. On the first iteration, `$prev_result` is None. "
        "Emit a flow when more decomposed work is needed; return directly when done. "
        "The `_delegated` marker in outputs indicates the result came from a sub-flow."
    )

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
