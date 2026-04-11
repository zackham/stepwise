# Agent Integration

How AI agents (Claude Code, Codex, etc.) call Stepwise flows as tools — discovery, execution, output handling, and error recovery.

---

## Overview

Stepwise flows are callable via CLI. No MCP servers, no protocol layers, no background services required. Your agent runs a bash command and gets JSON back.

```bash
stepwise run review.flow.yaml --wait --input repo="/path/to/repo" --input branch="feature-x"
# → {"status": "completed", "job_id": "job-...", "outputs": [{...}], ...}
```

`--wait` guarantees stdout purity: exactly one JSON object on stdout, all logging on stderr. Agents can parse stdout directly.

---

## 1. Generate Instructions for Your Agent

The fastest way to teach your agent about available flows:

```bash
# Print instructions to stdout
stepwise agent-help

# Insert into CLAUDE.md (or any instructions file)
stepwise agent-help --update CLAUDE.md
```

This scans your project for `.flow.yaml` files and generates a markdown block with:
- Per-flow entries (inputs, outputs, external steps, run command)
- Expected output shapes for every terminal state
- CLI quick reference and exit codes

The `--update` flag finds `<!-- stepwise-agent-help -->` / `<!-- /stepwise-agent-help -->` markers and replaces just that section. Idempotent — run it again after adding flows.

```bash
# Scan a specific directory instead of the project root
stepwise agent-help --flows-dir ./workflows --update CLAUDE.md
```

### Claude Code Setup

```bash
# Add flow instructions to your project
stepwise agent-help --update CLAUDE.md
```

Claude Code reads `CLAUDE.md` automatically. After running `agent-help --update`, Claude can discover and call your flows without additional prompting. It will use `stepwise schema <flow>` to check inputs, `--wait` for blocking calls, and handle errors based on exit codes.

---

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
  "externalSteps": []
}
```

Key fields:
- **inputs** — required `--input` flags. If empty, the flow needs no inputs.
- **outputs** — fields in the terminal step artifacts. This is what you get back on success.
- **externalSteps** — steps that will suspend and wait for external input. If non-empty, the flow will exit with code 5 (suspended) when it reaches one. See [handling external steps](#6-handle-external-steps) below.

---

## 3. Call a Flow (Blocking)

The primary pattern — run the flow and wait for the result:

```bash
stepwise run council --wait --input question="Should we use Postgres?"
```

**Stdout purity**: `--wait` prints exactly one JSON object to stdout. Nothing else. All logging goes to stderr. You can safely parse stdout as JSON.

```bash
# Suppress stderr too if you only want the JSON
result=$(stepwise run flow.yaml --wait --input k=v 2>/dev/null)
```

### Passing inputs

```bash
# Inline (repeatable)
stepwise run flow.yaml --wait --input topic="caching" --input depth="3"

# From a file (avoids shell escaping — good for long text)
stepwise run flow.yaml --wait --input spec=@spec.md --input context=@notes.txt

# From a YAML/JSON file (all variables at once)
stepwise run flow.yaml --wait --vars-file inputs.yaml
```

`--input KEY=@path` reads the file contents as the variable value. Use it when the input is multiline or contains special characters.

---

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
  "error": "Missing required input 'question'. Usage: --input question=\"...\""
}
```

Error messages are actionable — they tell you exactly which `--input` flags to add.

### Cancelled (exit code 4)

```json
{
  "status": "cancelled",
  "job_id": "job-a1b2c3d4"
}
```

### Suspended (exit code 5)

```json
{
  "status": "suspended",
  "job_id": "job-a1b2c3d4",
  "completed_steps": ["plan", "implement"],
  "suspended_step": "review"
}
```

