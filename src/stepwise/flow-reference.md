# Stepwise Flow Reference

Complete YAML format specification for authoring Stepwise flows.

## Flow Formats

Flows can be either a single file or a directory:

**Single file:** `my-flow.flow.yaml` — self-contained, everything in one file.

**Directory flow:** `my-flow/FLOW.yaml` — the flow definition lives in `FLOW.yaml` inside a directory. Co-located scripts, prompts, and data files sit alongside it.

```
my-flow/
  FLOW.yaml              # flow definition (required)
  analyze.py             # co-located script
  prompts/
    system.md            # prompt loaded via prompt_file
```

Both formats work everywhere: `stepwise run`, `stepwise validate`, `stepwise share`, etc. Create directory flows with `stepwise new <name>`.

If `name` is omitted, it defaults from the filename (`my-flow.flow.yaml` → `my-flow`).

## Structure

```yaml
name: my-flow                    # kebab-case identifier
description: "What this flow does"
author: alice                    # optional, auto from git config
version: "1.0"                   # optional
tags: [research, agent]          # optional

steps:
  step-name:
    # Executor (exactly one required)
    run: scripts/foo.sh              # shorthand for script executor
    # OR
    executor: human                  # human | llm | agent
    prompt: "Instructions for executor"
    # OR
    prompt_file: prompts/task.md     # mutually exclusive with prompt

    outputs: [field1, field2]        # required — keys the executor must produce

    inputs:                          # optional
      local_name: other_step.field   # from upstream step output
      from_job: $job.param           # from job-level inputs (--var)

    sequencing: [step_a]             # optional — ordering without data transfer

    exits:                           # optional — evaluated after step completes
      - name: rule_name
        when: "python expression"
        action: advance              # advance | loop | escalate | abandon
        target: step_name            # required when action is loop

    limits:                          # optional
      max_cost_usd: 1.50
      max_duration_minutes: 30
      max_iterations: 10

    idempotency: idempotent          # idempotent (default) | allow_restart | retriable_with_guard | non_retriable

    decorators:                      # optional — timeout, retry, fallback, notification
      - type: timeout
        config: { minutes: 30 }
      - type: retry
        config: { max_retries: 2 }
```

## Executor Types

### script

Runs a shell command. Stdout is parsed as JSON for outputs. Non-zero exit = failure.

```yaml
build:
  run: scripts/build.sh           # .py files auto-prefixed with python3
  outputs: [artifact, version]
```

| Config field | Type | Required | Description |
|---|---|---|---|
| `command` | string | yes | Shell command to run (set via `run:` shorthand) |
| `working_dir` | string | no | Override working directory |

**How outputs work:** The script prints JSON to stdout. Keys matching declared `outputs` are extracted as the step's artifact. If stdout is not valid JSON, it is stored as `{"stdout": "..."}`.

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

**Environment variables available to scripts:**
- `JOB_ENGINE_INPUTS` — path to JSON file containing resolved input values
- `JOB_ENGINE_WORKSPACE` — workspace directory path
- `STEPWISE_FLOW_DIR` — flow directory path (if directory flow)

**Directory flows:** `run:` paths resolve relative to the flow directory. E.g., `run: analyze.py` in `my-flow/FLOW.yaml` resolves to `my-flow/analyze.py`. Scripts execute with cwd=workspace.

### human

Suspends for user input via web UI (`--watch`) or terminal (headless).

```yaml
approve:
  executor: human
  prompt: "Review the plan and decide: approve or reject."
  outputs: [decision, reason]
  inputs:
    plan: generate.plan
```

| Config field | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | yes | Instructions shown to the user |
| `notify` | string | no | Notification channel/webhook |

In `--watch` mode, the UI shows the prompt and collects output fields. In headless mode, the terminal prompts for each output field.

### llm

Single LLM call via OpenRouter with structured output extraction.

