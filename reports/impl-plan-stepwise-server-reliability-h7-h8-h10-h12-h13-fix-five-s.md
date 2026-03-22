---
title: "Implementation Plan: Server Reliability (H7 + H8 + H10 + H12 + H13)"
date: "2026-03-21T12:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Server Reliability (H7 + H8 + H10 + H12 + H13)

## Overview

Fix five server reliability issues: zombie job resurrection on restart (H7), SQLite contention under parallel load (H8), missed agent completion results (H10), multi-server confusion (H12), and jobs lost to server identity mismatch (H13). All changes target the Python backend — no frontend changes needed.

## Requirements

### H7: Zombie jobs resurrect on server restart

**Acceptance criteria:**
- Completed jobs remain completed after server restart
- Failed jobs remain failed after server restart
- Running jobs at time of crash that had all steps completed are settled as COMPLETED (not FAILED)
- Running jobs with genuinely orphaned running steps are cleaned up (marked FAILED)
- PAUSED jobs with suspended external steps are left alone for normal resumption

### H8: Server survives parallel load (SQLite contention)

**Acceptance criteria:**
- Server survives 10+ concurrent jobs without crashing
- No "database is locked" errors under normal load
- Jobs queue gracefully as PENDING when `max_concurrent_jobs` is reached
- Queued jobs auto-start FIFO when a running job completes
- Artifacts over the size limit (configurable, default 5MB) fail the step with a descriptive error
- `max_concurrent_jobs` is configurable via `.stepwise/config.yaml` and `.stepwise/config.local.yaml`

### H10: Agent results always picked up by engine

**Acceptance criteria:**
- Agent outputs are always picked up by the engine after process exit
- No stuck "running" steps when the agent has actually completed
- Thread pool exceptions are logged and converted to step failures (never silently dropped)
- A periodic watchdog detects runs stuck in RUNNING beyond a time threshold and logs warnings

### H12: Clear behavior with multiple servers

**Acceptance criteria:**
- A global registry at `~/.config/stepwise/servers.json` tracks all active servers
- Running `stepwise server start` when other servers are active shows a warning listing them
- Running `stepwise run` when a server is active for a different project shows a clear warning
- Dead servers are automatically pruned from the global registry on read
- `stepwise server status` shows global server list when requested

### H13: Jobs not silently lost to server identity confusion

**Acceptance criteria:**
- `GET /api/health` includes `project_path` in its response
- Job delegation verifies the target server's project path matches the current project
- Mismatched project path aborts delegation with a clear error and suggested fix (`--local`)
- Stale `server.pid` pointing to a dead process is cleaned up with a log warning

## Assumptions

### A1: `_cleanup_zombie_jobs` only touches RUNNING server-owned jobs

**Verified at `server.py:345-370`:** The function iterates `store.active_jobs()` which queries `WHERE status = 'running'` (`store.py:214-216`). It skips jobs where `created_by != "server"` (line 346-347) and jobs with suspended runs (line 349-351). It then iterates `store.running_runs(job.id)` (line 353) — only RUNNING step runs. COMPLETED, FAILED, and PAUSED jobs are never queried or modified.

**The actual H7 bug:** When the server crashes *after* all steps complete but *before* `_check_job_terminal()` settles the job status from RUNNING to COMPLETED (the settlement gap at `engine.py:2861-2866`), `_cleanup_zombie_jobs` sees a RUNNING job with zero running runs and unconditionally marks it FAILED (line 367). This loses valid results.

### A2: SQLite contention is caused by the single threading.Lock, not missing pragmas

**Verified at `server.py:40-74` and `server.py:84-95`:** `_LockedConnection` wraps every `execute()`, `executemany()`, `executescript()`, and `commit()` call through one `threading.Lock` (line 44-66). `busy_timeout=5000` is already set (line 92). WAL mode is already enabled (line 90). The lock serializes *all* database access — concurrent jobs waiting on the lock create memory pressure as queued operations and their result data accumulate in memory. The fix is to limit concurrency at the job level rather than trying to add more database connections.

### A3: Agent result delivery is synchronous through the thread pool

**Verified at `engine.py:2685-2743`:** `_run_executor()` is an async coroutine that calls `loop.run_in_executor(self._executor_pool, executor.start, inputs, ctx)` (line 2730-2735). After the executor returns, it pushes `("step_result", ...)` to `self._queue` (line 2738). If the executor raises, it pushes `("step_error", ...)` (line 2743). The agent executor's `start()` blocks on `backend.wait(process)` at `agent.py:667` (calls `os.waitpid` at `agent.py:184`), then reads the output file and returns `ExecutorResult`. So the result is always delivered to the queue.

