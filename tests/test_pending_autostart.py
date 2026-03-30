"""Tests for PENDING job auto-start after engine initialization (server restart)."""

import asyncio
import time

import pytest

from stepwise.engine import AsyncEngine
from stepwise.executors import ExecutorRegistry
from stepwise.models import (
    ExecutorRef,
    InputBinding,
    Job,
    JobConfig,
    JobStatus,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _gen_id,
    _now,
)
from stepwise.store import SQLiteStore

from tests.conftest import register_step_fn, CallableExecutor


# ── Helpers ────────────────────────────────────────────────────────────


def _make_registry() -> ExecutorRegistry:
    reg = ExecutorRegistry()
    reg.register("callable", lambda config: CallableExecutor(
        fn_name=config.get("fn_name", "default"),
    ))
    return reg


def _simple_workflow(fn_name: str = "echo") -> WorkflowDefinition:
    return WorkflowDefinition(steps={
        "step-a": StepDefinition(
            name="step-a",
            executor=ExecutorRef(type="callable", config={"fn_name": fn_name}),
            outputs=["result"],
        ),
    })


def _create_pending_job(engine: AsyncEngine, objective: str = "test",
                        workflow: WorkflowDefinition | None = None,
                        **kwargs) -> Job:
    """Create a PENDING job directly in the store (simulates pre-restart state)."""
    wf = workflow or _simple_workflow()
    job = Job(
        id=_gen_id("job"),
        objective=objective,
        workflow=wf,
        status=JobStatus.PENDING,
        inputs=kwargs.get("inputs", {}),
        workspace_path="/tmp/test",
        config=JobConfig(),
        created_by=kwargs.get("created_by", "server"),
    )
    if "parent_job_id" in kwargs:
        job.parent_job_id = kwargs["parent_job_id"]
    if "job_group" in kwargs:
        job.job_group = kwargs["job_group"]
    engine.store.save_job(job)
    return job


def _create_completed_job(engine: AsyncEngine, objective: str = "dep") -> Job:
    """Create a COMPLETED job in the store."""
    job = Job(
        id=_gen_id("job"),
        objective=objective,
        workflow=_simple_workflow(),
        status=JobStatus.COMPLETED,
        inputs={},
        workspace_path="/tmp/test",
        config=JobConfig(),
        created_by="server",
    )
    engine.store.save_job(job)
    return job


# ══════════════════════════════════════════════════════════════════════
# Pending job auto-start after engine init
# ══════════════════════════════════════════════════════════════════════


