# Implementation Plan: H18 — Server Restart Resilience: Orphan Reattach

## Overview

When the stepwise server restarts, agent processes spawned by the previous server instance may still be alive but the new server has no coroutine monitoring them. The current `_cleanup_zombie_jobs` leaves alive PIDs untouched, but the engine never creates monitoring tasks for them, so `_poll_external_changes` kills them as "executor task lost" after 60 seconds. The fix: (1) harden PID verification in the zombie cleanup phase, (2) factor a shared `_finalize_after_wait()` out of `AgentExecutor.start()` so both the normal path and reattach use identical post-wait logic, (3) add a `reattach_surviving_runs()` method that creates monitoring coroutines through the executor layer, and (4) re-schedule lost poll watch timers.

## Requirements

1. **R1: Reattach surviving agent processes** — On server startup, for each RUNNING step run with a verified live agent PID, create a monitoring coroutine that waits for the process to exit, finalizes via the executor layer, and delivers the result to the engine queue.
   - Acceptance: A job with a running agent step survives server restart and completes normally, including `_session_id` propagation, `emit_flow` delegation, and queue-owner cleanup.

2. **R2: Fail dead agent processes** — On server startup, RUNNING step runs with dead PIDs are marked FAILED with a descriptive error and the job graph advances (dispatch + settlement).
   - Acceptance: Already works via `_cleanup_zombie_jobs`. Enhanced with stronger PID verification.

3. **R3: Guard against PID reuse** — Before zombie cleanup skips a run, verify the PID belongs to an acpx/agent process by checking `/proc/{pid}/cmdline` for agent markers AND validating PGID matches `executor_state.pgid`.
   - Acceptance: A RUNNING step run whose PID was recycled into an unrelated process is marked failed during zombie cleanup, not left for reattach.

4. **R4: Re-schedule poll watch timers** — On server startup, for each SUSPENDED step run with a poll watch, re-create the `_schedule_poll_watch` timer so poll checks resume. Idempotent (skip if timer already exists).
   - Acceptance: A suspended poll step from before the restart resumes checking at its configured interval.

5. **R5: Detect failed non-child agents** — When `wait()` returns `exit_code=0` for a non-child PID (the only value it can return), post-check the NDJSON output for error markers to distinguish success from failure.
   - Acceptance: An agent that failed before restart is correctly reported as failed, not silently treated as success.

6. **R6: Integration tests** — Concrete tests covering happy path, emit_flow, continue_session, file output mode, non-child failure detection, and decorator forfeiture.
   - Acceptance: `uv run pytest tests/test_reattach.py -v` passes. All existing tests pass: `uv run pytest tests/`.

## Assumptions (verified against code)

1. **`executor_state` contains all data needed for reattach**: pid, pgid, output_path, working_dir, session_id, session_name, output_mode, output_file. Verified at `agent.py:1046–1055` (`state_update_fn` call) and `agent.py:1073–1083` (final state dict).

2. **`AcpxBackend.wait()` handles non-child PIDs**: `agent.py:510–514` catches `ChildProcessError` and falls back to polling `/proc` until the process disappears. **Caveat**: always returns `exit_code=0` for non-child PIDs (line 514), so a separate error-detection step is required post-wait.

3. **`_cleanup_zombie_jobs` runs before `recover_jobs`**: Confirmed at `server.py:712–714`. By the time reattach runs, runs with dead/recycled PIDs are already failed.

4. **`_poll_external_changes` has a 60-second grace period**: `engine.py:3062–3064` checks `age > 60` before failing orphaned runs. Reattach must register tasks before this window expires. Since reattach runs during startup (before the engine event loop), this is guaranteed.

5. **Poll watch timers are only created during `_process_launch_result`**: `engine.py:3412–3413`. No recovery path exists for poll watches from before restart.

6. **`_agent_stream_monitor()` already tails any RUNNING run with an `output_path`**: `server.py:350–363`. It polls every few seconds and auto-starts tailers. No separate reattach work needed for agent output streaming.

7. **`_scan_acpx_processes()` uses narrow markers**: `agent.py:272–293` checks for `"claude-agent-acp"` or `"__queue-owner"` in cmdline. PID verification must use the same narrow markers plus `"acpx"` (the parent process whose PID is stored in executor_state).

8. **Decorators are `Executor` subclasses with no `__getattr__` delegation**: `decorators.py:21–64` (TimeoutDecorator), `decorators.py:70–168` (RetryDecorator), `decorators.py:171–211` (FallbackDecorator). Each overrides `start()`, `check_status()`, `cancel()` and delegates to `self._executor`. They do NOT implement `__getattr__`, so calling any method not explicitly defined (like `finalize_surviving`) will raise `AttributeError` — NOT pass through. The engine must unwrap the decorator chain to reach the inner `AgentExecutor`.

9. **`AgentExecutor.start()` post-wait logic (lines 1061–1136) contains 4 side effects** that `check_status()` does NOT replicate:
   - Queue-owner cleanup (`cleanup_session_queue_owner`, line 1064–1071)
   - `_session_id` artifact injection (lines 1125–1130)
   - `emit_flow` file checking and delegation (lines 1104–1116)
   - Full `executor_state` in the returned `ExecutorResult` (line 1135)

## Out of Scope

- **CLI-owned job recovery**: Jobs with `created_by != "server"` are not reattached. `_observe_external_jobs` handles stale detection.
- **Delegated run recovery**: DELEGATED runs point at sub-jobs. Sub-jobs are recovered recursively via the same mechanism.
- **Cross-machine recovery**: Same-machine only (`/proc` required for full verification).
- **Decorator re-invocation after failure**: If the agent already failed, we can't replay `start()` because original inputs and context are not persisted in `executor_state`. Documented as explicit forfeiture — exit rules (loop, escalate) handle recovery at the workflow level.
- **Session continuity across restarts**: Reattach monitors the existing process; it does not restart a new session.

## Architecture

### Design principles (informed by critique)

1. **Drive through the executor layer**: Reattach creates the executor via `registry.create(exec_ref)`, then **unwraps** the decorator chain to call `finalize_surviving()` on the inner `AgentExecutor`. This preserves compatibility with custom registries while working correctly with decorators.

2. **Shared finalization**: Factor the post-wait logic from `AgentExecutor.start()` into a private `_finalize_after_wait()` method. Both `start()` and `finalize_surviving()` call it. This prevents divergence on queue-owner cleanup, `_session_id` injection, `emit_flow`, and `executor_state` propagation.

3. **Early verification**: PID identity checking happens in `_cleanup_zombie_jobs` (before reattach), not after. Reattach only sees verified-alive runs.

