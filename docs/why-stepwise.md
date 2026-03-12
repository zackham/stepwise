# Why Stepwise

## The Problem

AI agent frameworks are stuck on the wrong abstraction.

CrewAI gives you "Senior Researcher" and "Technical Writer" — roles with backstories, goals, and persistent personas. AutoGen gives you conversations between named agents. These feel intuitive because they mirror how we think about delegating to people.

But they're modeling the wrong thing. An LLM doesn't "become" a Senior Researcher by reading a backstory. It gets a system prompt, a set of tools, and instructions. The persona is semantic sugar. And it comes with real costs: you can't run two instances of the same "role" in parallel without identity conflicts, you can't change the tool set mid-task without redefining the persona, and debugging "why did the researcher do that?" requires reverse-engineering prompt construction from role definitions.

## The Core Insight: Step Over Role

The primitive in any AI workflow is an **agentic loop** — an LLM with tools, iterating until the task is done. The only decisions are:

1. What context to load
2. What tools to provide
3. What instructions to give

These three things should be properties of **the step the work is in**, not a persistent actor. A "code review" step needs read-only file access, a diff, and review guidelines. A "planning" step needs search tools, a spec, and planning instructions. Same LLM, different configuration. The step IS the role.

This is what Stepwise does. Each step declares its executor (script, LLM, agent, or human), its inputs, and its outputs. The engine handles the rest — scheduling, dependency resolution, retries, cost tracking, observability.

```yaml
steps:
  research:
    executor: agent
    model: anthropic/claude-opus-4
    prompt: "Research $topic and produce structured findings"
    outputs: [findings, sources]
    inputs:
      topic: $job.topic

  draft:
    executor: llm
    model: anthropic/claude-sonnet-4
    prompt: "Write a report based on these findings: $findings"
    outputs: [content, word_count]
    inputs:
      findings: research.findings

  review:
    executor: human
    prompt: "Review this draft. Approve or request revisions."
    outputs: [decision, feedback]
    inputs:
      content: draft.content
```

No roles. No personas. No team definitions. Just steps with typed inputs and outputs, connected by data flow.

## What's Wrong With Existing Tools

### Agent Frameworks (CrewAI, AutoGen, OpenAI Agents SDK)

These solve agent *construction* — how to build a single agent or hand off between agents. They don't solve agent *orchestration* — how to coordinate multi-step work with quality gates, cost controls, persistence, and human oversight.

- **No durable execution.** If the process crashes mid-workflow, you start over. There's no persistent state to resume from.
- **No cost controls.** A runaway agent can burn through API credits with no guardrails. Stepwise enforces per-step cost and duration limits.
- **No human gates.** Most frameworks treat human input as an afterthought. Stepwise treats human steps as first-class — same contract as any other executor.
- **No observability.** When a 5-step agent pipeline produces wrong output, where did it go wrong? Stepwise logs every transition, every input/output, every attempt. The `--report` flag generates a self-contained HTML trace.

### Workflow Engines (Temporal, Prefect, Airflow)

These solve durable execution and scheduling — but they're infrastructure. Temporal requires a server, workers, and persistent storage. Prefect needs a control plane. These are the right tools for distributed production systems with teams of engineers.

Stepwise is for a different use case: a single developer or small team who wants to orchestrate AI workflows on their machine. SQLite, not Postgres. One process, not a cluster. Install with `pip`, not Helm charts.

### State Machine Libraries (LangGraph)

LangGraph is the closest architectural cousin — explicit graph topology, conditional edges, typed state. But:

- **Embedded, not standalone.** LangGraph is a Python library you wire into your app. Stepwise is an engine with its own CLI, web UI, and persistence. You author YAML, run `stepwise run`, and get results.
- **State corruption.** Shared mutable state across nodes leads to race conditions in concurrent execution. Stepwise steps are pure functions — inputs in, outputs out, no shared state.
- **No process concept.** LangGraph models a single execution graph. Stepwise models *jobs* that can spawn sub-jobs, each running their own workflow. Project workflows decompose into process workflows. Recurse to any depth.

## Design Principles

1. **Steps are pure functions.** Inputs in, outputs out. No shared mutable state. This makes retry, parallelism, and observability trivial.

2. **Deterministic orchestration, non-deterministic execution.** The engine's control plane is explicit and reproducible. The AI lives in the data plane — inside executors. You can reason about flow structure without reasoning about LLM behavior.

3. **Human gates over human management.** People approve, redirect, and provide judgment at key decision points. They don't micromanage every step. The engine handles the plumbing.

4. **Halt on failure, inspect, rerun.** When something breaks, the job stops. You look at the logs, fix the issue, and rerun the failed step. No hidden retry logic masking bugs.

5. **Observable by default.** Every state transition, every input/output handoff, every cost event is logged. The `--report` flag renders it all as an interactive HTML document.

6. **Single machine, zero infrastructure.** SQLite persistence. One Python process. Install with `pip install stepwise`. No servers, no workers, no cloud accounts required.

## Who It's For

- **Developers building AI pipelines** who want structure without framework lock-in
- **Solo builders and small teams** who need orchestration without infrastructure
- **Anyone mixing AI with human judgment** — content pipelines, code review, research workflows, anything where quality gates matter
- **People who think in processes** — if you can describe your workflow as a series of steps with inputs and outputs, Stepwise runs it

## What It's Not

- Not a hosted platform — it runs on your machine
- Not an agent framework — it orchestrates agents, it doesn't build them
- Not enterprise infrastructure — if you need distributed workers and compliance controls, look at Temporal
- Not a drag-and-drop builder — it's YAML-first, designed for people who read and write config files
