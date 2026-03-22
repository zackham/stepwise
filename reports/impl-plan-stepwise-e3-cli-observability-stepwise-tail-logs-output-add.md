---
title: "Implementation Plan: E3 — CLI Observability (stepwise tail, logs, output)"
date: "2026-03-22T00:15:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# E3: CLI Observability — stepwise tail, logs, output

## Overview

Add three CLI commands (`tail`, `logs`, `output` extension) that let agent orchestrators and humans inspect running and completed jobs from the terminal. `tail` streams live events via the E2 WebSocket event stream; `logs` dumps full event history and exits; `output` is extended to accept a positional step-name argument for quick single-step access.

## Requirements

### R1: `stepwise tail <job-id>`
- **AC1**: Connects to the server's WebSocket event stream at `/api/v1/events/stream?job_id=<id>&since_job_start=true`
- **AC2**: Replays all historical events for the job first, prints a separator line after receiving the `sys.replay.complete` boundary frame, then continues with live events
- **AC3**: Each event line is formatted as `[HH:MM:SS] <event.type>  <step-name>  <details>` — timestamp is local time, event type padded to 18 chars for alignment, step name padded to max step name width
- **AC4**: Exits automatically when a terminal job event is received (`job.completed`, `job.failed`, `job.paused`). Returns `EXIT_SUCCESS` (0) for completed, `EXIT_JOB_FAILED` (1) for failed, `EXIT_SUCCESS` for paused.
- **AC5**: Exits cleanly on Ctrl+C with exit code 130 (standard SIGINT convention)
- **AC6**: If the job is already complete when `tail` connects, replays all events (including the terminal event) and exits immediately after
- **AC7**: Requires a running server — prints `"stepwise tail requires a running server. Start one with: stepwise server start"` to stderr and returns `EXIT_CONFIG_ERROR` (3) if no server detected

### R2: `stepwise logs <job-id>`
- **AC1**: Prints a job summary header: `Job: <id> (<name or objective>)`, `Status: <status>`, `Duration: <Xm Ys>`, blank line separator
- **AC2**: Lists all events chronologically with same line format as `tail` (shared formatter)
- **AC3**: Works against a running server (via REST `GET /api/jobs/{id}/events` + `GET /api/jobs/{id}/status`) or falls back to direct SQLite (`store.load_events()` + `store.load_job()`) — same dual-mode pattern as `cmd_output`
- **AC4**: Non-streaming — dumps everything and exits with `EXIT_SUCCESS`
- **AC5**: Returns `EXIT_JOB_FAILED` with error message to stderr if job not found

### R3: `stepwise output <job-id> [step-name]`
- **AC1**: Existing `stepwise output <job-id>` behavior is byte-for-byte unchanged (all existing tests pass)
- **AC2**: `stepwise output <job-id> <step-name>` prints the step's output artifact as pretty-printed JSON — raw artifact, not wrapped in `{step: artifact}` dict, enabling `stepwise output job-123 plan | jq .result`
- **AC3**: `stepwise output <job-id> --step a,b` continues to work identically (backward compat — `--step` flag takes priority over positional)
- **AC4**: When positional step-name refers to a nonexistent step, prints JSON error and returns `EXIT_JOB_FAILED`
- **AC5**: Positional step-name routes through server when available (same `_try_server` pattern as existing `--step`)

## Assumptions

