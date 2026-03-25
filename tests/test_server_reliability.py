"""Tests for server reliability fixes: H7, H8, H10, H12, H13."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from stepwise.engine import AsyncEngine, MAX_ARTIFACT_BYTES
from stepwise.events import JOB_QUEUED
from stepwise.executors import ExecutorRegistry
from stepwise.models import (
    ExitRule,
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    Job,
    JobConfig,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _gen_id,
    _now,
)
from stepwise.store import SQLiteStore

from tests.conftest import register_step_fn, run_job_sync, CallableExecutor


# ── Helpers ────────────────────────────────────────────────────────────


def _make_registry() -> ExecutorRegistry:
    reg = ExecutorRegistry()
    reg.register("callable", lambda config: CallableExecutor(
        fn_name=config.get("fn_name", "default"),
    ))
    return reg


def _simple_workflow(step_name: str = "step-a", fn_name: str = "echo") -> WorkflowDefinition:
    return WorkflowDefinition(steps={
        step_name: StepDefinition(
            name=step_name,
            executor=ExecutorRef(type="callable", config={"fn_name": fn_name}),
            outputs=["result"],
        ),
    })


def _create_running_job(store: SQLiteStore, **kwargs) -> Job:
    """Create and save a RUNNING job with a simple workflow."""
    job_id = _gen_id("job")
    job = Job(
        id=job_id,
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
# H7: Zombie job resurrection on restart
# ══════════════════════════════════════════════════════════════════════


class TestH7ZombieJobCleanup:
    """Test _cleanup_zombie_jobs behavior on server restart."""

    def _make_store(self):
        """Create a ThreadSafeStore-like store (using plain SQLiteStore for tests)."""
        return SQLiteStore(":memory:")

    def test_completed_job_survives_cleanup(self):
        """Completed jobs must not be touched by cleanup."""
        store = self._make_store()
        job = _create_running_job(store)
        job.status = JobStatus.COMPLETED
        store.save_job(job)

        from stepwise.server import _cleanup_zombie_jobs
        _cleanup_zombie_jobs(store)

        reloaded = store.load_job(job.id)
        assert reloaded.status == JobStatus.COMPLETED

    def test_failed_job_survives_cleanup(self):
        """Failed jobs must not be touched by cleanup."""
        store = self._make_store()
        job = _create_running_job(store)
        job.status = JobStatus.FAILED
        store.save_job(job)

        from stepwise.server import _cleanup_zombie_jobs
        _cleanup_zombie_jobs(store)

        reloaded = store.load_job(job.id)
        assert reloaded.status == JobStatus.FAILED

    def test_running_job_with_running_steps_is_failed(self):
        """RUNNING job with orphaned running steps is failed on cleanup."""
        store = self._make_store()
        job = _create_running_job(store)

        # Add a RUNNING step run
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step-a",
            attempt=1, status=StepRunStatus.RUNNING,
            started_at=_now(),
        )
        store.save_run(run)

        from stepwise.server import _cleanup_zombie_jobs
        _cleanup_zombie_jobs(store)

        reloaded_job = store.load_job(job.id)
        assert reloaded_job.status == JobStatus.FAILED

        reloaded_run = store.load_run(run.id)
        assert reloaded_run.status == StepRunStatus.FAILED
        assert "Server restarted" in reloaded_run.error

    def test_running_job_with_completed_steps_is_recovered(self):
        """RUNNING job with all terminal steps completed is recovered as COMPLETED."""
        store = self._make_store()
        job = _create_running_job(store)

        # Add a COMPLETED step run for the terminal step
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step-a",
            attempt=1, status=StepRunStatus.COMPLETED,
            result=HandoffEnvelope(
                artifact={"result": "done"},
                sidecar=Sidecar(),
                workspace="/tmp/test",
                timestamp=_now(),
            ),
            started_at=_now(), completed_at=_now(),
        )
        store.save_run(run)

        from stepwise.server import _cleanup_zombie_jobs
        _cleanup_zombie_jobs(store)

        reloaded = store.load_job(job.id)
        assert reloaded.status == JobStatus.COMPLETED

    def test_paused_job_is_not_touched(self):
        """PAUSED jobs are not touched by cleanup (they're not RUNNING)."""
        store = self._make_store()
        job = _create_running_job(store)
        job.status = JobStatus.PAUSED
        store.save_job(job)

        from stepwise.server import _cleanup_zombie_jobs
        _cleanup_zombie_jobs(store)

        reloaded = store.load_job(job.id)
        assert reloaded.status == JobStatus.PAUSED

    def test_cli_owned_job_is_skipped(self):
        """Jobs with created_by != 'server' are skipped by cleanup."""
        store = self._make_store()
        job = _create_running_job(store, created_by="cli:12345")

        from stepwise.server import _cleanup_zombie_jobs
        _cleanup_zombie_jobs(store)

        reloaded = store.load_job(job.id)
        assert reloaded.status == JobStatus.RUNNING  # not touched

    def test_suspended_job_is_preserved(self):
        """RUNNING jobs with suspended steps are left alone for normal resumption."""
        store = self._make_store()
        job = _create_running_job(store)

        # Add a SUSPENDED step run
        from stepwise.models import WatchSpec
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step-a",
            attempt=1, status=StepRunStatus.SUSPENDED,
            watch=WatchSpec(mode="external", config={}, fulfillment_outputs=["result"]),
            started_at=_now(),
        )
        store.save_run(run)

        from stepwise.server import _cleanup_zombie_jobs
        _cleanup_zombie_jobs(store)

        reloaded = store.load_job(job.id)
        assert reloaded.status == JobStatus.RUNNING  # preserved

    def test_engine_recover_jobs(self):
        """recover_jobs settles RUNNING jobs whose steps are all COMPLETED."""
        store = self._make_store()
        registry = _make_registry()
        engine = AsyncEngine(store=store, registry=registry)

        job = _create_running_job(store)

        # Add a COMPLETED step run for the terminal step
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step-a",
            attempt=1, status=StepRunStatus.COMPLETED,
            result=HandoffEnvelope(
                artifact={"result": "done"},
                sidecar=Sidecar(),
                workspace="/tmp/test",
                timestamp=_now(),
            ),
            started_at=_now(), completed_at=_now(),
        )
        store.save_run(run)

        engine.recover_jobs()

        reloaded = store.load_job(job.id)
        assert reloaded.status == JobStatus.COMPLETED


