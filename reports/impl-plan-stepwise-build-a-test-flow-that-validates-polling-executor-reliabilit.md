---
title: "Implementation Plan: Polling Executor Reliability Test Flow"
date: "2026-03-20"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Polling Executor Reliability Test Flow

## Overview

Create a test flow at `flows/test-polling/` that validates polling executor reliability across four scenarios: basic file-based polling, server restart survival, long-running polls (>5 min), and simulated backoff. The flow uses script steps for setup and inline check_commands for polling, producing deterministic, self-contained tests with no external dependencies.

## Requirements

### R1: Basic polling with file-based condition
- **What:** A poll step that checks for a file's existence every N seconds, with a setup step that creates the file after a configurable delay.
- **Acceptance criteria:**
  - Setup step outputs `marker_path` as JSON
  - Poll step enters SUSPENDED within 2 seconds of setup completing
  - Poll step transitions to COMPLETED when marker file appears
  - Poll step artifact contains `{"ready": true}` from marker file content
  - `stepwise validate` produces zero warnings for this scenario

### R2: Server restart survival
- **What:** A poll step with a long enough interval that the user can restart the server mid-poll and observe recovery behavior.
- **Acceptance criteria:**
  - README documents step-by-step manual restart procedure
  - Poll step uses `interval_seconds: 5` and setup delay of 30s (enough time to restart)
  - Poll step has a `prompt` field explaining what the user should do
  - README documents the known gap: poll watch timers are NOT re-scheduled after restart (citing `engine.py:2502` and `server.py:337`)

### R3: Long-running poll (>5 min)
- **What:** A poll step with `interval_seconds: 30` that waits for a condition controlled by a configurable delay (default 6 minutes).
- **Acceptance criteria:**
  - Default delay is 360 seconds (6 min); overridable via `--var longrun_delay_seconds=N`
  - Poll step checks at 30-second intervals (verified via engine `_update_watch_state` timestamps in `executor_state._watch.last_checked_at`)
  - Flow completes successfully after the configured duration
  - Quick-mode override (`--var longrun_delay_seconds=10`) allows sub-minute testing

### R4: Simulated backoff
- **What:** A poll step whose `check_command` implements client-side backoff via an attempt counter in a state file, succeeding only after N failed checks.
- **Acceptance criteria:**
  - check_command maintains attempt count in a state file under the workspace
  - First `$max_fails` checks output empty string (not ready); check N+1 outputs JSON (fulfilled)
  - Attempt count and status are logged to stderr for observability
  - Flow completes when `max_fails` threshold is exceeded
  - `max_fails` is configurable via `--var backoff_fail_count=N`

## Assumptions

1. **Poll executor uses `check_command` shell scripts for condition checks.** Verified: `PollExecutor` in `src/stepwise/executors.py:311-356` runs `check_command` via `subprocess.run()` (called from `engine.py:1844`). JSON dict on stdout = fulfilled, empty stdout = not ready, non-zero exit = error/retry.

2. **`interval_seconds` is a fixed interval with no built-in backoff.** Verified: `_schedule_poll_watch` in `src/stepwise/engine.py:2447-2455` creates an asyncio task that pushes `poll_check` events at a constant interval (`watch.config.get("interval_seconds", 60)`). The `RetryDecorator` in `src/stepwise/decorators.py:67-110` supports `backoff: "exponential"` but only for executor-level retries, not poll check intervals.

3. **Server restart does NOT re-schedule poll watch timers.** Verified: `_schedule_poll_watch` is only called from `_handle_queue_event` when a step first suspends (`engine.py:2500-2502`). On server restart: `_cleanup_zombie_jobs` (`server.py:325-358`) skips jobs with suspended runs (`server.py:337`). The `AsyncEngine.run()` loop (`engine.py:2240-2258`) calls `_poll_external_changes` on its 5-second timeout (`engine.py:2254`), which calls `_dispatch_ready`/`_check_job_terminal` — neither re-schedules poll watch timers. The legacy `Engine.tick()` loop (`engine.py:736-738`) does check suspended poll runs directly via `_check_poll_watch()`, but is not used by the server.

