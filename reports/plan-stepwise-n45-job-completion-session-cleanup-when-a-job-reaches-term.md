# N45: Job-Completion Session Cleanup

**Status:** Implemented (commit `1e2ba92`)

## Overview

When a stepwise job reaches a terminal state (completed, failed, or cancelled), any acpx agent sessions created during that job's execution must be closed. Without cleanup, orphaned queue-owner processes accumulate on the host, consuming memory and file descriptors. This feature collects session names from each agent step's `executor_state`, deduplicates them, and closes sessions in a background thread so the engine's event loop is never blocked.

## Requirements

| # | Requirement | Acceptance Criteria |
|---|---|---|
| R1 | Close sessions on job **completion** | All unique `session_name` values from the job's step runs are sent to `acpx sessions close` when `job.status → COMPLETED` |
| R2 | Close sessions on job **failure** | Same behavior when `job.status → FAILED` (step failure, abandon exit rule, no-terminal-reached settlement) |
| R3 | Close sessions on job **cancellation** | Same behavior when `cancel_job()` is called |
| R4 | Don't close sessions for running jobs | `_cleanup_job_sessions` is only called after the job status has been set to a terminal value |
| R5 | Deduplicate across loop iterations | If multiple runs share the same `session_name` (e.g., `continue_session` loops), close it only once |
| R6 | Resolve agent name from step config | The correct agent binary (claude, codex, gemini) is passed to `acpx {agent} sessions close` |
| R7 | Handle "already closed" gracefully | Non-zero return from `acpx sessions close` logs a debug message and falls back to SIGTERM |
| R8 | Handle "not found" gracefully | Missing session / missing lock file is caught and logged, never crashes the engine |
| R9 | Non-blocking execution | Cleanup runs in a daemon thread; engine event loop is never blocked |
| R10 | Strip CLAUDECODE env var | The subprocess environment excludes `CLAUDECODE` to prevent re-entering Claude Code |
| R11 | Skip cleanup when no sessions exist | If no runs have `session_name` in `executor_state`, no thread is spawned |

## Assumptions (verified against source)

| Assumption | Verified in |
|---|---|
| Agent steps store `session_name` and `session_id` in `StepRun.executor_state` | `src/stepwise/agent.py:1069–1132` — `AgentExecutor.start()` builds state dict with both keys |
| `store.runs_for_job(job_id)` returns all runs (all attempts, all steps) for a job | `src/stepwise/store.py:495–500` — SQL query filters by `job_id`, ordered by attempt |
| `acpx {agent} sessions close --name {name}` is the cooperative close API | `src/stepwise/agent.py:566–610` — `cleanup_session_queue_owner()` uses same command |
| Queue-owner lock files at `~/.acpx/queues/{name}.lock` contain `{pid, sessionId}` | `src/stepwise/agent.py:88–112` — `_find_queue_owners()` parses this format |
| Per-step cleanup is skipped when `continue_session=True` (deferred to job cleanup) | `src/stepwise/agent.py:1057–1067` — explicit skip comment |
| Both Engine (legacy tick) and AsyncEngine share the same base class method | `src/stepwise/engine.py:1331` — `_cleanup_job_sessions` defined on `Engine`, inherited by `AsyncEngine` |

## Implementation Steps

### Step 1: Add `_cleanup_job_sessions` method to `Engine` base class

**File:** `src/stepwise/engine.py:1331–1406`

The method:
1. Loads the job (if not passed) to access `workflow.steps` for agent name resolution
2. Iterates `store.runs_for_job(job_id)` to collect `(session_name, agent_name, session_id)` tuples
3. Deduplicates by `session_name` (first occurrence wins — earlier attempts have the original session)
4. Resolves agent name from `step_def.executor.config.get("agent", "claude")`, defaulting to `"claude"`
5. Returns early if no sessions found (no thread spawned)
6. Builds a clean env dict stripping `CLAUDECODE`
7. Spawns a daemon thread running `_close_sessions()` inner function

The inner `_close_sessions` function (runs in thread):
- For each session: try `acpx {agent} sessions close --name {name}` with 10s timeout
- On success (rc=0): log debug, mark closed
- On failure: log debug, fall back to SIGTERM via lock file lookup
- Fallback: import `_find_queue_owners` and `_is_pid_alive` from `stepwise.agent`, find the queue owner by `session_id`, send `SIGTERM` if alive

### Step 2: Hook into legacy Engine completion path

**File:** `src/stepwise/engine.py:942–976` (`_tick_job`)

Call `_cleanup_job_sessions(job.id, job)` at three points:
- Line 952: After `job.status = COMPLETED` (normal completion)
- Line 969: After `job.status = COMPLETED` (post-settlement re-check)
- Line 976: After `job.status = FAILED` (no terminal reached)

### Step 3: Hook into legacy Engine cancellation path

**File:** `src/stepwise/engine.py:328–374` (`cancel_job`)

Call `_cleanup_job_sessions(job.id, job)` at line 374, after `job.status = CANCELLED` and `store.save_job()`.

### Step 4: Hook into legacy Engine failure paths

**File:** `src/stepwise/engine.py:2860–2888`

