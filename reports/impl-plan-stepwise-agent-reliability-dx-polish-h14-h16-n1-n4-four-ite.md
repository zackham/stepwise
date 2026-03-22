---
title: "Implementation Plan: Agent Reliability + DX Polish (H14, H16, N1, N4)"
date: "2026-03-21T18:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Agent Reliability + DX Polish (H14, H16, N1, N4)

## Overview

Four changes: add transient-error retry logic inside the agent executor (H14), implement per-for_each concurrency limiting with stagger delay (H16), upgrade the `stepwise new` template to a 3-step DAG (N1), and show the server's project path on job submission (N4).

## Requirements

### H14: Agent step retry on transient errors

**Acceptance criteria:**
- Agent steps retry on transient errors (HTTP 429, 503, 529, connection errors, timeouts) with exponential backoff (30s, 60s, 120s)
- Non-transient errors (exit code 0/1, permission errors, context length) fail immediately without retry
- Default: max 3 attempts. Configurable via existing `decorators` syntax on the step definition
- Each retry attempt is logged with error details and delay duration
- Retry metadata is injected into `executor_meta` on the final result (success or failure)

### H16: Stagger parallel agent steps

**Acceptance criteria:**
- `for_each` steps accept a `max_concurrent` option (default: no limit, preserving current behavior)
- When `max_concurrent` is set and items exceed it, only N sub-jobs run at a time; more launch as earlier ones complete
- A global `max_concurrent_agents` config option (default: 5) limits total simultaneous agent executor steps across all jobs
- A 1-second stagger delay between launching concurrent agent steps prevents thundering herd
- Both options are exposed in `.stepwise/config.yaml`

### N1: Update `stepwise new` template

**Acceptance criteria:**
- `stepwise new my-flow` creates a 3-step flow: script→llm→script
- The template demonstrates input/output data flow between steps
- The template passes `stepwise validate` with no errors or warnings

### N4: Server project visibility on job submission

**Acceptance criteria:**
- CLI prints the server's project path with submission confirmation: `▸ job submitted to running server (~/work/stepwise)`
- Falls back to current behavior (no path shown) when the server doesn't return `project_path`
- No additional latency — uses a single health check call over localhost

## Assumptions

### A1: Agent error classification already identifies transient errors

**Verified at `agent.py:957-968`.** `_classify_error()` returns `"infra_failure"` for rate limit/429/network/connection errors and `"timeout"` for timeout errors. These two categories map directly to "transient." The remaining categories (`"context_length"`, `"agent_failure"`) are non-transient. The retry logic can reuse this classification without modification.

### A2: The existing RetryDecorator works at the executor wrapper level, not inside the executor

**Verified at `decorators.py:64-127`.** `RetryDecorator.start()` calls `self._executor.start()` in a loop, checking `executor_state.get("failed")` and `envelope.executor_meta.get("failed")` after each attempt. It retries on any failure regardless of cause. For agent steps, this means the entire spawn→wait cycle reruns on retry. The decorator's backoff uses `time.sleep(0.01 * 2^attempt)` — appropriate for test environments but needs real delays (30s+) for production agent retries. **Decision:** Rather than modifying the generic RetryDecorator (which serves all executor types), add retry logic *inside* `AgentExecutor.start()` with transient-error-specific backoff. This keeps the decorator available as a separate concern.

### A3: For-each sub-jobs bypass `max_concurrent_jobs` and launch all at once

**Verified at `engine.py:1643-1645`.** `_launch_for_each()` calls `self.start_job(sub_job_id)` in a tight loop for all uncached sub-jobs. `start_job()` at line 2609 checks `max_concurrent_jobs` but only against `store.active_jobs()` which counts all active jobs. Sub-jobs are *not* excluded from this count, but in practice they all start because `max_concurrent_jobs` defaults to 10 and sub-jobs are started before the parent check triggers. The actual constraint is the thread pool (32 workers, `engine.py:2530-2532`).

