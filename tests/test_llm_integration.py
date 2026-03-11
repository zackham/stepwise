"""Integration tests: LLMExecutor through the engine with mock client.

Tests the full path: workflow definition → engine tick → LLM executor → output parsing → step completion.
"""

import pytest

from stepwise.engine import Engine
from stepwise.executors import (
    ExecutorRegistry,
    HumanExecutor,
    LLMExecutor,
    ScriptExecutor,
)
from stepwise.llm_client import LLMResponse
from stepwise.models import (
    ExecutorRef,
    InputBinding,
    JobConfig,
    JobStatus,
    StepDefinition,
    WorkflowDefinition,
)
from stepwise.store import SQLiteStore
from tests.conftest import CallableExecutor, register_step_fn
from tests.mock_llm_client import MockLLMClient


def make_llm_engine(client: MockLLMClient):
    """Create an engine with an LLM executor backed by the mock client."""
    store = SQLiteStore(":memory:")
    reg = ExecutorRegistry()

    # Standard executors
    reg.register("script", lambda c: ScriptExecutor(command=c.get("command", "echo '{}'")))
    reg.register("callable", lambda c: CallableExecutor(fn_name=c.get("fn_name", "default")))

    # LLM executor with mock client
    def _create_llm(config: dict) -> LLMExecutor:
        ex = LLMExecutor(
            client=client,
            model=config.get("model", "test/model"),
            prompt=config.get("prompt", "$input"),
            system=config.get("system"),
            temperature=config.get("temperature", 0.0),
            max_tokens=config.get("max_tokens", 4096),
        )
        ex._output_fields = config.get("output_fields", [])
        return ex

    reg.register("llm", _create_llm)

    return Engine(store=store, registry=reg)


def _submit(engine, name, w, inputs=None):
    """Helper: create + start a job, return job."""
    job = engine.create_job(name, w, inputs=inputs)
    engine.start_job(job.id)
    return job


# ── Single LLM Step ──────────────────────────────────────────────────


class TestSingleLLMStep:
    def test_classify_step(self):
        """Single LLM step: classify text → label."""
        client = MockLLMClient(tool_calls=[
            {"name": "step_output", "arguments": {"label": "positive", "confidence": "0.95"}}
        ])
        engine = make_llm_engine(client)

        w = WorkflowDefinition(steps={
            "classify": StepDefinition(
                name="classify",
                outputs=["label", "confidence"],
                executor=ExecutorRef("llm", {
                    "prompt": "Classify this text: $text",
                    "model": "anthropic/claude-sonnet-4",
                }),
            ),
        })
        job = _submit(engine, "Classify", w, inputs={"text": "I love this product!"})

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        runs = engine.get_runs(job.id)
        classify_run = [r for r in runs if r.step_name == "classify"][0]
        assert classify_run.result.artifact["label"] == "positive"
        assert classify_run.result.artifact["confidence"] == "0.95"
        # model in executor_meta comes from the LLM response (mock returns its default)
        assert classify_run.result.executor_meta["model"] == "mock/test-model"
        # But the actual model requested is in the client's call log
        assert client.calls[0].model == "anthropic/claude-sonnet-4"

    def test_llm_step_failure_propagates(self):
        """LLM API error fails the step, doesn't crash the engine."""
        client = MockLLMClient(error=ConnectionError("network down"))
        engine = make_llm_engine(client)

        w = WorkflowDefinition(steps={
            "analyze": StepDefinition(
                name="analyze",
                outputs=["result"],
                executor=ExecutorRef("llm", {"prompt": "Analyze: $data"}),
            ),
        })
        job = _submit(engine, "Analyze", w, inputs={"data": "test"})

        job = engine.get_job(job.id)
        assert job.status == JobStatus.FAILED

        runs = engine.get_runs(job.id)
        run = runs[0]
        assert run.result.executor_meta["failed"] is True
        assert "network down" in run.result.executor_meta["error"]


# ── Two-Step Pipeline ────────────────────────────────────────────────


class TestTwoStepPipeline:
    def test_extract_then_classify(self):
        """Step A extracts keywords, step B classifies based on them."""
        r1 = MockLLMClient.tool_call_response({"keywords": "ai, machine learning, neural"})
        r2 = MockLLMClient.tool_call_response({"category": "technology", "subtopic": "AI/ML"})
        client = MockLLMClient(responses=[r1, r2])
        engine = make_llm_engine(client)

        w = WorkflowDefinition(steps={
            "extract": StepDefinition(
                name="extract",
                outputs=["keywords"],
                executor=ExecutorRef("llm", {
                    "prompt": "Extract keywords from: $text",
                }),
            ),
            "classify": StepDefinition(
                name="classify",
                outputs=["category", "subtopic"],
                executor=ExecutorRef("llm", {
                    "prompt": "Classify based on keywords: $keywords",
                }),
                inputs=[InputBinding("keywords", "extract", "keywords")],
            ),
        })
        job = _submit(engine, "Pipeline", w, inputs={"text": "Deep learning paper"})
        engine.tick()  # runs classify (extract already ran on start_job)

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        runs = engine.get_runs(job.id)
        classify_run = [r for r in runs if r.step_name == "classify"][0]
        assert classify_run.result.artifact["category"] == "technology"

        # Verify the second call received the keywords from first step
        assert len(client.calls) == 2
        second_prompt = client.calls[1].messages[-1]["content"]
        assert "ai, machine learning, neural" in second_prompt