Call `_cleanup_job_sessions(job.id, job)` at:
- Line 2867: After `_emit(JOB_FAILED)` in `_fail_run` abandon-action branch
- Line 2888: At end of `_halt_job` (step-failure-induced halt)

### Step 5: Hook into AsyncEngine completion/failure path

**File:** `src/stepwise/engine.py:3467–3500` (`_check_job_terminal`)

Call `_cleanup_job_sessions(job.id, job)` at:
- Line 3477: After `job.status = COMPLETED` (normal)
- Line 3493: After `job.status = COMPLETED` (post-settlement)
- Line 3500: After `job.status = FAILED` (no terminal reached)

### Step 6: Hook into AsyncEngine cancellation path

**File:** `src/stepwise/engine.py:3131–3140` (`AsyncEngine.cancel_job`)

No additional call needed — `super().cancel_job()` delegates to the legacy Engine's `cancel_job` (Step 3), which already calls `_cleanup_job_sessions`.

### Summary of call sites (10 total)

| Call site | Engine | Terminal state | Line |
|---|---|---|---|
| `cancel_job` | Legacy Engine | CANCELLED | 374 |
| `_tick_job` (completion) | Legacy Engine | COMPLETED | 952 |
| `_tick_job` (post-settlement completion) | Legacy Engine | COMPLETED | 969 |
| `_tick_job` (no terminal reached) | Legacy Engine | FAILED | 976 |
| `_fail_run` (abandon action) | Shared | FAILED | 2867 |
| `_halt_job` | Shared | FAILED | 2888 |
| `_check_job_terminal` (completion) | AsyncEngine | COMPLETED | 3477 |
| `_check_job_terminal` (post-settlement completion) | AsyncEngine | COMPLETED | 3493 |
| `_check_job_terminal` (no terminal reached) | AsyncEngine | FAILED | 3500 |
| `cancel_job` (via super) | AsyncEngine | CANCELLED | 374 (inherited) |

## Edge Cases Handled

| Edge case | How handled |
|---|---|
| Step has no `executor_state` | `es = run.executor_state or {}` — safe dict access |
| Step has `executor_state` but no `session_name` | `es.get("session_name")` returns `None` → skipped |
| Multiple runs share same session (loop/continue_session) | Dedup dict keyed by `session_name` — first wins |
| `acpx sessions close` returns non-zero | Logs debug, falls through to SIGTERM fallback |
| `acpx sessions close` times out (hangs) | 10-second `timeout` on `subprocess.run` → `TimeoutExpired` caught |
| Queue owner already exited | `_is_pid_alive()` returns `False` → `os.kill` skipped |
| No lock file found for session_id | `_find_queue_owners()` returns no match → loop ends without action |
| No sessions to clean up at all | Early return before thread creation |
| `acpx` binary not found | `shutil.which("acpx") or "acpx"` — falls back to PATH lookup; subprocess failure caught |
| Job not found in store | `try/except` around `store.load_job()` → early return |
| CLAUDECODE env var present | Stripped from env dict to prevent re-entering Claude Code |

## Testing Strategy

### Test file: `tests/test_session_cleanup.py`

**Run:** `uv run pytest tests/test_session_cleanup.py -v`

| Test class | Test | What it verifies |
|---|---|---|
| `TestSessionCleanupOnCompletion` | `test_sessions_closed_on_job_complete` | Sessions from agent-like steps are collected on COMPLETED |
| | `test_custom_agent_name_resolved` | Agent name comes from `executor.config`, not hardcoded "claude" |
| | `test_deduplication_across_loop_iterations` | Same `session_name` from multiple runs yields single cleanup entry |
| `TestSessionCleanupOnCancel` | `test_cancel_triggers_cleanup` | `cancel_job()` triggers session collection |
| `TestSessionCleanupGracefulHandling` | `test_no_crash_with_missing_executor_state` | Steps without `executor_state` don't crash cleanup |
| | `test_mixed_steps_only_agent_sessions_closed` | Only runs with `session_name` appear in cleanup set |
| | `test_no_sessions_skips_cleanup` | No thread spawned when no sessions exist |
| `TestSessionCleanupEnvStripping` | `test_claudecode_stripped` | `CLAUDECODE` removed from subprocess env |
| `TestCloseSessionsFallback` | `test_sigterm_fallback_on_acpx_failure` | Non-zero `acpx close` → SIGTERM via lock file lookup |

### Test technique

Tests use `_call_cleanup_directly()` helper which patches `threading.Thread` to capture the target function and arguments without actually spawning a thread. This allows:
- Inspecting the session dict passed to the close function
- Inspecting the environment dict
- Calling the close function synchronously with mocked `subprocess.run` and agent helpers

The `AgentLikeExecutor` inline class mimics real agent behavior by returning `executor_state` with `session_name` and `session_id`, without needing acpx installed.

### Full test suite verification

```bash
uv run pytest tests/test_session_cleanup.py -v          # session cleanup tests
uv run pytest tests/test_session_continuity.py -v        # session continuity (no regression)
uv run pytest tests/test_engine.py -v                    # engine core (no regression)
uv run pytest tests/ -v                                  # full suite
```
