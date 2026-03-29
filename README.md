<p align="center">
  <img src="brand/stepwise-icon.png" width="120" alt="Stepwise logo" />
</p>

<h1 align="center">Stepwise</h1>

<p align="center">
  <strong>Deterministic scaffolding for nondeterministic work.</strong><br/>
  Observable runs. Scoped delegation. Human gates. Replays. Auditability.
</p>

<p align="center">
  <a href="https://stepwise.run"><strong>Homepage</strong></a> · <a href="docs/quickstart.md">Quickstart</a> · <a href="docs/agent-integration.md">Agent Integration</a> · <a href="docs/concepts.md">Concepts</a> · <a href="docs/cli.md">CLI Reference</a> · <a href="docs/api.md">API Reference</a>
</p>

---

AI agents are powerful. But power without structure is chaos. An agent that can't be observed, paused, or audited is an agent you can't trust with real work.

**The intelligence commoditizes. The harness does not.**

Stepwise is the harness. You define a workflow in YAML — steps, dependencies, branching logic, human checkpoints. Stepwise builds a DAG, runs independent steps in parallel, streams agent output in real time, and pauses at the gates you set. Every run is observable, replayable, and auditable.

## 60-second start

```bash
# Install
curl -fsSL https://raw.githubusercontent.com/zackham/stepwise/master/install.sh | sh

# Run the interactive demo — opens a real-time web UI
stepwise welcome
```

That's it. You'll see a live DAG, agent steps executing, and human checkpoints where the workflow pauses for your input.

## What a flow looks like

A `.flow.yaml` defines steps, their dependencies, and what happens at each decision point. This flow runs tests, branches on the result, and loops if fixes are needed:

```yaml
name: test-and-fix
steps:
  run-tests:
    run: './test.sh'
    outputs: [status, failures]

  open-pr:
    executor: agent
    prompt: "All tests pass. Open a PR with this summary: $status"
    inputs:
      status: run-tests.status
    when: "status == 'pass'"
    outputs: [pr_url]

  fix-tests:
    executor: agent
    prompt: "These tests failed: $failures — fix them."
    inputs:
      status: run-tests.status
      failures: run-tests.failures
    when: "status == 'fail'"
    outputs: [fixes]
    exits:
      - when: "True"
        action: loop
        target: run-tests
        max_iterations: 3
```

`run-tests` executes a shell script. Based on the output, either `open-pr` or `fix-tests` activates — the `when` conditions are mutually exclusive. If `fix-tests` runs, it loops back to `run-tests` up to 3 times. No orchestration code. No framework. Just YAML.

## Three actors, one DAG

Every step in a Stepwise flow is one of three things:

| | **Agent** | **Human** | **Script** |
|---|---|---|---|
| **What** | LLM or autonomous agent | Person reviewing, deciding, providing input | Shell command, API call, any executable |
| **How** | `executor: agent` or `executor: llm` | `executor: external` — pauses the job | `run: \|` — parses JSON from stdout |
| **When** | Complex reasoning, code generation, analysis | Approvals, creative judgment, escalation | Data fetching, testing, deterministic transforms |

The DAG doesn't care which actor runs a step. An agent step that fails can escalate to a human. A human approval can trigger a script. A script's output feeds the next agent. They compose.

## Agents call flows as tools

Stepwise flows are callable by AI agents (Claude Code, Codex, etc.) via plain CLI. No MCP server, no protocol layer — just a bash command that returns JSON:

```bash
stepwise run my-flow --wait --input question="Should we use Postgres?"
# → {"status": "completed", "outputs": [{"verdict": "yes", "reasoning": "..."}]}
```

`--wait` prints only JSON to stdout — zero logging, zero progress noise. Generate per-flow tool docs for your agent with:

```bash
stepwise agent-help --update CLAUDE.md
```

## Key capabilities

- **DAG engine** — automatic parallelism, conditional branching (`when`), expression-based exit rules, loops with safety caps
- **Five executor types** — script, llm, agent, external (human gate), poll (wait for external condition)
- **Four run modes** — headless `run`, ephemeral `--watch` with web UI, blocking `--wait` for JSON output, fire-and-forget `--async`
- **Real-time streaming** — agent output (text + tool calls) streamed live via WebSocket to the web UI
- **Human gates** — `external` steps pause the job for approval, feedback, or structured input (web UI, CLI, or API)
- **Escalation** — `action: escalate` pauses a job for human triage when an agent gets stuck
- **For-each fan-out** — run a sub-pipeline for each item in a list, with independent error handling
- **Agent flow emission** — agents can dynamically generate and delegate to sub-workflows
- **Session continuity** — agents reuse conversation context across loop iterations
- **Step caching** — content-addressable cache skips re-execution when inputs haven't changed
- **Decorators** — timeout, retry, fallback — composable per step

## CLI

```
stepwise run <flow> [--watch|--wait|--async]   Run a flow
stepwise welcome                               Interactive demo
stepwise server start [--detach]               Persistent server with web UI
stepwise validate <flow>                       Check flow for errors + warnings
stepwise jobs                                  List all jobs
stepwise status <job-id>                       Step-by-step detail
stepwise fulfill <run-id> '{...}'              Satisfy an external step
stepwise agent-help                            Generate agent tool docs
stepwise update                                Upgrade to latest version
```

Full reference: [`docs/cli.md`](docs/cli.md)

## Docs

| Doc | What you'll learn |
|-----|-------------------|
| [Quickstart](docs/quickstart.md) | Install, first flow, loops, external gates — 5 minutes |
| [Agent Integration](docs/agent-integration.md) | Making flows callable by AI agents |
| [Why Stepwise](docs/why-stepwise.md) | Design philosophy and motivation |
| [Concepts](docs/concepts.md) | Jobs, steps, executors, dependencies, loops, for-each |
| [Executors](docs/executors.md) | All executor types + decorators |
| [YAML Format](docs/yaml-format.md) | Complete `.flow.yaml` schema reference |
| [CLI Reference](docs/cli.md) | All commands, flags, examples, exit codes |
| [API Reference](docs/api.md) | REST endpoints, WebSocket protocol |
| [Comparison](docs/comparison.md) | How Stepwise compares to other tools |

## Development

```bash
git clone https://github.com/zackham/stepwise.git && cd stepwise

# Backend (Python 3.12+)
uv sync && uv run pytest tests/

# Frontend (Node 20+)
cd web && npm install && npm run dev     # dev server at :5173, proxies to :8340
```

## License

MIT
