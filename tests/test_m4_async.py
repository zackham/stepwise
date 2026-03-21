"""M4 tests: async execution, StepLimits, error categories, agent executor."""

import time
from datetime import datetime, timezone, timedelta

import pytest

from stepwise.agent import AgentExecutor, MockAgentBackend, AgentProcess, AgentStatus
from stepwise.engine import Engine
from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorRegistry,
    ExecutorResult,
    ExecutorStatus,
    ScriptExecutor,
    ExternalExecutor,
)
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
    StepLimits,
    StepRun,
    StepRunStatus,
    WorkflowDefinition,
    _gen_id,
    _now,
)
from stepwise.store import SQLiteStore
from tests.conftest import CallableExecutor, register_step_fn


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_backend():
    return MockAgentBackend()


@pytest.fixture
def agent_registry(mock_backend):
    reg = ExecutorRegistry()

    # Callable executor
    reg.register("callable", lambda config: CallableExecutor(
        fn_name=config.get("fn_name", "default"),
    ))

    # Script executor
    reg.register("script", lambda config: ScriptExecutor(
        command=config.get("command", "echo '{}'"),
    ))

    # External executor
    reg.register("external", lambda config: ExternalExecutor(
        prompt=config.get("prompt", ""),
    ))

    # Agent executor with mock backend
    def _create_agent(config):
        return AgentExecutor(
            backend=mock_backend,
            prompt=config.get("prompt", ""),
            output_mode=config.get("output_mode", "effect"),
            output_path=config.get("output_path"),
            **{k: v for k, v in config.items()
               if k not in ("prompt", "output_mode", "output_path")},
        )
    reg.register("agent", _create_agent)

    return reg


@pytest.fixture
def store():
    s = SQLiteStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def engine(store, agent_registry):
    return Engine(store=store, registry=agent_registry)


# ── Test: Blocking Agent with Engine ──────────────────────────────────


class TestBlockingAgentWithEngine:
    """Test that blocking AgentExecutor integrates correctly with Engine."""

    def test_agent_step_completes_immediately(self, engine, store, mock_backend):
        """Blocking agent step completes during start_job (no tick needed)."""
        mock_backend.set_auto_complete({"status": "completed"})
        wf = WorkflowDefinition(steps={
            "agent_step": StepDefinition(
                name="agent_step",
                outputs=["status"],
                executor=ExecutorRef("agent", {
                    "prompt": "Do something",
                    "output_mode": "effect",
                }),
            ),
        })
        job = engine.create_job("Test blocking", wf)
        engine.start_job(job.id)

        run = store.latest_run(job.id, "agent_step")
        assert run.status == StepRunStatus.COMPLETED
        assert run.result is not None

        job = store.load_job(job.id)
        assert job.status == JobStatus.COMPLETED

    def test_agent_step_produces_artifact(self, engine, store, mock_backend):
        """Blocking agent produces correct artifact data."""
        mock_backend.set_auto_complete({"status": "done", "count": 42})
        wf = WorkflowDefinition(steps={
            "agent_step": StepDefinition(
                name="agent_step",
                outputs=["status", "count"],
                executor=ExecutorRef("agent", {
                    "prompt": "Do something",
                }),
            ),
        })
        job = engine.create_job("Test artifact", wf)
        engine.start_job(job.id)

        run = store.latest_run(job.id, "agent_step")
        assert run.status == StepRunStatus.COMPLETED
        assert run.result.artifact["status"] == "done"
        assert run.result.artifact["count"] == 42

    def test_agent_step_failure_with_error_category(self, engine, store, mock_backend):
        """Blocking agent failure sets error_category on run."""
        mock_backend.set_auto_fail("Out of memory")
        wf = WorkflowDefinition(steps={
            "agent_step": StepDefinition(
                name="agent_step",
                outputs=["status"],
                executor=ExecutorRef("agent", {
                    "prompt": "Do something",
                }),
            ),
        })
        job = engine.create_job("Test fail", wf)
        engine.start_job(job.id)

        run = store.latest_run(job.id, "agent_step")
        assert run.status == StepRunStatus.FAILED
        assert "Out of memory" in run.error
        assert run.error_category == "agent_failure"

        job = store.load_job(job.id)
        assert job.status == JobStatus.FAILED

    def test_agent_spawn_count(self, engine, store, mock_backend):
        """Engine only spawns agent once for a single step."""
        mock_backend.set_auto_complete({"status": "done"})
        wf = WorkflowDefinition(steps={
            "agent_step": StepDefinition(
                name="agent_step",
                outputs=["status"],
                executor=ExecutorRef("agent", {"prompt": "Work"}),
            ),
        })
        job = engine.create_job("Test spawn count", wf)
        engine.start_job(job.id)
        assert mock_backend.spawn_count == 1