class TestPendingAutoStart:
    """PENDING jobs should be dispatched after engine startup sequence completes."""

    def test_pending_no_deps_starts_after_init(self):
        """PENDING jobs with no dependencies start and complete after startup dispatch."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()
        register_step_fn("echo", lambda inputs: {"result": "ok"})

        engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=5)

        # Simulate pre-restart state: a PENDING job sitting in the store
        job = _create_pending_job(engine, objective="pending-no-deps")

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                # Simulate the server startup sequence
                engine.recover_jobs()
                engine._start_queued_jobs()

                # Job should now be RUNNING
                assert store.load_job(job.id).status == JobStatus.RUNNING

                # Let it complete
                await asyncio.wait_for(engine.wait_for_job(job.id), timeout=10)
                assert store.load_job(job.id).status == JobStatus.COMPLETED
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_pending_with_unmet_deps_stays_pending(self):
        """PENDING jobs whose dependencies are not yet COMPLETED stay PENDING."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()

        engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=5)

        # Create a RUNNING dependency (not completed)
        dep_job = Job(
            id=_gen_id("job"),
            objective="dep-running",
            workflow=_simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
            workspace_path="/tmp/test",
            config=JobConfig(),
            created_by="server",
        )
        store.save_job(dep_job)

        # Create a PENDING job that depends on the running job
        pending_job = _create_pending_job(engine, objective="pending-with-dep")
        store.add_job_dependency(pending_job.id, dep_job.id)

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                engine.recover_jobs()
                engine._start_queued_jobs()

                # Job should stay PENDING (dep not met)
                assert store.load_job(pending_job.id).status == JobStatus.PENDING
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_pending_with_met_deps_starts(self):
        """PENDING jobs whose dependencies are all COMPLETED get started."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()
        register_step_fn("echo", lambda inputs: {"result": "ok"})

        engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=5)

        # Create a completed dependency
        dep_job = _create_completed_job(engine, objective="dep-done")

        # Create a PENDING job that depends on the completed job
        pending_job = _create_pending_job(engine, objective="pending-dep-met")
        store.add_job_dependency(pending_job.id, dep_job.id)

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                engine.recover_jobs()
                engine._start_queued_jobs()

                # Job should be RUNNING now
                assert store.load_job(pending_job.id).status == JobStatus.RUNNING
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_max_concurrent_respected_during_startup(self):
        """_start_queued_jobs respects max_concurrent_jobs during startup dispatch."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()
        register_step_fn("echo", lambda inputs: {"result": "ok"})

        engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=1)

        # One RUNNING job already in the store (survived restart)
        running_job = Job(
            id=_gen_id("job"),
            objective="already-running",
            workflow=_simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
            workspace_path="/tmp/test",
            config=JobConfig(),
            created_by="server",
        )
        store.save_job(running_job)

        # A PENDING job waiting for a slot
        pending_job = _create_pending_job(engine, objective="pending-at-limit")

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                engine._start_queued_jobs()

                # Pending job should stay PENDING (max_concurrent_jobs=1 and 1 already running)
                assert store.load_job(pending_job.id).status == JobStatus.PENDING
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_multiple_pending_started_fifo(self):
        """Multiple PENDING jobs are started in FIFO order up to the concurrency limit."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()
        register_step_fn("echo", lambda inputs: {"result": "ok"})

        engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=3)

        jobs = []
        for i in range(4):
            job = _create_pending_job(engine, objective=f"pending-{i}")
            jobs.append(job)
            time.sleep(0.01)  # ensure distinct created_at for FIFO ordering

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                engine._start_queued_jobs()

                # First 3 should be RUNNING, 4th should stay PENDING
                for i in range(3):
                    status = store.load_job(jobs[i].id).status
                    assert status == JobStatus.RUNNING, (
                        f"Job {i} expected RUNNING, got {status.value}"
                    )
                assert store.load_job(jobs[3].id).status == JobStatus.PENDING
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_no_interference_with_running_recovery(self):
        """Startup dispatch of PENDING jobs doesn't interfere with RUNNING job recovery."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()
        register_step_fn("echo", lambda inputs: {"result": "ok"})

        engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=5)

        # A RUNNING job that needs recovery (has a completed step run, needs settlement)
        running_job = Job(
            id=_gen_id("job"),
            objective="needs-recovery",
            workflow=_simple_workflow(fn_name="echo"),
            status=JobStatus.RUNNING,
            inputs={},
            workspace_path="/tmp/test",
            config=JobConfig(),
            created_by="server",
        )
        store.save_job(running_job)

        # Add a completed step run for the running job
        from stepwise.models import HandoffEnvelope, Sidecar
        run = StepRun(
            id=_gen_id("run"),
            job_id=running_job.id,
            step_name="step-a",
            attempt=1,
            status=StepRunStatus.COMPLETED,
            started_at=_now(),
            completed_at=_now(),
            result=HandoffEnvelope(
                artifact={"result": "ok"},
                sidecar=Sidecar(),
                workspace="/tmp/test",
                timestamp=_now(),
            ),
        )
        store.save_run(run)

        # A PENDING job waiting to start
        pending_job = _create_pending_job(engine, objective="pending-alongside-recovery")

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                # Full startup sequence
                engine.recover_jobs()
                engine._start_queued_jobs()

                # The RUNNING job should have been settled (completed)
                assert store.load_job(running_job.id).status == JobStatus.COMPLETED

                # The PENDING job should have started
                assert store.load_job(pending_job.id).status == JobStatus.RUNNING
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_sub_jobs_not_auto_started(self):
        """PENDING sub-jobs (parent_job_id set) are NOT auto-started by _start_queued_jobs."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()
        register_step_fn("echo", lambda inputs: {"result": "ok"})

        engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=5)

        # Create a parent job
        parent_job = Job(
            id=_gen_id("job"),
            objective="parent",
            workflow=_simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
            workspace_path="/tmp/test",
            config=JobConfig(),
            created_by="server",
        )
        store.save_job(parent_job)

        # Create a PENDING sub-job
        sub_job = _create_pending_job(
            engine, objective="sub-job",
            parent_job_id=parent_job.id,
        )

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                engine._start_queued_jobs()

                # Sub-job should remain PENDING (managed by parent engine, not auto-started)
                assert store.load_job(sub_job.id).status == JobStatus.PENDING
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_unlimited_concurrent_starts_all_pending(self):
        """With max_concurrent_jobs=0 (unlimited), all PENDING jobs start."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()
        register_step_fn("echo", lambda inputs: {"result": "ok"})

        engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=0)

        jobs = []
        for i in range(5):
            job = _create_pending_job(engine, objective=f"pending-{i}")
            jobs.append(job)

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                engine._start_queued_jobs()

                # All should be RUNNING
                for job in jobs:
                    assert store.load_job(job.id).status == JobStatus.RUNNING
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())
