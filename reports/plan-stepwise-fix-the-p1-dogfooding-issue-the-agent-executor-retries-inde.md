# Plan: Fix Infinite Agent Executor Retry on Backend Failure

## Overview

The agent executor enters an infinite retry loop when the acpx backend is unavailable or the agent thread crashes. The root cause is a **bypass of the failure-routing machinery**: when the engine detects a stuck executor task (thread pool crash), it marks the run as FAILED but skips `_fail_run()`, which means exit rules never evaluate, no error category is set, and `_dispatch_ready()` immediately relaunches the step — creating a tight infinite loop with no backoff.

There's also a secondary issue: even when failures properly go through `_fail_run()`, loop exit rules without `max_iterations` create unbounded retry cycles. Each engine-level relaunch resets the RetryDecorator's internal counter, producing 5 × ∞ total retries.

### What already works

- **RetryDecorator** (decorators.py): handles graceful executor failures (where `start()` returns `{failed: True}`). Agent executors auto-get 5 retries with 30s exponential backoff, transient-only. This is solid.
- **Error classification** (executors.py `classify_api_error()`): correctly categorizes auth/quota/timeout/infra errors. Transient-only filtering works.
- **`max_iterations` on exit rules**: prevents unbounded loops when explicitly set.

### What's broken

1. **Stuck task detection** (engine.py:3238-3253) bypasses `_fail_run()` — no exit rule evaluation, no error_category, no circuit breaker.
2. **No engine-level consecutive failure cap** — repeated infra failures of the same step relaunch indefinitely even if each `_fail_run()` call re-matches a loop exit rule.
3. **No logging/visibility** — the rapid retry loop exhausts rate limits silently until the user notices the job queue is stuck.

---

## Requirements

### R1: Route stuck task failures through `_fail_run()`
**Acceptance criteria:**
- When a stuck running step is detected in `_poll_external_changes()`, the failure goes through `_fail_run()` with `error_category="infra_failure"`.
- Exit rules evaluate normally (loop/escalate/abandon/advance).
- If no exit rule handles the failure, the job halts (existing `_fail_run` default).

