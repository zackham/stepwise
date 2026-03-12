# Skill: Create Stepwise Flow

## When to Use

Activate when the user asks to:
- Create, build, or define a flow
- "Make a flow that..."
- "I need a pipeline for..."
- "Set up steps to..."
- Convert a natural language process description into a Stepwise flow
- Modify or extend an existing flow definition

## What This Skill Does

Translates natural language descriptions into valid Stepwise flow definitions (`.flow.yaml` files), then optionally runs them via the CLI or creates jobs via the API.

## Conversation Flow

1. **Understand** what the flow should accomplish
2. **Identify** the steps, executor types, data flow, and loop conditions
3. **Generate** a valid `.flow.yaml` file
4. **Validate** with `stepwise validate <file>`
5. **Optionally** run with `stepwise run <file>` or `stepwise run <file> --watch`

---

## Flow Format (YAML)

Flows are `.flow.yaml` files. YAML is parsed into a `WorkflowDefinition` at runtime.

### Top-Level Structure

```yaml
name: my-flow                    # identifier (kebab-case)
description: "Human description" # optional
author: zack                     # optional (auto-populated from git config)
version: "1.0"                   # optional
tags: [demo, agent]              # optional

steps:
  step_name:
    # ... step definition
```

If `name` is omitted, it defaults from the filename (`my-flow.flow.yaml` → `my-flow`).

### Complete Step Definition

```yaml
step_name:
  # ── Executor (exactly one required) ──────────────────────
  run: scripts/foo.sh                  # shorthand for script executor
  # OR
  executor: human                      # human | llm | agent | mock_llm
  prompt: "Instructions shown to user" # used by human, llm, and agent

  # ── Outputs (required) ──────────────────────────────────
  outputs: [field1, field2]

  # ── Inputs (optional) ──────────────────────────────────
  inputs:
    local_name: other_step.field       # data from another step's output
    from_job: $job.field_name          # data from job-level inputs

  # ── Sequencing (optional) ──────────────────────────────
  sequencing: [step_a, step_b]         # wait for completion, no data transfer

  # ── Exit Rules (optional) ──────────────────────────────
  exits:
    - name: rule_name
      when: "python expression"        # evaluated with restricted namespace
      action: advance                  # advance | loop | escalate | abandon
      target: step_name               # required when action is loop

  # ── Idempotency (optional, default: "idempotent") ──────
  idempotency: idempotent             # idempotent | allow_restart | retriable_with_guard | non_retriable

  # ── Limits (optional) ──────────────────────────────────
  limits:
    max_cost_usd: 1.50
    max_duration_minutes: 30
    max_iterations: 10

  # ── Decorators (optional) ──────────────────────────────
  decorators:
    - type: timeout
      config: { minutes: 30 }
    - type: retry
      config: { max_retries: 2 }
```

---

## Executor Types

### script

Runs a shell command. Parses stdout as JSON for outputs. Non-zero exit code = failure.

```yaml
build:
  run: scripts/build.sh              # shorthand — auto-prefixes .py files with python3
  outputs: [artifact, version]
```

**Config:** `command` (string), `working_dir` (string, optional)

**How outputs work:** The script prints JSON to stdout. Keys matching declared `outputs` are extracted as the step's artifact. If stdout is not valid JSON, it is stored as `{"stdout": "..."}`.

**Environment variables available to scripts:**
- `JOB_ENGINE_INPUTS` -- path to JSON file containing resolved input values
- `JOB_ENGINE_WORKSPACE` -- workspace directory path

**Inline scripts:**
```yaml
count_words:
  run: |
    python3 << 'PYEOF'
    import json
    print(json.dumps({"word_count": 42}))
    PYEOF
  outputs: [word_count]
```

### human

Suspends the step and waits for user input via the web UI (in `--watch` mode) or stdin (in headless mode).

```yaml
approve:
  executor: human
  prompt: "Review this deployment plan and decide: approve or reject."
  outputs: [decision, reason]
  inputs:
    plan: generate_plan.plan
```

**Config:** `prompt` (string), `notify` (string, optional)

In `--watch` mode, the UI shows the prompt and collects output fields. In headless mode, the terminal prompts for each output field.

### llm

Single LLM call via OpenRouter. No tool use, no multi-turn conversation. Uses structured output (function calling) to extract declared output fields.

```yaml
summarize:
  executor: llm
  prompt: "Summarize this text in 3 bullet points:\n\n$text"
  model: balanced                    # tier alias or full model ID
  system: "You are a concise summarizer."
  temperature: 0.3
  max_tokens: 1024
  outputs: [summary]
  inputs:
    text: fetch.content
```

**Config:**
| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `prompt` | string | yes | -- | Python `string.Template` with `$var` placeholders |
| `model` | string | no | config default | Tier alias (`fast`, `balanced`, `strong`) or full OpenRouter model ID |
| `system` | string | no | none | System message |
| `temperature` | float | no | 0.0 | Sampling temperature |
| `max_tokens` | int | no | 4096 | Max output tokens |

