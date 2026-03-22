---
title: "Implementation Plan: Agent Reliability — H14 (Transient Error Retry) + H16 (Parallel Agent Staggering)"
date: "2026-03-21T14:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Agent Reliability — H14 + H16

## Overview

Add two resilience features to the Stepwise engine: (1) automatic retry with exponential backoff for agent steps that fail due to transient infrastructure errors (rate limits, timeouts, overloaded APIs), and (2) a global concurrency semaphore with stagger delay that limits how many agent steps run simultaneously, preventing thundering-herd failures during for_each fan-outs.

## Requirements

### H14: Agent step retry on transient errors

**R1:** When an agent executor returns a failure classified as transient (`infra_failure` or `timeout` from `AgentExecutor._classify_error()` at `src/stepwise/agent.py:957`), the engine retries the step automatically before surfacing the failure to exit rules.

- Acceptance: An agent step that fails with "rate limit" on attempts 1–2 but succeeds on attempt 3 produces a COMPLETED run with retry metadata in `envelope.executor_meta["retry"]`.

**R2:** Retries use exponential backoff: 30s, 60s, 120s (base=30, factor=2).

- Acceptance: Logged retry delays match the backoff schedule within ±1s. Measurable via `time.monotonic()` in test with small `backoff_base`.

**R3:** Default max transient retries: 3. Configurable via the existing `decorators` system on `ExecutorRef` (`src/stepwise/models.py:121-148`).

- Acceptance: A step with `decorators: [{type: retry, config: {max_retries: 5, backoff: exponential}}]` retries up to 5 times. Default agent steps (no explicit decorator) retry 3 times.

**R4:** Non-transient errors (`agent_failure`, `context_length`) fail immediately without retry.

- Acceptance: An agent step that fails with `error_category="agent_failure"` does NOT retry; the failure propagates to exit rules (via `_fail_run()` at `engine.py:2331`) on the first attempt.

**R5:** Each retry is logged at INFO level with the error message, retry number, and delay.

- Acceptance: Log output includes lines like `Transient retry 2/3 for step 'implement' after 60s delay (error: 429 rate limit)`.

**R6:** Expand `_classify_error()` to also match "overloaded", "503", and "capacity" as `infra_failure` — the spec lists these as transient patterns but they're missing from the current classifier at `agent.py:957-968`.

- Acceptance: An agent error containing "overloaded" or "503" or "capacity" is classified as `infra_failure` and triggers transient retry.

### H16: Stagger parallel agent steps

**R7:** A `max_concurrent_agents` setting in `StepwiseConfig` (`src/stepwise/config.py:104`) (default: 3) limits how many `executor: agent` steps run simultaneously across all jobs.

- Acceptance: With 7 agent steps ready, only 3 run at a time; the other 4 queue on the semaphore.

**R8:** Steps that exceed the limit queue and run as slots open.

- Acceptance: After one of the 3 running agents completes, a 4th begins within 5 seconds (semaphore release + stagger).

**R9:** A 2-second stagger delay separates successive agent launches to prevent thundering herd.

- Acceptance: If 3 agent steps launch at t=0, they start at approximately t=0, t=2, t=4 (±0.5s tolerance).

**R10:** Non-agent executor types (script, llm, external, poll) are unaffected by the limit.

- Acceptance: 10 script steps launch simultaneously regardless of `max_concurrent_agents`. Verifiable by checking that script step completion time is unaffected.

**R11:** The setting is configurable via `.stepwise/config.yaml`.

- Acceptance: Setting `max_concurrent_agents: 5` in config.yaml raises the limit to 5. Verified via `StepwiseConfig.from_dict()` round-trip and engine init reading the value.

## Assumptions

**A1:** Transient errors are already classified by `AgentExecutor._classify_error()` at `src/stepwise/agent.py:957-968`. The method returns `"infra_failure"` for rate limits/429/network/connection errors and `"timeout"` for timeout errors. These two categories are the transient set. However, the spec mentions "overloaded", "503", "capacity" as transient patterns — these are NOT currently matched. They need to be added.

