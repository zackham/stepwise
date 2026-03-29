# Implementation Plan: Runner Process Lifecycle Management

## Overview

Two related problems where agent subprocesses survive job lifecycle transitions and accumulate as zombies.

**Problem 1 — Pause/Cancel Doesn't Kill Runner:** When a job is paused or cancelled via the API, the engine marks state correctly but the actual OS process (Claude/agent) keeps running. Root cause: a race condition between asyncio task cancellation and PID persistence, plus insufficient kill verification.

**Problem 2 — Zombie Process Reaping:** Agent processes from old sessions accumulate indefinitely. No periodic health check runs for server-owned jobs, only CLI-stale detection exists. No TTL enforcement.

---

## What Already Exists (Verified)

The codebase has substantial infrastructure for process management:

| Capability | Location | Status |
|---|---|---|
| PID tracking in StepRun | `models.py:1516` (`pid: int \| None`) | Working |
| PGID tracking in executor_state | `agent.py:1089-1147` (AcpxBackend.spawn) | Working |
| state_update_fn callback | `engine.py:3782-3793` (writes PID to DB) | Has race condition |
| executor.cancel() ABC | `executors.py:122-133` | Working |
| AcpxBackend.cancel() | `agent.py:581-628` (cooperative → SIGTERM → SIGKILL) | Partial — 0.5s grace, no verification |
| Base Engine.cancel_job() | `engine.py:346-405` (iterates runs, calls cancel) | Working but uses stale state |
| AsyncEngine.cancel_job() | `engine.py:3545-3581` (task.cancel + super) | Has race condition |
| AsyncEngine.pause_job() | `engine.py:3583-3595` (task.cancel + super) | Has race condition |
| _cleanup_zombie_jobs() | `server.py:540-607` (startup recovery) | Working — startup only |
| _observe_external_jobs() | `server.py:438-493` (CLI stale detection) | Working — CLI jobs only |
| verify_agent_pid() | `agent.py:273-306` (Linux /proc check) | Working |
| _is_pid_alive() | `agent.py:130-138` (os.kill probe) | Working |
| Session cleanup | `engine.py:1464-1547` (_cleanup_job_sessions) | Working |

---

## Root Cause Analysis

### Problem 1: The Race Condition

When `AsyncEngine.cancel_job()` runs:

1. **`task.cancel()`** sends `CancelledError` to the `_run_executor` coroutine (line 3551)
2. But `_run_executor` runs `executor.start()` in a **thread pool** via `run_in_executor()` (line 3806-3810)
3. `CancelledError` does NOT propagate into threads — the thread keeps running
4. The coroutine catches `CancelledError` and returns silently (line 3814-3816)
5. **`super().cancel_job()`** calls `self.store.running_runs(job_id)` to get the state (line 350)
6. But the PID was stored via `call_soon_threadsafe(_do_update)` (line 3790) — this callback is **queued on the event loop** and cannot execute while cancel_job is running synchronously
7. So `run.executor_state` has no `pid`/`pgid` → `executor.cancel({})` does nothing (line 583-584 in agent.py)

**Result:** The agent subprocess runs indefinitely. No one tracks it. The PID eventually appears in the DB (when the queued callback runs), but the run is already marked CANCELLED with `pid=None`.

### Problem 2: No Server-Side Reaper

- `_cleanup_zombie_jobs()` runs **once at startup** (line 823) — not periodically
- `_observe_external_jobs()` monitors **CLI-owned** jobs only (line 448: `exclude_owner="server"`)
- For server-owned jobs: no periodic check that agent PIDs are still alive
- No TTL enforcement: a stuck agent process runs until the machine reboots

---

## Requirements & Acceptance Criteria

### R1: In-Memory PID Tracking for Cancel/Pause
- **AC1.1:** AsyncEngine maintains `_run_states: dict[str, dict]` mapping `run_id → executor_state`
- **AC1.2:** `state_update_fn` writes to `_run_states` immediately (GIL-safe dict assignment) in addition to the existing DB queue
- **AC1.3:** `cancel_job()` and `pause_job()` read from `_run_states` to get the PID/PGID, not from the DB
- **AC1.4:** `_run_states[run_id]` is cleaned up when the run completes, fails, or cancels

### R2: Reliable Process Kill
- **AC2.1:** On cancel: cooperative cancel attempt (existing) → SIGTERM → **5s grace** → SIGKILL (currently 0.5s)
- **AC2.2:** After SIGKILL, verify process is dead via `_is_pid_alive()`. Log warning if still alive after 3 attempts with 1s intervals.
- **AC2.3:** On pause: SIGTERM only (graceful), same death verification
- **AC2.4:** If cooperative cancel reports success, still verify process died within 2s. Fall back to SIGTERM if alive.