**Output extraction priority:**
1. Tool call response (structured output via function calling)
2. JSON in content body (markdown fences stripped automatically)
3. Single-field shortcut: if only one output declared, raw text content is used

### agent

Long-running autonomous AI agent session via ACP (Agent Client Protocol). The agent has full tool access and can read/write files in the workspace. Output streams in real time via WebSocket.

```yaml
implement:
  executor: agent
  prompt: |
    Implement the following changes:

    $plan

    Run the test suite before finishing.
  outputs: [result]
  inputs:
    plan: planning.result
  limits:
    max_cost_usd: 2.00
    max_duration_minutes: 60
```

**Config:**
| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `prompt` | string | yes | -- | Python `string.Template`. `$objective` and `$workspace` are auto-injected |
| `output_mode` | string | no | `"effect"` | How to extract outputs (see below) |
| `output_path` | string | no | -- | File path for `"file"` mode |
| `agent` | string | no | `"claude"` | ACP agent name (claude, codex, gemini, etc.) |
| `working_dir` | string | no | workspace | Override working directory |
| `timeout` | int | no | -- | Timeout in seconds |

**Output modes:**
| Mode | Artifact | Use When |
|------|----------|----------|
| `"effect"` (default) | `{"status": "completed", "session_id": "..."}` | Agent modifies files; workspace IS the output |
| `"stream_result"` | `{"result": "<full agent text>"}` | You need the agent's textual response downstream |
| `"file"` | Parsed JSON from `output_path` | Agent writes structured JSON to a specific file |

### mock_llm

Deterministic mock for testing. Echoes inputs with mock response.

```yaml
test_step:
  executor: mock_llm
  failure_rate: 0.1
  outputs: [result]
```

**Config:** `failure_rate` (float), `partial_rate` (float), `latency_range` ([min, max] seconds)

---

## Input Bindings

Inputs connect outputs from upstream steps to the current step's executor.

### YAML Syntax (Shorthand)

```yaml
inputs:
  local_name: source_step.source_field    # from another step
  topic: $job.topic                       # from job-level inputs
```

### JSON Syntax (for API / templates)

```json
"inputs": [
  {"local_name": "plan", "source_step": "planning", "source_field": "result"},
  {"local_name": "topic", "source_step": "$job", "source_field": "topic"}
]
```

### Key Rules

- `local_name` is what the executor sees (e.g., `$local_name` in prompt templates, key in script input JSON)
- `source_step` must be a valid step name or `"$job"`
- `source_field` must be in the source step's declared `outputs`
- `local_name` must be unique within a step
- **Input bindings create implicit ordering.** If step B has `inputs: { x: step_a.field }`, B automatically waits for A. You do NOT also need `sequencing: [step_a]`.
- Use `sequencing` only for ordering WITHOUT data transfer

### Job-level Inputs

Job inputs are passed via `--var` on the CLI or in the `inputs` dict via the API:

```bash
stepwise run my-flow.flow.yaml --var topic="login flow UX"
```

```yaml
inputs:
  topic: $job.topic       # resolves from job.inputs["topic"]
```

---

## Exit Rules

Exit rules are evaluated after a step completes successfully, in priority order (highest first). The first matching rule determines what happens. If no rules match (or none defined), the step implicitly advances.

### YAML Format

```yaml
exits:
  - name: approved
    when: "outputs.decision == 'approve'"
    action: advance

  - name: revise
    when: "outputs.decision == 'revise' and attempt < 5"
    action: loop
    target: draft

  - name: give_up
    when: "attempt >= 5"
    action: escalate
```

In YAML, all exit rules become `expression` type. Priority is assigned by position: first rule = highest priority.

### JSON Format

JSON gives full control over rule `type` and explicit `priority`:

```json
"exit_rules": [
  {
    "name": "approved",
    "type": "expression",
    "config": {
      "condition": "outputs.decision == 'approve'",
      "action": "advance"
    },
    "priority": 10
  },
  {
    "name": "max_retries",
    "type": "expression",
    "config": {
      "condition": "attempt >= 5",
      "action": "escalate"
    },
    "priority": 5
  },
  {
    "name": "retry",
    "type": "always",
    "config": {"action": "loop"},
    "priority": 1
  }
]
```

### Exit Rule Types

#### expression (recommended for all cases)

Python expression evaluated with `eval()` in a restricted namespace.

**Namespace variables:**
- `outputs` -- DotDict of the step's output artifact. Supports attribute access (`outputs.score`) and dict methods (`outputs.get('score', 0)`)
- `attempt` -- current attempt number (1-indexed)
- `max_attempts` -- `max_iterations` from limits, or None

**Safe builtins:** `any`, `all`, `len`, `min`, `max`, `sum`, `abs`, `round`, `sorted`, `int`, `float`, `str`, `bool`, `True`, `False`, `None`

