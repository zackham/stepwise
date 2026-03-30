# Stepwise YAML Workflow Format

## Overview

YAML is the authoring format for Stepwise workflows. It maps to the internal `WorkflowDefinition` data model. The YAML is parsed once at job creation time — the engine never sees YAML at runtime.

## Flow Formats

Flows can be authored as a single file or as a directory:

**Single file:** `my-flow.flow.yaml` — everything in one YAML file.

**Directory flow:** A directory containing `FLOW.yaml` as the entry point, with co-located scripts, prompts, and data files alongside it.

```
my-flow/
  FLOW.yaml              # flow definition (required)
  analyze.py             # script referenced via run: analyze.py
  prompts/
    system.md            # prompt loaded via prompt_file: prompts/system.md
```

Both formats work identically everywhere in the CLI and engine. Use `stepwise new <name>` to scaffold a directory flow.

## Minimal Example

```yaml
name: hello-world

steps:
  greet:
    run: scripts/greet.py
    outputs: [message]
```

## Complete Example: Iterative Review

```yaml
name: iterative-review
description: Draft content, review iteratively, publish when approved

steps:
  research:
    run: scripts/research.py
    outputs: [notes, sources]
    inputs:
      topic: $job.topic

  draft:
    run: scripts/draft.py
    outputs: [content, word_count]
    inputs:
      notes: research.notes
      prior_feedback: review.feedback

  review:
    executor: external
    prompt: "Review this draft and provide a decision (approve/revise) with feedback"
    outputs: [decision, feedback, score]
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

      - name: max_revisions
        when: "attempt >= 5"
        action: escalate

  publish:
    run: scripts/publish.py
    outputs: [url]
    inputs:
      content: draft.content

    after: [review]
```

## Format Reference

### Top Level

```yaml
name: workflow-name          # required, identifier
description: "..."           # optional, human-readable
config:                      # optional, declared config variables
  var_name:
    description: "..."
    type: str                # str, text, number, bool, choice
    default: "..."           # has default → required: false
    sensitive: true          # masks value in output, suggests env var
requires:                    # optional, external tool dependencies
  - name: tool_name
    description: "..."
    check: "command"         # shell command to verify availability
    install: "command"       # shown when check fails
    url: "https://..."       # docs link shown when check fails
steps:                       # required, map of step definitions
  step_name: { ... }
```

### Step Definition

```yaml
step_name:
  # Executor (one of these is required)
  run: scripts/foo.py              # script executor — runs this file
  # OR
  executor: external               # external executor
  prompt: "What should we do?"     # prompt shown in UI
  prompt_file: prompts/review.md   # alternative to prompt — loads file at parse time (mutually exclusive)
  # OR
  executor: mock_llm               # mock LLM for testing

  # Outputs (required)
  outputs: [field1, field2]        # declared output field names

  # Inputs (optional) — data flow from other steps
  inputs:
    local_name: source_step.source_field
    job_data: $job.field_name       # from job-level inputs

  # After (optional) — wait for steps without taking data
  after: [step_a, step_b]

  # Activation condition (optional) — gate on resolved inputs
  when: "expression"               # Python expression against input names

  # Derived Outputs (optional) — compute fields from executor output
  derived_outputs:
    field_name: "python expression"  # evaluated against artifact dict

  # Exit Rules (optional) — evaluated after step completion
  exits:
    - name: rule_name
      when: "expression"           # Python expression (eval with restricted namespace)
      action: advance              # advance | loop | escalate | abandon
      target: step_name            # required for loop action

  # Decorators (optional)
  decorators:
    - type: timeout
      config: { seconds: 300 }
    - type: retry
      config: { max_retries: 2 }
```

### Input Binding Syntax

Inputs are declared as `local_name: source` where source is:

| Source | Syntax | Example |
|--------|--------|---------|
| Step output | `step_name.field_name` | `notes: research.notes` |
| Job input | `$job.field_name` | `topic: $job.topic` |

The local_name is what the executor receives. It decouples the executor from the graph topology.

#### Optional Inputs

Optional inputs are weak-reference bindings that resolve to `None` when the source dep has no current completed run. They allow steps to proceed without waiting, enabling loop-back data feeding and first-run defaults.

```yaml
inputs:
  topic: $job.topic                    # required — step waits for this
  score:
    from: review.score
    optional: true                     # resolves to None if review hasn't completed
```

**Syntax:** Dict with `from` (same format as regular source) + `optional: true`.

**None handling:**
- In prompt templates: `None` renders as empty string `""`
- In expression evaluation (exit rules, `when`): `None` is first-class. Test with `score is None`
- In script executors: environment variable is unset (not "None" or "null")
- `any_of` + `optional`: allowed — "try these sources, but if none available, proceed with `None`"

**Cycle detection:** A cycle in the dependency graph is valid if every cycle contains at least one `optional: true` edge.

