# Stepwise Patterns

Idioms for building effective multi-agent flows. Complements [flow-reference.md](flow-reference.md) (YAML syntax), [executors.md](executors.md) (executor details), and [concepts.md](concepts.md) (core concepts).

**Prerequisites:** This document assumes familiarity with the core YAML syntax from flow-reference.md. Key concepts used throughout:

- **`$variable`** in prompts resolves from the step's `inputs:` bindings
- **`$job.variable`** references job-level inputs (from `--input` or `config:` block)
- **`attempt`** is a built-in counter: how many times this step has executed in the current job (1-indexed)
- **`outputs`** in exit rule expressions is a DotDict of the step's produced artifact
- **Script steps** (`run:`) print JSON to stdout; the engine parses it and maps keys to declared `outputs`

---

## 1. File-Based Context Passing

**Problem:** Agent steps degrade when large upstream outputs (plans, analyses, codebase scans) are inlined into their prompt via `$variable`. The agent loads everything into its context window, causing attention dilution on long inputs.

**Pattern:** Upstream steps write output to a file and pass the path. Downstream agents use their native tools (Read, Grep) to access only the sections they need.

### Using `output_mode: file`

The agent writes structured data to a known path. The engine reads that file and maps its JSON keys to outputs.

```yaml
steps:
  analyze:
    executor: agent
    working_dir: $project_path
    output_mode: file
    output_path: .stepwise/analysis.json
    prompt: |
      Analyze the codebase architecture. Write your findings as structured JSON
      to .stepwise/analysis.json with keys: overview, modules, dependencies,
      patterns, risks.
    outputs: [overview, modules, dependencies, patterns, risks]
    inputs:
      project_path: $job.project_path

  implement:
    executor: agent
    working_dir: $project_path
    prompt: |
      Implement the changes described in the analysis.

      The analysis is at: .stepwise/analysis.json
      Read only the sections relevant to your current task — don't load the
      entire file. Start with overview, then read per-module as you work.
    outputs: [result]
    inputs:
      project_path: $job.project_path
    after: [analyze]
```

### Using path-in-output (lighter variant)

When `output_mode: file` is too rigid, the agent writes to a file and outputs the path. This is what the `plan-and-build` flow does — the plan agent saves to `$plan_report_path`, and the implement agent reads selectively.

```yaml
steps:
  ingest:
    run: |
      python3 -c "
      import json; print(json.dumps({
        'project_path': '/home/user/myrepo',
        'plan_path': '/home/user/myrepo/.stepwise/plan.md'
      }))
      "
    outputs: [project_path, plan_path]

  plan:
    executor: agent
    working_dir: $project_path
    output_mode: stream_result
    prompt: |
      Create a detailed implementation plan. Save the plan to: $plan_path
      Your final response must be the complete plan text.
    outputs: [result]
    inputs:
      project_path: ingest.project_path
      plan_path: ingest.plan_path

  implement:
    executor: agent
    working_dir: $project_path
    prompt: |
      Implement the approved plan.
      **Plan location:** $plan_path
      Read sections selectively as you work through each step.
    inputs:
      project_path: ingest.project_path
      plan_path: ingest.plan_path
    after: [plan]
    outputs: [result]
```

### Prompting for selective reads

When downstream agents receive file paths, tell them **how** to read selectively. Use structured formats upstream so agents can navigate:

```yaml
# Upstream: instruct structured output
prompt: |
  Write the analysis with these exact markdown headers:
  ## Overview
  ## Architecture
  ## Dependencies
  ## Risks
  Save to .stepwise/analysis.md

# Downstream: instruct selective reading
prompt: |
  ## Available context (read on demand, not all at once)
  - Plan: $plan_path
  - Codebase analysis: .stepwise/analysis.md
  - Test results: .stepwise/test-output.log

  1. Read the plan's Overview section first
  2. For each step, read only that step's details
  3. Check analysis only for relevant patterns
  4. Reference test results only when fixing failures
```

The downstream agent can `grep -n "^## " .stepwise/analysis.md` to find sections and read only what it needs.

### Cross-codebase paths must be absolute

When using `working_dir` to run agent steps in a different codebase, the path must be absolute or use `~` expansion. Relative paths resolve against the flow's project directory, not the target codebase.

