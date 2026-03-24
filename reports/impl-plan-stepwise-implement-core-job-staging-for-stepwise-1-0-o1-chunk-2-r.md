---
title: "Implementation Plan: Core Job Staging for Stepwise 1.0 (O1 Chunk 2)"
date: "2026-03-24T00:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Core Job Staging for Stepwise 1.0 (O1 Chunk 2)

## Overview

Add a STAGED job status, group scoping, and inter-job ordering dependencies to enable multi-job orchestration via the CLI. Jobs are created in a STAGED holding state, organized into groups, wired with ordering deps, then released to PENDING for engine execution. The engine's existing PENDING→RUNNING pipeline gains a depends_on gate so jobs wait for their predecessors.

## Requirements

### R1: STAGED Job Status

- New `JobStatus.STAGED` enum value in `models.py`
- STAGED jobs are invisible to the engine scheduler (`_start_queued_jobs` skips them)
- STAGED jobs do not appear in `store.pending_jobs()` or `store.active_jobs()`
- **Acceptance criteria:**
  1. A STAGED job persisted to the DB is not returned by `store.pending_jobs()` (which queries `WHERE status = 'pending'`, `store.py:230`)
  2. A STAGED job is not returned by `store.active_jobs()` (which queries `WHERE status = 'running'`, `store.py:222-223`)
  3. `_start_queued_jobs()` (`engine.py:3269-3284`) iterates `store.pending_jobs()` and therefore never sees STAGED jobs — no code change needed there for exclusion
  4. `store.all_jobs(status=JobStatus.STAGED)` returns only STAGED jobs
  5. `JobStatus("staged")` round-trips correctly through the enum

### R2: GROUP Field on Jobs

- New `group` string field on `Job` dataclass (default `None`)
- New `group_name` TEXT column in SQLite `jobs` table (via migration) — column named `group_name` to avoid SQL keyword `GROUP`
- `store.all_jobs()` gains a `group` filter parameter
- **Acceptance criteria:**
  1. `Job(group="batch-1")` serializes to `{"group": "batch-1"}` in `to_dict()` and reconstructs via `from_dict()`
  2. `store.save_job()` persists the group to the `group_name` column; `store.load_job()` reads it back into `job.group`
  3. `store.all_jobs(group="batch-1")` returns only jobs where `group_name = 'batch-1'`
  4. Jobs with `group=None` are not returned by `all_jobs(group="batch-1")`
  5. `all_jobs()` without group filter returns all jobs regardless of group

### R3: DEPENDS_ON Field on Jobs

- New `depends_on` field on `Job` dataclass: `list[str]` (job IDs), default `[]`
- New `depends_on` TEXT column in SQLite (JSON-serialized list)
- **Acceptance criteria:**
  1. `Job(depends_on=["job-abc", "job-def"]).to_dict()` includes `"depends_on": ["job-abc", "job-def"]`
  2. `Job.from_dict({"depends_on": ["job-abc"]})` reconstructs correctly
  3. A job with empty `depends_on=[]` serializes as `"[]"` in SQLite and deserializes back to `[]`
  4. `save_job()` → `load_job()` round-trip preserves the list contents and order

### R4: CLI Commands (`stepwise job` subgroup)

All commands operate against local SQLite (like `cmd_jobs` at `cli.py:2242` and `cmd_cancel` at `cli.py:2382`). No server delegation in this chunk.

- **`stepwise job create --flow <path> [--input K=V]... [--group G] [--name N]`**
  - Acceptance: Creates a STAGED job, prints the job ID to stdout, exits 0. Job visible in DB with correct flow, inputs, group, name.

- **`stepwise job show [--group G]`** (no positional arg)
  - Acceptance: Prints a table of staged/pending jobs in the group. Columns: ID, NAME, STATUS, FLOW, INPUTS (truncated to 40 chars), DEPS. Exits 0.

- **`stepwise job show <job-id>`** (positional arg)
  - Acceptance: Prints single job detail: status, group, inputs (full), depends_on list, flow name. Exits 0. Exits 1 if not found.

- **`stepwise job run [--group G] [job-id]`**
  - Acceptance (single): Transitions the specified STAGED job to PENDING. Exits 0. Exits non-zero if job is not STAGED.
  - Acceptance (group): Transitions ALL STAGED jobs in the group to PENDING. Prints count. Exits 0.
  - Note: The engine's `_start_queued_jobs()` picks them up; this command does NOT call `engine.start_job()` directly. It just flips status and saves. The engine (or a subsequent `stepwise run`) handles actual execution.

- **`stepwise job cancel <job-id>`**
  - Acceptance: Sets STAGED or PENDING job to CANCELLED. Exits 0. Errors if job is already terminal.

- **`stepwise job rm <job-id>`**
  - Acceptance: Deletes a STAGED job via `store.delete_job()` (`store.py:235-240`). Exits 0. Refuses to delete non-STAGED jobs (exits non-zero with error).