**Data model:** `InputBinding("x", "step", "field", optional=True)`

### Exit Rule Expressions

Exit rules use Python expressions evaluated with `eval()` in a restricted namespace:

**Available variables:**
- `outputs` — the step's output artifact dict (e.g., `outputs.score`, `outputs.decision`)
- `attempt` — current attempt number (1-indexed)
- `max_attempts` — max_iterations if configured, else None

**Available builtins:**
- Comparison: `==`, `!=`, `<`, `>`, `<=`, `>=`
- Logic: `and`, `or`, `not`
- Functions: `any()`, `all()`, `len()`, `min()`, `max()`, `sum()`, `abs()`, `round()`, `sorted()`, `int()`, `float()`, `str()`, `bool()`
- Access: `in`, attribute access, indexing

**Examples:**
```yaml
when: "outputs.score >= 0.8"
when: "outputs.decision == 'approve'"
when: "attempt >= 5"
when: "any(s < 0.5 for s in outputs.scores)"
when: "sorted(outputs.scores)[1] > 0.7"  # second lowest score
when: "len(outputs.errors) == 0"
```

**Guidance:** Keep expressions simple. Push complex evaluation logic into the step's script, which sets output fields the exit rule reads. If an expression is more than ~80 characters, that's a smell — the step should compute a simpler summary field.

### Exit Actions

| Action | Behavior |
|--------|----------|
| `advance` | Normal progression to downstream steps |
| `loop` | Create new attempt for `target` step (requires `target:` field) |
| `escalate` | Pause the job for human inspection |
| `abandon` | Fail the job |

If no exit rules are defined, the step implicitly advances. When exit rules exist but none match: if the step has explicit `advance` rules, the step **fails** (prevents unhandled output cases from silently progressing); if the step has only loop/escalate/abandon rules, unmatched = implicit advance.

### External Steps

```yaml
approve:
  executor: external
  prompt: "Approve this deployment?"
  outputs: [approved, reason]
  inputs:
    artifact: build.artifact
    version: build.version
```

External steps immediately suspend with a watch. The UI shows the prompt and a "Fulfill Watch" button. The user provides the declared outputs as JSON.

### `prompt_file`

An alternative to inline `prompt:` — loads the file content at parse time. Useful for long prompts or prompts shared across flows.

```yaml
summarize:
  executor: llm
  prompt_file: prompts/summarize.md
  outputs: [summary]
  inputs:
    text: fetch.content
```

- Mutually exclusive with `prompt:` — specifying both is a parse error.
- Path is resolved relative to the flow file's directory (relevant for directory flows).
- File content replaces `prompt_file:` at parse time — the engine only sees `prompt:`.

### Script Path Resolution (Directory Flows)

For directory flows, `run:` paths resolve relative to the flow directory. A flow at `my-flow/FLOW.yaml` with `run: analyze.py` resolves to `my-flow/analyze.py`.

Scripts always execute with cwd set to the job workspace directory (not the flow directory). The following environment variables are set:

- `STEPWISE_PROJECT_DIR` — absolute path to the project root
- `STEPWISE_FLOW_DIR` — absolute path to the flow directory
- `PYTHONPATH` — project root is prepended, so scripts can import project modules directly
- All step inputs are passed as `STEPWISE_INPUT_<name>` env vars (strings, or JSON-encoded for dicts/lists). Inputs named `LD_PRELOAD`, `LD_LIBRARY_PATH`, `PYTHONPATH`, `PATH`, or `HOME` are rejected.

For single-file flows, `run:` paths resolve relative to cwd as before.

### Decorators

```yaml
build:
  run: scripts/build.sh
  outputs: [artifact]
  decorators:
    - type: timeout
      config: { seconds: 600 }
    - type: retry
      config: { max_retries: 2 }
```

Decorators wrap the executor. Applied in order (first decorator is outermost).

### For-Each Steps

For-each steps iterate over a list produced by an upstream step, running an embedded sub-flow for each item. Results are collected as an ordered array.

```yaml
steps:
  generate_sections:
    run: scripts/design.py
    outputs: [sections]          # must produce a list

  process_sections:
    for_each: generate_sections.sections   # "step_name.field" — must be a list
    as: section                            # variable name for current item (default: "item")
    on_error: continue                     # "fail_fast" (default) | "continue"
    outputs: [results]                     # defaults to [results] if omitted

    flow:
      steps:
        write:
          run: scripts/write.py
          outputs: [content]
          inputs:
            section: $job.section          # access current item via $job.<as_variable>

        review:
          executor: external
          prompt: "Review this section"
          outputs: [approved]
          inputs:
            content: write.content
```