| # | Assumption | Verification |
|---|-----------|-------------|
| A1 | `websockets` is already a core dependency | `pyproject.toml:18` — `"websockets>=14.0"` in `[project.dependencies]` |
| A2 | The WebSocket event stream (E2) is shipped and working | `server.py:2307-2423` — `@app.websocket("/api/v1/events/stream")` with replay, boundary, and live phases |
| A3 | Events REST endpoint exists at `GET /api/jobs/{id}/events` | `server.py:903-911` — returns `[e.to_dict() for e in events]` |
| A4 | `store.load_events(job_id)` returns `list[Event]` ordered by timestamp | `store.py:546-557` — `SELECT * FROM events WHERE job_id = ? ORDER BY timestamp` |
| A5 | `store.load_job(job_id)` returns `Job` object, raises `KeyError` if not found | `store.py:187` — confirmed by `test_cli_jobs.py:107-108` usage pattern |
| A6 | Event `data` dict contains `"step"` key for step-level events | Verified by emission sites: `engine.py:1228-1231` (started), `engine.py:1387-1390` (completed), `engine.py:1405-1410` (suspended), `engine.py:2394-2398` (failed) |
| A7 | Event `data` for `step.completed` does **not** contain duration — duration must be computed from `StepRun.started_at/completed_at` | Verified: `engine.py:1387-1390` emits only `{"step": name, "attempt": attempt}` — no duration field. Duration calculation pattern at `cli.py:2083` |
| A8 | WS envelope format differs from REST `Event.to_dict()` format | WS envelope: `{"event", "job_id", "timestamp", "event_id", "metadata", "data", "step"}` per `hooks.py:39-50`. REST: `{"id", "job_id", "timestamp", "type", "data", "is_effector"}` per `models.py:1404-1412`. Must normalize in formatter. |
| A9 | `_try_server` / `_detect_server_url` is the standard server-or-fallback pattern | `cli.py:216-238` — used by `cmd_output` (line 2848), `cmd_list`, `cmd_status`, `cmd_cancel` |
| A10 | The existing `output` command's `job_id` is `nargs="?"` (optional) | `cli.py:3558` — `p_output.add_argument("job_id", nargs="?", default=None)` |
| A11 | `StepwiseClient` has no `events()` or `get_job()` method yet | `api_client.py:23-170` — only `jobs()`, `status()`, `output()`, `cancel()`, `fulfill()`, `list_suspended()`, `health()`, `wait()` |
| A12 | Terminal job events are `job.completed`, `job.failed`, `job.paused` | `events.py:12-14` — `JOB_COMPLETED`, `JOB_FAILED`, `JOB_PAUSED`. Emitted at: `engine.py:813,3021` (completed), `engine.py:825,2069,2465,3032` (failed), `engine.py:211,2034,2058,2435` (paused) |

## Out of Scope

- **Colorized/ANSI output** — event lines are plain text. Color can be layered on in a future PR without changing the format contract.
- **`--output json` machine-consumable mode** for `tail`/`logs` — the current JSON event format is available via the REST API and WS directly; CLI is human-focused.
- **Event type filtering** (`--type step.completed`) — all events are shown. Filtering adds argparse complexity and can be added later.
- **Following sub-jobs** (recursive tail) — only events for the specified job ID. Sub-job tailing can be a future enhancement.
- **Agent output streaming within `tail`** — that's NDJSON file tailing, handled by `AgentStreamView` in the web UI. Out of scope for this CLI command.

## Architecture

### Where commands fit

All three commands follow the established CLI handler pattern (`cli.py:241+`):
1. Handler function: `def cmd_<name>(args: Namespace) -> int` — returns exit code constant
2. Subparser registered in `build_parser()` (`cli.py:3409-3635`)
3. Entry in `handlers` dict in `main()` (`cli.py:3826-3856`)

This is the exact same pattern used by all 25+ existing commands.

### Data flow

```
tail:   CLI → _detect_server_url() → ws://host:port/api/v1/events/stream?... → _format_event_line() → print
logs:   CLI → _try_server(client.events()) OR store.load_events() → _format_event_line() → print
output: CLI → existing cmd_output logic + new positional step-name arg → JSON print
```

### Shared event line formatting

`tail` and `logs` print identical event lines via a shared `_format_event_line()` function. This normalizes the two envelope formats:

**WS envelope** (from `hooks.py:39-50`): `{"event": "step.started", "timestamp": "...", "data": {"step": "plan", ...}, "step": "plan"}`

**REST Event.to_dict()** (from `models.py:1404-1412`): `{"type": "step.started", "timestamp": "...", "data": {"step": "plan", ...}}`

The formatter accepts either format, extracting:
- Event type from `envelope.get("event") or envelope.get("type")`
- Timestamp from `envelope["timestamp"]` (ISO format string)
- Step name from `envelope.get("step") or envelope.get("data", {}).get("step", "")`
- Detail data from `envelope.get("data", {})`