# ══════════════════════════════════════════════════════════════════════
# H8: Concurrent job limit and artifact size guard
# ══════════════════════════════════════════════════════════════════════


class TestH8ConcurrentJobLimit:
    """Test AsyncEngine.max_concurrent_jobs enforcement."""

    def test_concurrent_job_limit(self):
        """Jobs beyond max_concurrent_jobs stay PENDING until a slot opens."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()

        # Use a slow function so jobs stay RUNNING during test
        import time
        register_step_fn("slow", lambda inputs: (time.sleep(0.5), {"result": "ok"})[1])

        engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=2)

        wf = _simple_workflow(fn_name="slow")
        jobs = []
        for i in range(4):
            job = engine.create_job(objective=f"test-{i}", workflow=wf)
            jobs.append(job)

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                # Start all 4 jobs
                for job in jobs:
                    engine.start_job(job.id)

                # First 2 should be RUNNING, other 2 should be PENDING
                await asyncio.sleep(0.1)
                statuses = [store.load_job(j.id).status for j in jobs]
                running_count = sum(1 for s in statuses if s == JobStatus.RUNNING)
                pending_count = sum(1 for s in statuses if s == JobStatus.PENDING)
                assert running_count == 2, f"Expected 2 running, got {running_count} (statuses: {statuses})"
                assert pending_count == 2, f"Expected 2 pending, got {pending_count}"

                # Wait for all to complete
                for job in jobs:
                    await asyncio.wait_for(engine.wait_for_job(job.id), timeout=10)

                # All should be COMPLETED now
                for job in jobs:
                    assert store.load_job(job.id).status == JobStatus.COMPLETED
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_artifact_size_guard(self):
        """Artifacts over MAX_ARTIFACT_BYTES cause step failure."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()

        # Generate artifact just over limit
        big_data = "x" * (MAX_ARTIFACT_BYTES + 1000)
        register_step_fn("big", lambda inputs: {"result": big_data})

        engine = AsyncEngine(store=store, registry=registry)
        wf = _simple_workflow(fn_name="big")
        job = engine.create_job(objective="test-big", workflow=wf)
        result = run_job_sync(engine, job.id, timeout=10)

        assert result.status == JobStatus.FAILED
        runs = store.runs_for_job(job.id)
        failed_runs = [r for r in runs if r.status == StepRunStatus.FAILED]
        assert len(failed_runs) >= 1
        assert "too large" in failed_runs[0].error

    def test_queued_job_starts_on_completion(self):
        """When a running job completes, queued pending jobs are auto-started."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()

        register_step_fn("echo", lambda inputs: {"result": "ok"})

        engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=1)
        wf = _simple_workflow(fn_name="echo")

        job1 = engine.create_job(objective="test-1", workflow=wf)
        job2 = engine.create_job(objective="test-2", workflow=wf)

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                engine.start_job(job1.id)
                engine.start_job(job2.id)

                # job2 should be queued (PENDING)
                assert store.load_job(job2.id).status == JobStatus.PENDING

                # Wait for both to complete
                await asyncio.wait_for(engine.wait_for_job(job1.id), timeout=10)
                await asyncio.wait_for(engine.wait_for_job(job2.id), timeout=10)

                # Both should be completed
                assert store.load_job(job1.id).status == JobStatus.COMPLETED
                assert store.load_job(job2.id).status == JobStatus.COMPLETED
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_no_concurrent_limit_when_zero(self):
        """When max_concurrent_jobs=0, no queueing occurs (unlimited)."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()

        register_step_fn("echo", lambda inputs: {"result": "ok"})

        engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=0)
        wf = _simple_workflow(fn_name="echo")

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                jobs = []
                for i in range(5):
                    job = engine.create_job(objective=f"test-{i}", workflow=wf)
                    jobs.append(job)
                    engine.start_job(job.id)

                # All should be RUNNING (not queued)
                await asyncio.sleep(0.05)
                for job in jobs:
                    status = store.load_job(job.id).status
                    assert status != JobStatus.PENDING, f"Job {job.id} should not be PENDING"

                # Wait for all to complete
                for job in jobs:
                    await asyncio.wait_for(engine.wait_for_job(job.id), timeout=10)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_pending_jobs_query(self):
        """store.pending_jobs() returns PENDING jobs in FIFO order."""
        store = SQLiteStore(":memory:")
        import time

        pending_ids = []
        for i in range(3):
            job = Job(
                id=_gen_id("job"),
                objective=f"test-{i}",
                workflow=_simple_workflow(),
                status=JobStatus.PENDING,
                inputs={},
                workspace_path="/tmp/test",
                config=JobConfig(),
            )
            store.save_job(job)
            pending_ids.append(job.id)
            time.sleep(0.01)  # ensure distinct created_at

        # Add a RUNNING job (should not appear)
        running = Job(
            id=_gen_id("job"),
            objective="running",
            workflow=_simple_workflow(),
            status=JobStatus.RUNNING,
            inputs={},
            workspace_path="/tmp/test",
            config=JobConfig(),
        )
        store.save_job(running)

        result = store.pending_jobs()
        assert len(result) == 3
        assert [j.id for j in result] == pending_ids