**Key concepts:**
- `for_each: step.field` — the source list to iterate over (supports nested fields like `step.design.sections`)
- `as: variable_name` — names the iteration variable (default: `item`). Accessed in sub-flow steps via `$job.<variable_name>`
- `flow:` — an embedded workflow definition with its own `steps:` block. Each iteration runs this sub-flow as an independent sub-job
- `on_error: fail_fast` — first failure cancels remaining items and fails the step (default)
- `on_error: continue` — failures are recorded as `{"_error": "..."}` in results; remaining items continue. If **all** items fail, the for-each step itself fails
- Results are collected in source list order. The output artifact is `{"results": [...]}`
- Empty source lists complete immediately with `{"results": []}`
- Parent-level `inputs:` are passed through to every sub-job alongside the iteration variable

**Downstream access:**
```yaml
  summarize:
    run: scripts/summarize.py
    outputs: [summary]
    inputs:
      all_results: process_sections.results   # array of sub-flow terminal outputs
```

### Conditional Branching

Branch workflows using step-level `when` conditions and merge with `any_of` inputs.

```yaml
steps:
  triage:
    executor: llm
    prompt: "Classify this issue as trivial, standard, or complex"
    outputs: [category, summary]

  quick-fix:
    executor: llm
    prompt: "Quick fix for: $summary"
    inputs: { summary: triage.summary, category: triage.category }
    outputs: [result]
    when: "category == 'trivial'"

  deep-analysis:
    executor: agent
    prompt: "Deep analysis of: $summary"
    inputs: { summary: triage.summary, category: triage.category }
    outputs: [result]
    when: "category == 'complex'"

  report:
    run: scripts/report.sh
    inputs:
      result:
        any_of:
          - quick-fix.result
          - deep-analysis.result
    outputs: [final]
```

**Step-level `when`:**
- Evaluated against resolved inputs after all deps are satisfied
- If `when` is false, the step stays not-ready (never launches)
- At job settlement, never-started steps get SKIPPED runs for bookkeeping
- Expression namespace: input names directly available (e.g., `category == 'trivial'`)

**`any_of` inputs:**
- Resolves from the first available completed source (in list order)
- Must have >= 2 source entries
- Each entry uses `step.field` syntax

**Settlement:**
- When nothing is in motion and nothing is ready, the job is settled
- Steps that never ran get SKIPPED runs
- Job completes if at least one terminal has a current completed run; fails otherwise

### Session Continuity

Agent and LLM steps with `continue_session: true` reuse the same agent session across loop iterations, continuing the conversation instead of starting fresh.

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
| `loop_prompt` | string | — | Alternate prompt template on attempt > 1 (falls back to `prompt`) |
| `max_continuous_attempts` | int | — | After N iterations, force a fresh session |

**Behavior:**
- First run: creates session, sends `prompt`
- Loop-back (attempt > 1): continues session, sends `loop_prompt` (or `prompt` if not set)
- If session crashes or `max_continuous_attempts` exceeded, falls back to a fresh session

**Cross-step session sharing:** Agent steps with `continue_session: true` auto-emit `_session_id`. Downstream steps continue the same conversation via optional input:

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
    prompt: "Implement the plan."
    continue_session: true
    inputs:
      plan: plan.plan
      _session_id:
        from: plan._session_id
        optional: true
    outputs: [result]
```

`_session_id` is a reserved output field — don't declare it in `outputs:`. The engine serializes concurrent access to shared sessions.

**Data model:**
- `StepDefinition.continue_session = True`
- `StepDefinition.loop_prompt = "..."`
- `StepDefinition.max_continuous_attempts = 5`

### Agent Output Modes

Agent steps support three output modes, configured via `output_mode`:

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

**`output_mode: file` requires explicit prompt instructions.** The engine reads `output_path` after the agent finishes and parses it as JSON. The agent will not automatically write this file — your prompt must explicitly tell the agent to write JSON to the `output_path` location, and the JSON keys must match the step's declared `outputs`. If the file is missing or doesn't contain valid JSON, the step fails.

## Common Patterns

### Gating a post-loop step

Steps run as soon as their inputs or `after` deps are satisfied — they don't wait for a loop to finish. This means a step ordered after a looping step will fire after the **first iteration**, not after the loop exits.

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
    after: [review]                    # BUG: runs after review's first completion
    outputs: [url]
```

Here `publish` runs as soon as `review` completes once — even if review loops back to `draft`. The fix is to add a `when` condition that gates on the loop's exit state:

**Fix (gated with `when`):**

```yaml
  publish:
    run: './publish.sh "$content"'
    inputs:
      content: draft.content
      score: review.score
    when: "float(score) >= 0.8"       # only runs when the loop exits via "good"
    outputs: [url]
```

**General principle:** Any step downstream of a loop needs an explicit `when` condition to ensure it only runs after the loop terminates with the desired outcome. The engine has no concept of "loop finished" — it only knows "step completed." `stepwise validate` will warn about this pattern.