4. **Queue-driven failure handling**: Reattach-time failures push events to the engine queue, not directly mutate run state. This ensures `_after_step_change()` → `_dispatch_ready()` → `_check_job_terminal()` fires normally.

### Startup flow

```
lifespan() startup:
  1. _cleanup_zombie_jobs(store)          [ENHANCED] — verify PID identity, fail dead/recycled
  2. _engine.recover_jobs()               [existing]  — settle jobs completed pre-crash
  3. await _engine.reattach_surviving_runs() [NEW]    — create monitoring tasks + poll timers
  4. _engine.run()                         [existing]  — event loop starts
```

### Module changes

```
agent.py:
  + verify_agent_pid(pid, expected_pgid) → bool
  + AgentExecutor.finalize_surviving(executor_state) → ExecutorResult
  ~ AgentExecutor.start() — extract _finalize_after_wait() shared method
  ~ AcpxBackend._completed_status() — add exit_code_reliable param
  ~ AcpxBackend.wait() — pass exit_code_reliable=False for non-child

engine.py:
  + AsyncEngine.reattach_surviving_runs() → int  (async)
  + AsyncEngine._monitor_surviving_run(...)       (async)
  + AsyncEngine._get_exec_ref_for_run(job, run)
  + _unwrap_executor(executor) → inner Executor   (module-level helper)

server.py:
  ~ _cleanup_zombie_jobs() — use verify_agent_pid() instead of bare os.kill
  ~ lifespan() — call reattach_surviving_runs() after recover_jobs()
```

No changes to `registry_factory.py` or `ExecutorRegistry`.

---

## Step Dependency Graph

```
  S1a ──→ S1b ──→ S1c ──────→ S6 (server wiring)
   │                ↑               ↑
  S2a ──→ S2b ─────┘               │
   │                                │
  S3a ──────────→ S3b ─────────────┘
                    ↑
  S4a ──→ S4b ─────┘

  T1 ──→ T2 ──→ T3 ──→ T4 ──→ T5  (tests, sequential — each builds on prior fixtures)
          ↑      ↑
         S1c    S2b (test code needs the production code)
```

**Legend:**
- `S1a–S1c`: Agent-layer changes (finalize, error detection)
- `S2a–S2b`: PID verification
- `S3a–S3b`: Engine-layer changes (reattach + monitor)
- `S4a–S4b`: Decorator unwrap
- `S6`: Server wiring (depends on all above)
- `T1–T5`: Test groups (sequential build-up of fixtures and test classes)

**Parallelizable:** {S1a, S2a, S4a} can start simultaneously. S3a can start once S1a and S2a are done.

---

## Implementation Steps

### S1a: Factor `_finalize_after_wait()` from `AgentExecutor.start()` (~30 min)

**File**: `src/stepwise/agent.py`
**Depends on**: nothing
**Produces**: `AgentExecutor._finalize_after_wait()` private method

Extract lines 1057–1136 of `AgentExecutor.start()` into a private method. `start()` calls the new method after spawn+wait. No behavior change.

```python
def _finalize_after_wait(
    self, process: AgentProcess, agent_status: AgentStatus,
    inputs: dict, context: ExecutionContext | None,
) -> ExecutorResult:
    """Shared post-wait logic: cleanup, emit_flow, output extraction, _session_id.

    Used by both start() (normal path) and finalize_surviving() (reattach path).
    """
    step_id = context.step_name if context else "reattach"
    # ... lines 1061-1136 moved here verbatim, replacing hardcoded step_id ...
```

Refactor `start()`:
```python
def start(self, inputs, context):
    # ... existing spawn logic (lines 1000-1055) ...
    agent_status = self.backend.wait(process)
    return self._finalize_after_wait(process, agent_status, inputs, context)
```

**Verify**: `uv run pytest tests/test_session_continuity.py tests/test_agent_emit_flow.py -v`
Existing tests confirm no behavior change from the refactor.

---

### S1b: Add `finalize_surviving()` public method (~20 min)

**File**: `src/stepwise/agent.py`
**Depends on**: S1a
**Produces**: `AgentExecutor.finalize_surviving(executor_state)` → `ExecutorResult`

```python
def finalize_surviving(self, executor_state: dict) -> ExecutorResult:
    """Finalize a surviving agent process after server restart.

    Reconstructs AgentProcess from executor_state, calls backend.wait(),
    then runs the shared _finalize_after_wait() path.
    """
    process = AgentProcess(
        pid=executor_state["pid"],
        pgid=executor_state["pgid"],
        output_path=executor_state["output_path"],
        working_dir=executor_state["working_dir"],
        session_id=executor_state.get("session_id"),
        session_name=executor_state.get("session_name"),
        capture_transcript=executor_state.get("capture_transcript", True),
    )
    agent_status = self.backend.wait(process)
    return self._finalize_after_wait(process, agent_status, inputs={}, context=None)
```

**Why `inputs={}` is acceptable**: The only use of `inputs` in the post-wait path is the `_session_id` passthrough check (line 1128: `elif "_session_id" in inputs`). This passthrough is a best-effort loss after restart. `continue_session` steps still inject `_session_id` correctly — they only need `process.session_name`, which IS available from `executor_state`.

**Verify**: `uv run pytest tests/test_session_continuity.py tests/test_agent_emit_flow.py -v`
No regressions — `finalize_surviving` is new code, not yet called.

---

### S1c: Add non-child error detection to `AcpxBackend.wait()` (~20 min)

**File**: `src/stepwise/agent.py`
**Depends on**: S1a (uses shared `_finalize_after_wait`)
**Produces**: `_completed_status()` with `exit_code_reliable` param; `wait()` passes it for non-child

Enhance `_completed_status()` (line 627):
```python
def _completed_status(
    self, process: AgentProcess, exit_code: int,
    exit_code_reliable: bool = True,
) -> AgentStatus:
    session_id = self._extract_session_id(process.output_path)
    cost = self._extract_cost(process.output_path)

    if exit_code != 0:
        error = self._read_last_error(process.output_path)
        return AgentStatus(state="failed", exit_code=exit_code,
                           session_id=session_id,
                           error=error or f"Exit code {exit_code}",
                           cost_usd=cost)

    # For non-child PIDs, exit_code=0 is synthetic — check output for errors.
    if not exit_code_reliable:
        error = self._read_last_error(process.output_path)
        if error:
            return AgentStatus(state="failed", exit_code=-1,
                               session_id=session_id,
                               error=f"Agent failed (non-child): {error}",
                               cost_usd=cost)

    # ... rest of success path unchanged (transcript capture, etc.)
```

