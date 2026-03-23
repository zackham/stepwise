---
title: "Implementation Plan: Graceful Server Restart with Orphan & Reattach"
date: "2026-03-22T00:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Graceful Server Restart with Orphan & Reattach

## Overview

Enable the stepwise server to restart without killing running agent processes, then reattach to them on startup. Agent subprocesses already run in their own session (`start_new_session=True` at `agent.py:463`), so they survive server SIGTERM. The work is: (1) verify surviving agent PIDs on restart and reattach to them, (2) recover results from agents that finished while the server was down, (3) detect PID reuse, and (4) add a `--now` flag for hard-kill restarts.

## Requirements

### R1: Agent processes survive graceful server restart
- **Acceptance:** Start an agent step, run `stepwise server restart`, confirm the agent PID is still alive after the new server starts.
- **Verification path:** `_cleanup_zombie_jobs()` (`server.py:415-481`) already skips alive PIDs at line 440-443. Shutdown via `lifespan()` (`server.py:675-709`) cancels engine tasks but doesn't kill process groups. Server stop uses `os.kill(pid, signal.SIGTERM)` on server PID only (`cli.py:739`). No code path sends signals to agent pgids during graceful shutdown.
- **Work needed:** Minimal — verify no regressions, add PID cmdline verification (R4) to replace naive `os.kill(pid, 0)`.

### R2: Reattach to surviving agents on startup
- **Acceptance:** After restart, if an agent PID is alive and verified, the engine creates a reattach task that blocks on `backend.wait(process)` in the thread pool, tails NDJSON output via the existing `_agent_stream_monitor()`, and pushes results through the normal `("step_result", ...)` queue path. Web UI shows live agent output.
- **Work needed:** New `_reattach_agent()` method on `AsyncEngine`, new `reattach_and_wait()` in `agent.py`, wiring in `lifespan()`.

### R3: Handle agent-finished-while-server-was-down
- **Acceptance:** On restart, if agent PID is dead but `executor_state["output_file"]` or `executor_state["output_path"]` points to a valid output file, process the result from that file. Step completes normally.
- **Work needed:** New `process_completed_output()` in `agent.py`, new `_recover_agent_output()` on `AsyncEngine`.

### R4: PID reuse detection
- **Acceptance:** On restart, if a PID is alive but `/proc/{pid}/cmdline` doesn't contain `acpx` or `claude`, treat it as dead. Fall through to R3 or fail.
- **Work needed:** New `verify_agent_pid()` function in `agent.py`.

### R5: `--now` flag for hard kill
- **Acceptance:** `stepwise server stop --now` and `stepwise server restart --now` kill agent process groups before stopping the server, then SIGKILL the server.
- **Work needed:** New `--now` arg on unified `server` subparser, new `_hard_stop_server()` function in `cli.py`.

### R6: Cheap steps cancelled on shutdown (verify only)
- **Acceptance:** Non-agent running steps have their tasks cancelled during `AsyncEngine.shutdown()` (`engine.py:2624-2630`). Already implemented via `task.cancel()`. No new work — test only.

## Assumptions

### A1: `start_new_session=True` used for agent spawning
- **Verified:** `agent.py:463` — `subprocess.Popen(..., start_new_session=True)`. Agents are in their own session and survive server SIGTERM.

### A2: Agent output goes to files, not pipes
- **Verified:** `agent.py:450-462` — stdout/stderr redirected to file handles (`out_f`, `err_f`). Output path persisted in `executor_state["output_path"]` and `executor_state["output_file"]` at lines 1023-1034.

### A3: PID and executor_state persisted before agent completes
- **Verified:** `engine.py:2883-2894` — `update_state()` callback writes PID and full state via `call_soon_threadsafe()` immediately after spawn, before blocking on `wait()`. State is in the DB by the time the server restarts.

### A4: `executor_state` contains all info needed for reattach
- **Verified:** `agent.py:1023-1034` — state dict includes `pid`, `pgid`, `output_path`, `working_dir`, `session_name`, `output_mode`, `output_file`. Sufficient to reconstruct `AgentProcess` and call `backend.wait()`.

### A5: `_agent_stream_monitor` auto-discovers running runs for NDJSON tailing
- **Verified:** `server.py:335-364` — polls `store.running_runs(job.id)` every 1s, starts `_tail_agent_output()` for any run with `executor_state["output_path"]` not already in `_stream_tasks`. Reattached runs will be auto-discovered since they remain RUNNING with output_path set.