**The actual H10 bug:** The `_run_executor` coroutine itself can fail *outside* the try/except — specifically, `asyncio.CancelledError` at line 2739 silently returns without pushing any event. If the event loop is under heavy load and tasks are cancelled (e.g., during shutdown or job cancellation), completed results can be dropped. Additionally, `_handle_queue_event` at line 2784-2789 silently returns if `job.status != JobStatus.RUNNING` — a race where the job was cancelled/failed between the executor finishing and the queue event being processed causes the result to be silently dropped.

### A4: Server detection is project-scoped with no global coordination

**Verified at `server_detect.py:13-50`:** `detect_server(project_dir)` checks only `{project_dir}/server.pid` (line 25). The `_server_start()` function at `cli.py:402-409` calls `detect_server(project.dot_dir)` for the current project only. No global tracking exists at `~/.config/stepwise/` or elsewhere. If two projects start servers on different ports, neither is aware of the other.

### A5: No concurrent job limit exists anywhere in the codebase

**Verified at `engine.py:2487-2508`:** `AsyncEngine.__init__` takes no `max_concurrent_jobs` parameter. `start_job()` at line 2553-2562 unconditionally transitions PENDING→RUNNING and calls `_dispatch_ready()`. The thread pool at line 2505-2508 has a max_workers default of 32, but this only limits concurrent *step* executions, not concurrent *jobs*. There is no queue, semaphore, or counter for job-level concurrency.

### A6: StepwiseConfig uses `to_dict()`/`from_dict()` serialization pattern

**Verified at `config.py:103-178`:** `StepwiseConfig` is a `@dataclass` with `to_dict()` (line 133) and `from_dict()` (line 155). New fields must follow this pattern: add the field with a default, include it in `to_dict()` only if non-default, and parse it in `from_dict()` with `d.get()` fallback.

### A7: The existing events.py does not have a JOB_QUEUED event type

**Verified at `events.py`:** Event types include `JOB_STARTED`, `JOB_COMPLETED`, `JOB_FAILED`, `JOB_PAUSED`, `JOB_RESUMED`. There is no `JOB_QUEUED`. A new constant must be added.

## Out of Scope

