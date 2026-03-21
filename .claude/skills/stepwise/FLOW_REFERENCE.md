# Stepwise Flow Reference

Complete YAML format specification for authoring Stepwise flows.

## Flow Format

Flows use a directory format: `flows/<name>/FLOW.yaml`. Co-located scripts, prompts, and data files live alongside the definition.

```
flows/
  my-flow/
    FLOW.yaml              # flow definition (required)
    analyze.py             # co-located script
    prompts/
      system.md            # prompt loaded via prompt_file
```

Create new flows with `stepwise new <name>`. If `name` is omitted in the YAML, it defaults from the directory name.

## Structure

```yaml
name: my-flow                    # kebab-case identifier
description: "What this flow does"
author: alice                    # optional, auto from git config
version: "1.0"                   # optional
tags: [research, agent]          # optional
forked_from: "@bob:original"     # optional, provenance for forked flows

config:                            # optional — declared config variables
  persona:
    description: "Your AI persona"
    type: str                      # str, text, number, bool, choice
    required: true                 # inferred true when no default
    example: "You are a researcher..."
  api_key:
    description: "Service API key"
    type: str
    sensitive: true                # masks value in output, suggests env var
  voice_style:
    description: "TTS voice style"
    type: choice
    options: [conversational, formal, casual]
    default: conversational        # has default → required: false

requires:                          # optional — external tool dependencies
  - name: ffmpeg
    description: "Audio processing"
    check: "ffmpeg -version"       # validated by stepwise validate (5s timeout)
    install: "apt install ffmpeg"  # shown when check fails
    url: "https://ffmpeg.org"      # docs link shown when check fails
  - camofox                        # shorthand: just a name

readme: |                          # optional — extended documentation
  # My Flow
  Detailed description of what this flow does and how to use it.
  For directory flows, omit this and add a README.md file instead.

steps:
  step-name:
    # Executor (exactly one required)
    run: scripts/foo.sh              # shorthand for script executor
    # OR
    executor: external                  # external | llm | agent
    prompt: "Instructions for executor"
    # OR
    prompt_file: prompts/task.md     # mutually exclusive with prompt

    outputs: [field1, field2]        # required — keys the executor must produce

    inputs:                          # optional
      local_name: other_step.field   # from upstream step output
      from_job: $job.param           # from job-level inputs (--var)

    sequencing: [step_a]             # optional — ordering without data transfer

    when: "python expression"        # optional — activation gate on resolved inputs

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

### external

Suspends for user input via web UI (`--watch`) or terminal (headless).

```yaml
approve:
  executor: external
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

### poll

Suspends and periodically runs a shell command to check for an external condition. Useful for waiting on CI, PR reviews, deployments, etc.

```yaml
wait-for-review:
  executor: poll
  check_command: |
    gh pr view $pr_number --json reviewDecision \
      --jq 'select(.reviewDecision != "") | {decision: .reviewDecision}'
  interval_seconds: 30
  prompt: "Waiting for PR #$pr_number review"
  outputs: [decision]
  inputs:
    pr_number: create-pr.pr_number
```

| Config field | Type | Required | Default | Description |
|---|---|---|---|---|
| `check_command` | string | yes | — | Shell command to run periodically. `$var` placeholders from inputs |
| `interval_seconds` | int | no | 60 | Seconds between checks |
| `prompt` | string | no | — | Human-readable description of what is being waited on |

**How it works:** The engine runs `check_command` every `interval_seconds`:
- **JSON dict on stdout** → step is fulfilled, dict becomes the artifact
- **Empty stdout** → not ready, check again next interval
- **Non-zero exit** → error, retry next interval

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

For **single-output** steps (e.g. `outputs: [response]`): no tool_choice is forced. The model responds naturally.
1. JSON in content body matching the field name (markdown fences stripped automatically)
2. Raw text content assigned to the single field
3. Tool call response (fallback if no content)