```yaml
# Correct — absolute path
working_dir: /home/user/other-repo

# Correct — passed as job input (caller provides absolute path)
working_dir: $project_path
# stepwise run flow.yaml --input project_path=/home/user/other-repo

# Correct — ~ expansion
working_dir: ~/other-repo

# WRONG — resolves relative to the flow's .stepwise/ project, not the target
working_dir: ../other-repo
```

This applies to `output_path` and any file paths in agent prompts that reference the target codebase. Always use absolute paths or pass them as job inputs.

**When to use file-based passing:**
- Prior step output exceeds ~4K tokens
- Downstream agent only needs specific sections
- Output is structured (JSON, markdown with headers) and navigable
- Multiple downstream steps consume the same output

**When inline `$variable` is fine:**
- Short outputs (scores, decisions, status flags, file paths, booleans)
- Downstream step genuinely needs the complete text (e.g., LLM scoring a full plan)

---

## 2. Dynamic Fan-Out with emit_flow

**Problem:** Flow authors can't always predict the decomposition shape at authoring time. A planner might discover 3 independent modules or 12, each needing separate implementation.

**Pattern:** An agent with `emit_flow: true` analyzes the problem, then writes a `.stepwise/emit.flow.yaml` file. The engine detects it after the step completes, validates it, and launches it as a sub-job. Sub-flow outputs propagate back to the parent step.

```yaml
steps:
  plan:
    executor: agent
    working_dir: $project_path
    config:
      emit_flow: true
    prompt: |
      Analyze the codebase and create an implementation plan for: $spec

      If the work decomposes into independent modules, emit a sub-flow
      that fans out across them. Write the plan to .stepwise/plan.json first,
      then emit .stepwise/emit.flow.yaml to parallelize implementation.
    inputs:
      project_path: $job.project_path
      spec: $job.spec
    outputs: [result]
```

The agent writes `.stepwise/plan.json`, then emits a sub-flow:

```yaml
# .stepwise/emit.flow.yaml (written by the agent at runtime)
name: parallel-implementation
steps:
  setup:
    run: |
      python3 -c "
      import json
      plan = json.load(open('.stepwise/plan.json'))
      print(json.dumps({'modules': plan['modules']}))
      "
    outputs: [modules]

  implement:
    for_each: setup.modules
    as: module
    on_error: continue
    outputs: [results]
    flow:
      steps:
        build:
          executor: agent
          prompt: |
            Implement this module: $module
            Full plan at .stepwise/plan.json — read only your module's section.
          inputs:
            module: $job.module
          outputs: [result]

  integrate:
    executor: agent
    prompt: |
      All modules implemented. Results: $results
      Run integration tests and verify everything works together.
    inputs:
      results: implement.results
    outputs: [result]
```

**Key points:**
- The agent decides the parallelism at runtime — not the flow author at authoring time
- Each sub-job gets its own isolated context
- `on_error: continue` lets other modules finish even if one fails
- The integration step sees all results as an array
- Engine auto-detects `.stepwise/emit.flow.yaml` — no special output needed

**When to use:**
- Work naturally decomposes into independent units
- The number of units is data-dependent (varies per invocation)
- Units can run in parallel without interfering

**When not to use:**
- Decomposition is always the same — use static `for_each` in YAML
- Units have sequential dependencies — use regular step ordering
- The agent needs to explore first — let it explore in a separate step, then emit

---

## 3. Script Steps for Data Transformation

**Problem:** Using LLM or agent steps for mechanical data transformations (parsing, filtering, formatting) wastes tokens and introduces non-determinism.

**Pattern:** Use script steps for deterministic operations. Reserve LLM/agent steps for judgment.

```yaml
steps:
  analyze:
    executor: agent
    output_mode: file
    output_path: .stepwise/findings.json
    prompt: "Analyze the codebase and report findings as JSON"
    outputs: [overview, risks, modules]

  prepare:
    run: |
      python3 << 'PYEOF'
      import json
      findings = json.load(open('.stepwise/findings.json'))
      high_risk = [f for f in findings.get('risks', []) if f.get('severity') == 'high']
      print(json.dumps({
          "risk_summary": f"{len(high_risk)} high-risk items",
          "module_list": [m['name'] for m in findings.get('modules', [])],
          "needs_review": len(high_risk) > 0
      }))
      PYEOF
    outputs: [risk_summary, module_list, needs_review]
    after: [analyze]

  review:
    executor: llm
    prompt: "Risk assessment: $risk_summary. Modules: $module_list. Recommend action."
    inputs:
      risk_summary: prepare.risk_summary
      module_list: prepare.module_list
      needs_review: prepare.needs_review
    outputs: [recommendation]
    when: "needs_review == true or needs_review == True"
```

