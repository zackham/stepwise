<p align="center">
  <img src="brand/stepwise-icon.png" width="120" alt="Stepwise logo" />
</p>

<h1 align="center">Stepwise</h1>

<p align="center">
  <strong>Air traffic control for AI work.</strong><br/>
  Deterministic scaffolding around nondeterministic work.
</p>

<p align="center">
  <a href="https://stepwise.run"><strong>Homepage</strong></a> · <a href="docs/quickstart.md">Quickstart</a> · <a href="docs/agent-integration.md">Agent Integration</a> · <a href="docs/concepts.md">Concepts</a> · <a href="docs/cli.md">CLI Reference</a> · <a href="docs/api.md">API Reference</a>
</p>

---

You gave an agent a task. It ran for 40 minutes. Did it work?

Stepwise answers that question as a URL. Open the web UI, watch the DAG animate step by step — scripts running, agents thinking, humans approving — all in real time. Every decision logged, every output captured, every failure visible. The 3am question answered as a URL.

This isn't another agent framework. Stepwise doesn't replace your agents — it gives them structure. You define a workflow as a DAG of steps. Stepwise runs them in parallel where it can, pauses where you tell it to, and recovers from crashes without losing progress. The power meter for agent work.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/zackham/stepwise/master/install.sh | sh
stepwise welcome    # opens a live DAG demo in your browser
```

## Three audiences, one system

### For agents

Agents are good at reasoning. They're bad at remembering what they were doing 45 minutes ago. Stepwise lets an agent offload multi-step work outside its context window: JSON in, results out.

```bash
stepwise run deploy-pipeline --wait --input repo="myapp" env="staging"
# → {"status": "completed", "outputs": [{"url": "https://staging.myapp.dev", "healthy": true}]}
```

An agent calls this like any other tool. The flow runs tests, builds containers, deploys, health-checks — ten steps the agent doesn't need to hold in context. It gets back structured JSON. Agents can also *emit* sub-flows dynamically, delegating work they discover mid-task.

### For humans

You're the one who has to approve the production deploy at step 7. Stepwise pauses the job and waits — in the web UI, in the CLI, or via API. You see exactly what happened before your gate, review the agent's output, and decide.

The live DAG viewer is where it clicks. Steps light up as they run. Agent output streams in real time — you watch the LLM think, see tool calls fire, catch problems as they happen instead of reading a log after the fact. Escalation rules surface stuck jobs before they burn tokens.

### For infrastructure

Fire a job and walk away. Stepwise persists everything to SQLite — if the process crashes, the server restarts and picks up where it left off. Orphaned jobs get detected and adopted. No Redis, no Postgres, no external queue. One binary, one database file.

```bash
stepwise run nightly-analysis --async --input date="2026-03-29"
# Returns immediately. Job runs in the background.
# Check later: stepwise jobs | stepwise status <job-id>
```

## What makes it different

**The DAG viewer is the product.** Most orchestrators give you logs. Stepwise gives you a live, animated dependency graph — steps executing in parallel, edges showing data flow, sub-flows expanding inline. You *see* the work happening.

**Mixed step types that actually compose.** A shell script fetches data. An agent analyzes it. A human approves the analysis. A poll step waits for CI to pass. Another agent acts on the result. Five executor types, one DAG, zero glue code.

**Human-in-the-loop that works.** Not a checkbox feature — a first-class execution model. External steps pause the job with a schema-driven input form. Escalation rules promote stuck agents to human attention. The job tree shows exactly where you're needed.

**Crash-proof by default.** SQLite WAL mode, heartbeat-based stale detection, automatic orphan recovery. Kill -9 the server, restart it, jobs resume. No message queue. No distributed state.

**Observable and auditable.** Every step run is recorded with inputs, outputs, timing, and attempt count. Agent output (text + tool calls) streams live via WebSocket. Job reports generate shareable HTML. The audit trail is the database.

**Registry and marketplace.** Browse, search, and install community flows from [stepwise.run](https://stepwise.run) — directly in the web UI or via `stepwise search`.

## A flow in 30 seconds

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

A script runs tests. Based on output, either an agent opens a PR or an agent fixes the failures and loops back. Branching, looping, mixed executors — declared, not coded.

## CLI

```
stepwise run <flow> [--watch|--wait|--async]   Run a flow
stepwise welcome                               Interactive demo
stepwise server start [--detach]               Persistent server + web UI
stepwise validate <flow>                       Check a flow for errors
stepwise jobs                                  List all jobs
stepwise status <job-id>                       Step-by-step detail
stepwise fulfill <run-id> '{...}'              Provide human input
stepwise agent-help                            Generate agent tool docs
stepwise search <query>                        Find flows in the registry
stepwise update                                Upgrade to latest
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
