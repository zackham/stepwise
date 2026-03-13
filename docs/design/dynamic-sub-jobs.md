# Dynamic Sub-Job Spawning: Agent-Driven Flow Composition

## Context

Modern agent architectures (Slate's "thread weaving," Cadence's plan→auto pipeline) converge on a pattern: an orchestrating agent decomposes work into bounded units, executes them, and synthesizes results. Stepwise already has the DAG engine, sub-job infrastructure, and heterogeneous executors to support this — but currently all flow composition is declared statically in YAML.

Stepwise previously had an executor-driven dynamic sub-job API (`ExecutorResult(type="sub_job", sub_job_def=...)`) that was removed in commit `a1c442c` in favor of declarative `flow:`/`routes:`/`for_each:` directives. The removal was the right call for the general case — arbitrary executors emitting arbitrary workflows made topology unknowable until runtime. But it threw away a capability that's specifically valuable for **agent executors**, which have the judgment to decompose tasks dynamically.

This document describes two levels of restoring that capability with appropriate constraints.

### Architecture mapping

| Slate concept | Cadence concept | Stepwise equivalent |
|---|---|---|
| Orchestrator thread | `cadence auto` | Agent executor |
| Worker thread | Bead execution | Sub-job (steps with executors) |
| Episode | Bead result / closed status | HandoffEnvelope (terminal step artifact) |
| Episode as input | Bead dependency | InputBinding / parent_run.inputs |
| Parallel thread dispatch | Graph-aware concurrent workers | DAG-driven step readiness |
| Overdecomposition guard | N/A (flat graph) | `max_sub_job_depth` (default 5) |

### What Stepwise adds

Sub-jobs can contain heterogeneous executors — `human` steps (agent spawns work requiring review), `script` steps (run tests, build artifacts), `llm` steps (API calls), nested `agent` steps. This is strictly more powerful than Slate's LLM-only threads or Cadence's Claude-session-only beads.

Every sub-job gets full persistence (SQLite), event logging, web UI visibility in the job tree, heartbeat monitoring, and stale detection. Nothing is ephemeral.

---

## Level 1: Agent-Emitted Flows

The agent executor plans, then emits a `.flow.yaml` as its output. The engine launches it as a sub-job. The agent's step transitions to DELEGATED and completes when the sub-flow finishes.

This is the "plan then execute" pattern — equivalent to Cadence's `cadence plan` producing a plan that `cadence auto` executes, collapsed into a single agent step.

### User-facing behavior

```yaml
# Example workflow with an agent that can emit flows
steps:
  implement:
    executor: agent
    config:
      model: anthropic/claude-sonnet-4-20250514
      prompt: |
        Implement the user authentication feature.
        Break the work into steps and emit a flow.
      emit_flow: true          # opt-in capability
    inputs:
      spec: $job.spec
    outputs: [result]
```

The agent runs, analyzes the task, and writes a `.flow.yaml` to its workspace. When the agent exits, the executor detects the emitted flow, and the engine launches it as a sub-job. The sub-flow's terminal step outputs become the parent step's outputs.

If the agent decides the task is simple enough to do directly (no flow emitted), it completes normally with `type="data"`.

### Implementation plan

#### 1. Add `"delegate"` result type to ExecutorResult

**File: `src/stepwise/executors.py`**

Add `sub_job_def` field to `ExecutorResult`:

```python
@dataclass
class ExecutorResult:
    type: str  # "data" | "watch" | "async" | "delegate"
    envelope: HandoffEnvelope | None = None
    watch: WatchSpec | None = None
    executor_state: dict | None = None
    sub_job_def: SubJobDefinition | None = None   # NEW — for type="delegate"
```

Import `SubJobDefinition` from models (safe — `models` is upstream of `executors` in the module DAG).

#### 2. Handle `"delegate"` in engine result processing

**File: `src/stepwise/engine.py`**

Add a branch in `_process_launch_result()` (after the existing `"data"`, `"watch"`, `"async"` branches):