- **`stepwise job dep <job-id> --after <dep-job-id>`**
  - Acceptance: Appends `dep-job-id` to `job.depends_on`, saves. Exits 0. If adding the dep would create a cycle, exits non-zero with "Error: cycle detected" message. If either job ID doesn't exist, exits non-zero.

### R5: Engine Depends-On Readiness

- `_start_queued_jobs()` (`engine.py:3269-3284`) checks `depends_on` before starting a PENDING job
- When a job completes (in `_check_job_terminal`, `engine.py:3225-3267`), check if any PENDING jobs have all deps now met
- **Acceptance criteria:**
  1. Create PENDING job B with `depends_on=[A.id]` where A is PENDING. Engine starts A but not B.
  2. When A completes, engine automatically starts B.
  3. If A fails, B stays PENDING (not auto-started — failed deps don't satisfy the gate).
  4. If B has `depends_on=[A.id, C.id]`, B starts only when BOTH A and C are COMPLETED.

### R6: Cycle Detection

- At `job dep` time, perform DFS on the job dependency graph
- Reject the dep if it would create a cycle
- **Acceptance criteria:**
  1. Linear chain A→B→C: no cycle detected at any step
  2. Adding C→A after A→B→C: cycle detected, dep rejected
  3. Self-dep A→A: cycle detected
  4. Diamond A→B, A→C, B→D, C→D: no cycle
  5. Function returns boolean (True = cycle found), does not mutate state

## Assumptions

| # | Assumption | Verification |
|---|-----------|--------------|
| A1 | STAGED is naturally invisible to the engine because `store.pending_jobs()` queries `WHERE status = 'pending'` and `store.active_jobs()` queries `WHERE status = 'running'`. No engine code change needed for exclusion. | Verified: `store.py:222-223` (`active_jobs` → `WHERE status = ?` with `JobStatus.RUNNING.value`), `store.py:229-231` (`pending_jobs` → `WHERE status = ?` with `JobStatus.PENDING.value`). `_start_queued_jobs` at `engine.py:3274` calls `store.pending_jobs()`, so STAGED is automatically excluded. |
| A2 | The migration pattern is idempotent column-add in `_migrate()`. No schema version table exists. | Verified: `store.py:112-137` uses `PRAGMA table_info(jobs)` to get column names, then `ALTER TABLE ADD COLUMN` for missing ones. Each call is idempotent. |
| A3 | `engine.create_job()` hardcodes `status=JobStatus.PENDING` at line 201. | Verified: `engine.py:196-201` constructs `Job(status=JobStatus.PENDING, ...)`. The status is not parameterized. We must add an optional `status` parameter. |
| A4 | `store.all_jobs()` uses a WHERE clause builder pattern that we can extend with a `group` filter. | Verified: `store.py:242-260` — builds `clauses` list and `params` list, joins with AND. Adding `group_name = ?` follows the identical pattern used by `status` and `meta_filters`. |
| A5 | Existing commands (`stepwise jobs` at `cli.py:4301`, `stepwise cancel` at `cli.py:4303`, `stepwise status` at `cli.py:4302`) continue unchanged. The new `stepwise job` is a separate subparser. | Verified: `cli.py:4283-4319` handler map. `"jobs"` and `"job"` are different keys. No collision. |
| A6 | `parse_inputs()` in `runner.py` handles `--input K=V` parsing including `@file` syntax. | Verified: `runner.py:44-59` — splits on first `=`, handles `@` prefix for file reads. Imported by `cmd_run` at `cli.py:1882`. |
| A7 | `store.delete_job()` cascades to step_runs and events. | Verified: `store.py:235-240` — deletes from `events`, `step_runs`, then `jobs` in that order, then commits. |
| A8 | The CLI subcommand-with-subparsers pattern (parent parser + `add_subparsers`) is established by `stepwise cache` and `stepwise server`. | Verified: `cli.py:4012-4024` (`cache` with `cache_sub = p_cache.add_subparsers(dest="cache_action")`) and `cli.py:3809-3810` (`server` with `action` choices). The `cache` pattern (sub-subparsers) is the closest match. |
| A9 | `AsyncEngine` inherits `create_job()` from `Engine`. Only one definition to modify. | Verified: `engine.py:2760` — `class AsyncEngine(Engine)`. `create_job` is defined at `engine.py:149` on `Engine` and not overridden in `AsyncEngine`. Confirmed by grep: no `def create_job` in `AsyncEngine`. |
| A10 | `_check_job_terminal()` is only defined on `AsyncEngine`, not legacy `Engine`. The dependent-job trigger only needs to be added there. | Verified: `engine.py:3225` — `_check_job_terminal` is an `AsyncEngine` method. Legacy `Engine` uses `tick()` with inline terminal checks. We focus `_check_dependent_jobs` on `AsyncEngine` only. |

## Out of Scope

- **Data wiring between jobs** (passing outputs of job A as inputs to job B) — deferred to Chunk 3 per spec
- **Server API endpoints** for job staging — this chunk is CLI-only; all commands use direct SQLite access like `cmd_jobs` (`cli.py:2257`) and `cmd_cancel` (`cli.py:2396`)
- **Web UI changes** — no frontend work; the web UI doesn't need to know about STAGED yet
- **Changes to `stepwise run`** — the existing `cmd_run` → `runner.run_flow()` path is unchanged; `stepwise job run` is a separate code path that only flips status
- **Job priority or scheduling weights** — FIFO ordering within dep constraints, matching existing `pending_jobs()` behavior (`ORDER BY created_at`, `store.py:230`)
- **Legacy `Engine` class dep-checking** — `_check_dependent_jobs` only added to `AsyncEngine`. Legacy `Engine` is tick-based and used only in tests; it gets the `create_job` status param but no dep-trigger logic
- **`recover_jobs()` enhancement** — noted as a risk; deferred to a follow-up. Current `recover_jobs()` (`engine.py:2889-2899`) only handles RUNNING jobs. PENDING jobs with deps will be picked up on the next job completion event or server restart with manual trigger.

## Architecture

### Where STAGED fits in the job lifecycle

```
STAGED ──(job run)──→ PENDING ──(_start_queued_jobs)──→ RUNNING ──→ COMPLETED/FAILED
                         │                                   │
                    depends_on gate                    _check_job_terminal
                    (in _start_queued_jobs)                   │
                                                    _check_dependent_jobs
                                                    (starts unblocked PENDING jobs)
```

**STAGED** is a pre-PENDING hold state. The CLI `stepwise job run` command flips STAGED→PENDING by directly updating the job status in SQLite and calling `store.save_job()`.

**PENDING with depends_on**: The engine's `_start_queued_jobs()` (`engine.py:3269-3284`) currently iterates `store.pending_jobs()` in FIFO order and calls `start_job()` on each (subject to `max_concurrent_jobs`). We add a guard: if `pending_job.depends_on` is non-empty, load each dep and check it's COMPLETED. Skip the job if any dep isn't COMPLETED.

**Completion cascade**: `_check_job_terminal()` (`engine.py:3225-3267`) already calls `_start_queued_jobs()` at line 3267 when a slot opens. We add a `_check_dependent_jobs(job_id)` call immediately before that, which proactively finds PENDING jobs depending on the just-completed job and attempts to start them (still subject to the `max_concurrent_jobs` gate).

### Model layer changes (`src/stepwise/models.py`)

Two new fields on `Job` dataclass (lines 1394-1460):

```python
group: str | None = None          # after notify_context field
depends_on: list[str] = field(default_factory=list)  # after group
```

This follows the existing pattern of optional fields with defaults (like `notify_url: str | None = None` at line 1411, `metadata: dict` at line 1413).

### Store layer changes (`src/stepwise/store.py`)

Migration at `store.py:123-137` — add two entries to the column list:

```python
("group_name", "TEXT", None),      # avoids SQL GROUP keyword
("depends_on", "TEXT", "'[]'"),    # JSON array of job IDs
```

Column name `group_name` maps to `Job.group` in Python. This is the same pattern used for `created_by` (line 126) and `metadata` (line 132).

`save_job()` (`store.py:141-188`) gains two more columns in the INSERT/UPDATE. `_row_to_job()` (`store.py:198-218`) gains deserialization for both.

New query method `jobs_depending_on(job_id)` uses SQLite's `json_each()` function to find PENDING jobs whose `depends_on` array contains the given ID, matching the existing `json_extract` pattern used in `all_jobs` meta filtering (line 252).

### Engine layer changes (`src/stepwise/engine.py`)

**`create_job()`** (line 149): Add optional `status: JobStatus = JobStatus.PENDING` parameter. Line 201 changes from hardcoded `status=JobStatus.PENDING` to `status=status`. All existing callers pass no status arg → backward compatible.

**`_start_queued_jobs()`** (line 3269): Add depends_on check after the `parent_job_id` skip (line 3278). For each `pending_job`, if `depends_on` is non-empty, check each dep via `store.load_job()` — if any is not `COMPLETED`, skip.

**New `_check_dependent_jobs(job_id)`**: Called from `_check_job_terminal()` after a job reaches COMPLETED status (after line 3235 and 3248). Calls `store.jobs_depending_on(job_id)` to find PENDING candidates, then for each, verifies ALL deps are COMPLETED, and calls `start_job()`.

### Cycle detection

Module-level function `check_job_dep_cycle(store, from_id, to_id) -> bool` in `engine.py`. Algorithm:

1. Load all jobs, build adjacency dict: `{job_id: [dep_ids...]}` from `depends_on` fields
2. Add proposed edge: `from_id → to_id`
3. DFS from `to_id` following `depends_on` edges. If we reach `from_id`, return True (cycle).
4. Handles self-edges (from_id == to_id → immediate True)

This is called by `cmd_job_dep` in the CLI before mutating the job.

### CLI subcommand structure (`src/stepwise/cli.py`)

New parser group modeled on the `cache` subcommand pattern (`cli.py:4012-4024`):

```python
# job
p_job = sub.add_parser("job", help="Manage staged jobs")
job_sub = p_job.add_subparsers(dest="job_action")
# ... 6 sub-subparsers: create, show, run, cancel, rm, dep
```

Dispatcher `cmd_job(args)` reads `args.job_action` and routes to the appropriate `cmd_job_*` function, mirroring how `cmd_cache` (`cli.py:4095`) reads `args.cache_action` (line 4101).

## Implementation Steps

### Step 1: Add STAGED enum value to JobStatus
**File:** `src/stepwise/models.py:29-35`
**Changes:**
- Add `STAGED = "staged"` to `JobStatus` enum, between PENDING and RUNNING
**Why first:** Every subsequent step depends on this enum value existing. No dependencies on other changes.
**Verification:** `uv run python -c "from stepwise.models import JobStatus; print(JobStatus.STAGED)"`

### Step 2: Add `group` and `depends_on` fields to Job dataclass
**File:** `src/stepwise/models.py:1394-1460`
**Changes:**
- Add `group: str | None = None` field after `notify_context` (line 1412)
- Add `depends_on: list[str] = field(default_factory=list)` field after `group`
- Update `to_dict()` (lines 1415-1437): add `"group": self.group` and `"depends_on": self.depends_on`
- Update `from_dict()` (lines 1439-1460): add `group=d.get("group")` and `depends_on=d.get("depends_on", [])`
**Why after Step 1:** Independent of STAGED but groups with STAGED logically. Needed by Steps 3 and 4.
**Verification:** `uv run python -c "from stepwise.models import Job; j = Job(id='x', objective='t', workflow=None, group='g', depends_on=['a']); print(j.to_dict()['group'], j.to_dict()['depends_on'])"`

### Step 3: SQLite migration — add `group_name` and `depends_on` columns
**File:** `src/stepwise/store.py:123-137`
**Changes:**
- Add two entries to the migration column list at line 125:
  - `("group_name", "TEXT", None)`
  - `("depends_on", "TEXT", "'[]'")`
**Why after Step 2:** Model fields must exist before store can serialize them.
**Verification:** `uv run python -c "from stepwise.store import SQLiteStore; s = SQLiteStore(':memory:'); cols = {r[1] for r in s._conn.execute('PRAGMA table_info(jobs)').fetchall()}; assert 'group_name' in cols and 'depends_on' in cols; print('OK')"`

### Step 4: Update `save_job()` to persist new fields
**File:** `src/stepwise/store.py:141-188`
**Changes:**
- Add `group_name` and `depends_on` to the INSERT column list (after `metadata`, line 146-147)
- Add corresponding `?` placeholders in VALUES
- Add `group_name = excluded.group_name` and `depends_on = excluded.depends_on` to ON CONFLICT UPDATE
- Add `job.group` and `_dumps(job.depends_on)` to the parameter tuple (after `_dumps(job.metadata)`, line 185)
**Why after Step 3:** Columns must exist before we can write to them.
**Verification:** Run the store round-trip test from Step 5.

### Step 5: Update `_row_to_job()` and add query methods
**File:** `src/stepwise/store.py:198-260`
**Changes:**
- In `_row_to_job()` (line 198-218): add `group=row["group_name"] if "group_name" in row.keys() else None` and `depends_on=json.loads(row["depends_on"]) if "depends_on" in row.keys() and row["depends_on"] else []`
- In `all_jobs()` (line 242-260): add optional `group: str | None = None` parameter. If set, append `clauses.append("group_name = ?")` and `params.append(group)`.
- Add new method `jobs_depending_on(self, job_id: str) -> list[Job]`:
  ```python
  rows = self._conn.execute(
      "SELECT * FROM jobs WHERE status = ? AND EXISTS "
      "(SELECT 1 FROM json_each(depends_on) WHERE value = ?)",
      (JobStatus.PENDING.value, job_id),
  ).fetchall()
  return [self._row_to_job(r) for r in rows]
  ```
**Why after Step 4:** Needs columns to exist and be written to.
**Verification:** `uv run pytest tests/test_job_staging.py::TestStoreRoundTrip -v` (written in Step 10)

### Step 6: Add `status` parameter to `Engine.create_job()`
**File:** `src/stepwise/engine.py:149-210`
**Changes:**
- Add `status: JobStatus = JobStatus.PENDING` parameter to `create_job()` signature (line 149-160)
- Change line 201 from `status=JobStatus.PENDING` to `status=status`
**Why after Step 2:** Requires the STAGED enum value to exist in models.
**Verification:** `uv run python -c "from stepwise.store import SQLiteStore; from stepwise.engine import Engine; from stepwise.models import JobStatus, WorkflowDefinition, StepDefinition, ExecutorRef; s = SQLiteStore(':memory:'); e = Engine(s, None); j = e.create_job('t', WorkflowDefinition(steps={'a': StepDefinition(name='a', executor=ExecutorRef(type='script', config={'command': 'echo {}'}), outputs=['x'])}), status=JobStatus.STAGED); assert j.status == JobStatus.STAGED; print('OK')"`

### Step 7: Add depends_on gate to `_start_queued_jobs()`
**File:** `src/stepwise/engine.py:3269-3284`
**Changes:**
- After the `parent_job_id` skip (line 3278-3279), add depends_on check:
  ```python
  if pending_job.depends_on:
      all_met = True
      for dep_id in pending_job.depends_on:
          try:
              dep_job = self.store.load_job(dep_id)
              if dep_job.status != JobStatus.COMPLETED:
                  all_met = False
                  break
          except KeyError:
              all_met = False
              break
      if not all_met:
          continue
  ```
**Why after Step 6:** Depends on `depends_on` field being persisted correctly.
**Verification:** `uv run pytest tests/test_job_staging.py::TestEngineDepsGate -v` (written in Step 10)

### Step 8: Add `_check_dependent_jobs()` to AsyncEngine
**File:** `src/stepwise/engine.py:3225-3267`
**Changes:**
- Add method `_check_dependent_jobs(self, job_id: str) -> None` after `_check_job_terminal`:
  ```python
  def _check_dependent_jobs(self, job_id: str) -> None:
      for candidate in self.store.jobs_depending_on(job_id):
          # Verify ALL deps are met, not just this one
          all_met = all(
              self.store.load_job(dep_id).status == JobStatus.COMPLETED
              for dep_id in candidate.depends_on
          )
          if all_met:
              try:
                  self.start_job(candidate.id)
              except ValueError:
                  pass
  ```
- Call `self._check_dependent_jobs(job.id)` in `_check_job_terminal()` after the job transitions to COMPLETED (after line 3235 and after line 3248), BEFORE `self._start_queued_jobs()` at line 3267.
**Why after Step 7:** Needs `jobs_depending_on()` store method and depends_on field.
**Verification:** `uv run pytest tests/test_job_staging.py::TestDependentJobCascade -v` (written in Step 10)

### Step 9: Add cycle detection function
**File:** `src/stepwise/engine.py` (module-level function, near top after imports)
**Changes:**
- Add `check_job_dep_cycle(store, from_id: str, to_id: str) -> bool`:
  - If `from_id == to_id`: return True
  - Load all jobs with non-empty `depends_on` from store
  - Build adjacency dict `{job_id: set(depends_on_ids)}`
  - Add proposed edge: `from_id` depends on `to_id`
  - DFS from `to_id` through the adjacency dict. If `from_id` is reachable, return True.
**Why here:** This is a pure function operating on the store. Placed in engine.py since it's used by both CLI (Step 11) and potentially engine internals.
**Verification:** `uv run pytest tests/test_job_staging.py::TestCycleDetection -v` (written in Step 10)

### Step 10: Write tests for Steps 1-9 (model, store, engine)
**File:** `tests/test_job_staging.py` (new)
**Test classes and cases:** (see Testing Strategy section for full detail)
- `TestStagedStatus` — T1 (STAGED invisible to pending/active queries)
- `TestStoreRoundTrip` — T2 (group filter), T3 (depends_on round-trip)
- `TestCycleDetection` — T4 (linear chain, transitive cycle, self-dep, diamond)
- `TestEngineDepsGate` — T5 (B waits for A), T6 (STAGED skipped by scheduler)
- `TestDependentJobCascade` — T7 (completing A auto-starts B)
**Why after Steps 1-9:** Tests validate the model/store/engine changes.
**Verification:** `uv run pytest tests/test_job_staging.py -v`

### Step 11: CLI parser — add `stepwise job` subcommand group
**File:** `src/stepwise/cli.py`, in `build_parser()` (after line 4033, before `# welcome`)
**Changes:**
- Add `p_job = sub.add_parser("job", help="Manage staged jobs")`
- Add `job_sub = p_job.add_subparsers(dest="job_action")`
- Add 6 sub-subparsers following the `cache` subparser pattern (`cli.py:4012-4024`):
  - `create`: `--flow` (required str), `--input` (action="append", dest="inputs"), `--group`, `--name`, `--objective`
  - `show`: `job_id` (nargs="?"), `--group`, `--output` (choices=["table", "json"])
  - `run`: `job_id` (nargs="?"), `--group`
  - `cancel`: `job_id` (required positional)
  - `rm`: `job_id` (required positional)
  - `dep`: `job_id` (required positional), `--after` (required str)
**Why after Step 10:** Parser registration is independent but CLI handlers (Step 12-14) need it first.
**Verification:** `uv run stepwise job --help` shows subcommands.

### Step 12: CLI handlers — `cmd_job` dispatcher + `cmd_job_create` + `cmd_job_show`
**File:** `src/stepwise/cli.py`
**Changes:**
- Add `cmd_job(args)` dispatcher function (like `cmd_cache` at line 4095). Reads `args.job_action`, routes to `cmd_job_create`, `cmd_job_show`, etc. If no action, prints help.
- Add `"job": cmd_job` to the handler map at line 4283.
- `cmd_job_create(args)`:
  - Find project via `_find_project_or_exit(args)` (pattern from `cli.py:2252`)
  - Resolve flow via `resolve_flow()` (pattern from `cli.py:1903`)
  - Parse inputs via `parse_inputs(args.inputs)` (imported from `runner.py:44`)
  - Load workflow via `load_workflow_yaml()` (pattern from `cli.py:1911`)
  - Create engine, call `engine.create_job(status=JobStatus.STAGED, ...)`
  - Set `job.group = args.group` if provided, `store.save_job(job)`
  - Print job ID to stdout
- `cmd_job_show(args)`:
  - If `args.job_id`: load single job, display detail (status, group, inputs, deps, flow name)
  - If `--group`: `store.all_jobs(group=args.group)` filtered to STAGED/PENDING
  - Else: `store.all_jobs()` filtered to STAGED/PENDING
  - Table output via `_io(args).table(...)` (pattern from `cli.py:2306`)
**Why after Step 11:** Needs the parser subcommands registered.
**Verification:** `uv run stepwise job create --flow test.flow.yaml --input x=1 --group g1`

### Step 13: CLI handlers — `cmd_job_run` + `cmd_job_cancel` + `cmd_job_rm`
**File:** `src/stepwise/cli.py`
**Changes:**
- `cmd_job_run(args)`:
  - If `args.job_id`: load job, assert `status == STAGED`, set `status = PENDING`, save
  - If `--group` (no job_id): load all `store.all_jobs(group=args.group, status=JobStatus.STAGED)`, transition each to PENDING, save, print count
  - Exit 0 on success
- `cmd_job_cancel(args)`:
  - Load job, assert status in `{STAGED, PENDING}`, set CANCELLED, save
  - Pattern follows `cmd_cancel` logic (`cli.py:2407-2413`)
- `cmd_job_rm(args)`:
  - Load job, assert STAGED, call `store.delete_job(args.job_id)` (`store.py:235`)
  - Error if not STAGED
**Why after Step 12:** Needs dispatcher in place.
**Verification:** `uv run pytest tests/test_job_staging.py::TestCLIJobRun -v` (written in Step 15)

### Step 14: CLI handler — `cmd_job_dep`
**File:** `src/stepwise/cli.py`
**Changes:**
- `cmd_job_dep(args)`:
  - Load both jobs (args.job_id and args.after) from store, error if not found
  - Call `check_job_dep_cycle(store, args.job_id, args.after)` (from engine.py)
  - If cycle detected: print error, return `EXIT_USAGE_ERROR`
  - If dep already exists in `job.depends_on`: print "already depends on", exit 0 (idempotent)
  - Append `args.after` to `job.depends_on`, save
**Why after Step 9:** Needs cycle detection function.
**Verification:** `uv run pytest tests/test_job_staging.py::TestCLIJobDep -v` (written in Step 15)

### Step 15: Write CLI integration tests
**File:** `tests/test_job_staging.py` (append to file from Step 10)
**Test classes and cases:**
- `TestCLIJobCreate` — T8a: create with flow + inputs + group, verify stdout contains job ID
- `TestCLIJobShow` — T8b: show group listing, show single job detail
- `TestCLIJobRun` — T8c: run single staged job, run group of staged jobs
- `TestCLIJobCancel` — T8d: cancel staged job, cancel pending job, reject cancel on completed
- `TestCLIJobRm` — T9: rm staged job, reject rm on non-staged
- `TestCLIJobDep` — T10: add valid dep, reject cycle, reject unknown job ID
**Pattern:** Follows `tests/test_jobs_local.py` — uses `tmp_path`, `monkeypatch.chdir()`, `init_project()`, `main([...])`, `capsys.readouterr()`
**Why last:** Tests validate the full CLI integration.
**Verification:** `uv run pytest tests/test_job_staging.py -v`

### Step 16: Full regression
**No file changes.**
**Verification:** `uv run pytest tests/ -v` — ensure no existing tests are broken.

## Testing Strategy

All tests in `tests/test_job_staging.py`. Uses fixtures from `tests/conftest.py`: `store` (in-memory SQLite, line 138-142), `async_engine` (AsyncEngine, line 190-193), `registry` (with callable executor, line 145-181), `register_step_fn` (line 40-42), `run_job_sync` (line 130-132).

### Model & Store Tests

**T1: STAGED status invisible to scheduler queries** (`TestStagedStatus`)
```python
def test_staged_invisible_to_pending_and_active(self, store):
    # Create a job directly with STAGED status
    job = Job(id="job-1", objective="test", workflow=MINIMAL_WF, status=JobStatus.STAGED)
    store.save_job(job)
    assert store.pending_jobs() == []
    assert store.active_jobs() == []
    assert len(store.all_jobs(status=JobStatus.STAGED)) == 1

def test_staged_to_pending_becomes_visible(self, store):
    job = Job(id="job-1", objective="test", workflow=MINIMAL_WF, status=JobStatus.STAGED)
    store.save_job(job)
    job.status = JobStatus.PENDING
    store.save_job(job)
    assert len(store.pending_jobs()) == 1
```

**T2: Group field filter** (`TestStoreRoundTrip`)
```python
def test_group_filter(self, store):
    store.save_job(Job(id="j1", objective="t", workflow=MINIMAL_WF, group="batch-1"))
    store.save_job(Job(id="j2", objective="t", workflow=MINIMAL_WF, group="batch-1"))
    store.save_job(Job(id="j3", objective="t", workflow=MINIMAL_WF, group="batch-2"))
    assert len(store.all_jobs(group="batch-1")) == 2
    assert len(store.all_jobs(group="batch-2")) == 1
    assert len(store.all_jobs()) == 3
```

**T3: Depends_on round-trip** (`TestStoreRoundTrip`)
```python
def test_depends_on_round_trip(self, store):
    job = Job(id="j1", objective="t", workflow=MINIMAL_WF, depends_on=["j-a", "j-b"])
    store.save_job(job)
    loaded = store.load_job("j1")
    assert loaded.depends_on == ["j-a", "j-b"]
```

### Cycle Detection Tests

**T4: Cycle detection** (`TestCycleDetection`)
```python
def test_linear_chain_no_cycle(self, store):
    # A→B→C: no cycle
    _make_job(store, "A", depends_on=[])
    _make_job(store, "B", depends_on=["A"])
    assert not check_job_dep_cycle(store, "C", "B")  # proposing C→B

def test_transitive_cycle(self, store):
    _make_job(store, "A", depends_on=[])
    _make_job(store, "B", depends_on=["A"])
    _make_job(store, "C", depends_on=["B"])
    assert check_job_dep_cycle(store, "A", "C")  # proposing A→C creates A→C→B→A

def test_self_dep(self, store):
    _make_job(store, "A", depends_on=[])
    assert check_job_dep_cycle(store, "A", "A")

def test_diamond_no_cycle(self, store):
    _make_job(store, "A", depends_on=[])
    _make_job(store, "B", depends_on=["A"])
    _make_job(store, "C", depends_on=["A"])
    _make_job(store, "D", depends_on=["B", "C"])
    assert not check_job_dep_cycle(store, "D", "C")  # already exists, no cycle
```

### Engine Tests

**T5: Depends_on gate prevents premature start** (`TestEngineDepsGate`)
```python
def test_job_b_waits_for_job_a(self, async_engine):
    register_step_fn("pass", lambda inputs: {"result": "done"})
    wf = WorkflowDefinition(steps={"s": StepDefinition(
        name="s", executor=ExecutorRef(type="callable", config={"fn_name": "pass"}), outputs=["result"]
    )})
    job_a = async_engine.create_job("a", wf)
    job_b = async_engine.create_job("b", wf)
    job_b.depends_on = [job_a.id]
    async_engine.store.save_job(job_b)
    # Run both — B should wait
    result_a = run_job_sync(async_engine, job_a.id)
    assert result_a.status == JobStatus.COMPLETED
    # B should now be startable
    result_b = run_job_sync(async_engine, job_b.id)
    assert result_b.status == JobStatus.COMPLETED
```

**T6: STAGED job skipped by scheduler** (`TestEngineDepsGate`)
```python
def test_staged_not_started_by_scheduler(self, async_engine):
    register_step_fn("pass", lambda inputs: {"result": "done"})
    wf = ...  # minimal workflow
    job = async_engine.create_job("t", wf, status=JobStatus.STAGED)
    async_engine._start_queued_jobs()
    reloaded = async_engine.store.load_job(job.id)
    assert reloaded.status == JobStatus.STAGED
```

**T7: Completion triggers dependent job** (`TestDependentJobCascade`)
```python
def test_completing_a_starts_b(self, async_engine):
    register_step_fn("pass", lambda inputs: {"result": "done"})
    wf = ...  # minimal workflow
    job_a = async_engine.create_job("a", wf)
    job_b = async_engine.create_job("b", wf)
    job_b.depends_on = [job_a.id]
    async_engine.store.save_job(job_b)
    # Run engine — start A, when A completes, B should auto-start via _check_dependent_jobs
    # Use asyncio.run with engine.run() task + start both jobs
    result_a = run_job_sync(async_engine, job_a.id)
    assert result_a.status == JobStatus.COMPLETED
    # B should have been auto-started
    result_b = async_engine.store.load_job(job_b.id)
    # May need to explicitly start B if _check_dependent_jobs only runs inside engine.run()
    ...
```

### CLI Integration Tests

**T8: Full lifecycle** (`TestCLIJobCreate`, `TestCLIJobShow`, `TestCLIJobRun`)

Pattern follows `tests/test_jobs_local.py:43-49` (init project, write flow, run via CLI `main()`):

```python
def test_create_and_show(self, tmp_path, capsys, monkeypatch):
    project = init_project(tmp_path)
    flow_file = tmp_path / "test.flow.yaml"
    flow_file.write_text(SIMPLE_FLOW)
    monkeypatch.chdir(tmp_path)

    rc = main(["job", "create", "--flow", str(flow_file), "--group", "g1"])
    assert rc == EXIT_SUCCESS
    job_id = capsys.readouterr().out.strip()
    assert job_id.startswith("job-")

    rc = main(["job", "show", "--group", "g1"])
    assert rc == EXIT_SUCCESS
    assert job_id in capsys.readouterr().out + capsys.readouterr().err
```

**T9: rm on staged job** (`TestCLIJobRm`)
```python
def test_rm_staged_succeeds(self, tmp_path, capsys, monkeypatch):
    # create staged job, rm it, verify deleted
    ...
    rc = main(["job", "rm", job_id])
    assert rc == EXIT_SUCCESS
    store = SQLiteStore(str(project.db_path))
    with pytest.raises(KeyError):
        store.load_job(job_id)
```

**T10: dep cycle rejection** (`TestCLIJobDep`)
```python
def test_cycle_rejected(self, tmp_path, capsys, monkeypatch):
    # create two staged jobs, add A→B, then B→A
    ...
    rc = main(["job", "dep", job_b_id, "--after", job_a_id])
    assert rc == EXIT_SUCCESS
    rc = main(["job", "dep", job_a_id, "--after", job_b_id])
    assert rc != EXIT_SUCCESS
    assert "cycle" in capsys.readouterr().err.lower()
```

### Run Commands

```bash
# Run only staging tests
uv run pytest tests/test_job_staging.py -v

# Run a specific test class
uv run pytest tests/test_job_staging.py::TestStagedStatus -v
uv run pytest tests/test_job_staging.py::TestCycleDetection -v
uv run pytest tests/test_job_staging.py::TestEngineDepsGate -v
uv run pytest tests/test_job_staging.py::TestCLIJobCreate -v

# Full regression (must pass before merge)
uv run pytest tests/ -v
```

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `_start_queued_jobs` performance: iterates all PENDING jobs and loads deps for each | Slow job starts with many pending jobs | Dep check is O(D) per job where D = `len(depends_on)`. For foreseeable workloads (<100 pending jobs), this is negligible. The existing `_start_queued_jobs` already loads all pending jobs (`store.py:227-232`). Add SQLite index on `(status, group_name)` later if needed. |
| SQL keyword collision with `GROUP` | Migration fails or produces incorrect queries | Use `group_name` as the SQLite column name, mapped to `Job.group` in Python. Verified: SQLite's `ALTER TABLE ADD COLUMN group_name TEXT` has no keyword issue. |
| `stepwise jobs` shows STAGED jobs to users not using the new feature | Confusion for existing users | STAGED appears as a clearly distinct status label in the table. Users can filter with `--status pending` or `--status running` to exclude. No behavior change for existing workflows. |
| `job run --group` transitions many jobs to PENDING at once | Concurrency spike in engine | Engine's `max_concurrent_jobs` gate (`engine.py:2788`, default 10) in `_start_queued_jobs` (`engine.py:3272-3273`) naturally throttles. Excess jobs stay PENDING in queue. |
| Server restart: PENDING jobs with unmet deps stay stuck | Jobs never start after restart | `recover_jobs()` (`engine.py:2889`) only re-evaluates RUNNING jobs. Workaround: next job completion triggers `_start_queued_jobs` which checks deps. Proper fix (add `_check_all_pending_deps()` to `recover_jobs`) is noted as a fast follow-up, not blocking for this chunk. |
| `_check_dependent_jobs` calls `start_job()` which may fail if max concurrent reached | Dependent jobs stay PENDING longer than expected | `start_job()` (`engine.py:2867-2882`) already handles this: if at limit, job stays PENDING and `_start_queued_jobs` picks it up later. The `try/except ValueError` in `_check_dependent_jobs` handles any other edge cases. |
| `json_each()` in `jobs_depending_on` requires SQLite >= 3.38 | Old systems may fail | SQLite 3.38+ ships with Python 3.11+. Our min Python is 3.10 but `json_each` has been available since SQLite 3.9 (2015). Verified: `json_each` is stable and universally available. |