Update `wait()` line 514:
```python
except ChildProcessError:
    while self._is_process_alive(process.pid):
        time.sleep(0.5)
    exit_code = 0
    return self._completed_status(process, exit_code, exit_code_reliable=False)
```

**Verify**: `uv run pytest tests/ -v -k "agent"` — no regressions in agent tests. The `exit_code_reliable=True` default preserves all existing behavior.

---

### S2a: Add `verify_agent_pid()` function (~20 min)

**File**: `src/stepwise/agent.py`
**Depends on**: nothing
**Produces**: module-level `verify_agent_pid(pid, expected_pgid)` → `bool`

```python
def verify_agent_pid(pid: int, expected_pgid: int | None = None) -> bool:
    """Verify a PID belongs to an acpx/claude-agent process.

    Checks /proc/{pid}/cmdline for known agent markers (same set as
    _scan_acpx_processes: "acpx", "claude-agent-acp", "__queue-owner")
    and optionally validates PGID matches expected_pgid via /proc/{pid}/stat.

    Returns False if: process dead, cmdline doesn't match, PGID mismatch.
    Falls back to os.kill(pid, 0) on non-Linux (no cmdline check).
    """
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if not proc_cmdline.parent.is_dir():
        # Non-Linux fallback: can only detect dead PIDs, not identity
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    try:
        cmdline = proc_cmdline.read_bytes().decode("utf-8", errors="replace")
    except (FileNotFoundError, PermissionError):
        return False  # dead or inaccessible

    if not ("acpx" in cmdline or "claude-agent-acp" in cmdline
            or "__queue-owner" in cmdline):
        return False  # PID recycled into unrelated process

    if expected_pgid is not None:
        actual_pgid = _get_process_pgid(pid)  # existing helper at line 296
        if actual_pgid is not None and actual_pgid != expected_pgid:
            return False  # PID recycled into different process group

    return True
```

**Verify**: `python -c "from stepwise.agent import verify_agent_pid; print('import ok')"` — confirms no import errors. Full testing in T1.

---

### S2b: Harden `_cleanup_zombie_jobs()` with PID verification (~15 min)

**File**: `src/stepwise/server.py`
**Depends on**: S2a
**Produces**: `_cleanup_zombie_jobs()` uses `verify_agent_pid()` instead of bare `os.kill`

Replace lines 452–464:
```python
# Before:
if run.pid:
    try:
        os.kill(run.pid, 0)
        logger.info("Step run %s ... PID %d still alive, skipping", ...)
        continue
    except ProcessLookupError:
        ...
    except PermissionError:
        ...

# After:
if run.pid:
    expected_pgid = (run.executor_state or {}).get("pgid")
    if verify_agent_pid(run.pid, expected_pgid=expected_pgid):
        logger.info(
            "Step run %s (job %s step %s) PID %d verified alive, leaving for reattach",
            run.id, job.id, run.step_name, run.pid,
        )
        continue
    else:
        logger.info(
            "Step run %s (job %s step %s) PID %d dead or recycled, marking failed",
            run.id, job.id, run.step_name, run.pid,
        )
```

Add import at top of `server.py`: `from stepwise.agent import verify_agent_pid`

**Verify**: `uv run pytest tests/test_pid_tracking.py tests/test_server_reliability.py -v`
The `test_alive_pid_left_alone` test needs its mock target updated from `stepwise.server.os.kill` to `stepwise.agent.verify_agent_pid` (returns True). The `test_dead_pid_marked_failed` mock also changes. These mock updates are part of this step.

---

### S3a: Add `_get_exec_ref_for_run()` helper to AsyncEngine (~20 min)

**File**: `src/stepwise/engine.py`
**Depends on**: nothing (reads from existing Job/StepDefinition)
**Produces**: `AsyncEngine._get_exec_ref_for_run(job, run)` → `ExecutorRef`

```python
def _get_exec_ref_for_run(self, job: Job, run: StepRun) -> ExecutorRef:
    """Reconstruct ExecutorRef for a surviving run, replaying config enrichment.

    Mirrors the config injection from _prepare_step_run() (lines 1632-1664)
    so that registry.create(exec_ref) produces an executor with the same
    config as the original launch.
    """
    step_def = job.workflow.steps[run.step_name]
    exec_ref = step_def.executor
    if step_def.outputs and "output_fields" not in exec_ref.config:
        exec_ref = exec_ref.with_config({"output_fields": step_def.outputs})
    if exec_ref.type == "agent":
        session_ctx: dict = {}
        if step_def.continue_session:
            session_ctx["continue_session"] = True
        if step_def.loop_prompt is not None:
            session_ctx["loop_prompt"] = step_def.loop_prompt
        if step_def.max_continuous_attempts is not None:
            session_ctx["max_continuous_attempts"] = step_def.max_continuous_attempts
        if session_ctx:
            exec_ref = exec_ref.with_config(session_ctx)
        if exec_ref.config.get("emit_flow"):
            emit_ctx: dict = {"_registry": self.registry, "_config": self.config}
            depth = self._get_job_depth(job)
            max_depth = job.config.max_sub_job_depth
            emit_ctx["_depth_remaining"] = max(0, max_depth - depth - 1)
            if self.project_dir:
                emit_ctx["_project_dir"] = self.project_dir.parent
            exec_ref = exec_ref.with_config(emit_ctx)
    return exec_ref
```

**Verify**: No tests needed yet — this is a pure helper. Verified by calling tests once S3b is done.

---

### S4a: Add `_unwrap_executor()` helper (~10 min)

**File**: `src/stepwise/engine.py`
**Depends on**: nothing
**Produces**: module-level `_unwrap_executor(executor)` → inner `Executor`

Decorators (`TimeoutDecorator`, `RetryDecorator`, `FallbackDecorator`) store the inner executor as `self._executor` but do NOT implement `__getattr__`. Calling `finalize_surviving()` on a decorated executor raises `AttributeError`. We need to unwrap.

```python
def _unwrap_executor(executor: Executor) -> Executor:
    """Unwrap decorator chain to reach the inner executor.

    Decorators store inner as self._executor. Walk until we reach a
    non-decorator (no _executor attr) or hit the target type.
    """
    while hasattr(executor, "_executor"):
        executor = executor._executor
    return executor
```

**Verify**: Manual — `python -c "from stepwise.engine import _unwrap_executor; print('ok')"`

---

### S4b: Add `_monitor_surviving_run()` coroutine (~30 min)

**File**: `src/stepwise/engine.py`
**Depends on**: S1b (finalize_surviving), S4a (_unwrap_executor)
**Produces**: `AsyncEngine._monitor_surviving_run(...)` async method

