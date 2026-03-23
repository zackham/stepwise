---
title: "Implementation Plan: Graceful Server Restart with Orphan & Reattach Pattern"
date: "2026-03-22T00:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Graceful Server Restart with Orphan & Reattach Pattern

## Overview

Enable the stepwise server to restart without killing running agent processes, then reattach to those agents on startup. Agent subprocesses already run in their own process group (`start_new_session=True` at `agent.py:459`), so they survive SIGTERM. The work centers on (1) classifying orphaned runs at startup into reattach/recover/fail buckets, (2) creating async tasks that wait for surviving agents and push results through the engine's normal event queue, and (3) adding a `--now` flag for hard-kill scenarios.

## Requirements

### R1: Agent processes survive graceful server restart
- **Acceptance:** Start an agent step → `stepwise server restart` → agent PID is still alive after new server starts.
- **Verified safe:** `_stop_server_for_project()` at `cli.py:739` sends SIGTERM only to the server PID. `shutdown()` at `engine.py:2624-2630` cancels asyncio Tasks but does not kill OS process groups. Agents in separate sessions are untouched.

### R2: Reattach to surviving agents on startup
- **Acceptance:** After restart, if agent PID is alive and passes verification, the engine creates a reattach task that blocks on `backend.wait()` then pushes the result through `_queue` as a `step_result` event. The `_agent_stream_monitor()` loop automatically tails the NDJSON output file (it already starts tailers for any RUNNING run with `output_path` in `executor_state` — `server.py:342-348`).

### R3: Handle agent-finished-while-server-was-down
- **Acceptance:** On restart, if agent PID is dead but the NDJSON output file exists at `executor_state["output_path"]`, extract the result from that file and process it through the normal completion path.

### R4: PID reuse detection
- **Acceptance:** If a PID is alive but `/proc/{pid}/cmdline` doesn't contain `acpx` or `claude`, treat it as dead. Fall through to R3 (check output file) or fail.

### R5: `--now` flag for hard kill
- **Acceptance:** `stepwise server stop --now` and `stepwise server restart --now` kill agent process groups via their pgids, then SIGKILL the server.

### R6: Cheap steps cancelled on shutdown (no new work)
- **Acceptance:** Non-agent RUNNING steps have their engine tasks cancelled during `AsyncEngine.shutdown()` at `engine.py:2627-2628`. Already implemented.

## Assumptions (verified against code)

| # | Assumption | Verification |
|---|---|---|
| A1 | Agents spawn in own session | `agent.py:459` — `subprocess.Popen(..., start_new_session=True)` |
| A2 | Agent output goes to files | `agent.py:451-462` — stdout/stderr → files; path in `executor_state["output_path"]` and `["output_file"]` |
| A3 | PID/state persisted before completion | `engine.py:2883-2893` — `update_state()` callback writes to DB immediately after spawn |
| A4 | `executor_state` has all reattach info | `agent.py:1019-1029` — contains `pid`, `pgid`, `output_path`, `working_dir`, `session_name`, `output_mode`, `output_file` |
| A5 | Stream monitor auto-tails RUNNING runs | `server.py:342-348` — starts tailer for any RUNNING run with `output_path` |
| A6 | `AcpxBackend.wait()` handles non-child PIDs | `agent.py:484-488` — catches `ChildProcessError`, falls back to polling `/proc/{pid}` |
| A7 | Server stop targets only server PID | `cli.py:739` — `os.kill(pid, signal.SIGTERM)`, not `os.killpg()` |

## Out of Scope

- Agent internal state checkpointing — agents are black boxes
- Auto-retry of killed agents — dead agents fail cleanly, users retry manually
- systemd `KillMode=process` — documented but not enforced
- Timeout enforcement across restarts — clock resets on reattach (acceptable)
- Multi-server coordination — already prevented by PID file registry

## Architecture

### Reattach flow through the existing event-driven model

