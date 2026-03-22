---
title: "Implementation Plan: Fix Agent Concurrency Bottleneck"
date: "2026-03-20T17:30:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Fix Agent Concurrency Bottleneck

## Overview

Investigation confirmed that no component in stepwise or acpx explicitly limits agent concurrency — the engine, thread pool (32 workers), acpx queue-owner, and Claude Code sessions all support parallelism. Because the root cause is not statically visible, the plan takes a diagnose-then-fix approach: first add timing instrumentation and a diagnostic flow to pinpoint the bottleneck at runtime, then apply targeted fixes based on the evidence. Two concrete code changes (dedicated thread pool, `sessions ensure` optimization) are included because they are independently justified improvements that also serve as diagnostic levers.

## Requirements

### R1: Concurrency instrumentation for agent lifecycle
**Acceptance criteria:** Running 5+ concurrent agent steps produces log lines showing, for each step: (a) `sessions ensure` wall-clock duration, (b) `acpx` spawn timestamp, (c) `wait()` entry/exit timestamps, (d) thread name, (e) job_id + step_name. All timing uses `time.monotonic()` deltas. Logs appear at `INFO` level under logger `stepwise.agent`.

### R2: Engine dispatch observability
**Acceptance criteria:** `_dispatch_ready()` logs the number of ready steps found. `_run_executor()` logs coroutine start and thread pool submission with active thread count. Logger: `stepwise.async_engine`.

### R3: Dedicated thread pool for executor dispatch
**Acceptance criteria:** `AsyncEngine` creates its own `ThreadPoolExecutor` with `thread_name_prefix="stepwise-exec"`. Pool size read from `STEPWISE_EXECUTOR_THREADS` env var (default `"32"`). `_run_executor` uses `loop.run_in_executor(self._executor_pool, ...)` instead of `asyncio.to_thread(...)`. Pool is shut down in server lifespan teardown.

### R4: `sessions ensure` optimization
**Acceptance criteria:** If acpx auto-creates sessions on prompt (verified manually), the `sessions ensure` subprocess call in `AcpxBackend.spawn()` is removed. If not, timeout is reduced from 30s to 5s and failure is non-fatal.

### R5: Diagnostic concurrency flow
**Acceptance criteria:** A `flows/test-concurrency/FLOW.yaml` launches N parallel script steps (controlled by `$job.count`, default 5). Each step records its start/end timestamps. A final step computes max observed concurrency from the timestamps. Running `stepwise run flows/test-concurrency/ --var count=10` produces a JSON artifact `{max_concurrent: N, timeline: [...]}`.

## Investigation Findings

### Confirmed NOT bottlenecks

| Component | Location | Why eliminated |
|---|---|---|
| `_SessionLockManager` | `engine.py:2191-2209` | Only acquires lock when `inputs.get("_session_id")` is truthy (`engine.py:2430-2434`). Independent jobs don't set `_session_id`. Session continuity requires explicit `_session_id` input binding in YAML. |
| acpx queue-owner | `~/.acpx/queues/<key>.lock` | Key = `SHA256(sessionId)[:24]` (acpx `cli.js:4023`). Stepwise sets unique session names per job+step+attempt (`agent.py:103-104`: `step-{job_prefix}-{step_name}-{attempt}`). Verified: lock files in `~/.acpx/queues/` each map to distinct sessionIds. |
| Claude Code per-directory lock | N/A | acpx source confirms `AcpClient.start()` (`cli.js:1160-1256`) spawns a fresh Claude Code process per client instance. `createSession()` (`cli.js:5381-5434`) creates new `AcpClient`, starts new process, closes connection on completion. No shared daemon or directory lock. |
| Default thread pool | Python runtime | `os.cpu_count() = 32` → `min(32, 36) = 32` default `ThreadPoolExecutor` workers. Verified by running `python3 -c "import os; print(min(32, os.cpu_count()+4))"` → `32`. |
| Engine dispatch | `engine.py:2340-2350` `_dispatch_ready()` | Iterates all steps via `_find_ready()` (line 780-786), launches each without throttle. No `max_concurrent`, `Semaphore`, or rate limit. `_launch()` (line 2352) creates asyncio task immediately. |
| Executor creation | `executors.py:95-125` `registry.create()` | Calls factory lambda, wraps with decorators. No I/O, no subprocess calls, runs in microseconds. |
| Server endpoints | `server.py:456` `create_job()`, `server.py:513` `start_job()` | Sync FastAPI handlers run in anyio threadpool. Each returns in milliseconds. Multiple concurrent requests handled by separate threads. |
| SQLite store lock | `server.py:40-65` `_LockedConnection` | `threading.Lock` wrapping individual `execute()`/`commit()` calls. Each operation <1ms. Cannot explain sustained 1-2 limit. |
| `_prepare_step_run` | `engine.py:1044-1138` | Synchronous but fast: resolves inputs (dict lookups + store reads), creates StepRun, saves to store. No subprocess or I/O beyond SQLite. |