### `tail` WebSocket connection

Uses `websockets` library directly (same as `runner.py:271`). Synchronous CLI handler calls `asyncio.run()` to execute the async WebSocket loop — this is safe because CLI commands run in the main thread with no existing event loop (same pattern as `runner.py:232`).

The URL is **not** constructed via `runner.py:_ws_url_from_server()` — that function builds `/ws` (the legacy broadcast endpoint). Instead, `tail` constructs the event stream URL directly: `ws://host:port/api/v1/events/stream?job_id=X&since_job_start=true`.

### `logs` dual-mode pattern

Follows `cmd_output`'s server-or-fallback pattern exactly (`cli.py:2843-2858`):
1. Call `_try_server(args, lambda c: ...)` with API client methods
2. If server unavailable, fall back to `_find_project_or_exit()` → `SQLiteStore` → direct queries
3. Format and print regardless of source

### `output` positional step-name

Adding `step_name` as `nargs="?"` after the existing `job_id` (also `nargs="?"`) in argparse. Since `job_id` already has `nargs="?"`, argparse assigns positional values left-to-right: first to `job_id`, second to `step_name`. When only one positional is given, it goes to `job_id` (unchanged behavior).

In `cmd_output()`, if `args.step_name` is set and `args.step` (the `--step` flag) is not, route to a new single-step output path that prints raw artifact JSON.

## Implementation Steps

### Step 1: Add `events()` method to `StepwiseClient` (~5 min)

**File:** `src/stepwise/api_client.py` (after `output()` method at line 114)

Add:
```python
def events(self, job_id: str) -> list[dict]:
    return self._request("GET", f"/api/jobs/{job_id}/events")
```

This is needed by `cmd_logs` server path. Follows the exact same pattern as every other method in the class.

**Depends on:** Nothing. Independent.

### Step 2: Add shared event formatting functions (~20 min)

**File:** `src/stepwise/cli.py` (new helper functions near line 241, after `_try_server()`)

Add two functions:

**`_format_event_line(envelope: dict) -> str`**:
- Parse timestamp: `datetime.fromisoformat(envelope["timestamp"]).strftime("%H:%M:%S")`
- Extract event type: `envelope.get("event") or envelope.get("type", "unknown")`
- Extract step: `envelope.get("step") or envelope.get("data", {}).get("step", "")`
- Generate detail string based on event type:
  - `step.completed`: `f"({data.get('attempt', 1)})" + ", from_cache" if data.get("from_cache")`
  - `step.suspended`: `data.get("prompt", "Awaiting external input")`
  - `step.failed`: `data.get("error", "")[:80]` (truncated to 80 chars)
  - `step.delegated`: `"delegated to sub-flow"`
  - `exit.resolved`: `f"{data.get('action', '')} → {data.get('target', '')}"` if target present
  - `loop.iteration`: `f"attempt {data.get('attempt', '?')}"`
  - `job.failed`: `data.get("reason", "")`
  - Default: empty string
- Return: `f"[{timestamp}] {event_type:<18s} {step:<16s} {detail}"`

**`_format_job_header(job_data: dict) -> str`** (for `logs`):
- Takes a dict with `id`, `status`, `name`/`objective`, `created_at`, `completed_at` (or current time)
- Computes duration from timestamps
- Returns multi-line: `"Job: {id} ({name})\nStatus: {status}\nDuration: {dur}\n"`

**Depends on:** Nothing. Independent.

### Step 3: Implement `cmd_tail` (~30 min)

**File:** `src/stepwise/cli.py`

**`cmd_tail(args: Namespace) -> int`**:
1. `server_url = _detect_server_url(args)` — if `None`, print error to stderr, return `EXIT_CONFIG_ERROR` (3)
2. Build WS URL: replace `http://` → `ws://` or `https://` → `wss://`, append `/api/v1/events/stream?job_id={args.job_id}&since_job_start=true`
3. `return asyncio.run(_tail_ws(ws_url))`

