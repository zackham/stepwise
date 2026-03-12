# Stepwise YAML Workflow Format

## Overview

YAML is the authoring format for Stepwise workflows. It maps to the internal `WorkflowDefinition` data model. The YAML is parsed once at job creation time — the engine never sees YAML at runtime.

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
    executor: human
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

    sequencing: [review]
```

## Format Reference

### Top Level

```yaml
name: workflow-name          # required, identifier
description: "..."           # optional, human-readable
chains:                      # optional, context chain definitions (M7a)
  chain_name: { ... }
steps:                       # required, map of step definitions
  step_name: { ... }
```

### Step Definition

```yaml
step_name:
  # Executor (one of these is required)
  run: scripts/foo.py              # script executor — runs this file
  # OR
  executor: human                  # human executor
  prompt: "What should we do?"     # prompt shown in UI
  # OR
  executor: mock_llm               # mock LLM for testing

  # Outputs (required)
  outputs: [field1, field2]        # declared output field names

  # Inputs (optional) — data flow from other steps
  inputs:
    local_name: source_step.source_field
    job_data: $job.field_name       # from job-level inputs

  # Sequencing (optional) — wait for steps without taking data
  sequencing: [step_a, step_b]

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

If no exit rules are defined, or none match, the step implicitly advances.

### Human Steps

```yaml
approve:
  executor: human
  prompt: "Approve this deployment?"
  outputs: [approved, reason]
  inputs:
    artifact: build.artifact
    version: build.version
```

Human steps immediately suspend with a watch. The UI shows the prompt and a "Fulfill Watch" button. The user provides the declared outputs as JSON.

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
          executor: human
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
- `on_error: continue` — failures are recorded as `{"_error": "..."}` in results; remaining items continue
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

### Context Chains

Context chains give agent steps session continuity across a workflow. Prior chain members' conversations are compiled into an XML context block and prepended to the agent's prompt. This lets multi-step agent workflows build on prior reasoning without sharing mutable state.

```yaml
name: iterative-research
chains:
  research:
    max_tokens: 80000       # token budget for prior context (default: 80000)
    overflow: drop_oldest    # "drop_oldest" (default) or "drop_middle"
    include_thinking: false  # include thinking blocks (default: false)
    accumulation: full       # "full" (all attempts) or "latest" (most recent only)

steps:
  gather:
    executor: agent
    prompt: "Research $topic thoroughly"
    outputs: [findings]
    chain: research
    chain_label: Research Phase
    inputs:
      topic: $job.topic

  analyze:
    executor: agent
    prompt: "Analyze the findings and identify key patterns"
    outputs: [analysis]
    chain: research
    chain_label: Analysis Phase
    inputs:
      findings: gather.findings

  synthesize:
    executor: agent
    prompt: "Synthesize the analysis into a final report"
    outputs: [report]
    chain: research
    chain_label: Synthesis Phase
    inputs:
      analysis: analyze.analysis
```

**Key concepts:**
- `chains:` defines named chain configurations at the workflow level
- `chain: name` on a step assigns it to a chain
- `chain_label: "..."` provides a human-readable label in the context XML (defaults to step name)
- Steps in a chain receive prior members' conversations as `<prior_context>` XML prepended to their prompt
- Chains require at least 2 members
- The first step in a chain gets no prior context (nothing before it)
- Overflow drops whole transcripts, never truncates mid-conversation

**Overflow strategies:**
- `drop_oldest`: Remove oldest transcripts first until under budget
- `drop_middle`: Keep first and last transcripts, remove from the middle

**Accumulation modes:**
- `full`: Include all completed attempts for each prior step (useful with loops)
- `latest`: Only include the most recent completed attempt per step

## How It Maps to the Data Model

| YAML | Data Model |
|------|-----------|
| `run: scripts/foo.py` | `ExecutorRef("script", {"command": "python3 scripts/foo.py"})` |
| `executor: human` + `prompt:` | `ExecutorRef("human", {"prompt": "..."})` |
| `executor: mock_llm` | `ExecutorRef("mock_llm", {})` |
| `inputs: {x: step.field}` | `InputBinding("x", "step", "field")` |
| `inputs: {x: $job.field}` | `InputBinding("x", "$job", "field")` |
| `exits: [{when: "...", action: "loop", target: "s"}]` | `ExitRule("name", "expression", {"condition": "...", "action": "loop", "target": "s"})` |
| `sequencing: [a, b]` | `StepDefinition.sequencing = ["a", "b"]` |
| `outputs: [x, y]` | `StepDefinition.outputs = ["x", "y"]` |
| `for_each: step.field` + `flow:` | `ForEachSpec(source_step, source_field)` + `StepDefinition.sub_flow` |
| `as: var_name` | `ForEachSpec.item_var` |
| `on_error: continue` | `ForEachSpec.on_error` |
| `chains: {name: {...}}` | `WorkflowDefinition.chains = {name: ChainConfig(...)}` |
| `chain: chain_name` | `StepDefinition.chain = "chain_name"` |
| `chain_label: "Label"` | `StepDefinition.chain_label = "Label"` |
