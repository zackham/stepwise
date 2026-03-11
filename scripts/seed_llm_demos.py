"""Seed the Stepwise database with completed LLM demo jobs.

These demonstrate the LLM executor's capabilities in the web UI.
Uses MockLLMClient to simulate real responses without API calls.
"""

import sys
sys.path.insert(0, "src")

from stepwise.engine import Engine
from stepwise.executors import ExecutorRegistry, LLMExecutor, ScriptExecutor
from stepwise.llm_client import LLMResponse
from stepwise.models import (
    ExecutorRef, InputBinding, JobConfig, StepDefinition, WorkflowDefinition,
)
from stepwise.store import SQLiteStore


class SequenceClient:
    """Returns pre-configured responses in order."""
    def __init__(self, responses: list[LLMResponse]):
        self._responses = responses
        self._i = 0

    def chat_completion(self, model, messages, tools=None, temperature=0.0, max_tokens=4096):
        resp = self._responses[self._i]
        self._i += 1
        return resp


def make_response(arguments: dict, model: str = "anthropic/claude-sonnet-4", cost: float = 0.003):
    return LLMResponse(
        content=None,
        tool_calls=[{"name": "step_output", "arguments": arguments}],
        usage={"prompt_tokens": 250, "completion_tokens": 120},
        model=model,
        cost_usd=cost,
        latency_ms=1200,
    )


