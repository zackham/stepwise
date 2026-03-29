"""Per-executor-type concurrent step limits (dispatch-level gating)."""

import asyncio
import threading
import time

import pytest

from stepwise.config import StepwiseConfig
from stepwise.engine import AsyncEngine
from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorRegistry,
    ExecutorResult,
    ExecutorStatus,
)
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    JobStatus,
    Sidecar,
    StepDefinition,
    StepRunStatus,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore

from tests.conftest import run_job


# ── Helpers ──────────────────────────────────────────────────────────


class _ConcurrencyTracker:
    """Thread-safe tracker for concurrent execution count."""

    def __init__(self, sleep_seconds: float = 0.3):
        self.sleep_seconds = sleep_seconds
        self._lock = threading.Lock()
        self._current = 0
        self._max_concurrent = 0

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    def enter(self) -> None:
        with self._lock:
            self._current += 1
            if self._current > self._max_concurrent:
                self._max_concurrent = self._current

    def exit(self) -> None:
        with self._lock:
            self._current -= 1


class _SlowExecutor(Executor):
    """Executor that tracks concurrency and sleeps."""

    def __init__(self, tracker: _ConcurrencyTracker):
        self._tracker = tracker

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        self._tracker.enter()
        try:
            time.sleep(self._tracker.sleep_seconds)
        finally:
            self._tracker.exit()
        return ExecutorResult(
            type="data",
            envelope=HandoffEnvelope(
                artifact={"result": "ok"},
                sidecar=Sidecar(),
                workspace="",
                timestamp=_now(),
            ),
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        return ExecutorStatus(state="completed")

    def cancel(self, state: dict) -> None:
        pass


class _FailingExecutor(Executor):
    """Executor that always fails."""

    def __init__(self, tracker: _ConcurrencyTracker):
        self._tracker = tracker

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        self._tracker.enter()
        try:
            time.sleep(self._tracker.sleep_seconds)
        finally:
            self._tracker.exit()
        return ExecutorResult(
            type="data",
            envelope=HandoffEnvelope(
                artifact={},
                sidecar=Sidecar(),
                workspace="",
                timestamp=_now(),
                executor_meta={"failed": True},
            ),
            executor_state={"failed": True, "error": "simulated failure"},
        )

    def check_status(self, state: dict) -> ExecutorStatus:
        return ExecutorStatus(state="completed")

    def cancel(self, state: dict) -> None:
        pass


def _make_engine_with_limits(
    limits: dict[str, int],
    tracker: _ConcurrencyTracker,
    register_as: str = "callable",
) -> AsyncEngine:
    store = SQLiteStore(":memory:")
    registry = ExecutorRegistry()
    registry.register(register_as, lambda cfg, t=tracker: _SlowExecutor(t))
    config = StepwiseConfig(max_concurrent_by_executor=limits)
    engine = AsyncEngine(store=store, registry=registry, config=config)
    engine._agent_stagger_seconds = 0.0
    return engine


def _parallel_steps(exec_type: str, count: int) -> WorkflowDefinition:
    """Create N independent steps of the given executor type."""
    steps = {}
    for i in range(count):
        name = f"step-{i}"
        steps[name] = StepDefinition(
            name=name,
            executor=ExecutorRef(type=exec_type, config={}),
            outputs=["result"],
        )
    return WorkflowDefinition(steps=steps)


def _single_step(exec_type: str) -> WorkflowDefinition:
    return _parallel_steps(exec_type, 1)


# ── Core dispatch gating ────────────────────────────────────────────


class TestExecutorConcurrencyLimit:
    """Test per-executor-type dispatch gating."""

    @pytest.mark.asyncio
    async def test_single_type_limit_enforced(self):
        """With callable limit=2, at most 2 callable steps run at a time."""
        tracker = _ConcurrencyTracker(sleep_seconds=0.3)
        engine = _make_engine_with_limits({"callable": 2}, tracker)
        wf = _parallel_steps("callable", 5)
        job = engine.create_job(objective="test", workflow=wf)
        result = await run_job(engine, job.id, timeout=15)
        assert result.status == JobStatus.COMPLETED
        assert tracker.max_concurrent <= 2

    @pytest.mark.asyncio
    async def test_unlimited_type_not_gated(self):
        """Executor types without a limit run without restriction."""
        tracker = _ConcurrencyTracker(sleep_seconds=0.2)
        engine = _make_engine_with_limits({"agent": 1}, tracker, register_as="callable")
        wf = _parallel_steps("callable", 5)
        job = engine.create_job(objective="test", workflow=wf)
        start = time.monotonic()
        result = await run_job(engine, job.id, timeout=10)
        elapsed = time.monotonic() - start
        assert result.status == JobStatus.COMPLETED
        # 5 × 0.2s in parallel should be ~0.2s, not ~1.0s serial
        assert elapsed < 1.5
        assert tracker.max_concurrent >= 3

    @pytest.mark.asyncio
    async def test_throttled_step_has_no_run(self):
        """A throttled step should NOT have a StepRun (not RUNNING)."""
        tracker = _ConcurrencyTracker(sleep_seconds=1.0)
        engine = _make_engine_with_limits({"callable": 1}, tracker)
        wf = _parallel_steps("callable", 2)
        job = engine.create_job(objective="test", workflow=wf)
        engine_task = asyncio.create_task(engine.run())
        try:
            engine.start_job(job.id)
            await asyncio.sleep(0.2)
            runs = engine.store.runs_for_job(job.id)
            running = [r for r in runs if r.status == StepRunStatus.RUNNING]
            assert len(running) == 1
            assert len(runs) == 1
        finally:
            engine_task.cancel()

    @pytest.mark.asyncio
    async def test_job_not_prematurely_settled(self):
        """A job with throttled-but-ready steps must NOT settle as failed."""
        tracker = _ConcurrencyTracker(sleep_seconds=0.3)
        engine = _make_engine_with_limits({"callable": 1}, tracker)
        steps = {
            "a": StepDefinition(name="a", executor=ExecutorRef(type="callable", config={}), outputs=["result"]),
            "b": StepDefinition(name="b", executor=ExecutorRef(type="callable", config={}), outputs=["result"]),
            "c": StepDefinition(
                name="c",
                executor=ExecutorRef(type="callable", config={}),
                inputs=[InputBinding("ra", "a", "result"), InputBinding("rb", "b", "result")],
                outputs=["result"],
            ),
        }
        wf = WorkflowDefinition(steps=steps)
        job = engine.create_job(objective="test", workflow=wf)
        result = await run_job(engine, job.id, timeout=15)
        assert result.status == JobStatus.COMPLETED
        runs = engine.store.runs_for_job(job.id)
        assert len([r for r in runs if r.status == StepRunStatus.COMPLETED]) == 3


# ── Cross-job and slot release ──────────────────────────────────────


class TestCrossJobDispatch:

    @pytest.mark.asyncio
    async def test_cross_job_slot_release(self):
        """When job A's step completes, throttled step in job B should launch."""
        tracker = _ConcurrencyTracker(sleep_seconds=0.3)
        engine = _make_engine_with_limits({"callable": 1}, tracker)
        wf = _single_step("callable")
        job_a = engine.create_job(objective="a", workflow=wf)
        job_b = engine.create_job(objective="b", workflow=_single_step("callable"))
        engine_task = asyncio.create_task(engine.run())
        try:
            engine.start_job(job_a.id)
            engine.start_job(job_b.id)
            await asyncio.wait_for(engine.wait_for_job(job_b.id), timeout=10)
            assert engine.store.load_job(job_a.id).status == JobStatus.COMPLETED
            assert engine.store.load_job(job_b.id).status == JobStatus.COMPLETED
        finally:
            engine_task.cancel()

    @pytest.mark.asyncio
    async def test_slot_released_on_failure(self):
        """Failed step must release slot so next step can proceed."""
        tracker = _ConcurrencyTracker(sleep_seconds=0.1)
        store = SQLiteStore(":memory:")
        registry = ExecutorRegistry()
        call_count = 0

        class _FailFirstExecutor(Executor):
            def start(self, inputs, context):
                nonlocal call_count
                tracker.enter()
                call_count += 1
                try:
                    time.sleep(tracker.sleep_seconds)
                finally:
                    tracker.exit()
                if call_count == 1:
                    return ExecutorResult(
                        type="data",
                        envelope=HandoffEnvelope(
                            artifact={"result": ""}, sidecar=Sidecar(), workspace="", timestamp=_now(),
                            executor_meta={"failed": True},
                        ),
                        executor_state={"failed": True, "error": "fail"},
                    )
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact={"result": "ok"}, sidecar=Sidecar(), workspace="", timestamp=_now(),
                    ),
                )

            def check_status(self, state):
                return ExecutorStatus(state="completed")

            def cancel(self, state):
                pass

        registry.register("callable", lambda cfg: _FailFirstExecutor())
        config = StepwiseConfig(max_concurrent_by_executor={"callable": 1})
        engine = AsyncEngine(store=store, registry=registry, config=config)
        engine._agent_stagger_seconds = 0.0

        wf = _parallel_steps("callable", 2)
        job = engine.create_job(objective="test", workflow=wf)
        result = await run_job(engine, job.id, timeout=10)
        all_runs = engine.store.runs_for_job(job.id)
        assert len(all_runs) == 2