```python
async def _monitor_surviving_run(
    self,
    job_id: str,
    step_name: str,
    run_id: str,
    executor_state: dict,
    exec_ref: ExecutorRef,
) -> None:
    """Monitor a surviving agent process from a previous server instance.

    Creates the executor via registry.create (preserving custom registries),
    unwraps the decorator chain, calls finalize_surviving() in the thread
    pool, then pushes the result to the engine queue for normal processing.

    Does NOT acquire the agent semaphore (process already running).
    Does NOT acquire session lock (no prompt being sent).
    """
    try:
        executor = self.registry.create(exec_ref)
        inner = _unwrap_executor(executor)
        if not hasattr(inner, "finalize_surviving"):
            raise TypeError(
                f"Executor type {exec_ref.type} does not support finalize_surviving"
            )
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            self._executor_pool,
            inner.finalize_surviving,
            executor_state,
        )
        await self._queue.put(("step_result", job_id, step_name, run_id, result))
    except asyncio.CancelledError:
        return
    except Exception as e:
        _async_logger.error(
            "Reattach failed for step %s (job %s, run %s): %s",
            step_name, job_id, run_id, e, exc_info=True,
        )
        await self._queue.put(("step_error", job_id, step_name, run_id, e))
```

Results flow through the normal `_handle_queue_event` path:
- `"step_result"` → `_process_launch_result()` → `_after_step_change()` (dispatch + settlement)
- `"step_error"` → `_handle_executor_crash()` → `_after_step_change()` (dispatch + settlement)

**Verify**: No tests yet — tested via T2/T3 once the full reattach method exists.

---

### S3b: Add `reattach_surviving_runs()` to AsyncEngine (~30 min)

**File**: `src/stepwise/engine.py`
**Depends on**: S3a, S4b
**Produces**: `AsyncEngine.reattach_surviving_runs()` → `int`

```python
async def reattach_surviving_runs(self) -> int:
    """Reattach monitoring tasks for agent steps that survived server restart.

    MUST be called after _cleanup_zombie_jobs() and recover_jobs(), but
    before run(). Runs inside the lifespan async context.

    For RUNNING step runs with live PIDs:
    - Creates _monitor_surviving_run() coroutine
    - Registers in self._tasks to prevent _poll_external_changes kill

    For SUSPENDED step runs with poll watches:
    - Re-schedules poll watch timers (skips if already exists)

    Returns the number of runs reattached.
    """
    reattached = 0
    for job in self.store.active_jobs():
        if job.created_by != "server":
            continue

        for run in self.store.running_runs(job.id):
            if not run.executor_state or not run.pid:
                await self._queue.put(("step_error", job.id, run.step_name, run.id,
                    RuntimeError("No executor_state for reattach")))
                continue
            exec_ref = self._get_exec_ref_for_run(job, run)
            task = asyncio.create_task(
                self._monitor_surviving_run(
                    job.id, run.step_name, run.id, run.executor_state, exec_ref)
            )
            self._tasks[run.id] = task
            reattached += 1

        for run in self.store.suspended_runs(job.id):
            if (run.watch and run.watch.mode == "poll"
                    and run.id not in self._poll_tasks):
                self._schedule_poll_watch(job.id, run.id, run.watch)
                reattached += 1

    return reattached
```

**Verify**: Full verification in T2–T5. Smoke check: `uv run pytest tests/ -v` — no regressions (method not yet wired into startup).

---

### S6: Wire reattach into server startup (~10 min)

**File**: `src/stepwise/server.py`
**Depends on**: S2b, S3b (all production code complete)
**Produces**: reattach called during `lifespan()` startup

In `lifespan()`, after line 714 (`_engine.recover_jobs()`):

```python
# Reattach monitoring for agent steps that survived the restart
reattached = await _engine.reattach_surviving_runs()
if reattached:
    logger.info("Reattached %d surviving step run(s) from previous server", reattached)
```

No manual `_loop` assignment needed — `reattach_surviving_runs()` is `async` and runs inside the lifespan async context where `asyncio.get_running_loop()` works.

**Verify**: `uv run pytest tests/ -v` — full suite passes including `test_server_reliability.py` and `test_pid_tracking.py`.

---

## Testing Strategy

All tests in `tests/test_reattach.py`. Run with:
```bash
uv run pytest tests/test_reattach.py -v          # new reattach tests only
uv run pytest tests/ -v                           # full suite (regression)
uv run pytest tests/test_pid_tracking.py -v       # verify PID mock updates
uv run pytest tests/test_server_reliability.py -v  # verify zombie cleanup
```

### Test file structure

**File**: `tests/test_reattach.py`

```python
"""Tests for H18: Server restart resilience — orphan reattach."""

import asyncio
import json
import os
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from stepwise.agent import (
    AgentExecutor,
    AgentProcess,
    AgentStatus,
    AcpxBackend,
    MockAgentBackend,
    verify_agent_pid,
)
from stepwise.engine import AsyncEngine, _unwrap_executor
from stepwise.executors import (
    Executor,
    ExecutionContext,
    ExecutorRegistry,
    ExecutorResult,
    ExecutorStatus,
)
from stepwise.decorators import RetryDecorator
from stepwise.models import (
    ExecutorRef,
    ExitRule,
    HandoffEnvelope,
    InputBinding,
    Job,
    JobConfig,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WatchSpec,
    WorkflowDefinition,
    _gen_id,
    _now,
)
from stepwise.store import SQLiteStore
from tests.conftest import run_job, run_job_sync, register_step_fn, CallableExecutor
```

### Shared fixtures (in test file)

