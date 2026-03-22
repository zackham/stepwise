---
title: "Implementation Plan: E1 Job Metadata + Event Foundation"
date: "2026-03-21T00:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: E1 Job Metadata + Event Foundation

## Overview

Add a structured `metadata` JSON column to the jobs table with validated `sys`/`app` namespaces, standardize all event dispatch into a unified envelope format, and surface metadata through CLI, API, hooks, and webhooks. This is the foundation for session-aware routing, loop detection, and the extension architecture.

## Requirements

### R1: Job Metadata Schema

**Acceptance criteria:**
- Jobs have a `metadata` field with `{"sys": {...}, "app": {...}}` structure
- `sys` block: only known keys accepted (`origin`, `session_id`, `parent_job_id`, `root_job_id`, `depth`, `notify_url`, `created_by`), each type-validated (strings for all except `depth` which is integer)
- `app` block: any valid JSON, no structural validation
- Total serialized metadata <= 8KB (8192 bytes), rejected with `ValueError` if exceeded
- Unknown `sys` keys are rejected with `ValueError`
- Empty/missing `sys` or `app` keys default to `{}`

### R2: Database Storage

**Acceptance criteria:**
- `metadata TEXT DEFAULT '{}'` column exists on the `jobs` table
- Added via idempotent `ALTER TABLE` migration in `store.py:_migrate()` (same pattern as `notify_url`, `name` at lines 126-128)
- Older databases gain the column transparently on first access
- Metadata round-trips through `save_job()`/`load_job()` without loss
- `save_job()` serializes metadata via `_dumps()` (same as `config` at line 170)

### R3: Auto-Population

**Acceptance criteria:**
- `sys.depth`: 0 for top-level jobs; parent's `sys.depth + 1` for sub-jobs. Rejected with `ValueError` if > 10
- `sys.root_job_id`: set to own `job.id` for top-level jobs; inherited from parent's `sys.root_job_id` for sub-jobs
- Auto-populated fields override any user-provided values for those keys (depth and root_job_id are always engine-computed)

### R4: Immutability

**Acceptance criteria:**
- Metadata is set at `create_job()` time (`engine.py:123`) and never modified after
- No PATCH/UPDATE API endpoint for metadata
- Engine code never mutates `job.metadata` after the `save_job()` call in `create_job()` (line 153)
- `save_job()` upsert (line 138) updates the column on conflict but this is only triggered by engine operations that modify other fields (status, heartbeat), not metadata itself

### R5: Sub-Flow Inheritance

**Acceptance criteria:**
- Child jobs created via `_create_sub_job()` (`engine.py:2243`) inherit parent metadata with deep copy
- Child jobs created via for-each (`engine.py:1589`) inherit parent metadata with deep copy
- `sys.parent_job_id` is set to the parent's `job.id`
- `sys.root_job_id` and `sys.depth` are auto-populated per R3
- `app` block is inherited unchanged from parent

### R6: Event Envelope

**Acceptance criteria:**
- All dispatched events (hooks, webhooks) include the standardized envelope:
  ```json
  {
    "event": "step.suspended",
    "job_id": "job-abc",
    "step": "approve",
    "timestamp": "2026-03-21T20:15:00Z",
    "event_id": 42,
    "metadata": {"sys": {...}, "app": {...}},
    "data": {"step": "approve", "run_id": "run-xyz", "watch_mode": "external"}
  }
  ```
