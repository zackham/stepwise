# Concepts

Stepwise doesn't do the pedaling. Your agents, scripts, and humans do the work. Stepwise shows you the watts: what's running, what's waiting, what failed, and what needs your attention.

The system has three runtime primitives (jobs, steps, executors), a dependency system (inputs, ordering), and control flow (exit rules, branching, loops). Everything else — for-each, caching, sub-jobs, human gates — is built on top of these.

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
| **Kit** | A collection of related flows with a `KIT.yaml` manifest | Shared as a unit, referenced as `kit/flow` |

## Jobs

A **job** is a unit of work. It has an objective, initial inputs, and a workflow to execute.

```bash
stepwise run code-review --input repo="/path/to/repo" --input branch="feature-x"
```

Jobs track their own lifecycle: created -> running -> completed/failed. They persist to SQLite — if the process restarts, the job resumes where it left off. No re-running completed steps, no lost progress.

Jobs can spawn **sub-jobs**. A planning step might decompose a large objective into smaller pieces, each running its own workflow. The parent step waits for the sub-job to complete, then collects its output. This recurses to any depth.

### Server vs CLI ownership

Jobs are owned by whoever created them:

- **CLI-owned** — `stepwise run` creates and manages the job in its process. Ctrl+C orphans the job; the server detects this via heartbeat expiry and can adopt it.
- **Server-owned** — created through the web UI or API. The server monitors them, adopts orphans, and broadcasts status via WebSocket.

`stepwise jobs` lists all jobs regardless of owner. `stepwise server status` shows what the server is managing.

## Steps

A **step** is a typed node in the workflow graph. Each step declares:

- **Outputs** — the fields it produces (e.g., `[findings, sources]`)
- **Executor** — what does the work (script, LLM, agent, external, or poll)
- **Inputs** — data pulled from other steps' outputs
- **Exit rules** — what happens after completion (advance, loop, escalate)

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

Each execution of a step is a **step run** with its own attempt number, status, timing, and result. A step that loops 3 times has 3 step runs, each with its own recorded inputs and outputs.

## Executors

An **executor** is what does the actual work inside a step. Five types, covering the spectrum from deterministic scripts to full agentic sessions:

| Type | What it does | Use when |
|------|-------------|----------|
| **Script** | Runs a shell command | Data processing, API calls, builds, anything deterministic |
| **LLM** | Single LLM call via OpenRouter | Scoring, classification, text generation, structured extraction |
| **Agent** | Full agentic session (LLM + tools, iterating) | Complex tasks: code generation, research, multi-step reasoning |
| **External** | Suspends for input via web UI or API | Approvals, creative judgment, any decision needing a person |
| **Poll** | Runs a check command on an interval | Waiting for CI, deployments, PR reviews, external conditions |

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

# Agent — full agentic session with tool access
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

Executors are serializable references — a type name plus configuration. No live Python objects in the durable model. Jobs can be persisted, resumed, and inspected without executing code.

See the [Executors guide](executors.md) for configuration details and the [Writing Flows guide](writing-flows.md) for authorship patterns.

## Dependencies

Steps connect through two mechanisms.

### Input bindings — data flow

An input binding pulls a specific field from an upstream step's output:

```yaml
summarize:
  outputs: [summary]
  inputs:
    findings: research.findings      # "findings" comes from research step
    scores: evaluate.scores          # "scores" comes from evaluate step
    topic: $job.topic                # "topic" comes from job-level input
```

The local name (`findings`) is what the executor sees. This decouples the executor from the graph topology — rewire inputs without changing the executor's code.

Input bindings create **data dependencies**. The engine won't run a step until all its input sources have completed.

### After — pure ordering

When you need a step to wait for another without taking data:

```yaml
notify:
  run: scripts/send_notification.py
  outputs: [sent]
  after: [deploy]         # wait for deploy, don't use its output
```

### Parallel execution

Steps with no dependencies run in parallel automatically. The engine resolves the DAG and launches everything it can:

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

## Exit rules and loops

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

| Action | What it does |
|--------|-------------|
| `advance` | Normal progression to downstream steps |
| `loop` | Re-run the `target` step (new attempt). Downstream steps wait for fresh output. |
| `escalate` | Pause the job. A human inspects and decides what to do. |
| `abandon` | Fail the job. |

