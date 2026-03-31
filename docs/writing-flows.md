# Writing Flows

A flow is a YAML file that defines a workflow — a directed acyclic graph of steps. Each step declares what it does, what it needs, and what it produces. The engine handles ordering, parallelism, retries, and persistence.

This guide covers everything you need to author flows from scratch. For the complete field-by-field schema, see the [YAML Format](yaml-format.md).

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
| `output_mode` | `"effect"` (default), `"stream_result"`, or `"file"` |
| `output_path` | File path for `output_mode: file` |

When `outputs` is declared, the agent automatically receives a `STEPWISE_OUTPUT_FILE` environment variable and prompt instructions explaining the expected JSON structure. The agent writes the file; the engine reads and validates it.

**Agent output modes:**

| Mode | Artifact | Use When |
|---|---|---|
| `"effect"` (default) | `{"status": "completed"}` | Agent modifies files; workspace IS the output |
| `"stream_result"` | `{"result": "<full agent text>"}` | You need the agent's textual response downstream |
| `"file"` | Parsed JSON from `output_path` | Agent writes structured JSON to a specific file |

```yaml
analyze:
  executor: agent
  output_mode: file
  output_path: .stepwise/analysis.json
  prompt: |
    Analyze the codebase. Write your findings as JSON to .stepwise/analysis.json
    with keys: overview, modules, risks.
  outputs: [overview, modules, risks]
```

**`output_mode: file` requires explicit prompt instructions.** The engine reads `output_path` after the agent finishes and parses it as JSON. Your prompt must tell the agent to write JSON to that location with keys matching the declared `outputs`.

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
    model: anthropic/claude-sonnet-4
    prompt: |
      Score this content on a 0-10 scale. Return JSON with "score" and "reasoning".
      Content: $content
    inputs:
      content: $job.content
    outputs: [score, reasoning]
```

**Configuration fields** (all set at step level, not nested in `config:`):

| Field | Required | Description |
|-------|----------|-------------|
| `model` | Yes | Full model ID (e.g., `anthropic/claude-sonnet-4`) or tier alias (e.g., `balanced`) |
| `prompt` | Yes | The user message. Supports `$variable` substitution from inputs. |
| `system` | No | System prompt |
| `temperature` | No | Sampling temperature (default: 0.0) |
| `max_tokens` | No | Maximum output tokens (default: 4096) |

The response must be parseable as JSON matching the declared outputs. The LLM executor uses structured output tooling to enforce this.

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
        type: choice
        options: [approve, revise, reject]
        description: "Your decision"
      feedback:
        type: text
        description: "Optional notes"
```

Valid field types: `str`, `text`, `number`, `bool`, `choice`.

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
    model: anthropic/claude-sonnet-4
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
          model: anthropic/claude-sonnet-4
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
- The `as` variable is available as an input to steps within the sub-flow via `$job.<as_variable>`
- Empty source lists complete immediately with `{"results": []}`
- If all items fail under `on_error: continue`, the for-each step itself fails

For-each steps support `when` conditions for conditional activation, just like regular steps.

## Sub-flow composition

Steps can delegate to other flows via the `flow:` field:

```yaml
steps:
  evaluate:
    flow: evaluate-quality              # bare name — resolved from project
    inputs:
      content: generate.report          # becomes $job.content in sub-flow
      rubric: "Score on depth, accuracy"
    outputs: [scores, average, critique]
```

Sub-flow sources can be:
- **Bare flow names** — `flow: evaluate-quality` (resolved from `flows/`, project root, `.stepwise/flows/`)
- **File paths** — `flow: ./sub-flows/eval.flow.yaml`
- **Registry refs** — `flow: @alice:evaluate-quality`
- **Inline dicts** — embed a `flow: { steps: { ... } }` directly

Sub-flow steps support `when` conditions for conditional activation.

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

Rules evaluate in order — first match wins. No match with explicit `advance` rules = step fails (prevents silent advancement past unhandled cases). No match with only loop/escalate/abandon rules = implicit advance. No exit rules at all = implicit advance.

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