- `event_id` is the SQLite implicit rowid from the events table (monotonically increasing integer), captured via `cursor.lastrowid` after INSERT in `save_event()`
- `step` is promoted to top-level from `data` when present, while also remaining in `data` for backward compatibility
- Events are written to SQLite BEFORE dispatch to hooks/webhooks — this is already the case (`_emit()` calls `store.save_event()` at line 2440 before `fire_hook_for_event()` at line 2442)
- Metadata is NOT stored on each event row (it's on the job) — the envelope is constructed at dispatch time by loading the job

### R7: CLI `--meta` Flag

**Acceptance criteria:**
- `stepwise run <flow> --meta sys.origin=cli --meta app.project=stepwise` works
- Dot notation parsed into nested dict: `sys.origin=cli` → `{"sys": {"origin": "cli"}, "app": {}}`
- Multiple `--meta` flags merge into single metadata dict
- Values containing `=` are handled correctly (split on first `=` only)
- Invalid sys keys rejected with error message before job creation

### R8: CLI Display

**Acceptance criteria:**
- `stepwise status <job-id>` shows metadata block in formatted JSON output (both table and JSON modes)
- `stepwise jobs --meta sys.origin=cli` filters jobs by metadata values using SQLite `json_extract()`
- Default empty metadata `{"sys": {}, "app": {}}` is suppressed in table display (shown only if non-empty)

### R9: Hook Payload Enhancement

**Acceptance criteria:**
- Full event envelope written to a temp JSON file under the project's `.stepwise/tmp/` directory
- Temp file cleaned up in a `finally` block after `proc.communicate()` returns (or on timeout kill)
- `$STEPWISE_EVENT_FILE` env var set on the subprocess, pointing to the temp file absolute path
- `$STEPWISE_JOB_ID` env var set to the job ID
- `$STEPWISE_EVENT` env var set to the event type string (e.g., `step.suspended`)
- `$STEPWISE_SESSION_ID` env var set to `metadata.sys.session_id` if present, empty string otherwise
- Existing stdin JSON payload unchanged — same keys as before (`event`, `hook`, `job_id`, `timestamp`, `**event_data`, `fulfill_command`)
- `.stepwise/tmp/` directory created on demand (`mkdir(exist_ok=True)`) following the pattern in `project.py:80-84`

### R10: API Changes

**Acceptance criteria:**
- `POST /api/jobs` (`server.py:512`) accepts `metadata` field in `CreateJobRequest` (line 101)
- `GET /api/jobs/{id}` (`server.py:511`, `_serialize_job` at line 228) includes `metadata` in response
- `GET /api/jobs` (`server.py:504`) accepts query parameter for metadata filtering, passed to `store.all_jobs()` via new `meta_filters` parameter
- Webhook POST payloads (`hooks.py:fire_notify_webhook` at line 139) use standardized event envelope with metadata
- Backward compatibility: webhook payloads include existing top-level keys (`event`, `job_id`, `timestamp`, `context`) alongside the new nested `data` and `metadata` keys

## Assumptions

### A1: New column, not reuse of `config.metadata`

The existing `JobConfig.metadata` dict (`models.py:1252`) is engine-internal operational state used for `rerun_steps` (`engine.py:161`). The new `metadata` is a separate, user-facing field with structured `sys`/`app` namespaces stored in its own column. They coexist without conflict.

**Verification:** Read `models.py:1248-1267` — `JobConfig.metadata` is a catch-all dict inside the `config` JSON column, serialized at `store.py:170` as part of `job.config.to_dict()`. The spec requires a distinct top-level `metadata` column.

### A2: Event rowid is available via `cursor.lastrowid`

SQLite provides an implicit `rowid` on every table without `WITHOUT ROWID`. The `events` table definition (`store.py:83-90`) uses `id TEXT PRIMARY KEY` — this creates an alias-free table where the implicit rowid is separate from the text `id` column. `cursor.lastrowid` returns the rowid after INSERT.

**Verification:** Read `store.py:83-90` — standard `CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, ...)`, no `WITHOUT ROWID` clause. SQLite docs confirm `lastrowid` works for all standard tables.

### A3: `_emit()` is the single dispatch point for both engine classes

Both `Engine` and `AsyncEngine` use the same `_emit()` method (`engine.py:2431-2444`). `AsyncEngine` is defined at line 2501 as `class AsyncEngine(Engine)` and does not override `_emit()`. All 65+ emit call sites throughout `engine.py` use `self._emit()`.

**Verification:** Grepped `def _emit` in `engine.py` — single definition at line 2431. Grepped `class AsyncEngine` — defined at line 2501, inherits `Engine`. No `_emit` override in the AsyncEngine section (lines 2501-3100+).

### A4: `_LockedConnection.execute()` passes through cursor return values

`ThreadSafeStore` (`server.py:76-95`) wraps the sqlite3 connection with `_LockedConnection` (`server.py:40-74`). The `execute()` method at line 47-49 acquires the lock and returns `self._conn.execute(*args, **kwargs)` — the cursor object. When `save_event()` is modified to capture the cursor and read `cursor.lastrowid`, this works correctly through the proxy.

**Verification:** Read `server.py:47-49` — `execute()` returns the result of `self._conn.execute()`. Read `server.py:84-95` — `ThreadSafeStore.__init__` sets `self._conn = _LockedConnection(raw_conn, lock)` at line 94. All store methods inherited from `SQLiteStore` use `self._conn.execute()` which goes through the proxy.

### A5: Sub-job creation has exactly two paths

Sub-jobs are created in two places: `_create_sub_job()` for delegate/emit-flow (`engine.py:2243-2264`, passes `parent_job_id=parent_job.id` at line 2259) and the for-each loop (`engine.py:1589-1597`, passes `parent_job_id=job.id` at line 1594). Both currently inherit `job.config` but do not pass metadata. Both paths must be updated.

**Verification:** Grepped `parent_job_id=` in `engine.py` — two call sites: line 1594 (`parent_job_id=job.id` in for-each) and line 2259 (`parent_job_id=parent_job.id` in `_create_sub_job`). No other `create_job()` calls pass `parent_job_id`.

### A6: Temp file lifecycle is bounded to hook execution

Hook scripts run synchronously with a 30-second timeout (`hooks.py:77`). The temp event file must be created before subprocess launch and cleaned up in the same `finally` block that handles timeout kill (`hooks.py:85-91`). This matches the existing `cli_llm_client.py:60-76` pattern where `tempfile.mkdtemp()` is used with `shutil.rmtree(ignore_errors=True)` in a `finally` block.

**Verification:** Read `hooks.py:64-91` — `fire_hook()` uses `try/except/finally` structure with `proc.communicate(timeout=30)`. Read `cli_llm_client.py:60-76` — temp dir cleanup pattern: `try: ... finally: shutil.rmtree(tmpdir, ignore_errors=True)`.

## Out of Scope

- **WebSocket stream filtering by metadata** (E2) — WebSocket broadcasts (`server.py:244`) currently send to all `_ws_clients` without filtering; metadata-based subscription is a separate feature
- **CLI tail/logs commands** (E3) — event streaming CLI commands depend on E1's event envelope but are a separate deliverable
- **Extension discovery/registration** (E4) — the extension hook architecture uses metadata but discovery is separate
- **Vita webhook routing changes** (P1) — the current `fire_notify_webhook()` improvements in this plan are structural, not routing-specific
- **Changes to step_events table** — per-run step_events (`store.py:92-98`) are a different event system; this plan only touches job-level events
- **Metadata on StepRun objects** — metadata lives on Job only; StepRun inherits context via its parent job
- **Migration of existing `config.metadata` to new `metadata` field** — `config.metadata` continues to be used for `rerun_steps`; no migration needed
- **WebSocket envelope changes** — WS broadcasts (`server.py:138-153`) currently send `{"type": "tick", "changed_jobs": [...]}` which triggers client-side refetch; adding metadata to WS messages is out of scope

## Architecture

### Data Model

Add `metadata: dict` field to the `Job` dataclass (`models.py:1273`) alongside existing fields. Default: `{"sys": {}, "app": {}}`. Stored in a new `metadata TEXT` column in the `jobs` table (separate from the `config TEXT` column that holds `JobConfig.metadata`).

Validation is a standalone function `validate_job_metadata()` in `models.py` (not a dataclass method) — follows the pattern of keeping models as pure data containers while validation logic is in free functions (similar to `WorkflowDefinition.validate()` at `models.py` which is a method but returns errors rather than raising).

```
Job.metadata        ← new top-level field, own SQLite column, user-facing sys/app structure
Job.config.metadata ← existing dict in config column, engine-internal (rerun_steps)
```

### Validation Flow

```
User input (CLI --meta / API metadata field)
  → parse_meta_flags() [cli.py]              — dot notation → nested dict
  → engine.create_job(metadata=...) [engine.py:123]
    → validate_job_metadata() [models.py]    — sys key whitelist, types, 8KB size
    → auto-populate sys.depth, sys.root_job_id
    → Job(..., metadata=metadata) [engine.py:141]
    → store.save_job(job) [engine.py:153]    — persist to metadata column
```

### Event Dispatch Flow (updated `_emit()` at `engine.py:2431`)

```
engine._emit(job_id, event_type, data)
  → store.save_event(event)                  — INSERT, returns rowid (int)
  → store.load_job(job_id)                   — get job.metadata (single load, reused)
  → build_event_envelope(...)                — construct standardized envelope dict
  → fire_hook_for_event(event_type, event_data, job_id, project_dir, envelope)
    → fire_hook(hook_name, stdin_payload, project_dir, envelope)
      → write envelope to temp file in .stepwise/tmp/
      → subprocess.Popen with env vars + stdin
      → finally: delete temp file
  → _fire_notify(job_id, event_type, event_data, envelope)
    → fire_notify_webhook(..., envelope)     — HTTP POST with full envelope
```

**Key design choice:** The job is loaded once in `_emit()` to get metadata, then passed to both hook and webhook dispatch. Currently `_fire_notify()` loads the job independently at line 2449 — this will be consolidated into the `_emit()` method to avoid a duplicate `load_job()` call.

### Temp File Lifecycle

```
fire_hook() called with envelope dict
  → .stepwise/tmp/ created if missing (mkdir exist_ok=True)
  → tempfile.NamedTemporaryFile(dir=tmp_dir, suffix='.json', delete=False)
  → write JSON envelope to temp file, flush
  → set env STEPWISE_EVENT_FILE = temp file path
  → Popen(hook_script, stdin=payload, env=env_with_vars)
  → try: proc.communicate(timeout=30)
  → except TimeoutExpired: proc.kill(); proc.communicate()
  → finally: os.unlink(temp_file_path) with ignore_errors
```

This ensures no temp file accumulation even under high-volume event dispatch or hook timeouts.

### File Impact Summary

| File | Lines affected | Changes |
|---|---|---|
| `models.py` | ~1252, 1273-1336 | Add `VALID_SYS_KEYS`, `validate_job_metadata()`, `metadata` field on Job, update `to_dict`/`from_dict` |
| `store.py` | ~120-133, 137-181, 191-210, 234-248 | Add migration entry, update `save_job` INSERT/UPSERT, update `_row_to_job`, add `meta_filters` to `all_jobs` |
| `engine.py` | ~123-154, 1589-1597, 2243-2264, 2431-2453 | Accept `metadata` in `create_job()`, propagate in sub-job paths, update `_emit` for envelope |
| `hooks.py` | ~37-96, 98-136, 139-173 | Add `build_event_envelope()`, update `fire_hook` for temp file + env vars, update `fire_hook_for_event` signature, update `fire_notify_webhook` |
| `cli.py` | ~1549-1704, 1930-1988, 1991-2053, 3404-3430 | Add `parse_meta_flags()`, `--meta` arg, thread through `cmd_run`, display in `cmd_status`, filter in `cmd_jobs` |
| `runner.py` | ~380-397, 796-808, 1427-1438, 498-510, 926-936, 1494-1504 | Accept `metadata` param in `run_flow`/`run_wait`/`run_async`, pass to `create_job` |
| `server.py` | ~101-110, 228-241, 504-509, 512-547 | Add `metadata` to `CreateJobRequest`, update `_serialize_job`, add query filter to `list_jobs` |
| `api_client.py` | ~78-93 | Accept and pass `metadata` in `create_job` |

## Implementation Steps

### Step 1: Metadata Validation Function (models.py) — ~20min

**Depends on:** nothing (leaf step)

Add to `models.py` near the `JobConfig` definition (~line 1248):

1. Define `VALID_SYS_KEYS` constant dict mapping key names to expected Python types:
   `{"origin": str, "session_id": str, "parent_job_id": str, "root_job_id": str, "depth": int, "notify_url": str, "created_by": str}`

2. Define `validate_job_metadata(metadata: dict) -> None`:
   - Ensure `metadata` is a dict; raise `ValueError` if not
   - Default missing `sys`/`app` to `{}` (mutate in place)
   - Check `json.dumps(metadata)` length <= 8192; raise `ValueError` with size info if exceeded
   - For each key in `metadata["sys"]`: reject if not in `VALID_SYS_KEYS` (raise `ValueError` listing the unknown key); check `isinstance(value, expected_type)` (raise `ValueError` with key name, expected type, actual type)
   - `metadata["app"]`: no validation beyond being a dict

**Exit criteria:** `validate_job_metadata()` is callable with correct raise behavior. No other files changed.

### Step 2: Job Model Metadata Field (models.py) — ~15min

**Depends on:** Step 1 (validation function must exist for tests, but no code dependency)

Update `Job` dataclass at `models.py:1273`:

1. Add field after `notify_context` (line 1291): `metadata: dict = field(default_factory=lambda: {"sys": {}, "app": {}})`

2. Update `to_dict()` (line 1293): add `"metadata": self.metadata` to the returned dict (after the `notify_context` conditional block at lines 1311-1313)

3. Update `from_dict()` (line 1316): add `metadata=d.get("metadata", {"sys": {}, "app": {}})` to the constructor call

**Exit criteria:** `Job` instances have a `metadata` field; `to_dict()`/`from_dict()` round-trip it.

### Step 3: Database Migration + Store Read/Write (store.py) — ~30min

**Depends on:** Step 2 (Job model must have `metadata` field)

1. In `_migrate()` (line 122-133): add `("metadata", "TEXT", "'{}'")` to the migration column list after `("name", "TEXT", None)` at line 128

2. In `save_job()` (line 137-181):
   - Add `metadata` to the INSERT column list (line 140-142) and VALUES placeholder list (line 143)
   - Add `metadata = excluded.metadata` to the ON CONFLICT UPDATE SET clause (after line 159)
   - Add `_dumps(job.metadata)` to the parameter tuple (after `job.name` at line 178)

3. In `_row_to_job()` (line 191-210):
   - Add: `metadata=json.loads(row["metadata"]) if "metadata" in row.keys() and row["metadata"] else {"sys": {}, "app": {}}`
   - Pattern matches existing defensive reads like `notify_url` at line 208

4. In `all_jobs()` (line 234-248):
   - Add `meta_filters: dict[str, str] | None = None` parameter
   - When provided, for each `key, value` in meta_filters: add clause `json_extract(metadata, ?) = ?` with params `(f"$.{key}", value)` — e.g., `json_extract(metadata, '$.sys.origin') = 'cli'`
   - Insert filter clauses into the existing `clauses`/`params` lists (line 235-237)

**Exit criteria:** Jobs with metadata survive save/load cycle. `all_jobs(meta_filters={"sys.origin": "cli"})` returns filtered results.

### Step 4: Engine create_job Metadata + Auto-Population (engine.py) — ~30min

**Depends on:** Steps 1-3 (validation function, Job model field, store support)

1. Add `metadata: dict | None = None` parameter to `create_job()` at line 123 (after `name: str | None = None`)

2. After `job_id = _gen_id("job")` (line 138), add metadata initialization block:
   - `metadata = copy.deepcopy(metadata) if metadata else {"sys": {}, "app": {}}`
   - `metadata.setdefault("sys", {})` / `metadata.setdefault("app", {})`
   - Call `validate_job_metadata(metadata)` (import from models)
   - Auto-populate `sys.root_job_id`: if `metadata["sys"].get("parent_job_id")`, look up parent via `self.store.load_job()`, inherit `parent.metadata["sys"].get("root_job_id", parent.id)`; else set to `job_id`
   - Auto-populate `sys.depth`: if parent_job_id in sys, compute `parent.metadata["sys"].get("depth", 0) + 1`; else set to `0`. Raise `ValueError` if > 10.
   - Pass `metadata=metadata` to `Job(...)` constructor at line 141

3. Add `import copy` at top of file if not already present

**Exit criteria:** `create_job(metadata={"sys": {"origin": "cli"}, "app": {}})` works. Auto-populated depth/root_job_id are correct. Depth > 10 raises.

### Step 5: Sub-Flow Metadata Inheritance (engine.py) — ~20min

**Depends on:** Step 4 (create_job must accept metadata)

1. In `_create_sub_job()` (line 2243-2264):
   - After the depth check (line 2248), add: `sub_meta = copy.deepcopy(parent_job.metadata)` and `sub_meta["sys"]["parent_job_id"] = parent_job.id`
   - Add `metadata=sub_meta` to the `self.create_job()` call at line 2254
   - `create_job()` will auto-populate depth and root_job_id via Step 4 logic

2. In for-each sub-job creation (~line 1589):
   - Before the loop, add: `parent_meta = copy.deepcopy(job.metadata)` and `parent_meta["sys"]["parent_job_id"] = job.id`
   - Add `metadata=copy.deepcopy(parent_meta)` to each `self.create_job()` call at line 1589
   - Each sub-job gets its own deep copy to prevent cross-contamination

**Exit criteria:** Child jobs have metadata with correct `sys.parent_job_id`, inherited `sys.root_job_id`, incremented `sys.depth`, and full `app` block from parent.

### Step 6: save_event Returns Rowid (store.py) — ~10min

**Depends on:** nothing (independent leaf step, can run in parallel with Steps 1-5)

1. In `save_event()` (line 520-533):
   - Change `self._conn.execute(...)` to `cursor = self._conn.execute(...)`
   - Add `rowid = cursor.lastrowid` after the execute call
   - Change return type from `None` to `int`: `def save_event(self, event: Event) -> int:`
   - Return `rowid` before or after `self._conn.commit()` (lastrowid is valid after execute, before commit)

2. Verify `_LockedConnection.execute()` (`server.py:47-49`) returns the cursor — confirmed: `return self._conn.execute(*args, **kwargs)`.

**Exit criteria:** `save_event()` returns an integer rowid. All existing callers (just `_emit()`) currently ignore the return value, so no breakage.

### Step 7: Event Envelope Builder (hooks.py) — ~20min

**Depends on:** nothing (pure function, no dependencies)

Add `build_event_envelope()` function to `hooks.py`:

- Parameters: `event_type: str, event_data: dict, job_id: str, event_id: int, metadata: dict, timestamp: str`
- Returns dict with keys: `event`, `job_id`, `timestamp`, `event_id`, `metadata`, `data`
- Promote `step` from `event_data` to top-level if present (also keep in `data` for backward compat)
- `data` contains the full `event_data` dict

**Exit criteria:** `build_event_envelope("step.suspended", {"step": "approve", "run_id": "run-1"}, "job-1", 42, {"sys": {}, "app": {}}, "2026-03-21T00:00:00Z")` returns correct envelope dict.

### Step 8: Update _emit() for Envelope Dispatch (engine.py) — ~25min

**Depends on:** Steps 6 (rowid from save_event) and 7 (envelope builder)

Update `_emit()` at `engine.py:2431-2444`:

1. Capture rowid: `rowid = self.store.save_event(event)` (line 2440)

2. Load job for metadata (consolidate with `_fire_notify` which already loads the job at line 2449):
   ```python
   try:
       job = self.store.load_job(job_id)
       job_metadata = job.metadata
   except KeyError:
       job_metadata = {"sys": {}, "app": {}}
       job = None
   ```

3. Build envelope: `envelope = build_event_envelope(event_type, event.data, job_id, rowid, job_metadata, event.timestamp.isoformat())`

4. Update `fire_hook_for_event()` call to pass `envelope`

5. Consolidate `_fire_notify()` inline or pass `job` directly to avoid duplicate `load_job()`:
   - If `job` is not None and `job.notify_url`: call `fire_notify_webhook()` with envelope

**Exit criteria:** Every `_emit()` call constructs and dispatches the full event envelope. `_fire_notify()` no longer does its own `load_job()` call.

### Step 9: Hook Temp File + Env Vars (hooks.py) — ~30min

**Depends on:** Steps 7-8 (envelope is passed to hook functions)

1. Update `fire_hook_for_event()` signature (line 98):
   - Add `envelope: dict | None = None` parameter
   - Pass `envelope` through to `fire_hook()` call at line 136

2. Update `fire_hook()` (line 37-96):
   - Add `envelope: dict | None = None` parameter
   - Before `Popen`, if `envelope` is provided:
     - Create `.stepwise/tmp/` dir: `tmp_dir = project_dir / "tmp"; tmp_dir.mkdir(exist_ok=True)`
     - Write envelope: `import tempfile; fd, tmp_path = tempfile.mkstemp(dir=str(tmp_dir), suffix=".json"); os.write(fd, json.dumps(envelope, default=str).encode()); os.close(fd)`
   - Build env dict for subprocess:
     ```python
     env = os.environ.copy()
     env["STEPWISE_JOB_ID"] = envelope.get("job_id", "")
     env["STEPWISE_EVENT"] = envelope.get("event", "")
     env["STEPWISE_SESSION_ID"] = envelope.get("metadata", {}).get("sys", {}).get("session_id", "")
     env["STEPWISE_EVENT_FILE"] = tmp_path
     ```
   - Pass `env=env` to `Popen` call (line 66)
   - In the `finally` block (after `communicate` at line 76-91), add: `if tmp_path: os.unlink(tmp_path)` wrapped in `try/except OSError: pass`
   - Keep existing stdin payload construction unchanged (line 62: `payload_json = json.dumps(payload, default=str)`)

3. Existing stdin payload in `fire_hook_for_event()` (lines 122-134) is unchanged — backward compatible.

**Exit criteria:** Hook scripts receive stdin (old format) + env vars + temp file (new envelope). Temp file is always cleaned up.

### Step 10: Webhook Envelope (hooks.py) — ~15min

**Depends on:** Step 7 (envelope builder)

Update `fire_notify_webhook()` (line 139-173):

1. Add `envelope: dict | None = None` parameter

2. When `envelope` is provided, use it as the base payload instead of constructing a flat dict:
   - Start with `payload = dict(envelope)` (copy)
   - Merge backward-compat top-level keys from `event_data` (keep existing `**event_data` spread for consumers expecting flat keys)
   - Add `context` key if `notify_context` is provided
   - This gives consumers both the old flat format and the new structured format in the same payload

3. When `envelope` is None (fallback for any call sites that don't have it), keep existing behavior

**Exit criteria:** Webhook payloads contain `event_id`, `metadata`, `data` alongside existing flat keys. Old consumers reading `payload["event"]` and `payload["job_id"]` still work.

### Step 11: CLI --meta Flag Parsing (cli.py) — ~20min

**Depends on:** nothing (pure parsing function)

1. Add `parse_meta_flags(meta_args: list[str]) -> dict` function:
   - For each arg, split on first `=` to get `key, value`
   - Split `key` on `.` to get path segments (e.g., `["sys", "origin"]`)
   - First segment must be `sys` or `app`; raise `SystemExit` with error if not
   - Navigate/create nested dict structure and set leaf value
   - Return `{"sys": {...}, "app": {...}}` with defaults for missing top-level keys

2. Add `--meta` argument to run subparser (~line 3430):
   `p_run.add_argument("--meta", action="append", default=[], dest="meta", metavar="KEY=VALUE", help="Set job metadata (dot notation: sys.origin=cli, app.project=foo)")`

**Exit criteria:** `parse_meta_flags(["sys.origin=cli", "app.project=stepwise"])` returns `{"sys": {"origin": "cli"}, "app": {"project": "stepwise"}}`.

### Step 12: Thread Metadata Through CLI → Runner → Engine (cli.py, runner.py) — ~30min

**Depends on:** Steps 4 (engine accepts metadata) and 11 (parse_meta_flags)

1. In `cmd_run()` (`cli.py:1549`):
   - After input parsing (~line 1638), add: `metadata = parse_meta_flags(args.meta) if args.meta else None`
   - Pass `metadata=metadata` to all three call sites:
     - `run_async()` call at line 1644
     - `run_wait()` call at line 1668
     - `run_flow()` call at line 1690

2. In `runner.py`, update function signatures:
   - `run_flow()` (line 380): add `metadata: dict | None = None` parameter
   - `run_wait()` (line 796): add `metadata: dict | None = None` parameter
   - `run_async()` (line 1427): add `metadata: dict | None = None` parameter

3. In each function's `engine.create_job()` call:
   - `run_flow()` (~line 498-504): add `metadata=metadata`
   - `run_wait()` (~line 926-932): add `metadata=metadata`
   - `run_async()` (~line 1494-1500): add `metadata=metadata`

**Exit criteria:** `stepwise run myflow --meta sys.origin=cli` creates a job with correct metadata.

### Step 13: CLI Display + Filter (cli.py) — ~20min

**Depends on:** Steps 3 (store meta_filters) and 11 (parse_meta_flags)

1. In `cmd_status()` (~line 1991):
   - After the job info block (~line 2032), add metadata display:
   - If `job.metadata != {"sys": {}, "app": {}}` (non-default): print formatted JSON via `json.dumps(job.metadata, indent=2)`
   - In JSON output mode: `data["metadata"] = job.metadata` (if using `resolved_flow_status`)

2. In `cmd_jobs()` (~line 1930):
   - Add `--meta` argument to the `jobs` subparser: `p_jobs.add_argument("--meta", action="append", default=[], dest="meta")`
   - After status_filter parsing (~line 1960), add: `meta_filters = parse_meta_filter_flags(args.meta)` — reuse parse logic from Step 11 but extract as `{"sys.origin": "cli"}` format for `json_extract`
   - Pass `meta_filters=meta_filters` to `store.all_jobs()` at ~line 1965

**Exit criteria:** `stepwise status <job-id>` shows metadata. `stepwise jobs --meta sys.origin=cli` filters correctly.

### Step 14: Server API (server.py, api_client.py) — ~20min

**Depends on:** Steps 3 (store meta_filters) and 4 (engine accepts metadata)

1. In `CreateJobRequest` (`server.py:101-110`): add `metadata: dict | None = None`

2. In `POST /api/jobs` handler (`server.py:512-547`): pass `metadata=req.metadata` to `engine.create_job()`

3. In `_serialize_job()` (`server.py:228-241`):
   - Summary mode (line 229-240): add `"metadata": job.metadata`
   - Full mode (line 241): already uses `job.to_dict()` which includes metadata after Step 2

4. In `GET /api/jobs` (`server.py:504-509`):
   - Add `request: Request` parameter (from `starlette.requests`)
   - Extract `meta.*` query params: `meta_filters = {k[5:]: v for k, v in request.query_params.items() if k.startswith("meta.")}`
   - Pass to `engine.store.all_jobs(..., meta_filters=meta_filters or None)`

5. In `api_client.py` (`create_job` at line 78):
   - Add `metadata: dict | None = None` parameter
   - If metadata: `body["metadata"] = metadata`

**Exit criteria:** API accepts, persists, returns, and filters by metadata.

### Step 15: Tests (tests/test_metadata.py) — ~1hr

**Depends on:** Steps 1-14 (all implementation complete)

Create `tests/test_metadata.py`. Uses `async_engine` fixture from `conftest.py`, `register_step_fn`, and `run_job_sync` helper. Hook tests follow the pattern from `tests/test_agent_ergonomics.py:646-755`.

See Testing Strategy section below for full test list.

**Exit criteria:** All new tests pass. `uv run pytest tests/ -v` shows no regressions.

## Testing Strategy

### New test file: `tests/test_metadata.py`

All engine tests use the `async_engine` and `store` fixtures from `conftest.py`. Hook tests use `tmp_path` following the pattern in `test_agent_ergonomics.py:646-755`.

#### Validation tests (unit, no engine needed)

```python
class TestMetadataValidation:
    def test_valid_metadata_accepted(self):
        """Well-formed metadata with known sys keys passes validation."""
        from stepwise.models import validate_job_metadata
        meta = {"sys": {"origin": "cli", "session_id": "abc"}, "app": {"foo": "bar"}}
        validate_job_metadata(meta)  # should not raise

    def test_unknown_sys_key_rejected(self):
        """Unknown keys in sys block raise ValueError."""
        meta = {"sys": {"unknown_key": "x"}, "app": {}}
        with pytest.raises(ValueError, match="unknown_key"):
            validate_job_metadata(meta)

    def test_sys_key_wrong_type_rejected(self):
        """sys.depth must be int, not string."""
        meta = {"sys": {"depth": "notint"}, "app": {}}
        with pytest.raises(ValueError, match="depth"):
            validate_job_metadata(meta)

    def test_metadata_size_limit_enforced(self):
        """Metadata > 8KB is rejected."""
        meta = {"sys": {}, "app": {"big": "x" * 9000}}
        with pytest.raises(ValueError, match="8192"):
            validate_job_metadata(meta)

    def test_app_block_accepts_arbitrary_json(self):
        """app block accepts nested structures without validation."""
        meta = {"sys": {}, "app": {"nested": {"list": [1, 2, 3], "deep": {"a": True}}}}
        validate_job_metadata(meta)  # should not raise

    def test_missing_sys_app_keys_defaulted(self):
        """Missing sys/app keys are auto-filled with {}."""
        meta = {}
        validate_job_metadata(meta)
        assert meta == {"sys": {}, "app": {}}
```

#### Auto-population tests (engine integration)

```python
class TestMetadataAutoPopulation:
    def test_depth_zero_for_top_level(self, async_engine):
        """Top-level job gets sys.depth = 0."""
        register_step_fn("noop", lambda inputs: {"out": 1})
        wf = WorkflowDefinition(steps={"s": StepDefinition(...)})
        job = async_engine.create_job("test", wf, metadata={"sys": {"origin": "cli"}, "app": {}})
        assert job.metadata["sys"]["depth"] == 0

    def test_root_job_id_self_for_top_level(self, async_engine):
        """Top-level job gets sys.root_job_id = own job.id."""
        job = async_engine.create_job("test", wf)
        assert job.metadata["sys"]["root_job_id"] == job.id

    def test_depth_increments_for_sub_job(self, async_engine):
        """Sub-job depth = parent depth + 1."""
        parent = async_engine.create_job("parent", wf, metadata={"sys": {}, "app": {}})
        child = async_engine.create_job("child", wf, metadata={"sys": {"parent_job_id": parent.id}, "app": {}})
        assert child.metadata["sys"]["depth"] == 1

    def test_root_job_id_inherited(self, async_engine):
        """Sub-job inherits root_job_id from parent."""
        parent = async_engine.create_job("parent", wf)
        child = async_engine.create_job("child", wf, metadata={"sys": {"parent_job_id": parent.id}, "app": {}})
        assert child.metadata["sys"]["root_job_id"] == parent.id

    def test_depth_exceeds_10_rejected(self, async_engine):
        """Depth > 10 raises ValueError for loop prevention."""
        # Create a chain of jobs, set parent's depth to 10 manually
        parent = async_engine.create_job("parent", wf, metadata={"sys": {"depth": 10}, "app": {}})
        # Override depth (normally auto-populated, but we force it for the test via direct store manipulation)
        parent.metadata["sys"]["depth"] = 10
        async_engine.store.save_job(parent)
        with pytest.raises(ValueError, match="depth"):
            async_engine.create_job("child", wf, metadata={"sys": {"parent_job_id": parent.id}, "app": {}})
```

#### Immutability test

```python
    def test_metadata_unchanged_after_lifecycle(self, async_engine):
        """Metadata is not modified by job lifecycle operations."""
        register_step_fn("noop", lambda inputs: {"out": 1})
        wf = WorkflowDefinition(steps={"s": StepDefinition(name="s", executor=ExecutorRef(type="callable", config={"fn_name": "noop"}), outputs=["out"])})
        original_meta = {"sys": {"origin": "cli", "session_id": "sess-1"}, "app": {"tag": "test"}}
        job = async_engine.create_job("test", wf, metadata=original_meta)
        result = run_job_sync(async_engine, job.id)
        reloaded = async_engine.store.load_job(job.id)
        # sys.depth and sys.root_job_id are auto-populated, but other fields unchanged
        assert reloaded.metadata["sys"]["origin"] == "cli"
        assert reloaded.metadata["sys"]["session_id"] == "sess-1"
        assert reloaded.metadata["app"] == {"tag": "test"}
```

#### Sub-flow inheritance tests

```python
class TestSubFlowMetadataInheritance:
    def test_sub_job_inherits_app_metadata(self, async_engine):
        """App metadata propagated from parent to child via _create_sub_job."""
        # Use emit_flow or delegate pattern to trigger _create_sub_job
        # Verify child job's metadata["app"] matches parent's

    def test_sub_job_sys_fields_updated(self, async_engine):
        """Child sys.parent_job_id, depth, root_job_id are correctly set."""
        # Create parent with metadata, trigger sub-job, verify fields
```

#### Store filter tests

```python
class TestMetadataStoreFilters:
    def test_all_jobs_meta_filter(self, store):
        """meta_filters returns only jobs matching the filter."""
        # Save two jobs: one with sys.origin=cli, one with sys.origin=api
        # all_jobs(meta_filters={"sys.origin": "cli"}) returns only the first

    def test_all_jobs_meta_filter_no_match(self, store):
        """meta_filters with no matches returns empty list."""
```

#### CLI parsing tests (unit, no engine)

```python
class TestParseMetaFlags:
    def test_dot_notation_parsing(self):
        result = parse_meta_flags(["sys.origin=cli", "app.project=stepwise"])
        assert result == {"sys": {"origin": "cli"}, "app": {"project": "stepwise"}}

    def test_nested_keys(self):
        result = parse_meta_flags(["sys.session_id=abc-123"])
        assert result == {"sys": {"session_id": "abc-123"}, "app": {}}

    def test_value_with_equals_sign(self):
        result = parse_meta_flags(["app.query=a=b"])
        assert result == {"sys": {}, "app": {"query": "a=b"}}

    def test_empty_list(self):
        result = parse_meta_flags([])
        assert result == {"sys": {}, "app": {}}

    def test_invalid_top_level_key_rejected(self):
        with pytest.raises(SystemExit):
            parse_meta_flags(["invalid.key=val"])
```

#### Hook payload tests (following `test_agent_ergonomics.py:646-755` pattern)

```python
class TestHookEventEnvelope:
    def test_hook_receives_event_file(self, tmp_path):
        """Hook subprocess receives STEPWISE_EVENT_FILE env var pointing to temp JSON."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        hooks_dir = dot_dir / "hooks"
        hooks_dir.mkdir()
        # Hook script that copies $STEPWISE_EVENT_FILE content to output
        output_file = tmp_path / "envelope.json"
        hook = hooks_dir / "on-suspend"
        hook.write_text(f'#!/bin/sh\ncp "$STEPWISE_EVENT_FILE" {output_file}\n')
        hook.chmod(0o755)
        envelope = {"event": "step.suspended", "job_id": "job-1", "event_id": 42, "metadata": {"sys": {}, "app": {}}, "data": {}}
        fire_hook("suspend", {"event": "step.suspended"}, dot_dir, envelope=envelope)
        assert output_file.exists()
        written = json.loads(output_file.read_text())
        assert written["event_id"] == 42
        assert "metadata" in written

    def test_hook_env_vars_set(self, tmp_path):
        """STEPWISE_JOB_ID, STEPWISE_EVENT, STEPWISE_SESSION_ID env vars are set."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        hooks_dir = dot_dir / "hooks"
        hooks_dir.mkdir()
        output_file = tmp_path / "env_vars.txt"
        hook = hooks_dir / "on-suspend"
        hook.write_text(f'#!/bin/sh\necho "$STEPWISE_JOB_ID|$STEPWISE_EVENT|$STEPWISE_SESSION_ID" > {output_file}\n')
        hook.chmod(0o755)
        envelope = {"event": "step.suspended", "job_id": "job-abc", "event_id": 1,
                    "metadata": {"sys": {"session_id": "sess-xyz"}, "app": {}}, "data": {}}
        fire_hook("suspend", {}, dot_dir, envelope=envelope)
        content = output_file.read_text().strip()
        assert content == "job-abc|step.suspended|sess-xyz"

    def test_hook_stdin_backward_compat(self, tmp_path):
        """Stdin still receives the old-format payload alongside new env vars."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        hooks_dir = dot_dir / "hooks"
        hooks_dir.mkdir()
        output_file = tmp_path / "stdin_payload.json"
        hook = hooks_dir / "on-suspend"
        hook.write_text(f'#!/bin/sh\ncat > {output_file}\n')
        hook.chmod(0o755)
        payload = {"event": "step.suspended", "hook": "suspend", "job_id": "job-1", "step": "review"}
        fire_hook("suspend", payload, dot_dir, envelope={"event": "step.suspended"})
        written = json.loads(output_file.read_text())
        assert written["event"] == "step.suspended"
        assert written["hook"] == "suspend"  # old-format field

    def test_temp_file_cleaned_up_after_hook(self, tmp_path):
        """Temp event file is deleted after hook completes."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        (dot_dir / "hooks").mkdir()
        tmp_dir = dot_dir / "tmp"
        hook = dot_dir / "hooks" / "on-complete"
        hook.write_text("#!/bin/sh\ntrue\n")
        hook.chmod(0o755)
        envelope = {"event": "job.completed", "job_id": "job-1", "event_id": 1, "metadata": {"sys": {}, "app": {}}, "data": {}}
        fire_hook("complete", {}, dot_dir, envelope=envelope)
        # tmp dir may exist but should contain no leftover files
        if tmp_dir.exists():
            assert len(list(tmp_dir.iterdir())) == 0

    def test_temp_file_cleaned_up_on_hook_failure(self, tmp_path):
        """Temp event file is deleted even when hook script fails."""
        dot_dir = tmp_path / ".stepwise"
        dot_dir.mkdir()
        (dot_dir / "hooks").mkdir()
        (dot_dir / "logs").mkdir()
        tmp_dir = dot_dir / "tmp"
        hook = dot_dir / "hooks" / "on-fail"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        envelope = {"event": "job.failed", "job_id": "job-1", "event_id": 1, "metadata": {"sys": {}, "app": {}}, "data": {}}
        fire_hook("fail", {}, dot_dir, envelope=envelope)
        if tmp_dir.exists():
            assert len(list(tmp_dir.iterdir())) == 0
```

#### Event envelope tests

```python
class TestEventEnvelope:
    def test_envelope_has_all_required_fields(self):
        """build_event_envelope returns all spec-required fields."""
        envelope = build_event_envelope("step.completed", {"step": "s1", "run_id": "run-1"}, "job-1", 42, {"sys": {}, "app": {}}, "2026-03-21T00:00:00Z")
        assert envelope["event"] == "step.completed"
        assert envelope["job_id"] == "job-1"
        assert envelope["event_id"] == 42
        assert isinstance(envelope["event_id"], int)
        assert envelope["metadata"] == {"sys": {}, "app": {}}
        assert envelope["data"]["step"] == "s1"
        assert envelope["timestamp"] == "2026-03-21T00:00:00Z"

    def test_step_promoted_to_top_level(self):
        """step field from data is promoted to top-level."""
        envelope = build_event_envelope("step.suspended", {"step": "approve"}, "job-1", 1, {"sys": {}, "app": {}}, "t")
        assert envelope["step"] == "approve"
        assert envelope["data"]["step"] == "approve"  # also in data

    def test_event_id_monotonic(self, store):
        """Sequential save_event calls produce increasing rowids."""
        e1 = Event(id="evt-1", job_id="job-1", timestamp=_now(), type="step.started", data={})
        e2 = Event(id="evt-2", job_id="job-1", timestamp=_now(), type="step.completed", data={})
        r1 = store.save_event(e1)
        r2 = store.save_event(e2)
        assert isinstance(r1, int)
        assert isinstance(r2, int)
        assert r2 > r1
```

### Run commands

```bash
uv run pytest tests/test_metadata.py -v              # new tests only
uv run pytest tests/test_metadata.py -k "Validation"  # just validation tests
uv run pytest tests/test_metadata.py -k "Hook"        # just hook tests
uv run pytest tests/ -v                                # full regression suite
cd web && npm run test                                 # frontend (unaffected)
```

## Risks & Mitigations

### R1: `json_extract` compatibility

SQLite's `json_extract()` requires SQLite >= 3.9.0 (2015). Python 3.11+ ships with SQLite 3.39+; Python 3.10 ships 3.37+.

**Mitigation:** All supported Python versions include compatible SQLite. Add a startup check in `_migrate()` logging a warning if SQLite version < 3.9.0 (won't happen in practice). If needed, fall back to `SELECT * FROM jobs` and filter in Python.

### R2: Two `metadata` concepts on the same object

`job.config.metadata` (engine-internal, for `rerun_steps` at `engine.py:161`) and `job.metadata` (user-facing, `sys`/`app` structure) coexist on the same Job object.

**Mitigation:** Names are distinct (`config.metadata` vs `metadata`). `config.metadata` is only accessed in `start_job()` at line 161 for `rerun_steps`. Consider renaming to `config._engine_state` in a future cleanup commit. Document the distinction in CLAUDE.md.

### R3: Breaking webhook payload format

Webhook consumers currently expect flat payloads (`{"event": "...", "job_id": "...", **event_data}` at `hooks.py:157-162`). The new envelope adds `event_id`, `metadata`, and nests original data under `data`.

**Mitigation:** New envelope includes all existing top-level keys via `**event_data` spread. The `data`, `metadata`, and `event_id` keys are additive — they don't replace existing keys. Consumers reading `payload["event"]` or `payload["job_id"]` see no change. New consumers can use `payload["data"]` and `payload["metadata"]` for structured access.

### R4: Temp file accumulation under crash conditions

If the stepwise process is killed (SIGKILL) during hook execution, the `finally` block won't run and temp files remain in `.stepwise/tmp/`.

**Mitigation:** Temp files are small (< 9KB) and named with `tempfile.mkstemp` random suffixes. For production resilience, add a cleanup sweep in `init_project()` or server startup that removes `.stepwise/tmp/*.json` files older than 1 hour. This is a belt-and-suspenders measure — normal operation always cleans up via the `finally` block.

### R5: `save_event()` return type change

Changing `save_event()` from `-> None` to `-> int` is a signature change. Direct callers: only `_emit()` at `engine.py:2440` (currently ignores return value). `ThreadSafeStore` inherits the method without override (`server.py:76-95`).

**Mitigation:** Verified `_LockedConnection.execute()` (`server.py:47-49`) passes through cursor returns. No other code calls `save_event()`. The change is safe.

### R6: `_emit()` now loads job on every event

The updated `_emit()` calls `store.load_job(job_id)` to get metadata for every event. Currently, only `_fire_notify()` does this, and only when `notify_url` is set. This adds a SQLite read per event.

**Mitigation:** Job reads from SQLite WAL mode are fast (< 1ms). Events fire ~5-10 times per job lifecycle. The load is also needed for the hook envelope (required by spec), so it's unavoidable. If profiling shows issues, consider an in-memory job cache keyed by job_id, invalidated on `save_job()`.
