# Writing Flows

A flow is a YAML file that defines a workflow — a directed acyclic graph of steps. Each step declares what it does, what it needs, and what it produces. The engine handles ordering, parallelism, retries, and persistence.

This guide covers everything you need to author flows from scratch. For the complete field-by-field schema, see the [Flow Reference](flow-reference.md).

## Flow file structure

A flow file is any file ending in `.flow.yaml`. It can live anywhere — project root, a `flows/` directory, or nested in subdirectories. No special directory structure required.

Minimal flow:

```yaml
name: my-flow
steps:
  greet:
    run: echo '{"message": "hello"}'
    outputs: [message]
```

Every flow needs `name` and `steps`. Each step needs at least an executor (explicit or implied by `run:`) and `outputs`.

**Flow directories:** For flows with supporting files (scripts, prompts, data), use a directory with a `FLOW.yaml` inside:

```
flows/
  deploy/
    FLOW.yaml
    scripts/
      build.sh
      health-check.sh
```

Create a new flow directory with `stepwise new my-flow`.

**Validation:** Always validate before running:

```bash
stepwise validate my-flow.flow.yaml
```

This catches structural errors, missing references, unbounded loops, and more — without executing anything.

## Script steps

The simplest step type. Runs a shell command and parses JSON from stdout.

```yaml
name: fetch-example
steps:
  fetch:
    run: |
      curl -s "https://api.example.com/data?q=$query" | jq '{count: .total, items: .results}'
    inputs:
      query: $job.search_term
    outputs: [count, items]
```

**How it works:**
- The `run:` field is shorthand for `executor: script`
- Input values are available as `$variable_name` in the command and as `STEPWISE_INPUT_<NAME>` environment variables
- The command's stdout must be a JSON object whose keys match the declared `outputs`
- Non-zero exit code = step failure

**Multi-line scripts** work naturally with YAML `|` blocks. For complex logic, put it in a script file:

```yaml
  process:
    run: python3 scripts/process.py
    inputs:
      data: fetch.items
    outputs: [result, summary]
```

The script receives inputs as environment variables (`STEPWISE_INPUT_DATA`) and prints JSON to stdout.

## Agent steps

An agent step runs a full agentic session — an LLM with tools, iterating until it completes the task.

```yaml
name: research-example
steps:
  research:
    executor: agent
    prompt: |
      Research $topic thoroughly. Find primary sources, verify claims,
      and produce a structured summary.
    inputs:
      topic: $job.topic
    outputs: [summary, sources, confidence]
```

**Key options:**

| Field | Description |
|-------|-------------|
| `prompt` | The task description sent to the agent |
| `working_dir` | Directory where the agent runs (loads CLAUDE.md from there) |
| `outputs` | Declared outputs — agent receives instructions to write these as JSON |
| `emit_flow` | If `true`, agent can create sub-workflows dynamically |
| `continue_session` | If `true`, reuses the agent session across loop iterations |
| `loop_prompt` | Alternate prompt used on attempt > 1 |
| `max_continuous_attempts` | Circuit breaker for continued sessions |

When `outputs` is declared, the agent automatically receives a `STEPWISE_OUTPUT_FILE` environment variable and prompt instructions explaining the expected JSON structure. The agent writes the file; the engine reads and validates it.

**Dynamic sub-flows** with `emit_flow: true`:

```yaml
name: emit-example
steps:
  implement:
    executor: agent
    prompt: "Break this into steps and implement: $spec"
    emit_flow: true
    inputs:
      spec: $job.spec
    outputs: [result]
```

The agent can write a `.stepwise/emit.flow.yaml` file to its working directory. The engine launches the emitted flow as a sub-job and propagates results back.

## LLM steps

A single LLM API call — no tools, no iteration. Faster and cheaper than agent steps when you just need text generation or structured extraction.

```yaml
name: score-example
steps:
  score:
    executor: llm
    config:
      model: anthropic/claude-sonnet-4-20250514
      prompt: |
        Score this content on a 0-10 scale. Return JSON with "score" and "reasoning".
        Content: $content
    inputs:
      content: $job.content
    outputs: [score, reasoning]
```

The `config.model` field accepts any model available through your configured LLM provider. The response must be parseable as JSON matching the declared outputs.

## External steps

External steps pause the job and wait for human input. This is the primary mechanism for human-in-the-loop workflows — approvals, creative judgment, decisions that need a person.

