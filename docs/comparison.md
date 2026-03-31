# Stepwise vs Other Tools

Tradeoff guide, not a pitch. Every tool here is good at something. The question is whether your workflow fits Stepwise's model.

## The short version

Stepwise occupies a specific gap: between "chain some LLM calls in a script" and "deploy a workflow platform." You get real orchestration — persistent state, exit rules, human gates, cost tracking, five executor types — without infrastructure, a web platform, or a Python framework. The sweet spot is workflows with typed steps that include AI agents, scripts, and human decisions.

## vs Temporal / Airflow / Prefect

Serious workflow orchestration engines. Temporal gives you durable execution with event-sourced replay. Airflow gives you scheduled DAG pipelines. Prefect gives you observable Python workflows.

**Where they win:**
- Battle-tested distributed execution across machine clusters
- Team-level namespaces, versioning, and RBAC
- Scales to millions of concurrent workflows
- Mature ecosystems with years of production hardening

**Where Stepwise differs:**
- **Zero infrastructure.** Temporal needs a server cluster + Postgres/Cassandra. Airflow needs a scheduler + web server + metadata database. Stepwise needs `curl | sh` and stores everything in SQLite. One process, one machine.
- **AI-native step types.** Temporal and Airflow treat every step as "run this code." Stepwise has first-class executors for LLM calls, full agent sessions, human gates, and polling — each with typed contracts, cost tracking, and appropriate retry semantics.
- **Embedded in the dev workflow.** Write a `.flow.yaml`, run `stepwise run`, watch results in the terminal or browser. No deploy step, no worker registration, no Docker compose.
- **External fulfillment as a primitive.** The `external` executor pauses a job and waits for typed input. Not bolted on — same execution model as every other step.

**Use Temporal/Airflow when:** You need distributed execution across machines, team access controls, compliance audit trails, or production scale. Use Stepwise for developer-facing AI workflows on your own hardware.

## vs LangGraph

LangGraph builds stateful agent graphs in Python. Cyclic graphs with persistent state and conditional edges — LangChain's answer to "I need more than a chain."

**Where LangGraph wins:**
- Native Python with IDE support, debugging, and the full ecosystem
- Fine-grained control over each node's implementation
- LangGraph Platform provides hosted deployment with persistence and streaming
- Tight integration with LangChain's retrieval, tool calling, and prompt management

**Where Stepwise differs:**
- **Declarative vs imperative.** LangGraph graphs are Python code. Stepwise flows are YAML — portable, diffable, readable by non-programmers, runnable without writing application code. The tradeoff: you lose arbitrary Python in each node.
- **Full agent sessions, not single LLM calls.** Stepwise agent steps are complete agentic loops via ACP — Claude, Codex, Gemini with tools, file access, and multi-turn reasoning. LangGraph nodes are typically single LLM calls or tool invocations.
- **No library dependency.** Stepwise is a standalone engine. No `pip install langgraph`, no framework to learn, no API to wrap. Install once, call from anything.
- **Steps are pure functions.** Inputs in, outputs out, no shared mutable state. LangGraph uses shared mutable state across nodes, which creates race conditions in concurrent execution.

**Use LangGraph when:** Your graph is part of a Python application and you want code-level control over each node. Use Stepwise when you want declarative workflows that coordinate external agents, scripts, and humans.

## vs CrewAI

CrewAI orchestrates multiple AI agents with defined roles — "crews" of agents that collaborate, each with a persona, tools, and goals.

**Where CrewAI wins:**
- Role-based agent design with personas that can improve output quality for tasks benefiting from diverse perspectives
- Simple Python API for defining agent teams
- Built-in delegation between agents

**Where Stepwise differs:**
- **Durable execution.** CrewAI runs in-memory. If the process dies, you start over. Stepwise persists every step to SQLite — crash, restart, resume from the last completed step.
- **Real agent sessions.** CrewAI agents are Python objects with LLM calls. Stepwise agent steps are full ACP sessions — Claude, Codex, Gemini with tool access, file I/O, and shell execution.
- **Explicit control flow.** Stepwise's exit rules, human gates, and branching give you declared control over flow behavior. CrewAI delegates control flow to the agents themselves — flexible, but unpredictable.
- **Mixed executor types.** CrewAI is agents-only. Stepwise mixes shell scripts, single LLM calls, agent sessions, human gates, and polls in one DAG.

