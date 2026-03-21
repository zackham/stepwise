# API Reference

The Stepwise server exposes a REST API and WebSocket endpoint. Start it with `stepwise server start` (persistent) or `stepwise run --watch` (ephemeral).

**Base URL:** `http://localhost:8340` (default port, configurable via `--port`)

**Interactive docs:** Swagger UI at `/docs` (auto-generated from the FastAPI schema)

---

## Jobs

### List jobs

```
GET /api/jobs
```

**Query parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `status` | string | Filter by status: `pending`, `running`, `paused`, `completed`, `failed`, `cancelled` |
| `top_level` | boolean | Only return top-level jobs (exclude sub-jobs) |

**Response:** Array of job objects.

```bash
curl http://localhost:8340/api/jobs?status=running&top_level=true
```

### Create a job

```
POST /api/jobs
```

**Request body:**

```json
{
  "name": "ux-fix-sprint-12",
  "objective": "deploy-pipeline",
  "workflow": {
    "steps": {
      "build": {
        "outputs": ["artifact"],
        "executor": {"type": "script", "config": {"command": "scripts/build.sh"}}
      }
    }
  },
  "inputs": {"branch": "main"},
  "workspace_path": "/path/to/workspace"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | No | Human-friendly job name (not unique) |
| `objective` | string | Yes | Job goal/description |
| `workflow` | object | Yes | Workflow definition (steps, executors, bindings) |
| `inputs` | object | No | Initial input values (accessed via `$job.field`) |
| `config` | object | No | Job configuration overrides |
| `workspace_path` | string | No | Override workspace directory |

**Response:** The created job object. Status will be `pending`.

**Note:** Most users should use `stepwise run` or the web UI rather than creating jobs via the API directly. The CLI handles YAML parsing, validation, and workspace setup.

### Get a job

```
GET /api/jobs/{job_id}
```

**Response:** Full job object including workflow definition, status, and timing.

### Start a job

```
POST /api/jobs/{job_id}/start
```

Transitions the job from `pending` to `running`. The engine begins executing steps on the next tick.

### Pause / Resume a job

```
POST /api/jobs/{job_id}/pause
POST /api/jobs/{job_id}/resume
```

Pausing stops the engine from launching new steps. Already-running steps continue until they complete. Resuming re-enables step launching.

### Cancel a job

```
POST /api/jobs/{job_id}/cancel
```

Cancels the job and kills any running executor processes (agents, scripts).

### Delete a job

```
DELETE /api/jobs/{job_id}
```

Permanently removes the job and all its data from the store.

### Get job tree

```
GET /api/jobs/{job_id}/tree
```

Returns the job hierarchy — the job, its runs, and any sub-jobs (recursively).

**Response:**

```json
{
  "job": { "id": "job-abc", "status": "completed", ... },
  "runs": [ ... ],
  "sub_jobs": [
    {
      "job": { "id": "job-def", ... },
      "runs": [ ... ],
      "sub_jobs": []
    }
  ]
}
```

### Get runs for a job

```
GET /api/jobs/{job_id}/runs
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `step_name` | string | Filter runs by step name |

**Response:** Array of step run objects, ordered by creation time.

### Re-run a step

```
POST /api/jobs/{job_id}/steps/{step_name}/rerun
```

Creates a new step run (next attempt number) for the specified step. Useful for retrying failed steps or re-running completed steps with different upstream data.

**Response:** The new step run object.

### Get job events

