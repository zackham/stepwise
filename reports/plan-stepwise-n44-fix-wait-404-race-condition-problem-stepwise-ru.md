# N44: Fix --wait 404 Race Condition

## Overview

When `stepwise run --wait` delegates to a running server, the CLI creates a job via `POST /api/jobs` + `POST /api/jobs/{id}/start`, then immediately enters a polling loop that hits `GET /api/jobs/{id}` and `GET /api/jobs/{id}/runs`. The server sometimes returns 404 because the job hasn't been fully indexed yet.

There is already a partial fix in `_fetch_job_state()` (runner.py:163-183) — it retries 404s up to 3 times with 1-second constant backoff. This is insufficient: 3 seconds max wait is tight, there's no exponential backoff, no clear error message on exhaustion, and the legacy `StepwiseClient.wait()` path has zero retry logic.

The fix: replace the existing naive retry with proper exponential backoff (0.5s → 1s → 2s → 4s), increase max attempts, add a clear diagnostic error message on exhaustion, and apply the same pattern to `StepwiseClient.wait()` in `api_client.py`.

## Requirements

### R1: Exponential backoff on 404 in `_fetch_job_state()`
- **Accept:** Retries 404s with delays of 0.5s, 1s, 2s, 4s (exponential, base 0.5, factor 2)
- **Accept:** Max 8 retries (total ~30s wall time with backoff)
- **Accept:** Non-404 responses break immediately (no unnecessary waits)

### R2: Clear error message on exhaustion
- **Accept:** After max retries, outputs JSON to stdout: `{"status": "error", "exit_code": 1, "error": "Job {id} not found after 8 retries (~30s). The job may still be running — check with: stepwise wait {id}"}`
- **Accept:** Returns `EXIT_JOB_FAILED` (1)

### R3: Apply to `StepwiseClient.wait()` in api_client.py
- **Accept:** `StepwiseClient.status()` call in the wait loop retries 404s with same backoff
- **Accept:** After max retries, raises `StepwiseAPIError(404, ...)` with diagnostic message

### R4: Apply to `stepwise wait <job-id>` command
- **Accept:** `cmd_wait` in cli.py, when using the server path (calls `wait_for_job_id`), inherits the fix via `_delegated_wait_ws_loop` → `_fetch_job_state()`
- **Accept:** No additional changes needed — already shares the same code path

### R5: Tests
- **Accept:** Unit test for `_fetch_job_state()` 404 retry behavior (mocked httpx)
- **Accept:** Unit test for `StepwiseClient.wait()` 404 retry behavior (mocked urllib)
- **Accept:** Test that non-404 errors propagate immediately (no spurious retries)
- **Accept:** Test that exhausted retries produce the correct error message

## Assumptions

1. **The race is purely timing-based** — the job IS in SQLite, but the server hasn't loaded it yet. Verified: server.py endpoints query SQLite directly via `engine.store.load_job()` with no caching layer. The 404 likely comes from WAL read visibility timing under concurrent writes.
2. **`_fetch_job_state()` is the single chokepoint** for delegated --wait mode. Both `_delegated_wait_ws_loop` (run --wait) and `wait_for_job_id` (stepwise wait) funnel through it. Verified at runner.py:1094 and runner.py:1470.
3. **`StepwiseClient.wait()` is a separate code path** — the legacy REST polling client used by `api_client.py`. It has its own polling loop and needs independent 404 handling. Verified at api_client.py:157-179.
4. **The `/api/jobs/{id}/runs` endpoint can also 404** independently of the job endpoint (server.py:1155-1162 raises `KeyError` → 404). The retry must cover both endpoints. Verified in existing code.

## Implementation Steps

### Step 1: Harden `_fetch_job_state()` with exponential backoff
**File:** `src/stepwise/runner.py` (lines 163-183)

Replace the current 3-attempt / 1s-constant retry with:

```python
async def _fetch_job_state(
    client, job_id: str,
) -> tuple[dict, list[dict]]:
    """Fetch job and runs from the server. Returns (job_dict, runs_list).

    Both GETs retry on 404 with exponential backoff — the server may not
    have indexed the job yet immediately after creation.
    """
    max_retries = 8
    backoff = 0.5

    for attempt in range(max_retries):
        job_resp = await client.get(f"/api/jobs/{job_id}")
        if job_resp.status_code != 404:
            break
        if attempt == max_retries - 1:
            raise JobNotFoundError(
                f"Job {job_id} not found after {max_retries} retries (~30s). "
                f"The job may still be running — check with: stepwise wait {job_id}"
            )
        await asyncio.sleep(backoff * (2 ** attempt))
    job_resp.raise_for_status()

    for attempt in range(max_retries):
        runs_resp = await client.get(f"/api/jobs/{job_id}/runs")
        if runs_resp.status_code != 404:
            break
        if attempt == max_retries - 1:
            raise JobNotFoundError(
                f"Job {job_id} runs not found after {max_retries} retries (~30s). "
                f"The job may still be running — check with: stepwise wait {job_id}"
            )
        await asyncio.sleep(backoff * (2 ** attempt))
    runs_resp.raise_for_status()

    return job_resp.json(), runs_resp.json()
```

