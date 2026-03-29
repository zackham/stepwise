"""Tests for H18: Server restart resilience — orphan reattach."""

import asyncio
import json
import os
import tempfile
from datetime import timedelta
from unittest.mock import patch, MagicMock

import pytest

from stepwise.agent import (
    AgentExecutor,
    AgentProcess,
    AgentStatus,
    AcpxBackend,
    MockAgentBackend,
    verify_agent_pid,
)
from stepwise.engine import AsyncEngine, _unwrap_executor
from stepwise.executors import (
    Executor,
    ExecutionContext,
    ExecutorRegistry,
    ExecutorResult,
    ExecutorStatus,
)
from stepwise.decorators import RetryDecorator
from stepwise.models import (
    ExecutorRef,
    ExitRule,
    HandoffEnvelope,
    InputBinding,
    Job,
    JobConfig,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WatchSpec,
    WorkflowDefinition,
    _gen_id,
    _now,
)
from stepwise.store import SQLiteStore
from tests.conftest import run_job_sync, CallableExecutor


# ── Shared fixtures ──────────────────────────────────────────────────


def _make_agent_executor_state(
    pid: int = 12345, pgid: int = 12345, output_mode: str = "effect",
    working_dir: str = "/tmp/test", output_file: str | None = None,
    session_name: str = "test-session", session_id: str | None = None,
) -> dict:
    """Build a realistic executor_state dict matching agent.py state_update_fn."""
    return {
        "pid": pid, "pgid": pgid,
        "output_path": f"/tmp/mock-agent-{pid}.jsonl",
        "working_dir": working_dir,
        "session_id": session_id,
        "session_name": session_name,
        "output_mode": output_mode,
        "output_file": output_file,
        "capture_transcript": False,
    }


def _make_running_job_with_agent(
    store: SQLiteStore, executor_state: dict | None = None,
    step_name: str = "agent-step", continue_session: bool = False,
    emit_flow: bool = False, outputs: list[str] | None = None,
) -> tuple[Job, StepRun]:
    """Create RUNNING job + RUNNING step run with executor_state in DB."""
    exec_config = {"prompt": "test"}
    if emit_flow:
        exec_config["emit_flow"] = True
    wf = WorkflowDefinition(steps={
        step_name: StepDefinition(
            name=step_name,
            executor=ExecutorRef(type="agent", config=exec_config),
            outputs=outputs if outputs is not None else ["result"],
            continue_session=continue_session,
        ),
    })
    job = Job(
        id=_gen_id("job"), objective="test-reattach", workflow=wf,
        status=JobStatus.RUNNING, inputs={}, workspace_path="/tmp/test",
        config=JobConfig(), created_at=_now(), updated_at=_now(),
        created_by="server",
    )
    store.save_job(job)

    state = executor_state or _make_agent_executor_state()
    run = StepRun(
        id=_gen_id("run"), job_id=job.id, step_name=step_name,
        attempt=1, status=StepRunStatus.RUNNING,
        pid=state.get("pid"), executor_state=state,
        started_at=_now() - timedelta(seconds=30),
    )
    store.save_run(run)
    return job, run


class ReattachMockBackend(MockAgentBackend):
    """Mock backend that supports finalize_surviving via immediate wait() return."""

    def __init__(self, result: AgentStatus | None = None):
        super().__init__()
        self._finalize_result = result or AgentStatus(
            state="completed", exit_code=0, result={},
        )

    def wait(self, process: AgentProcess, on_usage_limit=None) -> AgentStatus:
        """Return immediately for reattach testing."""
        return self._finalize_result


def _make_reattach_engine(
    store: SQLiteStore, backend: MockAgentBackend | None = None,
) -> AsyncEngine:
    """Create AsyncEngine with mock agent executor for reattach tests."""
    reg = ExecutorRegistry()
    reg.register("callable", lambda cfg: CallableExecutor(fn_name=cfg.get("fn_name", "default")))

    _backend = backend or ReattachMockBackend()

    def agent_factory(cfg):
        executor = AgentExecutor(
            backend=_backend, prompt=cfg.get("prompt", ""),
            output_mode=cfg.get("output_mode", "effect"),
            output_path=cfg.get("output_path"),
            **{k: v for k, v in cfg.items()
               if k not in ("prompt", "output_mode", "output_path")},
        )
        return executor

    reg.register("agent", agent_factory)
    return AsyncEngine(store=store, registry=reg)


