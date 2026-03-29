# Plan: Per-Executor-Type Concurrent Step Limits (v3)

## Overview

Add a `max_concurrent_by_executor` settings map that caps how many steps of a given executor type can run simultaneously across all jobs. Gate at the dispatch level (before `RUNNING` status) rather than via semaphores in `_run_executor`, so throttled steps never enter the `RUNNING` lifecycle. Generalizes the existing `max_concurrent_agents` semaphore into a cleaner, per-type capacity check.

---

## Critique Response Summary

| # | Critique | Resolution |
|---|----------|------------|
| 1 | Semaphore dict replacement is not atomic | **Eliminated.** Dispatch-level gating uses a plain `dict[str, int]` for limits — safe to replace atomically. No semaphore objects. |
| 2 | Wrong boundary — step is RUNNING before semaphore | **Redesigned.** Gated in `_dispatch_ready()` at `engine.py:3635` *before* `_launch()`. A throttled step has no `StepRun`, never enters RUNNING. |
| 3 | Can't distinguish absent vs default `max_concurrent_agents` | **Resolved.** `resolved_executor_limits()` always seeds `agent` from `max_concurrent_agents`, then overlays `max_concurrent_by_executor`. No presence tracking needed. See Step 1. |
| 4 | `runner_bg.py` doesn't pass config to AsyncEngine | **Fixed.** Add `config=config` at `runner_bg.py:27`. Pre-existing bug. |
| 5 | Dynamic reload overpromises | **Narrowed.** Reload fires on API mutations only (same trigger as `_reload_engine_config` at `server.py:250`). Manual file edits require `POST /api/config/reload`. |
| 6 | Web visibility — no broadcast for throttle state | **Addressed.** Throttle info computed on-the-fly in `resolved_flow_status()` at `engine.py:867`. Cross-job dispatch triggers broadcasts for any job whose step actually launches. |
| 7 | Restart recovery of throttled runs | **Eliminated.** Throttled steps never enter RUNNING — no runs for `reattach_surviving_runs()` at `engine.py:3462` to choke on. |
| 8 | Validation missing | **Added.** Validation at `from_dict()` (`config.py:166`), CLI (`cli.py:1468`), and API (Pydantic model in `server.py`). |
| 9 | API/UI shape undefined | **Defined.** `concurrency_limits` added to `/api/config` response at `server.py:1678`. `throttle_info` added to `resolved_flow_status()` step entries at `engine.py:867`. TS types updated at `types.ts:12` and `api.ts:479`. |
| 10 | Status display hand-wavy | **Concrete.** Canonical representation in `resolved_flow_status()` at `engine.py:867-876`. CLI table uses `io.step_status()` at `cli.py:2458`. Actual counts from `_task_exec_types` dict. |
| 11 | Test plan misses real failure modes | **Expanded.** 21 test cases covering: cross-job dispatch, restart recovery, config reload, malformed values, config precedence, API shape, runner_bg fix, `_check_job_terminal` safety. |
| 12 | Step 8 is fake work | **Deleted.** `save_project_local_config` at `config.py:450` already accepts `**kwargs`. |

---

## Requirements

### R1: Settings field `max_concurrent_by_executor`
**Config**: A `dict[str, int]` on `StepwiseConfig` (at `config.py:104`) mapping executor type names to max concurrent step count. Unset types are unlimited. Merges across config levels via dict-merge (local > project > user).

**Acceptance criteria**:
- Setting `max_concurrent_by_executor: { agent: 1, llm: 3 }` in any config level is parsed correctly
- Omitted executor types have no limit
- The field round-trips through `to_dict()` / `from_dict()` at `config.py:137,166`
- Invalid values (negative, non-integer, non-dict) are rejected at parse time
- Backward compatibility: `max_concurrent_agents` (default 3, `config.py:117`) always seeds the agent limit; `max_concurrent_by_executor.agent` overrides it when present

### R2: Engine enforcement via dispatch-level gating
**Behavior**: In `_dispatch_ready()` at `engine.py:3627`, before calling `_launch()` at line 3636, check whether the step's executor type is at capacity. If at capacity, skip the step — it stays un-launched (no `StepRun`, no RUNNING status). When a slot opens, `_after_step_change()` at `engine.py:3875` re-evaluates all running jobs.

**Acceptance criteria**:
- With `agent: 1`, only one agent step is RUNNING at a time across multiple jobs
- With `llm: 3`, at most 3 LLM steps are RUNNING concurrently
- Non-limited executor types bypass gating entirely
- Throttled steps never enter RUNNING — they have no StepRun until launched
- `_check_job_terminal()` at `engine.py:3891` does not prematurely settle jobs with throttled-but-ready steps (because `_find_ready()` at `engine.py:1100` returns them)
- When a slot opens in job A, throttled steps in job B are dispatched
- Agent stagger delay (`_agent_stagger_seconds`, `engine.py:3170`) continues to work inside `_run_executor`

### R3: Visibility — throttled step indication
**Behavior**: The API computes throttle state on-the-fly: a step is "throttled" when it has no runs, its dependencies are met, and its executor type is at capacity. Surfaced in `resolved_flow_status()` at `engine.py:797`, CLI `cmd_status()` at `cli.py:2398`, and the web UI.

**Acceptance criteria**:
- `resolved_flow_status()` returns `"status": "throttled"` with `throttle_info: {executor_type, running, limit}` for affected steps (currently returns `"pending"` at `engine.py:868`)
- `stepwise status <job>` in JSON mode (server-delegated via `cli.py:2400-2404`) shows throttle info
- Web UI `StepNode` (at `StepNode.tsx:288-293`) and `StepDetailPanel` (at `StepDetailPanel.tsx:259`) show throttled indicator
- Local CLI table mode (`cli.py:2447-2460`) shows "waiting" for throttled steps (acceptable — throttle computation requires engine state)

### R4: CLI configuration
**Acceptance criteria**:
- `stepwise config set max_concurrent_by_executor.agent 1` — new case in `cmd_config()` at `cli.py:1468`, writes via `save_project_local_config()` at `config.py:450`
- `stepwise config set max_concurrent_by_executor.agent 0` — removes the key (0 = unlimited)
- `stepwise config get max_concurrent_by_executor` — new case in `cmd_config()` at `cli.py:1516`
- Non-integer and negative values rejected with error message

### R5: Dynamic reload (scoped)
**Acceptance criteria**:
- `_reload_engine_config()` at `server.py:250` updates `_executor_limits` on the engine
- In-flight RUNNING steps are unaffected
- No file watcher; reload via API mutations or new `POST /api/config/reload`

---

## Assumptions (verified against code)