### A6: `AcpxBackend.wait()` works for non-child processes
- **Verified:** `agent.py:480-495` — catches `ChildProcessError` from `os.waitpid()` and falls back to polling `_is_process_alive()` (checks `/proc/{pid}/status`) at 0.5s intervals. After restart, agent is not a child, so this fallback activates automatically.

### A7: Server stop targets only server PID
- **Verified:** `cli.py:739` — `os.kill(pid, signal.SIGTERM)` sends to server PID only, not process group. Agent children in separate sessions are unaffected.

### A8: `_extract_output` uses `self` only for `stream_result` mode
- **Verified:** `agent.py:1269-1332` — the only `self` access is `self.backend._extract_final_text()` at line 1310-1311 (stream_result case). For reattach, we can pass `backend` as a parameter or handle stream_result as a special case.

## Out of Scope

- **Agent state checkpointing/resumption** — agents are opaque processes; we don't serialize internal state.
- **Auto-retry of killed agents** — if an agent dies during restart, it fails. Users can manually retry.
- **Timeout enforcement across restarts** — TimeoutDecorator clock resets on reattach. Acceptable for now.
- **systemd integration** — `KillMode=process` is a deployment concern, not enforced by code.
- **Multi-server coordination** — global server registry prevents multiple instances per project.

## Architecture

### How reattach fits the event-driven model

Today: `_run_executor()` (`engine.py:2853-2926`) dispatches `executor.start()` to thread pool, pushes `("step_result", ...)` to `self._queue`, `_handle_queue_event()` (`engine.py:2959`) routes to `_process_launch_result()` (`engine.py:1360`).

Reattach follows the same pattern but replaces `executor.start()` with `reattach_and_wait()`:

```
server startup -> _cleanup_zombie_jobs() returns reattach/recovery lists
              -> engine._reattach_agent(run) creates async task
              -> task: backend.wait(process) blocks in thread pool
              -> task: extract_agent_output() builds ExecutorResult
              -> task: pushes ("step_result", ...) to engine queue
              -> engine processes result via normal path
```

The reattach task is registered in `self._tasks[run.id]` so:
1. `_poll_external_changes()` doesn't flag it as stuck (line 2668 check)
2. `cancel_job()` can cancel it (line 2737)
3. `shutdown()` cancels it (line 2627-2628)

### No new dependencies between modules

All new code respects the module DAG: `models -> executors -> engine -> server`. The `agent.py` additions are module-level functions that take dicts/dataclasses as input. `engine.py` imports from `agent.py` (already does via agent executor registration). No circular imports introduced.

## Implementation Steps

### Step 1: Add `verify_agent_pid()` to agent.py (~15 min)

Add a module-level function near `_is_process_alive()` at line 592:

```python
def verify_agent_pid(pid: int, expected_pgid: int | None = None) -> bool:
```

Logic:
1. Read `/proc/{pid}/cmdline` (null-separated). If missing, return False.
2. Check if any component contains `acpx`, `claude-agent`, or `claude-code`.
3. If `expected_pgid` is set, verify `os.getpgid(pid) == expected_pgid`. Catches PID reuse where new process has different pgid.
4. Return True only if cmdline matches AND pgid matches (when provided).

Make it a module-level function (not on `AcpxBackend`) since it's used by both `server.py` and `engine.py`.

### Step 2: Extract `_extract_output` to module function (~20 min)

Refactor `AgentExecutor._extract_output()` (`agent.py:1269-1332`) to a module-level function:

```python
def extract_agent_output(state: dict, output_mode: str,
                         agent_status: AgentStatus,
                         backend: AgentBackend | None = None) -> HandoffEnvelope:
```

The only `self` usage is `self.backend._extract_final_text()` for `stream_result` mode (line 1310). Pass `backend` as an optional parameter. Keep the instance method as a one-line wrapper:

```python
def _extract_output(self, state, output_mode, agent_status):
    return extract_agent_output(state, output_mode, agent_status, self.backend)
```

No callers outside `AgentExecutor` change. Only 2 internal call sites: `start()` at line 1093 and `check_status()` at line 1204.

### Step 3: Add `reattach_and_wait()` and `process_completed_output()` to agent.py (~30 min)

