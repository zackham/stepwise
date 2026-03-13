"""Integration tests: sub-jobs, watches, crash recovery.

Covers required test cases 10-14, 18.
"""

import json
import os
import tempfile

import pytest

from tests.conftest import CallableExecutor, register_step_fn
from stepwise.engine import Engine
from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorRegistry,
    ExecutorResult,
    ExecutorStatus,
    HumanExecutor,
    MockLLMExecutor,
    ScriptExecutor,
)
from stepwise.models import (
    ExitRule,
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
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


def make_registry():
    reg = ExecutorRegistry()
    reg.register("callable", lambda config: CallableExecutor(fn_name=config.get("fn_name", "default")))
    reg.register("script", lambda config: ScriptExecutor(command=config.get("command", "echo '{}'")))
    reg.register("human", lambda config: HumanExecutor(prompt=config.get("prompt", ""), notify=config.get("notify")))
    reg.register("mock_llm", lambda config: MockLLMExecutor(
        failure_rate=config.get("failure_rate", 0.0),
        partial_rate=config.get("partial_rate", 0.0),
        responses=config.get("responses"),
    ))
    return reg


# ── Test 10: Sub-flow delegation ─────────────────────────────────────


class TestSubFlowDelegation:
    def test_sub_flow_step(self):
        register_step_fn("sub_process", lambda i: {"result": f"sub_processed_{i.get('data', '')}"})

        store = SQLiteStore(":memory:")
        reg = make_registry()
        engine = Engine(store=store, registry=reg)

        sub_flow = WorkflowDefinition(steps={
            "sub_a": StepDefinition(
                name="sub_a", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "sub_process"}),
                inputs=[InputBinding("data", "$job", "data")],
            ),
        })

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("sub_flow", {"flow_ref": "test"}),
                inputs=[InputBinding("data", "$job", "data")],
                sub_flow=sub_flow,
            ),
        })

        job = engine.create_job("Sub-flow test", w, inputs={"data": "hello"})
        engine.start_job(job.id)

        for _ in range(10):
            job = engine.get_job(job.id)
            if job.status != JobStatus.RUNNING:
                break
            engine.tick()

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        runs = engine.get_runs(job.id, "a")
        assert runs[-1].status == StepRunStatus.COMPLETED
        assert "sub_processed" in runs[-1].result.artifact.get("result", "")


# ── Test 12: Nested sub-flows ────────────────────────────────────────


class TestNestedSubFlows:
    def test_depth_2_sub_flows(self):
        register_step_fn("deep_fn", lambda i: {"result": f"deep_{i.get('data', '')}"})

        store = SQLiteStore(":memory:")
        reg = make_registry()
        engine = Engine(store=store, registry=reg)

        inner_flow = WorkflowDefinition(steps={
            "inner": StepDefinition(
                name="inner", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "deep_fn"}),
                inputs=[InputBinding("data", "$job", "data")],
            ),
        })

        mid_flow = WorkflowDefinition(steps={
            "mid": StepDefinition(
                name="mid", outputs=["result"],
                executor=ExecutorRef("sub_flow", {"flow_ref": "inner"}),
                inputs=[InputBinding("data", "$job", "data")],
                sub_flow=inner_flow,
            ),
        })

        w = WorkflowDefinition(steps={
            "start": StepDefinition(
                name="start", outputs=["result"],
                executor=ExecutorRef("sub_flow", {"flow_ref": "mid"}),
                inputs=[InputBinding("data", "$job", "data")],
                sub_flow=mid_flow,
            ),
        })

        job = engine.create_job("Nested sub-flows", w, inputs={"data": "test"})
        engine.start_job(job.id)

        for _ in range(20):
            job = engine.get_job(job.id)
            if job.status != JobStatus.RUNNING:
                break
            engine.tick()

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED


# ── Test 13: Poll watch ──────────────────────────────────────────────


