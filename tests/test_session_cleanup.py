"""Tests for session cleanup on job completion/failure/cancellation."""

import os
from unittest.mock import patch, MagicMock

from tests.conftest import register_step_fn, run_job_sync

from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorResult,
    ExecutorStatus,
)
from stepwise.models import (
    ExecutorRef,
    ExitRule,
    HandoffEnvelope,
    InputBinding,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)


class AgentLikeExecutor(Executor):
    """Test executor that stores session info in executor_state like AgentExecutor."""

    def __init__(self, session_name: str, session_id: str = "sid-1"):
        self.session_name = session_name
        self.session_id = session_id

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        return ExecutorResult(
            type="data",
            envelope=HandoffEnvelope(
                artifact={"result": "done"},
                sidecar=Sidecar(),
                workspace=context.workspace_path,
                timestamp=_now(),
            ),
            executor_state={
                "session_name": self.session_name,
                "session_id": self.session_id,
            },
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        return ExecutorStatus(state="completed")

    def cancel(self, state: dict) -> None:
        pass


def _make_agent_step(name: str, agent: str = "claude", after: list[str] | None = None) -> StepDefinition:
    return StepDefinition(
        name=name,
        executor=ExecutorRef(type="agent_like", config={"agent": agent}),
        outputs=["result"],
        after=after or [],
    )


def _call_cleanup_directly(engine, job_id, job):
    """Call _cleanup_job_sessions and capture the thread args without actually spawning."""
    captured = {}
    original_thread = __import__("threading").Thread

    def mock_thread(*args, **kwargs):
        captured["target"] = kwargs.get("target") or (args[0] if args else None)
        captured["args"] = kwargs.get("args") or (args[1] if len(args) > 1 else None)
        t = MagicMock()
        return t

    with patch("stepwise.engine.threading.Thread", side_effect=mock_thread):
        engine._cleanup_job_sessions(job_id, job)

    return captured


class TestSessionCleanupOnCompletion:
    """Session names are collected from executor_state and closed on job completion."""

    def test_sessions_closed_on_job_complete(self, async_engine):
        """Sessions from agent steps are closed when job completes."""
        async_engine.registry.register(
            "agent_like",
            lambda cfg: AgentLikeExecutor("sess-abc", "sid-abc"),
        )

        wf = WorkflowDefinition(steps={
            "step-a": _make_agent_step("step-a"),
        })
        job = async_engine.create_job(objective="test", workflow=wf)
        run_job_sync(async_engine, job.id)

        # Verify runs have session info
        runs = async_engine.store.runs_for_job(job.id)
        assert any(r.executor_state and r.executor_state.get("session_name") == "sess-abc" for r in runs)

        # Call cleanup directly and inspect what would be passed to the thread
        job = async_engine.store.load_job(job.id)
        captured = _call_cleanup_directly(async_engine, job.id, job)
        sessions = captured["args"][0]
        assert "sess-abc" in sessions
        agent_name, session_id = sessions["sess-abc"]
        assert agent_name == "claude"
        assert session_id == "sid-abc"

    def test_custom_agent_name_resolved(self, async_engine):
        """Agent name is read from executor config, not hardcoded."""
        async_engine.registry.register(
            "agent_like",
            lambda cfg: AgentLikeExecutor("sess-custom", "sid-custom"),
        )

        wf = WorkflowDefinition(steps={
            "step-a": _make_agent_step("step-a", agent="codex"),
        })
        job = async_engine.create_job(objective="test", workflow=wf)
        run_job_sync(async_engine, job.id)

        job = async_engine.store.load_job(job.id)
        captured = _call_cleanup_directly(async_engine, job.id, job)
        sessions = captured["args"][0]
        agent_name, _ = sessions["sess-custom"]
        assert agent_name == "codex"

    def test_deduplication_across_loop_iterations(self, async_engine):
        """Same session_name from multiple runs is only closed once."""
        call_count = 0

        class LoopingAgentExecutor(Executor):
            def start(self, inputs, context):
                nonlocal call_count
                call_count += 1
                artifact = {"result": "retry" if call_count < 2 else "done"}
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact=artifact,
                        sidecar=Sidecar(),
                        workspace=context.workspace_path,
                        timestamp=_now(),
                    ),
                    executor_state={
                        "session_name": "sess-loop",
                        "session_id": "sid-loop",
                    },
                )

            def check_status(self, state):
                return ExecutorStatus(state="completed")

            def cancel(self, state):
                pass

        async_engine.registry.register("agent_like", lambda cfg: LoopingAgentExecutor())

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="agent_like", config={"agent": "claude"}),
                outputs=["result"],
                exit_rules=[
                    ExitRule("done", "expression", {
                        "condition": "outputs.result == 'done'",
                        "action": "advance",
                    }, priority=10),
                    ExitRule("retry", "expression", {
                        "condition": "attempt < 3",
                        "action": "loop", "target": "step-a",
                    }, priority=5),
                ],
            ),
        })
        job = async_engine.create_job(objective="test", workflow=wf)
        run_job_sync(async_engine, job.id)

        # Multiple runs exist
        runs = async_engine.store.runs_for_job(job.id)
        assert len(runs) >= 2

        job = async_engine.store.load_job(job.id)
        captured = _call_cleanup_directly(async_engine, job.id, job)
        sessions = captured["args"][0]
        assert len(sessions) == 1
        assert "sess-loop" in sessions


