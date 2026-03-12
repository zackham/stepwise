# Stepwise

**Step into your flow.** Portable workflow orchestration for agents and humans.

Stepwise is a workflow engine that coordinates multi-step jobs where steps can be scripts, LLM calls, autonomous AI agents, or human decisions. Define your workflow as a YAML file, run it from the command line, and optionally watch it execute in a real-time visual UI.

## Documentation

| Doc | Description |
|-----|-------------|
| [Quickstart](docs/quickstart.md) | Install, first flow, loops, human gates — 5 minutes |
| [Why Stepwise](docs/why-stepwise.md) | Motivation, design philosophy, competitive positioning |
| [Concepts](docs/concepts.md) | Mental model: jobs, steps, executors, dependencies, loops, for-each |
| [Executors](docs/executors.md) | Deep dive on all 4 executor types + decorators |
| [YAML Format](docs/yaml-format.md) | Complete `.flow.yaml` schema reference |
| [CLI Reference](docs/cli.md) | All commands, flags, examples, exit codes |
| [API Reference](docs/api.md) | REST endpoints, WebSocket protocol, error handling |
| [Flow Sharing](docs/flow-sharing.md) | Registry, `stepwise flow` commands |

## Install

```bash
pip install stepwise          # core (scripts, agents, human steps)
pip install "stepwise[llm]"   # + LLM steps via OpenRouter
```

## Quick Start

```bash
# Initialize a project
stepwise init

# Create a flow
cat > hello.flow.yaml << 'EOF'
name: hello-world
steps:
  greet:
    run: 'echo "{\"message\": \"Hello from Stepwise!\"}"'
    outputs: [message]
EOF

# Validate it
stepwise validate hello.flow.yaml

# Run it headless
stepwise run hello.flow.yaml

# Or run with the live web UI
stepwise run hello.flow.yaml --watch
```

`--watch` starts an ephemeral server on a random port and opens the web UI. Steps execute automatically — agents stream output in real time, human steps pause and wait for your input.

## CLI Commands

```
stepwise init                        # Create .stepwise/ project
stepwise run <flow.yaml>             # Headless execution
stepwise run <flow.yaml> --watch     # Live web UI
stepwise run <flow.yaml> --wait      # Block, JSON output (for agents)
stepwise run <flow.yaml> --async     # Fire-and-forget background run
stepwise run <flow.yaml> --var K=V   # Pass inputs
stepwise schema <flow.yaml>          # Input/output schema (JSON)
stepwise output <job-id>             # Retrieve job outputs
stepwise fulfill <run-id> '{...}'    # Satisfy a human step
stepwise agent-help                  # Generate agent instructions
stepwise validate <flow.yaml>        # Check a flow for errors
stepwise jobs                        # List jobs (--output json, --status, --limit)
stepwise status <job-id>             # Step-by-step detail
stepwise cancel <job-id>             # Cancel a running job
stepwise serve                       # Persistent server mode
stepwise templates                   # List available templates
stepwise config set <key> <value>    # Configure (API keys, models)
stepwise config get <key>            # Read config (masks secrets)
stepwise flow get <url>              # Download a flow from URL
stepwise --version                   # Print version
```

See [`docs/cli.md`](docs/cli.md) for full command reference with all flags and examples.

## Agent Integration

Stepwise flows are callable as tools by external agents (Claude Code, Codex, etc.) via CLI. No MCP servers, no protocol layers — just bash commands that return JSON.

```bash
# Agent runs a flow and gets structured JSON back
stepwise run council.flow.yaml --wait --var question="Should we use Postgres?"
# → {"status": "completed", "job_id": "job-...", "outputs": [{...}]}

# Generate instructions for CLAUDE.md
stepwise agent-help --update CLAUDE.md
```

The `agent-help` command scans your project for flows and generates a markdown block with per-flow usage, expected output shapes, and a CLI quick reference. Paste it into your agent's instructions file and it can call your flows.

Key design principles:
- **Stdout purity**: `--wait` prints ONLY JSON to stdout. Zero logging, zero progress noise.
- **Actionable errors**: Missing inputs? The error message includes the exact `--var` flags to fix it.
- **No server required**: `--async` spawns a detached background process. No `stepwise serve` prerequisite.
- **Exit codes**: 0=success, 1=failed, 2=input error, 3=timeout, 4=cancelled.

## Defining Flows

Flows are `.flow.yaml` files. Steps run in dependency order — if step B consumes an output from step A, B waits for A. Steps with no data dependencies run in parallel.

### Simple pipeline

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

### Loops and human review

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

When the reviewer selects "revise", the engine loops back to `draft` with the feedback. After 5 attempts, it escalates. On "approve", it advances to `publish`.

### Flow metadata

Flows support optional metadata for discovery and sharing:

```yaml
name: pr-review
description: AI-powered pull request review with human approval gate
author: zack
version: "1.0"
tags: [code-review, agent, human-in-the-loop]
steps:
  # ...
```

If `name` is omitted, it defaults from the filename (`my-flow.flow.yaml` → `my-flow`). The `author` field can be auto-populated from `git config user.name`.

See [`docs/yaml-format.md`](docs/yaml-format.md) for the complete format reference, or [`docs/concepts.md`](docs/concepts.md) for the mental model.

## Executor Types

| Type | Description | Execution |
|------|-------------|-----------|
| **script** | Run any shell command. Outputs parsed from stdout JSON. | Synchronous |
| **llm** | Single LLM call via OpenRouter. Structured output extraction. | Synchronous |
| **agent** | Autonomous AI agent via [ACP](https://agentclientprotocol.com). Real-time streaming. | Async (polled) |
| **human** | Pauses the job and waits for input (web UI or stdin). | Suspended |
| **mock_llm** | Deterministic mock for testing. | Synchronous |

## Features

- **DAG-based engine** with conditional loops, exit rules, and expression-based branching
- **Progressive CLI** — headless `run`, ephemeral `--watch` UI, persistent `serve`
- **Project-local `.stepwise/`** directory (like `.git/`) with SQLite DB, jobs, templates
- **Real-time streaming** of agent output (text + tool calls) via WebSocket
- **Human-in-the-loop** — stdin prompts in headless mode, web UI in watch mode
- **Expression exit rules** for dynamic control flow (`outputs.score >= 0.8`, `attempt < 5`)
- **YAML workflow format** — self-contained, shareable `.flow.yaml` files
- **Context chains** — session continuity across agent steps via compiled conversation transcripts
- **Cost tracking and step limits** — cap spend, duration, or iterations per step
- **Signal handling** — Ctrl+C cleanly cancels active runs
- **SQLite persistence** with WAL mode and crash recovery
- **Decorators** — timeout, retry, fallback, notification (composable per-step)
- **Template library** for reusable workflows
- **Sub-job delegation** for hierarchical workflows

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

## Configuration

```bash
# Set OpenRouter API key for LLM steps
stepwise config set openrouter_api_key sk-or-...

# Set default model
stepwise config set default_model anthropic/claude-sonnet-4-20250514

# View config (secrets masked by default)
stepwise config get openrouter_api_key       # **********xyz
stepwise config get openrouter_api_key --unmask  # sk-or-...
```

Config is stored in `~/.config/stepwise/config.json`.

## Development

```bash
git clone https://github.com/zackham/stepwise.git
cd stepwise

# Backend
uv sync
uv run pytest tests/           # 407 Python tests

# Frontend
cd web
npm install
npm run dev                    # dev server at :5173, proxies to :8340
npm test                       # frontend tests

# Build web assets into package
make build-web
```

**Requirements:** Python 3.12+, Node 20+ (for frontend development)

**Project structure:**

```
src/stepwise/
  cli.py          # CLI entry point (14 subcommands)
  runner.py       # Headless execution, terminal reporter, signal handling
  project.py      # .stepwise/ directory management
  engine.py       # Core DAG engine, tick loop, exit rule evaluation
  models.py       # Job, Step, ExitRule, FlowMetadata, etc.
  executors.py    # Script, LLM, Human, Mock executors
  agent.py        # Agent executor (ACP protocol) + transcript capture
  context.py      # Context chain compilation (M7a)
  server.py       # FastAPI REST + WebSocket server
  store.py        # SQLite persistence layer
  yaml_loader.py  # YAML → WorkflowDefinition parser
  registry_factory.py  # Shared executor registration
  config.py       # Configuration management
  decorators.py   # Timeout, retry, fallback, notification
web/              # React frontend (Vite, TanStack, Tailwind, shadcn/ui)
```

## API

The server (`stepwise serve`) runs at `http://localhost:8340` by default. 27 REST endpoints + WebSocket.

| Category | Key Endpoints |
|----------|--------------|
| **Jobs** | `GET /api/jobs`, `POST /api/jobs`, `POST /api/jobs/{id}/start`, `POST /api/jobs/{id}/cancel` |
| **Runs** | `POST /api/runs/{id}/fulfill`, `GET /api/runs/{id}/agent-output`, `GET /api/runs/{id}/cost` |
| **Engine** | `GET /api/status`, `POST /api/tick`, `GET /api/executors` |
| **Config** | `GET /api/config`, `PUT /api/config/api-key`, `PUT /api/config/models` |
| **WebSocket** | `ws://localhost:8340/ws` — tick updates + agent output streaming |

**Swagger UI:** `http://localhost:8340/docs`

See [`docs/api.md`](docs/api.md) for full endpoint documentation, request/response schemas, WebSocket protocol, and examples.

## License

MIT
