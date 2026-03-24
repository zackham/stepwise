---
title: "Implementation Plan: Core Job Staging (O1 Chunk 2)"
date: "2026-03-24"
project: stepwise
tags: [implementation, plan]
revision: 2
---

# Implementation Plan: Core Job Staging

## Overview

Add STAGED status, group labels, inter-job dependencies with cycle detection, and CLI commands for job lifecycle management. Jobs can be created in a staged state, organized into groups, ordered via `depends_on`, and batch-transitioned to PENDING via `job run`.

## Design Decisions

### D1: Dependency resolution on `job run`
All staged jobs in the group transition to PENDING atomically in a single SQL UPDATE. The engine handles sequencing: `_start_queued_jobs()` already starts PENDING jobs, and we add a dep-check gate there. Jobs with unmet deps sit in PENDING until their deps complete. No topological-sort transition ordering needed.

### D2: Dependency on non-existent or invalid jobs
`job dep` validates that the target job exists. It does NOT block deps on already-failed/cancelled jobs — that's a runtime concern. If a dep is failed/cancelled at the time the dependent is evaluated for start, it stays PENDING (the user can cancel it or remove the dep). This matches "if A fails, B stays pending with unmet deps" from the spec.

### D3: `job cancel` semantics
Cancel a staged job = move to CANCELLED. Cancel a pending job = move to CANCELLED. Cancel a running job = delegate to `engine.cancel_job()` (which already cancels active runs/tasks). When a job is cancelled, all STAGED/PENDING jobs that depend on it are **recursively** cascaded to CANCELLED (full transitive closure, not just direct dependents).

### D4: `job rm` cascade behavior
`job rm` on a staged job that has dependents is blocked with an error ("cannot remove: job X depends on this job"). User must remove deps first (`job dep rm`) or rm the dependent.

### D5: Cross-group deps
Allowed. No constraint on cross-group deps — groups are organizational labels, not isolation boundaries. `job run --group deploy` will transition those jobs to PENDING, but jobs with unmet deps simply won't start until deps complete. The CLI prints an informational note when running a group containing jobs with cross-group deps (count of jobs with unmet external deps).

### D6: Startup reconciliation
`_start_queued_jobs()` is called at the end of `AsyncEngine.recover_jobs()` (already called on server startup). This re-evaluates all PENDING jobs and starts any whose deps are all completed.

### D7: `job show` with no arguments
Show all STAGED and PENDING jobs. With `--all`, include RUNNING/PAUSED too. Single-job detail shows status, group, deps, and inputs.

### D8: Migration strategy
Use the existing `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` pattern from `store.py:_migrate()`. This is proven safe — columns are only added when absent. Existing databases post-migration will have `job_group=NULL` and no dependency rows, which is the expected zero state.

### D9: Join table for dependencies
Use a `job_dependencies` join table instead of JSON columns. This gives indexed lookups and eliminates substring false-positive bugs. The `depends_on` field on the Job model remains a `list[str]` in Python — the join table is the DB-level representation.

### D10: Staged status and engine isolation
`pending_jobs_with_deps_met()` is the new method used by `_start_queued_jobs()`. It returns only PENDING jobs with all deps COMPLETED (or no deps). STAGED jobs are never returned by `pending_jobs()` or `active_jobs()` because those filter on `status=PENDING` and `status=RUNNING` respectively. **`all_jobs()` default is unchanged** — it already accepts `status` filter, callers that don't want STAGED pass `status` explicitly. No breaking change to existing API.

### D11: `depends_on` hydration contract
The `job_dependencies` join table is the sole source of truth. `Job.depends_on` is populated only in `load_job()` (single-job detail). Bulk methods (`all_jobs`, `pending_jobs`, `active_jobs`) leave `depends_on` as `[]` — this is documented, intentional, and not displayed in listing tables. `save_job()` does **not** sync the join table; deps are managed exclusively via `add_job_dependency()` / `remove_job_dependency()`.

### D12: Dependency mutation lifecycle rule
Dependencies can only be added or removed when the **dependent** job (the one that waits) is in STAGED status. This prevents race conditions where adding a dep to a PENDING job could conflict with the engine concurrently evaluating startability. Enforced at both store and CLI/API layers.

### D13: CLI/server delegation contract
All mutation commands (`create`, `run`, `cancel`, `rm`, `dep`) use the existing `detect_server()` + `StepwiseClient` pattern. When a server is running, mutations go through the HTTP API; when no server, they go directly to the store. The existing codebase already has this pattern in `cmd_run()` — we follow it exactly. No dual-path writes to SQLite while server is running.

