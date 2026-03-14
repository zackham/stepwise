# Concepts

Stepwise has three runtime concepts, a dependency system, and a control flow mechanism. Everything else is built on top of these.

### Quick reference

| Concept | What it is | Key detail |
|---------|-----------|------------|
| **Job** | A unit of work with inputs and a workflow | Persists to SQLite, can spawn sub-jobs |
| **Step** | A typed node in the workflow graph | Declares outputs, executor, inputs, exit rules |
| **Executor** | What does the work inside a step | script, llm, agent, human, poll |
| **Input binding** | Pulls data from upstream outputs | `findings: research.findings` |
| **Exit rule** | Decides what happens after step completion | advance, loop, escalate, abandon |
| **For-each** | Iterates over a list with embedded sub-flows | Items execute in parallel |
| **Route step** | Conditional dispatch (switch statement) | First-match semantics |
| **Context chain** | Session continuity across agent steps | Prior transcripts compiled into context |

## Jobs

A **job** is a unit of work with an objective, initial inputs, and a workflow.

```bash
stepwise run code-review --var repo="/path/to/repo" --var branch="feature-x"
```

Jobs track their own lifecycle: created → running → completed/failed. They persist to SQLite — if the process restarts, the job resumes where it left off.

Jobs can spawn **sub-jobs**. A planning step might decompose a large objective into smaller pieces, each running its own workflow. The parent step waits for the sub-job to complete, then collects its output. This recurses to any depth — jobs all the way down.

## Steps

A **step** is a typed node in a job's workflow graph. Each step declares:

- **Outputs** — the fields it produces (e.g., `[findings, sources]`)
- **Executor** — what does the work (script, LLM, agent, or human)
- **Inputs** — data pulled from other steps' outputs
- **Exit rules** — what happens after the step completes (advance, loop, escalate)

```yaml
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
```

Steps are **pure functions** — inputs in, outputs out, no shared state. This is what makes retry, parallelism, and observability work cleanly.

Each execution of a step is a **step run** with its own attempt number, status, timing, and result. A step that loops 3 times has 3 step runs.

## Executors

An **executor** is what does the actual work inside a step. Stepwise ships with four executor types:

| Type | What it does | Use when |
|------|-------------|----------|
| **Script** | Runs a shell command or script | Data processing, API calls, file operations |
| **LLM** | Single LLM API call via OpenRouter | Scoring, classification, text generation, structured extraction |
| **Agent** | Full agentic session (LLM + tools, iterating) | Complex tasks requiring tool use, code generation, research |
| **Human** | Suspends for human input via the web UI | Approvals, creative judgment, decisions that need a person |

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
  model: anthropic/claude-opus-4
  prompt: "Research $topic thoroughly"
  outputs: [findings, sources]

# Human — waits for human input
approve:
  executor: human
  prompt: "Approve this deployment?"
  outputs: [approved, reason]
```

See the [Executors guide](executors.md) for detailed configuration options.

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

### Sequencing — Pure Ordering

Sometimes you need a step to wait for another without taking any data:

```yaml
notify:
  run: scripts/send_notification.py
  outputs: [sent]
  sequencing: [deploy]    # wait for deploy to finish, but don't use its output
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

If no exit rules match (or none are defined), the step advances by default.

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

## Route Steps

Route steps add **conditional dispatch** — run different sub-flows based on upstream output. Think of them as a `switch` statement for workflows.

```yaml
run_pipeline:
  inputs: { category: triage.category }
  routes:
    trivial:
      when: "category == 'trivial'"
      flow: flows/trivial.yaml
    complex:
      when: "category == 'complex'"
      flow:
        steps:
          analyze:
            executor: agent
            prompt: "Deep analysis..."
            outputs: [result]
    default:
      flow: flows/standard.yaml
  outputs: [result]
```

Routes evaluate in declaration order using first-match semantics. The `default` route (optional, no `when:`) always goes last. If nothing matches and there's no default, the step fails.

Route steps build on the same sub-job infrastructure as for-each: the matched sub-flow runs as a sub-job, the parent step stays in `DELEGATED` status until it completes, and the sub-flow's terminal outputs become the route step's result.

**Flow composition:** Sub-flows can be defined inline, loaded from local files, or referenced from the registry (`@author:name`). All three are resolved at parse time — the YAML is fetched and baked inline, so jobs never depend on external files or network at runtime.

## Context Chains

Steps are **pure functions** — they take inputs and produce outputs, with no shared state. But some workflows need session continuity: step B should know what step A discussed, not just its final output.

**Context chains** solve this by compiling prior chain members' conversation transcripts into an XML context block that's prepended to the agent's prompt:

```yaml
chains:
  review:
    max_tokens: 80000

steps:
  research:
    executor: agent
    prompt: "Research $topic"
    chain: review

  draft:
    executor: agent
    prompt: "Draft a report based on your research"
    chain: review
    inputs:
      findings: research.findings
```

When `draft` runs, the engine:
1. Loads `research`'s conversation transcript (captured after it completed)
2. Compiles it into `<prior_context>` XML
3. Prepends it to `draft`'s prompt

The agent sees the full reasoning, tool usage, and discoveries from the research phase — not just the final output fields. This is critical for agentic workflows where the process matters as much as the result.

**Key properties:**
- Chains maintain the pure-function step model — context flows through files, not shared memory
- Topological ordering ensures deterministic context regardless of parallel execution
- Overflow strategies drop whole transcripts (never mid-conversation) when the token budget is exceeded
- Transcript capture happens automatically for agent steps (via `acpx sessions show`)

## Handoff Envelopes

When a step completes, it produces a **handoff envelope** — a structured package containing:

- **Artifact** — the output data (a dict matching the step's declared outputs)
- **Sidecar** — optional metadata: decisions made, assumptions, confidence levels
- **Executor metadata** — model used, token counts, cost, latency

The envelope is the contract between steps. Downstream steps receive the artifact fields they bind to. The sidecar and executor metadata are available for observability and reporting.

## Observability

Every state transition, every input/output handoff, every cost event is persisted as a structured **step event**. This powers:

- **Web UI** — real-time DAG visualization, step detail panels, event timeline
- **HTML reports** — `stepwise run flow.yaml --report` generates a self-contained trace document with SVG DAG, step timeline, expandable details, and cost summary
- **Programmatic access** — query the SQLite store directly for custom analysis

The engine is designed to make the implicit explicit. When something goes wrong at step 4 of a 7-step pipeline, you can see exactly what inputs it received, what it produced, and why the exit rule fired the way it did.

## Flows as Tools

A Stepwise flow is a prompted workflow run with a working directory. The input is a string (the objective) plus optional string variables. The output is an array of terminal step artifacts. This makes flows callable by external agents via CLI — turning flows from "things humans run" into "tools agents delegate to."

**No MCP servers, no protocol layers, no required background services.** Just CLI commands that agents call via bash:

```bash
# Agent calls a flow and gets JSON back
stepwise run council --wait --var question="Should we use Postgres?"

# Self-documenting: generate the instructions block for CLAUDE.md
stepwise agent-help --update CLAUDE.md
```

### Five Interaction Modes

Agents interact with flows in five modes, all using the same flow definition:

1. **Automated** — Run end-to-end, get structured output. `stepwise run <flow> --wait`
2. **Mediated** — Run with human steps; agent fulfills them interactively. `--wait` returns exit 5 on suspension; `fulfill --wait` resumes.
3. **Monitoring** — Check job progress and suspension inbox. `status --output json`, `list --suspended`.
4. **Data Grab** — Retrieve specific outputs from completed steps. `output --step a,b`, `output --step a --inputs`.
5. **Takeover** — Cancel and inspect a running job. `cancel --output json`, `wait <job-id>`.

### Key Mechanics

- **`--wait`** blocks until the flow completes or all progress is blocked by human steps (exit 5). Returns JSON with `suspended_steps` including `run_id`, `prompt`, and `fields`.
- **`--async`** spawns a detached background process — no `stepwise serve` required. Poll with `stepwise status`, retrieve with `stepwise output`.
- **`stepwise fulfill <run-id> '{...}' --wait`** satisfies a human step and continues blocking until the next suspension or completion.
- **`stepwise list --suspended --output json`** shows all pending human steps across all active jobs — the agent's "inbox."
- **`stepwise schema`** generates a JSON tool contract: inputs, outputs, human steps.
- **`stepwise agent-help`** generates markdown instructions with the 5-mode interaction model. Self-documenting, zero infrastructure.

### Design for Agents

- **Stdout purity**: `--wait` prints ONLY the JSON payload to stdout. Zero logging, zero progress noise.
- **Actionable errors**: Every error includes the fix. `Missing required input 'question'. Usage: --var question="..."`.
- **Explicit exit codes**: 0=success, 1=failed, 2=input error, 3=timeout, 4=cancelled, 5=suspended.
- **Partial outputs on failure**: Steps that completed before the failure are included in the response.
- **`--var-file key=path`**: Agents write long inputs to a temp file and pass the path — no shell escaping needed.
- **Idempotent fulfill**: Double-fulfilling a step returns an error but doesn't corrupt state.
- **Project hooks**: `.stepwise/hooks/on-suspend` fires when steps suspend — agents and hooks can race safely.