**`reattach_and_wait(executor_state, step_def, context)`** — for R2 (alive agent):
1. Reconstruct `AgentProcess` from `executor_state` fields (pid, pgid, output_path, working_dir, session_name). Use `AgentProcess` dataclass directly (`agent.py:44-53`).
2. Create `AcpxBackend` instance (only needs `wait()` which uses `_is_process_alive` and `_completed_status`).
3. Call `backend.wait(process)` — blocks until exit. The `ChildProcessError` fallback handles non-child PIDs.
4. Read `output_mode` from `executor_state.get("output_mode", "effect")`.
5. Check for emitted flow file (reuse pattern from lines 1078-1090).
6. Call `extract_agent_output()` (from Step 2) to build envelope.
7. Handle `continue_session` by injecting `_session_id` into artifact if `session_name` is set.
8. Return `ExecutorResult(type="data", envelope=..., executor_state=state)`. For emit_flow, return delegate result.

**`process_completed_output(executor_state, step_def, context)`** — for R3 (dead agent with output):
1. Same setup but skip `wait()`.
2. Build `AgentStatus(state="completed", exit_code=0, session_id=state.get("session_id"))`.
3. Call `extract_agent_output()` with this synthetic status.
4. If output file missing/empty and output_mode is "file", return failure `ExecutorResult`.
5. Return `ExecutorResult`.

### Step 4: Modify `_cleanup_zombie_jobs()` in server.py (~30 min)

Change signature from `-> None` to `-> tuple[list[StepRun], list[StepRun]]` returning `(reattach_runs, recovery_runs)`.

Replace the alive-PID handling at lines 438-449. For alive PIDs: call `verify_agent_pid()` — if True, add to `reattach_runs`; if False (PID reuse), fall through to dead-PID handling. For dead PIDs: before killing pgid, check if output file exists and has content; if so, add to `recovery_runs` instead of failing.

Job-level logic update: if any run for this job is in reattach_runs or recovery_runs, leave job RUNNING (engine will handle via reattach tasks).

Initialize `reattach_runs, recovery_runs = [], []` at the top, return them at the end.

### Step 5: Add reattach methods to AsyncEngine (~45 min)

Add to `AsyncEngine` class in `engine.py`:

**`async def _reattach_agent(self, run: StepRun) -> None`** — follows the `_run_executor()` pattern (lines 2853-2926):
1. Load job and step_def from store.
2. Build `ExecutionContext` from run metadata.
3. Import `reattach_and_wait` from `stepwise.agent`.
4. Submit to thread pool via `loop.run_in_executor(self._executor_pool, ...)`.
5. On success: push `("step_result", ...)` to queue.
6. On exception: push `("step_error", ...)` to queue.
7. No agent semaphore acquisition (agent is already running).

**`async def _recover_agent_output(self, run: StepRun) -> None`** — same structure but calls `process_completed_output()` directly (fast I/O, no thread pool needed). Pushes result to queue.

**`def reattach_agents(self, reattach_runs, recovery_runs) -> None`** — creates async tasks via `asyncio.create_task()` for each run, registers in `self._tasks[run.id]`. Called after engine event loop is running.

### Step 6: Wire reattach into server lifespan (~10 min)

In `server.py:lifespan()`, change line 648 to capture return values from `_cleanup_zombie_jobs()`. After `_engine_task` creation (line 670), call `_engine.reattach_agents(reattach_runs, recovery_runs)` if either list is non-empty.

The `_agent_stream_monitor()` auto-discovers reattached runs for NDJSON tailing since they remain RUNNING with `executor_state["output_path"]` set. No changes needed.

### Step 7: Guard `_poll_external_changes()` (~10 min)

In `engine.py:_poll_external_changes()`, before the stuck-run failure logic at line 2670, add a PID-alive check:

If run has `executor_state["pid"]` and `verify_agent_pid()` returns True, `continue` (skip failing it). This prevents falsely failing a run during the brief window between engine startup and reattach task registration.

### Step 8: Add `--now` flag to CLI (~30 min)

1. Add `--now` argument to the unified `server` subparser (`cli.py:3745-3750`).
2. Add `_hard_stop_server(dot_dir, io)` function: reads pgids from running step runs in DB, kills pgids with SIGTERM then SIGKILL, SIGKILLs server PID, removes pidfile.
3. In `_server_stop()`: if `args.now`, call `_hard_stop_server()` instead of `_stop_server_for_project()`.
4. `_server_restart()` passes `args` through, so `--now` flows naturally.

## Testing Strategy

### Unit tests — `tests/test_agent_reattach.py`