**Use CrewAI when:** You want multiple LLM personas collaborating on a task in Python, and the task completes in one run without human intervention. Use Stepwise when you need durable execution, human gates, shell scripts, or real agent sessions with tool access.

## vs GitHub Actions / CI Systems

GitHub Actions, GitLab CI, and Jenkins run shell commands on git events. Great at what they do — not designed for AI agent orchestration.

**Where CI wins:**
- Massive ecosystem of community actions
- Tight GitHub integration (status checks, deployments, artifacts)
- Runs on provider infrastructure — no local setup
- Matrix builds for cross-platform testing

**Where Stepwise differs:**
- **Steps are LLM calls and agent sessions, not just shell scripts.** CI has one step type: run a command. Stepwise has five executor types including full agentic loops and human gates.
- **Flows are conversational, not fire-and-forget.** CI runs to completion or fails. Stepwise flows pause for human input, loop back to retry, branch on LLM output, and resume after crashes.
- **Local-first.** No YAML pushed to a remote server, no waiting for runners, no artifact uploads. `stepwise run` and you're going.

**Use CI when:** Your workflow is triggered by git events and runs deterministic commands. Use Stepwise when steps involve LLM decisions, human judgment, or multi-model orchestration.

## vs Prompt Chaining (LangChain, LlamaIndex)

Libraries for chaining LLM calls in Python — prompts, retrievers, output parsers, agents.

**Where they win:**
- Deep ecosystem for RAG, retrieval, and tool calling
- Embeddable in any Python application
- Large community and extensive documentation

**Where Stepwise differs:**
- **Real execution engine.** Stepwise has persistent state, step statuses, and event logs. If the process dies, you resume. Prompt chaining libraries run in-memory — crash = restart.
- **Declarative control flow.** Exit rules, loops, conditional branching — declared in YAML, enforced by the engine.
- **Human gates are first-class.** An `external` step pauses, presents a typed form, and resumes. In LangChain, this means custom callback handlers and manual state management.
- **Observability built in.** `--report` generates an interactive HTML trace. Every step transition and cost event is logged automatically.

**Use LangChain when:** You're building a Python application where LLM calls are embedded in your business logic — RAG pipelines, chatbots, tool-using agents within your app. Use Stepwise when you need an engine that runs multi-step workflows.

## vs Dify

Visual platform for building LLM applications — drag-and-drop canvas, prompt management, RAG, one-click deployment.

**Where Dify wins:**
- Non-technical users can create and modify flows visually
- Built-in RAG with document indexing
- Hosted deployment with API endpoints
- Team collaboration on shared workflows

**Where Stepwise differs:**
- **No GUI dependency.** Stepwise flows are `.flow.yaml` files in your repo — version-controlled, diffable, portable. Run headless in CI, cron, or agent tool calls.
- **Full agent sessions.** Stepwise orchestrates complete agentic loops with tool access and code generation. Dify is oriented around prompt chains and retrieval pipelines.
- **Developer-first.** If your workflow involves shell scripts, code generation, and git, Stepwise fits naturally. Dify targets prompt-and-retrieval workflows.

**Use Dify when:** Your team wants a visual builder for LLM applications, especially RAG-heavy use cases. Use Stepwise when workflows involve agentic code generation, shell commands, and human gates.

## Summary

| Tool | Best at | Stepwise advantage |
|---|---|---|
| Temporal/Airflow | Distributed durable workflows | Zero infrastructure, AI-native executors, YAML workflows |
| LangGraph | Stateful Python agent graphs | Declarative YAML, full agent sessions, no library dependency |
| CrewAI | Multi-persona agent teams | Durable state, human gates, mixed executor types |
| GitHub Actions | CI/CD on git events | LLM orchestration, human gates, local execution |
| LangChain/LlamaIndex | Embedding LLM calls in Python apps | Persistent state, declarative workflows, external fulfillment |
| Dify | Visual LLM app builder | Version-controlled flows, agentic execution, headless operation |
