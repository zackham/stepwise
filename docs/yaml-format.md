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
      revision_number: $step.attempt
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
    attempt_num: $step.attempt      # current attempt number (magic binding)

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