# ── Test: Async in DAG ────────────────────────────────────────────────


class TestAgentInDAG:
    """Test blocking agent steps in multi-step workflows."""

    def test_agent_step_chains_to_downstream(self, engine, store, mock_backend):
        """Agent step completes and downstream callable runs."""
        mock_backend.set_auto_complete({"data": "hello"})
        register_step_fn("downstream", lambda inputs: {"result": f"got {inputs['data']}"})

        wf = WorkflowDefinition(steps={
            "agent_step": StepDefinition(
                name="agent_step",
                outputs=["data"],
                executor=ExecutorRef("agent", {
                    "prompt": "Generate data",
                    "output_mode": "effect",
                }),
            ),
            "process": StepDefinition(
                name="process",
                outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "downstream"}),
                inputs=[InputBinding("data", "agent_step", "data")],
            ),
        })
        job = engine.create_job("Test DAG", wf)
        engine.start_job(job.id)

        # Both steps should complete during start_job (blocking agent)
        process_runs = store.runs_for_step(job.id, "process")
        assert len(process_runs) == 1
        assert process_runs[0].status == StepRunStatus.COMPLETED
        assert process_runs[0].result.artifact["result"] == "got hello"

        job = store.load_job(job.id)
        assert job.status == JobStatus.COMPLETED

    def test_agent_step_with_exit_rules(self, engine, store, mock_backend):
        """Agent step completion triggers downstream exit rule evaluation."""
        mock_backend.set_auto_complete({"plan": "my plan"})
        register_step_fn("scorer", lambda inputs: {
            "score": 5, "passed": True,
        })

        wf = WorkflowDefinition(steps={
            "agent_step": StepDefinition(
                name="agent_step",
                outputs=["plan"],
                executor=ExecutorRef("agent", {
                    "prompt": "Write a plan",
                    "output_mode": "effect",
                }),
            ),
            "score": StepDefinition(
                name="score",
                outputs=["score", "passed"],
                executor=ExecutorRef("callable", {"fn_name": "scorer"}),
                inputs=[InputBinding("plan", "agent_step", "plan")],
                exit_rules=[
                    ExitRule("pass", "field_match",
                             {"field": "passed", "value": True, "action": "advance"}, priority=10),
                ],
            ),
        })
        job = engine.create_job("Test exit rules", wf)
        engine.start_job(job.id)

        job = store.load_job(job.id)
        assert job.status == JobStatus.COMPLETED


# ── Test: StepLimits ──────────────────────────────────────────────────


class _SlowAsyncExecutor(Executor):
    """Test executor that returns type=async and stays running until completed externally."""

    def __init__(self):
        self._cancelled = False

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        return ExecutorResult(type="async", executor_state={"running": True})

    def check_status(self, state: dict) -> ExecutorStatus:
        if state.get("completed"):
            return ExecutorStatus(
                state="completed",
                result=ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact=state.get("result", {"status": "done"}),
                        sidecar=Sidecar(), workspace="", timestamp=_now(),
                    ),
                ),
            )
        return ExecutorStatus(state="running")

    def cancel(self, state: dict) -> None:
        self._cancelled = True


