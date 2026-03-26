# S9-1: Agent Step Output Bridge

## Overview

Agent steps that declare `outputs` fail because spawned agents have no knowledge that they need to write structured JSON to a specific location. The fix: when an agent step declares outputs but doesn't explicitly set `output_mode`, auto-promote to file mode, inject `STEPWISE_OUTPUT_FILE` into the agent's environment, and append system prompt instructions telling the agent to write its structured output there.

## Requirements

| # | Requirement | Acceptance Criteria |
|---|---|---|
| R1 | Auto-promote output mode | Agent steps with `outputs: [...]` and no explicit `output_mode` in YAML automatically use file-based output collection. Steps without declared outputs remain in "effect" mode. Steps with explicit `output_mode: effect` or `output_mode: stream_result` are unchanged. |
| R2 | Inject `STEPWISE_OUTPUT_FILE` env var | The agent process environment includes `STEPWISE_OUTPUT_FILE` pointing to an absolute path `{working_dir}/{step_name}-output.json`. The path's parent directory exists before the agent spawns. The env var is NOT set when the step has no declared outputs. |
| R3 | Auto-append output instructions to prompt | When the step declares outputs and output_mode is "file" (whether explicit or auto-promoted), append a structured instruction block to the agent prompt. Instructions include: exact field names, JSON example, file path, env var reference. Instructions are NOT appended when output_mode is "effect" or "stream_result", even if outputs are declared. |
| R4 | Read output file after agent completes | After the agent process exits, read `STEPWISE_OUTPUT_FILE`, parse as JSON, and use as the step artifact. The existing retry/fallback logic in `_extract_output` file-mode branch handles filesystem flush delays. When the file is missing, the error message includes the expected path and field names. |
| R5 | For-each fan-out works | N parallel agent children in a for-each each get unique `STEPWISE_OUTPUT_FILE` paths. This is guaranteed by sub-job workspace isolation (`for_each/{step_name}/{index}/`). Downstream steps receive aggregated structured data from all N items. |
| R6 | Backward-compatible | (a) Flows with explicit `output_mode: effect` are unchanged. (b) Flows with explicit `output_mode: stream_result` are unchanged. (c) Flows with explicit `output_mode: file` are unchanged (they already work). (d) Agent steps without declared outputs are unchanged (remain "effect"). (e) `AgentStatus.result` shortcut (used by MockAgentBackend) still takes precedence over file reading in all modes. |
| R7 | `emit_flow` coexistence | Agent steps with `emit_flow: true` AND declared outputs: emit_flow check happens first (agent.py:1062). If a flow is emitted, the output file is irrelevant (delegation path handles outputs). If no flow is emitted, the output file is read normally. Output instructions are appended to the prompt alongside emit_flow instructions — both paths are valid. |
| R8 | `output_schema` typed fields respected | When `output_schema` defines field types (choice, number, etc.), the output instructions include type hints so the agent produces correctly-typed values. Optional fields (from `output_schema`) are marked as optional in the instructions. |
| R9 | `_session_id` auto-injection unaffected | The existing `_session_id` injection at agent.py:1082-1087 continues to work. `_session_id` is exempt from artifact validation (underscore-prefixed). |
| R10 | Env var parity with ScriptExecutor | Agent processes also receive `STEPWISE_STEP_IO`, `STEPWISE_STEP_NAME`, and `STEPWISE_ATTEMPT` env vars, matching the convention established by ScriptExecutor (executors.py:314-321). |

## Assumptions

Each verified against the actual codebase as of 2026-03-25:

| # | Assumption | Verified At |
|---|---|---|
| A1 | Engine injects `output_fields` into executor config when step declares outputs | `engine.py:1631-1632`: `if step_def.outputs and "output_fields" not in exec_ref.config: exec_ref = exec_ref.with_config({"output_fields": step_def.outputs})` |
| A2 | `AgentExecutor.__init__` receives all config keys via `**config` kwargs | `agent.py:949`: `**config: Any` — all config keys land in `self.config` dict |
| A3 | `AcpxBackend.spawn()` inherits parent env minus `CLAUDECODE` but adds no `STEPWISE_*` vars | `agent.py:401`: `env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}` |
| A4 | `_render_prompt()` is the single prompt assembly point | `agent.py:1209-1257`: template substitution → chain context → injected context → emit_flow → file-mode rewrite. All prompt modifications funnel through here. |
| A5 | `_extract_output("file")` already reads from `state["output_file"]` with retry logic | `agent.py:1267-1300`: path traversal check, retry on empty/missing, fallback to `output_file_missing` artifact |
| A6 | For-each sub-jobs have isolated workspaces with distinct job_ids | `engine.py:1982-1984`: workspace = `job.workspace_path/for_each/{step_name}/{index}/`. Each sub-job gets its own `_gen_id("job")`. |
| A7 | `output_file` state key is set in `start()` when mode is "file" | `agent.py:970-972`: generates `{step_name}-output.json`, stored in `state["output_file"]` at line 1038 |
| A8 | YAML parser puts `output_mode` into executor config only when explicitly written | `yaml_loader.py:335`: `for k in ("prompt", "output_mode", ...): if k in step_data: config[k] = step_data[k]`. Key absent from config → user didn't write it. |
| A9 | `AgentStatus.result` (returned by MockAgentBackend) takes precedence over file reading | `agent.py:1263-1264`: `if agent_status.result: artifact = agent_status.result` — checked before the `match output_mode` block |
| A10 | `MockAgentBackend.spawn()` stores all config keys in `_processes[pid]` dict | `agent.py:841-847`: `self._processes[pid] = {"prompt": prompt, "config": config, ...}` — testable via `get_process_info()` |

## Dependency Graph

```
Step 1 (auto-promote in AgentExecutor.__init__)
  │
  ├──→ Step 2 (env vars in AcpxBackend.spawn)  ← depends on: output_fields in config
  │      │
  │      └──→ Step 2a (unit test: env var injection)
  │
  ├──→ Step 3 (prompt instructions in _render_prompt)
  │      │
  │      ├──→ Step 3a (unit test: prompt content)
  │      └──→ Step 3b (output_schema type hints)  ← depends on: Step 3
  │
  └──→ Step 1a (unit tests: auto-promotion logic)

Step 4 (improve error message in _extract_output)  ← independent
  │
  └──→ Step 4a (unit test: error messages)

Step 5 (update emit_flow instructions)  ← independent
  │
  └──→ Step 5a (manual verification)

Step 6 (integration tests)  ← depends on: Steps 1-4
  │
  ├──→ Step 6a (single agent → downstream step)
  ├──→ Step 6b (for-each fan-out)
  ├──→ Step 6c (emit_flow + outputs coexistence)
  └──→ Step 6d (explicit output_mode override)

Step 7 (documentation)  ← depends on: Steps 1-5
```

## Implementation Steps

---

### Step 1: Auto-promote output mode in `AgentExecutor.__init__`

**File:** `src/stepwise/agent.py`, lines 943-959
**Depends on:** nothing
**Commit:** standalone (feature flag for all subsequent steps)

When the engine injects `output_fields` into config (engine.py:1631) but the user didn't write `output_mode` in their YAML, the executor should auto-promote from "effect" to "file".

Detection mechanism: The YAML parser (yaml_loader.py:335) only adds `output_mode` to config when explicitly present in the step YAML. The registry factory (registry_factory.py:79) calls `cfg.get("output_mode", "effect")`, so the default "effect" is applied *after* the config dict is finalized. Therefore: `"output_mode" in cfg` at factory call time means the user explicitly wrote it.

**Changes:**

1. In `registry_factory.py:76-83`, pass a flag indicating whether the user explicitly set output_mode:

```python
registry.register("agent", lambda cfg: AgentExecutor(
    backend=acpx_backend,
    prompt=cfg.get("prompt", ""),
    output_mode=cfg.get("output_mode", "effect"),
    output_path=cfg.get("output_path"),
    _user_set_output_mode=("output_mode" in cfg),
    **{k: v for k, v in cfg.items()
       if k not in ("prompt", "output_mode", "output_path")},
))
```

2. In `AgentExecutor.__init__` (agent.py:943-959), after setting `self.output_mode`:

```python
self._auto_promoted = False
user_set = config.pop("_user_set_output_mode", False)
if not user_set and self.output_mode == "effect" and self.config.get("output_fields"):
    self.output_mode = "file"
    self._auto_promoted = True
```

**Verification:** `AgentExecutor(output_mode="effect", output_fields=["x"]).output_mode == "file"` when `_user_set_output_mode=False`. When `_user_set_output_mode=True`, stays "effect".

---

### Step 1a: Unit tests for auto-promotion logic

**File:** `tests/test_agent_output_bridge.py` (new)
**Depends on:** Step 1
**Commit:** with Step 1

| Test | Asserts |
|---|---|
| `test_auto_promote_when_outputs_declared` | `AgentExecutor(backend=mock, output_mode="effect", output_fields=["result"])` → `output_mode == "file"`, `_auto_promoted == True` |
| `test_no_promote_when_explicit_effect` | `AgentExecutor(backend=mock, output_mode="effect", output_fields=["result"], _user_set_output_mode=True)` → `output_mode == "effect"`, `_auto_promoted == False` |
| `test_no_promote_when_no_outputs` | `AgentExecutor(backend=mock, output_mode="effect")` → `output_mode == "effect"` |
| `test_no_promote_when_stream_result` | `AgentExecutor(backend=mock, output_mode="stream_result", output_fields=["result"])` → `output_mode == "stream_result"` |
| `test_explicit_file_not_flagged_auto` | `AgentExecutor(backend=mock, output_mode="file", output_fields=["result"], _user_set_output_mode=True)` → `output_mode == "file"`, `_auto_promoted == False` |

---

### Step 2: Inject `STEPWISE_OUTPUT_FILE` and other env vars into `AcpxBackend.spawn()`

**File:** `src/stepwise/agent.py`, `AcpxBackend.spawn()`, lines 400-401
**Depends on:** Step 1 (output_fields must be in config for this to activate)
**Commit:** standalone

After `env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}` (line 401), add:

```python
# Stepwise env vars for agent processes (parity with ScriptExecutor)
env["STEPWISE_STEP_NAME"] = context.step_name
env["STEPWISE_ATTEMPT"] = str(context.attempt)
env["STEPWISE_STEP_IO"] = str(step_io)

# Output file for structured output bridging
output_fields = config.get("output_fields")
if output_fields:
    output_filename = config.get("output_path") or f"{context.step_name}-output.json"
    output_file_abs = str((Path(working_dir) / output_filename).resolve())
    env["STEPWISE_OUTPUT_FILE"] = output_file_abs
```

Note: `step_io` is already defined at line 393 (`Path(working_dir) / ".stepwise" / "step-io" / ...`). The `STEPWISE_OUTPUT_FILE` path is in the working_dir root (not step-io), matching where `_extract_output` looks for it.

**Why not in AgentExecutor.start():** The executor doesn't control the subprocess env. `AcpxBackend.spawn()` owns the `env` dict and the `Popen` call. This is the right abstraction boundary — the backend is responsible for process-level concerns.

---

### Step 2a: Unit test for env var injection

**File:** `tests/test_agent_output_bridge.py`
**Depends on:** Step 2
**Commit:** with Step 2

