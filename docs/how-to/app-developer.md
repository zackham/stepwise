# Stepwise + Your Application

> Last verified: 2026-03-21

Embed Stepwise flows in any application — Python scripts, web backends, CI pipelines, or custom tooling. Three integration surfaces: CLI subprocess, HTTP API, and webhooks.

## What You'll Build

A working integration where your application runs Stepwise flows, parses structured JSON output, handles errors and suspensions, and optionally listens for webhook notifications on job events. You'll start with the simplest approach (CLI subprocess) and scale up to the HTTP API if needed.

## Prerequisites

- **Stepwise** >= 1.0.0 (`stepwise --version` to check; [install](../quickstart.md) if needed)
- **Python 3.10+**, **Node.js**, or any language that can spawn subprocesses
- **OpenRouter API key** (optional — only needed for flows with LLM/agent steps)

## Quick Start

### 1. Discover available flows

```bash
stepwise agent-help --format json
```

Returns a JSON array of all available flows with their inputs, outputs, and external steps. Parse this to build a catalog for your app.

For a single flow's contract:

```bash
stepwise schema my-flow
```

### 2. Run a flow from Python

```python
import subprocess
import json

result = subprocess.run(
    [
        "stepwise", "run", "code-review",
        "--wait", "--output", "json",
        "--input", "repo_path=/path/to/repo",
        "--input", "branch=main",
    ],
    capture_output=True,
    text=True,
)

if result.returncode == 0:
    data = json.loads(result.stdout)
    recommendations = data["outputs"][0]["recommendations"]
    print(f"Review complete: {recommendations}")
elif result.returncode == 5:
    data = json.loads(result.stdout)
    print(f"Waiting for human input: {data['suspended_steps']}")
else:
    data = json.loads(result.stdout)
    print(f"Error: {data.get('error', 'unknown')}")
```

That's it. `stepwise run --wait --output json` gives you clean JSON on stdout with a meaningful exit code.

## Integration Surfaces

### CLI subprocess (recommended starting point)

The simplest integration. Spawn `stepwise run --wait` as a subprocess and parse stdout.

**Advantages:** No server required, no dependencies beyond the `stepwise` binary, works from any language.

**Complete Python example:**

```python
import subprocess
import json
from typing import Any


def run_flow(
    flow: str,
    variables: dict[str, str],
    timeout: int = 300,
) -> dict[str, Any]:
    """Run a Stepwise flow and return parsed output."""
    cmd = ["stepwise", "run", flow, "--wait", "--output", "json"]
    for key, value in variables.items():
        cmd.extend(["--input", f"{key}={value}"])
    if timeout:
        cmd.extend(["--timeout", str(timeout)])

    result = subprocess.run(cmd, capture_output=True, text=True)
    output = json.loads(result.stdout) if result.stdout.strip() else {}

    return {
        "exit_code": result.returncode,
        "data": output,
    }


def fulfill_step(run_id: str, payload: dict, wait: bool = True) -> dict[str, Any]:
    """Fulfill a suspended external step."""
    cmd = ["stepwise", "fulfill", run_id, json.dumps(payload)]
    if wait:
        cmd.append("--wait")

    result = subprocess.run(cmd, capture_output=True, text=True)
    output = json.loads(result.stdout) if result.stdout.strip() else {}

    return {
        "exit_code": result.returncode,
        "data": output,
    }


# Usage
result = run_flow("council", {"question": "Should we use GraphQL?"})

if result["exit_code"] == 0:
    synthesis = result["data"]["outputs"][0]["synthesis"]
    print(f"Answer: {synthesis}")

elif result["exit_code"] == 5:
    # Flow needs human input
    for step in result["data"]["suspended_steps"]:
        print(f"Step '{step['step']}' asks: {step['prompt']}")
        # Collect input from your UI, then:
        fulfill_result = fulfill_step(step["run_id"], {"approved": True})
```

### Async mode with polling

For long-running flows, avoid blocking your application:

```python
import subprocess
import json
import time


def run_flow_async(flow: str, variables: dict[str, str]) -> str:
    """Start a flow asynchronously. Returns job_id."""
    cmd = ["stepwise", "run", flow, "--async"]
    for key, value in variables.items():
        cmd.extend(["--input", f"{key}={value}"])

    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    return data["job_id"]


def get_job_status(job_id: str) -> dict:
    """Check job status."""
    result = subprocess.run(
        ["stepwise", "status", job_id, "--output", "json"],
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def get_job_output(job_id: str) -> dict:
    """Retrieve job outputs."""
    result = subprocess.run(
        ["stepwise", "output", job_id],
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


# Usage
job_id = run_flow_async("long-analysis", {"dataset": "large.csv"})

# Poll until done (in production, use a task queue or webhook instead)
while True:
    status = get_job_status(job_id)
    if status["status"] in ("completed", "failed", "cancelled"):
        break
    time.sleep(5)

if status["status"] == "completed":
    output = get_job_output(job_id)
    print(output)
```