class TestStepLimits:
    """Test cost and duration limit enforcement."""

    @pytest.fixture
    def async_engine(self, store):
        """Engine with a slow async executor for limit testing."""
        reg = ExecutorRegistry()
        reg.register("slow_async", lambda config: _SlowAsyncExecutor())
        reg.register("callable", lambda config: CallableExecutor(fn_name=config.get("fn_name", "default")))
        return Engine(store=store, registry=reg)

    def test_duration_limit_kills_step(self, async_engine, store):
        """Step exceeding duration limit gets cancelled."""
        wf = WorkflowDefinition(steps={
            "slow_step": StepDefinition(
                name="slow_step",
                outputs=["status"],
                executor=ExecutorRef("slow_async", {}),
                limits=StepLimits(max_duration_minutes=0.001),  # ~60ms
            ),
        })
        job = async_engine.create_job("Test duration limit", wf)
        async_engine.start_job(job.id)

        # Step is RUNNING (async executor)
        run = store.latest_run(job.id, "slow_step")
        assert run.status == StepRunStatus.RUNNING

        # Wait for limit to be exceeded
        time.sleep(0.1)
        async_engine.tick()

        run = store.latest_run(job.id, "slow_step")
        assert run.status == StepRunStatus.FAILED
        assert run.error_category == "timeout"
        assert "Duration limit" in run.error

    def test_cost_limit_kills_step(self, async_engine, store):
        """Step exceeding cost limit gets cancelled (api_key billing only)."""
        async_engine.billing_mode = "api_key"
        wf = WorkflowDefinition(steps={
            "expensive_step": StepDefinition(
                name="expensive_step",
                outputs=["status"],
                executor=ExecutorRef("slow_async", {}),
                limits=StepLimits(max_cost_usd=0.50),
            ),
        })
        job = async_engine.create_job("Test cost limit", wf)
        async_engine.start_job(job.id)

        # Record cost exceeding limit
        run = store.runs_for_step(job.id, "expensive_step")[0]
        store.save_step_event(run.id, "cost", {"cost_usd": 0.60})

        async_engine.tick()

        run = store.latest_run(job.id, "expensive_step")
        assert run.status == StepRunStatus.FAILED
        assert run.error_category == "cost_limit"
        assert "Cost limit" in run.error

    def test_cost_limit_skipped_for_subscription_billing(self, async_engine, store):
        """Cost limits are NOT enforced when billing_mode is 'subscription' (default)."""
        assert async_engine.billing_mode == "subscription"  # default
        wf = WorkflowDefinition(steps={
            "expensive_step": StepDefinition(
                name="expensive_step",
                outputs=["status"],
                executor=ExecutorRef("slow_async", {}),
                limits=StepLimits(max_cost_usd=0.50),
            ),
        })
        job = async_engine.create_job("Test no cost enforcement", wf)
        async_engine.start_job(job.id)

        # Record cost exceeding limit
        run = store.runs_for_step(job.id, "expensive_step")[0]
        store.save_step_event(run.id, "cost", {"cost_usd": 0.60})

        async_engine.tick()

        # Step should still be running — cost limit not enforced for subscription
        run = store.latest_run(job.id, "expensive_step")
        assert run.status == StepRunStatus.RUNNING

    def test_limits_serialization(self):
        """StepLimits serialize/deserialize correctly."""
        limits = StepLimits(max_cost_usd=5.0, max_duration_minutes=30, max_iterations=5)
        d = limits.to_dict()
        assert d == {"max_cost_usd": 5.0, "max_duration_minutes": 30, "max_iterations": 5}

        restored = StepLimits.from_dict(d)
        assert restored.max_cost_usd == 5.0
        assert restored.max_duration_minutes == 30
        assert restored.max_iterations == 5

    def test_empty_limits_serialize_to_empty(self):
        """StepLimits with all None values serialize to empty dict."""
        limits = StepLimits()
        assert limits.to_dict() == {}

    def test_step_definition_with_limits(self):
        """StepDefinition serializes/deserializes limits correctly."""
        step = StepDefinition(
            name="test",
            outputs=["x"],
            executor=ExecutorRef("script", {"command": "echo hi"}),
            limits=StepLimits(max_cost_usd=2.0),
        )
        d = step.to_dict()
        assert d["limits"] == {"max_cost_usd": 2.0}

        restored = StepDefinition.from_dict(d)
        assert restored.limits is not None
        assert restored.limits.max_cost_usd == 2.0

    def test_step_definition_without_limits(self):
        """Existing step definitions without limits still work."""
        step = StepDefinition(
            name="test",
            outputs=["x"],
            executor=ExecutorRef("script", {"command": "echo hi"}),
        )
        d = step.to_dict()
        assert "limits" not in d

        restored = StepDefinition.from_dict(d)
        assert restored.limits is None

    def test_no_limits_means_no_enforcement(self, async_engine, store):
        """Steps without limits run indefinitely (no enforcement)."""
        wf = WorkflowDefinition(steps={
            "async_step": StepDefinition(
                name="async_step",
                outputs=["status"],
                executor=ExecutorRef("slow_async", {}),
            ),
        })
        job = async_engine.create_job("No limits", wf)
        async_engine.start_job(job.id)

        # Multiple ticks should not fail
        for _ in range(5):
            async_engine.tick()

        run = store.latest_run(job.id, "async_step")
        assert run.status == StepRunStatus.RUNNING