### R3: Periodic Process Health Check (Reaper)
- **AC3.1:** New `_reap_orphaned_processes()` background task in server lifespan, runs every 60s
- **AC3.2:** Detects server-owned RUNNING step runs whose PID is dead → marks run FAILED, emits event
- **AC3.3:** Detects server-owned RUNNING step runs whose PID is alive but running beyond TTL → SIGTERM + cleanup
- **AC3.4:** Detects entries in `_tasks` with no matching RUNNING step run (orphaned asyncio tasks) → cancels task
- **AC3.5:** All actions logged at INFO level with PID, job_id, step_name, run_id

### R4: Configurable Agent Process TTL
- **AC4.1:** `agent_process_ttl` field on `StepwiseConfig` (default: 7200 seconds = 2 hours)
- **AC4.2:** Settable in `.stepwise/config.yaml`
- **AC4.3:** Per-step override via `config.max_runtime` on StepDefinition (already partially exists via TimeoutDecorator — verify interaction)
- **AC4.4:** TTL clock starts at `run.started_at`, checked by the reaper

### R5: Process Lifecycle Logging
- **AC5.1:** Log at INFO: process spawn (PID, PGID, step, job), process kill (signal, reason), death detected, TTL exceeded
- **AC5.2:** Log at WARNING: kill failed, PID not found in state, process survived SIGKILL
- **AC5.3:** All logs use `stepwise.lifecycle` logger for easy filtering

---

## Assumptions (Verified Against Codebase)

| # | Assumption | Verified At |
|---|---|---|
| A1 | `StepRun.pid` and `executor_state["pgid"]` are populated by AgentExecutor via `state_update_fn` | `engine.py:3782-3793`, `agent.py:1089-1147` |
| A2 | `call_soon_threadsafe` queues the DB write — it does NOT execute synchronously | `engine.py:3789-3790` — confirmed by asyncio semantics |
| A3 | `task.cancel()` does not kill the thread pool thread — only the awaiting coroutine | Python stdlib: `asyncio.Task.cancel()` → `CancelledError` on coroutine, thread unaffected |
| A4 | `os.killpg(pgid, SIGTERM)` works because agents use `start_new_session=True` | `agent.py` — Popen with `start_new_session=True` |
| A5 | `_observe_external_jobs` only handles CLI jobs (excludes server-owned) | `server.py:448`: `exclude_owner="server"` |
| A6 | `_cleanup_zombie_jobs` runs only at startup, not periodically | `server.py:823` — called once in lifespan before yield |
| A7 | Simple dict assignment is GIL-safe for concurrent read/write between event loop and thread | Python memory model — dict `__setitem__` is atomic under GIL |
| A8 | `StepwiseConfig` uses `@dataclass` with simple fields, loaded from `.stepwise/config.yaml` | `config.py:104-119` |

---

## Implementation Steps

### Step 1: In-Memory PID State Tracking

**File:** `src/stepwise/engine.py`

Add `_run_states: dict[str, dict]` to `AsyncEngine.__init__()` (near line 3180):
```python
self._run_states: dict[str, dict] = {}  # run_id → latest executor_state (in-memory, no DB delay)
```

Modify `_run_executor()` `update_state` closure (line 3782-3792) to also write to `_run_states`:
```python
def update_state(state: dict) -> None:
    # In-memory update (immediate, GIL-safe) — used by cancel/pause
    self._run_states[run_id] = state
    # DB update (queued on event loop)
    def _do_update():
        run = self.store.load_run(run_id)
        run.executor_state = state
        if "pid" in state:
            run.pid = state["pid"]
        self.store.save_run(run)
    if _loop and _loop.is_running():
        _loop.call_soon_threadsafe(_do_update)
    else:
        _do_update()
```

Clean up `_run_states` in the result/error paths of `_run_executor()` and in `_process_launch_result()` / `_fail_run()` / `_complete_run()`. Also clean up on cancel/pause.

### Step 2: Fix Cancel/Pause to Use In-Memory State

**File:** `src/stepwise/engine.py`

Override `cancel_job()` in AsyncEngine (line 3545) to pass in-memory state to executor.cancel():