If no exit rules match (or none are defined), the step advances by default. When explicit `advance` rules exist but none match, the step **fails** — preventing silent advancement past unhandled cases.

Loops are **control flow, not graph cycles**. The workflow definition is always a DAG. When a loop fires, the engine creates a new step run (attempt N+1) for the target. The key mechanism is **supersession** — the new run invalidates the previous one, and that invalidation cascades downstream. Steps only run when all their dependencies are fresh.

## For-each

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

Each iteration runs as an independent sub-job. Results are collected in source list order. Items execute in parallel. `on_error: continue` means one failed item doesn't block the rest.

## Conditional branching

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
    when: "category == 'simple'"

  deep-path:
    executor: agent
    prompt: "Deep analysis..."
    inputs: { category: classify.category }
    outputs: [result]
    when: "category == 'complex'"

  final:
    run: scripts/report.sh
    inputs:
      result:
        any_of:
          - quick-path.result
          - deep-path.result
    outputs: [report]
```

When `classify` outputs `category == 'simple'`, only `quick-path` activates. `deep-path` stays not-ready. When no steps are in motion and nothing new can activate, the engine **settles** — skipping never-started steps and completing the job.

Key distinctions:
- `after: [step-x]` = ordering only
- `inputs: { field: step-x.field }` = data dependency (implies ordering)
- `when: "expr"` = conditional gate on resolved inputs

## Coordination rules

The validator enforces a set of coordination rules to prevent races between steps that share state (named sessions, forked sessions, shared workspaces). These rules are enforced both at parse time (`yaml_loader`) and by the coordination validator (`stepwise validate`). See `data/reports/2026-04-07-stepwise-coordination-and-validation-model.md` in the vita repo for the full model, derivations, and council findings.

**Pair safety for session writers (§7.3).** Any two steps writing to the same named session must be provably non-concurrent. The validator proves this by showing either (a) one step's `after:`-transitive closure includes the other (linear chain), or (b) their `when:` predicates are pairwise mutex (conditional branches). If neither can be proven, the validator emits `pair_unsafe` with a fix hint pointing at adding `after:` or a mutex `when:` gate.

**`after.any_of` and the universal-prefix rule (§7.2, §10).** When a step declares `after: [{any_of: [a, b, c]}]`, the "first success wins" eligibility model allows the step to launch as soon as any of the branches completes. Losing branches keep running (no cancellation in v1.0). For ordering proofs, only the **intersection** of mhb-ancestors across all `any_of` branches carries — i.e., a step that appears before EVERY branch of an `any_of` group is an mhb-ancestor of the joined step.

**Fork from step name (§8.2).** `fork_from: <step_name>` anchors a new session at a specific step's completion tail. The target step must declare its own `session:` (you cannot fork from an ephemeral one-shot agent step). The forking step must declare its own fresh `session:` and have the target in its `after:` chain. Both the forking step and all writers of the parent session must use explicit `agent: claude`.

**Conditional fork rejoin (§8.3).** Multiple chain roots on the same forked session are permitted when their `when:` clauses are pairwise mutex (e.g., two alternative chain roots gated on a routing step's output tag). The parse-time validator allows this structurally; the coordination validator verifies the mutex proof.

**Retries and cache are prohibited on session-writing and fork-source steps (§7.4).** The validator rejects flows that combine `session:` or `fork_from:` with `max_attempts > 1` or `cache:`. For crash recovery, re-executing a session-writing step carries documented duplicate-turn risk (§9.3); this is the acceptable v1.0 limitation. Authors who need retry semantics should use ephemeral one-shot agent steps (no `session:`), which can be retried freely because they don't accumulate session state.

**Eager snapshot via filesystem copy (§9).** When a step is a fork source (some downstream step declares `fork_from: <this_step>`), the engine serializes its post-exit lifecycle inside an exclusive `fcntl.flock`, copies its session JSONL file to a new UUID via `temp → os.replace → fsync(parent_dir)`, persists the snapshot UUID atomically with the completion record, then releases the lock. Downstream forks resume from the snapshot UUID (via `claude --resume <snap_uuid> --fork-session`), not from the live session tail. This eliminates the race where the parent session keeps mutating past the intended fork point.

**Running the coordination validator.** `stepwise validate <flow.yaml>` runs both the structural parse-time checks and the coordination validator. Parse-time errors are fatal; coordination validator findings are currently surfaced as warnings (will become fatal once the loop-back binding runtime lands in step 7).

## Job staging

Create jobs in a **STAGED** state, build up a batch with dependencies, review, then release:

```
STAGED -> (add deps, wire data, review) -> job run -> PENDING -> RUNNING -> COMPLETED/FAILED
```

### The create → dep → run pattern

The core workflow: create all jobs upfront, wire dependencies between them, then release the group. The engine handles execution ordering automatically.

```bash
# 1. Create staged jobs — capture IDs from JSON output
RESEARCH=$(stepwise job create research-v2 \
  --input topic="Widget architecture" \
  --group widget --name "research: widget arch" \
  --output json | jq -r .id)