def seed_all(db_path: str = "stepwise.db"):
    store = SQLiteStore(db_path)

    # ── Demo 1: Simple Classification ─────────────────────────────────
    client1 = SequenceClient([
        make_response({
            "sentiment": "positive",
            "confidence": "0.94",
            "reasoning": "Strong positive language: 'love', 'amazing', 'best ever'"
        }),
    ])
    reg1 = _make_registry(client1)
    engine1 = Engine(store=store, registry=reg1)

    w1 = WorkflowDefinition(steps={
        "classify": StepDefinition(
            name="classify",
            outputs=["sentiment", "confidence", "reasoning"],
            executor=ExecutorRef("llm", {
                "prompt": "Classify the sentiment of this customer review:\n\n$review",
                "model": "anthropic/claude-sonnet-4",
                "system": "You are a sentiment analysis expert. Classify as positive, negative, or neutral.",
            }),
        ),
    })
    job1 = engine1.create_job(
        "Sentiment Classification",
        w1,
        inputs={"review": "I absolutely love this product! It's the best thing I've ever bought. Amazing quality and fast shipping."},
    )
    engine1.start_job(job1.id)
    print(f"  Demo 1 (classification): {job1.id}")

    # ── Demo 2: Two-Step Pipeline (Extract → Summarize) ───────────────
    client2 = SequenceClient([
        make_response({
            "entities": "Python, FastAPI, SQLite, React, TypeScript, DAG visualization, workflow orchestration",
            "topics": "software architecture, personal tools, LLM integration",
        }),
        make_response({
            "summary": "A personal workflow orchestration engine built with Python/FastAPI backend and React/TypeScript frontend. Uses SQLite for persistence and features DAG-based workflow visualization. Currently integrating LLM capabilities for AI-powered workflow steps.",
            "key_insight": "The project prioritizes testability through dependency injection and mock-based testing, keeping the system portable and independent of external services.",
        }),
    ])
    reg2 = _make_registry(client2)
    engine2 = Engine(store=store, registry=reg2)

    w2 = WorkflowDefinition(steps={
        "extract": StepDefinition(
            name="extract",
            outputs=["entities", "topics"],
            executor=ExecutorRef("llm", {
                "prompt": "Extract key entities and topics from this project description:\n\n$description",
                "model": "anthropic/claude-sonnet-4",
            }),
        ),
        "summarize": StepDefinition(
            name="summarize",
            outputs=["summary", "key_insight"],
            executor=ExecutorRef("llm", {
                "prompt": "Given these extracted entities and topics, write a concise summary and identify the key architectural insight.\n\nEntities: $entities\nTopics: $topics",
                "model": "anthropic/claude-sonnet-4",
            }),
            inputs=[
                InputBinding("entities", "extract", "entities"),
                InputBinding("topics", "extract", "topics"),
            ],
        ),
    })
    job2 = engine2.create_job(
        "Project Analysis Pipeline",
        w2,
        inputs={"description": "Stepwise is a personal workflow orchestration engine. Python backend with FastAPI, SQLite persistence, React/TypeScript frontend with DAG visualization. Currently building M3: LLM executor integration for AI-powered steps. Uses dependency injection for testability."},
    )
    engine2.start_job(job2.id)
    engine2.tick()  # run summarize after extract
    print(f"  Demo 2 (pipeline): {job2.id}")

    # ── Demo 3: Parallel Analysis (Sentiment + Topic + Language) ──────
    client3 = SequenceClient([
        make_response({"sentiment": "enthusiastic", "intensity": "high"}),
        make_response({"category": "technology/AI", "subcategory": "developer tools"}),
        make_response({"language": "en", "formality": "casual", "tone": "excited"}),
    ])
    reg3 = _make_registry(client3)
    engine3 = Engine(store=store, registry=reg3)

    w3 = WorkflowDefinition(steps={
        "sentiment": StepDefinition(
            name="sentiment",
            outputs=["sentiment", "intensity"],
            executor=ExecutorRef("llm", {
                "prompt": "Analyze the emotional sentiment: $text",
                "model": "anthropic/claude-sonnet-4",
            }),
        ),
        "topic": StepDefinition(
            name="topic",
            outputs=["category", "subcategory"],
            executor=ExecutorRef("llm", {
                "prompt": "Categorize this text by topic: $text",
                "model": "anthropic/claude-sonnet-4",
            }),
        ),
        "language": StepDefinition(
            name="language",
            outputs=["language", "formality", "tone"],
            executor=ExecutorRef("llm", {
                "prompt": "Analyze the language characteristics: $text",
                "model": "anthropic/claude-sonnet-4",
            }),
        ),
    })
    job3 = engine3.create_job(
        "Parallel Text Analysis",
        w3,
        inputs={"text": "Just shipped the LLM executor for Stepwise! Three parse methods, full test coverage, mock infrastructure. The DAG view looks amazing with parallel steps running side by side."},
    )
    engine3.start_job(job3.id)
    print(f"  Demo 3 (parallel): {job3.id}")

    # ── Demo 4: Mixed Pipeline (Script → LLM) ────────────────────────
    client4 = SequenceClient([
        make_response({
            "analysis": "The data contains 3 key-value pairs with metadata about a demo workflow. The version indicates this is an early-stage system (0.1.0) with the 'demo' tag suggesting test/example data.",
            "recommendation": "Production-ready. The structure is clean and follows JSON conventions.",
        }),
    ])
    reg4 = _make_registry(client4)
    reg4.register("script", lambda c: ScriptExecutor(command=c.get("command", "echo '{}'")))
    engine4 = Engine(store=store, registry=reg4)

    w4 = WorkflowDefinition(steps={
        "fetch_data": StepDefinition(
            name="fetch_data",
            outputs=["raw_data"],
            executor=ExecutorRef("script", {
                "command": 'echo \'{"raw_data": "name=stepwise, version=0.1.0, tags=demo"}\'',
            }),
        ),
        "analyze": StepDefinition(
            name="analyze",
            outputs=["analysis", "recommendation"],
            executor=ExecutorRef("llm", {
                "prompt": "Analyze this raw data and provide a recommendation:\n\n$raw_data",
                "model": "anthropic/claude-sonnet-4",
                "system": "You are a data analyst. Be concise and actionable.",
            }),
            inputs=[InputBinding("raw_data", "fetch_data", "raw_data")],
        ),
    })
    job4 = engine4.create_job("Mixed Pipeline: Script → LLM", w4)
    engine4.start_job(job4.id)
    engine4.tick()
    print(f"  Demo 4 (mixed): {job4.id}")

    store.close()
    print(f"\nSeeded 4 demo jobs into {db_path}")


def _make_registry(client) -> ExecutorRegistry:
    reg = ExecutorRegistry()

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
    return reg


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "stepwise.db"
    print(f"Seeding LLM demo jobs into {db}...")
    seed_all(db)
