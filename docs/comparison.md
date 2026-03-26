# Stepwise vs Other Tools

This is a tradeoff guide, not a pitch. Every tool here is good at something. The question is whether your workflow fits stepwise's model or someone else's.

## vs GitHub Actions / CI Systems

GitHub Actions, GitLab CI, and Jenkins are build pipeline tools. They run shell commands on git events (push, PR, cron). They're great at what they do — but they're not designed for AI agent orchestration.

**Where CI wins:** Massive ecosystem of community actions. Tight GitHub integration (status checks, deployments, artifacts). Runs on GitHub's infrastructure — no local setup. Matrix builds for cross-platform testing.

**Where stepwise differs:**

- **Steps are LLM calls and agent sessions, not just shell scripts.** A stepwise step can be `executor: agent` (a full agentic loop with tools), `executor: llm` (a single model call), `executor: external` (paused for human input), or `run:` (a shell command). CI only has the last one.
- **Flows are conversational, not fire-and-forget.** A CI pipeline runs to completion or fails. A stepwise flow can pause for human approval, loop back to retry with different inputs, branch on LLM output, and resume after a crash.
- **Local-first execution.** Stepwise runs on your machine. No YAML pushed to a remote server, no waiting for runners, no artifact uploads. `stepwise run my-flow.yaml` and you're going.
- **No infrastructure.** GitHub Actions needs GitHub. Jenkins needs Jenkins. Stepwise needs Python and SQLite.

**Use CI when:** Your workflow is triggered by git events and runs deterministic commands (build, test, lint, deploy). Use stepwise when steps involve LLM decisions, human judgment, or multi-model orchestration.

## vs Temporal / Airflow / Prefect

These are serious workflow orchestration engines. Temporal gives you durable execution with replay. Airflow gives you scheduled DAG pipelines. Prefect gives you observable Python workflows.

**Where Temporal wins:** Battle-tested distributed execution. Workflows survive machine failures through event sourcing and replay. Team-level namespaces, versioning, and observability. Scales to millions of concurrent workflows. Strongly typed workflow definitions in Go, Java, Python, TypeScript.

**Where stepwise differs:**

- **Zero infrastructure.** Temporal needs a server cluster, workers, and Postgres/Cassandra. Airflow needs a scheduler, web server, and metadata database. Stepwise needs `curl | sh` and stores everything in SQLite. One process, one machine.
- **Embedded in the dev workflow.** You write a `.flow.yaml`, run `stepwise run`, and watch results in the terminal or web UI on localhost. No deploy step. No worker registration. No Docker compose files.
- **AI-native step types.** Temporal and Airflow treat every step as "run this code." Stepwise has first-class executors for LLM calls, agent sessions, human gates, and polling — each with typed contracts, cost tracking, and appropriate retry semantics.
- **Human-in-the-loop as a primitive.** The `external` executor pauses a job and waits for human input through a typed form. This isn't bolted on — it's the same execution model as every other step.

**Use Temporal/Airflow when:** You need distributed execution across machines, team access controls, compliance audit trails, or you're running workflows in production at scale. Use stepwise for developer-facing AI workflows on your own hardware.

## vs LangGraph

LangGraph builds stateful agent graphs in Python. It's LangChain's answer to "I need more than a chain" — cyclic graphs with persistent state and human-in-the-loop support.

**Where LangGraph wins:** Native Python — your graph is code, so you get IDE support, debugging, and the full Python ecosystem. Tight LangChain integration for retrieval, tool calling, and prompt management. LangGraph Platform provides hosted deployment with persistence and streaming.

**Where stepwise differs:** LangGraph graphs are Python code; stepwise flows are YAML. This makes flows portable, diffable, and readable by non-programmers — but you lose the flexibility of arbitrary Python in each node. Stepwise agents are full agentic loops (via ACP/acpx) that can use tools, browse the web, and write code; LangGraph nodes are typically single LLM calls or tool invocations. Stepwise has no library dependency — it's a standalone engine you install once.

**Use LangGraph when:** You're building a Python application where the graph is part of your codebase and you want fine-grained control over each node's implementation. Use stepwise when you want declarative workflows that coordinate external agents, scripts, and humans without writing application code.

## vs Prompt Chaining Libraries (LangChain, LlamaIndex, etc.)

LangChain, LlamaIndex, and similar libraries let you chain LLM calls in Python. They provide abstractions for prompts, retrievers, output parsers, and agents.

**Key differences:**

- **Real execution engine, not function composition.** Stepwise has a state machine with persistent runs, step statuses, and event logs. If your process dies mid-workflow, you resume from where you left off. Prompt chaining libraries run in-memory; crash = restart.
- **Exit rules and conditional branching.** Steps can loop, escalate to humans, or abandon based on output conditions. This is declarative control flow that the engine enforces and logs.
- **Human gates are first-class.** An `external` step pauses the job, presents a typed input form, and resumes when a human responds. In LangChain, human-in-the-loop means writing custom callback handlers and managing state yourself.
- **Declarative, not imperative.** Flows are YAML, not Python. Portable, versionable, diffable, and shareable. Non-programmers can review and modify flows.
- **Observability built in.** Every step transition, every input/output handoff, every cost event is logged. `stepwise run --report` generates an interactive HTML trace.

**Use LangChain when:** You're building a Python application where LLM calls are embedded in your business logic — RAG pipelines, chatbots, tool-using agents within your app. LangChain is a library you call from your code. Stepwise is an engine that runs your workflows.

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

## vs Claude Projects / Custom Instructions / System Prompts

Claude Projects and custom instructions let you configure a single AI session with persistent context. They're useful — but they're not workflow orchestration.

**Key differences:**

- **Portable and versionable.** A `.flow.yaml` lives in your repo. You can `git diff` it, review it in a PR, share it with teammates, and run it on any machine. Custom instructions live in a vendor's UI and stay there.
- **Multi-step with state.** A Claude Project gives you one long conversation. A stepwise flow gives you a DAG of steps with typed data flowing between them, each step potentially using a different model, different tools, or a human.
- **Provider-independent.** Stepwise routes LLM calls through OpenRouter. Your flows aren't locked to Claude, GPT, or any single provider.
- **Reproducible.** Run the same flow twice with the same inputs, and you get the same orchestration (even if LLM outputs differ). The structure is deterministic.

**Use Claude Projects when:** You want to have a conversation with an AI that has persistent context about your project. That's a different use case — interactive exploration vs. structured execution.

## Summary Table

| Tool | Best at | Stepwise advantage |
|---|---|---|
| GitHub Actions | CI/CD on git events | LLM orchestration, human gates, local execution |
| Temporal/Airflow | Distributed durable workflows | Zero infrastructure, AI-native executors, YAML workflows |
| LangGraph | Stateful Python agent graphs | Declarative YAML, full agent sessions, no library dependency |
| LangChain/LlamaIndex | Embedding LLM calls in Python apps | Persistent state, declarative workflows, human-in-the-loop |
| Dify | Visual LLM app builder | Version-controlled flows, agentic execution, headless operation |
| CrewAI | Multi-persona agent teams | Durable state, human-in-the-loop, real tool-using agents |
| Claude Projects | Interactive AI conversations with context | Multi-step orchestration, branching, reproducibility |

The common thread: stepwise occupies the space between "chain some LLM calls in a script" and "deploy a workflow platform." It gives you real orchestration — persistent state, exit rules, human gates, cost tracking — without requiring infrastructure, a web platform, or a Python framework. If your workflow is steps with typed inputs and outputs that include AI agents and human decisions, that's the sweet spot.
