---
title: "Implementation Plan: H18 Server Restart Resilience — Checkpoint/Resume for Agent Steps"
date: "2026-03-22T18:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# H18: Server Restart Resilience — Checkpoint/Resume for Agent Steps

## Overview

When the stepwise server restarts while agent steps are running, PID-aware cleanup (H9) correctly preserves alive processes but the engine has no mechanism to re-attach to them. The `_poll_external_changes` watchdog fails them after 60 seconds as "executor task lost." This change adds a `recover_orphaned_agents()` method that creates monitoring tasks for surviving agent processes, so their results flow through the normal completion pipeline.

## Requirements

### R1: Re-attach to surviving agent processes after restart
- **What**: After engine startup, detect RUNNING step runs with alive PIDs and create monitoring tasks that wait for process exit and process results
- **Acceptance criteria**:
  - Each surviving agent step gets an entry in `engine._tasks[run.id]`
  - The `_poll_external_changes` watchdog does not fail these steps
  - When the agent process exits, the result is pushed to the engine queue and processed through the normal `_process_launch_result` path
  - Exit rules, delegation, output extraction, and session ID injection all work identically to a non-restarted run

### R2: Graceful failure for agents that die between cleanup and re-attach
- **What**: If an agent process dies between `_cleanup_zombie_jobs` (which saw it alive) and `recover_orphaned_agents`, fail it cleanly
- **Acceptance criteria**:
  - Step is marked FAILED with error "Agent process died before re-attachment"
  - Job status is re-evaluated (may complete if other terminal steps are done)

### R3: Extract result-building logic from AgentExecutor.start()
- **What**: Factor the post-wait result assembly into a reusable `build_agent_result()` function that both `start()` and the monitoring task can call
- **Acceptance criteria**:
  - `AgentExecutor.start()` produces identical results before and after refactor (no behavior change)
  - `_monitor_orphaned_agent()` uses the same function for result building
  - Emit-flow detection, output extraction, failure handling, and session ID injection are all covered

### R4: Test coverage for restart/re-attach scenarios
- **Acceptance criteria**:
  - Tests for: monitoring task creation, monitoring task completion, agent death before re-attach, mixed alive/dead steps, emit_flow on re-attach, watchdog non-interference

## Assumptions

### A1: PID-aware cleanup is already implemented
**Verified**: `_cleanup_zombie_jobs()` at `server.py:415-481` already checks `run.pid` liveness via `os.kill(pid, 0)`, skips alive processes, and keeps the job RUNNING. This was done as part of H9. Tests exist at `tests/test_pid_tracking.py`.

### A2: `_poll_external_changes` is the actual failure point
**Verified**: `engine.py:2667-2679` — RUNNING steps with no entry in `self._tasks` and `started_at` >60s ago are failed with "Executor task lost." After restart, `_tasks` is empty (new engine instance), so all surviving agents hit this within 5 seconds of the engine loop starting.

### A3: AcpxBackend.wait() handles non-child processes
**Verified**: `agent.py:475-490` — `os.waitpid()` raises `ChildProcessError` when the new server isn't the process parent. The catch block falls back to polling `_is_process_alive()` via `/proc/{pid}/status` until the process disappears, then returns exit_code=0. Safe for re-adoption.

### A4: executor_state contains all data needed to reconstruct AgentProcess
**Verified**: `agent.py:1008-1020` — `state_update_fn` persists `pid`, `pgid`, `output_path`, `working_dir`, `session_name`, `output_mode`, `output_file`, and optionally `session_id`. These are exactly the fields of `AgentProcess` (plus output config for result building).

### A5: recover_orphaned_agents must run after engine.run() starts
**Verified**: `server.py:670` — `_engine_task = asyncio.create_task(_engine.run())` creates the engine task. `recover_orphaned_agents()` creates tasks that push to `self._queue`, which requires the engine loop to be consuming events. Calling it on the next line (before `yield`) is safe since asyncio tasks don't run until the event loop yields.

### A6: Agent semaphore must be acquired for re-attached agents
**Verified**: `engine.py:2616-2622` — `_agent_semaphore` limits concurrent agents (default 3). `_run_executor` acquires it at line 2868. Re-attached monitoring tasks must also acquire it to prevent thread pool exhaustion.

## Out of Scope