class TestPollWatch:
    def test_poll_watch_fulfill(self):
        store = SQLiteStore(":memory:")
        reg = make_registry()
        tmpdir = tempfile.mkdtemp()

        # Use a state file: first check returns empty (not ready),
        # second check returns JSON (fulfilled). This ensures the step
        # stays suspended through start_job and resolves on a later tick.
        state_file = os.path.join(tmpdir, "poll_state")
        check_script = (
            f'if [ -f "{state_file}" ]; then '
            f'echo \'{{"status": "done", "url": "http://test"}}\'; '
            f'else touch "{state_file}"; fi'
        )

        class PollStarter(Executor):
            def start(self, inputs, context):
                return ExecutorResult(
                    type="watch",
                    watch=WatchSpec(
                        mode="poll",
                        config={
                            "check_command": check_script,
                            "interval_seconds": 0,
                        },
                        fulfillment_outputs=["status", "url"],
                    ),
                )

            def check_status(self, state):
                return ExecutorStatus(state="running")

            def cancel(self, state):
                pass

        reg.register("poll_starter", lambda config: PollStarter())

        engine = Engine(store=store, registry=reg)

        w = WorkflowDefinition(steps={
            "deploy": StepDefinition(
                name="deploy", outputs=["status", "url"],
                executor=ExecutorRef("poll_starter", {}),
            ),
        })

        job = engine.create_job("Poll test", w, workspace_path=tmpdir)
        engine.start_job(job.id)

        runs = engine.get_runs(job.id, "deploy")
        assert runs[0].status == StepRunStatus.SUSPENDED

        # Tick checks the poll — second call finds state file and returns JSON
        engine.tick()

        for _ in range(5):
            job = engine.get_job(job.id)
            if job.status != JobStatus.RUNNING:
                break
            engine.tick()

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        runs = engine.get_runs(job.id, "deploy")
        assert runs[0].status == StepRunStatus.COMPLETED
        assert runs[0].result.artifact["status"] == "done"

    def test_poll_watch_not_ready(self):
        store = SQLiteStore(":memory:")
        reg = make_registry()
        tmpdir = tempfile.mkdtemp()

        class PollNotReady(Executor):
            def start(self, inputs, context):
                return ExecutorResult(
                    type="watch",
                    watch=WatchSpec(
                        mode="poll",
                        config={
                            "check_command": "true",
                            "interval_seconds": 0,
                        },
                        fulfillment_outputs=["status"],
                    ),
                )

            def check_status(self, state):
                return ExecutorStatus(state="running")

            def cancel(self, state):
                pass

        reg.register("poll_not_ready", lambda config: PollNotReady())

        engine = Engine(store=store, registry=reg)

        w = WorkflowDefinition(steps={
            "check": StepDefinition(
                name="check", outputs=["status"],
                executor=ExecutorRef("poll_not_ready", {}),
            ),
        })

        job = engine.create_job("Poll not ready", w, workspace_path=tmpdir)
        engine.start_job(job.id)

        engine.tick()

        runs = engine.get_runs(job.id, "check")
        assert runs[0].status == StepRunStatus.SUSPENDED

    def test_poll_watch_error_retries(self):
        store = SQLiteStore(":memory:")
        reg = make_registry()
        tmpdir = tempfile.mkdtemp()

        class PollError(Executor):
            def start(self, inputs, context):
                return ExecutorResult(
                    type="watch",
                    watch=WatchSpec(
                        mode="poll",
                        config={
                            "check_command": "exit 1",
                            "interval_seconds": 0,
                        },
                        fulfillment_outputs=["status"],
                    ),
                )

            def check_status(self, state):
                return ExecutorStatus(state="running")

            def cancel(self, state):
                pass

        reg.register("poll_error", lambda config: PollError())

        engine = Engine(store=store, registry=reg)

        w = WorkflowDefinition(steps={
            "check": StepDefinition(
                name="check", outputs=["status"],
                executor=ExecutorRef("poll_error", {}),
            ),
        })

        job = engine.create_job("Poll error", w, workspace_path=tmpdir)
        engine.start_job(job.id)

        engine.tick()

        runs = engine.get_runs(job.id, "check")
        assert runs[0].status == StepRunStatus.SUSPENDED
        watch_state = runs[0].executor_state.get("_watch", {})
        assert watch_state.get("last_error") is not None


