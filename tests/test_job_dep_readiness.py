"""Tests for job dependency readiness: when a job completes, pending dependents auto-start."""

from __future__ import annotations

import asyncio

import pytest

from stepwise.engine import AsyncEngine, Engine
from stepwise.models import (
    ExecutorRef,
    InputBinding,
    JobStatus,
    StepDefinition,
    WorkflowDefinition,
)
from stepwise.store import SQLiteStore
from tests.conftest import CallableExecutor, register_step_fn, run_job_sync


def _simple_wf(fn_name: str = "noop") -> WorkflowDefinition:
    return WorkflowDefinition(steps={
        "step-a": StepDefinition(
            name="step-a",
            executor=ExecutorRef(type="callable", config={"fn_name": fn_name}),
            outputs=["result"],
        ),
    })


@pytest.fixture(autouse=True)
def _register_noop():
    register_step_fn("noop", lambda inputs: {"result": "ok"})
    yield
    from tests.conftest import clear_step_fns
    clear_step_fns()


class TestAsyncEngineDependentJobs:
    """AsyncEngine: completing a job starts pending dependents whose deps are all met."""

    def test_dependent_auto_starts_on_completion(self, async_engine):
        """Job B depends on A. Starting A → A completes → B auto-starts and completes."""

        job_a = async_engine.create_job(objective="job-a", workflow=_simple_wf())
        job_b = async_engine.create_job(objective="job-b", workflow=_simple_wf())
        async_engine.store.add_job_dependency(job_b.id, job_a.id)

        async def run_both():
            engine_task = asyncio.create_task(async_engine.run())
            try:
                async_engine.start_job(job_a.id)
                # Wait for B to reach terminal state (it should be auto-started by A's completion)
                await asyncio.wait_for(async_engine.wait_for_job(job_b.id), timeout=10)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_both())

        result_a = async_engine.store.load_job(job_a.id)
        result_b = async_engine.store.load_job(job_b.id)
        assert result_a.status == JobStatus.COMPLETED
        assert result_b.status == JobStatus.COMPLETED

    def test_dependent_waits_for_all_deps(self, async_engine):
        """Job C depends on both A and B. C should not start until both complete."""

        # A→C and B→C: C needs both A and B to be COMPLETED.
        # B also depends on A so we control execution order.
        job_a = async_engine.create_job(objective="job-a", workflow=_simple_wf())
        job_b = async_engine.create_job(objective="job-b", workflow=_simple_wf())
        job_c = async_engine.create_job(objective="job-c", workflow=_simple_wf())
        async_engine.store.add_job_dependency(job_b.id, job_a.id)  # B depends on A
        async_engine.store.add_job_dependency(job_c.id, job_a.id)  # C depends on A
        async_engine.store.add_job_dependency(job_c.id, job_b.id)  # C depends on B

        async def run_all():
            engine_task = asyncio.create_task(async_engine.run())
            try:
                # Start A — B and C should cascade via _check_dependent_jobs
                async_engine.start_job(job_a.id)
                await asyncio.wait_for(async_engine.wait_for_job(job_c.id), timeout=10)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_all())

        assert async_engine.store.load_job(job_a.id).status == JobStatus.COMPLETED
        assert async_engine.store.load_job(job_b.id).status == JobStatus.COMPLETED
        assert async_engine.store.load_job(job_c.id).status == JobStatus.COMPLETED

    def test_non_pending_dependents_ignored(self, async_engine):
        """Dependents not in PENDING status are not started."""

        job_a = async_engine.create_job(objective="job-a", workflow=_simple_wf())
        job_b = async_engine.create_job(objective="job-b", workflow=_simple_wf())
        async_engine.store.add_job_dependency(job_b.id, job_a.id)

        # Manually set B to STAGED (not PENDING) — should not auto-start
        job_b.status = JobStatus.STAGED
        async_engine.store.save_job(job_b)

        result_a = run_job_sync(async_engine, job_a.id)
        assert result_a.status == JobStatus.COMPLETED

        result_b = async_engine.store.load_job(job_b.id)
        assert result_b.status == JobStatus.STAGED, "STAGED job should not be auto-started"


class TestSyncEngineDependentJobs:
    """Sync Engine: completing a job starts pending dependents whose deps are all met."""

    def test_dependent_auto_starts_on_completion(self, engine):
        """Job B depends on A. Starting A → A completes → B auto-starts and completes."""

        job_a = engine.create_job(objective="job-a", workflow=_simple_wf())
        job_b = engine.create_job(objective="job-b", workflow=_simple_wf())
        engine.store.add_job_dependency(job_b.id, job_a.id)

        engine.start_job(job_a.id)

        result_a = engine.store.load_job(job_a.id)
        result_b = engine.store.load_job(job_b.id)
        assert result_a.status == JobStatus.COMPLETED
        assert result_b.status == JobStatus.COMPLETED

    def test_dependent_waits_for_all_deps(self, engine):
        """Job C depends on A and B. C starts only after both complete."""

        job_a = engine.create_job(objective="job-a", workflow=_simple_wf())
        job_b = engine.create_job(objective="job-b", workflow=_simple_wf())
        job_c = engine.create_job(objective="job-c", workflow=_simple_wf())
        engine.store.add_job_dependency(job_c.id, job_a.id)
        engine.store.add_job_dependency(job_c.id, job_b.id)

        # Start A — C should NOT start (B not done)
        engine.start_job(job_a.id)
        assert engine.store.load_job(job_c.id).status == JobStatus.PENDING

        # Start B — now C should auto-start
        engine.start_job(job_b.id)
        assert engine.store.load_job(job_a.id).status == JobStatus.COMPLETED
        assert engine.store.load_job(job_b.id).status == JobStatus.COMPLETED
        assert engine.store.load_job(job_c.id).status == JobStatus.COMPLETED