```
[Server restart]
  → _cleanup_zombie_jobs(store) returns (reattach_runs, recovery_runs)
  → engine starts: asyncio.create_task(_engine.run())
  → _engine.reattach_agents(reattach_runs, recovery_runs)
      ├── for reattach: asyncio.create_task(_reattach_agent(run))
      │     → asyncio.to_thread(backend.wait(process))  # blocks in thread pool
      │     → _queue.put(("step_result", ...))           # normal event path
      └── for recovery: asyncio.create_task(_recover_agent_output(run))
            → read output file, build ExecutorResult
            → _queue.put(("step_result", ...))

  → _agent_stream_monitor() auto-starts NDJSON tailers for RUNNING runs
  → _handle_queue_event("step_result") → _process_launch_result() → normal completion
```

Reattach tasks register in `self._tasks[run.id]` so:
1. `_poll_external_changes()` doesn't flag them as stuck (the 60s timeout check at `engine.py:2668-2683`)
2. `cancel_job()` can cancel them if the job is cancelled
3. `shutdown()` cancels them on next server stop

### Key design decisions

- **No agent semaphore acquisition for reattach** — the agent is already running, acquiring the semaphore would block new launches unnecessarily. The semaphore governs _spawning_, not _waiting_.
- **`_extract_output()` refactored to module function** — called by both `AgentExecutor.start()` and the reattach path. The instance method becomes a thin wrapper.
- **Recovery (R3) is synchronous** — no `wait()` needed, just read the output file and push result to queue. Runs as a quick asyncio task (no thread pool needed).

## Implementation Steps

### Step 1: Add `verify_agent_pid()` to agent.py (~15 min)

Add module-level function after the `AgentProcess` dataclass (around line 55):

```python
def verify_agent_pid(pid, expected_pgid=None):
```

Implementation:
- Read `/proc/{pid}/cmdline` (null-byte separated), check if any component contains `acpx`, `claude-agent`, or `claude-code`
- If `expected_pgid` set, verify `os.getpgid(pid) == expected_pgid`
- Return `False` if `/proc/{pid}` doesn't exist, cmdline doesn't match, or pgid mismatch
- Catch `FileNotFoundError`, `ProcessLookupError`, `PermissionError` → return `False`

### Step 2: Extract `_extract_output()` to module function in agent.py (~20 min)

Create `extract_agent_output(state, output_mode, agent_status, backend=None) -> HandoffEnvelope` at module level. Contains the current logic from `AgentExecutor._extract_output()` at lines 1265-1328.

The `backend` parameter is needed for the `"stream_result"` mode which calls `backend._extract_final_text()` at line 1307.

The existing `AgentExecutor._extract_output()` becomes:
```python
return extract_agent_output(state, output_mode, agent_status, self.backend)
```

No external callers change.

### Step 3: Add `reattach_and_wait()` and `process_completed_output()` to agent.py (~30 min)

**`reattach_and_wait(executor_state, step_def, context) -> ExecutorResult`:**
1. Reconstruct `AgentProcess` from `executor_state` fields (pid, pgid, output_path, working_dir, session_name, exec_mode)
2. Instantiate `AcpxBackend` with agent name from `step_def.executor.config.get("agent", "claude-code")`
3. Call `backend.wait(process)` — blocks until exit (caller uses `asyncio.to_thread()`)
4. Clean up queue owner via `backend.cleanup_session_queue_owner()` if not exec_mode (mirrors `AgentExecutor.start()` lines 1038-1041)
5. Determine `output_mode` from `executor_state.get("output_mode", "effect")`
6. Call `extract_agent_output(executor_state, output_mode, agent_status, backend)`
7. Check for emitted flow if `step_def.executor.config.get("emit_flow")` (reuse pattern from lines 1075-1086)
8. Inject `_session_id` if `step_def.executor.config.get("continue_session")` and session_name exists (pattern from lines 1092-1096)
9. Build and return `ExecutorResult(type="data", envelope=..., executor_state=updated_state)`
10. On agent failure: return failure result matching pattern at lines 1057-1072