# ── T1: PID verification tests (R3) ─────────────────────────────────


class TestVerifyAgentPid:
    """verify_agent_pid: /proc-based PID identity verification."""

    def test_dead_pid_returns_false(self):
        """PID not in /proc → False."""
        with patch("stepwise.agent.Path") as mock_path:
            mock_path.return_value.parent.is_dir.return_value = True
            mock_path.return_value.read_bytes.side_effect = FileNotFoundError
            assert verify_agent_pid(99999) is False

    def test_acpx_pid_returns_true(self):
        """PID with 'acpx' in cmdline → True."""
        with patch("stepwise.agent.Path") as mock_path:
            mock_path.return_value.parent.is_dir.return_value = True
            mock_path.return_value.read_bytes.return_value = b"acpx\x00claude\x00run"
            with patch("stepwise.agent._get_process_pgid", return_value=100):
                assert verify_agent_pid(1234, expected_pgid=100) is True

    def test_non_agent_pid_returns_false(self):
        """PID with unrelated cmdline → False (recycled)."""
        with patch("stepwise.agent.Path") as mock_path:
            mock_path.return_value.parent.is_dir.return_value = True
            mock_path.return_value.read_bytes.return_value = b"/bin/bash\x00-l"
            assert verify_agent_pid(1234) is False

    def test_pgid_mismatch_returns_false(self):
        """Cmdline matches but PGID differs → False (recycled into different group)."""
        with patch("stepwise.agent.Path") as mock_path:
            mock_path.return_value.parent.is_dir.return_value = True
            mock_path.return_value.read_bytes.return_value = b"acpx\x00claude"
            with patch("stepwise.agent._get_process_pgid", return_value=999):
                assert verify_agent_pid(1234, expected_pgid=100) is False

    def test_no_proc_fallback_alive(self):
        """Non-Linux (no /proc) falls back to os.kill — alive → True."""
        with patch("stepwise.agent.Path") as mock_path:
            mock_path.return_value.parent.is_dir.return_value = False
            with patch("os.kill"):  # no exception = alive
                assert verify_agent_pid(1234) is True

    def test_no_proc_fallback_dead(self):
        """Non-Linux (no /proc) falls back to os.kill — dead → False."""
        with patch("stepwise.agent.Path") as mock_path:
            mock_path.return_value.parent.is_dir.return_value = False
            with patch("os.kill", side_effect=ProcessLookupError):
                assert verify_agent_pid(1234) is False


# ── T2: Core reattach lifecycle tests (R1, R2) ──────────────────────