Note: `after: [analyze]` on the `prepare` step creates ordering without data transfer — the script reads the file directly rather than receiving output through inputs. Use `after` when a step depends on a prior step's **side effects** (files written) but doesn't consume its output fields.

**When to use scripts:**
- Parsing, filtering, or reformatting data between steps
- Conditionals that don't need judgment (file existence checks, threshold comparisons)
- Extracting specific fields from large outputs for downstream consumption

For simple computations (aggregations, thresholds, sorting), prefer `derived_outputs` over a script step — it's inline, no extra step, and the computed fields become real outputs:

```yaml
score:
  executor: llm
  outputs: [scores]
  derived_outputs:
    average: "sum(scores.values()) / len(scores)"
    passed: "average >= 4.0"
```

See [YAML Format — Derived Outputs](yaml-format.md#derived-outputs-computed-fields) for full details.

---

## 4. Escalation Boundaries

**Problem:** Agent steps may encounter decisions outside their scope — architectural choices, product tradeoffs, ambiguous requirements. Without explicit boundaries, agents guess silently.

**Pattern:** Define escalation markers in agent prompts. Exit rules detect them and suspend the job for external input. After the human responds, the agent resumes with the answer via `continue_session`. This is exactly how the `plan-and-build` flow handles both planning and implementation questions.

```yaml
steps:
  implement:
    executor: agent
    continue_session: true
    output_mode: stream_result
    prompt: |
      Implement the feature according to the plan.

      If you encounter a decision the plan doesn't cover — architectural
      choices, tradeoffs between approaches, ambiguous requirements —
      stop and output exactly:
      >>>ESCALATE: [describe the decision and your options]
      A human will answer. Do NOT guess.
    outputs: [result]
    inputs:
      escalation_answer:
        from: implement_escalate.answer
        optional: true
    exits:
      - name: needs_input
        when: "'>>>ESCALATE:' in str(outputs.get('result', ''))[-500:]"
        action: escalate
        target: implement_escalate
      - name: done
        when: "True"
        action: advance

  implement_escalate:
    executor: external
    prompt: |
      The implementation agent hit a decision point:
      $question

      Respond with your guidance. The agent will continue with your direction.
    inputs:
      question: implement.result
    outputs: [answer]
```

**How the cycle works:**
1. `implement` runs. If it hits an ambiguous decision, it outputs `>>>ESCALATE: ...`
2. Exit rule matches → `action: escalate` suspends the job at `implement_escalate`
3. Human provides answer via web UI or terminal
4. Engine resumes: `implement` re-runs with `escalation_answer` now resolved
5. `continue_session: true` preserves the agent's full history — it picks up where it left off, now with the guidance in context

**Key details:**
- `continue_session: true` — agent retains full context across the escalation round-trip
- `optional: true` on `escalation_answer` — first run proceeds without blocking on the external step
- `[-500:]` slice — avoids scanning the entire output for the marker
- `action: escalate` suspends the job (not `action: loop`) — the external step needs the job paused to collect input

**When to use:**
- Agent is operating in someone else's codebase
- Spec has known ambiguities
- Changes are hard to reverse (database migrations, API contracts)
- You want a human checkpoint before expensive operations

---

## 5. Progressive Refinement Loops

**Problem:** Quality-gated loops where an agent refines work based on feedback can stall if the agent loses context of what it tried before, or repeat the same mistakes each iteration.

**Pattern:** Use `continue_session: true` with `loop_prompt` to maintain conversational context. The agent sees its prior work and receives targeted critique.

```yaml
steps:
  draft:
    executor: agent
    continue_session: true
    output_mode: stream_result
    prompt: |
      Write a technical spec for: $topic
      Save to .stepwise/spec.md
      Your final response must be the complete spec text.
    loop_prompt: |
      Your spec scored below threshold.
      Scores: $scores
      Weakest areas: $lowest
      Critique: $critique

      Focus on improving the weakest dimensions. Update .stepwise/spec.md.
      Your final response must be the updated spec text.
    outputs: [result]
    inputs:
      topic: $job.topic
      scores:
        from: evaluate.scores
        optional: true
      lowest:
        from: evaluate.lowest_three
        optional: true
      critique:
        from: evaluate.critique
        optional: true

  evaluate:
    executor: llm
    prompt: |
      Score this spec on 5 dimensions (1-5 each): $spec_text
      Return JSON: {"scores": {...}, "average": N, "lowest_three": [...], "critique": "..."}
    inputs:
      spec_text: draft.result
    outputs: [scores, average, lowest_three, critique]
    exits:
      - name: good
        when: "float(str(outputs.get('average', '0')).strip()) >= 4.0"
        action: advance
      - name: cap
        when: "attempt >= 5"
        action: escalate
      - name: refine
        when: "True"
        action: loop
        target: draft
```

**Why `continue_session` matters:** Without it, each loop iteration starts a fresh agent session. The agent re-reads the codebase, re-discovers patterns, and may repeat mistakes. With it, the agent builds on prior work and receives targeted feedback.

**`max_continuous_attempts`:** For long loops, set this to force a fresh session periodically. After N continuous attempts, the engine starts a new session, preventing context window exhaustion.

---

## 6. Flow Composition

**Problem:** Complex workflows become monolithic. Similar patterns (evaluation, review, research) repeat across flows.

**Pattern:** Extract reusable sub-flows and compose them via `flow:` steps. Sub-flows receive parent step's resolved inputs as their `$job.*` variables.

```yaml
# flows/evaluate-quality/FLOW.yaml — reusable evaluation sub-flow
name: evaluate-quality
description: "Reusable quality evaluation with scoring rubric"
config:
  content:
    type: text
    required: true
  rubric:
    type: text
    required: false
    default: "Score 1-5 on clarity, completeness, accuracy"
steps:
  score:
    executor: llm
    prompt: |
      Evaluate this content against the rubric.
      Content: $content
      Rubric: $rubric
      Return JSON with scores, average, and critique.
    inputs:
      content: $job.content    # ← resolves from parent's inputs mapping
      rubric: $job.rubric
    outputs: [scores, average, critique]
```

```yaml
# flows/my-pipeline/FLOW.yaml — parent flow composing the sub-flow
steps:
  generate:
    executor: agent
    prompt: "Write a report on $topic"
    inputs:
      topic: $job.topic
    outputs: [report]

  evaluate:
    flow: evaluate-quality              # bare name — preferred over file paths
    inputs:
      content: generate.report          # → becomes $job.content in sub-flow
      rubric: "Score on depth, accuracy, actionability, writing quality"
    outputs: [scores, average, critique]
```

**How the mapping works:** When the parent step's `inputs:` are resolved, they become the sub-flow's `$job.*` namespace. So `content: generate.report` in the parent means `$job.content` in the sub-flow resolves to the report text.

**Guidelines:**
- Use bare flow names (`flow: evaluate-quality`), not file paths — portable across machines
- Keep sub-flows focused: one responsibility, clear inputs/outputs
- `for_each` supports sub-flows for fan-out over composed flows

---

## 7. Error Recovery with Decorators

**Problem:** Agent steps can fail from transient errors (API timeouts, malformed output, tool failures). Without retry logic, a single failure kills the entire flow.

**Pattern:** Use decorators for automatic retry, and fallback steps for graceful degradation.

```yaml
steps:
  research:
    executor: agent
    prompt: "Research $topic and summarize findings"
    outputs: [findings]
    inputs:
      topic: $job.topic
    idempotency: idempotent
    decorators:
      - type: retry
        config:
          max_retries: 2
          backoff: exponential
      - type: timeout
        config:
          minutes: 15

  research_fallback:
    executor: llm
    prompt: "Summarize what you know about: $topic"
    inputs:
      topic: $job.topic
    outputs: [findings]
    when: "false"    # only runs as fallback — see decorator below
```

For steps where failure should route to a simpler alternative:

```yaml
  expensive_analysis:
    executor: agent
    prompt: "Deep analysis of $data"
    outputs: [result]
    inputs:
      data: fetch.data
    decorators:
      - type: timeout
        config: { minutes: 30 }
      - type: fallback
        config:
          fallback_ref:
            type: llm
            config:
              prompt: "Quick analysis of: $data"
              model: fast
```

**When to use:**
- Agent steps calling external tools that may fail transiently
- Expensive steps that should have a cheaper fallback
- Steps where partial results are better than job failure

See [flow-reference.md § Decorators](flow-reference.md#decorators) for full decorator syntax.

---

## 8. Plan-Light to Implement

**Problem:** Complex tasks need a planning phase that determines the implementation shape. Static flows can't adapt — the number and nature of implementation jobs emerges from planning. `for_each` requires the list shape to be known at author time. `emit_flow` keeps decomposition inside a single job. Sometimes you want a human to review the decomposition before execution begins.

**Pattern:** Run a planning job, then stage implementation jobs that reference the plan's outputs via data wiring. Review the staged batch, then release.

```bash
# 1. Run the planning flow — blocks until complete
stepwise run plan-task --wait --input spec="Build auth system"
# Returns job-plan-abc with outputs: {plan, tasks}

# 2. Stage implementation jobs referencing the plan
stepwise job create implement.flow.yaml \
  --input spec="Implement auth middleware" \
  --input plan=job-plan-abc.plan \
  --group auth-batch

stepwise job create implement.flow.yaml \
  --input spec="Implement auth tests" \
  --input plan=job-plan-abc.plan \
  --group auth-batch

# 3. Add ordering between implementation jobs if needed
stepwise job dep job-tests-456 --after job-middleware-123

# 4. Review what's staged
stepwise job show --group auth-batch

# 5. Release the batch
stepwise job run --group auth-batch
# Engine cascades execution based on dependency graph
```

**Key mechanics:**
- `--input plan=job-plan-abc.plan` wires data from the planning job's output and auto-creates a dependency
- `--group auth-batch` organizes related jobs for batch review and release
- `job run --group` atomically transitions all staged jobs to PENDING
- The engine auto-starts jobs as their dependencies complete

**When to use vs alternatives:**

| Situation | Approach |
|-----------|----------|
| Decomposition known at author time | Static `for_each` in YAML |
| Agent decides decomposition at runtime | `emit_flow: true` (dynamic, single job) |
| Human reviews decomposition before execution | **Plan-light to implement** (staged, multi-job) |

The plan-light pattern is most valuable when the cost of execution is high (agent steps that take minutes, deploy steps that are hard to reverse) and you want a human checkpoint between planning and doing.

See [Concepts: Job Staging](concepts.md#job-staging) for the full mental model and [CLI: Job Staging](cli.md#job-staging-commands) for command reference.

---

## Summary: When to Use What

| Situation | Pattern |
|-----------|---------|
| Prior step output > 4K tokens | **File-based context** — write to file, pass path (§1) |
| Downstream agent only needs parts of upstream output | **Selective reading** — pass path, instruct agent to read on demand (§1) |
| Decomposition shape unknown until runtime | **Dynamic fan-out** — `emit_flow: true` with for_each (§2) |
| Decomposition shape always the same | Static `for_each` in YAML ([flow-reference.md](flow-reference.md#for-each-fan-outfan-in)) |
| Mechanical data transformation between steps | **Script step** — deterministic, no LLM needed (§3) |
| Step depends on prior step's files, not its outputs | `after: [step]` — ordering without data transfer (§3) |
| Agent may hit decisions outside its scope | **Escalation boundary** — `>>>ESCALATE:` + human step + loop (§4) |
| Quality-gated iteration loop | **Progressive refinement** — `continue_session` + `loop_prompt` (§5) |
| Same pattern used in multiple flows | **Flow composition** — extract sub-flow, reference via `flow:` (§6) |
| Agent step may fail transiently | **Retry decorator** — `max_retries` + `backoff` (§7) |
| Expensive step needs a cheaper fallback | **Fallback decorator** — simpler executor as backup (§7) |
| Planning determines implementation shape | **Plan-light to implement** — stage jobs referencing plan outputs (§8) |
| Short output (scores, booleans, paths) | Inline `$variable` substitution — simple and direct |
