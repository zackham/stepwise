"""H14: Transient error retry for agent steps."""

import time

import pytest

from stepwise.decorators import RetryDecorator, TRANSIENT_ERROR_CATEGORIES
from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorRegistry,
    ExecutorResult,
    ExecutorStatus,
)
from stepwise.models import (
    DecoratorRef,
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    JobStatus,
    Sidecar,
    StepDefinition,
    WorkflowDefinition,
    _now,
)

from tests.conftest import register_step_fn, run_job_sync


# ── Helpers ──────────────────────────────────────────────────────────


class _FailNTimesThenSucceed(Executor):
    """Mock executor that fails N times with a given error_category, then succeeds."""

    def __init__(self, fail_count: int, error_category: str, artifact: dict | None = None):
        self._fail_count = fail_count
        self._error_category = error_category
        self._artifact = artifact or {"result": "ok"}
        self._calls = 0

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        self._calls += 1
        if self._calls <= self._fail_count:
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={},
                    sidecar=Sidecar(),
                    workspace="",
                    timestamp=_now(),
                    executor_meta={"failed": True},
                ),
                executor_state={
                    "failed": True,
                    "error": f"simulated {self._error_category} error",
                    "error_category": self._error_category,
                },
            )
        return ExecutorResult(
            type="data",
            envelope=HandoffEnvelope(
                artifact=self._artifact,
                sidecar=Sidecar(),
                workspace="",
                timestamp=_now(),
            ),
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        return ExecutorStatus(state="completed")

    def cancel(self, state: dict) -> None:
        pass


class _AlwaysFail(Executor):
    """Mock executor that always fails with a given error_category."""

    def __init__(self, error_category: str):
        self._error_category = error_category
        self._calls = 0

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        self._calls += 1
        return ExecutorResult(
            type="data",
            envelope=HandoffEnvelope(
                artifact={},
                sidecar=Sidecar(),
                workspace="",
                timestamp=_now(),
                executor_meta={"failed": True},
            ),
            executor_state={
                "failed": True,
                "error": f"simulated {self._error_category} error",
                "error_category": self._error_category,
            },
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        return ExecutorStatus(state="completed")

    def cancel(self, state: dict) -> None:
        pass


# ── Tests ────────────────────────────────────────────────────────────


class TestTransientRetry:
    """Test transient-only retry filtering in RetryDecorator."""

    def test_transient_error_retries(self):
        """Transient failures (infra_failure) retry and eventually succeed."""
        inner = _FailNTimesThenSucceed(fail_count=2, error_category="infra_failure")
        wrapped = RetryDecorator(inner, {
            "max_retries": 3,
            "backoff": "exponential",
            "backoff_base": 0.01,
            "transient_only": True,
        })
        ctx = ExecutionContext(
            job_id="j1", step_name="implement", attempt=1,
            workspace_path="", idempotency="retriable",
        )
        result = wrapped.start({}, ctx)

        # Should succeed after 3 calls (2 failures + 1 success)
        assert inner._calls == 3
        assert result.envelope is not None
        assert result.envelope.executor_meta.get("failed") is None
        assert result.envelope.executor_meta["retry"]["attempts"] == 3
        assert len(result.envelope.executor_meta["retry"]["reasons"]) == 2

    def test_timeout_error_retries(self):
        """Transient failures (timeout) also retry."""
        inner = _FailNTimesThenSucceed(fail_count=1, error_category="timeout")
        wrapped = RetryDecorator(inner, {
            "max_retries": 2,
            "backoff": "exponential",
            "backoff_base": 0.01,
            "transient_only": True,
        })
        ctx = ExecutionContext(
            job_id="j1", step_name="step-a", attempt=1,
            workspace_path="", idempotency="retriable",
        )
        result = wrapped.start({}, ctx)

        assert inner._calls == 2
        assert result.envelope.executor_meta["retry"]["attempts"] == 2

    def test_non_transient_error_no_retry(self):
        """Non-transient error (agent_failure) fails immediately without retry."""
        inner = _AlwaysFail(error_category="agent_failure")
        wrapped = RetryDecorator(inner, {
            "max_retries": 3,
            "backoff": "exponential",
            "backoff_base": 0.01,
            "transient_only": True,
        })
        ctx = ExecutionContext(
            job_id="j1", step_name="implement", attempt=1,
            workspace_path="", idempotency="retriable",
        )
        result = wrapped.start({}, ctx)

        # Should fail after just 1 call — no retry for non-transient
        assert inner._calls == 1
        assert result.executor_state["failed"] is True
        assert result.envelope.executor_meta["retry"]["attempts"] == 1

    def test_context_length_error_no_retry(self):
        """context_length error is non-transient and should not retry."""
        inner = _AlwaysFail(error_category="context_length")
        wrapped = RetryDecorator(inner, {
            "max_retries": 3,
            "backoff": "exponential",
            "backoff_base": 0.01,
            "transient_only": True,
        })
        ctx = ExecutionContext(
            job_id="j1", step_name="step-x", attempt=1,
            workspace_path="", idempotency="retriable",
        )
        result = wrapped.start({}, ctx)

        assert inner._calls == 1
        assert result.executor_state["failed"] is True

    def test_transient_retry_exhaustion(self):
        """All retries exhausted for transient error → returns final failure."""
        inner = _AlwaysFail(error_category="infra_failure")
        wrapped = RetryDecorator(inner, {
            "max_retries": 2,
            "backoff": "exponential",
            "backoff_base": 0.01,
            "transient_only": True,
        })
        ctx = ExecutionContext(
            job_id="j1", step_name="implement", attempt=1,
            workspace_path="", idempotency="retriable",
        )
        result = wrapped.start({}, ctx)

        # 1 initial + 2 retries = 3 total calls
        assert inner._calls == 3
        assert result.executor_state["failed"] is True
        assert result.envelope.executor_meta["retry"]["attempts"] == 3
        assert len(result.envelope.executor_meta["retry"]["reasons"]) == 3

    def test_backoff_timing(self):
        """Exponential backoff delays roughly match expected schedule."""
        inner = _AlwaysFail(error_category="infra_failure")
        wrapped = RetryDecorator(inner, {
            "max_retries": 3,
            "backoff": "exponential",
            "backoff_base": 0.05,
            "transient_only": True,
        })
        ctx = ExecutionContext(
            job_id="j1", step_name="step-a", attempt=1,
            workspace_path="", idempotency="retriable",
        )

        start = time.monotonic()
        wrapped.start({}, ctx)
        elapsed = time.monotonic() - start

        # Expected delays: 0.05 + 0.10 + 0.20 = 0.35s (3 backoffs before final attempt)
        assert elapsed >= 0.30  # allow some tolerance
        assert elapsed < 1.0    # shouldn't take too long

    def test_transient_only_false_retries_all(self):
        """When transient_only=False, all errors are retried (original behavior)."""
        inner = _FailNTimesThenSucceed(fail_count=2, error_category="agent_failure")
        wrapped = RetryDecorator(inner, {
            "max_retries": 3,
            "backoff": "none",
            "transient_only": False,
        })
        ctx = ExecutionContext(
            job_id="j1", step_name="step-a", attempt=1,
            workspace_path="", idempotency="retriable",
        )
        result = wrapped.start({}, ctx)

        # With transient_only=False, agent_failure IS retried
        assert inner._calls == 3
        assert result.envelope.executor_meta["retry"]["attempts"] == 3

    def test_transient_categories_constant(self):
        """Verify the transient categories set contains the expected values."""
        assert TRANSIENT_ERROR_CATEGORIES == {"infra_failure", "timeout"}


class TestAgentDefaultRetry:
    """Test that agent executors get auto-wrapped with transient retry."""

    def test_agent_default_retry_decorator(self, registry):
        """Agent executor gets auto-wrapped with RetryDecorator when no retry specified."""
        # Register a mock agent factory
        registry.register("agent", lambda cfg: _AlwaysFail(error_category="infra_failure"))

        ref = ExecutorRef(type="agent", config={})
        executor = registry.create(ref)

        # Should be a RetryDecorator wrapping the inner executor
        assert isinstance(executor, RetryDecorator)
        assert executor._transient_only is True
        assert executor._max_retries == 3
        assert executor._backoff_base == 30

    def test_agent_user_retry_overrides_default(self, registry):
        """User-specified retry decorator prevents auto-apply of default."""
        registry.register("agent", lambda cfg: _AlwaysFail(error_category="infra_failure"))

        ref = ExecutorRef(
            type="agent",
            config={},
            decorators=[DecoratorRef(type="retry", config={"max_retries": 5})],
        )
        executor = registry.create(ref)

        # Should be a RetryDecorator but with user's config (max_retries=5)
        assert isinstance(executor, RetryDecorator)
        assert executor._max_retries == 5
        # Should NOT be double-wrapped
        assert not isinstance(executor._executor, RetryDecorator)

    def test_non_agent_no_auto_retry(self, registry):
        """Non-agent executors don't get auto-wrapped."""
        ref = ExecutorRef(
            type="callable",
            config={"fn_name": "noop"},
        )
        executor = registry.create(ref)

        # callable executor should NOT be wrapped in RetryDecorator
        assert not isinstance(executor, RetryDecorator)


class TestTransientRetryIntegration:
    """Integration test: transient retry through the engine."""

    def test_transient_retry_in_engine(self, async_engine):
        """A callable step mimicking transient failure retries and succeeds in engine."""
        call_count = 0

        def transient_then_succeed(inputs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={},
                        sidecar=Sidecar(),
                        workspace="",
                        timestamp=_now(),
                        executor_meta={"failed": True},
                    ),
                    executor_state={
                        "failed": True,
                        "error": "429 rate limit",
                        "error_category": "infra_failure",
                    },
                )
            return {"result": "done"}

        register_step_fn("transient_fn", transient_then_succeed)

        # Build workflow with retry decorator (short backoff for test speed)
        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(
                    type="callable",
                    config={"fn_name": "transient_fn"},
                    decorators=[DecoratorRef(type="retry", config={
                        "max_retries": 3,
                        "backoff": "exponential",
                        "backoff_base": 0.01,
                        "transient_only": True,
                    })],
                ),
                outputs=["result"],
            ),
        })
        job = async_engine.create_job(objective="test retry", workflow=wf)
        result = run_job_sync(async_engine, job.id)

        assert result.status == JobStatus.COMPLETED
        runs = async_engine.store.runs_for_job(job.id)
        assert runs[0].result.artifact["result"] == "done"
        assert runs[0].result.executor_meta["retry"]["attempts"] == 3