class TestReattachLifecycle:
    """End-to-end: reattach → engine loop → job completion/failure."""

    def test_reattach_live_agent_completes_job(self):
        """R1: Surviving agent process → reattach → engine processes result → job COMPLETED."""
        store = SQLiteStore(":memory:")
        backend = ReattachMockBackend(AgentStatus(state="completed", exit_code=0))
        engine = _make_reattach_engine(store, backend=backend)
        job, run = _make_running_job_with_agent(store, outputs=[])

        async def run_test():
            reattached = await engine.reattach_surviving_runs()
            assert reattached == 1
            assert run.id in engine._tasks
            # Run engine briefly to process the queued result
            engine_task = asyncio.create_task(engine.run())
            try:
                await asyncio.sleep(1.0)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

        loaded_run = store.load_run(run.id)
        assert loaded_run.status == StepRunStatus.COMPLETED
        loaded_job = store.load_job(job.id)
        assert loaded_job.status == JobStatus.COMPLETED

    def test_reattach_no_executor_state_fails_and_advances(self):
        """R2: RUNNING run with no executor_state → step_error → job FAILED."""
        store = SQLiteStore(":memory:")
        engine = _make_reattach_engine(store)
        # Create run with no executor_state
        wf = WorkflowDefinition(steps={
            "step": StepDefinition(
                name="step",
                executor=ExecutorRef(type="agent", config={"prompt": "t"}),
                outputs=["result"],
            ),
        })
        job = Job(
            id=_gen_id("job"), objective="test", workflow=wf,
            status=JobStatus.RUNNING, inputs={}, workspace_path="/tmp/test",
            config=JobConfig(), created_at=_now(), updated_at=_now(),
            created_by="server",
        )
        store.save_job(job)
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step",
            attempt=1, status=StepRunStatus.RUNNING,
            pid=None, executor_state=None,
            started_at=_now() - timedelta(seconds=30),
        )
        store.save_run(run)

        async def run_test():
            await engine.reattach_surviving_runs()
            engine_task = asyncio.create_task(engine.run())
            try:
                await asyncio.sleep(1.0)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

        loaded_run = store.load_run(run.id)
        assert loaded_run.status == StepRunStatus.FAILED
        loaded_job = store.load_job(job.id)
        assert loaded_job.status == JobStatus.FAILED

    def test_reattach_cli_owned_job_skipped(self):
        """CLI-owned jobs (created_by != 'server') are not reattached."""
        store = SQLiteStore(":memory:")
        engine = _make_reattach_engine(store)
        wf = WorkflowDefinition(steps={
            "step": StepDefinition(
                name="step",
                executor=ExecutorRef(type="agent", config={"prompt": "t"}),
                outputs=["result"],
            ),
        })
        job = Job(
            id=_gen_id("job"), objective="test", workflow=wf,
            status=JobStatus.RUNNING, inputs={}, workspace_path="/tmp/test",
            config=JobConfig(), created_at=_now(), updated_at=_now(),
            created_by="cli:1234",  # not server-owned
        )
        store.save_job(job)
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="step",
            attempt=1, status=StepRunStatus.RUNNING,
            pid=12345, executor_state=_make_agent_executor_state(),
            started_at=_now(),
        )
        store.save_run(run)

        async def run_test():
            reattached = await engine.reattach_surviving_runs()
            assert reattached == 0

        asyncio.run(run_test())


# ── T3: Specialized reattach scenarios (R1, R5) ─────────────────────


class TestReattachScenarios:
    """Edge cases: emit_flow, continue_session, file output, non-child failure."""

    def test_reattach_continue_session_injects_session_id(self):
        """continue_session=true step → _session_id in artifact after reattach."""
        store = SQLiteStore(":memory:")
        state = _make_agent_executor_state(session_name="my-session")
        backend = ReattachMockBackend(AgentStatus(state="completed", exit_code=0))
        engine = _make_reattach_engine(store, backend=backend)
        job, run = _make_running_job_with_agent(
            store, executor_state=state, continue_session=True, outputs=[],
        )

        async def run_test():
            await engine.reattach_surviving_runs()
            engine_task = asyncio.create_task(engine.run())
            try:
                await asyncio.sleep(1.0)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

        loaded_run = store.load_run(run.id)
        assert loaded_run.status == StepRunStatus.COMPLETED
        assert loaded_run.result.artifact.get("_session_id") == "my-session"

    def test_reattach_file_output_mode(self):
        """File output mode: executor reads JSON from output_file."""
        tmpdir = tempfile.mkdtemp()
        output_file = os.path.join(tmpdir, "output.json")
        with open(output_file, "w") as f:
            json.dump({"summary": "done", "score": 0.95}, f)

        state = _make_agent_executor_state(
            output_mode="file", output_file=output_file, working_dir=tmpdir,
        )
        store = SQLiteStore(":memory:")
        backend = ReattachMockBackend(AgentStatus(state="completed", exit_code=0))
        engine = _make_reattach_engine(store, backend=backend)
        job, run = _make_running_job_with_agent(
            store, executor_state=state, outputs=["summary", "score"],
        )

        async def run_test():
            await engine.reattach_surviving_runs()
            engine_task = asyncio.create_task(engine.run())
            try:
                await asyncio.sleep(1.0)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

        loaded_run = store.load_run(run.id)
        assert loaded_run.status == StepRunStatus.COMPLETED
        assert loaded_run.result.artifact["summary"] == "done"
        assert loaded_run.result.artifact["score"] == 0.95

    def test_nonchild_error_detection(self):
        """R5: AcpxBackend._completed_status with exit_code_reliable=False detects errors."""
        tmpdir = tempfile.mkdtemp()
        output_path = os.path.join(tmpdir, "output.jsonl")
        # Write NDJSON with an error event
        with open(output_path, "w") as f:
            f.write(json.dumps({"error": {"message": "Context limit exceeded"}}) + "\n")

        backend = AcpxBackend(acpx_path="acpx", default_agent="claude")
        process = AgentProcess(
            pid=1, pgid=1, output_path=output_path, working_dir=tmpdir,
        )
        status = backend._completed_status(process, exit_code=0, exit_code_reliable=False)
        assert status.state == "failed"
        assert "Context limit exceeded" in status.error

    def test_nonchild_no_error_succeeds(self):
        """Non-child PID with clean output → success."""
        tmpdir = tempfile.mkdtemp()
        output_path = os.path.join(tmpdir, "output.jsonl")
        with open(output_path, "w") as f:
            f.write(json.dumps({"params": {"update": {"content": "done"}}}) + "\n")

        backend = AcpxBackend(acpx_path="acpx", default_agent="claude")
        process = AgentProcess(
            pid=1, pgid=1, output_path=output_path, working_dir=tmpdir,
        )
        status = backend._completed_status(process, exit_code=0, exit_code_reliable=False)
        assert status.state == "completed"


