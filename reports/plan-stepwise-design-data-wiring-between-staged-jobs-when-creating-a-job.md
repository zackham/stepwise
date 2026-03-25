# Plan: Data Wiring Between Staged Jobs

## Overview

Add cross-job data wiring so that a staged job's inputs can reference outputs from another job. When a user writes `--input plan=job-abc123.result`, the system parses the `job-{id}.{field}` pattern, validates the referenced job exists, stores it as a typed reference (not a raw string), and resolves the reference to the actual output value when the dependent job starts running.

This builds on the existing `depends_on` infrastructure (job dependency graph, cycle detection, auto-start on completion) by adding a **data** dimension to what is currently an **ordering-only** relationship.

---

## Requirements

### R1: Parse `job-{id}.{field}` references in `--input` values

**Acceptance criteria:**
- `parse_inputs(["plan=job-abc123.result"])` detects the pattern and returns a structured reference, not a raw string
- Pattern: value matches `^job-[a-zA-Z0-9]+\.[a-zA-Z0-9_.]+$` (job ID prefix + dot + field path supporting nested access)
- Non-matching values (plain strings, `@file` paths) are unaffected
- `--input count=42` remains `{"count": "42"}` (plain string)
- `--input plan=job-abc123.result` becomes `{"plan": {"$job_ref": "job-abc123", "field": "result"}}`

### R2: Validate referenced job exists

**Acceptance criteria:**
- At job creation time (both CLI local path and server API), the system checks that every referenced job ID exists in the store
- If a referenced job does not exist, creation fails with a clear error: `"Referenced job not found: job-abc123"`
- Validation happens before the job is saved

### R3: Auto-add dependency edge

**Acceptance criteria:**
- When a `$job_ref` input is detected, the system automatically calls `store.add_job_dependency(new_job_id, referenced_job_id)`
- This means `--input plan=job-abc123.result` implies `depends_on: [job-abc123]` without requiring a separate `job dep --after` call
- Cycle detection still applies — if adding the dependency would create a cycle, job creation fails
- Explicit `depends_on` entries and auto-inferred entries are merged (no duplicates)

### R4: Store references as structured data in `job.inputs`

**Acceptance criteria:**
- `job.inputs` stores `{"plan": {"$job_ref": "job-abc123", "field": "result"}}` (not the raw string)
- The `$job_ref` sentinel key distinguishes references from plain values during serialization
- `to_dict()` / `from_dict()` round-trip correctly
- Existing plain-string inputs are 100% backwards-compatible (no `$job_ref` key = plain value)

### R5: Resolve references when the dependent job starts

**Acceptance criteria:**
- When `start_job()` transitions a job from PENDING → RUNNING, all `$job_ref` inputs are resolved to actual values
- Resolution: load referenced job → find terminal step that produced the field → extract `artifact[field]`
- Nested field access works: `job-abc123.hero.headline` navigates `artifact["hero"]["headline"]`
- Resolved values replace the reference in `job.inputs` before the job's steps begin executing
- If a referenced job is COMPLETED but the field is missing from its outputs, the input resolves to `None` (with a warning log)
- If a referenced job is not COMPLETED when `start_job` runs, this is a bug (deps should prevent this) — raise an error

### R6: Support in server API

**Acceptance criteria:**
- `POST /api/jobs` accepts `$job_ref` objects in the `inputs` dict
- Server-side validation and auto-dependency-add work identically to CLI
- `GET /api/jobs/{id}` returns the raw (unresolved) inputs for staged/pending jobs, resolved inputs for running/completed jobs

---

## Assumptions

### Verified against code

1. **`job.inputs` is a plain dict, serialized as JSON** — `store.py:save_job()` uses `_dumps(job.inputs)`. The dict can hold nested structures (not limited to `dict[str, str]`), since `_dumps` is `json.dumps`. Confirmed at `store.py:178`.