### A4: `_handle_sub_job_done` is the completion callback for for-each sub-jobs

**Verified at `engine.py:2992-3008`.** When a sub-job finishes, `_check_job_terminal()` calls `_handle_sub_job_done()` (line 2960). For for-each sub-jobs, this calls `_check_for_each_completion()` (line 3007), which checks if all sub-jobs are done. This is the right hook for launching the next batch of sub-jobs when `max_concurrent` is set.

### A5: `ForEachSpec` in models.py has no `max_concurrent` field

**Verified at `models.py:217-239`.** Fields are: `source_step`, `source_field`, `item_var`, `on_error`. Adding `max_concurrent` requires changes to `ForEachSpec`, `to_dict()`, `from_dict()`, and the YAML parser in `yaml_loader.py:547-601`.

### A6: The health endpoint already returns `project_path`

**Verified at `server.py:943-951`.** `GET /api/health` returns `{"status": "ok", "active_jobs": N, "project_path": str|null}`. The `_project_dir` global is set from `STEPWISE_PROJECT_DIR` or defaults to `"."` (line 134). The `_submit_watch_job` function at `cli.py:1740-1794` already has access to `server_url` and can fetch this endpoint.

### A7: The `stepwise new` template is a string literal in `cmd_new`

**Verified at `cli.py:994-1002`.** The template is a plain f-string generating a single-step YAML. It writes to `flows/{name}/FLOW.yaml`. No validation is run on the generated template.

## Out of Scope

- **H15 (permissions configuration):** Needs design for agent sandbox policy — separate item.
- **H17 (Skill tool permission):** Requires investigation of session cancellation mechanics.
- **H18 (server restart resilience for agents):** Needs checkpoint/resume design for long-running agent sessions.
- **Modifying the generic `RetryDecorator`:** H14 adds retry inside `AgentExecutor` rather than changing the shared decorator. The existing decorator remains available for explicit per-step configuration.
- **Web UI changes:** None of these items require frontend changes.

## Architecture

### H14: Retry inside AgentExecutor

Add a `_retry_on_transient()` wrapper method to `AgentExecutor` (`agent.py`). It wraps the spawn→wait→classify cycle with transient-error retry logic:

- After `backend.wait(process)` returns a failed status (line 682), check `_classify_error()` result
- If transient (`"infra_failure"` or `"timeout"`), sleep with backoff and retry the entire spawn→wait cycle
- If non-transient (`"context_length"`, `"agent_failure"`), return failure immediately
- Inject retry metadata into `executor_state` and `executor_meta` on the final result

Default retry config: `max_retries=3`, `initial_delay=30`, `backoff_factor=2`. Configurable via `AgentExecutor.__init__()` params read from `ExecutorRef.config`.

This follows the same pattern as the existing retry decorator (`decorators.py:64-127`) but operates at the transport level (retry the subprocess spawn) rather than the executor wrapper level. The distinction matters because:
1. Transient errors need longer backoff (30s vs 10ms)
2. Only transient errors should trigger retry (not all failures)
3. The agent process state (PID, session) needs cleanup between retries

### H16: For-each concurrency limiting

Two mechanisms, both in `engine.py`:

**Per-for_each `max_concurrent`:** Add to `ForEachSpec` (`models.py:217`). In `_launch_for_each()` (line 1643-1645), instead of starting all sub-jobs, start only `max_concurrent` and store the remainder in `executor_state["pending_sub_jobs"]`. In `_handle_sub_job_done()` → `_check_for_each_completion()` (line 3006-3008), after processing a completed sub-job, start the next pending one.

**Global `max_concurrent_agents`:** Add to `StepwiseConfig` (`config.py:104`). In `AsyncEngine.__init__()`, create an `asyncio.Semaphore(max_concurrent_agents)`. In `_run_step()` (line 2790-2808), acquire the semaphore before `run_in_executor()` if the executor type is `"agent"`. Release on completion. Add 1-second `asyncio.sleep()` stagger after acquiring the semaphore.

