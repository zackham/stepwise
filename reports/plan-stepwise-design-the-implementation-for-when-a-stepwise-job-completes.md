# Plan: Close acpx Sessions on Job Completion/Failure

## Overview

When a stepwise job finishes (completes, fails, or is cancelled), the acpx queue-owner processes for agent steps should be closed immediately rather than idling until the 4-hour TTL. This reclaims resources and avoids orphaned processes accumulating on long-running servers.

**Current state:** A `_cleanup_job_sessions()` method already exists in `engine.py:1329` and is called from all COMPLETED and FAILED paths in both the legacy `Engine` and `AsyncEngine`. However, it has several gaps that prevent it from working reliably in all cases.

## Requirements

### R1: Session cleanup on all terminal job states
**Acceptance criteria:**
- Sessions are closed when a job reaches COMPLETED status
- Sessions are closed when a job reaches FAILED status
- Sessions are closed when a job is CANCELLED (currently missing)
- Cleanup is non-blocking (fire-and-forget daemon thread)

### R2: Correct acpx invocation
**Acceptance criteria:**
- Agent name is resolved from the step's executor config, not hardcoded to `"claude"`
- `CLAUDECODE` env var is stripped to avoid nested-session conflicts (matching `AcpxBackend` behavior)
- acpx binary path is resolved via the same logic as `AcpxBackend` (not bare `"acpx"`)

### R3: Fallback cleanup
**Acceptance criteria:**
- If `acpx sessions close` fails or times out, fall back to SIGTERM via queue-owner lock file (matching `AcpxBackend.cleanup_session_queue_owner` at `agent.py:535`)
- Log failures at warning level without blocking the engine

### R4: Test coverage
**Acceptance criteria:**
- Unit test verifying session names are collected from executor_state across multiple runs
- Unit test verifying cancel_job triggers session cleanup
- Unit test verifying cleanup tolerates missing/empty executor_state gracefully

## Assumptions

| # | Assumption | Verified against |
|---|---|---|
| A1 | `executor_state["session_name"]` is the stable string name used by acpx for session identification | `agent.py:1037` — confirmed: `"session_name": process.session_name` |
| A2 | `executor_state["session_id"]` is the UUID needed for lock-file fallback | `agent.py:1036` — confirmed: `"session_id": agent_status.session_id or process.session_id` |
| A3 | `AcpxBackend.cleanup_session_queue_owner()` is the reference implementation for proper cleanup | `agent.py:535-579` — confirmed: tries cooperative close, falls back to SIGTERM via `_find_queue_owners()` |
| A4 | The engine does not hold a reference to `AcpxBackend` instances after executor `start()` returns | Confirmed: executors are created per-launch via `registry.create()` and not retained |
| A5 | `_cleanup_job_sessions` is already called from all COMPLETED and FAILED paths | `engine.py` grep: lines 950, 967, 974 (legacy), 2818, 2839 (halt/abandon), 3428, 3444, 3451 (async) |
| A6 | `cancel_job` does NOT call `_cleanup_job_sessions` | `engine.py:327-372` (legacy), `engine.py:3082-3111` (async) — confirmed: no cleanup call |

## Implementation Steps

### Step 1: Add `_cleanup_job_sessions` call to `cancel_job`

**File:** `src/stepwise/engine.py`

In `Engine.cancel_job()` (line 327), add `self._cleanup_job_sessions(job.id)` after setting `job.status = JobStatus.CANCELLED` (after line 372). The `AsyncEngine.cancel_job()` (line 3082) calls `super().cancel_job()`, so the base class change covers both.

### Step 2: Resolve agent name from executor config instead of hardcoding

**File:** `src/stepwise/engine.py`, method `_cleanup_job_sessions` (line 1329)

Currently the method runs:
```python
["acpx", "claude", "sessions", "close", "--name", name]
```

Change to collect the agent name from each run's step definition executor config. The agent name lives in `step_def.executor.config.get("agent")` or defaults to `"claude"`. Since different steps in the same job could use different agents, collect `(session_name, agent_name)` pairs instead of just session names.

```python
runs = self.store.runs_for_job(job_id)
sessions: dict[str, str] = {}  # session_name → agent_name
for run in runs:
    es = run.executor_state or {}
    name = es.get("session_name")
    if name and name not in sessions:
        step_def = job.workflow.steps.get(run.step_name)
        agent = "claude"
        if step_def and step_def.executor and step_def.executor.config:
            agent = step_def.executor.config.get("agent", "claude")
        sessions[name] = agent
```

This requires loading the job to access workflow step definitions. The job is already available via `self.store.load_job(job_id)`, or can be passed as a parameter.

