# Stepwise + Codex / OpenCode

> Last verified: 2026-03-21

Use Stepwise flows as structured tools from OpenAI Codex CLI or OpenCode. The same CLI interface works identically across agents — Stepwise is agent-agnostic.

## What You'll Build

Your Codex or OpenCode agent will discover available Stepwise flows, invoke them with `--wait` for blocking JSON output, handle exit codes programmatically, and fulfill external fulfillment steps. The same flow file works whether called from Codex, Claude Code, or any other agent.

## Prerequisites

- **Stepwise** >= 1.0.0 (`stepwise --version` to check; [install](../quickstart.md) if needed)
- **Codex CLI** or **OpenCode** installed
- **OpenRouter API key** (optional — only needed for flows that use LLM/agent steps)

## Quick Start

### 1. Generate agent instructions

```bash
stepwise agent-help --format full
```

This outputs markdown describing all available flows — their inputs, outputs, external steps, and example commands. Paste this into your agent's instruction file:

- **Codex**: `AGENTS.md` or the system prompt in your Codex configuration
- **OpenCode**: `instructions.md` or equivalent configuration file

To auto-update a file in place (same as Claude Code):

```bash
stepwise agent-help --update AGENTS.md
```

This inserts or refreshes a `<!-- STEPWISE -->` block with current flow documentation.

### 2. Inspect a flow's contract

```bash
stepwise schema my-flow
```

Returns a JSON tool contract with parameter schemas, output fields, and external step definitions. The agent uses this to construct valid calls.

### 3. Run a flow

```bash
stepwise run council --wait --input question="Should we migrate to microservices?" --output json
```

Output:

```json
{
  "status": "completed",
  "job_id": "job-a1b2c3d4",
  "outputs": [{"synthesis": "Migrate incrementally because...", "model_responses": [...]}],
  "cost_usd": 0.048,
  "duration_seconds": 38.1
}
```

The agent reads `outputs[0]` for the terminal step's artifact and continues its work.

## Exit Code Handling

Every `stepwise run --wait` call returns a structured exit code. The agent should handle each:

| Exit code | Status | Meaning | What to do |
|---|---|---|---|
| 0 | `completed` | Flow finished successfully | Parse `outputs[0]` |
| 1 | `failed` | A step failed | Read `error` and `failed_step`; check `completed_outputs` for partial results |
| 2 | `error` | Invalid input | Read `error` message — it tells you which `--input` to add |
| 3 | `timeout` | Timed out (job still alive) | Use `stepwise output <job_id>` to check progress, or `stepwise fulfill` if suspended |
| 4 | `cancelled` | Job was cancelled | Retry or report to user |
| 5 | `suspended` | Waiting for external input | Read `suspended_steps` and fulfill (see below) |

Example error handling in a script context:

```bash
result=$(stepwise run my-flow --wait --input input="data" --output json 2>/dev/null)
exit_code=$?

case $exit_code in
  0) echo "Success: $(echo $result | jq -r '.outputs[0]')" ;;
  1) echo "Failed: $(echo $result | jq -r '.error')" ;;
  2) echo "Bad input: $(echo $result | jq -r '.error')" ;;
  5) echo "Needs human input — check suspended_steps" ;;
  *) echo "Unexpected: exit $exit_code" ;;
esac
```

## Common Patterns

### Synchronous delegation

Call a flow, wait for the result, act on it:

```bash
stepwise run code-review --wait --input repo_path="/path/to/repo" --output json
```

The agent treats this like a subprocess call — structured in, structured out. Works for any flow that runs to completion without external steps.

### Human-in-the-loop

When a flow suspends (exit code 5), the output contains details about what's needed:

```json
{
  "status": "suspended",
  "job_id": "job-x1y2z3",
  "suspended_steps": [
    {
      "run_id": "run-abc123",
      "step": "approve-deploy",
      "prompt": "Deploy to staging?",
      "fields": [{"name": "approved", "type": "bool"}]
    }
  ]
}
```

The agent presents this to the user, collects input, and fulfills:

```bash
stepwise fulfill run-abc123 '{"approved": true}' --wait
```

The `--wait` flag blocks until the next suspension or completion.

### Async with polling

For long-running flows, use async mode to avoid blocking:

```bash
# Start the flow — returns immediately
stepwise run long-analysis --async --input dataset="large.csv"
# → {"job_id": "job-e5f6g7h8"}

# Check progress later
stepwise status job-e5f6g7h8 --output json

# Retrieve outputs when done
stepwise output job-e5f6g7h8
```

### Monitoring the suspension inbox

Check all flows waiting for human input:

```bash
stepwise list --suspended --output json
```

This returns suspended steps across all active jobs — useful for agents that periodically check for work.

## Configuration

### Environment variables

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Required for flows with LLM/agent steps |
| `STEPWISE_DB` | Override database location (default: `.stepwise/stepwise.db`) |
| `STEPWISE_VAR_<NAME>` | Pre-set flow input variables via environment |

### Instruction file setup

After running `stepwise agent-help --update AGENTS.md`, your file will contain:

```markdown
<!-- STEPWISE:BEGIN -->
## Available Flows

### council
Run: `stepwise run council --wait --input question="..."`
Outputs: synthesis, model_responses
...
<!-- STEPWISE:END -->
```

Re-run whenever flows change to keep the agent current.

## Cross-Agent Portability

The key point: **the same flow file works from any agent.** A flow written for Claude Code works identically from Codex or OpenCode. The CLI interface is the contract:

- Same `stepwise run --wait --output json` invocation
- Same JSON output structure
- Same exit codes
- Same `stepwise fulfill` for human steps
- Same `stepwise schema` for discovery

This means teams can share flows across different agent setups. Write once, call from anywhere.

## Troubleshooting

**"Missing required input" (exit code 2)**
Run `stepwise schema <flow>` to see required inputs. The error message tells you exactly which `--input` flags to add.

**Agent doesn't know about flows**
Re-run `stepwise agent-help --update AGENTS.md` to refresh the instruction block. Make sure your agent's configuration points to the right instruction file.

**Flow runs but agent can't parse output**
Ensure you pass `--output json`. Without it, `stepwise run --wait` may include non-JSON terminal output.

**Server detection issues**
If you're running multiple jobs, start a persistent server: `stepwise server start --detach`. The CLI auto-delegates to it. Use `--local` to force standalone execution.

**Timeout with no result**
Increase the timeout: `stepwise run my-flow --wait --timeout 600 --output json`. Or switch to async mode and poll.

## Next Steps

- [Agent Integration Reference](../agent-integration.md) — full protocol for flows-as-tools
- [CLI Reference](../cli.md) — complete command documentation
- [Stepwise + Claude Code](claude-code.md) — same patterns, Claude Code-specific setup
- [Stepwise + Your Application](app-developer.md) — embedding Stepwise in custom apps
