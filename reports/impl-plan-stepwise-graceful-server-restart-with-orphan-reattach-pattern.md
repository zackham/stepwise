---
title: "Implementation Plan: Graceful Server Restart with Orphan & Reattach Pattern"
date: "2026-03-22T00:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Graceful Server Restart with Orphan & Reattach Pattern

## Overview

Enable the stepwise server to restart without killing expensive agent processes. Agent subprocesses already run in their own process group (`start_new_session=True`), so they naturally survive server SIGTERM. The work centers on **reattaching** to still-running agents on restart (tailing their output, waiting for exit, processing results) and adding a `--now` flag for hard kill when needed.

## Requirements

### R1: Agent processes survive graceful server restart
- **Acceptance:** Start an agent step, run `stepwise server restart`, confirm the agent PID is still alive after the new server starts.
- Server shutdown must NOT kill agent process groups. Currently `_cleanup_zombie_jobs()` calls `os.killpg()` on dead-PID agents' pgids — this is fine. The key is that alive agents are skipped (already the case at `server.py:441-443`) and not killed elsewhere during shutdown.

### R2: Reattach to surviving agents on startup
- **Acceptance:** After restart, if an agent PID is alive, the engine creates a reattach task that tails the agent's NDJSON output file and waits for the process to exit. When it exits, the result is processed through the normal `_process_launch_result` path. The web UI shows live agent output streaming during the reattach.

### R3: Handle agent-finished-while-server-was-down
- **Acceptance:** If on restart the agent PID is dead but a valid output file exists at the path in `executor_state["output_file"]`, process the result from that file instead of failing the step.

### R4: PID reuse detection
- **Acceptance:** On restart, if a PID is alive but `/proc/{pid}/cmdline` doesn't contain `acpx` or `claude`, treat it as dead (PID was reused by an unrelated process). Fall through to R3 (check output file) or fail.

### R5: `--now` flag for hard kill
- **Acceptance:** `stepwise server stop --now` and `stepwise server restart --now` kill agent process groups via pgid before stopping the server, then send SIGKILL to the server.

### R6: Cheap steps cancelled on shutdown
- **Acceptance:** Non-agent running steps (script, LLM) have their engine tasks cancelled during `AsyncEngine.shutdown()`. This already happens via `task.cancel()` in the existing `shutdown()` method. No new work needed — just verify.

## Assumptions

### A1: `start_new_session=True` already used for agent spawning
- **Verified:** `agent.py:450-462` — `subprocess.Popen(..., start_new_session=True)`. Agent processes are already in their own session/process group and will survive server SIGTERM.

### A2: Agent output goes to files, not pipes
- **Verified:** `agent.py:450-462` — stdout/stderr redirected to files (`out_f`, `err_f`). Output path stored in `executor_state["output_file"]` and `executor_state["output_path"]`.

### A3: PID and executor_state persisted in database before agent completes
- **Verified:** `engine.py:2883-2893` — `update_state()` callback writes PID and full state dict to StepRun immediately after spawn, before blocking on `wait()`.

### A4: `executor_state` contains all info needed for reattach
- **Verified:** `agent.py:1017-1029` — state includes `pid`, `pgid`, `output_path`, `working_dir`, `session_name`, `output_mode`, `output_file`. This is sufficient to reconstruct the waiting and result-extraction logic.

### A5: `_tail_agent_output` already handles live NDJSON tailing
- **Verified:** `server.py:274-296` — asyncio task that seeks, reads, parses NDJSON, and broadcasts via WebSocket. Can be reused for reattached agents.

### A6: `AcpxBackend.wait()` works for non-child processes
- **Verified:** `agent.py:480-487` — catches `ChildProcessError` from `os.waitpid()` and falls back to polling `/proc/{pid}` until the process disappears. After restart, the agent is no longer a child process, so it will use the polling fallback.

### A7: Server stop sends SIGTERM only to the server PID
- **Verified:** `cli.py:739` — `os.kill(pid, signal.SIGTERM)` targets only the server PID, not the process group. Agent children in separate sessions are unaffected.

## Out of Scope

