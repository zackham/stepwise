"""Generate self-contained HTML trace reports from completed flows.

Used by `stepwise run --report`. Produces a single HTML file with:
- Flow metadata header
- DAG visualization (inline SVG with anchor links)
- Step timeline with durations
- Expandable step details (native <details>/<summary>, zero JS)
- Cost summary
- YAML source appendix
- Print stylesheet + light mode support
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from stepwise.models import (
    ExitRule,
    FlowMetadata,
    HandoffEnvelope,
    Job,
    JobStatus,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
)
from stepwise.store import SQLiteStore


@dataclass
class StepReport:
    """Collected data for one step in the report."""
    name: str
    executor_type: str
    outputs_declared: list[str]
    exit_rules: list[ExitRule]
    runs: list[StepRun]
    cost: float = 0.0
    events: list[dict] = field(default_factory=list)


def generate_report(job: Job, store: SQLiteStore, flow_path: Path | None = None) -> str:
    """Generate a self-contained HTML report for a completed job.

    Returns the HTML string.
    """
    workflow = job.workflow
    metadata = workflow.metadata
    all_runs = store.runs_for_job(job.id)

    # Group runs by step
    step_reports: dict[str, StepReport] = {}
    for step_name, step_def in workflow.steps.items():
        runs = [r for r in all_runs if r.step_name == step_name]
        cost = sum(store.accumulated_cost(r.id) for r in runs)
        step_reports[step_name] = StepReport(
            name=step_name,
            executor_type=step_def.executor.type,
            outputs_declared=step_def.outputs,
            exit_rules=step_def.exit_rules,
            runs=runs,
            cost=cost,
        )

    # Compute topology layers for DAG
    layers = _compute_layers(workflow)

    # Read YAML source if available
    yaml_source = ""
    if flow_path and flow_path.exists():
        try:
            yaml_source = flow_path.read_text()
        except Exception:
            pass

    # Build HTML
    parts = [
        _html_head(metadata, job),
        _html_header(metadata, job, flow_path, step_reports, workflow),
        _html_dag(workflow, layers, step_reports),
        _html_timeline(workflow, step_reports, job),
        _html_step_details(workflow, step_reports, store),
        _html_yaml_appendix(yaml_source, flow_path),
        _html_footer(job, step_reports),
        _html_tail(),
    ]
    return "\n".join(parts)


def save_report(html_content: str, output_path: Path) -> Path:
    """Write report HTML to file. Returns the path."""
    output_path.write_text(html_content)
    return output_path


def default_report_path(flow_path: Path) -> Path:
    """Generate default report filename: <flow-stem>-report.html"""
    stem = flow_path.stem
    if stem.endswith(".flow"):
        stem = stem[:-5]
    return flow_path.parent / f"{stem}-report.html"


# ── Topology ──────────────────────────────────────────────────────────


def _compute_layers(workflow: WorkflowDefinition) -> list[list[str]]:
    """Assign steps to layers via topological sort (longest-path layering)."""
    deps: dict[str, set[str]] = {}
    for name, step in workflow.steps.items():
        d: set[str] = set()
        for b in step.inputs:
            if b.source_step != "$job":
                d.add(b.source_step)
        d.update(step.after)
        deps[name] = d

    layer_of: dict[str, int] = {}

    def _depth(name: str) -> int:
        if name in layer_of:
            return layer_of[name]
        d = deps.get(name, set())
        if not d:
            layer_of[name] = 0
            return 0
        result = max(_depth(dep) for dep in d) + 1
        layer_of[name] = result
        return result

    for name in workflow.steps:
        _depth(name)

    max_layer = max(layer_of.values()) if layer_of else 0
    layers: list[list[str]] = [[] for _ in range(max_layer + 1)]
    for name, layer in sorted(layer_of.items(), key=lambda x: x[1]):
        layers[layer].append(name)
    return layers


# ── HTML Builders ─────────────────────────────────────────────────────


def _e(s: str) -> str:
    """Escape for HTML."""
    return html.escape(str(s))


def _status_color(status: str) -> str:
    return {
        "completed": "#22c55e",
        "failed": "#ef4444",
        "running": "#3b82f6",
        "suspended": "#f59e0b",
        "pending": "#6b7280",
        "cancelled": "#6b7280",
    }.get(status, "#6b7280")


def _status_color_light(status: str) -> str:
    """Colors for light mode."""
    return {
        "completed": "#16a34a",
        "failed": "#dc2626",
        "running": "#2563eb",
        "suspended": "#d97706",
        "pending": "#6b7280",
        "cancelled": "#6b7280",
    }.get(status, "#6b7280")


def _status_bg(status: str) -> str:
    return {
        "completed": "rgba(34, 197, 94, 0.1)",
        "failed": "rgba(239, 68, 68, 0.1)",
        "running": "rgba(59, 130, 246, 0.1)",
        "suspended": "rgba(245, 158, 11, 0.1)",
    }.get(status, "rgba(107, 114, 128, 0.1)")


def _status_icon(status: str) -> str:
    return {
        "completed": "&#x2713;",
        "failed": "&#x2717;",
        "running": "&#x25cf;",
        "suspended": "&#x25c6;",
    }.get(status, "&#x25cb;")


def _executor_icon(executor_type: str) -> str:
    return {
        "script": "&#x25b6;",
        "external": "&#x1f464;",
        "llm": "&#x2728;",
        "agent": "&#x1f916;",
        "mock_llm": "&#x2728;",
        "mock": "&#x25a0;",
    }.get(executor_type, "&#x25a0;")


def _format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:.1f}m"
    hours = minutes / 60
    return f"{hours:.1f}h"


def _format_cost(cost: float) -> str:
    if cost == 0:
        return ""
    if cost < 0.01:
        return f"${cost:.4f}"
    return f"${cost:.3f}"


def _format_json(obj: object, max_depth: int = 3) -> str:
    """Pretty-format JSON for display, truncating deep nesting."""
    try:
        text = json.dumps(obj, indent=2, default=str)
        lines = text.split("\n")
        if len(lines) > 50:
            lines = lines[:50] + [f"  ... ({len(lines) - 50} more lines)"]
        return "\n".join(lines)
    except (TypeError, ValueError):
        return str(obj)


def _html_head(metadata: FlowMetadata, job: Job) -> str:
    title = _e(metadata.name or job.objective or "Stepwise Report")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — Stepwise Report</title>
<style>
:root {{
  --bg: #0a0a0a;
  --surface: #141414;
  --surface2: #1a1a1a;
  --border: #2a2a2a;
  --border2: #333;
  --text: #e5e5e5;
  --text2: #a3a3a3;
  --text3: #737373;
  --green: #22c55e;
  --red: #ef4444;
  --blue: #3b82f6;
  --amber: #f59e0b;
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  --mono: "SF Mono", "Cascadia Code", "Fira Code", Consolas, monospace;
}}
@media (prefers-color-scheme: light) {{
  :root {{
    --bg: #ffffff;
    --surface: #f9fafb;
    --surface2: #f3f4f6;
    --border: #e5e7eb;
    --border2: #d1d5db;
    --text: #111827;
    --text2: #4b5563;
    --text3: #9ca3af;
    --green: #16a34a;
    --red: #dc2626;
    --blue: #2563eb;
    --amber: #d97706;
  }}
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  font-family: var(--font);
  background: var(--bg);
  color: var(--text);
  line-height: 1.6;
  min-height: 100vh;
}}
.container {{
  max-width: 960px;
  margin: 0 auto;
  padding: 32px 24px;
}}

/* Header */
.report-header {{
  margin-bottom: 32px;
  padding-bottom: 24px;
  border-bottom: 1px solid var(--border);
}}
.report-header h1 {{
  font-size: 24px;
  font-weight: 600;
  margin-bottom: 8px;
}}
.report-header .description {{
  color: var(--text2);
  margin-bottom: 16px;
  font-size: 15px;
}}
.meta-row {{
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
  font-size: 13px;
  color: var(--text3);
}}
.meta-row .meta-item {{
  display: flex;
  align-items: center;
  gap: 6px;
}}
.meta-row .meta-value {{
  color: var(--text2);
  font-weight: 500;
}}
.status-badge {{
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}}
.tag {{
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 11px;
  background: var(--surface2);
  color: var(--text3);
  border: 1px solid var(--border);
}}

/* Stats row */
.stats-row {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
  margin-bottom: 32px;
}}
.stat-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  text-align: center;
}}
.stat-card .stat-value {{
  font-size: 24px;
  font-weight: 700;
  font-family: var(--mono);
}}
.stat-card .stat-label {{
  font-size: 11px;
  color: var(--text3);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-top: 4px;
}}

/* Sections */
.section {{
  margin-bottom: 32px;
}}
.section h2 {{
  font-weight: 600;
  margin-bottom: 16px;
  color: var(--text2);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  font-size: 12px;
}}

/* DAG */
.dag-container {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 24px;
  overflow-x: auto;
}}
.dag-container a {{
  text-decoration: none;
}}

/* Timeline */
.timeline {{
  position: relative;
  padding-left: 24px;
}}
.timeline::before {{
  content: '';
  position: absolute;
  left: 7px;
  top: 4px;
  bottom: 4px;
  width: 2px;
  background: var(--border);
}}
.timeline-item {{
  position: relative;
  padding: 8px 0 16px 16px;
}}
.timeline-dot {{
  position: absolute;
  left: -20px;
  top: 12px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid;
}}
.timeline-item .step-name {{
  font-weight: 600;
  font-size: 14px;
}}
.timeline-item .step-name a {{
  color: inherit;
  text-decoration: none;
}}
.timeline-item .step-name a:hover {{
  text-decoration: underline;
}}
.timeline-item .step-meta {{
  font-size: 12px;
  color: var(--text3);
  margin-top: 2px;
}}
.timeline-item .step-meta span {{
  margin-right: 12px;
}}
.attempt-label {{
  font-size: 11px;
  color: var(--text3);
  font-style: italic;
}}

/* Step details — native <details>/<summary> */
.step-detail {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  margin-bottom: 12px;
  overflow: hidden;
}}
.step-detail > summary {{
  padding: 12px 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: space-between;
  user-select: none;
  list-style: none;
}}
.step-detail > summary::-webkit-details-marker {{
  display: none;
}}
.step-detail > summary:hover {{
  background: var(--surface2);
}}
.step-detail > summary .left {{
  display: flex;
  align-items: center;
  gap: 10px;
}}
.step-detail > summary .chevron {{
  color: var(--text3);
  transition: transform 0.15s;
  font-size: 12px;
}}
.step-detail[open] > summary .chevron {{
  transform: rotate(90deg);
}}
.step-detail-body {{
  padding: 16px;
  border-top: 1px solid var(--border);
}}
.detail-section {{
  margin-bottom: 16px;
}}
.detail-section:last-child {{
  margin-bottom: 0;
}}
.detail-label {{
  font-size: 11px;
  font-weight: 600;
  color: var(--text3);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
}}
pre.detail-code {{
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px;
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.5;
  overflow-x: auto;
  max-height: 300px;
  overflow-y: auto;
  color: var(--text2);
  white-space: pre-wrap;
  word-break: break-word;
  position: relative;
}}
.error-box {{
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.2);
  border-radius: 6px;
  padding: 12px;
  font-family: var(--mono);
  font-size: 12px;
  color: var(--red);
}}
.exit-rule-fired {{
  background: rgba(34, 197, 94, 0.08);
  border: 1px solid rgba(34, 197, 94, 0.2);
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 12px;
  color: var(--green);
}}
.sidecar-item {{
  font-size: 13px;
  color: var(--text2);
  padding: 2px 0;
}}
.sidecar-item::before {{
  content: '\\2022';
  margin-right: 8px;
  color: var(--text3);
}}

/* YAML appendix */
.yaml-appendix > summary {{
  padding: 12px 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 10px;
  user-select: none;
  list-style: none;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text3);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}}
.yaml-appendix > summary::-webkit-details-marker {{
  display: none;
}}
.yaml-appendix > summary:hover {{
  background: var(--surface2);
}}
.yaml-appendix > summary .chevron {{
  transition: transform 0.15s;
}}
.yaml-appendix[open] > summary {{
  border-radius: 8px 8px 0 0;
}}
.yaml-appendix[open] > summary .chevron {{
  transform: rotate(90deg);
}}
.yaml-appendix .yaml-body {{
  border: 1px solid var(--border);
  border-top: none;
  border-radius: 0 0 8px 8px;
  padding: 16px;
  background: var(--surface);
}}
.yaml-appendix pre {{
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 16px;
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.6;
  overflow-x: auto;
  color: var(--text2);
  white-space: pre-wrap;
  word-break: break-word;
}}

/* Copy button */
.copy-btn {{
  position: absolute;
  top: 6px;
  right: 6px;
  background: var(--surface2);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 10px;
  color: var(--text3);
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s;
}}
pre:hover .copy-btn,
.yaml-body:hover .copy-btn {{
  opacity: 1;
}}
.copy-btn:hover {{
  color: var(--text);
  background: var(--border);
}}

/* Footer */
.report-footer {{
  margin-top: 32px;
  padding-top: 16px;
  border-top: 1px solid var(--border);
  font-size: 12px;
  color: var(--text3);
  display: flex;
  justify-content: space-between;
}}

/* Responsive */
@media (max-width: 640px) {{
  .container {{ padding: 16px 12px; }}
  .stats-row {{ grid-template-columns: repeat(2, 1fr); }}
  .meta-row {{ flex-direction: column; gap: 8px; }}
}}

/* Print */
@media print {{
  :root {{
    --bg: #ffffff;
    --surface: #f9fafb;
    --surface2: #f3f4f6;
    --border: #e5e7eb;
    --text: #111827;
    --text2: #4b5563;
    --text3: #9ca3af;
    --green: #16a34a;
    --red: #dc2626;
    --blue: #2563eb;
    --amber: #d97706;
  }}
  body {{ background: white; }}
  .container {{ max-width: none; padding: 0; }}
  .step-detail-body {{ display: block !important; }}
  .step-detail[open] > summary .chevron {{ display: none; }}
  .copy-btn {{ display: none; }}
  .dag-container {{ page-break-inside: avoid; }}
  .step-detail {{ page-break-inside: avoid; }}
  pre.detail-code {{ max-height: none; overflow: visible; }}
}}
</style>
</head>
<body>
<div class="container">
"""