The semaphore approach follows the existing session lock pattern at `engine.py:2796-2806` (acquire async lock → run in executor → release).

### N1: Template update

Replace the f-string template in `cli.py:994-1002` with a 3-step YAML showing script→llm→script data flow. Use `textwrap.dedent()` for readability.

### N4: Health check on submission

In `_submit_watch_job()` (`cli.py:1787-1789`), add a `GET {server_url}/api/health` call before printing the confirmation. Extract `project_path`, abbreviate with `~` for home directory, and include in the print statement. Wrap in try/except to gracefully fall back.

## Implementation Steps

### Step 1: H14 — Add retry config to AgentExecutor constructor

**File:** `src/stepwise/agent.py`
**Lines:** ~587-613 (AgentExecutor.__init__)

1. Add constructor params: `max_retries: int = 3`, `retry_initial_delay: float = 30.0`, `retry_backoff_factor: float = 2.0`, `retriable_categories: set[str] = {"infra_failure", "timeout"}`
2. Read these from `config` dict in `__init__`: `self._max_retries = config.get("max_retries", 3)`, etc.
3. Update `registry_factory.py` — the agent factory lambda at the registration site passes config through to `AgentExecutor` (currently at `registry_factory.py`, search for `"agent"` registration). No change needed since config is already forwarded.

**Depends on:** Nothing
**Estimated time:** 15 minutes

### Step 2: H14 — Implement transient retry loop in AgentExecutor.start()

**File:** `src/stepwise/agent.py`
**Lines:** ~614-729 (AgentExecutor.start)

1. Extract the spawn→wait→classify block (lines 651-699) into a helper `_attempt_run(inputs, context) -> tuple[AgentStatus, dict]` that returns the agent status and state dict
2. Wrap with retry loop in `start()`:
   ```python
   for retry_num in range(1 + self._max_retries):
       status, state = self._attempt_run(inputs, context)
       if status.state != "failed":
           break
       error_cat = self._classify_error(status)
       if error_cat not in self._retriable_categories:
           break  # non-transient, don't retry
       if retry_num < self._max_retries:
           delay = self._retry_initial_delay * (self._retry_backoff_factor ** retry_num)
           logger.warning(f"[{step_id}] transient error ({error_cat}), retry {retry_num+1}/{self._max_retries} in {delay}s")
           time.sleep(delay)
   ```
3. After the loop, inject retry metadata into the result's `executor_state` and `envelope.executor_meta`:
   ```python
   state["retry"] = {"attempts": retry_num + 1, "errors": retry_errors}
   ```
4. The rest of `start()` (emit flow check, output extraction, session injection) remains unchanged

**Depends on:** Step 1
**Estimated time:** 45 minutes

### Step 3: H14 — Add HTTP 503/529 to error classification

**File:** `src/stepwise/agent.py`
**Lines:** 957-968 (`_classify_error`)

Add patterns for 503 and 529 status codes, and "usage cap" / "capacity" patterns:
```python
if any(s in error for s in ("rate limit", "429", "503", "529", "usage", "capacity")):
    return "infra_failure"
```

**Depends on:** Nothing (can parallelize with Step 2)
**Estimated time:** 10 minutes

### Step 4: H14 — Write tests for agent transient retry

**File:** `tests/test_agent_retry.py` (new file)

Test cases using `MockAgentBackend` (defined at `agent.py:465-581`):

1. **`test_agent_retry_on_transient_error`**: Configure `MockAgentBackend` to fail twice with "429 rate limit" error then succeed. Assert 3 attempts, final result is success, retry metadata present.
2. **`test_agent_no_retry_on_permanent_error`**: Configure to fail with "context length exceeded". Assert only 1 attempt, immediate failure.
3. **`test_agent_retry_exhausted`**: Configure to always fail with "connection refused". Assert `max_retries + 1` attempts, final result is failure with all error reasons logged.
4. **`test_agent_retry_backoff_timing`**: Verify delays between retries are exponentially increasing (use `time.monotonic()` deltas with low delay values for test speed).
5. **`test_agent_retry_config_from_step_definition`**: Create step with `executor: agent` and `config: {max_retries: 1, retry_initial_delay: 0.01}`. Assert custom config is respected.

