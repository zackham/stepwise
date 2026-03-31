# Extensions

How to integrate external systems with Stepwise via events, webhooks, WebSocket streams, and CLI plugins.

---

Stepwise delivers every event (step started, suspended, completed, failed; job lifecycle) through three mechanisms simultaneously. Extensions consume these events and optionally act on them — all via public APIs, never by loading code into the engine.

## Event Delivery

### 1. Shell Hooks (local, simple)

Scripts in `.stepwise/hooks/` that fire on events. Best for simple automation — deploy on completion, notify on failure.

```bash
# .stepwise/hooks/on-complete
#!/bin/sh
echo "Job $STEPWISE_JOB_ID completed"
```

**Environment variables:**
- `STEPWISE_JOB_ID` — the job ID
- `STEPWISE_EVENT` — event type (e.g., `step.suspended`)
- `STEPWISE_SESSION_ID` — from `metadata.sys.session_id` (if set)
- `STEPWISE_EVENT_FILE` — path to a temp JSON file containing the full event envelope

**Event envelope** (JSON, written to `$STEPWISE_EVENT_FILE`):
```json
{
  "event": "step.suspended",
  "job_id": "job-abc123",
  "step": "approve",
  "timestamp": "2026-03-22T08:00:00Z",
  "event_id": 42,
  "metadata": {
    "sys": { "origin": "cli", "session_id": "sess-1" },
    "app": { "project": "my-project" }
  },
  "data": { "prompt": "Review the plan...", "run_id": "run-xyz" }
}
```

Hooks also receive the event on stdin for backward compatibility.

### 2. Webhooks (remote, stateless)

Fire-and-forget HTTP POST to a URL. Configured per-job with `--notify`:

```bash
stepwise run my-flow --async \
  --notify https://my-server.com/api/stepwise-events \
  --notify-context '{"channel": "#deploys"}'
```

The POST body is the same event envelope as hooks, with `notify_context` merged in.

### 3. WebSocket Stream (remote, stateful)

Persistent connection for real-time event monitoring with filtering and replay.

```
ws://localhost:PORT/api/v1/events/stream
```

**Query parameters:**
- `?session_id=X` — only events for jobs with `metadata.sys.session_id == X`
- `?job_id=X` — events for a specific job (can specify multiple)
- `?since_event_id=N` — replay events with ID > N, then switch to live
- `?since_job_start=true` — replay from job creation (requires `job_id` or `session_id`)

**Replay behavior:**
1. Historical events are sent first
2. A boundary frame marks the transition: `{"type": "sys.replay.complete", "last_event_id": 57}`
3. Live events follow

**CLI consumers:**
```bash
stepwise tail job-abc123    # Stream live events
stepwise logs job-abc123    # Dump full history
stepwise output job-abc123 plan  # Show step output
```

---

## Job Metadata

Every job can carry structured metadata, set at creation:

```bash
stepwise run my-flow \
  --meta sys.origin=cli \
  --meta sys.session_id=my-session \
  --meta app.project=stepwise \
  --meta app.ticket=H15
```

**Schema:**
- `sys` — routing and operational fields. Stepwise reads these. Validated at ingress.
  - `origin` (string) — who launched: `cli`, `telegram`, `api`, etc.
  - `session_id` (string) — for event routing back to originator
  - `parent_job_id` (string) — set automatically for sub-jobs
  - `root_job_id` (string) — set automatically, root of execution tree
  - `depth` (int) — auto-incremented, prevents infinite loops (max 10)
  - `notify_url` (string) — webhook override
  - `created_by` (string) — authenticated user
- `app` — freeform user data. Stepwise never reads, only passes through.

**Constraints:** immutable after creation, 8KB total limit.

---

## Building an Extension

An extension is any external process that consumes stepwise events and optionally acts on them. No private interfaces — everything uses the public APIs.

### Pattern: WebSocket Consumer

The most common extension pattern: connect to the WebSocket stream, filter events, react.

```python
import asyncio
import websockets
import json

async def listen(server_url, session_id):
    ws_url = f"ws://{server_url}/api/v1/events/stream?session_id={session_id}&since_job_start=true"
    async with websockets.connect(ws_url) as ws:
        async for message in ws:
            event = json.loads(message)
            if event.get("type") == "sys.replay.complete":
                print("Caught up with history, now live")
                continue
            print(f"[{event['event']}] job={event['job_id']} step={event.get('step', '-')}")

            # React to suspensions
            if event["event"] == "step.suspended":
                # Your logic here: send to Slack, ask a human, auto-approve, etc.
                pass

asyncio.run(listen("localhost:8340", "my-session"))
```

### Pattern: CLI Extension (Git-style Discovery)

Extensions that add CLI subcommands use PATH discovery. If `stepwise-telegram` is on your PATH, then `stepwise telegram` works automatically. See [How to Build an Extension](how-to-plugins.md) for the full walkthrough.

### Pattern: Webhook Handler

For serverless or remote integrations:

```bash
stepwise run my-flow --async \
  --notify https://my-server.com/hooks/stepwise
```

Your server receives POST requests with the event envelope. React accordingly.

### Fulfilling External Steps

When a step uses `executor: external` and suspends for input, extensions can fulfill it:

```bash
# CLI
stepwise fulfill <run-id> '{"approved": true, "feedback": "Looks good"}'

# API
curl -X POST http://localhost:PORT/api/runs/<run-id>/fulfill \
  -H "Content-Type: application/json" \
  -d '{"approved": true, "feedback": "Looks good"}'
```

---

## Design Principles

- **Platform-agnostic**: No privileged integration for any AI platform. Claude Code, Codex, custom agents — same surface area.
- **Out-of-process**: Extensions never load code into the engine. They interact via HTTP, WebSocket, or stdin/stdout.
- **Core works standalone**: Zero extensions required. Hooks + CLI + web UI cover all basic needs.
- **Same envelope everywhere**: Hooks, webhooks, and WebSocket stream all use the identical JSON event format.

---

See [How to Build an Extension](how-to-plugins.md) for a step-by-step guide. See [API Reference](api.md) for endpoint documentation.