# ══════════════════════════════════════════════════════════════════════
# Pause releases concurrency slot for queued jobs
# ══════════════════════════════════════════════════════════════════════


class TestPauseReleasesSlot:
    """Pausing a job (API or escalate) should free a concurrency slot for queued jobs."""

    def test_pause_job_starts_queued(self):
        """When a running job is paused via API, a pending queued job starts."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()

        import threading
        gate = threading.Event()

        def blocking(inputs):
            gate.wait(timeout=5)
            return {"result": "ok"}

        register_step_fn("block", blocking)
        register_step_fn("echo", lambda inputs: {"result": "ok"})

        engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=1)

        wf_block = _simple_workflow(fn_name="block")
        wf_fast = _simple_workflow(fn_name="echo")

        job1 = engine.create_job(objective="blocking-job", workflow=wf_block)
        job2 = engine.create_job(objective="queued-job", workflow=wf_fast)

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                engine.start_job(job1.id)
                engine.start_job(job2.id)

                # job2 should be queued
                await asyncio.sleep(0.1)
                assert store.load_job(job2.id).status == JobStatus.PENDING

                # Pause job1 — should release the slot
                engine.pause_job(job1.id)
                assert store.load_job(job1.id).status == JobStatus.PAUSED

                # job2 should now start and complete
                await asyncio.wait_for(engine.wait_for_job(job2.id), timeout=10)
                assert store.load_job(job2.id).status == JobStatus.COMPLETED
            finally:
                gate.set()
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

    def test_escalate_starts_queued(self):
        """When a step escalates (pausing the job), a pending queued job starts."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()

        register_step_fn("will_escalate", lambda inputs: {"status": "stuck"})
        register_step_fn("echo", lambda inputs: {"result": "ok"})

        engine = AsyncEngine(store=store, registry=registry, max_concurrent_jobs=1)

        wf_escalate = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "will_escalate"}),
                outputs=["status"],
                exit_rules=[
                    ExitRule("escalate-always", "expression", {
                        "condition": "True",
                        "action": "escalate",
                    }, priority=10),
                ],
            ),
        })
        wf_fast = _simple_workflow(fn_name="echo")

        job1 = engine.create_job(objective="escalate-job", workflow=wf_escalate)
        job2 = engine.create_job(objective="queued-job", workflow=wf_fast)

        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                engine.start_job(job1.id)
                engine.start_job(job2.id)

                # job1 should escalate → PAUSED, freeing the slot for job2
                await asyncio.wait_for(engine.wait_for_job(job1.id), timeout=10)
                assert store.load_job(job1.id).status == JobStatus.PAUSED

                await asyncio.wait_for(engine.wait_for_job(job2.id), timeout=10)
                assert store.load_job(job2.id).status == JobStatus.COMPLETED
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())