```python
elif result.type == "delegate":
    sub_def = result.sub_job_def
    if not sub_def:
        self._fail_run(run, "delegate result missing sub_job_def", job)
        return

    # Validate the emitted workflow
    errors = sub_def.workflow.validate()
    if errors:
        self._fail_run(run, f"Emitted flow validation failed: {'; '.join(errors)}", job)
        return

    # Transition run from RUNNING to DELEGATED
    run.status = StepRunStatus.DELEGATED
    run.executor_state = {
        **(result.executor_state or {}),
        "emitted_flow": True,
    }
    self.store.save_run(run)

    # Create and start the sub-job
    try:
        sub = self._create_sub_job(job, run, sub_def)
        run.sub_job_id = sub.id
        self.store.save_run(run)
        self._emit(events.STEP_DELEGATED, job.id, {
            "step": step_def.name,
            "run_id": run.id,
            "sub_job_id": sub.id,
        })
    except Exception as e:
        self._fail_run(run, f"Failed to create sub-job: {e}", job)
        self._halt_job(job, str(e))
```

The existing `_handle_sub_job_done()` cascade (line 2171) already handles completion of sub-jobs linked via `run.sub_job_id`. No changes needed there — when the sub-job completes, it finds the DELEGATED parent run, copies terminal output, marks COMPLETED, and calls `_process_completion()`.

#### 3. Add emitted flow detection to AgentExecutor

**File: `src/stepwise/agent.py`**

After the agent subprocess completes and before constructing the final `ExecutorResult`, check for an emitted flow file:

```python
EMIT_FLOW_FILENAME = "emit.flow.yaml"
EMIT_FLOW_DIR = ".stepwise"

def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
    # ... existing spawn + wait logic ...

    if agent_status.state == "failed":
        # ... existing failure handling ...
        return ExecutorResult(type="data", ...)

    # Check for emitted flow (only if emit_flow enabled in config)
    if self.config.get("emit_flow"):
        emit_path = os.path.join(
            self.backend.working_dir or context.workspace_path,
            EMIT_FLOW_DIR,
            EMIT_FLOW_FILENAME,
        )
        if os.path.exists(emit_path):
            return self._build_delegate_result(emit_path, state, context)

    # ... existing output extraction ...
    envelope = self._extract_output(state, self.output_mode, agent_status)
    return ExecutorResult(type="data", envelope=envelope, executor_state=state)
```

The `_build_delegate_result()` helper:

```python
def _build_delegate_result(
    self, flow_path: str, state: dict, context: ExecutionContext
) -> ExecutorResult:
    from stepwise.yaml_loader import load_workflow_yaml

    workflow = load_workflow_yaml(flow_path)
    errors = workflow.validate()
    if errors:
        # Return as a failed data result so the engine handles it cleanly
        return ExecutorResult(
            type="data",
            envelope=HandoffEnvelope(
                artifact={},
                sidecar=Sidecar(),
                workspace=context.workspace_path,
                timestamp=_now(),
                executor_meta={"failed": True, "error": f"Invalid emitted flow: {errors}"},
            ),
            executor_state={**state, "failed": True, "error": f"Invalid emitted flow: {errors}"},
        )

    sub_def = SubJobDefinition(
        objective=f"Agent-emitted flow from step {context.step_name}",
        workflow=workflow,
    )
    return ExecutorResult(
        type="delegate",
        sub_job_def=sub_def,
        executor_state=state,
    )
```

#### 4. Teach agents how to emit flows

**File: `src/stepwise/agent_help.py`**

When the agent's config has `emit_flow: true`, append a section to the generated prompt instructions:

```markdown
## Flow Emission

You can delegate complex multi-step work by writing a flow definition to:

    $workspace/.stepwise/emit.flow.yaml

When this file exists at the end of your session, it will be launched as a
sub-workflow. Your current step will wait for the sub-workflow to complete,
and the sub-workflow's final outputs become your step's outputs.

When to emit a flow:
- The task naturally decomposes into multiple sequential or parallel steps
- Different parts of the task need different executors (scripts, LLM calls, human review)
- You want built-in retry, timeout, or fallback behavior on individual steps

When NOT to emit a flow (just do the work directly):
- The task is straightforward and you can complete it in one session
- The task is purely exploratory/research

### Available executor types

| Type | Usage | Notes |
|---|---|---|
| `script` | `run: \|` shorthand | Shell commands, stdout parsed as JSON |
| `llm` | `executor: llm` | LLM API call via OpenRouter |
| `human` | `executor: human` | Suspends for human input via web UI |
| `agent` | `executor: agent` | Spawns another agent session |

### Flow format

```yaml
name: descriptive-name
steps:
  step-one:
    run: |
      echo '{"key": "value"}'
    outputs: [key]

  step-two:
    executor: llm
    config:
      model: anthropic/claude-sonnet-4-20250514
      prompt: "Analyze: $data"
    inputs:
      data: step-one.key
    outputs: [analysis]

  step-three:
    run: |
      cd $workspace && run-tests.sh
    inputs:
      analysis: step-two.analysis
    outputs: [test_results]
```

### Rules

- Step names must be kebab-case
- Output field names must be underscore_case
- Each step's `outputs` list must match the JSON keys produced by that step
- Steps with no `inputs` referencing other steps run first (entry steps)
- Steps run as soon as all their dependencies have completed
- The terminal step's outputs become your parent step's outputs
- You can use `$job.param_name` to reference job-level inputs
- You can use `source-step.field` in inputs to reference upstream step outputs
```

This section is only injected into the agent's prompt when `emit_flow: true` is in the agent's config.

#### 5. Pass emit_flow config through to prompt generation

**File: `src/stepwise/agent.py`**

In `_render_prompt()`, when building the agent's context, check for `emit_flow` in config and include the flow emission instructions. The prompt template variable `$stepwise_help` (generated by `agent_help.py`) should conditionally include the emission section.

Concretely, in `_render_prompt()` (or wherever the agent help content is assembled), pass `emit_flow=self.config.get("emit_flow", False)` to the help generator so it knows whether to include the emission instructions.

#### 6. Validation and guardrails

These are already in place:

- **Depth limit:** `_create_sub_job()` checks `_get_job_depth()` against `max_sub_job_depth` (default 5). No changes needed.
- **Workflow validation:** `WorkflowDefinition.validate()` runs cycle detection, checks input bindings, validates outputs, etc. Called in `_build_delegate_result()`.
- **Artifact validation:** When the sub-job's terminal step completes, `_validate_artifact()` checks that its outputs match the parent step's declared `outputs` list. Already happens in `_process_launch_result()` for the terminal step.

Additional validation to add:

- **The emitted flow must have at least one terminal step** whose outputs are a superset of the parent step's declared `outputs`. Add this check in `_build_delegate_result()`:

```python
terminal = workflow.terminal_steps()
if not terminal:
    # fail: no terminal steps
    ...

if step_outputs:  # parent step's declared outputs
    terminal_def = workflow.steps[terminal[0]]
    missing = [o for o in step_outputs if o not in (terminal_def.outputs or [])]
    if missing:
        # fail: terminal step doesn't produce required outputs
        ...
```

This was the same validation that existed in the removed `_validate_sub_job()` method.

#### 7. Testing strategy

**File: `tests/test_agent_emit_flow.py`** (new file)

Tests using `MockAgentBackend` which already supports programmatic completion:

```python
def test_agent_emits_flow_creates_sub_job(async_engine):
    """Agent writes emit.flow.yaml → engine creates sub-job → outputs propagate."""
    # Register a callable step for the emitted flow's inner steps
    register_step_fn("double", lambda inputs: {"result": inputs["n"] * 2})

    # Create agent executor with emit_flow=true and MockAgentBackend
    # that writes a .flow.yaml file on start, then completes
    ...

def test_agent_no_emit_completes_normally(async_engine):
    """Agent with emit_flow=true but no file written → normal data completion."""
    ...

def test_agent_emits_invalid_flow_fails(async_engine):
    """Agent writes invalid YAML → step fails with validation error."""
    ...

def test_agent_emit_flow_depth_limit(async_engine):
    """Emitted flow that would exceed max_sub_job_depth → fails."""
    ...

def test_agent_emit_flow_output_mismatch(async_engine):
    """Emitted flow terminal outputs don't match parent step outputs → fails."""
    ...
```

