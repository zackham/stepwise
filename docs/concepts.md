# Concepts

Stepwise is a power meter for agent work. It doesn't do the pedaling — your agents, scripts, and humans do the actual work. Stepwise shows you the watts: what's running, what's waiting, what failed, and what needs your attention.

The system has three core runtime concepts (jobs, steps, executors), a dependency system (inputs, ordering), and control flow mechanisms (exit rules, branching). Everything else — for-each, caching, sub-jobs, human gates — is built on top of these.

### Quick reference

| Concept | What it is | Key detail |
|---------|-----------|------------|
| **Job** | A unit of work with inputs and a workflow | Persists to SQLite, survives crashes, can spawn sub-jobs |
| **Step** | A typed node in the workflow graph | Declares outputs, executor, inputs, exit rules |
| **Executor** | What does the work inside a step | script, llm, agent, external, poll |
| **Input binding** | Pulls data from upstream outputs | `findings: research.findings` |
| **Exit rule** | Decides what happens after step completion | advance, loop, escalate, abandon |
| **For-each** | Iterates over a list with embedded sub-flows | Items execute in parallel |
| **Branching** | Conditional activation via step-level `when` | Pull-based: each step decides when it runs |

## Jobs

A **job** is a unit of work with an objective, initial inputs, and a workflow.

```bash
stepwise run code-review --input repo="/path/to/repo" --input branch="feature-x"
```

Jobs track their own lifecycle: created → running → completed/failed. They persist to SQLite — if the process restarts, the job resumes where it left off.

Jobs can spawn **sub-jobs**. A planning step might decompose a large objective into smaller pieces, each running its own workflow. The parent step waits for the sub-job to complete, then collects its output. This recurses to any depth — jobs all the way down.

### Server vs CLI ownership

Jobs are owned by whoever created them. When you run `stepwise run`, the CLI owns the job. When you create a job through the web UI or API, the server owns it.

This matters for lifecycle management:
- **Server-owned jobs** are managed by the persistent server process. The server monitors them, adopts orphaned jobs, and broadcasts status updates via WebSocket.
- **CLI-owned jobs** run in the CLI process. If you Ctrl+C, the job is orphaned. The server detects orphaned jobs via heartbeat expiry and can adopt them — continuing execution without losing progress.

The `stepwise server status` command shows which jobs the server is managing. `stepwise jobs` lists all jobs regardless of owner.

## Steps

A **step** is a typed node in a job's workflow graph. Each step declares:

- **Outputs** — the fields it produces (e.g., `[findings, sources]`)
- **Executor** — what does the work (script, LLM, agent, external, or poll)
- **Inputs** — data pulled from other steps' outputs
- **Exit rules** — what happens after the step completes (advance, loop, escalate)

```yaml
review:
  executor: external
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
```

Steps are **pure functions** — inputs in, outputs out, no shared state. This is what makes retry, parallelism, and observability work cleanly.

Each execution of a step is a **step run** with its own attempt number, status, timing, and result. A step that loops 3 times has 3 step runs.

## Executors

An **executor** is what does the actual work inside a step. Stepwise ships with five executor types:

| Type | What it does | Use when |
|------|-------------|----------|
| **Script** | Runs a shell command or script | Data processing, API calls, file operations |
| **LLM** | Single LLM API call via OpenRouter | Scoring, classification, text generation, structured extraction |
| **Agent** | Full agentic session (LLM + tools, iterating) | Complex tasks requiring tool use, code generation, research |
| **External** | Suspends for external input via the web UI or API | Approvals, creative judgment, decisions that need a person |
| **Poll** | Runs a check command on an interval until it returns JSON | Waiting for CI, deployments, PR reviews, external conditions |

Executors are **serializable references** — a type name plus configuration. No live Python objects in the durable model. This means jobs can be persisted, resumed, and inspected without executing code.

```yaml
# Script — runs a command, parses JSON output
fetch:
  run: python3 scripts/fetch_data.py
  outputs: [data, count]

# LLM — single API call with structured output
score:
  executor: llm
  model: anthropic/claude-sonnet-4
  prompt: "Score this content 0-10: $content"
  outputs: [score, reasoning]

# Agent — full agentic session
research:
  executor: agent
  prompt: "Research $topic thoroughly"
  outputs: [findings, sources]

# External — waits for human input
approve:
  executor: external
  prompt: "Approve this deployment?"
  outputs: [approved, reason]

# Poll — waits for an external condition
wait-for-ci:
  executor: poll
  check_command: 'gh pr checks $pr --json conclusion --jq "select(.conclusion != \"\") | {done: true}"'
  interval_seconds: 30
  outputs: [done]
```

See the [Executors guide](executors.md) for detailed configuration options and the [Writing Flows guide](writing-flows.md) for step-by-step authorship.

## Dependencies

Steps connect through two mechanisms:

### Input Bindings — Data Flow

An input binding pulls a specific field from an upstream step's output and gives it a local name:

```yaml
summarize:
  outputs: [summary]
  inputs:
    findings: research.findings      # "findings" comes from research step's output
    scores: evaluate.scores          # "scores" comes from evaluate step's output
    topic: $job.topic                # "topic" comes from job-level input
```