- **Checkpointing intermediate agent progress**: We re-attach to the live process and wait for it to finish. There's no mid-stream checkpointing of agent conversation state. The session transcript is captured on completion as usual.
- **Re-adopting non-agent executors**: Script, LLM, and poll executors run synchronously in the engine's thread pool. When the server dies, their thread dies. Only agent processes (external OS processes with `start_new_session=True`) survive restarts.
- **Session continuity across server restarts for loop iterations**: If an agent step completes after re-attach and its exit rules trigger a loop, the new iteration launches normally. The session continuity mechanism (`continue_session: true`) works as before.
- **PID reuse detection**: Low probability on modern Linux (PID space typically 4M+). The worst case is waiting for an unrelated process, which eventually exits and the step fails on output parsing. Acceptable for v1.

## Architecture

### Current flow (bug)
```
Server restart
  → _cleanup_zombie_jobs()
      → pid alive? → skip (keep RUNNING)    ← H9, already works
  → _engine.recover_jobs()                   ← settles completed-but-not-settled jobs
  → asyncio.create_task(engine.run())        ← engine loop starts
  → [5 seconds later] _poll_external_changes()
      → RUNNING step not in _tasks, age >60s
      → FAILS step with "Executor task lost"  ← BUG: kills surviving agent
```

### New flow (fix)
```
Server restart
  → _cleanup_zombie_jobs()                   ← unchanged
  → _engine.recover_jobs()                   ← unchanged
  → asyncio.create_task(engine.run())        ← engine loop starts
  → _engine.recover_orphaned_agents()        ← NEW: creates monitoring tasks
      → For each RUNNING step with alive pid:
          → Reconstruct AgentProcess from executor_state
          → Create _monitor_orphaned_agent() task
          → Register in _tasks[run.id]       ← prevents watchdog kill
      → On agent exit:
          → build_agent_result() → ExecutorResult
          → queue.put(("step_result", ...))  ← normal processing pipeline
```

### Result handling reuse
```
_monitor_orphaned_agent() → backend.wait(process) → AgentStatus
    → build_agent_result(backend, agent_status, state, config)
    → ExecutorResult
    → queue.put(("step_result", ...))
    → _handle_queue_event() → _process_launch_result()
    → exit rules, delegation, settlement — all standard
```

### Files touched
| File | Change |
|------|--------|
| `src/stepwise/agent.py` | Extract `build_agent_result()` from `AgentExecutor.start()`, promote `_is_process_alive` to module level |
| `src/stepwise/engine.py` | Add `recover_orphaned_agents()` and `_monitor_orphaned_agent()` to `AsyncEngine` |
| `src/stepwise/server.py` | Wire `recover_orphaned_agents()` into lifespan after engine task creation |
| `tests/test_restart_reattach.py` | New test file for H18 re-attachment scenarios |

## Implementation Steps

### Step 1: Promote `_is_process_alive` to module level (~10 min)

**File**: `src/stepwise/agent.py`

Move `AcpxBackend._is_process_alive()` (line 586) to a module-level function `is_process_alive(pid: int) -> bool`. Update the two call sites in `AcpxBackend.wait()` (line 485) and `AcpxBackend.check()` (line 501) to call the module-level version. This allows `engine.py` to import it without depending on `AcpxBackend`.

Keep `AcpxBackend._is_process_alive` as a thin delegation wrapper for backward compatibility (one line: `return is_process_alive(pid)`).

### Step 2: Extract `build_agent_result()` from `AgentExecutor.start()` (~30 min)

**File**: `src/stepwise/agent.py`

The post-wait result assembly in `AgentExecutor.start()` (lines 1033-1092) handles four cases: failure, emit-flow delegation, output extraction, and session ID injection. Extract this into a standalone function:

```python
def build_agent_result(
    backend: AgentBackend,
    agent_status: AgentStatus,
    executor_state: dict,
    config: dict,
    inputs: dict | None = None,
) -> ExecutorResult:
```

Parameters:
- `executor_state` — the dict already persisted in DB (has `pid`, `pgid`, `output_path`, `working_dir`, `session_name`, `output_mode`, `output_file`)
- `config` — executor config (needs `emit_flow`, `continue_session`, `output_fields`)
- `inputs` — original step inputs (for `_session_id` passthrough); `None` for re-adoption since inputs aren't stored

Refactor `AgentExecutor.start()` to call `build_agent_result()` instead of inline code. Verify zero behavior change by running existing agent tests.

Also add `"exec_mode": process.exec_mode` to the early state dict (lines 1009-1017) so it's persisted in `executor_state`. Currently missing but needed for accurate `AgentProcess` reconstruction. The monitoring task doesn't use it directly (no queue owner cleanup), but it's correct to persist for debugging and future use.