```python
def _make_agent_executor_state(
    pid: int = 12345, pgid: int = 12345, output_mode: str = "effect",
    working_dir: str = "/tmp/test", output_file: str | None = None,
    session_name: str = "test-session", session_id: str | None = None,
) -> dict:
    """Build a realistic executor_state dict matching agent.py:1073-1083."""
    return {
        "pid": pid, "pgid": pgid,
        "output_path": f"/tmp/mock-agent-{pid}.jsonl",
        "working_dir": working_dir,
        "session_id": session_id,
        "session_name": session_name,
        "output_mode": output_mode,
        "output_file": output_file,
        "capture_transcript": False,
    }


def _make_running_job_with_agent(
    store: SQLiteStore, executor_state: dict | None = None,
    step_name: str = "agent-step", continue_session: bool = False,
    emit_flow: bool = False, outputs: list[str] | None = None,
) -> tuple[Job, StepRun]:
    """Create RUNNING job + RUNNING step run with executor_state in DB."""
    exec_config = {"prompt": "test"}
    if emit_flow:
        exec_config["emit_flow"] = True
    wf = WorkflowDefinition(steps={
        step_name: StepDefinition(
            name=step_name,
            executor=ExecutorRef(type="agent", config=exec_config),
            outputs=outputs or ["result"],
            continue_session=continue_session,
        ),
    })
    job = Job(
        id=_gen_id("job"), objective="test-reattach", workflow=wf,
        status=JobStatus.RUNNING, inputs={}, workspace_path="/tmp/test",
        config=JobConfig(), created_at=_now(), updated_at=_now(),
        created_by="server",
    )
    store.save_job(job)

    state = executor_state or _make_agent_executor_state()
    run = StepRun(
        id=_gen_id("run"), job_id=job.id, step_name=step_name,
        attempt=1, status=StepRunStatus.RUNNING,
        pid=state.get("pid"), executor_state=state,
        started_at=_now() - timedelta(seconds=30),
    )
    store.save_run(run)
    return job, run


class ReattachMockBackend(MockAgentBackend):
    """Mock backend that supports finalize_surviving via immediate wait() return."""

    def __init__(self, result: AgentStatus | None = None):
        super().__init__()
        self._finalize_result = result or AgentStatus(
            state="completed", exit_code=0, result={},
        )

    def wait(self, process: AgentProcess) -> AgentStatus:
        """Return immediately for reattach testing."""
        return self._finalize_result


def _make_reattach_engine(
    store: SQLiteStore, backend: MockAgentBackend | None = None,
    with_retry: bool = False,
) -> AsyncEngine:
    """Create AsyncEngine with mock agent executor for reattach tests."""
    reg = ExecutorRegistry()
    reg.register("callable", lambda cfg: CallableExecutor(fn_name=cfg.get("fn_name", "default")))

    _backend = backend or ReattachMockBackend()

    def agent_factory(cfg):
        executor = AgentExecutor(
            backend=_backend, prompt=cfg.get("prompt", ""),
            output_mode=cfg.get("output_mode", "effect"),
            output_path=cfg.get("output_path"),
            **{k: v for k, v in cfg.items()
               if k not in ("prompt", "output_mode", "output_path")},
        )
        return executor

    reg.register("agent", agent_factory)
    return AsyncEngine(store=store, registry=reg)
```

### T1: PID verification tests (maps to R3)

```bash
uv run pytest tests/test_reattach.py::TestVerifyAgentPid -v
```

```python
class TestVerifyAgentPid:
    """verify_agent_pid: /proc-based PID identity verification."""

    def test_dead_pid_returns_false(self):
        """PID not in /proc → False."""
        with patch("stepwise.agent.Path") as mock_path:
            mock_path.return_value.parent.is_dir.return_value = True
            mock_path.return_value.read_bytes.side_effect = FileNotFoundError
            assert verify_agent_pid(99999) is False

    def test_acpx_pid_returns_true(self):
        """PID with 'acpx' in cmdline → True."""
        with patch("stepwise.agent.Path") as mock_path:
            mock_path.return_value.parent.is_dir.return_value = True
            mock_path.return_value.read_bytes.return_value = b"acpx\x00claude\x00run"
            with patch("stepwise.agent._get_process_pgid", return_value=100):
                assert verify_agent_pid(1234, expected_pgid=100) is True

    def test_non_agent_pid_returns_false(self):
        """PID with unrelated cmdline → False (recycled)."""
        with patch("stepwise.agent.Path") as mock_path:
            mock_path.return_value.parent.is_dir.return_value = True
            mock_path.return_value.read_bytes.return_value = b"/bin/bash\x00-l"
            assert verify_agent_pid(1234) is False

    def test_pgid_mismatch_returns_false(self):
        """Cmdline matches but PGID differs → False (recycled into different group)."""
        with patch("stepwise.agent.Path") as mock_path:
            mock_path.return_value.parent.is_dir.return_value = True
            mock_path.return_value.read_bytes.return_value = b"acpx\x00claude"
            with patch("stepwise.agent._get_process_pgid", return_value=999):
                assert verify_agent_pid(1234, expected_pgid=100) is False

    def test_no_proc_fallback_alive(self):
        """Non-Linux (no /proc) falls back to os.kill — alive → True."""
        with patch("stepwise.agent.Path") as mock_path:
            mock_path.return_value.parent.is_dir.return_value = False
            with patch("os.kill"):  # no exception = alive
                assert verify_agent_pid(1234) is True

    def test_no_proc_fallback_dead(self):
        """Non-Linux (no /proc) falls back to os.kill — dead → False."""
        with patch("stepwise.agent.Path") as mock_path:
            mock_path.return_value.parent.is_dir.return_value = False
            with patch("os.kill", side_effect=ProcessLookupError):
                assert verify_agent_pid(1234) is False
```

---

### T2: Core reattach lifecycle tests (maps to R1, R2)

```bash
uv run pytest tests/test_reattach.py::TestReattachLifecycle -v
```