- Verified: Read `src/stepwise/agent.py:957-968`. Current patterns: `"timeout"/"timed out"` → `"timeout"`, `"context"+"length"` → `"context_length"`, `"rate limit"/"429"` → `"infra_failure"`, `"network"/"connection"` → `"infra_failure"`, default → `"agent_failure"`. Missing: "overloaded", "503", "capacity".

**A2:** The `RetryDecorator` in `src/stepwise/decorators.py:64-127` already implements retry-on-failure for any executor. It checks `result.executor_state.get("failed")` (line 85) and `result.envelope.executor_meta.get("failed")` (line 87) — the same signals AgentExecutor sets on failure at `src/stepwise/agent.py:691` (`executor_meta={"failed": True}`) and line 695 (`"failed": True` in `executor_state`). The failure signaling protocol matches exactly.

- Verified: Read `src/stepwise/decorators.py:84-88` and `src/stepwise/agent.py:684-698`.

**A3:** The existing retry decorator's backoff is hardcoded to `time.sleep(0.01 * (2 ** attempt_num))` at `src/stepwise/decorators.py:110`. This is intentionally trivial for test speed. The `0.01` base is not configurable. We need to add a `backoff_base` config parameter.

- Verified: Read `src/stepwise/decorators.py:109-110`. The sleep formula is `0.01 * (2 ** attempt_num)`.

**A4:** `ExecutorRegistry.create()` at `src/stepwise/executors.py:95-125` applies decorators from `ref.decorators` at construction time, wrapping the base executor. The `ref` object carries the executor type as `ref.type` (string). After the for-loop at line 109-123, we can check `ref.type` and `ref.decorators` to auto-apply the transient retry without modifying `registry_factory.py`.

- Verified: Read `src/stepwise/executors.py:95-125`. The method has access to `ref.type` (line 103) and `ref.decorators` (line 109).

**A5:** The `AsyncEngine._run_executor()` coroutine at `src/stepwise/engine.py:2755-2817` is the single dispatch point for all normal steps (non-for_each, non-sub_flow). It's called from `_launch()` at line 2743. For-each steps go through `_launch_for_each()` (line 2712) which creates sub-jobs — those sub-jobs' steps then dispatch through the same `_dispatch_ready()` → `_launch()` → `_run_executor()` path on the same engine instance. This means the agent semaphore at `_run_executor` level naturally governs for_each agent parallelism.

- Verified: Read `src/stepwise/engine.py:2705-2753` (`_launch` dispatches normal steps to `_run_executor` at line 2743; for_each goes to `_launch_for_each` at line 2712 which creates sub-jobs). Sub-jobs run on the same engine instance (same store, same registry, same `_run_executor`).

**A6:** `StepwiseConfig` at `src/stepwise/config.py:104-115` already has `max_concurrent_jobs: int = 10`. The pattern for adding a new config field is: (1) add field with default at line 115, (2) add conditional serialization in `to_dict()` at lines 134-156, (3) add deserialization in `from_dict()` at lines 158-182. The `max_concurrent_jobs` field at line 115/154/181 is the exact template.

- Verified: Read `src/stepwise/config.py:115` (field), `154-155` (to_dict), `181` (from_dict).

**A7:** `AsyncEngine.__init__` at `src/stepwise/engine.py:2510-2533` receives `config: object | None = None` at line 2517. The server passes `config=config` (a `StepwiseConfig`) at `src/stepwise/server.py:422`. The runner passes `config=config` at `src/stepwise/runner.py:493`. Both use `StepwiseConfig` instances. Test fixtures at `tests/conftest.py:191-193` don't pass config (defaults to `None`), so the semaphore init must handle `config=None` gracefully.

- Verified: Read `src/stepwise/engine.py:2517`, `src/stepwise/server.py:422`, `src/stepwise/runner.py:493`, `tests/conftest.py:191-193`.

**A8:** The `_handle_executor_crash()` at `src/stepwise/engine.py:1281-1299` handles Python exceptions from `executor.start()` (caught at `_run_executor` line 2812). It calls `_halt_job()` directly with no exit rule evaluation. Since the `RetryDecorator.start()` wraps `executor.start()`, any exception thrown inside the agent executor will first be caught by the retry decorator (if it raises rather than returning a failure `ExecutorResult`). However, `RetryDecorator` only catches failure results, not exceptions. The crash path (uncaught exception) bypasses retry — this is correct because Python exceptions from `executor.start()` indicate programming errors, not transient API failures. Agent transient failures are returned as `ExecutorResult(type="data", executor_state={"failed": True, ...})`, not raised as exceptions.

