---
title: "Implementation Plan: H9 Orphaned Agent Step PID Tracking"
date: "2026-03-22T15:30:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# H9: Orphaned Agent Step PID Tracking

## Overview

When the stepwise server restarts, in-flight agent processes continue running but the engine loses all reference to them. The current `_cleanup_zombie_jobs` kills all such processes unconditionally. This change makes the server PID-aware on restart: check if agent processes are still alive, re-adopt them if so, and only fail truly dead ones.

## Requirements

### R1: PID-aware startup cleanup
- **What**: Modify `_cleanup_zombie_jobs` to check PID liveness before killing agent processes
- **Acceptance criteria**:
  - Running step with alive PID is left in RUNNING status; its job stays RUNNING
  - Running step with dead PID is marked FAILED with descriptive error
  - Running step with no PID in executor_state is marked FAILED (backward compat)
  - Jobs with a mix of alive and dead running steps: dead ones are failed, alive ones preserved, job stays RUNNING

### R2: Re-adoption of alive agent processes
- **What**: After engine startup, create monitoring tasks for still-alive agent processes so the engine receives their results when they complete
- **Acceptance criteria**:
  - Each alive agent step gets an entry in `engine._tasks` (prevents watchdog from killing it)
  - When the agent process exits, its result is pushed to the engine queue and processed normally
  - Re-adoption handles the `ChildProcessError` case (new server is not the process's parent)

### R3: No killing of alive agents
- **What**: Alive agent processes must NOT be killed during server restart
- **Acceptance criteria**:
  - `os.killpg` / `os.kill(SIGTERM)` is never called for PIDs confirmed alive
  - Agent work in progress is preserved across server restarts

## Assumptions

### A1: PIDs are already persisted in executor_state
**Verified**: `AgentExecutor.start()` calls `context.state_update_fn()` with `{"pid": ..., "pgid": ..., "output_path": ..., "working_dir": ..., "session_name": ...}` at `agent.py:663-672`. The `update_state` callback in `AsyncEngine._run_executor()` persists this to the DB via `store.save_run()` at `engine.py:2857-2865`. No new column or schema change needed.

### A2: Agent processes run in their own session
**Verified**: `subprocess.Popen(..., start_new_session=True)` at `agent.py:165`. This means the agent process survives server restart — it's not killed when the parent dies.

### A3: AcpxBackend.wait() handles non-child processes
**Verified**: `AcpxBackend.wait()` at `agent.py:186-201` catches `ChildProcessError` from `os.waitpid()` and falls back to polling `/proc/{pid}/status`. The re-adopted monitoring task can use `backend.wait()` safely even though the new server process is not the agent's parent.

### A4: _cleanup_zombie_jobs runs before the engine event loop
**Verified**: In `lifespan()` at `server.py:502-519`, the call order is: `_cleanup_zombie_jobs(store)` → `_engine.recover_jobs()` → `asyncio.create_task(_engine.run())`. The re-adoption step must happen after the engine task is created (so the queue is active).

### A5: executor_state for non-agent steps has no pid
**Verified**: `ScriptExecutor`, `ExternalExecutor`, `PollExecutor`, and `LLMExecutor` in `executors.py` do not store `pid` in their executor_state. Only `AgentExecutor` does this via `state_update_fn`. The PID check is safe to apply to all running steps — non-agent steps will simply have no PID and be failed as before.

### A6: _is_process_alive exists on AcpxBackend
**Verified**: `AcpxBackend._is_process_alive()` at `agent.py:252-261` reads `/proc/{pid}/status` and rejects zombies (checks for `"Z"` in State line). This is more robust than `os.kill(pid, 0)` which returns True for zombies. We should reuse this check.

### A7: Stuck step detector auto-fails untracked RUNNING steps
**Verified**: `AsyncEngine._poll_external_changes()` at `engine.py:2639-2658` detects RUNNING steps whose `run.id` is NOT in `self._tasks`. If the step's `started_at` is more than 60 seconds old, it is auto-failed with "Executor task lost (possible thread pool crash)". This means `recover_orphaned_agents()` MUST register monitoring tasks in `self._tasks[run.id]` or surviving agents will be killed after 60s. This is the most critical integration point.

## Out of Scope

- **Adding a `pid` column to step_runs**: Not needed. PIDs are already in `executor_state` JSON.
- **Re-adopting non-agent executors**: Script, LLM, and poll executors run synchronously in the thread pool. If the server dies, their work is lost. Only agent processes (external OS processes) survive restarts.
- **Reconnecting to agent sessions for prompt continuation**: We only wait for the existing process to finish. Session continuity for loop iterations will work normally when the step completes and re-dispatches.
- **Handling `emit_flow` during re-adoption**: The re-adopted monitoring path needs to detect emitted flows after the agent finishes. This is handled by using the same result-building logic as the normal path (Step 5 below).

## Architecture

### Existing flow (before this change)
```
Server restart → _cleanup_zombie_jobs() → kill ALL running processes → fail ALL running steps → fail job
```

### New flow (after this change)
```
Server restart
  → _cleanup_zombie_jobs()
      → For each running step:
          pid alive?  → skip (keep RUNNING)
          pid dead?   → fail step (don't kill — already dead)
          no pid?     → kill pgid if available, fail step (current behavior)
      → If any steps still alive: keep job RUNNING
      → Else: fail job (or complete if terminal steps done)
  → _engine.recover_jobs()        (settle fully-completed jobs)
  → asyncio.create_task(engine.run())
  → _engine.recover_orphaned_agents()
      → For each RUNNING step with alive pid:
          → Create monitoring task (wait for PID exit in thread pool)
          → Register in engine._tasks (prevents watchdog from failing it)
          → On exit: push result to engine queue → normal processing
```

### Key insight: No new data model
The `executor_state` dict on `StepRun` already contains `pid`, `pgid`, `output_path`, `working_dir`, `session_name`, and `output_mode` for agent steps. This was added as part of the `state_update_fn` mechanism for agent output streaming. We're reusing it for recovery.

### Critical integration: Stuck step detector
The engine's main loop (`engine.py:2639-2658`) auto-fails RUNNING steps that have no entry in `self._tasks` after 60s. `recover_orphaned_agents()` must register tasks in `self._tasks[run.id]` before this timeout fires. Since `recover_orphaned_agents()` is called in the same `lifespan()` startup sequence, immediately after `engine.run()` starts (within the first event loop iteration), the 60-second threshold provides ample margin.

### Result handling flow for re-adopted agents
```
_monitor_orphaned_agent() → backend.wait(process) → AgentStatus
    → build_agent_result() → ExecutorResult
    → queue.put(("step_result", ...))
    → engine main loop → _handle_queue_event() → _process_launch_result()
    → normal step completion/failure/delegation path
```

This reuses the entire existing result processing pipeline. The only new code is the monitoring coroutine and the result builder extraction.

### Files touched
- `src/stepwise/agent.py` — Extract `_is_process_alive()` to module level; add `build_agent_result()` helper
- `src/stepwise/server.py` — Modify `_cleanup_zombie_jobs()`, update lifespan to call recovery
- `src/stepwise/engine.py` — Add `recover_orphaned_agents()` and `_monitor_orphaned_agent()` to `AsyncEngine`
- `tests/test_server_reliability.py` — New test class for H9

## Implementation Steps

### Step 1: Extract `_is_process_alive()` to module level in `agent.py` (~10 min)

**File**: `src/stepwise/agent.py`

Move `AcpxBackend._is_process_alive()` (line 252) to a module-level function `is_process_alive(pid: int) -> bool`. Update `AcpxBackend` callers (lines 196, 212) to use the module-level function. This allows `server.py` and `engine.py` to import it without depending on `AcpxBackend`.

This is better than `os.kill(pid, 0)` because it correctly rejects zombie processes (checks `/proc/{pid}/status` for `"Z"` state).

### Step 2: Modify `_cleanup_zombie_jobs()` in `server.py` (~30 min)

**File**: `src/stepwise/server.py`, lines 415-463

Replace the unconditional kill-and-fail loop with PID-aware logic:

1. For each running run in a server-owned job:
   - Extract `pid` from `run.executor_state`
   - If `pid` exists and `is_process_alive(pid)`: skip this run, log "agent still alive, will re-adopt"
   - If `pid` exists and dead: fail the run with error `"Agent process died during server restart"` (no SIGTERM — already dead)
   - If no `pid`: kill pgid if available, then fail the run (current behavior for non-agent steps)
2. After processing all runs, track how many were left alive
3. If any runs still alive: keep job RUNNING (don't fail it)
4. If no runs alive: apply existing logic (complete if terminal steps done, else fail)

Existing test `test_running_job_with_running_steps_is_failed` still passes — its step has no `executor_state`, so no PID, so it gets failed as before.

### Step 3: Extract result-building from `AgentExecutor.start()` (~30 min)

**File**: `src/stepwise/agent.py`

The post-wait result assembly in `AgentExecutor.start()` (lines 676-737) handles:
- **Failure case** (lines 690-707): `AgentStatus.state == "failed"` → `ExecutorResult(type="data", executor_state={failed: True, error: ...})`
- **Emit flow detection** (lines 709-721): Check `emit.flow.yaml` in working_dir → `ExecutorResult(type="delegate", sub_job_def=...)`
- **Output extraction** (lines 724-737): `_extract_output()` based on `output_mode` → `HandoffEnvelope`
- **Session ID injection** (lines 727-731): Auto-inject `_session_id` if `continue_session` is set

Extract the failure + emit_flow + output sections into a standalone function:

```python
def build_agent_result(
    backend, agent_status, state, config, inputs
) -> ExecutorResult:
```

Parameters: `agent_status` (from `backend.wait()`), `state` (the executor_state dict with pid/pgid/paths), `config` (emit_flow flag, output_mode, output_fields, continue_session), `inputs` (for session ID passthrough). For re-adoption, `config` values can be reconstructed from `executor_state` which already stores `output_mode` and `output_file`.

Call this from both `AgentExecutor.start()` (replacing inline code) and `_monitor_orphaned_agent()`.

### Step 4: Add `recover_orphaned_agents()` to `AsyncEngine` (~45 min)

**File**: `src/stepwise/engine.py`, in `AsyncEngine` class (after `recover_jobs()` at line 2696)

New method:
```python
def recover_orphaned_agents(self) -> None:
```

Logic:
1. Import `is_process_alive` from `stepwise.agent`
2. Iterate `store.active_jobs()` where `created_by == "server"`
3. For each job, iterate `store.running_runs(job_id)`
4. For each run with `executor_state.get("pid")`:
   - Re-check PID liveness (may have died between cleanup and now)
   - If dead: fail the run directly
   - If alive: look up executor ref from `job.workflow.steps[run.step_name].executor`, create asyncio task via `_monitor_orphaned_agent()`, register in `self._tasks[run.id]`

### Step 5: Add `_monitor_orphaned_agent()` to `AsyncEngine` (~45 min)

**File**: `src/stepwise/engine.py`, in `AsyncEngine` class

New async method:
```python
async def _monitor_orphaned_agent(
    self, job_id, step_name, run_id, executor_state, exec_ref
):
```

Logic:
1. Acquire `self._agent_semaphore` (same as `_run_executor` — prevents thread pool exhaustion)
2. Reconstruct `AgentProcess(pid, pgid, output_path, working_dir, session_name)` from `executor_state`
3. Create an `AcpxBackend` instance (stateless — safe to construct fresh)
4. Run `backend.wait(process)` in the thread pool via `loop.run_in_executor()` — this blocks until the agent exits, handling the non-parent case via `/proc` polling
5. Call `build_agent_result()` (from Step 3) to convert `AgentStatus` → `ExecutorResult`
6. Push `("step_result", job_id, step_name, run_id, result)` to `self._queue`
7. On `asyncio.CancelledError`: return silently (job was cancelled)
8. On other exceptions: push `("step_error", ...)` to the queue
9. In `finally`: release `self._agent_semaphore`

The result flows through `_handle_queue_event()` at `engine.py:2935` which calls `_process_launch_result()` — the standard path. No special handling needed.

This follows the same pattern as `_run_executor()` (lines 2827-2898) but skips the spawn phase.

### Step 6: Wire recovery into server lifespan (~10 min)

**File**: `src/stepwise/server.py`, in `lifespan()`, after line 519

Add after `_engine_task = asyncio.create_task(_engine.run())`:
```python
_engine.recover_orphaned_agents()
```

This must be after the engine task is created so the queue is being consumed. The method itself creates asyncio tasks that push to the queue, which the engine task processes.

### Step 7: Write tests (~45 min)

**File**: `tests/test_server_reliability.py`

See Testing Strategy below.

### Step 8: Full regression run (~15 min)

```bash
uv run pytest tests/
cd web && npm run test
```

## Testing Strategy

### Test class: `TestH9OrphanedAgentPIDTracking`

All tests go in `tests/test_server_reliability.py`, following the existing pattern (direct store manipulation + function calls, no HTTP server needed).

#### T1: `test_alive_pid_step_survives_cleanup`
- Create a RUNNING job with a RUNNING step that has `executor_state={"pid": <current_pid>, "pgid": <current_pid>}`
- Patch `stepwise.server.is_process_alive` to return True (or use `os.getpid()` since the test process is alive)
- Run `_cleanup_zombie_jobs(store)`
- Assert: step still RUNNING, job still RUNNING, no SIGTERM sent

#### T2: `test_dead_pid_step_is_failed`
- Create a RUNNING job with a RUNNING step that has `executor_state={"pid": 999999999, "pgid": 999999999}`
- Patch `is_process_alive` to return False
- Run `_cleanup_zombie_jobs(store)`
- Assert: step FAILED with error containing "died during server restart", job FAILED

#### T3: `test_no_pid_step_is_failed`
- Create a RUNNING job with a RUNNING step, no `executor_state`
- Run `_cleanup_zombie_jobs(store)`
- Assert: step FAILED with "Server restarted: step was orphaned", job FAILED (backward compat with existing test `test_running_job_with_running_steps_is_failed`)

#### T4: `test_mixed_alive_and_dead_steps`
- Create a RUNNING job with a two-step workflow, both steps RUNNING
- One step has alive PID (patch `is_process_alive` selectively), one has dead PID
- Run `_cleanup_zombie_jobs(store)`
- Assert: alive step still RUNNING, dead step FAILED, job still RUNNING

#### T5: `test_recover_orphaned_agents_creates_task`
- Create an `AsyncEngine`, add a RUNNING job with a RUNNING step + alive PID in executor_state
- Mock `is_process_alive` to return True
- Call `engine.recover_orphaned_agents()`
- Assert: `run.id in engine._tasks`

#### T6: `test_monitor_completes_on_agent_exit`
- Integration test using mock backend
- Set up a RUNNING step with PID in executor_state
- Call `_monitor_orphaned_agent`, have the mock backend return a completed status
- Assert: engine queue receives the `step_result` event, step transitions to COMPLETED

### Existing test compatibility
- `test_running_job_with_running_steps_is_failed`: Still passes — the step has no `executor_state`, so no PID, so it gets failed as before.
- All other H7 tests: Unaffected — they test completed/failed/paused/CLI-owned/suspended jobs, not the running-step-with-PID case.

### Commands
```bash
# Run H9 tests specifically
uv run pytest tests/test_server_reliability.py::TestH9OrphanedAgentPIDTracking -v

# Run all server reliability tests
uv run pytest tests/test_server_reliability.py -v

# Full regression
uv run pytest tests/ -v
```

## Risks & Mitigations

### Risk 1: Re-adopted process completes before monitoring task starts
**Risk**: Between `_cleanup_zombie_jobs` (checks PID alive) and `recover_orphaned_agents()` (creates monitoring task), the agent process could exit.
**Mitigation**: `recover_orphaned_agents()` checks PID liveness again before creating the task. If dead by then, it fails the run directly. Additionally, `AcpxBackend.wait()` handles the case where the process is already gone — `_is_process_alive` returns False immediately, and it returns a completed status with exit_code=0.

### Risk 2: PID reuse (PID recycled by OS)
**Risk**: The original agent process died, a new unrelated process got the same PID, and `is_process_alive` returns True.
**Mitigation**: Low probability on modern Linux (PID space is large, typically 32768+, often 4194304). The monitoring task will wait for this unrelated process to exit, then fail to parse its output, resulting in a step failure with a clear error. This is still better than the current behavior (unconditionally killing processes, which could kill the wrong process with the same PID reuse scenario). Could additionally validate the process command line via `/proc/{pid}/cmdline` for extra safety, but the cost/benefit is marginal for v1.

### Risk 3: Agent process in zombie state
**Risk**: Agent process exited but wasn't reaped (zombie). `os.kill(pid, 0)` succeeds for zombies.
**Mitigation**: Using `is_process_alive()` from `agent.py` which checks `/proc/{pid}/status` for the `"Z"` (zombie) state. Zombies are treated as dead.

### Risk 4: Thread pool exhaustion from re-attached agents
**Risk**: Multiple re-adopted agents consume thread pool slots.
**Mitigation**: Re-attached tasks acquire the agent semaphore (`self._agent_semaphore`) before submitting to the thread pool, same as fresh launches in `_run_executor()`.

### Risk 5: Stuck step detector races with re-adoption
**Risk**: The engine's stuck step detector (`engine.py:2639-2658`) auto-fails RUNNING steps that have no entry in `self._tasks` after 60s. If `recover_orphaned_agents()` runs late, surviving agents could be auto-failed.
**Mitigation**: `recover_orphaned_agents()` is called in the same `lifespan()` startup sequence, immediately after `engine.run()` starts. The 60-second threshold provides ample margin. Additionally, `recover_orphaned_agents()` registers tasks synchronously (creating the asyncio Task immediately) before yielding, so there is no window.

### Risk 6: emit_flow file from before restart
**Risk**: Agent wrote `emit.flow.yaml` before the server crashed. On re-attach, when the agent completes, the result-building logic may or may not detect it.
**Mitigation**: The `build_agent_result` function checks for the emit file at the stored `working_dir` path, same as the normal path. If the agent already consumed/removed it before restart, no issue. If it's still there, delegation proceeds normally.

### Risk 7: Agent output stream monitor
**Risk**: The `_agent_stream_monitor()` task (`server.py:337-365`) tails NDJSON from running agent steps for WebSocket broadcast. Does it pick up re-adopted agents?
**Mitigation**: No change needed. The stream monitor discovers running agent steps via `store.running_runs()` on each iteration, and the output file path is in `executor_state`. It will automatically start tailing re-adopted agents.