**Boomerang steps:** Steps with no `advance` exit rules (only loop + escalate/abandon) are excluded from terminal step detection. They exist purely as loop machinery, not as workflow outputs.

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

## Derived outputs

Compute fields deterministically from a step's executor output. Evaluated after the executor returns but before exit rules.

```yaml
score:
  executor: llm
  prompt: |
    Score this plan on 8 dimensions (1-5 each).
    Respond with ONLY: {"scores": {"completeness": 4, "grounding": 3, ...}}
  outputs: [scores]
  derived_outputs:
    average: "sum(scores.values()) / len(scores)"
    passed: "sum(scores.values()) / len(scores) >= 4.0"
    lowest_three: "sorted(scores, key=scores.get)[:3]"
```

The LLM returns only `scores`. The engine computes `average`, `passed`, and `lowest_three` deterministically. All three become real step outputs that downstream steps and exit rules can reference.

**Expression environment:** Artifact fields as local variables, plus Python builtins (`sum`, `len`, `sorted`, `min`, `max`, `float`, `int`, `str`, `list`, `dict`, `set`, `tuple`, `round`, `abs`, `any`, `all`, `enumerate`, `zip`, `map`, `filter`, `range`, `True`, `False`, `None`) and `regex_extract(pattern, text, default)`.

## Session continuity

Agent and LLM steps with `continue_session: true` reuse the same session across loop iterations, continuing the conversation instead of starting fresh.

```yaml
implement:
  executor: agent
  prompt: "Implement: $spec"
  loop_prompt: "Tests failed:\n$failures\nFix the issues."
  continue_session: true
  max_continuous_attempts: 5
  inputs:
    spec: $job.spec
    failures:
      from: run-tests.failures
      optional: true
  outputs: [result]
```

| Field | Type | Default | Description |
|---|---|---|---|
| `continue_session` | bool | `false` | Reuse agent session across loop iterations |
| `loop_prompt` | string | --- | Alternate prompt template on attempt > 1 (falls back to `prompt`) |
| `max_continuous_attempts` | int | --- | After N iterations, force a fresh session |

**Cross-step session sharing:** Agent steps with `continue_session: true` auto-emit `_session_id`. Downstream steps continue the same conversation via optional input:

```yaml
steps:
  plan:
    executor: agent
    prompt: "Plan: $spec"
    continue_session: true
    inputs: { spec: $job.spec }
    outputs: [plan]

  implement:
    executor: agent
    prompt: "Implement the plan."
    continue_session: true
    inputs:
      plan: plan.plan
      _session_id:
        from: plan._session_id
        optional: true
    outputs: [result]
```

`_session_id` is a reserved output field — don't declare it in `outputs:`.

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
    model: anthropic/claude-sonnet-4
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

## Config variables

Declare configurable variables in a top-level `config:` block. These map to `$job.*` input bindings.

```yaml
config:
  persona:
    description: "Your AI persona"
    type: str
    required: true
    example: "You are a researcher..."
  api_key:
    description: "Service API key"
    sensitive: true                # masks in output, resolves from STEPWISE_VAR_API_KEY
  max_rounds:
    type: number
    default: 5
  voice_style:
    type: choice
    options: [conversational, formal, casual]
    default: conversational
```

**Resolution priority** (highest wins): `--input` > `--vars-file` > `config.local.yaml` > `STEPWISE_VAR_{NAME}` env vars > config defaults.

## Requirements

Declare external tool dependencies in a top-level `requires:` block.

```yaml
requires:
  - name: ffmpeg
    description: "Audio processing"
    check: "ffmpeg -version"
    install: "apt install ffmpeg"
    url: "https://ffmpeg.org"
  - camofox                        # shorthand: just a name
```

Requirements are checked by `stepwise validate`, `stepwise info`, and `stepwise preflight`. They are advisory — they don't block `stepwise run`.

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

- [YAML Format](yaml-format.md) — complete field-by-field schema for every YAML option
- [Executors](executors.md) — deep dive into executor configuration and decorators
- [Patterns](patterns.md) — advanced idioms: session continuity, iterative delegation, fan-out/fan-in
- [Troubleshooting](troubleshooting.md) — error messages and fixes
