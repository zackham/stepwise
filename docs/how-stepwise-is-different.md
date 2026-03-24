# How Stepwise is Different

There are many tools in the "run AI stuff" space. Here's how stepwise compares to things you might already use, and why you'd pick it.

## vs GitHub Actions / CI Systems

GitHub Actions, GitLab CI, and Jenkins are build pipeline tools. They run shell commands on events (push, PR, cron). They're great at what they do — but they're not designed for AI agent orchestration.

**Key differences:**

- **Steps are LLM calls and agent sessions, not just shell scripts.** A stepwise step can be `executor: agent` (a full agentic loop with tools), `executor: llm` (a single model call), `executor: external` (paused for human input), or `run:` (a shell command). CI only has the last one.
- **Flows are conversational, not fire-and-forget.** A CI pipeline runs to completion or fails. A stepwise flow can pause for human approval, loop back to retry with different inputs, branch on LLM output, and resume after a crash. Jobs are stateful objects you interact with, not log streams you watch.
- **Local-first execution.** Stepwise runs on your machine. No YAML pushed to a remote server, no waiting for runners, no artifact uploads. `stepwise run my-flow.yaml` and you're going.
- **No infrastructure.** GitHub Actions needs GitHub. Jenkins needs Jenkins. Stepwise needs Python and SQLite.

**When to use CI instead:** If your workflow is "on push, run tests, build artifact, deploy" — use CI. That's its sweet spot. Use stepwise when the workflow involves LLMs making decisions, humans providing judgment, or multi-step agent work with quality gates.

## vs Temporal / Airflow / Prefect

These are serious workflow orchestration engines. Temporal gives you durable execution with replay. Airflow gives you scheduled DAG pipelines. Prefect gives you observable Python workflows. They're production infrastructure for teams.

**Key differences:**

- **Zero infrastructure.** Temporal needs a server cluster, workers, and Postgres/Cassandra. Airflow needs a scheduler, web server, and metadata database. Stepwise needs `curl | sh` and stores everything in SQLite. One process, one machine.
- **Embedded in the dev workflow.** You write a `.flow.yaml`, run `stepwise run`, and watch results in the terminal or web UI on localhost. No deploy step. No worker registration. No Docker compose files.
- **AI-native step types.** Temporal and Airflow treat every step as "run this code." Stepwise has first-class executors for LLM calls, agent sessions, human gates, and polling — each with typed contracts, cost tracking, and appropriate retry semantics.
- **Human-in-the-loop as a primitive.** The `external` executor pauses a job and waits for human input through a typed form. This isn't bolted on — it's the same execution model as every other step. Temporal can do this with signals, but it requires custom code.

**When to use Temporal/Airflow instead:** If you need distributed execution across multiple machines, team-level access controls, compliance audit trails, or workflows processing millions of records. Stepwise is for developers and small teams running AI workflows on their own hardware.

## vs Prompt Chaining Libraries (LangChain, LlamaIndex, etc.)

LangChain, LlamaIndex, and similar libraries let you chain LLM calls in Python. They provide abstractions for prompts, retrievers, output parsers, and agents.

**Key differences:**

- **Real execution engine, not function composition.** Stepwise has a state machine with persistent runs, step statuses, and event logs. If your process dies mid-workflow, you resume from where you left off — not from the beginning. Prompt chaining libraries run in-memory; crash = restart.
- **Exit rules and conditional branching.** Steps can loop, escalate to humans, or abandon based on output conditions. This isn't "if/else in Python" — it's a declarative control flow that the engine enforces and logs. You see every branch decision in the event stream.
- **Human gates are first-class.** An `external` step pauses the job, presents a typed input form, and resumes when a human responds. In LangChain, human-in-the-loop means writing custom callback handlers and managing state yourself.
- **Declarative, not imperative.** Flows are YAML, not Python. This means they're portable, versionable, diffable, and shareable. You can read a flow file and understand the entire workflow without tracing through function calls. Non-programmers can review and modify flows.
- **Observability built in.** Every step transition, every input/output handoff, every cost event is logged. `stepwise run --report` generates an interactive HTML trace. In a chaining library, you get whatever logging you remembered to add.

**When to use LangChain instead:** If you're building a Python application where LLM calls are embedded in your business logic — RAG pipelines, chatbots, tool-using agents within your app. LangChain is a library you call from your code. Stepwise is an engine that runs your workflows.

## vs Claude Projects / Custom Instructions / System Prompts

Claude Projects and custom instructions let you configure a single AI session with persistent context. They're useful — but they're not workflow orchestration.

**Key differences:**

- **Portable and versionable.** A `.flow.yaml` lives in your repo. You can `git diff` it, review it in a PR, share it with teammates, and run it on any machine. Custom instructions live in a vendor's UI and stay there.
- **Multi-step with state.** A Claude Project gives you one long conversation. A stepwise flow gives you a DAG of steps with typed data flowing between them, each step potentially using a different model, different tools, or a human.
- **Provider-independent.** Stepwise routes LLM calls through OpenRouter. Your flows aren't locked to Claude, GPT, or any single provider. Switch models per-step based on cost, capability, or preference.
- **Reproducible.** Run the same flow twice with the same inputs, and you get the same orchestration (even if LLM outputs differ). The structure is deterministic. Custom instructions produce whatever the model produces in that session.

**When to use Claude Projects instead:** When you want to have a conversation with an AI that has persistent context about your project. That's a different use case — interactive exploration vs. structured execution. Use Projects for thinking, use stepwise for doing.

## The Short Version

| Tool | Good at | Not designed for |
|---|---|---|
| CI (GitHub Actions) | Build/test/deploy on events | LLM orchestration, human gates, interactive flows |
| Temporal/Airflow | Distributed durable workflows at scale | Single-developer AI workflows, zero-infra setup |
| LangChain/LlamaIndex | Embedding LLM calls in Python apps | Persistent state, declarative workflows, human-in-the-loop |
| Claude Projects | Interactive AI conversations with context | Multi-step orchestration, branching, reproducibility |
| **Stepwise** | AI workflow orchestration with human gates | Distributed systems, embedded library use, chat UIs |

Stepwise occupies the space between "chain some LLM calls in a script" and "deploy a distributed workflow engine." It gives you real orchestration — state machines, exit rules, human gates, observability — without requiring infrastructure. If you can describe your workflow as steps with inputs and outputs, stepwise runs it.