### R2: Engine-level consecutive infrastructure failure circuit breaker
**Acceptance criteria:**
- After N consecutive FAILED runs of the same step (default 3, configurable per-step via `max_infra_retries` in StepDefinition), the engine halts the job with a clear error message including the retry count and last error.
- The circuit breaker only counts consecutive failures — a success resets the counter.
- The breaker applies regardless of whether exit rules match (it's a safety cap, not an exit rule).

### R3: Distinguish transient vs permanent errors at engine level
**Acceptance criteria:**
- Stuck task failures are classified as `infra_failure` (transient).
- Auth failures (`auth_error`), quota failures (`quota_error`), and context length errors (`context_length`) bypass the circuit breaker and fail the job immediately on first occurrence (no retry at engine level).
- The permanent-vs-transient distinction uses the existing `error_category` from `classify_api_error()`.

### R4: Clear error messages
**Acceptance criteria:**
- When the circuit breaker fires, the job's error message includes: step name, retry count, last error message, error category.
- Example: `"Step 'implement' failed after 3 consecutive infrastructure failures (last error: Executor task lost (possible thread pool crash))"`
- The STEP_FAILED and JOB_FAILED events include the retry count.

---

## Assumptions (verified against code)

| # | Assumption | Verified in |
|---|-----------|-------------|
| 1 | Stuck task detection directly sets `run.status=FAILED` and calls `store.save_run()` without going through `_fail_run()` | engine.py:3244-3252 |
| 2 | After stuck task failure, `_dispatch_ready()` sees the step as ready because `_is_step_ready()` only blocks on RUNNING/SUSPENDED/DELEGATED status, not FAILED | engine.py:1131-1138 |
| 3 | `_is_step_ready()` has a guard for FAILED + exit_rules + loop (prevents dual-launch), but this only activates when the loop target is already in-flight — which never happens in the stuck task path since `_fail_run` was never called | engine.py:1147-1155 |
| 4 | RetryDecorator resets on each engine-level relaunch (it wraps the executor, not the engine loop) | decorators.py:96 — `for attempt_num in range(1 + self._max_retries)` runs fresh each `start()` call |
| 5 | Agent executors auto-get a RetryDecorator(max_retries=5, backoff="exponential", backoff_base=30, transient_only=True) when no explicit retry decorator is specified | executors.py:223-230 |
| 6 | `_fail_run()` handles exit rule evaluation, including loop with max_iterations check | engine.py:2978-3032 |
| 7 | `classify_api_error()` returns well-defined categories; `TRANSIENT_ERROR_CATEGORIES = {"infra_failure", "timeout"}` | executors.py:79-163, decorators.py:67 |
| 8 | `StepDefinition` is a dataclass with `to_dict()/from_dict()` — new fields must follow this pattern | models.py, CLAUDE.md guardrail |

---

## Implementation Steps

### Step 1: Route stuck task failures through `_fail_run()`

**File:** `src/stepwise/engine.py` — `_poll_external_changes()` method (lines 3232-3254)

**Change:** Replace the direct FAILED-marking block with a call to `_fail_run()`.

Current code (lines 3238-3253):
```python
if age > 60:
    _async_logger.warning(...)
    self._task_exec_types.pop(run.id, None)
    run.status = StepRunStatus.FAILED
    run.error = "Executor task lost (possible thread pool crash)"
    run.completed_at = _now()
    self.store.save_run(run)
    self._emit(job.id, STEP_FAILED, {...})
```

Replace with:
```python
if age > 60:
    _async_logger.warning(
        "Stuck running step detected: %s/%s (run %s, age %.0fs) — "
        "no executor task found, routing through _fail_run",
        job.id, run.step_name, run.id, age,
    )
    self._task_exec_types.pop(run.id, None)
    step_def = job.workflow.steps.get(run.step_name)
    if step_def is None:
        # Orphan run — no step definition, just fail directly
        run.status = StepRunStatus.FAILED
        run.error = "Executor task lost (orphan step — no definition found)"
        run.completed_at = _now()
        self.store.save_run(run)
        self._emit(job.id, STEP_FAILED, {
            "step": run.step_name,
            "error": run.error,
        })
    else:
        self._fail_run(
            job, run, step_def,
            error="Executor task lost (possible thread pool crash)",
            error_category="infra_failure",
        )
```

**Why:** This ensures exit rules evaluate, `error_category` is set for downstream classification, and the standard failure machinery handles routing (loop with max_iterations, escalate, abandon, or halt).

### Step 2: Add consecutive infrastructure failure circuit breaker

**File:** `src/stepwise/engine.py` — `_fail_run()` method (around line 2934)

**Change:** Before evaluating exit rules, check if this step has exceeded its consecutive failure limit. Insert a check early in `_fail_run()`, after saving the run but before exit rule evaluation.

```python
def _fail_run(self, job, run, step_def, error, error_category=None, traceback_str=None):
    # ... existing: set status, save run, emit event (lines 2940-2966) ...

    # ... existing: on_error: continue check (lines 2968-2975) ...

    # ── Circuit breaker: consecutive infrastructure failures ──
    PERMANENT_ERROR_CATEGORIES = {"auth_error", "quota_error", "context_length"}
    if error_category in PERMANENT_ERROR_CATEGORIES:
        _engine_logger.warning(
            "Permanent error for step '%s' (category=%s) — halting job immediately",
            run.step_name, error_category,
        )
        self._halt_job(job, run)
        return

    max_infra_retries = step_def.limits.max_infra_retries if step_def.limits else 3
    if max_infra_retries > 0:
        recent_runs = self.store.runs_for_step(job.id, run.step_name)
        # Count consecutive FAILED runs from most recent backward
        consecutive_failures = 0
        for r in reversed(recent_runs):
            if r.status == StepRunStatus.FAILED:
                consecutive_failures += 1
            else:
                break
        if consecutive_failures >= max_infra_retries:
            _engine_logger.error(
                "Step '%s' hit circuit breaker: %d consecutive failures "
                "(max_infra_retries=%d, last error: %s)",
                run.step_name, consecutive_failures, max_infra_retries, error,
            )
            run.error = (
                f"Step '{run.step_name}' failed after {consecutive_failures} "
                f"consecutive failures (last: {error})"
            )
            self.store.save_run(run)
            self._halt_job(job, run)
            return

    # ... existing: exit rule evaluation (lines 2978-3032) ...
```

**Note:** The circuit breaker fires before exit rules, ensuring it acts as a hard safety cap even when exit rules would otherwise loop. This prevents the N-retries × M-loop-iterations multiplication.

### Step 3: Add `max_infra_retries` to StepLimits

**File:** `src/stepwise/models.py` — `StepLimits` dataclass

**Change:** Add `max_infra_retries` field with default 3.

```python
@dataclass
class StepLimits:
    max_duration_minutes: int | None = None
    max_infra_retries: int = 3  # consecutive failures before circuit breaker

    def to_dict(self) -> dict:
        d = {}
        if self.max_duration_minutes is not None:
            d["max_duration_minutes"] = self.max_duration_minutes
        if self.max_infra_retries != 3:
            d["max_infra_retries"] = self.max_infra_retries
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "StepLimits":
        return cls(
            max_duration_minutes=d.get("max_duration_minutes"),
            max_infra_retries=d.get("max_infra_retries", 3),
        )
```

**YAML usage:**
```yaml
steps:
  implement:
    executor: agent
    prompt: "Implement: $spec"
    limits:
      max_infra_retries: 5    # allow more retries for flaky infra
```

### Step 4: Parse `max_infra_retries` in YAML loader

**File:** `src/stepwise/yaml_loader.py`

**Change:** Ensure the `limits` block parsing picks up `max_infra_retries`. Check if `StepLimits.from_dict()` is already called for the `limits:` key — if so, no change needed since `from_dict` handles unknown keys gracefully. If limits parsing is manual, add the field.

*(Likely no change needed if `StepLimits.from_dict()` is already used — verify.)*

### Step 5: Add `runs_for_step()` store method (if missing)

**File:** `src/stepwise/store.py`

**Change:** Check if `runs_for_step(job_id, step_name)` exists. If only `runs_for_job(job_id)` exists, either add a filtered method or filter in the caller.

```python
def runs_for_step(self, job_id: str, step_name: str) -> list[StepRun]:
    """Return all runs for a specific step in a job, ordered by attempt."""
    rows = self._execute(
        "SELECT * FROM step_runs WHERE job_id = ? AND step_name = ? ORDER BY attempt",
        (job_id, step_name),
    ).fetchall()
    return [self._row_to_run(row) for row in rows]
```

*(Check if this or an equivalent already exists — `run_count()` exists per the max_iterations code, so the store already tracks per-step runs.)*

### Step 6: Update tests

**File:** `tests/test_agent_retry.py` (extend existing test file)

**New test cases:**

1. **`test_stuck_task_routes_through_fail_run`** — Verify that a stuck task (run marked RUNNING with no task in registry, age > 60s) goes through `_fail_run()` with `error_category="infra_failure"` and evaluates exit rules.

2. **`test_circuit_breaker_halts_after_consecutive_failures`** — Register a callable executor that always fails with `infra_failure`. Run a job with a step that has `max_infra_retries=3`. Verify the job halts after 3 consecutive failures with a clear error message.

3. **`test_circuit_breaker_resets_on_success`** — Executor fails twice, succeeds once, then fails twice more. Verify the circuit breaker does NOT fire (consecutive count resets on success).

4. **`test_permanent_error_halts_immediately`** — Executor fails with `error_category="auth_error"`. Verify the job halts immediately without evaluating exit rules or looping.

5. **`test_circuit_breaker_default_3`** — Verify default `max_infra_retries=3` when no `limits` block is set.

6. **`test_circuit_breaker_configurable`** — Set `limits: { max_infra_retries: 5 }`, verify breaker fires at 5 not 3.

**File:** `tests/test_engine.py` or new `tests/test_stuck_task.py`

7. **`test_stuck_task_no_infinite_relaunch`** — The integration test: create a job where the executor's thread dies immediately. Verify the step is NOT relaunched infinitely — it either goes through exit rules or the circuit breaker fires.

---

## Testing Strategy

### Unit tests
```bash
# Run all retry/error tests
uv run pytest tests/test_agent_retry.py -x -v

# Run engine tests
uv run pytest tests/test_engine.py -x -v

# Run all tests
uv run pytest tests/ -x -q
```

### Manual verification
After implementing, verify with a flow that uses an agent step while acpx is unavailable:
1. The step should fail after RetryDecorator exhausts its 5 retries (with exponential backoff).
2. If exit rules loop, the engine should relaunch (going through RetryDecorator again).
3. After `max_infra_retries` (default 3) consecutive engine-level failures, the circuit breaker fires.
4. Total attempts: 3 × (1 + 5) = 18 executor calls maximum (not infinite).
5. The job's error message clearly shows the retry count and last error.

### Edge cases to verify
- Step with `on_error: continue` — circuit breaker should NOT fire (on_error takes precedence).
- Step with `max_iterations` on a loop exit rule — both `max_iterations` and `max_infra_retries` should be respected (whichever fires first).
- Step that fails with a mix of error categories — circuit breaker counts all consecutive failures, not just infra.
- Server restart during retry — run is stuck, detected by `_poll_external_changes`, routes through `_fail_run` correctly.

---

## Files Modified

| File | Change |
|------|--------|
| `src/stepwise/engine.py` | Route stuck task through `_fail_run()`; add circuit breaker in `_fail_run()` |
| `src/stepwise/models.py` | Add `max_infra_retries` to `StepLimits` |
| `src/stepwise/yaml_loader.py` | Verify limits parsing (likely no change) |
| `src/stepwise/store.py` | Add `runs_for_step()` if missing |
| `tests/test_agent_retry.py` | 6 new test cases |
| `tests/test_stuck_task.py` | 1 integration test |

## Non-goals

- **Changing the RetryDecorator** — it already works correctly for graceful failures. The fix is at the engine level.
- **Adding backoff between engine-level relaunches** — the circuit breaker caps total attempts; backoff within each attempt is handled by RetryDecorator (30s exponential).
- **Changing the auto-retry defaults** (5 retries, 30s base) — these are reasonable. The issue is the unbounded outer loop, not the inner retry count.
- **Adding a `max_infra_retries` YAML keyword at the top level** — it goes under `limits:` to stay consistent with `max_duration_minutes`.