- **H9** (orphaned agent steps with PID tracking) — requires careful cross-platform process management design, separate concern
- **H11** (zombie claude processes) — requires claude-specific process detection and lifecycle, separate issue
- **H14-H18** (agent reliability) — separate batch focused on agent executor robustness
- **Frontend changes** — all fixes are Python backend only; the web UI already reacts to status changes via WebSocket
- **ORM or repository abstraction** — raw SQL per project guardrails (`CLAUDE.md` guardrail #7)
- **Connection pooling or multiple SQLite connections** — WAL mode + single `_LockedConnection` is the project's established pattern; adding pooling would be a larger architectural change that isn't needed when concurrent jobs are limited
- **Multi-host/distributed server deployment** — single-machine only, consistent with existing architecture

## Architecture

### Module dependency compliance

All changes respect the strict module DAG (`models → executors → engine → server`) per `CLAUDE.md` guardrail #1:

- `store.py` (data layer): new `pending_jobs()` query — no new imports
- `config.py` (data layer): new fields on `StepwiseConfig` — no new imports
- `events.py` (data layer): new `JOB_QUEUED` constant — no imports
- `engine.py`: new `max_concurrent_jobs` logic, `recover_jobs()`, watchdog — imports only from `models`, `store`, `executors` (existing)
- `server.py`: enhanced cleanup, registration calls — imports from `engine`, `server_detect` (existing)
- `server_detect.py` (utility): new global registry functions — no engine/server imports (standalone)
- `runner.py`: identity verification before delegation — imports from `server_detect` (existing)
- `cli.py`: warnings on server start/run — imports from `server_detect` (existing)

### Global server registry data schema (H12)

File: `~/.config/stepwise/servers.json` (follows existing `~/.config/stepwise/` convention from `config.py:21`)

```json
{
  "servers": [
    {
      "project_path": "/home/user/project-a",
      "dot_dir": "/home/user/project-a/.stepwise",
      "pid": 12345,
      "port": 8340,
      "url": "http://localhost:8340",
      "started_at": "2026-03-21T10:00:00+00:00"
    }
  ]
}
```

**Schema rules:**
- `project_path` is the canonical key (resolved absolute path, matches `StepwiseProject.root`)
- `dot_dir` is the `.stepwise/` directory (matches `StepwiseProject.dot_dir`)
- On read: prune entries where `_pid_alive(pid)` returns false (reuses `server_detect.py:102-108`)
- On write: atomic write via `tempfile.NamedTemporaryFile` + `os.replace()` (prevents corruption from concurrent writers — follows pattern used by `write_pidfile` at `server_detect.py:53-76`)
- On register: upsert by `project_path` (if project already has an entry, replace it)
- Directory auto-created: `CONFIG_DIR.mkdir(parents=True, exist_ok=True)` (follows `config.py:21` convention)

### Concurrent job limiting architecture (H8)

Follows the existing PENDING→RUNNING lifecycle. Jobs stay PENDING when the limit is reached; no new job states or database schema changes needed.

**Data flow:**
1. `start_job()` (engine.py:2553) checks `len(store.active_jobs()) >= max_concurrent_jobs`
2. If over limit: emit `JOB_QUEUED` event, return without status change (job stays PENDING)
3. When any job reaches terminal state: `_check_job_terminal()` (engine.py:2857) calls `_start_queued_jobs()`
4. `_start_queued_jobs()` calls `store.pending_jobs()` → starts the oldest PENDING job if below limit
5. `pending_jobs()` returns `WHERE status = 'pending' ORDER BY created_at` — FIFO ordering

**Why this works with the existing architecture:**
- `start_job()` already requires `status == PENDING` (line 2556) — re-calling it for a queued job is safe
- `_dispatch_ready()` is idempotent (line 2620) — no risk of double-launching
- The server's `/api/jobs/{id}/start` endpoint calls `engine.start_job()` — queueing is transparent to the API layer

### Artifact size validation architecture (H8)

Added to `_process_launch_result()` (engine.py:1297-1410), the single chokepoint where all executor results flow. Size check happens after the executor returns but before `store.save_run()` persists the artifact. Follows the existing `_validate_artifact()` pattern (engine.py:2182-2207) — returns an error string on failure, `None` on success.

### Health endpoint enhancement (H13)

The existing health endpoint at `server.py` returns `{"status": "ok"}`. This is checked by `_probe_health()` at `server_detect.py:111-122` which only inspects `data.get("status") == "ok"`. Adding `project_path` and `version` fields is backward-compatible — the probe ignores extra fields.

### Engine recovery architecture (H7)

New `recover_jobs()` method on `AsyncEngine`, called from `lifespan()` (server.py:395) after `_cleanup_zombie_jobs()` and before `_engine.run()` task starts. For each RUNNING server-owned job:
1. If all steps completed but job unsettled → call `_check_job_terminal()` to settle as COMPLETED
2. If steps still running (shouldn't happen after cleanup) → no action (engine loop will handle)

This is safe because `_check_job_terminal()` (engine.py:2857-2886) is idempotent and only acts on RUNNING jobs.

### Step result watchdog architecture (H10)

A periodic check in the engine's `_poll_external_changes()` method (engine.py:2541-2549, called every 5 seconds on queue timeout). For each RUNNING step run:
1. If `started_at` is older than 30 minutes AND no task exists in `self._tasks` for the run → the thread pool task was lost
2. Log a warning and mark the run FAILED with error "Executor result lost (watchdog recovery)"
3. Call `_after_step_change()` to continue the job

This reuses the existing 5-second poll cycle rather than adding a new timer.

## Implementation Steps

### Step 1: H7a — Fix `_cleanup_zombie_jobs` to settle completed jobs (~20 min)

**Depends on:** nothing
**Why first:** Foundation fix — prevents data loss on every server restart

**File: `src/stepwise/server.py`, function `_cleanup_zombie_jobs()` (lines 337-370)**

Currently, line 367 unconditionally sets `job.status = JobStatus.FAILED` after processing running runs. When the server crashes in the settlement gap (all steps COMPLETED, job still RUNNING), there are zero running runs — the for-loop at line 353 does nothing — and the job is incorrectly failed.

**Change:** After the running_runs loop, before setting job status, check whether the job is actually complete. Reuse the existing `_job_complete()` check pattern from `engine.py:975-1002`:

1. Check `store.running_runs(job.id)` — if empty after cleanup
2. Check terminal steps for completed runs (follows `_job_complete` pattern at engine.py:989-992)
3. If terminal steps have completed runs → mark COMPLETED instead of FAILED
4. If no terminal steps completed → mark FAILED (genuinely orphaned job)

The check must be done against the store directly (no engine instance available yet), so extract the terminal-step check as a helper function that takes `(store, job)`.

### Step 2: H7b — Add `recover_jobs()` to AsyncEngine (~20 min)

**Depends on:** Step 1 (cleanup must be correct before recovery runs)
**Why this order:** Cleanup fixes the "wrongly failed" bug; recovery handles the settlement gap more robustly

**File: `src/stepwise/engine.py`, class `AsyncEngine` (add after line 2508)**

Add method `recover_jobs(self) -> None`:
1. Iterate `self.store.active_jobs()` (returns RUNNING jobs only, per store.py:214)
2. For each job where `job.created_by == "server"`:
   - Call `self._check_job_terminal(job.id)` — this is the same method used in normal operation (line 2857)
   - It will settle the job as COMPLETED or FAILED based on actual step states

**File: `src/stepwise/server.py`, function `lifespan()` (line 395)**

After `_cleanup_zombie_jobs(store)`, before `_engine_task = asyncio.create_task(_engine.run())`:
```python
_engine.recover_jobs()
```

### Step 3: H8a — Add `pending_jobs()` store method and `JOB_QUEUED` event (~15 min)

**Depends on:** nothing (pure additions, no existing code changes)
**Why before Step 4:** Step 4 (engine limiter) needs these primitives

**File: `src/stepwise/store.py`**

Add `pending_jobs()` method after `active_jobs()` (line 217), following the same query pattern:
```python
def pending_jobs(self) -> list[Job]:
    """Return PENDING jobs ordered by creation time (FIFO queue)."""
    rows = self._conn.execute(
        "SELECT * FROM jobs WHERE status = ? ORDER BY created_at",
        (JobStatus.PENDING.value,),
    ).fetchall()
    return [self._row_to_job(r) for r in rows]
```

**File: `src/stepwise/events.py`**

Add constant alongside existing job events:
```python
JOB_QUEUED = "job.queued"
```

### Step 4: H8b — Add concurrent job limit to AsyncEngine (~45 min)

**Depends on:** Step 3 (`pending_jobs()` and `JOB_QUEUED`)
**Why this order:** Core throttling logic, needs store method first

**File: `src/stepwise/engine.py`, class `AsyncEngine`**

1. Add `max_concurrent_jobs` parameter to `__init__` (line 2487-2508), default `0` (no limit):
   ```python
   def __init__(self, ..., max_concurrent_jobs: int = 0) -> None:
       ...
       self.max_concurrent_jobs = max_concurrent_jobs
   ```

2. Override `start_job()` (line 2553-2562) to check the limit before starting:
   - If `self.max_concurrent_jobs > 0` and `len(self.store.active_jobs()) >= self.max_concurrent_jobs`:
   - Emit `JOB_QUEUED` event (import from `events.py`)
   - Return without changing status (job stays PENDING)
   - Broadcast `{"type": "job_queued", "job_id": job_id}` for the UI

3. Add `_start_queued_jobs()` private method:
   - Called from `_check_job_terminal()` (line 2857) after a job reaches terminal state
   - Calls `self.store.pending_jobs()`
   - For each pending job (up to available slots): call `self.start_job(job.id)`
   - Catches `ValueError` in case job was already started by another code path

4. In `_check_job_terminal()` (line 2857), after the terminal state block (after line 2886), call `self._start_queued_jobs()`.

### Step 5: H8c — Add config fields and wire to engine (~20 min)

**Depends on:** Step 4 (engine accepts the config values)
**Why this order:** Config wiring depends on engine parameter existing

**File: `src/stepwise/config.py`, class `StepwiseConfig` (line 103-178)**

Add two fields after `notify_context` (line 114):
```python
max_concurrent_jobs: int = 0          # 0 = unlimited
max_artifact_bytes: int = 5_242_880   # 5MB default
```

In `to_dict()` (line 133): add both fields only if non-default (follows existing pattern — e.g., `billing` at line 145).

In `from_dict()` (line 155): parse with `d.get("max_concurrent_jobs", 0)` and `d.get("max_artifact_bytes", 5_242_880)`.

**File: `src/stepwise/server.py`, function `lifespan()` (line 392)**

Pass config values to AsyncEngine constructor:
```python
_engine = AsyncEngine(
    store, registry, ...,
    max_concurrent_jobs=config.max_concurrent_jobs,
)
```

Store `config.max_artifact_bytes` on the engine or pass to `_process_launch_result` — simplest approach: add `self.max_artifact_bytes` to Engine base `__init__`.

### Step 6: H8d — Add artifact size validation (~20 min)

**Depends on:** Step 5 (`max_artifact_bytes` on engine)
**Why this order:** Needs the config field wired to the engine

**File: `src/stepwise/engine.py`, method `_process_launch_result()` (lines 1297-1410)**

In the `case "data"` branch (line 1306), after the `is_failure` check at line 1316 but before `_validate_artifact` at line 1326, add artifact size validation:

```python
if not is_failure and result.envelope and result.envelope.artifact:
    import json
    size = len(json.dumps(result.envelope.artifact))
    if self.max_artifact_bytes and size > self.max_artifact_bytes:
        # Follows existing _validate_artifact pattern
        run.status = StepRunStatus.FAILED
        run.error = (
            f"Artifact size {size:,} bytes exceeds limit "
            f"({self.max_artifact_bytes:,} bytes)"
        )
        run.completed_at = _now()
        self.store.save_run(run)
        self._emit(job.id, STEP_FAILED, {
            "step": step_name, "attempt": attempt,
            "error": run.error,
        })
        self._halt_job(job, run)
        return
```

This mirrors the validation-failure path at lines 1328-1338.

### Step 7: H10a — Harden thread pool result delivery (~30 min)

**Depends on:** nothing (independent fix)
**Why this position:** After H8 changes are stable, avoids merge conflicts in engine.py

**File: `src/stepwise/engine.py`, method `_run_executor()` (lines 2685-2743)**

The current exception handling at line 2738-2743 correctly catches general exceptions and pushes `step_error`. But there are two gaps:

1. **Gap 1:** If `await self._queue.put(("step_result", ...))` at line 2738 itself raises (e.g., queue full, loop closing), the result is lost. Wrap line 2738 in a try/except that logs the error:
   ```python
   try:
       await self._queue.put(("step_result", ...))
   except Exception as qe:
       _async_logger.error(
           "Failed to queue step result for %s: %s", step_name, qe
       )
   ```

2. **Gap 2:** The `except asyncio.CancelledError: return` at line 2739-2741 silently drops results if the task is cancelled *after* the executor completes but *before* the queue put. Add logging:
   ```python
   except asyncio.CancelledError:
       _async_logger.warning(
           "Executor task cancelled for %s (job %s, run %s)",
           step_name, job_id, run_id,
       )
       return
   ```

**File: `src/stepwise/engine.py`, method `_handle_queue_event()` (lines 2776-2839)**

The silent returns at lines 2787-2789 (job not found or not RUNNING) and lines 2795-2796 (run already handled) should log at debug level so dropped events are traceable:
```python
if job.status != JobStatus.RUNNING:
    _async_logger.debug("Dropping step_result for %s: job status is %s", run_id, job.status.value)
    return
```

### Step 8: H10b — Add stuck-run watchdog (~30 min)

**Depends on:** Step 7 (hardened delivery first, watchdog catches remaining cases)
**Why this order:** Fix the common case first, watchdog handles edge cases

**File: `src/stepwise/engine.py`, method `_poll_external_changes()` (lines 2541-2549)**

After the existing `_dispatch_ready` / `_check_job_terminal` loop, add a stuck-run check:

```python
# Watchdog: detect runs stuck in RUNNING with no active task
for job in self.store.active_jobs():
    for run in self.store.running_runs(job.id):
        if run.id in self._tasks:
            continue  # task is still active
        age = (_now() - run.started_at).total_seconds()
        if age > 1800:  # 30 minutes
            _async_logger.warning(
                "Watchdog: run %s (%s) stuck RUNNING for %ds with no task",
                run.id, run.step_name, int(age),
            )
            run.status = StepRunStatus.FAILED
            run.error = "Executor result lost (watchdog recovery)"
            run.completed_at = _now()
            self.store.save_run(run)
            self._emit(job.id, STEP_FAILED, {
                "step": run.step_name, "error": run.error,
            })
            self._after_step_change(job.id)
```

This runs every 5 seconds (the existing `_poll_external_changes` cadence at engine.py:2530-2534). The 30-minute threshold is conservative — most agent steps complete in under 10 minutes.

### Step 9: H12a — Global server registry CRUD functions (~45 min)

**Depends on:** nothing (new standalone functions)
**Why this position:** H12b and H13 depend on these functions

**File: `src/stepwise/server_detect.py`**

Add four functions after `remove_pidfile()` (line 99), before `_pid_alive()` (line 102):

1. **`_global_registry_path() -> Path`**: Returns `Path.home() / ".config" / "stepwise" / "servers.json"`. Follows convention from `config.py:21` (`CONFIG_DIR = Path.home() / ".config" / "stepwise"`).

2. **`register_server(project_path: str, dot_dir: str, pid: int, port: int, url: str) -> None`**:
   - Read existing registry (or empty `{"servers": []}`)
   - Remove any existing entry with same `project_path` (upsert)
   - Append new entry with `project_path`, `dot_dir`, `pid`, `port`, `url`, `started_at` (ISO 8601)
   - Atomic write: `tempfile.NamedTemporaryFile(dir=parent, delete=False)` + `os.replace(tmp, path)`
   - Create parent dir: `path.parent.mkdir(parents=True, exist_ok=True)`

3. **`unregister_server(project_path: str) -> None`**:
   - Read registry, remove entry matching `project_path`, atomic write back
   - No-op if entry not found or file doesn't exist

4. **`list_active_servers() -> list[dict]`**:
   - Read registry, prune entries where `_pid_alive(entry["pid"])` returns false
   - Write back pruned list (atomic) if any were removed
   - Return surviving entries

### Step 10: H12b — Wire global registry into server lifecycle and CLI (~45 min)

**Depends on:** Step 9 (registry CRUD functions)
**Why this order:** Can't wire what doesn't exist yet

**File: `src/stepwise/server.py`, function `lifespan()` (lines 373-429)**

After `_cleanup_zombie_jobs(store)` (line 395), register the server:
```python
from stepwise.server_detect import register_server
register_server(
    project_path=str(_project_dir),
    dot_dir=str(dot_dir),
    pid=os.getpid(),
    port=int(os.environ.get("STEPWISE_PORT", 8340)),
    url=f"http://localhost:{os.environ.get('STEPWISE_PORT', 8340)}",
)
```

In the shutdown block (after line 402, the `yield`), add unregistration:
```python
from stepwise.server_detect import unregister_server
unregister_server(str(_project_dir))
```

Need to pass port through — add `STEPWISE_PORT` env var in `cli.py:_server_start()` alongside the other env vars at lines 424-428.

**File: `src/stepwise/cli.py`, function `_server_start()` (lines 396-459)**

After the existing `detect_server()` check (line 404), add cross-project warning:
```python
from stepwise.server_detect import list_active_servers
others = list_active_servers()
if others:
    for s in others:
        if s["project_path"] != str(project.root):
            io.log("warn", f"Another server running: {s['url']} → {s['project_path']}")
```

Add `os.environ["STEPWISE_PORT"] = str(port)` alongside the other env vars at line 424-428.

In `_server_start_detached()` (line 462-515), register the server after health check succeeds (line 505):
```python
register_server(str(project.root), str(project.dot_dir), ..., port, url)
```

In `_stop_server_for_project()` (line 518), call `unregister_server(str(project.root))` after killing the process.

### Step 11: H13 — Server identity verification in health endpoint and delegation (~45 min)

**Depends on:** Step 9 (uses `_probe_health` pattern from `server_detect.py`)
**Why last among feature steps:** builds on the global registry from Steps 9-10

**File: `src/stepwise/server.py`**

Find the existing `/api/health` endpoint (search for `"/api/health"`). Enhance the response:
```python
@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "project_path": str(_project_dir) if _project_dir else None,
    }
```

**File: `src/stepwise/server_detect.py`**

Add `verify_server_identity(url: str, expected_project: Path) -> tuple[bool, str | None]` after `list_active_servers()`:
- GET `{url}/api/health` with 2-second timeout (reuses `_probe_health` pattern at line 111-122)
- Parse `project_path` from response
- Return `(True, None)` if paths match (compare resolved absolute paths)
- Return `(False, actual_path)` if mismatch
- Return `(True, None)` on any error (fail-open: don't block delegation on probe failure)

Enhance `detect_server()` (line 37-44) to log when cleaning up a stale pidfile:
```python
if pid and not _pid_alive(pid):
    import logging
    logging.getLogger("stepwise.server_detect").warning(
        "Stale server.pid (PID %d dead, was at port %d). Cleaning up.",
        pid, port,
    )
    ...
```

**File: `src/stepwise/runner.py`**

In `_async_run_flow()` around line 454-458, after `detect_server()` returns a URL:
```python
from stepwise.server_detect import verify_server_identity
match, actual = verify_server_identity(server_url, project.root)
if not match:
    adapter.log("warn",
        f"Server at {server_url} serves project '{actual}', "
        f"not '{project.root}'. Use --local to bypass.")
```

Same pattern in `_wait_run_flow()` around line 889-891 and `_async_run_async_flow()` around line 1461-1464 — all three delegation entry points.

## Implementation Step Dependencies

```
Step 1 (H7a: fix cleanup) ──→ Step 2 (H7b: engine recovery)
                                     │
Step 3 (H8a: store+event) ──→ Step 4 (H8b: engine limiter)
                                     │
                               Step 5 (H8c: config wiring)
                                     │
                               Step 6 (H8d: artifact size)

Step 7 (H10a: harden delivery) ──→ Step 8 (H10b: watchdog)

Step 9 (H12a: registry CRUD) ──→ Step 10 (H12b: wire CLI/server)
                                       │
                                 Step 11 (H13: identity verification)
```

Steps 1-2, 3-6, 7-8, and 9-11 are four independent chains that can be parallelized. Within each chain, ordering is sequential as noted.

## Testing Strategy

### Test file: `tests/test_server_reliability.py` (new)

Uses existing fixtures from `tests/conftest.py:138-200`: `store()`, `registry()`, `async_engine()`, `register_step_fn()`, `run_job_sync()`.

#### H7 tests

**`test_completed_job_survives_cleanup`:**
```python
def test_completed_job_survives_cleanup(store):
    """Completed jobs must not be touched by cleanup."""
    from stepwise.server import _cleanup_zombie_jobs, ThreadSafeStore
    # Create a COMPLETED job with created_by="server"
    job = Job(id="job-1", ..., status=JobStatus.COMPLETED, created_by="server")
    store.save_job(job)
    ts_store = _wrap_as_threadsafe(store)  # helper
    _cleanup_zombie_jobs(ts_store)
    assert store.load_job("job-1").status == JobStatus.COMPLETED
```

**`test_failed_job_survives_cleanup`:** Same structure, `status=JobStatus.FAILED`, assert remains FAILED.

**`test_running_job_with_orphaned_steps_is_failed`:**
- Create RUNNING job (created_by="server") with a RUNNING step run
- Run `_cleanup_zombie_jobs()`
- Assert job status is FAILED, step run status is FAILED with "Server restarted" error

**`test_running_job_with_completed_steps_is_settled`:**
- Create RUNNING job (created_by="server") with a COMPLETED step run on a terminal step
- Run `_cleanup_zombie_jobs()`
- Assert job status is COMPLETED (not FAILED)

**`test_suspended_job_not_touched_by_cleanup`:**
- Create RUNNING job (created_by="server") with a SUSPENDED step run
- Run `_cleanup_zombie_jobs()`
- Assert job status is still RUNNING (skipped by suspended check at line 349)

**`test_cli_owned_job_not_touched_by_cleanup`:**
- Create RUNNING job (created_by="cli:12345")
- Run `_cleanup_zombie_jobs()`
- Assert job status unchanged (skipped by created_by check at line 346)

**`test_engine_recover_jobs`:**
- Create RUNNING job with all steps COMPLETED but job not settled
- Call `engine.recover_jobs()`
- Assert job status is COMPLETED

#### H8 tests

**`test_concurrent_job_limit_queues_excess`:**
```python
def test_concurrent_job_limit_queues_excess(store, registry):
    engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=2)
    register_step_fn("noop", lambda inputs: {"ok": True})
    # Create 3 jobs with one step each
    for i in range(3):
        wf = WorkflowDefinition(steps={"step": StepDefinition(
            name="step", executor=ExecutorRef(type="callable", config={"fn_name": "noop"}),
            outputs=["ok"],
        )})
        job = engine.create_job(objective=f"test-{i}", workflow=wf)
        engine.start_job(job.id)
    # First 2 should be RUNNING, 3rd should be PENDING (queued)
    assert len(store.active_jobs()) == 2
    assert len(store.pending_jobs()) == 1
```

**`test_queued_job_starts_on_completion`:**
- Create engine with `max_concurrent_jobs=1`
- Start 2 jobs: first RUNNING, second PENDING
- Run first job to completion via `run_job_sync()`
- Assert second job is now RUNNING

**`test_artifact_size_guard`:**
```python
def test_artifact_size_guard(store, registry):
    engine = AsyncEngine(store=store, registry=registry)
    engine.max_artifact_bytes = 100  # tiny limit for testing
    register_step_fn("big", lambda inputs: {"data": "x" * 200})
    wf = WorkflowDefinition(steps={"step": StepDefinition(
        name="step", executor=ExecutorRef(type="callable", config={"fn_name": "big"}),
        outputs=["data"],
    )})
    job = engine.create_job(objective="test", workflow=wf)
    result = run_job_sync(engine, job.id)
    assert result.status == JobStatus.FAILED
    runs = store.runs_for_job(job.id)
    assert "exceeds limit" in runs[0].error
```

**`test_pending_jobs_fifo_ordering`:**
- Insert 3 PENDING jobs with ascending `created_at` timestamps
- Call `store.pending_jobs()`
- Assert returned in chronological order

**`test_no_limit_when_zero`:**
- Create engine with `max_concurrent_jobs=0` (default)
- Start 20 jobs
- All should be RUNNING (no queueing)

#### H10 tests

**`test_executor_exception_becomes_step_failure`:**
```python
def test_executor_exception_becomes_step_failure(async_engine):
    register_step_fn("crash", lambda inputs: (_ for _ in ()).throw(RuntimeError("boom")))
    wf = WorkflowDefinition(steps={"step": StepDefinition(
        name="step", executor=ExecutorRef(type="callable", config={"fn_name": "crash"}),
        outputs=["result"],
    )})
    job = async_engine.create_job(objective="test", workflow=wf)
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.FAILED
    runs = async_engine.store.runs_for_job(job.id)
    assert runs[0].status == StepRunStatus.FAILED
    assert "boom" in runs[0].error
```

**`test_watchdog_detects_stuck_run`:**
- Create a RUNNING job with a RUNNING step run whose `started_at` is 31 minutes ago
- Ensure run.id is NOT in `engine._tasks`
- Call `engine._poll_external_changes()`
- Assert run is now FAILED with "watchdog recovery" in error

#### H12 tests

**`test_global_registry_register_and_list`:**
```python
def test_global_registry_register_and_list(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from stepwise.server_detect import register_server, list_active_servers
    register_server("/project/a", "/project/a/.stepwise",
                    pid=os.getpid(), port=8340, url="http://localhost:8340")
    servers = list_active_servers()
    assert len(servers) == 1
    assert servers[0]["project_path"] == "/project/a"
```

**`test_global_registry_prunes_dead_pids`:**
```python
def test_global_registry_prunes_dead_pids(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    register_server("/project/a", "/project/a/.stepwise",
                    pid=99999999, port=8340, url="http://localhost:8340")
    servers = list_active_servers()
    assert len(servers) == 0  # dead PID pruned
```

**`test_global_registry_upsert_by_project`:**
- Register same project_path twice with different ports
- List → assert only one entry with the second port

**`test_global_registry_unregister`:**
- Register, unregister, list → empty

**`test_global_registry_atomic_write`:**
- Verify registry file is valid JSON after concurrent register calls (use threads)

#### H13 tests

**`test_health_includes_project_path`:**
```python
@pytest.mark.asyncio
async def test_health_includes_project_path():
    from httpx import AsyncClient, ASGITransport
    from stepwise.server import app
    os.environ["STEPWISE_PROJECT_DIR"] = "/test/project"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/health")
        assert resp.json()["project_path"] is not None
```

**`test_verify_identity_match`:**
- Mock `_probe_health` to return `{"status": "ok", "project_path": "/project/a"}`
- Call `verify_server_identity("http://localhost:8340", Path("/project/a"))`
- Assert returns `(True, None)`

**`test_verify_identity_mismatch`:**
- Same mock but expected path is `/project/b`
- Assert returns `(False, "/project/a")`

**`test_stale_pidfile_cleaned_up`:**
```python
def test_stale_pidfile_cleaned_up(tmp_path):
    from stepwise.server_detect import detect_server, write_pidfile
    write_pidfile(tmp_path, 8340, pid=99999999)
    assert (tmp_path / "server.pid").exists()
    result = detect_server(tmp_path)
    assert result is None
    assert not (tmp_path / "server.pid").exists()
```

### Running tests

```bash
# Run all new reliability tests
uv run pytest tests/test_server_reliability.py -v

# Run by issue
uv run pytest tests/test_server_reliability.py -k "cleanup or recover or settle" -v  # H7
uv run pytest tests/test_server_reliability.py -k "concurrent or artifact or pending or queued" -v  # H8
uv run pytest tests/test_server_reliability.py -k "executor_exception or watchdog" -v  # H10
uv run pytest tests/test_server_reliability.py -k "registry" -v  # H12
uv run pytest tests/test_server_reliability.py -k "health or identity or stale_pidfile" -v  # H13

# Full regression suite
uv run pytest tests/ -x -q
cd web && npm run test
```

### Manual smoke tests

1. **H7:** Start server → `stepwise run` a simple flow → wait for completion → kill server process (`kill -9`) → restart server → verify job shows as COMPLETED in UI
2. **H8:** Start server → submit 15 concurrent jobs via shell loop → watch server logs for errors → verify all complete eventually
3. **H10:** Start server → run a flow with an agent step → verify output appears in UI → repeat 5x under load
4. **H12:** In project A: `stepwise server start` → In project B: `stepwise server start` → verify warning about project A's server
5. **H13:** Start server in project A → kill it → cd to project B with `.stepwise/` using same port → `stepwise run` → verify warning about stale pidfile

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| `_cleanup_zombie_jobs` settlement check incorrectly marks a partially-complete job as COMPLETED | Job completes with missing steps | Low — terminal step check follows exact `_job_complete` logic from engine.py:975-1002 | Test with multiple step topologies; settlement always marks unrun steps as SKIPPED |
| `max_concurrent_jobs` causes indefinite PENDING if slot never frees | Jobs stuck forever | Low — slots free when jobs reach COMPLETED/FAILED/CANCELLED | The watchdog (Step 8) also applies to stuck jobs; user can always cancel |
| Atomic write to `servers.json` fails on some filesystems | Registry corruption | Very low — `os.replace()` is atomic on POSIX and NTFS | Wrap in try/except, fall back to direct write |
| Artifact size check uses `json.dumps()` which costs CPU for large artifacts | Performance hit on large outputs | Low — only triggers for artifacts that would cause problems anyway | The JSON serialization already happens for store persistence; this adds one extra pass but only for large artifacts |
| Watchdog false-positive kills a legitimately long-running step | Step incorrectly failed | Low — 30-minute threshold is very conservative for most steps | Only kills runs where `run.id not in self._tasks` (no active executor), so only orphaned runs |
| Health endpoint adding `project_path` breaks existing clients | Client parse errors | None — existing `_probe_health()` only checks `data.get("status") == "ok"` and ignores extra fields | Backward-compatible additive change |
| Global registry file read by multiple processes simultaneously | Stale reads | Low — registry is advisory, not authoritative | Each process independently verifies PID liveness; stale entries are pruned on every read |