For **multi-output** steps (e.g. `outputs: [analysis, recommendation]`): tool_choice is forced via function calling.
1. Tool call response (structured output via function calling)
2. JSON in content body — preferred over tool call if content is 3x+ longer (truncation protection)
3. JSON extraction from mixed prose+JSON content

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
| `working_dir` | string | no | workspace | Override working directory. Agent starts in this directory and auto-loads CLAUDE.md from it. Supports `$variable` references from inputs. |
| `timeout` | int | no | — | Timeout in seconds |

*One of `prompt` or `prompt_file` required.

**`working_dir` — agent project targeting:**

When an agent step needs to operate on an external codebase, set `working_dir` to that project's path. The agent's CWD is set before it starts, and CLAUDE.md from that directory is auto-loaded into the agent's context — giving it project-specific conventions, architecture notes, and guardrails.

```yaml
plan:
  executor: agent
  working_dir: $project_path           # from --var project_path=/path/to/repo
  prompt: "Implement the feature: $spec"
  output_mode: stream_result
  outputs: [result]
  inputs:
    spec: $job.spec
    project_path: $job.project_path
```

Pass the path via `--var project_path=/home/user/my-repo`. Use `$variable` syntax — literal paths work but aren't portable across machines.

**Output modes:**

| Mode | Artifact | Use When |
|---|---|---|
| `"effect"` (default) | `{"status": "completed"}` | Agent modifies files; workspace IS the output |
| `"stream_result"` | `{"result": "<full agent text>"}` | You need the agent's textual response downstream |
| `"file"` | Parsed JSON from `output_path` | Agent writes structured JSON to a specific file |

**Important:** If downstream steps need the agent's text, use `output_mode: stream_result`.

Auto-injected prompt variables: `$objective`, `$workspace`.

### Session Continuity

Agent and LLM steps with `continue_session: true` reuse the same agent session across loop iterations, continuing the conversation instead of starting fresh. This saves tokens and preserves full conversational context.

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

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `continue_session` | bool | no | `false` | Reuse agent session across loop iterations |
| `loop_prompt` | string | no | — | Alternate prompt template used on attempt > 1 (falls back to `prompt`) |
| `max_continuous_attempts` | int | no | — | After N iterations, force fresh session with chain context backfill |

**Behavior:**
- First run (attempt 1): creates a new session, sends `prompt`
- Loop-back (attempt 2+): continues existing session, sends `loop_prompt` (or `prompt` if not set)
- When `continue_session` is true, M7a chain context injection is skipped (agent already has full history)
- If `max_continuous_attempts` is exceeded or the session crashes, falls back to fresh session + chain context backfill

**Cross-step session sharing via `_session_id`:**

Agent steps with `continue_session: true` automatically emit a `_session_id` output field. Downstream steps can reference this to continue the same conversation:

```yaml
steps:
  plan:
    executor: agent
    prompt: "Plan: $spec"
    continue_session: true
    inputs: { spec: $job.spec }
    outputs: [plan]
    # automatically emits _session_id

  implement:
    executor: agent
    prompt: "Now implement the plan."
    continue_session: true
    inputs:
      plan: plan.plan
      _session_id:
        from: plan._session_id
        optional: true
    outputs: [result]
    # continues plan's session
```

- `_session_id` is a reserved output field — flow authors don't declare it in `outputs:`
- If a step receives `_session_id` input + `continue_session`, it continues that session
- If a step has `continue_session` but no `_session_id` input, it creates a new session
- The engine serializes concurrent access to shared sessions via `_SessionLockManager`

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
- `any_of` inputs resolve from the first available completed source:
  ```yaml
  inputs:
    result:
      any_of:
        - branch-a.result
        - branch-b.result
  ```

### Optional Inputs

Optional inputs are weak-reference bindings that resolve to `None` when the source dep has no current completed run. They allow steps to proceed without waiting for a dependency, enabling loop-back data feeding, first-run defaults, and graceful degradation.

```yaml
inputs:
  topic: $job.topic                    # required — step waits for this
  score:
    from: review.score
    optional: true                     # resolves to None if review hasn't run
```

**Syntax:** Required inputs use bare `local_name: source` strings. Optional inputs use a dict with `from` + `optional: true`. This is consistent with the `any_of` dict syntax.