**Expression examples:**
```python
outputs.score >= 0.8
outputs.decision == 'approve'
attempt >= 5
'STATUS: READY' in str(outputs.get('result', ''))
any(s < 0.5 for s in outputs.scores)
len(outputs.errors) == 0
```

**Guidance:** Keep expressions simple. If over ~80 characters, push the logic into the step itself and output a simple summary field.

#### field_match (JSON only)

Direct field equality check:

```json
{
  "name": "success",
  "type": "field_match",
  "config": {"field": "status", "value": "ok", "action": "advance"},
  "priority": 10
}
```

#### always (JSON only)

Unconditional match. Used as lowest-priority fallback:

```json
{
  "name": "default_loop",
  "type": "always",
  "config": {"action": "loop"},
  "priority": 1
}
```

### Exit Actions

| Action | Behavior |
|--------|----------|
| `advance` | Normal progression -- downstream steps become ready |
| `loop` | Re-launch `target` step (default: self), creating a new attempt. Previous run is superseded; downstream steps re-execute with fresh data |
| `escalate` | Pause the job (`paused` status) for human inspection |
| `abandon` | Fail the job (`failed` status) |

### Priority Design Pattern

Always structure exit rules with this priority ordering:
1. **Highest:** Success/advance conditions
2. **Middle:** Safety bounds (max attempts, escalation)
3. **Lowest:** Loop/retry fallback

---

## Loop Patterns

### Self-loop (single step retries itself)

```yaml
generate:
  executor: llm
  prompt: "Generate content about $topic."
  outputs: [content, quality_score]
  inputs:
    topic: $job.topic

  exits:
    - name: good_enough
      when: "float(outputs.get('quality_score', 0)) >= 0.8"
      action: advance
    - name: max_attempts
      when: "attempt >= 5"
      action: escalate
    - name: retry
      when: "True"
      action: loop
      target: generate
```

### Multi-step loop (clarify-answer-check)

A downstream step loops back to an upstream step, re-executing the intermediate chain.

```yaml
steps:
  clarify:
    executor: agent
    prompt: "Ask ONE clarifying question about: $objective"
    outputs: [result]

  answer:
    executor: human
    prompt: "Answer the clarifying question."
    outputs: [response]
    sequencing: [clarify]

  check:
    executor: agent
    prompt: |
      Do we have enough context?
      Question: $question
      Answer: $response
      End with STATUS: READY or STATUS: NEEDS_MORE
    outputs: [result]
    inputs:
      question: clarify.result
      response: answer.response

    exits:
      - name: ready
        when: "'STATUS: READY' in str(outputs.get('result', ''))"
        action: advance
      - name: max_rounds
        when: "attempt >= 5"
        action: advance
      - name: needs_more
        when: "'STATUS: NEEDS_MORE' in str(outputs.get('result', ''))"
        action: loop
        target: clarify

  plan:
    executor: agent
    prompt: "Write a plan using this context: $context"
    outputs: [result]
    inputs:
      context: check.result
    sequencing: [check]
```

When `check` loops to `clarify`:
1. `clarify` gets a new run (superseding its previous completed run)
2. `answer` becomes non-current (its dep `clarify` was superseded) and re-executes
3. `check` becomes non-current and re-executes after new `answer`
4. Cycle continues until `check` advances

### Evaluate-refine loop (self-loop with always fallback)

In JSON format, you can use an `always` type rule for self-loops:

```json
"exit_rules": [
  {
    "name": "passed",
    "type": "expression",
    "config": {
      "condition": "'STATUS: PASS' in str(outputs.get('result', ''))",
      "action": "advance"
    },
    "priority": 10
  },
  {
    "name": "max_attempts",
    "type": "expression",
    "config": {"condition": "attempt >= 5", "action": "advance"},
    "priority": 5
  },
  {
    "name": "refine",
    "type": "always",
    "config": {"action": "loop"},
    "priority": 1
  }
]
```

The `always` type is safe here because the self-loop does not target a dependency step.

### Human-in-the-loop draft/review cycle

```yaml
steps:
  draft:
    executor: agent
    prompt: |
      Write a proposal for: $objective
      Previous feedback: $feedback
    outputs: [proposal]
    inputs:
      feedback: review.feedback

  review:
    executor: human
    prompt: "Review the proposal. Set approved=true to accept, or provide feedback."
    outputs: [approved, feedback]
    sequencing: [draft]

    exits:
      - name: approved
        when: "outputs.approved == 'true' or outputs.approved == True"
        action: advance
      - name: max_rounds
        when: "attempt >= 5"
        action: escalate
      - name: revise
        when: "True"
        action: loop
        target: draft
```

Note: `draft` has an input from `review` (feedback). On the first iteration, this resolves to null/empty since `review` hasn't run yet. After the loop, `review` has output, so `draft` receives the feedback.

---

## For-Each Steps (Fan-Out/Fan-In)