2. **`parse_inputs()` returns `dict[str, str]`** — Currently all values are strings (`runner.py:44`). The return type annotation will need to change to `dict[str, str | dict]` to accommodate references.

3. **`add_job_dependency` requires both jobs to exist** — The store's foreign key constraint (`store.py:113-114`) enforces this. No additional check needed at the SQL level, but we should give a user-friendly error before hitting the FK constraint.

4. **`would_create_cycle()` is BFS-based and works on the `job_dependencies` table** — `store.py:300-319`. Adding auto-inferred dependencies goes through the same path.

5. **`start_job()` in AsyncEngine transitions PENDING → RUNNING** — `engine.py:2960-2974`. This is the natural hook point for resolving cross-job references.

6. **Step-level `_resolve_inputs()` reads from `job.inputs`** — `engine.py:2126`: `inputs[binding.local_name] = job.inputs.get(binding.source_field)`. After we resolve `$job_ref` entries in `start_job`, step-level resolution works unchanged.

7. **Terminal step outputs are accessible via `store.latest_completed_run(job_id, step_name)`** — `store.py:538-544`. For cross-job resolution, we need to find the right step, which means we either need the step name in the reference or we search all completed runs.

### Design decision: field resolution strategy

A job's "outputs" aren't declared at the job level — they're per-step. `job-abc123.result` is ambiguous if multiple steps produce a `result` field. Two options:

**Option A: Require step name** — `job-abc123.step-name.result`. Precise, no ambiguity. But verbose and leaks internal structure.

**Option B: Search terminal steps** — Find the last completed terminal step whose artifact contains the field. "Terminal" = no downstream dependents within the job. This matches intuition: the job's "output" is what its final steps produce.