```yaml
summarize:
  executor: llm
  prompt: "Summarize: $text"        # $var placeholders from inputs
  # OR
  prompt_file: prompts/summarize.md # loads file at parse time (mutually exclusive with prompt)
  model: balanced                   # tier alias or full model ID (fast/balanced/strong)
  system: "You are concise."        # optional system message
  temperature: 0.3                  # optional, default 0.0
  max_tokens: 1024                  # optional, default 4096
  outputs: [summary]
  inputs:
    text: fetch.content
```

| Config field | Type | Required | Default | Description |
|---|---|---|---|---|
| `prompt` | string | yes* | — | Python `string.Template` with `$var` placeholders |
| `prompt_file` | string | yes* | — | Path to prompt file (mutually exclusive with `prompt`) |
| `model` | string | no | config default | Tier alias (`fast`, `balanced`, `strong`) or full OpenRouter model ID |
| `system` | string | no | none | System message |
| `temperature` | float | no | 0.0 | Sampling temperature |
| `max_tokens` | int | no | 4096 | Max output tokens |

*One of `prompt` or `prompt_file` required.

**Output extraction priority:**
1. Tool call response (structured output via function calling)
2. JSON in content body (markdown fences stripped automatically)
3. Single-field shortcut: if only one output declared, raw text content is used

### agent

Long-running AI agent session via ACP. Has tool access, reads/writes workspace files.

```yaml
implement:
  executor: agent
  prompt: "Implement: $plan\nRun tests before finishing."
  # OR: prompt_file: prompts/implement.md   (mutually exclusive with prompt)
  outputs: [result]
  inputs:
    plan: planning.result
  limits:
    max_cost_usd: 2.00
    max_duration_minutes: 60
  idempotency: allow_restart        # recommended for agent steps
```

| Config field | Type | Required | Default | Description |
|---|---|---|---|---|
| `prompt` | string | yes* | — | Python `string.Template`. `$objective` and `$workspace` are auto-injected |
| `prompt_file` | string | yes* | — | Path to prompt file (mutually exclusive with `prompt`) |
| `output_mode` | string | no | `"effect"` | How to extract outputs (see below) |
| `output_path` | string | no | — | File path for `"file"` mode |
| `agent` | string | no | `"claude"` | ACP agent name (claude, codex, gemini, etc.) |
| `working_dir` | string | no | workspace | Override working directory |
| `timeout` | int | no | — | Timeout in seconds |

*One of `prompt` or `prompt_file` required.

**Output modes:**

| Mode | Artifact | Use When |
|---|---|---|
| `"effect"` (default) | `{"status": "completed"}` | Agent modifies files; workspace IS the output |
| `"stream_result"` | `{"result": "<full agent text>"}` | You need the agent's textual response downstream |
| `"file"` | Parsed JSON from `output_path` | Agent writes structured JSON to a specific file |

**Important:** If downstream steps need the agent's text, use `output_mode: stream_result`.

Auto-injected prompt variables: `$objective`, `$workspace`.

## Inputs

```yaml
inputs:
  data: fetch_step.result          # from upstream step's output field
  topic: $job.topic                # from job-level input (--var topic="...")
```

- Input bindings **create implicit ordering** — no need to also add `sequencing`
- Use `sequencing: [step]` only for ordering without data transfer
- Dot-paths work: `step.field.nested`
- `local_name` must be unique within a step
- `source_field` must be in the source step's declared `outputs`

Job inputs are passed via `--var` on the CLI or in the `inputs` dict via the API:

```bash
stepwise run my-flow.flow.yaml --var topic="login flow UX"
```

## Exit Rules

Evaluated after step completion, first match wins. No rules = implicit advance.

```yaml
exits:
  - name: good_enough
    when: "float(outputs.quality_score) >= 0.8"
    action: advance
  - name: max_attempts
    when: "attempt >= 5"
    action: escalate                # pauses job for human inspection
  - name: retry
    when: "True"
    action: loop
    target: generate                # required for loop action
```

**Expression namespace:** `outputs` (DotDict), `attempt` (1-indexed), `max_attempts`.
**Safe builtins:** `any`, `all`, `len`, `min`, `max`, `sum`, `abs`, `round`, `sorted`, `int`, `float`, `str`, `bool`, `True`, `False`, `None`.