#### 8. Web UI considerations

No UI changes needed. The existing job tree view already shows parent→child relationships. An agent step that delegates will appear as DELEGATED with a child job in the tree, identical to how `flow:` steps look today. The agent's NDJSON output stream is still available for viewing in the agent output panel.

#### 9. Summary of file changes

| File | Change |
|---|---|
| `src/stepwise/executors.py` | Add `sub_job_def` field to `ExecutorResult` |
| `src/stepwise/engine.py` | Add `"delegate"` branch in `_process_launch_result()` |
| `src/stepwise/agent.py` | Add emitted flow detection after agent completion, add `_build_delegate_result()` |
| `src/stepwise/agent_help.py` | Add conditional flow emission instructions section |
| `tests/test_agent_emit_flow.py` | New test file |

Estimated scope: ~150 lines of new code, ~50 lines of test code.

---

## Level 2: Mid-Execution Thread Spawning

Level 1 is "plan then execute" — the agent finishes, then the sub-flow runs. Level 2 is "weave while executing" — the agent stays alive, spawns sub-jobs on the fly, sees their results, and uses those to decide what to do next. This is Slate's actual thread weaving model.

### The core idea

While the agent is still running inside `executor.start()`, it can spawn sub-jobs, wait for their results (episodes), and route those results into further sub-jobs. The agent's step stays RUNNING the whole time.

```
Agent runs
  ├→ spawns sub-job A (blocks, waits for result)
  │   └→ gets artifact: {"schema": {...}}
  ├→ spawns sub-job B with A's schema as input (blocks, waits)
  │   └→ gets artifact: {"migration": "...", "tests": "passed"}
  ├→ does some direct work using A and B's results
  └→ returns final synthesized result
```

### The IPC constraint

The agent runs as an `acpx` subprocess. There is no bidirectional IPC channel — stepwise writes a prompt file, spawns the process, and reads NDJSON output. The agent cannot call a Python callback or invoke a function on `ExecutionContext`.

The agent CAN run shell commands. This is the realistic integration surface.

### Proposed mechanism: sub-job-aware CLI

Enhance `stepwise run --wait` to accept parent linkage flags:

```bash
# Agent calls this from within its session:
stepwise run --wait my-flow.yaml \
  --parent-job $STEPWISE_JOB_ID \
  --parent-run $STEPWISE_RUN_ID \
  --input schema='{"tables": [...]}'
```

The agent executor sets `STEPWISE_JOB_ID` and `STEPWISE_RUN_ID` as environment variables in the subprocess. When the agent calls `stepwise run --wait --parent-job ...`, the runner creates a sub-job (via `_create_sub_job()`) instead of a top-level job.

The `--wait` flag makes the CLI block and print the sub-job's terminal output as JSON when complete. The agent reads this output and continues.

For parallel dispatch:

```bash
# Fire-and-forget:
JOB_A=$(stepwise run --async my-flow.yaml --parent-job $STEPWISE_JOB_ID --input ...)
JOB_B=$(stepwise run --async other-flow.yaml --parent-job $STEPWISE_JOB_ID --input ...)

# Later, collect results:
RESULT_A=$(stepwise wait $JOB_A)
RESULT_B=$(stepwise wait $JOB_B)
```

### Open questions

**1. DELEGATED vs RUNNING status**

In the current model, a step with sub-jobs is DELEGATED — it has handed off execution. In Level 2, the step is still RUNNING (the agent is alive and working), but it has spawned child jobs. This is a new state that the engine doesn't model today.

Options:
- Keep the step RUNNING and track spawned sub-jobs separately (via `executor_state` or a new field)
- Introduce a new status like `ORCHESTRATING` that means "running + has active children"
- Don't track the relationship at the step level — the sub-jobs have `parent_job_id` pointing to the agent's job, which is sufficient for the job tree view

