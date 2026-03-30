<p align="center">
  <img src="brand/stepwise-icon.png" width="120" alt="Stepwise logo" />
</p>

<h1 align="center">Stepwise</h1>

<p align="center">
  <strong>The power meter for agent work.</strong><br/>
  Deterministic scaffolding around nondeterministic work.
</p>

<p align="center">
  <a href="https://stepwise.run"><strong>Homepage</strong></a> · <a href="docs/quickstart.md">Quickstart</a> · <a href="docs/concepts.md">Concepts</a> · <a href="docs/writing-flows.md">Writing Flows</a> · <a href="docs/cli.md">CLI Reference</a> · <a href="docs/web-ui.md">Web UI</a>
</p>

---

You gave an agent a task. It ran for 40 minutes. Did it work?

Most of what we call AI delegation right now isn't really delegation — it's assisted anxiety. You hand something off, then check four times instead of zero. Stepwise fixes this. Define a workflow as a DAG of steps, and Stepwise runs them — scripts, agents, external gates, polls — all observable in real time. The 3am question answered as a URL.

This isn't another agent framework. Stepwise doesn't replace your agents — it gives them a harness. Agent steps run via [ACP](https://agentclientprotocol.com) (Agent Client Protocol) through [acpx](https://github.com/openclaw/acpx) — one protocol surface for Claude, Codex, Gemini, and 15+ coding agents. The intelligence commoditizes. The harness does not.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/zackham/stepwise/master/install.sh | sh
stepwise run @stepwise:demo --watch   # interactive demo — live DAG in your browser
```

## Three audiences, one system

### For agents

Agents reason well. They're bad at remembering what they were doing 45 minutes ago. Stepwise lets an agent offload multi-step work outside its context window — JSON in, structured results out. Zero infrastructure on the agent's side.

```bash
stepwise run deploy-pipeline --wait --input repo="myapp" env="staging"
# → {"status": "completed", "outputs": [{"url": "https://staging.myapp.dev", "healthy": true}]}
```

An agent calls this like any other tool. The flow runs tests, builds containers, deploys, health-checks — ten steps the agent doesn't need in context. Agents can also *emit* sub-flows dynamically, delegating work they discover mid-task.

### For humans

You're the one who approves the production deploy at step 7. Stepwise pauses and waits — in the web UI, CLI, or API. You see exactly what happened before your gate, review the agent's output, and decide.

The live DAG viewer is where it clicks. Steps light up as they run. Agent output streams in real time — you watch the LLM think, see tool calls fire, catch problems as they happen instead of reading a log after the fact. It's a power meter — it doesn't pedal, it shows you the watts. Escalation rules surface stuck jobs before they burn tokens.

### For infrastructure

Fire a job and walk away. Stepwise persists everything to SQLite — if the process crashes, the server restarts and picks up where it left off. Orphaned jobs get detected and adopted. No Redis, no Postgres, no external queue. One binary, one database file.

```bash
stepwise run nightly-analysis --async --input date="2026-03-29"
# Returns immediately. Check later: stepwise jobs | stepwise status <job-id>
```

## What makes it different

**Packaged trust, not raw capability.** The moat isn't the DAG or the YAML — it's observable runs, scoped delegation, human gates, and audit trails. Every step run recorded with inputs, outputs, timing, and attempt count.

**The DAG viewer is the product.** Most orchestrators give you logs. Stepwise gives you a live, animated dependency graph — steps executing in parallel, data flowing between them, sub-flows expanding inline. You *see* the work happening.

**Mixed executors that compose.** A shell script fetches data. An agent analyzes it. An external gate pauses for approval. A poll waits for CI. Another agent acts on the result. Five types, one DAG, zero glue code.

**External steps — not just human gates.** The `external` executor suspends a step and waits for fulfillment from *anyone* — a human in the web UI, a webhook from another service, or another agent via the API. It's a universal suspension primitive with schema-driven typed inputs. Escalation rules promote stuck jobs to human attention.

**Session continuity across steps.** Agent steps can share a persistent session (`continue_session: true`) — the model accumulates context across steps, just like sequential prompts in a conversation. Or start fresh per step. Your choice, per step, in the same flow. Session IDs flow as typed data through the DAG, so the engine knows the dependencies.

**Crash-proof by default.** SQLite WAL mode, heartbeat-based stale detection, automatic orphan recovery. Kill the server, restart it, jobs resume. No message queue, no distributed state.

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

A script runs tests. Based on output, an agent opens a PR or fixes the failures and loops back. Branching, looping, mixed executors — declared, not coded.

## CLI

```
stepwise run <flow> [--watch|--wait|--async]   Run a flow
stepwise server start [--detach]               Persistent server + web UI
stepwise validate <flow>                       Check a flow for errors
stepwise jobs                                  List all jobs
stepwise status <job-id>                       Step-by-step detail
stepwise fulfill <run-id> '{...}'              Fulfill a suspended external step
stepwise agent-help                            Generate agent tool docs
stepwise search <query>                        Find flows in the registry
stepwise update                                Upgrade to latest
```

Full reference: [`docs/cli.md`](docs/cli.md)

## Docs

| Doc | What you'll learn |
|-----|-------------------|
| [Quickstart](docs/quickstart.md) | Install, first flow, loops, external gates — 5 minutes |
| [Concepts](docs/concepts.md) | Jobs, steps, executors, trust model, how agents fit in |
| [Writing Flows](docs/writing-flows.md) | Flow authorship — all step types, wiring, control flow |
| [Web UI](docs/web-ui.md) | The dashboard — DAG viewer, step detail, external input |
| [CLI Reference](docs/cli.md) | Every command, flag, example, exit code |
| [Agent Integration](docs/agent-integration.md) | Making flows callable by AI agents |
| [Executors](docs/executors.md) | All five executor types + decorators |
| [Flow Reference](docs/flow-reference.md) | Complete `.flow.yaml` schema |
| [Why Stepwise](docs/why-stepwise.md) | Design philosophy — the harness, not the intelligence |
| [Comparison](docs/comparison.md) | How Stepwise compares to other tools |
| [Troubleshooting](docs/troubleshooting.md) | Error messages, diagnostics, common fixes |

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