**None handling:**
- **In prompt templates** (`$var` interpolation): `None` renders as empty string `""`
- **In expression evaluation** (exit rules, `when`): `None` is first-class. Test with `score is None` / `score is not None`. Comparing `None` with `>`, `<`, `float()` raises an eval error (flow authoring bug).
- **In script executors** (`run:`): environment variable is unset (not "None" or "null"). Use `if [ -z "$score" ]; then ...`
- **`any_of` + `optional`:** Allowed. Means "try to get one of these, but if none are available, resolve to `None` and proceed."

**Cycle detection:** A cycle in the dependency graph is valid if every cycle contains at least one `optional: true` edge.

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
**Safe builtins:** `any`, `all`, `len`, `min`, `max`, `sum`, `abs`, `round`, `sorted`, `int`, `float`, `str`, `bool`, `True`/`true`, `False`/`false`, `None`/`null`.

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

**Actions:** `advance` (continue DAG), `loop` (re-run target, supersedes old run), `escalate` (pause job), `abandon` (fail job). Use step-level `when` for conditional branching instead of `advance` with `target`.

**Default when no rule matches:** If the step has explicit `advance` rules but none match, the step **fails** (prevents unhandled output cases from silently progressing). If the step has only loop/escalate/abandon rules (no advance rules), unmatched = implicit advance. No exit rules at all = implicit advance.

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
    executor: external
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
    inputs:
      style: $job.style             # parent inputs pass through to all sub-jobs
    outputs: [results]              # defaults to [results]
    flow:
      steps:
        research:
          executor: agent
          prompt: "Research: $topic (style: $style)"
          outputs: [result]
          inputs:
            topic: $job.topic       # access item via $job.<as_variable>
            style: $job.style       # access parent input via $job.<name>

  summarize:
    executor: llm
    prompt: "Synthesize: $all_results"
    outputs: [summary]
    inputs:
      all_results: research_all.results  # array of sub-flow outputs
```

- `on_error: continue` — failures become `{"_error": "..."}` in results; if all items fail, the step fails
- Empty source list → immediate completion with `{"results": []}`
- Nested field paths work: `step.design.sections`
- Sub-flows can have multiple steps — standard DAG rules apply inside
- Use for_each when the list size is dynamic; use explicit parallel steps when branches have different logic

## Flow Steps (Sub-Flow Composition)

Run another flow as a step.

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

1. `flow: name` — **bare flow name (preferred).** Resolved via project discovery (flows/ directory).
2. `flow: @author:name` — registry ref, fetched and baked at parse time.
3. `flow: path.yaml` — file path, resolved relative to parent flow. Avoid — bare names are portable.
4. Inline `flow:` with nested `steps:` block — use sparingly; prefer standalone flows.

**Rules:**
- `flow:` is mutually exclusive with `run:`, `executor:`, and `for_each:`
- Must declare `outputs:` — every terminal step in the sub-flow must produce them
- All refs resolved at parse time — no network/file access at runtime
- Sub-flow receives parent step's resolved inputs as job-level inputs
- Cycle detection: A→B→A raises an error at parse time

## Conditional Branching

Branch workflows using step-level `when` conditions (pure-pull) and merge with `any_of` inputs.

```yaml
steps:
  classify:
    executor: llm
    prompt: "Classify: $input"
    outputs: [category]
    inputs: { input: $job.input }

  quick-path:
    run: scripts/quick.sh
    inputs: { category: classify.category }
    outputs: [answer]
    when: "category == 'simple'"       # I decide when I run

  deep-path:
    executor: agent
    prompt: "Deep analysis..."
    inputs: { category: classify.category }
    outputs: [answer]
    when: "category == 'complex'"      # I decide when I run

  report:
    executor: llm
    prompt: "Generate report from: $answer"
    outputs: [report]
    inputs:
      answer:
        any_of:
          - quick-path.answer
          - deep-path.answer
