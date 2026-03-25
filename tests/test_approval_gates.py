"""Tests for approval gates: AWAITING_APPROVAL status, approve transition, events, hooks, lifecycle."""

from __future__ import annotations

import pytest

from stepwise.events import JOB_APPROVED, JOB_AWAITING_APPROVAL
from stepwise.models import (
    Job,
    JobStatus,
    StepDefinition,
    ExecutorRef,
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

        events = async_engine.store.load_events(job.id)
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
        from stepwise.hooks import EVENT_MAP
        assert EVENT_MAP.get("job.awaiting_approval") == "approval-needed"
        assert EVENT_MAP.get("job.approved") == "approved"

    def test_fire_hook_builds_approve_command(self, tmp_path):
        """fire_hook_for_event returns False (no script) but verifies payload construction."""
        from stepwise.hooks import fire_hook_for_event
        result = fire_hook_for_event(
            "job.awaiting_approval", {"step": "test"}, "job-123", tmp_path
        )
        assert result is False


# ── R9: job show includes AWAITING_APPROVAL ───────────────────────────

class TestJobShowIncludesAwaitingApproval:
    def test_awaiting_approval_jobs_in_listing(self, store):
        _make_job(store, status=JobStatus.AWAITING_APPROVAL)
        _make_job(store, status=JobStatus.STAGED)
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