```
GET /api/jobs/{job_id}/events
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `since` | ISO 8601 string | Only return events after this timestamp |

**Response:** Array of structured events (state transitions, step launches, completions, errors).

### Inject context

```
POST /api/jobs/{job_id}/context
```

**Request body:**

```json
{
  "context": "Additional context string to inject into the job"
}
```

Adds supplemental context to a running job. Available to agent and LLM executors.

---

## Runs (Step Executions)

### Fulfill a watch (human step)

```
POST /api/runs/{run_id}/fulfill
```

Provides the human response for a suspended step.

**Request body:**

```json
{
  "payload": {
    "decision": "approve",
    "feedback": "Looks good, ship it."
  }
}
```

The `payload` keys must match the step's declared `outputs`. After fulfillment, the step completes and the workflow continues.

### Get step events

```
GET /api/runs/{run_id}/step-events
```

Fine-grained events for a specific run — cost updates, activity markers, status changes.

| Parameter | Type | Description |
|-----------|------|-------------|
| `since` | ISO 8601 string | Only return events after this timestamp |
| `limit` | integer | Max events to return (default: 100, max: 1000) |

### Get run cost

```
GET /api/runs/{run_id}/cost
```

**Response:**

```json
{
  "run_id": "run-abc123",
  "cost_usd": 0.0342
}
```

Accumulated cost from all cost events for this run. Useful for LLM and agent steps.

### Cancel a run

```
POST /api/runs/{run_id}/cancel
```

Cancels a running step. Kills the executor process (if applicable) and marks the run as failed with `error_category: "user_cancelled"`.

Returns `400` if the run is not in `running` status.

### Get agent output

```
GET /api/runs/{run_id}/agent-output
```

Returns condensed agent output events for a run. Works for both running and completed agent steps.

**Response:**

```json
{
  "events": [
    {"t": "text", "text": "Analyzing the codebase structure..."},
    {"t": "tool_start", "id": "tc_1", "title": "Search files", "kind": "search"},
    {"t": "tool_end", "id": "tc_1"},
    {"t": "text", "text": "Found 12 relevant files. Let me read..."},
    {"t": "usage", "used": 9434, "size": 258400}
  ]
}
```

**Event types:**

| Type | Fields | Description |
|------|--------|-------------|
| `text` | `text` | Agent text output (streaming tokens) |
| `tool_start` | `id`, `title`, `kind` | Agent started using a tool |
| `tool_end` | `id` | Tool call completed |
| `usage` | `used`, `size` | Context window usage (tokens used / total) |

---

## Engine

### Manual tick

```
POST /api/tick
```

Forces the engine to run one tick cycle immediately — checks for completed steps, launches ready steps, evaluates exit rules. Normally the engine ticks automatically (every 2s when active, 10s when idle).

### Engine status

```
GET /api/status
```

**Response:**

```json
{
  "active_jobs": 2,
  "total_jobs": 15,
  "registered_executors": ["script", "external", "mock_llm", "llm", "agent"]
}
```

### List executors

```
GET /api/executors
```

**Response:**

```json
{
  "executors": ["script", "external", "mock_llm", "llm", "agent"]
}
```

---

## Templates

### Save template

```
POST /api/templates
```

```json
{
  "name": "iterative-review",
  "description": "Draft → Review → Loop pattern",
  "workflow": { ... }
}
```

### List templates

```
GET /api/templates
```

### Get template

```
GET /api/templates/{name}
```

### Delete template

```
DELETE /api/templates/{name}
```

---

## Configuration

### Get config

```
GET /api/config
```

**Response:**

```json
{
  "has_api_key": true,
  "model_registry": [
    {"id": "anthropic/claude-sonnet-4", "name": "Sonnet", "provider": "anthropic", "tier": "balanced"}
  ],
  "default_model": "anthropic/claude-sonnet-4"
}
```

Note: The API key value is never exposed via the API. Only `has_api_key` (boolean) is returned.

### Get models

```
GET /api/config/models
```

### Update models

```
PUT /api/config/models
```

```json
{
  "models": [
    {"id": "anthropic/claude-opus-4", "name": "Opus", "provider": "anthropic", "tier": "strong"},
    {"id": "anthropic/claude-sonnet-4", "name": "Sonnet", "provider": "anthropic", "tier": "balanced"}
  ],
  "default_model": "anthropic/claude-sonnet-4"
}
```

### Set API key

```
PUT /api/config/api-key
```

```json
{
  "api_key": "sk-or-v1-abc123..."
}
```

---

## WebSocket

```
ws://localhost:8340/ws
```

Connect to receive real-time updates. The server broadcasts two message types:

### Tick updates

Sent when engine state changes (steps complete, new steps launch, jobs finish):

```json
{
  "type": "tick",
  "changed_jobs": ["job-abc123", "job-def456"],
  "timestamp": "2026-03-11T14:30:00Z"
}
```

Use `changed_jobs` to know which jobs to re-fetch. The web UI uses this to invalidate react-query caches.

### Agent output streaming

Sent in real-time (~100ms intervals) while agent steps are running:

```json
{
  "type": "agent_output",
  "run_id": "run-abc123",
  "events": [
    {"t": "text", "text": "Let me analyze"},
    {"t": "tool_start", "id": "tc_1", "title": "Read file", "kind": "read"}
  ]
}
```

Same event format as the `GET /api/runs/{run_id}/agent-output` endpoint. Subscribe to the WebSocket for live streaming; use the REST endpoint for historical replay of completed runs.

### Connection pattern

```javascript
const ws = new WebSocket("ws://localhost:8340/ws");

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "tick") {
    // Re-fetch changed jobs
    msg.changed_jobs.forEach(id => refetchJob(id));
  } else if (msg.type === "agent_output") {
    // Append streaming output for run
    appendAgentOutput(msg.run_id, msg.events);
  }
};
```

---

## Error Responses

All error responses follow this format:

```json
{
  "detail": "Human-readable error message"
}
```

| Status | Meaning |
|--------|---------|
| `400` | Bad request — invalid input, job already started, run not cancellable |
| `404` | Not found — job, run, or template doesn't exist |
| `500` | Internal server error |

---

## Environment Variables

The server reads these environment variables (set automatically by the CLI):

| Variable | Description | Default |
|----------|-------------|---------|
| `STEPWISE_DB` | Path to SQLite database | `stepwise.db` |
| `STEPWISE_TEMPLATES` | Path to templates directory | `templates` |
| `STEPWISE_JOBS_DIR` | Path to jobs workspace directory | `jobs` |
| `STEPWISE_PORT` | Server port (legacy entry point only) | `8340` |
| `STEPWISE_RELOAD` | Enable hot reload (legacy, dev only) | `false` |

You generally don't need to set these — the CLI manages them based on `.stepwise/` project discovery.
