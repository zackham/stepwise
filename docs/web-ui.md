# Web UI

Stepwise ships a web dashboard for watching jobs execute, inspecting step data, providing human input, and editing flows. It runs as part of the Stepwise server — no separate install.

## Opening the Dashboard

Two ways to get to the UI:

```bash
# Ephemeral — starts a server, opens the browser, stops on Ctrl+C
stepwise run my-flow.flow.yaml --watch

# Persistent — runs in the background, serves UI at the configured port
stepwise server start --detach
```

The server binds to port 8340 by default (`--port` to override). Open `http://localhost:8340` in your browser.

`--watch` mode is the fastest path to visual feedback. It starts a server, creates the job, opens the browser, and tears everything down on Ctrl+C. Use `server start --detach` when you want the server to persist across multiple jobs.

## Job List

The landing page shows all jobs across the system. Each row displays the job name, status, flow name, creation time, and duration.

**Filtering:**
- **Status tabs:** running, awaiting input, paused, completed, failed, pending, cancelled
- **Time range:** today, 7d, 30d, all
- **Search:** filters by job name or ID

Click a job to open its detail view.

**Creating jobs:** The "New Job" button opens a dialog where you pick a flow, set inputs, and optionally name the job. Jobs created from the UI are owned by the server.

**Tip:** Always pass `--name` when launching jobs from the CLI. The job list shows the name as the primary label — without it, every job shows as "implement" or "plan-light" with no way to tell them apart:

```bash
stepwise run my-flow --name "deploy: staging" --input env="staging"
```

## DAG View

The primary visualization. Shows the job's workflow as an interactive dependency graph.

### Reading the DAG

Each node is a step. The visual encoding:

**Node status** (background/border color):
- Pending — zinc/gray
- Running — blue, animated
- Completed — emerald/green
- Failed — red
- Suspended — amber/yellow (waiting for input)
- Waiting reset — amber (waiting for upstream re-run)
- Throttled — orange

**Executor type** (left border accent):
- `script` — cyan
- `agent` — violet
- `llm` — blue
- `external` — amber
- `poll` — indigo

Edges show data flow between steps. Animated edges indicate active data transfer. Loopback edges (from exit rules with `action: loop`) are drawn as curved arrows pointing backward.

### Interaction

- **Pan and zoom:** Scroll to zoom, drag to pan.
- **Follow flow:** When a job is running, the view auto-scrolls to keep the active step visible. Toggle this off to explore freely.
- **Click a step** to open the Step Detail Panel.
- **Sub-flows** expand inline within their parent step node. For-each steps show each iteration as a separate lane.

## View Modes

The job detail page supports four view modes, switched via tabs or URL search params (`?view=`):

| Mode | URL param | What it shows |
|------|-----------|---------------|
| **DAG** | `?view=dag` | Interactive dependency graph (default) |
| **Events** | `?view=events` | Chronological event log — every state transition, input resolution, cost event |
| **Timeline** | `?view=timeline` | Gantt-style timeline showing step execution in parallel |
| **Tree** | `?view=tree` | Hierarchical job tree — parent jobs, sub-jobs, delegation chains |

**Events view** is the audit trail — every state transition, every input resolution, every cost event in chronological order. Use it to understand exactly what the engine did and when. Each event is timestamped and shows the step name, event type, and payload.

**Timeline view** shows a Gantt-style chart of step execution. Steps that ran in parallel appear on separate rows at the same time offset. This makes it easy to identify bottlenecks — the longest bar is where the time went.

**Tree view** is for delegation workflows. When a step emits a sub-flow (via `emit_flow: true`) or spawns sub-jobs, the tree shows the parent-child relationship across all levels of nesting.

## Step Detail Panel

Click any step in the DAG to open the detail panel. Three tabs (`?tab=`):

### Step tab (`?tab=step`)

Shows the step's current state:
- **Status** and attempt number
- **Inputs** — resolved values passed to the executor
- **Outputs** — the artifact produced (JSON, expandable)
- **Executor metadata** — model used, token counts, cost, latency
- **Sidecar** — decisions, assumptions, confidence levels (if the executor provided them)
- **Timing** — start time, duration, queue wait

For agent steps, the **Agent Stream** section shows the agent's output in real time — text generation, tool calls, and tool results as they happen. This streams via WebSocket, so you see it live, not after the fact. You can watch an agent reason through a problem, call tools, evaluate results, and iterate — all in the panel. This is the observability that turns "the agent ran for 40 minutes" into "I watched the agent work."

For failed steps, the error message and stack trace are shown inline with the full context of what inputs the step received, making it straightforward to diagnose what went wrong.

### Data Flow tab (`?tab=data-flow`)