# ── Test 14: Human watch fulfilled via API ───────────────────────────


class TestHumanWatch:
    def test_human_watch_fulfilled(self):
        store = SQLiteStore(":memory:")
        reg = make_registry()
        engine = Engine(store=store, registry=reg)

        w = WorkflowDefinition(steps={
            "review": StepDefinition(
                name="review", outputs=["decision", "comments"],
                executor=ExecutorRef("human", {
                    "prompt": "Review this code",
                    "notify": "slack:#reviews",
                }),
            ),
        })

        job = engine.create_job("Human watch test", w)
        engine.start_job(job.id)

        runs = engine.get_runs(job.id, "review")
        assert runs[0].status == StepRunStatus.SUSPENDED
        assert runs[0].watch.mode == "human"

        engine.fulfill_watch(runs[0].id, {
            "decision": True,
            "comments": "looks good",
        })

        engine.tick()

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        runs = engine.get_runs(job.id, "review")
        assert runs[0].status == StepRunStatus.COMPLETED
        assert runs[0].result.artifact["decision"] is True

    def test_fulfill_validates_payload(self):
        store = SQLiteStore(":memory:")
        reg = make_registry()
        engine = Engine(store=store, registry=reg)

        w = WorkflowDefinition(steps={
            "review": StepDefinition(
                name="review", outputs=["decision", "comments"],
                executor=ExecutorRef("human", {"prompt": "Review"}),
            ),
        })

        job = engine.create_job("Validation test", w)
        engine.start_job(job.id)

        runs = engine.get_runs(job.id, "review")
        run_id = runs[0].id

        with pytest.raises(ValueError, match="missing required field"):
            engine.fulfill_watch(run_id, {"decision": True})


# ── Test 18: Crash recovery ──────────────────────────────────────────


class TestCrashRecovery:
    def test_persist_restart_continue(self):
        register_step_fn("cr_a", lambda i: {"value": "from_a"})

        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "test.db")

        store1 = SQLiteStore(db_path)
        reg = make_registry()
        engine1 = Engine(store=store1, registry=reg)

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("callable", {"fn_name": "cr_a"}),
            ),
            "b": StepDefinition(
                name="b", outputs=["result"],
                executor=ExecutorRef("human", {"prompt": "approve"}),
                inputs=[InputBinding("data", "a", "value")],
            ),
        })

        job = engine1.create_job("Crash test", w)
        engine1.start_job(job.id)
        job_id = job.id

        runs = engine1.get_runs(job_id)
        a_runs = [r for r in runs if r.step_name == "a"]
        b_runs = [r for r in runs if r.step_name == "b"]
        assert a_runs[0].status == StepRunStatus.COMPLETED
        assert b_runs[0].status == StepRunStatus.SUSPENDED

        b_run_id = b_runs[0].id
        store1.close()

        # "Crash" — new engine, same DB
        store2 = SQLiteStore(db_path)
        engine2 = Engine(store=store2, registry=reg)

        job2 = engine2.get_job(job_id)
        assert job2.status == JobStatus.RUNNING

        engine2.fulfill_watch(b_run_id, {"result": "approved"})
        engine2.tick()

        job2 = engine2.get_job(job_id)
        assert job2.status == JobStatus.COMPLETED

        store2.close()

    def test_running_steps_on_crash(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "crash2.db")

        store1 = SQLiteStore(db_path)
        reg = make_registry()
        engine1 = Engine(store=store1, registry=reg)

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("callable", {"fn_name": "default"}),
            ),
        })

        job = engine1.create_job("Crash running test", w)
        job_id = job.id

        # Simulate crash with a RUNNING step
        run = StepRun(
            id=_gen_id("run"),
            job_id=job_id,
            step_name="a",
            attempt=1,
            status=StepRunStatus.RUNNING,
            started_at=_now(),
        )
        store1.save_run(run)

        job.status = JobStatus.RUNNING
        store1.save_job(job)
        store1.close()

        # New engine
        store2 = SQLiteStore(db_path)
        engine2 = Engine(store=store2, registry=reg)

        runs = engine2.get_runs(job_id, "a")
        assert runs[0].status == StepRunStatus.RUNNING

        engine2.tick()
        store2.close()