The local name (`findings`) is what the executor sees. It decouples the executor from the graph topology — you can rewire inputs without changing the executor's code.

Input bindings create **data dependencies**. The engine won't run a step until all its input sources have completed.

### After — Pure Ordering

Sometimes you need a step to wait for another without taking any data:

```yaml
notify:
  run: scripts/send_notification.py
  outputs: [sent]
  after: [deploy]         # wait for deploy to finish, but don't use its output
```

### Parallel Execution

Steps with no dependencies (direct or transitive) run in parallel automatically. The engine resolves the DAG and launches everything it can:

```yaml
steps:
  # These three run in parallel — no dependencies between them
  research_a:
    outputs: [findings]
    inputs: { topic: $job.topic_a }
  research_b:
    outputs: [findings]
    inputs: { topic: $job.topic_b }
  research_c:
    outputs: [findings]
    inputs: { topic: $job.topic_c }

  # This waits for all three
  synthesize:
    outputs: [report]
    inputs:
      a: research_a.findings
      b: research_b.findings
      c: research_c.findings
```

## Exit Rules & Loops

Exit rules fire after a step completes. They evaluate conditions against the step's output and decide what happens next.

```yaml
exits:
  - name: passed
    when: "outputs.score >= 0.8"
    action: advance           # continue to downstream steps

  - name: needs_work
    when: "outputs.score < 0.8 and attempt < 3"
    action: loop              # re-run a step (creates a new attempt)
    target: draft             # which step to re-run

  - name: give_up
    when: "attempt >= 3"
    action: escalate          # pause the job for human inspection
```

**Actions:**

| Action | What it does |
|--------|-------------|
| `advance` | Normal progression to downstream steps |
| `loop` | Re-run the `target` step (new attempt). Downstream steps wait for the fresh output. |
| `escalate` | Pause the job. A human inspects and decides what to do. |
| `abandon` | Fail the job. |

If no exit rules match (or none are defined), the step advances by default. When explicit `advance` rules exist but none match, the step **fails** — this prevents silent advancement past unhandled cases.

Loops are **control flow, not graph cycles**. The workflow definition is always a DAG. When a loop fires, the engine creates a new step run (attempt N+1) for the target. The key mechanism is **supersession** — the new run invalidates the previous one, and that invalidation cascades downstream. Steps only run when all their dependencies are fresh.

## For-Each

For-each steps iterate over a list, running an embedded sub-flow for each item:

```yaml
process_sections:
  for_each: plan.sections        # iterate over this list
  as: section                    # name for current item
  on_error: continue             # or "fail_fast" (default)
  outputs: [results]

  flow:
    steps:
      generate:
        executor: llm
        prompt: "Generate content for: $section"
        outputs: [html]

      review:
        executor: llm
        prompt: "Review this HTML for quality"
        outputs: [pass, feedback]
        inputs:
          html: generate.html
        exits:
          - name: good
            when: "outputs.pass == True"
            action: advance
          - name: retry
            when: "outputs.pass == False and attempt < 3"
            action: loop
            target: generate
```

Each iteration runs as an independent sub-job. Results are collected in source list order. Items can execute in parallel.

## Conditional Branching

Branching is **pure-pull**: each step declares its own activation condition via `when`, evaluated against its resolved inputs. Merge points use `any_of` inputs to take from whichever branch completed.

```yaml
steps:
  classify:
    run: scripts/classify.sh
    outputs: [category]

  quick-path:
    run: scripts/quick.sh
    inputs: { category: classify.category }
    outputs: [result]
    when: "category == 'simple'"       # I decide when I run

  deep-path:
    executor: agent
    prompt: "Deep analysis..."
    inputs: { category: classify.category }
    outputs: [result]
    when: "category == 'complex'"      # I decide when I run

  final:
    run: scripts/report.sh
    inputs:
      result:
        any_of:                        # takes from whichever branch completed
          - quick-path.result
          - deep-path.result
    outputs: [report]
```

When `classify` completes with `category == 'simple'`, `quick-path`'s `when` condition is satisfied so it activates. `deep-path`'s condition is false, so it stays not-ready. When no steps are in motion and nothing new can activate, the engine **settles** the job: never-started steps get SKIPPED runs, and the job completes if at least one terminal has a current completed run.

**Key distinctions:**
- `after: [step-x]` = ordering only (wait for step-x to complete)
- `inputs: { field: step-x.field }` = data dependency (also implies ordering)
- `when: "expr"` = conditional gate on resolved inputs (evaluated after deps are satisfied)

## Job Staging

Job staging lets you create jobs in a **STAGED** state, build up a batch with dependencies and data wiring, review the plan, and then release everything for execution.

### Lifecycle

```
STAGED → (add deps, wire data, review) → job run → PENDING → RUNNING → COMPLETED/FAILED
```

Staged jobs don't execute until explicitly released. This gives you a review checkpoint between planning and execution.

### Groups and Data Wiring

Groups are string labels for batch operations:

```bash
stepwise job create my-flow --input task="Build API" --group wave-1
stepwise job create my-flow --input task="Write tests" --group wave-1
stepwise job show --group wave-1       # review staged jobs
stepwise job run --group wave-1        # release the batch
```