**Chosen: Option B with fallback to scanning all completed runs.** Rationale:
- Users think of jobs as black boxes with outputs, not as step graphs
- Most flows have one terminal step (the DAG's sink)
- If multiple terminal steps have the same field, take the last completed one (deterministic: latest `completed_at`)
- If no terminal step has the field, scan all completed runs (handles edge cases like mid-flow steps that produce the desired field)

---

## Implementation Steps

### Step 1: Add `$job_ref` reference type to input parsing

**File:** `src/stepwise/runner.py`

In `parse_inputs()` (line 44-65):
- After splitting `key=value`, check if value matches `^job-[a-zA-Z0-9]+\..+$`
- If matched, parse into `{"$job_ref": "<job-id>", "field": "<field-path>"}`
- Update return type annotation to `dict[str, str | dict]`

```python
import re

_JOB_REF_RE = re.compile(r'^(job-[a-zA-Z0-9]+)\.(.+)$')

def parse_inputs(input_list: list[str] | None) -> dict[str, str | dict]:
    # ... existing logic ...
    else:
        m = _JOB_REF_RE.match(value)
        if m:
            result[key] = {"$job_ref": m.group(1), "field": m.group(2)}
        else:
            result[key] = value
    return result
```

### Step 2: Add helper to extract job output fields

**File:** `src/stepwise/store.py`

Add a new method `get_job_output_field(job_id, field_path)` that:
1. Loads all completed runs for the job
2. Identifies terminal steps (steps with no downstream deps in the workflow)
3. Searches terminal step artifacts first, then all completed runs
4. Supports nested field access via dot-path (`hero.headline`)
5. Returns the value or `None`

```python
def get_job_output_field(self, job_id: str, field_path: str) -> tuple[Any, bool]:
    """Resolve a field from a completed job's outputs.

    Returns (value, found). Searches terminal steps first, then all completed runs.
    """
```

Also add a convenience method:

```python
def completed_runs_for_job(self, job_id: str) -> list[StepRun]:
    """Return all COMPLETED runs for a job, ordered by completed_at DESC."""
```

### Step 3: Add validation and auto-dependency at job creation

**File:** `src/stepwise/engine.py`

In `create_job()` (line 150-211 for Engine, similar for AsyncEngine):
- After creating the Job object but before `store.save_job()`:
  - Scan `inputs` for any `$job_ref` dicts
  - For each reference, validate that the referenced job exists (`store.load_job`)
  - Collect referenced job IDs for dependency edges
- After `store.save_job()`:
  - For each referenced job ID, check `store.would_create_cycle()` and call `store.add_job_dependency()`

Extract a shared helper since both Engine and AsyncEngine need this:

```python
def _process_job_ref_inputs(self, job: Job) -> list[str]:
    """Validate $job_ref inputs and return list of referenced job IDs.

    Raises ValueError if a referenced job doesn't exist.
    """
    ref_job_ids = []
    for key, value in job.inputs.items():
        if isinstance(value, dict) and "$job_ref" in value:
            ref_id = value["$job_ref"]
            try:
                self.store.load_job(ref_id)
            except KeyError:
                raise ValueError(f"Referenced job not found: {ref_id} (from input '{key}')")
            ref_job_ids.append(ref_id)
    return ref_job_ids
```

### Step 4: Resolve references at job start

**File:** `src/stepwise/engine.py`

In both `Engine.start_job()` (line 213) and `AsyncEngine.start_job()` (line 2954):
- Before transitioning to RUNNING, resolve all `$job_ref` inputs
- Replace reference dicts with actual values in `job.inputs`
- Save the updated job to persist resolved values

Add a shared helper:

```python
def _resolve_job_ref_inputs(self, job: Job) -> None:
    """Resolve all $job_ref inputs to actual values. Mutates job.inputs in place.

    Raises ValueError if a referenced job is not COMPLETED.
    """
    for key, value in list(job.inputs.items()):
        if isinstance(value, dict) and "$job_ref" in value:
            ref_id = value["$job_ref"]
            field_path = value["field"]
            ref_job = self.store.load_job(ref_id)
            if ref_job.status != JobStatus.COMPLETED:
                raise ValueError(
                    f"Referenced job {ref_id} is {ref_job.status.value}, expected COMPLETED"
                )
            resolved, found = self.store.get_job_output_field(ref_id, field_path)
            if not found:
                logger.warning(
                    "Job %s input '%s': field '%s' not found in job %s outputs, resolving to None",
                    job.id, key, field_path, ref_id,
                )
            job.inputs[key] = resolved
```

Insert call in `start_job()` just before setting `job.status = JobStatus.RUNNING`:

```python
def start_job(self, job_id: str) -> None:
    job = self.store.load_job(job_id)
    # ... existing validation ...
    self._resolve_job_ref_inputs(job)  # <-- NEW
    job.status = JobStatus.RUNNING
    job.updated_at = _now()
    self.store.save_job(job)
    # ...
```

### Step 5: Support in server API

**File:** `src/stepwise/server.py`

The `POST /api/jobs` handler (line 794-839) already passes `req.inputs` to `engine.create_job()`. Since validation and auto-dependency happen inside `create_job()` (Step 3), the server gets this for free.

One change needed: allow `$job_ref` dicts in the `inputs` field of `CreateJobRequest`. Currently `inputs: dict | None = None` (line 118) — this already accepts nested dicts, so no Pydantic model change needed.

Add a note to the API docs (if any) that `inputs` values can be either strings or `{"$job_ref": "job-xxx", "field": "path"}` objects.

### Step 6: Support in `api_client.py` (CLI → server delegation)

**File:** `src/stepwise/api_client.py`

The `create_job()` method (line 78-102) already passes `inputs` as-is in the JSON body. Since `parse_inputs()` now returns `$job_ref` dicts, these flow through to the server naturally. No change needed here.

### Step 7: Update web UI types (optional, for future UI support)

**File:** `web/src/lib/types.ts`

Add a type for job references so the frontend can distinguish them:

```typescript
interface JobRef {
  $job_ref: string;
  field: string;
}

type JobInputValue = string | JobRef;
```

This is optional for the initial implementation but enables future UI features like showing "waiting for job-abc123.result" in the job detail view.

---

## Testing Strategy

### Unit tests

**File:** `tests/test_job_ref_inputs.py` (new)

```bash
uv run pytest tests/test_job_ref_inputs.py -v
```

**Test cases for `parse_inputs()`:**

1. `test_plain_input_unchanged` — `["count=42"]` → `{"count": "42"}`
2. `test_file_input_unchanged` — `["data=@file.txt"]` → reads file
3. `test_job_ref_parsed` — `["plan=job-abc123.result"]` → `{"plan": {"$job_ref": "job-abc123", "field": "result"}}`
4. `test_job_ref_nested_field` — `["x=job-abc123.hero.headline"]` → `{"$job_ref": "job-abc123", "field": "hero.headline"}`
5. `test_mixed_inputs` — plain + ref in same call
6. `test_not_a_job_ref` — `["x=job-without-dot"]` → plain string (no dot = no ref)
7. `test_job_ref_with_hyphen_id` — `["x=job-abc-123.field"]` — only `[a-zA-Z0-9]` after `job-`, so this should NOT match (stays as plain string)

**Test cases for validation (engine `create_job`):**

8. `test_create_job_with_valid_ref` — referenced job exists → job created + dependency auto-added
9. `test_create_job_with_missing_ref` — referenced job doesn't exist → `ValueError`
10. `test_create_job_ref_auto_dependency` — after creation, `store.get_job_dependencies(new_id)` includes the referenced job
11. `test_create_job_ref_cycle_detection` — A refs B, B refs A → second creation fails
12. `test_create_job_ref_merges_with_explicit_deps` — explicit `depends_on` + ref deps don't duplicate

**Test cases for resolution (`start_job`):**

13. `test_resolve_ref_on_start` — referenced job completed with `{"result": "hello"}` → dependent job inputs resolve to `{"plan": "hello"}`
14. `test_resolve_nested_field` — `hero.headline` navigates nested artifact
15. `test_resolve_missing_field_returns_none` — field not in any step's artifact → `None` + warning
16. `test_resolve_ref_job_not_completed_raises` — referenced job is RUNNING → error (should never happen if deps work correctly)
17. `test_plain_inputs_unchanged_on_start` — jobs without refs pass through `start_job` without modification

### Integration test

**File:** `tests/test_job_ref_inputs.py` (same file, separate class)

18. `test_end_to_end_job_chain` — Full flow:
    - Create job A with a workflow that produces `{"result": "hello"}`
    - Create job B with `inputs={"plan": {"$job_ref": "job-A-id", "field": "result"}}`
    - Run job A to completion
    - Verify job B auto-starts and its step receives `plan="hello"`
    - Uses `async_engine` fixture and `run_job_sync()`

### Run all tests

```bash
uv run pytest tests/test_job_ref_inputs.py -v
uv run pytest tests/test_job_staging.py tests/test_job_dep_readiness.py -v  # existing tests still pass
uv run pytest tests/ -v  # full suite
```

---

## Files Modified (Summary)

| File | Change |
|------|--------|
| `src/stepwise/runner.py` | `parse_inputs()` detects `job-{id}.{field}` pattern, returns structured ref |
| `src/stepwise/store.py` | Add `get_job_output_field()` and `completed_runs_for_job()` |
| `src/stepwise/engine.py` | Add `_process_job_ref_inputs()` in `create_job()`, `_resolve_job_ref_inputs()` in `start_job()` — both Engine and AsyncEngine |
| `web/src/lib/types.ts` | Add `JobRef` type (optional) |
| `tests/test_job_ref_inputs.py` | New test file with ~18 test cases |

No changes needed to: `models.py` (inputs dict already supports nested structures), `server.py` (inputs pass-through works), `api_client.py` (JSON serialization handles dicts), `cli.py` (uses `parse_inputs()` which handles the new pattern).