For-each steps iterate over a list produced by an upstream step, running an embedded sub-flow for each item. Results are collected as an ordered array.

### YAML Syntax

```yaml
steps:
  generate:
    executor: llm
    prompt: "List 5 topics about $subject"
    outputs: [topics]
    inputs:
      subject: $job.subject

  research_all:
    for_each: generate.topics        # "step_name.field" — must produce a list
    as: topic                        # iteration variable name (default: "item")
    on_error: continue               # "fail_fast" (default) | "continue"
    outputs: [results]               # defaults to [results] if omitted

    flow:
      steps:
        research:
          executor: agent
          prompt: "Research this topic: $topic"
          outputs: [result]
          inputs:
            topic: $job.topic        # access current item via $job.<as_variable>

        review:
          executor: llm
          prompt: "Rate research quality for: $finding"
          outputs: [score]
          inputs:
            finding: research.result

  summarize:
    executor: llm
    prompt: "Synthesize these research results: $all_results"
    outputs: [summary]
    inputs:
      all_results: research_all.results   # array of sub-flow terminal outputs
```

### Key Concepts

- `for_each: step.field` — source list to iterate over. Supports nested fields like `step.design.sections`
- `as: variable_name` — names the iteration variable (default: `item`). Accessed in sub-flow via `$job.<variable_name>`
- `flow:` — embedded workflow with its own `steps:` block. Each iteration runs this as an independent sub-job
- `on_error: fail_fast` (default) — first failure cancels remaining items and fails the step
- `on_error: continue` — failures become `{"_error": "..."}` in results; remaining items continue
- Results are collected in source list order. Output artifact: `{"results": [...]}`
- Empty source lists complete immediately with `{"results": []}`
- Parent-level `inputs:` on the for_each step are passed through to every sub-job

### When to Use For-Each vs Manual Fan-Out

**Use for_each when:**
- The list size is dynamic (determined at runtime)
- Every item runs the same pipeline
- You want ordered results collected automatically

**Use manual fan-out (explicit parallel steps) when:**
- You have a fixed, known set of branches
- Each branch has different logic/configuration
- Branches need different executor types

### For-Each Patterns

#### Simple transform
```yaml
process_all:
  for_each: source.items
  flow:
    steps:
      transform:
        run: scripts/transform.py
        outputs: [result]
        inputs:
          item: $job.item
```

#### Multi-step pipeline per item
```yaml
review_all:
  for_each: generate.sections
  as: section
  flow:
    steps:
      write:
        executor: agent
        prompt: "Write content for: $section"
        outputs: [content]
        inputs:
          section: $job.section
      review:
        executor: llm
        prompt: "Review: $text"
        outputs: [score, feedback]
        inputs:
          text: write.content
```

#### With parent context passthrough
```yaml
process_all:
  for_each: source.items
  inputs:
    style: $job.style              # parent input passed to all iterations
  flow:
    steps:
      process:
        executor: llm
        prompt: "Process $item in style: $style"
        outputs: [result]
        inputs:
          item: $job.item
          style: $job.style
```

---

## Route Steps (Conditional Dispatch)

Route steps dispatch to different sub-flows based on upstream output. An upstream step classifies or categorizes, then the route step picks the right pipeline. Think of it as a `switch` statement for workflows.

### YAML Syntax

```yaml
steps:
  triage:
    executor: llm
    prompt: "Classify this issue as trivial, standard, or complex"
    outputs: [category, summary]

  run_pipeline:
    inputs: { category: triage.category, summary: triage.summary }
    routes:
      trivial:
        when: "category == 'trivial'"
        flow:
          steps:
            quick_fix:
              executor: llm
              prompt: "Quick fix for: $summary"
              outputs: [result]
      standard:
        when: "category == 'standard'"
        flow: flows/standard-pipeline.yaml
      complex:
        when: "category == 'complex'"
        flow: flows/complex-pipeline.yaml
      default:
        flow: flows/standard-pipeline.yaml
    outputs: [result]
```

### Key Concepts

- `routes:` is a mapping of route names to `{when, flow}` entries
- **First-match semantics** — routes evaluate in YAML declaration order; first `when:` that returns true wins
- `default:` route may omit `when:` — always matches, always evaluated last regardless of YAML position
- Non-default routes **must** have a `when:` expression
- Route steps **must** declare `outputs:`
- Route steps **cannot** also be for-each steps (mutually exclusive)

### Three Flow Source Types

| Type | Syntax | Resolved |
|------|--------|----------|
| **Inline** | dict with `steps:` | Parsed at load time |
| **File path** | string ending `.yaml`/`.yml` | Loaded and baked at parse time (relative to parent flow dir) |
| **Registry ref** | string starting `@` (e.g., `@alice:fast-pipeline`) | Resolved at runtime (coming in M9) |

File refs are "baked" at parse time — the resolved workflow is stored inline, so jobs don't depend on the original file at runtime.

### Output Contract