**`async _tail_ws(ws_url: str) -> int`**:
1. `import websockets`
2. `try: async with websockets.connect(ws_url) as ws:`
3. Inner loop: `msg = await ws.recv()` → `envelope = json.loads(msg)`
4. If `envelope.get("type") == "sys.replay.complete"`: print `"--- replay complete ---"`, continue
5. `print(_format_event_line(envelope), flush=True)`
6. `event_type = envelope.get("event", "")` — if in `{"job.completed", "job.failed", "job.paused"}`:
   - Return `EXIT_JOB_FAILED` if `"job.failed"`, else `EXIT_SUCCESS`
7. Outer: `except websockets.exceptions.ConnectionClosed: return EXIT_JOB_FAILED`
8. Wrap entire function body in `try/except KeyboardInterrupt: return 130`

**Depends on:** Step 2 (uses `_format_event_line`).

### Step 4: Implement `cmd_logs` (~30 min)

**File:** `src/stepwise/cli.py`

**`cmd_logs(args: Namespace) -> int`**:

**Server path** (following `cmd_output` pattern at line 2846-2858):
```python
data, code = _try_server(args, lambda c: {
    "job": c.status(args.job_id),
    "events": c.events(args.job_id),
})
```
If `code is not None`: format and print header from `data["job"]`, then each event from `data["events"]` via `_format_event_line()`. REST events use `Event.to_dict()` format (key is `"type"`, not `"event"`), which `_format_event_line()` handles via normalization.

**Direct SQLite path** (following `cmd_status` pattern at lines 2025-2091):
1. `project = _find_project_or_exit(args)`
2. `store = SQLiteStore(str(project.db_path))`
3. `job = store.load_job(args.job_id)` — wrap in try/except KeyError
4. `events = store.load_events(args.job_id)`
5. Build header dict from `Job` fields: `{"id": job.id, "status": job.status.value, "name": job.name or job.objective, "created_at": job.created_at.isoformat(), "completed_at": ...}`
6. Print header, then format each `Event` by converting to dict via `event.to_dict()` and passing to `_format_event_line()`
7. `store.close()` in finally block

**Depends on:** Step 1 (uses `client.events()`), Step 2 (uses `_format_event_line`, `_format_job_header`).

### Step 5: Extend `cmd_output` with positional step name (~15 min)

**File:** `src/stepwise/cli.py`

**Parser change** in `build_parser()` (after `p_output` `job_id` arg at line 3558):
```python
p_output.add_argument("step_name", nargs="?", default=None,
                       help="Step name (positional shorthand for --step)")
```

**Handler change** in `cmd_output()` (early in the function, before the `_try_server` call at line 2846):
- If `args.step_name` and not `args.step`: set `args.step = args.step_name` and set a flag `raw_output = True`
- In the step-retrieval block (line 2888+): when `raw_output` is True, print only the artifact dict (not wrapped in `{step: artifact}`)
- Server path: pass `step=args.step_name` to `client.output()` and unwrap the response to get just the step's artifact

**Depends on:** Nothing (modifies existing code only). Independent of Steps 1-4.

### Step 6: Register commands in parser and handler dict (~5 min)

**File:** `src/stepwise/cli.py`

In `build_parser()` (between the `schema` and `output` subparsers, around line 3555):
```python
# tail
p_tail = sub.add_parser("tail", help="Stream live events for a job")
p_tail.add_argument("job_id", help="Job ID to tail")

# logs
p_logs = sub.add_parser("logs", help="Show full event history for a job")
p_logs.add_argument("job_id", help="Job ID")
```

In `handlers` dict at line 3826, add:
```python
"tail": cmd_tail,
"logs": cmd_logs,
```

**Depends on:** Steps 3, 4 (the handler functions must exist).

### Step 7: Write tests — format functions (~20 min)

**File:** `tests/test_cli_observability.py` (new)