# ── Test: Sub-job output flows back to parent ────────────────────────


class TestSubFlowOutputFlowback:
    def test_sub_flow_terminal_result_becomes_parent_result(self):
        """The child job's terminal step output becomes the parent step's result."""
        register_step_fn("sub_work", lambda i: {
            "analysis": "deep analysis result",
            "confidence": 0.95,
        })

        store = SQLiteStore(":memory:")
        reg = make_registry()
        engine = Engine(store=store, registry=reg)

        sub_flow = WorkflowDefinition(steps={
            "deep_analyze": StepDefinition(
                name="deep_analyze", outputs=["analysis", "confidence"],
                executor=ExecutorRef("callable", {"fn_name": "sub_work"}),
            ),
        })

        w = WorkflowDefinition(steps={
            "analyze": StepDefinition(
                name="analyze", outputs=["analysis", "confidence"],
                executor=ExecutorRef("sub_flow", {"flow_ref": "analysis"}),
                sub_flow=sub_flow,
            ),
            "report": StepDefinition(
                name="report", outputs=["summary"],
                executor=ExecutorRef("callable", {"fn_name": "reporter"}),
                inputs=[
                    InputBinding("data", "analyze", "analysis"),
                    InputBinding("score", "analyze", "confidence"),
                ],
            ),
        })

        register_step_fn("reporter", lambda i: {
            "summary": f"Report: {i['data']} (score: {i['score']})"
        })

        job = engine.create_job("Flowback test", w)
        engine.start_job(job.id)

        for _ in range(20):
            job = engine.get_job(job.id)
            if job.status != JobStatus.RUNNING:
                break
            engine.tick()

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        # Check parent step got the sub-flow output
        analyze_runs = engine.get_runs(job.id, "analyze")
        assert analyze_runs[-1].result.artifact["analysis"] == "deep analysis result"
        assert analyze_runs[-1].result.artifact["confidence"] == 0.95

        # Check downstream step got the data
        report_runs = engine.get_runs(job.id, "report")
        assert "deep analysis result" in report_runs[-1].result.artifact["summary"]


# ── Test: Cancel propagates to children ──────────────────────────────


class TestCancelPropagation:
    def test_cancel_job_cancels_sub_flow(self):
        """cancel_job propagates to child jobs."""
        store = SQLiteStore(":memory:")
        reg = make_registry()
        engine = Engine(store=store, registry=reg)

        sub_flow = WorkflowDefinition(steps={
            "slow": StepDefinition(
                name="slow", outputs=["result"],
                executor=ExecutorRef("human", {"prompt": "do something slow"}),
            ),
        })

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("sub_flow", {"flow_ref": "slow"}),
                sub_flow=sub_flow,
            ),
        })

        job = engine.create_job("Cancel propagation test", w)
        engine.start_job(job.id)

        # Step a should be delegated with a sub-job
        runs = engine.get_runs(job.id, "a")
        a_run = runs[-1]
        assert a_run.status == StepRunStatus.DELEGATED
        sub_job_id = a_run.sub_job_id
        assert sub_job_id is not None

        # Sub-job should be running
        sub_job = engine.get_job(sub_job_id)
        assert sub_job.status == JobStatus.RUNNING

        # Cancel parent
        engine.cancel_job(job.id)

        # Parent should be cancelled
        job = engine.get_job(job.id)
        assert job.status == JobStatus.CANCELLED

        # Sub-job should also be cancelled
        sub_job = engine.get_job(sub_job_id)
        assert sub_job.status == JobStatus.CANCELLED


# ── Test: Rerun rejected for DELEGATED status ───────────────────────