# ── Test: Error Categories and Failure Routing ────────────────────────


class TestErrorCategories:
    """Test error classification and exit rule routing on failures."""

    def test_error_category_on_failure(self, engine, store, mock_backend):
        """Failed step gets error_category set."""
        mock_backend.set_auto_fail("Connection refused")
        wf = WorkflowDefinition(steps={
            "agent_step": StepDefinition(
                name="agent_step",
                outputs=["status"],
                executor=ExecutorRef("agent", {"prompt": "Fail"}),
            ),
        })
        job = engine.create_job("Test error cat", wf)
        engine.start_job(job.id)

        run = store.latest_run(job.id, "agent_step")
        assert run.status == StepRunStatus.FAILED
        assert run.error_category == "infra_failure"

    def test_exit_rule_routes_failure_to_loop(self, engine, store, mock_backend):
        """Exit rule can catch a failure and loop to retry."""
        mock_backend.set_auto_fail("Connection refused")
        wf = WorkflowDefinition(steps={
            "agent_step": StepDefinition(
                name="agent_step",
                outputs=["status"],
                executor=ExecutorRef("agent", {"prompt": "Try this"}),
                exit_rules=[
                    ExitRule("retry_on_infra", "field_match", {
                        "field": "error_category",
                        "value": "infra_failure",
                        "action": "loop",
                        "target": "agent_step",
                        "max_iterations": 3,
                    }, priority=10),
                ],
            ),
        })
        job = engine.create_job("Test failure routing", wf)
        engine.start_job(job.id)

        # The exit rule should have triggered a retry — blocking start() means
        # the retry also runs immediately. Two attempts: both fail with same auto_fail.
        runs = store.runs_for_step(job.id, "agent_step")
        assert len(runs) >= 2
        assert runs[0].status == StepRunStatus.FAILED

    def test_exit_rule_escalates_on_agent_failure(self, engine, store, mock_backend):
        """Exit rule can escalate on agent_failure."""
        mock_backend.set_auto_fail("Agent could not complete task")
        wf = WorkflowDefinition(steps={
            "agent_step": StepDefinition(
                name="agent_step",
                outputs=["status"],
                executor=ExecutorRef("agent", {"prompt": "Try this"}),
                exit_rules=[
                    ExitRule("escalate_on_failure", "field_match", {
                        "field": "error_category",
                        "value": "agent_failure",
                        "action": "escalate",
                    }, priority=10),
                ],
            ),
        })
        job = engine.create_job("Test escalate", wf)
        engine.start_job(job.id)

        job = store.load_job(job.id)
        assert job.status == JobStatus.PAUSED  # Escalated, not failed

    def test_unhandled_failure_halts_job(self, engine, store, mock_backend):
        """Failure with no matching exit rule halts the job."""
        mock_backend.set_auto_fail("Total failure")
        wf = WorkflowDefinition(steps={
            "agent_step": StepDefinition(
                name="agent_step",
                outputs=["status"],
                executor=ExecutorRef("agent", {"prompt": "Try this"}),
                # No exit rules for failures
            ),
        })
        job = engine.create_job("Test unhandled", wf)
        engine.start_job(job.id)

        job = store.load_job(job.id)
        assert job.status == JobStatus.FAILED

    def test_timeout_limit_sets_timeout_category(self, store):
        """Duration limit exceeded sets error_category=timeout on async executor."""
        reg = ExecutorRegistry()
        reg.register("slow_async", lambda config: _SlowAsyncExecutor())
        eng = Engine(store=store, registry=reg)

        wf = WorkflowDefinition(steps={
            "slow_step": StepDefinition(
                name="slow_step",
                outputs=["status"],
                executor=ExecutorRef("slow_async", {}),
                limits=StepLimits(max_duration_minutes=0.001),
                exit_rules=[
                    ExitRule("handle_timeout", "field_match", {
                        "field": "error_category",
                        "value": "timeout",
                        "action": "escalate",
                    }, priority=10),
                ],
            ),
        })
        job = eng.create_job("Test timeout routing", wf)
        eng.start_job(job.id)

        time.sleep(0.1)
        eng.tick()

        # Should be escalated (paused), not failed
        job = store.load_job(job.id)
        assert job.status == JobStatus.PAUSED

        run = store.latest_run(job.id, "slow_step")
        assert run.error_category == "timeout"