Every terminal step of each sub-flow must independently produce **all** declared `outputs:` of the route step. This prevents false positives when sub-flows branch internally. If the contract fails, validation errors at load time.

### Expression Namespace

Route `when:` expressions use the same safe eval as exit rules, with:
- All input bindings by name (e.g., `category`, `summary`)
- `attempt` — starts at 1, increments if the route step is re-executed via loop
- The name `attempt` is reserved — cannot be used as an input binding name
- Same safe builtins as exit rules (`any`, `all`, `len`, `min`, `max`, etc.)

### Error Handling

- Expression evaluation errors fail the step immediately (no fallthrough to next route)
- If no route matches and no `default` exists, the step fails with `route_no_match`
- Sub-job creation failures mark the run as failed, not orphaned

### Route Patterns

#### Simple category dispatch
```yaml
handle:
  inputs: { type: classify.type }
  routes:
    bug:
      when: "type == 'bug'"
      flow:
        steps:
          fix:
            executor: agent
            prompt: "Fix the bug: $description"
            outputs: [result]
    feature:
      when: "type == 'feature'"
      flow:
        steps:
          design:
            executor: agent
            prompt: "Design the feature: $description"
            outputs: [result]
    default:
      flow:
        steps:
          generic:
            executor: llm
            prompt: "Handle: $description"
            outputs: [result]
  outputs: [result]
```

#### Route with file refs
```yaml
process:
  inputs: { tier: evaluate.tier }
  routes:
    premium:
      when: "tier == 'premium'"
      flow: flows/premium-pipeline.yaml
    standard:
      when: "tier == 'standard'"
      flow: flows/standard-pipeline.yaml
    default:
      flow: flows/basic-pipeline.yaml
  outputs: [result, summary]
```

#### Route + downstream consumption
```yaml
steps:
  classify:
    executor: llm
    prompt: "Classify: $input"
    outputs: [category]

  dispatch:
    inputs: { category: classify.category }
    routes:
      fast:
        when: "category == 'simple'"
        flow:
          steps:
            solve:
              run: scripts/quick.sh
              outputs: [answer]
      slow:
        when: "category == 'complex'"
        flow:
          steps:
            solve:
              executor: agent
              prompt: "Deep analysis..."
              outputs: [answer]
    outputs: [answer]

  report:
    executor: llm
    prompt: "Generate report from: $answer"
    outputs: [report]
    inputs:
      answer: dispatch.answer     # from whichever sub-flow ran
```

---

## Template Variables (Prompt Templating)

Prompts use Python `string.Template` syntax: `$variable` or `${variable}`. Rendering uses `safe_substitute`.

### Auto-injected variables (agent executor only)

- `$objective` -- the job's objective string
- `$workspace` -- the job's workspace directory path

### Input-bound variables

Every input binding creates a template variable matching its `local_name`:

```yaml
analyze:
  executor: llm
  prompt: "Analyze: $data\nFocus: $focus_area"
  outputs: [analysis]
  inputs:
    data: fetch.result              # creates $data
    focus_area: $job.focus          # creates $focus_area
```

---

## Step Limits

Enforce cost, time, and iteration bounds per step. The engine checks limits each tick and cancels executors that exceed them.

```yaml
expensive_step:
  executor: agent
  prompt: "..."
  outputs: [result]
  limits:
    max_cost_usd: 2.00             # cap API spend (checked via executor cost_so_far)
    max_duration_minutes: 30       # wall-clock from step start
    max_iterations: 10             # max completed runs when looping
```

- `max_cost_usd` -- engine cancels the executor if cost exceeds this
- `max_duration_minutes` -- engine cancels the executor if wall-clock exceeds this
- `max_iterations` -- when exit rules loop back to this step, after N completions the loop escalates (pauses the job)

---

## Idempotency Modes

| Mode | Behavior |
|------|----------|
| `idempotent` (default) | Safe to re-run. Engine may auto-retry on transient failure |
| `allow_restart` | Can be manually restarted but not auto-retried. Recommended for agent steps |
| `retriable_with_guard` | Retry only with guards checking for side effects |
| `non_retriable` | Never retried. Retry decorator skips these. Use for irreversible operations |

```yaml
send_email:
  run: scripts/send_email.py
  outputs: [sent]
  idempotency: non_retriable         # don't accidentally send twice
```

---

## Decorators

Composable wrappers applied to executors. Declared per-step, applied in order (first = outermost).

### timeout

```yaml
decorators:
  - type: timeout
    config: { minutes: 30 }
```

### retry

```yaml
decorators:
  - type: retry
    config:
      max_retries: 2
      backoff: none                  # "none" or "exponential"
```

Respects the step's `idempotency` -- won't retry `non_retriable` steps.

### notification

```yaml
decorators:
  - type: notification
    config: { webhook_url: "https://..." }
```

### fallback

```yaml
decorators:
  - type: fallback
    config:
      fallback_ref:
        type: script
        config: { command: "echo '{\"result\": \"fallback\"}'" }
```