- Verified: Read `src/stepwise/agent.py:682-698` (failure returns result, doesn't raise), `src/stepwise/engine.py:2809-2817` (exception → step_error event → `_handle_executor_crash`), `src/stepwise/decorators.py:80-81` (only checks return value, doesn't catch exceptions).

**A9:** The `_SessionLockManager` at `engine.py:2528` already provides per-session async locking for agent steps sharing a `_session_id`. The agent semaphore is a separate concern (global agent concurrency) and operates at a different level. The semaphore should be acquired *before* the session lock to avoid holding a concurrency slot while waiting for a session. The current session lock is inside `_run_executor` at lines 2796-2806.

- Verified: Read `src/stepwise/engine.py:2796-2806`. Session lock acquisition is after thread pool submission setup.

**A10:** Existing tests at `tests/conftest.py:146-181` register executor types `callable`, `script`, `external`, `mock_llm`, `poll` — but NOT `agent`. The H16 tests need an `"agent"` type registered so that `exec_ref.type == "agent"` triggers the semaphore. Tests will register their own mock under the `"agent"` type name.

- Verified: Read `tests/conftest.py:146-181`. No `"agent"` registration in the test registry fixture.

## Out of Scope

- **H9** (orphaned agent PID tracking) — separate feature, no overlap with retry/concurrency
- **H11** (zombie claude process detection) — requires OS-level process monitoring, orthogonal
- **H15** (permissions configuration) — access control, unrelated
- **H17** (Skill tool permission) — agent capability scoping, unrelated
- **H18** (server restart resilience for agents) — state persistence across restarts, separate concern
- **Retry for non-agent executors** — script/LLM already have `RetryDecorator` available via YAML `decorators:`; no new auto-apply logic for them
- **UI changes** — no frontend work to surface retry metadata or concurrency queue depth; can be added later
- **Per-step concurrency overrides** — e.g., `max_concurrent: 5` on a specific step; global limit is sufficient for initial implementation
- **Retry for `_handle_executor_crash` path** — Python exceptions from `executor.start()` indicate bugs, not transient failures; they should crash immediately (see A8)

## Architecture

### H14: Transient retry via enhanced RetryDecorator

The existing `RetryDecorator` (`src/stepwise/decorators.py:64-127`) retries on any failure. Two gaps need filling:

1. **No transient-vs-permanent distinction.** Lines 84-88 check `failed` flag but don't inspect `error_category`. All failures retry equally.
2. **Backoff is not configurable.** Line 110 hardcodes `0.01 * 2^n` seconds.

**Approach: Extend RetryDecorator, auto-apply for agents.**

Add two config keys to `RetryDecorator.__init__` (line 67-70):
- `transient_only: bool` (default: `false`) — when true, only retry if `executor_state.error_category` is in `TRANSIENT_CATEGORIES = {"infra_failure", "timeout"}`
- `backoff_base: float` (default: `0.01`) — replaces the hardcoded `0.01` at line 110

Then in `ExecutorRegistry.create()` (`src/stepwise/executors.py:95-125`), after the decorator loop at line 123, auto-apply `RetryDecorator({"max_retries": 3, "backoff": "exponential", "backoff_base": 30, "transient_only": True})` for `ref.type == "agent"` when no user retry decorator exists. This mirrors how the existing decorator loop works — it's just a conditional auto-application after user decorators.

This follows the decorator composition pattern already established: decorators wrap executors at `ExecutorRegistry.create()` time (line 95). The auto-retry wraps outermost (after user decorators), so user decorators like timeout apply first.

Also expand `_classify_error()` at `src/stepwise/agent.py:957-968` to match "overloaded", "503", "capacity" as `infra_failure`.

### H16: Agent concurrency semaphore in AsyncEngine

Add to `AsyncEngine.__init__` (`src/stepwise/engine.py:2510-2533`):
- `self._agent_semaphore = asyncio.Semaphore(N)` where N comes from `config.max_concurrent_agents` (default 3)
- `self._agent_last_launch = 0.0` (monotonic timestamp)
- `self._agent_stagger_lock = asyncio.Lock()` (serializes stagger timing)
- `self._agent_stagger_seconds = 2.0`

