# Stepwise vs Other Tools — A Practical Comparison

This is a tradeoff guide, not a pitch. Every tool here is good at something. The question is whether your workflow fits stepwise's model or someone else's.

## vs GitHub Actions

GitHub Actions runs shell commands on git events. It's the default CI/CD for most teams and it's excellent at build/test/deploy pipelines.

**Where Actions wins:** Massive ecosystem of community actions. Tight GitHub integration (status checks, deployments, artifacts). Runs on GitHub's infrastructure — no local setup. Matrix builds for cross-platform testing.

**Where stepwise differs:** Actions steps are shell commands. Stepwise steps can be LLM calls, agent sessions, human approval gates, or shell commands — with typed data flowing between them. A stepwise job can pause for human input and resume hours later; an Actions workflow either passes or fails. Stepwise runs locally with no infrastructure; Actions requires GitHub and runners.

**Use Actions when:** Your workflow is triggered by git events and runs deterministic commands (build, test, lint, deploy). Use stepwise when steps involve LLM decisions, human judgment, or multi-model orchestration.

## vs Temporal

Temporal provides durable execution for distributed systems. It's production infrastructure for companies running mission-critical workflows across clusters of workers.

**Where Temporal wins:** Battle-tested distributed execution. Workflows survive machine failures through event sourcing and replay. Team-level namespaces, versioning, and observability. Scales to millions of concurrent workflows. Strongly typed workflow definitions in Go, Java, Python, TypeScript.

**Where stepwise differs:** Temporal needs a server cluster, workers, and a backing database (Postgres/Cassandra/MySQL). Stepwise is one process and SQLite. Temporal treats every step as "run this function"; stepwise has first-class executors for LLM calls, agent sessions, human gates, and polling — each with appropriate retry semantics and cost tracking. Stepwise workflows are YAML files you run from the terminal; Temporal workflows are compiled code you deploy to a cluster.

**Use Temporal when:** You need distributed execution across machines, team access controls, compliance audit trails, or you're running workflows in production at scale. Use stepwise for developer-facing AI workflows on your own hardware.

## vs LangGraph

LangGraph builds stateful agent graphs in Python. It's LangChain's answer to "I need more than a chain" — cyclic graphs with persistent state and human-in-the-loop support.

**Where LangGraph wins:** Native Python — your graph is code, so you get IDE support, debugging, and the full Python ecosystem. Tight LangChain integration for retrieval, tool calling, and prompt management. LangGraph Platform provides hosted deployment with persistence and streaming.

**Where stepwise differs:** LangGraph graphs are Python code; stepwise flows are YAML. This makes flows portable, diffable, and readable by non-programmers — but you lose the flexibility of arbitrary Python in each node. Stepwise agents are full agentic loops (via ACP/acpx) that can use tools, browse the web, and write code; LangGraph nodes are typically single LLM calls or tool invocations. Stepwise has no library dependency — it's a standalone engine you install once.

**Use LangGraph when:** You're building a Python application where the graph is part of your codebase and you want fine-grained control over each node's implementation. Use stepwise when you want declarative workflows that coordinate external agents, scripts, and humans without writing application code.

## vs Dify

Dify is a visual platform for building LLM applications. It provides a drag-and-drop canvas, prompt management, RAG pipelines, and one-click deployment.

**Where Dify wins:** Visual workflow builder — non-technical users can create and modify flows in a browser. Built-in RAG with document indexing. Hosted deployment with API endpoints. Team collaboration on shared workflows.

**Where stepwise differs:** Dify runs as a web application (self-hosted or cloud); stepwise runs from your terminal with no server required. Dify workflows live in its database; stepwise flows are `.flow.yaml` files in your repo — version-controlled, diffable, portable. Stepwise can orchestrate full agent sessions (code generation, tool use, multi-turn reasoning); Dify is oriented around prompt chains and retrieval pipelines. Stepwise has no GUI dependency — flows run headless in CI, cron jobs, or agent tool calls.

**Use Dify when:** Your team wants a visual builder for LLM applications, especially RAG-heavy use cases, and you want hosted deployment. Use stepwise when your workflows involve agentic code generation, shell commands, and human gates, and you want everything in version control.

## vs CrewAI

CrewAI orchestrates multiple AI agents with defined roles. You create "crews" of agents that collaborate to complete tasks, each with a role, goal, backstory, and tools.

**Where CrewAI wins:** Role-based agent design — each agent has a persona and specialty, which can improve output quality for tasks that benefit from diverse perspectives. Simple Python API for defining agent teams. Built-in delegation between agents.

**Where stepwise differs:** CrewAI agents are Python objects with LLM calls; stepwise agent steps are full ACP-compatible agent sessions (Claude, Codex, Gemini) that can use tools, read/write files, and execute shell commands. CrewAI manages conversations between agents in-memory; stepwise persists every step's inputs, outputs, and state to SQLite — if your process crashes, you resume from the last completed step, not from scratch. Stepwise's exit rules, human gates, and conditional branching provide control flow that CrewAI delegates to the agents themselves.

**Use CrewAI when:** You want multiple LLM personas collaborating on a task in Python, and the task completes in a single run without human intervention. Use stepwise when you need durable execution, human-in-the-loop, shell commands, or real agent sessions with tool access.

## Summary Table

| Tool | Best at | Stepwise advantage |
|---|---|---|
| GitHub Actions | CI/CD on git events | LLM orchestration, human gates, local execution |
| Temporal | Distributed durable workflows | Zero infrastructure, AI-native executors, YAML workflows |
| LangGraph | Stateful Python agent graphs | Declarative YAML, full agent sessions, no library dependency |
| Dify | Visual LLM app builder | Version-controlled flows, agentic execution, headless operation |
| CrewAI | Multi-persona agent teams | Durable state, human-in-the-loop, real tool-using agents |

The common thread: stepwise occupies the space between "chain some LLM calls in a script" and "deploy a workflow platform." It gives you real orchestration — persistent state, exit rules, human gates, cost tracking — without requiring infrastructure, a web platform, or a Python framework. If your workflow is steps with typed inputs and outputs that include AI agents and human decisions, that's the sweet spot.