**Expression examples:**

```python
outputs.score >= 0.8
outputs.decision == 'approve'
attempt >= 5
'STATUS: READY' in str(outputs.get('result', ''))
any(s < 0.5 for s in outputs.scores)
len(outputs.errors) == 0
```

Keep expressions simple. If over ~80 characters, push the logic into the step itself and output a simple summary field.

**Actions:** `advance` (continue DAG), `loop` (re-run target, supersedes old run), `escalate` (pause job), `abandon` (fail job).

**Priority pattern:** success conditions first → safety bounds → loop fallback last.

## Loop Patterns

### Self-loop

```yaml
generate:
  executor: llm
  prompt: "Generate content about $topic."
  outputs: [content, quality_score]
  inputs: { topic: $job.topic }
  exits:
    - name: good
      when: "float(outputs.get('quality_score', 0)) >= 0.8"
      action: advance
    - name: cap
      when: "attempt >= 5"
      action: escalate
    - name: retry
      when: "True"
      action: loop
      target: generate
```

### Multi-step loop

When a downstream step loops back upstream, all intermediate steps re-execute with fresh data:

```yaml
steps:
  draft:
    executor: agent
    prompt: "Write proposal. Previous feedback: $feedback"
    outputs: [proposal]
    inputs:
      feedback: review.feedback     # null on first iteration — design prompts accordingly
  review:
    executor: human
    prompt: "Review. Set approved=true or provide feedback."
    outputs: [approved, feedback]
    sequencing: [draft]
    exits:
      - name: approved
        when: "outputs.approved == 'true' or outputs.approved == True"
        action: advance
      - name: cap
        when: "attempt >= 5"
        action: escalate
      - name: revise
        when: "True"
        action: loop
        target: draft
```

**Always include a safety cap** (`attempt >= N`) to prevent infinite loops.

## For-Each (Fan-Out/Fan-In)

Iterates over a list, running a sub-flow per item. Results collected in order.

```yaml
steps:
  generate:
    executor: llm
    prompt: "List 5 topics about $subject"
    outputs: [topics]
    inputs: { subject: $job.subject }

  research_all:
    for_each: generate.topics       # must produce a list
    as: topic                       # iteration variable (default: "item")
    on_error: continue              # "fail_fast" (default) | "continue"
    outputs: [results]              # defaults to [results]
    flow:
      steps:
        research:
          executor: agent
          prompt: "Research: $topic"
          outputs: [result]
          inputs:
            topic: $job.topic       # access item via $job.<as_variable>

  summarize:
    executor: llm
    prompt: "Synthesize: $all_results"
    outputs: [summary]
    inputs:
      all_results: research_all.results  # array of sub-flow outputs
```

- `on_error: continue` — failures become `{"_error": "..."}` in results
- Parent-level `inputs:` on for_each steps pass through to all sub-jobs
- Empty source list → immediate completion with `{"results": []}`
- Nested field paths work: `step.design.sections`

### When to use for-each vs manual fan-out

**Use for_each when:**
- The list size is dynamic (determined at runtime)
- Every item runs the same pipeline
- You want ordered results collected automatically

**Use manual fan-out (explicit parallel steps) when:**
- You have a fixed, known set of branches
- Each branch has different logic/configuration
- Branches need different executor types

### For-each patterns

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

## Flow Steps (Sub-Flow Composition)

Run another flow as a step. The simplest way to compose flows — and the **preferred approach** when a step delegates to a single flow.

```yaml
steps:
  research:
    executor: agent
    prompt: "Research $topic"
    outputs: [findings]

  council:
    flow: generic-council              # bare name — preferred
    inputs: { question: research.findings }
    outputs: [consensus]

  podcast:
    flow: @alice:podcast-generator     # community flow from registry
    inputs: { content: council.consensus }
    outputs: [episode_url]
```

**Flow reference types (in order of preference):**