# ── Test: A→B→C→B→D Loop Pattern ─────────────────────────────────────


class TestLoopPattern:
    """Test the plan→score→refine→score→implement pattern."""

    def test_plan_score_refine_loop(self, engine, store):
        """A→B→C→B→D: plan, score (fail), refine, score (pass), implement.

        Uses callable executors to test the DAG loop flow without async concerns.
        """
        score_call_count = [0]

        def score_fn(inputs):
            score_call_count[0] += 1
            plan = inputs.get("plan_content", "unknown")
            if score_call_count[0] == 1:
                return {
                    "plan_content": plan,
                    "passed": False, "score": 2,
                    "suggestions": "needs work",
                }
            return {
                "plan_content": plan,
                "passed": True, "score": 5,
                "suggestions": "good",
            }

        register_step_fn("score_fn", score_fn)
        register_step_fn("plan_fn", lambda inputs: {"plan_content": "initial plan"})
        register_step_fn("refine_fn", lambda inputs: {"plan_content": "refined plan"})
        register_step_fn("implement_fn", lambda inputs: {"status": "done"})

        wf = WorkflowDefinition(steps={
            "plan": StepDefinition(
                name="plan",
                outputs=["plan_content"],
                executor=ExecutorRef("callable", {"fn_name": "plan_fn"}),
            ),
            "score": StepDefinition(
                name="score",
                outputs=["plan_content", "passed", "score", "suggestions"],
                executor=ExecutorRef("callable", {"fn_name": "score_fn"}),
                inputs=[InputBinding("plan_content", "plan", "plan_content")],
                exit_rules=[
                    ExitRule("pass", "field_match",
                             {"field": "passed", "value": True, "action": "advance"}, priority=10),
                    ExitRule("refine", "field_match",
                             {"field": "passed", "value": False,
                              "action": "loop", "target": "refine", "max_iterations": 3}, priority=5),
                ],
            ),
            "refine": StepDefinition(
                name="refine",
                outputs=["plan_content"],
                executor=ExecutorRef("callable", {"fn_name": "refine_fn"}),
                inputs=[
                    InputBinding("plan_content", "score", "plan_content"),
                    InputBinding("suggestions", "score", "suggestions"),
                ],
                exit_rules=[
                    ExitRule("rescore", "always",
                             {"action": "loop", "target": "score"}, priority=10),
                ],
            ),
            "implement": StepDefinition(
                name="implement",
                outputs=["status"],
                executor=ExecutorRef("callable", {"fn_name": "implement_fn"}),
                inputs=[InputBinding("plan_content", "score", "plan_content")],
                sequencing=["score"],
            ),
        })

        job = engine.create_job("Test loop", wf)
        engine.start_job(job.id)

        # All steps complete synchronously during start_job
        job = store.load_job(job.id)
        assert job.status == JobStatus.COMPLETED

        # Score ran twice (fail then pass)
        score_runs = store.runs_for_step(job.id, "score")
        assert len(score_runs) == 2
        assert score_runs[0].result.artifact["passed"] is False
        assert score_runs[1].result.artifact["passed"] is True

        # Refine ran once
        refine_runs = store.runs_for_step(job.id, "refine")
        assert len(refine_runs) == 1

        # Implement ran
        impl_run = store.latest_run(job.id, "implement")
        assert impl_run.status == StepRunStatus.COMPLETED
        assert impl_run.result.artifact["status"] == "done"


