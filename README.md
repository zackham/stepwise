# Stepwise

**Enter the flow state. Portable orchestration for agents and humans.**

Stepwise is a workflow engine that coordinates multi-step jobs where steps can be scripts, LLM calls, autonomous AI agents, or human decisions. Define your workflow as a YAML file, run it from the command line, and optionally watch it execute in a real-time visual UI.

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
stepwise run <flow.yaml> --var K=V   # Pass inputs
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

See [`docs/yaml-format.md`](docs/yaml-format.md) for the complete format reference.

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
  cli.py          # CLI entry point (10 subcommands)
  runner.py       # Headless execution, terminal reporter, signal handling
  project.py      # .stepwise/ directory management
  engine.py       # Core DAG engine, tick loop, exit rule evaluation
  models.py       # Job, Step, ExitRule, FlowMetadata, etc.
  executors.py    # Script, LLM, Human, Mock executors
  agent.py        # Agent executor (ACP protocol)
  server.py       # FastAPI REST + WebSocket server
  store.py        # SQLite persistence layer
  yaml_loader.py  # YAML → WorkflowDefinition parser
  registry_factory.py  # Shared executor registration
  config.py       # Configuration management
  decorators.py   # Timeout, retry, fallback, notification
web/              # React frontend (Vite, TanStack, Tailwind, shadcn/ui)
```

## API

The server runs at `http://localhost:8340` by default.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/jobs` | List all jobs |
| `POST` | `/api/jobs` | Create a job |
| `POST` | `/api/jobs/{id}/start` | Start a job |
| `GET` | `/api/jobs/{id}/tree` | Job hierarchy |
| `POST` | `/api/runs/{id}/fulfill` | Submit human step response |
| `GET` | `/api/runs/{id}/agent-output` | Agent output for a run |
| `GET/POST` | `/api/templates` | Template CRUD |

**WebSocket:** `ws://localhost:8340/ws` — real-time state updates and agent output streaming.

**Auto-docs:** `http://localhost:8340/docs` (Swagger UI).

## License

MIT