# ── Backward compatibility ──────────────────────────────────────────


class TestBackwardCompatibility:

    def test_legacy_max_concurrent_agents(self):
        """max_concurrent_agents=2 without new field → agent limit is 2."""
        cfg = StepwiseConfig(max_concurrent_agents=2)
        assert cfg.resolved_executor_limits() == {"agent": 2}

    def test_new_field_overrides_legacy(self):
        """max_concurrent_by_executor.agent overrides max_concurrent_agents."""
        cfg = StepwiseConfig(max_concurrent_agents=5, max_concurrent_by_executor={"agent": 1})
        assert cfg.resolved_executor_limits() == {"agent": 1}

    def test_zero_removes_limit(self):
        """max_concurrent_by_executor.agent=0 removes the agent limit."""
        cfg = StepwiseConfig(max_concurrent_agents=3, max_concurrent_by_executor={"agent": 0})
        assert cfg.resolved_executor_limits() == {}

    def test_additive_types(self):
        """Legacy agent + new llm → both present."""
        cfg = StepwiseConfig(max_concurrent_agents=5, max_concurrent_by_executor={"llm": 3})
        assert cfg.resolved_executor_limits() == {"agent": 5, "llm": 3}


# ── Config serialization ────────────────────────────────────────────


class TestConfigSerialization:

    def test_round_trip(self):
        cfg = StepwiseConfig(max_concurrent_by_executor={"agent": 1, "llm": 3})
        d = cfg.to_dict()
        assert d["max_concurrent_by_executor"] == {"agent": 1, "llm": 3}
        cfg2 = StepwiseConfig.from_dict(d)
        assert cfg2.max_concurrent_by_executor == {"agent": 1, "llm": 3}

    def test_validation_drops_bad_values(self):
        d = {"max_concurrent_by_executor": {"agent": -1, "llm": "foo", "ok": 5}}
        cfg = StepwiseConfig.from_dict(d)
        assert cfg.max_concurrent_by_executor == {"ok": 5}

    def test_merge_levels(self):
        """Local overrides project overrides user."""
        user = StepwiseConfig(max_concurrent_by_executor={"agent": 5})
        project = StepwiseConfig(max_concurrent_by_executor={"llm": 3})
        local = StepwiseConfig(max_concurrent_by_executor={"agent": 1})
        merged: dict[str, int] = {}
        merged.update(user.max_concurrent_by_executor)
        merged.update(project.max_concurrent_by_executor)
        merged.update(local.max_concurrent_by_executor)
        assert merged == {"agent": 1, "llm": 3}


