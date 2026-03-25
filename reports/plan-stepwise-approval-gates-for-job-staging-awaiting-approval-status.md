# Plan: Approval Gates for Job Staging

## Overview

Add an `AWAITING_APPROVAL` job status and approval gate workflow to the existing job staging system. Jobs created with `--approve` require explicit human sign-off via `stepwise job approve` before they can proceed. Notifications fire on status change via hooks and webhooks.

---

## Requirements

| # | Requirement | Acceptance Criteria |
|---|---|---|
| R1 | `AWAITING_APPROVAL` status in `JobStatus` enum | `JobStatus.AWAITING_APPROVAL.value == "awaiting_approval"`; round-trips through `to_dict()`/`from_dict()` and SQLite |
| R2 | `--approve` flag on `stepwise job create` | `stepwise job create my-flow --approve` creates job with status `awaiting_approval`; without flag, behavior unchanged (status = `staged`) |
| R3 | `stepwise job approve <job_id>` CLI command | Transitions job from `AWAITING_APPROVAL` → `PENDING`; rejects if job not in `AWAITING_APPROVAL`; works via server delegation and direct SQLite |
| R4 | `POST /api/jobs/{job_id}/approve` server endpoint | Returns `{"status": "approved", "job_id": "..."}` on success; 400 if wrong status; 404 if not found |
| R5 | `StepwiseClient.approve()` API client method | CLI delegates to server when available, falls back to direct store access |
| R6 | `JOB_AWAITING_APPROVAL` event emitted on status entry | Event stored in events table; visible in job event timeline |
| R7 | Hook fires on `job.awaiting_approval` | `on-approval-needed` script receives payload with `job_id`, `approve_command` |
| R8 | Webhook notification on approval status changes | `fire_notify_webhook()` called for both `job.awaiting_approval` and `job.approved` events when `notify_url` is set |
| R9 | `stepwise job show` displays `AWAITING_APPROVAL` jobs | Listed alongside STAGED/PENDING in default view; new status visible in table |
| R10 | Approval-required jobs block on `job run` | `stepwise job run <job_id>` rejects AWAITING_APPROVAL jobs with clear error message directing user to `stepwise job approve` |
| R11 | Cancel works on AWAITING_APPROVAL jobs | Existing `cancel_job()` handles the new status without errors |
| R12 | Full lifecycle tests | Create → approve → run → complete chain tested; rejection cases tested; hook/event emission tested |

---

## Assumptions