### Webhooks for event-driven integration

Instead of polling, receive push notifications when jobs complete, suspend, or fail:

```bash
stepwise run my-flow --async \
  --input input="data" \
  --notify "https://your-app.com/webhooks/stepwise" \
  --notify-context '{"request_id": "req-123"}'
```

Your webhook endpoint receives POST requests with job event payloads. The `--notify-context` JSON is included in every webhook so you can correlate events back to your application's request.

You can also configure webhooks at the project level using [hooks](../cli.md) in `.stepwise/hooks/`:

```bash
# .stepwise/hooks/on-complete
#!/bin/bash
curl -X POST https://your-app.com/webhooks/stepwise \
  -H "Content-Type: application/json" \
  -d "{\"event\": \"complete\", \"job_id\": \"$STEPWISE_JOB_ID\"}"
```

### HTTP API (for server mode)

When running `stepwise server start`, a REST API is available at port 8340. This gives you direct access without spawning subprocesses:

```python
import httpx

BASE = "http://localhost:8340/api"

# Create and run a job
resp = httpx.post(f"{BASE}/jobs", json={
    "flow": "council",
    "inputs": {"question": "Should we use GraphQL?"},
})
job = resp.json()
job_id = job["id"]

# Check status
resp = httpx.get(f"{BASE}/jobs/{job_id}")
status = resp.json()

# Fulfill a suspended step
resp = httpx.post(f"{BASE}/jobs/{job_id}/fulfill", json={
    "run_id": "run-abc123",
    "payload": {"approved": True},
})
```

See the [API Reference](../api.md) for complete endpoint documentation. The server also supports WebSocket connections at `/ws` for real-time job updates.

## Exit Code Reference

All `stepwise run --wait` calls return these exit codes:

| Exit code | Status | Meaning |
|---|---|---|
| 0 | `completed` | Success — read `outputs[0]` |
| 1 | `failed` | Step failed — read `error`, `failed_step`, `completed_outputs` |
| 2 | `error` | Invalid input — read `error` for which `--input` to add |
| 3 | `timeout` | Timed out — job still alive, use `stepwise output` to check |
| 4 | `cancelled` | Job cancelled |
| 5 | `suspended` | Waiting for external input — read `suspended_steps` |

## JSON Output Structure

### Success (exit code 0)

```json
{
  "status": "completed",
  "job_id": "job-a1b2c3d4",
  "outputs": [{"field1": "value1", "field2": "value2"}],
  "cost_usd": 0.052,
  "duration_seconds": 45.2
}
```

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

`completed_outputs` contains artifacts from steps that finished before the failure — useful for partial recovery.

### Suspended (exit code 5)

```json
{
  "status": "suspended",
  "job_id": "job-x1y2z3",
  "suspended_steps": [
    {
      "run_id": "run-abc123",
      "step": "approve",
      "prompt": "Approve this deployment?",
      "fields": [{"name": "approved", "type": "bool"}]
    }
  ]
}
```

## Passing Large Inputs

For inputs that are multiline or contain special characters, use `--input KEY=@path`:

```bash
stepwise run analyze --wait --input document=@report.md --input format="summary" --output json
```

This reads the file contents as the variable value. For multiple variables from a single file:

```bash
# vars.yaml
question: "Should we migrate to microservices?"
context: "We currently have a monolith serving 10k requests/sec..."
```

```bash
stepwise run council --wait --vars-file vars.yaml --output json
```

## Troubleshooting

**"command not found: stepwise"**
Stepwise installs via `uv tool`. Ensure `~/.local/bin` is on your PATH, or use the full path: `~/.local/bin/stepwise`.

**Subprocess hangs indefinitely**
Always pass `--timeout` when calling from application code. Without it, `--wait` blocks until completion with no time limit.

**JSON parse errors on stdout**
Use `--output json` to ensure clean JSON. Without it, terminal formatting may be mixed in. Also redirect stderr: `capture_output=True` in Python captures both streams separately.

**Server connection refused**
If using the HTTP API, ensure the server is running: `stepwise server status`. Start it with `stepwise server start --detach`.

**"No .stepwise/ directory"**
Run `stepwise init` in your project root to create the project structure. Or pass `--project-dir` to point to an existing project.

## Next Steps

- [API Reference](../api.md) — complete HTTP API documentation for server mode
- [Agent Integration Reference](../agent-integration.md) — the full flows-as-tools protocol
- [CLI Reference](../cli.md) — all commands and flags
- [Stepwise + Claude Code](claude-code.md) — agent-specific integration guide
- [Stepwise + Codex / OpenCode](codex-opencode.md) — cross-agent portability