Use short delays (`retry_initial_delay=0.01`) in all tests to avoid slow tests.

**Depends on:** Steps 2, 3
**Estimated time:** 45 minutes

### Step 5: H16 — Add `max_concurrent` to ForEachSpec

**File:** `src/stepwise/models.py`
**Lines:** 217-239 (ForEachSpec)

1. Add field: `max_concurrent: int = 0` (0 = unlimited, matching the `max_concurrent_jobs` convention from `engine.py:2609`)
2. Update `to_dict()` (line 224-230): include `max_concurrent` if non-zero
3. Update `from_dict()` (line 233-239): read `max_concurrent` with default 0

**File:** `src/stepwise/yaml_loader.py`
**Lines:** 547-601 (`_parse_for_each`)

4. Read `max_concurrent` from `step_data` (after `on_error` at line 581): `max_concurrent = step_data.get("max_concurrent", 0)`
5. Pass to `ForEachSpec` constructor

**Depends on:** Nothing
**Estimated time:** 20 minutes

### Step 6: H16 — Gate sub-job launching in `_launch_for_each`

**File:** `src/stepwise/engine.py`
**Lines:** 1600-1646

1. After building `actual_sub_job_ids` (line 1601), check `fe.max_concurrent`:
   - If `max_concurrent > 0` and `len(actual_sub_job_ids) > max_concurrent`: split into `initial_batch` (first N) and `pending_batch` (remainder)
   - Store `pending_batch` in `run.executor_state["pending_sub_jobs"]`
   - Only call `self.start_job()` for `initial_batch` items
   - If `max_concurrent == 0`: start all (current behavior)
2. Add 1-second stagger between sub-job starts when `max_concurrent > 0`:
   ```python
   for i, sub_job_id in enumerate(initial_batch):
       if i > 0:
           time.sleep(1)  # stagger to prevent thundering herd
       self.start_job(sub_job_id)
   ```
   Note: `_launch_for_each` runs synchronously (not async), so `time.sleep` is correct here. It's called from `_launch()` → `_dispatch_ready()` which runs in the engine's event loop — but for_each launch is intentionally synchronous (unlike `_run_step` which uses the thread pool). The 1s stagger per batch adds at most `max_concurrent - 1` seconds of delay, acceptable for batch orchestration.

**Depends on:** Step 5
**Estimated time:** 30 minutes

### Step 7: H16 — Release pending sub-jobs on completion

**File:** `src/stepwise/engine.py`
**Lines:** 1649-1769 (`_check_for_each_completion`) and 2992-3008 (`_handle_sub_job_done`)

1. In `_check_for_each_completion()`, after processing a completed/failed sub-job (before the `if not all_done: return False` at line 1718), check for pending sub-jobs:
   ```python
   pending = run.executor_state.get("pending_sub_jobs", [])
   if pending:
       # Count currently active sub-jobs
       active = sum(1 for sid in sub_job_ids
                    if self.store.load_job(sid).status == JobStatus.RUNNING)
       max_c = run.executor_state.get("max_concurrent", 0)
       while pending and (max_c == 0 or active < max_c):
           next_id = pending.pop(0)
           self.start_job(next_id)
           active += 1
       run.executor_state["pending_sub_jobs"] = pending
       self.store.save_run(run)
   ```
2. Store `max_concurrent` in `executor_state` alongside existing fields (line 1602-1609) so it's available during completion checks.
3. Add pending sub-job IDs to the full `sub_job_ids` tracking list so `_check_for_each_completion` considers them for the final all_done check.