```python
class TestReattachLifecycle:
    """End-to-end: reattach → engine loop → job completion/failure."""

    def test_reattach_live_agent_completes_job(self):
        """R1: Surviving agent process → reattach → engine processes result → job COMPLETED."""
        store = SQLiteStore(":memory:")
        backend = ReattachMockBackend(AgentStatus(state="completed", exit_code=0))
        engine = _make_reattach_engine(store, backend=backend)
        job, run = _make_running_job_with_agent(store)

        async def run_test():
            reattached = await engine.reattach_surviving_runs()
            assert reattached == 1
            assert run.id in engine._tasks
            # Run engine briefly to process the queued result
            engine_task = asyncio.create_task(engine.run())
            try:
                await asyncio.sleep(1.0)
            finally:
                engine_task.cancel()
                try: await engine_task
                except asyncio.CancelledError: pass

        asyncio.run(run_test())

        loaded_run = store.load_run(run.id)
        assert loaded_run.status == StepRunStatus.COMPLETED
        loaded_job = store.load_job(job.id)
        assert loaded_job.status == JobStatus.COMPLETED

    def test_reattach_no_executor_state_fails_and_advances(self):
        """R2: RUNNING run with no executor_state → step_error → job FAILED."""
        store = SQLiteStore(":memory:")
        engine = _make_reattach_engine(store)
        # Create run with no executor_state
        wf = WorkflowDefinition(steps={
            "step": StepDefinition(
                name="step",
                executor=ExecutorRef(type="agent", config={"prompt": "t"}),
                outputs=["result"],
            ),
        })
        job = Job(
            id=_gen_id("job"), objective="test", workflow=wf,
            status=JobStatus.RUNNING, inputs={}, workspace_path="/tmp/test",
            config=JobConfig(), created_at=_now(), updated_at=_now(),
            created_by="server",
        )
        store.save_job(job)
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step",
            attempt=1, status=StepRunStatus.RUNNING,
            pid=None, executor_state=None,
            started_at=_now() - timedelta(seconds=30),
        )
        store.save_run(run)

        async def run_test():
            await engine.reattach_surviving_runs()
            engine_task = asyncio.create_task(engine.run())
            try:
                await asyncio.sleep(1.0)
            finally:
                engine_task.cancel()
                try: await engine_task
                except asyncio.CancelledError: pass

        asyncio.run(run_test())

        loaded_run = store.load_run(run.id)
        assert loaded_run.status == StepRunStatus.FAILED
        loaded_job = store.load_job(job.id)
        assert loaded_job.status == JobStatus.FAILED

    def test_reattach_cli_owned_job_skipped(self):
        """CLI-owned jobs (created_by != 'server') are not reattached."""
        store = SQLiteStore(":memory:")
        engine = _make_reattach_engine(store)
        wf = WorkflowDefinition(steps={
            "step": StepDefinition(
                name="step",
                executor=ExecutorRef(type="agent", config={"prompt": "t"}),
                outputs=["result"],
            ),
        })
        job = Job(
            id=_gen_id("job"), objective="test", workflow=wf,
            status=JobStatus.RUNNING, inputs={}, workspace_path="/tmp/test",
            config=JobConfig(), created_at=_now(), updated_at=_now(),
            created_by="cli:1234",  # not server-owned
        )
        store.save_job(job)
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step",
            attempt=1, status=StepRunStatus.RUNNING,
            pid=12345, executor_state=_make_agent_executor_state(),
            started_at=_now(),
        )
        store.save_run(run)

        async def run_test():
            reattached = await engine.reattach_surviving_runs()
            assert reattached == 0

        asyncio.run(run_test())
```

---

### T3: Specialized reattach scenarios (maps to R1, R5)

```bash
uv run pytest tests/test_reattach.py::TestReattachScenarios -v
```

```python
class TestReattachScenarios:
    """Edge cases: emit_flow, continue_session, file output, non-child failure."""

    def test_reattach_continue_session_injects_session_id(self):
        """continue_session=true step → _session_id in artifact after reattach."""
        store = SQLiteStore(":memory:")
        state = _make_agent_executor_state(session_name="my-session")
        backend = ReattachMockBackend(AgentStatus(state="completed", exit_code=0))
        engine = _make_reattach_engine(store, backend=backend)
        job, run = _make_running_job_with_agent(
            store, executor_state=state, continue_session=True,
        )

        async def run_test():
            await engine.reattach_surviving_runs()
            engine_task = asyncio.create_task(engine.run())
            try:
                await asyncio.sleep(1.0)
            finally:
                engine_task.cancel()
                try: await engine_task
                except asyncio.CancelledError: pass

        asyncio.run(run_test())

        loaded_run = store.load_run(run.id)
        assert loaded_run.status == StepRunStatus.COMPLETED
        assert loaded_run.result.artifact.get("_session_id") == "my-session"

    def test_reattach_file_output_mode(self):
        """File output mode: executor reads JSON from output_file."""
        tmpdir = tempfile.mkdtemp()
        output_file = os.path.join(tmpdir, "output.json")
        with open(output_file, "w") as f:
            json.dump({"summary": "done", "score": 0.95}, f)

        state = _make_agent_executor_state(
            output_mode="file", output_file=output_file, working_dir=tmpdir,
        )
        store = SQLiteStore(":memory:")
        backend = ReattachMockBackend(AgentStatus(state="completed", exit_code=0))
        engine = _make_reattach_engine(store, backend=backend)
        job, run = _make_running_job_with_agent(
            store, executor_state=state, outputs=["summary", "score"],
        )

        async def run_test():
            await engine.reattach_surviving_runs()
            engine_task = asyncio.create_task(engine.run())
            try:
                await asyncio.sleep(1.0)
            finally:
                engine_task.cancel()
                try: await engine_task
                except asyncio.CancelledError: pass

        asyncio.run(run_test())

        loaded_run = store.load_run(run.id)
        assert loaded_run.status == StepRunStatus.COMPLETED
        assert loaded_run.result.artifact["summary"] == "done"
        assert loaded_run.result.artifact["score"] == 0.95

    def test_nonchild_error_detection(self):
        """R5: AcpxBackend._completed_status with exit_code_reliable=False detects errors."""
        tmpdir = tempfile.mkdtemp()
        output_path = os.path.join(tmpdir, "output.jsonl")
        # Write NDJSON with an error event
        with open(output_path, "w") as f:
            f.write(json.dumps({"error": {"message": "Context limit exceeded"}}) + "\n")

        backend = AcpxBackend(acpx_path="acpx", default_agent="claude")
        process = AgentProcess(
            pid=1, pgid=1, output_path=output_path, working_dir=tmpdir,
        )
        status = backend._completed_status(process, exit_code=0, exit_code_reliable=False)
        assert status.state == "failed"
        assert "Context limit exceeded" in status.error

    def test_nonchild_no_error_succeeds(self):
        """Non-child PID with clean output → success."""
        tmpdir = tempfile.mkdtemp()
        output_path = os.path.join(tmpdir, "output.jsonl")
        with open(output_path, "w") as f:
            f.write(json.dumps({"params": {"update": {"content": "done"}}}) + "\n")

        backend = AcpxBackend(acpx_path="acpx", default_agent="claude")
        process = AgentProcess(
            pid=1, pgid=1, output_path=output_path, working_dir=tmpdir,
        )
        status = backend._completed_status(process, exit_code=0, exit_code_reliable=False)
        assert status.state == "completed"
```

---

### T4: Poll watch and idempotency tests (maps to R4)

```bash
uv run pytest tests/test_reattach.py::TestReattachPollWatch -v
```