```yaml
name: approval-example
steps:
  review:
    executor: external
    prompt: |
      The agent produced this analysis. Review and decide:

      Analysis: $analysis
      Confidence: $confidence

      Approve for publication, or request revisions?
    inputs:
      analysis: analyze.analysis
      confidence: analyze.confidence
    outputs: [decision, feedback]
```

When the job reaches this step, it suspends. The prompt appears in the web UI with input fields for each declared output. You can also fulfill from the CLI or API:

```bash
stepwise fulfill <run-id> '{"decision": "approve", "feedback": "Looks good"}'
```

**Typed fields** with `output_fields` for richer input forms:

```yaml
    output_fields:
      decision:
        type: enum
        options: [approve, revise, reject]
        description: "Your decision"
      feedback:
        type: text
        description: "Optional notes"
```

## Poll steps

Poll steps wait for an external condition by running a check command on an interval.

```yaml
name: poll-example
steps:
  wait-for-ci:
    executor: poll
    check_command: |
      gh pr view $pr_number --json statusCheckRollup \
        --jq 'select(.statusCheckRollup[0].conclusion != "") | {status: .statusCheckRollup[0].conclusion}'
    interval_seconds: 30
    prompt: "Waiting for CI checks on PR #$pr_number"
    inputs:
      pr_number: create-pr.pr_number
    outputs: [status]
```

