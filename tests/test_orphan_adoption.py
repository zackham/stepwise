"""Tests for orphaned CLI job auto-adoption.

Covers: startup adoption, periodic adoption, for_each parent reconciliation
after adoption, and step failure on dead runner processes.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest

from stepwise.engine import AsyncEngine
from stepwise.executors import ExecutorRegistry
from stepwise.models import (
    ExecutorRef,
    ForEachSpec,
    HandoffEnvelope,
    InputBinding,
    Job,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore
from tests.conftest import CallableExecutor, register_step_fn, run_job_sync


def _make_engine():
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()
    reg.register("callable", lambda config: CallableExecutor(fn_name=config.get("fn_name", "default")))
    return store, reg, AsyncEngine(store=store, registry=reg)


def _simple_workflow():
    return WorkflowDefinition(steps={
        "step-a": StepDefinition(
            name="step-a",
            executor=ExecutorRef(type="callable", config={"fn_name": "pass_through"}),
            outputs=["x"],
        ),
    })


def _two_step_workflow():
    """A→B linear workflow for testing recovery dispatch."""
    return WorkflowDefinition(steps={
        "step-a": StepDefinition(
            name="step-a",
            executor=ExecutorRef(type="callable", config={"fn_name": "produce"}),
            outputs=["val"],
        ),
        "step-b": StepDefinition(
            name="step-b",
            executor=ExecutorRef(type="callable", config={"fn_name": "consume"}),
            inputs=[InputBinding("val", "step-a", "val")],
            outputs=["result"],
        ),
    })


class TestAdoptStaleCLIJob:
    """Tests for _adopt_stale_cli_job() helper."""

    def test_adopt_transfers_ownership(self):
        """Adopted job should become server-owned."""
        from stepwise.server import _adopt_stale_cli_job

        store, reg, engine = _make_engine()
        job = engine.create_job(objective="test-adopt", workflow=_simple_workflow())
        job.status = JobStatus.RUNNING
        job.created_by = "cli:99999"
        job.runner_pid = 99999
        store.save_job(job)

        _adopt_stale_cli_job(engine, job)

        loaded = store.load_job(job.id)
        assert loaded.created_by == "server"
        assert loaded.runner_pid is None

    def test_adopt_fails_running_steps_with_dead_pid(self):
        """Running steps with dead PIDs should be failed on adoption."""
        from stepwise.server import _adopt_stale_cli_job

        store, reg, engine = _make_engine()
        job = engine.create_job(objective="test-adopt", workflow=_simple_workflow())
        job.status = JobStatus.RUNNING
        job.created_by = "cli:99999"
        job.runner_pid = 99999
        store.save_job(job)

        # Create a running step with a dead PID
        run = StepRun(
            id=str(uuid.uuid4()),
            job_id=job.id,
            step_name="step-a",
            attempt=1,
            status=StepRunStatus.RUNNING,
            pid=99999,  # non-existent PID
        )
        store.save_run(run)

        with patch("stepwise.agent._is_pid_alive", return_value=False):
            _adopt_stale_cli_job(engine, job)

        loaded_run = store.load_run(run.id)
        assert loaded_run.status == StepRunStatus.FAILED
        assert "Runner died" in loaded_run.error

    def test_adopt_preserves_running_steps_with_alive_pid(self):
        """Running steps with alive PIDs should NOT be failed."""
        from stepwise.server import _adopt_stale_cli_job

        store, reg, engine = _make_engine()
        job = engine.create_job(objective="test-adopt", workflow=_simple_workflow())
        job.status = JobStatus.RUNNING
        job.created_by = "cli:99999"
        job.runner_pid = 99999
        store.save_job(job)

        run = StepRun(
            id=str(uuid.uuid4()),
            job_id=job.id,
            step_name="step-a",
            attempt=1,
            status=StepRunStatus.RUNNING,
            pid=99999,
        )
        store.save_run(run)

        with patch("stepwise.agent._is_pid_alive", return_value=True):
            _adopt_stale_cli_job(engine, job)

        loaded_run = store.load_run(run.id)
        assert loaded_run.status == StepRunStatus.RUNNING  # still running

    def test_adopt_fails_steps_with_no_pid(self):
        """Running steps with no PID should be failed (can't verify process)."""
        from stepwise.server import _adopt_stale_cli_job

        store, reg, engine = _make_engine()
        job = engine.create_job(objective="test-adopt", workflow=_simple_workflow())
        job.status = JobStatus.RUNNING
        job.created_by = "cli:99999"
        store.save_job(job)

        run = StepRun(
            id=str(uuid.uuid4()),
            job_id=job.id,
            step_name="step-a",
            attempt=1,
            status=StepRunStatus.RUNNING,
            pid=None,
        )
        store.save_run(run)

        _adopt_stale_cli_job(engine, job)

        loaded_run = store.load_run(run.id)
        assert loaded_run.status == StepRunStatus.FAILED
        assert "Runner died" in loaded_run.error


class TestAutoAdoptStaleCLIJobs:
    """Tests for _auto_adopt_stale_cli_jobs() batch function."""

    def test_adopts_stale_jobs(self):
        """Jobs with stale heartbeats should be adopted."""
        from stepwise.server import _auto_adopt_stale_cli_jobs

        store, reg, engine = _make_engine()

        # Create a CLI job with no heartbeat (immediately stale)
        job = engine.create_job(objective="stale-job", workflow=_simple_workflow())
        job.status = JobStatus.RUNNING
        job.created_by = "cli:99999"
        job.runner_pid = 99999
        store.save_job(job)

        adopted = _auto_adopt_stale_cli_jobs(engine, max_age_seconds=0)
        assert job.id in adopted
        assert store.load_job(job.id).created_by == "server"

    def test_skips_fresh_jobs(self):
        """Jobs with recent heartbeats should not be adopted."""
        from stepwise.server import _auto_adopt_stale_cli_jobs

        store, reg, engine = _make_engine()

        job = engine.create_job(objective="fresh-job", workflow=_simple_workflow())
        job.status = JobStatus.RUNNING
        job.created_by = "cli:99999"
        job.runner_pid = 99999
        store.save_job(job)
        store.heartbeat(job.id)  # fresh heartbeat

        adopted = _auto_adopt_stale_cli_jobs(engine, max_age_seconds=60)
        assert job.id not in adopted
        assert store.load_job(job.id).created_by == "cli:99999"

    def test_skips_server_owned_jobs(self):
        """Server-owned jobs should not be adopted (already ours)."""
        from stepwise.server import _auto_adopt_stale_cli_jobs

        store, reg, engine = _make_engine()

        job = engine.create_job(objective="server-job", workflow=_simple_workflow())
        job.status = JobStatus.RUNNING
        job.created_by = "server"
        store.save_job(job)

        adopted = _auto_adopt_stale_cli_jobs(engine, max_age_seconds=0)
        assert job.id not in adopted


class TestAdoptedJobRecovery:
    """Tests that adopted jobs are properly recovered by the engine."""

    def test_adopted_job_settles_when_all_steps_done(self):
        """An adopted job with all steps completed should settle to COMPLETED."""
        from stepwise.server import _adopt_stale_cli_job

        store, reg, engine = _make_engine()
        wf = _simple_workflow()
        job = engine.create_job(objective="test-settle", workflow=wf)
        job.status = JobStatus.RUNNING
        job.created_by = "cli:99999"
        store.save_job(job)

        # Simulate step-a already completed before CLI died
        run = StepRun(
            id=str(uuid.uuid4()),
            job_id=job.id,
            step_name="step-a",
            attempt=1,
            status=StepRunStatus.COMPLETED,
            result=HandoffEnvelope(
                artifact={"x": 42},
                sidecar=Sidecar(),
                workspace=".",
                timestamp=_now(),
            ),
        )
        store.save_run(run)

        # Adopt
        _adopt_stale_cli_job(engine, job)

        # Engine recovery settles it
        engine.recover_jobs()
        loaded = store.load_job(job.id)
        assert loaded.status == JobStatus.COMPLETED

    def test_adopted_job_redispatches_after_failed_step(self):
        """After adoption fails a running step, engine should re-dispatch ready steps."""
        import asyncio
        from stepwise.server import _adopt_stale_cli_job

        register_step_fn("produce", lambda inputs: {"val": 10})
        register_step_fn("consume", lambda inputs: {"result": inputs["val"] * 2})

        store, reg, engine = _make_engine()
        wf = _two_step_workflow()
        job = engine.create_job(objective="test-redispatch", workflow=wf)
        job.status = JobStatus.RUNNING
        job.created_by = "cli:99999"
        store.save_job(job)

        # step-a completed, step-b was running when CLI died
        run_a = StepRun(
            id=str(uuid.uuid4()),
            job_id=job.id,
            step_name="step-a",
            attempt=1,
            status=StepRunStatus.COMPLETED,
            result=HandoffEnvelope(
                artifact={"val": 10},
                sidecar=Sidecar(),
                workspace=".",
                timestamp=_now(),
            ),
        )
        store.save_run(run_a)

        run_b = StepRun(
            id=str(uuid.uuid4()),
            job_id=job.id,
            step_name="step-b",
            attempt=1,
            status=StepRunStatus.RUNNING,
            pid=99999,
        )
        store.save_run(run_b)

        # Adopt — fails step-b
        with patch("stepwise.agent._is_pid_alive", return_value=False):
            _adopt_stale_cli_job(engine, job)

        # step-b should be failed
        loaded_b = store.load_run(run_b.id)
        assert loaded_b.status == StepRunStatus.FAILED

        # Engine recover + dispatch should re-launch step-b (job is now server-owned)
        async def _run_adopted():
            engine.recover_jobs()
            # recover_jobs only checks terminal — explicitly dispatch ready steps
            engine._dispatch_ready(job.id)
            engine_task = asyncio.create_task(engine.run())
            try:
                return await asyncio.wait_for(engine.wait_for_job(job.id), timeout=5)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        result = asyncio.run(_run_adopted())
        assert result.status == JobStatus.COMPLETED
        runs_b = [r for r in store.runs_for_job(job.id) if r.step_name == "step-b"]
        # Should have attempt 2 that completed
        completed_b = [r for r in runs_b if r.status == StepRunStatus.COMPLETED]
        assert len(completed_b) == 1
        assert completed_b[0].result.artifact["result"] == 20


class TestForEachParentReconciliation:
    """Tests that for_each parent jobs settle after sub-job adoption."""

    def test_parent_completes_when_adopted_subjobs_complete(self):
        """Parent for_each job should settle when all sub-jobs are completed."""
        from stepwise.engine import Engine
        from stepwise.executors import ScriptExecutor

        register_step_fn("produce_list", lambda inputs: {"items": ["a", "b"]})
        register_step_fn("process_item", lambda inputs: {"result": f"done_{inputs['item']}"})

        store = SQLiteStore(":memory:")
        reg = ExecutorRegistry()
        reg.register("callable", lambda config: CallableExecutor(fn_name=config.get("fn_name", "default")))
        engine = Engine(store=store, registry=reg)

        sub_flow = WorkflowDefinition(steps={
            "process": StepDefinition(
                name="process", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "process_item"}),
                inputs=[InputBinding("item", "$job", "item")],
            ),
        })

        wf = WorkflowDefinition(steps={
            "produce": StepDefinition(
                name="produce", outputs=["items"],
                executor=ExecutorRef("callable", {"fn_name": "produce_list"}),
            ),
            "each_item": StepDefinition(
                name="each_item", outputs=["results"],
                executor=ExecutorRef("callable", {}),
                for_each=ForEachSpec(
                    source_step="produce",
                    source_field="items",
                    item_var="item",
                ),
                sub_flow=sub_flow,
            ),
        })

        job = engine.create_job("for-each-reconcile", wf)
        engine.start_job(job.id)

        # Run to completion — for_each creates sub-jobs, they complete,
        # parent should reconcile
        for _ in range(100):
            loaded = store.load_job(job.id)
            if loaded.status not in (JobStatus.RUNNING, JobStatus.PENDING):
                break
            engine.tick()

        loaded = store.load_job(job.id)
        assert loaded.status == JobStatus.COMPLETED

    def test_delegated_parent_settles_after_subjob_done(self):
        """_handle_sub_job_done + _check_job_terminal settles parent with single delegation."""
        store, reg, engine = _make_engine()

        # Simple parent workflow with one step that delegates to a sub-job
        wf = WorkflowDefinition(steps={
            "delegate-step": StepDefinition(
                name="delegate-step",
                executor=ExecutorRef("callable", {}),
                outputs=["result"],
            ),
        })

        sub_wf = WorkflowDefinition(steps={
            "work": StepDefinition(
                name="work", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "do_work"}),
            ),
        })

        parent = engine.create_job("parent-job", wf)
        parent.status = JobStatus.RUNNING
        parent.created_by = "server"
        store.save_job(parent)

        sub_job = engine.create_job("sub-job", sub_wf)
        sub_job.status = JobStatus.COMPLETED
        sub_job.parent_job_id = parent.id
        store.save_job(sub_job)

        # Simulate completed sub-job run with result
        sub_run = StepRun(
            id=str(uuid.uuid4()),
            job_id=sub_job.id,
            step_name="work",
            attempt=1,
            status=StepRunStatus.COMPLETED,
            result=HandoffEnvelope(
                artifact={"result": "done"},
                sidecar=Sidecar(),
                workspace=".",
                timestamp=_now(),
            ),
        )
        store.save_run(sub_run)

        # Parent's delegated run
        parent_run = StepRun(
            id=str(uuid.uuid4()),
            job_id=parent.id,
            step_name="delegate-step",
            attempt=1,
            status=StepRunStatus.DELEGATED,
            sub_job_id=sub_job.id,
        )
        store.save_run(parent_run)

        # Trigger sub-job done handling
        engine._handle_sub_job_done(sub_job)

        # Parent's delegated run should now be completed
        loaded_run = store.load_run(parent_run.id)
        assert loaded_run.status == StepRunStatus.COMPLETED

        # Parent job should settle
        loaded_parent = store.load_job(parent.id)
        assert loaded_parent.status == JobStatus.COMPLETED
