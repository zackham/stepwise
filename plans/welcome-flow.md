# Welcome Flow: Interactive Product Tour

## File

`flows/welcome/FLOW.yaml` — single file, no supporting files needed.

## What This Is

A stepwise flow that serves as both the first-time user experience AND a visual demo of stepwise's capabilities. When run with `stepwise run flows/welcome/FLOW.yaml --watch`, it teaches the user what stepwise does by *doing it*. The medium is the message.

The target user just installed stepwise and has never seen it before. They're a developer (likely a Claude Code / AI agent user). They opened the web UI and are watching the DAG.

## Success Criteria

Every criterion must be met. If any fails, iterate until it passes.

### 1. No LLM, no config, no API keys

Only `script` and `human` executor types. A fresh `curl install | sh` must be able to run this flow with zero additional setup. No OpenRouter key, no environment variables, no config files.

### 2. Parallel execution is visually obvious

At least 3 steps must execute simultaneously. This is THE visual moment in `--watch` mode — multiple DAG nodes lighting up at once. The parallel steps should be in the middle of the flow (not at the start or end) so the user sees the sequential → parallel → sequential transition.

### 3. Human-in-the-loop steps gather real input

At least 2 `executor: human` steps. The first should gather info early (name, what they want to use stepwise for). A later one should present the assembled result. Human prompts should be concise and friendly — not walls of text.

Use the typed output schema for human steps where appropriate:

```yaml
outputs:
  name:
    type: str
    description: "Your first name"
  use_case:
    type: choice
    options: [agent-tools, ci-cd, team-workflows, exploration]
    description: "What interests you most about stepwise?"
```

### 4. Data flows visibly between steps

The edge labels in the DAG (which now show actual values after a step completes) should tell a readable story. Outputs should be short, meaningful strings or small values — NOT giant JSON blobs. Think: `name: "Zack"`, `tip: "Flows persist in SQLite..."`, `welcome_msg: "Welcome Zack! Here's..."`.

Design artifact values to be readable at a glance on the DAG edge labels.

### 5. The DAG shape is visually impressive

NOT a straight line. Should have a clear fan-out/fan-in diamond shape or similar. Something like:

```
    [gather-info]
     /    |    \
  [tip1] [tip2] [tip3]     ← parallel
     \    |    /
    [assemble]
       |
   [welcome]
```

The exact shape can differ but it must branch and reconverge. This is what makes stepwise visually distinct from a bash script.

### 6. Personalized final output

The terminal step's output should reference the user's name and chosen use case. It should feel custom, not generic. The assembled message should include specific next-step suggestions based on what they said they want to do.

### 7. Each step educates

Step `description:` fields (shown in the web UI on each node) should teach stepwise concepts:
- "Gathering your info — this is a human-in-the-loop step"
- "These three steps run in parallel — watch the DAG"
- "Assembling your personalized guide from upstream data"

Keep descriptions SHORT (one line). They appear on the DAG node.

### 8. Completes fast

Script steps should finish in under 1 second each. No `sleep`, no downloads, no network calls. Just echo/printf JSON. Total flow time (excluding human waits) under 5 seconds.

### 9. Clean YAML

- Step names: kebab-case
- Output field names: underscore_case
- Every step has `description:`
- Script steps use `run:` shorthand
- Human steps use `executor: human` with `prompt:` and typed `outputs:`
- Inputs use proper binding syntax: `source_step.field` or `$job.param`
- All step `outputs:` lists match the JSON keys produced by `run:` commands

### 10. It actually runs

The flow must parse without errors: `uv run python -c "from stepwise.yaml_loader import load_workflow_yaml; load_workflow_yaml('flows/welcome/FLOW.yaml')"` must succeed.

Script steps must produce valid JSON on stdout with keys matching their `outputs:` list.

## How to Verify

After each iteration:

1. **Parse check**: `uv run python -c "from stepwise.yaml_loader import load_workflow_yaml; wf = load_workflow_yaml('flows/welcome/FLOW.yaml'); print(f'OK: {len(wf.steps)} steps'); [print(f'  {n}: {s.executor.type} -> {s.outputs}') for n,s in wf.steps.items()]"`

2. **Visual review**: Read through the YAML. Is the DAG shape right? Are the descriptions educational? Are the script outputs designed to look good on edge labels?

3. **Script step test**: For each `run:` step, mentally (or actually) execute the shell command and verify it produces valid JSON with the right keys.

4. **Human step review**: Read each human prompt. Is it concise? Friendly? Does it teach something? Does the output schema make sense?

5. **Data flow trace**: Trace the data path from gather-info through parallel steps to final assembly. Does each step's input binding correctly reference an upstream output? Does the assembled output use all gathered data?

## Anti-Patterns to Avoid

- **No trivial echo steps** — `echo '{"msg": "hello"}'` is boring. Each script step should compute something contextual based on its inputs.
- **No walls of text** in human prompts — 2-4 lines max. The UI renders these in a form panel.
- **No LLM steps** — the whole point is zero-config.
- **No more than 8-9 steps total** — it should feel tight, not tedious.
- **Don't forget the `description:` field** on every step — these are the DAG node subtitles.
- **Don't produce giant artifact values** — edge labels truncate at ~14 chars. Design values to be readable when truncated AND when viewed in full via hover.
- **Script commands must be POSIX-compatible** — no bashisms. Use `printf` over `echo -e`. Works on macOS and Linux.

## Technical Reference

### Human step format
```yaml
step-name:
  executor: human
  description: "Short node subtitle"
  prompt: |
    Multi-line prompt shown in the UI form.
    Can reference inputs with $var_name.
  outputs:
    field_name:
      type: str|text|number|bool|choice
      description: "Help text"
      options: [a, b, c]  # for choice type
      required: true
  inputs:
    local_var: upstream-step.field
```

### Script step format
```yaml
step-name:
  description: "Short node subtitle"
  run: |
    printf '{"field1": "value1", "field2": "value2"}'
  outputs: [field1, field2]
  inputs:
    local_var: upstream-step.field
```

Input variables are available as environment variables: `$local_var`.

### Input binding
- `upstream-step.field_name` — from another step's output
- `$job.param_name` — from job-level inputs (not applicable here since we gather via human steps)