### Step 3: Strip CLAUDECODE env var and resolve acpx path

**File:** `src/stepwise/engine.py`, method `_cleanup_job_sessions`

Add env filtering matching `AcpxBackend` pattern:
```python
env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
```

For acpx path resolution: use `shutil.which("acpx")` or a module-level constant. The `AcpxBackend` stores `self.acpx_path` from config, but the engine doesn't have access to agent config. Using `shutil.which("acpx")` with fallback to `"acpx"` is sufficient since the engine only needs to close sessions, not spawn agents.

### Step 4: Add SIGTERM fallback via lock file

**File:** `src/stepwise/engine.py`, method `_cleanup_job_sessions`

After `acpx sessions close` fails (non-zero return or timeout), fall back to killing the queue-owner process. Import and reuse `_find_queue_owners()` and `_is_pid_alive()` from `agent.py`:

```python
from stepwise.agent import _find_queue_owners, _is_pid_alive

# In _close_sessions(), after subprocess.run fails:
session_id = run_session_ids.get(name)
if session_id:
    for info in _find_queue_owners():
        if info.session_id == session_id:
            if _is_pid_alive(info.pid):
                os.kill(info.pid, signal.SIGTERM)
            break
```

This requires also collecting `session_id` (UUID) alongside `session_name` from executor_state.

**Module DAG check:** `agent.py` imports from `executors.py` and `models.py`. `engine.py` already imports from `agent.py` (for `AgentExecutor`). Importing helper functions from `agent.py` into `engine.py` is allowed by the DAG (`models → executors → engine`; `agent` is at the same level as `engine`, and engine already imports from it).

### Step 5: Pass job workflow to `_cleanup_job_sessions`

**File:** `src/stepwise/engine.py`

Change the signature from `_cleanup_job_sessions(self, job_id: str)` to `_cleanup_job_sessions(self, job_id: str, job: Job | None = None)` so callers that already have the job loaded can pass it in, avoiding a redundant `store.load_job()`. All existing call sites already have `job` in scope.

Update all call sites (9 total):
- Legacy Engine: lines 950, 967, 974
- AsyncEngine `_halt_job`: line 2839
- AsyncEngine `_fail_run` abandon: line 2818
- AsyncEngine `_check_job_terminal`: lines 3428, 3444, 3451
- New: `Engine.cancel_job` (from Step 1)

### Step 6: Add tests

**File:** `tests/test_session_cleanup.py` (new file)

```python
# Test 1: Session names collected from executor_state
# - Create a job with 2 agent steps that have session_name in executor_state
# - Call _cleanup_job_sessions
# - Verify subprocess.run called with correct session names

# Test 2: Deduplication
# - Two runs with the same session_name (loop iterations)
# - Verify close is called only once per unique name

# Test 3: cancel_job triggers cleanup
# - Create a running job, cancel it
# - Verify _cleanup_job_sessions was called

# Test 4: Graceful handling of missing executor_state
# - Mix of runs with and without executor_state
# - No crash, only valid sessions closed

# Test 5: CLAUDECODE env var is stripped
# - Set CLAUDECODE in os.environ
# - Verify subprocess.run env doesn't contain it
```

Use `unittest.mock.patch("subprocess.run")` to mock the acpx call. Use `register_step_fn` and `run_job_sync` for integration-style tests that exercise the full completion path.

## Testing Strategy

```bash
# Run just the new test file
uv run pytest tests/test_session_cleanup.py -v

# Run all engine tests to verify no regressions
uv run pytest tests/test_engine.py -v

# Run the full suite
uv run pytest tests/
```

Manual verification:
1. Run a flow with an agent step (`stepwise run` with a `.flow.yaml` that uses `executor: agent`)
2. After job completes, verify no orphaned `acpx __queue-owner` processes remain: `ps aux | grep queue-owner`
3. Cancel a running agent job, verify same cleanup occurs

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| `_find_queue_owners()` import creates tight coupling with agent.py internals | Low — these are stable utility functions | If they change, tests will catch it. Could extract to a shared `acpx_utils.py` later if needed. |
| SIGTERM fallback kills a queue owner that's being reused by another job | Medium — data loss in concurrent session | Only kill if session_id matches exactly. Queue owners are per-session, not shared. |
| `cancel_job` is called recursively for sub-jobs — cleanup runs multiple times for shared sessions | Low — `acpx sessions close` is idempotent | The dedup set prevents double-close within a single job. Cross-job dedup not needed since close is safe to call twice. |
| Cleanup thread outlives engine shutdown | Low — daemon thread dies with process | Already the case with current implementation; daemon=True is correct. |