class TestSessionCleanupOnCancel:
    """cancel_job triggers session cleanup."""

    def test_cancel_triggers_cleanup(self, async_engine):
        """Cancelling a job closes its agent sessions."""
        async_engine.registry.register(
            "agent_like",
            lambda cfg: AgentLikeExecutor("sess-cancel", "sid-cancel"),
        )

        wf = WorkflowDefinition(steps={
            "step-a": _make_agent_step("step-a"),
        })
        job = async_engine.create_job(objective="test", workflow=wf)
        # Run the job to completion so there are runs with executor_state
        run_job_sync(async_engine, job.id)

        # Reset job to running so we can cancel it
        job = async_engine.store.load_job(job.id)
        job.status = JobStatus.RUNNING
        async_engine.store.save_job(job)

        captured = {}

        def mock_thread(*args, **kwargs):
            captured["args"] = kwargs.get("args")
            return MagicMock()

        with patch("stepwise.engine.threading.Thread", side_effect=mock_thread):
            async_engine.cancel_job(job.id)

        assert "args" in captured
        sessions = captured["args"][0]
        assert "sess-cancel" in sessions


class TestSessionCleanupGracefulHandling:
    """Cleanup tolerates missing/empty executor_state."""

    def test_no_crash_with_missing_executor_state(self, async_engine):
        """Steps without executor_state don't cause errors."""
        register_step_fn("simple", lambda inputs: {"result": "ok"})

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "simple"}),
                outputs=["result"],
            ),
        })
        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

    def test_mixed_steps_only_agent_sessions_closed(self, async_engine):
        """Only runs with session_name in executor_state are cleaned up."""
        register_step_fn("simple", lambda inputs: {"result": "ok"})
        async_engine.registry.register(
            "agent_like",
            lambda cfg: AgentLikeExecutor("sess-mixed", "sid-mixed"),
        )

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "simple"}),
                outputs=["result"],
            ),
            "step-b": StepDefinition(
                name="step-b",
                executor=ExecutorRef(type="agent_like", config={"agent": "claude"}),
                outputs=["result"],
                after=["step-a"],
            ),
        })
        job = async_engine.create_job(objective="test", workflow=wf)
        run_job_sync(async_engine, job.id)

        job = async_engine.store.load_job(job.id)
        captured = _call_cleanup_directly(async_engine, job.id, job)
        sessions = captured["args"][0]
        assert len(sessions) == 1
        assert "sess-mixed" in sessions

    def test_no_sessions_skips_cleanup(self, async_engine):
        """When no runs have session_name, no thread is spawned."""
        register_step_fn("simple", lambda inputs: {"result": "ok"})

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "simple"}),
                outputs=["result"],
            ),
        })
        job = async_engine.create_job(objective="test", workflow=wf)
        run_job_sync(async_engine, job.id)

        # Call cleanup directly — should return without spawning a thread
        job = async_engine.store.load_job(job.id)
        captured = _call_cleanup_directly(async_engine, job.id, job)
        assert captured == {}  # No thread was created


class TestSessionCleanupEnvStripping:
    """CLAUDECODE env var is stripped from subprocess env."""

    def test_claudecode_stripped(self, async_engine):
        """The cleanup thread strips CLAUDECODE from the env passed to subprocess."""
        async_engine.registry.register(
            "agent_like",
            lambda cfg: AgentLikeExecutor("sess-env", "sid-env"),
        )

        wf = WorkflowDefinition(steps={
            "step-a": _make_agent_step("step-a"),
        })
        job = async_engine.create_job(objective="test", workflow=wf)
        run_job_sync(async_engine, job.id)

        job = async_engine.store.load_job(job.id)
        try:
            os.environ["CLAUDECODE"] = "/tmp/fake"
            os.environ["STEPWISE_OUTPUT_FILE"] = "/tmp/fake-output"
            captured = _call_cleanup_directly(async_engine, job.id, job)
        finally:
            os.environ.pop("CLAUDECODE", None)
            os.environ.pop("STEPWISE_OUTPUT_FILE", None)

        clean_env = captured["args"][2]
        assert "CLAUDECODE" not in clean_env
        assert "STEPWISE_OUTPUT_FILE" not in clean_env


class TestCloseSessionsFallback:
    """SIGTERM fallback when acpx sessions close fails."""

    def test_sigterm_fallback_on_acpx_failure(self):
        """When acpx close returns non-zero, falls back to SIGTERM via lock file."""
        from stepwise.engine import Engine
        from stepwise.store import SQLiteStore

        store = SQLiteStore(":memory:")
        registry = MagicMock()
        engine = Engine(store=store, registry=registry)

        # Create a minimal job
        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="agent", config={"agent": "claude"}),
                outputs=["result"],
            ),
        })
        job = engine.create_job(objective="test", workflow=wf)

        # Manually insert a completed run with session info
        run = StepRun(
            id="run-1",
            job_id=job.id,
            step_name="step-a",
            attempt=1,
            status=StepRunStatus.COMPLETED,
            executor_state={"session_name": "sess-fb", "session_id": "sid-fb"},
            started_at=_now(),
            completed_at=_now(),
        )
        store.save_run(run)

        # Capture the thread target and args
        captured = _call_cleanup_directly(engine, job.id, job)
        close_fn = captured["target"]
        sessions = captured["args"][0]
        acpx_path = captured["args"][1]
        clean_env = captured["args"][2]

        # Now call close_fn with mocked subprocess and agent helpers
        mock_result = MagicMock()
        mock_result.returncode = 1  # simulate failure

        mock_queue_owner = MagicMock()
        mock_queue_owner.session_id = "sid-fb"
        mock_queue_owner.pid = 12345

        import signal
        with (
            patch("subprocess.run", return_value=mock_result),
            patch("stepwise.agent._find_queue_owners", return_value=[mock_queue_owner]),
            patch("stepwise.agent._is_pid_alive", return_value=True),
            patch("os.kill") as mock_kill,
        ):
            close_fn(sessions, acpx_path, clean_env)
            mock_kill.assert_called_once_with(12345, signal.SIGTERM)

        store.close()