- **Agent state checkpointing/resumption** — agents are black boxes; we don't serialize their internal state.
- **Auto-retry of killed agents** — if an agent dies during restart, it fails cleanly. Users can manually retry.
- **systemd integration** — `KillMode=process` is a deployment concern, documented but not enforced by code.
- **Agent timeout enforcement across restarts** — if a step has a timeout decorator, the clock resets on reattach. Acceptable for now.
- **Multi-server coordination** — the global server registry (already shipped) prevents multiple instances per project.

## Architecture

### How reattach fits the existing event-driven model

The `AsyncEngine` processes results via its event queue (`_queue`). Today, `_run_executor()` dispatches `executor.start()` to the thread pool and pushes a `("step_result", ...)` event when done. Reattach creates a similar async task — but instead of calling `executor.start()`, it calls `backend.wait(process)` on the orphaned PID and then pushes the result through the same event queue.

```
[Restart] → _cleanup_zombie_jobs() identifies alive PIDs
         → engine._reattach_agent(run) creates asyncio task
         → task: backend.wait(process) blocks in thread pool
         → task: extract output, build ExecutorResult
         → task: push ("step_result", ...) to engine queue
         → engine processes result normally
```

The reattach task is registered in `self._tasks[run.id]` so that `_poll_external_changes()` doesn't flag it as stuck, and cancellation works if the job is cancelled.

### File-level changes

| File | Change |
|---|---|
| `server.py` | Modify `_cleanup_zombie_jobs()` to return reattach/recovery lists instead of skipping alive agents. Add reattach wiring in `lifespan()`. |
| `engine.py` | Add `_reattach_agent(run)` and `_recover_agent_output(run)` methods to AsyncEngine. Add `reattach_agents()` orchestration method. Add PID-alive guard in `_poll_external_changes()`. |
| `agent.py` | Add `reattach_and_wait()`, `process_completed_output()`, and `verify_agent_pid()` functions. |
| `cli.py` | Add `--now` flag to `server stop` and `server restart` subparsers. In `--now` mode, kill agent pgids then SIGKILL the server. |
| `store.py` | No changes — `executor_state` already stores all required fields. |
| `models.py` | No changes — `pid` and `executor_state` already exist on StepRun. |

## Implementation Steps

### Step 1: Add reattach helpers to agent.py (~30 min)

Add three module-level functions:

**`verify_agent_pid(pid: int, expected_pgid: int | None = None) -> bool`**
- Read `/proc/{pid}/cmdline`, check if any component contains `acpx`, `claude-agent`, or `claude-code`
- If `expected_pgid` is set, verify `os.getpgid(pid) == expected_pgid` (catches PID reuse where new process has different pgid)
- Return False if `/proc/{pid}` doesn't exist or cmdline doesn't match

**`reattach_and_wait(executor_state: dict, step_def: StepDefinition, context: ExecutionContext) -> ExecutorResult`**
- Reconstruct `AgentProcess` from `executor_state` fields (pid, pgid, output_path, working_dir, session_name, exec_mode)
- Instantiate `AcpxBackend` with agent name from `step_def.executor.config`
- Call `backend.wait(process)` — blocks until exit
- Determine `output_mode` from `executor_state.get("output_mode", "effect")`
- Check for emitted flow file (reuse logic from `AgentExecutor.start()` lines 1073-1085)
- Call `AgentExecutor._extract_output()` (make it a static/classmethod or extract to module function)
- Build and return `ExecutorResult`

**`process_completed_output(executor_state: dict, step_def: StepDefinition, context: ExecutionContext) -> ExecutorResult`**
- Same as above but skip the `wait()` — assume exit_code=0
- Read output file directly, build artifact
- If output file doesn't exist or is empty, return failure result

Refactor `_extract_output()` from a method on `AgentExecutor` to a module-level function (or make it static) so `reattach_and_wait` can call it without instantiating the full executor.

### Step 2: Modify `_cleanup_zombie_jobs()` in server.py (~45 min)

Change the return type to `tuple[list[StepRun], list[StepRun]]` — `(reattach_runs, recovery_runs)`.

For each running run in a server-owned job:

1. **PID alive + passes `verify_agent_pid()`**: add to `reattach_runs`, do NOT fail the run. Log "Agent PID {pid} alive, will reattach".
2. **PID alive + fails verification**: treat as dead PID (PID reuse). Log warning. Fall through to check 3.
3. **PID dead + output file exists and has content**: add to `recovery_runs`. Log "Agent PID {pid} dead but output exists, will recover".
4. **PID dead + no output file**: fail the run (existing behavior). Kill pgid if applicable.

