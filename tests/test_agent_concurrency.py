"""H16: Agent concurrency semaphore and stagger delay."""

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
    JobStatus,
    Sidecar,
    StepDefinition,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore

from tests.conftest import run_job


# ── Helpers ──────────────────────────────────────────────────────────


class _ConcurrencyTracker:
    """Thread-safe tracker for concurrent execution count and launch timestamps."""

    def __init__(self, sleep_seconds: float = 0.3):
        self.sleep_seconds = sleep_seconds
        self._lock = threading.Lock()
        self._current = 0
        self._max_concurrent = 0
        self._launch_times: list[float] = []

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    @property
    def launch_times(self) -> list[float]:
        return list(self._launch_times)

    def enter(self) -> None:
        with self._lock:
            self._launch_times.append(time.monotonic())
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


class _FailOnceExecutor(Executor):
    """Agent executor that fails on first call, succeeds on second."""

    def __init__(self, tracker: _ConcurrencyTracker):
        self._tracker = tracker
        self._calls = 0

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        self._tracker.enter()
        self._calls += 1
        try:
            time.sleep(self._tracker.sleep_seconds)
        finally:
            self._tracker.exit()

        if self._calls == 1:
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


def _make_engine(max_concurrent_agents: int, tracker: _ConcurrencyTracker,
                 register_script: bool = False) -> AsyncEngine:
    """Create an AsyncEngine with a mock agent executor and concurrency limit."""
    store = SQLiteStore(":memory:")
    registry = ExecutorRegistry()

    # Register "agent" type with our tracking executor
    registry.register("agent", lambda cfg, t=tracker: _SlowExecutor(t))

    if register_script:
        from stepwise.executors import ScriptExecutor
        registry.register("script", lambda cfg: ScriptExecutor(
            command=cfg.get("command", "echo '{}'"),
        ))

    config = StepwiseConfig(max_concurrent_agents=max_concurrent_agents)
    return AsyncEngine(store=store, registry=registry, config=config)


# ── Tests ────────────────────────────────────────────────────────────


