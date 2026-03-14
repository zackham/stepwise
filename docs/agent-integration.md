# Agent Integration

> **TL;DR:** `stepwise run flow.yaml --wait --var key=value` → JSON on stdout. `stepwise agent-help --update CLAUDE.md` to teach your agent about available flows. No MCP, no background services.

How to make your AI agent call Stepwise flows as tools. This guide covers the complete workflow — from discovery to execution to handling every response shape.

## Overview

Stepwise flows are callable via CLI. No MCP servers, no protocol layers, no background services required. Your agent runs a bash command and gets JSON back.

```bash
stepwise run review.flow.yaml --wait --var repo="/path/to/repo" --var branch="feature-x"
# → {"status": "completed", "job_id": "job-...", "outputs": [{...}], ...}
```

The design prioritizes reliability for non-human callers: stdout purity, actionable errors, explicit exit codes, and partial outputs on failure.

## 1. Generate Instructions for Your Agent

The fastest way to teach your agent about available flows:

```bash
# Print instructions to stdout
stepwise agent-help

# Insert into CLAUDE.md (or any instructions file)
stepwise agent-help --update CLAUDE.md
```

This scans your project for `.flow.yaml` files and generates a markdown block with:
- Per-flow entries (inputs, outputs, human steps, run command)
- Expected output shapes for every terminal state
- CLI quick reference
- Exit codes

The `--update` flag finds `<!-- stepwise-agent-help -->` / `<!-- /stepwise-agent-help -->` markers and replaces just that section. Run it again after adding flows — it's idempotent.

```bash
# Scan a specific directory instead of the project root
stepwise agent-help --flows-dir ./workflows --update CLAUDE.md
```

## 1b. From Claude Code

If you're using Claude Code, the fastest setup is:

```bash
# Add flow instructions to your project
stepwise agent-help --update CLAUDE.md

# Claude Code now knows how to call your flows
# It will use --wait for blocking calls, --async for background work
```

Claude Code reads `CLAUDE.md` automatically. After running `agent-help --update`, Claude can discover and call your flows without additional prompting. It will:
- Use `stepwise schema <flow>` to check inputs before calling
- Use `--wait` for flows it needs results from
- Use `--timeout` for flows with human steps
- Handle errors based on exit codes

## 2. Discover What a Flow Needs

Before calling a flow, inspect its contract:

```bash
stepwise schema council
```

```json
{
  "name": "council",
  "description": "Ask multiple frontier models and synthesize responses",
  "inputs": ["question"],
  "outputs": ["synthesis", "model_responses"],
  "humanSteps": []
}
```

Key fields:
- **inputs** — required `--var` flags. If empty, the flow needs no inputs.
- **outputs** — fields in the terminal step artifacts. This is what you get back on success.
- **humanSteps** — steps that will suspend and wait for human input. If non-empty, use `--timeout` with `--wait` to avoid blocking forever.

## 3. Call a Flow (Blocking)

The primary pattern — run the flow and wait for the result:

```bash
stepwise run council --wait --var question="Should we use Postgres?"
```

**Stdout purity**: `--wait` prints exactly one JSON object to stdout. Nothing else. All logging goes to stderr. You can safely parse stdout as JSON.

```bash
# Suppress stderr too if you only want the JSON
result=$(stepwise run flow.yaml --wait --var k=v 2>/dev/null)
```

### Passing inputs

```bash
# Inline (repeatable)
stepwise run flow.yaml --wait --var topic="caching" --var depth="3"

# From a file (avoids shell escaping — good for long text)
stepwise run flow.yaml --wait --var-file spec=spec.md --var-file context=notes.txt

# From a YAML/JSON file (all variables at once)
stepwise run flow.yaml --wait --vars-file inputs.yaml
```

`--var-file` reads the file contents as the variable value. Use it when the input is multiline or contains special characters.

## 4. Handle Every Response Shape

### Success (exit code 0)

```json
{
  "status": "completed",
  "job_id": "job-a1b2c3d4",
  "outputs": [{"synthesis": "Use Postgres because...", "model_responses": [...]}],
  "cost_usd": 0.052,
  "duration_seconds": 45.2
}
```

`outputs` is an array of terminal step artifacts. Most flows have one terminal step, so `outputs[0]` is usually what you want.

### Failure (exit code 1)

```json
{
  "status": "failed",
  "job_id": "job-a1b2c3d4",
  "error": "Step 'test' failed: exit code 1",
  "failed_step": "test",
  "completed_outputs": [{"build_artifact": "..."}],
  "cost_usd": 0.012,
  "duration_seconds": 12.8
}
```

`completed_outputs` contains artifacts from steps that finished before the failure. Useful for debugging or partial recovery.

### Input error (exit code 2)

```json
{
  "status": "error",
  "error": "Missing required input 'question'. Usage: --var question=\"...\""
}
```

Error messages are actionable — they tell you exactly which `--var` flags to add.

### Timeout (exit code 3)

```json
{
  "status": "timeout",
  "job_id": "job-a1b2c3d4",
  "timeout_seconds": 300,
  "suspended_at_step": "approve"
}
```

The job is still alive. You can fulfill suspended steps (see section 6) or retrieve it later with `stepwise output`.

### Cancelled (exit code 4)