PLAN=$(stepwise job create plan \
  --input spec="Design widget system" \
  --input project="my-app" \
  --group widget --name "plan: widget system" \
  --output json | jq -r .id)

IMPL=$(stepwise job create implement \
  --input spec="Build widget system" \
  --input project="my-app" \
  --group widget --name "impl: widget system" \
  --output json | jq -r .id)

# 2. Wire dependencies — plan waits for research, impl waits for plan
stepwise job dep $PLAN --after $RESEARCH
stepwise job dep $IMPL --after $PLAN

# 3. Review the DAG
stepwise job show --group widget

# 4. Release and wait — engine cascades execution in dependency order
stepwise job run --group widget --wait
```

### Data wiring between jobs

Use `--input key=job-id.field` to pass outputs from one job as inputs to another. This **auto-creates a dependency edge** — no separate `job dep` call needed:

```bash
# Plan job ran and produced outputs including "plan" and "plan_file"
IMPL=$(stepwise job create implement \
  --input spec="Build auth middleware" \
  --input plan_file=$PLAN.plan_file \
  --group auth --name "impl: auth middleware" \
  --output json | jq -r .id)
```

The `$PLAN.plan_file` syntax means "the `plan_file` output from the job whose ID is in `$PLAN`." The engine resolves this at runtime when the upstream job completes.

### Ordering vs data dependencies

- **Data wiring** (`--input key=job-id.field`) — passes data AND creates ordering. Use when the downstream job needs the upstream job's output.
- **Ordering only** (`job dep A --after B`) — no data flow, just "B must finish before A starts." Use when jobs must run sequentially but don't share data (e.g., both write to the same directory).

### Parallel workstreams

Jobs in the same group with no dependencies between them run in parallel:

```bash
# These three run concurrently — no deps between them
stepwise job create plan --input spec="Memory game" --group gumball --name "plan: memory game"
stepwise job create plan --input spec="Reading module" --group gumball --name "plan: reading module"
stepwise job create research-v2 --input topic="Scene composers" --group gumball --name "research: scene composer"