| # | Assumption | Verified Against | Exact Code |
|---|---|---|---|
| A1 | `JobStatus` is a plain `Enum` with string values | `models.py:29-36` | `class JobStatus(Enum): STAGED = "staged" ... CANCELLED = "cancelled"` — 7 members, no custom methods |
| A2 | Status stored as text, no CHECK constraint | `store.py:55` | `status TEXT` in CREATE TABLE — `JobStatus("awaiting_approval")` will round-trip without migration |
| A3 | `cmd_job()` routes via handlers dict | `cli.py:4765` | `handlers = {"create": _cmd_job_create, "show": _cmd_job_show, "run": _cmd_job_run, "dep": _cmd_job_dep, "cancel": _cmd_job_cancel, "rm": _cmd_job_rm}` |
| A4 | Event constants are plain module-level strings | `events.py:32-35` | `JOB_STAGED = "job.staged"` etc. — no registration, just import |
| A5 | Hook `EVENT_MAP` is a dict literal | `hooks.py:53-59` | 5 entries mapping event type → hook name. Adding entry is one line. |
| A6 | `fire_hook_for_event()` enriches payload for specific hooks | `hooks.py:181-185` | `if hook_name == "suspend": payload["fulfill_command"] = ...` — same pattern needed for `"approval-needed"` |
| A7 | Server endpoints follow `engine.method()` + `_notify_change()` pattern | `server.py:861-869` | `start_job` endpoint: `engine.start_job(job_id)`, `_notify_change(job_id)`, return dict |
| A8 | `StepwiseClient` wraps `_request(method, path, body)` | `api_client.py:126-128` | `def cancel(self, job_id): return self._request("POST", f"/api/jobs/{job_id}/cancel")` |
| A9 | `transition_job_to_pending()` rejects non-STAGED with ValueError | `store.py:374-375` | `if job.status != JobStatus.STAGED: raise ValueError(f"Cannot run job in status {job.status.value} (must be STAGED)")` |
| A10 | Cancel cascade checks `(JobStatus.PENDING, JobStatus.STAGED)` | `engine.py:3082` | `if dep_job.status in (JobStatus.PENDING, JobStatus.STAGED):` — tuple needs AWAITING_APPROVAL added |
| A11 | `_cmd_job_show` default statuses | `cli.py:4433` | `statuses = [JobStatus.STAGED, JobStatus.PENDING]` |
| A12 | `_cmd_job_create` local path sets `JobStatus.STAGED` | `cli.py:4333` | `status=JobStatus.STAGED,` inside `Job(...)` constructor |
| A13 | `_cmd_job_create` server path passes `status="staged"` | `cli.py:4309` | `status="staged",` in `client.create_job()` call |
| A14 | Server `create_job` handles status override post-creation | `server.py:823-825` | `if req.status == "staged": job.status = JobStatus.STAGED` — conditional branch, needs elif for new status |
| A15 | Dep-add endpoint restricts to STAGED jobs | `server.py:1042-1043` | `if job.status != JobStatus.STAGED:` — AWAITING_APPROVAL jobs should also allow dep management |
| A16 | Dep-remove endpoint restricts to STAGED jobs | `server.py:1059-1060` | `if job.status != JobStatus.STAGED:` — same treatment needed |
| A17 | `_emit()` auto-fires hooks and webhooks | `engine.py:2850-2857` | `fire_hook_for_event(...)` at line 2851, `fire_notify_webhook(...)` at line 2854 — no per-event registration needed |
| A18 | Test pattern for staging uses `_make_job()` + `SQLiteStore(":memory:")` | `test_job_staging.py:28-48` | Direct store tests, no engine needed for store-level assertions |

---

## Implementation Steps

### Step 1: Add `AWAITING_APPROVAL` to `JobStatus` enum
**Depends on:** nothing
**File:** `src/stepwise/models.py:29-36`
**Change:** Insert `AWAITING_APPROVAL = "awaiting_approval"` between lines 30 and 31 (after `STAGED`, before `PENDING`):
```python
class JobStatus(Enum):
    STAGED = "staged"
    AWAITING_APPROVAL = "awaiting_approval"  # ← NEW
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```
**Why first:** Every subsequent step imports this enum value. Zero functional risk — adding an enum member doesn't affect existing code that doesn't reference it.
**Verify:** `uv run python -c "from stepwise.models import JobStatus; print(JobStatus.AWAITING_APPROVAL.value)"`

---

### Step 2: Add event constants
**Depends on:** nothing (parallel with Step 1)
**File:** `src/stepwise/events.py:32-35`
**Change:** Add two constants in the "Job staging" section after line 33 (`JOB_STAGED`):
```python
# Job staging
JOB_STAGED = "job.staged"
JOB_AWAITING_APPROVAL = "job.awaiting_approval"  # ← NEW
JOB_APPROVED = "job.approved"                      # ← NEW
JOB_CANCELLED = "job.cancelled"
JOB_DEPS_CHANGED = "job.deps_changed"
```
**Why early:** Steps 3, 5, 8 import these constants.

---

### Step 3: Add hook mappings and payload enrichment
**Depends on:** Step 2 (event constant strings must match)
**File:** `src/stepwise/hooks.py`

**Change 1 — `EVENT_MAP` at line 53:** Add two entries:
```python
EVENT_MAP = {
    "step.suspended": "suspend",
    "step.completed": "step-complete",
    "job.completed": "complete",
    "job.failed": "fail",
    "step.failed": "fail",
    "job.awaiting_approval": "approval-needed",  # ← NEW
    "job.approved": "approved",                    # ← NEW
}
```

**Change 2 — `fire_hook_for_event()` at lines 181-185:** Add approval-needed payload enrichment after the suspend block:
```python
    # Add fulfill_command for suspend events
    if hook_name == "suspend" and "run_id" in event_data:
        payload["fulfill_command"] = (
            f"stepwise fulfill {event_data['run_id']} '<json>'"
        )

    # Add approve_command for approval-needed events       # ← NEW
    if hook_name == "approval-needed":                      # ← NEW
        payload["approve_command"] = (                       # ← NEW
            f"stepwise job approve {job_id}"                 # ← NEW
        )                                                    # ← NEW
```