To check output file existence (check 3): look at `run.executor_state.get("output_file")` or `run.executor_state.get("output_path")`. Use `os.path.isfile()` and `os.path.getsize() > 0`.

Job-level logic after processing runs:
- If reattach_runs or recovery_runs contain entries for this job: leave job RUNNING (engine will handle)
- If all runs are failed and job looks complete: mark COMPLETED (existing recovery)
- Otherwise: mark FAILED (existing behavior)

### Step 3: Add `_reattach_agent()` to AsyncEngine (~45 min)

Add to `AsyncEngine` class in `engine.py`:

**`async def _reattach_agent(self, run: StepRun) -> None`**
1. Load job from store
2. Get step_def from `job.workflow.steps[run.step_name]`
3. Build `ExecutionContext` from run metadata (workspace_path from executor_state["working_dir"], step_name, attempt)
4. Create async coroutine that:
   - Calls `reattach_and_wait(run.executor_state, step_def, ctx)` in thread pool via `asyncio.to_thread()`
   - On success: push `("step_result", job_id, step_name, run_id, result)` to `self._queue`
   - On exception: push `("step_error", job_id, step_name, run_id, error)` to `self._queue`
5. Create `asyncio.Task` from coroutine
6. Register in `self._tasks[run.id]`

**`async def _recover_agent_output(self, run: StepRun) -> None`**
1. Same setup as above
2. Call `process_completed_output()` — no blocking, runs directly
3. Push result to queue

**`def reattach_agents(self, reattach_runs: list[StepRun], recovery_runs: list[StepRun]) -> None`**
- For each run in `reattach_runs`: create task via `_reattach_agent(run)`
- For each run in `recovery_runs`: create task via `_recover_agent_output(run)`
- Log counts

### Step 4: Wire reattach into server lifespan (~20 min)

In `server.py:lifespan()`:

Change line 648 from:
```python
_cleanup_zombie_jobs(store)
```
To:
```python
reattach_runs, recovery_runs = _cleanup_zombie_jobs(store)
```

After `_engine_task = asyncio.create_task(_engine.run())` (line 670), add:
```python
if reattach_runs or recovery_runs:
    _engine.reattach_agents(reattach_runs, recovery_runs)
```

The `_agent_stream_monitor()` will automatically pick up reattached runs for NDJSON tailing since they're still RUNNING with `executor_state["output_file"]` set — no changes needed.

### Step 5: Guard `_poll_external_changes()` against alive agents (~15 min)

In `engine.py:_poll_external_changes()`, before failing a stuck run (lines 2670-2683), add a PID-alive check:

```python
# Don't fail agent steps whose process is still alive
# (covers edge case where reattach task wasn't created)
if run.executor_state and run.executor_state.get("pid"):
    from stepwise.agent import verify_agent_pid
    pid = run.executor_state["pid"]
    pgid = run.executor_state.get("pgid")
    if verify_agent_pid(pid, pgid):
        continue
```

This provides defense-in-depth: even if reattach somehow missed a run, we won't fail it as long as the agent is alive.

### Step 6: Add `--now` flag to CLI (~30 min)

In `cli.py`:

1. Add `--now` argument to `stop` and `restart` subparsers
2. Add `_hard_stop_server(dot_dir, io)` function:
   - Open `SQLiteStore(dot_dir / "stepwise.db")` read-only
   - Collect pgids from all running step runs' `executor_state`
   - Close store
   - For each pgid: `os.killpg(pgid, signal.SIGTERM)`
   - Wait 2s for processes to exit
   - For stubborn pgids: `os.killpg(pgid, signal.SIGKILL)`
   - Then send SIGKILL to server PID (skip SIGTERM grace period)
   - Remove pidfile

3. In `_server_stop()`: if `args.now`, call `_hard_stop_server()` instead of `_stop_server_for_project()`
4. In `_server_restart()`: pass `args` through so `--now` applies to the stop phase

## Testing Strategy

### Unit tests

**File:** `tests/test_agent_reattach.py`

1. **`test_verify_agent_pid_match`** — spawn a subprocess with a known command (e.g., `python -c "import time; time.sleep(60)"`), verify `verify_agent_pid()` returns False (not acpx). Then test with a mock `/proc` entry if feasible, or just test the negative case.