Cannot test via `MockAgentBackend` (it doesn't spawn real processes or set env vars). Two options:

**Option A (preferred): Capture env in a test-specific backend subclass.**

```python
class EnvCapturingBackend(MockAgentBackend):
    """Mock backend that captures the env vars that would be passed to Popen."""
    def __init__(self):
        super().__init__()
        self.captured_env = {}

    def spawn(self, prompt, config, context):
        # Simulate env construction logic from AcpxBackend.spawn()
        # Store config for inspection
        process = super().spawn(prompt, config, context)
        self.last_config = config
        self.last_context = context
        return process
```

This tests that `output_fields` flows through config correctly. The actual env var construction in `AcpxBackend.spawn()` is tested by:

**Option B: Direct unit test of the env construction logic.**

Extract the env-building code from `AcpxBackend.spawn()` into a `_build_env(config, context, step_io, working_dir)` classmethod or standalone function. Test it directly:

| Test | Asserts |
|---|---|
| `test_env_has_stepwise_output_file_when_outputs_declared` | `_build_env(config={"output_fields": ["x"]}, ...)` → `env["STEPWISE_OUTPUT_FILE"]` is set, path ends with `{step_name}-output.json`, path is absolute |
| `test_env_no_output_file_when_no_outputs` | `_build_env(config={}, ...)` → `"STEPWISE_OUTPUT_FILE" not in env` |
| `test_env_has_step_io_and_attempt` | Always set: `STEPWISE_STEP_IO`, `STEPWISE_STEP_NAME`, `STEPWISE_ATTEMPT` |
| `test_env_output_file_uses_custom_output_path` | `config={"output_fields": ["x"], "output_path": "custom.json"}` → path ends with `custom.json` |

**Decision:** Use Option B. Extracting the env logic makes it testable without mocking `Popen`. Keep the function private: `_build_agent_env(...)`.

---

### Step 3: Append output instructions to agent prompt

**File:** `src/stepwise/agent.py`, `_render_prompt()`, lines 1209-1257
**Depends on:** Step 1 (auto-promoted mode must be "file" for instructions to fire)
**Commit:** standalone

At the end of `_render_prompt()`, after the existing file-mode `output.json` rewrite (line 1248-1255), append structured output instructions when output_fields is present and mode is "file":

```python
output_fields = self.config.get("output_fields", [])
if output_fields and self.output_mode == "file":
    output_file = self.output_path or f"{context.step_name}-output.json"
    field_list = ", ".join(f'"{f}"' for f in output_fields)
    example = {f: f"<{f} value>" for f in output_fields}
    prompt += (
        f"\n\n<stepwise-output>\n"
        f"When you have completed your task, write your structured output "
        f"as a JSON file to: {output_file}\n\n"
        f"Required JSON keys: {field_list}\n"
        f"Example:\n```json\n"
        f"{json.dumps(example, indent=2)}\n```\n"
        f"\nThe file path is also available as $STEPWISE_OUTPUT_FILE.\n"
        f"Write this file as one of your final actions.\n"
        f"</stepwise-output>"
    )
```

**Key design choices:**
- XML tags (`<stepwise-output>`) make the block parseable and visually distinct for agents.
- The instruction says "one of your final actions" rather than "your last action" — agents may need to clean up after writing.
- Both literal path and env var reference are included for flexibility.
- The instruction is appended after emit_flow instructions (line 1239-1246). Both can coexist: the agent either emits a flow or writes output directly.

---

### Step 3a: Unit test for prompt content

**File:** `tests/test_agent_output_bridge.py`
**Depends on:** Step 3
**Commit:** with Step 3

Directly instantiate `AgentExecutor` with a `MockAgentBackend` and call `_render_prompt()`:

| Test | Asserts |
|---|---|
| `test_prompt_includes_output_instructions` | With `output_fields=["summary", "score"]`, mode "file": prompt contains `<stepwise-output>`, `"summary"`, `"score"`, `STEPWISE_OUTPUT_FILE`, JSON example block |
| `test_prompt_no_instructions_for_effect_mode` | With `output_mode="effect"` (explicit, `_user_set_output_mode=True`), `output_fields=["x"]`: prompt does NOT contain `<stepwise-output>` |
| `test_prompt_no_instructions_without_outputs` | With no `output_fields`: prompt does NOT contain `<stepwise-output>` |
| `test_prompt_uses_custom_output_path` | With `output_path="results.json"`, `output_fields=["x"]`: prompt contains `results.json` not `{step_name}-output.json` |
| `test_prompt_output_instructions_after_emit_flow` | With both `emit_flow=True` and `output_fields=["result"]`: prompt contains emit_flow instructions AND output instructions (both present, in that order) |

---

### Step 3b: Add `output_schema` type hints to output instructions

**File:** `src/stepwise/agent.py`, `_render_prompt()` (continuation of Step 3 block)
**Depends on:** Step 3
**Commit:** with Step 3 or separately

When the executor config includes `output_schema` (passed through from `step_def.output_schema` — need to verify this is wired up), enhance the instructions with type information:

```python
# Enhance example with output_schema type hints if available
output_schema = self.config.get("_output_schema", {})
if output_schema:
    for f in output_fields:
        if f in output_schema:
            spec = output_schema[f]
            type_hint = spec.get("type", "str")
            if type_hint == "number":
                example[f] = 0.0
            elif type_hint == "bool":
                example[f] = True
            elif type_hint == "choice":
                options = spec.get("options", [])
                example[f] = options[0] if options else "<value>"
            # Mark optional fields
            if not spec.get("required", True):
                example[f] = f"<optional: {example[f]}>"