# ── Dynamic reload ──────────────────────────────────────────────────


class TestDynamicReload:

    @pytest.mark.asyncio
    async def test_limit_change_takes_effect(self):
        """Updating _executor_limits changes dispatch behavior."""
        tracker = _ConcurrencyTracker(sleep_seconds=0.3)
        engine = _make_engine_with_limits({"callable": 1}, tracker)

        wf = _parallel_steps("callable", 3)
        job1 = engine.create_job(objective="run1", workflow=wf)
        result = await run_job(engine, job1.id, timeout=15)
        assert result.status == JobStatus.COMPLETED
        assert tracker.max_concurrent <= 1

        # Change limit to 3
        engine._executor_limits = {"callable": 3}
        tracker2 = _ConcurrencyTracker(sleep_seconds=0.3)
        engine.registry.register("callable", lambda cfg, t=tracker2: _SlowExecutor(t))

        wf2 = _parallel_steps("callable", 3)
        job2 = engine.create_job(objective="run2", workflow=wf2)
        result2 = await run_job(engine, job2.id, timeout=15)
        assert result2.status == JobStatus.COMPLETED
        assert tracker2.max_concurrent >= 2


# ── Throttle visibility ─────────────────────────────────────────────


class TestThrottleVisibility:

    @pytest.mark.asyncio
    async def test_resolved_flow_status_throttled(self):
        """resolved_flow_status shows 'throttled' for capacity-gated steps."""
        tracker = _ConcurrencyTracker(sleep_seconds=2.0)
        engine = _make_engine_with_limits({"callable": 1}, tracker)
        wf = _parallel_steps("callable", 2)
        job = engine.create_job(objective="test", workflow=wf)
        engine_task = asyncio.create_task(engine.run())
        try:
            engine.start_job(job.id)
            await asyncio.sleep(0.3)
            status = engine.resolved_flow_status(job.id)
            statuses = {s["name"]: s["status"] for s in status["steps"]}
            assert "running" in statuses.values()
            assert "throttled" in statuses.values()
            throttled = [s for s in status["steps"] if s["status"] == "throttled"]
            assert len(throttled) == 1
            ti = throttled[0]["throttle_info"]
            assert ti["executor_type"] == "callable"
            assert ti["limit"] == 1
            assert ti["running"] == 1
        finally:
            engine_task.cancel()

    @pytest.mark.asyncio
    async def test_pending_not_confused_with_throttled(self):
        """Steps with unmet deps show 'pending', not 'throttled'."""
        tracker = _ConcurrencyTracker(sleep_seconds=2.0)
        engine = _make_engine_with_limits({"callable": 1}, tracker)
        steps = {
            "a": StepDefinition(name="a", executor=ExecutorRef(type="callable", config={}), outputs=["result"]),
            "b": StepDefinition(
                name="b", executor=ExecutorRef(type="callable", config={}),
                inputs=[InputBinding("result", "a", "result")], outputs=["result"]),
        }
        wf = WorkflowDefinition(steps=steps)
        job = engine.create_job(objective="test", workflow=wf)
        engine_task = asyncio.create_task(engine.run())
        try:
            engine.start_job(job.id)
            await asyncio.sleep(0.3)
            status = engine.resolved_flow_status(job.id)
            b_status = [s for s in status["steps"] if s["name"] == "b"][0]
            assert b_status["status"] == "pending"
        finally:
            engine_task.cancel()


# ── Runner_bg config passthrough ────────────────────────────────────


class TestRunnerBgConfig:

    def test_runner_bg_passes_config(self):
        """Smoke test: runner_bg._run constructs engine with config."""
        import ast
        import inspect
        from stepwise import runner_bg
        source = inspect.getsource(runner_bg._run)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "AsyncEngine":
                    kwarg_names = [kw.arg for kw in node.keywords]
                    assert "config" in kwarg_names, "runner_bg must pass config= to AsyncEngine"
                    return
        pytest.fail("AsyncEngine() call not found in runner_bg._run")
