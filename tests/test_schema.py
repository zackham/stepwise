"""Tests for stepwise.schema — JSON tool contract generation."""

import pytest

from stepwise.models import (
    ExecutorRef,
    FlowMetadata,
    InputBinding,
    StepDefinition,
    WorkflowDefinition,
    ExitRule,
    ForEachSpec,
    ChainConfig,
)
from stepwise.schema import generate_schema


def _workflow(steps: dict[str, StepDefinition], **meta_kwargs) -> WorkflowDefinition:
    """Helper to build a workflow with metadata."""
    return WorkflowDefinition(
        steps=steps,
        metadata=FlowMetadata(**meta_kwargs),
    )


class TestGenerateSchema:
    """Tests for generate_schema()."""

    def test_linear_flow(self):
        """Linear flow: A → B → C with job inputs."""
        wf = _workflow(
            steps={
                "fetch": StepDefinition(
                    name="fetch",
                    outputs=["data"],
                    executor=ExecutorRef("script", {"command": "fetch.py"}),
                    inputs=[InputBinding("url", "$job", "url")],
                ),
                "process": StepDefinition(
                    name="process",
                    outputs=["result"],
                    executor=ExecutorRef("script", {"command": "process.py"}),
                    inputs=[InputBinding("data", "fetch", "data")],
                ),
                "report": StepDefinition(
                    name="report",
                    outputs=["summary", "score"],
                    executor=ExecutorRef("script", {"command": "report.py"}),
                    inputs=[InputBinding("result", "process", "result")],
                ),
            },
            name="data-pipeline",
            description="Fetch, process, and report on data",
            version="1.0",
        )

        schema = generate_schema(wf)

        assert schema["name"] == "data-pipeline"
        assert schema["description"] == "Fetch, process, and report on data"
        assert schema["version"] == "1.0"
        assert schema["inputs"] == ["url"]
        assert schema["outputs"] == ["summary", "score"]
        assert schema["humanSteps"] == []

    def test_parallel_flow(self):
        """Parallel flow with multiple terminal steps."""
        wf = _workflow(
            steps={
                "split": StepDefinition(
                    name="split",
                    outputs=["chunks"],
                    executor=ExecutorRef("script", {}),
                    inputs=[InputBinding("text", "$job", "text")],
                ),
                "analyze_a": StepDefinition(
                    name="analyze_a",
                    outputs=["sentiment"],
                    executor=ExecutorRef("llm", {}),
                    inputs=[InputBinding("chunks", "split", "chunks")],
                ),
                "analyze_b": StepDefinition(
                    name="analyze_b",
                    outputs=["entities"],
                    executor=ExecutorRef("llm", {}),
                    inputs=[InputBinding("chunks", "split", "chunks")],
                ),
            },
            name="parallel-analysis",
        )

        schema = generate_schema(wf)

        assert schema["inputs"] == ["text"]
        # Both analyze_a and analyze_b are terminal
        assert "sentiment" in schema["outputs"]
        assert "entities" in schema["outputs"]
        assert schema["humanSteps"] == []

    def test_human_step_flow(self):
        """Flow with a human approval step."""
        wf = _workflow(
            steps={
                "build": StepDefinition(
                    name="build",
                    outputs=["artifact"],
                    executor=ExecutorRef("script", {}),
                    inputs=[
                        InputBinding("repo", "$job", "repo"),
                        InputBinding("branch", "$job", "branch"),
                    ],
                ),
                "approve": StepDefinition(
                    name="approve",
                    outputs=["approved", "reason"],
                    executor=ExecutorRef("human", {
                        "prompt": "Review this deployment package. Approve or reject with a reason.",
                    }),
                    inputs=[InputBinding("artifact", "build", "artifact")],
                ),
                "deploy": StepDefinition(
                    name="deploy",
                    outputs=["url", "version"],
                    executor=ExecutorRef("script", {}),
                    inputs=[InputBinding("approved", "approve", "approved")],
                ),
            },
            name="deploy-pipeline",
            description="Build, test, and deploy with human approval",
        )

        schema = generate_schema(wf)

        assert schema["inputs"] == ["branch", "repo"]
        assert schema["outputs"] == ["url", "version"]
        assert len(schema["humanSteps"]) == 1
        hs = schema["humanSteps"][0]
        assert hs["step"] == "approve"
        assert "Review this deployment" in hs["prompt"]
        assert hs["fields"] == ["approved", "reason"]

    def test_no_inputs(self):
        """Flow with no $job.* inputs."""
        wf = _workflow(
            steps={
                "generate": StepDefinition(
                    name="generate",
                    outputs=["content"],
                    executor=ExecutorRef("llm", {"prompt": "Write something"}),
                ),
            },
            name="no-input-flow",
        )

        schema = generate_schema(wf)

        assert schema["inputs"] == []
        assert schema["outputs"] == ["content"]

    def test_no_outputs(self):
        """Flow whose terminal step has no declared outputs (edge case)."""
        wf = _workflow(
            steps={
                "fire_and_forget": StepDefinition(
                    name="fire_and_forget",
                    outputs=[],
                    executor=ExecutorRef("script", {"command": "send.sh"}),
                    inputs=[InputBinding("msg", "$job", "message")],
                ),
            },
            name="notification",
        )

        schema = generate_schema(wf)

        assert schema["inputs"] == ["message"]
        assert schema["outputs"] == []

    def test_loop_flow(self):
        """Loop flow: terminal step should be the one AFTER the loop."""
        wf = _workflow(
            steps={
                "generate": StepDefinition(
                    name="generate",
                    outputs=["content", "quality_score"],
                    executor=ExecutorRef("llm", {}),
                    inputs=[InputBinding("topic", "$job", "topic")],
                    exit_rules=[
                        ExitRule("good_enough", "expression",
                                 {"condition": "float(outputs.get('quality_score', 0)) >= 0.8", "action": "advance"}),
                        ExitRule("retry", "always",
                                 {"action": "loop", "target": "generate"}),
                    ],
                ),
                "publish": StepDefinition(
                    name="publish",
                    outputs=["url"],
                    executor=ExecutorRef("script", {}),
                    inputs=[InputBinding("content", "generate", "content")],
                ),
            },
            name="quality-loop",
        )

        schema = generate_schema(wf)

        assert schema["inputs"] == ["topic"]
        # publish is the terminal, not generate (which loops)
        assert schema["outputs"] == ["url"]

    def test_version_omitted_when_empty(self):
        """Version field omitted from schema when not set."""
        wf = _workflow(
            steps={
                "step": StepDefinition(
                    name="step",
                    outputs=["out"],
                    executor=ExecutorRef("script", {}),
                ),
            },
            name="simple",
        )

        schema = generate_schema(wf)

        assert "version" not in schema

    def test_multiple_job_inputs(self):
        """Multiple steps consuming different $job fields."""
        wf = _workflow(
            steps={
                "a": StepDefinition(
                    name="a",
                    outputs=["x"],
                    executor=ExecutorRef("script", {}),
                    inputs=[
                        InputBinding("q", "$job", "question"),
                        InputBinding("ctx", "$job", "context"),
                    ],
                ),
                "b": StepDefinition(
                    name="b",
                    outputs=["y"],
                    executor=ExecutorRef("script", {}),
                    inputs=[
                        InputBinding("q", "$job", "question"),  # same input
                        InputBinding("t", "$job", "temperature"),
                    ],
                ),
            },
            name="multi-input",
        )

        schema = generate_schema(wf)

        # Should be deduplicated and sorted
        assert schema["inputs"] == ["context", "question", "temperature"]

    def test_multiple_human_steps(self):
        """Flow with multiple human steps."""
        wf = _workflow(
            steps={
                "draft": StepDefinition(
                    name="draft",
                    outputs=["text"],
                    executor=ExecutorRef("llm", {}),
                    inputs=[InputBinding("topic", "$job", "topic")],
                ),
                "review1": StepDefinition(
                    name="review1",
                    outputs=["feedback"],
                    executor=ExecutorRef("human", {"prompt": "Review draft"}),
                    inputs=[InputBinding("text", "draft", "text")],
                ),
                "revise": StepDefinition(
                    name="revise",
                    outputs=["text"],
                    executor=ExecutorRef("llm", {}),
                    inputs=[InputBinding("feedback", "review1", "feedback")],
                ),
                "review2": StepDefinition(
                    name="review2",
                    outputs=["approved"],
                    executor=ExecutorRef("human", {"prompt": "Final approval"}),
                    inputs=[InputBinding("text", "revise", "text")],
                ),
            },
            name="dual-review",
        )

        schema = generate_schema(wf)

        assert len(schema["humanSteps"]) == 2
        names = [hs["step"] for hs in schema["humanSteps"]]
        assert "review1" in names
        assert "review2" in names

    def test_deduplicates_terminal_outputs(self):
        """When multiple terminal steps share output field names, no duplicates."""
        wf = _workflow(
            steps={
                "source": StepDefinition(
                    name="source",
                    outputs=["data"],
                    executor=ExecutorRef("script", {}),
                ),
                "branch_a": StepDefinition(
                    name="branch_a",
                    outputs=["result", "score"],
                    executor=ExecutorRef("script", {}),
                    inputs=[InputBinding("data", "source", "data")],
                ),
                "branch_b": StepDefinition(
                    name="branch_b",
                    outputs=["result", "label"],
                    executor=ExecutorRef("script", {}),
                    inputs=[InputBinding("data", "source", "data")],
                ),
            },
            name="dedup-test",
        )

        schema = generate_schema(wf)

        # "result" appears in both terminals but should only appear once
        assert schema["outputs"].count("result") == 1
        assert "score" in schema["outputs"]
        assert "label" in schema["outputs"]