In `_run_executor()` (`src/stepwise/engine.py:2755-2817`), gate the agent path: acquire semaphore → apply stagger → run executor → release semaphore. The semaphore acquisition is *before* the session lock (lines 2796-2806) to avoid holding a concurrency slot while waiting for session access.

The stagger uses a separate lock from the semaphore to prevent blocking: `_apply_stagger()` acquires `_agent_stagger_lock`, checks elapsed time since `_agent_last_launch`, sleeps if needed, updates timestamp. A step waiting for a semaphore slot does NOT hold the stagger lock.

Non-agent steps (`exec_ref.type != "agent"`) skip the entire semaphore/stagger path.

### File change map

| File | Lines affected | Change |
|---|---|---|
| `src/stepwise/agent.py` | 957-968 | Add "overloaded", "503", "capacity" patterns to `_classify_error()` |
| `src/stepwise/decorators.py` | 67-70, 84-88, 109-110 | Add `transient_only` + `backoff_base` config, transient filter in retry loop, configurable backoff delay, logging |
| `src/stepwise/executors.py` | After line 123 | Auto-apply transient retry for `agent` type in `ExecutorRegistry.create()` |
| `src/stepwise/config.py` | 115, 154-155, 181 | Add `max_concurrent_agents: int = 3` field, `to_dict()`, `from_dict()` |
| `src/stepwise/engine.py` | 2510-2533, 2755-2817 | Add `_agent_semaphore`, `_agent_stagger_lock`, `_agent_last_launch` to init; gate agent dispatch in `_run_executor()` |
| `tests/test_agent_retry.py` | New file | H14 unit + integration tests |
| `tests/test_agent_concurrency.py` | New file | H16 unit + integration tests |

## Implementation Steps

### Step 1: Expand `_classify_error()` with missing transient patterns (~10 min)

**Why first:** This is a prerequisite for H14 — the transient category set must be complete before the retry decorator can filter on it. No other step depends on this, and it's a self-contained 3-line change.

**File:** `src/stepwise/agent.py:957-968`

Add three new patterns to `_classify_error()`, after the existing "rate limit"/"429" check at line 964-965 and before the default return at line 968:

```python
if "overloaded" in error or "503" in error:
    return "infra_failure"
if "capacity" in error:
    return "infra_failure"
```

### Step 2: Extend RetryDecorator with transient filtering and configurable backoff (~30 min)

**Why second:** This must be done before Step 3 (auto-apply) because Step 3 depends on the `transient_only` and `backoff_base` config keys existing. Must be after Step 1 since the transient categories it filters on must be fully defined.

**File:** `src/stepwise/decorators.py`

**2a.** In `RetryDecorator.__init__` (line 67-70), add after line 70:

```python
self._transient_only = config.get("transient_only", False)
self._backoff_base = config.get("backoff_base", 0.01)
```

**2b.** In the retry loop (line 80-106), after `is_failure` is determined to be True (line 88) and before `error_msg` extraction (line 102), insert the transient filter:

```python
if is_failure and self._transient_only:
    cat = ""
    if result.executor_state:
        cat = result.executor_state.get(
            "error_category", "")
    if cat not in {"infra_failure", "timeout"}:
        return result  # non-transient, fail fast
```

**2c.** Replace the backoff sleep at line 109-110:

```python
if attempt_num < self._max_retries:
    if self._backoff == "exponential":
        delay = self._backoff_base * (2 ** attempt_num)
        time.sleep(delay)
```

**2d.** Add `import logging` at top. Add INFO log line before `time.sleep()`:

```python
logging.getLogger("stepwise.retry").info(
    "Transient retry %d/%d after %.0fs delay "
    "(error: %s)", attempt_num + 1,
    self._max_retries, delay, error_msg)
```

### Step 3: Auto-apply default transient retry for agent executors (~20 min)

**Why third:** Depends on Step 2 (the `transient_only`/`backoff_base` config keys must exist in `RetryDecorator`). Independent of Steps 4-5 (config/engine changes).

**File:** `src/stepwise/executors.py`, in `ExecutorRegistry.create()` after line 123