```

**Pre-requisite check:** Verify that `output_schema` is passed to the executor. Currently `_prepare_step_run` (engine.py:1631-1633) only injects `output_fields`. Need to also inject `_output_schema`:

```python
# In engine.py _prepare_step_run, after the output_fields injection:
if step_def.output_schema:
    schema_dict = {k: v.to_dict() for k, v in step_def.output_schema.items()}
    exec_ref = exec_ref.with_config({"_output_schema": schema_dict})
```

**Scope note:** This sub-step is a nice-to-have enhancement. The core bridge (Steps 1-4) works without it. Can be deferred to a follow-up if the main implementation takes longer than expected.

---

### Step 4: Improve error message when output file is missing

**File:** `src/stepwise/agent.py`, `_extract_output()`, line 1300
**Depends on:** nothing (independent improvement)
**Commit:** standalone

Currently, when file-mode output reading fails after retries, it returns:
```python
artifact = {"status": "completed", "output_file_missing": True}
```

This then fails `_validate_artifact` with a generic "missing declared outputs" error. Improve:

```python
except (FileNotFoundError, json.JSONDecodeError) as exc:
    expected_fields = self.config.get("output_fields", [])
    artifact = {
        "status": "completed",
        "output_file_missing": True,
        "_error": (
            f"Agent did not write output file: {file_path}. "
            f"Expected JSON with keys: {expected_fields}. "
            f"Error: {type(exc).__name__}: {exc}"
        ),
    }
```

The `_error` field (underscore-prefixed) is exempt from artifact validation but visible in the web UI's `HandoffEnvelopeView`, giving operators a clear diagnostic.

---

### Step 4a: Unit test for error messages

**File:** `tests/test_agent_output_bridge.py`
**Depends on:** Step 4
**Commit:** with Step 4

| Test | Asserts |
|---|---|
| `test_missing_output_file_error_includes_path_and_fields` | Create `AgentExecutor` with `output_fields=["result"]`, mode "file". Call `_extract_output` with state pointing to a non-existent file. Assert artifact contains `output_file_missing: True` and `_error` string includes the file path and `["result"]`. |
| `test_malformed_json_error_includes_context` | Write invalid JSON to the output path. Assert `_error` mentions JSONDecodeError. |

---

### Step 5: Update `agent_help.py` emit_flow instructions

**File:** `src/stepwise/agent_help.py`, `build_emit_flow_instructions()`, lines 565-614
**Depends on:** nothing (independent)
**Commit:** standalone

Add a paragraph after the "When to emit vs direct" section (line 598):

```python
lines.append("**Structured output:**")
lines.append("- If your step declares `outputs`, stepwise automatically sets `STEPWISE_OUTPUT_FILE`")
lines.append("- Write a JSON object with the declared output keys to this file before finishing")
lines.append("- If you emit a flow instead, the sub-flow's terminal step outputs are used (file is ignored)\n")
```

Also update the sub-step documentation section to note that agent sub-steps in emitted flows also get `STEPWISE_OUTPUT_FILE` automatically when they declare outputs.

---

### Step 6a: Integration test — single agent with outputs, downstream consumer

**File:** `tests/test_agent_output_bridge.py`
**Depends on:** Steps 1, 3, 4 (auto-promote + prompt + error handling)
**Commit:** with other Step 6 tests

```python
def test_agent_output_bridge_end_to_end(async_engine):
    """Agent step declares outputs, writes JSON file, downstream step consumes."""
    workspace = tempfile.mkdtemp()

    # Backend that writes the output file to the expected location
    class OutputWritingBackend(MockAgentBackend):
        def spawn(self, prompt, config, context):
            process = super().spawn(prompt, config, context)
            # Simulate agent writing output JSON
            output_file = config.get("output_path") or f"{context.step_name}-output.json"
            output_path = Path(process.working_dir) / output_file
            output_path.write_text(json.dumps({"summary": "good", "score": 0.9}))
            return process

    backend = OutputWritingBackend()
    backend.set_auto_complete()  # result=None so file is read
    # Register WITHOUT passing output_mode (simulates user YAML without output_mode)
    async_engine.registry.register("agent", lambda cfg: AgentExecutor(
        backend=backend,
        prompt=cfg.get("prompt", ""),
        output_mode=cfg.get("output_mode", "effect"),
        output_path=cfg.get("output_path"),
        **{k: v for k, v in cfg.items()
           if k not in ("prompt", "output_mode", "output_path")},
    ))

    register_step_fn("format", lambda inputs: {
        "formatted": f"Summary: {inputs['summary']}, Score: {inputs['score']}"
    })

    wf = WorkflowDefinition(steps={
        "analyze": StepDefinition(
            name="analyze",
            executor=ExecutorRef("agent", {"prompt": "Analyze data"}),
            outputs=["summary", "score"],
        ),
        "format": StepDefinition(
            name="format",
            executor=ExecutorRef("callable", {"fn_name": "format"}),
            inputs=[
                InputBinding("summary", "analyze", "summary"),
                InputBinding("score", "analyze", "score"),
            ],
            outputs=["formatted"],
        ),
    })

    job = async_engine.create_job(objective="test", workflow=wf, workspace_path=workspace)
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.COMPLETED

    runs = async_engine.store.runs_for_job(job.id)
    format_run = [r for r in runs if r.step_name == "format"][0]
    assert "Summary: good" in format_run.result.artifact["formatted"]
