# Stepwise + Claude Code

> Last verified: 2026-03-21

Use Stepwise flows as structured tools from Claude Code. Run multi-step workflows, handle external fulfillment steps, and get structured JSON output — all from within a Claude Code session.

## What You'll Build

By the end of this guide, your Claude Code agent will be able to discover available Stepwise flows, invoke them with structured inputs, parse their JSON output, and interactively fulfill human steps when a flow needs external input. The agent treats flows as composable tools alongside its existing skills.

## Prerequisites

- **Stepwise** >= 1.0.0 (`stepwise --version` to check; [install](../quickstart.md) if needed)
- **Claude Code** with CLAUDE.md support
- **OpenRouter API key** (optional — only needed for flows that use LLM/agent steps)

## Quick Start

### 1. Teach the agent about your flows

Run this from your project root:

```bash
stepwise agent-help --update CLAUDE.md
```

This inserts (or updates) a `<!-- STEPWISE -->` block in your CLAUDE.md with:
- Available flows and their descriptions
- Required/optional inputs for each flow
- Output fields and their types
- External steps that may need human input
- Example run commands

The agent reads this on every session start and knows what flows are available.

### 2. Discover a flow's contract

Before calling a flow, the agent can inspect its schema:

```bash
stepwise schema my-flow
```

This outputs a JSON tool contract with input parameters, output fields, and any external steps — everything the agent needs to construct a valid invocation.

### 3. Run a flow and use the result

```bash
stepwise run my-flow --wait --input question="What database should we use?" --output json
```

The `--wait` flag blocks until the flow completes (or suspends), and `--output json` ensures clean JSON on stdout. The agent parses the result and acts on it:

```json
{
  "status": "completed",
  "job_id": "job-a1b2c3d4",
  "outputs": [{"recommendation": "Use Postgres because...", "confidence": 0.92}],
  "cost_usd": 0.052,
  "duration_seconds": 45.2
}
```

`outputs` is an array of terminal step artifacts. Most flows have one terminal step, so `outputs[0]` is what the agent reads.

## Common Patterns

### Synchronous delegation

The simplest pattern: the agent calls a flow, waits for the result, and continues.

```bash
# Agent decides it needs a code review
result=$(stepwise run code-review --wait --input repo_path="/path/to/repo" --output json)

# Agent parses the JSON output and acts on it
# e.g., reads outputs[0].recommendations and applies fixes
```

The agent treats the flow like a function call — structured inputs in, structured outputs out. This works for any flow that runs to completion without external steps.

### Human-in-the-loop

When a flow has an `external` step, it suspends and waits for human input. The `--wait` output tells the agent exactly what's needed:

```bash
# Run a flow that includes a human approval step
stepwise run deploy-pipeline --wait --input env="staging" --output json
```

If the flow suspends (exit code 5):

```json
{
  "status": "suspended",
  "job_id": "job-x1y2z3",
  "suspended_steps": [
    {
      "run_id": "run-abc123",
      "step": "approve-deploy",
      "prompt": "Deploy to staging? Review the test results above.",
      "fields": [{"name": "approved", "type": "bool"}, {"name": "notes", "type": "str"}]
    }
  ]
}
```

The agent shows the prompt to the user, collects their response, and fulfills:

```bash
stepwise fulfill run-abc123 '{"approved": true, "notes": "LGTM"}' --wait
```

The `--wait` flag on `fulfill` blocks until the flow reaches the next suspension or completion, so the agent can continue handling the flow in one conversation.

### Mediated mode

For flows where the agent itself can make the decisions that external steps ask for — without prompting the human — the agent reads the suspended step's prompt and fields, reasons about the answer using its own context, and fulfills directly.

```bash
# Agent inspects what the flow is asking
stepwise list --suspended --output json

# Agent reads the prompt: "Rate the quality of this analysis (1-10)"
# Agent has context from earlier in the conversation to answer this
stepwise fulfill run-def456 '{"score": 8, "feedback": "Thorough analysis, minor gaps in error handling"}'
```

This is powerful when the agent has enough context to act as the "human" in the loop. The flow doesn't know or care whether a person or an agent fulfilled the step.

## Configuration

### Environment variables

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Required for flows with LLM/agent steps |
| `STEPWISE_DB` | Override database location (default: `.stepwise/stepwise.db`) |
| `STEPWISE_VAR_<NAME>` | Pre-set flow input variables via environment |

### CLAUDE.md setup

After running `stepwise agent-help --update CLAUDE.md`, your file will contain a block like:

```markdown
<!-- STEPWISE:BEGIN -->
## Available Flows

### council
Run: `stepwise run council --wait --input question="..."`
Outputs: synthesis, model_responses
...
<!-- STEPWISE:END -->
```

Re-run the command whenever you add or modify flows to keep the agent's knowledge current.

### Skills and flows together

Claude Code skills and Stepwise flows are complementary. A skill can trigger a flow:

```markdown
# My Skill

When asked to review code, run:
stepwise run code-review --wait --input repo_path="$PWD" --output json
```

And a flow can include agent steps that use Claude Code's tool access (file editing, shell commands, etc.). The two systems compose naturally.

## Troubleshooting

**"Missing required input" error (exit code 2)**
The flow needs inputs you didn't provide. Run `stepwise schema <flow>` to see all required inputs and their types.

**Flow hangs with no output**
You likely forgot `--wait`. Without it, `stepwise run` enters interactive terminal mode. Always use `--wait --output json` when calling from an agent.

**"No flows found" from agent-help**
Stepwise discovers flows in `flows/` directories and the registry cache. Make sure you're in a project with a `.stepwise/` directory, or specify `--flows-dir`.

**Suspended but no suspended_steps in output**
Check with `stepwise list --suspended --output json` to see all suspended steps across jobs. The step may belong to a different job.

**"Server not running" warnings**
Stepwise auto-detects a running server and delegates to it. If no server is running, it executes locally. Start one with `stepwise server start --detach` for persistent multi-job support.

## Next Steps

- [Agent Integration Reference](../agent-integration.md) — full protocol for flows-as-tools, including async mode and chaining
- [CLI Reference](../cli.md) — complete command documentation
- [Patterns](../patterns.md) — design idioms for multi-step flows
- [Quickstart](../quickstart.md) — create your first flow from scratch