```python
def cancel_job(self, job_id: str) -> None:
    # Kill processes using in-memory state (avoids DB race condition)
    for run in self.store.running_runs(job_id):
        state = self._run_states.pop(run.id, run.executor_state or {})
        task = self._tasks.pop(run.id, None)
        self._task_exec_types.pop(run.id, None)
        if task:
            task.cancel()
        # Kill the process directly using in-memory state
        step_def = self.store.load_job(job_id).workflow.steps.get(run.step_name)
        if step_def and (state.get("pid") or state.get("pgid")):
            try:
                executor = self.registry.create(step_def.executor)
                executor.cancel(state)
            except Exception:
                _lifecycle_logger.warning(...)
    # ... rest of cancellation (poll tasks, super for state updates, cascade)
```

Key change: `self._run_states.pop(run.id, ...)` gets the latest state that may not yet be in the DB.

Similarly update `pause_job()` to use `_run_states`.

**Important:** The base `Engine.cancel_job()` still calls `executor.cancel(run.executor_state or {})` from the DB. Since AsyncEngine now handles process killing before calling `super()`, add a flag or skip the redundant kill in super. Simplest approach: override fully in AsyncEngine rather than calling super(), or have super() skip the kill when the run is already CANCELLED.

**Revised approach**: Have AsyncEngine do its own kill pass using `_run_states`, then call a new `_cancel_job_state_only()` method on the base Engine that handles the state transitions (marking runs CANCELLED, updating job status) without re-calling executor.cancel(). This avoids double-kill and the stale-state problem.

Actually, cleaner: just override fully in AsyncEngine. The method is ~60 lines. Duplicating the state-transition logic is less error-prone than adding a flag to the base class.

### Step 3: Improve Kill Reliability in AcpxBackend.cancel()

**File:** `src/stepwise/agent.py`

Modify `AcpxBackend.cancel()` (line 581):

1. **After cooperative cancel**: verify process died within 2s. If still alive, proceed to SIGTERM:
```python
if cooperative_cancelled:
    # Verify the process actually died
    for _ in range(4):
        time.sleep(0.5)
        if not _is_pid_alive(process.pid):
            break
    else:
        _lifecycle_logger.warning("Cooperative cancel succeeded but PID %d still alive, sending SIGTERM", process.pid)
        cooperative_cancelled = False  # fall through to SIGTERM
```

2. **Increase SIGKILL grace period** from 0.5s → 5s:
```python
if not cooperative_cancelled:
    pgid = process.pgid or process.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    else:
        # Wait up to 5s for graceful shutdown
        for _ in range(10):
            time.sleep(0.5)
            if not _is_pid_alive(process.pid):
                break
        else:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
```

3. **Death verification** after SIGKILL:
```python
            # Verify death
            time.sleep(0.5)
            if _is_pid_alive(process.pid):
                _lifecycle_logger.error(
                    "Process PID %d survived SIGKILL — manual intervention required",
                    process.pid,
                )
```

### Step 4: Add Configurable Agent Process TTL

**File:** `src/stepwise/config.py`

Add field to `StepwiseConfig` (after line 119):
```python
agent_process_ttl: int = 7200  # seconds (default 2 hours)
```

**File:** `src/stepwise/config.py` — `_merge_config()` / `_load_config_file()` — ensure `agent_process_ttl` is loaded from YAML config.

### Step 5: Periodic Process Reaper

**File:** `src/stepwise/server.py`

New async function `_reap_orphaned_processes()` (add near `_observe_external_jobs`):