---

## Complete Flow Examples

### Example 1: Simple Linear Pipeline

```yaml
name: data-pipeline

steps:
  fetch:
    run: scripts/fetch_data.py
    outputs: [data, record_count]
    inputs:
      source_url: $job.url

  transform:
    run: scripts/transform.py
    outputs: [cleaned_data, stats]
    inputs:
      raw_data: fetch.data

  load:
    run: scripts/load_to_db.py
    outputs: [rows_inserted]
    inputs:
      data: transform.cleaned_data
    idempotency: non_retriable
```

### Example 2: AI Code Review with Human Approval

```yaml
name: code-review

steps:
  analyze:
    executor: agent
    prompt: |
      Review the code changes in this PR: $pr_url

      Check for:
      1. Correctness and logic errors
      2. Security vulnerabilities
      3. Performance concerns
      4. Style and maintainability
    outputs: [result]
    inputs:
      pr_url: $job.pr_url
    limits:
      max_cost_usd: 1.00
    idempotency: allow_restart

  approve:
    executor: human
    prompt: "Review the AI analysis. Decide: approve, request_changes, or escalate."
    outputs: [decision, notes]
    inputs:
      review: analyze.result

    exits:
      - name: approved
        when: "outputs.decision == 'approve'"
        action: advance
      - name: needs_changes
        when: "outputs.decision == 'request_changes'"
        action: loop
        target: analyze
      - name: escalate_it
        when: "outputs.decision == 'escalate' or attempt >= 3"
        action: escalate

  merge:
    run: scripts/merge_pr.sh
    outputs: [merged]
    inputs:
      pr_url: $job.pr_url
    sequencing: [approve]
    idempotency: non_retriable
```

### Example 3: Research with Parallel Branches

```yaml
name: market-research

steps:
  gather_web:
    executor: agent
    prompt: "Research $topic using web searches. Summarize key findings."
    outputs: [result]
    inputs:
      topic: $job.topic
    limits:
      max_cost_usd: 0.50
    idempotency: allow_restart

  gather_internal:
    run: scripts/search_docs.py
    outputs: [findings]
    inputs:
      query: $job.topic

  synthesize:
    executor: llm
    prompt: |
      Synthesize these research findings:

      Web research: $web_findings
      Internal docs: $internal_findings

      Provide: executive summary, key themes, recommendations.
    model: balanced
    temperature: 0.3
    outputs: [analysis]
    inputs:
      web_findings: gather_web.result
      internal_findings: gather_internal.findings

  review:
    executor: human
    prompt: "Review the research synthesis. Accept or request deeper analysis."
    outputs: [accepted, feedback]
    inputs:
      analysis: synthesize.analysis

    exits:
      - name: accepted
        when: "outputs.accepted == 'yes' or outputs.accepted == True"
        action: advance
      - name: redo
        when: "attempt < 3"
        action: loop
        target: gather_web
      - name: max_iterations
        when: "attempt >= 3"
        action: advance
```

`gather_web` and `gather_internal` run in parallel (no dependencies between them). `synthesize` waits for both.

### Example 4: Full Agent Pipeline (JSON, for API/Templates)