### D14: Column naming
Use `job_group` instead of `group` to avoid quoting a SQL reserved keyword in every query.

---

## Step-by-Step Implementation

### Step 1: Add STAGED to JobStatus enum

**File:** `src/stepwise/models.py`

Add `STAGED = "staged"` to the `JobStatus` enum (line ~30), before PENDING:

```python
class JobStatus(Enum):
    STAGED = "staged"      # new
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

**Test:** Existing tests pass (STAGED is additive, no existing code creates staged jobs).

---

### Step 2: Add `job_group` and `depends_on` fields to Job dataclass

**File:** `src/stepwise/models.py`

Add to the `Job` dataclass (after `metadata`, line ~1413):

```python
    job_group: str | None = None
    depends_on: list[str] = field(default_factory=list)
```

Update `to_dict()` to include:
```python
    "job_group": self.job_group,
    "depends_on": self.depends_on,
```

Update `from_dict()` to parse:
```python
    job_group=d.get("job_group"),
    depends_on=d.get("depends_on", []),
```

**Test:** `Job.to_dict()` / `Job.from_dict()` roundtrip with job_group and depends_on.

---

### Step 3: DB schema — add `job_group` column + `job_dependencies` table

**File:** `src/stepwise/store.py`

**3a. Migration in `_migrate()`** — add after existing job column migrations (~line 133):

```python
# Add job_group column to jobs
if "job_group" not in job_columns:
    self._conn.execute("ALTER TABLE jobs ADD COLUMN job_group TEXT")