# ── Test: Agent Executor ──────────────────────────────────────────────


class TestAgentExecutor:
    """Test the AgentExecutor implementation (blocking start())."""

    def test_agent_executor_start_returns_data(self, mock_backend):
        """AgentExecutor.start() blocks and returns type=data with executor_state."""
        mock_backend.set_auto_complete({"status": "done"})
        executor = AgentExecutor(
            backend=mock_backend,
            prompt="Hello $name",
            output_mode="effect",
        )
        ctx = ExecutionContext(
            job_id="job-1", step_name="test", attempt=1,
            workspace_path="/tmp/test", idempotency="idempotent",
        )
        result = executor.start({"name": "world"}, ctx)

        assert result.type == "data"
        assert result.executor_state is not None
        assert "pid" in result.executor_state
        assert result.executor_state["output_mode"] == "effect"

    def test_agent_executor_completed(self, mock_backend):
        """start() returns data result with envelope after process exits."""
        mock_backend.set_auto_complete({"status": "done"})
        executor = AgentExecutor(backend=mock_backend, prompt="test")
        ctx = ExecutionContext(
            job_id="job-1", step_name="test", attempt=1,
            workspace_path="/tmp/test", idempotency="idempotent",
        )
        result = executor.start({}, ctx)

        assert result.type == "data"
        assert result.envelope is not None

    def test_agent_executor_failed(self, mock_backend):
        """start() returns failure with error category."""
        mock_backend.set_auto_fail("Connection refused")
        executor = AgentExecutor(backend=mock_backend, prompt="test")
        ctx = ExecutionContext(
            job_id="job-1", step_name="test", attempt=1,
            workspace_path="/tmp/test", idempotency="idempotent",
        )
        result = executor.start({}, ctx)

        assert result.type == "data"
        assert result.executor_state["failed"] is True
        assert "Connection refused" in result.executor_state["error"]
        assert result.executor_state["error_category"] == "infra_failure"

    def test_agent_executor_cancel(self, mock_backend):
        """cancel() delegates to backend."""
        mock_backend.set_auto_complete()
        executor = AgentExecutor(backend=mock_backend, prompt="test")
        ctx = ExecutionContext(
            job_id="job-1", step_name="test", attempt=1,
            workspace_path="/tmp/test", idempotency="idempotent",
        )
        result = executor.start({}, ctx)

        executor.cancel(result.executor_state)
        assert mock_backend.cancel_count == 1

    def test_prompt_template_rendering(self, mock_backend):
        """Prompt template renders with input values."""
        mock_backend.set_auto_complete()
        executor = AgentExecutor(
            backend=mock_backend,
            prompt="Analyze $topic and report on $aspect",
        )
        ctx = ExecutionContext(
            job_id="job-1", step_name="test", attempt=1,
            workspace_path="/tmp/test", idempotency="idempotent",
        )
        executor.start({"topic": "databases", "aspect": "performance"}, ctx)

        # Check that the prompt was rendered
        info = mock_backend.get_process_info(mock_backend._next_pid - 1)
        assert info is not None
        assert "databases" in info["prompt"]
        assert "performance" in info["prompt"]

    def test_output_mode_preserved_in_state(self, mock_backend):
        """Output mode is stored in executor_state."""
        mock_backend.set_auto_complete()
        for mode in ["effect", "file", "stream_result"]:
            executor = AgentExecutor(
                backend=mock_backend, prompt="test", output_mode=mode,
            )
            ctx = ExecutionContext(
                job_id="job-1", step_name="test", attempt=1,
                workspace_path="/tmp/test", idempotency="idempotent",
            )
            result = executor.start({}, ctx)
            assert result.executor_state["output_mode"] == mode

    def test_completed_agent_returns_envelope(self, mock_backend):
        """Completed agent produces a HandoffEnvelope with artifact."""
        mock_backend.set_auto_complete({"analysis": "done", "confidence": 0.95})
        executor = AgentExecutor(backend=mock_backend, prompt="test")
        ctx = ExecutionContext(
            job_id="job-1", step_name="test", attempt=1,
            workspace_path="/tmp/test", idempotency="idempotent",
        )
        result = executor.start({}, ctx)

        assert result.type == "data"
        assert result.envelope.artifact["analysis"] == "done"
        assert result.envelope.artifact["confidence"] == 0.95

    def test_error_classification(self, mock_backend):
        """Error messages are classified into categories."""
        executor = AgentExecutor(backend=mock_backend, prompt="test")
        ctx = ExecutionContext(
            job_id="job-1", step_name="test", attempt=1,
            workspace_path="/tmp/test", idempotency="idempotent",
        )

        test_cases = [
            ("Connection refused", "infra_failure"),
            ("Rate limit exceeded (429)", "infra_failure"),
            ("Request timed out", "timeout"),
            ("Context length exceeded", "context_length"),
            ("Could not complete task", "agent_failure"),
        ]
        for error_msg, expected_cat in test_cases:
            mock_backend.set_auto_fail(error_msg)
            result = executor.start({}, ctx)
            assert result.executor_state["error_category"] == expected_cat, (
                f"Expected '{expected_cat}' for '{error_msg}', got '{result.executor_state.get('error_category')}'"
            )


