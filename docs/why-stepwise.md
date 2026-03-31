# Why Stepwise

## The felt problem

You ask an agent to do something that takes 20 minutes. While it runs, you do something else. When you come back: did it work? Did it go off the rails at step 3? Did it burn $14 in API credits retrying a hallucinated command?

You don't know. So you read the logs. You re-run parts of it. You check the output manually. The 20 minutes of agent time saved you 20 minutes of verification time. Net gain: zero.

Now multiply that by a flow that takes 3 hours. Or one that runs overnight. Or one where a human needs to approve step 7 before step 8 can proceed. The agent is capable. But you can't *trust* the run, because the run isn't observable, isn't recoverable, and isn't auditable.

This is the gap Stepwise fills. Not the intelligence — the harness.

## The harness, not the intelligence

The intelligence commoditizes. Claude, GPT, Gemini, Codex — they get better every quarter. The models are not the bottleneck.

The bottleneck is packaging AI work into something you can delegate, observe, gate, and audit. A brisket doesn't need a better cow. It needs a smoker with a reliable thermometer, predictable airflow, and a way to check the bark without opening the door every 20 minutes.

Stepwise is the smoker. Your agents are the brisket.

Concretely, this means:

- **Observable runs.** Every step records inputs, outputs, timing, cost, and attempt count. Not as optional logging — as the execution model. Downstream steps depend on this data.
- **Human gates.** External steps pause the flow and wait for judgment. In the web UI, CLI, or via API. The human sees full context of what happened before the gate.
- **Crash recovery.** Everything persists to SQLite. Kill the process, restart, jobs resume from the last completed step. Orphaned jobs get adopted automatically.
- **Audit trail.** Every state transition is an event. The `--report` flag renders a self-contained HTML trace. You can see exactly what went wrong at step 4, with the exact inputs it received.
- **Cost controls.** Per-step limits on dollars, wall-clock time, and iterations. When a limit fires, the step fails cleanly and exit rules route to a fallback or human escalation.

## Step over role

Most agent frameworks model *who* does the work. CrewAI gives you "Senior Researcher" and "Technical Writer." AutoGen gives you named agents in conversations.

But an LLM doesn't *become* a Senior Researcher by reading a backstory. It gets a system prompt, a set of tools, and instructions. The persona is semantic sugar — and it comes with real costs: identity conflicts when you parallelize, tool rigidity, and opaque prompt construction.

Stepwise models *what* the work is. Each step declares its executor type, inputs, and outputs. A "research" step needs search tools and a topic. A "review" step needs the draft and approval criteria. Same LLM, different configuration. The step IS the role.

```yaml
steps:
  research:
    executor: agent
    prompt: "Research $topic and produce structured findings"
    outputs: [findings, sources]
    inputs:
      topic: $job.topic

  score:
    executor: llm
    model: anthropic/claude-sonnet-4
    prompt: "Score these findings 0-10 on depth and relevance: $findings"
    outputs: [score, reasoning]
    inputs:
      findings: research.findings

  review:
    executor: external
    prompt: "Score: $score. Approve or request deeper research?"
    outputs: [decision]
    inputs:
      score: score.score
```

Three steps, three executor types, zero role definitions. The agent researches because its step says to research, not because it was cast as a researcher.

## Deterministic orchestration, nondeterministic execution

The engine's control plane is explicit and reproducible. Dependencies are a DAG. Exit rules are evaluated expressions. Parallel execution follows from graph topology. You can reason about flow structure without reasoning about LLM behavior.

The nondeterminism lives inside executors — inside agent sessions, LLM calls, and human decisions. The engine doesn't care what an executor does internally. It cares about the contract: declared inputs in, declared outputs out, within stated limits.

This separation is what makes the system trustworthy. When you look at a flow definition, you see the structure. When you look at a step run, you see the content. They don't bleed into each other.

## What if AI work lived in a system instead of a vibe?

Right now, most AI delegation looks like: copy context into a chat window, prompt carefully, hope for the best, manually verify. The "workflow" lives in your head and your clipboard.

Stepwise makes the workflow explicit:

- **Declared, not coded.** Flows are YAML files. Version-controlled, diffable, shareable, runnable on any machine. Non-programmers can read and review them.
- **Mixed executors that compose.** A shell script fetches data. An LLM scores it. An agent implements the fix. A poll waits for CI. A human approves the deploy. One DAG, zero glue code.
- **Loops with safety caps.** Exit rules fire after each step. If quality is too low, loop back. If attempts hit the ceiling, escalate to a human. Declared in YAML, enforced by the engine.
- **External fulfillment as a primitive.** The `external` executor pauses a step and waits for input from *anyone* — a human, a webhook, another agent. It's the same execution model as every other step, not a bolt-on.

## Design principles

1. **Steps are pure functions.** Inputs in, outputs out. No shared mutable state. This is what makes retry, parallelism, and observability clean.

2. **Deterministic orchestration, nondeterministic execution.** The engine is explicit and reproducible. The AI lives inside executors.

3. **Human gates over human management.** People approve, redirect, and judge at key points. They don't micromanage every step.

4. **Halt on failure, inspect, rerun.** When something breaks, the job stops. You look at the data, fix the issue, rerun the failed step. No hidden retry logic masking bugs.

5. **Observable by default.** Every transition, every handoff, every cost event is logged — because downstream steps depend on it.

6. **Single machine, zero infrastructure.** SQLite. One process. `curl | sh` to install. No servers, no workers, no cloud accounts.

## Who it's for

- **The founder with one ugly process held together by duct tape.** You don't think "I need workflow orchestration." You think "I need a better spreadsheet." Stepwise is the upgrade.
- **Developers building AI pipelines** who want structure without framework lock-in.
- **Solo builders and small teams** who need orchestration without infrastructure.
- **Anyone mixing AI with human judgment** — content pipelines, code review, research, anything where quality gates matter.

## What it's not

- Not a hosted platform — it runs on your machine
- Not an agent framework — it orchestrates agents, it doesn't build them
- Not enterprise infrastructure — if you need distributed workers and compliance controls, look at Temporal
- Not a drag-and-drop builder — it's YAML-first, designed for people who read and write config files