# ══════════════════════════════════════════════════════════════════════
# H10: Agent results always picked up
# ══════════════════════════════════════════════════════════════════════


class TestH10AgentResultPickup:
    """Test that executor results are always delivered to the engine."""

    def test_executor_exception_becomes_step_failure(self):
        """When an executor raises, the error is pushed to the queue and becomes a step failure."""
        store = SQLiteStore(":memory:")
        registry = _make_registry()

        register_step_fn("crash", lambda inputs: (_ for _ in ()).throw(RuntimeError("boom")))

        engine = AsyncEngine(store=store, registry=registry)
        wf = _simple_workflow(fn_name="crash")
        job = engine.create_job(objective="test-crash", workflow=wf)
        result = run_job_sync(engine, job.id, timeout=10)

        assert result.status == JobStatus.FAILED
        runs = store.runs_for_job(job.id)
        failed_runs = [r for r in runs if r.status == StepRunStatus.FAILED]
        assert len(failed_runs) >= 1
        assert "boom" in failed_runs[0].error

    def test_watchdog_detects_stuck_run(self):
        """Stuck RUNNING steps with no executor task are failed by the watchdog."""
        from datetime import timedelta

        store = SQLiteStore(":memory:")
        registry = _make_registry()
        engine = AsyncEngine(store=store, registry=registry)

        job = _create_running_job(store)

        # Add a RUNNING step run that started >60s ago
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step-a",
            attempt=1, status=StepRunStatus.RUNNING,
            started_at=_now() - timedelta(minutes=5),
        )
        store.save_run(run)

        # Ensure no task in engine registry for this run
        assert run.id not in engine._tasks

        # Run in async context so _dispatch_ready can work after watchdog fires
        async def run_test():
            engine_task = asyncio.create_task(engine.run())
            try:
                engine._poll_external_changes()
                # Give engine a moment to settle the job
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
        assert "task lost" in reloaded.error.lower()

    def test_done_callback_logs_errors(self):
        """Verify that _run_executor logs errors when executor.start() raises."""
        from stepwise.executors import Executor, ExecutorResult, ExecutorStatus, ExecutionContext

        class CrashingExecutor(Executor):
            """Executor that raises an exception (not caught internally)."""
            def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
                raise RuntimeError("executor crashed hard")
            def check_status(self, state: dict) -> ExecutorStatus:
                return ExecutorStatus(state="completed")
            def cancel(self, state: dict) -> None:
                pass

        store = SQLiteStore(":memory:")
        registry = ExecutorRegistry()
        registry.register("crashing", lambda config: CrashingExecutor())

        engine = AsyncEngine(store=store, registry=registry)
        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="crashing", config={}),
                outputs=["result"],
            ),
        })
        job = engine.create_job(objective="test-log", workflow=wf)

        with patch("stepwise.engine._async_logger") as mock_logger:
            result = run_job_sync(engine, job.id, timeout=10)

        assert result.status == JobStatus.FAILED
        # The error should have been logged by _run_executor
        error_calls = [c for c in mock_logger.error.call_args_list
                       if "failed" in str(c).lower() or "crashed" in str(c).lower()]
        assert len(error_calls) > 0