After the decorator for-loop (line 108-123) and before `return executor` (line 125), add:

```python
# Auto-apply transient retry for agent executors
if (ref.type == "agent"
        and not any(d.type == "retry"
                    for d in ref.decorators)):
    executor = RetryDecorator(executor, {
        "max_retries": 3,
        "backoff": "exponential",
        "backoff_base": 30,
        "transient_only": True,
    })
```

This ensures:
- Agent executors get transient retry by default (no YAML needed)
- Users who specify `decorators: [{type: retry, ...}]` in YAML override the default (the `any()` check prevents double-wrap)
- The auto-retry wraps outermost, so user decorators like `timeout` apply first (correct — timeout fires before retry catches the failure)

### Step 4: Add `max_concurrent_agents` to StepwiseConfig (~15 min)

**Why fourth:** Must be done before Step 5 (engine reads this config). Independent of Steps 1-3 (H14 changes).

**File:** `src/stepwise/config.py`

**4a.** Add field to `StepwiseConfig` dataclass (after line 115, mirroring `max_concurrent_jobs`):

```python
max_concurrent_agents: int = 3
```

**4b.** Add to `to_dict()` (after lines 154-155, same pattern as `max_concurrent_jobs`):

```python
if self.max_concurrent_agents != 3:
    d["max_concurrent_agents"] = \
        self.max_concurrent_agents
```

**4c.** Add to `from_dict()` (after line 181, same pattern):

```python
max_concurrent_agents=d.get(
    "max_concurrent_agents", 3),
```

### Step 5: Add agent semaphore + stagger to AsyncEngine (~45 min)

**Why fifth:** Depends on Step 4 (reads `max_concurrent_agents` from config). Independent of Steps 1-3 (H14 is complete before this).

**File:** `src/stepwise/engine.py`

**5a.** In `AsyncEngine.__init__` (after line 2533), add semaphore and stagger state:

```python
_max_agents = 3
if (config is not None
        and hasattr(config, 'max_concurrent_agents')):
    _max_agents = config.max_concurrent_agents
self._agent_semaphore = asyncio.Semaphore(
    _max_agents)
self._agent_last_launch: float = 0.0
self._agent_stagger_lock = asyncio.Lock()
self._agent_stagger_seconds: float = 2.0
```

Uses `hasattr()` because `config` is typed as `object | None` (line 2517) — both `StepwiseConfig` instances (production) and `None` (tests) are handled.

**5b.** In `_run_executor()` (lines 2755-2817), restructure the method body. Currently the structure is:

```
try:
    executor = registry.create(exec_ref)  # line 2769
    ...setup state_update_fn...           # lines 2771-2788
    ...session locking...                 # lines 2790-2806
    await queue.put(step_result)          # line 2808
except CancelledError: return
except Exception: await queue.put(step_error)
```

New structure wrapping agent steps:

```
try:
    executor = registry.create(exec_ref)
    ...setup state_update_fn...

    is_agent = exec_ref.type == "agent"
    if is_agent:
        await self._agent_semaphore.acquire()
    try:
        if is_agent:
            await self._apply_stagger()
        ...session locking + run_in_executor...
    finally:
        if is_agent:
            self._agent_semaphore.release()

    await queue.put(step_result)
except CancelledError: ...
except Exception: ...
```

The semaphore acquire is *outside* the session lock block (lines 2796-2806) so that waiting for a semaphore slot doesn't hold a session lock. The `finally` ensures release even on exception.

**5c.** Add `_apply_stagger()` method to `AsyncEngine`:

```python
async def _apply_stagger(self) -> None:
    """Enforce minimum delay between agent
    launches to prevent thundering herd."""
    async with self._agent_stagger_lock:
        now = asyncio.get_event_loop().time()
        elapsed = now - self._agent_last_launch
        if elapsed < self._agent_stagger_seconds:
            await asyncio.sleep(
                self._agent_stagger_seconds
                - elapsed)
        self._agent_last_launch = (
            asyncio.get_event_loop().time())
```

### Step 6: Write H14 tests (~45 min)

**Why sixth:** Steps 1-3 must be complete (the code under test). Writing tests after code lets us write precise assertions against the actual implementation.

**File:** `tests/test_agent_retry.py` (new)