```json
{
  "name": "plan-implement-review",
  "description": "Plan a code change, implement it, evaluate quality",
  "workflow": {
    "steps": {
      "clarify": {
        "name": "clarify",
        "executor": {
          "type": "agent",
          "config": {
            "prompt": "Explore the codebase for: $objective\n\nAsk ONE clarifying question.",
            "output_mode": "stream_result"
          },
          "decorators": []
        },
        "outputs": ["result"],
        "inputs": [],
        "sequencing": [],
        "exit_rules": [],
        "idempotency": "allow_restart",
        "limits": null
      },
      "answer": {
        "name": "answer",
        "executor": {
          "type": "human",
          "config": {"prompt": "Answer the clarifying question from the clarify step."},
          "decorators": []
        },
        "outputs": ["response"],
        "inputs": [],
        "sequencing": ["clarify"],
        "exit_rules": [],
        "idempotency": "allow_restart",
        "limits": null
      },
      "check": {
        "name": "check",
        "executor": {
          "type": "agent",
          "config": {
            "prompt": "Evaluate context sufficiency.\n\nRequest: $objective\nQuestion: $question\nAnswer: $response\n\nEnd with STATUS: READY or STATUS: NEEDS_MORE",
            "output_mode": "stream_result"
          },
          "decorators": []
        },
        "outputs": ["result"],
        "inputs": [
          {"local_name": "question", "source_step": "clarify", "source_field": "result"},
          {"local_name": "response", "source_step": "answer", "source_field": "response"}
        ],
        "sequencing": ["answer"],
        "exit_rules": [
          {
            "name": "ready",
            "type": "expression",
            "config": {
              "condition": "'STATUS: READY' in str(outputs.get('result', ''))",
              "action": "advance"
            },
            "priority": 10
          },
          {
            "name": "max_attempts",
            "type": "expression",
            "config": {"condition": "attempt >= 5", "action": "advance"},
            "priority": 5
          },
          {
            "name": "needs_more",
            "type": "expression",
            "config": {
              "condition": "'STATUS: NEEDS_MORE' in str(outputs.get('result', ''))",
              "action": "loop",
              "target": "clarify"
            },
            "priority": 1
          }
        ],
        "idempotency": "allow_restart",
        "limits": null
      },
      "implement": {
        "name": "implement",
        "executor": {
          "type": "agent",
          "config": {
            "prompt": "Implement the changes.\n\nContext: $context\nRequest: $objective\n\nWrite code, run tests, iterate until passing.",
            "output_mode": "effect"
          },
          "decorators": []
        },
        "outputs": ["result"],
        "inputs": [
          {"local_name": "context", "source_step": "check", "source_field": "result"}
        ],
        "sequencing": ["check"],
        "exit_rules": [],
        "idempotency": "allow_restart",
        "limits": {"max_cost_usd": 5.0, "max_duration_minutes": 30}
      },
      "evaluate": {
        "name": "evaluate",
        "executor": {
          "type": "agent",
          "config": {
            "prompt": "Review the code changes in $workspace.\n\nScore 1-10: correctness, completeness, quality.\nIf all >= 7: STATUS: PASS\nElse: STATUS: NEEDS_WORK with specific improvements.",
            "output_mode": "stream_result"
          },
          "decorators": []
        },
        "outputs": ["result"],
        "inputs": [],
        "sequencing": ["implement"],
        "exit_rules": [
          {
            "name": "passed",
            "type": "expression",
            "config": {
              "condition": "'STATUS: PASS' in str(outputs.get('result', ''))",
              "action": "advance"
            },
            "priority": 10
          },
          {
            "name": "max_attempts",
            "type": "expression",
            "config": {"condition": "attempt >= 3", "action": "escalate"},
            "priority": 5
          },
          {
            "name": "needs_work",
            "type": "expression",
            "config": {
              "condition": "'STATUS: NEEDS_WORK' in str(outputs.get('result', ''))",
              "action": "loop",
              "target": "implement"
            },
            "priority": 1
          }
        ],
        "idempotency": "allow_restart",
        "limits": null
      }
    }
  }
}
```

---

## Running Flows

### CLI (preferred)

```bash
# Headless — terminal output, stdin for human steps
stepwise run my-flow.flow.yaml

# With live web UI
stepwise run my-flow.flow.yaml --watch

# Pass inputs
stepwise run my-flow.flow.yaml --var topic="login flow UX" --var pr_url="https://..."

# Specific port
stepwise run my-flow.flow.yaml --watch --port 9000
```

### API (for programmatic use)

Server default: `http://localhost:8340` (via `stepwise serve` or `stepwise run --watch`).

#### Create a job

```bash
curl -X POST http://localhost:8340/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "objective": "Review and improve the login flow",
    "workflow": { "steps": { ... } },
    "inputs": { "topic": "login flow UX" },
    "config": {
      "max_sub_job_depth": 5,
      "timeout_minutes": 120
    },
    "workspace_path": "/path/to/workspace"
  }'
```

Returns full job object with `id` field.

#### Start a job

```bash
curl -X POST http://localhost:8340/api/jobs/{job_id}/start
```

#### Other endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/jobs` | List jobs (query: `status`, `top_level`) |
| `GET` | `/api/jobs/{id}` | Job details |
| `POST` | `/api/jobs/{id}/pause` | Pause a running job |
| `POST` | `/api/jobs/{id}/resume` | Resume a paused job |
| `POST` | `/api/jobs/{id}/cancel` | Cancel (terminates running executors) |
| `DELETE` | `/api/jobs/{id}` | Delete job and all data |
| `GET` | `/api/jobs/{id}/tree` | Job hierarchy with sub-jobs |
| `GET` | `/api/jobs/{id}/runs` | Step runs (query: `step_name`) |
| `POST` | `/api/jobs/{id}/steps/{name}/rerun` | Manually re-run a step |
| `POST` | `/api/runs/{id}/fulfill` | Submit human step response |
| `POST` | `/api/jobs/{id}/context` | Inject context into running job |
| `GET/POST` | `/api/templates` | Template CRUD |

#### Fulfill a human step

```bash
curl -X POST http://localhost:8340/api/runs/{run_id}/fulfill \
  -H "Content-Type: application/json" \
  -d '{"payload": {"decision": "approve", "reason": "Looks good"}}'
```

#### WebSocket

`ws://localhost:8340/ws` -- real-time job state updates and agent output streaming.

---

## Common Gotchas

### 1. Loop guard blocks `always`-type loops to dependency steps

The engine prevents infinite re-triggering with a loop guard. If a step has:
- A completed (but non-current) run
- An `always`-type exit rule with `action: loop` targeting one of its own dependency steps