1. `flow: name` — **bare flow name (preferred).** Resolved via project discovery (flows/ directory). Use this for local flows.
2. `flow: @author:name` — registry ref, fetched and baked at parse time. Use for community/shared flows.
3. `flow: path.yaml` — file path, resolved relative to the parent flow. **Avoid** — bare names are cleaner, portable, and don't break when directories move. Only use for flows outside the project's flows/ directory.
4. Inline sub-flow definition — use sparingly. If the sub-flow is worth naming, make it a standalone flow:

```yaml
  step-name:
    flow:
      steps:
        inner-step:
          executor: llm
          prompt: "..."
          outputs: [result]
    outputs: [result]
```

**Rules:**
- `flow:` is mutually exclusive with `run:`, `executor:`, `routes:`, and `for_each:`
- Must declare `outputs:` — every terminal step in the sub-flow must produce them
- All refs resolved at parse time — no network/file access at runtime
- Sub-flow receives parent step's resolved inputs as job-level inputs
- Cycle detection: A→B→A raises an error at parse time

**Prefer `flow:` over single-route `routes:` blocks.** This:

```yaml
council:
  flow: generic-council
  inputs: { question: research.findings }
  outputs: [consensus]
```

replaces this:

```yaml
# Don't write this — use flow: instead
council:
  inputs: { question: research.findings }
  routes:
    default:
      flow: ../generic-council/FLOW.yaml
  outputs: [consensus]
```

## Route Steps (Conditional Dispatch)

Dispatch to different sub-flows based on upstream output. First match wins.

```yaml
steps:
  triage:
    executor: llm
    prompt: "Classify this issue"
    outputs: [category, summary]

  handle:
    inputs: { category: triage.category, summary: triage.summary }
    routes:
      trivial:
        when: "category == 'trivial'"
        flow:
          steps:
            fix:
              executor: llm
              prompt: "Quick fix: $summary"
              outputs: [result]
      complex:
        when: "category == 'complex'"
        flow: complex-pipeline               # bare name (preferred over file paths)
      default:                               # no when: — always matches last
        flow: standard-pipeline
    outputs: [result]                        # required — every sub-flow must produce these
```

**Flow source types (prefer bare names):**

| Type | Syntax | Resolved |
|---|---|---|
| Bare name (preferred) | `flow: name` | Project discovery (flows/ directory) |
| Registry ref | `flow: @author:name` | Fetched and baked at parse time |
| File path | `flow: path.yaml` | Loaded relative to parent flow dir |
| Inline | `flow:` with nested `steps:` block | Parsed at load time |

All four types are resolved at parse time — the YAML is fetched/loaded and baked inline, so jobs never depend on external files or network at runtime.

**Output contract:** Every terminal step of each sub-flow must independently produce **all** declared `outputs:`. If the contract fails, validation errors at load time.

**Expression namespace:** Route `when:` expressions use the same safe eval as exit rules, with all input bindings by name plus `attempt` (starts at 1). The name `attempt` is reserved — cannot be used as an input binding name.

**Error handling:**
- Expression evaluation errors fail the step immediately (no fallthrough to next route)
- No match + no default = step failure
- Route steps cannot also be for_each steps

### Route patterns

#### Simple category dispatch

```yaml
handle:
  inputs: { type: classify.type, description: classify.description }
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

#### Route with bare name refs

```yaml
process:
  inputs: { tier: evaluate.tier }
  routes:
    premium:
      when: "tier == 'premium'"
      flow: premium-pipeline
    standard:
      when: "tier == 'standard'"
      flow: standard-pipeline
    default:
      flow: basic-pipeline
  outputs: [result, summary]
```

#### Route with downstream consumption

```yaml
steps:
  classify:
    executor: llm
    prompt: "Classify: $input"
    outputs: [category]
    inputs:
      input: $job.input

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

## Prompt Templating

`$variable` or `${variable}` — uses Python `string.Template.safe_substitute`.

- Input bindings create template variables matching `local_name`
- Agent executor auto-injects `$objective` and `$workspace`
- Missing variables render as literal `$name` (no error) — double-check spelling
- Literal `$` → escape as `$$`