### Remaining hypotheses (ranked)

1. **Claude Code startup convoy.** Each agent step spawns two Claude Code Node.js processes: `sessions ensure` (line 119) + prompt via queue-owner. At ~200MB RAM and 3-5s startup each, 10 concurrent startups may saturate I/O bandwidth, causing staggered initialization that *appears* as 1-2 concurrent agents. The instrumentation (R1) will confirm or eliminate this by showing wall-clock gaps between `spawn()` return and first API activity.

2. **`sessions ensure` serialization.** The 30s-timeout `subprocess.run()` call blocks a thread pool thread per agent. If acpx session creation internally serializes (e.g., directory walk + file lock on `~/.acpx/sessions/`), 10 concurrent calls would queue. Instrumentation will show this as long `sessions ensure` durations.

3. **Anthropic API rate limiting.** Concurrent API calls from 10 Claude Code instances may trigger per-key throttling. Previous 12-session runs may have been staggered; simultaneous starts hit the rate limit wall.

4. **System resource limits.** `ulimit -n` (open files) or `ulimit -u` (max user processes) could cap subprocess spawning.

## Assumptions

| # | Assumption | Verification |
|---|---|---|
| A1 | `_run_executor` dispatches to default thread pool via `asyncio.to_thread` | Read `engine.py:2434-2436`: both code paths call `asyncio.to_thread(executor.start, inputs, ctx)`. No custom executor configured. |
| A2 | No other code sets the loop's default executor | Grepped for `set_default_executor` and `ThreadPoolExecutor` across `src/stepwise/` — zero results. |
| A3 | `sessions ensure` blocks the calling thread for its full duration | Read `agent.py:119-123`: `subprocess.run(...)` with `timeout=30` is synchronous, blocking. Runs inside `executor.start()` which runs in thread pool. |
| A4 | Each job gets a unique workspace by default | Read `engine.py:128-129`: `workspace_path or os.path.join(self.jobs_dir, job_id, "workspace")`. Unique per job_id unless caller overrides. |
| A5 | Session names include job_id, preventing cross-job collisions | Read `agent.py:103-104`: `f"step-{job_prefix}-{step_name}-{attempt}"` where `job_prefix = context.job_id.replace("job-", "")`. |
| A6 | Server lifespan manages engine lifecycle | Read `server.py:362-415`: `lifespan()` creates engine (line 380), starts `engine.run()` task (line 386), cancels task on shutdown (lines 409-414). No existing `shutdown()` method on engine. |

## Out of Scope

| Item | Why excluded |
|---|---|
| Modifying acpx internals | External npm package (`~/.local/share/fnm/.../acpx`). Not our code; changes would be lost on update. If acpx serialization is confirmed as the bottleneck, the fix is to upstream a patch or work around it. |
| Claude Code daemon/connection pooling | Binary we don't control. Claude Code is spawned by acpx as a subprocess. No API to configure pooling. |
| Anthropic API rate limit tuning | Requires account-level changes, not code. If confirmed as bottleneck, the fix is request staggering (a new step after diagnosis). |
| Multi-process engine architecture | The `AsyncEngine` is designed as a single-process event loop (see `engine.py:2211-2217` docstring). Splitting across processes requires rethinking store access, event broadcasting, and job ownership — a separate project. |
| Changing `_SessionLockManager` | Already confirmed it doesn't fire for independent jobs. Its behavior is correct for its purpose (session continuity). |

## Architecture

### Why a dedicated thread pool (R3) despite no confirmed thread bottleneck

Three justifications, each sufficient independently:

1. **Diagnostic lever.** With `thread_name_prefix="stepwise-exec"`, thread dumps and log output show exactly which threads are executor workers vs. FastAPI handlers vs. asyncio internals. Setting `STEPWISE_EXECUTOR_THREADS=2` can experimentally reproduce a thread-limited scenario to rule it in/out.

2. **Isolation.** The default asyncio thread pool is shared by ALL `asyncio.to_thread` callers — including future library dependencies. Agent steps block threads for minutes/hours (`agent.py:163`, `os.waitpid`). A dedicated pool prevents executor threads from starving other async operations.

3. **Existing pattern.** `AsyncEngine.__init__` already manages lifecycle resources as instance attributes: `_queue` (line 2230), `_tasks` (2231), `_poll_tasks` (2232), `_job_done` (2233), `_session_locks` (2236). A `_executor_pool` fits this pattern exactly. Server lifespan already cancels the engine task (lines 409-414); adding a `pool.shutdown()` call is a one-liner.

### Instrumentation placement

Timing logs go in two layers:
- **`agent.py`** (`AcpxBackend.spawn`, `.wait`, `AgentExecutor.start`): measures acpx/Claude Code overhead per agent step. Uses existing `logger = logging.getLogger("stepwise.agent")` (already at module level, line 24 after the recent file modification).
- **`engine.py`** (`_dispatch_ready`, `_run_executor`): measures engine dispatch latency (time from "step ready" to "thread pool submission"). Uses existing `_async_logger` (line 2188).

### Diagnostic flow design

Uses **script executor** (not agent) for the parallel steps to isolate engine concurrency from Claude Code behavior. Each script step does `sleep $duration && echo '{"start_ts": ..., "end_ts": ...}'`. The for-each pattern follows `flows/welcome/FLOW.yaml:87-96` (for_each + sub-flow). A subsequent agent-based variant can be created if script-level concurrency is confirmed working.

## Implementation Steps

### Dependency graph

```
1a ──┐
     ├──→ 3 ──→ 4
1b ──┘
2 ─────────────→ 4
     ┌─── 5 (independent, manual test)
     └──→ 6 (apply result of 5)
```

- **1a, 1b**: No dependencies. Pure additive logging, can be done in parallel.
- **3**: Depends on 1a + 1b. The diagnostic flow's value comes from the log output those steps produce.
- **2**: No dependencies. Thread pool change is structurally independent. But must be done before step 4 so verification uses the new pool.
- **4**: Depends on 2 + 3. Verification runs the diagnostic flow against the new thread pool.
- **5**: No dependencies. Manual test of acpx behavior.
- **6**: Depends on 5. Code change depends on the test result.

### Step 1a: Instrument `AcpxBackend` spawn and wait (~20min)

**File:** `src/stepwise/agent.py`
**Depends on:** nothing

Add timing to `spawn()` (around lines 118-150):
- Before line 119: `t0 = time.monotonic()` + log `"[{step_name}] sessions ensure starting (thread={thread_name})"`
- After line 123: log `"[{step_name}] sessions ensure done ({elapsed:.1f}s)"`
- Before line 142: log `"[{step_name}] acpx spawning"`
- After line 150: log `"[{step_name}] acpx spawned pid={proc.pid}"`

Add timing to `wait()` (around lines 160-171):
- At entry: log `"[{session_name}] wait() started"`
- At exit (line 171): log `"[{session_name}] wait() done ({elapsed:.1f}s)"`

Access thread name via `threading.current_thread().name`. Access step_name via `context.step_name` (passed through config or as a new field on `AgentProcess`).

**Done when:** `uv run pytest tests/test_agent.py -v` passes. Manual check: adding `logging.basicConfig(level=logging.INFO)` to a test shows the new log lines.

### Step 1b: Instrument engine dispatch path (~15min)

**File:** `src/stepwise/engine.py`
**Depends on:** nothing

In `_dispatch_ready()` (line 2340): after the for loop, log via `_async_logger.info(f"Dispatched {count} ready steps for job {job_id}")`.

In `_run_executor()` (line 2397): at entry (after line 2405), log `f"Executor coroutine started: {step_name} job={job_id}"`. Before the `to_thread` call (line 2436), log `f"Submitting to thread pool: {step_name}"`.

**Done when:** `uv run pytest tests/test_engine.py -v` passes. The new log lines do not change behavior — they only add INFO-level output.