def _html_header(
    metadata: FlowMetadata,
    job: Job,
    flow_path: Path | None,
    step_reports: dict[str, StepReport],
    workflow: WorkflowDefinition | None = None,
) -> str:
    name = metadata.name or job.objective or "Untitled Flow"
    description = metadata.description or ""
    status = job.status.value
    color = _status_color(status)

    # Compute stats
    total_steps = len(step_reports)
    total_runs = sum(len(sr.runs) for sr in step_reports.values())
    completed_steps = sum(
        1 for sr in step_reports.values()
        if any(r.status == StepRunStatus.COMPLETED for r in sr.runs)
    )
    failed_steps = sum(
        1 for sr in step_reports.values()
        if any(r.status == StepRunStatus.FAILED for r in sr.runs)
        and not any(r.status == StepRunStatus.COMPLETED for r in sr.runs)
    )
    total_cost = sum(sr.cost for sr in step_reports.values())

    # Duration
    all_runs = [r for sr in step_reports.values() for r in sr.runs]
    started_times = [r.started_at for r in all_runs if r.started_at]
    completed_times = [r.completed_at for r in all_runs if r.completed_at]
    duration = 0.0
    if started_times and completed_times:
        duration = (max(completed_times) - min(started_times)).total_seconds()

    # Loop count
    loop_steps = sum(
        1 for sr in step_reports.values()
        if len([r for r in sr.runs if r.status == StepRunStatus.COMPLETED]) > 1
    )

    parts = []
    parts.append('<div class="report-header">')
    parts.append(f'<h1>{_e(name)}</h1>')
    if description:
        parts.append(f'<div class="description">{_e(description)}</div>')

    parts.append('<div class="meta-row">')
    parts.append(
        f'<div class="meta-item">'
        f'<span class="status-badge" style="background: {_status_bg(status)}; color: {color};">'
        f'{_status_icon(status)} {_e(status)}</span></div>'
    )
    if metadata.author:
        parts.append(f'<div class="meta-item">by <span class="meta-value">{_e(metadata.author)}</span></div>')
    if flow_path:
        parts.append(f'<div class="meta-item">from <span class="meta-value">{_e(flow_path.name)}</span></div>')
    if job.created_at:
        ts = job.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        parts.append(f'<div class="meta-item"><span class="meta-value">{ts}</span></div>')
    if metadata.tags:
        tags_html = " ".join(f'<span class="tag">{_e(t)}</span>' for t in metadata.tags)
        parts.append(f'<div class="meta-item">{tags_html}</div>')
    parts.append("</div>")  # meta-row
    parts.append("</div>")  # report-header

    # Stats cards
    parts.append('<div class="stats-row">')
    parts.append(_stat_card(f"{completed_steps}/{total_steps}", "Steps"))

    if total_runs > total_steps:
        parts.append(_stat_card(str(total_runs), "Runs"))

    parts.append(_stat_card(_format_duration(duration), "Duration"))

    if total_cost > 0:
        parts.append(_stat_card(_format_cost(total_cost), "Cost"))

    if failed_steps:
        parts.append(_stat_card(str(failed_steps), "Failed", color=_status_color("failed")))

    if loop_steps:
        parts.append(_stat_card(str(loop_steps), "Looped"))

    # M7a: Chain count
    if workflow and workflow.chains:
        chain_count = len(workflow.chains)
        chain_members = sum(
            1 for s in workflow.steps.values() if s.chain
        )
        parts.append(_stat_card(
            f"{chain_count}", f"Chain{'s' if chain_count != 1 else ''} ({chain_members} steps)"
        ))

    parts.append("</div>")
    return "\n".join(parts)