**How it works:**
- `check_command` runs every `interval_seconds`
- Empty stdout or non-zero exit = not ready yet, keep polling
- JSON dict on stdout = fulfilled (the dict becomes the step's artifact)
- `$variable` placeholders in `check_command` and `prompt` are interpolated from inputs

Use poll steps for: CI status, deployment health, PR reviews, external API readiness — anything where you're waiting for a condition that changes on its own.

## Wiring inputs and outputs

Steps connect through input bindings. Three sources:

```yaml
inputs:
  # From another step's output
  data: fetch-data.raw_data         # step-name.field-name

  # From job-level inputs (--input flags)
  query: $job.search_term            # $job.field-name

  # Optional binding (resolves to None if source unavailable)
  previous_score:
    from: review.score
    optional: true
```

**Optional inputs** are key for iterative patterns. On the first iteration, an optional input resolves to `None`. On subsequent iterations (after a loop), the source has a value. This lets cycles work without deadlocks:

```yaml
name: iterate-example
steps:
  generate:
    executor: agent
    prompt: "Write content about $topic. Previous score: $score"
    inputs:
      topic: $job.topic
      score:
        from: review.score
        optional: true
    outputs: [content]

  review:
    executor: llm
    config:
      model: anthropic/claude-sonnet-4-20250514
      prompt: "Score this content 0-10: $content"
    inputs:
      content: generate.content
    outputs: [score]
    exits:
      - when: "float(outputs.score) >= 8"
        action: advance
      - when: "attempt < 3"
        action: loop
        target: generate
```

**`any_of` inputs** take from whichever branch completed (used with conditional branching):

```yaml
inputs:
  result:
    any_of:
      - quick-path.result
      - deep-path.result
```

**Nested paths** work for deeply structured outputs: `step-name.field.nested.path`.

## For-each

Iterate over a list, running an embedded sub-flow for each item:

```yaml
name: foreach-example
steps:
  plan:
    executor: agent
    prompt: "Break this into sections: $spec"
    inputs:
      spec: $job.spec
    outputs: [sections]

  process-sections:
    for_each: plan.sections
    as: section
    on_error: continue
    outputs: [results]
    flow:
      steps:
        write:
          executor: agent
          prompt: "Write content for: $section"
          outputs: [content]

        review:
          executor: llm
          config:
            model: anthropic/claude-sonnet-4-20250514
            prompt: "Review quality: $content"
          inputs:
            content: write.content
          outputs: [pass, feedback]
          exits:
            - when: "outputs.pass == True"
              action: advance
            - when: "attempt < 3"
              action: loop
              target: write
```

Each iteration runs as an independent sub-job. Items execute in parallel. Results are collected in source list order.

- `on_error: continue` — other items keep running if one fails (default: `fail_fast`)
- The `as` variable is available as an input to steps within the sub-flow

## Exit rules

Exit rules fire after a step completes, evaluating conditions to decide what happens next.

```yaml
exits:
  - name: success
    when: "outputs.status == 'done'"
    action: advance

  - name: stuck
    when: "attempt >= 3"
    action: escalate

  - name: retry
    when: "True"
    action: loop
    target: implement
    max_iterations: 5
```

**Four actions:**

| Action | Effect |
|--------|--------|
| `advance` | Continue to downstream steps |
| `loop` | Re-run `target` step (new attempt, fresh run) |
| `escalate` | Pause the job for human inspection |
| `abandon` | Fail the job |

Rules evaluate in order — first match wins. No match with explicit `advance` rules = step fails (prevents silent advancement past unhandled cases).

**The escalate pattern:** Use `escalate` as a safety bound between success and retry:

```yaml
exits:
  - name: success
    when: "outputs.passed == true"
    action: advance
  - name: stuck
    when: "attempt >= 3"
    action: escalate          # pauses for human triage
  - name: retry
    when: "True"
    action: loop
    target: implement
```

Priority: success first, then escalate as ceiling, then loop as fallback. Escalated jobs appear in `stepwise list --suspended`.

## Conditional branching

Steps declare their own activation condition via `when`, evaluated against resolved inputs:

```yaml
name: branch-example
steps:
  classify:
    run: scripts/classify.sh
    outputs: [category]

  quick-path:
    run: scripts/quick.sh
    inputs:
      category: classify.category
    when: "category == 'simple'"
    outputs: [result]

  deep-path:
    executor: agent
    prompt: "Deep analysis of $category data"
    inputs:
      category: classify.category
    when: "category == 'complex'"
    outputs: [result]

  report:
    run: scripts/report.sh
    inputs:
      result:
        any_of:
          - quick-path.result
          - deep-path.result
    outputs: [report]
```

Branching is **pull-based** — each step decides when it activates. When `classify` outputs `category == 'simple'`, `quick-path` activates and `deep-path` stays not-ready. At settlement, never-started steps get SKIPPED.

**Key distinction:**
- `after: [step-x]` — ordering only (wait, but no data)
- `inputs: { f: step-x.f }` — data dependency (implies ordering)
- `when: "expr"` — conditional gate on resolved inputs

## Caching

Opt-in, content-addressable caching for step results:

```yaml
steps:
  fetch:
    run: 'curl -s "$url" | jq .'
    inputs:
      url: $job.url
    outputs: [data]
    cache: true                    # enable with default TTL

  analyze:
    executor: llm
    config:
      prompt: "Analyze: $data"
    inputs:
      data: fetch.data
    outputs: [analysis]
    cache:
      ttl: 30m                    # custom TTL (default: 1h for script, 24h for llm/agent)
      key_extra: v2                # bump to invalidate existing cache
```

Cache key = SHA-256 of resolved inputs + executor config. Same inputs + config = cache hit, skipping execution entirely.

**Default TTLs:** script = 1 hour, llm/agent = 24 hours. External, poll, and emit_flow steps are never cached.

**Bypass cache** for a specific step in a single run:

```bash
stepwise run my-flow --rerun fetch
```

**Manage cache:**

```bash
stepwise cache stats                     # entries, hits, size
stepwise cache clear                     # clear all
stepwise cache clear --step fetch        # clear one step
stepwise cache debug my-flow fetch --input url=https://...  # inspect cache key
```

## Validation and preflight

Always validate before running. `stepwise validate` catches errors without executing anything:

```bash
stepwise validate my-flow.flow.yaml
```

**What it catches:**
- YAML syntax errors
- Missing step references in inputs and exit rules
- Invalid input bindings (referencing undeclared outputs)
- Unbounded loops (no `attempt` safety cap or `max_iterations`)
- Uncovered output combinations in external steps
- Type coercion warnings (`float()` on potentially None values)

**Preflight check** goes further — verifies runtime requirements:

```bash
stepwise preflight my-flow.flow.yaml
```

Checks that required API keys are configured, models are accessible, and script files exist.

**Treat warnings as defects.** A warning-free validate is the quality bar for production flows.

## What's next

- [Flow Reference](flow-reference.md) — complete field-by-field schema for every YAML option
- [Executors](executors.md) — deep dive into executor configuration and decorators
- [Patterns](patterns.md) — advanced idioms: session continuity, iterative delegation, fan-out/fan-in
- [Concepts](concepts.md) — the mental model behind jobs, steps, and the engine
- [Troubleshooting](troubleshooting.md) — error messages and fixes