class TestRerunSafetyDelegated:
    def test_reject_rerun_when_delegated(self):
        """Cannot rerun a step whose latest run is DELEGATED."""
        store = SQLiteStore(":memory:")
        reg = make_registry()
        engine = Engine(store=store, registry=reg)

        sub_flow = WorkflowDefinition(steps={
            "inner": StepDefinition(
                name="inner", outputs=["result"],
                executor=ExecutorRef("human", {"prompt": "waiting"}),
            ),
        })

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("sub_flow", {"flow_ref": "test"}),
                sub_flow=sub_flow,
            ),
        })

        job = engine.create_job("Rerun delegated", w)
        engine.start_job(job.id)

        runs = engine.get_runs(job.id, "a")
        assert runs[-1].status == StepRunStatus.DELEGATED

        with pytest.raises(ValueError, match="Cannot rerun"):
            engine.rerun_step(job.id, "a")


# ── Test: Decorator executor_meta passthrough ────────────────────────


class TestDecoratorMetaPassthrough:
    def test_timeout_meta_in_completed_step(self):
        """Timeout decorator adds metadata to HandoffEnvelope."""
        register_step_fn("meta_fn", lambda i: {"result": "done"})

        store = SQLiteStore(":memory:")
        reg = make_registry()

        # Register callable with timeout decorator
        reg.register("callable", lambda config: CallableExecutor(fn_name=config.get("fn_name", "default")))

        from stepwise.decorators import TimeoutDecorator

        def callable_with_timeout(config):
            inner = CallableExecutor(fn_name=config.get("fn_name", "default"))
            return TimeoutDecorator(inner, {"minutes": 60})

        reg.register("callable_timeout", callable_with_timeout)

        engine = Engine(store=store, registry=reg)

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("callable_timeout", {"fn_name": "meta_fn"}),
            ),
        })

        job = engine.create_job("Meta test", w)
        engine.start_job(job.id)

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        runs = engine.get_runs(job.id, "a")
        assert runs[0].result.executor_meta.get("timeout") is not None
        assert runs[0].result.executor_meta["timeout"]["triggered"] is False


# ── Test: Inject context ─────────────────────────────────────────────


class TestInjectContext:
    def test_inject_context_creates_event(self):
        store = SQLiteStore(":memory:")
        reg = make_registry()
        engine = Engine(store=store, registry=reg)

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("human", {"prompt": "do something"}),
            ),
        })

        job = engine.create_job("Context test", w)
        engine.start_job(job.id)

        engine.inject_context(job.id, "Focus on the API first")

        events = engine.get_events(job.id)
        context_events = [e for e in events if e.type == "context.injected"]
        assert len(context_events) == 1
        assert context_events[0].data["context"] == "Focus on the API first"

    def test_inject_context_does_not_modify_job_inputs(self):
        """inject_context does NOT modify Job.inputs — they are immutable."""
        store = SQLiteStore(":memory:")
        reg = make_registry()
        engine = Engine(store=store, registry=reg)

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("human", {"prompt": "do something"}),
            ),
        })

        job = engine.create_job("Immutable test", w, inputs={"key": "original"})
        engine.start_job(job.id)

        engine.inject_context(job.id, "new context info")

        job = engine.get_job(job.id)
        assert job.inputs == {"key": "original"}

    def test_inject_context_available_to_future_steps(self):
        """Injected context is passed in ExecutionContext.injected_context."""
        captured_contexts = []

        def capture_fn(inputs):
            return {"result": "ok"}

        register_step_fn("capture_fn", capture_fn)

        store = SQLiteStore(":memory:")
        reg = make_registry()

        class ContextCapturingExecutor(Executor):
            def start(self, inputs, context):
                captured_contexts.append(context.injected_context)
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={"result": "captured"},
                        sidecar=Sidecar(),
                        workspace=context.workspace_path,
                        timestamp=_now(),
                    ),
                )

            def check_status(self, state):
                return ExecutorStatus(state="completed")

            def cancel(self, state):
                pass

        reg.register("capturing", lambda config: ContextCapturingExecutor())

        engine = Engine(store=store, registry=reg)

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("human", {"prompt": "step 1"}),
            ),
            "b": StepDefinition(
                name="b", outputs=["result"],
                executor=ExecutorRef("capturing", {}),
                inputs=[InputBinding("data", "a", "value")],
            ),
        })

        job = engine.create_job("Context passthrough", w)
        engine.start_job(job.id)

        # Inject context while waiting for human step
        engine.inject_context(job.id, "Focus on API first")
        engine.inject_context(job.id, "Also consider caching")

        # Fulfill the human step
        runs = engine.get_runs(job.id, "a")
        engine.fulfill_watch(runs[0].id, {"value": "human input"})
        engine.tick()

        # The capturing executor should have received the injected contexts
        assert len(captured_contexts) == 1
        assert captured_contexts[0] == ["Focus on API first", "Also consider caching"]