For re-adoption, the config values are reconstructed from `executor_state` (which stores `output_mode`, `output_file`) and the step definition from `job.workflow.steps[step_name].executor.config` (which has `emit_flow`, `continue_session`, `output_fields`).

### Step 3: Add `recover_orphaned_agents()` to AsyncEngine (~45 min)

**File**: `src/stepwise/engine.py`

New public method on `AsyncEngine`, placed after `recover_jobs()` (line 2721):

```python
def recover_orphaned_agents(self) -> None:
```

Logic:
1. Import `is_process_alive` from `stepwise.agent`
2. Iterate `store.active_jobs()` where `created_by == "server"`
3. For each job, iterate `store.running_runs(job_id)`
4. For each run with `run.executor_state` and `run.executor_state.get("pid")`:
   - Call `is_process_alive(pid)` — re-check, may have died since cleanup
   - If dead: fail the run directly (set FAILED, save, emit event, check terminal)
   - If alive: look up step def from `job.workflow.steps[run.step_name]`, create asyncio task via `_monitor_orphaned_agent()`, register in `self._tasks[run.id]`
5. Log summary: "Re-attached N surviving agent(s) across M job(s)"

### Step 4: Add `_monitor_orphaned_agent()` to AsyncEngine (~45 min)

**File**: `src/stepwise/engine.py`

New async method on `AsyncEngine`, parallel structure to `_run_executor()`:

```python
async def _monitor_orphaned_agent(
    self, job_id: str, step_name: str, run_id: str,
    executor_state: dict, exec_config: dict,
) -> None:
```

Logic:
1. Acquire `self._agent_semaphore` (same as `_run_executor`)
2. Reconstruct `AgentProcess` from `executor_state`:
   ```python
   process = AgentProcess(
       pid=executor_state["pid"],
       pgid=executor_state.get("pgid", executor_state["pid"]),
       output_path=executor_state.get("output_path", ""),
       working_dir=executor_state.get("working_dir", ""),
       session_name=executor_state.get("session_name"),
       exec_mode=executor_state.get("exec_mode", False),
   )
   ```
3. Create fresh `AcpxBackend()` (stateless, safe to construct)
4. Run `backend.wait(process)` in thread pool via `loop.run_in_executor()` — blocks until agent exits
5. Call `build_agent_result()` to convert `AgentStatus` → `ExecutorResult`
6. Push `("step_result", job_id, step_name, run_id, result)` to `self._queue`
7. On `CancelledError`: return (job was cancelled)
8. On other exceptions: push `("step_error", ...)` to queue
9. In `finally`: release `self._agent_semaphore`

### Step 5: Wire into server lifespan (~10 min)

**File**: `src/stepwise/server.py`

In `lifespan()`, after `_engine_task = asyncio.create_task(_engine.run())` (line 670), add:

```python
_engine.recover_orphaned_agents()
```

This must come after the engine task is created (so the queue is being consumed) but before `yield` (so it runs during startup). The method creates asyncio tasks synchronously — no await needed.

### Step 6: Write tests (~1 hr)

**File**: `tests/test_restart_reattach.py`

See Testing Strategy below.

### Step 7: Full regression (~15 min)

```bash
uv run pytest tests/
cd web && npm run test
```

## Testing Strategy

### Test file: `tests/test_restart_reattach.py`

All tests use direct store manipulation and function calls — no HTTP server needed. Follow patterns from `tests/test_pid_tracking.py` and `tests/conftest.py`.

#### T1: `test_recover_creates_monitoring_task`
- Create AsyncEngine + store with a RUNNING job + RUNNING step (pid=os.getpid(), executor_state has full agent state)
- Patch `stepwise.agent.is_process_alive` → True
- Patch `AcpxBackend.wait` to return immediately with `AgentStatus(state="completed")`
- Call `engine.recover_orphaned_agents()`
- Assert: `run.id in engine._tasks`

#### T2: `test_monitoring_task_completes_step`
- Same setup as T1 but run the engine event loop briefly (via `asyncio.wait_for`)
- Mock `AcpxBackend.wait` to return `AgentStatus(state="completed", exit_code=0)`
- Mock output extraction to return a simple artifact
- Assert: step transitions to COMPLETED, job settles to COMPLETED

#### T3: `test_agent_dies_before_reattach`
- Create store with RUNNING step (pid of a dead process)
- Patch `is_process_alive` → False
- Call `engine.recover_orphaned_agents()`
- Assert: step FAILED with "died before re-attachment", run.id NOT in `_tasks`