# ══════════════════════════════════════════════════════════════════════
# H12: Global server registry
# ══════════════════════════════════════════════════════════════════════


class TestH12GlobalServerRegistry:
    """Test global server registry CRUD operations."""

    def test_register_list_unregister(self):
        """Basic CRUD: register, list, unregister."""
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "servers.json"
            with patch("stepwise.server_detect.GLOBAL_REGISTRY", registry_path):
                from stepwise.server_detect import (
                    register_server, unregister_server, list_active_servers,
                )

                # Register
                register_server("/project/a", os.getpid(), 8340, "http://localhost:8340")
                servers = list_active_servers()
                assert len(servers) == 1
                assert servers[0]["project_path"] == "/project/a"
                assert servers[0]["port"] == 8340

                # Register another
                register_server("/project/b", os.getpid(), 8341, "http://localhost:8341")
                servers = list_active_servers()
                assert len(servers) == 2

                # Unregister
                unregister_server("/project/a")
                servers = list_active_servers()
                assert len(servers) == 1
                assert servers[0]["project_path"] == "/project/b"

    def test_dead_servers_pruned_on_list(self):
        """Servers with dead PIDs are removed when listing."""
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "servers.json"
            with patch("stepwise.server_detect.GLOBAL_REGISTRY", registry_path):
                from stepwise.server_detect import register_server, list_active_servers

                # Register with a PID that definitely doesn't exist
                register_server("/project/dead", 999999999, 8340, "http://localhost:8340")

                servers = list_active_servers()
                assert len(servers) == 0

                # Verify the dead entry was pruned from the file
                data = json.loads(registry_path.read_text())
                assert len(data) == 0

    def test_global_registry_upsert_by_project(self):
        """Registering the same project_path twice updates (upserts), not duplicates."""
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "servers.json"
            with patch("stepwise.server_detect.GLOBAL_REGISTRY", registry_path):
                from stepwise.server_detect import register_server, list_active_servers

                register_server("/project/a", os.getpid(), 8340, "http://localhost:8340")
                register_server("/project/a", os.getpid(), 9999, "http://localhost:9999")

                servers = list_active_servers()
                assert len(servers) == 1
                assert servers[0]["port"] == 9999

    def test_registry_atomic_write(self):
        """Registry file is written atomically (no partial writes)."""
        with tempfile.TemporaryDirectory() as tmp:
            registry_path = Path(tmp) / "servers.json"
            with patch("stepwise.server_detect.GLOBAL_REGISTRY", registry_path):
                from stepwise.server_detect import register_server, list_active_servers

                register_server("/project/a", os.getpid(), 8340, "http://localhost:8340")

                # File should be valid JSON
                data = json.loads(registry_path.read_text())
                assert "/project/a" in data