```

- `when`: step-level condition evaluated against resolved inputs. If deps are satisfied but `when` is false, the step stays not-ready.
- `any_of` inputs: resolves from first available completed source (>= 2 entries required)
- At job settlement, never-started steps get SKIPPED runs for bookkeeping
- Job completes if at least one terminal completed; fails if no terminal reached
- Key distinction: `sequencing: [step-x]` = ordering only, `inputs: { field: step-x.field }` = data dep, `when: "expr"` = conditional gate

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

## Common Patterns

### Gating a post-loop step

Steps run as soon as their inputs or sequencing deps are satisfied — they don't wait for a loop to finish. A step sequenced after a looping step fires after the **first iteration**, not after the loop exits.

**Bug (runs too early):**

```yaml
steps:
  draft:
    executor: llm
    prompt: "Write about $topic"
    inputs: { topic: $job.topic }
    outputs: [content]

  review:
    executor: external
    prompt: "Score this: $content"
    inputs: { content: draft.content }
    outputs: [score]
    exits:
      - name: good
        when: "float(outputs.score) >= 0.8"
        action: advance
      - name: retry
        when: "attempt < 3"
        action: loop
        target: draft
        max_iterations: 3

  publish:
    run: './publish.sh "$content"'
    inputs: { content: draft.content }
    sequencing: [review]              # BUG: runs after review's first completion
    outputs: [url]
```

`publish` runs as soon as `review` completes once — even if review loops back to `draft`. Fix: gate with `when` on the loop's exit state:

```yaml
  publish:
    run: './publish.sh "$content"'
    inputs:
      content: draft.content
      score: review.score
    when: "float(score) >= 0.8"       # only runs when loop exits via "good"
    outputs: [url]
```

**Principle:** Any step downstream of a loop needs an explicit `when` condition. The engine has no concept of "loop finished" — only "step completed." `stepwise validate` warns about ungated post-loop steps.

## Gotchas

1. **Input bindings already imply ordering.** Don't add `sequencing: [A]` if you already have `inputs: {x: A.field}`.
2. **Agent `output_mode` must match downstream needs.** Default `effect` produces `{"status": "completed"}`, not agent text. Use `stream_result` if downstream steps need the response.
3. **Outputs must be declared AND produced.** Artifact keys must match the step's `outputs` list — missing keys fail the step.
4. **First-iteration null inputs in loops.** When `draft` takes `feedback` from `review` on first run, it's null. Design prompts to handle: `"Previous feedback (if any): $feedback"`.
5. **`safe_substitute` doesn't error on typos.** `$dta` instead of `$data` renders as literal `$dta`.
6. **YAML loop exits need `target`.** Always set `target` to the step name, even for self-loops.
7. **Currentness cascade.** When a loop supersedes a step's run, ALL downstream dependents re-execute.
8. **`any_of` needs >= 2 sources.** Single-source `any_of` is invalid — just use a regular input binding.
9. **File refs are baked at parse time.** Changes to referenced files require re-parsing the parent flow.
10. **Use bare flow names, not file paths.** `flow: generic-council` not `flow: ../generic-council/FLOW.yaml`. Bare names are portable and resolve via project discovery.
11. **Use step-level `when` for branching.** Each branch declares its own activation condition. Merge branches with `any_of` inputs.
12. **`flow:` can't combine with other executors.** Mutually exclusive with `run:`, `executor:`, and `for_each:`.
13. **`prompt` and `prompt_file` are mutually exclusive.** Never set both on the same step.
14. **Directory flow `run:` paths resolve relative to the flow directory**, not the current working directory.
15. **Optional inputs use dict syntax.** `score: {from: "review.score", optional: true}` — not `score: review.score?` or similar shorthand.
16. **Exit rules with `advance` actions fail on no-match.** When you define explicit `advance` rules, the step fails if none match. Add a catch-all `advance` rule or handle all cases.
17. **`_session_id` is auto-emitted.** Don't declare it in `outputs:` — it's automatically added by agent steps with `continue_session: true`.
18. **Post-loop steps need `when` gates.** A step with `sequencing: [looping-step]` fires after the first iteration, not after the loop exits. Add a `when` condition to gate on the loop's exit state.

## Config Variables

Declare configurable variables in a top-level `config:` block. These map 1:1 to `$job.*` inputs — the engine resolves them identically.

```yaml
config:
  persona:
    description: "Your AI persona description"
    type: str           # str, text, number, bool, choice
    required: true      # inferred true when no default provided
    example: "You are a senior researcher..."
  api_key:
    description: "Service API key"
    type: str
    sensitive: true     # masks value in output, suggests env var
  max_rounds:
    description: "Maximum iteration count"
    type: number
    default: 5          # has default → required: false
  voice_style:
    type: choice
    options: [conversational, formal, casual]
    default: conversational