#### T4: `test_watchdog_does_not_kill_reattached_step`
- Create engine + RUNNING step with alive PID, started >120s ago
- Call `recover_orphaned_agents()` — registers task
- Trigger `_poll_external_changes()` manually
- Assert: step still RUNNING (task is in `_tasks`, watchdog skips it)

#### T5: `test_failed_agent_on_reattach`
- Mock `AcpxBackend.wait` to return `AgentStatus(state="failed", error="segfault")`
- Run monitoring through completion
- Assert: step FAILED, exit rules evaluated

#### T6: `test_emit_flow_on_reattach`
- Set up RUNNING step with `emit_flow: true` in executor config
- Create a `.stepwise/emit.flow.yaml` file in the step's working_dir
- Mock `AcpxBackend.wait` to return completed
- Run monitoring through completion
- Assert: result type is "delegate" with sub_job_def populated

#### T7: `test_multiple_surviving_agents_across_jobs`
- Two RUNNING jobs, each with one surviving agent step
- Call `recover_orphaned_agents()`
- Assert: both steps get tasks in `_tasks`, both jobs stay RUNNING

### Existing test compatibility
- `tests/test_pid_tracking.py` — all tests remain valid. The `_cleanup_zombie_jobs` behavior is unchanged.
- `tests/test_server_reliability.py` — zombie/stale tests unaffected. `recover_orphaned_agents()` runs after `_cleanup_zombie_jobs`, acting on the surviving subset.

### Commands
```bash
# H18 tests only
uv run pytest tests/test_restart_reattach.py -v

# H18 + PID tracking
uv run pytest tests/test_restart_reattach.py tests/test_pid_tracking.py -v

# Full regression
uv run pytest tests/ -v
cd web && npm run test
```

## Risks & Mitigations

### Risk 1: Agent completes between cleanup check and monitoring task creation
**Likelihood**: Low (startup sequence runs in <100ms)
**Impact**: `backend.wait()` returns immediately — process already gone, `ChildProcessError` → polls `/proc` → not alive → returns exit_code=0. Result processing works normally.
**Mitigation**: Handled by existing `AcpxBackend.wait()` non-child fallback (agent.py:483-487).

### Risk 2: Thread pool exhaustion from re-attached agents
**Likelihood**: Low (bounded by agent semaphore)
**Impact**: New agent launches blocked until re-attached agents finish.
**Mitigation**: `_monitor_orphaned_agent` acquires `_agent_semaphore` before submitting to thread pool, same as `_run_executor`. The semaphore default is 3 concurrent agents.

### Risk 3: Stuck step watchdog races with recovery
**Likelihood**: None (deterministic)
**Impact**: Would fail surviving agents.
**Mitigation**: `recover_orphaned_agents()` runs synchronously during `lifespan()` startup, registering tasks in `_tasks` before `yield`. The engine loop's first `_poll_external_changes` call happens at least 5 seconds later (queue timeout). Even if it ran immediately, `_tasks` already has entries.

### Risk 4: `build_agent_result()` extraction introduces regression
**Likelihood**: Medium (refactoring active code path)
**Impact**: Agent step completions broken.
**Mitigation**: Step 2 is a pure refactor — extract, call, verify. Run full test suite after extraction before proceeding. The function signature captures exactly the data available at both call sites. Focus on passing existing `test_agent*.py` tests.

### Risk 5: Agent output streaming after re-attach
**Likelihood**: None (already handled)
**Impact**: Would miss real-time output in web UI.
**Mitigation**: `_agent_stream_monitor()` (server.py) discovers agents via `store.running_runs()` on each poll cycle and tails their `output_path` from `executor_state`. Re-attached agents have the same store state, so streaming resumes automatically. No change needed.

### Risk 6: Queue owner cleanup kills re-attached agent's queue owner
**Likelihood**: Low (protection already exists)
**Impact**: Agent process loses ACP session, may fail.
**Mitigation**: `_collect_active_agent_info()` (server.py:559-581) scans `store.all_running_runs()` for active session IDs and PIDs. Re-attached agents are RUNNING in the store with their session info in `executor_state`, so they're automatically protected.

### Risk 7: Non-agent RUNNING steps after restart
**Likelihood**: Rare (script/LLM steps are short-lived)
**Impact**: `recover_orphaned_agents()` only processes steps with PIDs in `executor_state`. Non-agent steps have no PID → left alone → watchdog fails them after 60s as "Executor task lost." This is correct: script/LLM executors run in-process, so if the server died, their work is gone.
**Mitigation**: No action needed — current behavior is correct for non-agent steps.