def _stat_card(value: str, label: str, color: str | None = None) -> str:
    style = f' style="color: {color}"' if color else ""
    return (
        f'<div class="stat-card">'
        f'<div class="stat-value"{style}>{_e(value)}</div>'
        f'<div class="stat-label">{_e(label)}</div>'
        f"</div>"
    )


# ── DAG SVG ──────────────────────────────────────────────────────────


def _html_dag(
    workflow: WorkflowDefinition,
    layers: list[list[str]],
    step_reports: dict[str, StepReport],
) -> str:
    if not layers:
        return ""

    node_w = 140
    node_h = 44
    h_gap = 60
    v_gap = 32
    pad = 24

    # Compute positions
    positions: dict[str, tuple[int, int]] = {}
    max_nodes_in_layer = max(len(layer) for layer in layers)

    for li, layer in enumerate(layers):
        x = pad + li * (node_w + h_gap)
        layer_height = len(layer) * node_h + (len(layer) - 1) * v_gap
        total_height = max_nodes_in_layer * node_h + (max_nodes_in_layer - 1) * v_gap
        y_offset = (total_height - layer_height) // 2
        for ni, name in enumerate(layer):
            y = pad + y_offset + ni * (node_h + v_gap)
            positions[name] = (x, y)

    svg_w = pad * 2 + len(layers) * (node_w + h_gap) - h_gap
    svg_h = pad * 2 + max_nodes_in_layer * (node_h + v_gap) - v_gap

    parts = ['<div class="section">']
    parts.append('<h2>Flow Graph</h2>')
    parts.append(f'<div class="dag-container">')
    parts.append(
        f'<svg width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}" '
        f'xmlns="http://www.w3.org/2000/svg">'
    )

    # Arrowhead marker
    parts.append(
        '<defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#555"/>'
        "</marker></defs>"
    )

    # Draw edges first (behind nodes)
    for name, step in workflow.steps.items():
        if name not in positions:
            continue
        tx, ty = positions[name]
        deps: set[str] = set()
        for b in step.inputs:
            if b.source_step != "$job":
                deps.add(b.source_step)
        deps.update(step.after)

        for dep in deps:
            if dep not in positions:
                continue
            sx, sy = positions[dep]
            x1 = sx + node_w
            y1 = sy + node_h // 2
            x2 = tx
            y2 = ty + node_h // 2
            cx = (x1 + x2) / 2
            parts.append(
                f'<path d="M {x1} {y1} C {cx} {y1}, {cx} {y2}, {x2} {y2}" '
                f'fill="none" stroke="#444" stroke-width="1.5" marker-end="url(#arrow)"/>'
            )

    # Draw nodes (wrapped in <a> links to step details)
    for name, step in workflow.steps.items():
        if name not in positions:
            continue
        x, y = positions[name]
        sr = step_reports.get(name)

        if sr and sr.runs:
            status = sr.runs[-1].status.value
        else:
            status = "pending"

        color = _status_color(status)
        fill = _status_bg(status)
        executor = sr.executor_type if sr else "unknown"
        icon = _executor_icon(executor)

        attempt_count = len(sr.runs) if sr else 0
        badges = ""
        if attempt_count > 1:
            badges += (
                f'<circle cx="{x + node_w - 6}" cy="{y + 6}" r="9" fill="{color}" opacity="0.8"/>'
                f'<text x="{x + node_w - 6}" y="{y + 10}" text-anchor="middle" '
                f'font-size="10" font-weight="700" fill="#fff">{attempt_count}</text>'
            )

        # M7a: Chain membership indicator
        if step.chain:
            badges += (
                f'<rect x="{x + node_w - 8}" y="{y + node_h - 14}" width="8" height="8" rx="2" '
                f'fill="#8b5cf6" opacity="0.7"/>'
            )

        # Wrap in anchor link to step detail
        parts.append(f'<a href="#step-{_e(name)}">')
        parts.append(
            f'<rect x="{x}" y="{y}" width="{node_w}" height="{node_h}" rx="6" '
            f'fill="{fill}" stroke="{color}" stroke-width="1.5" opacity="0.9"/>'
        )
        parts.append(
            f'<text x="{x + 12}" y="{y + node_h // 2 + 1}" '
            f'font-size="12" font-family="-apple-system, system-ui, sans-serif" '
            f'font-weight="600" fill="{color}" dominant-baseline="middle">'
            f'{icon} {_e(name)}</text>'
        )
        if badges:
            parts.append(badges)
        parts.append("</a>")

    parts.append("</svg>")
    parts.append("</div>")  # dag-container
    parts.append("</div>")  # section
    return "\n".join(parts)