2. **`test_verify_agent_pid_dead`** — pass a PID that doesn't exist, assert returns False.

3. **`test_process_completed_output_valid`** — create a tempfile with valid JSON output, construct `executor_state` pointing at it, call `process_completed_output()`, assert `ExecutorResult.type == "data"` and artifact matches JSON.

4. **`test_process_completed_output_missing_file`** — nonexistent output file path, assert returns failure result.

### Integration tests

**File:** `tests/test_graceful_restart.py`

5. **`test_cleanup_zombie_jobs_alive_pid_returns_reattach`** — create a store with a RUNNING step run whose PID is a live `sleep` process. Mock `verify_agent_pid` to return True. Call `_cleanup_zombie_jobs()`. Assert run appears in `reattach_runs` and is still RUNNING in store.

6. **`test_cleanup_zombie_jobs_dead_pid_with_output`** — RUNNING run, dead PID, valid output file. Assert appears in `recovery_runs`.

7. **`test_cleanup_zombie_jobs_dead_pid_no_output`** — PID dead, no output. Assert run is FAILED.

8. **`test_cleanup_zombie_jobs_pid_reuse`** — PID alive, `verify_agent_pid` returns False. Assert treated as dead (checks output file or fails).

9. **`test_reattach_creates_task`** — use `async_engine` fixture. Create a RUNNING step run with executor_state. Call `engine.reattach_agents([run], [])`. Assert `run.id in engine._tasks`.

10. **`test_poll_external_changes_skips_alive_agent`** — RUNNING step with executor_state containing alive PID, no task registered. Assert `_poll_external_changes` does NOT fail the run.

### Commands

```bash
uv run pytest tests/test_agent_reattach.py -v
uv run pytest tests/test_graceful_restart.py -v
uv run pytest tests/ -v  # full suite regression check
```

### Manual E2E test

1. Start server, run a flow with an agent step, run `stepwise server restart`, verify agent PID survives, verify web UI reconnects and shows live output, verify result is processed after agent completes.
2. Same but kill the agent while server is down. Restart server, verify step is FAILED with clear error.
3. Test `stepwise server restart --now` kills agent processes.

## Risks & Mitigations

### Risk 1: PID reuse false positives
- **Impact:** Reattach to wrong process, wait forever or get garbage result
- **Mitigation:** `verify_agent_pid()` checks `/proc/{pid}/cmdline` for acpx/claude AND compares pgid. Both must match.
- **Residual:** Extremely unlikely race where a new acpx process gets the same PID and pgid.

### Risk 2: Reattach task and engine startup ordering
- **Impact:** Reattach tasks created before engine event loop is running
- **Mitigation:** Create reattach tasks AFTER `asyncio.create_task(_engine.run())` in lifespan. The engine loop is running by the time `reattach_agents()` is called.

### Risk 3: Agent completes exactly during server restart window
- **Impact:** NDJSON output file written, PID gone, but output not yet processed
- **Mitigation:** R3 handles this — dead PID + valid output file = process result from file.

### Risk 4: Queue owner cleanup kills active session's queue owner
- **Impact:** Session state lost, agent fails
- **Mitigation:** Existing `_collect_active_agent_info()` already protects alive sessions. Reattached runs remain RUNNING with executor_state intact, so their session_ids and PIDs stay in the active set.

### Risk 5: `_poll_external_changes` races with reattach task creation
- **Impact:** RUNNING step with no task gets flagged as stuck before reattach runs
- **Mitigation:** Step 5 adds PID-alive check before failing stuck runs. Also, reattach happens at startup before the first poll cycle fires.

### Risk 6: Thread pool exhaustion during reattach
- **Impact:** Reattached agents consume thread pool workers for blocking `wait()` calls
- **Mitigation:** Thread pool default is 32 workers. Agent concurrency capped at 3. Worst case = 6 workers (3 reattached + 3 new). Note: `_agent_semaphore` is NOT acquired for reattach since the agent is already running — this is intentional.

### Risk 7: `_extract_output` refactoring breaks existing tests
- **Impact:** Moving from instance method to module function could break callers
- **Mitigation:** Keep the instance method as a thin wrapper that calls the module function. No external callers change.