Add `JobNotFoundError` as a simple exception class at module level in runner.py:

```python
class JobNotFoundError(Exception):
    """Raised when a job is not found after exhausting retries."""
    pass
```

### Step 2: Handle `JobNotFoundError` in `_delegated_wait_ws_loop`
**File:** `src/stepwise/runner.py` (lines 1093-1102)

The existing `except Exception as e` block at line 1096 already catches all exceptions from `_fetch_job_state()` and produces a JSON error. The `JobNotFoundError` message will flow through naturally. However, we should give it a distinct status:

```python
try:
    job_data, runs = await _fetch_job_state(client, job_id)
except JobNotFoundError as e:
    _json_stdout({
        "status": "error",
        "exit_code": EXIT_JOB_FAILED,
        "error": str(e),
    })
    return EXIT_JOB_FAILED
except Exception as e:
    _json_stdout({
        "status": "error",
        "exit_code": EXIT_JOB_FAILED,
        "error": f"Lost connection to server: {e}",
    })
    return EXIT_JOB_FAILED
```

This ensures the error message is the clear diagnostic one, not "Lost connection to server: ...".

### Step 3: Add 404 retry to `StepwiseClient.wait()`
**File:** `src/stepwise/api_client.py` (lines 157-179)

Add retry logic around the `self.status(job_id)` call:

```python
def wait(self, job_id: str) -> dict:
    """Long-poll until job reaches terminal state or suspension."""
    import time

    max_retries = 8
    not_found_count = 0

    while True:
        try:
            status = self.status(job_id)
        except StepwiseAPIError as e:
            if e.status == 404 and not_found_count < max_retries:
                not_found_count += 1
                time.sleep(min(0.5 * (2 ** (not_found_count - 1)), 8))
                continue
            raise

        not_found_count = 0  # reset on success
        job_status = status.get("status", "")

        if job_status in ("completed", "failed", "cancelled"):
            return status

        steps = status.get("steps", [])
        has_suspended = any(s["status"] == "suspended" for s in steps)
        has_active = any(s["status"] in ("running", "delegated") for s in steps)
        if has_suspended and not has_active:
            return status

        time.sleep(0.5)
```

### Step 4: Add tests
**File:** `tests/test_wait_retry.py` (new file)

Tests to add:

1. **`test_fetch_job_state_retries_on_404`** — Mock httpx client to return 404 twice then 200. Verify `_fetch_job_state` succeeds after retries. Verify sleep was called with exponential delays (0.5, 1.0).

2. **`test_fetch_job_state_exhausts_retries`** — Mock httpx client to always return 404. Verify `JobNotFoundError` raised with correct message containing job ID and "stepwise wait" hint.

3. **`test_fetch_job_state_no_retry_on_non_404`** — Mock httpx client to return 500. Verify raises immediately (httpx.HTTPStatusError), no sleep called.

4. **`test_fetch_job_state_runs_endpoint_retries`** — Mock job endpoint to succeed, runs endpoint to 404 twice then succeed. Verify both retries work independently.

5. **`test_client_wait_retries_on_404`** — Mock `StepwiseClient.status()` to raise `StepwiseAPIError(404, ...)` twice then return completed status. Verify wait returns successfully.

6. **`test_client_wait_exhausts_retries`** — Mock `StepwiseClient.status()` to always raise 404. Verify `StepwiseAPIError` re-raised after max retries.

7. **`test_client_wait_no_retry_on_non_404`** — Mock `StepwiseClient.status()` to raise `StepwiseAPIError(500, ...)`. Verify raises immediately.

## Testing Strategy

```bash
# Run just the new retry tests
uv run pytest tests/test_wait_retry.py -v

# Run existing wait mode tests to verify no regressions
uv run pytest tests/test_cli_tools.py::TestWaitMode -v
uv run pytest tests/test_cli_tools.py::TestWaitCommand -v

# Full test suite
uv run pytest tests/
```

All tests use mocks (no real server needed). The `_fetch_job_state` tests mock `httpx.AsyncClient` responses and `asyncio.sleep`. The `StepwiseClient.wait` tests mock the `_request` method.

## Files Changed

| File | Change |
|------|--------|
| `src/stepwise/runner.py` | Add `JobNotFoundError`, rewrite `_fetch_job_state()` with exponential backoff, add explicit catch in `_delegated_wait_ws_loop` |
| `src/stepwise/api_client.py` | Add 404 retry to `StepwiseClient.wait()` |
| `tests/test_wait_retry.py` | New — 7 test cases for retry behavior |

## Non-goals

- **Server-side fix** (e.g., making the server pre-warm job state). The 404 is a natural race condition; client-side retry is the correct, minimal fix.
- **Retry on other HTTP errors** (500, 503). Only 404 is retried — other errors are genuine failures.
- **Jitter**. Not needed for a single client polling a local server. Exponential backoff alone is sufficient.