4. **`check_command` runs with `cwd=workspace`, not `flow_dir`.** Verified: `engine.py:1850` sets `cwd=workspace`. The env only contains `{**os.environ, "JOB_ENGINE_INPUTS": str(input_file)}` (`engine.py:1841`). Unlike `ScriptExecutor` which receives `flow_dir` and resolves relative script paths (`executors.py:138-187`), `PollExecutor` does not. **Implication:** check_commands must be inline bash, not relative script paths.

5. **`check_command` template variables are interpolated once at start time.** Verified: `PollExecutor.start()` at `executors.py:331-333` uses `Template(self.check_command).safe_substitute(str_inputs)` to resolve `$var` placeholders before storing the resolved command in `WatchSpec.config`. The engine runs the pre-resolved command string on each poll interval.

6. **`run:` script steps DO resolve relative paths via `flow_dir`.** Verified: `ScriptExecutor.__init__` receives `flow_dir` (`executors.py:138`), and `_resolve_command` (`executors.py:144-187`) converts relative paths like `bash scripts/setup-marker.sh` to absolute paths when found under `flow_dir`. Setup steps use `run:` and benefit from this resolution.

7. **No existing README files in flows.** Verified: `Glob` for `flows/**/README*` returned no results. This flow will be the first.

## Out of Scope

### Excluded with rationale

- **Modifying the engine to fix the poll watch restart gap.** The restart test (R2) documents the gap as a known issue. Fixing it requires changes to `AsyncEngine._poll_external_changes()` (`engine.py:2261`) to re-schedule `_schedule_poll_watch` for existing suspended poll runs, or adding re-arm logic to the server lifespan startup (`server.py:361-388`). That's engine work; this plan is flow-only. The test flow becomes a regression test once the fix lands.

- **HTTP endpoint polling.** The spec offers "check if a file exists, or poll an HTTP endpoint." File-based is chosen because: (a) the polling executor contract is check_command → JSON on stdout — the reliability being tested is the engine's poll scheduling and fulfillment pipeline (`engine.py:2447-2539`), not the check_command's internals; (b) HTTP polling adds a network dependency (requires a test server or external endpoint), making the flow non-portable and flaky; (c) a `curl`-based check_command would be a one-line substitution in the YAML if HTTP coverage is later desired — no structural changes needed.

- **Poll timeout / cancellation edge cases.** The engine supports `limits.max_duration_minutes` for steps (`engine.py:2000-2009`) and poll watches are cancelled on job cancel (`engine.py:2295-2303`). These are already tested in `tests/test_poll_executor.py:162-193` (`test_poll_watch_cancelled_on_job_cancel`). Adding timeout/cancel scenarios to this flow would duplicate existing unit test coverage without adding new signal.

- **Concurrent poll steps competing for resources.** The four scenarios already run in parallel (testing concurrent polls), but we don't test for thread starvation or lock contention. The engine runs check_commands synchronously in `_check_poll_watch` (`engine.py:1844`) — concurrent polls could theoretically starve each other if check_commands are slow. This is a benchmarking concern, not a reliability test.

- **Automated CI integration.** The server restart test (R2) is inherently manual. The long-running test (R3, 6 min default) exceeds typical CI timeouts. The quick smoke test (`--var` overrides) is CI-compatible but adding a CI job is outside this plan's scope.