All tests use inline executor subclasses following the pattern at `tests/test_executors.py:193-222` (`FailOnceExecutor`). Failure result construction follows the `AgentExecutor` pattern at `src/stepwise/agent.py:684-698`.

**Test 1: `test_transient_error_retries_then_succeeds`**
- Mock executor: fails with `error_category="infra_failure"` on calls 1-2, succeeds on call 3
- Wrap with `RetryDecorator({"max_retries": 3, "backoff": "exponential", "backoff_base": 0.01, "transient_only": True})`
- Assert: `result.envelope.artifact` has success data, `executor_meta["retry"]["attempts"] == 3`, `executor_meta["retry"]["reasons"]` has 2 error strings
- Run: `uv run pytest tests/test_agent_retry.py::test_transient_error_retries_then_succeeds -v`

**Test 2: `test_non_transient_error_no_retry`**
- Mock executor: always fails with `error_category="agent_failure"`
- Wrap with same transient retry config (`max_retries=3`)
- Assert: `call_count == 1` (no retry), result has `executor_state["failed"] == True`
- Run: `uv run pytest tests/test_agent_retry.py::test_non_transient_error_no_retry -v`

**Test 3: `test_timeout_error_retries`**
- Mock executor: fails with `error_category="timeout"` (the other transient category)
- Wrap with `transient_only=True, max_retries=1`
- Assert: `call_count == 2` (1 original + 1 retry)
- Run: `uv run pytest tests/test_agent_retry.py::test_timeout_error_retries -v`

**Test 4: `test_transient_retry_exhaustion`**
- Mock executor: always fails with `error_category="infra_failure"`
- Wrap with `max_retries=2, transient_only=True`
- Assert: `call_count == 3`, result is failure, `executor_meta["retry"]["attempts"] == 3`
- Run: `uv run pytest tests/test_agent_retry.py::test_transient_retry_exhaustion -v`

**Test 5: `test_backoff_timing`**
- Mock executor: always fails with `error_category="infra_failure"`
- Wrap with `max_retries=2, backoff="exponential", backoff_base=0.05, transient_only=True`
- Record `time.monotonic()` at each `start()` call
- Assert: gap between call 1→2 is ~0.05s (±0.03), gap 2→3 is ~0.10s (±0.03)
- Run: `uv run pytest tests/test_agent_retry.py::test_backoff_timing -v`

**Test 6: `test_agent_auto_retry_decorator_applied`**
- Create `ExecutorRegistry`, register mock under `"agent"` type name
- Call `registry.create(ExecutorRef(type="agent", config={...}))` (no decorators)
- Make the returned executor fail transiently, then call `.start()`
- Assert: executor was called multiple times (auto-retry kicked in)
- Run: `uv run pytest tests/test_agent_retry.py::test_agent_auto_retry_decorator_applied -v`

**Test 7: `test_user_retry_decorator_overrides_default`**
- Create `ExecutorRef(type="agent", decorators=[DecoratorRef(type="retry", config={"max_retries": 1})])`
- Call `registry.create(ref)` — should use user's `max_retries=1`, not default 3
- Make executor fail transiently
- Assert: `call_count == 2` (1 original + 1 retry), not 4
- Run: `uv run pytest tests/test_agent_retry.py::test_user_retry_decorator_overrides_default -v`

**Test 8: `test_classify_error_overloaded_503_capacity`**
- Create `AgentExecutor` with `MockAgentBackend`
- Call `_classify_error()` with error strings containing "overloaded", "503", "capacity"
- Assert: all return `"infra_failure"`
- Run: `uv run pytest tests/test_agent_retry.py::test_classify_error_overloaded_503_capacity -v`

**Test 9 (integration): `test_transient_retry_in_async_engine`**
- Use `async_engine` fixture with `"agent"` type registered to a mock
- Mock fails transiently on call 1, succeeds on call 2
- Build single-step workflow, `run_job_sync()`
- Assert: `job.status == COMPLETED`, run has retry metadata
- Run: `uv run pytest tests/test_agent_retry.py::test_transient_retry_in_async_engine -v`

### Step 7: Write H16 tests (~45 min)

**Why seventh:** Steps 4-5 must be complete (the engine code under test). Must be after Step 6 to keep test files organized in commit sequence.