```

**3b. Create `job_dependencies` table** — add to `_create_tables()`:

```sql
CREATE TABLE IF NOT EXISTS job_dependencies (
    job_id TEXT NOT NULL,
    depends_on_job_id TEXT NOT NULL,
    PRIMARY KEY (job_id, depends_on_job_id),
    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_job_deps_depends_on
    ON job_dependencies(depends_on_job_id);
```

`ON DELETE CASCADE` is defense-in-depth alongside app-level cleanup. `PRAGMA foreign_keys=ON` is already set at store.py line 42.

**3c. Update `save_job()`** to persist `job_group`:

Add `job_group` to the INSERT column list and the ON CONFLICT SET clause. `save_job()` does **not** touch `job_dependencies` — deps are managed exclusively via dedicated methods (see D11).

**3d. Update `_row_to_job()`** to read `job_group`:

```python
job_group=row["job_group"] if "job_group" in row.keys() else None,
```

`depends_on` is NOT read from the jobs table — it's reconstituted from `job_dependencies` only in `load_job()`.

**3e. Add dependency store methods:**

```python
def add_job_dependency(self, job_id: str, depends_on_job_id: str) -> None:
    """Add a dependency edge: job_id depends on depends_on_job_id.

    Caller must validate: both jobs exist, dependent is STAGED, no cycle.
    INSERT OR IGNORE makes this idempotent (silent no-op on duplicate).
    """
    with self._conn:
        self._conn.execute(
            "INSERT OR IGNORE INTO job_dependencies (job_id, depends_on_job_id) VALUES (?, ?)",
            (job_id, depends_on_job_id),
        )

def remove_job_dependency(self, job_id: str, depends_on_job_id: str) -> None:
    """Remove a dependency edge. Caller must validate dependent is STAGED."""
    with self._conn:
        self._conn.execute(
            "DELETE FROM job_dependencies WHERE job_id = ? AND depends_on_job_id = ?",
            (job_id, depends_on_job_id),
        )

def get_job_dependencies(self, job_id: str) -> list[str]:
    """Return list of job IDs that this job depends on."""
    rows = self._conn.execute(
        "SELECT depends_on_job_id FROM job_dependencies WHERE job_id = ?",
        (job_id,),
    ).fetchall()
    return [r[0] for r in rows]

def get_job_dependents(self, depends_on_job_id: str) -> list[str]:
    """Return list of job IDs that depend on the given job."""
    rows = self._conn.execute(
        "SELECT job_id FROM job_dependencies WHERE depends_on_job_id = ?",
        (depends_on_job_id,),
    ).fetchall()
    return [r[0] for r in rows]

def pending_jobs_with_deps_met(self) -> list[Job]:
    """Return PENDING jobs whose dependencies are all COMPLETED (or have no deps).

    Uses LEFT JOIN to handle missing dep rows defensively — if a dep's job record
    was forcefully deleted, the dep is treated as unmet (job stays PENDING).
    """
    rows = self._conn.execute(
        """SELECT j.* FROM jobs j
           WHERE j.status = ?
             AND NOT EXISTS (
               SELECT 1 FROM job_dependencies d
               LEFT JOIN jobs dep ON dep.id = d.depends_on_job_id
               WHERE d.job_id = j.id
                 AND (dep.id IS NULL OR dep.status != ?)
             )
           ORDER BY j.created_at""",
        (JobStatus.PENDING.value, JobStatus.COMPLETED.value),
    ).fetchall()
    return [self._row_to_job(r) for r in rows]
```

**3f. Update `delete_job()`** to cascade dependency cleanup:

```python
def delete_job(self, job_id: str) -> None:
    """Delete a job and all associated runs, events, and dependency edges."""
    with self._conn:
        self._conn.execute(
            "DELETE FROM job_dependencies WHERE job_id = ? OR depends_on_job_id = ?",
            (job_id, job_id),
        )
        self._conn.execute("DELETE FROM events WHERE job_id = ?", (job_id,))
        self._conn.execute("DELETE FROM step_runs WHERE job_id = ?", (job_id,))
        self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
```

App-level cascade + `ON DELETE CASCADE` FK = belt and suspenders.

**Test:** Create jobs, add deps via store methods, verify `get_job_dependencies()`, `get_job_dependents()`, `pending_jobs_with_deps_met()`.

---

### Step 4: Hydrate `depends_on` on Job load

**File:** `src/stepwise/store.py`

Update `load_job()` only:
```python
def load_job(self, job_id: str) -> Job:
    row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise KeyError(f"Job not found: {job_id}")
    job = self._row_to_job(row)
    job.depends_on = self.get_job_dependencies(job_id)
    return job
```

Bulk methods (`all_jobs`, `pending_jobs`, `active_jobs`) leave `depends_on` as `[]`. This is intentional — those paths don't display deps, and hydrating per-job would be N+1 queries. If bulk dep data is ever needed, a single `SELECT * FROM job_dependencies` + dict build is O(1) extra queries (added later if needed).

---

### Step 5: Cycle detection

**File:** `src/stepwise/store.py`

```python
def would_create_cycle(self, job_id: str, depends_on_job_id: str) -> bool:
    """Check if adding edge (job_id depends_on depends_on_job_id) creates a cycle.

    When adding A→B ("A depends on B"), check: does B already transitively
    depend on A? BFS from B through existing forward edges; if we reach A,
    adding A→B would create A→B→...→A.
    """
    # Self-dependency is always a cycle
    if job_id == depends_on_job_id:
        return True
    visited: set[str] = set()
    queue = [depends_on_job_id]
    while queue:
        current = queue.pop(0)
        if current == job_id:
            return True
        if current in visited:
            continue
        visited.add(current)
        queue.extend(self.get_job_dependencies(current))
    return False
```

Direction correctness: Graph edges represent "waits for" (A→B = "A waits for B"). Adding A→B creates a cycle iff B→...→A already exists. BFS from B following forward edges (`get_job_dependencies(B)` = what B waits for) checks exactly this.

**Test:** Self-cycle (A→A rejected), direct cycle (A→B, B→A rejected), transitive cycle (A→B→C, C→A rejected), long chain (A→B→C→D, D→A rejected), valid DAG accepted.

---

### Step 6: Engine integration — dep-aware job starting

**File:** `src/stepwise/engine.py`

**6a. Modify `_start_queued_jobs()`** (AsyncEngine, line ~3312):

Replace `self.store.pending_jobs()` with `self.store.pending_jobs_with_deps_met()`:

```python
def _start_queued_jobs(self) -> None:
    """Start PENDING jobs if slots are available and deps are met (FIFO order)."""
    active_count = len(self.store.active_jobs())
    if self.max_concurrent_jobs > 0 and active_count >= self.max_concurrent_jobs:
        return
    for pending_job in self.store.pending_jobs_with_deps_met():
        if self.max_concurrent_jobs > 0 and active_count >= self.max_concurrent_jobs:
            break
        if pending_job.parent_job_id:
            continue
        try:
            self.start_job(pending_job.id)
            active_count += 1
        except ValueError:
            pass
```

**6b. Trigger dep evaluation on job completion.** In `_check_job_terminal()`, after a job completes or fails, `_start_queued_jobs()` is already called (line ~3310). This naturally picks up newly-unblocked dependents. No additional hook needed.

**6c. Recursive cancel cascade.** Override `cancel_job()` in AsyncEngine:

```python
def cancel_job(self, job_id: str) -> None:
    super().cancel_job(job_id)  # existing cancellation logic (runs, tasks, etc.)
    # Recursive cascade: cancel all STAGED/PENDING transitive dependents
    visited: set[str] = set()
    queue = list(self.store.get_job_dependents(job_id))
    while queue:
        dep_job_id = queue.pop(0)
        if dep_job_id in visited:
            continue
        visited.add(dep_job_id)
        try:
            dep_job = self.store.load_job(dep_job_id)
            if dep_job.status in (JobStatus.PENDING, JobStatus.STAGED):
                dep_job.status = JobStatus.CANCELLED
                dep_job.updated_at = _now()
                self.store.save_job(dep_job)
                self._emit(dep_job_id, JOB_CANCELLED, {
                    "reason": "dependency_cancelled",
                    "cancelled_dep": job_id,
                })
                self._signal_job_done(dep_job_id)
                # Recurse into this job's dependents
                queue.extend(self.store.get_job_dependents(dep_job_id))
        except KeyError:
            pass
    self._start_queued_jobs()
```

Uses iterative BFS with `visited` set — handles arbitrary depth (A→B→C→D...) and guards against corrupted cyclic data.

**6d. Startup reconciliation.** In `recover_jobs()` (line ~2932), add after the existing loop:

```python
def recover_jobs(self) -> None:
    """Re-evaluate all RUNNING server-owned jobs after startup."""
    for job in self.store.active_jobs():
        if job.created_by != "server":
            continue
        self._check_job_terminal(job.id)
    # Reconcile pending jobs with deps — a dep may have completed while server was down
    self._start_queued_jobs()
```

**6e. STAGED naturally excluded from `start_job()`.** The existing check `if job.status != JobStatus.PENDING` already rejects STAGED jobs. No change needed.

**Test:** Create chain A→B→C. Start all as PENDING. Verify C doesn't start until B completes, B doesn't start until A completes. Cancel A → verify B and C both CANCELLED. Verify recovery reconciles deps on startup.

---

### Step 7: New event types

**File:** `src/stepwise/events.py`

Add:
```python
# Job staging
JOB_STAGED = "job.staged"
JOB_CANCELLED = "job.cancelled"
JOB_DEPS_CHANGED = "job.deps_changed"
```

`JOB_CANCELLED` is used for both direct cancellation and cascade cancellation (with `reason` field distinguishing them). This is semantically correct — cancelled jobs should not emit `JOB_FAILED`.

---

### Step 8: CLI commands under `stepwise job`

**File:** `src/stepwise/cli.py`

Add a new `job` subcommand group with sub-subcommands, following the `cache` subparser pattern.

**8a. Parser registration** (in `build_parser()`, around line ~3915):

```python
# job (staging & orchestration)
p_job = sub.add_parser("job", help="Job staging and orchestration")
job_sub = p_job.add_subparsers(dest="job_command")

# job create
p_job_create = job_sub.add_parser("create", help="Create a staged job")
p_job_create.add_argument("flow", help="Flow name or path")
p_job_create.add_argument("--input", "-i", action="append", default=[], dest="inputs",
                          metavar="KEY=VALUE", help="Input parameter")
p_job_create.add_argument("--group", "-g", help="Group label")
p_job_create.add_argument("--name", help="Job name")
p_job_create.add_argument("--output", choices=["table", "json"], default="table")

# job show
p_job_show = job_sub.add_parser("show", help="Show staged/pending jobs")
p_job_show.add_argument("job_id", nargs="?", help="Job ID (omit for listing)")
p_job_show.add_argument("--group", "-g", help="Filter by group")
p_job_show.add_argument("--all", action="store_true", help="Include running/paused jobs")
p_job_show.add_argument("--output", choices=["table", "json"], default="table")

# job run
p_job_run = job_sub.add_parser("run", help="Run staged jobs")
p_job_run.add_argument("job_id", nargs="?", help="Run a single staged job")
p_job_run.add_argument("--group", "-g", help="Run all staged jobs in group")
p_job_run.add_argument("--output", choices=["table", "json"], default="table")

# job dep
p_job_dep = job_sub.add_parser("dep", help="Manage dependencies between jobs")
p_job_dep.add_argument("job_id", help="Job that should wait")
p_job_dep.add_argument("--after", help="Job to wait for (add dep)")
p_job_dep.add_argument("--rm", help="Remove dependency on this job")
p_job_dep.add_argument("--output", choices=["table", "json"], default="table")

# job cancel
p_job_cancel = job_sub.add_parser("cancel", help="Cancel a staged/pending/running job")
p_job_cancel.add_argument("job_id", help="Job ID")
p_job_cancel.add_argument("--output", choices=["table", "json"], default="table")

# job rm
p_job_rm = job_sub.add_parser("rm", help="Remove a staged job")
p_job_rm.add_argument("job_id", help="Job ID")
p_job_rm.add_argument("--output", choices=["table", "json"], default="table")
```

**8b. Handler registration** — add to the `handlers` dict:
```python
"job": cmd_job,
```

**8c. Command implementations:**

All mutation commands follow the **existing server delegation pattern** from `cmd_run()`:

```python
def _get_store_or_client(args):
    """Return (store, client) — exactly one is non-None.

    Uses detect_server() from server_detect.py. If server running, return
    StepwiseClient for API calls. If not, open store directly.
    """
    project = get_project()
    server_info = detect_server(project.dot_dir)
    if server_info and not getattr(args, 'local', False):
        return None, StepwiseClient(f"http://localhost:{server_info.port}")
    return SQLiteStore(project.db_path), None
```

Every `_cmd_job_*` handler calls this and branches on which is non-None. The server path uses REST endpoints (Step 9); the local path uses the store directly.

**`_cmd_job_create`:**
1. Resolve flow path via existing `_resolve_flow_path()`
2. Parse `--input` flags into dict (reuse existing `_parse_inputs()`)
3. Load workflow via `load_workflow_yaml()` — validates YAML structure
4. Create `Job` with `status=JobStatus.STAGED`, `job_group=args.group`
5. Server path: POST `/api/jobs` with `status: "staged"`, `job_group: group`
6. Local path: `store.save_job(job)`, print job ID
7. Print job ID and status

**`_cmd_job_show`:**
- With `job_id`: load single job via `load_job()` (hydrates deps), show detail (status, group, deps, inputs)
- Without `job_id`: list STAGED+PENDING jobs (add RUNNING+PAUSED with `--all`), filter by `--group`

**`_cmd_job_run`:**
- With `job_id`: load job, verify STAGED, transition to PENDING
- With `--group`: call `store.transition_group_to_pending(group)`
- Without either: error ("specify --group or job_id")
- After transition: print count of transitioned jobs + note if any have cross-group unmet deps (count only)
- Server path: POST `/api/jobs/run-group` or POST `/api/jobs/{id}/run`

**`_cmd_job_dep`:**
- With `--after`: add dependency
  1. Validate both jobs exist
  2. Validate dependent job is STAGED (D12)
  3. Check cycle via `store.would_create_cycle()`
  4. `store.add_job_dependency()`
- With `--rm`: remove dependency
  1. Validate dependent job is STAGED (D12)
  2. `store.remove_job_dependency()`
- Neither: show current deps for the job

**`_cmd_job_cancel`:**
- Load job, verify not terminal
- If staged: set CANCELLED directly, emit JOB_CANCELLED
- If pending/running: delegate to engine cancel (which handles recursive cascade)

**`_cmd_job_rm`:**
- Load job, verify status is STAGED (STAGED only — CANCELLED/FAILED cleanup is `job cancel` then regular cleanup)
- Check no dependents exist (`store.get_job_dependents()`)
- If dependents: error listing dependent job IDs, suggest `job dep rm` first
- `store.delete_job(job_id)`

**Test:** Each CLI command via direct function calls with both store-direct and server-delegated paths.

---

### Step 9: Server API endpoints for staging

**File:** `src/stepwise/server.py`

**9a. Extend `CreateJobRequest`** to accept `job_group` and `status`:

```python
class CreateJobRequest(BaseModel):
    ...
    job_group: str | None = None
    status: str | None = None  # "staged" to create in staged state
```

**9b. Extend `POST /api/jobs`** to handle staged creation:

In `create_job()` handler, after creating the job:
```python
if request.status == "staged":
    job.status = JobStatus.STAGED
if request.job_group:
    job.job_group = request.job_group
store.save_job(job)
```

**9c. Add new endpoints:**

```python
@app.post("/api/jobs/{job_id}/run")
async def run_staged_job(job_id: str):
    """Transition a single STAGED job to PENDING."""
    ...

@app.post("/api/jobs/run-group")
async def run_group(request: RunGroupRequest):
    """Transition all staged jobs in a group to PENDING."""
    ...

@app.post("/api/jobs/{job_id}/deps")
async def add_dependency(job_id: str, request: AddDepRequest):
    """Add a dependency edge. Validates STAGED status and cycle-free."""
    ...

@app.delete("/api/jobs/{job_id}/deps/{dep_job_id}")
async def remove_dependency(job_id: str, dep_job_id: str):
    """Remove a dependency edge. Validates STAGED status."""
    ...

@app.get("/api/jobs/{job_id}/deps")
async def get_dependencies(job_id: str):
    """Return dependency list for a job."""
    ...
```

**9d. `GET /api/jobs`** already supports `status` query param — no change needed. Callers can filter `?status=staged` or omit for all.

All mutation endpoints that affect the dependency graph or job status call `engine._start_queued_jobs()` after committing, so the engine re-evaluates immediately.

**Test:** Server endpoint tests via httpx test client (existing pattern in `tests/test_server.py`).

---

### Step 10: Atomic group transition + single-job transition

**File:** `src/stepwise/store.py`

```python
def transition_group_to_pending(self, group: str) -> list[str]:
    """Atomically transition all STAGED jobs in a group to PENDING. Returns transitioned job IDs."""
    now = _now().isoformat()
    with self._conn:
        self._conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE job_group = ? AND status = ?",
            (JobStatus.PENDING.value, now, group, JobStatus.STAGED.value),
        )
        rows = self._conn.execute(
            "SELECT id FROM jobs WHERE job_group = ? AND status = ? AND updated_at = ?",
            (group, JobStatus.PENDING.value, now),
        ).fetchall()
    return [r[0] for r in rows]

