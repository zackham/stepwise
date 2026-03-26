# S9-8: Multi-Job Wait

## Overview

Extend `stepwise wait` to accept multiple job IDs with `--all` (block until all terminal) and `--any` (block until first terminal) semantics. Output per-job JSON status. Write sentinel files to `.stepwise/completed/{job_id}.json` on job completion for file-based watchers. Apply the N44 404-retry pattern to each job individually.

## Requirements

| # | Requirement | Acceptance Criteria |
|---|---|---|
| R1 | `stepwise wait --all job-A job-B` blocks until **all** jobs reach terminal state or suspension | Exit code 0 only if all completed; 1 if any failed; 4 if any cancelled (and none failed); 5 if any suspended (and none failed/cancelled) |
| R2 | `stepwise wait --any job-A job-B` blocks until **first** job reaches terminal state or suspension | Returns immediately when the first job resolves; reports that job's status; exit code matches the first-resolved job |
| R3 | JSON output includes per-job status array | `{"mode": "all"|"any", "jobs": [{"job_id": "...", "status": "...", ...}], "summary": {"completed": N, "failed": N, ...}}` |
| R4 | Single-job `stepwise wait <job-id>` remains backward-compatible | Identical JSON output shape as today (no `mode`/`jobs` wrapper); same exit codes |
| R5 | N44 404-retry applied per-job | Each job's status fetch retries independently on 404 with exponential backoff; one job's 404 exhaustion doesn't block others |
| R6 | Sentinel files at `.stepwise/completed/{job_id}.json` on job terminal | File contains `{"job_id": "...", "status": "...", "completed_at": "..."}`. Written atomically (write-to-temp + rename). Created by both `--all` and `--any` wait, and by single-job wait. |
| R7 | `StepwiseClient.wait_many()` method | Provides programmatic access for orchestrators; same semantics as CLI |
| R8 | SIGINT/SIGTERM cancels all watched jobs; SIGTSTP detaches cleanly | Same signal contract as single-job wait |
| R9 | Tests cover all combinations | All/any × completed/failed/suspended/cancelled/mixed; 404-retry; sentinel file creation; signal handling |

### Exit Code Semantics (multi-job)

For `--all`, exit code reflects the **worst** status across all jobs:
- 0 = all completed (`EXIT_SUCCESS`, `runner.py:36`)
- 1 = any failed (`EXIT_JOB_FAILED`, `runner.py:37`)
- 4 = any cancelled (currently hardcoded literal `4` at `runner.py:1087` and `runner.py:1371`; will be named `EXIT_CANCELLED`)
- 5 = any suspended (`EXIT_SUSPENDED`, `runner.py:40`)

For `--any`, exit code reflects the **first resolved** job's status (same as single-job).

## Assumptions

| # | Assumption | Verified Against |
|---|---|---|
| A1 | `cmd_wait` is the CLI handler at `cli.py:3460-3491`, dispatched via the `handlers` dict at `cli.py:5272` (`"wait": cmd_wait`) | Read `cli.py:3460-3491` — handler body. Read `cli.py:5272` — dispatch entry. |
| A2 | `wait` subparser at `cli.py:4207-4208` takes a single positional `job_id`: `p_wait.add_argument("job_id", help="Job ID to wait on")` | Read `cli.py:4207-4208` — confirmed exact argparse definition. |
| A3 | Server path: `cmd_wait` calls `_detect_server_url(args)` at `cli.py:3467`, then `wait_for_job_id(server_url, args.job_id)` at `cli.py:3470`. `wait_for_job_id()` is defined at `runner.py:1486-1497` and calls `asyncio.run(_delegated_wait_ws_loop(server_url, job_id))`. | Read `cli.py:3467-3470` and `runner.py:1486-1497` — confirmed call chain. |
| A4 | Direct (no-server) path: `cmd_wait` creates `Engine` at `cli.py:3488`, calls `wait_for_job(engine, store, args.job_id)` at `cli.py:3489`. `wait_for_job()` is defined at `runner.py:1336-1454` as a sync tick-based loop. | Read `cli.py:3480-3489` and `runner.py:1336-1454` — confirmed. |
| A5 | `_fetch_job_state()` at `runner.py:168-203` implements N44 404-retry: 8 retries, 0.5s base backoff with `2**attempt` multiplier, raises `JobNotFoundError` on exhaustion. Used by `_delegated_wait_ws_loop` at `runner.py:1115`. | Read `runner.py:168-203` — confirmed retry loop. Read `runner.py:1115` — confirmed usage. |
| A6 | `StepwiseClient.wait()` at `api_client.py:158-191` has its own 404-retry: `max_retries=8`, `not_found_count` tracking, `time.sleep(min(0.5 * (2 ** (not_found_count - 1)), 8))` backoff. | Read `api_client.py:165-176` — confirmed retry logic. |
| A7 | No `.stepwise/completed/` directory or sentinel file pattern exists anywhere in the codebase. | Grep for `completed_dir`, `.stepwise/completed` across `src/stepwise/` — zero matches. |
| A8 | `_json_stdout()` at `runner.py:1627-1631` writes `json.dumps(data, default=str) + "\n"` to `sys.stdout`. `_json_error()` at `runner.py:1634-1641` writes structured error JSON. | Read `runner.py:1627-1641` — confirmed signatures and behavior. |
| A9 | Exit codes in `runner.py:36-40`: `EXIT_SUCCESS=0`, `EXIT_JOB_FAILED=1`, `EXIT_USAGE_ERROR=2`, `EXIT_CONFIG_ERROR=3`, `EXIT_SUSPENDED=5`. In `cli.py:57-62`: same set plus `EXIT_PROJECT_ERROR=4`. The value `4` for cancelled is used as literal `return 4` at `runner.py:1087` and `runner.py:1252` and `runner.py:1371`. | Read `runner.py:36-40`, `cli.py:57-62`, `runner.py:1087`, `runner.py:1252`, `runner.py:1371` — confirmed. Note: `EXIT_PROJECT_ERROR=4` in cli.py shares value `4` with cancelled — acceptable since they're in different domains (argument errors vs job outcomes). |
| A10 | Job terminal states in `models.py:29-37`: `JobStatus` enum has `COMPLETED`, `FAILED`, `CANCELLED` as terminal states. Suspension detection via `_is_blocked_by_suspension()` at `runner.py:1457-1470` checks `StepRunStatus.SUSPENDED` with no `RUNNING`/`DELEGATED` runs. | Read `models.py:29-37` and `runner.py:1457-1470` — confirmed. |
| A11 | `_delegated_wait_ws_loop()` at `runner.py:1031-1207` handles WS connection at `runner.py:1066-1067` via `websockets.connect(ws_url)`, falls back to REST polling at 2s interval (`runner.py:1111`). Signal handlers set at `runner.py:1041-1056`. | Read `runner.py:1031-1207` — confirmed full structure. |
| A12 | `_async_wait_for_job()` at `runner.py:1210-1334` is the async engine wait loop (creates `engine.run()` task at `runner.py:1216`, polls status in loop). Returns exit codes matching `_delegated_wait_ws_loop`. | Read `runner.py:1210-1334` — confirmed parallel structure. |
| A13 | `.stepwise/.gitignore` content at `project.py:108`: `"*\n!config.yaml\nconfig.local.yaml\n"` — wildcard ignores everything inside `.stepwise/` including any new `completed/` subdirectory. No project.py change needed. | Read `project.py:108` — confirmed. |
| A14 | `_detect_server_url(args)` at `cli.py:202-218` resolves server URL from `--standalone`, `--server`, or auto-detect via pidfile. Calls `_find_project_or_exit(args)` at `cli.py:214` internally. | Read `cli.py:202-218` — confirmed. |
| A15 | CLI docstring at `cli.py:19` documents current single-job syntax: `stepwise wait <job-id>`. Must be updated for multi-job. | Read `cli.py:19` — confirmed. |