# Release all and wait — engine runs all three in parallel
stepwise job run --group gumball --wait
```

### Concurrency control

```bash
# Limit to 2 concurrent jobs in the group
stepwise job run --group gumball --max-concurrent 2
```

## The trust model

Stepwise is built around **packaged trust** — the idea that the real barrier to AI delegation isn't capability, it's confidence. You need to know what happened, why, and whether to let it continue.

### Observable runs

Every step run is recorded with:
- **Inputs** — the exact values passed to the executor
- **Outputs** — the artifact produced, validated against declared output fields
- **Timing** — start time, duration, queue wait
- **Executor metadata** — model used, token counts, cost
- **Attempt count** — which iteration this is (for looped steps)

This isn't optional logging. It's the execution model. The engine can't run a step without recording these facts, because downstream steps depend on them.

### Scoped delegation

Each step has bounded authority: declared inputs, declared outputs, a specific executor type. An agent step can't silently access data from an unrelated step. A script can't produce outputs it didn't declare. The engine validates artifact keys against the step's `outputs` list.

This scoping makes mixed workflows safe. You can have an untrusted script fetch data, a trusted agent analyze it, and a human approve the result — each step's scope is explicit in the YAML.

### Human gates

External steps are the trust primitive. They pause the job and present full context of what happened before the gate. The `escalate` exit rule is the safety valve: when an agent has tried 3 times and is still failing, the job pauses for human triage instead of burning more tokens.

External steps + escalation rules = workflows that delegate aggressively but fail safely. The human is always in the loop — not as a bottleneck, but as a circuit breaker.

### Audit trail

Every state transition, input resolution, and cost event is persisted as a structured step event. This powers the web UI's event timeline, HTML reports via `--report`, and direct queries against the SQLite store.

When something goes wrong at step 4 of a 7-step pipeline, you see exactly what inputs it received, what it produced, and why the exit rule fired the way it did.

## Kits

A **kit** is a directory containing a `KIT.yaml` manifest and one or more flow subdirectories. Kits group related flows into a single, shareable package — for example, a software development kit might bundle `plan`, `implement`, and `research` flows.

```
swdev/
  KIT.yaml                 # manifest: name, description, includes, defaults
  plan/FLOW.yaml
  implement/FLOW.yaml
  research/FLOW.yaml
```

Locally, kit flows are referenced as `kit/flow` (e.g., `stepwise run swdev/plan`). On the registry, installed kits use `@author:kit/flow` (e.g., `stepwise run @zack:swdev/plan`).

Kits can declare **includes** — references to other registry flows that are auto-fetched during `stepwise get`. This lets a kit depend on shared utility flows without bundling them.

See [Flow and Kit Sharing](flow-sharing.md) for publishing and installation.

## How agents fit in

Agents interact with Stepwise in three roles.

### As callers

An agent calls a flow like a CLI tool:

```bash
stepwise run deploy --wait --input repo="/path" --input branch="main"
```

`--wait` prints pure JSON to stdout. Exit codes are explicit (0=success, 1=failed, 2=input error, 3=timeout, 4=cancelled). No MCP servers, no protocol layers. Just bash commands.

### As workers

Agent steps (`executor: agent`) use an LLM with tools inside a step. The agent is scoped to its step's inputs and outputs — it iterates autonomously but only within the boundaries the step defines.

With `session: <name>`, steps sharing the same session name maintain context across iterations and across steps, saving tokens by continuing the conversation rather than re-injecting context each time.

### As architects

With `emit_flow: true`, an agent step can dynamically create sub-workflows. The agent analyzes a task, writes a flow definition, and the engine executes it as a sub-job. Results propagate back to the parent step.

This is recursive delegation: an agent decides *how* to break down work, not just *what* to do.

## Handoff envelopes

When a step completes, it produces a **handoff envelope**:

- **Artifact** — the output data (a dict matching the step's declared outputs)
- **Sidecar** — optional metadata: decisions made, assumptions, confidence levels
- **Executor metadata** — model used, token counts, cost, latency

The envelope is the contract between steps. Downstream steps receive the artifact fields they bind to. The sidecar and executor metadata are available for observability and reporting.

## Observability

Every state transition, input/output handoff, and cost event is persisted as a structured step event. This powers:

- **Web UI** — real-time DAG visualization, step detail panels, event timeline. See the [Web UI guide](web-ui.md).
- **HTML reports** — `stepwise run flow.yaml --report` generates a self-contained trace document
- **Programmatic access** — query the SQLite store directly for custom analysis

## Hooks and notifications

**Shell hooks** (`.stepwise/hooks/on-suspend`, `on-complete`, `on-fail`) run in the engine's process context for local automation.

**Server notifications** (`--notify URL`) fire HTTP webhooks on job events for remote integrations.

See [Extensions](extensions.md) for details.

## What's next

- [Writing Flows](writing-flows.md) — author workflows using all step types, wiring, and control flow
- [Executors](executors.md) — deep dive into executor configuration and decorators
- [Flow Reference](flow-reference.md) — complete field-by-field YAML schema
- [Agent Integration](agent-integration.md) — making flows callable by AI agents
- [Web UI](web-ui.md) — the dashboard, DAG viewer, and step detail