**Depends on:** Step 6
**Estimated time:** 45 minutes

### Step 8: H16 — Add `max_concurrent_agents` to config and engine

**File:** `src/stepwise/config.py`
**Lines:** 104-115 (StepwiseConfig)

1. Add field: `max_concurrent_agents: int = 5`
2. Update `to_dict()` (line 134-155): include if non-default
3. Update `from_dict()` (line 159-182): read with default 5
4. Update `load_config()` merge (line 312-332): same pattern as `max_concurrent_jobs`

**File:** `src/stepwise/engine.py`
**Lines:** 2510-2533 (AsyncEngine.__init__)

5. Accept `max_concurrent_agents: int = 5` param
6. Create `self._agent_semaphore = asyncio.Semaphore(max_concurrent_agents)` (or `None` if 0 = unlimited)

**File:** `src/stepwise/engine.py`
**Lines:** 2790-2808 (`_run_step`)

7. Before `run_in_executor()`, check if executor type is `"agent"`. If so, acquire the semaphore and add 1s stagger:
   ```python
   is_agent = step_def.executor.type == "agent"
   if is_agent and self._agent_semaphore:
       await self._agent_semaphore.acquire()
       await asyncio.sleep(1)  # stagger
   try:
       result = await loop.run_in_executor(...)
   finally:
       if is_agent and self._agent_semaphore:
           self._agent_semaphore.release()
   ```

**File:** `src/stepwise/server.py`
**Line:** ~422 (engine construction)

8. Pass `max_concurrent_agents=config.max_concurrent_agents` to `AsyncEngine()`

**Depends on:** Nothing (independent of Steps 5-7)
**Estimated time:** 45 minutes

### Step 9: H16 — Write tests for for-each concurrency and agent throttle

**File:** `tests/test_for_each_concurrency.py` (new file)

Test cases:

1. **`test_for_each_max_concurrent_limits_active_jobs`**: Create a for-each step with 5 items and `max_concurrent=2`. Use slow `CallableExecutor` (sleep 0.1s). Assert that at most 2 sub-jobs are in RUNNING state at any time. Assert all 5 complete successfully with correct results.
2. **`test_for_each_max_concurrent_zero_means_unlimited`**: Same setup with `max_concurrent=0`. Assert all 5 sub-jobs start immediately.
3. **`test_for_each_max_concurrent_with_failure`**: 5 items, `max_concurrent=2`, item 1 fails. With `on_error="fail_fast"`, assert pending sub-jobs are never started. With `on_error="continue"`, assert remaining items still process.
4. **`test_max_concurrent_agents_semaphore`**: Create 4 independent agent steps (not for-each). Set `max_concurrent_agents=2`. Use `MockAgentBackend` with delay. Assert at most 2 agent executors run concurrently. (Track via a shared `threading.Event` counter.)
5. **`test_for_each_max_concurrent_in_yaml`**: Parse YAML with `max_concurrent: 3` on a for-each step. Assert `ForEachSpec.max_concurrent == 3`.

**Depends on:** Steps 7, 8
**Estimated time:** 60 minutes

### Step 10: N1 — Update `cmd_new` template

**File:** `src/stepwise/cli.py`
**Lines:** 994-1002

Replace the single-step template with:

```yaml
name: {name}
description: "A starter flow with three steps"

steps:
  setup:
    run: |
      echo '{{"greeting": "Hello from {name}!"}}'
    outputs: [greeting]

  process:
    executor: llm
    config:
      prompt: "Take this greeting and make it more creative: $greeting"
    outputs: [creative_greeting]
    inputs:
      greeting: setup.greeting

  deliver:
    run: |
      echo '{{"result": "Processed: '$creative_greeting'"}}'
    outputs: [result]
    inputs:
      creative_greeting: process.creative_greeting
```

Verify the template parses correctly by loading it with `yaml_loader.load_workflow_yaml()` in a test.

**Depends on:** Nothing
**Estimated time:** 20 minutes

