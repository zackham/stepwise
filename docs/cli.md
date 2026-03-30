# CLI Reference

Stepwise is a CLI-first tool. All commands are available via `stepwise <command>`.

See [Quickstart](quickstart.md) for installation and first-run instructions. See [Concepts](concepts.md) for the mental model behind jobs and steps, [Writing Flows](writing-flows.md) for flow authorship, and [Troubleshooting](troubleshooting.md) for error messages and diagnostic commands.

## Overview

| Group | Commands |
|-------|----------|
| [Core](#core-commands) | `run`, `new`, `validate`, `check`, `preflight`, `test-fixture` |
| [Jobs](#job-commands) | `jobs`, `status`, `output`, `tail`, `logs`, `wait`, `cancel`, `fulfill`, `list` |
| [Job Staging](#job-staging-commands) | `job create`, `job show`, `job run`, `job dep`, `job cancel`, `job rm` |
| [Server](#server-commands) | `server start`, `server stop`, `server restart`, `server status` |
| [Registry](#registry-commands) | `share`, `get`, `search`, `info`, `login`, `logout` |
| [Configuration](#configuration-commands) | `config`, `init`, `templates`, `schema`, `diagram` |
| [Utility](#utility-commands) | `agent-help`, `flows`, `extensions`, `docs`, `cache`, `version`, `update`, `uninstall` |

## Common Workflows

Quick recipes for the most frequent tasks. Each one combines several commands.

### Run a flow and watch it live

```bash
stepwise run my-flow --watch --input task="build the API" --name "impl: API"
```

Opens the [web UI](web-ui.md) with real-time DAG visualization. External steps prompt in the browser.

### Agent calls a flow as a tool

```bash
stepwise run deploy --wait --input repo="/path" --input branch="main"
# Returns only JSON to stdout. Exit code 5 = suspended (needs human input).
stepwise fulfill <run-id> '{"approved": true}' --wait
# Continues blocking until next suspension or completion.
```

See [Agent Integration](agent-integration.md) for the full guide. Generate agent-facing docs with `stepwise agent-help`.

### Stage, wire, and release a batch

```bash
stepwise job create plan-flow --input spec="new feature" --group sprint-1 --name "plan: feature"
stepwise job create impl-flow --input plan=job-<plan-id>.plan --group sprint-1 --name "impl: feature"
stepwise job show --group sprint-1     # review the batch
stepwise job run --group sprint-1      # release — engine runs in dependency order
```

See [Concepts: Job Staging](concepts.md#job-staging) for the mental model.

### Monitor and triage

```bash
stepwise jobs                          # list all jobs
stepwise list --suspended              # show jobs waiting for human input
stepwise status <job-id>               # step-by-step detail
stepwise tail <job-id>                 # stream events in real time
stepwise output <job-id>               # retrieve terminal outputs
```

### Validate before running

```bash
stepwise validate my-flow.flow.yaml    # structural check (fast, no execution)
stepwise preflight my-flow.flow.yaml   # runtime check (API keys, models, scripts)
```

See [Writing Flows: Validation](writing-flows.md#validation-and-preflight) for what the validator catches.

### Server lifecycle

```bash
stepwise server start --detach         # persistent background server
stepwise server status                 # PID, port, uptime, log path
stepwise server restart                # stop + start
stepwise server stop                   # graceful shutdown
```

The server provides the [web UI](web-ui.md), WebSocket live updates, and job adoption for orphaned CLI jobs.

---

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
| `5` | Suspended (flow blocked by external steps) |

**`--wait` mode exit codes** (override codes 3-4 for agent callers):

| Code | Meaning |
|------|---------|
| `0` | Completed successfully |
| `1` | Flow execution failed |
| `2` | Input validation error |
| `4` | Cancelled |
| `5` | Suspended (all progress blocked by external steps) |

---

## Core Commands

### `stepwise run`

Execute a flow. The primary command — three modes depending on flags.

The `<flow>` argument accepts flow names (e.g., `my-flow`), file paths (e.g., `flows/my-flow.flow.yaml`), or directory paths (e.g., `flows/my-flow/`). Names are resolved across the project root, `flows/`, and `.stepwise/flows/`.

See [YAML Format](yaml-format.md) for flow file syntax.

#### Headless (default)

```bash
stepwise run my-flow.flow.yaml
```

Runs the flow in the terminal. Prints step-by-step progress. Exits when the job completes or fails. External steps prompt via stdin.

#### Watch mode

```bash
stepwise run my-flow.flow.yaml --watch
```

Starts an ephemeral web server on a random port and opens the browser. The DAG executes visually — steps light up, agent output streams in real-time, external steps show a form in the UI. The server stops when you press Ctrl+C.

#### With report

```bash
stepwise run my-flow.flow.yaml --report
stepwise run my-flow.flow.yaml --report --report-output custom-report.html
```

Runs the flow headless and generates a self-contained HTML report on completion. The report includes SVG DAG visualization, step timeline, expandable details (inputs/outputs/errors/cost), and YAML source.

#### Passing inputs

```bash
stepwise run my-flow.flow.yaml --input topic="distributed caching" --input depth=3
stepwise run my-flow.flow.yaml --vars-file inputs.yaml
```

Input variables are available in steps via `$job.field_name`:

```yaml
steps:
  research:
    inputs:
      topic: $job.topic       # ← reads from --input topic="..."
```

#### Agent mode (--wait)

```bash
stepwise run council --wait --input question="Should we use Postgres?"
```

Blocks until the flow completes. Prints a single JSON object to stdout — nothing else. All logging goes to stderr. This is the primary integration path for agents calling flows as tools.

```bash
stepwise run deploy.flow.yaml --wait --input repo=/path --input branch=main
stepwise run review.flow.yaml --wait --input spec=@spec.md
```

Exit codes in wait mode: 0=success, 1=failed, 2=input error, 4=cancelled, 5=suspended.

When a flow has external steps and `--wait` is used, the command returns exit code 5 with a JSON response containing `suspended_steps` — each with `run_id`, `prompt`, and `fields`. Use `stepwise fulfill <run-id> '{...}' --wait` to satisfy the step and continue blocking.

#### Async mode (--async)

```bash
stepwise run council --async --input question="..."
```

Returns `{"job_id": "job-a1b2c3d4", "status": "running"}`.

Fire-and-forget. Spawns a detached background process (no server required), returns the job ID immediately. Poll with stepwise status or retrieve results with stepwise output.

#### JSON output (--output json)

```bash
stepwise run my-flow.flow.yaml --output json --input k=v
```

Same as headless mode (shows step progress to stderr) but prints structured JSON result to stdout on completion. Combine with --wait for fully silent machine-readable output.

| Flag | Description |
|------|-------------|
| `--watch` | Ephemeral server + browser UI |
| `--wait` | Block until completion, JSON output on stdout |
| `--async` | Fire-and-forget, returns job_id immediately |
| `--output json` | Print structured JSON result to stdout on completion |
| `--input KEY=VALUE` | Pass input variable (repeatable) |
| `--input KEY=@PATH` | Pass input from file contents (`@` prefix, repeatable, avoids shell escaping) |
| `--vars-file PATH` | Load variables from YAML or JSON file |
| `--port INT` | Override port for --watch (default: random) |
| `--objective STR` | Set job objective (default: flow filename) |
| `--workspace PATH` | Override workspace directory |
| `--report` | Generate HTML report after completion |
| `--report-output PATH` | Custom report file path (default: `<flow>-report.html`) |
| `--no-open` | Don't auto-open browser in --watch mode |
| `--name STR` | Human-friendly job name (optional) |
| `--local` | Force local execution (skip server delegation) |
| `--rerun STEP` | Bypass cache for this step (repeatable) |
| `--notify URL` | Webhook URL for job event notifications |
| `--notify-context JSON` | JSON context to include in webhook payloads |
| `--meta KEY=VALUE` | Set job metadata (dot notation: `sys.origin=cli`, `app.project=foo`) |

---

### `stepwise new`

Create a new flow directory with a minimal `FLOW.yaml` template.

```bash
stepwise new my-flow
```

```
Created flows/my-flow/FLOW.yaml
```

Creates `flows/<name>/FLOW.yaml` in the current project. The name must match `[a-zA-Z0-9_-]+`. Fails if the directory already exists.

See [YAML Format](yaml-format.md) for flow file syntax.

---

### `stepwise validate`

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

Catches: YAML syntax errors, missing step references, invalid input bindings, bad exit rule targets, undeclared outputs, unbounded loops, uncovered output combinations.

See [YAML Format](yaml-format.md) for the flow file spec.

---

### `stepwise check`

Verify model resolution for a flow. Confirms that all LLM/agent steps can resolve their configured models against available model aliases and API keys.

```bash
stepwise check my-flow.flow.yaml
```

| Argument | Description |
|----------|-------------|
| `flow` | Flow name or path to `.flow.yaml` file |

---

### `stepwise preflight`

Pre-run check: validates config, requirements, and model resolution for a flow. A more thorough version of `check` that also verifies external dependencies.

```bash
stepwise preflight my-flow.flow.yaml
stepwise preflight my-flow.flow.yaml --input api_key=sk-test
```

| Flag | Description |
|------|-------------|
| `flow` | Flow name or path to `.flow.yaml` file (positional) |
| `--input KEY=VALUE` | Variable override (repeatable) |

---

### `stepwise test-fixture`

Generate a pytest test harness for a flow. Produces a ready-to-run test file with `CallableExecutor` stubs for each step, `WorkflowDefinition` matching the flow, and assertions on expected outputs.

```bash
stepwise test-fixture my-flow.flow.yaml               # print to stdout
stepwise test-fixture my-flow.flow.yaml -o tests/test_my_flow.py
```

The generated test uses `register_step_fn()` and `run_job_sync()` from the standard test fixtures (see [Testing](../CLAUDE.md#testing)).

| Flag | Description |
|------|-------------|
| `flow` | Flow name or path to `.flow.yaml` file (positional) |
| `-o, --output PATH` | Output file path (default: stdout) |

---

## Job Commands

### `stepwise jobs`

List jobs in the project database.

```bash
stepwise jobs                      # last 20 jobs, table format
stepwise jobs --all                # all jobs
stepwise jobs --limit 5            # last 5 jobs
stepwise jobs --status running     # filter by status
stepwise jobs --output json        # JSON output
stepwise jobs --meta sys.origin=cli  # filter by metadata
```

```
ID               NAME              STATUS       OBJECTIVE                STEPS    CREATED
job-a1b2c3d4     ux-fix            completed    deploy-pipeline          3/3      2 hours ago
job-e5f6g7h8                       running      content-review           2/4      5 min ago
job-i9j0k1l2                       failed       data-analysis            1/3      1 hours ago
```

| Flag | Description |
|------|-------------|
| `--output {table,json}` | Output format (default: table) |
| `--limit INT` | Number of recent jobs to show (default: 20) |
| `--all` | Show all jobs (ignore limit) |
| `--status STR` | Filter by status: `pending`, `running`, `paused`, `completed`, `failed`, `cancelled` |
| `--name STR` | Filter by name (case-insensitive substring match) |
| `--meta KEY=VALUE` | Filter by metadata (e.g., `sys.origin=cli`) |

---

### `stepwise status`

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

Status icons: `✓` completed, `✗` failed, `⠋` running, `◆` suspended (waiting for external input), `↗` delegated (sub-job), `○` pending.

**JSON output** provides a full resolved flow status (DAG view) with per-step costs, outputs, suspension details, route decisions, and sub-jobs.

| Flag | Description |
|------|-------------|
| `--output {table,json}` | Output format (default: table) |

---

### `stepwise output`

Retrieve job outputs after completion (or partial outputs for running/failed jobs).

```bash
stepwise output job-a1b2c3d4                          # terminal outputs only
stepwise output job-a1b2c3d4 review                   # shorthand for --step review
stepwise output job-a1b2c3d4 --scope full             # per-step details + cost + events
stepwise output job-a1b2c3d4 --step build,test        # specific step outputs
stepwise output job-a1b2c3d4 --step review --inputs   # step inputs instead of outputs
stepwise output --run run-abc12345                     # direct run access by ID
```

**Per-step mode** (--step or positional step_name): Returns a JSON object keyed by step name. Steps not yet completed have `null` values with a `_status` field. Non-existent steps have an `_error` field.

**Input mode** (--inputs): Returns the inputs that were fed to the step instead of its outputs.

**Direct run mode** (--run): Access any run's output by its global run ID, without needing the job ID.

| Flag | Description |
|------|-------------|
| `job_id` | Job ID (positional) |
| `step_name` | Step name (positional shorthand for --step) |
| `--scope {default,full}` | Output scope (default: terminal outputs only) |
| `--step NAMES` | Comma-separated step names for per-step output |
| `--inputs` | Return step inputs instead of outputs (use with `--step`) |
| `--run RUN_ID` | Retrieve output for a specific run by ID |

---

### `stepwise tail`

Stream live events for a running job. Attaches to the event stream and prints events as they occur. Exits when the job reaches a terminal state.

```bash
stepwise tail job-e5f6g7h8
```

| Argument | Description |
|----------|-------------|
| `job_id` | Job ID to tail |

---

### `stepwise logs`

Show the full event history for a job. Unlike `tail`, this prints all past events and exits immediately.

```bash
stepwise logs job-a1b2c3d4
```

| Argument | Description |
|----------|-------------|
| `job_id` | Job ID |

---

### `stepwise wait`

Block until one or more existing jobs reach a terminal state or all progress is blocked by suspensions.

```bash
stepwise wait job-a1b2c3d4
stepwise wait job-a1b2c3d4 job-e5f6g7h8 --all     # wait for all to finish
stepwise wait job-a1b2c3d4 job-e5f6g7h8 --any     # wait for first to finish
```

Returns the same JSON format as `stepwise run --wait`. Exit code 0 for completion, 1 for failure, 5 for suspension.

Useful for attaching to a job started with `--async`, or for re-checking a job after fulfilling a step without using `fulfill --wait`.

Unlike `stepwise run --wait` (which creates and waits on a new job), `stepwise wait` attaches to an already-running job.

| Flag | Description |
|------|-------------|
| `JOB_ID` | One or more job IDs (positional, repeatable) |
| `--all` | Wait for all specified jobs to reach terminal state |
| `--any` | Wait for the first specified job to reach terminal state |

---

### `stepwise cancel`

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

### `stepwise fulfill`

Satisfy a suspended external step from the command line. Used by agents to complete external steps programmatically.

See [Agent Integration](agent-integration.md) for the full wait/fulfill mediation pattern.

```bash
stepwise fulfill run-abc12345 '{"approved": true, "reason": "looks good"}'
echo '{"approved": true}' | stepwise fulfill run-abc12345 --stdin
cat payload.json | stepwise fulfill run-abc12345 -
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

### `stepwise list`

List items across the project. Currently supports the `--suspended` flag for a global suspension inbox.

```bash
stepwise list --suspended --output json
stepwise list --suspended --since 24h
stepwise list --suspended --flow meeting-ingest
```

Returns all suspended steps across all active jobs — the "inbox" of external steps awaiting fulfillment.

Each item includes: `job_id`, `flow_name`, `run_id`, `step_name`, `prompt`, `expected_outputs`, `suspended_at`, `age_seconds`, `fulfill_command`.

| Flag | Description |
|------|-------------|
| `--suspended` | Show suspended steps across all active jobs |
| `--output {table,json}` | Output format (default: table) |
| `--since DURATION` | Filter by age (e.g., `24h`, `7d`, `30m`) |
| `--flow NAME` | Filter by flow name |

---

## Job Staging Commands

Stage, review, and release jobs before execution. Wire data between jobs and organize them into groups. See [Concepts: Job Staging](concepts.md#job-staging) for the mental model.

### `stepwise job create`

Create a STAGED job from a flow file. The job won't execute until explicitly released with `job run`.

```bash
stepwise job create my-flow --input task="Build API"
stepwise job create my-flow --input task="Build API" --group wave-1
stepwise job create my-flow --input plan=job-abc123.plan --group wave-1
```

Data wiring: `--input key=job-id.field` references another job's output. This auto-creates a dependency edge.

```
Created job-def456 (staged, group: wave-1)
  Auto-dependency: job-def456 → job-abc123 (via input "plan")
```

| Flag | Description |
|------|-------------|
| `--input KEY=VALUE` | Pass input variable (repeatable). Use `KEY=job-id.field` for cross-job data wiring |
| `--group NAME` | Assign to a group for batch operations |
| `--name STR` | Human-friendly job name |
| `--objective STR` | Set job objective (default: flow filename) |

---

### `stepwise job show`

List staged/pending jobs, or show detail for a single job.

```bash
stepwise job show                          # all staged/pending jobs
stepwise job show --group wave-1           # jobs in a specific group
stepwise job show job-def456               # detail for one job
stepwise job show job-def456 --output json # JSON detail
```

```
GROUP     ID               FLOW        STATUS    DEPS         INPUTS
wave-1    job-abc123       plan-flow   staged    —            task="Build API"
wave-1    job-def456       impl-flow   staged    job-abc123   plan=job-abc123.plan
```

| Flag | Description |
|------|-------------|
| `--group NAME` | Filter by group |
| `--output {table,json}` | Output format (default: table) |

---

### `stepwise job run`

Transition STAGED jobs to PENDING, making them eligible for execution. Jobs start automatically when their dependencies are met.

```bash
stepwise job run job-abc123                # release a single job
stepwise job run --group wave-1            # release all jobs in a group
```

```
✓ Transitioned 2 jobs to pending (group: wave-1)
  job-abc123 → pending (no deps, starting immediately)
  job-def456 → pending (waiting for job-abc123)
```

| Flag | Description |
|------|-------------|
| `--group NAME` | Transition all staged jobs in this group |

---

### `stepwise job dep`

Add, remove, or list job dependencies.

```bash
stepwise job dep job-def456 --after job-abc123    # add dependency
stepwise job dep job-def456 --rm job-abc123       # remove dependency
stepwise job dep job-def456                       # list dependencies
```

Cycle detection: if adding the dependency would create a cycle, the command fails with an error.

| Flag | Description |
|------|-------------|
| `--after JOB_ID` | Add a dependency (this job waits for the specified job) |
| `--rm JOB_ID` | Remove a dependency |

---

### `stepwise job cancel`

Cancel a staged or pending job.

```bash
stepwise job cancel job-def456
```

---

### `stepwise job rm`

Delete a staged job. Cascade-deletes its dependency edges.

```bash
stepwise job rm job-def456
```

---

## Server Commands

### `stepwise server`

Manage the Stepwise server lifecycle: start, stop, restart, and check status.

#### `stepwise server start`

Start the server in the foreground (default) or background with --detach.

```bash
stepwise server start                          # foreground (Ctrl+C to stop)
stepwise server start --detach                 # background daemon
stepwise server start -d --port 9000           # background on custom port
stepwise server start --host 0.0.0.0           # bind to all interfaces
stepwise server start --no-open                # don't auto-open browser
```

In detached mode, logs go to `.stepwise/logs/server.log` (5 MB rotation, 3 backups).

#### `stepwise server stop`

Gracefully stop a running server (SIGTERM, then SIGKILL after 5s).

```bash
stepwise server stop
```

#### `stepwise server restart`

Stop the server (if running) and start it again. Passes through --detach, --port, --host.

```bash
stepwise server restart --detach
```

#### `stepwise server status`

Show whether the server is running, its PID, port, uptime, and log file path.

```bash
stepwise server status
```

| Flag | Description |
|------|-------------|
| `action` | `start`, `stop`, `restart`, or `status` |
| `--port INT` | Port (default: 8340) |
| `--host STR` | Bind address (default: 127.0.0.1) |
| `--detach`, `-d` | Run in background (for `start`/`restart`) |
| `--no-open` | Don't auto-open browser (for `start`/`restart`) |

**Note:** The server exposes a REST API and WebSocket at the same address. See the [API Reference](api.md) for endpoint documentation. Swagger UI is available at `/docs`.

---

## Registry Commands

Commands for sharing and discovering flows on the [stepwise.run](https://stepwise.run) registry.

See [Flow Sharing](flow-sharing.md) for detailed publishing and discovery workflows.

### `stepwise share`

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

| Flag | Description |
|------|-------------|
| `--author NAME` | Override author (default: from YAML or git config) |
| `--update` | Update a previously published flow (uses stored token) |

---

### `stepwise get`

Download a flow from the registry or a URL.

```bash
stepwise get code-review
stepwise get https://example.com/code-review.flow.yaml
```

```
✓ Downloaded code-review.flow.yaml (3 steps, by alice, 1,247 downloads)
  Run: stepwise run code-review.flow.yaml
```

Saves to the current directory. Fails if a file with the same name already exists.

| Flag | Description |
|------|-------------|
| `--force` | Overwrite existing file |

---

### `stepwise search`

Search the flow registry.

```bash
stepwise search "code review"
stepwise search --tag agent --sort downloads
```

```
NAME                 AUTHOR     STEPS  DOWNLOADS  TAGS
code-review          alice      3      1,247      agent, external-fulfillment
pr-review-lite       bob        2      892        script, code-review
```

| Flag | Description |
|------|-------------|
| `--tag TAG` | Filter by tag |
| `--sort FIELD` | Sort by `downloads` (default), `name`, `newest` |
| `--output json` | Machine-readable output |

---

### `stepwise info`

Show details about a published flow.

```bash
stepwise info code-review
```

Shows metadata for a published flow without downloading it (name, author, description, tags, step count, downloads).

---

### `stepwise login`

Log in to the Stepwise registry via GitHub. Opens a browser for OAuth authentication and stores the token locally.

```bash
stepwise login
```

---

### `stepwise logout`

Log out of the Stepwise registry. Removes the locally stored authentication token.

```bash
stepwise logout
```

---

## Configuration Commands

### `stepwise config`

Manage configuration. Config is stored in `~/.config/stepwise/config.json`.

#### Set a value

```bash
stepwise config set openrouter_api_key sk-or-v1-abc123
stepwise config set default_model anthropic/claude-sonnet-4
stepwise config set openrouter_api_key --stdin
```

#### Get a value

```bash
stepwise config get openrouter_api_key           # **********123  (masked)
stepwise config get openrouter_api_key --unmask   # sk-or-v1-abc123
stepwise config get default_model                 # anthropic/claude-sonnet-4
```

#### Initialize flow-specific config

```bash
stepwise config init my-flow
```

Creates a flow-specific configuration file for overriding settings per-flow.

**Config keys:**

| Key | Description |
|-----|-------------|
| `openrouter_api_key` | API key for LLM and agent steps (via OpenRouter) |
| `default_model` | Default model for LLM steps when not specified in the flow |

| Flag | Description |
|------|-------------|
| `action` | `get`, `set`, or `init` |
| `--stdin` | Read value from stdin (for `set`) |
| `--unmask` | Show full values (for `get`) |

---

### `stepwise init`

Create a `.stepwise/` project directory in the current folder.

```bash
stepwise init
stepwise init --force        # reinitialize existing project
stepwise init --no-skill     # skip agent skill installation
stepwise init --skill .claude # install agent skill to specific directory
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
| `--no-skill` | Skip agent skill installation |
| `--skill DIR` | Install agent skill to specific directory (e.g., `.claude` or `.agents`) |

---

### `stepwise templates`

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

### `stepwise schema`

Generate a JSON tool contract from a flow file. Shows what inputs the flow needs, what outputs it produces, and whether it has external steps.

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

For flows with external steps, includes the step name, prompt, and required fields:

```json
{
  "externalSteps": [
    {
      "step": "approve",
      "prompt": "Review this deployment package. Approve or reject.",
      "fields": ["approved", "reason"]
    }
  ]
}
```

---

### `stepwise diagram`

Generate a Graphviz DAG image from a flow file.

```bash
stepwise diagram my-flow.flow.yaml
stepwise diagram my-flow.flow.yaml -f png
stepwise diagram my-flow.flow.yaml -o output/my-flow.svg
stepwise diagram @alice:code-review              # registry flow
```

```
✓ my-flow.svg
```

Renders a dark-themed DAG matching the web UI aesthetic. Node shapes indicate executor type (box for script, parallelogram for external, rounded box for LLM, double octagon for agent, hexagon for poll). Edges are color-coded: blue for data flow, gray dashed for `after` ordering, amber dotted for loops, green bold for conditional advance, purple bold for for-each.

Requires the system `graphviz` package (`dot` binary). Install with `brew install graphviz` or `apt install graphviz`.

| Flag | Description |
|------|-------------|
| `-f, --format {svg,png,pdf}` | Output format (default: svg) |
| `-o, --output PATH` | Output file path (default: `<flow-name>.<format>` in cwd) |

---

## Utility Commands

### `stepwise agent-help`

Generate agent-readable instructions for all flows in the current project. Output is a markdown block suitable for pasting into CLAUDE.md or similar.

See [Agent Integration](agent-integration.md) for how agents use flows as tools.

```bash
stepwise agent-help                        # print to stdout (compact format)
stepwise agent-help --format full          # verbose output
stepwise agent-help --format json          # machine-readable JSON
stepwise agent-help --update CLAUDE.md     # update file in-place (uses full format)
stepwise agent-help --flows-dir ./flows    # scan a specific directory
```

The `--update` flag finds `<!-- stepwise-agent-help -->` / `<!-- /stepwise-agent-help -->` markers in the target file and replaces just that section. Creates the markers if they don't exist.

| Flag | Description |
|------|-------------|
| `--update FILE` | Update a file in-place between markers (uses `full` format) |
| `--flows-dir DIR` | Override flow discovery directory (scan only this dir) |
| `--format {compact,json,full}` | Output format: `compact` (default), `json`, or `full` |

---

### `stepwise flows`

List all flows discovered in the current project. Scans the project root, `flows/`, and `.stepwise/flows/` for `.flow.yaml` files.

```bash
stepwise flows
```

---

### `stepwise extensions`

List discovered extensions. Stepwise extensions are executables on `PATH` matching the `stepwise-*` naming convention.

```bash
stepwise extensions list
stepwise extensions list --refresh     # bypass cache, force fresh scan
```

| Flag | Description |
|------|-------------|
| `--refresh` | Bypass cache and force a fresh scan |

---

### `stepwise docs`

Print reference documentation to the terminal. Useful for quick lookups without leaving the CLI.

```bash
stepwise docs                  # list available topics
stepwise docs patterns         # show patterns reference
stepwise docs cli              # show CLI reference
stepwise docs executors        # show executor reference
```

| Argument | Description |
|----------|-------------|
| `topic` | Documentation topic (e.g., `patterns`, `cli`, `executors`). Omit to list available topics. |

---

### `stepwise cache`

Manage the step result cache. Cached results are stored in `.stepwise/cache/results.db`.

To bypass the cache for a specific step during a run, use `stepwise run --rerun <step>` (see [`run` flags](#stepwise-run)).

---

#### `stepwise cache stats`

Show cache entries, hits, size, and per-flow/step breakdown.

```bash
stepwise cache stats
```

#### `stepwise cache clear`

Clear cached results, optionally filtered by flow or step.

```bash
stepwise cache clear                       # clear all
stepwise cache clear --flow my-flow        # clear for a specific flow
stepwise cache clear --step fetch          # clear for a specific step
```

| Flag | Description |
|------|-------------|
| `--flow FLOW` | Filter by flow name |
| `--step STEP` | Filter by step name |

#### `stepwise cache debug`

Show the computed cache key for a step. Useful for understanding why a step is or isn't getting cache hits.

```bash
stepwise cache debug my-flow.flow.yaml fetch --input url="https://example.com"
```

| Argument | Description |
|----------|-------------|
| `flow` | Flow file path (positional) |
| `step` | Step name (positional) |

| Flag | Description |
|------|-------------|
| `--input KEY=VALUE` | Input variable (repeatable) |

---

### `stepwise update`

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

### `stepwise version`

Print the installed stepwise version and exit.

```bash
stepwise version
```

Equivalent to `stepwise --version`.

---

### `stepwise uninstall`

Remove stepwise from the current project. Deletes the `.stepwise/` directory and optionally the `flows/` directory and CLI tool.

```bash
stepwise uninstall                   # interactive confirmation
stepwise uninstall --yes             # skip confirmation
stepwise uninstall --remove-flows    # also remove flows/ directory
stepwise uninstall --cli             # also uninstall the CLI tool
stepwise uninstall --force           # proceed even with active/paused jobs
```

| Flag | Description |
|------|-------------|
| `--yes`, `-y` | Skip confirmation prompts |
| `--force` | Proceed even with active/paused jobs |
| `--remove-flows` | Also remove `flows/` directory |
| `--cli` | Also uninstall the stepwise CLI tool |

---

## Project Hooks

Hook scripts in `.stepwise/hooks/` are fired by the engine on key events. Each hook receives a JSON payload on stdin.

| Hook | Event | Trigger |
|------|-------|---------|
| `on-suspend` | `step.suspended` | A step is suspended (awaiting external input) |
| `on-complete` | `job.completed` | A job completes successfully |
| `on-fail` | `job.failed`, `step.failed` | A job or step fails |

**Payload fields:**
- All hooks: `event`, `hook`, `job_id`, `timestamp`
- `on-suspend` additionally: `step`, `run_id`, `watch_mode`, `prompt`, `fulfill_command`
- `on-fail` additionally: `step` (if step failure), `error`, `reason`

Hooks are fire-and-forget with a 30-second timeout. Failures are logged to `.stepwise/logs/hooks.log`. Hooks are scaffolded by `stepwise init` with commented examples.

**Example: Slack notification on suspension**

`.stepwise/hooks/on-suspend`:

```sh
#!/bin/sh
payload=$(cat)
step=$(echo "$payload" | jq -r '.step')
cmd=$(echo "$payload" | jq -r '.fulfill_command')
curl -s -X POST "$SLACK_WEBHOOK" -d "{\"text\":\"Step '$step' needs input: $cmd\"}"
```

---

## Server-Aware CLI

When the Stepwise server is running, CLI commands can automatically route through the server API instead of accessing SQLite directly. This prevents database locking conflicts.

**Detection:** The CLI checks `.stepwise/server.pid` and probes the health endpoint.

**Override flags:**
- `--standalone` — force direct SQLite mode (skip server detection)
- `--server URL` — force API mode with a specific server URL

The server writes `.stepwise/server.pid` on startup and removes it on clean shutdown.

---

## Signal Handling

Ctrl+C during stepwise run cleanly cancels the active job. Running agent processes are killed, the job is marked as cancelled, and the process exits. In --watch mode, Ctrl+C stops the ephemeral server.

## Project Discovery

Stepwise searches upward from the current directory for a `.stepwise/` folder, similar to how git searches for `.git/`. Use `--project-dir` to override:

```bash
stepwise --project-dir /path/to/project jobs
```
