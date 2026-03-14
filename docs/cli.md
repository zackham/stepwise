# CLI Reference

Stepwise is a CLI-first tool. All commands are available via `stepwise <command>`.

## Global Flags

| Flag | Description |
|------|-------------|
| `--version` | Print version and exit |
| `-v, --verbose` | Verbose output |
| `-q, --quiet` | Suppress non-essential output |
| `--project-dir PATH` | Use a specific project directory instead of searching from cwd |
| `--standalone` | Force direct SQLite mode (skip server detection) |
| `--server URL` | Force API mode with specified server URL |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Job failed (step error, validation failure) |
| `2` | Usage error (bad arguments, missing file) |
| `3` | Configuration error |
| `4` | Project error (no `.stepwise/` found) |
| `5` | Suspended (flow blocked by human steps) |

**`--wait` mode exit codes** (override codes 3-4 for agent callers):

| Code | Meaning |
|------|---------|
| `0` | Completed successfully |
| `1` | Flow execution failed |
| `2` | Input validation error |
| `3` | Timeout (`--timeout` exceeded) |
| `4` | Cancelled |
| `5` | Suspended (all progress blocked by human steps) |

---

## `stepwise init`

Create a `.stepwise/` project directory in the current folder.

```bash
stepwise init
stepwise init --force    # reinitialize existing project
```

This creates the project structure:

```
.stepwise/
  db/stepwise.db      # SQLite job store
  jobs/                # job workspace directories
  templates/           # project-local templates
  hooks/               # event hook scripts (on-suspend, on-complete, on-fail)
  logs/                # hook failure logs
```