- **Adding native backoff to `PollExecutor`.** No backoff exists in the engine for poll intervals (assumption #2). If native backoff is needed, it would be a `PollExecutor` enhancement adding `backoff_strategy` and `max_interval` to `WatchSpec.config` — separate from this test flow.

- **`for_each` or sub-flow wrapping.** The four scenarios are flat top-level steps. Using sub-flows would hide them from the top-level DAG view and add indirection. Since each scenario is only two steps (setup + poll), the flat structure is simpler to debug.

## Architecture

### Pattern alignment

The flow follows established patterns from two existing flows:

| Pattern | Source | Usage in this flow |
|---------|--------|-------------------|
| `config:` block with typed params | `flows/eval-1-0/FLOW.yaml:6-19` | Configurable delays and fail counts |
| `run: \|` inline scripts with `printf` JSON | `flows/welcome/FLOW.yaml:42-50` | Setup steps that create marker files |
| `executor: poll` with `check_command` | CLAUDE.md poll step docs + `src/stepwise/executors.py:311-356` | Core poll steps under test |
| `scripts/` subdirectory | `flows/eval-1-0/scripts/` | Setup scripts (resolved via `ScriptExecutor.flow_dir`) |
| `prompt:` on poll steps | `src/stepwise/executors.py:326-328` | User-facing messages during suspension |
| Parallel independent steps | `flows/eval-1-0/FLOW.yaml:50-104` (parallel test phases) | Four test scenarios run concurrently |

### Key architectural constraint: check_command CWD

`PollExecutor`'s `check_command` runs with `cwd=workspace` and no `flow_dir` resolution (unlike `ScriptExecutor`). This means:
- **Setup steps** (`run:` field) → can reference `scripts/setup-marker.sh` (resolved via `ScriptExecutor._resolve_command`, `executors.py:144-187`)
- **Poll check_commands** → must use inline bash or absolute paths. We use inline bash with `$var` placeholders resolved at start time (`executors.py:331-333`)

### Flow structure

```
flows/test-polling/
├── FLOW.yaml              # All 9 steps (4 setups + 4 polls + 1 summarize)
├── README.md              # Usage docs, known issues, test procedures
└── scripts/
    └── setup-marker.sh    # Shared setup script for creating delayed markers
```

Note: check_commands are inline in FLOW.yaml (not separate script files) because `PollExecutor` does not resolve relative script paths (assumption #4).

### Step DAG

```
setup-basic ───→ poll-basic ────┐
setup-restart ─→ poll-restart ──┤
setup-longrun ─→ poll-longrun ──┤
setup-backoff ─→ poll-backoff ──┤
                                └→ summarize
```

Each scenario is independent: no cross-scenario data deps or sequencing. The `summarize` step has data dependencies (input bindings) on all four poll steps' outputs, which enforces the ordering without explicit `sequencing:` declarations.

## Implementation Steps

### Dependency graph

```
Step 1 (setup-marker.sh)
  └→ Step 2a (FLOW.yaml skeleton + basic scenario)
       ├→ Step 2b (restart scenario)    ─┐
       ├→ Step 2c (longrun scenario)    ─┤ [parallel — no interdependencies]
       ├→ Step 2d (backoff scenario)    ─┤
       └→ Step 3 (summarize step)       ─┘ [needs all scenarios defined]
            └→ Step 4 (validate)
                 ├→ Step 5 (README)     ─┐ [parallel — independent outputs]
                 └→ Step 6 (smoke test) ─┘
```

### Step 1: Create `scripts/setup-marker.sh` (~15 min)

**File:** `flows/test-polling/scripts/setup-marker.sh`

**Why first:** All four setup steps reference this script via `run: bash scripts/setup-marker.sh`. It must exist before FLOW.yaml can be validated. No other step produces inputs this step needs.

**Behavior:**
- Creates marker directory under workspace: `marker_dir="$JOB_ENGINE_WORKSPACE/markers"` (`JOB_ENGINE_WORKSPACE` is set by `ScriptExecutor` at `executors.py:202`)
- Sets `marker_path="$marker_dir/${marker_id}.json"`
- Launches background delayed write: `(sleep "$delay_seconds" && printf '{"ready": true, "created_at": "%s"}' "$(date -Iseconds)" > "$marker_path") &`
- Traps EXIT to kill background jobs: `trap 'kill $(jobs -p) 2>/dev/null' EXIT`
- Outputs JSON: `printf '{"marker_path": "%s"}' "$marker_path"`

**Why a script file, not inline:** Setup logic needs `trap`, background process management, and workspace path construction — 8+ lines of bash, which is cleaner in a file than inline YAML. `ScriptExecutor` resolves `bash scripts/setup-marker.sh` via `flow_dir` (`executors.py:144-177`).

### Step 2a: Write FLOW.yaml — skeleton + basic polling scenario (~20 min)

**File:** `flows/test-polling/FLOW.yaml`

**Why this ordering:** The YAML skeleton (metadata, config block) must exist before any scenario can be added. Basic polling is the simplest scenario and validates that the overall structure works — if this passes `stepwise validate`, the pattern is proven for the remaining scenarios.

**Contents:**
- Flow metadata: `name: test-polling`, `description`, `author: stepwise`, `tags: [test, poll, reliability]`
- Config block with three parameters (following `flows/eval-1-0/FLOW.yaml:6-19`):
  - `basic_delay_seconds` (type: str, default: "5")
  - `longrun_delay_seconds` (type: str, default: "360")
  - `backoff_fail_count` (type: str, default: "4")
- `setup-basic`: `run: bash scripts/setup-marker.sh`, inputs `{delay_seconds: $job.basic_delay_seconds, marker_id: "basic"}`, outputs `[marker_path]`
- `poll-basic`: `executor: poll`, inline `check_command` (file existence check using pre-resolved `$marker_path`), `interval_seconds: 2`, `prompt: "Waiting for basic marker file..."`, inputs `{marker_path: setup-basic.marker_path}`, outputs `[ready]`

**Why `poll-basic` depends on `setup-basic`:** The poll step needs `setup-basic.marker_path` as an input binding. This creates a data dependency: the engine won't launch `poll-basic` until `setup-basic` has a current completed run with a `marker_path` artifact (per `_is_step_ready` in `engine.py`).

### Step 2b: Write FLOW.yaml — restart survival scenario (~10 min)

**File:** `flows/test-polling/FLOW.yaml` (append steps)

**Why parallel with 2c/2d:** This scenario is structurally identical to basic (same setup script, same check_command pattern). No code from Steps 2c or 2d is needed. Can be written in any order relative to other scenarios.

**Contents:**
- `setup-restart`: `run: bash scripts/setup-marker.sh`, inputs `{delay_seconds: "30", marker_id: "restart"}`, outputs `[marker_path]`. Hard-coded 30s delay gives the user time to restart the server.
- `poll-restart`: `executor: poll`, `interval_seconds: 5`, `prompt` explaining the restart procedure, inputs `{marker_path: setup-restart.marker_path}`, outputs `[ready]`

**Why `interval_seconds: 5` (not 2):** A 5-second interval is long enough to observe distinct poll cycles in the web UI during the restart test, but short enough that the test doesn't drag. The 30-second setup delay provides a 6-cycle window for the restart.

### Step 2c: Write FLOW.yaml — long-running poll scenario (~10 min)

**File:** `flows/test-polling/FLOW.yaml` (append steps)

**Why parallel with 2b/2d:** Same structural pattern, no dependencies on other scenarios.

**Contents:**
- `setup-longrun`: `run: bash scripts/setup-marker.sh`, inputs `{delay_seconds: $job.longrun_delay_seconds, marker_id: "longrun"}`, outputs `[marker_path]`
- `poll-longrun`: `executor: poll`, `interval_seconds: 30`, `prompt` showing expected duration, inputs `{marker_path: setup-longrun.marker_path, longrun_delay_seconds: $job.longrun_delay_seconds}`, outputs `[ready]`

**Why `interval_seconds: 30`:** Matches a realistic production polling cadence. At default `longrun_delay_seconds=360`, this produces ~12 poll cycles — enough to verify sustained polling behavior without flooding the event log.

### Step 2d: Write FLOW.yaml — backoff simulation scenario (~20 min)

**File:** `flows/test-polling/FLOW.yaml` (append steps)

**Why 20 min (longer than 2b/2c):** This scenario has unique complexity: the check_command must maintain state across invocations via a file-based counter, and the setup step differs from the shared `setup-marker.sh` pattern (it creates a state directory instead of a delayed marker). The inline check_command is ~8 lines of POSIX shell.

**Why parallel with 2b/2c:** Despite being more complex, this scenario has no data or structural dependency on the restart or longrun scenarios. It only depends on the FLOW.yaml skeleton from Step 2a.

**Contents:**
- `setup-backoff`: `run:` inline script that creates `markers/backoff-state` directory under workspace, outputs `[marker_path, state_dir, max_fails]`. Does NOT use `setup-marker.sh` because there's no delayed file creation — the check_command itself decides when to write the marker.
- `poll-backoff`: `executor: poll`, `interval_seconds: 3`, inline multi-line check_command that: reads/increments counter from `$state_dir/attempt_count`, logs to stderr, outputs JSON when `n > $max_fails`. The `$state_dir` and `$max_fails` placeholders are resolved at start time (assumption #5).

**Why `setup-backoff` doesn't use `setup-marker.sh`:** The backoff scenario's fulfillment condition is controlled by the check_command itself (attempt count), not by an external timer. The setup step only creates the state directory; the check_command creates the marker file when the threshold is reached.

### Step 3: Write FLOW.yaml — summarize step (~10 min)

**File:** `flows/test-polling/FLOW.yaml` (append)

**Why after all scenarios:** The summarize step has input bindings from all four poll steps (`poll-basic.ready`, `poll-restart.ready`, `poll-longrun.ready`, `poll-backoff.ready`). These input bindings must reference steps that exist in the YAML. Writing summarize before its dependencies are defined would cause `stepwise validate` to fail with "unknown step" errors.

**Contents:**
- `summarize`: `run:` inline script. Inputs: `ready` output from all four poll steps. Outputs: `[basic_passed, restart_passed, longrun_passed, backoff_passed, all_passed]`. Each `*_passed` is `true` if the corresponding input is truthy. `all_passed` is `true` only if all four pass.

**Why summarize exists:** Without it, there's no single terminal step to indicate overall pass/fail. The engine considers a job complete when all terminal steps (steps with no downstream dependents) have completed (`engine.py:965-971`). With four independent poll steps as terminals, the job completes when all four finish, but there's no unified artifact. Summarize provides that.

### Step 4: Validate FLOW.yaml (~10 min)

**Command:** `stepwise validate flows/test-polling/FLOW.yaml`

**Why after Step 3:** Validation checks that all input bindings resolve, all outputs are declared, and no structural errors exist. Running it before all steps are defined would produce false positives (missing step references). Running it after all steps but before README/testing catches errors before investing time in documentation.

**Fix any warnings:** Common issues to watch for: undeclared outputs in check_commands, missing `check_command` field (caught by `yaml_loader.py` poll validation), input bindings referencing wrong field names.

### Step 5: Write README.md (~20 min)

**File:** `flows/test-polling/README.md`

**Why after validation (not before):** The README documents expected behavior, which must match the validated flow definition. Writing docs for an unvalidated flow risks documenting behavior that doesn't match the actual YAML.

**Why parallel with Step 6:** README and smoke testing are independent deliverables. README documents expected behavior; smoke testing verifies actual behavior. Neither blocks the other.

**Sections:**
1. **Purpose** — What each scenario tests and why
2. **Quick run** — exact `stepwise run` command with `--var` overrides (~30s)
3. **Full run** — `stepwise run --watch` with defaults (~6 min)
4. **Server restart test procedure** — Numbered manual steps
5. **Known issue: poll watch restart gap** — cites `engine.py:2447-2502`, `server.py:325-358`, explains the `AsyncEngine` vs legacy `Engine` difference
6. **Expected behavior table** — Scenario, default duration, interval, expected outcome

### Step 6: Smoke test with quick-mode overrides (~15 min)

**Commands:**
```bash
stepwise run flows/test-polling/FLOW.yaml \
  --var basic_delay_seconds=3 \
  --var longrun_delay_seconds=10 \
  --var backoff_fail_count=2
```

**Why after validation (not before):** A flow that fails validation will also fail at runtime, wasting time debugging runtime errors that are actually structural YAML issues.

**Why parallel with Step 5:** Testing doesn't require the README to exist, and README doesn't require test results.

**Verify:**
- Exit code 0 (job completed)
- All four poll steps show COMPLETED status
- Summarize step outputs `all_passed: true`
- No orphaned background processes (`ps aux | grep sleep | grep marker` returns nothing)

## Testing Strategy

### T1: Static validation (automated, <5s)
```bash
stepwise validate flows/test-polling/FLOW.yaml
```
**Expected:** Zero warnings, zero errors. Validates: input bindings resolve, outputs declared, no unbounded loops, poll steps have `check_command`.

### T2: Quick smoke test (automated, ~30s)
```bash
stepwise run flows/test-polling/FLOW.yaml \
  --var basic_delay_seconds=3 \
  --var longrun_delay_seconds=10 \
  --var backoff_fail_count=2
```
**Expected:** Job status = COMPLETED. All steps transition: setup → COMPLETED, poll → SUSPENDED → COMPLETED, summarize → COMPLETED.

**Verification after completion:**
```bash
stepwise list --limit 1
```

### T3: Interactive web UI test (~2 min)
```bash
stepwise run --watch flows/test-polling/FLOW.yaml \
  --var basic_delay_seconds=5 \
  --var longrun_delay_seconds=20 \
  --var backoff_fail_count=3
```
**Expected:** DAG visualization shows:
1. All four setup steps complete first (green)
2. All four poll steps enter SUSPENDED (yellow) with prompt text visible
3. Poll steps transition to COMPLETED (green) as markers appear: basic at ~5s, backoff at ~12s (4 fails × 3s interval), longrun at ~20s, restart at ~30s
4. Summarize step completes last

### T4: Server restart test (manual, ~3 min)
```bash
# Terminal 1:
stepwise server start
stepwise run flows/test-polling/FLOW.yaml \
  --var basic_delay_seconds=120 \
  --var longrun_delay_seconds=120 \
  --var backoff_fail_count=20

# Wait until poll-restart shows SUSPENDED in web UI

# Terminal 2:
stepwise server restart

# Back to web UI: observe poll steps
# Expected: poll timers do NOT resume (known gap)
# The job stays RUNNING with suspended polls that never get checked
```

### T5: Long-running test (manual, ~6 min)
```bash
stepwise run --watch flows/test-polling/FLOW.yaml
```
**Expected:** Using defaults (`longrun_delay_seconds=360`, `interval_seconds=30`), poll-longrun checks ~12 times over 6 minutes. Monitor via web UI. Other scenarios complete earlier (basic at ~5s, backoff at ~15s, restart at ~30s).

### T6: Existing poll tests unchanged (automated, <10s)
```bash
uv run pytest tests/test_poll_executor.py -v
```
**Expected:** All 7 existing tests pass. This plan adds no engine changes — only new flow files.

### T7: Backoff attempt counting verification
```bash
stepwise run flows/test-polling/FLOW.yaml \
  --var basic_delay_seconds=2 \
  --var longrun_delay_seconds=5 \
  --var backoff_fail_count=3
# Verify: poll-backoff artifact shows {"ready": true, "attempts": 4}
# (3 fails + 1 success = 4 total attempts)
```

### T8: Orphan process check
```bash
# After any test run completes:
ps aux | grep 'sleep.*marker' | grep -v grep
# Expected: no results — setup-marker.sh's trap should clean up
```

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Background `sleep` in setup-marker.sh orphans on job cancel | Leftover sleep processes consuming resources | Medium — depends on user cancelling during sleep | `trap 'kill $(jobs -p) 2>/dev/null' EXIT` in setup-marker.sh. T8 test case verifies cleanup |
| Marker files accumulate across runs | Workspace directory grows | Low — workspace is per-job | Markers are created under job workspace path (`jobs/<id>/workspace/markers/`), which is job-scoped. No cross-run pollution |
| `check_command` inline bash portability | Fails on non-bash shells | Low — `shell=True` uses `/bin/sh` by default | Keep inline commands POSIX-compatible (no bashisms): `[ -f ]`, `cat`, `printf`, `$((...))` |
| Long-running test (6 min) blocks development | Developer frustration, CI timeout | Medium | All delays configurable via `--var`. README prominently documents quick-mode: `--var longrun_delay_seconds=10`. Default is only used for explicit reliability testing |
| Server restart test exposes engine gap | Test documents a bug, not a feature | Certain — verified in assumption #3 | Document as known issue in README with specific code references. The test serves dual purpose: regression test for the eventual fix + documentation of current behavior |
| `PollExecutor` doesn't pass input vars as env to check_command | check_command can't access `$marker_path` at runtime | None — mitigated by design | `$var` placeholders in `check_command` are resolved at start time by `Template.safe_substitute()` (`executors.py:333`). The engine runs the pre-resolved literal command. No runtime env var access needed |
| State file race condition in backoff check_command | Corrupted attempt count | Very low — poll checks are serialized | Engine processes one `poll_check` event per run at a time (`engine.py:2526-2539`). No concurrent execution of the same check_command |
