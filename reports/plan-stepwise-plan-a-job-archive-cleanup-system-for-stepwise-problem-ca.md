# Job Archive/Cleanup System

## Overview

Add an archive mechanism so users can hide completed, cancelled, and abandoned jobs from the default view without permanently deleting them. Archived jobs are hidden by default in both the web UI (job list + canvas) and CLI, but recoverable via a filter toggle. Supports individual archive, bulk archive by status/age/group, and unarchive.

**Design choice: `archived_at` timestamp column** — not a new `JobStatus`, not metadata. A dedicated nullable `TEXT` column on the `jobs` table keeps archive orthogonal to job lifecycle (a completed job is still completed, just hidden). The timestamp records *when* it was archived, enabling "archived in last 7 days" queries and audit trails. `NULL` = not archived.

---

## Requirements & Acceptance Criteria

### R1: Archive individual jobs
- **AC1.1**: `POST /api/jobs/{job_id}/archive` sets `archived_at` to current ISO timestamp
- **AC1.2**: `POST /api/jobs/{job_id}/unarchive` sets `archived_at` back to `NULL`
- **AC1.3**: `stepwise jobs archive <job_id>` archives from CLI; `stepwise jobs unarchive <job_id>` restores
- **AC1.4**: Archiving a RUNNING or PAUSED job is rejected (400) — must cancel first

### R2: Bulk archive by status
- **AC2.1**: `POST /api/jobs/archive` with `{"status": "cancelled"}` archives all cancelled jobs
- **AC2.2**: `POST /api/jobs/archive` with `{"status": "completed", "older_than": "7d"}` archives completed jobs older than 7 days
- **AC2.3**: `stepwise jobs archive --status cancelled` and `stepwise jobs archive --status completed --older-than 7d` work from CLI

### R3: Bulk archive by group
- **AC3.1**: `POST /api/jobs/archive` with `{"group": "my-group"}` archives all terminal jobs in the group
- **AC3.2**: `stepwise jobs archive --group my-group` works from CLI