### Step 11: N1 — Test new template validates

**File:** `tests/test_cli.py` (existing, add test) or `tests/test_new_template.py` (new file)

1. **`test_new_template_validates`**: Call `cmd_new` with a temp directory, then run `load_workflow_yaml()` on the generated YAML. Assert no validation errors. Assert 3 steps exist: `setup`, `process`, `deliver`. Assert `process` has `executor: llm`. Assert `deliver.inputs` references `process.creative_greeting`.
2. **`test_new_template_dag_structure`**: Load the template, call `workflow.entry_steps()`. Assert `["setup"]` is the only entry step. Assert `workflow.terminal_steps()` includes `deliver`.

**Depends on:** Step 10
**Estimated time:** 20 minutes

### Step 12: N4 — Add project path to job submission output

**File:** `src/stepwise/cli.py`
**Lines:** 1787-1789 (`_submit_watch_job`)

1. Before the print statements at line 1787, add a health check:
   ```python
   project_path = None
   try:
       health_req = urllib.request.Request(f"{server_url}/api/health")
       with urllib.request.urlopen(health_req, timeout=2) as resp:
           health = json.loads(resp.read())
           project_path = health.get("project_path")
   except Exception:
       pass
   ```
2. Format the project path for display (abbreviate home dir with `~`):
   ```python
   if project_path:
       display_path = project_path.replace(str(Path.home()), "~")
       print(f"▸ job submitted to running server ({display_path})")
   else:
       print(f"▸ job submitted to running server")
   ```
3. Keep the job URL print on the next line unchanged.

**Depends on:** Nothing
**Estimated time:** 15 minutes

### Step 13: N4 — Test project path display

**File:** `tests/test_cli.py` (existing, add test) or `tests/test_submit_display.py` (new file)

1. **`test_submit_shows_project_path`**: Mock the `urllib.request.urlopen` calls in `_submit_watch_job`. The health check mock returns `{"status": "ok", "project_path": "/home/user/my-project"}`. Capture stdout. Assert output contains `(~/my-project)` (with home dir abbreviation).
2. **`test_submit_graceful_without_project_path`**: Mock health check to return `{"status": "ok"}` (no project_path). Assert output is `▸ job submitted to running server` without parenthetical.
3. **`test_submit_graceful_on_health_failure`**: Mock health check to raise `urllib.error.URLError`. Assert output is `▸ job submitted to running server` without error.

**Depends on:** Step 12
**Estimated time:** 20 minutes

## Testing Strategy

### Commands

```bash
# Run all new tests
uv run pytest tests/test_agent_retry.py -v
uv run pytest tests/test_for_each_concurrency.py -v
uv run pytest tests/test_cli.py -v -k "test_new_template"

# Run related existing tests to check for regressions
uv run pytest tests/test_for_each.py -v
uv run pytest tests/test_engine.py -v
uv run pytest tests/test_concurrency.py -v
uv run pytest tests/test_server_reliability.py -v

# Full suite
uv run pytest tests/ -v

# Manual validation for N1
uv run stepwise new test-template-flow
uv run stepwise validate flows/test-template-flow/FLOW.yaml
rm -rf flows/test-template-flow
```

### Test matrix

| Item | Test file | Test count | Key assertions |
|------|-----------|------------|----------------|
| H14 | `test_agent_retry.py` | 5 | Transient retry with backoff, non-transient no retry, exhaustion, timing, config |
| H16 (for-each) | `test_for_each_concurrency.py` | 3 | max_concurrent limits, zero=unlimited, failure+pending interaction |
| H16 (global) | `test_for_each_concurrency.py` | 1 | Semaphore limits concurrent agents across jobs |
| H16 (YAML) | `test_for_each_concurrency.py` | 1 | YAML parsing of max_concurrent field |
| N1 | `test_cli.py` or `test_new_template.py` | 2 | Template validates, DAG structure correct |
| N4 | `test_cli.py` or `test_submit_display.py` | 3 | Path shown, graceful without path, graceful on error |

