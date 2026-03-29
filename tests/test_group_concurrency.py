"""Tests for per-group job concurrency limits."""

import time
import uuid

from stepwise.models import (
    Job,
    JobStatus,
    StepDefinition,
    WorkflowDefinition,
    ExecutorRef,
)
from stepwise.store import SQLiteStore

from tests.conftest import register_step_fn, run_job_sync


def _simple_wf(fn_name: str = "instant") -> WorkflowDefinition:
    return WorkflowDefinition(steps={
        "s": StepDefinition(
            name="s",
            executor=ExecutorRef(type="callable", config={"fn_name": fn_name}),
            outputs=["ok"],
        ),
    })


class TestGroupSettings:
    """Store-level group settings CRUD."""

    def test_default_max_concurrent_is_zero(self, store):
        assert store.get_group_max_concurrent("mygroup") == 0

    def test_set_and_get(self, store):
        store.set_group_max_concurrent("mygroup", 3)
        assert store.get_group_max_concurrent("mygroup") == 3

    def test_upsert_overwrites(self, store):
        store.set_group_max_concurrent("g", 5)
        store.set_group_max_concurrent("g", 2)
        assert store.get_group_max_concurrent("g") == 2

    def test_list_group_settings(self, store):
        store.set_group_max_concurrent("a", 3)
        store.set_group_max_concurrent("b", 5)
        assert store.list_group_settings() == {"a": 3, "b": 5}

    def test_active_jobs_in_group(self, store):
        wf = _simple_wf()
        for i in range(3):
            job = Job(id=str(uuid.uuid4()), objective=f"j{i}", workflow=wf, job_group="batch")
            job.status = JobStatus.RUNNING if i < 2 else JobStatus.PENDING
            store.save_job(job)
        active = store.active_jobs_in_group("batch")
        assert len(active) == 2

    def test_active_jobs_in_group_excludes_sub_jobs(self, store):
        wf = _simple_wf()
        # Parent job
        parent = Job(id=str(uuid.uuid4()), objective="parent", workflow=wf, job_group="batch")
        parent.status = JobStatus.RUNNING
        store.save_job(parent)
        # Sub-job (should not count)
        child = Job(id=str(uuid.uuid4()), objective="child", workflow=wf, job_group="batch")
        child.status = JobStatus.RUNNING
        child.parent_job_id = parent.id
        store.save_job(child)
        active = store.active_jobs_in_group("batch")
        assert len(active) == 1
        assert active[0].id == parent.id


class TestGroupConcurrency:
    """Engine-level group concurrency enforcement."""

    def test_job_starts_when_group_slot_opens(self, async_engine):
        """When a group job completes, next PENDING job auto-starts."""
        register_step_fn("instant", lambda inputs: {"ok": True})
        wf = _simple_wf("instant")
        async_engine.store.set_group_max_concurrent("batch", 1)

        j1 = async_engine.create_job(objective="j1", workflow=wf)
        j1.job_group = "batch"
        j1.status = JobStatus.PENDING
        async_engine.store.save_job(j1)

        j2 = async_engine.create_job(objective="j2", workflow=wf)
        j2.job_group = "batch"
        j2.status = JobStatus.PENDING
        async_engine.store.save_job(j2)

        # Run j1 to completion — j2 should auto-start via _start_queued_jobs
        result = run_job_sync(async_engine, j1.id, timeout=5)
        assert result.status == JobStatus.COMPLETED

        # j2 should have been started by _start_queued_jobs after j1 completed
        j2_updated = async_engine.store.load_job(j2.id)
        assert j2_updated.status in (JobStatus.RUNNING, JobStatus.COMPLETED)

    def test_group_limit_blocks_via_start_job(self, async_engine):
        """start_job() queues job when group is at capacity."""
        register_step_fn("instant", lambda inputs: {"ok": True})
        wf = _simple_wf("instant")
        async_engine.store.set_group_max_concurrent("batch", 1)

        # Manually put j1 in RUNNING state
        j1 = async_engine.create_job(objective="j1", workflow=wf)
        j1.job_group = "batch"
        j1.status = JobStatus.RUNNING
        async_engine.store.save_job(j1)

        # j2 should be blocked by group limit
        j2 = async_engine.create_job(objective="j2", workflow=wf)
        j2.job_group = "batch"
        j2.status = JobStatus.PENDING
        async_engine.store.save_job(j2)

        async_engine.start_job(j2.id)
        assert async_engine.store.load_job(j2.id).status == JobStatus.PENDING

    def test_group_limit_zero_means_unlimited(self, async_engine):
        """Default (0) allows jobs to start without group constraint."""
        register_step_fn("instant", lambda inputs: {"ok": True})
        wf = _simple_wf("instant")
        # No group setting = 0 = unlimited

        # Manually put one job in RUNNING
        j1 = async_engine.create_job(objective="j1", workflow=wf)
        j1.job_group = "batch"
        j1.status = JobStatus.RUNNING
        async_engine.store.save_job(j1)

        # j2 should still be able to start (no limit)
        j2 = async_engine.create_job(objective="j2", workflow=wf)
        j2.job_group = "batch"
        j2.status = JobStatus.PENDING
        async_engine.store.save_job(j2)

        # start_job should transition to RUNNING (no group limit)
        # Use run_job_sync to start properly
        result = run_job_sync(async_engine, j2.id, timeout=5)
        assert result.status == JobStatus.COMPLETED

    def test_group_limit_ignores_other_groups(self, async_engine):
        """Jobs in group-A don't count against group-B's limit."""
        register_step_fn("instant", lambda inputs: {"ok": True})
        wf = _simple_wf("instant")
        async_engine.store.set_group_max_concurrent("alpha", 1)
        async_engine.store.set_group_max_concurrent("beta", 1)

        # Put one RUNNING job in alpha
        ja_run = async_engine.create_job(objective="a-running", workflow=wf)
        ja_run.job_group = "alpha"
        ja_run.status = JobStatus.RUNNING
        async_engine.store.save_job(ja_run)

        # Beta job should start fine (different group)
        jb = async_engine.create_job(objective="b1", workflow=wf)
        jb.job_group = "beta"
        jb.status = JobStatus.PENDING
        async_engine.store.save_job(jb)

        result = run_job_sync(async_engine, jb.id, timeout=5)
        assert result.status == JobStatus.COMPLETED

    def test_global_and_group_limits_both_enforced(self, async_engine):
        """start_job checks group limit even when global limit has room."""
        register_step_fn("instant", lambda inputs: {"ok": True})
        wf = _simple_wf("instant")
        async_engine.max_concurrent_jobs = 10  # plenty of global room
        async_engine.store.set_group_max_concurrent("batch", 1)

        # One RUNNING job in the group
        j1 = async_engine.create_job(objective="j1", workflow=wf)
        j1.job_group = "batch"
        j1.status = JobStatus.RUNNING
        async_engine.store.save_job(j1)

        # Second job should be blocked by group limit
        j2 = async_engine.create_job(objective="j2", workflow=wf)
        j2.job_group = "batch"
        j2.status = JobStatus.PENDING
        async_engine.store.save_job(j2)

        async_engine.start_job(j2.id)
        assert async_engine.store.load_job(j2.id).status == JobStatus.PENDING