## Implementation Steps

### Step 1: Add `EXIT_CANCELLED` constant and unify exit codes (~15 min)

**Depends on:** nothing (foundation step)

**File:** `src/stepwise/runner.py`

At `runner.py:36-40`, add `EXIT_CANCELLED = 4` after `EXIT_CONFIG_ERROR = 3`:

```python
# runner.py:36-40, current:
EXIT_SUCCESS = 0
EXIT_JOB_FAILED = 1
EXIT_USAGE_ERROR = 2
EXIT_CONFIG_ERROR = 3
EXIT_SUSPENDED = 5

# Change to:
EXIT_SUCCESS = 0
EXIT_JOB_FAILED = 1
EXIT_USAGE_ERROR = 2
EXIT_CONFIG_ERROR = 3
EXIT_CANCELLED = 4
EXIT_SUSPENDED = 5
```

Replace three literal `return 4` sites:
- `runner.py:1087`: `return 4  # EXIT_CANCELLED` → `return EXIT_CANCELLED`
- `runner.py:1252`: `return 4  # EXIT_CANCELLED` → `return EXIT_CANCELLED`
- `runner.py:1371`: `return 4  # EXIT_CANCELLED` → `return EXIT_CANCELLED`

**Verification:** `uv run pytest tests/test_wait_retry.py -v` (existing wait tests still pass)

### Step 2: Add sentinel file writer utility (~30 min)

**Depends on:** Step 1 (uses `EXIT_CANCELLED` constant for status mapping)

**File:** `src/stepwise/runner.py`

Add `_write_sentinel()` near the other utility functions (after `_json_error()` at `runner.py:1641`):

```python
def _write_sentinel(project_dir: Path | None, job_id: str, status: str, extra: dict | None = None) -> None:
    """Write a completion sentinel file for file-based watchers.

    Writes atomically via tmp+rename to .stepwise/completed/{job_id}.json.
    No-op if project_dir is None (e.g., no project context available).
    """
    if project_dir is None:
        return
    completed_dir = project_dir / "completed"
    completed_dir.mkdir(exist_ok=True)
    import json as json_mod
    data = {"job_id": job_id, "status": status, "completed_at": datetime.now(timezone.utc).isoformat()}
    if extra:
        data.update(extra)
    tmp = completed_dir / f".{job_id}.json.tmp"
    final = completed_dir / f"{job_id}.json"
    tmp.write_text(json_mod.dumps(data, default=str))
    os.replace(tmp, final)
```

Requires adding `import os` at the top of `runner.py` (currently not imported; `os` is only used via `Path`). Also needs `from datetime import timezone` — already imported via `from datetime import datetime` at `runner.py:12`; add `timezone` to that import.

**Wire into existing single-job wait paths** (adds `project_dir` parameter):

1. **`_delegated_wait_ws_loop()`** at `runner.py:1031-1207`:
   - Add parameter: `project_dir: Path | None = None`
   - After `_json_stdout(...)` at lines 1147-1153 (completed), 1170-1177 (failed/cancelled), 1196-1203 (suspended): insert `_write_sentinel(project_dir, job_id, job_status)` before the `return` statement.

2. **`wait_for_job_id()`** at `runner.py:1486-1497`:
   - Add parameter: `project_dir: Path | None = None`
   - Thread through: `return asyncio.run(_delegated_wait_ws_loop(server_url, job_id, project_dir=project_dir))`

3. **`wait_for_job()`** at `runner.py:1336-1454`:
   - Add parameter: `project_dir: Path | None = None`
   - After `_json_stdout(result)` at lines 1386 (completed), 1409 (failed), 1434 (suspended): insert `_write_sentinel(project_dir, job_id, job.status.value)` before the `return`.

4. **`_async_wait_for_job()`** at `runner.py:1210-1334`:
   - Add parameter: `project_dir: Path | None = None`
   - Same pattern: insert `_write_sentinel()` after each `_json_stdout()` call at lines 1275 (completed), 1298 (failed), 1323 (suspended).

5. **`cmd_wait()`** at `cli.py:3460-3491`:
   - Thread `project.dot_dir` through to `wait_for_job_id()` at `cli.py:3470` and `wait_for_job()` at `cli.py:3489`.
   - Server path (`cli.py:3470`): `return wait_for_job_id(server_url, args.job_id, project_dir=project.dot_dir)` — but `project` isn't resolved on this path yet. Need to call `_find_project_or_exit(args)` before the server check (move `cli.py:3473` before `cli.py:3467`), or resolve lazily.

**Verification:** `uv run pytest tests/test_wait_retry.py -v` (still passes — no behavior change, just sentinel side-effect)

### Step 3: Extend CLI argument parsing for multi-job wait (~20 min)

**Depends on:** nothing (can be done in parallel with Steps 1-2, but logically feeds Step 6)

**File:** `src/stepwise/cli.py`

**Change 1:** Update subparser at `cli.py:4207-4208`:

```python
# Before (cli.py:4207-4208):
p_wait = sub.add_parser("wait", help="Block until job completes or suspends")
p_wait.add_argument("job_id", help="Job ID to wait on")

# After:
p_wait = sub.add_parser("wait", help="Block until job(s) complete or suspend")
p_wait.add_argument("job_ids", nargs="+", metavar="JOB_ID", help="Job ID(s) to wait on")
wait_mode = p_wait.add_mutually_exclusive_group()
wait_mode.add_argument("--all", dest="wait_mode", action="store_const", const="all",
                       help="Wait for all jobs to reach terminal state")
wait_mode.add_argument("--any", dest="wait_mode", action="store_const", const="any",
                       help="Wait for first job to reach terminal state")
```

**Change 2:** Update docstring at `cli.py:19`:

```python
# Before:
#     stepwise wait <job-id>                 Block until job completes or suspends

# After:
#     stepwise wait <job-id> [...]           Block until job(s) complete or suspend
#     stepwise wait --all <id1> <id2> ...    Wait for all jobs
#     stepwise wait --any <id1> <id2> ...    Wait for first job
```

**Validation rules** (enforced in `cmd_wait()`, Step 6):
- 1 job ID, no flags → backward-compatible single-job path
- 1+ job IDs with `--all` or `--any` → multi-job path
- 2+ job IDs without `--all`/`--any` → error (exit 2)

**Verification:** `uv run python -c "from stepwise.cli import build_parser; p = build_parser(); a = p.parse_args(['wait', 'j1', 'j2', '--all']); print(a.job_ids, a.wait_mode)"` → `['j1', 'j2'] all`

### Step 4: Implement `_aggregate_exit_code()` and `_build_multi_result()` helpers (~20 min)

**Depends on:** Step 1 (uses named exit code constants)

**File:** `src/stepwise/runner.py`

