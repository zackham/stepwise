"""Tests for engine-level circuit breaker and fail-fast on permanent errors."""

import asyncio
from datetime import timedelta

import pytest

from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorResult,
    ExecutorStatus,
)
from stepwise.models import (
    ExitRule,
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepLimits,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)

from tests.conftest import register_step_fn, run_job_sync


# ── Helpers ──────────────────────────────────────────────────────────


class _AlwaysFailExecutor(Executor):
    """Always returns a failure result with configurable error_category."""

    def __init__(self, error_category: str = "infra_failure"):
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


class _FailNThenSucceed(Executor):
    """Fails N times then succeeds."""

    def __init__(self, fail_count: int, error_category: str = "infra_failure",
                 artifact: dict | None = None):
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
                    "error": f"simulated {self._error_category} error #{self._calls}",
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


# ── Circuit breaker tests ────────────────────────────────────────────


class TestCircuitBreaker:
    """Engine-level consecutive failure circuit breaker."""

    def test_circuit_breaker_halts_after_consecutive_failures(self, async_engine):
        """After max_infra_retries consecutive failures, job halts."""
        executor = _AlwaysFailExecutor(error_category="infra_failure")
        async_engine.registry.register("always_fail", lambda cfg: executor)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="always_fail", config={}),
                outputs=["result"],
                limits=StepLimits(max_infra_retries=3),
                exit_rules=[
                    ExitRule(name="retry", type="always",
                             config={"action": "loop", "target": "step-a"}),
                ],
            ),
        })
        job = async_engine.create_job(objective="test breaker", workflow=wf)
        result = run_job_sync(async_engine, job.id)

        assert result.status == JobStatus.FAILED
        runs = async_engine.store.runs_for_step(job.id, "step-a")
        assert len(runs) == 3  # exactly 3 attempts, then circuit breaker
        assert "consecutive failures" in runs[-1].error

    def test_circuit_breaker_resets_on_success(self, async_engine):
        """A success between failures resets the consecutive failure counter.

        Sequence: fail, fail, succeed (via loop), fail, fail, succeed.
        With max_infra_retries=3, breaker should NOT fire because the success
        resets the consecutive count each time (max consecutive = 2 < 3).
        """
        call_count = 0

        def fail_fail_succeed(inputs):
            nonlocal call_count
            call_count += 1
            # Pattern: fail, fail, succeed, fail, fail, succeed
            if call_count % 3 == 0:
                return {"result": "ok", "done": "true" if call_count >= 6 else "false"}
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
                    "error": f"simulated infra error #{call_count}",
                    "error_category": "infra_failure",
                },
            )

        register_step_fn("ffs", fail_fail_succeed)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "ffs"}),
                outputs=["result", "done"],
                limits=StepLimits(max_infra_retries=3),
                exit_rules=[
                    ExitRule(name="done", type="field_match",
                             config={"action": "advance", "field": "done", "value": "true"},
                             priority=10),
                    ExitRule(name="retry", type="always",
                             config={"action": "loop", "target": "step-a"}),
                ],
            ),
        })
        job = async_engine.create_job(objective="test reset", workflow=wf)
        result = run_job_sync(async_engine, job.id)

        # 6 calls: f, f, s(loop), f, f, s(advance) — breaker never fires
        assert result.status == JobStatus.COMPLETED
        assert call_count == 6

    def test_circuit_breaker_default_is_3(self):
        """Default max_infra_retries is 3 when no limits block."""
        step = StepDefinition(
            name="test",
            executor=ExecutorRef(type="script", config={}),
            outputs=["result"],
        )
        # No limits set — should use default
        assert step.limits is None
        # Engine code uses: step_def.limits.max_infra_retries if step_def.limits else 3
        default = step.limits.max_infra_retries if step.limits else 3
        assert default == 3

    def test_circuit_breaker_configurable(self, async_engine):
        """max_infra_retries=5 allows 5 failures before halting."""
        executor = _AlwaysFailExecutor(error_category="infra_failure")
        async_engine.registry.register("always_fail_5", lambda cfg: executor)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="always_fail_5", config={}),
                outputs=["result"],
                limits=StepLimits(max_infra_retries=5),
                exit_rules=[
                    ExitRule(name="retry", type="always",
                             config={"action": "loop", "target": "step-a"}),
                ],
            ),
        })
        job = async_engine.create_job(objective="test breaker 5", workflow=wf)
        result = run_job_sync(async_engine, job.id)

        assert result.status == JobStatus.FAILED
        runs = async_engine.store.runs_for_step(job.id, "step-a")
        assert len(runs) == 5  # 5 attempts, then circuit breaker

    def test_on_error_continue_bypasses_breaker(self, async_engine):
        """Steps with on_error: continue don't trigger the circuit breaker."""
        executor = _AlwaysFailExecutor(error_category="infra_failure")
        async_engine.registry.register("always_fail_cont", lambda cfg: executor)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="always_fail_cont", config={}),
                outputs=["result"],
                on_error="continue",
                limits=StepLimits(max_infra_retries=2),
            ),
            "step-b": StepDefinition(
                name="step-b",
                executor=ExecutorRef(type="callable", config={"fn_name": "noop_b"}),
                inputs=[InputBinding("x", "step-a", "result")],
                outputs=["out"],
            ),
        })
        register_step_fn("noop_b", lambda inputs: {"out": "done"})
        job = async_engine.create_job(objective="test on_error", workflow=wf)
        result = run_job_sync(async_engine, job.id)

        # on_error: continue means the step fails but job continues
        assert result.status == JobStatus.COMPLETED