# ── Test: Cancel Running Step ────────────────────────────────────────


class TestCancelStep:
    """Test cancelling running steps (uses async executor to keep step running)."""

    def test_cancel_running_async_step(self, store):
        """Cancelling a running async step marks it failed."""
        reg = ExecutorRegistry()
        reg.register("slow_async", lambda config: _SlowAsyncExecutor())
        eng = Engine(store=store, registry=reg)

        wf = WorkflowDefinition(steps={
            "slow_step": StepDefinition(
                name="slow_step",
                outputs=["status"],
                executor=ExecutorRef("slow_async", {}),
            ),
        })
        job = eng.create_job("Test cancel", wf)
        eng.start_job(job.id)

        run = store.runs_for_step(job.id, "slow_step")[0]
        assert run.status == StepRunStatus.RUNNING

        eng.cancel_job(job.id)

        job = store.load_job(job.id)
        assert job.status == JobStatus.CANCELLED

        run = store.latest_run(job.id, "slow_step")
        assert run.status == StepRunStatus.FAILED
        assert "cancelled" in run.error.lower()


# ── Test: Step Events ─────────────────────────────────────────────────


class TestStepEvents:
    """Test step_events table for fine-grained activity tracking."""

    def test_save_and_load_step_events(self, store):
        """Basic CRUD for step events."""
        store.save_step_event("run-1", "text", {"content": "Hello"})
        store.save_step_event("run-1", "tool_call", {"tool": "read_file"})
        store.save_step_event("run-1", "cost", {"cost_usd": 0.05})

        events = store.load_step_events("run-1")
        assert len(events) == 3
        assert events[0]["type"] == "text"
        assert events[1]["type"] == "tool_call"
        assert events[2]["type"] == "cost"

    def test_accumulated_cost(self, store):
        """Accumulated cost sums cost events."""
        store.save_step_event("run-1", "cost", {"cost_usd": 0.05})
        store.save_step_event("run-1", "cost", {"cost_usd": 0.10})
        store.save_step_event("run-1", "cost", {"cost_usd": 0.03})

        total = store.accumulated_cost("run-1")
        assert abs(total - 0.18) < 0.001

    def test_accumulated_cost_zero_when_no_events(self, store):
        """No cost events means zero cost."""
        assert store.accumulated_cost("nonexistent-run") == 0.0

    def test_step_event_count(self, store):
        """Count step events for a run."""
        for i in range(5):
            store.save_step_event("run-1", "text", {"n": i})

        assert store.step_event_count("run-1") == 5
        assert store.step_event_count("run-other") == 0

    def test_batch_insert(self, store):
        """Batch insert multiple events."""
        events = [
            ("run-1", "2026-01-01T00:00:00", "text", {"n": 1}),
            ("run-1", "2026-01-01T00:00:01", "tool_call", {"n": 2}),
            ("run-1", "2026-01-01T00:00:02", "cost", {"cost_usd": 0.01}),
        ]
        store.save_step_events_batch(events)

        loaded = store.load_step_events("run-1")
        assert len(loaded) == 3