```python
async def _reap_orphaned_processes() -> None:
    """Periodic health check for server-owned agent processes.

    Detects:
    - PIDs registered in step runs but no longer alive (dead process cleanup)
    - PIDs alive but running beyond TTL (force kill + cleanup)
    - Orphaned asyncio tasks with no matching RUNNING step run
    """
    engine = _get_engine()
    config = engine.config or StepwiseConfig()
    ttl = config.agent_process_ttl

    while True:
        try:
            now = _now()
            for job in engine.store.active_jobs():
                if job.created_by != "server":
                    continue
                for run in engine.store.running_runs(job.id):
                    if not run.pid:
                        continue

                    expected_pgid = (run.executor_state or {}).get("pgid")

                    # Case A: PID is dead → clean up
                    if not verify_agent_pid(run.pid, expected_pgid=expected_pgid):
                        _lifecycle_logger.info(
                            "Reaper: PID %d dead for job %s step %s (run %s) — marking failed",
                            run.pid, job.id, run.step_name, run.id,
                        )
                        # Cancel asyncio task if exists
                        task = engine._tasks.pop(run.id, None)
                        engine._task_exec_types.pop(run.id, None)
                        if task:
                            task.cancel()
                        run.status = StepRunStatus.FAILED
                        run.error = f"Agent process died (PID {run.pid} no longer alive)"
                        run.pid = None
                        run.completed_at = now
                        engine.store.save_run(run)
                        engine._emit(job.id, STEP_FAILED, {...})
                        engine._check_job_terminal(job.id)
                        _notify_change(job.id)
                        continue

                    # Case B: PID alive but beyond TTL → kill
                    if run.started_at and ttl > 0:
                        elapsed = (now - run.started_at).total_seconds()
                        if elapsed > ttl:
                            _lifecycle_logger.warning(
                                "Reaper: PID %d exceeded TTL (%ds > %ds) for job %s step %s — killing",
                                run.pid, int(elapsed), ttl, job.id, run.step_name,
                            )
                            # Kill via executor.cancel()
                            state = engine._run_states.get(run.id, run.executor_state or {})
                            step_def = job.workflow.steps.get(run.step_name)
                            if step_def:
                                try:
                                    executor = engine.registry.create(step_def.executor)
                                    executor.cancel(state)
                                except Exception:
                                    _lifecycle_logger.warning(...)
                            # Cancel asyncio task
                            task = engine._tasks.pop(run.id, None)
                            engine._task_exec_types.pop(run.id, None)
                            if task:
                                task.cancel()
                            run.status = StepRunStatus.FAILED
                            run.error = f"Agent process exceeded TTL ({int(elapsed)}s > {ttl}s)"
                            run.error_category = "ttl_exceeded"
                            run.pid = None
                            run.completed_at = now
                            engine.store.save_run(run)
                            engine._emit(job.id, STEP_FAILED, {...})
                            engine._check_job_terminal(job.id)
                            _notify_change(job.id)

            await asyncio.sleep(60)
        except asyncio.CancelledError:
            break
        except Exception:
            _lifecycle_logger.exception("Reaper loop error")
            await asyncio.sleep(60)
```

**File:** `src/stepwise/server.py` — lifespan (near line 858):

```python
_reaper = asyncio.create_task(_reap_orphaned_processes())
```

And cancel it on shutdown (near line 865).

### Step 6: Process Lifecycle Logger

**File:** `src/stepwise/engine.py` and `src/stepwise/agent.py`

Add a dedicated logger:
```python
_lifecycle_logger = logging.getLogger("stepwise.lifecycle")
```

Add INFO-level log lines at:
- **Agent spawn** (`agent.py`, AcpxBackend.spawn): `"Process spawned: PID=%d PGID=%d step=%s job=%s"`
- **State update** (`engine.py`, update_state): `"PID registered: PID=%d run=%s"` (DEBUG level to avoid noise)
- **Process kill** (`agent.py`, AcpxBackend.cancel): `"Sending %s to PGID %d (step %s)"` for each signal
- **Death detected** (reaper): `"Dead process detected: PID=%d step=%s job=%s"`
- **TTL exceeded** (reaper): `"TTL exceeded: PID=%d elapsed=%ds ttl=%ds"`
- **Kill verified** (`agent.py`): `"Process confirmed dead after %s: PID=%d"`
- **Kill failed** (`agent.py`): `"Process survived SIGKILL: PID=%d — manual intervention needed"`

### Step 7: Clean Up on Job Lifecycle Transitions

**File:** `src/stepwise/engine.py`

Verify that `_fail_run()` (line 2931) and `_halt_job()` (line 3033) clean up `_run_states`:
```python
def _fail_run(self, ...):
    ...
    self._run_states.pop(run.id, None)  # Clean up in-memory state
    ...
```

Also verify `_complete_run()` path clears `_run_states`.

In `AsyncEngine.shutdown()` (line 3187), kill all tracked processes before shutting down the thread pool:
```python
async def shutdown(self) -> None:
    # Kill all running agent processes
    for run_id, state in list(self._run_states.items()):
        if state.get("pid") or state.get("pgid"):
            pgid = state.get("pgid") or state.get("pid")
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
    self._run_states.clear()
    self._executor_pool.shutdown(wait=False)
    ...
```

---

## File Change Summary

| File | Changes |
|---|---|
| `src/stepwise/engine.py` | Add `_run_states` dict, update `_run_executor` state callback, override `cancel_job`/`pause_job` to use in-memory state, clean up `_run_states` on completion/failure, add lifecycle logging |
| `src/stepwise/agent.py` | Increase SIGKILL grace to 5s, add death verification loop, verify cooperative cancel, add lifecycle logging |
| `src/stepwise/config.py` | Add `agent_process_ttl: int = 7200` to StepwiseConfig |
| `src/stepwise/server.py` | Add `_reap_orphaned_processes()` background task, launch in lifespan, cancel on shutdown |
| `tests/test_runner_lifecycle.py` | New test file (see Testing Strategy) |