def transition_job_to_pending(self, job_id: str) -> None:
    """Transition a single STAGED job to PENDING. Raises ValueError if not STAGED."""
    job = self.load_job(job_id)
    if job.status != JobStatus.STAGED:
        raise ValueError(f"Cannot run job in status {job.status.value} (must be STAGED)")
    with self._conn:
        self._conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
            (JobStatus.PENDING.value, _now().isoformat(), job_id),
        )
```

Note: Avoids SQLite `RETURNING` clause — not used elsewhere in the codebase and requires SQLite ≥ 3.35.0. The UPDATE-then-SELECT pattern is compatible with all SQLite versions the project supports.

**Test:** Create 3 staged jobs in group "build". Call `transition_group_to_pending("build")`. Verify all 3 are now PENDING. Verify a 4th job in group "deploy" is untouched. Test `transition_job_to_pending()` on staged and non-staged jobs.

---

## Council Feedback Resolution

| # | Council Issue | Resolution |
|---|---|---|
| 1 | [CRITICAL] Cancel cascade not recursive | **Fixed.** Step 6c now uses iterative BFS with `visited` set, traverses full transitive closure. |
| 2 | [CRITICAL] CLI direct-store vs server delegation | **Fixed.** D13 + Step 8c define `_get_store_or_client()` pattern, matching existing `cmd_run()` behavior. |
| 3 | [CRITICAL] `depends_on` hydration ambiguous | **Fixed.** D11 codifies: hydrate only in `load_job()`, bulk paths return `[]`, documented as intentional. |
| 4 | [CRITICAL] `save_job()` doesn't sync deps | **Fixed.** D11 explicitly states `save_job()` does not touch deps; managed via dedicated methods only. |
| 5 | [CRITICAL] No lifecycle rule for dep mutation | **Fixed.** D12 restricts add/remove to STAGED dependent only, enforced at store + CLI/API. |
| 6 | [IMPORTANT] `all_jobs()` default change | **Fixed.** D10 preserves existing default. STAGED is just another status — existing `status` filter works. |
| 7 | [IMPORTANT] FK pragma missing | **Already handled.** `PRAGMA foreign_keys=ON` at store.py line 42. Added `ON DELETE CASCADE` to DDL (Step 3b). |
| 8 | [IMPORTANT] SQLite `RETURNING` compatibility | **Fixed.** Step 10 uses UPDATE-then-SELECT pattern instead. No `RETURNING` anywhere in codebase. |
| 9 | [IMPORTANT] No `job dep rm` command | **Fixed.** Step 8a adds `--rm` flag to `job dep` command. |
| 10 | [IMPORTANT] Cascade emits JOB_FAILED | **Fixed.** Step 6c emits `JOB_CANCELLED` event. Step 7 adds the constant. |
| 11 | [IMPORTANT] Cycle detection direction | **Verified correct.** Added explicit self-dep check and clarified BFS direction in Step 5 comments. |
| 12 | [IMPORTANT] Transaction boundaries | **Fixed.** Steps 3e, 3f, 10 use `with self._conn:` context manager for all multi-statement ops. |
| 13 | [NICE-TO-HAVE] `group` SQL keyword | **Fixed.** D14 renames to `job_group`. |
| — | Minority: `_cmd_job_create` underspecified | **Fixed.** Step 8c details the flow resolution + job creation steps. |
| — | Minority: No `transition_job_to_pending()` | **Fixed.** Step 10 adds both group and single-job transition methods. |
| — | Minority: `job rm` scope too narrow | **Deferred.** STAGED-only is correct for now — `rm` means "undo a staged job that shouldn't have been created." Cleaning up terminal jobs is a different operation (future: `job clean`). |
| — | Minority: LEFT JOIN safety | **Fixed.** Step 3e `pending_jobs_with_deps_met()` uses `LEFT JOIN` + `dep.id IS NULL` check. |
| — | Minority: `job show` inconsistency | **Fixed.** D7 clarifies: STAGED+PENDING by default, `--all` adds RUNNING/PAUSED. |
| — | Minority: Sub-job interaction | **Out of scope.** Sub-jobs (parent_job_id != None) are engine-managed. They cannot be staged, grouped, or depended upon. |

---

## Risk Mitigation Matrix

| Risk | Mitigation | Implementation Step |
|---|---|---|
| Dangling dep IDs after `job rm` | Block rm if dependents exist; `ON DELETE CASCADE` + app-level cleanup in `delete_job()` | Steps 3b, 3f, 8c |
| Startup reconciliation missing | `_start_queued_jobs()` in `recover_jobs()` | Step 6d |
| Substring false-positive on dep lookup | `job_dependencies` join table with parameterized queries | Step 3b |
| Partial `job run` on crash | Single atomic SQL UPDATE in `with self._conn:` | Step 10 |
| Cancelled job blocks dependents forever | Recursive cancel cascade with `visited` set | Step 6c |
| Cross-group dep confusion | Informational note printed by `job run` | Step 8c |
| Race: dep added to PENDING job | D12 restricts dep mutation to STAGED only | Steps 3e, 8c, 9c |
| CLI writes to SQLite while server holds connection | D13 enforces API-only when server is running | Step 8c |
| Cascade cancellation emits wrong event type | `JOB_CANCELLED` event (not `JOB_FAILED`) | Steps 6c, 7 |
| Missing dep job record (force-deleted) | LEFT JOIN + NULL check in `pending_jobs_with_deps_met()` | Step 3e |
| Corrupted cyclic dep data at runtime | `visited` set guards in both cancel cascade and cycle check | Steps 5, 6c |

---

## Testing Strategy

### New test file: `tests/test_job_staging.py`

```
Test groups:

1. STAGED status
   - test_staged_job_not_auto_started: create STAGED job, run engine, verify still STAGED
   - test_staged_job_survives_save_load: save+load roundtrip preserves STAGED status
   - test_staged_invisible_to_active_jobs: store.active_jobs() excludes STAGED
   - test_staged_invisible_to_pending_jobs: store.pending_jobs() excludes STAGED

2. Group operations
   - test_create_job_with_group: verify job_group field persisted
   - test_transition_group_to_pending: 3 staged → 3 pending atomically
   - test_transition_group_leaves_other_groups: only target group affected
   - test_transition_empty_group: no crash, returns empty list
   - test_transition_group_skips_cancelled: CANCELLED jobs in group not transitioned

3. Dependencies
   - test_add_dependency: add dep, verify get_job_dependencies returns it
   - test_add_dependency_idempotent: adding same dep twice is silent no-op
   - test_deps_met_all_completed: all deps completed → True (via pending_jobs_with_deps_met)
   - test_deps_met_one_pending: one dep not completed → job not in result
   - test_deps_met_no_deps: job with no deps → in result
   - test_deps_met_cancelled_dep: dep cancelled → job stays out of result (correct: stays PENDING)
   - test_get_job_dependents: reverse lookup works
   - test_delete_job_cascades_deps: delete job cleans up both directions
   - test_dep_lookup_substring_safety: "job-1" and "job-10" — completing "job-10" doesn't trigger dep on "job-1"
   - test_add_dep_rejected_on_pending_job: ValueError when dependent is PENDING
   - test_add_dep_rejected_on_running_job: ValueError when dependent is RUNNING
   - test_missing_dep_record_treated_as_unmet: LEFT JOIN safety check

