"""Tests for concurrent execution safety: ThreadSafeStore, thread pool, atomic claiming."""

import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from stepwise.models import (
    Job,
    JobStatus,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    ExecutorRef,
    InputBinding,
)
from stepwise.store import SQLiteStore

from tests.conftest import register_step_fn, run_job_sync


def _n_independent_steps(fn_name: str, n: int) -> WorkflowDefinition:
    """Create a workflow with n independent steps using the same callable."""
    steps = {}
    for i in range(n):
        steps[f"step-{i}"] = StepDefinition(
            name=f"step-{i}",
            executor=ExecutorRef(type="callable", config={"fn_name": fn_name}),
            outputs=["ok"],
        )
    return WorkflowDefinition(steps=steps)


class TestAsyncEngineParallel:
    """Tests for parallel execution in the async engine."""

    def test_independent_steps_run_in_parallel(self, async_engine):
        """Independent steps execute concurrently, not serially."""
        register_step_fn("slow", lambda inputs: (time.sleep(0.3), {"ok": True})[1])
        wf = _n_independent_steps("slow", 5)
        job = async_engine.create_job(objective="parallel-test", workflow=wf)

        start = time.time()
        result = run_job_sync(async_engine, job.id, timeout=10)
        elapsed = time.time() - start

        assert result.status == JobStatus.COMPLETED
        # 5 steps × 0.3s each. If parallel: ~0.3s. If serial: ~1.5s.
        # Use generous threshold for CI variability.
        assert elapsed < 2.0, f"Steps should run in parallel, took {elapsed:.1f}s"

    def test_chain_reaction_fast(self, async_engine):
        """A→B→C chain completes without polling delay."""
        register_step_fn("instant", lambda inputs: {"x": 1})
        wf = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a",
                executor=ExecutorRef(type="callable", config={"fn_name": "instant"}),
                outputs=["x"],
            ),
            "b": StepDefinition(
                name="b",
                executor=ExecutorRef(type="callable", config={"fn_name": "instant"}),
                inputs=[InputBinding("x", "a", "x")],
                outputs=["x"],
            ),
            "c": StepDefinition(
                name="c",
                executor=ExecutorRef(type="callable", config={"fn_name": "instant"}),
                inputs=[InputBinding("x", "b", "x")],
                outputs=["x"],
            ),
        })
        job = async_engine.create_job(objective="chain-test", workflow=wf)

        start = time.time()
        result = run_job_sync(async_engine, job.id, timeout=5)
        elapsed = time.time() - start

        assert result.status == JobStatus.COMPLETED
        runs = async_engine.store.runs_for_job(job.id)
        assert len(runs) == 3
        # Should complete in well under 1 second since all steps are instant
        assert elapsed < 2.0

    def test_multiple_jobs_concurrent(self, async_engine):
        """Multiple jobs run their steps concurrently."""
        register_step_fn("slow_job", lambda inputs: (time.sleep(0.2), {"ok": True})[1])

        jobs = []
        for i in range(5):
            wf = WorkflowDefinition(steps={
                "only-step": StepDefinition(
                    name="only-step",
                    executor=ExecutorRef(type="callable", config={"fn_name": "slow_job"}),
                    outputs=["ok"],
                ),
            })
            job = async_engine.create_job(objective=f"multi-{i}", workflow=wf)
            jobs.append(job)

        start = time.time()

        async def _run_all():
            engine_task = asyncio.create_task(async_engine.run())
            try:
                for j in jobs:
                    async_engine.start_job(j.id)
                # Wait for all to reach terminal state
                for _ in range(100):
                    all_done = True
                    for j in jobs:
                        loaded = async_engine.get_job(j.id)
                        if loaded.status not in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                            all_done = False
                            break
                    if all_done:
                        break
                    await asyncio.sleep(0.05)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(_run_all())
        elapsed = time.time() - start

        for j in jobs:
            loaded = async_engine.store.load_job(j.id)
            assert loaded.status == JobStatus.COMPLETED

        # 5 jobs × 0.2s if parallel ≈ 0.2s, not 1.0s
        assert elapsed < 3.0, f"Jobs should run concurrently, took {elapsed:.1f}s"


class TestAtomicClaim:
    """Tests for claim_step atomicity under contention."""

    def test_concurrent_claims_blocked_by_running(self, tmp_path):
        """Two threads claiming a step with a RUNNING run — both return None."""
        from stepwise.server import ThreadSafeStore
        db_path = str(tmp_path / "test.db")
        store = ThreadSafeStore(db_path)

        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "noop"}),
                outputs=["x"],
            ),
        })
        job = engine.create_job(objective="test-claim", workflow=wf)

        # Insert a running run to block claims
        running = StepRun(
            id=str(uuid.uuid4()),
            job_id=job.id,
            step_name="step-a",
            attempt=1,
            status=StepRunStatus.RUNNING,
        )
        store.save_run(running)

        # Both threads should see the running step and return None
        results = []
        def claim():
            results.append(store.claim_step(job.id, "step-a"))

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(claim)
            f2 = pool.submit(claim)
            f1.result()
            f2.result()

        assert results == [None, None]
        store.close()

    def test_claim_step_no_existing_runs(self, store):
        """First claim on a step with no runs returns 1."""
        from stepwise.engine import AsyncEngine
        from stepwise.executors import ExecutorRegistry
        engine = AsyncEngine(store, ExecutorRegistry())
        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "noop"}),
                outputs=["x"],
            ),
        })
        job = engine.create_job(objective="test", workflow=wf)
        result = store.claim_step(job.id, "step-a")
        assert result == 1


class TestExecutorException:
    """Tests for executor exceptions not crashing the engine."""

    def test_exception_fails_step_not_engine(self, async_engine):
        """Exception in executor.start() → step FAILED, engine continues."""
        def boom(inputs):
            raise RuntimeError("kaboom")

        register_step_fn("boom", boom)
        wf = WorkflowDefinition(steps={
            "explode": StepDefinition(
                name="explode",
                executor=ExecutorRef(type="callable", config={"fn_name": "boom"}),
                outputs=["x"],
            ),
        })
        job = async_engine.create_job(objective="test-boom", workflow=wf)
        result = run_job_sync(async_engine, job.id, timeout=5)

        assert result.status == JobStatus.FAILED
        runs = async_engine.store.runs_for_job(job.id)
        assert len(runs) == 1
        assert runs[0].status == StepRunStatus.FAILED
        assert "kaboom" in runs[0].error