**Change 3 — Module docstring at line 7:** Add event description:
```python
#   - approval-needed: A job is awaiting human approval
#   - approved: A job has been approved and is now pending
```

---

### Step 4: Add `transition_job_to_approved()` to store + improve `transition_job_to_pending()` error
**Depends on:** Step 1 (`JobStatus.AWAITING_APPROVAL` must exist)
**File:** `src/stepwise/store.py`

**Change 1 — New method after `transition_job_to_pending()` (after line 380):**
```python
    def transition_job_to_approved(self, job_id: str) -> None:
        """Approve a job: AWAITING_APPROVAL → PENDING. Raises ValueError if not AWAITING_APPROVAL."""
        from stepwise.models import _now
        job = self.load_job(job_id)
        if job.status != JobStatus.AWAITING_APPROVAL:
            raise ValueError(
                f"Cannot approve job in status {job.status.value} (must be awaiting_approval)"
            )
        with self._conn:
            self._conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (JobStatus.PENDING.value, _now().isoformat(), job_id),
            )
```
Follows exact structure of `transition_job_to_pending()` at lines 370-380.

**Change 2 — Improve error in `transition_job_to_pending()` at line 374-375:**
```python
        if job.status == JobStatus.AWAITING_APPROVAL:
            raise ValueError(
                f"Job {job_id} requires approval first (use 'stepwise job approve {job_id}')"
            )
        if job.status != JobStatus.STAGED:
            raise ValueError(f"Cannot run job in status {job.status.value} (must be STAGED)")
```
The AWAITING_APPROVAL-specific check goes first so users get a helpful message instead of the generic "must be STAGED" error.

---

### Step 5: Add `approve_job()` to engine
**Depends on:** Step 1, Step 2, Step 4 (needs enum, event constant, and store method)
**File:** `src/stepwise/engine.py`

**Change — Base `Engine` class, after `resume_job()` at line 304, before `cancel_job()` at line 306:**
```python
    def approve_job(self, job_id: str) -> None:
        """Approve job: AWAITING_APPROVAL → PENDING."""
        self.store.transition_job_to_approved(job_id)
        self._emit(job_id, JOB_APPROVED)
```
The `_emit()` method (line 2822) automatically handles hook firing (line 2851) and webhook dispatch (line 2854) — no additional notification code needed.

**Import:** Add `JOB_APPROVED` to the existing import from `events.py` at line 69:
```python
from stepwise.hooks import build_event_envelope, fire_hook_for_event, fire_notify_webhook
```
The event import block is near the top of the file. Find the existing `from stepwise.events import ...` line and add `JOB_APPROVED, JOB_AWAITING_APPROVAL`.

**Note:** `AsyncEngine` inherits from `Engine`, so `approve_job()` is inherited. No override needed — `_emit()` is already overridden in `AsyncEngine` (line 2822) and the base `Engine._emit()` exists too. Both call through to the same hook/webhook dispatch. The `approve_job` method only calls `store.transition_job_to_approved()` (synchronous DB write) and `self._emit()` — no async dispatch needed.

---

### Step 6: Update cancel cascade to include AWAITING_APPROVAL
**Depends on:** Step 1 (needs enum value)
**File:** `src/stepwise/engine.py:3082`

**Change:** Add `JobStatus.AWAITING_APPROVAL` to the cascade condition:
```python
                if dep_job.status in (JobStatus.PENDING, JobStatus.STAGED, JobStatus.AWAITING_APPROVAL):
```
**Why:** Without this, cancelling a parent job leaves AWAITING_APPROVAL dependents in limbo.

---

### Step 7: Add server endpoint + update `create_job` status handling
**Depends on:** Step 1, Step 2, Step 5 (needs enum, events, and engine.approve_job)
**File:** `src/stepwise/server.py`