**File:** `tests/test_agent_concurrency.py` (new)

All tests create their own `AsyncEngine` instances with custom config to control `max_concurrent_agents`. Tests register a mock executor under the `"agent"` type name (following the pattern at `tests/conftest.py:150-152`) that sleeps briefly and tracks concurrency via a shared `threading.Lock`-protected counter.

**Test 1: `test_max_concurrent_agents_enforced`**
- Create `StepwiseConfig(max_concurrent_agents=2)`, pass to `AsyncEngine(config=config)`
- Register `"agent"` executor that sleeps 0.3s, increments/decrements atomic counter, records max-concurrent-seen
- Build workflow with 5 independent agent steps (no deps)
- `run_job_sync(engine, job_id, timeout=15)`
- Assert: max-concurrent-seen ≤ 2
- Run: `uv run pytest tests/test_agent_concurrency.py::test_max_concurrent_agents_enforced -v`

**Test 2: `test_stagger_delay_between_agents`**
- Create engine with `max_concurrent_agents=5` (high limit, no semaphore blocking)
- Override `engine._agent_stagger_seconds = 0.2` (short for test speed)
- Register `"agent"` executor that records `time.monotonic()` on each `start()` call
- Build workflow with 3 independent agent steps
- `run_job_sync()`
- Sort launch timestamps, assert consecutive gaps ≥ 0.15s (0.2s target with tolerance)
- Run: `uv run pytest tests/test_agent_concurrency.py::test_stagger_delay_between_agents -v`

**Test 3: `test_non_agent_steps_bypass_semaphore`**
- Create engine with `max_concurrent_agents=1` (very restrictive)
- Register `"script"` executor that sleeps 0.1s
- Build workflow with 5 independent script steps
- `run_job_sync()`
- Assert: total elapsed time < 1s (parallel, not serialized through semaphore)
- Run: `uv run pytest tests/test_agent_concurrency.py::test_non_agent_steps_bypass_semaphore -v`

**Test 4: `test_agent_slot_released_on_failure`**
- Create engine with `max_concurrent_agents=1`
- Register `"agent"` executor: first call fails (transient), second call succeeds
- Build workflow with 2 agent steps: `step-a` (fails) → `step-b` (succeeds, depends on step-a via sequencing only)
- Note: step-a failure with auto-retry will succeed on retry (transient), so step-b gets a slot after
- Assert: job completes, both steps ran
- Run: `uv run pytest tests/test_agent_concurrency.py::test_agent_slot_released_on_failure -v`

**Test 5: `test_config_round_trip_max_concurrent_agents`**
- `cfg = StepwiseConfig(max_concurrent_agents=5)`
- `d = cfg.to_dict()` → assert `d["max_concurrent_agents"] == 5`
- `cfg2 = StepwiseConfig.from_dict(d)` → assert `cfg2.max_concurrent_agents == 5`
- Default: `StepwiseConfig().to_dict()` should NOT include the key (omit-if-default pattern per lines 154-155)
- Run: `uv run pytest tests/test_agent_concurrency.py::test_config_round_trip_max_concurrent_agents -v`

**Test 6: `test_semaphore_default_when_no_config`**
- Create `AsyncEngine(store=store, registry=registry)` with no config (like test fixtures at `conftest.py:193`)
- Assert: `engine._agent_semaphore._value == 3` (default)
- Run: `uv run pytest tests/test_agent_concurrency.py::test_semaphore_default_when_no_config -v`

### Step 8: Run full test suite, fix regressions (~30 min)

**Why last:** All implementation and new tests must be complete. This catches any interactions between the new code and existing behavior.

```bash
uv run pytest tests/ -x -q                       # full backend suite (~1,399 tests)
cd web && npm run test                             # frontend suite (no changes, sanity check)
```

**Known risk areas to check:**
- Tests that create `AsyncEngine` with no config (e.g., `conftest.py:193`) — they'll get default semaphore (3). Since test executors complete in <100ms, the semaphore won't bottleneck. The stagger only fires for `"agent"` type, which test fixtures don't register.
- Tests that use `ExecutorRegistry.create()` with `type="agent"` in production registry — the auto-retry wrapper changes the return type to `RetryDecorator(AgentExecutor(...))`. Any test that does `isinstance(executor, AgentExecutor)` would break. Grep for this pattern and fix if found.
- The `RetryDecorator` backoff change from hardcoded `0.01` to configurable `backoff_base` (default `0.01`) is backward-compatible — existing tests that don't pass `backoff_base` get the same behavior.

