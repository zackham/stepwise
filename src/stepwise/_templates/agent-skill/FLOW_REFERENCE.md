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

    idempotency: idempotent          # idempotent (default) | allow_restart | non_retriable

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

Inline scripts work too: `run: |` with a heredoc. Environment: `JOB_ENGINE_INPUTS` (path to input JSON), `JOB_ENGINE_WORKSPACE` (workspace dir), `STEPWISE_FLOW_DIR` (flow directory path, if directory flow).

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

### llm

Single LLM call via OpenRouter with structured output extraction.

```yaml
summarize:
  executor: llm
  prompt: "Summarize: $text"        # $var placeholders from inputs
  # OR
  prompt_file: prompts/summarize.md # alternative — loads file at parse time (mutually exclusive with prompt)
  model: balanced                   # tier alias or full model ID (fast/balanced/strong)
  system: "You are concise."        # optional system message
  temperature: 0.3                  # optional, default 0.0
  max_tokens: 1024                  # optional, default 4096
  outputs: [summary]
  inputs:
    text: fetch.content
```

Output extraction: (1) tool call response, (2) JSON in content, (3) raw text if single output field.

### agent

Long-running AI agent session via ACP. Has tool access, reads/writes workspace files.

```yaml
implement:
  executor: agent
  prompt: "Implement: $plan\nRun tests before finishing."
  # OR: prompt_file: prompts/implement.md   (mutually exclusive with prompt, resolved at parse time)
  outputs: [result]
  inputs:
    plan: planning.result
  limits:
    max_cost_usd: 2.00
    max_duration_minutes: 60
  idempotency: allow_restart        # recommended for agent steps
```

**Output modes** (set in config):
- `effect` (default) — workspace IS the output, artifact = `{"status": "completed"}`
- `stream_result` — captures agent's text response as `{"result": "..."}`
- `file` — reads JSON from `output_path`

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
        flow: flows/complex-pipeline.yaml    # file ref (loaded at parse time)
      default:                               # no when: — always matches last
        flow: flows/standard.yaml
    outputs: [result]                        # required — every sub-flow must produce these
```

- **Three flow sources:** inline `{steps: ...}`, file path (`flows/x.yaml`), registry ref (`@author:name`)
- All resolved at parse time — no network/file access at runtime
- Route `when:` expressions use input bindings + `attempt` + safe builtins
- Expression errors fail the step immediately (no fallthrough)
- No match + no default = step failure
- Route steps cannot also be for_each steps

## Prompt Templating

`$variable` or `${variable}` — uses Python `string.Template.safe_substitute`.

- Input bindings create template variables matching `local_name`
- Agent executor auto-injects `$objective` and `$workspace`
- Missing variables render as literal `$name` (no error) — double-check spelling
- Literal `$` → escape as `$$`

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

After generating, validate with `stepwise validate <flow>`.