### Derived Outputs (computed fields)

Use `derived_outputs` to compute fields deterministically from a step's executor output. The engine evaluates Python expressions against the artifact dict after the executor returns, and merges results back into the artifact as new output fields.

This is especially useful when an LLM returns raw data (scores, classifications) and you need derived values (averages, booleans, sorted lists) that downstream steps can reference — without trusting the LLM to do arithmetic.

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

The LLM returns only `scores`. The engine computes `average`, `passed`, and `lowest_three` deterministically. All three become real step outputs that downstream steps can reference:

```yaml
output:
  inputs:
    passed: score.passed
  when: "passed == True"

refine:
  inputs:
    passed: score.passed
    lowest_three: score.lowest_three
  when: "passed == False"
```

**Expression environment:** Expressions run in a restricted namespace with the artifact fields as local variables, plus Python builtins (`sum`, `len`, `sorted`, `min`, `max`, `float`, `int`, `str`, `list`, `dict`, `set`, `tuple`, `round`, `abs`, `any`, `all`, `enumerate`, `zip`, `map`, `filter`, `range`, `True`, `False`, `None`). No imports, no file access.

**Evaluation order:** Derived outputs are evaluated after the executor returns but before exit rules. This means exit rules can reference derived fields.

**When to use:**
- Aggregating LLM scores (average, min, max)
- Boolean gates (`passed`, `needs_review`) computed from thresholds
- Extracting/sorting subsets of structured output
- Any computation you don't want to trust to the LLM

## Config Variables

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

| Field | Type | Default | Description |
|---|---|---|---|
| `description` | string | `""` | Human-readable description |
| `type` | string | `"str"` | `str`, `text`, `number`, `bool`, `choice` |
| `default` | any | `None` | Default value (has default → `required: false`) |
| `required` | bool | `true` | Inferred from default presence |
| `example` | string | `""` | Example value shown in `stepwise info` |
| `options` | list | `None` | Required for `choice` type |
| `sensitive` | bool | `false` | Masks value in output, suggests env var |

**Resolution priority** (highest wins): `--input` → `--vars-file` → `config.local.yaml` → `STEPWISE_VAR_{NAME}` env vars → config defaults.

**Sensitive variables:** When `sensitive: true`, the value is masked in `stepwise info` output, missing-input errors suggest `STEPWISE_VAR_{NAME}` instead of `--input`, and the env var is auto-resolved by `load_flow_config`.

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

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Tool or capability name |
| `description` | string | no | What this requirement is for |
| `check` | string | no | Shell command to verify (5s timeout) |
| `install` | string | no | Install command shown when check fails |
| `url` | string | no | Docs link shown when check fails |

Requirements are checked by `stepwise validate`, `stepwise info`, and `stepwise preflight`. They are advisory — they don't block `stepwise run`.

## How It Maps to the Data Model

| YAML | Data Model |
|------|-----------|
| `run: scripts/foo.py` | `ExecutorRef("script", {"command": "python3 scripts/foo.py"})` |
| `executor: external` + `prompt:` | `ExecutorRef("external", {"prompt": "..."})` |
| `executor: mock_llm` | `ExecutorRef("mock_llm", {})` |
| `inputs: {x: step.field}` | `InputBinding("x", "step", "field")` |
| `inputs: {x: $job.field}` | `InputBinding("x", "$job", "field")` |
| `exits: [{when: "...", action: "loop", target: "s"}]` | `ExitRule("name", "expression", {"condition": "...", "action": "loop", "target": "s"})` |
| `after: [a, b]` | `StepDefinition.after = ["a", "b"]` |
| `outputs: [x, y]` | `StepDefinition.outputs = ["x", "y"]` |
| `for_each: step.field` + `flow:` | `ForEachSpec(source_step, source_field)` + `StepDefinition.sub_flow` |
| `as: var_name` | `ForEachSpec.item_var` |
| `on_error: continue` | `ForEachSpec.on_error` |
| `prompt_file: path/to/file` | Resolved at parse time → `ExecutorRef.config["prompt"]` |
| `inputs: {x: {any_of: [a.f, b.f]}}` | `InputBinding("x", "", "", any_of_sources=[("a","f"),("b","f")])` |
| `inputs: {x: {from: "a.f", optional: true}}` | `InputBinding("x", "a", "f", optional=True)` |
| `continue_session: true` | `StepDefinition.continue_session = True` |
| `loop_prompt: "..."` | `StepDefinition.loop_prompt = "..."` |
| `max_continuous_attempts: 5` | `StepDefinition.max_continuous_attempts = 5` |
| `config: {var: {...}}` | `WorkflowDefinition.config_vars = [ConfigVar(...)]` |
| `requires: [{name: "..."}]` | `WorkflowDefinition.requires = [FlowRequirement(...)]` |