# ── Test: StepRun Error Category ──────────────────────────────────────


class TestStepRunErrorCategory:
    """Test error_category on StepRun model."""

    def test_error_category_serialization(self):
        """error_category round-trips through to_dict/from_dict."""
        run = StepRun(
            id="run-1", job_id="job-1", step_name="test",
            attempt=1, status=StepRunStatus.FAILED,
            error="timeout", error_category="timeout",
        )
        d = run.to_dict()
        assert d["error_category"] == "timeout"

        restored = StepRun.from_dict(d)
        assert restored.error_category == "timeout"

    def test_error_category_none_by_default(self):
        """error_category defaults to None."""
        run = StepRun(
            id="run-1", job_id="job-1", step_name="test",
            attempt=1, status=StepRunStatus.COMPLETED,
        )
        assert run.error_category is None
        d = run.to_dict()
        assert d["error_category"] is None

    def test_error_category_persists_in_store(self, store):
        """error_category persists through SQLite."""
        from stepwise.models import _now
        run = StepRun(
            id="run-1", job_id="job-1", step_name="test",
            attempt=1, status=StepRunStatus.FAILED,
            error="timeout", error_category="timeout",
            started_at=_now(),
        )
        # Need a job first
        job = Job(
            id="job-1", objective="test",
            workflow=WorkflowDefinition(steps={
                "test": StepDefinition(name="test", outputs=[], executor=ExecutorRef("script", {})),
            }),
        )
        store.save_job(job)
        store.save_run(run)

        loaded = store.load_run("run-1")
        assert loaded.error_category == "timeout"


# ── Test: Max Iterations in StepLimits ────────────────────────────────


class TestMaxIterations:
    """Test max_iterations as a StepLimits field (not just exit rule config)."""

    def test_step_limits_max_iterations(self):
        """StepLimits.max_iterations serializes correctly."""
        limits = StepLimits(max_iterations=5)
        d = limits.to_dict()
        assert d["max_iterations"] == 5

        restored = StepLimits.from_dict(d)
        assert restored.max_iterations == 5


# ── Test: Mixed Sync + Async Workflow ─────────────────────────────────


class TestMixedWorkflow:
    """Test workflows with both sync (script/callable) and blocking agent steps."""

    def test_sync_then_agent_then_sync(self, engine, store, mock_backend):
        """Callable → Agent → Callable pipeline with blocking agent."""
        mock_backend.set_auto_complete({"output": "processed data"})
        register_step_fn("prep", lambda inputs: {"data": "prepared"})
        register_step_fn("finish", lambda inputs: {"result": f"finished with {inputs['output']}"})

        wf = WorkflowDefinition(steps={
            "prep": StepDefinition(
                name="prep",
                outputs=["data"],
                executor=ExecutorRef("callable", {"fn_name": "prep"}),
            ),
            "agent": StepDefinition(
                name="agent",
                outputs=["output"],
                executor=ExecutorRef("agent", {"prompt": "Process $data"}),
                inputs=[InputBinding("data", "prep", "data")],
            ),
            "finish": StepDefinition(
                name="finish",
                outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "finish"}),
                inputs=[InputBinding("output", "agent", "output")],
            ),
        })
        job = engine.create_job("Mixed workflow", wf)
        engine.start_job(job.id)

        # All steps complete during start_job (blocking agent)
        prep_run = store.latest_run(job.id, "prep")
        assert prep_run.status == StepRunStatus.COMPLETED

        agent_run = store.latest_run(job.id, "agent")
        assert agent_run.status == StepRunStatus.COMPLETED

        finish_run = store.latest_run(job.id, "finish")
        assert finish_run.status == StepRunStatus.COMPLETED
        assert finish_run.result.artifact["result"] == "finished with processed data"

        job = store.load_job(job.id)
        assert job.status == JobStatus.COMPLETED