The flow reached an `executor: external` step and is waiting for input. See [handling external steps](#6-handle-external-steps).

---

## 5. Fire-and-Forget (Async)

For long-running flows or when you don't want to block:

```bash
# Start the flow — returns immediately
stepwise run deploy.flow.yaml --async --input repo="/path" --input branch="main"
# → {"job_id": "job-e5f6g7h8", "status": "running"}
```

```bash
# Check progress
stepwise status job-e5f6g7h8

# Retrieve outputs when done
stepwise output job-e5f6g7h8
# → {"status": "completed", "outputs": [...]}

# Get full details (per-step outputs, cost, event count)
stepwise output job-e5f6g7h8 --scope full
```

Typical agent polling loop:

```python
import subprocess, json, time

# Start
result = json.loads(subprocess.check_output([
    "stepwise", "run", "flow.yaml", "--async", "--input", "k=v"
]))
job_id = result["job_id"]

# Poll
while True:
    output = json.loads(subprocess.check_output([
        "stepwise", "output", job_id
    ]))
    if output["status"] in ("completed", "failed", "cancelled", "suspended"):
        break
    time.sleep(5)
```

---

## 6. Handle External Steps

Some flows have steps with `executor: external` that pause for input — human approval, external API responses, or agent decisions.

### Detecting a suspended flow

When `--wait` returns exit code 5 (suspended), or when polling shows `"status": "suspended"`:

```bash
# List suspended steps
stepwise list --suspended

# Or check a specific job
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

The `fields` array tells you exactly what the external step expects.

### Fulfill the step

```bash
stepwise fulfill run-x1y2z3w4 '{"decision": "approve", "feedback": "Looks good"}'

# Or pipe from stdin (useful for large payloads)
echo '{"decision": "approve"}' | stepwise fulfill run-x1y2z3w4 --stdin

# Or use '-' to read from stdin
cat response.json | stepwise fulfill run-x1y2z3w4 -
```

After fulfillment, the flow continues. Use `stepwise wait <job-id>` to block until the next suspension or completion.

### Full external-step lifecycle

```bash
# 1. Start the flow
result=$(stepwise run flow.yaml --wait --input k=v 2>/dev/null)
exit_code=$?

# 2. If suspended, get the step details
if [ "$exit_code" -eq 5 ]; then
    job_id=$(echo "$result" | jq -r .job_id)
    output=$(stepwise output "$job_id" 2>/dev/null)
    run_id=$(echo "$output" | jq -r '.suspended_steps[0].run_id')

    # 3. Fulfill (agent decides, or prompts user)
    stepwise fulfill "$run_id" '{"approved": true, "reason": "auto-approved"}'

    # 4. Wait for completion
    stepwise wait "$job_id"
fi
```

---

## 7. Exit Code Reference

| Code | Meaning | When |
|------|---------|------|
| `0` | Success | Flow completed |
| `1` | Failed | A step errored |
| `2` | Usage error | Missing/invalid `--input`, bad file path |
| `4` | Cancelled | Job was cancelled |
| `5` | Suspended | Flow is waiting for external input |

---

## 8. Common Patterns

### Retry on failure

```bash
for attempt in 1 2 3; do
    result=$(stepwise run flow.yaml --wait --input k=v 2>/dev/null)
    if [ $? -eq 0 ]; then break; fi
    echo "Attempt $attempt failed, retrying..." >&2
done
```

### Chain flows

```bash
# Flow 1: research
research=$(stepwise run research.flow.yaml --wait --input topic="caching" 2>/dev/null)
findings=$(echo "$research" | jq -r '.outputs[0].findings')

# Flow 2: write report using research output (pass via temp file)
echo "$findings" > /tmp/findings.txt
stepwise run report.flow.yaml --wait --input findings=@/tmp/findings.txt
```

### Conditional on external steps

```bash
# Check schema first — if external steps exist, handle suspension
schema=$(stepwise schema flow.yaml)
has_external=$(echo "$schema" | jq '.externalSteps | length')

if [ "$has_external" -gt 0 ]; then
    echo "Flow has external steps — may suspend for input" >&2
fi

stepwise run flow.yaml --wait --input k=v
```

---

## Agent Security: Containment

Agent steps can run inside hardware-isolated microVMs, bounding the blast radius of autonomous sessions. When containment is enabled, each agent runs in a separate VM with access limited to explicitly declared filesystem paths, credentials, and network endpoints. This is especially relevant for agent-called flows where the calling agent delegates work to sub-agents — containment ensures that a compromised sub-agent can't escape its declared scope.

Enable containment via the CLI flag (`--containment cloud-hypervisor`), per-step in the flow YAML, or globally in agent settings. See the [Containment guide](containment.md) for setup, architecture, and the full security model.

---

## Troubleshooting

**"Missing required input"** — Run `stepwise schema flow.yaml` to see what inputs are required.

**Suspended on external step** — The flow is waiting for external input. Use `stepwise output <job-id>` to see the suspended step, then `stepwise fulfill` to provide the input.

**Empty outputs array** — The job may still be running. Check `stepwise status <job-id>`. If it's completed but outputs are empty, the terminal step may not have produced output fields.

**"Job not found"** — Job IDs are scoped to the `.stepwise/` project directory. Make sure you're running commands from the same project, or use `--project-dir`.

---

See [Writing Flows](writing-flows.md) for YAML syntax. See [CLI Reference](cli.md) for the full command list.