# ── Timeline ─────────────────────────────────────────────────────────


def _html_timeline(
    workflow: WorkflowDefinition,
    step_reports: dict[str, StepReport],
    job: Job,
) -> str:
    all_items: list[tuple[datetime, StepRun, StepReport]] = []
    for sr in step_reports.values():
        for run in sr.runs:
            ts = run.started_at or run.completed_at or job.created_at
            all_items.append((ts, run, sr))

    all_items.sort(key=lambda x: x[0])

    if not all_items:
        return ""

    parts = ['<div class="section">']
    parts.append('<h2>Timeline</h2>')
    parts.append('<div class="timeline">')

    for ts, run, sr in all_items:
        status = run.status.value
        color = _status_color(status)
        duration_str = ""
        if run.started_at and run.completed_at:
            dur = (run.completed_at - run.started_at).total_seconds()
            duration_str = _format_duration(dur)

        attempt_label = ""
        if run.attempt > 1:
            attempt_label = f' <span class="attempt-label">attempt {run.attempt}</span>'

        cost_str = ""
        if sr.cost > 0 and len(sr.runs) == 1:
            cost_str = f"<span>{_format_cost(sr.cost)}</span>"

        # M7a: Chain badge for timeline
        step_def = workflow.steps.get(sr.name)
        chain_badge = ""
        if step_def and step_def.chain:
            chain_badge = (
                f' <span style="font-size: 10px; padding: 1px 6px; border-radius: 4px; '
                f'background: rgba(139, 92, 246, 0.15); color: #8b5cf6; font-weight: 500;">'
                f'{_e(step_def.chain)}</span>'
            )

        parts.append(f'<div class="timeline-item">')
        parts.append(f'<div class="timeline-dot" style="border-color: {color}; background: {_status_bg(status)};"></div>')
        parts.append(
            f'<div class="step-name" style="color: {color};">'
            f'{_status_icon(status)} <a href="#step-{_e(sr.name)}">{_e(sr.name)}</a>{attempt_label}{chain_badge}</div>'
        )
        parts.append(f'<div class="step-meta">')
        parts.append(f'<span>{_e(sr.executor_type)}</span>')
        if duration_str:
            parts.append(f"<span>{duration_str}</span>")
        if cost_str:
            parts.append(cost_str)
        if run.error:
            error_preview = run.error[:80] + ("..." if len(run.error) > 80 else "")
            parts.append(f'<span style="color: var(--red);">{_e(error_preview)}</span>')
        parts.append("</div>")  # step-meta
        parts.append("</div>")  # timeline-item

    parts.append("</div>")  # timeline
    parts.append("</div>")  # section
    return "\n".join(parts)