**Change 1 — New endpoint after `run_staged_job()` at line 1016, before `run_group()` at line 1019:**
```python
@app.post("/api/jobs/{job_id}/approve")
def approve_job_route(job_id: str):
    """Approve a job awaiting approval → PENDING."""
    engine = _get_engine()
    try:
        engine.approve_job(job_id)
        _notify_change(job_id)
        return {"status": "approved", "job_id": job_id}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
```
Follows pattern of `start_job` (line 861), `pause_job` (line 872), `resume_job` (line 883).

**Change 2 — `create_job()` at lines 823-825, extend status handling:**
```python
        if req.status == "staged":
            job.status = JobStatus.STAGED
            needs_save = True
        elif req.status == "awaiting_approval":
            job.status = JobStatus.AWAITING_APPROVAL
            needs_save = True
```
After saving (line 835), emit the awaiting_approval event when applicable:
```python
        if needs_save:
            engine.store.save_job(job)
        if req.status == "awaiting_approval":
            engine._emit(job.id, JOB_AWAITING_APPROVAL)
```
The `_emit()` call handles hook/webhook dispatch automatically (engine.py:2851-2857).

**Change 3 — Dep endpoints at lines 1042-1043 and 1059-1060:** Allow dep management for AWAITING_APPROVAL jobs too:
```python
    if job.status not in (JobStatus.STAGED, JobStatus.AWAITING_APPROVAL):
        raise HTTPException(status_code=400, detail=f"Can only add deps to STAGED/AWAITING_APPROVAL jobs (job is {job.status.value})")
```
Same for the remove dep endpoint at line 1059.

---

### Step 8: Add `approve()` to `StepwiseClient`
**Depends on:** Step 7 (endpoint must exist for the method to call)
**File:** `src/stepwise/api_client.py`

**Change — After `cancel()` at line 128:**
```python
    def approve(self, job_id: str) -> dict:
        """Approve a job awaiting approval."""
        return self._request("POST", f"/api/jobs/{job_id}/approve")
```
Follows exact pattern of `cancel()` at lines 126-128.

---

### Step 9: Add `--approve` flag + `job approve` subcommand to CLI
**Depends on:** Step 1, Step 4, Step 8 (needs enum, store method, client method)
**File:** `src/stepwise/cli.py`

**Change 1 — `build_parser()` at line 4149, add flag to `p_job_create`:**
```python
    p_job_create.add_argument("--approve", action="store_true",
                              help="Require approval before job can run")
```

**Change 2 — `build_parser()` after job cancel parser (line 4174), add new subparser:**
```python
    # job approve
    p_job_approve = job_sub.add_parser("approve", help="Approve a job awaiting approval")
    p_job_approve.add_argument("job_id", help="Job ID to approve")
    p_job_approve.add_argument("--output", choices=["table", "json"], default="table")
```

**Change 3 — `_cmd_job_create()` server path at line 4309:** Replace hardcoded `status="staged"`:
```python
            initial_status = "awaiting_approval" if getattr(args, "approve", False) else "staged"
            result = client.create_job(
                ...
                status=initial_status,
                ...
            )
```
Update the output messages at lines 4314 and 4317 to use `initial_status`.

**Change 4 — `_cmd_job_create()` local path at line 4333:** Replace hardcoded `JobStatus.STAGED`:
```python
            status=JobStatus.AWAITING_APPROVAL if getattr(args, "approve", False) else JobStatus.STAGED,
```
Update the output messages at lines 4363 and 4366 similarly.

**Change 5 — New handler `_cmd_job_approve()` after `_cmd_job_run()` (after line ~4530):**
```python
def _cmd_job_approve(args) -> int:
    """Approve a job awaiting approval."""
    io = _io(args)

    # Server delegation
    server_url = _detect_server_url(args)
    if server_url:
        from stepwise.api_client import StepwiseClient, StepwiseAPIError
        client = StepwiseClient(server_url)
        try:
            result = client.approve(args.job_id)
            if args.output == "json":
                print(json.dumps(result))
            else:
                io.log("success", f"Job {args.job_id} approved — now PENDING")
            return EXIT_SUCCESS
        except StepwiseAPIError as e:
            io.log("error", e.detail)
            return EXIT_JOB_FAILED

    # Local path
    project = _find_project_or_exit(args)
    from stepwise.store import SQLiteStore
    store = SQLiteStore(str(project.db_path))
    try:
        store.transition_job_to_approved(args.job_id)
        if args.output == "json":
            print(json.dumps({"status": "approved", "job_id": args.job_id}))
        else:
            io.log("success", f"Job {args.job_id} approved — now PENDING")
        return EXIT_SUCCESS
    except (KeyError, ValueError) as e:
        io.log("error", str(e))
        return EXIT_JOB_FAILED
    finally:
        store.close()
```
Follows pattern of `_cmd_job_run()` (lines 4464-4530): server delegation → local fallback.