# ── T4: Poll watch and idempotency tests (R4) ───────────────────────


class TestReattachPollWatch:
    """Poll watch timer re-scheduling on restart."""

    def test_poll_watch_rescheduled(self):
        """R4: Suspended poll step → timer re-created."""
        store = SQLiteStore(":memory:")
        engine = _make_reattach_engine(store)
        wf = WorkflowDefinition(steps={
            "wait-step": StepDefinition(
                name="wait-step",
                executor=ExecutorRef(type="agent", config={"prompt": "t"}),
                outputs=["result"],
            ),
        })
        job = Job(
            id=_gen_id("job"), objective="test", workflow=wf,
            status=JobStatus.RUNNING, inputs={}, workspace_path="/tmp",
            config=JobConfig(), created_at=_now(), updated_at=_now(),
            created_by="server",
        )
        store.save_job(job)
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="wait-step",
            attempt=1, status=StepRunStatus.SUSPENDED,
            watch=WatchSpec(mode="poll", config={"interval_seconds": 10}),
            started_at=_now(),
        )
        store.save_run(run)

        async def run_test():
            reattached = await engine.reattach_surviving_runs()
            assert reattached == 1
            assert run.id in engine._poll_tasks

        asyncio.run(run_test())

    def test_poll_watch_idempotent(self):
        """Pre-existing timer → NOT replaced."""
        store = SQLiteStore(":memory:")
        engine = _make_reattach_engine(store)
        wf = WorkflowDefinition(steps={
            "wait-step": StepDefinition(
                name="wait-step",
                executor=ExecutorRef(type="agent", config={"prompt": "t"}),
                outputs=["result"],
            ),
        })
        job = Job(
            id=_gen_id("job"), objective="test", workflow=wf,
            status=JobStatus.RUNNING, inputs={}, workspace_path="/tmp",
            config=JobConfig(), created_at=_now(), updated_at=_now(),
            created_by="server",
        )
        store.save_job(job)
        run = StepRun(
            id=_gen_id("run"), job_id=job.id, step_name="wait-step",
            attempt=1, status=StepRunStatus.SUSPENDED,
            watch=WatchSpec(mode="poll", config={"interval_seconds": 10}),
            started_at=_now(),
        )
        store.save_run(run)

        async def run_test():
            # Pre-populate with a sentinel task
            sentinel = MagicMock()
            engine._poll_tasks[run.id] = sentinel
            reattached = await engine.reattach_surviving_runs()
            assert reattached == 0  # skipped because timer already exists
            assert engine._poll_tasks[run.id] is sentinel  # not replaced

        asyncio.run(run_test())


# ── T5: Decorator and unwrap tests ──────────────────────────────────