...the engine will NOT re-trigger it.

**Fix:** Use `expression` type instead of `always` for multi-step loops:

```yaml
# BAD (in JSON): type "always" targeting a dependency — blocked by loop guard
{"type": "always", "config": {"action": "loop", "target": "my_dep"}}

# GOOD: expression type that always evaluates true — NOT blocked
{"type": "expression", "config": {"condition": "True", "action": "loop", "target": "my_dep"}}

# GOOD (YAML): all YAML exits are expression type automatically
- name: retry
  when: "True"
  action: loop
  target: my_dep
```

The guard only checks `rule.type == "always"`. Self-loops with `always` type (target is self or omitted) are fine.

### 2. safe_substitute leaves missing variables as literal text

`string.Template.safe_substitute` does NOT error on missing variables. If you typo `$dta` instead of `$data`, the literal `$dta` appears in the rendered prompt. Double-check that `$variable` names in prompts match input `local_name` values exactly.

To include a literal `$` in a prompt, escape it as `$$`.

### 3. Outputs must be declared AND produced

The engine validates that a step's artifact contains all declared output fields. If the executor doesn't produce a declared field, the step fails. Only declare fields you actually produce.

### 4. Input bindings already imply ordering

You don't need `sequencing: [step_a]` if you already have `inputs: { x: step_a.field }`. The data binding creates the dependency. Use `sequencing` only for ordering without data transfer.

### 5. Agent output_mode must match downstream expectations

If a downstream step expects `result` from an agent step, make sure the agent uses `output_mode: "stream_result"`. The default `"effect"` mode produces `{"status": "completed", ...}`, not the agent's textual response.

### 6. Currentness and supersession

When a loop creates a new run of a step, ALL downstream steps that depended on the previous run become non-current and will re-execute. This is by design for data consistency.

### 7. dep_will_be_superseded prevents premature launches

If step C depends on step A, and step B is currently running with a loop exit rule targeting A, the engine won't launch C yet -- B might loop back to A, invalidating C's dependency. C waits until B's exit resolves.

### 8. YAML exits vs JSON exit_rules

YAML uses `exits:` with `when:` conditions. The loader converts these to `expression` type rules automatically, assigning priority by position. JSON uses `exit_rules:` with explicit `type`, `config`, and `priority`.

### 9. In YAML, loop exits require a target

The YAML parser requires `target:` when `action: loop`. If you want a self-loop in YAML, set `target` to the step's own name.

### 10. Route expression errors don't fallthrough

If a route's `when:` expression throws an error (bad syntax, undefined variable), the step **fails immediately**. It does NOT skip to the next route. Make sure all variables in `when:` expressions are bound via `inputs:`. Common mistake: referencing a field name that's not in the route step's `inputs` mapping.

### 11. Route file refs are baked at parse time

File path flow references (e.g., `flow: flows/pipeline.yaml`) are loaded and inlined when the flow YAML is parsed. The resolved workflow is stored inline — the original file is not needed at runtime. If you update the referenced file, you must re-parse the parent flow to pick up changes.

### 12. First-iteration null inputs in loops

When a loop step has an input from a step that hasn't run yet (e.g., `draft` takes feedback from `review` on the first iteration), the input resolves to null/empty. Design prompts to handle this gracefully (e.g., "Previous feedback (if any): $feedback").

---

## Validation Checklist

Before outputting a flow, verify:

1. Every step's `name` matches its dict key (JSON) or is set automatically (YAML)
2. Every input binding's `source_step` is a valid step name or `"$job"`
3. Every input binding's `source_field` exists in the source step's `outputs`
4. Every exit rule with `action: loop` has a valid `target` step name
5. No structural cycles in the DAG (loops are modeled via exit rules, not graph edges)
6. At least one entry step (no input deps from other steps, no sequencing)
7. At least one terminal step (no other step depends on it)
8. Every step has at least one declared output
9. Expression conditions use only `outputs`, `attempt`, `max_attempts`, and safe builtins
10. Multi-step loops (target is a dependency) use `expression` type, not `always`
11. Agent steps that pass text downstream use `output_mode: "stream_result"`
12. Prompt `$variables` match input `local_name` values exactly
13. All loop patterns have a safety cap (`attempt >= N` rule at medium priority)
14. For-each steps: `for_each` source references a valid step and field that produces a list
15. For-each steps: sub-flow steps access the iteration variable via `$job.<as_variable>`
16. For-each steps: downstream steps reference `for_each_step.results` (the collected array)
17. Route steps: every non-default route has a `when:` expression
18. Route steps: at most one `default` route (no `when:`)
19. Route steps: every terminal step of each sub-flow produces all declared `outputs:`
20. Route steps: `outputs:` is declared (required)
21. Route steps: not combined with `for_each` on the same step
22. Route steps: `attempt` is not used as an input binding name (reserved)
23. Route steps: `when:` expressions use only input bindings, `attempt`, and safe builtins