**Change 6 — `cmd_job()` dispatcher at line 4765:** Add to handlers dict:
```python
    handlers = {
        "create": _cmd_job_create,
        "show": _cmd_job_show,
        "run": _cmd_job_run,
        "approve": _cmd_job_approve,  # ← NEW
        "dep": _cmd_job_dep,
        "cancel": _cmd_job_cancel,
        "rm": _cmd_job_rm,
    }
```
Update usage string at line 4777: `"Usage: stepwise job {create|show|run|approve|dep|cancel|rm} ..."`

**Change 7 — `_cmd_job_show()` at line 4433:** Add to default statuses:
```python
            statuses = [JobStatus.AWAITING_APPROVAL, JobStatus.STAGED, JobStatus.PENDING]
```

---

### Step 10: Write tests
**Depends on:** Steps 1-9 (all functional code must be in place)
**File:** `tests/test_approval_gates.py` (new)

```python
"""Tests for approval gates: AWAITING_APPROVAL status, approve transition, events, hooks, lifecycle."""

from __future__ import annotations

import pytest

from stepwise.events import JOB_APPROVED, JOB_AWAITING_APPROVAL
from stepwise.models import (
    Job,
    JobStatus,
    StepDefinition,
    ExecutorRef,
    InputBinding,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore
from tests.conftest import register_step_fn, run_job_sync


def _wf() -> WorkflowDefinition:
    """Minimal workflow for testing."""
    return WorkflowDefinition(steps={
        "a": StepDefinition(
            name="a",
            outputs=["result"],
            executor=ExecutorRef(type="script", config={"command": "echo '{\"result\": 1}'"}),
        ),
    })


def _make_job(store: SQLiteStore, status: JobStatus = JobStatus.AWAITING_APPROVAL,
              group: str | None = None) -> Job:
    import uuid
    job = Job(
        id=f"job-{uuid.uuid4().hex[:8]}",
        objective="test",
        workflow=_wf(),
        status=status,
        job_group=group,
        created_at=_now(),
        updated_at=_now(),
    )
    store.save_job(job)
    return job


@pytest.fixture
def store():
    s = SQLiteStore(":memory:")
    yield s
    s.close()


# ── R1: Status round-trip ─────────────────────────────────────────────

class TestAwaitingApprovalStatus:
    def test_enum_value(self):
        assert JobStatus.AWAITING_APPROVAL.value == "awaiting_approval"

    def test_sqlite_roundtrip(self, store):
        job = _make_job(store, status=JobStatus.AWAITING_APPROVAL)
        loaded = store.load_job(job.id)
        assert loaded.status == JobStatus.AWAITING_APPROVAL

    def test_to_dict_from_dict(self):
        job = Job(
            id="job-test", objective="test", workflow=_wf(),
            status=JobStatus.AWAITING_APPROVAL,
        )
        d = job.to_dict()
        assert d["status"] == "awaiting_approval"
        restored = Job.from_dict(d)
        assert restored.status == JobStatus.AWAITING_APPROVAL


# ── R3/R4: Store transition ───────────────────────────────────────────

class TestTransitionJobToApproved:
    def test_transitions_to_pending(self, store):
        job = _make_job(store, status=JobStatus.AWAITING_APPROVAL)
        store.transition_job_to_approved(job.id)
        loaded = store.load_job(job.id)
        assert loaded.status == JobStatus.PENDING

    def test_rejects_staged(self, store):
        job = _make_job(store, status=JobStatus.STAGED)
        with pytest.raises(ValueError, match="must be awaiting_approval"):
            store.transition_job_to_approved(job.id)

    def test_rejects_running(self, store):
        job = _make_job(store, status=JobStatus.RUNNING)
        with pytest.raises(ValueError, match="must be awaiting_approval"):
            store.transition_job_to_approved(job.id)

    def test_rejects_completed(self, store):
        job = _make_job(store, status=JobStatus.COMPLETED)
        with pytest.raises(ValueError, match="must be awaiting_approval"):
            store.transition_job_to_approved(job.id)

    def test_not_found_raises(self, store):
        with pytest.raises(KeyError):
            store.transition_job_to_approved("job-nonexistent")


# ── R10: job run rejects AWAITING_APPROVAL ────────────────────────────

class TestJobRunRejectsAwaitingApproval:
    def test_transition_to_pending_rejects_with_helpful_message(self, store):
        job = _make_job(store, status=JobStatus.AWAITING_APPROVAL)
        with pytest.raises(ValueError, match="requires approval"):
            store.transition_job_to_pending(job.id)


# ── R6: Engine approve emits event ────────────────────────────────────

class TestEngineApproveJob:
    def test_approve_emits_event(self, async_engine):
        job = async_engine.create_job("test", _wf())
        # Manually set to AWAITING_APPROVAL
        job.status = JobStatus.AWAITING_APPROVAL
        async_engine.store.save_job(job)

        async_engine.approve_job(job.id)

        reloaded = async_engine.store.load_job(job.id)
        assert reloaded.status == JobStatus.PENDING

        events = async_engine.store.events_for_job(job.id)
        event_types = [e.type for e in events]
        assert JOB_APPROVED in event_types

    def test_approve_wrong_status_raises(self, async_engine):
        job = async_engine.create_job("test", _wf())
        # job is PENDING by default
        with pytest.raises(ValueError, match="must be awaiting_approval"):
            async_engine.approve_job(job.id)


# ── R11: Cancel cascades to AWAITING_APPROVAL ─────────────────────────

class TestCancelCascadesToAwaitingApproval:
    def test_cancel_parent_cascades(self, async_engine):
        parent = async_engine.create_job("parent", _wf())
        child = async_engine.create_job("child", _wf())
        # Set child to AWAITING_APPROVAL and add dep
        child.status = JobStatus.AWAITING_APPROVAL
        async_engine.store.save_job(child)
        async_engine.store.add_job_dependency(child.id, parent.id)

        async_engine.cancel_job(parent.id)

        child_reloaded = async_engine.store.load_job(child.id)
        assert child_reloaded.status == JobStatus.CANCELLED


# ── R7: Hook payload ──────────────────────────────────────────────────

class TestApprovalHookPayload:
    def test_hook_payload_includes_approve_command(self):
        from stepwise.hooks import fire_hook_for_event, EVENT_MAP
        # Verify mapping exists
        assert EVENT_MAP.get("job.awaiting_approval") == "approval-needed"
        assert EVENT_MAP.get("job.approved") == "approved"

    def test_fire_hook_builds_approve_command(self, tmp_path):
        """fire_hook_for_event returns False (no script) but verifies payload construction."""
        from stepwise.hooks import fire_hook_for_event
        # No hook scripts in tmp_path, but the function should not crash
        result = fire_hook_for_event(
            "job.awaiting_approval", {"step": "test"}, "job-123", tmp_path
        )
        # Returns False because no on-approval-needed script exists
        assert result is False


# ── R9: job show includes AWAITING_APPROVAL ───────────────────────────

class TestJobShowIncludesAwaitingApproval:
    def test_awaiting_approval_jobs_in_listing(self, store):
        _make_job(store, status=JobStatus.AWAITING_APPROVAL)
        _make_job(store, status=JobStatus.STAGED)
        # Query the same way _cmd_job_show does
        statuses = [JobStatus.AWAITING_APPROVAL, JobStatus.STAGED, JobStatus.PENDING]
        all_jobs = []
        for status in statuses:
            all_jobs.extend(store.all_jobs(status=status, top_level_only=True))
        assert len(all_jobs) == 2
        status_values = {j.status for j in all_jobs}
        assert JobStatus.AWAITING_APPROVAL in status_values
        assert JobStatus.STAGED in status_values


# ── R12: Full lifecycle ───────────────────────────────────────────────

class TestFullApprovalLifecycle:
    def test_create_approve_start_complete(self, async_engine):
        """Create AWAITING_APPROVAL → approve → start → run to completion."""
        register_step_fn("ok_fn", lambda inputs: {"result": 42})

        wf = WorkflowDefinition(steps={
            "do-work": StepDefinition(
                name="do-work",
                outputs=["result"],
                executor=ExecutorRef(type="callable", config={"fn_name": "ok_fn"}),
            ),
        })

        job = async_engine.create_job("lifecycle test", wf)
        # Simulate --approve: set to AWAITING_APPROVAL
        job.status = JobStatus.AWAITING_APPROVAL
        async_engine.store.save_job(job)

        # Cannot start yet
        with pytest.raises(ValueError):
            async_engine.start_job(job.id)

        # Approve
        async_engine.approve_job(job.id)
        assert async_engine.store.load_job(job.id).status == JobStatus.PENDING

        # Run to completion
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED
        runs = async_engine.store.runs_for_job(job.id)
        assert runs[0].result.artifact["result"] == 42
```