# ══════════════════════════════════════════════════════════════════════
# H13: Server identity verification
# ══════════════════════════════════════════════════════════════════════


class TestH13ServerIdentity:
    """Test server identity verification."""

    def test_health_endpoint_includes_project_path(self):
        """The /api/health response includes project_path."""
        from unittest.mock import MagicMock

        # We test the function directly instead of starting a server
        import stepwise.server as server_mod
        original_engine = server_mod._engine
        original_project_dir = server_mod._project_dir

        try:
            mock_engine = MagicMock()
            mock_engine.store.active_jobs.return_value = []
            server_mod._engine = mock_engine
            server_mod._project_dir = Path("/test/project")

            result = server_mod.health_check()
            assert result["status"] == "ok"
            assert result["project_path"] == "/test/project"
        finally:
            server_mod._engine = original_engine
            server_mod._project_dir = original_project_dir

    def test_stale_pidfile_cleanup_with_warning(self):
        """Stale pidfile (dead PID) is cleaned up with a warning."""
        with tempfile.TemporaryDirectory() as tmp:
            dot_dir = Path(tmp)
            pid_file = dot_dir / "server.pid"
            pid_file.write_text(json.dumps({
                "pid": 999999999,  # dead PID
                "port": 8340,
                "url": "http://localhost:8340",
            }))

            from stepwise.server_detect import detect_server
            result = detect_server(dot_dir)
            assert result is None
            assert not pid_file.exists()  # cleaned up

    def test_verify_server_identity_match(self):
        """verify_server_identity returns True when project paths match."""
        mock_response = json.dumps({
            "status": "ok",
            "project_path": "/test/project",
        }).encode()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = mock_response
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = lambda s, *a: None
            mock_urlopen.return_value = mock_resp

            from stepwise.server_detect import verify_server_identity
            assert verify_server_identity("http://localhost:8340", Path("/test/project")) is True

    def test_verify_server_identity_mismatch(self):
        """verify_server_identity returns False when project paths differ."""
        mock_response = json.dumps({
            "status": "ok",
            "project_path": "/other/project",
        }).encode()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = mock_response
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = lambda s, *a: None
            mock_urlopen.return_value = mock_resp

            from stepwise.server_detect import verify_server_identity
            assert verify_server_identity("http://localhost:8340", Path("/test/project")) is False

    def test_verify_server_identity_no_path_in_response(self):
        """verify_server_identity returns True for old servers without project_path."""
        mock_response = json.dumps({
            "status": "ok",
        }).encode()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = mock_response
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = lambda s, *a: None
            mock_urlopen.return_value = mock_resp

            from stepwise.server_detect import verify_server_identity
            assert verify_server_identity("http://localhost:8340", Path("/test/project")) is True

    def test_verify_server_identity_connection_error(self):
        """verify_server_identity returns False on connection error."""
        with patch("urllib.request.urlopen", side_effect=ConnectionError("refused")):
            from stepwise.server_detect import verify_server_identity
            assert verify_server_identity("http://localhost:8340", Path("/test/project")) is False
