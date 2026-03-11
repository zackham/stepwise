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
    HumanExecutor,
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

    # Human executor
    reg.register("human", lambda config: HumanExecutor(
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


# ── Test: Async Result Type ───────────────────────────────────────────


class TestAsyncResultType:
    """Test that executors can return type="async" and be polled."""

    def test_async_executor_returns_running(self, engine, store, mock_backend):
        """Agent executor returns async result, run stays RUNNING."""
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
        job = engine.create_job("Test async", wf)
        engine.start_job(job.id)

        runs = store.runs_for_step(job.id, "agent_step")
        assert len(runs) == 1
        assert runs[0].status == StepRunStatus.RUNNING
        assert runs[0].executor_state is not None
        assert "pid" in runs[0].executor_state

        # Job should still be running
        job = store.load_job(job.id)
        assert job.status == JobStatus.RUNNING

    def test_async_executor_completes_on_tick(self, engine, store, mock_backend):
        """After backend completes, next tick completes the run."""
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
        job = engine.create_job("Test async complete", wf)
        engine.start_job(job.id)

        # Get the run and its PID
        run = store.runs_for_step(job.id, "agent_step")[0]
        pid = run.executor_state["pid"]

        # Complete the mock process
        mock_backend.complete_process(pid, {"status": "completed"})

        # Tick should complete the run
        engine.tick()

        run = store.latest_run(job.id, "agent_step")
        assert run.status == StepRunStatus.COMPLETED
        assert run.result is not None
        assert run.result.artifact["status"] == "completed"

        # Job should be completed (single terminal step)
        job = store.load_job(job.id)
        assert job.status == JobStatus.COMPLETED

    def test_async_executor_failure(self, engine, store, mock_backend):
        """Backend failure marks run as failed."""
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
        job = engine.create_job("Test async fail", wf)
        engine.start_job(job.id)

        run = store.runs_for_step(job.id, "agent_step")[0]
        pid = run.executor_state["pid"]

        # Fail the mock process
        mock_backend.fail_process(pid, "Out of memory")

        engine.tick()

        run = store.latest_run(job.id, "agent_step")
        assert run.status == StepRunStatus.FAILED
        assert "Out of memory" in run.error
        assert run.error_category == "agent_failure"

        job = store.load_job(job.id)
        assert job.status == JobStatus.FAILED

    def test_async_stays_running_between_ticks(self, engine, store, mock_backend):
        """Run stays RUNNING across multiple ticks while agent is working."""
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
        job = engine.create_job("Test running", wf)
        engine.start_job(job.id)

        # Multiple ticks while still running
        for _ in range(3):
            engine.tick()
            run = store.latest_run(job.id, "agent_step")
            assert run.status == StepRunStatus.RUNNING

        # Now complete
        pid = run.executor_state["pid"]
        mock_backend.complete_process(pid, {"status": "completed"})
        engine.tick()

        run = store.latest_run(job.id, "agent_step")
        assert run.status == StepRunStatus.COMPLETED


# ── Test: Async in DAG ────────────────────────────────────────────────


class TestAsyncInDAG:
    """Test async steps in multi-step workflows."""

    def test_async_step_blocks_downstream(self, engine, store, mock_backend):
        """Downstream steps wait for async step to complete."""
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

        # Agent step is running, process step should NOT be launched
        assert len(store.runs_for_step(job.id, "process")) == 0
        engine.tick()
        assert len(store.runs_for_step(job.id, "process")) == 0

        # Complete agent step
        run = store.runs_for_step(job.id, "agent_step")[0]
        mock_backend.complete_process(run.executor_state["pid"], {"data": "hello"})
        engine.tick()

        # Now process step should have run
        process_runs = store.runs_for_step(job.id, "process")
        assert len(process_runs) == 1
        assert process_runs[0].status == StepRunStatus.COMPLETED
        assert process_runs[0].result.artifact["result"] == "got hello"

    def test_async_step_with_exit_rules(self, engine, store, mock_backend):
        """Async step completion triggers exit rule evaluation."""
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

        # Complete agent step
        run = store.runs_for_step(job.id, "agent_step")[0]
        mock_backend.complete_process(run.executor_state["pid"], {"plan": "my plan"})
        engine.tick()

        # Score step should complete and job should be done
        job = store.load_job(job.id)
        assert job.status == JobStatus.COMPLETED


# ── Test: StepLimits ──────────────────────────────────────────────────


class TestStepLimits:
    """Test cost and duration limit enforcement."""

    def test_duration_limit_kills_step(self, engine, store, mock_backend):
        """Step exceeding duration limit gets cancelled."""
        wf = WorkflowDefinition(steps={
            "slow_agent": StepDefinition(
                name="slow_agent",
                outputs=["status"],
                executor=ExecutorRef("agent", {"prompt": "Slow work"}),
                limits=StepLimits(max_duration_minutes=0.001),  # ~60ms
            ),
        })
        job = engine.create_job("Test duration limit", wf)
        engine.start_job(job.id)

        # Wait a tiny bit for the limit to be exceeded
        import time
        time.sleep(0.1)

        engine.tick()

        run = store.latest_run(job.id, "slow_agent")
        assert run.status == StepRunStatus.FAILED
        assert run.error_category == "timeout"
        assert "Duration limit" in run.error

        # Backend should have been cancelled
        assert mock_backend.cancel_count >= 1

    def test_cost_limit_kills_step(self, engine, store, mock_backend):
        """Step exceeding cost limit gets cancelled."""
        wf = WorkflowDefinition(steps={
            "expensive_agent": StepDefinition(
                name="expensive_agent",
                outputs=["status"],
                executor=ExecutorRef("agent", {"prompt": "Expensive work"}),
                limits=StepLimits(max_cost_usd=0.50),
            ),
        })
        job = engine.create_job("Test cost limit", wf)
        engine.start_job(job.id)

        # Record cost exceeding limit
        run = store.runs_for_step(job.id, "expensive_agent")[0]
        store.save_step_event(run.id, "cost", {"cost_usd": 0.60})

        engine.tick()

        run = store.latest_run(job.id, "expensive_agent")
        assert run.status == StepRunStatus.FAILED
        assert run.error_category == "cost_limit"
        assert "Cost limit" in run.error

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

    def test_no_limits_means_no_enforcement(self, engine, store, mock_backend):
        """Steps without limits run indefinitely (no enforcement)."""
        wf = WorkflowDefinition(steps={
            "agent_step": StepDefinition(
                name="agent_step",
                outputs=["status"],
                executor=ExecutorRef("agent", {"prompt": "No limits"}),
                # No limits set
            ),
        })
        job = engine.create_job("No limits", wf)
        engine.start_job(job.id)

        # Multiple ticks should not fail
        for _ in range(5):
            engine.tick()

        run = store.latest_run(job.id, "agent_step")
        assert run.status == StepRunStatus.RUNNING


# ── Test: Error Categories and Failure Routing ────────────────────────


class TestErrorCategories:
    """Test error classification and exit rule routing on failures."""

    def test_error_category_on_failure(self, engine, store, mock_backend):
        """Failed step gets error_category set."""
        wf = WorkflowDefinition(steps={
            "agent_step": StepDefinition(
                name="agent_step",
                outputs=["status"],
                executor=ExecutorRef("agent", {"prompt": "Fail"}),
            ),
        })
        job = engine.create_job("Test error cat", wf)
        engine.start_job(job.id)

        run = store.runs_for_step(job.id, "agent_step")[0]
        mock_backend.fail_process(run.executor_state["pid"], "Connection refused")

        engine.tick()

        run = store.latest_run(job.id, "agent_step")
        assert run.status == StepRunStatus.FAILED
        assert run.error_category is not None

    def test_exit_rule_routes_failure_to_loop(self, engine, store, mock_backend):
        """Exit rule can catch a failure and loop to retry."""
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

        # First attempt fails with infra_failure
        run = store.runs_for_step(job.id, "agent_step")[0]
        mock_backend.fail_process(run.executor_state["pid"], "Connection refused")
        # Classify as infra_failure
        mock_backend._completions[run.executor_state["pid"]] = AgentStatus(
            state="failed", error="Connection refused"
        )

        engine.tick()

        # The exit rule should have triggered a retry (new run)
        runs = store.runs_for_step(job.id, "agent_step")
        assert len(runs) == 2  # Original + retry
        assert runs[0].status == StepRunStatus.FAILED
        assert runs[1].status == StepRunStatus.RUNNING

    def test_exit_rule_escalates_on_agent_failure(self, engine, store, mock_backend):
        """Exit rule can escalate on agent_failure."""
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

        run = store.runs_for_step(job.id, "agent_step")[0]
        mock_backend.fail_process(run.executor_state["pid"], "Agent could not complete task")

        engine.tick()

        job = store.load_job(job.id)
        assert job.status == JobStatus.PAUSED  # Escalated, not failed

    def test_unhandled_failure_halts_job(self, engine, store, mock_backend):
        """Failure with no matching exit rule halts the job."""
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

        run = store.runs_for_step(job.id, "agent_step")[0]
        mock_backend.fail_process(run.executor_state["pid"], "Total failure")

        engine.tick()

        job = store.load_job(job.id)
        assert job.status == JobStatus.FAILED

    def test_timeout_limit_sets_timeout_category(self, engine, store, mock_backend):
        """Duration limit exceeded sets error_category=timeout."""
        wf = WorkflowDefinition(steps={
            "agent_step": StepDefinition(
                name="agent_step",
                outputs=["status"],
                executor=ExecutorRef("agent", {"prompt": "Slow"}),
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
        job = engine.create_job("Test timeout routing", wf)
        engine.start_job(job.id)

        time.sleep(0.1)
        engine.tick()

        # Should be escalated (paused), not failed
        job = store.load_job(job.id)
        assert job.status == JobStatus.PAUSED

        run = store.latest_run(job.id, "agent_step")
        assert run.error_category == "timeout"


# ── Test: A→B→C→B→D Loop Pattern ─────────────────────────────────────


class TestLoopPattern:
    """Test the plan→score→refine→score→implement pattern."""

    def test_plan_score_refine_loop(self, engine, store, mock_backend):
        """A→B→C→B→D: plan, score (fail), refine, score (pass), implement.

        Data flow: plan outputs plan_content → score passes it through along
        with scores/suggestions → refine uses suggestions → outputs plan_content
        → score re-runs (supersedes) → loops until pass → implement.
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

        wf = WorkflowDefinition(steps={
            "plan": StepDefinition(
                name="plan",
                outputs=["plan_content"],
                executor=ExecutorRef("agent", {
                    "prompt": "Write a plan",
                    "output_mode": "effect",
                }),
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
                executor=ExecutorRef("agent", {
                    "prompt": "Refine: $suggestions",
                    "output_mode": "effect",
                }),
                inputs=[
                    InputBinding("plan_content", "score", "plan_content"),
                    InputBinding("suggestions", "score", "suggestions"),
                ],
                exit_rules=[
                    # After refine, always loop back to score for re-evaluation
                    ExitRule("rescore", "always",
                             {"action": "loop", "target": "score"}, priority=10),
                ],
            ),
            "implement": StepDefinition(
                name="implement",
                outputs=["status"],
                executor=ExecutorRef("agent", {
                    "prompt": "Implement the plan",
                    "output_mode": "effect",
                }),
                inputs=[InputBinding("plan_content", "score", "plan_content")],
                sequencing=["score"],
            ),
        })

        job = engine.create_job("Test loop", wf)
        engine.start_job(job.id)

        # Step 1: plan is RUNNING (async)
        plan_run = store.latest_run(job.id, "plan")
        assert plan_run.status == StepRunStatus.RUNNING

        # Complete plan
        mock_backend.complete_process(
            plan_run.executor_state["pid"],
            {"plan_content": "initial plan"}
        )
        engine.tick()

        # Step 2: score runs (sync), returns passed=False → loops to refine
        score_run = store.latest_completed_run(job.id, "score")
        assert score_run is not None
        assert score_run.result.artifact["passed"] is False

        # Step 3: refine is now RUNNING (async)
        refine_run = store.latest_run(job.id, "refine")
        assert refine_run.status == StepRunStatus.RUNNING

        # Complete refine
        mock_backend.complete_process(
            refine_run.executor_state["pid"],
            {"plan_content": "refined plan"}
        )
        engine.tick()

        # Step 4: score runs again (attempt 2), returns passed=True → advance
        score_runs = store.runs_for_step(job.id, "score")
        assert len(score_runs) == 2  # Two score attempts
        assert score_runs[1].result.artifact["passed"] is True

        # Step 5: implement is now RUNNING (async)
        impl_run = store.latest_run(job.id, "implement")
        assert impl_run.status == StepRunStatus.RUNNING

        # Complete implement
        mock_backend.complete_process(
            impl_run.executor_state["pid"],
            {"status": "done"}
        )
        engine.tick()

        # Job should be completed
        job = store.load_job(job.id)
        assert job.status == JobStatus.COMPLETED

        # Verify spawn count: plan + refine + implement = 3 agent spawns
        assert mock_backend.spawn_count == 3


# ── Test: Agent Executor ──────────────────────────────────────────────


class TestAgentExecutor:
    """Test the AgentExecutor implementation."""

    def test_agent_executor_start_returns_async(self, mock_backend):
        """AgentExecutor.start() returns type=async with executor_state."""
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

        assert result.type == "async"
        assert result.executor_state is not None
        assert "pid" in result.executor_state
        assert result.executor_state["output_mode"] == "effect"

    def test_agent_executor_check_status_running(self, mock_backend):
        """check_status returns running while process is alive."""
        executor = AgentExecutor(backend=mock_backend, prompt="test")
        ctx = ExecutionContext(
            job_id="job-1", step_name="test", attempt=1,
            workspace_path="/tmp/test", idempotency="idempotent",
        )
        result = executor.start({}, ctx)
        status = executor.check_status(result.executor_state)

        assert status.state == "running"

    def test_agent_executor_check_status_completed(self, mock_backend):
        """check_status returns completed with result after process exits."""
        executor = AgentExecutor(backend=mock_backend, prompt="test")
        ctx = ExecutionContext(
            job_id="job-1", step_name="test", attempt=1,
            workspace_path="/tmp/test", idempotency="idempotent",
        )
        result = executor.start({}, ctx)
        pid = result.executor_state["pid"]

        mock_backend.complete_process(pid, {"status": "done"})
        status = executor.check_status(result.executor_state)

        assert status.state == "completed"
        assert status.result is not None
        assert status.result.envelope is not None

    def test_agent_executor_check_status_failed(self, mock_backend):
        """check_status returns failed with error category."""
        executor = AgentExecutor(backend=mock_backend, prompt="test")
        ctx = ExecutionContext(
            job_id="job-1", step_name="test", attempt=1,
            workspace_path="/tmp/test", idempotency="idempotent",
        )
        result = executor.start({}, ctx)
        pid = result.executor_state["pid"]

        mock_backend.fail_process(pid, "Connection refused")
        status = executor.check_status(result.executor_state)

        assert status.state == "failed"
        assert status.error_category is not None

    def test_agent_executor_cancel(self, mock_backend):
        """cancel() delegates to backend."""
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
        """Output mode is stored in executor_state for check_status."""
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
        executor = AgentExecutor(backend=mock_backend, prompt="test")
        ctx = ExecutionContext(
            job_id="job-1", step_name="test", attempt=1,
            workspace_path="/tmp/test", idempotency="idempotent",
        )
        result = executor.start({}, ctx)
        pid = result.executor_state["pid"]

        mock_backend.complete_process(pid, {"analysis": "done", "confidence": 0.95})
        status = executor.check_status(result.executor_state)

        assert status.state == "completed"
        assert status.result.envelope.artifact["analysis"] == "done"
        assert status.result.envelope.artifact["confidence"] == 0.95

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
            result = executor.start({}, ctx)
            pid = result.executor_state["pid"]
            mock_backend.fail_process(pid, error_msg)
            status = executor.check_status(result.executor_state)
            assert status.error_category == expected_cat, (
                f"Expected '{expected_cat}' for '{error_msg}', got '{status.error_category}'"
            )


# ── Test: Cancel Running Step ────────────────────────────────────────


class TestCancelStep:
    """Test cancelling individual running steps."""

    def test_cancel_running_agent_step(self, engine, store, mock_backend):
        """Cancelling a running agent step marks it failed."""
        wf = WorkflowDefinition(steps={
            "agent_step": StepDefinition(
                name="agent_step",
                outputs=["status"],
                executor=ExecutorRef("agent", {"prompt": "Work"}),
            ),
        })
        job = engine.create_job("Test cancel", wf)
        engine.start_job(job.id)

        run = store.runs_for_step(job.id, "agent_step")[0]
        assert run.status == StepRunStatus.RUNNING

        # Cancel via engine's cancel_job
        engine.cancel_job(job.id)

        job = store.load_job(job.id)
        assert job.status == JobStatus.CANCELLED

        run = store.latest_run(job.id, "agent_step")
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
    """Test workflows with both sync (script/callable) and async (agent) steps."""

    def test_sync_then_async_then_sync(self, engine, store, mock_backend):
        """Script → Agent → Script pipeline."""
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

        # Prep completes immediately, agent starts
        prep_run = store.latest_run(job.id, "prep")
        assert prep_run.status == StepRunStatus.COMPLETED

        agent_run = store.latest_run(job.id, "agent")
        assert agent_run.status == StepRunStatus.RUNNING

        # Finish should NOT have started
        assert store.latest_run(job.id, "finish") is None

        # Complete agent
        mock_backend.complete_process(
            agent_run.executor_state["pid"],
            {"output": "processed data"}
        )
        engine.tick()

        # Finish should now be done
        finish_run = store.latest_run(job.id, "finish")
        assert finish_run.status == StepRunStatus.COMPLETED
        assert finish_run.result.artifact["result"] == "finished with processed data"

        job = store.load_job(job.id)
        assert job.status == JobStatus.COMPLETED