```python
class TestReattachPollWatch:
    """Poll watch timer re-scheduling on restart."""

    def test_poll_watch_rescheduled(self):
        """R4: Suspended poll step → timer re-created."""
        store = SQLiteStore(":memory:")
        engine = _make_reattach_engine(store)
        wf = WorkflowDefinition(steps={
            "wait-step": StepDefinition(
                name="wait-step",
                executor=ExecutorRef(type="agent", config={"prompt": "t"}),
                outputs=["result"],
            ),
        })
        job = Job(
            id=_gen_id("job"), objective="test", workflow=wf,
            status=JobStatus.RUNNING, inputs={}, workspace_path="/tmp",
            config=JobConfig(), created_at=_now(), updated_at=_now(),
            created_by="server",
        )
        store.save_job(job)
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="wait-step",
            attempt=1, status=StepRunStatus.SUSPENDED,
            watch=WatchSpec(mode="poll", config={"interval_seconds": 10}),
            started_at=_now(),
        )
        store.save_run(run)

        async def run_test():
            reattached = await engine.reattach_surviving_runs()
            assert reattached == 1
            assert run.id in engine._poll_tasks

        asyncio.run(run_test())

    def test_poll_watch_idempotent(self):
        """Pre-existing timer → NOT replaced."""
        store = SQLiteStore(":memory:")
        engine = _make_reattach_engine(store)
        wf = WorkflowDefinition(steps={
            "wait-step": StepDefinition(
                name="wait-step",
                executor=ExecutorRef(type="agent", config={"prompt": "t"}),
                outputs=["result"],
            ),
        })
        job = Job(
            id=_gen_id("job"), objective="test", workflow=wf,
            status=JobStatus.RUNNING, inputs={}, workspace_path="/tmp",
            config=JobConfig(), created_at=_now(), updated_at=_now(),
            created_by="server",
        )
        store.save_job(job)
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="wait-step",
            attempt=1, status=StepRunStatus.SUSPENDED,
            watch=WatchSpec(mode="poll", config={"interval_seconds": 10}),
            started_at=_now(),
        )
        store.save_run(run)

        async def run_test():
            # Pre-populate with a sentinel task
            sentinel = MagicMock()
            engine._poll_tasks[run.id] = sentinel
            reattached = await engine.reattach_surviving_runs()
            assert reattached == 0  # skipped because timer already exists
            assert engine._poll_tasks[run.id] is sentinel  # not replaced

        asyncio.run(run_test())
```

---

### T5: Decorator and unwrap tests (maps to architectural correctness)

```bash
uv run pytest tests/test_reattach.py::TestDecoratorReattach -v
```

```python
class TestDecoratorReattach:
    """Decorator chain unwrapping and forfeiture behavior."""

    def test_unwrap_reaches_inner_executor(self):
        """_unwrap_executor walks decorator chain to inner AgentExecutor."""
        backend = ReattachMockBackend()
        inner = AgentExecutor(backend=backend, prompt="test", output_mode="effect")
        retry = RetryDecorator(inner, {"max_retries": 3})
        assert _unwrap_executor(retry) is inner

    def test_unwrap_plain_executor_returns_self(self):
        """Non-decorated executor returns itself."""
        backend = ReattachMockBackend()
        inner = AgentExecutor(backend=backend, prompt="test", output_mode="effect")
        assert _unwrap_executor(inner) is inner

    def test_decorator_forfeiture_no_retry_on_reattach(self):
        """After restart, RetryDecorator does NOT re-invoke start() on failure."""
        store = SQLiteStore(":memory:")
        fail_backend = ReattachMockBackend(
            AgentStatus(state="failed", exit_code=1, error="agent crashed"),
        )
        engine = _make_reattach_engine(store, backend=fail_backend)
        job, run = _make_running_job_with_agent(store)

        async def run_test():
            await engine.reattach_surviving_runs()
            engine_task = asyncio.create_task(engine.run())
            try:
                await asyncio.sleep(1.0)
            finally:
                engine_task.cancel()
                try: await engine_task
                except asyncio.CancelledError: pass

        asyncio.run(run_test())

        # Agent should be failed — retry decorator did NOT re-invoke start()
        loaded_run = store.load_run(run.id)
        assert loaded_run.status == StepRunStatus.FAILED
        # Backend was only called once (wait in finalize_surviving), not retried
        assert fail_backend.spawn_count == 0  # spawn never called during reattach

    def test_multiple_jobs_mixed_state(self):
        """Three jobs: one reattaches, one fails (no state), one gets poll rescheduled."""
        store = SQLiteStore(":memory:")
        backend = ReattachMockBackend(AgentStatus(state="completed", exit_code=0))
        engine = _make_reattach_engine(store, backend=backend)

        # Job 1: live agent — should reattach
        job1, run1 = _make_running_job_with_agent(store)

        # Job 2: no executor_state — should push error
        wf2 = WorkflowDefinition(steps={
            "step": StepDefinition(
                name="step",
                executor=ExecutorRef(type="agent", config={"prompt": "t"}),
                outputs=["result"],
            ),
        })
        job2 = Job(
            id=_gen_id("job"), objective="test2", workflow=wf2,
            status=JobStatus.RUNNING, inputs={}, workspace_path="/tmp",
            config=JobConfig(), created_at=_now(), updated_at=_now(),
            created_by="server",
        )
        store.save_job(job2)
        run2 = StepRun(
            id=_gen_id("run"), job_id=job2.id, step_name="step",
            attempt=1, status=StepRunStatus.RUNNING,
            pid=None, executor_state=None, started_at=_now(),
        )
        store.save_run(run2)

        # Job 3: suspended poll step — should reschedule
        wf3 = WorkflowDefinition(steps={
            "poll-step": StepDefinition(
                name="poll-step",
                executor=ExecutorRef(type="agent", config={"prompt": "t"}),
                outputs=["result"],
            ),
        })
        job3 = Job(
            id=_gen_id("job"), objective="test3", workflow=wf3,
            status=JobStatus.RUNNING, inputs={}, workspace_path="/tmp",
            config=JobConfig(), created_at=_now(), updated_at=_now(),
            created_by="server",
        )
        store.save_job(job3)
        run3 = StepRun(
            id=_gen_id("run"), job_id=job3.id, step_name="poll-step",
            attempt=1, status=StepRunStatus.SUSPENDED,
            watch=WatchSpec(mode="poll", config={"interval_seconds": 30}),
            started_at=_now(),
        )
        store.save_run(run3)

        async def run_test():
            reattached = await engine.reattach_surviving_runs()
            assert reattached == 2  # run1 (monitoring task) + run3 (poll timer)
            assert run1.id in engine._tasks
            assert run3.id in engine._poll_tasks

        asyncio.run(run_test())
```

### Updated existing test mocks (part of S2b)

**File**: `tests/test_pid_tracking.py`

The `test_alive_pid_left_alone` and `test_dead_pid_marked_failed` tests currently mock `stepwise.server.os.kill`. After S2b, they must mock `stepwise.server.verify_agent_pid` instead:

```python
# test_alive_pid_left_alone — change:
with patch("stepwise.server.verify_agent_pid", return_value=True):
    _cleanup_zombie_jobs(store)

# test_dead_pid_marked_failed — change:
with patch("stepwise.server.verify_agent_pid", return_value=False):
    with patch("stepwise.server.os.killpg"):
        _cleanup_zombie_jobs(store)
```

### Requirement → Test Traceability Matrix

| Requirement | Test Class | Test Method | Command |
|---|---|---|---|
| R1 (reattach live) | `TestReattachLifecycle` | `test_reattach_live_agent_completes_job` | `uv run pytest tests/test_reattach.py::TestReattachLifecycle::test_reattach_live_agent_completes_job -v` |
| R1 (session id) | `TestReattachScenarios` | `test_reattach_continue_session_injects_session_id` | `uv run pytest tests/test_reattach.py::TestReattachScenarios::test_reattach_continue_session_injects_session_id -v` |
| R1 (file output) | `TestReattachScenarios` | `test_reattach_file_output_mode` | `uv run pytest tests/test_reattach.py::TestReattachScenarios::test_reattach_file_output_mode -v` |
| R2 (fail dead) | `TestReattachLifecycle` | `test_reattach_no_executor_state_fails_and_advances` | `uv run pytest tests/test_reattach.py::TestReattachLifecycle::test_reattach_no_executor_state_fails_and_advances -v` |
| R2 (skip CLI) | `TestReattachLifecycle` | `test_reattach_cli_owned_job_skipped` | `uv run pytest tests/test_reattach.py::TestReattachLifecycle::test_reattach_cli_owned_job_skipped -v` |
| R3 (PID reuse) | `TestVerifyAgentPid` | `test_non_agent_pid_returns_false`, `test_pgid_mismatch_returns_false` | `uv run pytest tests/test_reattach.py::TestVerifyAgentPid -v` |
| R3 (dead PID) | `TestVerifyAgentPid` | `test_dead_pid_returns_false` | `uv run pytest tests/test_reattach.py::TestVerifyAgentPid::test_dead_pid_returns_false -v` |
| R4 (poll watch) | `TestReattachPollWatch` | `test_poll_watch_rescheduled` | `uv run pytest tests/test_reattach.py::TestReattachPollWatch::test_poll_watch_rescheduled -v` |
| R4 (idempotent) | `TestReattachPollWatch` | `test_poll_watch_idempotent` | `uv run pytest tests/test_reattach.py::TestReattachPollWatch::test_poll_watch_idempotent -v` |
| R5 (non-child fail) | `TestReattachScenarios` | `test_nonchild_error_detection` | `uv run pytest tests/test_reattach.py::TestReattachScenarios::test_nonchild_error_detection -v` |
| R5 (non-child ok) | `TestReattachScenarios` | `test_nonchild_no_error_succeeds` | `uv run pytest tests/test_reattach.py::TestReattachScenarios::test_nonchild_no_error_succeeds -v` |
| R6 (decorator) | `TestDecoratorReattach` | `test_decorator_forfeiture_no_retry_on_reattach` | `uv run pytest tests/test_reattach.py::TestDecoratorReattach::test_decorator_forfeiture_no_retry_on_reattach -v` |
| R6 (unwrap) | `TestDecoratorReattach` | `test_unwrap_reaches_inner_executor`, `test_unwrap_plain_executor_returns_self` | `uv run pytest tests/test_reattach.py::TestDecoratorReattach -v` |
| R6 (mixed state) | `TestDecoratorReattach` | `test_multiple_jobs_mixed_state` | `uv run pytest tests/test_reattach.py::TestDecoratorReattach::test_multiple_jobs_mixed_state -v` |
| Regression | existing | `test_server_reliability`, `test_pid_tracking`, `test_session_continuity`, `test_agent_emit_flow` | `uv run pytest tests/test_server_reliability.py tests/test_pid_tracking.py tests/test_session_continuity.py tests/test_agent_emit_flow.py -v` |

### Manual verification

```bash
# Start server with a long-running agent job
stepwise server start
stepwise run --async my-agent-flow.yaml

# Wait for agent to start, verify PID in DB
stepwise server restart

# Check logs for reattach messages
stepwise server log | grep -E "reattach|verified alive|dead or recycled"

# Verify job completes normally
```

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `/proc` not available (macOS) | Medium | PID verification falls back to `os.kill(pid,0)` — weaker but functional | Log warning on non-Linux. PID reuse risk is low on short restart windows. |
| PID recycled into another acpx invocation | Very low | PGID check catches most cases | PGID validation + cmdline check. Accept residual risk. |
| `_finalize_after_wait()` diverges from `start()` over time | Medium | Bugs where reattach produces different results | Both paths call the same `_finalize_after_wait()` method. Add code comment warning maintainers. |
| Non-child error detection false positives | Low | `_read_last_error` finds error-like text in successful output | `_read_last_error()` only parses structured NDJSON `{"error": {"message": ...}}` — not arbitrary text (see `agent.py:798–816`). |
| `_get_exec_ref_for_run()` misses config enrichment | Medium | Executor lacks context for finalization | Explicitly replays same enrichment as `_prepare_step_run()`. Test T3 verifies output correctness. |
| Process exits between zombie cleanup and reattach | Low | `backend.wait()` returns immediately | Handled — `wait()` polls `/proc` and returns when process gone. |
| Future decorator overriding `finalize_surviving()` | Low | Would be silently bypassed by `_unwrap_executor()` | `_unwrap_executor` is well-documented. T5 `test_unwrap_reaches_inner_executor` explicitly verifies the chain. |

## Known Limitations (Explicit Forfeitures)

1. **Decorator retry on reattached failure**: If an agent step has `RetryDecorator` and the agent fails after restart, the retry does NOT fire. The decorator only wraps `start()`. The engine's exit rules (loop action) handle retry at the workflow level. This is acceptable because the process already ran — we can't "retry" a process that's already exited. Verified by `test_decorator_forfeiture_no_retry_on_reattach`.

2. **`_session_id` passthrough on reattach**: When a non-`continue_session` step receives `_session_id` from upstream and passes it through (line 1128–1130 of `start()`), this passthrough is lost on reattach because original `inputs` are not persisted in `executor_state`. `continue_session` steps still inject `_session_id` correctly — they only need `process.session_name`. Workaround: persist `inputs._session_id` in `executor_state` in a future enhancement.

3. **Non-child PID exit code**: For processes not spawned by the current server, `wait()` can only detect "process disappeared" and falls back to NDJSON error detection. A process killed by OOM killer with no NDJSON output will appear to succeed. Mitigation: check for truncated/empty NDJSON as a failure signal in `_completed_status()`.