Visualizes how data moves through this step:
- **Upstream** — which steps provided input, and what values
- **Downstream** — which steps consume this step's output
- **Resolution** — how input bindings were resolved (including optional inputs that resolved to None)

### Job tab (`?tab=job`)

Job-level metadata:
- Job ID, name, status, objective
- Creation time, runner, ownership
- Full input set
- Terminal outputs (from completed terminal steps)

## External Input

When a job reaches an `external` step, it suspends and waits for human input. The step appears in the DAG with an amber glow.

Click the suspended step to open the **input form**. The form is generated from the step's declared `outputs` — each output becomes a typed field. If the step defines `output_fields` with types and descriptions, the form uses those for labels, placeholders, and validation.

Three ways to provide input:

| Method | How |
|--------|-----|
| **Web UI** | Fill in the form, click Submit |
| **CLI** | `stepwise fulfill <run-id> '{"decision": "approve"}'` |
| **API** | `POST /api/jobs/{job_id}/runs/{run_id}/fulfill` with JSON body |

After fulfillment, the job resumes immediately. The DAG updates in real time — you'll see the suspended step transition to completed and downstream steps begin executing.

**Suspension inbox:** To see all suspended steps across all active jobs, use:

```bash
stepwise list --suspended
```

This is the agent's "inbox" — the list of places where human input is needed. In the web UI, the job list's "awaiting input" tab serves the same purpose.

**Schema-driven forms:** When a step declares `output_fields` with types like `enum`, `text`, `number`, or `boolean`, the web UI renders appropriate form controls — dropdowns, text areas, number inputs, checkboxes. This makes human input structured and validated, not freeform.

## Canvas View

The canvas page (`/canvas`) shows a multi-job overview — all active jobs laid out on a single interactive canvas. Each job appears as a miniature DAG with its current status. This is useful when you're running many jobs simultaneously and want a birds-eye view of system activity.

Jobs are grouped by status. Click any job to jump to its detail view.

## Flow Editor

The editor page (`/editor`) provides a visual flow authoring environment with four integrated panels:

**YAML editor** — CodeMirror-based with syntax highlighting, error markers, and auto-completion for step references. Changes are validated in real-time — structural errors appear inline before you save.

**Chat panel** — Agent-assisted flow editing. Describe what you want in natural language ("add an approval step after deploy") and the AI modifies the YAML directly. Supports multiple AI backends (Claude, Codex, or a simpler completion mode). The chat has full context of your current flow, so it understands step names, input bindings, and executor types.

**Step panel** — Click any step in the preview DAG to edit its properties visually: executor type, prompt, inputs, outputs, exit rules. Changes sync back to the YAML editor.

**Registry browser** — Search the [stepwise.run](https://stepwise.run) registry, preview flow details, and install community flows into your project with one click. Installed flows appear in your local flow list.

**File tree** — Browse and switch between flow files in your project. Create new flows, rename, or delete from the sidebar.

## Flows Page

The flows page (`/flows`) lists all flow files discovered in your project — both in the project root and `flows/` subdirectories. Each flow shows its name, step count, and executor types used.

Click a flow to see its structure, or open it in the editor for modification.

## Settings

The settings page (`/settings`) manages runtime configuration:

**API keys** — Configure keys for LLM providers (OpenRouter, Anthropic, etc.). Keys are stored locally in the project's `.stepwise/` directory, never sent anywhere except to the LLM provider.

**Default model** — Set which model LLM and agent steps use when no explicit `model` is configured in the flow.

**Model labels** — Create short aliases for long model identifiers. For example, define `fast` → `anthropic/claude-haiku-4-5-20251001` and then use `model: fast` in your flows. This makes it easy to switch models across all flows by updating one label.

**OpenRouter model search** — Browse and search available models if you're using OpenRouter as your provider.

## Common patterns

**Watch a flow from start to finish:**
```bash
stepwise run my-flow --watch --input task="build the API"
```
The browser opens automatically. Watch the DAG animate, provide input at external steps, and see the result.

**Monitor a long-running background job:**
Start the server, fire a job async, then check the dashboard periodically:
```bash
stepwise server start --detach
stepwise run my-flow --async --name "nightly: data pipeline" --input date="2026-03-30"
# Open http://localhost:8340 to watch progress
```

**Triage suspended jobs:**
Filter the job list to "awaiting input" to see all jobs waiting for human decisions. Click through to each, review the context, and provide input.

## What's next

- [Quickstart](quickstart.md) — run your first flow and see it in the UI
- [Writing Flows](writing-flows.md) — author workflows that use all step types
- [CLI Reference](cli.md) — server management, job control, and fulfillment commands