```

**Key subtlety:** `backend.set_auto_complete()` passes `result=None` (actually `result={}`). Since `AgentStatus.result` is `{}` (truthy empty dict), `_extract_output` will use it as the artifact — bypassing file reading. Need to handle this: `set_auto_complete(result=None)` should set `result=None`, or the test backend should NOT set `result` at all.

Check agent.py:1263: `if agent_status.result:` — empty dict `{}` is falsy in Python? No, `bool({})` is `False`. So `set_auto_complete(result={})` → `agent_status.result = {}` → `bool({}) == False` → falls through to file reading. **This is correct.** The `set_auto_complete(result={})` call at line 893 sets `result={}` which is falsy, so file reading proceeds. Verified.

Wait, re-check: `set_auto_complete` at line 888-894:
```python
def set_auto_complete(self, result: dict | None = None, ...):
    self._auto_result = AgentStatus(state="completed", exit_code=0, cost_usd=cost_usd, result=result or {})
```
`result or {}` — if `result=None`, this becomes `{}`. So `agent_status.result = {}`, which is falsy. File reading proceeds.

But if `result={"key": "value"}`, then `agent_status.result` is truthy and file is skipped. This is the existing `set_auto_complete(result={"result": "done"})` pattern used in emit_flow tests. **No conflict.**

---

### Step 6b: Integration test — for-each agent fan-out

**File:** `tests/test_agent_output_bridge.py`
**Depends on:** Steps 1, 3, 6a pattern
**Commit:** with Step 6a

```python
def test_for_each_agent_output_bridge(async_engine):
    """For-each with 3 items, each agent writes structured output."""
    # ... produce_list returns ["a", "b", "c"]
    # ... sub-flow has agent step with outputs: [result]
    # ... OutputWritingBackend writes {"result": f"processed_{item}"} per item
    # ... verify for-each completes with 3 results, each containing correct data
```

This validates R5 (for-each fan-out). Each sub-job's agent gets its own workspace, so output files don't collide.

---

### Step 6c: Integration test — emit_flow + outputs coexistence

**File:** `tests/test_agent_output_bridge.py`
**Depends on:** Steps 1, 3
**Commit:** with Step 6a

```python
def test_emit_flow_takes_precedence_over_output_file(async_engine):
    """Agent with emit_flow=True and outputs=[result]: emit_flow wins."""
    # ... FileWritingMockBackend that writes BOTH emit.flow.yaml AND output.json
    # ... verify: delegation happens, output file ignored
    # ... sub-flow terminal step output becomes the artifact