### Step 2: Add dedicated thread pool to AsyncEngine (~25min)

**File:** `src/stepwise/engine.py`
**Depends on:** nothing (structurally independent of instrumentation)

**2a.** In `AsyncEngine.__init__()` (after line 2236), add:
```python
pool_size = int(os.environ.get("STEPWISE_EXECUTOR_THREADS", "32"))
self._executor_pool = ThreadPoolExecutor(
    max_workers=pool_size, thread_name_prefix="stepwise-exec"
)
```
Add `from concurrent.futures import ThreadPoolExecutor` to imports at top of file (near line 2184).

**2b.** In `_run_executor()`, replace both `asyncio.to_thread` calls (lines 2434, 2436). The locked path (line 2434):
```python
result = await _loop.run_in_executor(
    self._executor_pool, executor.start, inputs, ctx
)
```
Same for unlocked path (line 2436). Note: `_loop` is already captured at line 2414.

**2c.** Add shutdown method to `AsyncEngine` (after the `__init__` method):
```python
def shutdown(self) -> None:
    self._executor_pool.shutdown(wait=False)
```

**2d.** In `server.py` lifespan shutdown (between lines 414-415, before `store.close()`):
```python
if hasattr(_engine, 'shutdown'):
    _engine.shutdown()
```

**Done when:** `uv run pytest tests/test_engine.py -v` passes. Thread pool is verified by checking `self._executor_pool` exists on `AsyncEngine` instances in tests. `STEPWISE_EXECUTOR_THREADS=2` env var is respected (testable by inspection or a new unit test that reads the attribute).

### Step 3: Create diagnostic concurrency flow (~25min)

**File:** `flows/test-concurrency/FLOW.yaml`, `flows/test-concurrency/scripts/timed-step.sh`, `flows/test-concurrency/scripts/analyze.py`
**Depends on:** 1a, 1b (instrumentation must be present for log output to be useful during verification)

The flow:
```yaml
name: test-concurrency
steps:
  generate-items:
    run: python3 -c "import json; print(json.dumps({'items': list(range(int('$count')))}))"
    inputs:
      count: $job.count
    outputs: [items]
  run-parallel:
    for_each: generate-items.items
    as: item_index
    inputs: { items: generate-items.items }
    outputs: [results]
    flow:
      steps:
        timed-step:
          run: bash scripts/timed-step.sh
          inputs: { item_index: $job.item_index }
          outputs: [start_ts, end_ts, index]
  analyze:
    run: python3 scripts/analyze.py
    inputs: { results: run-parallel.results }
    outputs: [max_concurrent, timeline]
```

`timed-step.sh` records `date +%s.%N` at start, sleeps 5s, records end, outputs JSON.
`analyze.py` reads all start/end timestamps, computes max overlapping windows.

**Done when:** `stepwise validate flows/test-concurrency/` passes with no warnings. `uv run stepwise run flows/test-concurrency/ --var count=3` completes and produces `max_concurrent` output.

### Step 4: Verify concurrency with new thread pool (~15min)

**Depends on:** 2 (dedicated pool), 3 (diagnostic flow)

Run the diagnostic flow with varying concurrency:
```bash
# Baseline: should show count concurrent steps
uv run stepwise run flows/test-concurrency/ --var count=5 --local
# Constrained: should show max 2 concurrent steps
STEPWISE_EXECUTOR_THREADS=2 uv run stepwise run flows/test-concurrency/ --var count=5 --local
```

Compare `max_concurrent` output. If baseline shows 5 and constrained shows 2, the thread pool is functioning correctly. Examine `stepwise.agent` and `stepwise.async_engine` log lines for timing gaps.

**Done when:** Both runs complete. `max_concurrent` values match expectations. Log output confirms instrumentation is working.

### Step 5: Test `sessions ensure` redundancy (~10min, manual)

**Depends on:** nothing (standalone manual verification)

Run:
```bash
# Create a new session name that doesn't exist
TEST_NAME="test-auto-$(date +%s)"
# Try sending a prompt WITHOUT sessions ensure first
echo "say hello" > /tmp/test-prompt.md
acpx --format json --approve-all claude -s "$TEST_NAME" --file /tmp/test-prompt.md --cwd /tmp
echo "Exit code: $?"
```