**`process_completed_output(executor_state, step_def, context) -> ExecutorResult`:**
- Construct `AgentStatus(state="completed", exit_code=0)` with session_id/cost extracted via `AcpxBackend._extract_session_id()` and `_extract_cost()` from the output file
- Call `extract_agent_output()` to build envelope
- If output file doesn't exist: return failure `ExecutorResult` with `executor_state["failed"] = True`
- Same emit_flow and session_id injection as above

### Step 4: Modify `_cleanup_zombie_jobs()` in server.py (~45 min)

Change signature to `_cleanup_zombie_jobs(store) -> tuple[list[StepRun], list[StepRun]]`.

Replace the per-run PID logic (lines 436-464) with four-way classification:

1. **PID alive + `verify_agent_pid(pid, pgid)` passes** → append to `reattach_runs`. Log "will reattach". Do NOT fail or kill.
2. **PID alive + verification fails** → log "PID reuse detected". Fall to check 3.
3. **PID dead (or PID-reuse) + output file exists** (check `executor_state.get("output_path")` via `os.path.isfile()` and `os.path.getsize() > 0`) → append to `recovery_runs`. Do NOT fail or kill.
4. **PID dead + no usable output** → existing behavior: kill pgid, set FAILED, save.

Job-level logic (lines 466-481): add condition — if `reattach_runs` or `recovery_runs` contain entries for this job, leave job RUNNING. Existing "looks complete" and "fail" paths unchanged.

Return `(reattach_runs, recovery_runs)` at the end.

### Step 5: Add reattach methods to `AsyncEngine` in engine.py (~40 min)

Add to `AsyncEngine` class, after `recover_jobs()` (around line 2722):

**`async def _reattach_agent(self, run: StepRun) -> None`:**
1. Load job: `self.store.load_job(run.job_id)`
2. Get step_def: `job.workflow.steps[run.step_name]`
3. Build `ExecutionContext(job_id=run.job_id, step_name=run.step_name, attempt=run.attempt, workspace_path=run.executor_state.get("working_dir", ""))` — fill other fields from job/step_def as `_run_executor()` does
4. `result = await asyncio.to_thread(reattach_and_wait, run.executor_state, step_def, ctx)`
5. `await self._queue.put(("step_result", job.id, run.step_name, run.id, result))`
6. Exception handler: `await self._queue.put(("step_error", ...))`
7. `except asyncio.CancelledError: return`

**`async def _recover_agent_output(self, run: StepRun) -> None`:**
1. Same job/step_def/context setup
2. `result = process_completed_output(run.executor_state, step_def, ctx)` — synchronous, no thread pool
3. Push to queue

**`def reattach_agents(self, reattach_runs, recovery_runs) -> None`:**
1. Must be called when event loop is running (after `_engine.run()` task created)
2. For each in `reattach_runs`: `task = asyncio.ensure_future(self._reattach_agent(run)); self._tasks[run.id] = task`
3. For each in `recovery_runs`: `task = asyncio.ensure_future(self._recover_agent_output(run)); self._tasks[run.id] = task`
4. Log counts

### Step 6: Wire reattach into server lifespan (~10 min)

In `server.py:lifespan()`:

**Line 648** — change:
```python
_cleanup_zombie_jobs(store)
```
to:
```python
reattach_runs, recovery_runs = _cleanup_zombie_jobs(store)
```

**After line 670** (`_engine_task = asyncio.create_task(_engine.run())`), add:
```python
if reattach_runs or recovery_runs:
    _engine.reattach_agents(reattach_runs, recovery_runs)
```

No changes needed for `_agent_stream_monitor()` — it auto-discovers RUNNING runs with `output_path` in `executor_state`.

### Step 7: Guard `_poll_external_changes()` in engine.py (~10 min)

In `_poll_external_changes()`, before the stuck-run check at line 2668, add:

```python
if run.executor_state and run.executor_state.get("pid"):
    from stepwise.agent import verify_agent_pid
    if verify_agent_pid(
        run.executor_state["pid"],
        run.executor_state.get("pgid"),
    ):
        continue
```

This ensures that even if a run somehow isn't in `self._tasks`, we don't fail it while the agent process is verifiably alive.

### Step 8: Add `--now` flag to CLI (~30 min)

