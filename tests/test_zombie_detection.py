"""Tests for enhanced zombie step detection and cancel --force/--run."""

import asyncio
from datetime import timedelta
from unittest.mock import patch, MagicMock
import argparse

import pytest

from stepwise.engine import AsyncEngine
from stepwise.executors import ExecutionContext, ExecutorRegistry
from stepwise.models import (
    ExecutorRef,
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

from tests.conftest import CallableExecutor, register_step_fn, run_job_sync


# ── Helpers ────────────────────────────────────────────────────────────


def _make_registry() -> ExecutorRegistry:
    reg = ExecutorRegistry()
    reg.register("callable", lambda config: CallableExecutor(
        fn_name=config.get("fn_name", "default"),
    ))
    return reg


def _simple_workflow(step_name: str = "step-a") -> WorkflowDefinition:
    return WorkflowDefinition(steps={
        step_name: StepDefinition(
            name=step_name,
            executor=ExecutorRef(type="callable", config={"fn_name": "echo"}),
            outputs=["result"],
        ),
    })


def _create_running_job(store: SQLiteStore, **kwargs) -> Job:
    """Create and save a RUNNING job with a simple workflow."""
    job = Job(
        id=_gen_id("job"),
        objective="test",
        workflow=_simple_workflow(),
        status=JobStatus.RUNNING,
        inputs={},
        workspace_path="/tmp/test",
        config=JobConfig(),
        created_by=kwargs.get("created_by", "server"),
    )
    for k, v in kwargs.items():
        if hasattr(job, k):
            setattr(job, k, v)
    store.save_job(job)
    return job


# ══════════════════════════════════════════════════════════════════════
# Feature 1A: PID liveness check in _poll_external_changes
# ══════════════════════════════════════════════════════════════════════


class TestDeadPIDDetection:
    """Test that dead PID detection catches zombie runs."""

    def test_dead_pid_detected_and_run_failed(self):
        """When a run has a task but its PID is dead (>30s), it should be failed."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()
        engine = AsyncEngine(store=store, registry=registry)

        job = _create_running_job(store)

        # Add a RUNNING step run with a PID, started >30s ago
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step-a",
            attempt=1, status=StepRunStatus.RUNNING,
            pid=99999,
            started_at=_now() - timedelta(minutes=2),
        )
        store.save_run(run)

        # Simulate: the run IS in _tasks (thread exists) but PID is dead
        mock_task = MagicMock()
        engine._tasks[run.id] = mock_task

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                with patch("stepwise.process_lifecycle._is_pid_alive", return_value=False):
                    engine._poll_external_changes()
                await asyncio.sleep(0.1)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

        reloaded = store.load_run(run.id)
        assert reloaded.status == StepRunStatus.FAILED
        assert "PID 99999 no longer alive" in reloaded.error
        assert reloaded.error_category == "infra_failure"
        # Task should have been removed and cancelled
        assert run.id not in engine._tasks
        mock_task.cancel.assert_called_once()

    def test_alive_pid_not_affected(self):
        """When a run has a task and its PID is alive, it should not be touched."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()
        engine = AsyncEngine(store=store, registry=registry)

        job = _create_running_job(store)

        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step-a",
            attempt=1, status=StepRunStatus.RUNNING,
            pid=99999,
            started_at=_now() - timedelta(minutes=2),
        )
        store.save_run(run)

        mock_task = MagicMock()
        engine._tasks[run.id] = mock_task

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                with patch("stepwise.process_lifecycle._is_pid_alive", return_value=True):
                    engine._poll_external_changes()
                await asyncio.sleep(0.1)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

        reloaded = store.load_run(run.id)
        assert reloaded.status == StepRunStatus.RUNNING
        assert run.id in engine._tasks

    def test_dead_pid_within_grace_period_not_affected(self):
        """When PID is dead but run is <30s old, it should not be touched (grace period)."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()
        engine = AsyncEngine(store=store, registry=registry)

        job = _create_running_job(store)

        # Run started only 10 seconds ago
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step-a",
            attempt=1, status=StepRunStatus.RUNNING,
            pid=99999,
            started_at=_now() - timedelta(seconds=10),
        )
        store.save_run(run)

        mock_task = MagicMock()
        engine._tasks[run.id] = mock_task

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                with patch("stepwise.process_lifecycle._is_pid_alive", return_value=False):
                    engine._poll_external_changes()
                await asyncio.sleep(0.1)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

        reloaded = store.load_run(run.id)
        assert reloaded.status == StepRunStatus.RUNNING
        assert run.id in engine._tasks

    def test_run_without_pid_not_affected_by_pid_check(self):
        """Runs without a PID should not trigger the dead PID check."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()
        engine = AsyncEngine(store=store, registry=registry)

        job = _create_running_job(store)

        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step-a",
            attempt=1, status=StepRunStatus.RUNNING,
            pid=None,
            started_at=_now() - timedelta(minutes=5),
        )
        store.save_run(run)

        mock_task = MagicMock()
        engine._tasks[run.id] = mock_task

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                with patch("stepwise.process_lifecycle._is_pid_alive", return_value=False) as mock_alive:
                    engine._poll_external_changes()
                    mock_alive.assert_not_called()
                await asyncio.sleep(0.1)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

        reloaded = store.load_run(run.id)
        assert reloaded.status == StepRunStatus.RUNNING