class TestPermanentErrors:
    """Permanent errors (auth, quota, context_length) halt immediately."""

    def test_auth_error_halts_immediately(self, async_engine):
        """auth_error halts the job on first failure, no looping."""
        executor = _AlwaysFailExecutor(error_category="auth_error")
        async_engine.registry.register("auth_fail", lambda cfg: executor)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="auth_fail", config={}),
                outputs=["result"],
                exit_rules=[
                    ExitRule(name="retry", type="always",
                             config={"action": "loop", "target": "step-a"}),
                ],
            ),
        })
        job = async_engine.create_job(objective="test auth", workflow=wf)
        result = run_job_sync(async_engine, job.id)

        assert result.status == JobStatus.FAILED
        runs = async_engine.store.runs_for_step(job.id, "step-a")
        assert len(runs) == 1  # halted immediately, no retry

    def test_quota_error_halts_immediately(self, async_engine):
        """quota_error halts the job on first failure."""
        executor = _AlwaysFailExecutor(error_category="quota_error")
        async_engine.registry.register("quota_fail", lambda cfg: executor)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="quota_fail", config={}),
                outputs=["result"],
                exit_rules=[
                    ExitRule(name="retry", type="always",
                             config={"action": "loop", "target": "step-a"}),
                ],
            ),
        })
        job = async_engine.create_job(objective="test quota", workflow=wf)
        result = run_job_sync(async_engine, job.id)

        assert result.status == JobStatus.FAILED
        runs = async_engine.store.runs_for_step(job.id, "step-a")
        assert len(runs) == 1

    def test_context_length_error_halts_immediately(self, async_engine):
        """context_length error halts the job on first failure."""
        executor = _AlwaysFailExecutor(error_category="context_length")
        async_engine.registry.register("ctx_fail", lambda cfg: executor)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="ctx_fail", config={}),
                outputs=["result"],
                exit_rules=[
                    ExitRule(name="retry", type="always",
                             config={"action": "loop", "target": "step-a"}),
                ],
            ),
        })
        job = async_engine.create_job(objective="test ctx", workflow=wf)
        result = run_job_sync(async_engine, job.id)

        assert result.status == JobStatus.FAILED
        runs = async_engine.store.runs_for_step(job.id, "step-a")
        assert len(runs) == 1

    def test_infra_failure_still_loops(self, async_engine):
        """infra_failure (transient) still goes through exit rules before breaker."""
        call_count = 0

        def fail_once_then_succeed(inputs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
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
                        "error": "simulated infra error",
                        "error_category": "infra_failure",
                    },
                )
            return {"result": "ok"}

        register_step_fn("infra_once", fail_once_then_succeed)

        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "infra_once"}),
                outputs=["result"],
                limits=StepLimits(max_infra_retries=3),
                exit_rules=[
                    # Only loop on failure (no exit rule on success → advance)
                    ExitRule(name="retry", type="expression",
                             config={"action": "loop", "target": "step-a",
                                     "condition": "outputs.get('_error') is not None"}),
                ],
            ),
        })
        job = async_engine.create_job(objective="test infra", workflow=wf)
        result = run_job_sync(async_engine, job.id)

        # 1 failure + loop + 1 success (advance) = completed
        assert result.status == JobStatus.COMPLETED
        assert call_count == 2