# ── Step Details ─────────────────────────────────────────────────────


def _html_step_details(
    workflow: WorkflowDefinition,
    step_reports: dict[str, StepReport],
    store: SQLiteStore,
) -> str:
    parts = ['<div class="section">']
    parts.append('<h2>Step Details</h2>')

    for step_name, step_def in workflow.steps.items():
        sr = step_reports[step_name]
        parts.append(_html_one_step_detail(step_def, sr, store))

    parts.append("</div>")
    return "\n".join(parts)


def _html_one_step_detail(
    step_def: StepDefinition,
    sr: StepReport,
    store: SQLiteStore,
) -> str:
    if not sr.runs:
        status = "pending"
    else:
        status = sr.runs[-1].status.value

    color = _status_color(status)

    # Use native <details>/<summary> — zero JS, accessible, printable
    parts = [f'<details class="step-detail" id="step-{_e(sr.name)}">']

    # Summary (always visible)
    parts.append("<summary>")
    parts.append(f'<div class="left">')
    parts.append(f'<span style="color: {color}; font-size: 14px;">{_status_icon(status)}</span>')
    parts.append(f'<span style="font-weight: 600;">{_e(sr.name)}</span>')
    parts.append(f'<span style="color: var(--text3); font-size: 12px;">{_e(sr.executor_type)}</span>')
    if step_def.chain:
        parts.append(
            f'<span style="font-size: 11px; padding: 1px 6px; border-radius: 4px; '
            f'background: rgba(139, 92, 246, 0.15); color: #8b5cf6;">'
            f'&#x26d3; {_e(step_def.chain)}</span>'
        )
    if sr.cost > 0:
        parts.append(f'<span style="color: var(--text3); font-size: 12px;">{_format_cost(sr.cost)}</span>')
    if len(sr.runs) > 1:
        parts.append(f'<span style="color: var(--text3); font-size: 12px;">{len(sr.runs)} runs</span>')
    parts.append("</div>")
    parts.append('<span class="chevron">&#x25b6;</span>')
    parts.append("</summary>")

    # Body
    parts.append('<div class="step-detail-body">')

    for ri, run in enumerate(sr.runs):
        if len(sr.runs) > 1:
            parts.append(f'<div style="margin-bottom: 16px; padding-bottom: 16px; border-bottom: 1px solid var(--border);">')
            parts.append(f'<div style="font-size: 12px; font-weight: 600; color: var(--text3); margin-bottom: 8px;">Attempt {run.attempt}</div>')

        # Inputs
        if run.inputs:
            parts.append('<div class="detail-section">')
            parts.append('<div class="detail-label">Inputs</div>')
            parts.append(f'<pre class="detail-code">{_e(_format_json(run.inputs))}</pre>')
            parts.append("</div>")

        # Result / Output
        if run.result:
            artifact = run.result.artifact
            if artifact:
                parts.append('<div class="detail-section">')
                parts.append('<div class="detail-label">Output</div>')
                parts.append(f'<pre class="detail-code">{_e(_format_json(artifact))}</pre>')
                parts.append("</div>")

            # Sidecar
            sidecar = run.result.sidecar
            has_sidecar = (
                sidecar.decisions_made
                or sidecar.assumptions
                or sidecar.open_questions
                or sidecar.constraints_discovered
            )
            if has_sidecar:
                parts.append('<div class="detail-section">')
                parts.append('<div class="detail-label">Sidecar</div>')
                for label, items in [
                    ("Decisions", sidecar.decisions_made),
                    ("Assumptions", sidecar.assumptions),
                    ("Open Questions", sidecar.open_questions),
                    ("Constraints", sidecar.constraints_discovered),
                ]:
                    if items:
                        parts.append(f'<div style="font-size: 12px; color: var(--text3); margin-top: 8px;">{label}:</div>')
                        for item in items:
                            parts.append(f'<div class="sidecar-item">{_e(item)}</div>')
                parts.append("</div>")

            # Executor meta
            if run.result.executor_meta:
                parts.append('<div class="detail-section">')
                parts.append('<div class="detail-label">Executor</div>')
                parts.append(f'<pre class="detail-code">{_e(_format_json(run.result.executor_meta))}</pre>')
                parts.append("</div>")

        # Error
        if run.error:
            parts.append('<div class="detail-section">')
            parts.append('<div class="detail-label">Error</div>')
            parts.append(f'<div class="error-box">{_e(run.error)}</div>')
            parts.append("</div>")
            if run.error_category:
                parts.append(f'<div style="font-size: 12px; color: var(--text3); margin-top: 4px;">Category: {_e(run.error_category)}</div>')

        # Chain context compilation info from events
        events = store.load_step_events(run.id)
        chain_events = [e for e in events if e.get("type") == "chain.context_compiled"]
        for evt in chain_events:
            data = evt.get("data", {})
            chain_name = data.get("chain", "")
            transcript_count = data.get("transcript_count", 0)
            total_tokens = data.get("total_tokens", 0)
            parts.append('<div class="detail-section">')
            parts.append('<div class="detail-label">Chain Context</div>')
            parts.append(
                f'<div style="font-size: 12px; color: #8b5cf6; padding: 6px 10px; '
                f'background: rgba(139, 92, 246, 0.08); border: 1px solid rgba(139, 92, 246, 0.2); '
                f'border-radius: 6px;">&#x26d3; Chain <strong>{_e(chain_name)}</strong> &mdash; '
                f'{transcript_count} prior transcript{"s" if transcript_count != 1 else ""}, '
                f'~{total_tokens:,} tokens injected</div>'
            )
            parts.append("</div>")

        # Exit rule info from events
        exit_events = [e for e in events if e.get("type") == "exit_resolved"]
        for evt in exit_events:
            data = evt.get("data", {})
            rule_name = data.get("rule_name", "")
            action = data.get("action", "")
            if rule_name:
                parts.append(f'<div class="exit-rule-fired">Exit: {_e(rule_name)} &rarr; {_e(action)}</div>')

        if len(sr.runs) > 1:
            parts.append("</div>")  # attempt wrapper

    parts.append("</div>")  # step-detail-body
    parts.append("</details>")  # step-detail
    return "\n".join(parts)