# ══════════════════════════════════════════════════════════════════════
# Feature 1B: cancel --force and cancel --run
# ══════════════════════════════════════════════════════════════════════


class TestCancelForce:
    """Test cancel --force for zombie cleanup on terminal jobs."""

    def test_force_cancels_running_runs_on_failed_job(self):
        """--force should cancel RUNNING step runs on a FAILED job."""
        store = SQLiteStore(":memory:")
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=_simple_workflow(),
            status=JobStatus.FAILED,
            inputs={},
            workspace_path="/tmp/test",
            config=JobConfig(),
            created_by="server",
        )
        store.save_job(job)

        # Add a zombie RUNNING step run
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step-a",
            attempt=1, status=StepRunStatus.RUNNING,
            pid=99999,
            started_at=_now() - timedelta(minutes=10),
        )
        store.save_run(run)

        from stepwise.cli import _cancel_force
        args = argparse.Namespace(output="table")
        # Mock _io to prevent actual output
        with patch("stepwise.cli._io") as mock_io:
            mock_io.return_value = MagicMock()
            result = _cancel_force(args, store, job)

        assert result == 0  # EXIT_SUCCESS
        reloaded = store.load_run(run.id)
        assert reloaded.status == StepRunStatus.FAILED
        assert "Force-cancelled" in reloaded.error
        assert reloaded.error_category == "user_cancelled"

    def test_force_no_running_runs(self):
        """--force with no RUNNING runs should report success with no action."""
        store = SQLiteStore(":memory:")
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=_simple_workflow(),
            status=JobStatus.FAILED,
            inputs={},
            workspace_path="/tmp/test",
            config=JobConfig(),
            created_by="server",
        )
        store.save_job(job)

        # Add a completed run (not RUNNING)
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step-a",
            attempt=1, status=StepRunStatus.COMPLETED,
            started_at=_now() - timedelta(minutes=10),
        )
        store.save_run(run)

        from stepwise.cli import _cancel_force
        args = argparse.Namespace(output="table")
        with patch("stepwise.cli._io") as mock_io:
            mock_io.return_value = MagicMock()
            result = _cancel_force(args, store, job)

        assert result == 0
        reloaded = store.load_run(run.id)
        assert reloaded.status == StepRunStatus.COMPLETED


class TestCancelRun:
    """Test cancel --run for specific run cancellation."""

    def test_cancel_specific_running_run(self, tmp_path):
        """--run should cancel a specific RUNNING step run via _cancel_run."""
        from stepwise.project import init_project
        project = init_project(tmp_path)
        store = SQLiteStore(str(project.db_path))

        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=_simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
            workspace_path="/tmp/test",
            config=JobConfig(),
            created_by="server",
        )
        store.save_job(job)

        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step-a",
            attempt=1, status=StepRunStatus.RUNNING,
            started_at=_now(),
        )
        store.save_run(run)
        store.close()

        from stepwise.cli import _cancel_run
        args = argparse.Namespace(output="json")

        with patch("stepwise.cli._find_project_or_exit", return_value=project):
            result = _cancel_run(args, run.id)

        assert result == 0  # EXIT_SUCCESS

        store2 = SQLiteStore(str(project.db_path))
        reloaded = store2.load_run(run.id)
        store2.close()
        assert reloaded.status == StepRunStatus.FAILED
        assert "Cancelled by user" in reloaded.error
        assert reloaded.error_category == "user_cancelled"

    def test_cancel_run_rejects_non_running(self, tmp_path):
        """--run should reject runs that are not in RUNNING status."""
        from stepwise.project import init_project
        project = init_project(tmp_path)
        store = SQLiteStore(str(project.db_path))

        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=_simple_workflow(),
            status=JobStatus.COMPLETED,
            inputs={},
            workspace_path="/tmp/test",
            config=JobConfig(),
            created_by="server",
        )
        store.save_job(job)

        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step-a",
            attempt=1, status=StepRunStatus.COMPLETED,
            started_at=_now(),
        )
        store.save_run(run)
        store.close()

        from stepwise.cli import _cancel_run, EXIT_USAGE_ERROR
        args = argparse.Namespace(output="json")

        with patch("stepwise.cli._find_project_or_exit", return_value=project):
            result = _cancel_run(args, run.id)

        assert result == EXIT_USAGE_ERROR

    def test_cancel_run_not_found(self, tmp_path):
        """--run with non-existent run ID should return error."""
        from stepwise.project import init_project
        project = init_project(tmp_path)

        from stepwise.cli import _cancel_run, EXIT_JOB_FAILED
        args = argparse.Namespace(output="json")

        with patch("stepwise.cli._find_project_or_exit", return_value=project):
            result = _cancel_run(args, "run-nonexistent")

        assert result == EXIT_JOB_FAILED