```json
{
  "status": "cancelled",
  "job_id": "job-a1b2c3d4"
}
```

## 5. Fire-and-Forget (Async)

For long-running flows or when you don't want to block:

```bash
# Start the flow — returns immediately
stepwise run deploy.flow.yaml --async --var repo="/path" --var branch="main"
# → {"job_id": "job-e5f6g7h8", "status": "running"}
```

This spawns a detached background process. No `stepwise serve` required.

```bash
# Check progress
stepwise status job-e5f6g7h8

# Retrieve outputs when done
stepwise output job-e5f6g7h8
# → {"status": "completed", "outputs": [...]}

# Get full details (per-step outputs, cost, event count)
stepwise output job-e5f6g7h8 --scope full
```

Typical agent loop:

```python
import subprocess, json, time

# Start
result = json.loads(subprocess.check_output([
    "stepwise", "run", "flow.yaml", "--async", "--var", "k=v"
]))
job_id = result["job_id"]

# Poll
while True:
    output = json.loads(subprocess.check_output([
        "stepwise", "output", job_id
    ]))
    if output["status"] in ("completed", "failed", "cancelled"):
        break
    time.sleep(5)
```

## 6. Handle Human Steps

Some flows have steps that pause for human input. When you hit one:

### With --wait and --timeout

```bash
stepwise run review.flow.yaml --wait --timeout 300 --var content="Draft text"
```

If the flow reaches a human step and the timeout fires, you get:

```json
{
  "status": "timeout",
  "job_id": "job-a1b2c3d4",
  "timeout_seconds": 300,
  "suspended_at_step": "review"
}
```

### Find the suspended step

```bash
stepwise output job-a1b2c3d4
```

```json
{
  "status": "running",
  "outputs": [],
  "suspended_steps": [
    {
      "run_id": "run-x1y2z3w4",
      "step": "review",
      "prompt": "Review this draft. Approve or request revisions.",
      "fields": ["decision", "feedback"]
    }
  ]
}
```

The `fields` array tells you exactly what the human step expects.

### Fulfill the step

```bash
stepwise fulfill run-x1y2z3w4 '{"decision": "approve", "feedback": "Looks good"}'

# Or pipe from stdin (useful for large payloads)
echo '{"decision": "approve"}' | stepwise fulfill run-x1y2z3w4 --stdin

# Or use '-' to read from stdin
cat response.json | stepwise fulfill run-x1y2z3w4 -
```

After fulfillment, the flow continues. If you're still blocking with `--wait`, it resumes automatically and returns the final result.

### Full human-step lifecycle

```bash
# 1. Start with timeout
result=$(stepwise run flow.yaml --wait --timeout 60 --var k=v 2>/dev/null)

# 2. If timeout, get suspended step details
if [ $? -eq 3 ]; then
    job_id=$(echo "$result" | jq -r .job_id)
    output=$(stepwise output "$job_id" 2>/dev/null)
    run_id=$(echo "$output" | jq -r '.suspended_steps[0].run_id')

    # 3. Fulfill (agent decides, or prompts user)
    stepwise fulfill "$run_id" '{"approved": true, "reason": "auto-approved"}'

    # 4. Get final result
    stepwise output "$job_id"
fi
```

## 7. Exit Code Reference

| Code | Meaning | When |
|------|---------|------|
| `0` | Success | Flow completed |
| `1` | Failed | A step errored |
| `2` | Input error | Missing/invalid `--var`, bad file path |
| `3` | Timeout | `--timeout` exceeded (job still alive) |
| `4` | Cancelled | Job was cancelled |

## 8. Common Patterns

### Retry on failure

```bash
for attempt in 1 2 3; do
    result=$(stepwise run flow.yaml --wait --var k=v 2>/dev/null)
    if [ $? -eq 0 ]; then break; fi
    echo "Attempt $attempt failed, retrying..." >&2
done
```

### Chain flows

```bash
# Flow 1: research
research=$(stepwise run research.flow.yaml --wait --var topic="caching" 2>/dev/null)
findings=$(echo "$research" | jq -r '.outputs[0].findings')

# Flow 2: write report using research output (pass via temp file)
echo "$findings" > /tmp/findings.txt
stepwise run report.flow.yaml --wait --var-file findings=/tmp/findings.txt
```

### Conditional on human steps

```bash
# Check schema first — if human steps exist, use timeout
schema=$(stepwise schema flow.yaml)
has_human=$(echo "$schema" | jq '.humanSteps | length')

if [ "$has_human" -gt 0 ]; then
    stepwise run flow.yaml --wait --timeout 300 --var k=v
else
    stepwise run flow.yaml --wait --var k=v
fi
```

## Troubleshooting

**"Missing required input"** — The flow needs `--var` flags. Run `stepwise schema flow.yaml` to see what inputs are required.

**Timeout on human step** — The flow is waiting for human input. Use `stepwise output <job-id>` to see the suspended step, then `stepwise fulfill` to provide the input.

**Empty outputs array** — The job may still be running. Check `stepwise status <job-id>`. If it's completed but outputs are empty, the terminal step may not have produced output fields.

**"Job not found"** — Job IDs are scoped to the `.stepwise/` project directory. Make sure you're running commands from the same project, or use `--project-dir`.
