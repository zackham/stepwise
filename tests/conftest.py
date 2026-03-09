"""Shared fixtures for Stepwise tests."""

import pytest

from stepwise.events import EventBus
from stepwise.executors import (
    ExecutorRegistry,
    HumanExecutor,
    MockLLMExecutor,
    ScriptExecutor,
    SubJobExecutor,
)
from stepwise.models import (
    InputBinding,
    StepDefinition,
    WorkflowDefinition,
)
from stepwise.store import StepwiseStore


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def store():
    s = StepwiseStore(":memory:")
    yield s
    s.close()


@pytest.fixture
def script_executor():
    return ScriptExecutor()


@pytest.fixture
def human_executor():
    return HumanExecutor()


@pytest.fixture
def mock_llm():
    return MockLLMExecutor()


@pytest.fixture
def registry(script_executor, human_executor, mock_llm):
    reg = ExecutorRegistry()
    reg.register("script", script_executor)
    reg.register("human", human_executor)
    reg.register("mock_llm", mock_llm)
    reg.register("sub_job", SubJobExecutor(reg))
    return reg


# ── Workflow fixtures ────────────────────────────────────────────────


@pytest.fixture
def linear_workflow():
    """A -> B -> C linear workflow using callables."""
    return WorkflowDefinition(
        name="linear",
        steps=[
            StepDefinition(
                name="step_a",
                executor="script",
                config={"callable": lambda inputs: {"value": 1}},
            ),
            StepDefinition(
                name="step_b",
                executor="script",
                config={
                    "callable": lambda inputs: {
                        "value": inputs.get("a_value", 0) + 10
                    }
                },
                depends_on=["step_a"],
                inputs=[InputBinding("step_a", "value", "a_value")],
            ),
            StepDefinition(
                name="step_c",
                executor="script",
                config={
                    "callable": lambda inputs: {
                        "value": inputs.get("b_value", 0) * 2
                    }
                },
                depends_on=["step_b"],
                inputs=[InputBinding("step_b", "value", "b_value")],
            ),
        ],
    )


@pytest.fixture
def fanout_workflow():
    """A -> (B, C) -> D fan-out/fan-in workflow."""
    return WorkflowDefinition(
        name="fanout",
        steps=[
            StepDefinition(
                name="produce",
                executor="script",
                config={"callable": lambda inputs: {"data": "hello"}},
            ),
            StepDefinition(
                name="branch_1",
                executor="script",
                config={
                    "callable": lambda inputs: {
                        "result": inputs.get("data", "") + "_b1"
                    }
                },
                depends_on=["produce"],
                inputs=[InputBinding("produce", "data", "data")],
            ),
            StepDefinition(
                name="branch_2",
                executor="script",
                config={
                    "callable": lambda inputs: {
                        "result": inputs.get("data", "") + "_b2"
                    }
                },
                depends_on=["produce"],
                inputs=[InputBinding("produce", "data", "data")],
            ),
            StepDefinition(
                name="merge",
                executor="script",
                config={
                    "callable": lambda inputs: {
                        "combined": f"{inputs.get('r1', '')},{inputs.get('r2', '')}"
                    }
                },
                depends_on=["branch_1", "branch_2"],
                inputs=[
                    InputBinding("branch_1", "result", "r1"),
                    InputBinding("branch_2", "result", "r2"),
                ],
            ),
        ],
    )


@pytest.fixture
def loop_workflow():
    """Produce a list, then process each item via loop_over."""
    return WorkflowDefinition(
        name="loop",
        steps=[
            StepDefinition(
                name="generate",
                executor="script",
                config={
                    "callable": lambda inputs: {"items": [1, 2, 3]},
                },
            ),
            StepDefinition(
                name="process",
                executor="script",
                config={
                    "callable": lambda inputs: {
                        "doubled": inputs.get("item", 0) * 2
                    }
                },
                depends_on=["generate"],
                loop_over="generate.items",
            ),
            StepDefinition(
                name="collect",
                executor="script",
                config={
                    "callable": lambda inputs: {
                        "total": sum(
                            r["doubled"]
                            for r in inputs.get("all_results", [])
                        )
                    }
                },
                depends_on=["process"],
                inputs=[InputBinding("process", "results", "all_results")],
            ),
        ],
    )