```

This validates R7 (emit_flow coexistence).

---

### Step 6d: Integration test — explicit output_mode overrides

**File:** `tests/test_agent_output_bridge.py`
**Depends on:** Steps 1
**Commit:** with Step 6a

```python
def test_explicit_effect_mode_not_promoted(async_engine):
    """User explicitly sets output_mode: effect — no auto-promotion."""
    # ... register agent with _user_set_output_mode=True, output_mode="effect"
    # ... step has outputs: [result] but mode stays effect
    # ... verify: artifact is {"status": "completed", "session_id": ...}
    # ... step FAILS artifact validation (expected behavior — user chose this)
```

```python
def test_explicit_stream_result_not_promoted(async_engine):
    """User explicitly sets output_mode: stream_result — no auto-promotion."""
    # ... similar, verify stream_result behavior unchanged
```

This validates R6a and R6b.

---

### Step 7: Documentation updates

**File:** `CLAUDE.md`
**Depends on:** Steps 1-5 (document final behavior)
**Commit:** standalone

Add to the Executors table row for `agent`:
> Agent steps with declared `outputs` automatically use file-based output collection. The agent receives `STEPWISE_OUTPUT_FILE` env var and prompt instructions. Set `output_mode: effect` to opt out.

Add to the YAML workflow format section, after the agent step example:
```yaml
# Agent with structured output (auto-bridged)
steps:
  analyze:
    executor: agent
    prompt: "Analyze $data and produce a summary with quality score"
    inputs:
      data: $job.data
    outputs: [summary, score]    # agent auto-receives instructions to write these
```

Add `STEPWISE_OUTPUT_FILE` to the environment variables documentation alongside existing `STEPWISE_INPUT_*` vars.

---

## Testing Strategy

### Unit Tests
```bash
uv run pytest tests/test_agent_output_bridge.py -v
```

13 test cases total:
- 5 auto-promotion logic tests (Step 1a)
- 4 env var construction tests (Step 2a)
- 5 prompt content tests (Step 3a)
- 2 error message tests (Step 4a)

### Integration Tests
```bash
uv run pytest tests/test_agent_output_bridge.py -v -k "end_to_end or for_each or emit_flow or explicit"
```

5 integration test cases:
- 1 single agent → downstream step (Step 6a)
- 1 for-each fan-out (Step 6b)
- 1 emit_flow coexistence (Step 6c)
- 2 explicit mode override (Step 6d)

### Regression Tests
```bash
uv run pytest tests/ -v
```

Critical existing suites that must pass unchanged:
- `test_agent_emit_flow.py` — emit_flow path unaffected (MockAgentBackend uses `result={}` which is falsy → file reading is a no-op since no file exists → but these tests use `set_auto_complete(result={})` where the agent also writes `emit.flow.yaml`, so delegation path runs before `_extract_output`. **Safe.**)
- `test_agent_ergonomics.py` — existing agent CLI patterns
- `test_engine.py` — artifact validation, including `TestArtifactValidation`
- `test_for_each.py` — fan-out (no agent steps in these tests)
- `test_output_schema.py` — schema validation unaffected (operates on external executor)

### Manual Smoke Test
```bash
# Create test flow
cat > /tmp/test-bridge.flow.yaml << 'EOF'
name: test-output-bridge
steps:
  analyze:
    executor: agent
    prompt: "Write a one-sentence summary of: $phrase"
    inputs:
      phrase: $job.phrase
    outputs: [summary]

  format:
    run: |
      echo "{\"result\": \"Got: $STEPWISE_INPUT_summary\"}"
    inputs:
      summary: analyze.summary
    outputs: [result]
EOF