Add near `_write_sentinel()` (after Step 2's addition):

```python
def _aggregate_exit_code(job_results: list[dict]) -> int:
    """Determine exit code from multiple job results. Worst status wins.

    Priority: failed (1) > cancelled (4) > suspended (5) > completed (0).
    """
    statuses = {r["status"] for r in job_results}
    if "failed" in statuses or "error" in statuses:
        return EXIT_JOB_FAILED
    if "cancelled" in statuses:
        return EXIT_CANCELLED
    if "suspended" in statuses:
        return EXIT_SUSPENDED
    return EXIT_SUCCESS


def _build_multi_result(mode: str, job_results: list[dict], duration: float) -> dict:
    """Build the multi-job JSON output envelope."""
    summary = {"total": len(job_results), "completed": 0, "failed": 0, "cancelled": 0, "suspended": 0, "error": 0}
    for r in job_results:
        s = r.get("status", "error")
        if s in summary:
            summary[s] += 1
        else:
            summary["error"] += 1

    exit_code = _aggregate_exit_code(job_results)
    overall_status = {EXIT_SUCCESS: "completed", EXIT_JOB_FAILED: "failed",
                      EXIT_CANCELLED: "cancelled", EXIT_SUSPENDED: "suspended"}[exit_code]

    return {
        "mode": mode,
        "status": overall_status,
        "jobs": job_results,
        "summary": summary,
        "duration_seconds": round(duration, 1),
    }
```

**Verification:** Unit-testable in isolation — tested directly in Step 9.

### Step 5: Implement `_delegated_wait_multi_ws_loop()` — server path (~1.5 hr)

**Depends on:** Steps 1, 2, 4 (exit codes, sentinel writer, result builder)

**File:** `src/stepwise/runner.py`

New async function, placed after `_delegated_wait_ws_loop()` (after `runner.py:1207`). Structurally mirrors `_delegated_wait_ws_loop()` at `runner.py:1031-1207` but tracks multiple jobs:

```python
async def _delegated_wait_multi_ws_loop(
    server_url: str,
    job_ids: list[str],
    mode: str,  # "all" or "any"
    project_dir: Path | None = None,
) -> int:
    """WebSocket-driven wait loop for multiple jobs."""
    import json as json_mod

    # Track per-job results: None = still pending
    pending: dict[str, dict | None] = {jid: None for jid in job_ids}

    shutdown_requested = False
    detach_requested = False
    loop = asyncio.get_running_loop()

    # Signal handlers — same pattern as _delegated_wait_ws_loop (runner.py:1041-1056)
    loop.add_signal_handler(signal.SIGINT, lambda: _set_shutdown())
    loop.add_signal_handler(signal.SIGTERM, lambda: _set_shutdown())

    def _set_shutdown():
        nonlocal shutdown_requested
        shutdown_requested = True

    def _set_detach():
        nonlocal detach_requested
        detach_requested = True

    try:
        loop.add_signal_handler(signal.SIGTSTP, lambda: _set_detach())
    except (OSError, NotImplementedError):
        pass

    start_time = time.time()
    base_url = server_url.rstrip("/")

    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        # WebSocket setup — same as runner.py:1063-1073
        ws_url = _ws_url_from_server(server_url)
        use_ws = True
        try:
            import websockets
            ws_conn = await websockets.connect(ws_url)
        except Exception:
            use_ws = False
            ws_conn = None

        try:
            while True:
                # Shutdown: cancel all unresolved jobs
                if shutdown_requested:
                    for jid in pending:
                        if pending[jid] is None:
                            try:
                                await client.post(f"/api/jobs/{jid}/cancel")
                            except Exception:
                                pass
                            pending[jid] = {"job_id": jid, "status": "cancelled",
                                            "duration_seconds": round(time.time() - start_time, 1)}
                    result = _build_multi_result(mode, list(pending.values()), time.time() - start_time)
                    _json_stdout(result)
                    return EXIT_CANCELLED

                # Detach
                if detach_requested:
                    unresolved = [jid for jid, r in pending.items() if r is None]
                    sys.stderr.write(f"\nDetached. {len(unresolved)} job(s) still running.\n")
                    sys.stderr.flush()
                    return EXIT_SUCCESS

                # Wait for WS event or poll — same as runner.py:1098-1111
                if use_ws and ws_conn:
                    try:
                        await asyncio.wait_for(ws_conn.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        pass
                    except Exception:
                        use_ws = False
                        ws_conn = None
                else:
                    await asyncio.sleep(2.0)

                # Fetch state for all pending jobs concurrently
                unresolved_ids = [jid for jid, r in pending.items() if r is None]
                if not unresolved_ids:
                    break

                # Concurrent fetch with per-job error handling
                # _fetch_job_state (runner.py:168-203) already retries 404s per call
                fetch_tasks = {jid: _fetch_job_state(client, jid) for jid in unresolved_ids}
                results_or_errors = await asyncio.gather(
                    *fetch_tasks.values(), return_exceptions=True
                )

                for jid, result_or_err in zip(fetch_tasks.keys(), results_or_errors):
                    if isinstance(result_or_err, JobNotFoundError):
                        pending[jid] = {"job_id": jid, "status": "error",
                                        "error": str(result_or_err)}
                        _write_sentinel(project_dir, jid, "error")
                        if mode == "any":
                            # Error counts as resolved for --any
                            result = _build_multi_result(mode, [pending[jid]], time.time() - start_time)
                            _json_stdout(result)
                            return EXIT_JOB_FAILED
                        continue
                    if isinstance(result_or_err, Exception):
                        continue  # transient error, retry next cycle

                    job_data, runs = result_or_err
                    job_status = job_data.get("status", "")
                    duration = round(time.time() - start_time, 1)

                    # Check terminal states — mirrors runner.py:1134-1204 logic
                    if job_status == "completed":
                        try:
                            out_resp = await client.get(f"/api/jobs/{jid}/output")
                            outputs = out_resp.json()
                        except Exception:
                            outputs = {}
                        try:
                            cost_resp = await client.get(f"/api/jobs/{jid}/cost")
                            cost_usd = cost_resp.json().get("cost_usd", 0)
                        except Exception:
                            cost_usd = 0
                        pending[jid] = {"job_id": jid, "status": "completed",
                                        "outputs": outputs, "cost_usd": cost_usd,
                                        "duration_seconds": duration}
                        _write_sentinel(project_dir, jid, "completed")

                    elif job_status in ("failed", "cancelled"):
                        failed_step = error_msg = None
                        for run in runs:
                            if run["status"] == "failed":
                                failed_step = run["step_name"]
                                error_msg = run.get("error")
                                break
                        try:
                            cost_resp = await client.get(f"/api/jobs/{jid}/cost")
                            cost_usd = cost_resp.json().get("cost_usd", 0)
                        except Exception:
                            cost_usd = 0
                        pending[jid] = {"job_id": jid, "status": job_status,
                                        "error": error_msg, "failed_step": failed_step,
                                        "cost_usd": cost_usd, "duration_seconds": duration}
                        _write_sentinel(project_dir, jid, job_status)

                    elif _is_blocked_by_suspension_from_runs(runs):
                        # Suspension — mirrors runner.py:1181-1204
                        try:
                            cost_resp = await client.get(f"/api/jobs/{jid}/cost")
                            cost_usd = cost_resp.json().get("cost_usd", 0)
                        except Exception:
                            cost_usd = 0
                        completed_steps = [r["step_name"] for r in runs if r["status"] == "completed"]
                        pending[jid] = {"job_id": jid, "status": "suspended",
                                        "completed_steps": completed_steps,
                                        "cost_usd": cost_usd, "duration_seconds": duration}
                        _write_sentinel(project_dir, jid, "suspended")

                    # --any: return on first resolution
                    if mode == "any" and pending[jid] is not None:
                        result = _build_multi_result(mode, [pending[jid]], time.time() - start_time)
                        _json_stdout(result)
                        exit_map = {"completed": EXIT_SUCCESS, "failed": EXIT_JOB_FAILED,
                                    "cancelled": EXIT_CANCELLED, "suspended": EXIT_SUSPENDED}
                        return exit_map.get(pending[jid]["status"], EXIT_JOB_FAILED)

                # --all: check if all resolved
                if mode == "all" and all(r is not None for r in pending.values()):
                    all_results = list(pending.values())
                    result = _build_multi_result(mode, all_results, time.time() - start_time)
                    _json_stdout(result)
                    return _aggregate_exit_code(all_results)

        finally:
            if ws_conn:
                await ws_conn.close()
```

New public entry point (after `wait_for_job_id()` at `runner.py:1497`):

```python
def wait_for_job_ids(
    server_url: str,
    job_ids: list[str],
    mode: str,
    project_dir: Path | None = None,
) -> int:
    """Wait for multiple jobs on a running server. mode='all' or 'any'.

    Returns exit code: 0=all completed, 1=any failed, 4=cancelled, 5=suspended.
    """
    return asyncio.run(_delegated_wait_multi_ws_loop(server_url, job_ids, mode, project_dir))
```

**Verification:** `uv run pytest tests/test_multi_wait.py::test_multi_wait_all_completed -v` (from Step 9)

### Step 6: Implement local (no-server) multi-job wait (~1 hr)

**Depends on:** Steps 1, 2, 4 (exit codes, sentinel writer, result builder)

**File:** `src/stepwise/runner.py`

New function placed after `wait_for_job()` at `runner.py:1454`. Mirrors `wait_for_job()` structure but tracks multiple jobs. Uses the legacy `Engine` tick-based loop (same as `wait_for_job` at `runner.py:1336`):

```python
def wait_for_jobs(
    engine: Engine,
    store: SQLiteStore,
    job_ids: list[str],
    mode: str,
    project_dir: Path | None = None,
) -> int:
    """Block until multiple jobs reach terminal/suspended state (legacy sync API).

    mode='all': wait for all jobs. mode='any': wait for first job.
    Returns aggregated exit code.
    """
    pending: dict[str, dict | None] = {jid: None for jid in job_ids}

    # Signal handling — same pattern as wait_for_job (runner.py:1347-1356)
    shutdown_requested = False
    original_sigint = signal.getsignal(signal.SIGINT)
    original_sigterm = signal.getsignal(signal.SIGTERM)

    def _shutdown_handler(signum, frame):
        nonlocal shutdown_requested
        shutdown_requested = True

    signal.signal(signal.SIGINT, _shutdown_handler)
    signal.signal(signal.SIGTERM, _shutdown_handler)

    start_time = time.time()

    try:
        while True:
            if shutdown_requested:
                for jid in pending:
                    if pending[jid] is None:
                        engine.cancel_job(jid)
                        pending[jid] = {"job_id": jid, "status": "cancelled",
                                        "duration_seconds": round(time.time() - start_time, 1)}
                        _write_sentinel(project_dir, jid, "cancelled")
                result = _build_multi_result(mode, list(pending.values()), time.time() - start_time)
                _json_stdout(result)
                return EXIT_CANCELLED

            # Check each unresolved job — mirrors wait_for_job (runner.py:1373-1435)
            for jid in list(pending):
                if pending[jid] is not None:
                    continue

                job = engine.get_job(jid)
                duration = round(time.time() - start_time, 1)

                if job.status == JobStatus.COMPLETED:
                    cost = engine.job_cost(jid)
                    pending[jid] = {"job_id": jid, "status": "completed",
                                    "outputs": engine.terminal_outputs(jid),
                                    "cost_usd": round(cost, 4) if cost else 0,
                                    "duration_seconds": duration}
                    _write_sentinel(project_dir, jid, "completed")

                elif job.status in (JobStatus.FAILED, JobStatus.CANCELLED):
                    cost = engine.job_cost(jid)
                    failed_step = error_msg = None
                    for run in engine.get_runs(jid):
                        if run.status == StepRunStatus.FAILED:
                            failed_step = run.step_name
                            error_msg = run.error
                            break
                    pending[jid] = {"job_id": jid, "status": job.status.value,
                                    "error": error_msg or "Unknown error",
                                    "failed_step": failed_step,
                                    "cost_usd": round(cost, 4) if cost else 0,
                                    "duration_seconds": duration}
                    _write_sentinel(project_dir, jid, job.status.value)

                elif _is_blocked_by_suspension(engine, jid):
                    cost = engine.job_cost(jid)
                    completed_steps = [r.step_name for r in engine.get_runs(jid)
                                       if r.status == StepRunStatus.COMPLETED]
                    pending[jid] = {"job_id": jid, "status": "suspended",
                                    "completed_steps": completed_steps,
                                    "cost_usd": round(cost, 4) if cost else 0,
                                    "duration_seconds": duration}
                    _write_sentinel(project_dir, jid, "suspended")

                # --any: return on first resolution
                if mode == "any" and pending[jid] is not None:
                    result = _build_multi_result(mode, [pending[jid]], time.time() - start_time)
                    _json_stdout(result)
                    exit_map = {"completed": EXIT_SUCCESS, "failed": EXIT_JOB_FAILED,
                                "cancelled": EXIT_CANCELLED, "suspended": EXIT_SUSPENDED}
                    return exit_map.get(pending[jid]["status"], EXIT_JOB_FAILED)

            # --all: check if all resolved
            if mode == "all" and all(r is not None for r in pending.values()):
                all_results = list(pending.values())
                result = _build_multi_result(mode, all_results, time.time() - start_time)
                _json_stdout(result)
                return _aggregate_exit_code(all_results)

            time.sleep(0.1)
            try:
                engine.tick()
            except Exception:
                pass

    finally:
        signal.signal(signal.SIGINT, original_sigint)
        signal.signal(signal.SIGTERM, original_sigterm)
```

**Verification:** `uv run pytest tests/test_multi_wait.py::test_multi_wait_local_all_completed -v` (from Step 9)

### Step 7: Update `cmd_wait()` to dispatch multi-job paths (~30 min)

**Depends on:** Steps 2, 3, 5, 6 (sentinel wiring, CLI args, both wait implementations)

**File:** `src/stepwise/cli.py`

Refactor `cmd_wait()` at `cli.py:3460-3491`. Extract existing single-job logic into `_wait_single()`:

```python
def _wait_single(args: argparse.Namespace, job_id: str) -> int:
    """Single-job wait — backward-compatible path (original cmd_wait logic)."""
    project = _find_project_or_exit(args)
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.runner import wait_for_job_id
        return wait_for_job_id(server_url, job_id, project_dir=project.dot_dir)

    from stepwise.engine import Engine
    from stepwise.registry_factory import create_default_registry
    from stepwise.runner import wait_for_job
    from stepwise.store import SQLiteStore

    store = SQLiteStore(str(project.db_path))
    try:
        try:
            store.load_job(job_id)
        except KeyError:
            print(json.dumps({"status": "error", "error": f"Job not found: {job_id}"}))
            return EXIT_JOB_FAILED

        engine = Engine(store, create_default_registry(), jobs_dir=str(project.jobs_dir),
                        project_dir=project.dot_dir)
        return wait_for_job(engine, store, job_id, project_dir=project.dot_dir)
    finally:
        store.close()


def cmd_wait(args: argparse.Namespace) -> int:
    """Block until job(s) reach terminal state or suspension."""
    job_ids = args.job_ids
    wait_mode = getattr(args, "wait_mode", None)

    # Multiple jobs without --all/--any is ambiguous
    if len(job_ids) > 1 and not wait_mode:
        print(json.dumps({"status": "error",
                          "error": "Multiple job IDs require --all or --any flag"}))
        return EXIT_USAGE_ERROR

    # Single job without flags → backward-compatible single-job path
    if len(job_ids) == 1 and not wait_mode:
        return _wait_single(args, job_ids[0])

    # Multi-job path (or single job with explicit --all/--any)
    project = _find_project_or_exit(args)
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.runner import wait_for_job_ids
        return wait_for_job_ids(server_url, job_ids, wait_mode, project_dir=project.dot_dir)

    # Local (no-server) multi-job wait
    from stepwise.engine import Engine
    from stepwise.registry_factory import create_default_registry
    from stepwise.runner import wait_for_jobs
    from stepwise.store import SQLiteStore

    store = SQLiteStore(str(project.db_path))
    try:
        engine = Engine(store, create_default_registry(), jobs_dir=str(project.jobs_dir),
                        project_dir=project.dot_dir)
        return wait_for_jobs(engine, store, job_ids, wait_mode, project_dir=project.dot_dir)
    finally:
        store.close()
```

The `handlers` dict at `cli.py:5272` already maps `"wait": cmd_wait` — no change needed there.

**Verification:** `uv run pytest tests/test_multi_wait.py::test_single_job_backward_compat -v`

### Step 8: Add `StepwiseClient.wait_many()` (~30 min)

**Depends on:** Step 4 (`_aggregate_exit_code` logic, replicated in client)

**File:** `src/stepwise/api_client.py`

Add after `wait()` method at `api_client.py:191`:

```python
def wait_many(self, job_ids: list[str], mode: str = "all") -> dict:
    """Poll until jobs reach terminal state or suspension.

    Args:
        job_ids: List of job IDs to wait on.
        mode: 'all' (wait for all) or 'any' (wait for first).

    Returns dict with keys: mode, status, jobs (list of per-job results), summary.
    Uses same 404-retry logic as wait() per job.
    """
    pending: dict[str, dict | None] = {jid: None for jid in job_ids}
    # Per-job 404 retry counters (same as wait() at api_client.py:165-176)
    not_found_counts: dict[str, int] = {jid: 0 for jid in job_ids}
    max_retries = 8

    while True:
        for jid in list(pending):
            if pending[jid] is not None:
                continue

            try:
                status = self.status(jid)
            except StepwiseAPIError as e:
                if e.status == 404 and not_found_counts[jid] < max_retries:
                    not_found_counts[jid] += 1
                    time.sleep(min(0.5 * (2 ** (not_found_counts[jid] - 1)), 8))
                    continue
                if e.status == 404:
                    pending[jid] = {"job_id": jid, "status": "error",
                                    "error": f"Job {jid} not found after {max_retries} retries"}
                    if mode == "any":
                        return self._build_wait_many_result(mode, [pending[jid]])
                    continue
                raise

            not_found_counts[jid] = 0
            job_status = status.get("status", "")

            if job_status in ("completed", "failed", "cancelled"):
                pending[jid] = {"job_id": jid, "status": job_status, **status}
            else:
                # Check suspension — same logic as wait() at api_client.py:185-189
                steps = status.get("steps", [])
                has_suspended = any(s["status"] == "suspended" for s in steps)
                has_active = any(s["status"] in ("running", "delegated") for s in steps)
                if has_suspended and not has_active:
                    pending[jid] = {"job_id": jid, "status": "suspended", **status}

            if pending[jid] is not None and mode == "any":
                return self._build_wait_many_result(mode, [pending[jid]])

        # Check --all completion
        if mode == "all" and all(r is not None for r in pending.values()):
            return self._build_wait_many_result(mode, list(pending.values()))

        time.sleep(0.5)

def _build_wait_many_result(self, mode: str, results: list[dict]) -> dict:
    """Build multi-job result dict."""
    summary = {"total": len(results), "completed": 0, "failed": 0,
               "cancelled": 0, "suspended": 0, "error": 0}
    for r in results:
        s = r.get("status", "error")
        if s in summary:
            summary[s] += 1
        else:
            summary["error"] += 1

    # Worst status wins
    if summary["failed"] or summary["error"]:
        overall = "failed"
    elif summary["cancelled"]:
        overall = "cancelled"
    elif summary["suspended"]:
        overall = "suspended"
    else:
        overall = "completed"

    return {"mode": mode, "status": overall, "jobs": results, "summary": summary}
```

**Verification:** `uv run pytest tests/test_multi_wait.py::test_client_wait_many_all -v`

### Step 9: Write tests (~1.5 hr)

**Depends on:** Steps 1-8 (all implementation complete)

**File:** `tests/test_multi_wait.py` (new file)

```python
"""Tests for multi-job wait (--all, --any) and sentinel files."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from stepwise.api_client import StepwiseClient, StepwiseAPIError
from stepwise.runner import (
    EXIT_CANCELLED,
    EXIT_JOB_FAILED,
    EXIT_SUCCESS,
    EXIT_SUSPENDED,
    _aggregate_exit_code,
    _build_multi_result,
    _write_sentinel,
)


# ---------------------------------------------------------------------------
# _aggregate_exit_code tests
# ---------------------------------------------------------------------------

class TestAggregateExitCode:
    def test_all_completed(self):
        assert _aggregate_exit_code([
            {"status": "completed"}, {"status": "completed"}
        ]) == EXIT_SUCCESS

    def test_one_failed(self):
        assert _aggregate_exit_code([
            {"status": "completed"}, {"status": "failed"}
        ]) == EXIT_JOB_FAILED

    def test_one_cancelled(self):
        assert _aggregate_exit_code([
            {"status": "completed"}, {"status": "cancelled"}
        ]) == EXIT_CANCELLED

    def test_one_suspended(self):
        assert _aggregate_exit_code([
            {"status": "completed"}, {"status": "suspended"}
        ]) == EXIT_SUSPENDED

    def test_failed_beats_suspended(self):
        """Failed has highest priority over suspended and cancelled."""
        assert _aggregate_exit_code([
            {"status": "completed"}, {"status": "suspended"}, {"status": "failed"}
        ]) == EXIT_JOB_FAILED

    def test_cancelled_beats_suspended(self):
        assert _aggregate_exit_code([
            {"status": "completed"}, {"status": "suspended"}, {"status": "cancelled"}
        ]) == EXIT_CANCELLED

    def test_error_counts_as_failed(self):
        assert _aggregate_exit_code([
            {"status": "completed"}, {"status": "error"}
        ]) == EXIT_JOB_FAILED


# ---------------------------------------------------------------------------
# _build_multi_result tests
# ---------------------------------------------------------------------------

class TestBuildMultiResult:
    def test_all_mode_completed(self):
        results = [{"job_id": "j1", "status": "completed"},
                   {"job_id": "j2", "status": "completed"}]
        out = _build_multi_result("all", results, 10.0)
        assert out["mode"] == "all"
        assert out["status"] == "completed"
        assert out["summary"]["total"] == 2
        assert out["summary"]["completed"] == 2
        assert out["duration_seconds"] == 10.0

    def test_any_mode_single_result(self):
        results = [{"job_id": "j1", "status": "failed"}]
        out = _build_multi_result("any", results, 5.0)
        assert out["mode"] == "any"
        assert out["status"] == "failed"
        assert len(out["jobs"]) == 1


# ---------------------------------------------------------------------------
# _write_sentinel tests
# ---------------------------------------------------------------------------

class TestWriteSentinel:
    def test_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            _write_sentinel(project_dir, "job-abc", "completed")
            sentinel = project_dir / "completed" / "job-abc.json"
            assert sentinel.exists()
            data = json.loads(sentinel.read_text())
            assert data["job_id"] == "job-abc"
            assert data["status"] == "completed"
            assert "completed_at" in data

    def test_no_tmp_files_left(self):
        """Atomic write should not leave .tmp files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            _write_sentinel(project_dir, "job-abc", "completed")
            completed_dir = project_dir / "completed"
            tmp_files = list(completed_dir.glob("*.tmp"))
            assert tmp_files == []

    def test_noop_when_no_project_dir(self):
        """Should silently do nothing when project_dir is None."""
        _write_sentinel(None, "job-abc", "completed")  # should not raise

    def test_extra_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            _write_sentinel(project_dir, "job-abc", "completed",
                           extra={"outputs": {"result": 42}})
            data = json.loads((project_dir / "completed" / "job-abc.json").read_text())
            assert data["outputs"] == {"result": 42}

    def test_multiple_sentinels(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            _write_sentinel(project_dir, "job-1", "completed")
            _write_sentinel(project_dir, "job-2", "failed")
            assert (project_dir / "completed" / "job-1.json").exists()
            assert (project_dir / "completed" / "job-2.json").exists()
            data2 = json.loads((project_dir / "completed" / "job-2.json").read_text())
            assert data2["status"] == "failed"


# ---------------------------------------------------------------------------
# StepwiseClient.wait_many() tests
# ---------------------------------------------------------------------------

class TestClientWaitMany:
    def test_all_mode_both_complete(self):
        client = StepwiseClient("http://localhost:8340")
        call_counts = {"j1": 0, "j2": 0}

        def mock_status(job_id):
            call_counts[job_id] += 1
            return {"status": "completed", "steps": []}

        with patch.object(client, "status", side_effect=mock_status):
            with patch("time.sleep"):
                result = client.wait_many(["j1", "j2"], mode="all")

        assert result["mode"] == "all"
        assert result["status"] == "completed"
        assert result["summary"]["completed"] == 2
        assert result["summary"]["total"] == 2

    def test_any_mode_first_completes(self):
        client = StepwiseClient("http://localhost:8340")
        call_count = 0

        def mock_status(job_id):
            nonlocal call_count
            call_count += 1
            if job_id == "j1":
                return {"status": "completed", "steps": []}
            return {"status": "running", "steps": [{"status": "running"}]}

        with patch.object(client, "status", side_effect=mock_status):
            with patch("time.sleep"):
                result = client.wait_many(["j1", "j2"], mode="any")

        assert result["status"] == "completed"
        assert len(result["jobs"]) == 1
        assert result["jobs"][0]["job_id"] == "j1"

    def test_all_mode_one_failed(self):
        client = StepwiseClient("http://localhost:8340")

        def mock_status(job_id):
            if job_id == "j1":
                return {"status": "completed", "steps": []}
            return {"status": "failed", "steps": []}

        with patch.object(client, "status", side_effect=mock_status):
            with patch("time.sleep"):
                result = client.wait_many(["j1", "j2"], mode="all")

        assert result["status"] == "failed"
        assert result["summary"]["failed"] == 1
        assert result["summary"]["completed"] == 1

    def test_retry_per_job_404(self):
        """One job 404s twice then succeeds, other succeeds immediately."""
        client = StepwiseClient("http://localhost:8340")
        j1_calls = 0

        def mock_status(job_id):
            nonlocal j1_calls
            if job_id == "j1":
                j1_calls += 1
                if j1_calls <= 2:
                    raise StepwiseAPIError(404, "Not found")
                return {"status": "completed", "steps": []}
            return {"status": "completed", "steps": []}

        with patch.object(client, "status", side_effect=mock_status):
            with patch("time.sleep"):
                result = client.wait_many(["j1", "j2"], mode="all")

        assert result["status"] == "completed"
        assert result["summary"]["completed"] == 2

    def test_all_mode_suspended(self):
        client = StepwiseClient("http://localhost:8340")

        def mock_status(job_id):
            if job_id == "j1":
                return {"status": "completed", "steps": []}
            return {"status": "running",
                    "steps": [{"status": "suspended"}]}  # suspended, no active

        with patch.object(client, "status", side_effect=mock_status):
            with patch("time.sleep"):
                result = client.wait_many(["j1", "j2"], mode="all")

        assert result["status"] == "suspended"
        assert result["summary"]["suspended"] == 1


# ---------------------------------------------------------------------------
# CLI argument parsing tests
# ---------------------------------------------------------------------------

class TestWaitArgParsing:
    def test_single_job_parses(self):
        from stepwise.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["wait", "job-abc"])
        assert args.job_ids == ["job-abc"]
        assert getattr(args, "wait_mode", None) is None

    def test_multiple_jobs_with_all(self):
        from stepwise.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["wait", "--all", "j1", "j2", "j3"])
        assert args.job_ids == ["j1", "j2", "j3"]
        assert args.wait_mode == "all"

    def test_multiple_jobs_with_any(self):
        from stepwise.cli import build_parser
        parser = build_parser()
        args = parser.parse_args(["wait", "--any", "j1", "j2"])
        assert args.job_ids == ["j1", "j2"]
        assert args.wait_mode == "any"

    def test_all_and_any_mutually_exclusive(self):
        from stepwise.cli import build_parser
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["wait", "--all", "--any", "j1"])
```

**Run commands:**
```bash
# All multi-wait tests
uv run pytest tests/test_multi_wait.py -v

# Just the pure-function tests (fast, no engine needed)
uv run pytest tests/test_multi_wait.py::TestAggregateExitCode -v
uv run pytest tests/test_multi_wait.py::TestWriteSentinel -v

# Client tests
uv run pytest tests/test_multi_wait.py::TestClientWaitMany -v

# CLI parsing tests
uv run pytest tests/test_multi_wait.py::TestWaitArgParsing -v

# Existing wait tests still pass
uv run pytest tests/test_wait_retry.py -v

# Full test suite
uv run pytest tests/ -v
```

### Step 10: Integration smoke test (~15 min)

**Depends on:** Steps 1-9 (everything implemented and unit-tested)

Manually verify end-to-end with a running server:

```bash
# Ensure tests pass first
uv run pytest tests/test_multi_wait.py tests/test_wait_retry.py -v

# Start server
stepwise server start

# Create two quick jobs (use a simple flow that completes fast)
JOB_A=$(stepwise run --async examples/hello.flow.yaml 2>/dev/null | jq -r .job_id)
JOB_B=$(stepwise run --async examples/hello.flow.yaml 2>/dev/null | jq -r .job_id)

# Wait on both — verify JSON output and exit code
stepwise wait --all $JOB_A $JOB_B
echo "Exit code: $?"

# Verify sentinel files
ls -la .stepwise/completed/
cat .stepwise/completed/$JOB_A.json | jq .
cat .stepwise/completed/$JOB_B.json | jq .

# Test --any mode
JOB_C=$(stepwise run --async examples/hello.flow.yaml 2>/dev/null | jq -r .job_id)
JOB_D=$(stepwise run --async examples/hello.flow.yaml 2>/dev/null | jq -r .job_id)
stepwise wait --any $JOB_C $JOB_D
echo "Exit code: $?"

# Test backward compat (single job, no flags)
JOB_E=$(stepwise run --async examples/hello.flow.yaml 2>/dev/null | jq -r .job_id)
stepwise wait $JOB_E
echo "Exit code: $?"

# Test error case (multiple jobs without flag)
stepwise wait $JOB_A $JOB_B
echo "Exit code: $?"  # Should be 2

# Cleanup
stepwise server stop
```

## Dependency Graph

```
Step 1 (EXIT_CANCELLED constant)
  ↓
Step 2 (sentinel writer) ←── Step 1
  ↓
Step 4 (aggregate/build helpers) ←── Step 1
  ↓
Step 5 (server multi-wait) ←── Steps 1, 2, 4
Step 6 (local multi-wait) ←── Steps 1, 2, 4
  ↓
Step 3 (CLI args) ←── nothing (parallel with 1-2-4)
  ↓
Step 7 (cmd_wait dispatch) ←── Steps 2, 3, 5, 6
  ↓
Step 8 (client wait_many) ←── Step 4
  ↓
Step 9 (tests) ←── Steps 1-8
  ↓
Step 10 (integration) ←── Step 9
```

**Parallelizable groups:**
- Group A: Steps 1 → 2 → 4 (sequential — each builds on prior)
- Group B: Step 3 (independent — CLI args only)
- Group C: Steps 5, 6 (parallel — both depend on Group A, independent of each other)
- Then: Step 7 (depends on Groups A, B, C)
- Then: Step 8 (depends on Step 4 only, but logically after Step 7)
- Then: Step 9, Step 10 (sequential — tests then integration)

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **WebSocket fan-out**: All jobs share one WS connection via `_delegated_wait_multi_ws_loop`. High job count → many concurrent `_fetch_job_state` calls per tick. | Performance degradation with 50+ jobs (unlikely in practice). | `asyncio.gather()` already runs fetches concurrently. Add `asyncio.Semaphore(10)` if perf becomes an issue. V1 target is <20 jobs. |
| **Positional arg rename**: `job_id` → `job_ids` in argparse at `cli.py:4208`. | Internal code at `cli.py:3470` references `args.job_id` — will break if not updated. No external breakage (positional args are unnamed in shell). | Step 7 explicitly updates all `args.job_id` → `args.job_ids[0]` references. The `_wait_single` extraction makes this safe. |
| **`EXIT_PROJECT_ERROR=4` conflict**: `cli.py:62` defines `EXIT_PROJECT_ERROR=4`, same value as new `EXIT_CANCELLED=4`. | Semantic overlap — but these are used in different contexts (CLI arg errors vs job outcomes). | Acceptable: `EXIT_PROJECT_ERROR` is returned from `_find_project_or_exit` (before any job exists). `EXIT_CANCELLED` is returned from wait loops. No ambiguity in practice. Document in code comment. |
| **Sentinel file accumulation**: `.stepwise/completed/` grows unboundedly. | Disk usage (minimal — ~200 bytes per file). | Files inside `.stepwise/` which is already gitignored. Users can `rm -rf .stepwise/completed/`. Future: add cleanup to `stepwise cache clear`. |
| **Race: sentinel visible before stdout**: File watcher sees sentinel before JSON is printed to stdout. | Orchestrator may read partial state. | Write sentinel *after* building result dict, *before* `_json_stdout()`. Both happen synchronously in same thread — effectively simultaneous. |
| **N44 retry exhaustion for one job in `--all` mode**: `_fetch_job_state` raises `JobNotFoundError` for one job. | Should this fail the whole wait? | Catch `JobNotFoundError` per-job in the gather results. Store `{"status": "error"}` for that job. Continue waiting for others. Overall exit code = 1 (error ⊂ failure). |
| **`project_dir` threading**: `_delegated_wait_ws_loop` and `wait_for_job` gain a new `project_dir` parameter. | Existing callers (e.g., `run_wait` at `runner.py:1006`, `_delegated_run_wait` at `runner.py:1028`) don't pass it → no sentinel for `--wait` mode. | Default `project_dir=None` makes sentinel writing opt-in. Only `cmd_wait` and multi-job paths pass it. Future: thread through `run_wait` path too. |

## File Change Summary

| File | Lines Affected | Changes |
|---|---|---|
| `src/stepwise/runner.py:36-40` | 1 line added | Add `EXIT_CANCELLED = 4` constant |
| `src/stepwise/runner.py:1087,1252,1371` | 3 lines changed | Replace `return 4` with `return EXIT_CANCELLED` |
| `src/stepwise/runner.py:1` | 2 lines changed | Add `import os` and `timezone` to datetime import |
| `src/stepwise/runner.py:~1641` | ~20 lines added | New `_write_sentinel()` function |
| `src/stepwise/runner.py:~1661` | ~25 lines added | New `_aggregate_exit_code()` and `_build_multi_result()` |
| `src/stepwise/runner.py:1031-1207` | ~5 lines changed | Add `project_dir` param to `_delegated_wait_ws_loop`, wire sentinel calls |
| `src/stepwise/runner.py:1210-1334` | ~5 lines changed | Add `project_dir` param to `_async_wait_for_job`, wire sentinel calls |
| `src/stepwise/runner.py:1336-1454` | ~5 lines changed | Add `project_dir` param to `wait_for_job`, wire sentinel calls |
| `src/stepwise/runner.py:1486-1497` | ~2 lines changed | Add `project_dir` param to `wait_for_job_id`, thread through |
| `src/stepwise/runner.py:~1208` | ~120 lines added | New `_delegated_wait_multi_ws_loop()` |
| `src/stepwise/runner.py:~1500` | ~10 lines added | New `wait_for_job_ids()` entry point |
| `src/stepwise/runner.py:~1455` | ~80 lines added | New `wait_for_jobs()` (local multi-job) |
| `src/stepwise/cli.py:19` | 3 lines changed | Update docstring for multi-job syntax |
| `src/stepwise/cli.py:3460-3491` | ~50 lines rewritten | Refactor `cmd_wait()`, extract `_wait_single()` |
| `src/stepwise/cli.py:4207-4208` | ~8 lines changed | Update `wait` subparser: `job_ids` nargs="+", `--all`/`--any` flags |
| `src/stepwise/api_client.py:191` | ~60 lines added | New `wait_many()` and `_build_wait_many_result()` methods |
| `tests/test_multi_wait.py` | ~250 lines (new) | 17 test cases across 5 test classes |