class TestCancelRunSettlement:
    """F30: cancel --run must not strand the parent job in RUNNING."""

    def _make_job(self, store, workflow=None, **kwargs):
        job = Job(
            id=_gen_id("job"),
            objective="test",
            workflow=workflow or _simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
            workspace_path="/tmp/test",
            config=JobConfig(),
            created_by=kwargs.pop("created_by", "server"),
        )
        for k, v in kwargs.items():
            setattr(job, k, v)
        store.save_job(job)
        return job

    def _make_run(self, store, job, step_name="step-a", status=StepRunStatus.RUNNING):
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name=step_name,
            attempt=1, status=status, started_at=_now(),
        )
        store.save_run(run)
        return run

    def test_cancel_run_settles_orphaned_job(self, tmp_path):
        """Cancelling the only active run of an orphaned job settles the job
        (FAILED) instead of leaving it RUNNING forever."""
        import json as json_mod
        from stepwise.project import init_project
        project = init_project(tmp_path)
        store = SQLiteStore(str(project.db_path))
        job = self._make_job(store)  # runner_pid unset → no live owner
        run = self._make_run(store, job)
        store.close()

        from stepwise.cli import _cancel_run, EXIT_SUCCESS
        args = argparse.Namespace(output="json")

        with patch("stepwise.cli._find_project_or_exit", return_value=project):
            result = _cancel_run(args, run.id)

        assert result == EXIT_SUCCESS

        store2 = SQLiteStore(str(project.db_path))
        reloaded_run = store2.load_run(run.id)
        reloaded_job = store2.load_job(job.id)
        store2.close()
        assert reloaded_run.status == StepRunStatus.FAILED
        assert reloaded_job.status == JobStatus.FAILED  # settled, not stranded

    def test_cancel_run_leaves_job_running_with_other_active_runs(self, tmp_path):
        """A sibling step still RUNNING keeps the job alive."""
        from stepwise.project import init_project
        project = init_project(tmp_path)
        store = SQLiteStore(str(project.db_path))

        workflow = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "echo"}),
                outputs=["result"],
            ),
            "step-b": StepDefinition(
                name="step-b",
                executor=ExecutorRef(type="callable", config={"fn_name": "echo"}),
                outputs=["result"],
            ),
        })
        job = self._make_job(store, workflow=workflow)
        run_a = self._make_run(store, job, "step-a")
        self._make_run(store, job, "step-b")
        store.close()

        from stepwise.cli import _cancel_run, EXIT_SUCCESS
        args = argparse.Namespace(output="json")

        with patch("stepwise.cli._find_project_or_exit", return_value=project):
            result = _cancel_run(args, run_a.id)

        assert result == EXIT_SUCCESS
        store2 = SQLiteStore(str(project.db_path))
        assert store2.load_run(run_a.id).status == StepRunStatus.FAILED
        assert store2.load_job(job.id).status == JobStatus.RUNNING
        store2.close()

    def test_cancel_run_refuses_when_owner_alive(self, tmp_path):
        """A run whose job is owned by a live engine process is refused —
        flipping it behind the engine's back would strand or race the job."""
        from stepwise.project import init_project
        project = init_project(tmp_path)
        store = SQLiteStore(str(project.db_path))
        job = self._make_job(store, runner_pid=999999)
        run = self._make_run(store, job)
        store.close()

        from stepwise.cli import _cancel_run, EXIT_USAGE_ERROR
        args = argparse.Namespace(output="json")

        with patch("stepwise.cli._find_project_or_exit", return_value=project), \
             patch("stepwise.server_detect._pid_alive", return_value=True):
            result = _cancel_run(args, run.id)

        assert result == EXIT_USAGE_ERROR
        store2 = SQLiteStore(str(project.db_path))
        assert store2.load_run(run.id).status == StepRunStatus.RUNNING  # untouched
        assert store2.load_job(job.id).status == JobStatus.RUNNING
        store2.close()
