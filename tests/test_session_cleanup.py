"""Tests for session cleanup on job completion/failure/cancellation.

With native ACP, _cleanup_job_sessions() only cleans up the in-memory
session registry. Process cleanup is handled by ACPBackend's lifecycle manager.
"""

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
    JobStatus,
    Sidecar,
    StepDefinition,
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


class TestSessionRegistryCleanup:
    """_cleanup_job_sessions removes the in-memory session registry."""

    def test_registry_removed_on_cleanup(self, async_engine):
        """Session registry is removed when _cleanup_job_sessions is called."""
        async_engine.registry.register(
            "agent_like",
            lambda cfg: AgentLikeExecutor("sess-abc", "sid-abc"),
        )

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="agent_like", config={"agent": "claude"}),
                outputs=["result"],
                session="planning",
            ),
        })
        job = async_engine.create_job(objective="test", workflow=wf)
        async_engine._ensure_session_registry(job)
        assert job.id in async_engine._session_registries

        async_engine._cleanup_job_sessions(job.id, job)
        assert job.id not in async_engine._session_registries

    def test_cleanup_idempotent(self, async_engine):
        """Calling cleanup twice doesn't crash."""
        wf = WorkflowDefinition(steps={
            "step-a": _make_agent_step("step-a"),
        })
        job = async_engine.create_job(objective="test", workflow=wf)

        async_engine._cleanup_job_sessions(job.id, job)
        async_engine._cleanup_job_sessions(job.id, job)  # second call is no-op


class TestSessionCleanupOnCompletion:
    """Session cleanup runs on job completion."""

    def test_registry_cleaned_on_completion(self, async_engine):
        """Session registry is cleaned up when job completes."""
        async_engine.registry.register(
            "agent_like",
            lambda cfg: AgentLikeExecutor("sess-abc", "sid-abc"),
        )

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="agent_like", config={"agent": "claude"}),
                outputs=["result"],
                session="planning",
            ),
        })
        job = async_engine.create_job(objective="test", workflow=wf)
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED

        # Registry should be cleaned up after job completion
        assert job.id not in async_engine._session_registries


class TestSessionCleanupGracefulHandling:
    """Cleanup tolerates missing/empty executor_state."""

    def test_no_crash_with_missing_executor_state(self, async_engine):
        """Steps without executor_state don't cause errors during cleanup."""
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

    def test_mixed_steps_complete_normally(self, async_engine):
        """Mixed callable + agent steps complete and clean up without error."""
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
        result = run_job_sync(async_engine, job.id)
        assert result.status == JobStatus.COMPLETED