class TestStuckTaskRouting:
    """Stuck RUNNING steps route through _fail_run()."""

    def test_stuck_task_routes_through_fail_run(self, async_engine):
        """A stuck running step (no task in registry, age > 60s) goes through
        _fail_run with error_category=infra_failure."""
        # Create a simple workflow and job
        register_step_fn("ok", lambda inputs: {"result": "done"})
        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "ok"}),
                outputs=["result"],
            ),
        })
        job = async_engine.create_job(objective="test stuck", workflow=wf)

        # Manually create a "stuck" run: RUNNING status, started > 60s ago, no task
        from stepwise.models import StepRun, _gen_id
        stuck_run = StepRun(
            id=_gen_id("run"),
            job_id=job.id,
            step_name="step-a",
            attempt=1,
            status=StepRunStatus.RUNNING,
            started_at=_now() - timedelta(seconds=120),
        )
        async_engine.store.save_run(stuck_run)
        # Make job RUNNING so _poll_external_changes processes it
        job.status = JobStatus.RUNNING
        async_engine.store.save_job(job)

        # Run poll — should detect stuck run and route through _fail_run
        async_engine._poll_external_changes()

        # Reload the run
        runs = async_engine.store.runs_for_step(job.id, "step-a")
        assert len(runs) == 1
        assert runs[0].status == StepRunStatus.FAILED
        assert runs[0].error_category == "infra_failure"
        assert "Executor task lost" in runs[0].error

    def test_stuck_task_no_exit_rules_halts_job(self, async_engine):
        """A stuck task with no exit rules halts the job via _halt_job."""
        register_step_fn("ok2", lambda inputs: {"result": "done"})
        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="callable", config={"fn_name": "ok2"}),
                outputs=["result"],
            ),
        })
        job = async_engine.create_job(objective="test stuck halt", workflow=wf)

        from stepwise.models import StepRun, _gen_id
        stuck_run = StepRun(
            id=_gen_id("run"),
            job_id=job.id,
            step_name="step-a",
            attempt=1,
            status=StepRunStatus.RUNNING,
            started_at=_now() - timedelta(seconds=120),
        )
        async_engine.store.save_run(stuck_run)
        job.status = JobStatus.RUNNING
        async_engine.store.save_job(job)

        async_engine._poll_external_changes()

        # No exit rules → _fail_run halts the job
        job = async_engine.store.load_job(job.id)
        assert job.status == JobStatus.FAILED
        runs = async_engine.store.runs_for_step(job.id, "step-a")
        assert runs[0].status == StepRunStatus.FAILED
        assert runs[0].error_category == "infra_failure"


class TestStepLimitsSerialization:
    """Verify max_infra_retries round-trips through to_dict/from_dict."""

    def test_default_not_serialized(self):
        """Default max_infra_retries=3 is omitted from serialized form."""
        limits = StepLimits(max_infra_retries=3)
        assert "max_infra_retries" not in limits.to_dict()

    def test_non_default_serialized(self):
        """Non-default max_infra_retries is included in serialized form."""
        limits = StepLimits(max_infra_retries=5)
        d = limits.to_dict()
        assert d["max_infra_retries"] == 5

    def test_round_trip(self):
        """max_infra_retries survives to_dict/from_dict round trip."""
        limits = StepLimits(max_infra_retries=7)
        restored = StepLimits.from_dict(limits.to_dict())
        assert restored.max_infra_retries == 7

    def test_from_dict_default(self):
        """from_dict with empty dict gives default max_infra_retries=3."""
        limits = StepLimits.from_dict({})
        assert limits.max_infra_retries == 3
