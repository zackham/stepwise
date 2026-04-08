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

**Kit:** A directory containing `KIT.yaml` and multiple flow subdirectories. Kits group related flows into a single shareable package. See [KIT.yaml Format](#kityaml-format) below.

```
swdev/
  KIT.yaml                 # kit manifest
  plan/
    FLOW.yaml
  implement/
    FLOW.yaml
    scripts/build.sh
```

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
author: alice                # optional, auto from git config
version: "1.0"               # optional
forked_from: "@bob:original" # optional, provenance for forked flows
visibility: interactive      # optional: interactive | background | internal
inputs:                      # optional, per-run parameters
  var_name:
    description: "..."
    type: str                # str, text, number, bool, choice
config:                      # optional, set-and-forget settings
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
readme: |                    # optional, long-form description
  Multi-line documentation...
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
  executor: llm                    # LLM executor
  model: anthropic/claude-sonnet-4 # required for LLM steps
  prompt: "Score this: $content"
  system: "You are a scorer."      # optional system prompt
  temperature: 0.2                 # optional
  max_tokens: 1024                 # optional
  # OR
  executor: agent                  # agent executor
  prompt: "Implement: $spec"
  working_dir: $project_path       # optional
  output_mode: effect              # optional: effect | stream_result | file
  output_path: .stepwise/out.json  # required when output_mode is file
  emit_flow: true                  # optional, agent can emit sub-flows
  agent: claude                    # optional: claude | codex | gemini
  # OR
  executor: poll                   # poll executor
  check_command: "gh pr checks..." # required for poll steps
  interval_seconds: 30             # optional (default: 60)
  prompt: "Waiting for CI..."      # optional, shown in UI
  # OR
  executor: mock_llm               # mock LLM for testing

  # Outputs (required)
  outputs: [field1, field2]        # declared output field names

  # Typed output fields (optional, for external steps)
  output_fields:
    field1:
      type: choice                 # str, text, number, bool, choice
      options: [a, b, c]
      description: "Pick one"
      required: true               # default: true
      default: a                   # optional
      min: 0                       # number type only
      max: 100                     # number type only

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
      max_iterations: 5            # optional loop bound

  # Limits (optional) — cost/time/iteration guards
  limits:
    max_cost_usd: 5.00
    max_duration_minutes: 30
    max_iterations: 50

  # Named sessions (optional, agent and LLM steps)
  session: impl                    # named session — matching names share a conversation
  loop_prompt: "Fix: $errors"      # alternate prompt on attempt > 1
  max_continuous_attempts: 5       # force fresh session after N iterations
  fork_from: plan                  # fork from named STEP's completion tail (requires agent: claude; target step must declare session:)

  # Caching (optional)
  cache: true                      # enable with default TTL
  # OR
  cache:
    ttl: 30m                       # custom TTL (duration: Ns, Nm, Nh, Nd)
    key_extra: v2                  # bump to invalidate

  # Error handling (optional)
  on_error: continue               # "fail" (default) | "continue"
  idempotency: idempotent          # idempotent | retriable_with_guard | non_retriable

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
| Nested step output | `step_name.field.path` | `score: review.metrics.avg` |
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

**Cycle detection:** A cycle in the dependency graph is valid if every cycle contains at least one `optional: true` or `any_of` edge AND the cycle is closed by an enclosing loop exit rule (`action: loop` or `action: escalate` with a `target:`). The parser marks such edges as *loop-back bindings* (`is_back_edge=True`) and excludes them from the forward cycle check. Unguarded back-edges (a plain binding forming a cycle with no optional/any_of fallback) are rejected at parse time with `rule_id: loop_back_binding_ambiguous_closure`.

**Loop-back runtime semantics (§11):**
- On the first iteration of the enclosing loop frame, the loop-back binding resolves to *absent* (presence = False). Optional bindings deliver `None`; `any_of` falls through to the first non-back-edge source.
- On iter-N > 1, the binding resolves normally, carrying the producer's most recent output from the previous iteration.
- Nested loops get independent `LoopFrame` stacks — child frames reset to iteration 0 each time their parent bumps, so inner loop presence starts absent on every outer iteration.

**Data model:** `InputBinding("x", "step", "field", optional=True)`. A binding marked as a back-edge additionally carries `is_back_edge=True` and `closing_loop_id=<frame_id>` (the loop target step name).

#### `any_of` Inputs

Take from whichever branch completed. Used for conditional branching merge points.

```yaml
inputs:
  result:
    any_of:
      - quick-path.result
      - deep-path.result
```

- Must have >= 2 source entries
- Each entry uses `step.field` syntax
- Resolves from the first available completed source (in list order)
- Can be combined with `optional: true`

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
- String/regex: `regex_extract(pattern, text, default)`
- Literals: `True`, `False`, `None`, `true`, `false`, `null`
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

**Prohibited:** Lambda expressions, f-strings, and attribute access starting with `_` (blocks `__class__`, `__bases__`, etc.).

### Exit Actions

| Action | Behavior |
|--------|----------|
| `advance` | Normal progression to downstream steps |
| `loop` | Create new attempt for `target` step (requires `target:` field) |
| `escalate` | Pause the job for human inspection |
| `abandon` | Fail the job |

If no exit rules are defined, the step implicitly advances. When exit rules exist but none match: if the step has explicit `advance` rules, the step **fails** (prevents unhandled output cases from silently progressing); if the step has only loop/escalate/abandon rules, unmatched = implicit advance.

**Boomerang steps:** Steps with no `advance` exit rules (only loop + escalate/abandon) are excluded from terminal step detection. They are treated as loop machinery, not workflow outputs.

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
  model: anthropic/claude-sonnet-4
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
- `STEPWISE_ATTEMPT` — current attempt number
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
    - type: fallback
      config:
        fallback_ref:
          type: llm
          config: { prompt: "Quick analysis", model: fast }
```

Decorators wrap the executor. Applied in order (first decorator is outermost).

| Decorator | Config | What it does |
|-----------|--------|-------------|
| `timeout` | `seconds` | Kills the executor after N seconds |
| `retry` | `max_retries`, `backoff` | Re-runs the executor up to N times on failure |
| `fallback` | `fallback_ref` | Falls back to alternate executor on failure |

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
    when: "some_condition == true"         # optional activation condition

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
    model: anthropic/claude-sonnet-4
    prompt: "Classify this issue as trivial, standard, or complex"
    outputs: [category, summary]

  quick-fix:
    executor: llm
    model: anthropic/claude-sonnet-4
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
- Returns false on NameError/AttributeError/TypeError (missing input treated as condition-not-met)

**Presence predicates (`is_present:` / `is_null:`):**

On **loop-back bindings** (an optional or `any_of` input whose source is closed by an enclosing loop exit rule), the `when` condition can route on presence rather than value. A loop-back binding is *absent* on the first pass of its enclosing loop and *present* on iter-N > 1:

```yaml
analyze:
  when:
    input: prev_note
    is_present: false    # only run when the loop-back binding is absent (iter-1 init)
  inputs:
    prev_note:
      from: critique.note
      optional: true
  outputs: [text]

refine:
  when:
    input: prev_note
    is_present: true     # only run when the loop-back binding has resolved (iter-N > 1)
  inputs:
    prev_note:
      from: critique.note
      optional: true
  outputs: [text]
```

Truth table (§11.3):

| Binding state              | `is_present: true` | `is_present: false` | `is_null: true` | `is_null: false` |
|----------------------------|-------------------|---------------------|-----------------|------------------|
| Absent (frame not active)  | false             | **true**            | true            | false            |
| Present with non-null value| **true**           | false               | false           | **true**          |
| Present with `None` value  | **true**           | false               | **true**         | false            |

- `is_present` is only meaningful on loop-back bindings. Using it on a regular input is rejected at parse time.
- For `any_of` loop-back bindings, all sources must share the same closing loop frame (§11.4) — otherwise the parser rejects the `is_present` use.

**Settlement:**
- When nothing is in motion and nothing is ready, the job is settled
- Steps that never ran get SKIPPED runs
- Job completes if at least one terminal has a current completed run; fails otherwise

### Named Sessions

Agent and LLM steps can share conversations using **named sessions**. Steps with the same `session: <name>` reuse the same agent session, continuing the conversation instead of starting fresh.

```yaml
implement:
  executor: agent
  session: impl
  prompt: "Implement: $spec"
  loop_prompt: "Tests failed:\n$failures\nFix the issues."
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
| `session` | string | --- | Named session. Steps with the same name share a conversation |
| `loop_prompt` | string | --- | Alternate prompt template on attempt > 1 (falls back to `prompt`) |
| `max_continuous_attempts` | int | --- | After N iterations, force a fresh session |
| `fork_from` | string | --- | Fork an independent session from the completion tail of a named **step** (not a session name — see note below) |

**Behavior:**
- First run: creates session, sends `prompt`
- Loop-back (attempt > 1): continues session, sends `loop_prompt` (or `prompt` if not set)
- If session crashes or `max_continuous_attempts` exceeded, falls back to a fresh session

**Cross-step session sharing:** Steps with the same `session` name share a conversation — no special input bindings needed:

```yaml
steps:
  plan:
    executor: agent
    agent: claude
    session: main
    prompt: "Plan: $spec"
    inputs: { spec: $job.spec }
    outputs: [plan]

  implement:
    executor: agent
    agent: claude
    session: main
    after: [plan]
    prompt: "Implement the plan."
    inputs:
      plan: plan.plan
    outputs: [result]
    # continues plan's session — same session name
```

**Forking sessions:** Use `fork_from: <step_name>` to create an independent session that starts with a **specific step's completion tail** and diverges from that point:

```yaml
  review:
    executor: agent
    agent: claude
    session: review_session    # fresh session name — forks MUST declare their own
    fork_from: plan            # STEP name — the snapshot anchor (not a session name)
    after: [plan]              # fork target must be in after: chain
    prompt: "Review the plan critically."
    outputs: [feedback]
```

`fork_from` rules (enforced by the parse-time validator):
- `fork_from` references a **step name**, not a session name. The referenced step must declare its own `session:` (you cannot fork from an ephemeral one-shot agent step).
- The forking step must declare its own fresh `session:` — forks always start a new session.
- Both the forking step AND every writer of the parent session must have explicit `agent: claude` in their executor config.
- The forking step must have the fork target in its `after:` chain (or reach it via input bindings).
- All chain roots on the same forked session must fork from steps writing to the **same** parent session (single-chain rule, §8.1 of the coordination model).
- `max_attempts > 1` and `cache:` are prohibited on session-writers and fork-source steps. Ephemeral one-shot agent steps (no `session:`, no `fork_from:`) may retry freely.

The engine snapshots the fork target's session state at its completion tail, under a dedicated exclusive lock (`fcntl.flock`), before any downstream writer can mutate the live session. This guarantees forks see the parent's completion-tail state, not a racy live-UUID tail. See `data/reports/2026-04-07-stepwise-coordination-and-validation-model.md` §9 and §13 (in the vita repo) for the full coordination model.

**Conditional fork rejoin** (§8.3 of the coordination model) is permitted when multiple chain roots on the same forked session have pairwise-mutex `when:` clauses (e.g., a routing step produces a tag, and two alternative chain roots are gated on `tag == "a"` vs `tag == "b"`). The parse-time validator allows this; the coordination validator (`stepwise validate`) verifies the mutex proof.

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

### Derived Outputs (Computed Fields)

Use `derived_outputs` to compute fields deterministically from a step's executor output. The engine evaluates Python expressions against the artifact dict after the executor returns, and merges results back into the artifact as new output fields.

```yaml
score:
  executor: llm
  model: anthropic/claude-sonnet-4
  prompt: |
    Score this plan on 8 dimensions (1-5 each).
    Respond with ONLY: {"scores": {"completeness": 4, "grounding": 3, ...}}
  outputs: [scores]
  derived_outputs:
    average: "sum(scores.values()) / len(scores)"
    passed: "sum(scores.values()) / len(scores) >= 4.0"
    lowest_three: "sorted(scores, key=scores.get)[:3]"
```

**Expression environment:** Expressions run in a restricted namespace with the artifact fields as local variables, plus Python builtins (`sum`, `len`, `sorted`, `min`, `max`, `float`, `int`, `str`, `list`, `dict`, `set`, `tuple`, `round`, `abs`, `any`, `all`, `enumerate`, `zip`, `map`, `filter`, `range`, `True`, `False`, `None`) and `regex_extract(pattern, text, default)`. No imports, no file access.

**Evaluation order:** Derived outputs are evaluated after the executor returns but before exit rules. This means exit rules can reference derived fields.

## Input Variables

Declare per-run parameters in a top-level `inputs:` block. These are values that change every time the flow runs — shown in the run dialog, passed via `--input` on the CLI.

```yaml
inputs:
  topic:
    description: "Subject to research"
    type: str
    required: true
    example: "quantum computing advances"
  depth:
    description: "How deep to go"
    type: choice
    options: [shallow, moderate, deep]
    default: moderate
```

Input variables use the same field schema as config variables (see table below). Both map to `$job.*` input bindings at runtime. The distinction is about authoring intent and UI presentation:

- **`inputs:`** = per-run parameters. Shown in the run dialog. Change every job.
- **`config:`** = set-and-forget settings. Shown in the settings panel. Saved to `config.local.yaml`.

## Config Variables

Declare set-and-forget settings in a top-level `config:` block. These are values configured once and reused across runs — API keys, model choices, persona prompts.

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

### Variable Field Schema

Both `inputs:` and `config:` variables share the same field schema:

| Field | Type | Default | Description |
|---|---|---|---|
| `description` | string | `""` | Human-readable description |
| `type` | string | `"str"` | `str`, `text`, `number`, `bool`, `choice` |
| `default` | any | `None` | Default value (has default -> `required: false`) |
| `required` | bool | `true` | Inferred from default presence |
| `example` | string | `""` | Example value shown in `stepwise info` |
| `options` | list | `None` | Required for `choice` type |
| `sensitive` | bool | `false` | Masks value in output, suggests env var |

**Resolution priority** (highest wins): `--input` > inputs (run dialog) > `config.local.yaml` > `STEPWISE_VAR_{NAME}` env vars > config/input defaults.

**Sensitive variables:** When `sensitive: true`, the value is masked in `stepwise info` output, missing-input errors suggest `STEPWISE_VAR_{NAME}` instead of `--input`, and the env var is auto-resolved by `load_flow_config`.

**`config.local.yaml`** only stores `config:` values, not `inputs:` values. Input values are transient — passed at run time via `--input` or the run dialog.

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

## KIT.yaml Format

A `KIT.yaml` file defines a kit — a collection of related flows that are shared and installed together.

```yaml
name: swdev                                  # required — kebab-case identifier
description: Software development flows      # required — what this kit provides
author: zack                                 # optional — auto from git config
category: development                        # optional — broad grouping
tags: [agent, code, planning]                # optional — for registry search
usage: |                                     # optional — usage instructions
  stepwise run swdev/plan --input spec="..."
include:                                     # optional — registry flows to auto-fetch
  - @alice:code-review
  - @bob:test-runner
defaults:                                    # optional — default values for bundled flows
  project_path: .
  model: anthropic/claude-sonnet-4
```

### KIT.yaml Field Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | **yes** | Kit name, used as slug for registry. Must match directory name. |
| `description` | string | **yes** | Short description of what the kit provides |
| `author` | string | no | Author name (auto-populated from git config if absent) |
| `category` | string | no | Broad category for grouping (e.g., `development`, `data`, `ops`) |
| `tags` | list[string] | no | Tags for registry search |
| `usage` | string | no | Usage instructions shown on registry page |
| `include` | list[string] | no | Registry flow references auto-fetched on `stepwise get` |
| `defaults` | dict | no | Default input values available to all bundled flows |

### Directory Structure

Each subdirectory of the kit that contains a `FLOW.yaml` is a bundled flow. Subdirectories without `FLOW.yaml` are ignored. Co-located files (scripts, prompts) within each flow subdirectory are included when sharing.

```
my-kit/
  KIT.yaml               # kit manifest
  flow-a/
    FLOW.yaml             # flow-a definition
    helper.py             # co-located file, bundled with flow-a
  flow-b/
    FLOW.yaml             # flow-b definition
  docs/                   # ignored — no FLOW.yaml
    readme.md
```

Kit flows are referenced locally as `kit/flow` (e.g., `stepwise run my-kit/flow-a`) and from the registry as `@author:kit/flow`.

---

## How It Maps to the Data Model

| YAML | Data Model |
|------|-----------|
| `run: scripts/foo.py` | `ExecutorRef("script", {"command": "python3 scripts/foo.py"})` |
| `executor: external` + `prompt:` | `ExecutorRef("external", {"prompt": "..."})` |
| `executor: agent` + `prompt:` | `ExecutorRef("agent", {"prompt": "...", ...})` |
| `executor: llm` + `prompt:` | `ExecutorRef("llm", {"prompt": "...", "model": "..."})` |
| `executor: poll` + `check_command:` | `ExecutorRef("poll", {"check_command": "..."})` |
| `executor: mock_llm` | `ExecutorRef("mock_llm", {})` |
| `inputs: {x: step.field}` | `InputBinding("x", "step", "field")` |
| `inputs: {x: $job.field}` | `InputBinding("x", "$job", "field")` |
| `inputs: {x: {from: "a.f", optional: true}}` | `InputBinding("x", "a", "f", optional=True)` |
| `inputs: {x: {any_of: [a.f, b.f]}}` | `InputBinding("x", "", "", any_of_sources=[("a","f"),("b","f")])` |
| `exits: [{when: "...", action: "loop", target: "s"}]` | `ExitRule("name", "expression", {"condition": "...", "action": "loop", "target": "s"})` |
| `after: [a, b]` | `StepDefinition.after = ["a", "b"]` |
| `outputs: [x, y]` | `StepDefinition.outputs = ["x", "y"]` |
| `when: "expr"` | `StepDefinition.when = "expr"` |
| `for_each: step.field` + `flow:` | `ForEachSpec(source_step, source_field)` + `StepDefinition.sub_flow` |
| `as: var_name` | `ForEachSpec.item_var` |
| `on_error: continue` | `ForEachSpec.on_error` |
| `prompt_file: path/to/file` | Resolved at parse time -> `ExecutorRef.config["prompt"]` |
| `session: impl` | `StepDefinition.session = "impl"` |
| `fork_from: main` | `StepDefinition.fork_from = "main"` |
| `loop_prompt: "..."` | `StepDefinition.loop_prompt = "..."` |
| `max_continuous_attempts: 5` | `StepDefinition.max_continuous_attempts = 5` |
| `config: {var: {...}}` | `WorkflowDefinition.config_vars = [ConfigVar(...)]` |
| `requires: [{name: "..."}]` | `WorkflowDefinition.requires = [FlowRequirement(...)]` |
| `derived_outputs: {f: "expr"}` | `StepDefinition.derived_outputs = {"f": "expr"}` |
| `cache: true` | `StepDefinition.cache = CacheConfig(enabled=True)` |
| `limits: {max_cost_usd: 5}` | `StepDefinition.limits = StepLimits(max_cost_usd=5.0)` |
