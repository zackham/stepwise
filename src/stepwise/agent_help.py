"""Generate agent-readable instructions for Stepwise flows.

Used by `stepwise agent-help` to produce a flow catalog that tells agents
what flows are available and how to call them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from stepwise.flow_resolution import FlowInfo, KitInfo, RegistryFlowInfo, is_archived
from stepwise.flow_resolution import discover_flows as _discover_flows
from stepwise.flow_resolution import discover_kits as _discover_kits
from stepwise.flow_resolution import discover_registry_flows as _discover_registry_flows
from stepwise.schema import generate_schema
from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError


def _build_flow_entries(
    flows: list[FlowInfo | Path], project_dir: Path,
    visibility_filter: set[str] | None = None,
) -> list[dict]:
    """Parse flows and return structured entries.

    Args:
        visibility_filter: If set, only include flows whose visibility is in this set.
    """
    entries = []
    for item in flows:
        # Accept both FlowInfo objects and raw Path objects
        if isinstance(item, FlowInfo):
            flow_path = item.path
            flow_name = item.name
        else:
            flow_path = item
            flow_name = None

        if is_archived(flow_path):
            continue

        try:
            wf = load_workflow_yaml(str(flow_path))
            schema = generate_schema(wf)
        except (YAMLLoadError, Exception):
            continue

        # Filter by visibility
        if visibility_filter and wf.metadata.visibility not in visibility_filter:
            continue

        name = schema["name"] or flow_name or flow_path.stem.replace(".flow", "")
        inputs = schema.get("inputs", [])
        outputs = schema.get("outputs", [])
        external_steps = schema.get("externalSteps", [])
        desc = schema.get("description", "")

        # Build run command — prefer flow name for clean invocation
        if flow_name:
            run_ref = flow_name
        else:
            try:
                run_ref = str(flow_path.relative_to(project_dir))
            except ValueError:
                run_ref = flow_path.name
        # Use config/input var descriptions for --input hints when available
        config_map = {v.name: v for v in wf.config_vars} if wf.config_vars else {}
        if wf.input_vars:
            config_map.update({v.name: v for v in wf.input_vars})
        var_parts = []
        for inp in inputs:
            cv = config_map.get(inp)
            hint = f"<{cv.description}>" if cv and cv.description else "..."
            var_parts.append(f'--input {inp}="{hint}"')
        var_args = " ".join(var_parts)
        cmd = f"stepwise run {run_ref} --wait"
        if var_args:
            cmd += f" {var_args}"

        # rel_path for file field
        try:
            rel_path = str(flow_path.relative_to(project_dir))
        except ValueError:
            rel_path = flow_path.name

        # Include kit name for grouping
        kit_name_val = item.kit_name if isinstance(item, FlowInfo) else None

        entry: dict = {
            "name": name,
            "file": rel_path,
            "inputs": inputs,
            "outputs": outputs,
            "run": cmd,
        }
        if kit_name_val:
            entry["kit_name"] = kit_name_val
        if desc:
            entry["description"] = desc
        if wf.config_vars:
            entry["config"] = {v.name: v.to_dict() for v in wf.config_vars}
        if wf.input_vars:
            entry["inputs_schema"] = {v.name: v.to_dict() for v in wf.input_vars}
        if wf.requires:
            entry["requires"] = [r.name for r in wf.requires]
        if external_steps:
            entry["external_steps"] = []
            for hs in external_steps:
                hs_entry: dict = {"step": hs["step"], "fields": hs["fields"]}
                if hs.get("schema"):
                    hs_entry["schema"] = hs["schema"]
                entry["external_steps"].append(hs_entry)

        entries.append(entry)

    return entries


def _build_registry_entries(
    registry_flows: list[RegistryFlowInfo], project_dir: Path
) -> list[dict]:
    """Parse registry flows and return structured entries."""
    entries = []
    for rf in registry_flows:
        try:
            wf = load_workflow_yaml(str(rf.path))
            schema = generate_schema(wf)
        except (YAMLLoadError, Exception):
            continue

        name = rf.ref
        inputs = schema.get("inputs", [])
        outputs = schema.get("outputs", [])
        external_steps = schema.get("externalSteps", [])
        desc = schema.get("description", "")

        var_args = " ".join(f'--input {inp}="..."' for inp in inputs)
        cmd = f"stepwise run {rf.ref} --wait"
        if var_args:
            cmd += f" {var_args}"

        try:
            rel_path = str(rf.path.relative_to(project_dir))
        except ValueError:
            rel_path = str(rf.path)

        entry: dict = {
            "name": name,
            "file": rel_path,
            "inputs": inputs,
            "outputs": outputs,
            "run": cmd,
            "registry": True,
        }
        if desc:
            entry["description"] = desc
        if external_steps:
            entry["external_steps"] = []
            for hs in external_steps:
                hs_entry: dict = {"step": hs["step"], "fields": hs["fields"]}
                if hs.get("schema"):
                    hs_entry["schema"] = hs["schema"]
                entry["external_steps"].append(hs_entry)

        entries.append(entry)

    return entries


def generate_agent_help(
    project_dir: Path,
    flows_dir: Path | None = None,
    fmt: str = "compact",
    kit_name: str | None = None,
) -> str:
    """Generate agent instructions for all flows in a project.

    Args:
        project_dir: Project root directory.
        flows_dir: Override flow discovery directory.
        fmt: Output format — "compact" (default, tight markdown),
             "json" (machine-readable), or "full" (legacy verbose).
        kit_name: If provided, show only flows in this kit (L2 detail view).
    """
    if flows_dir:
        # Legacy: raw path glob for --flows-dir override
        flow_paths = sorted(flows_dir.rglob("*.flow.yaml"))
        flows: list[FlowInfo | Path] = list(flow_paths)
    else:
        flows = _discover_flows(project_dir)

    # Discover kits
    kits = _discover_kits(project_dir)
    kit_map: dict[str, KitInfo] = {k.name: k for k in kits}

    # Load kit definitions for metadata
    kit_defs: dict[str, object] = {}
    if kits:
        from stepwise.yaml_loader import load_kit_yaml, KitLoadError
        for k in kits:
            try:
                kit_defs[k.name] = load_kit_yaml(k.path)
            except (KitLoadError, Exception):
                pass

    # Also discover registry flows
    registry_flows = _discover_registry_flows(project_dir)

    if not flows and not registry_flows:
        if fmt == "json":
            return json.dumps({"flows": [], "count": 0})
        return "No flows found. Create one with `stepwise new <name>` or install from the registry with `stepwise get @author:name`."

    # Agent-help only shows interactive flows (not background/internal)
    agent_visibility = {"interactive"}
    entries = _build_flow_entries(flows, project_dir, visibility_filter=agent_visibility)
    registry_entries = _build_registry_entries(registry_flows, project_dir)

    # If requesting a specific kit, filter to just that kit's flows
    if kit_name:
        if kit_name not in kit_map:
            return f"Kit '{kit_name}' not found. Available kits: {', '.join(sorted(kit_map.keys())) or 'none'}"
        entries = [e for e in entries if e.get("kit_name") == kit_name]
        registry_entries = []

    all_entries = entries + registry_entries

    if fmt == "json":
        return json.dumps({"flows": all_entries, "count": len(all_entries)}, indent=2)

    if fmt == "full":
        return _format_full(all_entries)

    return _format_compact(entries, registry_entries, kit_defs=kit_defs, kit_filter=kit_name)


def _get_doc_description(path: Path) -> str:
    """Extract one-line description from a markdown file."""
    try:
        text = path.read_text()
    except OSError:
        return ""
    lines = text.split("\n")
    past_heading = False
    for line in lines:
        stripped = line.strip()
        if not past_heading:
            if stripped.startswith("# "):
                past_heading = True
            continue
        if not stripped:
            continue
        if stripped.startswith(">") or stripped.startswith("#") or stripped.startswith("**") or stripped.startswith("---"):
            continue
        if len(stripped) > 80:
            return stripped[:77] + "..."
        return stripped
    return ""


def _append_docs_section(lines: list[str]) -> None:
    """Append a Documentation section listing available docs."""
    from stepwise.project import get_docs_dir

    docs_dir = get_docs_dir()
    if not docs_dir:
        return

    md_files = sorted(docs_dir.rglob("*.md"))
    if not md_files:
        return

    lines.extend(["## Documentation", ""])
    for md_file in md_files:
        stem = md_file.stem
        desc = _get_doc_description(md_file)
        entry = f"`stepwise docs {stem}`"
        if desc:
            entry += f" — {desc}"
        lines.append(entry)
    lines.append("")


def _format_compact(
    entries: list[dict],
    registry_entries: list[dict] | None = None,
    kit_defs: dict[str, object] | None = None,
    kit_filter: str | None = None,
) -> str:
    """Tight, self-sufficient output for agent consumption.

    Includes 5-mode interaction model, flow catalog, and CLI reference
    so an agent can run and manage flows with no other context needed.
    """
    lines: list[str] = []

    # Interaction model — background --wait is the default
    lines.extend([
        "# Stepwise Agent Instructions",
        "",
        "## How to Run Flows",
        "",
        "**Default: background `--wait`** — Run a flow, stay free, get notified on completion.",
        "  `stepwise run <flow> --wait --input k=v`",
        "  Run this command in a **background process** (not foreground). You get:",
        "  - Automatic notification with the full JSON result when the job completes",
        "  - The job is visible in the server's web UI for human monitoring",
        "  - Your session stays free for conversation and parallel work",
        "  This is the right choice for almost all situations.",
        "",
        "  `--wait` uses the server when one is running (same as `--async`). The job shows",
        "  up in `stepwise jobs`, the web DAG viewer, and `stepwise tail/status/output`.",
        "",
        "**Foreground `--wait`** — Blocks your session until the job completes.",
        "  Only use this when you have nothing else to do and the job is fast (<30s).",
        "",
        "**Async (for external callers)** — Fire-and-forget, requires manual polling.",
        "  `stepwise run <flow> --async --input k=v`",
        "  Returns `{\"job_id\": \"...\"}`. You must poll with `stepwise status`/`output`.",
        "  Use this for webhooks, cron triggers, or non-interactive callers that can't",
        "  run background processes. **Don't use this in interactive agent sessions** —",
        "  background `--wait` is strictly better (auto-notification, no polling).",
        "  Optionally add `--notify <url>` for webhook callbacks on events.",
        "",
        "**Mediated** — Run a flow with external (awaiting fulfillment) steps.",
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

    # Flow catalog — grouped by kit
    def _format_flow_entry(entry: dict) -> list[str]:
        flines: list[str] = []
        name = entry["name"]
        desc = entry.get("description", "")
        header = f"**{name}**"
        if desc:
            header += f" — {desc}"
        flines.append(header)
        flines.append(f"  `{entry['run']}`")
        parts = []
        if entry["inputs"]:
            parts.append(f"in: {', '.join(entry['inputs'])}")
        if entry["outputs"]:
            parts.append(f"out: {', '.join(entry['outputs'])}")
        if entry.get("external_steps"):
            step_names = [hs["step"] for hs in entry["external_steps"]]
            parts.append(f"external: {', '.join(step_names)}")
        if parts:
            flines.append(f"  {' | '.join(parts)}")
        flines.append("")
        return flines

    if entries:
        # Separate kit flows from standalone
        kit_entries: dict[str, list[dict]] = {}
        standalone_entries: list[dict] = []
        for entry in entries:
            kn = entry.get("kit_name")
            if kn:
                kit_entries.setdefault(kn, []).append(entry)
            else:
                standalone_entries.append(entry)

        # Kit sections with usage info
        if kit_entries:
            lines.extend(["## Kits", ""])
            if not kit_filter:
                lines.append("Use `stepwise agent-help <kit>` for full flow details within a kit.")
                lines.append("")
            for kn in sorted(kit_entries.keys()):
                kit_def = (kit_defs or {}).get(kn)
                kit_desc = getattr(kit_def, "description", "") if kit_def else ""
                kit_usage = getattr(kit_def, "usage", "") if kit_def else ""
                flow_names = [e["name"] for e in kit_entries[kn]]

                lines.append(f"### {kn}" + (f" — {kit_desc}" if kit_desc else ""))
                lines.append(f"Flows: {', '.join(flow_names)}")
                lines.append("")

                if kit_usage:
                    lines.append(kit_usage.rstrip())
                    lines.append("")

                # In L2 (kit_filter set) or if only one kit, show full flow details
                if kit_filter:
                    for entry in kit_entries[kn]:
                        lines.extend(_format_flow_entry(entry))

        # Standalone flows
        if standalone_entries:
            lines.extend(["## Flows", ""])
            for entry in standalone_entries:
                lines.extend(_format_flow_entry(entry))

    # Registry flows
    if registry_entries:
        lines.extend(["## Registry Flows (read-only)", ""])
        for entry in registry_entries:
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
            if entry.get("external_steps"):
                step_names = [hs["step"] for hs in entry["external_steps"]]
                parts.append(f"external: {', '.join(step_names)}")
            if parts:
                lines.append(f"  {' | '.join(parts)}")

            lines.append("")

    # Typed external inputs
    lines.extend([
        "## Typed External Inputs",
        "",
        "External steps may declare typed output fields. When fulfilling, match the expected types:",
        "",
        "| Type | JSON value | Example |",
        "|------|-----------|---------|",
        "| `str` | string | `\"name\": \"Alice\"` |",
        "| `text` | string (multiline) | `\"notes\": \"Line 1\\nLine 2\"` |",
        "| `number` | number | `\"score\": 8.5` |",
        "| `bool` | boolean | `\"approved\": true` |",
        "| `choice` | string from options | `\"priority\": \"high\"` |",
        "| `choice` (multiple) | array of strings | `\"tags\": [\"a\", \"b\"]` |",
        "",
        "Use `stepwise list --suspended --output json` to see `output_schema` for each field.",
        "The engine validates types and returns errors — fix and retry.",
        "",
    ])

    # Documentation
    _append_docs_section(lines)

    # CLI reference
    lines.extend([
        "## CLI Reference",
        "",
        "`stepwise run <flow> --wait --input k=v` — run and block for JSON result.",
        "`stepwise run <flow> --async` — fire-and-forget, returns job_id.",
        "`stepwise run <flow> --async --notify <url>` — async with webhook callbacks on suspend/complete/fail.",
        "`stepwise run <flow> --async --notify <url> --notify-context '{...}'` — async with context passed to webhooks.",
        "`stepwise status <job-id> --output json` — resolved flow status (DAG view).",
        "`stepwise output <job-id>` — terminal outputs after completion.",
        "`stepwise output <job-id> --step a,b` — per-step outputs.",
        "`stepwise output <job-id> --step a --inputs` — step inputs.",
        "`stepwise output --run <run-id>` — direct run output.",
        "`stepwise fulfill <run-id> '{...}'` — satisfy an external step.",
        "`stepwise fulfill <run-id> '{...}' --wait` — fulfill then block on job.",
        "`stepwise list --suspended --output json` — global suspension inbox.",
        "`stepwise wait <job-id>` — block until completion or suspension.",
        "`stepwise cancel <job-id> --output json` — cancel with step details.",
        "`stepwise schema <flow>` — JSON tool contract (inputs, outputs, externalSteps).",
        "`stepwise validate <flow>` — syntax check a .flow.yaml file.",
        "`stepwise job create <flow> --input k=v --name 'plan: foo' --group <name>` — stage a job.",
        "`stepwise job create <flow> --approve` — stage a job that requires explicit approval before running.",
        "`stepwise job approve <job-id>` — approve a job in awaiting_approval status, transitions to pending.",
        "`stepwise job show [--group <name>]` — list staged jobs.",
        "`stepwise job run [<job-id>] [--group <name>] [--wait]` — release to pending, optionally block until complete.",
        "`stepwise job dep <job-id> [--after <id>] [--rm <id>]` — manage deps.",
        "`stepwise job cancel <job-id>` — cancel a staged/pending job.",
        "`stepwise job rm <job-id>` — delete a staged job.",
        "",
        "Exit codes: 0=success, 1=failed, 2=input error, 4=cancelled, 5=suspended.",
        "",
        "**--wait JSON responses:**",
        "  Success: `{\"status\": \"completed\", \"job_id\": \"...\", \"outputs\": {...}, \"cost_usd\": N}`",
        "  Failure: `{\"status\": \"failed\", \"error\": \"...\", \"failed_step\": \"...\"}`",
        "  Suspended: `{\"status\": \"suspended\", \"suspended_steps\": [{\"step\": \"...\", \"run_id\": \"...\", \"prompt\": \"...\", \"fields\": [...]}]}`",
        "",
        "**IMPORTANT: Always pass `--name` when launching jobs.** The web UI shows the name as the primary",
        "label. Without it, every job shows 'implement' or 'plan-light' with no context. Use short,",
        "human-readable labels: `--name 'plan: cost-analytics'`, `--name 'impl: mobile-overhaul'`.",
        "",
        "**Mediation example:**",
        "```",
        "# 1. Start flow — blocks until suspended",
        "result=$(stepwise run meeting-ingest.flow.yaml --wait --name 'ingest: standup-recording' --input audio=rec.mp3)",
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

    # Schedule management
    lines.extend([
        "## Schedule Management",
        "",
        "Stepwise has built-in scheduling for automated job launches.",
        "",
        "**Cron schedules** fire a flow on a time pattern (always launch):",
        "`stepwise schedule create <flow> --cron '0 9 * * *' --name 'daily-report'`",
        "",
        "**Poll schedules** check a condition, only launch when it passes:",
        "`stepwise schedule create <flow> --cron '*/5 * * * *' --poll-command './check.sh' --name 'watcher'`",
        "",
        "**Management:**",
        "`stepwise schedule list` — show all schedules with status and stats.",
        "`stepwise schedule describe <name>` — full config, stats, recent ticks.",
        "`stepwise schedule pause <name>` — pause a schedule.",
        "`stepwise schedule resume <name>` — resume a paused schedule.",
        "`stepwise schedule trigger <name>` — fire immediately (bypass timing).",
        "`stepwise schedule history <name>` — tick evaluation history.",
        "`stepwise schedule delete <name>` — remove a schedule.",
        "",
        "**Options:** `--overlap skip|queue|allow`, `--cooldown <seconds>`, `--recovery skip|catch_up_once`,",
        "`--input key=value` (static job inputs), `--timezone <tz>`.",
        "",
        "Poll commands: exit 0 + JSON dict on stdout = fire (JSON becomes job inputs).",
        "Exit 0 + empty = skip. Non-zero = error. `STEPWISE_POLL_CURSOR` env var carries last fired output.",
        "",
    ])

    # Orchestration guidance (O4)
    lines.extend([
        "## Orchestrating Multiple Jobs",
        "",
        "When dispatching several jobs (e.g., a batch of fixes, parallel research tasks),",
        "use background `--wait` for each (see \"How to Run Flows\" above).",
        "",
        "**Concurrency**: 2-3 concurrent jobs in the same repo work well when they touch different",
        "files. For heavier parallelism or overlapping files, consider sequential dispatch.",
        "",
        "**Monitoring while jobs run:**",
        "  `stepwise tail <job-id>` — live event stream (best for watching progress).",
        "  `stepwise status <job-id>` — point-in-time snapshot of step states.",
        "  `stepwise output <job-id> <step>` — retrieve a specific step's output.",
        "  `stepwise logs <job-id>` — chronological event dump for debugging.",
        "",
        "**When a job fails:**",
        "  1. `stepwise logs <job-id>` — see what happened (which step, what error).",
        "  2. `stepwise output <job-id> <failed-step>` — see the step's output/error.",
        "  3. Decide: re-run with adjusted spec, or investigate the root cause.",
        "  Jobs are resumable — a failed job preserves all completed step outputs.",
        "",
    ])

    # Job staging (between Orchestrating and Why Use Flows)
    lines.extend([
        "## Job Staging (Multi-Job DAGs)",
        "",
        "When a task decomposes into multiple flows (research → plan → implement),",
        "create the full DAG upfront with job staging. **This is the preferred pattern**",
        "for multi-phase work — don't manually launch wave by wave.",
        "",
        "### The create → dep → run pattern",
        "",
        "```bash",
        "# 1. Create all jobs — capture IDs with --output json",
        "RESEARCH=$(stepwise job create research-v2 \\",
        '  --input topic="Widget architecture" \\',
        "  --group sprint-1 --name \"research: widgets\" \\",
        "  --output json | jq -r .id)",
        "",
        "PLAN=$(stepwise job create plan \\",
        '  --input spec="Design widget system" --input project="my-app" \\',
        "  --group sprint-1 --name \"plan: widgets\" \\",
        "  --output json | jq -r .id)",
        "",
        "IMPL=$(stepwise job create implement \\",
        '  --input spec="Build widget system" --input project="my-app" \\',
        "  --group sprint-1 --name \"impl: widgets\" \\",
        "  --output json | jq -r .id)",
        "",
        "# 2. Wire dependencies",
        "stepwise job dep $PLAN --after $RESEARCH",
        "stepwise job dep $IMPL --after $PLAN",
        "",
        "# 3. Review and release (--wait blocks until all jobs complete)",
        "stepwise job show --group sprint-1",
        "stepwise job run --group sprint-1 --wait",
        "```",
        "",
        "### Data wiring between jobs",
        "",
        "`--input key=job-id.field` passes data AND auto-creates a dependency:",
        "  `stepwise job create implement --input plan_file=$PLAN.plan_file --group batch`",
        "",
        "`stepwise job dep A --after B` creates ordering without data flow.",
        "",
        "Use data wiring when downstream needs upstream's output. Use ordering-only",
        "when jobs must be sequenced but don't share data.",
        "",
        "### Parallel workstreams",
        "",
        "Jobs in the same group with no deps between them run in parallel.",
        "Use `--max-concurrent N` on `job run --group` to limit parallelism.",
        "",
        "### Commands",
        "",
        "  `stepwise job create <flow> --input k=v --group <name> --output json` — stage a job, get ID.",
        "  `stepwise job create <flow> --approve` — stage requiring approval before execution.",
        "  `stepwise job approve <id>` — approve a job, transitions to pending.",
        "  `stepwise job dep <id> --after <other-id>` — add ordering constraint.",
        "  `stepwise job show [--group <name>]` — list staged jobs.",
        "  `stepwise job run [--group <name>] [--max-concurrent N] [--wait]` — release, optionally block.",
        "  `stepwise job cancel <id>` — cancel a staged/pending job.",
        "  `stepwise job rm <id>` — delete a staged job.",
        "",
        "Jobs auto-start when all dependencies complete. The engine cascades.",
        "",
    ])

    # Why stepwise flows over direct implementation (O5)
    lines.extend([
        "## Why Use Flows (vs. Direct Implementation)",
        "",
        "Stepwise flows provide structural advantages over doing work directly:",
        "",
        "**Validation loop** — Flows like quick-fix run implement → validate → fix cycles.",
        "An agent working directly might report \"done\" without verifying. The validate step",
        "catches false completions by actually running tests.",
        "",
        "**Correct project context** — Agent steps run in the target project directory and",
        "inherit that project's CLAUDE.md and configuration. A direct agent dispatched from",
        "another project carries the wrong context (the caller's CLAUDE.md, not the target's).",
        "",
        "**Escalation** — When an agent hits ambiguity, external steps suspend for human input",
        "instead of guessing. Direct agents either succeed or fail with no middle ground.",
        "",
        "**Persistence** — Jobs survive session boundaries. If your session ends, the job keeps",
        "running and results are retrievable later. Direct agents die with the session.",
        "",
        "**Flow selection** — Different problems need different processes. quick-fix for small",
        "changes, plan-and-build for multi-file architecture, research-v2 for gathering context,",
        "council for design decisions. Picking the right flow encodes the right process.",
        "",
        "**Observability** — Every step's inputs, outputs, timing, and status are recorded.",
        "`stepwise tail/status/output/logs` give structured visibility. Direct agents are opaque.",
        "",
        "These advantages compound with scale. For a single simple task, direct work is fine.",
        "For a batch of heterogeneous work, flows save time and catch errors that direct",
        "implementation misses.",
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

        if entry.get("external_steps"):
            parts = []
            for hs in entry["external_steps"]:
                fields_str = ", ".join(hs["fields"])
                parts.append(f"{hs['step']} (→ {fields_str})")
            lines.append(f"- External steps: {'; '.join(parts)}")
        else:
            lines.append("- External steps: none")

        lines.append(f"- Run: `{entry['run']}`")

        if entry["outputs"]:
            lines.append(f"- Output fields: {', '.join(entry['outputs'])}")

        lines.append("")

    lines.extend([
        "## Quick Reference",
        "",
        "```",
        "stepwise run <flow> --wait --input k=v     # run, block, get JSON",
        "stepwise run <flow> --async               # fire-and-forget",
        "stepwise output <job-id>                  # retrieve outputs",
        "stepwise fulfill <run-id> '{...}'         # satisfy external step",
        "stepwise status <job-id>                  # check progress",
        "```",
        "",
        "Exit codes: 0=success, 1=failed, 2=input error, 4=cancelled, 5=suspended",
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
    lines.append("- Different parts need different executors (scripts, LLM calls, external review)")
    lines.append("- You want retry, timeout, or fallback on individual steps\n")
    lines.append("**When NOT to emit (just do the work directly):**")
    lines.append("- The task is straightforward and you can complete it in one session")
    lines.append("- The task is purely exploratory/research\n")

    lines.append("**Structured output:**")
    lines.append("- If your step declares `outputs`, stepwise automatically sets `STEPWISE_OUTPUT_FILE`")
    lines.append("- Write a JSON object with the declared output keys to this file before finishing")
    lines.append("- If you emit a flow instead, the sub-flow's terminal step outputs are used (file is ignored)\n")

    # Available executor types — dynamic from registry
    lines.append("### Available executor types\n")
    executor_docs = {
        "script": ("script", "`run: |` shorthand", "Shell commands, stdout parsed as JSON"),
        "llm": ("llm", "`executor: llm`", "LLM API call"),
        "external": ("external", "`executor: external`", "Suspends for external input via web UI"),
        "agent": ("agent", "`executor: agent`", "Spawns another agent session"),
    }

    available_types: list[str] = []
    if registry and hasattr(registry, "_factories"):
        available_types = [t for t in ("script", "llm", "external", "agent")
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
                 "uses the user's configured default (claude, codex, etc.).")
    lines.append("Agent sub-steps that declare `outputs` automatically receive "
                 "`STEPWISE_OUTPUT_FILE` and prompt instructions for writing structured output.\n")

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

    # Optional inputs
    lines.append("### Optional inputs\n")
    lines.append("Weak-reference bindings that resolve to `None` when unavailable.")
    lines.append("Use for loop-back data, first-run defaults, and cross-step sessions.\n")
    lines.append("```yaml")
    lines.append("inputs:")
    lines.append("  spec: $job.spec                  # required — step waits")
    lines.append("  failures:")
    lines.append("    from: run-tests.failures")
    lines.append("    optional: true                  # None on first iteration")
    lines.append("```\n")
    lines.append("- In prompts: `None` → empty string. In scripts: env var unset.")
    lines.append("- Cycles via optional edges are valid (enables loop-back data).\n")

    # Session continuity
    lines.append("### Session continuity\n")
    lines.append("Agent/LLM steps can reuse sessions across loop iterations:\n")
    lines.append("```yaml")
    lines.append("implement:")
    lines.append("  executor: agent")
    lines.append("  prompt: \"Implement: $spec\"")
    lines.append("  loop_prompt: \"Tests failed:\\n$failures\\nFix them.\"")
    lines.append("  continue_session: true")
    lines.append("  max_continuous_attempts: 5")
    lines.append("  inputs:")
    lines.append("    spec: $job.spec")
    lines.append("    failures:")
    lines.append("      from: run-tests.failures")
    lines.append("      optional: true")
    lines.append("  outputs: [result]")
    lines.append("```\n")
    lines.append("- `continue_session: true` — reuse agent session across iterations")
    lines.append("- `loop_prompt` — alternate prompt on attempt > 1 (falls back to `prompt`)")
    lines.append("- `max_continuous_attempts` — circuit breaker; force fresh session after N iterations")
    lines.append("- `_session_id` auto-emitted for cross-step sharing via optional input:\n")
    lines.append("```yaml")
    lines.append("inputs:")
    lines.append("  _session_id:")
    lines.append("    from: plan._session_id")
    lines.append("    optional: true")
    lines.append("```\n")

    # Rules
    lines.append("### Rules\n")
    lines.append("- Step names: kebab-case. Output fields: underscore_case")
    lines.append("- `outputs` must match JSON keys produced by the step")
    lines.append("- Steps with no `inputs` referencing other steps run first")
    lines.append("- Steps run as soon as all dependencies complete")
    lines.append("- Terminal step outputs become the parent step's outputs")
    lines.append("- `$job.param` references job-level inputs; `source-step.field` for upstream")
    lines.append("- Always include a safety cap (`attempt >= N`) in loop exit rules")
    lines.append("- Exit rules with `advance` actions fail if none match — handle all output cases\n")

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