### R4: Default hiding
- **AC4.1**: `GET /api/jobs` excludes archived jobs by default (`WHERE archived_at IS NULL`)
- **AC4.2**: `GET /api/jobs?include_archived=true` returns all jobs (archived + active)
- **AC4.3**: `GET /api/jobs?archived_only=true` returns only archived jobs
- **AC4.4**: `stepwise jobs` hides archived by default; `stepwise jobs --archived` shows only archived; `stepwise jobs --all` shows everything
- **AC4.5**: The web UI job list hides archived jobs by default
- **AC4.6**: The DAG canvas view for a selected job is unaffected (you can still view an archived job's DAG if you navigate to it directly)

### R5: Web UI archive controls
- **AC5.1**: "Archive" option in the per-job dropdown menu (the `...` `MoreVertical` menu) for terminal jobs (completed/failed/cancelled)
- **AC5.2**: "Unarchive" option appears in the dropdown for archived jobs (when viewing archived list)
- **AC5.3**: Toggle/filter pill in the job list header bar: "Show archived" that switches between active-only and all/archived-only views
- **AC5.4**: Bulk sweep buttons: "Archive all cancelled", "Archive completed older than..." in a toolbar/dropdown near the existing "Delete All" area
- **AC5.5**: Toast notification on archive/unarchive with count of affected jobs

### R6: CLI archive subcommands
- **AC6.1**: `stepwise jobs archive <job_id> [<job_id> ...]` — archive specific jobs by ID
- **AC6.2**: `stepwise jobs archive --status <status> [--older-than <duration>]` — bulk by status+age
- **AC6.3**: `stepwise jobs archive --group <group>` — bulk by group
- **AC6.4**: `stepwise jobs unarchive <job_id> [<job_id> ...]` — restore specific jobs
- **AC6.5**: `stepwise jobs unarchive --all` — unarchive everything
- **AC6.6**: Output shows count of archived/unarchived jobs

---

## Assumptions (verified against codebase)

1. **No existing archive mechanism** — confirmed: Job model has no `archived_at`, `hidden`, or similar field. The only removal path is permanent `DELETE`.

2. **Migration pattern is ALTER TABLE** — confirmed: `store.py:_migrate()` uses `PRAGMA table_info()` + conditional `ALTER TABLE ... ADD COLUMN`. New columns are added to the migration list at `store.py:138-153`.

3. **`all_jobs()` is the central query method** — confirmed at `store.py:457`. Both the server endpoint (`GET /api/jobs` at `server.py:880`) and CLI (`cmd_jobs` at `cli.py:2372`) flow through `store.all_jobs()`. Adding `archived_at IS NULL` to this method's default WHERE clause hides archived jobs everywhere.

4. **Job serialization flows through `_serialize_job()`** — confirmed at `server.py:880`. The `archived_at` field needs to be added to `Job.to_dict()` and the TypeScript `Job` interface.

5. **`_row_to_job()` maps DB rows to Job objects** — confirmed at `store.py:218`. Must handle the new `archived_at` column with a fallback for older DBs.

6. **`save_job()` INSERT/UPDATE covers all columns** — confirmed at `store.py:157-201`. Must add `archived_at` to both the INSERT and ON CONFLICT UPDATE clauses.

7. **Frontend filtering is client-side** — confirmed: `JobList.tsx` fetches all jobs via `useJobs()` then filters in `useMemo`. Archive filtering fits the same pattern (server-side default exclusion + client-side toggle).

8. **Mutations pattern** — confirmed: `useStepwiseMutations()` in `useStepwise.ts:134` follows a consistent pattern: `useMutation` → `invalidateAll()` → `toast.success()`. Archive/unarchive mutations follow the same shape.

9. **`job_group` already exists** — confirmed at `models.py:1650` and `store.py:148,349`. Bulk archive by group can leverage `jobs_in_group()`.

10. **Active job protection** — the engine tracks RUNNING/PAUSED jobs for heartbeat, stale detection, and adoption. Archiving must be restricted to terminal states (COMPLETED, FAILED, CANCELLED) to avoid hiding live work.

---

## Implementation Steps

### Step 1: Add `archived_at` to Job model
**File: `src/stepwise/models.py`**

- Add field `archived_at: datetime | None = None` to `Job` dataclass (after `job_group`, line ~1651)
- Add `"archived_at": self.archived_at.isoformat() if self.archived_at else None` to `to_dict()` (line ~1672)
- Add `archived_at=datetime.fromisoformat(d["archived_at"]) if d.get("archived_at") else None` to `from_dict()` (line ~1700)

### Step 2: Add DB column + migration
**File: `src/stepwise/store.py`**

- Add `("archived_at", "TEXT", None)` to the migration list at line 148 (alongside `job_group`)
- Update `save_job()` (line 157-201): add `archived_at` to INSERT columns, VALUES placeholders, ON CONFLICT UPDATE clause, and the parameter tuple
- Update `_row_to_job()` (line 218-240): add `archived_at=_parse_dt(row["archived_at"]) if "archived_at" in row.keys() and row["archived_at"] else None`

### Step 3: Add archive/unarchive store methods
**File: `src/stepwise/store.py`**

Add after `delete_job()` (line ~271):

```python
def archive_job(self, job_id: str) -> None:
    """Set archived_at timestamp on a job."""
    self._conn.execute(
        "UPDATE jobs SET archived_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), job_id),
    )
    self._conn.commit()

def unarchive_job(self, job_id: str) -> None:
    """Clear archived_at timestamp on a job."""
    self._conn.execute(
        "UPDATE jobs SET archived_at = NULL WHERE id = ?",
        (job_id,),
    )
    self._conn.commit()

def archive_jobs_by_status(
    self, status: JobStatus, older_than: datetime | None = None
) -> list[str]:
    """Archive all terminal jobs matching status (and optionally age). Returns archived job IDs."""
    now = datetime.now(timezone.utc).isoformat()
    clauses = ["status = ?", "archived_at IS NULL"]
    params: list = [status.value]
    if older_than:
        clauses.append("created_at < ?")
        params.append(older_than.isoformat())
    params.insert(0, now)  # for SET archived_at = ?
    where = " AND ".join(clauses)
    cursor = self._conn.execute(
        f"UPDATE jobs SET archived_at = ? WHERE {where} RETURNING id",
        params,
    )
    ids = [row[0] for row in cursor.fetchall()]
    self._conn.commit()
    return ids

def archive_jobs_by_group(self, group: str) -> list[str]:
    """Archive all terminal jobs in a group. Returns archived job IDs."""
    now = datetime.now(timezone.utc).isoformat()
    terminal = [JobStatus.COMPLETED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value]
    placeholders = ",".join("?" * len(terminal))
    cursor = self._conn.execute(
        f"UPDATE jobs SET archived_at = ? WHERE job_group = ? AND status IN ({placeholders}) AND archived_at IS NULL RETURNING id",
        [now, group] + terminal,
    )
    ids = [row[0] for row in cursor.fetchall()]
    self._conn.commit()
    return ids

def unarchive_all(self) -> int:
    """Unarchive all archived jobs. Returns count."""
    cursor = self._conn.execute(
        "UPDATE jobs SET archived_at = NULL WHERE archived_at IS NOT NULL"
    )
    self._conn.commit()
    return cursor.rowcount
```

### Step 4: Update `all_jobs()` to exclude archived by default
**File: `src/stepwise/store.py`**

Modify `all_jobs()` signature and body (line 457):

```python
def all_jobs(
    self,
    status: JobStatus | None = None,
    top_level_only: bool = False,
    limit: int = 0,
    meta_filters: dict[str, str] | None = None,
    include_archived: bool = False,
    archived_only: bool = False,
) -> list[Job]:
```

Add to the clauses block:
- If `archived_only`: append `"archived_at IS NOT NULL"`
- Elif not `include_archived`: append `"archived_at IS NULL"`
- (If `include_archived` is True, no archive clause — returns everything)

### Step 5: Add REST API endpoints
**File: `src/stepwise/server.py`**

**5a. Update `GET /api/jobs`** (line 880): Add query params `include_archived: bool = False` and `archived_only: bool = False`, pass through to `store.all_jobs()`.

**5b. Add individual archive/unarchive endpoints** (after the cancel endpoint):

```python
@app.post("/api/jobs/{job_id}/archive")
def archive_job(job_id: str):
    engine = _get_engine()
    job = engine.store.load_job(job_id)
    if job.status in (JobStatus.RUNNING, JobStatus.PAUSED):
        raise HTTPException(400, "Cannot archive a running/paused job")
    engine.store.archive_job(job_id)
    _notify_change(job_id)
    return {"status": "archived"}

@app.post("/api/jobs/{job_id}/unarchive")
def unarchive_job(job_id: str):
    engine = _get_engine()
    engine.store.load_job(job_id)  # verify exists
    engine.store.unarchive_job(job_id)
    _notify_change(job_id)
    return {"status": "unarchived"}
```

**5c. Add bulk archive endpoint:**

```python
class BulkArchiveRequest(BaseModel):
    status: str | None = None
    older_than: str | None = None  # e.g. "7d", "24h", "30d"
    group: str | None = None

@app.post("/api/jobs/archive")
def bulk_archive(req: BulkArchiveRequest):
    engine = _get_engine()
    ids: list[str] = []
    if req.group:
        ids = engine.store.archive_jobs_by_group(req.group)
    elif req.status:
        job_status = JobStatus(req.status)
        if job_status in (JobStatus.RUNNING, JobStatus.PAUSED):
            raise HTTPException(400, "Cannot archive running/paused jobs")
        older_than_dt = _parse_duration(req.older_than) if req.older_than else None
        ids = engine.store.archive_jobs_by_status(job_status, older_than_dt)
    for jid in ids:
        _notify_change(jid)
    return {"status": "archived", "count": len(ids), "job_ids": ids}
```

**5d. Add bulk unarchive endpoint:**

```python
@app.post("/api/jobs/unarchive")
def bulk_unarchive():
    engine = _get_engine()
    count = engine.store.unarchive_all()
    # broadcast generic change
    if _event_loop:
        _event_loop.call_soon_threadsafe(
            _event_loop.create_task,
            _broadcast({"type": "jobs_changed"}),
        )
    return {"status": "unarchived", "count": count}
```

**5e. Add `_parse_duration()` helper** to convert "7d", "24h", "30d" strings into a `datetime` cutoff (now minus duration). This is straightforward: parse the numeric prefix and unit suffix, compute `datetime.now(UTC) - timedelta(...)`.

### Step 6: Add CLI archive/unarchive subcommands
**File: `src/stepwise/cli.py`**

**6a. Add `archive` and `unarchive` subcommands** under the existing `jobs` command parser:

```
stepwise jobs archive <job_id> [<job_id> ...]
stepwise jobs archive --status cancelled [--older-than 7d]
stepwise jobs archive --group my-group
stepwise jobs unarchive <job_id> [<job_id> ...]
stepwise jobs unarchive --all
```

Implementation: add `archive_parser = jobs_subparsers.add_parser("archive")` and `unarchive_parser = jobs_subparsers.add_parser("unarchive")` alongside existing subcommands.

**6b. Update `cmd_jobs`** (line 2372): Add `--archived` flag (show only archived) and `--all` flag override to include archived in the default list view.

**6c. Handler functions**: `cmd_jobs_archive(args)` and `cmd_jobs_unarchive(args)` that call store methods directly (local mode) or delegate to server API (if server is running), following the existing delegation pattern.

### Step 7: Update TypeScript types
**File: `web/src/lib/types.ts`**

Add to `Job` interface (line ~245):
```typescript
archived_at: string | null;
```

### Step 8: Add API client functions
**File: `web/src/lib/api.ts`**

```typescript
export function archiveJob(jobId: string): Promise<{ status: string }> {
  return request(`/jobs/${jobId}/archive`, { method: "POST" });
}

export function unarchiveJob(jobId: string): Promise<{ status: string }> {
  return request(`/jobs/${jobId}/unarchive`, { method: "POST" });
}

export function bulkArchive(params: {
  status?: string;
  older_than?: string;
  group?: string;
}): Promise<{ status: string; count: number; job_ids: string[] }> {
  return request("/jobs/archive", {
    method: "POST",
    body: JSON.stringify(params),
  });
}

export function bulkUnarchive(): Promise<{ status: string; count: number }> {
  return request("/jobs/unarchive", { method: "POST" });
}
```

Update `fetchJobs` to accept `includeArchived` and `archivedOnly` params:
```typescript
export function fetchJobs(
  status?: string,
  topLevel?: boolean,
  includeArchived?: boolean,
  archivedOnly?: boolean,
): Promise<Job[]> {
  const searchParams = new URLSearchParams();
  if (status) searchParams.set("status", status);
  if (topLevel) searchParams.set("top_level", "true");
  if (includeArchived) searchParams.set("include_archived", "true");
  if (archivedOnly) searchParams.set("archived_only", "true");
  const qs = searchParams.toString();
  return request<Job[]>(`/jobs${qs ? `?${qs}` : ""}`);
}
```

### Step 9: Add React Query mutations
**File: `web/src/hooks/useStepwise.ts`**

Add `archiveJob`, `unarchiveJob`, `bulkArchive`, `bulkUnarchive` mutations following the existing pattern (mutationFn → invalidateAll → toast).

Update `useJobs` hook to accept archive filter params:
```typescript
export function useJobs(
  status?: string,
  topLevel: boolean = true,
  archivedOnly: boolean = false,
) {
  return useQuery({
    queryKey: ["jobs", status, topLevel, archivedOnly],
    queryFn: () => api.fetchJobs(status, topLevel, false, archivedOnly),
  });
}
```

Add mutations to the return block of `useStepwiseMutations()`.

### Step 10: Update JobList UI
**File: `web/src/components/jobs/JobList.tsx`**

**10a. Add archive filter toggle** — a new filter pill or toggle in the header bar (alongside the existing status/date filters). Three states: "Active" (default, hides archived), "Archived" (shows only archived), "All".

Implementation: Add `showArchived` state (persisted to URL query param `archived`), pass `archivedOnly` to `useJobs()`.

**10b. Add "Archive" to per-job dropdown** — in the `JobActions` component (line 190), add an "Archive" menu item for jobs in terminal states (completed/failed/cancelled). When viewing archived jobs, show "Unarchive" instead.

**10c. Add bulk sweep controls** — a dropdown button or popover near the "New Job" button area:
- "Archive all cancelled" → `bulkArchive({ status: "cancelled" })`
- "Archive completed > 7d" → `bulkArchive({ status: "completed", older_than: "7d" })`
- "Archive completed > 30d" → `bulkArchive({ status: "completed", older_than: "30d" })`

Use a `DropdownMenu` with these preset options. Show the count from `statusCounts` as hint text.

**10d. Visual treatment for archived jobs** (when viewing "All" or "Archived" mode):
- Reduced opacity (`opacity-60`) on the row
- Small "Archived" badge next to the status badge (muted color, e.g. `bg-zinc-500/15 text-zinc-400`)

### Step 11: Update `_serialize_job` in server
**File: `src/stepwise/server.py`**

Ensure `archived_at` is included in the serialized job response. Since `_serialize_job` likely calls `job.to_dict()`, this should flow through automatically after Step 1, but verify and add if the summary mode strips it.

---

## Testing Strategy

### Python unit tests

**New file: `tests/test_job_archive.py`**

```bash
uv run pytest tests/test_job_archive.py -v
```

Test cases:
1. **`test_archive_job`** — create job (COMPLETED), archive it, verify `archived_at` is set, `load_job()` still returns it
2. **`test_unarchive_job`** — archive then unarchive, verify `archived_at` is None
3. **`test_archive_running_rejected`** — create RUNNING job, attempt archive via API → 400
4. **`test_all_jobs_hides_archived`** — create 3 jobs, archive 1, call `all_jobs()` → returns 2
5. **`test_all_jobs_include_archived`** — same setup, `all_jobs(include_archived=True)` → returns 3
6. **`test_all_jobs_archived_only`** — same setup, `all_jobs(archived_only=True)` → returns 1
7. **`test_bulk_archive_by_status`** — create mix of COMPLETED/CANCELLED/RUNNING, bulk archive cancelled → only cancelled get archived
8. **`test_bulk_archive_by_status_older_than`** — create old + new completed jobs, archive with older_than → only old ones
9. **`test_bulk_archive_by_group`** — create group with mixed statuses, archive by group → only terminal jobs get archived
10. **`test_unarchive_all`** — archive several, unarchive all → all have `archived_at = None`
11. **`test_archive_preserves_job_data`** — archive + unarchive, verify all other fields unchanged
12. **`test_migration_adds_column`** — new `SQLiteStore(":memory:")` has `archived_at` column

### Server endpoint tests

**Extend: `tests/test_server.py`** (or new `tests/test_server_archive.py`)

```bash
uv run pytest tests/test_server_archive.py -v
```

Test cases:
1. `POST /api/jobs/{id}/archive` → 200, job excluded from `GET /api/jobs`
2. `POST /api/jobs/{id}/archive` on running job → 400
3. `POST /api/jobs/{id}/unarchive` → 200, job reappears in default listing
4. `POST /api/jobs/archive` with `status=cancelled` → archives correct set
5. `POST /api/jobs/archive` with `status=completed&older_than=7d` → age filter works
6. `GET /api/jobs?include_archived=true` → returns all
7. `GET /api/jobs?archived_only=true` → returns only archived

### CLI tests

```bash
uv run pytest tests/test_cli_jobs.py -v -k archive
```

Test `cmd_jobs_archive` and `cmd_jobs_unarchive` with store directly.

### Frontend tests

```bash
cd web && npm run test -- --run
```

Test cases (Vitest + testing-library):
1. JobList renders without archived jobs by default
2. Archive toggle switches to archived view
3. Archive button appears in dropdown for terminal jobs
4. Archive button does not appear for running jobs
5. Bulk archive button calls correct API

### Manual verification

```bash
# Start server
stepwise server start

# Create some test jobs, let them complete/cancel
stepwise run flows/test/FLOW.yaml --name "test-1"
stepwise run flows/test/FLOW.yaml --name "test-2"

# Archive via CLI
stepwise jobs archive --status completed
stepwise jobs                    # should not show archived
stepwise jobs --archived         # should show only archived

# Archive via web UI
# Open http://localhost:8340, verify:
# - Archived jobs hidden in job list
# - "Archive" in dropdown menu for completed jobs
# - Toggle to show archived
# - Bulk sweep buttons work
```

---

## File Change Summary

| File | Change |
|------|--------|
| `src/stepwise/models.py` | Add `archived_at` field to `Job`, update `to_dict()`/`from_dict()` |
| `src/stepwise/store.py` | Migration, `save_job()` columns, `_row_to_job()`, `archive_job()`, `unarchive_job()`, `archive_jobs_by_status()`, `archive_jobs_by_group()`, `unarchive_all()`, update `all_jobs()` |
| `src/stepwise/server.py` | 4 new endpoints (archive, unarchive, bulk archive, bulk unarchive), update `GET /api/jobs` params, `_parse_duration()` helper, `BulkArchiveRequest` model |
| `src/stepwise/cli.py` | `archive`/`unarchive` subcommands under `jobs`, `--archived`/`--all` flags on `jobs` list |
| `web/src/lib/types.ts` | Add `archived_at: string \| null` to `Job` |
| `web/src/lib/api.ts` | `archiveJob()`, `unarchiveJob()`, `bulkArchive()`, `bulkUnarchive()`, update `fetchJobs()` |
| `web/src/hooks/useStepwise.ts` | Archive/unarchive mutations, update `useJobs()` params |
| `web/src/components/jobs/JobList.tsx` | Archive toggle pill, archive/unarchive in dropdown, bulk sweep dropdown, archived visual treatment |
| `tests/test_job_archive.py` | New: store + model archive tests |
| `tests/test_server_archive.py` | New: endpoint integration tests |

**Estimated scope:** ~400-500 lines of Python, ~200 lines of TypeScript, ~200 lines of tests.