4. Cycle detection
   - test_self_cycle_rejected: A→A detected (explicit early exit)
   - test_direct_cycle_rejected: A→B, B→A second call detected
   - test_transitive_cycle_rejected: A→B→C, C→A detected
   - test_long_chain_cycle_rejected: A→B→C→D, D→A detected
   - test_valid_dag_accepted: A→B→C no cycle

5. Engine integration
   - test_dep_blocks_job_start: B depends on A, both PENDING, B doesn't start until A completes
   - test_dep_unblocks_on_completion: A completes → B auto-starts
   - test_failed_dep_blocks_forever: A fails → B stays PENDING
   - test_cancel_cascades_direct: cancel A → B (direct dependent) cancelled
   - test_cancel_cascades_transitive: cancel A → B → C, all three cancelled
   - test_cancel_cascade_skips_running: cancel A, B is RUNNING → B not cancelled (only STAGED/PENDING)
   - test_startup_reconciliation: A completed while server down, B pending with dep on A → B starts on recover
   - test_dep_on_already_completed_job: B depends on completed A → B starts immediately
   - test_engine_filter_audit: existing test suite unaffected — no staged jobs in active/running/retry

6. CLI commands
   - test_cmd_job_create: creates staged job, prints ID
   - test_cmd_job_show_group: lists jobs in group
   - test_cmd_job_show_single: shows detail with deps
   - test_cmd_job_run_group: transitions group to pending
   - test_cmd_job_run_single: transitions single job to pending
   - test_cmd_job_run_already_pending: error message
   - test_cmd_job_dep_add: adds dep
   - test_cmd_job_dep_rm: removes dep
   - test_cmd_job_dep_add_to_pending_rejected: error when dependent is PENDING
   - test_cmd_job_dep_cycle_rejected: cycle detected, error message
   - test_cmd_job_cancel_staged: cancels staged job
   - test_cmd_job_rm_staged: removes staged job
   - test_cmd_job_rm_blocked_by_dependents: error when dependents exist
   - test_cmd_job_rm_non_staged_rejected: can't rm pending/running/completed