# ── Mixed Executor Pipeline ──────────────────────────────────────────


class TestMixedExecutorPipeline:
    def test_script_then_llm(self):
        """Script executor produces data, LLM executor processes it."""
        client = MockLLMClient(tool_calls=[
            {"name": "step_output", "arguments": {"summary": "File contains 42 records"}}
        ])
        engine = make_llm_engine(client)

        w = WorkflowDefinition(steps={
            "load": StepDefinition(
                name="load",
                outputs=["data"],
                executor=ExecutorRef("script", {
                    "command": 'echo \'{"data": "42 records found"}\'',
                }),
            ),
            "summarize": StepDefinition(
                name="summarize",
                outputs=["summary"],
                executor=ExecutorRef("llm", {
                    "prompt": "Summarize this data: $data",
                }),
                inputs=[InputBinding("data", "load", "data")],
            ),
        })
        job = _submit(engine, "Mixed", w)
        engine.tick()  # runs summarize

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        runs = engine.get_runs(job.id)
        summarize_run = [r for r in runs if r.step_name == "summarize"][0]
        assert summarize_run.result.artifact["summary"] == "File contains 42 records"


# ── Output Field Injection ───────────────────────────────────────────


class TestOutputFieldInjection:
    def test_engine_injects_output_fields(self):
        """Engine passes step.outputs as output_fields to LLM executor config."""
        client = MockLLMClient(tool_calls=[
            {"name": "step_output", "arguments": {"label": "ok", "score": "5"}}
        ])
        engine = make_llm_engine(client)

        w = WorkflowDefinition(steps={
            "rate": StepDefinition(
                name="rate",
                outputs=["label", "score"],
                executor=ExecutorRef("llm", {"prompt": "Rate: $item"}),
            ),
        })
        job = _submit(engine, "Rate", w, inputs={"item": "test"})

        # The tool schema sent to the LLM should have the output fields
        tools = client.calls[0].tools
        assert tools is not None
        props = tools[0]["function"]["parameters"]["properties"]
        assert "label" in props
        assert "score" in props

    def test_outputs_declared_builds_tool_schema(self):
        """Step with declared outputs always gets a tool schema."""
        client = MockLLMClient(content='{"result": "free form response"}')
        engine = make_llm_engine(client)

        w = WorkflowDefinition(steps={
            "freeform": StepDefinition(
                name="freeform",
                outputs=["result"],
                executor=ExecutorRef("llm", {"prompt": "Say something"}),
            ),
        })
        job = _submit(engine, "Freeform", w)

        # Since outputs are declared, tools should be built
        tools = client.calls[0].tools
        assert tools is not None


# ── JSON Content Fallback Through Engine ─────────────────────────────


class TestJsonContentFallback:
    def test_json_content_when_no_tool_call(self):
        """LLM returns JSON in content (no tool call) — still works."""
        client = MockLLMClient(content='{"sentiment": "positive", "score": "0.92"}')
        engine = make_llm_engine(client)

        w = WorkflowDefinition(steps={
            "analyze": StepDefinition(
                name="analyze",
                outputs=["sentiment", "score"],
                executor=ExecutorRef("llm", {"prompt": "Analyze: $text"}),
            ),
        })
        job = _submit(engine, "Analyze", w, inputs={"text": "Great day!"})

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED

        runs = engine.get_runs(job.id)
        assert runs[0].result.artifact["sentiment"] == "positive"
        assert runs[0].result.executor_meta["parse_method"] == "json_content"


# ── Parallel LLM Steps ──────────────────────────────────────────────


class TestParallelLLMSteps:
    def test_independent_llm_steps(self):
        """Two LLM steps with no deps both run on first tick."""
        r1 = MockLLMClient.tool_call_response({"sentiment": "positive"})
        r2 = MockLLMClient.tool_call_response({"topic": "technology"})
        client = MockLLMClient(responses=[r1, r2])
        engine = make_llm_engine(client)

        w = WorkflowDefinition(steps={
            "sentiment": StepDefinition(
                name="sentiment",
                outputs=["sentiment"],
                executor=ExecutorRef("llm", {"prompt": "Sentiment of: $text"}),
            ),
            "topic": StepDefinition(
                name="topic",
                outputs=["topic"],
                executor=ExecutorRef("llm", {"prompt": "Topic of: $text"}),
            ),
        })
        job = _submit(engine, "Parallel", w, inputs={"text": "AI is amazing"})

        job = engine.get_job(job.id)
        assert job.status == JobStatus.COMPLETED
        assert len(client.calls) == 2