stepwise run /tmp/test-bridge.flow.yaml --input phrase="Hello world" --local
```

Verify: agent step completes, format step receives summary, job succeeds.

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Agent ignores output instructions** | Medium | Step fails with descriptive error (Step 4) | Clear XML-tagged instructions with example JSON. Error message tells user exactly what was expected. Retry/escalate exit rule pattern handles transient failures. |
| **Output file written with wrong keys** | Medium | `_validate_artifact` fails with "missing declared outputs: [x]" | Example JSON in prompt shows exact expected keys. Consider adding a validator hint in the error message suggesting the agent wrote wrong keys. |
| **`set_auto_complete(result={"key": ...})` in existing tests bypasses file reading** | Low | Tests pass for wrong reason | This is actually correct behavior: `AgentStatus.result` truthy → use it directly. Tests that need file reading must pass `result=None` or `result={}`. Document this in test docstrings. |
| **Auto-promotion changes behavior of previously-broken flows** | Very Low | Flows that failed with "missing declared outputs" now attempt file-based output (may succeed or fail with different error) | Strictly better: gives flows a chance to succeed. Users who explicitly set `output_mode: effect` are unaffected (Step 1 respects explicit config). |
| **Env var `STEPWISE_OUTPUT_FILE` conflicts with user env** | Very Low | Agent writes to wrong path | Uses established `STEPWISE_` prefix convention. No existing env var uses this name (verified via grep). |
| **For-each race on output file** | Very Low | File collision | Mitigated by sub-job workspace isolation: each sub-job has `for_each/{step_name}/{index}/` workspace. Verified in engine.py:1982-1984. |
| **Prompt length increase** | Very Low | Token waste | Instructions block is ~10 lines (~150 tokens). Negligible vs. typical agent context. |
| **`_build_agent_env` extraction breaks `AcpxBackend.spawn` flow** | Low | Refactoring error | Keep extraction minimal — private function, same module. Test both the extracted function and an existing emit_flow integration test to verify no regression. |

## File Change Summary

| File | Change Type | Lines Affected | Description |
|---|---|---|---|
| `src/stepwise/agent.py` (AgentExecutor) | Modify | ~943-959 | Auto-promote output_mode when outputs declared |
| `src/stepwise/agent.py` (AcpxBackend) | Modify | ~400-401 | Extract `_build_agent_env()`, set STEPWISE_OUTPUT_FILE + STEPWISE_STEP_IO/NAME/ATTEMPT |
| `src/stepwise/agent.py` (_render_prompt) | Modify | ~1248-1257 | Append `<stepwise-output>` instruction block |
| `src/stepwise/agent.py` (_extract_output) | Modify | ~1300 | Improve error message with path and field names |
| `src/stepwise/registry_factory.py` | Modify | ~76-83 | Pass `_user_set_output_mode` flag |
| `src/stepwise/engine.py` | Modify | ~1631-1633 | Inject `_output_schema` into agent executor config (Step 3b, optional) |
| `src/stepwise/agent_help.py` | Modify | ~598 | Document STEPWISE_OUTPUT_FILE in emit_flow instructions |
| `tests/test_agent_output_bridge.py` | New | ~250 lines | 18 test cases (13 unit + 5 integration) |
| `CLAUDE.md` | Modify | ~3 sections | Document output bridge, env var, YAML example |

## Implementation Order (Recommended)

For a single implementer working sequentially:

1. **Step 1 + 1a** → commit: `feat: auto-promote agent output mode when step declares outputs`
2. **Step 4 + 4a** → commit: `fix: improve error message when agent output file is missing`
3. **Step 2 + 2a** → commit: `feat: inject STEPWISE_OUTPUT_FILE env var into agent processes`
4. **Step 3 + 3a** → commit: `feat: auto-append output instructions to agent prompt`
5. **Step 3b** → commit: `feat: include output_schema type hints in agent instructions` (optional, can defer)
6. **Step 6a-6d** → commit: `test: integration tests for agent output bridge`
7. **Step 5** → commit: `docs: document STEPWISE_OUTPUT_FILE in emit_flow instructions`
8. **Step 7** → commit: `docs: document agent output bridge in CLAUDE.md`

Steps 1 and 4 are independent and can be parallelized. Steps 2 and 3 depend on Step 1 but are independent of each other. Step 6 depends on all of 1-4.
