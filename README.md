<p align="center">
  <img src="brand/stepwise-icon.png" width="120" alt="Stepwise logo" />
</p>

<h1 align="center">Stepwise</h1>

<p align="center">
  <strong>Step into your flow.</strong><br/>
  Portable workflow orchestration for agents and humans.
</p>

<p align="center">
  <a href="https://stepwise.run"><strong>Homepage</strong></a> · <a href="docs/quickstart.md">Quickstart</a> · <a href="docs/concepts.md">Concepts</a> · <a href="docs/cli.md">CLI Reference</a> · <a href="docs/api.md">API Reference</a> · <a href="docs/agent-integration.md">Agent Integration</a>
</p>

---

Stepwise is a workflow engine that coordinates multi-step jobs where each step can be a **shell script**, an **LLM call**, an **autonomous AI agent**, or a **human decision**. Define your workflow as a YAML file, run it from the CLI, and optionally watch it execute in a real-time web UI.

```bash
curl -fsSL https://raw.githubusercontent.com/zackham/stepwise/master/install.sh | sh
```

## Get started in 30 seconds

```bash
# Try the interactive demo — plan, implement, review, deploy
stepwise run @stepwise:welcome --watch
```

`--watch` opens a browser with a real-time DAG visualization. Steps execute automatically — agents stream output live, human steps pause and wait for your input.

```bash
# Or create your own flow
stepwise new my-flow                   # scaffold flows/my-flow/FLOW.yaml
stepwise run my-flow --watch           # run it in the browser
```

## How it works

You write a `.flow.yaml` file describing your steps and their dependencies. Stepwise builds a DAG, figures out what can run in parallel, and executes everything in the right order. When a step needs human input, the whole job pauses until you respond.

### A simple pipeline

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

`test` waits for `build` because it needs `build.artifact`. `deploy` waits for both. Steps with no data dependencies run in parallel automatically.

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

The reviewer picks "revise" and the engine loops back to `draft` with the feedback attached. After 5 attempts it escalates. On "approve" it advances to `publish`.

## Executor types

| Type | What it does |
|------|-------------|
| **script** | Runs any shell command, parses JSON from stdout |
| **llm** | Single LLM call via OpenRouter with structured output |
| **agent** | Autonomous AI agent via [ACP](https://agentclientprotocol.com) with real-time streaming |
| **human** | Pauses the job and waits for input (web UI or stdin) |

## Calling flows from agents

Stepwise flows are callable as tools by AI agents (Claude Code, Codex, etc.) via plain CLI. No MCP servers, no protocol layers — just bash commands that return JSON.

```bash
# Run a flow and get structured output
stepwise run council.flow.yaml --wait --var question="Should we use Postgres?"
# → {"status": "completed", "job_id": "job-...", "outputs": [{...}]}

# Generate per-flow instructions for your agent
stepwise agent-help --update CLAUDE.md
```

`--wait` prints **only** JSON to stdout — zero logging, zero progress noise. Missing an input? The error tells you exactly which `--var` flags to add.

## CLI at a glance

```
stepwise init                        # Create .stepwise/ project
stepwise run <flow> [--watch|--wait|--async]  # Run a flow
stepwise run <flow> --var K=V        # Pass inputs
stepwise serve                       # Persistent server mode
stepwise validate <flow>             # Check for errors
stepwise jobs                        # List jobs
stepwise status <job-id>             # Step-by-step detail
stepwise cancel <job-id>             # Cancel a running job
stepwise fulfill <run-id> '{...}'    # Satisfy a human step
stepwise schema <flow>               # Input/output schema (JSON)
stepwise output <job-id>             # Retrieve job outputs
stepwise agent-help                  # Generate agent instructions
stepwise update                 # Upgrade to latest version
stepwise templates                   # List available templates
stepwise config set <key> <value>    # Configure (API keys, models)
stepwise get <name-or-url>           # Download a flow from registry
stepwise share <flow>                # Publish a flow to registry
stepwise search <query>              # Search the registry
stepwise new <name>                  # Create a new flow
```

See [`docs/cli.md`](docs/cli.md) for the full reference with all flags, examples, and exit codes.

## Features

- **DAG engine** — automatic parallelism, conditional loops, route steps, expression-based branching
- **Three CLI modes** — headless `run`, ephemeral `--watch` UI, persistent `serve`
- **Human-in-the-loop** — stdin prompts in headless mode, web form in watch mode
- **Real-time streaming** — agent output (text + tool calls) via WebSocket
- **Context chains** — session continuity across agent steps via compiled transcripts
- **Expression exit rules** — `outputs.score >= 0.8`, `attempt < 5`, etc.
- **Cost tracking** — cap spend, duration, or iterations per step
- **Decorators** — timeout, retry, fallback, notification (composable per step)
- **SQLite persistence** with WAL mode and crash recovery
- **Project-local `.stepwise/`** directory (like `.git/`)

## Architecture

```
┌────────────────────────────────────────────────────────┐
│                   Web UI (React)                       │
│  DAG Visualization · Step Panels · Agent Streaming     │
└───────────────────────┬────────────────────────────────┘
                        │ WebSocket + REST
┌───────────────────────┴────────────────────────────────┐
│                  FastAPI Server                         │
│  /api/jobs · /api/runs · /api/templates · /ws          │
├───────────────────────┬────────────────────────────────┤
│       Engine          │       SQLite Store             │
│  DAG resolution       │  Jobs, steps, runs, events     │
│  Exit rule eval       │  Crash recovery                │
│  Loop management      │  Agent output streams          │
├─────────┬──────┬──────┴───┬───────────┐               │
│ Script  │ LLM  │  Agent   │  Human    │  ← Executors  │
│         │      │  (ACP)   │           │               │
└─────────┴──────┴──────────┴───────────┘
```

## Configuration

```bash
stepwise config set openrouter_api_key sk-or-...
stepwise config set default_model anthropic/claude-sonnet-4-20250514
stepwise config get openrouter_api_key       # **********xyz
```

Config lives in `~/.config/stepwise/config.json`.

## Development

```bash
git clone https://github.com/zackham/stepwise.git
cd stepwise

# Backend
uv sync
uv run pytest tests/

# Frontend
cd web
npm install
npm run dev        # dev server at :5173, proxies to :8340
npm test

# Build web assets into package
make build-web
```

**Requirements:** Python 3.12+, Node 20+ (for frontend development)

## Docs

| Doc | Description |
|-----|-------------|
| [Quickstart](docs/quickstart.md) | Install, first flow, loops, human gates — 5 minutes |
| [Agent Integration](docs/agent-integration.md) | End-to-end guide for agent callers |
| [Why Stepwise](docs/why-stepwise.md) | Motivation and design philosophy |
| [Concepts](docs/concepts.md) | Jobs, steps, executors, dependencies, loops, for-each, route steps |
| [Executors](docs/executors.md) | Deep dive on all executor types + decorators |
| [YAML Format](docs/yaml-format.md) | Complete `.flow.yaml` schema reference |
| [CLI Reference](docs/cli.md) | All commands, flags, examples, exit codes |
| [API Reference](docs/api.md) | REST endpoints, WebSocket protocol, error handling |
| [Flow Sharing](docs/flow-sharing.md) | Registry commands (`get`, `share`, `search`, `info`) |

## License

MIT