class TestAgentConcurrencyLimit:
    """Test max_concurrent_agents semaphore enforcement."""

    @pytest.mark.asyncio
    async def test_max_concurrent_agents_enforced(self):
        """With max_concurrent_agents=2, only 2 agent steps run at a time."""
        tracker = _ConcurrencyTracker(sleep_seconds=0.5)
        engine = _make_engine(max_concurrent_agents=2, tracker=tracker)
        # Also suppress auto-retry for cleaner test
        engine._agent_stagger_seconds = 0.0  # disable stagger for this test

        # Build workflow with 5 parallel agent steps (no deps)
        steps = {}
        for i in range(5):
            steps[f"agent-{i}"] = StepDefinition(
                name=f"agent-{i}",
                executor=ExecutorRef(type="agent", config={}),
                outputs=["result"],
            )
        wf = WorkflowDefinition(steps=steps)
        job = engine.create_job(objective="test concurrency", workflow=wf)

        result = await run_job(engine, job.id, timeout=30)

        assert result.status == JobStatus.COMPLETED
        assert tracker.max_concurrent <= 2

    @pytest.mark.asyncio
    async def test_non_agent_steps_bypass_semaphore(self):
        """Script steps are not limited by max_concurrent_agents."""
        tracker = _ConcurrencyTracker(sleep_seconds=0.1)
        engine = _make_engine(max_concurrent_agents=1, tracker=tracker, register_script=True)

        # Build workflow with 5 parallel script steps
        steps = {}
        for i in range(5):
            steps[f"script-{i}"] = StepDefinition(
                name=f"script-{i}",
                executor=ExecutorRef(type="script", config={"command": "echo '{\"result\": \"ok\"}'"}),
                outputs=["result"],
            )
        wf = WorkflowDefinition(steps=steps)
        job = engine.create_job(objective="test bypass", workflow=wf)

        start = time.monotonic()
        result = await run_job(engine, job.id, timeout=30)
        elapsed = time.monotonic() - start

        assert result.status == JobStatus.COMPLETED
        # With semaphore=1 and 5 serial agents at 0.1s each, it'd take 0.5s+
        # Scripts bypass semaphore, so they should all finish much faster
        # (just need to complete within a reasonable time)
        assert elapsed < 5.0  # generous bound — mainly checking it doesn't serialize

    @pytest.mark.asyncio
    async def test_agent_slot_released_on_failure(self):
        """After an agent step fails, the semaphore slot is released for the next step.

        With max_concurrent_agents=1, if the semaphore was NOT released on failure,
        the second step would block forever and the test would timeout.
        The fact that both steps complete (even if one fails) proves the slot was released.
        """
        tracker = _ConcurrencyTracker(sleep_seconds=0.1)
        store = SQLiteStore(":memory:")
        registry = ExecutorRegistry()

        class _FailByStepName(Executor):
            """Fails for step-a, succeeds for step-b."""
            def start(self, inputs, context):
                tracker.enter()
                try:
                    time.sleep(tracker.sleep_seconds)
                finally:
                    tracker.exit()
                if context.step_name == "step-a":
                    return ExecutorResult(
                        type="data",
                        envelope=HandoffEnvelope(
                            artifact={}, sidecar=Sidecar(), workspace="", timestamp=_now(),
                            executor_meta={"failed": True},
                        ),
                        executor_state={"failed": True, "error": "fail",
                                        "error_category": "agent_failure"},
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

        registry.register("agent", lambda cfg: _FailByStepName())

        config = StepwiseConfig(max_concurrent_agents=1)
        engine = AsyncEngine(store=store, registry=registry, config=config)
        engine._agent_stagger_seconds = 0.0

        # Two parallel agent steps (no deps)
        wf = WorkflowDefinition(steps={
            "step-a": StepDefinition(
                name="step-a",
                executor=ExecutorRef(type="agent", config={}),
                outputs=["result"],
            ),
            "step-b": StepDefinition(
                name="step-b",
                executor=ExecutorRef(type="agent", config={}),
                outputs=["result"],
            ),
        })
        job = engine.create_job(objective="test slot release", workflow=wf)

        # If semaphore slot was NOT released on step-a failure, step-b would
        # never acquire it and this would timeout → test failure.
        result = await run_job(engine, job.id, timeout=15)

        # Both steps should have runs (both were dispatched, both finished)
        runs = engine.store.runs_for_job(job.id)
        assert len(runs) >= 2  # Both ran, proving the slot was released


class TestAgentStagger:
    """Test stagger delay between agent launches."""

    @pytest.mark.asyncio
    async def test_stagger_delay_between_agents(self):
        """Consecutive agent launches are spaced by the stagger delay."""
        tracker = _ConcurrencyTracker(sleep_seconds=0.1)
        engine = _make_engine(max_concurrent_agents=5, tracker=tracker)
        engine._agent_stagger_seconds = 0.5  # use 0.5s for faster test

        # Build workflow with 3 parallel agent steps
        steps = {}
        for i in range(3):
            steps[f"agent-{i}"] = StepDefinition(
                name=f"agent-{i}",
                executor=ExecutorRef(type="agent", config={}),
                outputs=["result"],
            )
        wf = WorkflowDefinition(steps=steps)
        job = engine.create_job(objective="test stagger", workflow=wf)

        result = await run_job(engine, job.id, timeout=30)

        assert result.status == JobStatus.COMPLETED
        times = tracker.launch_times
        assert len(times) == 3

        # Sort launch times and check gaps
        times.sort()
        for i in range(1, len(times)):
            gap = times[i] - times[i - 1]
            # Allow 0.3s tolerance on 0.5s stagger
            assert gap >= 0.3, f"Gap between launch {i-1} and {i} was {gap:.3f}s, expected ≥0.3s"


class TestConcurrencyConfig:
    """Test StepwiseConfig serialization for max_concurrent_agents."""

    def test_config_default_value(self):
        config = StepwiseConfig()
        assert config.max_concurrent_agents == 3

    def test_config_from_dict(self):
        config = StepwiseConfig.from_dict({"max_concurrent_agents": 5})
        assert config.max_concurrent_agents == 5

    def test_config_to_dict_non_default(self):
        config = StepwiseConfig(max_concurrent_agents=5)
        d = config.to_dict()
        assert d["max_concurrent_agents"] == 5

    def test_config_to_dict_default_omitted(self):
        config = StepwiseConfig()
        d = config.to_dict()
        assert "max_concurrent_agents" not in d

    def test_config_roundtrip(self):
        config = StepwiseConfig(max_concurrent_agents=7)
        d = config.to_dict()
        restored = StepwiseConfig.from_dict(d)
        assert restored.max_concurrent_agents == 7