**`TestFormatEventLine`** — pure unit tests, no I/O:
- `test_step_started_format`: Input `{"type": "step.started", "timestamp": "2026-03-21T12:05:01", "data": {"step": "plan", "attempt": 1}}` → assert output matches `"[12:05:01] step.started       plan             "`
- `test_step_completed_format`: Same but with `step.completed`, verify attempt shown
- `test_step_completed_from_cache`: Verify `"from_cache"` annotation when `data.from_cache=True`
- `test_step_suspended_format`: Verify prompt text from `data.prompt` is included
- `test_step_failed_format`: Verify error message from `data.error` is included and truncated at 80 chars
- `test_job_completed_format`: Verify no step name, no crash on empty data
- `test_job_failed_format`: Verify reason from `data.reason`
- `test_ws_envelope_format`: Input with `"event"` key (WS format) instead of `"type"` — same output
- `test_exit_resolved_format`: Input with `exit.resolved`, verify action + target shown
- `test_loop_iteration_format`: Verify attempt number shown

**`TestFormatJobHeader`**:
- `test_completed_job_header`: Verify ID, status, duration present
- `test_running_job_header`: Verify "running" status, duration shows elapsed so far

### Step 8: Write tests — logs command (~25 min)

**File:** `tests/test_cli_observability.py`

Follow the pattern from `test_cli_jobs.py:26-33` (`_setup_project_with_jobs()`):

**`TestLogs`**:
- `test_logs_completed_job`: Create project, run simple flow via `run_flow()`, get job_id from store. Call `main(["logs", job_id])`. Assert: output contains job ID, "completed" status, `step.started` and `step.completed` lines for the step, chronological order (started before completed).
- `test_logs_nonexistent_job`: Init project, call `main(["logs", "nonexistent"])`. Assert `EXIT_JOB_FAILED`, stderr contains "not found".
- `test_logs_header_format`: Run flow, call logs, verify header lines match `"Job: ..."`, `"Status: completed"`, `"Duration: "` patterns.
- `test_logs_event_count`: Run a 2-step flow, verify logs output contains at least 4 event lines (2 started + 2 completed + job events).

**Test infrastructure**:
```python
SIMPLE_FLOW = """\
name: simple
steps:
  hello:
    run: 'echo "{\\"msg\\": \\"hi\\"}"'
    outputs: [msg]
"""

def _setup_project_with_jobs(tmp_path, n_jobs=1):
    project = init_project(tmp_path)
    flow = tmp_path / "test.flow.yaml"
    flow.write_text(SIMPLE_FLOW)
    for _ in range(n_jobs):
        run_flow(flow, project, quiet=True, output_stream=StringIO(), config=StepwiseConfig())
    return project
```
(Same pattern as `test_cli_jobs.py:17-33`.)

### Step 9: Write tests — output extension (~20 min)

**File:** `tests/test_cli_observability.py`

Follow the pattern from `test_cli_tools.py:301-357` (`TestOutputCommand`):

**`TestOutputPositionalStep`**:
- `test_output_positional_step`: Run flow via `_capture_stdout(["--project-dir", ..., "run", flow, "--wait", "--var", "question=test"])`, extract job_id. Call `_capture_stdout(["--project-dir", ..., "output", job_id, "hello"])`. Assert: `EXIT_SUCCESS`, output is valid JSON, contains `"msg"` key.
- `test_output_positional_step_not_found`: Same setup, call with nonexistent step name. Assert: `EXIT_JOB_FAILED`, output JSON contains `"_error"` key.
- `test_output_flag_still_works`: Verify `--step hello` still produces same result as before (backward compat).
- `test_output_no_step_unchanged`: Verify bare `stepwise output <id>` still returns `{"status": "completed", "outputs": [...]}` format.
- `test_output_flag_overrides_positional`: If both `--step a` and positional `b` are given, `--step` wins (existing behavior preserved since `args.step` is checked first).

### Step 10: Write tests — tail command (~15 min)

**File:** `tests/test_cli_observability.py`

**`TestTail`** — limited because tail requires a running server:
- `test_tail_no_server_error`: Call `main(["--standalone", "tail", "job-123"])`. Assert `EXIT_CONFIG_ERROR`, stderr contains "requires a running server".
- `test_tail_no_server_prints_help`: Verify the error message includes "stepwise server start" as actionable guidance.

Tail integration testing (against a real server) is deferred to manual smoke testing — the WS protocol itself is already tested by E2's test suite.

### Step 11: Run regression suite (~10 min)

```bash
uv run pytest tests/ -v
cd web && npm run test
```