7. Server API
   - test_api_create_staged_job: POST /api/jobs with status=staged
   - test_api_add_dep: POST /api/jobs/{id}/deps
   - test_api_add_dep_missing_target_404: dep target doesn't exist
   - test_api_add_dep_cycle_409: cycle detected returns 409
   - test_api_run_group: POST /api/jobs/run-group
   - test_api_run_single: POST /api/jobs/{id}/run
   - test_api_staged_job_not_auto_started: create staged via API, verify engine doesn't start it

8. Migration
   - test_migration_idempotent: run on DB that already has job_group column
   - test_migration_preserves_existing_jobs: existing PENDING/RUNNING jobs have job_group=NULL post-migration
```

### Commands to run tests

```bash
# All staging tests
uv run pytest tests/test_job_staging.py -v

# Full regression (ensure no breakage)
uv run pytest tests/ -v

# Single test
uv run pytest tests/test_job_staging.py::TestDependencies::test_direct_cycle_rejected -v
```

---

## Implementation Order

The steps above are ordered by dependency. The recommended sequence:

1. **Steps 1-2** (model changes) — pure additive, no behavior change
2. **Step 3** (DB schema + store methods) — foundation for everything
3. **Steps 4-5** (hydration + cycle detection) — enables dep management
4. **Step 7** (events) — trivial, needed by engine
5. **Step 6** (engine integration) — core behavior
6. **Step 10** (atomic transition) — needed by CLI
7. **Step 8** (CLI) — user-facing commands
8. **Step 9** (server API) — enables server delegation

Commit after each step. Each step should be independently testable.

---

## Files Modified (summary)

| File | Changes |
|---|---|
| `src/stepwise/models.py` | STAGED enum, job_group + depends_on fields, to_dict/from_dict |
| `src/stepwise/store.py` | job_dependencies table, migration, dep CRUD methods, cycle detection, atomic transition, pending_jobs_with_deps_met |
| `src/stepwise/engine.py` | _start_queued_jobs uses deps_met, recursive cancel cascade, recover_jobs reconciliation |
| `src/stepwise/events.py` | JOB_STAGED, JOB_CANCELLED, JOB_DEPS_CHANGED constants |
| `src/stepwise/cli.py` | `stepwise job` subcommand group with create/show/run/dep/cancel/rm |
| `src/stepwise/server.py` | CreateJobRequest extended, new staging/dep endpoints |
| `tests/test_job_staging.py` | New file: ~40 tests covering all behavior |


## Execution Instructions

Please taskify and execute. Work through each step in order, committing after each step. Run tests after each step to verify no regressions.