The simplest option is probably: don't change step status at all. The step stays RUNNING. The sub-jobs are children of the job (not the step run) via `parent_job_id`. The job tree shows them. When the agent's `start()` finally returns, the step completes normally with `type="data"`. The sub-jobs are already done (because the agent waited for them) or get cleaned up on failure.

**2. Server vs headless mode**

`stepwise run --wait` currently delegates to a running server if detected. With `--parent-job`, the runner needs access to the same engine instance that owns the parent job. In server mode, this works — the CLI delegates to the server via WebSocket, and the server's engine creates the sub-job. In headless mode (`--local`), the runner spawns its own engine, which doesn't have access to the parent job's engine.

Options:
- Only support Level 2 in server mode (the agent executor would require `stepwise serve` to be running)
- In headless mode, the agent executor starts a lightweight local server that the CLI can delegate to
- Pass the store path via environment so the headless CLI can open the same SQLite database (but two engines on one DB is unsafe without proper locking)

Server-only is probably the right starting constraint. `stepwise serve` is the recommended mode for anything non-trivial anyway.

**3. Failure and cleanup**

If the agent crashes after spawning sub-jobs:
- Sub-jobs that are still RUNNING become orphaned
- The parent step fails (agent process died)
- Who cleans up the orphaned sub-jobs?

Options:
- The agent executor's `start()` method tracks spawned sub-job IDs (via env vars or a manifest file) and cancels them on failure
- The engine's stale detection (`_observe_external_jobs()`) catches them
- A new cleanup hook on step failure that cancels all sub-jobs with `parent_job_id` matching the current job

**4. Input passing**

In Level 1, the sub-job inherits `parent_run.inputs` — the resolved inputs of the delegating step. In Level 2, the agent needs to pass arbitrary data to sub-jobs, including results from previous sub-jobs. The `--input key=value` CLI flag handles simple cases, but complex nested data (like a full schema dict) is awkward on the command line.

Options:
- `--input-file inputs.json` flag — agent writes inputs to a JSON file
- Stdin: `echo '{"schema": {...}}' | stepwise run --wait --input-stdin flow.yaml`
- The agent writes the inputs into the flow YAML itself (as `$job.param` defaults)

**5. Teaching agents the pattern**

`agent_help.py` would need a new section explaining the thread-spawning pattern. The agent needs to understand:
- When to spawn a sub-job vs do the work directly
- How to pass data between sub-jobs (via `--input` or input files)
- How to handle sub-job failures
- When to use `--wait` (sequential) vs `--async` + `stepwise wait` (parallel)
- The depth limit and how to stay within it

**6. Sub-job identity and the job tree**

With `--parent-job`, sub-jobs link to the parent job. But there's no link to the specific step run that spawned them (unlike Level 1's `parent_step_run_id`). This means the job tree shows them as children of the job, but not associated with any particular step.

Options:
- Add `--parent-run` flag too, and thread both through
- Only link at the job level — the agent's step is the only step in the parent job that's RUNNING, so the association is implicit
- Add a `spawned_by` metadata field on the sub-job for informational purposes

**7. Output aggregation**

In Level 1, the sub-flow has a single terminal step whose outputs map to the parent step's outputs. Clean and deterministic. In Level 2, the agent spawns multiple sub-jobs and synthesizes their results itself. The agent's final `ExecutorResult` is the synthesis — the sub-jobs' outputs are intermediate.

This is actually fine — it's the same as the current model where an agent does work and returns `type="data"`. The sub-jobs are just a means to that end. No aggregation logic needed in the engine.

### Benefits of Level 2