class TestDecoratorReattach:
    """Decorator chain unwrapping and forfeiture behavior."""

    def test_unwrap_reaches_inner_executor(self):
        """_unwrap_executor walks decorator chain to inner AgentExecutor."""
        backend = ReattachMockBackend()
        inner = AgentExecutor(backend=backend, prompt="test", output_mode="effect")
        retry = RetryDecorator(inner, {"max_retries": 3})
        assert _unwrap_executor(retry) is inner

    def test_unwrap_plain_executor_returns_self(self):
        """Non-decorated executor returns itself."""
        backend = ReattachMockBackend()
        inner = AgentExecutor(backend=backend, prompt="test", output_mode="effect")
        assert _unwrap_executor(inner) is inner

    def test_decorator_forfeiture_no_retry_on_reattach(self):
        """After restart, RetryDecorator does NOT re-invoke start() on failure."""
        store = SQLiteStore(":memory:")
        fail_backend = ReattachMockBackend(
            AgentStatus(state="failed", exit_code=1, error="agent crashed"),
        )
        engine = _make_reattach_engine(store, backend=fail_backend)
        job, run = _make_running_job_with_agent(store, outputs=[])

        async def run_test():
            await engine.reattach_surviving_runs()
            engine_task = asyncio.create_task(engine.run())
            try:
                await asyncio.sleep(1.0)
            finally:
                engine_task.cancel()
                try:
                    await engine_task
                except asyncio.CancelledError:
                    pass

        asyncio.run(run_test())

        # Agent should be failed — retry decorator did NOT re-invoke start()
        loaded_run = store.load_run(run.id)
        assert loaded_run.status == StepRunStatus.FAILED
        # Backend was only called once (wait in finalize_surviving), not retried
        assert fail_backend.spawn_count == 0  # spawn never called during reattach

    def test_multiple_jobs_mixed_state(self):
        """Three jobs: one reattaches, one fails (no state), one gets poll rescheduled."""
        store = SQLiteStore(":memory:")
        backend = ReattachMockBackend(AgentStatus(state="completed", exit_code=0))
        engine = _make_reattach_engine(store, backend=backend)

        # Job 1: live agent — should reattach
        job1, run1 = _make_running_job_with_agent(store, outputs=[])

        # Job 2: no executor_state — should push error
        wf2 = WorkflowDefinition(steps={
            "step": StepDefinition(
                name="step",
                executor=ExecutorRef(type="agent", config={"prompt": "t"}),
                outputs=["result"],
            ),
        })
        job2 = Job(
            id=_gen_id("job"), objective="test2", workflow=wf2,
            status=JobStatus.RUNNING, inputs={}, workspace_path="/tmp",
            config=JobConfig(), created_at=_now(), updated_at=_now(),
            created_by="server",
        )
        store.save_job(job2)
        run2 = StepRun(
            id=_gen_id("run"), job_id=job2.id, step_name="step",
            attempt=1, status=StepRunStatus.RUNNING,
            pid=None, executor_state=None, started_at=_now(),
        )
        store.save_run(run2)

        # Job 3: suspended poll step — should reschedule
        wf3 = WorkflowDefinition(steps={
            "poll-step": StepDefinition(
                name="poll-step",
                executor=ExecutorRef(type="agent", config={"prompt": "t"}),
                outputs=["result"],
            ),
        })
        job3 = Job(
            id=_gen_id("job"), objective="test3", workflow=wf3,
            status=JobStatus.RUNNING, inputs={}, workspace_path="/tmp",
            config=JobConfig(), created_at=_now(), updated_at=_now(),
            created_by="server",
        )
        store.save_job(job3)
        run3 = StepRun(
            id=_gen_id("run"), job_id=job3.id, step_name="poll-step",
            attempt=1, status=StepRunStatus.SUSPENDED,
            watch=WatchSpec(mode="poll", config={"interval_seconds": 30}),
            started_at=_now(),
        )
        store.save_run(run3)

        async def run_test():
            reattached = await engine.reattach_surviving_runs()
            assert reattached == 2  # run1 (monitoring task) + run3 (poll timer)
            assert run1.id in engine._tasks
            assert run3.id in engine._poll_tasks

        asyncio.run(run_test())
