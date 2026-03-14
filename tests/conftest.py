"""Shared fixtures for Stepwise tests."""

import asyncio

import pytest

from stepwise.engine import AsyncEngine, Engine
from stepwise.executors import (
    ExecutionContext,
    Executor,
    ExecutorRegistry,
    ExecutorResult,
    ExecutorStatus,
    HumanExecutor,
    MockLLMExecutor,
    PollExecutor,
    ScriptExecutor,
)
from stepwise.models import (
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
    Job,
    JobStatus,
    Sidecar,
    StepDefinition,
    WorkflowDefinition,
    _now,
)
from stepwise.store import SQLiteStore


# ── CallableExecutor ──────────────────────────────────────────────────
# For tests that need Python callables. Uses a global registry of
# step functions indexed by name, so config stays serializable.

_STEP_FUNCTIONS: dict[str, callable] = {}


def register_step_fn(name: str, fn: callable) -> None:
    """Register a step function for use with CallableExecutor."""
    _STEP_FUNCTIONS[name] = fn


def clear_step_fns() -> None:
    """Clear all registered step functions."""
    _STEP_FUNCTIONS.clear()


class CallableExecutor(Executor):
    """Test executor that looks up a Python callable by name from a global registry."""

    def __init__(self, fn_name: str) -> None:
        self.fn_name = fn_name

    def start(self, inputs: dict, context: ExecutionContext) -> ExecutorResult:
        fn = _STEP_FUNCTIONS.get(self.fn_name)
        if fn is None:
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={},
                    sidecar=Sidecar(),
                    workspace=context.workspace_path,
                    timestamp=_now(),
                    executor_meta={"failed": True},
                ),
                executor_state={"failed": True, "error": f"No function registered for '{self.fn_name}'"},
            )
        try:
            result = fn(inputs)
            if isinstance(result, ExecutorResult):
                return result
            if isinstance(result, dict):
                return ExecutorResult(
                    type="data",
                    envelope=HandoffEnvelope(
                        artifact=result,
                        sidecar=Sidecar(),
                        workspace=context.workspace_path,
                        timestamp=_now(),
                    ),
                )
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={"result": result},
                    sidecar=Sidecar(),
                    workspace=context.workspace_path,
                    timestamp=_now(),
                ),
            )
        except Exception as e:
            return ExecutorResult(
                type="data",
                envelope=HandoffEnvelope(
                    artifact={},
                    sidecar=Sidecar(),
                    workspace=context.workspace_path,
                    timestamp=_now(),
                    executor_meta={"failed": True},
                ),
                executor_state={"failed": True, "error": str(e)},
            )

    def check_status(self, state: dict) -> ExecutorStatus:
        return ExecutorStatus(state="completed")

    def cancel(self, state: dict) -> None:
        pass


# ── Async engine helpers ─────────────────────────────────────────────


async def run_job(engine: AsyncEngine, job_id: str, timeout: float = 10) -> Job:
    """Start a job and run the engine until it reaches terminal state."""
    engine_task = asyncio.create_task(engine.run())
    try:
        engine.start_job(job_id)
        return await asyncio.wait_for(engine.wait_for_job(job_id), timeout)
    finally:
        engine_task.cancel()
        try:
            await engine_task
        except asyncio.CancelledError:
            pass


def run_job_sync(engine: AsyncEngine, job_id: str, timeout: float = 10) -> Job:
    """Sync wrapper — run an async engine job to completion."""
    return asyncio.run(run_job(engine, job_id, timeout))


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def store():
    s = SQLiteStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def registry():
    reg = ExecutorRegistry()

    # Callable executor factory — looks up fn by name from config
    reg.register("callable", lambda config: CallableExecutor(
        fn_name=config.get("fn_name", "default"),
    ))

    # Script executor factory
    reg.register("script", lambda config: ScriptExecutor(
        command=config.get("command", "echo '{}'"),
        working_dir=config.get("working_dir"),
        flow_dir=config.get("flow_dir"),
    ))

    # Human executor factory
    reg.register("human", lambda config: HumanExecutor(
        prompt=config.get("prompt", ""),
    ))

    # Mock LLM factory
    reg.register("mock_llm", lambda config: MockLLMExecutor(
        failure_rate=config.get("failure_rate", 0.0),
        partial_rate=config.get("partial_rate", 0.0),
        latency_range=tuple(config.get("latency_range", (0.0, 0.0))),
        responses=config.get("responses"),
    ))

    # Poll executor factory
    reg.register("poll", lambda config: PollExecutor(
        check_command=config.get("check_command", "echo"),
        interval_seconds=config.get("interval_seconds", 60),
        prompt=config.get("prompt", ""),
    ))

    return reg


@pytest.fixture
def engine(store, registry):
    """Sync tick-based engine (legacy — use async_engine for new tests)."""
    return Engine(store=store, registry=registry)


@pytest.fixture
def async_engine(store, registry):
    """Event-driven async engine."""
    return AsyncEngine(store=store, registry=registry)


@pytest.fixture(autouse=True)
def cleanup_step_fns():
    """Clear step functions after each test."""
    yield
    clear_step_fns()