| # | Assumption | Evidence |
|---|-----------|----------|
| 1 | `_prepare_step_run()` creates `StepRun` as RUNNING and emits STEP_STARTED *before* `_run_executor()` | `engine.py:1702` (status=RUNNING), `engine.py:1708` (STEP_STARTED emit) |
| 2 | `_find_ready()` returns steps with no active run and met deps | `engine.py:1100-1106` |
| 3 | `_check_job_terminal()` checks `not self._find_ready(job)` before settling | `engine.py:3908` — throttled-but-ready steps prevent premature settlement |
| 4 | `_poll_external_changes()` flags RUNNING runs missing from `_tasks` as stuck after 60s | `engine.py:3215-3231` — throttled steps have no run, so unaffected |
| 5 | `reattach_surviving_runs()` errors on RUNNING runs without pid/executor_state | `engine.py:3483-3485` — throttled steps have no run, so unaffected |
| 6 | `runner_bg.py` constructs AsyncEngine without `config` | `runner_bg.py:27` — `config` loaded at line 22 but not passed |
| 7 | All other AsyncEngine sites pass `config` | `runner.py:560,991,1866` and `server.py:768` — all pass `config=config` |
| 8 | `_reload_engine_config()` is called from API endpoint handlers only | `server.py:250` definition; called after label/model/key mutations (e.g., `server.py:1835,1854,1874,1893`) |
| 9 | `/api/config` response has no concurrency fields | `server.py:1683-1692` — returns `has_api_key`, `model_registry`, `labels`, `billing_mode` only |
| 10 | `resolved_flow_status()` returns `"pending"` for steps with no run | `engine.py:868` |
| 11 | `save_project_local_config()` accepts arbitrary `**kwargs` | `config.py:450-458` — `data.update(kwargs)` |
| 12 | `load_config()` merges `max_concurrent_agents` via "first non-default wins" (can't distinguish 3 from absent) | `config.py:341-344` |
| 13 | `STEP_STATUS_COLORS` record at `status-colors.ts:51-97` is typed `Record<StepRunStatus, ...>` — adding "throttled" to `StepRunStatus` requires a matching entry | `status-colors.ts:51-53` |
| 14 | `StepNode.tsx` uses `latestRun?.status ?? "pending"` to determine step display | `StepNode.tsx:288` |
| 15 | `ConfigResponse` interface at `api.ts:479-488` has no concurrency fields | Verified |

---

## Out of Scope

- Per-flow or per-step overrides (global engine-level setting only)
- File-watching for config reload (API-triggered only)
- Deprecation/removal of `max_concurrent_agents` (backward compat maintained)
- Cross-server / distributed coordination (one engine per server)
- Web settings UI panel for editing limits (API endpoint provided; UI panel is separate)

---

## Architecture

### Why dispatch-level gating, not semaphores

| Concern | Semaphore in `_run_executor` (v1) | Dispatch-level gate in `_dispatch_ready` (v2) |
|---------|-----------------------------------|-----------------------------------------------|
| Step status while waiting | RUNNING at `engine.py:1702` (misleading) | No run exists (accurate) |
| Duration accounting | Inflated — `started_at` set at `engine.py:1705` before semaphore | Correct — `started_at` set at actual launch |
| Stuck-run detection (`engine.py:3215`) | False positive after 60s (RUNNING but no executor started) | Not applicable (no RUNNING run) |
| Restart recovery (`engine.py:3483`) | RUNNING-without-PID → error | No run to recover; re-discovered by `_dispatch_ready` |
| Reload safety | Old `Semaphore` objects leaked to waiters | Plain `dict[str,int]` — atomic reference swap |
| STEP_STARTED signal integrity | Emitted at `engine.py:1708` before execution | Emitted at actual launch |

### Data flow

```
config.yaml                    StepwiseConfig                    AsyncEngine
┌──────────────────┐    ┌──────────────────────────┐    ┌───────────────────────────┐
│ max_concurrent_   │───>│ max_concurrent_by_executor│───>│ _executor_limits:         │
│ by_executor:      │    │ (config.py:118)           │    │   {"agent": 1, "llm": 3}  │
│   agent: 1        │    │                          │    │   (engine.py:3163)        │
│   llm: 3          │    │ resolved_executor_limits()│    │                           │
└──────────────────┘    │ (config.py:~120)          │    │ _task_exec_types:         │
                        │                          │    │   {run_id: "agent", ...}  │
                        │ max_concurrent_agents: 3  │    │   (engine.py:3164)        │
                        │ (config.py:117, legacy)   │    │                           │
                        └──────────────────────────┘    │ _dispatch_ready():        │
                                                        │   (engine.py:3627)        │
                                                        │   if at_capacity: skip    │
                                                        │   else: _launch()         │
                                                        └───────────────────────────┘
```

### Dispatch flow with gating

```
_dispatch_ready(job_id)         @ engine.py:3627
  ready = _find_ready(job)      @ engine.py:3632  (existing — deps met, no active run)
  for step_name in ready:       @ engine.py:3635
    exec_type = step.executor.type
    if _executor_at_capacity(exec_type):   ← NEW CHECK
      log throttle, continue               ← skip launch
    _launch(job, step_name)     @ engine.py:3636  (existing)

_launch(job, step_name)         @ engine.py:3642
  ...
  self._tasks[run.id] = task    @ engine.py:3692  (existing)
  self._task_exec_types[run.id] = exec_type  ← NEW TRACKING

_handle_queue_event(event)      @ engine.py:3810
  "step_result":
    self._tasks.pop(run_id)     @ engine.py:3816  (existing)
    self._task_exec_types.pop(run_id)  ← NEW CLEANUP
  "step_error":
    self._tasks.pop(run_id)     @ engine.py:3841  (existing)
    self._task_exec_types.pop(run_id)  ← NEW CLEANUP

_after_step_change(job_id)      @ engine.py:3875
  _broadcast(job_changed)       @ engine.py:3877  (existing)
  _dispatch_ready(job_id)       @ engine.py:3888  (existing)
  _dispatch_cross_job()         ← NEW (re-evaluate other running jobs)
  _check_job_terminal(job_id)   @ engine.py:3889  (existing)
```

### Running count tracking

In-memory `_task_exec_types: dict[str, str]` maps `run_id → executor_type`. Parallels `_tasks` dict (`engine.py:3152`).

- **Add** in `_launch()` after `self._tasks[run.id] = task` at `engine.py:3692`
- **Remove** in `_handle_queue_event()` after `self._tasks.pop(run_id)` at `engine.py:3816` and `engine.py:3841`
- **Clear** in `shutdown()` at `engine.py:3172`

Count: `sum(1 for t in self._task_exec_types.values() if t == exec_type)`

### Backward compatibility resolution

`resolved_executor_limits()` on `StepwiseConfig`:

```python
def resolved_executor_limits(self) -> dict[str, int]:
    limits: dict[str, int] = {}
    if self.max_concurrent_agents > 0:       # always seeds agent (even default 3)
        limits["agent"] = self.max_concurrent_agents
    limits.update(self.max_concurrent_by_executor)  # new field overlays
    return {k: v for k, v in limits.items() if v > 0}  # 0 = unlimited = remove
```

| Config state | Result |
|---|---|
| Neither set (defaults) | `{"agent": 3}` — preserves current behavior |
| `max_concurrent_agents: 5` only | `{"agent": 5}` — legacy works |
| `max_concurrent_by_executor: {agent: 1, llm: 3}` only | `{"agent": 1, "llm": 3}` — new field controls agent |
| Both: `agents: 5` + `by_executor: {llm: 3}` | `{"agent": 5, "llm": 3}` — additive |
| Both conflict: `agents: 5` + `by_executor: {agent: 1}` | `{"agent": 1}` — new wins |
| Zero override: `agents: 3` + `by_executor: {agent: 0}` | `{}` — explicit unlimited |

### `_check_job_terminal` safety proof

At `engine.py:3903-3908`, a job is settled as failed when:
```python
if (not self.store.running_runs(job.id) and       # no RUNNING runs
        not self.store.suspended_runs(job.id) and  # no SUSPENDED runs
        not self.store.delegated_runs(job.id) and  # no DELEGATED runs
        not self._find_ready(job)):                # no ready-to-launch steps
```

A throttled step has: no runs (so `running_runs` is empty) BUT `_find_ready(job)` returns it (deps met, no active run). The final `not self._find_ready(job)` is **False**, so the settlement block is **not entered**. The job stays RUNNING.

When the throttled step eventually launches and completes, `_check_job_terminal` runs again and can now settle normally.

---

## Implementation Steps

### Step 1: Config field and validation

**Files**: `src/stepwise/config.py`

**1a. Add field** — insert after `max_concurrent_agents` at line 117:
```python
# config.py:118 (new)
max_concurrent_by_executor: dict[str, int] = field(default_factory=dict)
```

**1b. Add `resolved_executor_limits()` method** — insert after `get_model_entry()` ending at line 135:
```python
# config.py:~136 (new method on StepwiseConfig)
def resolved_executor_limits(self) -> dict[str, int]:
    """Effective per-executor-type concurrency limits.
    max_concurrent_agents seeds the agent limit; max_concurrent_by_executor overlays."""
    limits: dict[str, int] = {}
    if self.max_concurrent_agents > 0:
        limits["agent"] = self.max_concurrent_agents
    limits.update(self.max_concurrent_by_executor)
    return {k: v for k, v in limits.items() if v > 0}
```

**1c. Update `to_dict()`** — insert after `max_concurrent_agents` serialization at line 160:
```python
# config.py:~161 (new line in to_dict)
if self.max_concurrent_by_executor:
    d["max_concurrent_by_executor"] = self.max_concurrent_by_executor
```

**1d. Update `from_dict()`** — insert validation before the constructor call at line 178, and add kwarg at line ~190:
```python
# config.py:~177 (new validation block in from_dict)
raw_limits = d.get("max_concurrent_by_executor", {})
if not isinstance(raw_limits, dict):
    raw_limits = {}
validated_limits = {}
for k, v in raw_limits.items():
    if isinstance(v, int) and v >= 0:
        validated_limits[str(k)] = v
    # silently drop invalid entries (non-int, negative)

# config.py:~190 (add to StepwiseConfig constructor call)
max_concurrent_by_executor=validated_limits,
```

**1e. Update `load_config()` merge** — insert after labels merge at line 309, before the `StepwiseConfig()` constructor at line 321:
```python
# config.py:~310 (new merge block)
executor_limits: dict[str, int] = {}
executor_limits.update(user.max_concurrent_by_executor)
executor_limits.update(project.max_concurrent_by_executor)
executor_limits.update(local.max_concurrent_by_executor)

# config.py:~321 (add to StepwiseConfig constructor)
max_concurrent_by_executor=executor_limits,
```

---

### Step 2: Fix `runner_bg.py` config passthrough

**File**: `src/stepwise/runner_bg.py`

**Change at line 27** — replace:
```python
# runner_bg.py:27 (current)
engine = AsyncEngine(store, registry, jobs_dir=args.jobs_dir, project_dir=project_dir)
```
with:
```python
# runner_bg.py:27 (new)
engine = AsyncEngine(
    store, registry, jobs_dir=args.jobs_dir, project_dir=project_dir,
    billing_mode=config.billing, config=config,
    max_concurrent_jobs=config.max_concurrent_jobs,
)
```

This is a standalone bug fix (background CLI runs currently ignore all engine config).

---

### Step 3a: Engine — add state and helpers to `AsyncEngine.__init__`

**File**: `src/stepwise/engine.py`

**Replace lines 3163-3167** (the `_agent_semaphore` setup):
```python
# engine.py:3163-3167 (current — REMOVE)
# Agent concurrency: semaphore + stagger delay
_max_agents = 3
if config and hasattr(config, "max_concurrent_agents"):
    _max_agents = config.max_concurrent_agents
self._agent_semaphore = asyncio.Semaphore(_max_agents)
```
with:
```python
# engine.py:3163 (new — dispatch-level gating)
self._executor_limits: dict[str, int] = {}
self._task_exec_types: dict[str, str] = {}  # run_id → executor type name
if config and hasattr(config, "resolved_executor_limits"):
    self._executor_limits = config.resolved_executor_limits()
```

Keep lines 3168-3170 (stagger state) unchanged.

**Add two helper methods** — insert after `_apply_agent_stagger()` ending at line 3702:
```python
# engine.py:~3703 (new)
def _running_count_for_type(self, exec_type: str) -> int:
    """Count in-flight executor tasks of a given type (across all jobs)."""
    return sum(1 for t in self._task_exec_types.values() if t == exec_type)

def _executor_at_capacity(self, exec_type: str) -> bool:
    """Check if an executor type has hit its concurrency limit."""
    limit = self._executor_limits.get(exec_type, 0)
    return limit > 0 and self._running_count_for_type(exec_type) >= limit
```

**Update `shutdown()`** — add at end of method body at line 3178:
```python
# engine.py:3178 (append)
self._task_exec_types.clear()
```

---

### Step 3b: Engine — add throttle gate in `_dispatch_ready`

**File**: `src/stepwise/engine.py`

**Replace `_dispatch_ready()` body at lines 3627-3640**:
```python
# engine.py:3627-3640 (replace entire method body)
def _dispatch_ready(self, job_id: str) -> None:
    """Find and launch all ready steps for a job (respecting executor limits)."""
    job = self.store.load_job(job_id)
    if job.status != JobStatus.RUNNING:
        return
    ready = self._find_ready(job)
    if ready:
        _async_logger.info(f"Dispatching {len(ready)} ready step(s) for job {job_id}: {ready}")
    for step_name in ready:
        step_def = job.workflow.steps[step_name]
        exec_type = step_def.executor.type
        if self._executor_at_capacity(exec_type):
            _async_logger.debug(
                "Step %s throttled: %s at capacity (%d/%d)",
                step_name, exec_type,
                self._running_count_for_type(exec_type),
                self._executor_limits.get(exec_type, 0),
            )
            continue
        self._launch(job, step_name)
        # Reload — _launch may change job status (for_each, route, sub_flow)
        job = self.store.load_job(job_id)
        if job.status != JobStatus.RUNNING:
            return
```

---

### Step 3c: Engine — track executor type in `_launch` and clean up in `_handle_queue_event`

**File**: `src/stepwise/engine.py`

**In `_launch()`** — insert after `self._tasks[run.id] = task` at line 3692:
```python
# engine.py:3693 (new)
self._task_exec_types[run.id] = step_def.executor.type
```

**In `_handle_queue_event()`** — insert after `self._tasks.pop(run_id, None)` at line 3816 (step_result):
```python
# engine.py:3817 (new)
self._task_exec_types.pop(run_id, None)
```

**And** after `self._tasks.pop(run_id, None)` at line 3841 (step_error):
```python
# engine.py:3842 (new)
self._task_exec_types.pop(run_id, None)
```

---

### Step 3d: Engine — add cross-job dispatch in `_after_step_change`

**File**: `src/stepwise/engine.py`

**Insert between `_dispatch_ready(job_id)` at line 3888 and `_check_job_terminal(job_id)` at line 3889**:
```python
# engine.py:3889 (new — between existing dispatch and terminal check)
# When executor limits are active, a slot opening in this job may
# unblock throttled steps in other jobs. Re-evaluate all running jobs.
if self._executor_limits:
    for other_job in self.store.active_jobs():
        if other_job.id != job_id:
            self._dispatch_ready(other_job.id)
```

---

### Step 3e: Engine — remove semaphore from `_run_executor`

**File**: `src/stepwise/engine.py`

**At line 3717-3719** — remove semaphore acquire:
```python
# engine.py:3717-3719 (REMOVE these three lines)
is_agent = exec_ref.type == "agent"
if is_agent:
    await self._agent_semaphore.acquire()
```

**At line 3721-3722** — keep stagger but remove `is_agent` guard:
```python
# engine.py:3721-3722 (CHANGE from)
if is_agent:
    await self._apply_agent_stagger()
# (CHANGE to)
if exec_ref.type == "agent":
    await self._apply_agent_stagger()
```

**At lines 3775-3777** — remove semaphore release:
```python
# engine.py:3775-3777 (REMOVE these lines from finally block)
finally:
    if is_agent:
        self._agent_semaphore.release()
```

The `try/except/finally` structure now just wraps the executor logic and queue push — the `finally` block is removed entirely (or kept empty if there are other cleanup tasks; here the CancelledError return and the queue.put are the only paths, already handled).

---

### Step 3f: Engine — add throttle info to `resolved_flow_status`

**File**: `src/stepwise/engine.py`

**Replace the `else` block at lines 867-876** (pending step display in `resolved_flow_status`):
```python
# engine.py:867-876 (REPLACE)
else:
    step_info["status"] = "pending"
    # Check if throttled: step would be ready but executor is at capacity
    if (hasattr(self, '_executor_limits') and self._executor_limits
            and self._is_step_ready(job, step_name, step_def)):
        exec_type = step_def.executor.type
        limit = self._executor_limits.get(exec_type, 0)
        if limit > 0:
            running = self._running_count_for_type(exec_type)
            if running >= limit:
                step_info["status"] = "throttled"
                step_info["throttle_info"] = {
                    "executor_type": exec_type,
                    "running": running,
                    "limit": limit,
                }
    # Show dependencies for pending/throttled steps
    deps = []
    for binding in step_def.inputs:
        if binding.source_step and binding.source_step != "$job":
            if binding.source_step not in deps:
                deps.append(binding.source_step)
    if deps:
        step_info["depends_on"] = deps
```

Note: `resolved_flow_status` is on the base `Engine` class (line 797). The `_executor_limits` and `_running_count_for_type` attributes exist only on `AsyncEngine`. The `hasattr` guard ensures this works for both classes (base `Engine` returns "pending"; `AsyncEngine` returns "throttled" when applicable).

---

### Step 4a: Server — update `_reload_engine_config`

**File**: `src/stepwise/server.py`

**Replace lines 262-263** (semaphore rebuild):
```python
# server.py:262-263 (REPLACE)
if hasattr(_engine, "_agent_semaphore"):
    _engine._agent_semaphore = asyncio.Semaphore(cfg.max_concurrent_agents)
# (WITH)
if hasattr(_engine, "_executor_limits"):
    _engine._executor_limits = cfg.resolved_executor_limits()
```

---

### Step 4b: Server — add concurrency fields to `GET /api/config`

**File**: `src/stepwise/server.py`

**In `get_config()` at line 1678** — add two fields to the return dict (after `billing_mode` at line 1692):
```python
# server.py:~1692 (add to return dict)
"concurrency_limits": cfg.resolved_executor_limits(),
"concurrency_running": {
    t: sum(1 for v in _get_engine()._task_exec_types.values() if v == t)
    for t in set(_get_engine()._task_exec_types.values())
} if _engine else {},
```

---

### Step 4c: Server — add Pydantic model and `PUT /api/config/concurrency` endpoint

**File**: `src/stepwise/server.py`

**Insert new Pydantic model** after `SetApiKeyRequest` at line 1676:
```python
# server.py:~1677 (new)
class UpdateConcurrencyRequest(BaseModel):
    executor_type: str
    limit: int  # 0 = remove limit (unlimited)
```

**Insert new endpoint** after `set_default_agent` ending at line 1893:
```python
# server.py:~1894 (new endpoints)
@app.put("/api/config/concurrency")
def update_concurrency_limit(req: UpdateConcurrencyRequest):
    """Set max concurrent steps for an executor type. 0 = unlimited."""
    if req.limit < 0:
        raise HTTPException(status_code=400, detail="Limit must be non-negative (0 = unlimited)")
    cfg = load_config(_project_dir)
    limits = dict(cfg.max_concurrent_by_executor)
    if req.limit == 0:
        limits.pop(req.executor_type, None)
    else:
        limits[req.executor_type] = req.limit
    save_project_local_config(_project_dir, max_concurrent_by_executor=limits)
    new_cfg = _reload_engine_config()
    return {"status": "updated", "limits": new_cfg.resolved_executor_limits()}


@app.post("/api/config/reload")
def reload_config():
    """Reload config from disk. Use after manual YAML edits."""
    cfg = _reload_engine_config()
    return {"status": "reloaded", "limits": cfg.resolved_executor_limits()}
```

---

### Step 5a: CLI — add `config set` handler

**File**: `src/stepwise/cli.py`

**Insert new `elif` block** before the `else: Unknown config key` at line 1508:
```python
# cli.py:~1508 (insert before the "else: Unknown config key" block)
elif args.key.startswith("max_concurrent_by_executor"):
    from stepwise.config import save_project_local_config
    project_dir = _project_dir(args) or Path.cwd()
    if "." in args.key:
        _, exec_type = args.key.split(".", 1)
        try:
            limit = int(value)
        except ValueError:
            print(f"Error: value must be an integer, got '{value}'", file=sys.stderr)
            return EXIT_USAGE_ERROR
        if limit < 0:
            print("Error: limit must be non-negative (0 = unlimited)", file=sys.stderr)
            return EXIT_USAGE_ERROR
        config = load_config(project_dir)
        limits = dict(config.max_concurrent_by_executor)
        if limit == 0:
            limits.pop(exec_type, None)
        else:
            limits[exec_type] = limit
        save_project_local_config(project_dir, max_concurrent_by_executor=limits)
    else:
        import json as _json
        try:
            limits = _json.loads(value)
        except _json.JSONDecodeError:
            print("Error: value must be JSON dict, e.g. '{\"agent\": 1}'", file=sys.stderr)
            return EXIT_USAGE_ERROR
        if not isinstance(limits, dict) or not all(isinstance(v, int) and v >= 0 for v in limits.values()):
            print("Error: each value must be a non-negative integer", file=sys.stderr)
            return EXIT_USAGE_ERROR
        save_project_local_config(project_dir, max_concurrent_by_executor=limits)
    _io(args).log("success", f"Set {args.key} in project config")
    return EXIT_SUCCESS
```

---

### Step 5b: CLI — add `config get` handler

**File**: `src/stepwise/cli.py`

**Insert new `elif` block** before the `else: Unknown config key` at line 1536:
```python
# cli.py:~1536 (insert before the "else: Unknown config key" block)
elif args.key.startswith("max_concurrent_by_executor"):
    limits = config.resolved_executor_limits()
    if "." in args.key:
        _, exec_type = args.key.split(".", 1)
        print(limits.get(exec_type, "unlimited"))
    else:
        if limits:
            for t, v in sorted(limits.items()):
                print(f"  {t}: {v}")
        else:
            print("  (no limits set)")
    return EXIT_SUCCESS
```

---

### Step 5c: CLI — update `cmd_status` JSON path for throttle display

**File**: `src/stepwise/cli.py`

The JSON mode at line 2420-2426 creates a local `Engine` instance and calls `resolved_flow_status()`. Since `Engine` (base class) doesn't have `_executor_limits`, throttle info won't appear in local JSON mode. This is acceptable — the server-delegated path (line 2400-2404) uses the running server's `AsyncEngine` which does have it.

**No change needed for table mode** (lines 2447-2460) — throttled steps have no runs and correctly show "waiting" via `io.step_status(step_name, "waiting")` at line 2460.

---

### Step 6a: Web types — add throttled status

**File**: `web/src/lib/types.ts`

**At lines 12-19** — add `"throttled"` to `StepRunStatus`:
```typescript
// types.ts:12-19 (add "throttled" to union)
export type StepRunStatus =
  | "running"
  | "suspended"
  | "delegated"
  | "completed"
  | "failed"
  | "cancelled"
  | "skipped"
  | "throttled";
```

**After `StepRun` interface at line 189** — add throttle info type:
```typescript
// types.ts:~190 (new)
export interface ThrottleInfo {
  executor_type: string;
  running: number;
  limit: number;
}
```

---

### Step 6b: Web API types — add concurrency to config

**File**: `web/src/lib/api.ts`

**At lines 479-488** — extend `ConfigResponse`:
```typescript
// api.ts:479-488 (add two fields)
export interface ConfigResponse {
  has_api_key: boolean;
  has_anthropic_key: boolean;
  api_key_source: string | null;
  model_registry: ModelInfo[];
  default_model: string;
  default_agent: string;
  labels: LabelInfo[];
  billing_mode: string;
  concurrency_limits?: Record<string, number>;   // NEW
  concurrency_running?: Record<string, number>;   // NEW
}
```

---

### Step 6c: Web status colors — add throttled entry

**File**: `web/src/lib/status-colors.ts`

**At line 97** — insert before the closing `};` of `STEP_STATUS_COLORS`:
```typescript
// status-colors.ts:~96 (insert new entry)
throttled: {
  bg: "bg-orange-500/15",
  text: "text-orange-400",
  border: "border-orange-500/40",
  ring: "ring-orange-500/50",
},
```

---

### Step 6d: Web StepNode — add throttled handle color and subtitle

**File**: `web/src/components/dag/StepNode.tsx`

**At line 288** — the status type is `StepRunStatus | "pending"`. Since "throttled" is now in `StepRunStatus`, no type change needed. But `resolved_flow_status` returns "throttled" for steps with NO run. The step node gets status from `latestRun?.status ?? "pending"`. Since throttled steps have no run, `latestRun` is null and status falls through to `"pending"`.

This means we need to thread the throttle info from the API differently. The `resolved_flow_status` endpoint returns a step-level status field. The DAG view needs to use that status for steps without runs. **The cleanest approach**: pass the flow status step info through to `StepNode` and use its status when no `latestRun` exists.

**In StepNode at line 288** — accept an optional `flowStatus` prop:
```typescript
// StepNode.tsx props (add)
flowStatus?: string;  // from resolved_flow_status step.status
```

```typescript
// StepNode.tsx:288 (CHANGE)
const status: StepRunStatus | "pending" | "throttled" =
  latestRun?.status ?? (flowStatus === "throttled" ? "throttled" : "pending");
```

**At lines 344-349 and 426-431** — add throttled case to handle colors:
```typescript
// (add after the "suspended" case)
status === "throttled" ? "bg-orange-500/60 border-orange-400/60" :
```

**At line 413-420** — add throttled subtitle case (insert before the existing suspended case):
```tsx
// StepNode.tsx:~413 (insert before isSuspended check)
} : status === "throttled" ? (
  <span className="flex items-center gap-1 text-orange-400">
    <Clock className="w-2.5 h-2.5" />
    Waiting for executor slot
  </span>
)
```

---

### Step 6e: Web StepDetailPanel — add throttle reason

**File**: `web/src/components/jobs/StepDetailPanel.tsx`

**After `isSuspended` check at line 259-260** — add throttle detection:
```typescript
// StepDetailPanel.tsx:~261 (new)
// Throttle info would come from the flow status API. For now, detect via
// props or a separate query. Show when step has no run but is throttled.
```

The detail panel gets its data from `useRuns()` which returns `StepRun[]`. Throttled steps have no runs, so we need additional data. **Add a `throttleInfo` prop** passed from the parent (which has the flow status data):
```tsx
// StepDetailPanel.tsx props (add)
throttleInfo?: { executor_type: string; running: number; limit: number } | null;
```

**In the render, after the header section at line 264**:
```tsx
// StepDetailPanel.tsx:~264 (insert)
{throttleInfo && (
  <div className="mx-4 mt-2 px-3 py-2 rounded-md bg-orange-500/10 border border-orange-500/30 text-sm text-orange-400">
    Waiting for {throttleInfo.executor_type} slot ({throttleInfo.running}/{throttleInfo.limit} in use)
  </div>
)}
```

---

### Step 7: Tests

**File**: `tests/test_executor_concurrency.py` (new)

All tests use the established patterns from `tests/test_agent_concurrency.py`: `_ConcurrencyTracker` for measuring actual concurrency, `_SlowExecutor` for blocking steps, `_make_engine` factory, `run_job` async helper from `conftest.py`.

**7a. Core dispatch gating (5 tests)**:

```python
# test_executor_concurrency.py

class TestExecutorConcurrencyLimit:
    """Test per-executor-type dispatch gating."""

    @pytest.mark.asyncio
    async def test_single_type_limit_enforced(self):
        """With callable limit=2, at most 2 callable steps run at a time."""
        tracker = _ConcurrencyTracker(sleep_seconds=0.3)
        engine = _make_engine_with_limits({"callable": 2}, tracker)
        engine._agent_stagger_seconds = 0.0
        wf = _parallel_steps("callable", 5)
        job = engine.create_job(objective="test", workflow=wf)
        result = await run_job(engine, job.id, timeout=15)
        assert result.status == JobStatus.COMPLETED
        assert tracker.max_concurrent <= 2

    @pytest.mark.asyncio
    async def test_multiple_types_independent(self):
        """Each executor type has independent limits."""
        # callable limited to 1, script limited to 2
        # 3 callable + 3 script steps: callable serialized, script 2-at-a-time
        tracker_c = _ConcurrencyTracker(sleep_seconds=0.2)
        tracker_s = _ConcurrencyTracker(sleep_seconds=0.2)
        engine = _make_engine_multi({"callable": 1, "script": 2}, tracker_c, tracker_s)
        wf = _mixed_parallel_steps(3, 3)  # 3 callable, 3 script
        job = engine.create_job(objective="test", workflow=wf)
        result = await run_job(engine, job.id, timeout=15)
        assert result.status == JobStatus.COMPLETED
        assert tracker_c.max_concurrent <= 1
        assert tracker_s.max_concurrent <= 2

    @pytest.mark.asyncio
    async def test_unlimited_type_not_gated(self):
        """Executor types without a limit run without restriction."""
        tracker = _ConcurrencyTracker(sleep_seconds=0.2)
        engine = _make_engine_with_limits({"agent": 1}, tracker, register_as="callable")
        engine._agent_stagger_seconds = 0.0
        wf = _parallel_steps("callable", 5)
        job = engine.create_job(objective="test", workflow=wf)
        start = time.monotonic()
        result = await run_job(engine, job.id, timeout=10)
        elapsed = time.monotonic() - start
        assert result.status == JobStatus.COMPLETED
        # 5 × 0.2s in parallel should be ~0.2s, not ~1.0s serial
        assert elapsed < 1.5
        assert tracker.max_concurrent >= 3  # most ran in parallel

    @pytest.mark.asyncio
    async def test_throttled_step_has_no_run(self):
        """A throttled step should NOT have a StepRun (not RUNNING)."""
        tracker = _ConcurrencyTracker(sleep_seconds=1.0)  # long enough to inspect
        engine = _make_engine_with_limits({"callable": 1}, tracker)
        wf = _parallel_steps("callable", 2)  # step-0, step-1
        job = engine.create_job(objective="test", workflow=wf)
        engine_task = asyncio.create_task(engine.run())
        try:
            engine.start_job(job.id)
            await asyncio.sleep(0.2)  # let first step start
            runs = engine.store.runs_for_job(job.id)
            # Only 1 step should be RUNNING — the other has no run at all
            running = [r for r in runs if r.status == StepRunStatus.RUNNING]
            assert len(running) == 1
            assert len(runs) == 1  # second step not yet created
        finally:
            engine_task.cancel()

    @pytest.mark.asyncio
    async def test_job_not_prematurely_settled(self):
        """A job with throttled-but-ready steps must NOT settle as failed."""
        tracker = _ConcurrencyTracker(sleep_seconds=0.3)
        engine = _make_engine_with_limits({"callable": 1}, tracker)
        # Two parallel callable steps + a downstream step depending on both
        steps = {
            "a": StepDefinition(name="a", executor=ExecutorRef(type="callable", config={}), outputs=["r"]),
            "b": StepDefinition(name="b", executor=ExecutorRef(type="callable", config={}), outputs=["r"]),
            "c": StepDefinition(
                name="c",
                executor=ExecutorRef(type="callable", config={}),
                inputs=[InputBinding("ra", "a", "r"), InputBinding("rb", "b", "r")],
                outputs=["final"],
            ),
        }
        wf = WorkflowDefinition(steps=steps)
        job = engine.create_job(objective="test", workflow=wf)
        result = await run_job(engine, job.id, timeout=15)
        assert result.status == JobStatus.COMPLETED
        runs = engine.store.runs_for_job(job.id)
        assert len([r for r in runs if r.status == StepRunStatus.COMPLETED]) == 3
```

**7b. Cross-job and slot release (3 tests)**:

```python
class TestCrossJobDispatch:

    @pytest.mark.asyncio
    async def test_cross_job_slot_release(self):
        """When job A's step completes, throttled step in job B should launch."""
        tracker = _ConcurrencyTracker(sleep_seconds=0.3)
        engine = _make_engine_with_limits({"callable": 1}, tracker)
        wf = _single_step("callable")
        job_a = engine.create_job(objective="a", workflow=wf)
        job_b = engine.create_job(objective="b", workflow=wf)
        engine_task = asyncio.create_task(engine.run())
        try:
            engine.start_job(job_a.id)
            engine.start_job(job_b.id)
            await asyncio.wait_for(engine.wait_for_job(job_b.id), timeout=10)
            assert engine.store.load_job(job_a.id).status == JobStatus.COMPLETED
            assert engine.store.load_job(job_b.id).status == JobStatus.COMPLETED
        finally:
            engine_task.cancel()

    @pytest.mark.asyncio
    async def test_slot_released_on_failure(self):
        """Failed step must release slot so next step can proceed."""
        # First step fails, second should still complete
        tracker = _ConcurrencyTracker(sleep_seconds=0.1)
        engine = _make_engine_fail_first({"callable": 1}, tracker)
        wf = _parallel_steps("callable", 2)
        job = engine.create_job(objective="test", workflow=wf)
        result = await run_job(engine, job.id, timeout=10)
        # Job may fail (due to first step failing) but second step should have run
        all_runs = engine.store.runs_for_job(job.id)
        assert len(all_runs) == 2  # both steps got a run

    @pytest.mark.asyncio
    async def test_slot_released_on_cancellation(self):
        """Cancelling a job must release the slot."""
        tracker = _ConcurrencyTracker(sleep_seconds=5.0)  # long sleep
        engine = _make_engine_with_limits({"callable": 1}, tracker)
        wf_a = _single_step("callable")
        wf_b = _single_step("callable")
        job_a = engine.create_job(objective="a", workflow=wf_a)
        job_b = engine.create_job(objective="b", workflow=wf_b)
        engine_task = asyncio.create_task(engine.run())
        try:
            engine.start_job(job_a.id)
            engine.start_job(job_b.id)
            await asyncio.sleep(0.2)
            engine.cancel_job(job_a.id)
            # job_b's step should now be free to launch
            await asyncio.wait_for(engine.wait_for_job(job_b.id), timeout=10)
            assert engine.store.load_job(job_b.id).status == JobStatus.COMPLETED
        finally:
            engine_task.cancel()
```

**7c. Backward compatibility (3 tests)**:

```python
class TestBackwardCompatibility:

    def test_legacy_max_concurrent_agents(self):
        """max_concurrent_agents=2 without new field → agent limit is 2."""
        cfg = StepwiseConfig(max_concurrent_agents=2)
        assert cfg.resolved_executor_limits() == {"agent": 2}

    def test_new_field_overrides_legacy(self):
        """max_concurrent_by_executor.agent overrides max_concurrent_agents."""
        cfg = StepwiseConfig(max_concurrent_agents=5, max_concurrent_by_executor={"agent": 1})
        assert cfg.resolved_executor_limits() == {"agent": 1}

    def test_zero_removes_limit(self):
        """max_concurrent_by_executor.agent=0 removes the agent limit."""
        cfg = StepwiseConfig(max_concurrent_agents=3, max_concurrent_by_executor={"agent": 0})
        assert cfg.resolved_executor_limits() == {}

    def test_additive_types(self):
        """Legacy agent + new llm → both present."""
        cfg = StepwiseConfig(max_concurrent_agents=5, max_concurrent_by_executor={"llm": 3})
        assert cfg.resolved_executor_limits() == {"agent": 5, "llm": 3}
```

**7d. Config serialization and validation (3 tests)**:

```python
class TestConfigSerialization:

    def test_round_trip(self):
        cfg = StepwiseConfig(max_concurrent_by_executor={"agent": 1, "llm": 3})
        d = cfg.to_dict()
        assert d["max_concurrent_by_executor"] == {"agent": 1, "llm": 3}
        cfg2 = StepwiseConfig.from_dict(d)
        assert cfg2.max_concurrent_by_executor == {"agent": 1, "llm": 3}

    def test_validation_drops_bad_values(self):
        d = {"max_concurrent_by_executor": {"agent": -1, "llm": "foo", "ok": 5}}
        cfg = StepwiseConfig.from_dict(d)
        assert cfg.max_concurrent_by_executor == {"ok": 5}

    def test_merge_levels(self):
        """Local overrides project overrides user."""
        from stepwise.config import StepwiseConfig
        user = StepwiseConfig(max_concurrent_by_executor={"agent": 5})
        project = StepwiseConfig(max_concurrent_by_executor={"llm": 3})
        local = StepwiseConfig(max_concurrent_by_executor={"agent": 1})
        # Simulate merge
        merged = {}
        merged.update(user.max_concurrent_by_executor)
        merged.update(project.max_concurrent_by_executor)
        merged.update(local.max_concurrent_by_executor)
        assert merged == {"agent": 1, "llm": 3}
```

**7e. Dynamic reload (1 test)**:

```python
class TestDynamicReload:

    @pytest.mark.asyncio
    async def test_limit_change_takes_effect(self):
        """Updating _executor_limits changes dispatch behavior."""
        tracker = _ConcurrencyTracker(sleep_seconds=0.3)
        engine = _make_engine_with_limits({"callable": 1}, tracker)
        engine._agent_stagger_seconds = 0.0

        # First run: limit 1
        wf = _parallel_steps("callable", 3)
        job1 = engine.create_job(objective="run1", workflow=wf)
        result = await run_job(engine, job1.id, timeout=15)
        assert result.status == JobStatus.COMPLETED
        assert tracker.max_concurrent <= 1

        # Change limit to 3
        engine._executor_limits = {"callable": 3}
        tracker2 = _ConcurrencyTracker(sleep_seconds=0.3)
        # Re-register executor with new tracker
        engine.registry.register("callable", lambda cfg, t=tracker2: _SlowExecutor(t))

        wf2 = _parallel_steps("callable", 3)
        job2 = engine.create_job(objective="run2", workflow=wf2)
        result2 = await run_job(engine, job2.id, timeout=15)
        assert result2.status == JobStatus.COMPLETED
        assert tracker2.max_concurrent >= 2  # at least 2 ran in parallel
```

**7f. Throttle visibility in API (2 tests)**:

```python
class TestThrottleVisibility:

    @pytest.mark.asyncio
    async def test_resolved_flow_status_throttled(self):
        """resolved_flow_status shows 'throttled' for capacity-gated steps."""
        tracker = _ConcurrencyTracker(sleep_seconds=2.0)
        engine = _make_engine_with_limits({"callable": 1}, tracker)
        wf = _parallel_steps("callable", 2)
        job = engine.create_job(objective="test", workflow=wf)
        engine_task = asyncio.create_task(engine.run())
        try:
            engine.start_job(job.id)
            await asyncio.sleep(0.3)  # let first step start
            status = engine.resolved_flow_status(job.id)
            steps_by_name = {s["name"]: s for s in status["steps"]}
            statuses = {s["name"]: s["status"] for s in status["steps"]}
            assert "running" in statuses.values()
            assert "throttled" in statuses.values()
            # Find the throttled step and check throttle_info
            throttled = [s for s in status["steps"] if s["status"] == "throttled"]
            assert len(throttled) == 1
            ti = throttled[0]["throttle_info"]
            assert ti["executor_type"] == "callable"
            assert ti["limit"] == 1
            assert ti["running"] == 1
        finally:
            engine_task.cancel()

    @pytest.mark.asyncio
    async def test_pending_not_confused_with_throttled(self):
        """Steps with unmet deps show 'pending', not 'throttled'."""
        tracker = _ConcurrencyTracker(sleep_seconds=2.0)
        engine = _make_engine_with_limits({"callable": 1}, tracker)
        # a → b (b depends on a, both callable)
        steps = {
            "a": StepDefinition(name="a", executor=ExecutorRef(type="callable", config={}), outputs=["r"]),
            "b": StepDefinition(
                name="b", executor=ExecutorRef(type="callable", config={}),
                inputs=[InputBinding("r", "a", "r")], outputs=["r2"]),
        }
        wf = WorkflowDefinition(steps=steps)
        job = engine.create_job(objective="test", workflow=wf)
        engine_task = asyncio.create_task(engine.run())
        try:
            engine.start_job(job.id)
            await asyncio.sleep(0.3)
            status = engine.resolved_flow_status(job.id)
            b_status = [s for s in status["steps"] if s["name"] == "b"][0]
            # b is pending (deps not met), not throttled
            assert b_status["status"] == "pending"
        finally:
            engine_task.cancel()
```

**7g. Runner_bg fix (1 test)**:

```python
class TestRunnerBgConfig:
    """Verify runner_bg passes config to AsyncEngine."""

    def test_runner_bg_passes_config(self):
        """Smoke test: runner_bg._run constructs engine with config."""
        # This test verifies the code path, not runtime behavior.
        import ast, inspect
        from stepwise import runner_bg
        source = inspect.getsource(runner_bg._run)
        tree = ast.parse(source)
        # Find AsyncEngine(...) call and verify config= is in keywords
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "AsyncEngine":
                    kwarg_names = [kw.arg for kw in node.keywords]
                    assert "config" in kwarg_names, "runner_bg must pass config= to AsyncEngine"
                    return
        pytest.fail("AsyncEngine() call not found in runner_bg._run")
```

**File: `tests/test_agent_concurrency.py` (update)**

**7h. Update existing tests**:

- `_make_engine()` at line 138-154: `StepwiseConfig(max_concurrent_agents=N)` still works — `resolved_executor_limits()` returns `{"agent": N}`. **No change needed** to the factory.
- `test_max_concurrent_agents_enforced` at line 164: References `engine._agent_stagger_seconds = 0.0` — **keep** (stagger state is unchanged).
- `test_non_agent_steps_bypass_semaphore` at line 188: Tests that script steps bypass agent limits. Still valid — dispatch gating checks step's executor type, not a global semaphore. **No change needed**.
- `test_agent_slot_released_on_failure` at line 215: Tests slot release. With dispatch gating, slot release happens when `_task_exec_types.pop()` runs in `_handle_queue_event`. **Test logic unchanged** — it verifies the second step eventually completes.
- `TestAgentStagger` at line 287: Stagger is independent of gating mechanism. **No change needed**.
- `TestConcurrencyConfig` at line 322: Serialization tests. **Add** a test for `max_concurrent_by_executor` round-trip (covered in 7d above).

---

## Step Ordering and Dependencies

```
Step 1  (config field)           ← no deps
Step 2  (runner_bg fix)          ← no deps (standalone bug fix)
Step 3a (engine init)            ← depends on Step 1
Step 3b (dispatch gate)          ← depends on Step 3a
Step 3c (launch/event tracking)  ← depends on Step 3a
Step 3d (cross-job dispatch)     ← depends on Step 3b
Step 3e (remove semaphore)       ← depends on Step 3b (gate replaces semaphore)
Step 3f (resolved_flow_status)   ← depends on Step 3a
Step 4a (server reload)          ← depends on Step 1, 3a
Step 4b (config API fields)      ← depends on Step 1
Step 4c (concurrency API)        ← depends on Step 1, 4a
Step 5a (CLI set)                ← depends on Step 1
Step 5b (CLI get)                ← depends on Step 1
Step 5c (CLI status)             ← depends on Step 3f
Step 6a (TS types)               ← no deps
Step 6b (API types)              ← no deps
Step 6c (status colors)          ← depends on Step 6a
Step 6d (StepNode)               ← depends on Step 6a, 6c
Step 6e (StepDetailPanel)        ← depends on Step 6a
Step 7  (tests)                  ← write alongside Steps 1-3
```

Recommended commit sequence:
1. **Commit 1**: Steps 1 + 7c + 7d (config field + config tests)
2. **Commit 2**: Step 2 + 7g (runner_bg fix + test)
3. **Commit 3**: Steps 3a-3e + 7a + 7b + 7e (engine gating + core tests)
4. **Commit 4**: Step 3f + 7f (throttle visibility in API + tests)
5. **Commit 5**: Steps 4a-4c (server reload + API endpoints)
6. **Commit 6**: Steps 5a-5c (CLI commands)
7. **Commit 7**: Steps 6a-6e (web UI)

---

## Testing Strategy

### Commands

```bash
# Config and backward compat tests (after commit 1)
uv run pytest tests/test_executor_concurrency.py::TestBackwardCompatibility -v
uv run pytest tests/test_executor_concurrency.py::TestConfigSerialization -v

# Runner_bg fix (after commit 2)
uv run pytest tests/test_executor_concurrency.py::TestRunnerBgConfig -v

# Core gating tests (after commit 3)
uv run pytest tests/test_executor_concurrency.py::TestExecutorConcurrencyLimit -v
uv run pytest tests/test_executor_concurrency.py::TestCrossJobDispatch -v
uv run pytest tests/test_executor_concurrency.py::TestDynamicReload -v

# Verify existing agent concurrency tests still pass (after commit 3)
uv run pytest tests/test_agent_concurrency.py -v

# Throttle visibility (after commit 4)
uv run pytest tests/test_executor_concurrency.py::TestThrottleVisibility -v

# Full suite (before push)
uv run pytest tests/
cd web && npm run test
```

### Manual testing

```bash
stepwise config set max_concurrent_by_executor.agent 1
stepwise config get max_concurrent_by_executor   # → agent: 1

# Server mode — full visibility
stepwise server start --detach
stepwise run flows/test/FLOW.yaml --watch --name "test"
# Web UI: first agent step = running (spinner), second = throttled (orange clock)
# JSON: stepwise status <job_id> --output json | jq '.steps[] | .throttle_info'

# Cross-job throttling
stepwise run flows/test/FLOW.yaml --async --name "job-1"
stepwise run flows/test/FLOW.yaml --async --name "job-2"
# Only one agent step running at a time across both jobs

# Dynamic change via API
curl -X PUT localhost:8340/api/config/concurrency \
  -H 'Content-Type: application/json' -d '{"executor_type":"agent","limit":3}'

# Remove limit
stepwise config set max_concurrent_by_executor.agent 0
```

---

## Risks & Mitigations

### Risk 1: Cross-job dispatch overhead
**What**: `_after_step_change` iterates all active jobs when limits are configured (new loop after `engine.py:3888`).
**Impact**: With 50+ concurrent jobs, each step completion triggers 50 `_dispatch_ready` calls.
**Mitigation**: Gated by `if self._executor_limits:` — zero overhead when no limits configured. For typical use (1-10 jobs), negligible. `_dispatch_ready` short-circuits immediately if job isn't RUNNING (`engine.py:3630`). Future optimization: track which jobs have throttled steps.

### Risk 2: `_task_exec_types` drifts from `_tasks`
**What**: If a code path removes from `_tasks` without removing from `_task_exec_types`, counts would be wrong.
**Impact**: Permanent over-count → steps never unblocked.
**Mitigation**: Both dicts are updated at the same three points: add in `_launch` (`engine.py:3692-3693`), remove in `_handle_queue_event` for step_result (`engine.py:3816-3817`) and step_error (`engine.py:3841-3842`), clear in `shutdown` (`engine.py:3172-3178`). Test 7a `test_throttled_step_has_no_run` directly verifies the count is accurate.

### Risk 3: `_poll_external_changes` re-evaluates throttled steps every 5s
**What**: Every 5s timeout in the event loop (`engine.py:3192-3196`) calls `_dispatch_ready` for all active jobs, re-checking throttled steps.
**Impact**: Minor CPU waste — one dict lookup per throttled step per 5s cycle.
**Mitigation**: Already happens today (the 5s poll is existing behavior). The throttle check adds a single `_executor_at_capacity()` call (dict lookup + list comprehension) per ready step. Negligible.

### Risk 4: Agent stagger interaction
**What**: Stagger delay at `engine.py:3695-3702` runs inside `_run_executor` AFTER the step is RUNNING. During the stagger sleep, the step occupies a slot.
**Impact**: If limit=1 and stagger=2s, there's a 2s gap between agent completions where the slot appears full.
**Mitigation**: This is correct behavior — the stagger intentionally spaces out agent launches. The slot IS occupied during stagger (the executor is about to start). Users who set `agent: 1` expect serial execution with stagger delays.

### Risk 5: `resolved_flow_status` called on base Engine class
**What**: Base `Engine` class (used in CLI local JSON mode at `cli.py:2423`) doesn't have `_executor_limits`.
**Impact**: Local CLI JSON output shows "pending" instead of "throttled" for throttled steps.
**Mitigation**: The `hasattr(self, '_executor_limits')` guard in Step 3f handles this gracefully. Server-delegated path (primary use case) uses `AsyncEngine` and shows full throttle info. Documented in R3 acceptance criteria as acceptable limitation.