1. **`test_verify_agent_pid_dead`** — nonexistent PID returns False.
2. **`test_verify_agent_pid_wrong_cmdline`** — spawn `sleep 60`, verify returns False (not acpx/claude).
3. **`test_verify_agent_pid_pgid_mismatch`** — alive PID, correct cmdline mock, wrong pgid returns False.
4. **`test_extract_agent_output_file_mode`** — create temp JSON file, build state dict, call `extract_agent_output()` with mode="file" and synthetic `AgentStatus`. Assert artifact matches JSON.
5. **`test_extract_agent_output_effect_mode`** — mode="effect", assert artifact contains session_id.
6. **`test_process_completed_output_valid`** — valid output file, assert `ExecutorResult.type == "data"` with correct artifact.
7. **`test_process_completed_output_missing_file`** — nonexistent output file, assert failure result.

### Integration tests — `tests/test_graceful_restart.py`

8. **`test_cleanup_returns_reattach_for_alive_pid`** — RUNNING run with alive PID, mock `verify_agent_pid` True. Assert run in `reattach_runs`, still RUNNING.
9. **`test_cleanup_returns_recovery_for_dead_pid_with_output`** — dead PID, valid output file. Assert in `recovery_runs`.
10. **`test_cleanup_fails_dead_pid_no_output`** — dead PID, no output. Assert FAILED.
11. **`test_cleanup_handles_pid_reuse`** — alive PID, `verify_agent_pid` False. Assert treated as dead.
12. **`test_reattach_creates_task_and_registers`** — `async_engine` fixture, call `reattach_agents([run], [])`. Assert `run.id in engine._tasks`.
13. **`test_poll_external_changes_skips_alive_agent`** — RUNNING run with alive PID, no task, mock verify True. Assert NOT failed.
14. **`test_poll_external_changes_fails_dead_untracked`** — RUNNING run, dead PID, no task, age > 60s. Assert FAILED.

### Commands

```bash
uv run pytest tests/test_agent_reattach.py -v
uv run pytest tests/test_graceful_restart.py -v
uv run pytest tests/ -v  # full regression
```

### Manual E2E test

1. Start server, run flow with agent step, `stepwise server restart`, verify agent PID survives, web UI reconnects with live output, result processed after agent completes.
2. Kill agent while server is down, restart, verify step FAILED with clear error.
3. `stepwise server restart --now` kills agent processes.

## Risks & Mitigations

### Risk 1: PID reuse false positives
- **Impact:** Reattach to wrong process, wait forever or get garbage.
- **Mitigation:** `verify_agent_pid()` checks both `/proc/{pid}/cmdline` for acpx/claude AND compares pgid. Both must match.
- **Residual:** Astronomically unlikely race: new acpx gets same PID + same pgid.

### Risk 2: Reattach task and engine startup ordering
- **Impact:** Reattach tasks created before engine event loop is running.
- **Mitigation:** `reattach_agents()` called AFTER `asyncio.create_task(_engine.run())` in lifespan. The `_loop` is set by `run()` (line 2641) before any queue processing.

### Risk 3: Agent completes exactly during restart window
- **Impact:** Output written, PID gone, result not processed.
- **Mitigation:** R3 handles this — dead PID + valid output file triggers `process_completed_output()`.

### Risk 4: `_poll_external_changes` races with reattach
- **Impact:** RUNNING run flagged as stuck before reattach task created.
- **Mitigation:** (1) Step 7 adds PID-alive guard. (2) First poll cycle fires after 5s timeout (`engine.py:2644`), by which time `reattach_agents()` has registered tasks.

### Risk 5: Thread pool exhaustion
- **Impact:** Reattached `wait()` calls consume thread pool workers.
- **Mitigation:** Pool default is 32 workers (line 2611). Agent concurrency cap is 3. Worst case: 6 workers (3 reattached + 3 new). Agent semaphore NOT acquired for reattach (intentional — agent is already running).

### Risk 6: `_extract_output` refactoring breaks callers
- **Impact:** Moving from instance method to module function could break tests.
- **Mitigation:** Keep instance method as thin wrapper. Only 2 internal call sites: `start()` at line 1093 and `check_status()` at line 1204.

### Risk 7: Output file partial write
- **Impact:** Agent crashed mid-write, corrupt JSON.
- **Mitigation:** `extract_agent_output()` already retries once after 0.1s delay (lines 1297-1306). If still corrupt, returns `{"status": "completed", "output_file_missing": True}` which routes through normal failure handling.
