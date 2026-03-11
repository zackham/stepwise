# Stepwise

**Portable workflow orchestration for agents and humans.**

<!-- badges placeholder -->
<!-- ![PyPI](https://img.shields.io/pypi/v/stepwise) -->
<!-- ![Tests](https://img.shields.io/github/actions/workflow/status/...) -->
<!-- ![License](https://img.shields.io/badge/license-MIT-blue) -->

Stepwise is a workflow engine that coordinates multi-step jobs where steps can be scripts, LLM calls, autonomous AI agents, or human decisions. Define your workflow as a DAG with conditional loops and exit rules, then watch it execute in a real-time visual UI with live agent output streaming.

<!-- TODO: add screenshot -->

## Features

- **DAG-based engine** with conditional loops, exit rules, and expression-based branching
- **Real-time streaming** of agent output (text + tool calls) via WebSocket
- **Multiple executor types** — scripts, LLMs, AI agents (via [ACP](https://agentclientprotocol.com)), human-in-the-loop
- **Visual web UI** with live DAG, step detail panels, run history, and inline agent output
- **Expression exit rules** for dynamic control flow (`outputs.score >= 0.8`, `attempt < 5`)
- **Sub-job delegation** for hierarchical workflows
- **YAML or JSON** workflow definitions
- **Template library** for reusable, shareable workflows
- **SQLite persistence** with crash recovery
- **Cost tracking and step limits** — cap spend, duration, or iterations per step
- **Decorators** — timeout, retry, fallback, notification (composable per-step)

## Quick Start

```bash
pip install stepwise    # or: uv add stepwise
```

Start the server:

```bash
python -m stepwise.server
# → http://localhost:8340
```

Open the UI, create a job from a template, and hit Start. Steps execute automatically — agents stream output in real time, human steps pause and wait for your input.

## Defining Workflows

### Simple linear workflow (YAML)

```yaml
name: deploy-pipeline

steps:
  build:
    run: scripts/build.sh
    outputs: [artifact, version]

  test:
    run: scripts/test.sh
    outputs: [passed, coverage]
    inputs:
      artifact: build.artifact

  deploy:
    run: scripts/deploy.sh
    outputs: [url]
    inputs:
      artifact: build.artifact
      version: build.version
```

Steps run in dependency order. `deploy` waits for `build` because it consumes `build.artifact`. `test` also waits for `build`. Steps with no data dependencies run in parallel.

### Workflow with loops and human review

```yaml
name: iterative-review

steps:
  draft:
    run: scripts/draft.py
    outputs: [content, word_count]
    inputs:
      topic: $job.topic
      prior_feedback: review.feedback

  review:
    executor: human
    prompt: "Review this draft. Approve or request revisions."
    outputs: [decision, feedback]
    inputs:
      content: draft.content

    exits:
      - name: approve
        when: "outputs.decision == 'approve'"
        action: advance

      - name: revise
        when: "outputs.decision == 'revise' and attempt < 5"
        action: loop
        target: draft

      - name: max_revisions
        when: "attempt >= 5"
        action: escalate

  publish:
    run: scripts/publish.py
    outputs: [url]
    inputs:
      content: draft.content
    sequencing: [review]
```

When the human reviewer selects "revise", the engine loops back to `draft` with the feedback. After 5 attempts, it escalates for manual intervention. On "approve", it advances to `publish`.

See [`docs/yaml-format.md`](docs/yaml-format.md) for the complete format reference.

## Executor Types

| Type | Description | Execution |
|------|-------------|-----------|
| **script** | Run any shell command or script. Outputs parsed from stdout JSON. | Synchronous |
| **llm** | Single LLM call via OpenRouter. Structured output extraction. | Synchronous |
| **agent** | Autonomous AI agent session via [ACP](https://agentclientprotocol.com). Real-time output streaming. | Async (polled) |
| **human** | Pauses the job and waits for human input via the UI. | Suspended |
| **mock_llm** | Deterministic mock for testing. | Synchronous |

Agent steps stream their output — every text chunk and tool call appears in the UI as it happens. Cost and duration are tracked per step, with configurable limits that the engine enforces.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Web UI (React)                       │
│  DAG Visualization  ·  Step Panels  ·  Agent Streaming   │
└──────────────────────────┬──────────────────────────────┘
                           │ WebSocket + REST
┌──────────────────────────┴──────────────────────────────┐
│                   FastAPI Server                          │
│  /api/jobs  ·  /api/runs  ·  /api/templates  ·  /ws      │
├──────────────────────────┬──────────────────────────────┤
│         Engine           │        SQLite Store           │
│  DAG resolution          │  Jobs, steps, runs, events    │
│  Exit rule evaluation    │  Crash recovery               │
│  Loop management         │  Agent output streams         │
│  Cost enforcement        │                               │
├──────────┬───────┬───────┴──┬────────────┐              │
│  Script  │  LLM  │  Agent   │   Human    │  ← Executors │
│          │       │  (ACP)   │            │              │
└──────────┴───────┴──────────┴────────────┘
```

The **Engine** ticks continuously, resolving step dependencies and dispatching ready steps to executors. Each step run is persisted to **SQLite** — the engine can recover from crashes mid-job. The **FastAPI server** exposes REST endpoints for job management and a WebSocket for real-time UI updates and agent output streaming.

## Claude Code Integration

Stepwise ships with a Claude Code skill for generating workflows from natural language:

```
.claude/skills/create-workflow/
```

Install the skill in your Claude Code project, then:

```
> create a workflow that reviews PRs with an AI agent, gets human approval, then merges
```

Claude Code will generate a valid Stepwise YAML workflow, create the job via the API, and start it.

## API

The server runs at `http://localhost:8340` by default (configure with `STEPWISE_PORT`).

**REST endpoints:**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/jobs` | List all jobs |
| `POST` | `/api/jobs` | Create a job |
| `POST` | `/api/jobs/{id}/start` | Start a job |
| `GET` | `/api/jobs/{id}/tree` | Job hierarchy (parent + sub-jobs) |
| `POST` | `/api/runs/{id}/fulfill` | Submit human step response |
| `GET` | `/api/runs/{id}/agent-output` | Stream agent output |
| `GET/POST` | `/api/templates` | Template CRUD |
| `GET` | `/api/config` | Server configuration |

**WebSocket:** `ws://localhost:8340/ws` — real-time job state updates and agent output streaming.

**Auto-docs:** `http://localhost:8340/docs` (Swagger UI) and `http://localhost:8340/redoc`.

## Development

```bash
git clone https://github.com/user/stepwise.git
cd stepwise

# Backend
uv sync
uv run python -m stepwise.server

# Frontend (separate terminal)
cd web
npm install
npm run dev

# Tests
uv run pytest tests/           # 236 Python tests
cd web && npm test             # Frontend tests
```

**Requirements:** Python 3.12+, Node 20+

**Project structure:**

```
src/stepwise/
  engine.py       # Core DAG engine, tick loop, exit rule evaluation
  models.py       # Data structures: Job, Step, ExitRule, etc.
  executors.py    # Script, LLM, Human, Mock executors
  agent.py        # Agent executor (ACP protocol)
  server.py       # FastAPI REST + WebSocket server
  store.py        # SQLite persistence layer
  yaml_loader.py  # YAML → WorkflowDefinition parser
  decorators.py   # Timeout, retry, fallback, notification
  config.py       # Server configuration
web/
  src/
    components/dag/   # DAG visualization (dagre layout)
    components/jobs/  # Job list, detail, events
    hooks/            # useStepwise, useAgentStream, WebSocket
    pages/            # Dashboard, job detail, builder
templates/            # Reusable workflow templates
examples/             # Example scripts
docs/                 # Format specs and design docs
```

## Contributing

Contributions are welcome. Please:

1. Fork the repo and create a feature branch
2. Add tests for new functionality
3. Run `uv run pytest tests/` and `cd web && npm test` before submitting
4. Open a PR with a clear description of the change

For larger changes, open an issue first to discuss the approach.

## License

MIT