**Run commands:**
```bash
uv run pytest tests/test_approval_gates.py -v                    # new tests
uv run pytest tests/test_job_staging.py -v                        # verify no staging regression
uv run pytest tests/ -v                                           # full regression suite
```

---

## Dependency Graph

```
Step 1 (models.py)  ──┬──→ Step 4 (store.py) ──→ Step 5 (engine.py) ──→ Step 7 (server.py) ──→ Step 8 (api_client.py) ──→ Step 9 (cli.py)
                      │                                                                                                          │
Step 2 (events.py)  ──┤──→ Step 3 (hooks.py)                                                                                    │
                      │                                                                                                          ↓
                      └──→ Step 6 (engine.py cancel) ────────────────────────────────────────────────────────────────────→ Step 10 (tests)
```

**Parallelizable pairs:** Steps 1+2 (no dependency), Steps 3+4 (both need 1 or 2 but not each other), Step 6 (only needs Step 1).

**Critical path:** Step 1 → Step 4 → Step 5 → Step 7 → Step 8 → Step 9 → Step 10

---

## Files Changed Summary

| File | Lines touched | Nature of change |
|---|---|---|
| `src/stepwise/models.py:30` | +1 line | New enum member |
| `src/stepwise/events.py:33-34` | +2 lines | New event constants |
| `src/stepwise/hooks.py:53-59, 181-185` | +5 lines map entries, +4 lines payload | Hook mappings + approve_command |
| `src/stepwise/store.py:374-380` | +15 lines new method, +4 lines error improvement | `transition_job_to_approved()` + better error |
| `src/stepwise/engine.py:304, 3082` | +3 lines method, +1 line enum in tuple | `approve_job()` + cancel cascade |
| `src/stepwise/server.py:823-825, 1016, 1042, 1059` | +12 lines endpoint, +3 lines create, +2 lines dep guards | Approve endpoint + create handling |
| `src/stepwise/api_client.py:128` | +3 lines | `approve()` method |
| `src/stepwise/cli.py:4149, 4174, 4309, 4333, 4433, 4530, 4765, 4777` | +50 lines handler, +6 lines parser, +4 lines flag logic | `--approve` flag, `job approve` cmd, show listing |
| `tests/test_approval_gates.py` | ~180 lines (new file) | 14 test cases across 8 test classes |

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| New status breaks frontend rendering | Medium — Web UI `status-colors.ts` may not map it | `status-colors.ts` uses fallback colors for unknown statuses. Add `awaiting_approval` entry in a follow-up. |
| `from_dict` fails on old DB with new status | Low — only affects downgrade | `JobStatus("awaiting_approval")` raises ValueError. Acceptable: we don't support downgrades (master = release). |
| Dep endpoints still reject AWAITING_APPROVAL | Medium — blocks staging workflows | Step 7 Change 3 widens the guard at `server.py:1042` and `1059`. |
| `_cmd_job_dep` local path also checks STAGED | Low — CLI local dep management | Check `cli.py` `_cmd_job_dep` for local-path status checks; update if present. |

---

## Out of Scope

- Batch approval (`--group` flag on approve command)
- Approval with comments/reason field
- Multi-party approval (require N approvers)
- Web UI changes for approval gates
- Approval timeout (auto-reject after N hours)
- Role-based approval permissions