# ── Test: Effector event emission ────────────────────────────────────


class TestEffectorEvents:
    def test_executor_meta_effector_events_emitted(self):
        """Executors can mark events as effectors via executor_meta."""

        def effector_fn(inputs):
            from stepwise.executors import ExecutorResult
            from stepwise.models import HandoffEnvelope, Sidecar, _now
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={"result": "deployed"},
                    sidecar=Sidecar(),
                    workspace="",
                    timestamp=_now(),
                    executor_meta={
                        "effector_events": [
                            {"type": "deploy.executed", "data": {"target": "production"}},
                            {"type": "notification.sent", "data": {"channel": "slack"}},
                        ],
                    },
                ),
            )

        register_step_fn("effector_fn", effector_fn)

        store = SQLiteStore(":memory:")
        reg = make_registry()
        engine = Engine(store=store, registry=reg)

        w = WorkflowDefinition(steps={
            "deploy": StepDefinition(
                name="deploy", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "effector_fn"}),
            ),
        })

        job = engine.create_job("Effector test", w)
        engine.start_job(job.id)

        events = engine.get_events(job.id)
        effector_events = [e for e in events if e.is_effector]
        assert len(effector_events) == 2
        types = {e.type for e in effector_events}
        assert "deploy.executed" in types
        assert "notification.sent" in types
        # Check data is passed through
        deploy_evt = [e for e in effector_events if e.type == "deploy.executed"][0]
        assert deploy_evt.data["target"] == "production"

    def test_no_effector_events_when_meta_empty(self):
        """No effector events emitted when executor_meta doesn't contain them."""
        register_step_fn("normal_fn", lambda i: {"result": "ok"})

        store = SQLiteStore(":memory:")
        reg = make_registry()
        engine = Engine(store=store, registry=reg)

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["result"],
                executor=ExecutorRef("callable", {"fn_name": "normal_fn"}),
            ),
        })

        job = engine.create_job("No effector test", w)
        engine.start_job(job.id)

        events = engine.get_events(job.id)
        effector_events = [e for e in events if e.is_effector]
        assert len(effector_events) == 0


# ── Test: Pause and Resume ───────────────────────────────────────────


class TestPauseResume:
    def test_pause_and_resume(self):
        store = SQLiteStore(":memory:")
        reg = make_registry()
        engine = Engine(store=store, registry=reg)

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("human", {"prompt": "do something"}),
            ),
        })

        job = engine.create_job("Pause test", w)
        engine.start_job(job.id)

        engine.pause_job(job.id)
        job = engine.get_job(job.id)
        assert job.status == JobStatus.PAUSED

        engine.resume_job(job.id)
        job = engine.get_job(job.id)
        assert job.status == JobStatus.RUNNING


# ── Test: Cancel job ─────────────────────────────────────────────────


class TestCancelJob:
    def test_cancel_running_job(self):
        store = SQLiteStore(":memory:")
        reg = make_registry()
        engine = Engine(store=store, registry=reg)

        w = WorkflowDefinition(steps={
            "a": StepDefinition(
                name="a", outputs=["value"],
                executor=ExecutorRef("human", {"prompt": "do something"}),
            ),
        })

        job = engine.create_job("Cancel test", w)
        engine.start_job(job.id)

        engine.cancel_job(job.id)
        job = engine.get_job(job.id)
        assert job.status == JobStatus.CANCELLED

        runs = engine.get_runs(job.id, "a")
        assert runs[0].status == StepRunStatus.FAILED
