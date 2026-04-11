# CLI Reference

Stepwise is a CLI-first tool. All commands are available via `stepwise <command>`.

See [Quickstart](quickstart.md) for installation and first-run instructions. See [Writing Flows](writing-flows.md) for flow authorship, and [Troubleshooting](troubleshooting.md) for error messages and diagnostic commands.

## Overview

| Group | Commands |
|-------|----------|
| [Core](#core-commands) | `run`, `open`, `new`, `validate`, `check`, `preflight`, `test-fixture` |
| [Jobs](#job-commands) | `jobs`, `status`, `output`, `tail`, `logs`, `wait`, `cancel`, `fulfill`, `list` |
| [Job Lifecycle](#job-lifecycle-commands) | `archive`, `unarchive`, `rm` |
| [Job Staging](#job-staging-commands) | `job create`, `job show`, `job run`, `job dep`, `job approve`, `job cancel`, `job rm` |
| [Server](#server-commands) | `server start`, `server stop`, `server restart`, `server status` |
| [Registry](#registry-commands) | `share`, `get`, `search`, `info`, `login`, `logout` |
| [Configuration](#configuration-commands) | `config`, `init`, `templates`, `schema`, `diagram` |
| [Containment](#containment-commands) | `doctor --containment`, `build-rootfs`, `audit`, `vmmd start/stop/status` |
| [Utility](#utility-commands) | `agent-help`, `catalog`, `flows`, `extensions`, `docs`, `cache`, `help`, `version`, `update`, `uninstall` |

## Common Workflows

Quick recipes for the most frequent tasks.

### Run a flow and watch it live

```bash
stepwise run my-flow --watch --input task="build the API" --name "impl: API"
```

Opens the [web UI](web-ui.md) with real-time DAG visualization. External steps prompt in the browser.

### Run the demo flow

```bash
stepwise run @stepwise:demo --watch
```

Downloads and runs the demo flow from the registry with the web UI.

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
stepwise job run --group sprint-1 --wait  # release and wait for all to complete
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

The `<flow>` argument accepts flow names (e.g., `my-flow`), file paths (e.g., `flows/my-flow.flow.yaml`), directory paths (e.g., `flows/my-flow/`), or registry refs (e.g., `@alice:code-review`). Names are resolved across the project root, `flows/`, and `.stepwise/flows/`.

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
stepwise run my-flow.flow.yaml --input spec=@spec.md      # @ prefix reads from file
stepwise run my-flow.flow.yaml --vars-file inputs.yaml
```

Input variables are available in steps via `$job.field_name`.

#### Agent mode (--wait)

```bash
stepwise run council --wait --input question="Should we use Postgres?"
```

Blocks until the flow completes. Prints a single JSON object to stdout — nothing else. All logging goes to stderr. This is the primary integration path for agents calling flows as tools.

Exit codes in wait mode: 0=success, 1=failed, 2=input error, 4=cancelled, 5=suspended.

When a flow has external steps and `--wait` is used, the command returns exit code 5 with a JSON response containing `suspended_steps` — each with `run_id`, `prompt`, and `fields`. Use `stepwise fulfill <run-id> '{...}' --wait` to satisfy the step and continue blocking.

#### Async mode (--async)

```bash
stepwise run council --async --input question="..."
```

Returns `{"job_id": "job-a1b2c3d4", "status": "running"}`.

Fire-and-forget. Spawns a detached background process (no server required), returns the job ID immediately. Poll with `stepwise status` or retrieve results with `stepwise output`.

#### JSON output (--output json)

```bash
stepwise run my-flow.flow.yaml --output json --input k=v
```

Same as headless mode (shows step progress to stderr) but prints structured JSON result to stdout on completion.

| Flag | Description |
|------|-------------|
| `--watch` | Ephemeral server + browser UI |
| `--wait` | Block until completion, JSON output on stdout |
| `--async` | Fire-and-forget, returns job_id immediately |
| `--output json` | Print structured JSON result to stdout on completion |
| `--input KEY=VALUE` | Pass input variable (repeatable) |
| `--input KEY=@PATH` | Pass input from file contents (`@` prefix, repeatable) |
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
| `--containment BACKEND` | Run agent steps with containment isolation (e.g., `cloud-hypervisor`). See [Containment](containment.md) |
| `--meta KEY=VALUE` | Set job metadata (dot notation: `sys.origin=cli`, `app.project=foo`) |

---

### `stepwise open`

Open a flow or job in the web UI. Starts an ephemeral server if none is running.

```bash
stepwise open my-flow                    # open flow detail page
stepwise open @stepwise:demo             # open registry flow (auto-fetches)
stepwise open flows/my-flow/FLOW.yaml    # open flow by path
stepwise open job-abc123                 # open job detail page
```

| Flag | Description |
|------|-------------|
| `--port INT` | Override port for ephemeral server |
| `--url` | Print URL instead of opening browser |

---

### `stepwise new`

Create a new flow directory with a minimal `FLOW.yaml` template.

```bash
stepwise new my-flow                     # creates flows/my-flow/FLOW.yaml
stepwise new swdev/my-flow               # creates flows/swdev/my-flow/FLOW.yaml (inside kit)
```

```
Created flows/my-flow/FLOW.yaml
```

Creates `flows/<name>/FLOW.yaml` in the current project. Use `kit/flow` syntax to create a flow inside a kit directory. The name must match `[a-zA-Z0-9_-]+`. Fails if the directory already exists.

---

### `stepwise validate`

Check a flow file for syntax, structural, and coordination errors without running it.

```bash
stepwise validate my-flow.flow.yaml
```

**Structural errors (fatal):** YAML syntax errors, missing step references, invalid input bindings, bad exit rule targets, undeclared outputs, unbounded loops, uncovered output combinations.

**Coordination warnings:** The validator also checks session-writer safety, loop-back binding guards, and cycle detection:
- `pair_unsafe` — two steps write to the same session without provable non-concurrency. Fix: add an `after:` chain or use predicate-form `when:` clauses to prove mutual exclusion.
- `cyclic_dependency` — steps form a cycle with no `action: loop` exit rule to break it.
- `loop_back_binding_ambiguous_closure` — a loop-back input has no `optional:`, `any_of`, or `is_present:` guard for iter-1.

---

### `stepwise check`

Verify model resolution for a flow. Confirms that all LLM/agent steps can resolve their configured models against available model aliases and API keys.

```bash
stepwise check my-flow.flow.yaml
```

---

### `stepwise preflight`

Pre-run check: validates config, requirements, and model resolution for a flow. A more thorough version of `check` that also verifies external dependencies.

```bash
stepwise preflight my-flow.flow.yaml
stepwise preflight my-flow.flow.yaml --input api_key=sk-test
```

| Flag | Description |
|------|-------------|
| `--input KEY=VALUE` | Variable override (repeatable) |

---

### `stepwise test-fixture`

Generate a pytest test harness for a flow. Produces a ready-to-run test file with `CallableExecutor` stubs for each step, `WorkflowDefinition` matching the flow, and assertions on expected outputs.

```bash
stepwise test-fixture my-flow.flow.yaml               # print to stdout
stepwise test-fixture my-flow.flow.yaml -o tests/test_my_flow.py
```

| Flag | Description |
|------|-------------|
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

Status icons: `checkmark` completed, `x` failed, `spinner` running, `diamond` suspended (waiting for external input), `arrow` delegated (sub-job), `circle` pending.

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

| Flag | Description |
|------|-------------|
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

---

### `stepwise logs`

Show the full event history for a job. Unlike `tail`, this prints all past events and exits immediately.

```bash
stepwise logs job-a1b2c3d4
```

---

### `stepwise wait`

Block until one or more existing jobs reach a terminal state or all progress is blocked by suspensions.

```bash
stepwise wait job-a1b2c3d4
stepwise wait job-a1b2c3d4 job-e5f6g7h8 --all     # wait for all to finish
stepwise wait job-a1b2c3d4 job-e5f6g7h8 --any     # wait for first to finish
```

Returns the same JSON format as `stepwise run --wait`. Exit code 0 for completion, 1 for failure, 5 for suspension.

Unlike `stepwise run --wait` (which creates and waits on a new job), `stepwise wait` attaches to an already-running job.

| Flag | Description |
|------|-------------|
| `--all` | Wait for all specified jobs to reach terminal state |
| `--any` | Wait for the first specified job to reach terminal state |

---

### `stepwise cancel`

Cancel a running or paused job. Active agent processes are killed.

```bash
stepwise cancel job-e5f6g7h8
stepwise cancel job-e5f6g7h8 --output json
```

**JSON output** returns `{job_id, status, completed_steps, cancelled_steps, remaining_steps}`.

| Flag | Description |
|------|-------------|
| `--output {table,json}` | Output format (default: table) |

---

### `stepwise fulfill`

Satisfy a suspended external step from the command line. Used by agents to complete external steps programmatically.

```bash
stepwise fulfill run-abc12345 '{"approved": true, "reason": "looks good"}'
echo '{"approved": true}' | stepwise fulfill run-abc12345 --stdin
cat payload.json | stepwise fulfill run-abc12345 -
stepwise fulfill run-abc12345 '{"approved": true}' --wait
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

## Job Lifecycle Commands

### `stepwise archive`

Archive completed, failed, or cancelled jobs. Archived jobs are hidden from `stepwise jobs` but preserved in the database.

```bash
stepwise archive job-a1b2c3d4                     # archive specific job(s)
stepwise archive --status completed                # archive all completed jobs
stepwise archive --group sprint-1                  # archive all terminal jobs in a group
```

| Flag | Description |
|------|-------------|
| `--status STR` | Archive all jobs with this status (completed, failed, cancelled) |
| `--group NAME` | Archive all terminal jobs in this group |
| `--output {table,json}` | Output format (default: table) |

---

### `stepwise unarchive`

Restore archived jobs to their original status.

```bash
stepwise unarchive job-a1b2c3d4
```

| Flag | Description |
|------|-------------|
| `--output {table,json}` | Output format (default: table) |

---

### `stepwise rm`

Permanently delete jobs from the database. Irreversible.

```bash
stepwise rm job-a1b2c3d4                          # delete specific job(s)
stepwise rm --status failed                        # delete all failed jobs
stepwise rm --group old-batch                      # delete all jobs in a group
stepwise rm --archived                             # delete all archived jobs
stepwise rm --archived --force                     # skip confirmation
```

| Flag | Description |
|------|-------------|
| `--status STR` | Delete all jobs with this status |
| `--group NAME` | Delete all jobs in this group |
| `--archived` | Delete all archived jobs |
| `--force, -f` | Skip confirmation for bulk deletes |
| `--output {table,json}` | Output format (default: table) |

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
stepwise job run --group wave-1 --wait     # release and block until all complete
```

| Flag | Description |
|------|-------------|
| `--group NAME` | Transition all staged jobs in this group |
| `--wait` | Block until all released jobs reach a terminal state (JSON output on stdout) |

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

### `stepwise job approve`

Approve a job that is in `awaiting_approval` status.

```bash
stepwise job approve job-abc123
```

| Flag | Description |
|------|-------------|
| `--output {table,json}` | Output format (default: table) |

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

Commands for sharing and discovering flows and kits on the [stepwise.run](https://stepwise.run) registry.

See [Flow and Kit Sharing](flow-sharing.md) for detailed publishing and discovery workflows.

### `stepwise share`

Publish a flow or kit to the registry. Auto-detects kits by the presence of `KIT.yaml` in the target directory.

```bash
stepwise share my-pipeline.flow.yaml        # share a flow
stepwise share swdev                         # share a kit (has KIT.yaml)
```

For flows: validates, reads metadata, and uploads YAML + co-located files. For kits: validates KIT.yaml and all bundled flows, collects everything, and uploads as a kit bundle.

| Flag | Description |
|------|-------------|
| `--author NAME` | Override author (default: from YAML or git config) |
| `--update` | Update a previously published flow or kit (uses stored token) |

---

### `stepwise get`

Download a flow or kit from the registry or a URL. Tries flow lookup first, falls back to kit.

```bash
stepwise get code-review                     # flow (by name)
stepwise get swdev                           # kit (auto-detected via fallback)
stepwise get @zack:swdev                     # kit (by author:name)
stepwise get https://example.com/flow.yaml   # flow (by URL)
```

Flows are saved to `.stepwise/registry/@author/slug/`. Kits install `KIT.yaml` plus all bundled flow subdirectories. Registry includes listed in the kit's `KIT.yaml` are auto-fetched.

| Flag | Description |
|------|-------------|
| `--force` | Overwrite existing file |

---

### `stepwise search`

Search the flow and kit registry. Results include a TYPE column distinguishing flows from kits.

```bash
stepwise search "code review"
stepwise search --tag agent --sort downloads
```

```
TYPE   NAME            AUTHOR   STEPS  DOWNLOADS
flow   code-review     zack     3      1,247
kit    swdev           zack     5      634
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

Shows metadata for a published flow without downloading it (name, author, description, step count, downloads).

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
| `anthropic_api_key` | API key for Anthropic direct access |
| `default_model` | Default model for LLM steps when not specified in the flow |
| `default_agent` | Default agent runtime (`claude`, `codex`, etc.) |
| `billing` | `subscription` (default) or `api_key` |
| `max_concurrent_jobs` | Max simultaneous running jobs (default: 10) |
| `max_concurrent_agents` | Max simultaneous agent processes (default: 3) |
| `agent_process_ttl` | Global safety-net timeout for agent processes in seconds. `0` = disabled (default). Agent steps run until done. Use per-step `limits.max_duration_minutes` for intentional timeouts |
| `agent_permissions` | `approve_all` (default), `prompt`, or `deny` |

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

For flows with external steps, includes the step name, prompt, and required fields.

---

### `stepwise diagram`

Generate a Graphviz DAG image from a flow file.

```bash
stepwise diagram my-flow.flow.yaml
stepwise diagram my-flow.flow.yaml -f png
stepwise diagram my-flow.flow.yaml -o output/my-flow.svg
stepwise diagram @alice:code-review              # registry flow
```

Renders a dark-themed DAG matching the web UI aesthetic. Requires the system `graphviz` package (`dot` binary). Install with `brew install graphviz` or `apt install graphviz`.

| Flag | Description |
|------|-------------|
| `-f, --format {svg,png,pdf}` | Output format (default: svg) |
| `-o, --output PATH` | Output file path (default: `<flow-name>.<format>` in cwd) |

---

## Containment Commands

Commands for running agent steps inside hardware-isolated microVMs. See the [Containment guide](containment.md) for architecture, security model, and configuration.

### `stepwise doctor --containment`

Check containment prerequisites: KVM availability, cloud-hypervisor binary, virtiofsd binary, vhost_vsock kernel module, and guest kernel.

```bash
stepwise doctor --containment
```

Reports pass/fail for each requirement with fix instructions for failures.

---

### `stepwise build-rootfs`

Build the Alpine-based ext4 rootfs image used by containment VMs. The image includes Python, Node.js, and ACP adapter packages. Stored at `~/.stepwise/vmm/rootfs.ext4`.

```bash
stepwise build-rootfs
stepwise build-rootfs --size 2048              # custom size in MB (default: 1024)
stepwise build-rootfs --output /path/to/rootfs.ext4  # custom output path
stepwise build-rootfs --no-node                # omit Node.js from the image
stepwise build-rootfs --no-python              # omit Python from the image
```

| Flag | Description |
|------|-------------|
| `--size MB` | Root filesystem size in megabytes (default: 1024) |
| `--output PATH` | Custom output path (default: `~/.stepwise/vmm/rootfs.ext4`) |
| `--no-node` | Exclude Node.js from the rootfs image |
| `--no-python` | Exclude Python from the rootfs image |

---

### `stepwise audit`

Show the containment security profile of a flow. Reports which steps run in VMs, which run on host, how many VM groups exist, and what each group can access.

```bash
stepwise audit my-flow.yaml
```

---

### `stepwise vmmd`

Manage the VM manager daemon (vmmd). The daemon runs as root and handles all privileged operations: virtiofsd, cloud-hypervisor, shared memory mapping. Stepwise runs unprivileged and talks to vmmd via a Unix socket.

#### `stepwise vmmd start`

Start the VM manager daemon.

```bash
sudo stepwise vmmd start                       # foreground
sudo stepwise vmmd start --detach              # background daemon
sudo stepwise vmmd start --detach --work-dir /path/to/vmm  # custom working directory
```

| Flag | Description |
|------|-------------|
| `--detach` | Run as a background daemon |
| `--work-dir DIR` | Override the vmmd working directory (default: `~/.stepwise/vmm`) |

#### `stepwise vmmd stop`

Stop the VM manager daemon. Shuts down all running VMs.

```bash
stepwise vmmd stop
```

#### `stepwise vmmd status`

Show whether the daemon is running, its PID, socket path, and active VM count.

```bash
stepwise vmmd status
```

---

### Running flows with containment

Use the `--containment` flag on `stepwise run` to enable hardware isolation for all agent steps in a flow:

```bash
stepwise run my-flow --containment cloud-hypervisor
```

This can also be set per-step, per-flow, or per-agent in configuration. See [Containment: Enable containment](containment.md#enable-containment) for the full override chain.

---

## Utility Commands

### `stepwise agent-help`

Generate agent-readable instructions for all flows in the current project. Output is a markdown block suitable for pasting into CLAUDE.md or similar.

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

### `stepwise catalog`

Generate a kit/flow catalog for documentation (e.g., SKILL.md). Lists all kits and their member flows with descriptions and input/output summaries.

```bash
stepwise catalog                             # print to stdout
stepwise catalog -o SKILL.md                 # write to file
```

| Flag | Description |
|------|-------------|
| `-o, --output FILE` | Write to file instead of stdout |

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

---

### `stepwise help`

Ask a question about Stepwise in natural language. Uses LLM to answer based on the documentation.

```bash
stepwise help "how do I add a retry loop?"
stepwise help "what are exit rules?"
```

---

### `stepwise cache`

Manage the step result cache. Cached results are stored in `.stepwise/cache/results.db`.

To bypass the cache for a specific step during a run, use `stepwise run --rerun <step>`.

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

---

### `stepwise update`

Upgrade stepwise to the latest version. Automatically detects the install method (uv, pipx, or pip) and runs the appropriate upgrade command.

```bash
stepwise update
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

---

## Signal Handling

Ctrl+C during `stepwise run` cleanly cancels the active job. Running agent processes are killed, the job is marked as cancelled, and the process exits. In `--watch` mode, Ctrl+C stops the ephemeral server.

## Project Discovery

Stepwise searches upward from the current directory for a `.stepwise/` folder, similar to how git searches for `.git/`. Use `--project-dir` to override:

```bash
stepwise --project-dir /path/to/project jobs
```