Hook scripts are scaffolded with commented examples. See [Project Hooks](#project-hooks) for details.

Stepwise commands search upward from cwd for `.stepwise/` (like git searches for `.git/`).

| Flag | Description |
|------|-------------|
| `--force` | Reinitialize even if `.stepwise/` already exists |

---

## `stepwise new`

Create a new flow directory with a minimal `FLOW.yaml` template.

```bash
stepwise new my-flow
```

```
Created flows/my-flow/FLOW.yaml
```

Creates `flows/<name>/FLOW.yaml` in the current project. The name must match `[a-zA-Z0-9_-]+`. Fails if the directory already exists.

---

## `stepwise run`

Execute a flow. The primary command — three modes depending on flags.

The `<flow>` argument accepts flow names (e.g., `my-flow`), file paths (e.g., `flows/my-flow.flow.yaml`), or directory paths (e.g., `flows/my-flow/`). Names are resolved across the project root, `flows/`, and `.stepwise/flows/`.

### Headless (default)

```bash
stepwise run my-flow.flow.yaml
```

Runs the flow in the terminal. Prints step-by-step progress. Exits when the job completes or fails. Human steps prompt via stdin.

### Watch mode

```bash
stepwise run my-flow.flow.yaml --watch
```

Starts an ephemeral web server on a random port and opens the browser. The DAG executes visually — steps light up, agent output streams in real-time, human steps show a form in the UI. The server stops when you press Ctrl+C.

### With report

```bash
stepwise run my-flow.flow.yaml --report
stepwise run my-flow.flow.yaml --report --report-output custom-report.html
```

Runs the flow headless and generates a self-contained HTML report on completion. The report includes SVG DAG visualization, step timeline, expandable details (inputs/outputs/errors/cost), and YAML source.

### Passing inputs

```bash
# Inline key=value pairs (repeatable)
stepwise run my-flow.flow.yaml --var topic="distributed caching" --var depth=3

# From a YAML or JSON file
stepwise run my-flow.flow.yaml --vars-file inputs.yaml
```

Input variables are available in steps via `$job.field_name`:

```yaml
steps:
  research:
    inputs:
      topic: $job.topic       # ← reads from --var topic="..."
```

### Agent mode (--wait)

```bash
stepwise run council --wait --var question="Should we use Postgres?"
```

Blocks until the flow completes. Prints a single JSON object to stdout — nothing else. All logging goes to stderr. This is the primary integration path for agents calling flows as tools.

```bash
# With timeout (returns exit code 3 if exceeded)
stepwise run deploy.flow.yaml --wait --timeout 300 --var repo=/path --var branch=main

# Read long input from a file instead of shell-escaping
stepwise run review.flow.yaml --wait --var-file spec=spec.md
```

Exit codes for `--wait`: 0=success, 1=failed, 2=input error, 3=timeout, 4=cancelled, 5=suspended.

When a flow has human steps and `--wait` is used, the command returns exit code 5 with a JSON response containing `suspended_steps` — each with `run_id`, `prompt`, and `fields`. Use `stepwise fulfill <run-id> '{...}' --wait` to satisfy the step and continue blocking.

### Async mode (--async)

```bash
stepwise run council --async --var question="..."
# → {"job_id": "job-a1b2c3d4", "status": "running"}
```

Fire-and-forget. Spawns a detached background process (no `stepwise serve` required), returns the job ID immediately. Poll with `stepwise status` or retrieve results with `stepwise output`.

### JSON output (--output json)

```bash
stepwise run my-flow.flow.yaml --output json --var k=v
```

Same as headless mode (shows step progress to stderr) but prints structured JSON result to stdout on completion. Combine with `--wait` for fully silent machine-readable output.

| Flag | Description |
|------|-------------|
| `--watch` | Ephemeral server + browser UI |
| `--wait` | Block until completion, JSON output on stdout |
| `--async` | Fire-and-forget, returns job_id immediately |
| `--output json` | Print structured JSON result to stdout on completion |
| `--timeout INT` | Timeout in seconds (for `--wait`) |
| `--var KEY=VALUE` | Pass input variable (repeatable) |
| `--var-file KEY=PATH` | Pass input from file contents (repeatable, avoids shell escaping) |
| `--vars-file PATH` | Load variables from YAML or JSON file |
| `--port INT` | Override port (for `--watch`, default: random) |
| `--objective STR` | Set job objective (default: flow filename) |
| `--workspace PATH` | Override workspace directory |
| `--report` | Generate HTML report after completion |
| `--report-output PATH` | Custom report file path (default: `<flow>-report.html`) |
| `--no-open` | Don't auto-open browser (for `--watch`) |

---

## `stepwise serve`

Start a persistent server with the web UI. For long-running use where you want to manage multiple jobs over time.

```bash
stepwise serve
stepwise serve --port 9000
stepwise serve --host 0.0.0.0 --port 8340    # bind to all interfaces
stepwise serve --no-open                       # don't auto-open browser
```

The server runs until you stop it (Ctrl+C). Jobs persist across restarts via SQLite.

| Flag | Description |
|------|-------------|
| `--port INT` | Port to listen on (default: 8340) |
| `--host STR` | Bind address (default: 127.0.0.1) |
| `--no-open` | Don't auto-open browser |

**Note:** The server exposes a REST API and WebSocket at the same address. See the [API Reference](api.md) for endpoint documentation. Swagger UI is available at `/docs`.

---

## `stepwise validate`

Check a flow file for syntax and structural errors without running it.

```bash
stepwise validate my-flow.flow.yaml
```

```
✓ my-flow.flow.yaml (5 steps, 2 loops)
```

```
✗ my-flow.flow.yaml:
  - Step "deploy" references unknown input source "bild.artifact"
  - Exit rule on "review" targets unknown step "draf"
```

Catches: YAML syntax errors, missing step references, invalid input bindings, bad exit rule targets, undeclared outputs.

---

## `stepwise jobs`

List jobs in the project database.

```bash
stepwise jobs                      # last 20 jobs, table format
stepwise jobs --all                # all jobs
stepwise jobs --limit 5            # last 5 jobs
stepwise jobs --status running     # filter by status
stepwise jobs --output json        # JSON output
```

```
ID               STATUS       OBJECTIVE                STEPS    CREATED
job-a1b2c3d4     completed    deploy-pipeline          3/3      2 hours ago
job-e5f6g7h8     running      content-review           2/4      5 min ago
job-i9j0k1l2     failed       data-analysis            1/3      1 hours ago
```

| Flag | Description |
|------|-------------|
| `--output {table,json}` | Output format (default: table) |
| `--limit INT` | Number of recent jobs to show (default: 20) |
| `--all` | Show all jobs (ignore limit) |
| `--status STR` | Filter by status: `pending`, `running`, `paused`, `completed`, `failed`, `cancelled` |

---

## `stepwise status`

Show detailed step-by-step status for a specific job.

```bash
stepwise status job-a1b2c3d4
stepwise status job-a1b2c3d4 --output json
```

```
Job: job-a1b2c3d4
Status: completed
Objective: deploy-pipeline
Created: 2 hours ago

Steps:
  ✓ build            completed    script   (3.2s, $0.000)
  ✓ test             completed    script   (8.1s, $0.000)
  ✓ deploy           completed    script   (1.4s, $0.000)
```

Status icons: `✓` completed, `✗` failed, `⠋` running, `◆` suspended (waiting for human), `↗` delegated (sub-job), `○` pending.

**JSON output** provides a full resolved flow status (DAG view) with per-step costs, outputs, suspension details, route decisions, and sub-jobs.

| Flag | Description |
|------|-------------|
| `--output {table,json}` | Output format (default: table) |

---

## `stepwise cancel`

Cancel a running or paused job. Active agent processes are killed.

```bash
stepwise cancel job-e5f6g7h8
stepwise cancel job-e5f6g7h8 --output json
```

```
✓ Cancelled job-e5f6g7h8
```

**JSON output** returns `{job_id, status, completed_steps, cancelled_steps, remaining_steps}` where `remaining_steps` includes prompts/descriptions from the workflow definition.

Returns an error if the job is already completed, failed, or cancelled.

| Flag | Description |
|------|-------------|
| `--output {table,json}` | Output format (default: table) |

---

## `stepwise list`

List items across the project. Currently supports the `--suspended` flag for a global suspension inbox.

```bash
stepwise list --suspended --output json
stepwise list --suspended --since 24h
stepwise list --suspended --flow meeting-ingest
```

Returns all suspended steps across all active jobs — the "inbox" of human steps awaiting fulfillment.

Each item includes: `job_id`, `flow_name`, `run_id`, `step_name`, `prompt`, `expected_outputs`, `suspended_at`, `age_seconds`, `fulfill_command`.

| Flag | Description |
|------|-------------|
| `--suspended` | Show suspended steps across all active jobs |
| `--output {table,json}` | Output format (default: table) |
| `--since DURATION` | Filter by age (e.g., `24h`, `7d`, `30m`) |
| `--flow NAME` | Filter by flow name |

---

## `stepwise wait`

Block until an existing job reaches a terminal state or all progress is blocked by suspensions.

```bash
stepwise wait job-a1b2c3d4
stepwise wait job-a1b2c3d4 --timeout 300
```

Returns the same JSON format as `stepwise run --wait`. Exit code 0 for completion, 1 for failure, 5 for suspension.

Useful for attaching to a job started with `--async` or for re-checking a job after fulfilling a step without using `fulfill --wait`.

| Flag | Description |
|------|-------------|
| `--timeout INT` | Timeout in seconds |

---

## `stepwise templates`

List available workflow templates (built-in and project-local).

```bash
stepwise templates
```

```
BUILT-IN:
  hello-world
  iterative-review

PROJECT:
  deploy-pipeline
  content-engine
```

Templates are saved via the web UI and stored in `.stepwise/templates/`.

---

## `stepwise config`

Manage configuration. Config is stored in `~/.config/stepwise/config.json`.

### Set a value

```bash
stepwise config set openrouter_api_key sk-or-v1-abc123
stepwise config set default_model anthropic/claude-sonnet-4

# Read from stdin (hides input — good for API keys)
stepwise config set openrouter_api_key --stdin
```

### Get a value

```bash
stepwise config get openrouter_api_key           # **********123  (masked)
stepwise config get openrouter_api_key --unmask   # sk-or-v1-abc123
stepwise config get default_model                 # anthropic/claude-sonnet-4
```

**Config keys:**

| Key | Description |
|-----|-------------|
| `openrouter_api_key` | API key for LLM and agent steps (via OpenRouter) |
| `default_model` | Default model for LLM steps when not specified in the flow |

---

## `stepwise get`

Download a flow from the registry or a URL.

```bash
# By registry name
stepwise get code-review

# By URL (direct download)
stepwise get https://example.com/code-review.flow.yaml
```

```
✓ Downloaded code-review.flow.yaml (3 steps, by alice, 1,247 downloads)
  Run: stepwise run code-review.flow.yaml
```

Saves to the current directory. Fails if a file with the same name already exists.

Flags:
- `--force` — overwrite existing file

## `stepwise share`

Publish a flow to the registry.

```bash
stepwise share my-pipeline.flow.yaml
```

```
Validating my-pipeline.flow.yaml... ✓ (3 steps)
Publishing as "my-pipeline"...

✓ Published: https://stepwise.run/flows/my-pipeline
  Get: stepwise get my-pipeline
  Token saved to ~/.config/stepwise/tokens.json
```

Validates the flow, reads metadata from the YAML header, and uploads to the registry. The update token is saved locally for future updates.

Flags:
- `--author <name>` — override author (default: from YAML or git config)
- `--update` — update a previously published flow (uses stored token)

## `stepwise search`

Search the flow registry.

```bash
stepwise search "code review"
stepwise search --tag agent --sort downloads
```

```
NAME                 AUTHOR     STEPS  DOWNLOADS  TAGS
code-review          alice      3      1,247      agent, human-in-the-loop
pr-review-lite       bob        2      892        script, code-review
```

Flags:
- `--tag <tag>` — filter by tag
- `--sort <field>` — sort by `downloads` (default), `name`, `newest`
- `--output json` — machine-readable output

## `stepwise info`

Show details about a published flow.

```bash
stepwise info code-review
```

Shows metadata for a published flow without downloading it (name, author, description, tags, step count, downloads).

---

## `stepwise schema`

Generate a JSON tool contract from a flow file. Shows what inputs the flow needs, what outputs it produces, and whether it has human steps.

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

For flows with human steps, includes the step name, prompt, and required fields:

```json
{
  "humanSteps": [
    {
      "step": "approve",
      "prompt": "Review this deployment package. Approve or reject.",
      "fields": ["approved", "reason"]
    }
  ]
}
```

---

## `stepwise output`

Retrieve job outputs after completion (or partial outputs for running/failed jobs).

```bash
stepwise output job-a1b2c3d4                          # terminal outputs only
stepwise output job-a1b2c3d4 --scope full             # per-step details + cost + events
stepwise output job-a1b2c3d4 --step build,test        # specific step outputs
stepwise output job-a1b2c3d4 --step review --inputs   # step inputs instead of outputs
stepwise output --run run-abc12345                     # direct run access by ID
```

**Per-step mode** (`--step`): Returns a JSON object keyed by step name. Steps not yet completed have `null` values with a `_status` field. Non-existent steps have an `_error` field.

**Input mode** (`--inputs`): Returns the inputs that were fed to the step instead of its outputs.

**Direct run mode** (`--run`): Access any run's output by its global run ID, without needing the job ID.

| Flag | Description |
|------|-------------|
| `--scope {default,full}` | Output scope (default: terminal outputs only) |
| `--step NAMES` | Comma-separated step names for per-step output |
| `--inputs` | Return step inputs instead of outputs (with `--step`) |
| `--run RUN_ID` | Retrieve output for a specific run by ID |

---

## `stepwise fulfill`

Satisfy a suspended human step from the command line. Used by agents to complete human-in-the-loop steps programmatically.

```bash
stepwise fulfill run-abc12345 '{"approved": true, "reason": "looks good"}'

# Read payload from stdin (useful for large payloads or piping)
echo '{"approved": true}' | stepwise fulfill run-abc12345 --stdin
cat payload.json | stepwise fulfill run-abc12345 -

# Fulfill and wait for the job to complete or suspend again
stepwise fulfill run-abc12345 '{"approved": true}' --wait
```

```json
{"status": "fulfilled", "run_id": "run-abc12345"}
```

The run ID comes from `--wait` responses (which include `suspended_steps`), `stepwise list --suspended`, or `stepwise status --output json`.

**`--wait` mode:** After fulfilling, enters a blocking wait loop on the parent job. Returns the same JSON format as `stepwise run --wait` (completed, failed, or suspended again with new `suspended_steps`). This is the primary mediation pattern for agents.

**Idempotent:** If a step is already fulfilled (e.g., by a concurrent hook), returns an error JSON but doesn't corrupt state. With `--wait`, it still blocks on the job.

| Flag | Description |
|------|-------------|
| `--stdin` | Read JSON payload from stdin instead of positional argument |
| `--wait` | After fulfilling, block until job completes or suspends again |

**Note:** You can also pass `-` as the payload argument to read from stdin.

---

## `stepwise agent-help`

Generate agent-readable instructions for all flows in the current project. Output is a markdown block suitable for pasting into CLAUDE.md or similar.

```bash
stepwise agent-help                        # print to stdout
stepwise agent-help --update CLAUDE.md     # update file in-place
stepwise agent-help --flows-dir ./flows    # scan a specific directory
```

The `--update` flag finds `<!-- stepwise-agent-help -->` / `<!-- /stepwise-agent-help -->` markers in the target file and replaces just that section. Creates the markers if they don't exist.

| Flag | Description |
|------|-------------|
| `--update FILE` | Update a file in-place between markers |
| `--flows-dir DIR` | Override flow discovery directory (scan only this dir) |

---

## `stepwise update`

Upgrade stepwise to the latest version. Automatically detects the install method (uv, pipx, or pip) and runs the appropriate upgrade command.

```bash
stepwise update
```

```
Upgrading via uv...
Updated: 0.1.0 → 0.2.0
```

If already on the latest version:

```
Upgrading via uv...
Already up to date (0.2.0).
```

---

## Project Hooks

Hook scripts in `.stepwise/hooks/` are fired by the engine on key events. Each hook receives a JSON payload on stdin.

| Hook | Event | Trigger |
|------|-------|---------|
| `on-suspend` | `step.suspended` | A step is suspended (awaiting human input) |
| `on-complete` | `job.completed` | A job completes successfully |
| `on-fail` | `job.failed`, `step.failed` | A job or step fails |

**Payload fields:**
- All hooks: `event`, `hook`, `job_id`, `timestamp`
- `on-suspend` additionally: `step`, `run_id`, `watch_mode`, `prompt`, `fulfill_command`
- `on-fail` additionally: `step` (if step failure), `error`, `reason`

Hooks are fire-and-forget with a 30-second timeout. Failures are logged to `.stepwise/logs/hooks.log`. Hooks are scaffolded by `stepwise init` with commented examples.

**Example: Slack notification on suspension**

```sh
#!/bin/sh
# .stepwise/hooks/on-suspend
payload=$(cat)
step=$(echo "$payload" | jq -r '.step')
cmd=$(echo "$payload" | jq -r '.fulfill_command')
curl -s -X POST "$SLACK_WEBHOOK" -d "{\"text\":\"Step '$step' needs input: $cmd\"}"
```

---

## Server-Aware CLI

When `stepwise serve` is running, CLI commands can automatically route through the server API instead of accessing SQLite directly. This prevents database locking conflicts.

**Detection:** The CLI checks `.stepwise/server.pid` and probes the health endpoint.

**Override flags:**
- `--standalone` — force direct SQLite mode (skip server detection)
- `--server URL` — force API mode with a specific server URL

The server writes `.stepwise/server.pid` on startup and removes it on clean shutdown.

---

## Signal Handling

`Ctrl+C` during `stepwise run` cleanly cancels the active job. Running agent processes are killed, the job is marked as cancelled, and the process exits. In `--watch` mode, Ctrl+C stops the ephemeral server.

## Project Discovery

Stepwise searches upward from the current directory for a `.stepwise/` folder, similar to how git searches for `.git/`. Use `--project-dir` to override:

```bash
stepwise --project-dir /path/to/project jobs
```