## Testing Strategy

### New test suites
```bash
uv run pytest tests/test_agent_retry.py -v         # 9 tests for H14
uv run pytest tests/test_agent_concurrency.py -v   # 6 tests for H16
```

### Full regression
```bash
uv run pytest tests/ -x -q                         # all backend tests
cd web && npm run test                              # all frontend tests
```

### Manual smoke tests

1. **Normal agent step:** Run a simple flow with `executor: agent`, confirm completion. Verify no retry metadata in envelope (no failure occurred).
2. **Stagger verification:** Set `max_concurrent_agents: 1` in `.stepwise/config.yaml`. Create a for_each with 3 agent steps. Watch server logs — expect serialized execution with ~2s gaps between launches.
3. **Config persistence:** Set `max_concurrent_agents: 5` in `.stepwise/config.yaml`, restart server, verify `stepwise server status` or engine init log reflects the value.

## Risks & Mitigations

**Risk 1: Backoff sleeps block the thread pool.**
The `RetryDecorator.start()` runs inside `loop.run_in_executor()` (called at `engine.py:2800-2805`), so `time.sleep(30)` blocks one thread in the 32-thread pool (`engine.py:2530`). With max 3 agents × 3 retries = 9 blocked threads in the worst case, this leaves 23 threads free. Mitigation: pool size is configurable via `STEPWISE_EXECUTOR_THREADS` env var.

**Risk 2: Semaphore starvation under high job load.**
If many jobs have agent steps, the global semaphore (default 3) causes queueing. Mitigation: the default matches typical API concurrency limits (Anthropic allows ~5 concurrent requests on standard plans). Users can raise it in config. The semaphore is async — waiting coroutines don't block the event loop or thread pool.

**Risk 3: Auto-retry decorator interacts with step-level exit rules.**
The retry decorator wraps the executor — it retries *before* the engine sees the failure. If all retries exhaust, the engine receives the final failure and routes through `_process_launch_result()` (line 1301) → `_fail_run()` (line 2331) → exit rules. This is the correct ordering: retry transient errors silently at the executor layer, then let exit rules handle persistent failures at the engine layer.

**Risk 4: Stagger delay adds latency to solo agent steps.**
A single agent step with no concurrent agents still checks the stagger timestamp. Mitigation: `_apply_stagger()` only sleeps if `elapsed < 2s` since the last agent launch. For a solo agent, elapsed is effectively infinite → no sleep.

**Risk 5: Test isolation — semaphore/stagger state between tests.**
Each `AsyncEngine` instance creates its own `_agent_semaphore`, `_agent_stagger_lock`, and `_agent_last_launch`. The `async_engine` fixture at `conftest.py:191-193` creates a fresh instance per test. No shared state leaks between tests.

**Risk 6: Existing tests broken by auto-retry on agent type.**
The test `registry` fixture (`conftest.py:146-181`) does NOT register `"agent"`, so `ExecutorRegistry.create()` is never called with `type="agent"` in existing tests. Only production code via `registry_factory.py` creates agent executors. The auto-retry addition won't affect existing tests.

## Step Dependency Graph

```
Step 1 (classify_error)
  ↓
Step 2 (RetryDecorator extension) — depends on Step 1 (transient categories must be defined)
  ↓
Step 3 (auto-apply in registry) — depends on Step 2 (transient_only config must exist)
  ↓
Step 6 (H14 tests) — depends on Steps 1-3 (code under test)

Step 4 (config field) — independent of Steps 1-3
  ↓
Step 5 (engine semaphore) — depends on Step 4 (reads config field)
  ↓
Step 7 (H16 tests) — depends on Steps 4-5 (code under test)

Step 8 (full regression) — depends on Steps 6-7 (all code + tests complete)
```

Steps 1→2→3 and Step 4 can be done in parallel. Steps 6 and 7 can be done in parallel (different test files, different features). Step 8 is the final gate.