### Regression surface

- **H14:** Could interact with existing `RetryDecorator` if both are applied (double retry). Mitigated: inner retry handles transient only, decorator handles all failures. Document that combining them is valid but nested.
- **H16 (for-each):** Changes to `_launch_for_each` and `_check_for_each_completion` could break existing for-each behavior. Mitigated: `max_concurrent=0` preserves exact current behavior (the default). Existing `test_for_each.py` tests run unchanged.
- **H16 (global):** The semaphore in `_run_step` wraps the executor call. Could deadlock if agent steps are also session-locked. Mitigated: semaphore is acquired *before* session lock, consistent ordering prevents deadlock.
- **N1:** Template change doesn't affect existing flows. Only risk is template itself being invalid YAML — caught by the validation test.
- **N4:** Health check adds a network call to submission path. Mitigated: 2-second timeout, try/except fallback to current behavior.

## Risks & Mitigations

### R1: Agent retry delays slow down test suite

**Risk:** Default 30s backoff makes tests very slow.
**Mitigation:** All tests use `retry_initial_delay=0.01` (10ms). Production defaults only apply to real agent steps in YAML workflows. The `AgentExecutor` constructor reads delay from config, so tests can override easily.

### R2: For-each pending queue lost on server crash

**Risk:** `pending_sub_jobs` is stored in `run.executor_state` (persisted to SQLite). If the server crashes, pending sub-jobs exist as PENDING jobs in the DB but the for-each run's state may be stale.
**Mitigation:** `recover_jobs()` (line 2622-2632) already re-evaluates RUNNING jobs on startup. The for-each completion check will see pending sub-jobs and either start them or detect they're already done. No additional recovery logic needed.

### R3: Stagger delay in `_launch_for_each` blocks the event loop

**Risk:** `_launch_for_each` is called from `_dispatch_ready` which runs in the main asyncio event loop (not a thread). A `time.sleep(1)` would block the entire engine.
**Mitigation:** Use `asyncio.sleep(1)` instead, but this requires making `_launch_for_each` async. Alternative: launch sub-jobs via `asyncio.create_task()` with staggered `asyncio.sleep()` calls inside the task, keeping `_launch_for_each` synchronous. The task approach is simpler — create a helper `_stagger_launch_sub_jobs(sub_job_ids, delay)` that launches them with delays and runs as a background task. The for-each run is already in DELEGATED status, so the engine doesn't need to wait for all launches.

### R4: `max_concurrent_agents` semaphore in AsyncEngine may not be created when engine runs without config

**Risk:** Tests create `AsyncEngine` without passing config, so no `max_concurrent_agents` parameter.
**Mitigation:** Default to `None` (no semaphore) when not explicitly set. Only create semaphore when `max_concurrent_agents > 0`. Test fixtures in `conftest.py` don't need changes.

## Dependency Order

```
Step 1 (H14 config) ─── no deps
Step 2 (H14 retry logic) ─── depends on Step 1
Step 3 (H14 error patterns) ─── no deps (parallel with Step 2)
Step 4 (H14 tests) ─── depends on Steps 2, 3

Step 5 (H16 ForEachSpec) ─── no deps
Step 6 (H16 launch gating) ─── depends on Step 5
Step 7 (H16 completion release) ─── depends on Step 6
Step 8 (H16 global semaphore) ─── no deps (parallel with Steps 5-7)
Step 9 (H16 tests) ─── depends on Steps 7, 8

Step 10 (N1 template) ─── no deps
Step 11 (N1 tests) ─── depends on Step 10

Step 12 (N4 health check) ─── no deps
Step 13 (N4 tests) ─── depends on Step 12
```

Optimal parallel execution: {1, 3, 5, 8, 10, 12} can all start simultaneously. Then {2, 6} after their deps. Then {4, 7, 9, 11, 13} for testing.