**If exit code is 0:** acpx auto-creates sessions. `sessions ensure` is redundant → proceed to step 6 with removal.
**If non-zero:** acpx requires pre-created sessions. → proceed to step 6 with timeout reduction (30s → 5s).

**Done when:** Test executed, result documented in commit message.

### Step 6: Apply `sessions ensure` optimization (~15min)

**File:** `src/stepwise/agent.py`
**Depends on:** 5 (the manual test result determines which change to make)

**If step 5 confirmed auto-creation:** Remove lines 118-123 (`subprocess.run` call) and the comment on line 118. Remove the first `env = ...` line (116) since it's duplicated at line 136.

**If step 5 showed auto-creation fails:** Change `timeout=30` to `timeout=5` on line 122. Wrap in try/except to make failure non-fatal:
```python
try:
    subprocess.run(..., timeout=5, env=env)
except (subprocess.TimeoutExpired, FileNotFoundError):
    pass  # acpx will create session on demand if ensure fails
```

**Done when:** `uv run pytest tests/test_agent.py -v` passes. Running a single agent step via `stepwise run` still works correctly.

## Testing Strategy

### Regression tests (run after every step)
```bash
uv run pytest tests/test_engine.py -v      # Engine tests (steps 1b, 2)
uv run pytest tests/test_agent.py -v       # Agent tests (steps 1a, 6)
uv run pytest tests/ -v                    # Full suite (final verification)
```

### Thread pool verification (step 2)
```bash
# Verify pool attribute exists and respects env var
python3 -c "
import os; os.environ['STEPWISE_EXECUTOR_THREADS'] = '4'
from stepwise.store import SQLiteStore
from stepwise.engine import AsyncEngine
e = AsyncEngine(SQLiteStore(':memory:'))
assert e._executor_pool._max_workers == 4
assert e._executor_pool._thread_name_prefix == 'stepwise-exec'
print('OK: pool configured correctly')
"
```

### Diagnostic flow validation (step 3)
```bash
stepwise validate flows/test-concurrency/   # No errors, no warnings
uv run stepwise run flows/test-concurrency/ --var count=3 --local  # Completes successfully
```

### Concurrency measurement (step 4)
```bash
# Full concurrency
uv run stepwise run flows/test-concurrency/ --var count=5 --local 2>timing.log
grep "stepwise.agent" timing.log | head -20     # Verify agent instrumentation
grep "stepwise.async_engine" timing.log | head -10  # Verify dispatch instrumentation

# Constrained concurrency — proves pool controls parallelism
STEPWISE_EXECUTOR_THREADS=2 uv run stepwise run flows/test-concurrency/ --var count=5 --local
# Expected: max_concurrent=2 in output
```

### Sessions ensure test (step 5)
```bash
# Manual test documented above in step 5
acpx --format json --approve-all claude -s "test-auto-$(date +%s)" \
  --file /tmp/test-prompt.md --cwd /tmp
```

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Root cause is external (API rate limit, Claude Code startup) and none of our code changes fix the 1-2 limit | Medium | Low — all changes are independently valuable | Instrumentation logs pinpoint the external bottleneck, enabling a targeted follow-up (e.g., staggered agent startup, or per-job working directories). |
| Removing `sessions ensure` breaks session creation for some acpx versions | Low | High — agents fail to start | Step 5 tests this before step 6 applies the change. Fallback: reduce timeout instead of removing. |
| `run_in_executor` behaves differently from `asyncio.to_thread` for exception handling | Low | Medium — executor errors could be swallowed | `asyncio.to_thread` internally calls `run_in_executor`. The only difference is `to_thread` uses `functools.partial` with `contextvars.copy_context()`. Agent executors don't use context vars, so behavior is identical. Existing tests (`test_engine.py`) will catch regressions. |
| Diagnostic flow's for-each doesn't launch sub-steps in parallel | Low | Low — only affects diagnosis accuracy | Verified: engine's `_launch_for_each` creates sub-jobs, each started immediately via `start_job`. `_dispatch_ready` launches all ready steps. Sub-flow steps with no deps are immediately ready. |
| `STEPWISE_EXECUTOR_THREADS` set too low in production | Low | Medium — fewer concurrent agents than expected | Default is 32 (matches current implicit behavior). Env var is opt-in. Document in CLAUDE.md. |
