"""Shared fixtures for Stepwise tests."""

import pytest

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
    ExecutorRef,
    HandoffEnvelope,
    InputBinding,
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
    ))

    # Human executor factory
    reg.register("human", lambda config: HumanExecutor(
        prompt=config.get("prompt", ""),
        notify=config.get("notify"),
    ))

    # Mock LLM factory
    reg.register("mock_llm", lambda config: MockLLMExecutor(
        failure_rate=config.get("failure_rate", 0.0),
        partial_rate=config.get("partial_rate", 0.0),
        latency_range=tuple(config.get("latency_range", (0.0, 0.0))),
        responses=config.get("responses"),
    ))

    return reg


@pytest.fixture
def engine(store, registry):
    return Engine(store=store, registry=registry)


@pytest.fixture(autouse=True)
def cleanup_step_fns():
    """Clear step functions after each test."""
    yield
    clear_step_fns()