```yaml
analyze:
  executor: llm
  prompt: "Analyze: $data\nFocus: $focus_area"
  outputs: [analysis]
  inputs:
    data: fetch.result              # creates $data
    focus_area: $job.focus          # creates $focus_area
```

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

- `max_cost_usd` — engine cancels the executor if cost exceeds this
- `max_duration_minutes` — engine cancels the executor if wall-clock exceeds this
- `max_iterations` — when exit rules loop back to this step, after N completions the loop escalates (pauses the job)

## Idempotency Modes

| Mode | Behavior |
|---|---|
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

Respects the step's `idempotency` — won't retry `non_retriable` steps.

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

## Complete Flow Examples

### Simple linear pipeline

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

### AI code review with human approval

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

### Research with parallel branches

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

## Gotchas

1. **Input bindings already imply ordering.** Don't add `sequencing: [A]` if you already have `inputs: {x: A.field}`.
2. **Agent `output_mode` must match downstream needs.** Default `effect` produces `{"status": "completed"}`, not agent text. Use `stream_result` if downstream steps need the response.
3. **Outputs must be declared AND produced.** Artifact keys must match the step's `outputs` list — missing keys fail the step.
4. **First-iteration null inputs in loops.** When `draft` takes `feedback` from `review` on first run, it's null. Design prompts to handle: `"Previous feedback (if any): $feedback"`.
5. **`safe_substitute` doesn't error on typos.** `$dta` instead of `$data` renders as literal `$dta`.
6. **YAML loop exits need `target`.** Always set `target` to the step name, even for self-loops.
7. **Currentness cascade.** When a loop supersedes a step's run, ALL downstream dependents re-execute.
8. **Route expression errors don't fallthrough.** Bad syntax or undefined variables fail the step, not skip to next route.
9. **File refs are baked at parse time.** Changes to referenced files require re-parsing the parent flow.
10. **Use bare flow names, not file paths.** `flow: generic-council` not `flow: ../generic-council/FLOW.yaml`. Bare names are portable and resolve via project discovery.
11. **Use `flow:` instead of single-route `routes:` blocks.** If there's no conditional dispatch, `flow: name` is cleaner than `routes: {default: {flow: name}}`.
12. **`flow:` can't combine with other executors.** Mutually exclusive with `run:`, `executor:`, `routes:`, and `for_each:`.
13. **`prompt` and `prompt_file` are mutually exclusive.** Never set both on the same step.
14. **Directory flow `run:` paths resolve relative to the flow directory**, not the current working directory.

## Validation Checklist

Before outputting a flow, verify:

1. Every input binding's `source_step` is a valid step name or `$job`
2. Every input binding's `source_field` exists in the source step's `outputs`
3. Every `loop` exit rule has a valid `target` step name
4. No structural cycles in the DAG (loops use exit rules, not graph edges)
5. At least one entry step (no deps) and one terminal step (nothing depends on it)
6. Every step has at least one declared output
7. Prompt `$variables` match input `local_name` values exactly
8. All loops have a safety cap (`attempt >= N` at medium priority)
9. Agent steps passing text downstream use `output_mode: stream_result`
10. `prompt` and `prompt_file` are mutually exclusive — never both on the same step
11. For directory flows, `run:` paths are relative to the flow directory, not cwd
12. For-each: `for_each` source produces a list; sub-flow accesses item via `$job.<as>`
13. Routes: every non-default route has `when:`; at most one `default`
14. Routes: every sub-flow terminal step produces all declared `outputs`
15. Routes: `attempt` cannot be used as an input binding name (reserved)
16. Flow steps: `flow:` cannot combine with `run:`, `executor:`, `routes:`, or `for_each:`
17. Flow steps: sub-flow terminal step(s) must produce all declared `outputs`
18. Flow refs: prefer bare names (`flow: council`) over file paths (`flow: ../council/FLOW.yaml`)

After generating, validate with `stepwise validate <flow>`.