- **Reactivity:** The agent sees results from early work before deciding what to do next. This is Slate's core argument — rigid upfront decomposition fails when the environment changes mid-task.
- **Heterogeneous threads:** Sub-jobs can contain human steps, script steps, other agents. An agent can spawn a sub-job that runs tests, sees the results, spawns another that fixes failures, iterates.
- **Parallel execution:** With `--async` + `stepwise wait`, the agent can dispatch multiple sub-jobs concurrently — true thread weaving.
- **Full observability:** Every sub-job is in SQLite, visible in the web UI job tree, event-logged. Unlike Slate's ephemeral threads.
- **Composability with Level 1:** An agent could emit a flow (Level 1) that contains agent steps with Level 2 capabilities — recursive dynamic composition.
- **Natural depth limiting:** `max_sub_job_depth` prevents runaway decomposition. The agent can't spawn sub-jobs that spawn sub-jobs that spawn sub-jobs beyond the configured depth.

### Implementation sketch (not yet implementation-ready)

1. Add `--parent-job` and `--parent-run` flags to `stepwise run` CLI command
2. In `runner.py`, when `--parent-job` is set:
   - In server delegation mode: include parent IDs in the WebSocket delegation message
   - Server creates sub-job via `_create_sub_job()` instead of `create_job()`
3. In `agent.py`, set `STEPWISE_JOB_ID` and `STEPWISE_RUN_ID` env vars on the subprocess when `capabilities` includes `spawn_thread` (or similar)
4. In `agent_help.py`, add thread-spawning instructions when capability is enabled
5. Add `stepwise wait <job-id>` CLI command (or enhance existing) for collecting async results
6. Handle cleanup: track spawned sub-job IDs, cancel on agent failure

### Estimated complexity

Significantly more than Level 1. The CLI flag changes are straightforward, but the server delegation protocol, headless mode limitations, cleanup logic, and agent instruction design all require careful work. Estimate: 400-600 lines of code + thorough testing.

---

## Level 1.5: Iterative Delegation via Exit Rules (recommended next step)

Instead of Level 2's complex IPC, achieve reactive decomposition using existing primitives — exit rules + context chains:

```yaml
steps:
  agent-phase:
    executor: agent
    config:
      emit_flow: true
    chain: planning
    exits:
      - name: needs-more
        when: "outputs.get('needs_more_work', False)"
        action: loop
        target: agent-phase
      - name: done
        action: advance
    outputs: [result]
```

**How it works:** Agent runs → emits flow → engine executes it → step completes → exit rule evaluates → loops back to agent step → M7a context chain gives agent the previous iteration's results → agent sees what happened → emits another flow or completes directly.

**What you get:**
- Reactive decomposition (agent sees results before deciding next steps)
- Full persistence (every iteration is a step run in SQLite)
- Portability preserved (no server-only constraint)
- Debuggability (each iteration visible in job tree)
- ~100 lines on top of Level 1

**What you lose vs Level 2:** No mid-step parallelism (but `for_each` handles that if the agent knows the set upfront), and higher latency per iteration (agent restarts each loop, but context chains keep it oriented).

**Estimated scope:** ~100 lines on top of Level 1.

---

## Implementation order

1. **Level 1 — implemented.** Agents can dynamically decompose work into Stepwise flows. `ExecutorResult(type="delegate")`, emit flow detection in `AgentExecutor`, `case "delegate"` in engine.

2. **Level 1.5 — implemented.** Iterative delegation via exit rules. `_delegated: True` marker injection in `_handle_sub_job_done()`, self-referencing input bindings for iteration context.

3. **Level 2 — defer** until there are 3+ real workflows where 1.5 genuinely can't do the job. Level 2's server-only constraint, orphan cleanup complexity, and dependency on agents reliably managing async state are design-level concerns, not implementation details.

### The strategic argument

Stepwise's differentiator is NOT "agents can spawn sub-jobs" — that's LangGraph's territory. Stepwise's story is: **portable, persistent DAG engine that treats scripts, humans, LLMs, and agents as equal citizens.** Level 1 supports that story: an unpredictable agent writes a predictable, static workflow that the deterministic engine executes. That's the bridge between agent flexibility and workflow reliability. Level 2 muddies it by making the agent the orchestrator, which breaks portability and puts orchestration logic in ephemeral agent context instead of SQLite.