---

## Testing Strategy

### New Test File: `tests/test_runner_lifecycle.py`

All tests use `async_engine` fixture + `register_step_fn()` + mock `os.killpg`/`os.kill`/`_is_pid_alive`.

**Test 1: `test_cancel_kills_process_with_inmemory_pid`**
- Register a step fn that stores a fake PID via `context.state_update_fn({"pid": 12345, "pgid": 12345})`
- Use a blocking step (sleeps in thread) to simulate long-running agent
- Call `cancel_job()` while step is running
- Assert `os.killpg` was called with the PID
- Assert the step run is CANCELLED

**Test 2: `test_cancel_before_pid_reported`**
- Register a step fn that does NOT call state_update_fn before cancel arrives
- Call `cancel_job()` immediately
- Assert no crash, run still marked CANCELLED
- Assert warning logged about missing PID

**Test 3: `test_pause_kills_process`**
- Same as test 1 but with `pause_job()`
- Assert process is killed and run is SUSPENDED

**Test 4: `test_reaper_detects_dead_pid`**
- Create a RUNNING job with a step run that has a PID
- Mock `verify_agent_pid()` to return False (process dead)
- Call the reaper function once
- Assert run marked FAILED with descriptive error

**Test 5: `test_reaper_kills_over_ttl`**
- Create a RUNNING job with a step run started 3 hours ago
- Mock `verify_agent_pid()` to return True (alive) and `_is_pid_alive` to return True
- Set TTL to 7200
- Call the reaper function once
- Assert `os.killpg` was called
- Assert run marked FAILED with "TTL exceeded" error

**Test 6: `test_reaper_ignores_healthy_processes`**
- Create a RUNNING job with a step run started 30 minutes ago
- Mock PID as alive
- Call the reaper function once
- Assert no state changes

**Test 7: `test_cancel_sigkill_after_sigterm_timeout`**
- Mock `_is_pid_alive` to return True for 10 checks then False
- Call `AcpxBackend.cancel()` with a fake AgentProcess
- Assert SIGTERM sent first, then SIGKILL after grace period

**Test 8: `test_cooperative_cancel_verified`**
- Mock acpx cancel to return 0 but process still alive
- Assert SIGTERM fallback is used

**Test 9: `test_run_states_cleaned_on_completion`**
- Run a job to completion
- Assert `engine._run_states` is empty

**Test 10: `test_agent_process_ttl_config`**
- Load config with `agent_process_ttl: 3600`
- Assert config field populated correctly

### Run Commands

```bash
# Run new tests only
uv run pytest tests/test_runner_lifecycle.py -v

# Run all tests (ensure no regressions)
uv run pytest tests/ -x -q

# Run with lifecycle logging visible
uv run pytest tests/test_runner_lifecycle.py -v --log-cli-level=INFO -k lifecycle
```

### Baseline

All 2042 existing tests pass as of 2026-03-29 (80s runtime). No regressions allowed.

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| GIL-safe dict assignment assumption is wrong | Python dict `__setitem__` for simple key/value is atomic under GIL. Verified by CPython implementation. If concerned, use `threading.Lock`. |
| Reaper conflicts with engine's own completion handling | Reaper checks `run.status == RUNNING` before acting. Engine sets status to COMPLETED/FAILED atomically in the store. Double-write is idempotent. |
| 5s SIGKILL grace slows down cancel | Only applies when SIGTERM doesn't work. Normal cancel (cooperative or SIGTERM) completes in <1s. |
| Full `cancel_job()` override in AsyncEngine diverges from base | Keep base class's `_cancel_job_runs()` as a shared helper for state transitions. Override only the process-killing logic. |
| TTL kills a legitimately long-running agent | Default 2h is generous. Users can increase or set to 0 (disabled). Per-step TimeoutDecorator is the preferred mechanism for known-duration steps. |

---

## Execution Order

1. Step 6 (lifecycle logger) — standalone, no deps
2. Step 1 (in-memory PID tracking) — foundation for steps 2, 5, 7
3. Step 3 (kill reliability) — standalone improvement to agent.py
4. Step 4 (TTL config) — standalone, needed by step 5
5. Step 2 (fix cancel/pause) — depends on step 1
6. Step 7 (cleanup on transitions) — depends on step 1
7. Step 5 (periodic reaper) — depends on steps 1, 4
8. Tests — after all implementation steps, one commit per logical group