Specific regressions to watch:
- `tests/test_cli_tools.py::TestOutputCommand` — all 3 existing tests must pass unchanged
- `tests/test_cli.py` — parser tests, version tests
- `tests/test_cli_jobs.py` — status, cancel, jobs list

## Testing Strategy

### Unit tests (no server, no I/O)

```bash
# Event line formatting — pure function tests
uv run pytest tests/test_cli_observability.py::TestFormatEventLine -v

# Job header formatting
uv run pytest tests/test_cli_observability.py::TestFormatJobHeader -v
```

### Integration tests (store + CLI, no server)

```bash
# Logs command — creates project, runs flow, queries events
uv run pytest tests/test_cli_observability.py::TestLogs -v

# Output extension — runs flow, verifies new positional step arg
uv run pytest tests/test_cli_observability.py::TestOutputPositionalStep -v

# Tail error path — verifies server-required error
uv run pytest tests/test_cli_observability.py::TestTail -v
```

### Regression

```bash
# Existing output command tests
uv run pytest tests/test_cli_tools.py::TestOutputCommand -v

# All CLI tests
uv run pytest tests/test_cli.py tests/test_cli_jobs.py tests/test_cli_tools.py -v

# Full suite
uv run pytest tests/
```

### Manual smoke test

```bash
# Terminal 1: start server
stepwise server start

# Terminal 2: run a multi-step flow
stepwise run my-flow.flow.yaml --var question=hello

# Terminal 3: tail live (while running)
stepwise tail <job-id>
# Expected: see events stream in real-time, separator after replay, auto-exit on completion

# After completion:
stepwise logs <job-id>
# Expected: header with job info + all events chronologically

stepwise output <job-id> plan
# Expected: raw JSON artifact for "plan" step

stepwise output <job-id>
# Expected: same format as before (unchanged)

# Edge cases:
stepwise tail nonexistent-id      # Expected: WebSocket error, clean exit
stepwise logs nonexistent-id      # Expected: "not found" error
stepwise output <id> nonexistent  # Expected: error JSON
Ctrl+C during tail                # Expected: clean exit, code 130
```

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Positional `step_name` arg on `output` breaks existing modes (`--run`, `--step`, no-step) | High — backward compat | `step_name` is `nargs="?"`, only consumed if a second positional is present. `--step` flag takes priority (checked first in handler). Existing `TestOutputCommand` tests (3 cases at `test_cli_tools.py:301-357`) must pass. New tests explicitly verify backward compat. |
| WebSocket connection fails in `tail` (server down, wrong port, job doesn't exist) | Medium | `websockets.connect()` raises `ConnectionRefusedError` or `InvalidStatusCode` — catch and print actionable error. The server returns `close(1008)` for invalid params (see `server.py:2329`). |
| Event envelope format differs between WS stream and REST `/events` endpoint | Medium | WS uses `"event"` key (from `hooks.py:40`), REST uses `"type"` key (from `models.py:1407`). `_format_event_line` normalizes via `envelope.get("event") or envelope.get("type")`. Step name normalized via `envelope.get("step") or envelope.get("data", {}).get("step")`. Both formats tested in `TestFormatEventLine`. |
| Duration not in event data — `step.completed` only has `{"step", "attempt"}` | Medium | Per A7 (verified at `engine.py:1387-1390`), events don't carry duration. For `tail`, we skip duration display (events arrive in real-time so elapsed is visible). For `logs`, we could compute from run data but this adds complexity — defer to v2. Show attempt number instead. |
| `asyncio.run()` in `cmd_tail` conflicts with existing event loop | Low | CLI commands run in the main thread with no existing loop. `runner.py:232` uses `asyncio.run()` in the same context successfully. |
| Large job with thousands of events makes `logs` slow | Low | `store.load_events()` query at `store.py:553` is `SELECT * FROM events WHERE job_id = ? ORDER BY timestamp` — indexed by job_id. Acceptable for v1. Can add `--limit N` / `--tail N` later. |
| `tail` has no integration test against real server | Low | The WS protocol is tested by E2's test suite. `tail` is a thin consumer — format functions are unit-tested, error paths are tested. Manual smoke test covers the full path. |