**1. Add argument** to server subparser at `cli.py:3746`:
```python
p_server.add_argument("--now", action="store_true",
                       help="Hard kill: terminate agents before stopping")
```

**2. Add `_hard_stop_server(dot_dir, io)` function:**
- Open `SQLiteStore(str(dot_dir / "stepwise.db"))` (read-only access)
- Query all RUNNING step runs, collect `executor_state.get("pgid")` into a set
- Close store
- For each pgid: try `os.killpg(pgid, signal.SIGTERM)`, catch `ProcessLookupError`/`PermissionError`
- Sleep 2s, then for still-alive pgids: `os.killpg(pgid, signal.SIGKILL)`
- Read pidfile, send SIGKILL to server PID
- Remove pidfile

**3. Route in `_server_stop()`** at line 757:
```python
if getattr(args, 'now', False):
    return _hard_stop_server(project.dot_dir, io)
```

**4. `_server_restart()`** at line 767 works automatically — it calls `_server_stop(args)` which routes through `--now`.

## Testing Strategy

### Unit tests — `tests/test_agent_reattach.py`

| # | Test | Asserts |
|---|---|---|
| 1 | `test_verify_agent_pid_dead` | Non-existent PID → `False` |
| 2 | `test_verify_agent_pid_non_agent` | Spawn `sleep 60` → `False` (not acpx) |
| 3 | `test_verify_agent_pid_pgid_mismatch` | Alive PID, wrong expected_pgid → `False` |
| 4 | `test_process_completed_output_valid` | Temp NDJSON file → `ExecutorResult.type == "data"`, correct artifact |
| 5 | `test_process_completed_output_missing` | Nonexistent path → failure result |
| 6 | `test_extract_agent_output_module_fn` | Module function matches instance method output |

### Integration tests — `tests/test_graceful_restart.py`

| # | Test | Asserts |
|---|---|---|
| 7 | `test_cleanup_reattach_alive_agent` | Live PID + mock verify → in `reattach_runs`, still RUNNING |
| 8 | `test_cleanup_recovery_dead_with_output` | Dead PID + output file → in `recovery_runs` |
| 9 | `test_cleanup_fails_dead_no_output` | Dead PID, no file → FAILED in store |
| 10 | `test_cleanup_pid_reuse` | Alive but unverified + output file → in `recovery_runs` |
| 11 | `test_reattach_creates_task` | `reattach_agents([run], [])` → `run.id in engine._tasks` |
| 12 | `test_poll_skips_verified_agent` | >60s RUNNING, no task, verified PID → NOT failed |

### Run commands

```bash
uv run pytest tests/test_agent_reattach.py -v
uv run pytest tests/test_graceful_restart.py -v
uv run pytest tests/ -v  # full regression
```

### Manual E2E validation

1. `stepwise server start` → run agent flow → `stepwise server restart` → verify agent PID alive → web UI reconnects + streams → result processed
2. Same but kill agent during server downtime → restart → step FAILED with clear error
3. `stepwise server restart --now` → agent pgids killed → server killed → clean restart

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **PID reuse false positive** | Reattach to wrong process | `verify_agent_pid()` checks cmdline + pgid match |
| **Reattach before engine loop** | Tasks orphaned in queue | `reattach_agents()` called AFTER `create_task(_engine.run())` |
| **Agent finishes during restart** | Output not processed | R3 handles: dead PID + output file = recover |
| **Queue owner cleanup kills reattached session** | Agent loses session state | `_collect_active_agent_info()` protects RUNNING runs' PIDs and session IDs |
| **`_poll_external_changes` races reattach** | Premature FAILED status | Step 7 adds PID-alive guard; reattach tasks exist before first poll cycle |
| **Thread pool exhaustion** | New steps can't launch | Pool=32, max agents=3, worst case reattach=3+3=6 workers. No semaphore for reattach. |
| **`_extract_output` refactor** | Breaks existing tests | Instance method wraps module function; no signature changes; full test suite validates |
| **Partial output file** | Corrupt JSON on recovery | `_extract_output` already handles `JSONDecodeError` with fallback (lines 1293-1302) |