# ── YAML Appendix ────────────────────────────────────────────────────


def _html_yaml_appendix(yaml_source: str, flow_path: Path | None) -> str:
    if not yaml_source:
        return ""

    filename = flow_path.name if flow_path else "flow.yaml"
    parts = ['<div class="section">']
    parts.append(f'<details class="yaml-appendix">')
    parts.append(f'<summary><span class="chevron">&#x25b6;</span> Flow Source &mdash; {_e(filename)}</summary>')
    parts.append('<div class="yaml-body" style="position: relative;">')
    parts.append(f'<pre>{_e(yaml_source)}</pre>')
    parts.append("</div>")
    parts.append("</details>")
    parts.append("</div>")
    return "\n".join(parts)


# ── Footer ───────────────────────────────────────────────────────────


def _html_footer(job: Job, step_reports: dict[str, StepReport]) -> str:
    total_cost = sum(sr.cost for sr in step_reports.values())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    parts = ['<div class="report-footer">']
    parts.append(f"<span>Generated by Stepwise &middot; {now}</span>")
    if total_cost > 0:
        parts.append(f"<span>Total cost: {_format_cost(total_cost)}</span>")
    else:
        parts.append(f"<span>Job: {_e(job.id)}</span>")
    parts.append("</div>")
    return "\n".join(parts)


def _html_tail() -> str:
    return """
</div>
</body>
</html>"""
