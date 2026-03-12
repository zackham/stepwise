# CLI Reference

Stepwise is a CLI-first tool. All commands are available via `stepwise <command>`.

## Global Flags

| Flag | Description |
|------|-------------|
| `--version` | Print version and exit |
| `-v, --verbose` | Verbose output |
| `-q, --quiet` | Suppress non-essential output |
| `--project-dir PATH` | Use a specific project directory instead of searching from cwd |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | Job failed (step error, validation failure) |
| `2` | Usage error (bad arguments, missing file) |
| `3` | Configuration error |
| `4` | Project error (no `.stepwise/` found) |

**`--wait` mode exit codes** (override codes 3-4 for agent callers):

| Code | Meaning |
|------|---------|
| `0` | Completed successfully |
| `1` | Flow execution failed |
| `2` | Input validation error |
| `3` | Timeout (`--timeout` exceeded) |
| `4` | Cancelled |

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
```

Stepwise commands search upward from cwd for `.stepwise/` (like git searches for `.git/`).

| Flag | Description |
|------|-------------|
| `--force` | Reinitialize even if `.stepwise/` already exists |

---

## `stepwise run`

Execute a flow file. The primary command — three modes depending on flags.

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
stepwise run council.flow.yaml --wait --var question="Should we use Postgres?"
```

Blocks until the flow completes. Prints a single JSON object to stdout — nothing else. All logging goes to stderr. This is the primary integration path for agents calling flows as tools.

```bash
# With timeout (returns exit code 3 if exceeded)
stepwise run deploy.flow.yaml --wait --timeout 300 --var repo=/path --var branch=main

# Read long input from a file instead of shell-escaping
stepwise run review.flow.yaml --wait --var-file spec=spec.md
```

Exit codes for `--wait`: 0=success, 1=failed, 2=input error, 3=timeout, 4=cancelled.

### Async mode (--async)

```bash
stepwise run council.flow.yaml --async --var question="..."
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

| Flag | Description |
|------|-------------|
| `--output {table,json}` | Output format (default: table) |

---

## `stepwise cancel`

Cancel a running or paused job. Active agent processes are killed.

```bash
stepwise cancel job-e5f6g7h8
```

```
✓ Cancelled job-e5f6g7h8
```

Returns an error if the job is already completed, failed, or cancelled.

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

## `stepwise flow`

Flow sharing commands. Download flows from URLs, share to the registry (coming soon), or search for community flows.

### Download a flow

```bash
stepwise flow get https://example.com/code-review.flow.yaml
```

```
✓ Downloaded code-review.flow.yaml
  Run 'stepwise run code-review.flow.yaml' to execute.
```

Saves the file to the current directory. Fails if a file with the same name already exists.

### Share and search (coming soon)

```bash
stepwise flow share my-flow.flow.yaml    # publish to registry
stepwise flow search "agent review"      # search community flows
```

---

## `stepwise schema`

Generate a JSON tool contract from a flow file. Shows what inputs the flow needs, what outputs it produces, and whether it has human steps.

```bash
stepwise schema council.flow.yaml
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
stepwise output job-a1b2c3d4                   # terminal outputs only
stepwise output job-a1b2c3d4 --scope full      # per-step details + cost + events
```

| Flag | Description |
|------|-------------|
| `--scope {default,full}` | Output scope (default: terminal outputs only) |

---

## `stepwise fulfill`

Satisfy a suspended human step from the command line. Used by agents to complete human-in-the-loop steps programmatically.

```bash
stepwise fulfill run-abc12345 '{"approved": true, "reason": "looks good"}'

# Read payload from stdin (useful for large payloads or piping)
echo '{"approved": true}' | stepwise fulfill run-abc12345 --stdin
cat payload.json | stepwise fulfill run-abc12345 -
```

```json
{"status": "fulfilled", "run_id": "run-abc12345"}
```

The run ID comes from `stepwise output` (which shows `suspended_steps` for running jobs) or from a `--wait --timeout` response.

| Flag | Description |
|------|-------------|
| `--stdin` | Read JSON payload from stdin instead of positional argument |

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

## Signal Handling

`Ctrl+C` during `stepwise run` cleanly cancels the active job. Running agent processes are killed, the job is marked as cancelled, and the process exits. In `--watch` mode, Ctrl+C stops the ephemeral server.

## Project Discovery

Stepwise searches upward from the current directory for a `.stepwise/` folder, similar to how git searches for `.git/`. Use `--project-dir` to override:

```bash
stepwise --project-dir /path/to/project jobs
```