```

**Types:** Uses the same set as output field schemas — `str`, `text`, `number`, `bool`, `choice`. Choice type requires an `options` list.

**Sensitive variables:** `sensitive: true` marks a config var as containing secrets. Effects:
- `stepwise info` masks default values and shows `env: STEPWISE_VAR_{NAME}` hint
- Missing-input errors suggest env var instead of `--var` flag
- `load_flow_config` resolves `STEPWISE_VAR_{NAME}` environment variables automatically

**User config values:** Users provide their values in a co-located `config.local.yaml` file (gitignored, auto-loaded):

- Directory flows (`my-flow/FLOW.yaml`): `my-flow/config.local.yaml`
- Single-file flows (`my-flow.flow.yaml`): `my-flow.config.local.yaml` sibling

**Resolution priority** (highest wins): `--var` → `--vars-file` → `config.local.yaml` → `STEPWISE_VAR_{NAME}` env vars → config defaults.

**CLI commands:**
- `stepwise config init <flow>` — scaffold a `config.local.yaml` from declarations
- `stepwise info <flow>` — show config variables, requirements, readme, and readiness status
- `stepwise validate <flow>` — warns on mismatches between config and `$job.*` bindings
- `stepwise preflight <flow>` — combined pre-run check: config resolution + requirements + model resolution

## Requirements

Declare external tool dependencies in a top-level `requires:` block:

```yaml
requires:
  - name: ffmpeg
    description: "Audio/video processing"
    check: "ffmpeg -version"      # shell command, validated by stepwise validate
    install: "apt install ffmpeg" # shown when check fails
    url: "https://ffmpeg.org"     # docs link shown when check fails
  - name: camofox
    description: "Browser automation"
    check: "docker ps | grep camofox"
    install: "pip install camofox"
  - jq                            # shorthand: just a name, no check
```

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Tool or capability name |
| `description` | string | no | What this requirement is for |
| `check` | string | no | Shell command to verify availability (5s timeout) |
| `install` | string | no | Install command shown when check fails |
| `url` | string | no | Documentation link shown when check fails |

- `check` commands run during `stepwise validate`, `stepwise info`, and `stepwise preflight` (5-second timeout)
- Requirements are advisory — they don't block `stepwise run`
- When a check fails, `install` and `url` are shown to guide the user
- Included in `stepwise schema` and `stepwise agent-help` output

## Validation Checklist

Before outputting a flow, verify:

1. Input bindings: `source_step` is valid step name or `$job`; `source_field` exists in source step's `outputs`
2. Every `loop` exit rule has a valid `target` step name
3. No structural cycles in the DAG (loops use exit rules, not graph edges). Cycles via optional inputs are allowed.
4. At least one entry step (no deps) and one terminal step (nothing depends on it)
5. Every step has at least one declared output
6. Prompt `$variables` match input `local_name` values exactly
7. All loops have a safety cap (`attempt >= N` at medium priority)
8. For-each: source produces a list; sub-flow accesses item via `$job.<as>`
9. Branching: `when` conditions reference input variable names; `any_of` has >= 2 sources with valid `step.field` refs
10. All sub-flows (flow steps, for-each) produce all declared parent `outputs`
11. Post-loop steps: any step with `sequencing` on a looping step has a `when` condition to gate execution

After generating, validate with `stepwise validate <flow>`.