Wire data between jobs by referencing another job's outputs:

```bash
stepwise job create impl-flow --input plan=job-plan-456.plan --group batch
```

The `job-plan-456.plan` syntax means "the `plan` output field from job `job-plan-456`". This auto-creates a dependency edge. When the upstream job completes, the engine resolves the reference to the actual value before starting the downstream job. Dependencies cascade — releasing a group triggers execution in dependency order.

## The Trust Model

Stepwise is built around **packaged trust** — the idea that the real barrier to AI delegation isn't capability, it's confidence. You need to know what happened, why, and whether to let it continue.

### Observable runs

Every step run is recorded with:
- **Inputs** — the exact values passed to the executor
- **Outputs** — the artifact produced, validated against declared output fields
- **Timing** — start time, duration, queue wait
- **Executor metadata** — model used, token counts, cost, latency
- **Attempt count** — which iteration this is (for looped steps)

This isn't logging you opt into. It's the execution model. The engine can't run a step without recording these facts, because downstream steps depend on them.

### Scoped delegation

Each step has a bounded scope: declared inputs, declared outputs, a specific executor type. An agent step can't silently access data from an unrelated step. A script can't produce outputs it didn't declare. The engine validates artifact keys against the step's `outputs` list.

This scoping is what makes mixed workflows safe. You can have an untrusted script fetch data, a trusted agent analyze it, and a human approve the result — each step's authority is explicit in the YAML.

### Human gates

External steps are the trust primitive. They pause the job and wait for a person — with full context of what happened before. The escalate exit rule is the safety valve: when an agent has tried three times and is still failing, the job pauses for human triage instead of burning more tokens.

The combination of external steps and escalation rules means you can build workflows that delegate aggressively but fail safely. The human is always in the loop — not as a bottleneck, but as a circuit breaker.

### Audit trail

The event system records every state transition, every input resolution, every cost event. This powers the web UI's event timeline, HTML reports, and programmatic access via the SQLite store.

When something goes wrong at step 4 of a 7-step pipeline, you see exactly what inputs it received, what it produced, and why the exit rule fired the way it did. The audit trail isn't a feature — it's the database.

## How Agents Fit In

Agents interact with Stepwise in three roles:

### As callers

An agent calls a flow like a tool via the CLI:

```bash
stepwise run deploy --wait --input repo="/path" --input branch="main"
```

The flow runs. The agent gets JSON back. `--wait` prints only the JSON payload to stdout — zero logging, zero progress noise. Missing inputs get actionable error messages. Exit codes are explicit (0=success, 1=failed, 5=suspended).

This is the primary integration path. No MCP servers, no protocol layers. Just CLI commands that agents call via bash.

### As workers

Agent steps (`executor: agent`) use an LLM with tools to complete a task inside a step. The agent is scoped to its step's inputs and outputs. It can use tools, iterate, and produce structured output — but only within the boundaries the step defines.

With `continue_session: true`, an agent can maintain conversation context across loop iterations. This saves tokens by continuing the conversation rather than re-injecting context each time.

### As architects

With `emit_flow: true`, an agent step can dynamically create sub-workflows. The agent analyzes a task, writes a flow definition, and the engine executes it as a sub-job. Results propagate back to the parent step.

This is recursive delegation: an agent decides *how* to break down work, not just *what* to do. Exit rules with `outputs.get('_delegated', False)` can loop the agent with sub-flow results, enabling iterative planning-and-execution cycles.

## Handoff Envelopes

When a step completes, it produces a **handoff envelope** — a structured package containing:

- **Artifact** — the output data (a dict matching the step's declared outputs)
- **Sidecar** — optional metadata: decisions made, assumptions, confidence levels
- **Executor metadata** — model used, token counts, cost, latency

The envelope is the contract between steps. Downstream steps receive the artifact fields they bind to. The sidecar and executor metadata are available for observability and reporting.

## Observability

Every state transition, every input/output handoff, every cost event is persisted as a structured **step event**. This powers:

- **Web UI** — real-time DAG visualization, step detail panels, event timeline. See the [Web UI guide](web-ui.md).
- **HTML reports** — `stepwise run flow.yaml --report` generates a self-contained trace document
- **Programmatic access** — query the SQLite store directly for custom analysis

## Hooks and Notifications

**Shell hooks** (`.stepwise/hooks/on-suspend`, `on-complete`, `on-fail`) run in the engine's process context. Use them for local automation: notifications, log writes, build triggers.

**Server notifications** (`--notify URL`) are HTTP webhooks fired on job events. Use them for remote integrations: dashboards, CI pipelines, Slack.

See [Extensions](extensions.md) for full details.

## What's next

- [Writing Flows](writing-flows.md) — author workflows using all step types, wiring, and control flow
- [Executors](executors.md) — deep dive into executor configuration and decorators
- [Flow Reference](flow-reference.md) — complete field-by-field YAML schema
- [Agent Integration](agent-integration.md) — making flows callable by AI agents
- [Web UI](web-ui.md) — the dashboard, DAG viewer, and step detail
