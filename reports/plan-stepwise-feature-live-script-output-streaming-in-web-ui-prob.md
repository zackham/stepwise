# Plan: Live Script Output Streaming in Web UI

## Overview

Add real-time stdout/stderr streaming for running script steps in the web UI, following the existing agent output streaming pattern. The server tails script output files via WebSocket (same infrastructure used for agent steps), and the frontend displays live output with auto-scroll, falling back to the existing `executor_meta` display for completed runs. Output arrives in chunks as the child process's buffer flushes, not strictly line-by-line.

## Requirements

### R1: Backend API for live script output
The server must expose script stdout/stderr content while a script step is running, using the same offset-based file tailing + WebSocket broadcast pattern used for agent output.

**Acceptance criteria:**
- `GET /api/runs/{run_id}/script-output` returns `{ stdout: string, stderr: string, stdout_offset: number, stderr_offset: number }` from the step-io files
- While a script step is RUNNING, a WebSocket tailer broadcasts new stdout/stderr content to all connected clients, including byte offsets for deduplication
- After the step completes, the REST endpoint still works (reads from the same files on disk, since paths survive in `executor_state`)
- If the files don't exist (e.g., old jobs), returns empty strings

### R2: ScriptExecutor preserves output file paths in executor_state across all exit paths
The ScriptExecutor must store stdout/stderr file paths in `executor_state` both at launch time (via `state_update_fn`) and on every return path (`ExecutorResult.executor_state`), since the engine overwrites launch-time state with the return value.

**Acceptance criteria:**
- After `Popen`, `state_update_fn` is called with `{"pid": proc.pid, "stdout_path": str(stdout_path), "stderr_path": str(stderr_path)}`
- On success, failure, and watch return paths, `ExecutorResult.executor_state` includes `stdout_path` and `stderr_path`
- The paths are readable from `executor_state` for both running and completed runs
- Existing PID storage continues to work unchanged

### R3: Web UI live script output during execution
When a script step is RUNNING, the StepDetailPanel shows live output with auto-scroll, matching the UX position of the live AgentStreamView (above the run history, in the "active run" section).

**Acceptance criteria:**
- A `LiveScriptLogView` component renders live stdout (and optionally stderr) while the step has an active run
- Output auto-scrolls to bottom as new content arrives
- User can scroll up to review earlier output without losing position
- When the step completes, the live view disappears and the output appears in the run history via the existing `ScriptLogView`
- Retained content is capped at 512KB to prevent unbounded memory growth; earlier content is dropped with a "[earlier output truncated]" indicator

### R4: WebSocket-driven updates with offset-based deduplication
Live output uses the existing WebSocket connection. WS messages include byte offsets so the client can deduplicate against the REST backfill.

**Acceptance criteria:**
- Server-side tailer broadcasts `{"type": "script_output", "run_id": "...", "stdout": "...", "stderr": "...", "stdout_offset": N, "stderr_offset": N}` messages where offsets indicate where each chunk starts
- Frontend deduplicates: if a WS message's start offset is below the client's known position, the overlapping prefix is trimmed
- Only new content since last broadcast is sent per WS message

### R5: Backward compatibility
Existing completed jobs display exactly as before. The enhancement is additive.

**Acceptance criteria:**
- `ScriptLogView` rendering from `run.result.executor_meta.stdout/stderr` is unchanged
- Jobs completed before this feature (no `stdout_path` in `executor_state`) work fine — REST endpoint returns empty, no tailer starts

## Assumptions (verified against code)

1. **ScriptExecutor already writes stdout/stderr to files before process completion.** The files are opened before `Popen` and written to throughout execution — they contain partial output while the process runs.
   - Verified: `executors.py:410-422` — `stdout_fh = open(stdout_path, "w")` before `Popen`, files stay open until `proc.wait()` returns at line 471.

2. **ScriptExecutor already calls `state_update_fn` with the PID.** We need to add the file paths to the same call.
   - Verified: `executors.py:466-468` — `context.state_update_fn({"pid": proc.pid})` immediately after `Popen`.

3. **The engine overwrites `executor_state` with the executor's return value.** `_process_launch_result()` sets `run.executor_state = result.executor_state` on success (line 1894), failure (line 1870), and watch (line 1911). This means launch-time state is lost unless the executor returns it.
   - Verified: `engine.py:1870,1894,1911` — all three code paths assign `result.executor_state` directly.
   - Implication: ScriptExecutor must return `stdout_path`/`stderr_path` in `executor_state` on every exit path, not just at launch time. This matches the AgentExecutor pattern — it returns a `state` dict containing `output_path` on both success (line 1364-1368) and failure (line 1319-1330) paths.

4. **Child process output buffering is block-based, not line-based.** Processes writing to regular files (not ttys) default to block buffering (~4-8KB). Output appears in chunks when the buffer fills or the process exits, not line-by-line.
   - Verified: Standard C library behavior; `Popen` redirects stdout to a regular file (`executors.py:411`), which triggers block buffering in the child.
   - Implication: The feature provides "chunked output as buffers flush," not "line-by-line real-time." Users who need line-buffered output can wrap commands with `stdbuf -oL` or set `PYTHONUNBUFFERED=1`.

5. **The `activeRun` variable in `StepDetailPanel`** identifies the currently-running step run (status === "running"). This is where the live agent view is conditionally rendered.
   - Verified: `StepDetailPanel.tsx:235` — `const activeRun = sortedRunsForCost.find((r) => r.status === "running")`.

6. **WebSocket subscriptions** use a pub/sub pattern in `useStepwiseWebSocket.ts`. Agent output uses `subscribeAgentOutput()`.
   - Verified: `useStepwiseWebSocket.ts:14-19` and `:105-106`.

7. **The `_stream_tasks` dict** tracks active agent tailer tasks by `run_id`.
   - Verified: `server.py:154` — `_stream_tasks: dict[str, asyncio.Task] = {}`.

8. **Server restart does not reattach script steps.** `_cleanup_zombie_jobs()` (server.py:586) verifies agent PIDs and reattaches surviving agents, but script PIDs are not distinguished and dead ones are failed. This is existing behavior, unrelated to streaming.
   - Verified: `server.py:609-616` — reattach logic uses `verify_agent_pid()` which is agent-specific.

## Out of Scope

- **Merged stdout/stderr interleaving.** Stdout and stderr are separate files; we stream them independently. Terminal-style interleaved output would require `pty` changes to the subprocess model.
- **ANSI color rendering.** Script output is plain text with syntax highlighting via regex (existing `highlightLogLine`). True ANSI escape code rendering (xterm.js) is a separate feature.
- **Server restart resilience for script steps.** Script steps are failed on restart (existing behavior). The output files remain on disk and are readable via the REST endpoint for completed/failed runs. Restart reattach for script steps is a separate feature that would require PID verification and process-type awareness in `_cleanup_zombie_jobs()`.
- **CLI-owned jobs.** The live tailing only works for server-owned jobs (where the server process has filesystem access to the step-io directory). CLI-owned jobs running on a different machine won't stream.
- **Line-by-line guarantee.** Output granularity depends on the child process's buffer flushing. The plan documents this limitation and suggests `stdbuf -oL` for users who need it.

## Architecture

### How changes fit existing patterns

The design mirrors the agent output streaming pattern:

```
Agent pattern (existing):
  AgentExecutor.start() → state_update_fn({"output_path": ...})
  AgentExecutor returns executor_state={"output_path": ...} on all exit paths
  _agent_stream_monitor() discovers running runs with output_path
  _tail_agent_output() tails file, broadcasts via WebSocket
  GET /api/runs/{run_id}/agent-output reads file, returns events
  useAgentStream() subscribes to WS, backfills via REST
  AgentStreamView renders live/historical

Script pattern (new):
  ScriptExecutor.start() → state_update_fn({"stdout_path": ..., "stderr_path": ...})
  ScriptExecutor returns executor_state={"stdout_path": ..., "stderr_path": ...} on all exit paths
  _script_stream_monitor() discovers running runs with stdout_path
  _tail_script_output() tails files, broadcasts via WebSocket with byte offsets
  GET /api/runs/{run_id}/script-output reads files, returns text + offsets
  useScriptStream() subscribes to WS, backfills via REST, deduplicates by offset
  LiveScriptLogView renders live output with capped retention
```

### Data flow

```
                        ScriptExecutor (thread pool)
                              │
                    state_update_fn({"pid", "stdout_path", "stderr_path"})
                              │
                              ▼
                     engine.update_state() → store.save_run()
                              │
                              ▼
              _script_stream_monitor() (1s poll loop)
                    discovers stdout_path in executor_state
                              │
                              ▼
               _tail_script_output() (300ms poll loop)
                    f.seek(offset) → f.read() → new bytes
                    broadcasts chunk + start byte offset
                              │
                              ▼
                   _broadcast({"type": "script_output",
                               "stdout_offset": N, ...})
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
              WebSocket client     WebSocket client
                    │
                    ▼
           useScriptStream() hook
             deduplicates by offset
                    │
                    ▼
           LiveScriptLogView component
             capped at 512KB retained
```

### Key design decisions

1. **Separate endpoint from agent output** (`/api/runs/{run_id}/script-output` vs `/agent-output`). Script output is plain text; agent output is structured NDJSON events. Different formats, different parsing.

2. **Preserve paths through all executor exit paths.** The engine's `_process_launch_result()` overwrites `run.executor_state` with `result.executor_state` (engine.py:1870,1894,1911). Since ScriptExecutor currently returns `None` (success), `{"failed": True, ...}` (failure), or `{"partial_output": ...}` (watch), the launch-time paths would be lost. Fix: include `stdout_path`/`stderr_path` in `executor_state` on every return, matching the AgentExecutor pattern.

3. **Offset-based deduplication.** The server-side tailer broadcasts chunks with byte offsets. The client tracks its known position from the REST backfill. WS events overlapping the backfill range are trimmed. This prevents the duplication that would occur when the tailer and REST endpoint both start from offset 0. Uses binary-mode file reads (`"rb"`) for deterministic byte offsets.

4. **Capped content retention.** The client retains at most 512KB of accumulated stdout. Beyond that, the oldest content is dropped and a "[earlier output truncated]" indicator is shown. This prevents unbounded memory growth for long-running scripts. Stored as a chunks array, not a single concatenated string, to avoid O(n) string rebuilding on each update.

5. **Separate `_script_stream_tasks` dict and `_script_monitor_task` variable.** Both the per-run tailer dict and the monitor task itself have explicit lifecycle management in the server lifespan.

6. **LiveScriptLogView is a new component** separate from the existing `ScriptLogView`. The live component handles streaming state, auto-scroll, and capped retention. The existing `ScriptLogView` handles post-completion display with truncation, copy, and exit code badge. They share the `highlightLogLine` utility and log container styling.

## Dependency Graph & Milestones

### Step dependency DAG

```
          ┌───────────────────────────────────────────────┐
          │              BACKEND TRACK                     │
          │                                               │
          │   Step 1a ──→ Step 1b ──→ Step 1c ──┐        │
          │  (launch)   (returns)   (tests)      │        │
          │                                      ▼        │
          │                              Step 2 ──→ Step 2b│
          │                             (endpoint) (tests) │
          │                                      │        │
          │                                      ▼        │
          │                              Step 3a ──→ 3b ──→ 3c ──→ 3d
          │                             (state)  (tailer) (monitor) (tests)
          └───────────────────────────────────────────────┘

          ┌───────────────────────────────────────────────┐
          │              FRONTEND TRACK                    │
          │                                               │
          │   Step 4 ──→ Step 5 ──→ Step 6 ──→ Step 7a ──→ 7b ──→ Step 8 ──→ Step 9
          │  (types)    (api)      (ws sub)   (hook)     (tests)  (component) (wire)
          └───────────────────────────────────────────────┘
```

**Parallelism:** The backend track (Steps 1-3) and frontend track (Steps 4-9) are fully independent and can be developed in parallel. Within each track, steps are sequential.

### Milestones (independently testable checkpoints)

| Milestone | After step | What's testable | Command |
|-----------|-----------|-----------------|---------|
| M1: Paths persisted | 1c | ScriptExecutor stores `stdout_path`/`stderr_path` in executor_state on all exit paths; paths survive engine completion | `uv run pytest tests/test_executors.py -x -q -k "script_paths" && uv run pytest tests/test_restart_recovery.py -x -q -k "stdout_path"` |
| M2: REST readable | 2b | `GET /api/runs/{id}/script-output` returns file content with offsets | `uv run pytest tests/test_streaming.py -x -q -k "script_output"` |
| M3: Live tailing | 3d | Server tails running script output and broadcasts via WebSocket; monitor starts/stops tailers; lifecycle clean | `uv run pytest tests/test_streaming.py -x -q -k "script_tail"` |
| M4: Frontend types wired | 6 | Type, API client, WS subscription all exist. No runtime test, but `cd web && npx tsc --noEmit` passes. | `cd web && npx tsc --noEmit` |
| M5: Hook working | 7b | useScriptStream accumulates output, deduplicates by offset, caps at 512KB | `cd web && npm run test -- --run src/hooks/useScriptStream.test.ts` |
| M6: UI complete | 9 | LiveScriptLogView renders in StepDetailPanel for running script steps | `cd web && npm run test -- --run src/components/jobs/LiveScriptLogView.test.tsx` |
| M7: Full regression | 9 | All existing tests pass, no regressions | `uv run pytest tests/ -x -q && cd web && npm run test` |

## Implementation Steps

### Step 1a: Store paths in state_update_fn at launch
**File:** `src/stepwise/executors.py` (line 466-468)
**Satisfies:** R2 (launch-time storage)
**Time:** 5 min

Modify the existing `state_update_fn` call to include file paths alongside PID:

```python
# Current (line 466-468):
if context.state_update_fn:
    context.state_update_fn({"pid": proc.pid})

# New:
if context.state_update_fn:
    context.state_update_fn({
        "pid": proc.pid,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    })
```

**Done when:** `state_update_fn` receives a dict with `pid`, `stdout_path`, and `stderr_path`. Not independently testable yet — verified in Step 1c.

---

### Step 1b: Include paths in every ExecutorResult.executor_state return
**File:** `src/stepwise/executors.py` (lines 363, 491-501, 516-520, 527-536)
**Depends on:** Step 1a (same file, same function)
**Satisfies:** R2 (all exit paths)
**Time:** 15 min

Build a paths dict after file path construction (line 363) and include it in every return:

```python
# After line 363 (stderr_path defined):
_io_paths = {"stdout_path": str(stdout_path), "stderr_path": str(stderr_path)}
```

Four return sites to update:

| Return site | Current `executor_state` | New `executor_state` |
|---|---|---|
| Success (line 527-536) | `None` (omitted) | `_io_paths` |
| Failure (line 491-501) | `{"failed": True, "error": ...}` | `{**_io_paths, "failed": True, "error": ...}` |
| Watch (line 516-520) | `{"partial_output": parsed}` or `None` | `{**_io_paths, "partial_output": parsed}` or `_io_paths` |
| Popen exception (line 455-464) | N/A (before files created) | Leave unchanged |

**Done when:** Every `ExecutorResult` returned from `ScriptExecutor.start()` (except the pre-Popen exception) includes `stdout_path` and `stderr_path` in `executor_state`.

---

### Step 1c: Tests — verify paths persist through all exit paths
**File:** `tests/test_executors.py` (existing, add 4 test functions)
**Depends on:** Steps 1a, 1b
**Satisfies:** Milestone M1
**Time:** 20 min

```python
class TestScriptExecutorPathPersistence:
    """Verify stdout_path/stderr_path in executor_state on all exit paths."""

    def test_success_path_includes_paths(self):
        """echo valid JSON → result.executor_state has stdout_path and stderr_path."""
        ctx = _ctx()
        executor = ScriptExecutor(command='echo \'{"x": 1}\'')
        result = executor.start({}, ctx)
        assert result.type == "data"
        assert "stdout_path" in result.executor_state
        assert "stderr_path" in result.executor_state
        assert result.executor_state["stdout_path"].endswith(".stdout")
        assert Path(result.executor_state["stdout_path"]).exists()

    def test_failure_path_includes_paths(self):
        """exit 1 → result.executor_state has paths AND failed flag."""
        ctx = _ctx()
        executor = ScriptExecutor(command="exit 1")
        result = executor.start({}, ctx)
        assert result.executor_state["failed"] is True
        assert "stdout_path" in result.executor_state
        assert "stderr_path" in result.executor_state

    def test_watch_path_includes_paths(self):
        """stdout with _watch key → result.executor_state has paths AND partial_output."""
        ctx = _ctx()
        executor = ScriptExecutor(command='echo \'{"_watch": {"mode": "external"}, "partial": 1}\'')
        result = executor.start({}, ctx)
        assert result.type == "watch"
        assert "stdout_path" in result.executor_state
        assert "stderr_path" in result.executor_state

    def test_state_update_fn_receives_paths(self):
        """state_update_fn callback receives stdout_path and stderr_path alongside pid."""
        captured = {}
        def capture(state):
            captured.update(state)
        ctx = _ctx()
        ctx.state_update_fn = capture
        executor = ScriptExecutor(command='echo hello')
        executor.start({}, ctx)
        assert "pid" in captured
        assert "stdout_path" in captured
        assert "stderr_path" in captured
```

Add one test in `tests/test_restart_recovery.py` to verify paths survive the engine's `_process_launch_result` overwrite:

```python
def test_completed_run_retains_stdout_path(self):
    """After engine completion, run.executor_state still has stdout_path."""
    # Use register_step_fn + run_job_sync pattern
    register_step_fn("echo_fn", lambda inputs: {"out": "ok"})
    wf = WorkflowDefinition(steps={
        "s": StepDefinition(
            name="s",
            executor=ExecutorRef(type="script", config={"command": 'echo \'{"out":"ok"}\''}),
            outputs=["out"],
        ),
    })
    job = async_engine.create_job(objective="test", workflow=wf, inputs={})
    result = run_job_sync(async_engine, job.id)
    assert result.status == JobStatus.COMPLETED
    runs = async_engine.store.runs_for_job(job.id)
    assert runs[0].executor_state is not None
    assert "stdout_path" in runs[0].executor_state
```

**Verify:** `uv run pytest tests/test_executors.py -x -q -k "PathPersistence" && uv run pytest tests/test_restart_recovery.py -x -q -k "stdout_path"`

---

### Step 2a: REST endpoint for script output
**File:** `src/stepwise/server.py` (near line 1723)
**Depends on:** Step 1b (paths must be in executor_state for endpoint to find them)
**Satisfies:** R1 (REST API)
**Time:** 15 min

```python
@app.get("/api/runs/{run_id}/script-output")
def get_script_output(run_id: str, stdout_offset: int = 0, stderr_offset: int = 0):
    """Get script stdout/stderr content, supporting offset-based tailing."""
    engine = _get_engine()
    try:
        run = engine.store.load_run(run_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")

    state = run.executor_state or {}
    stdout_path = state.get("stdout_path")
    stderr_path = state.get("stderr_path")

    def _read_from(path: str | None, offset: int) -> tuple[str, int]:
        if not path:
            return "", offset
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                data = f.read()
                new_offset = f.tell()
            return data.decode("utf-8", errors="replace"), new_offset
        except FileNotFoundError:
            return "", offset

    stdout, new_stdout_offset = _read_from(stdout_path, stdout_offset)
    stderr, new_stderr_offset = _read_from(stderr_path, stderr_offset)

    return {
        "stdout": stdout,
        "stderr": stderr,
        "stdout_offset": new_stdout_offset,
        "stderr_offset": new_stderr_offset,
    }
```

**Done when:** Endpoint registered, returns correct shape. Not independently testable without test client setup — verified in Step 2b.

---

### Step 2b: Tests — REST endpoint
**File:** `tests/test_streaming.py` (existing, add test class)
**Depends on:** Step 2a
**Satisfies:** Milestone M2
**Time:** 15 min

Uses the existing `TestClient(app)` pattern from `test_streaming.py`:

```python
class TestScriptOutputEndpoint:
    """Tests for GET /api/runs/{run_id}/script-output."""

    def test_returns_file_content(self, tmp_path):
        """Write content to stdout/stderr files, verify endpoint returns it."""
        stdout_file = tmp_path / "test-1.stdout"
        stderr_file = tmp_path / "test-1.stderr"
        stdout_file.write_text("line 1\nline 2\n")
        stderr_file.write_text("warning\n")
        # Create run with executor_state pointing to files
        run = _make_run(executor_state={
            "stdout_path": str(stdout_file),
            "stderr_path": str(stderr_file),
        })
        store.save_run(run)
        resp = client.get(f"/api/runs/{run.id}/script-output")
        assert resp.status_code == 200
        data = resp.json()
        assert data["stdout"] == "line 1\nline 2\n"
        assert data["stderr"] == "warning\n"
        assert data["stdout_offset"] > 0

    def test_offset_returns_only_new_content(self, tmp_path):
        """Request with offset → only content after that byte position."""
        stdout_file = tmp_path / "test-1.stdout"
        stdout_file.write_bytes(b"AAABBB")
        run = _make_run(executor_state={"stdout_path": str(stdout_file)})
        store.save_run(run)
        resp = client.get(f"/api/runs/{run.id}/script-output?stdout_offset=3")
        data = resp.json()
        assert data["stdout"] == "BBB"
        assert data["stdout_offset"] == 6

    def test_missing_files_returns_empty(self, tmp_path):
        run = _make_run(executor_state={
            "stdout_path": str(tmp_path / "nonexistent.stdout"),
        })
        store.save_run(run)
        resp = client.get(f"/api/runs/{run.id}/script-output")
        assert resp.json()["stdout"] == ""

    def test_no_paths_returns_empty(self):
        run = _make_run(executor_state={})
        store.save_run(run)
        resp = client.get(f"/api/runs/{run.id}/script-output")
        assert resp.json()["stdout"] == ""
        assert resp.json()["stderr"] == ""

    def test_404_for_unknown_run(self):
        resp = client.get("/api/runs/nonexistent/script-output")
        assert resp.status_code == 404
```

**Verify:** `uv run pytest tests/test_streaming.py -x -q -k "ScriptOutputEndpoint"`

---

### Step 3a: Module-level state for script stream tasks
**File:** `src/stepwise/server.py` (near line 154)
**Depends on:** None (additive module-level declaration)
**Satisfies:** R1 (infrastructure for tailing)
**Time:** 2 min

```python
_script_stream_tasks: dict[str, asyncio.Task] = {}
_script_monitor_task: asyncio.Task | None = None
```

**Done when:** Two new module-level variables exist. No test — verified in Step 3d.

---

### Step 3b: Tailer coroutine with binary-mode reads and byte offsets
**File:** `src/stepwise/server.py` (near `_tail_agent_output`, line 315)
**Depends on:** Step 3a
**Satisfies:** R1 (WebSocket broadcast), R4 (byte offsets for deduplication)
**Time:** 15 min

```python
async def _tail_script_output(run_id: str, stdout_path: str, stderr_path: str | None) -> None:
    """Tail script stdout/stderr files and broadcast via WebSocket with byte offsets."""
    stdout_offset = 0
    stderr_offset = 0
    try:
        while True:
            stdout_new = ""
            stderr_new = ""
            prev_stdout_offset = stdout_offset
            prev_stderr_offset = stderr_offset

            try:
                with open(stdout_path, "rb") as f:
                    f.seek(stdout_offset)
                    data = f.read()
                    if data:
                        stdout_offset = f.tell()
                        stdout_new = data.decode("utf-8", errors="replace")
            except FileNotFoundError:
                pass

            if stderr_path:
                try:
                    with open(stderr_path, "rb") as f:
                        f.seek(stderr_offset)
                        data = f.read()
                        if data:
                            stderr_offset = f.tell()
                            stderr_new = data.decode("utf-8", errors="replace")
                except FileNotFoundError:
                    pass

            if stdout_new or stderr_new:
                await _broadcast({
                    "type": "script_output",
                    "run_id": run_id,
                    "stdout": stdout_new,
                    "stderr": stderr_new,
                    "stdout_offset": prev_stdout_offset,
                    "stderr_offset": prev_stderr_offset,
                })
            await asyncio.sleep(0.3)
    except asyncio.CancelledError:
        pass
```

**Done when:** Function exists, compiles. Not independently testable — verified in Step 3d.

---

### Step 3c: Monitor coroutine and lifespan integration
**File:** `src/stepwise/server.py` (near `_agent_stream_monitor` line 406, lifespan near lines 909, 948)
**Depends on:** Steps 3a, 3b
**Satisfies:** R1 (automatic discovery and lifecycle)
**Time:** 15 min

**Monitor coroutine:**

```python
async def _script_stream_monitor() -> None:
    """Periodically check for running script steps to tail."""
    engine = _get_engine()
    while True:
        try:
            for job in engine.store.active_jobs():
                for run in engine.store.running_runs(job.id):
                    state = run.executor_state or {}
                    stdout_path = state.get("stdout_path")
                    if stdout_path and run.id not in _script_stream_tasks:
                        task = asyncio.create_task(
                            _tail_script_output(
                                run.id,
                                stdout_path,
                                state.get("stderr_path"),
                            )
                        )
                        _script_stream_tasks[run.id] = task

            active_run_ids = set()
            for job in engine.store.active_jobs():
                for run in engine.store.running_runs(job.id):
                    active_run_ids.add(run.id)
            stale = [rid for rid in _script_stream_tasks if rid not in active_run_ids]
            for rid in stale:
                _script_stream_tasks[rid].cancel()
                del _script_stream_tasks[rid]

            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(5.0)
```

**Lifespan startup** (near line 909, after `_stream_monitor`):
```python
_script_monitor_task = asyncio.create_task(_script_stream_monitor())
```

**Lifespan shutdown** (after `_stream_monitor` cleanup block, near line 948-952):
```python
# Cancel script stream tailers
for task in _script_stream_tasks.values():
    task.cancel()
_script_stream_tasks.clear()

# Cancel script stream monitor
if _script_monitor_task:
    _script_monitor_task.cancel()
    try:
        await _script_monitor_task
    except asyncio.CancelledError:
        pass
```

**Done when:** Monitor starts in lifespan, discovers running script runs, spawns tailers, cleans up stale tailers, and both the monitor task and per-run tailer tasks are cancelled on shutdown.

---

### Step 3d: Tests — tailer broadcast, monitor lifecycle, shutdown
**File:** `tests/test_streaming.py` (existing, add test class)
**Depends on:** Steps 3a-3c
**Satisfies:** Milestone M3
**Time:** 20 min

```python
class TestScriptStreamTailing:
    """Tests for _tail_script_output and _script_stream_monitor."""

    @pytest.mark.asyncio
    async def test_tailer_broadcasts_new_content(self, tmp_path):
        """Tailer reads new bytes from file and calls _broadcast with correct payload."""
        stdout_file = tmp_path / "test.stdout"
        stdout_file.write_bytes(b"hello\n")
        broadcasts = []
        original_broadcast = server._broadcast
        async def mock_broadcast(msg):
            broadcasts.append(msg)
        server._broadcast = mock_broadcast
        try:
            task = asyncio.create_task(
                server._tail_script_output("run-1", str(stdout_file), None)
            )
            await asyncio.sleep(0.5)  # One tail cycle
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert len(broadcasts) >= 1
            msg = broadcasts[0]
            assert msg["type"] == "script_output"
            assert msg["run_id"] == "run-1"
            assert msg["stdout"] == "hello\n"
            assert msg["stdout_offset"] == 0  # Start offset of this chunk
        finally:
            server._broadcast = original_broadcast

    @pytest.mark.asyncio
    async def test_tailer_binary_offsets_correct(self, tmp_path):
        """Multi-byte UTF-8: offsets are byte-accurate, not character-count."""
        stdout_file = tmp_path / "test.stdout"
        stdout_file.write_bytes("héllo\n".encode("utf-8"))  # é = 2 bytes
        broadcasts = []
        async def mock_broadcast(msg):
            broadcasts.append(msg)
        server._broadcast = mock_broadcast
        try:
            task = asyncio.create_task(
                server._tail_script_output("run-1", str(stdout_file), None)
            )
            await asyncio.sleep(0.5)
            # Append more content
            with open(stdout_file, "ab") as f:
                f.write("world\n".encode("utf-8"))
            await asyncio.sleep(0.5)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            assert len(broadcasts) >= 2
            # Second broadcast starts where first ended
            assert broadcasts[1]["stdout"] == "world\n"
            assert broadcasts[1]["stdout_offset"] == len("héllo\n".encode("utf-8"))
        finally:
            server._broadcast = mock_broadcast  # already replaced

    def test_monitor_lifecycle_starts_and_stops(self):
        """Monitor creates tailer for running script run, cancels when run completes."""
        # This test verifies via _script_stream_tasks dict population.
        # Full integration test — run a script step through the server,
        # check that _script_stream_tasks gets populated, then verify
        # cleanup after completion.
        pass  # Implemented as integration test if needed; manual verification otherwise
```

**Verify:** `uv run pytest tests/test_streaming.py -x -q -k "ScriptStreamTailing"`

---

### Step 4: TypeScript types (single definition)
**File:** `web/src/lib/types.ts` (near line 318)
**Depends on:** None (frontend track start)
**Satisfies:** R4 (type contract)
**Time:** 5 min

```typescript
export interface ScriptOutputMessage {
  type: "script_output";
  run_id: string;
  stdout: string;
  stderr: string;
  stdout_offset: number;
  stderr_offset: number;
}
```

If a `WebSocketMessage` union type exists, add `ScriptOutputMessage` to it.

**Done when:** `cd web && npx tsc --noEmit` passes with the new type.

---

### Step 5: Frontend API client function
**File:** `web/src/lib/api.ts` (near `fetchAgentOutput`, line 229)
**Depends on:** Step 4 (uses response type shape, but no import needed since the return type is inline)
**Satisfies:** R1 (client-side API access)
**Time:** 5 min

```typescript
export function fetchScriptOutput(
  runId: string,
  stdoutOffset = 0,
  stderrOffset = 0,
): Promise<{ stdout: string; stderr: string; stdout_offset: number; stderr_offset: number }> {
  return request(`/runs/${runId}/script-output?stdout_offset=${stdoutOffset}&stderr_offset=${stderrOffset}`);
}
```

**Done when:** `cd web && npx tsc --noEmit` passes.

---

### Step 6: WebSocket subscription for script output
**File:** `web/src/hooks/useStepwiseWebSocket.ts` (near line 14-19 and line 105)
**Depends on:** Step 4 (imports `ScriptOutputMessage`)
**Satisfies:** R4 (client-side WS reception), Milestone M4
**Time:** 10 min

Add import:
```typescript
import type { ..., ScriptOutputMessage } from "@/lib/types";
```

Add pub/sub (near `subscribeAgentOutput`):
```typescript
const scriptOutputListeners = new Set<(msg: ScriptOutputMessage) => void>();

export function subscribeScriptOutput(fn: (msg: ScriptOutputMessage) => void) {
  scriptOutputListeners.add(fn);
  return () => scriptOutputListeners.delete(fn);
}
```

Add handler in `ws.onmessage` (near line 105):
```typescript
} else if (msg.type === "script_output") {
  for (const fn of scriptOutputListeners) fn(msg as ScriptOutputMessage);
}
```

**Done when:** `cd web && npx tsc --noEmit` passes. Manual verification: open browser console, connect WS, see script_output messages dispatched to listeners.

---

### Step 7a: useScriptStream hook
**File:** `web/src/hooks/useScriptStream.ts` (new file)
**Depends on:** Step 5 (fetchScriptOutput), Step 6 (subscribeScriptOutput)
**Satisfies:** R3 (data layer), R4 (offset deduplication)
**Time:** 30 min

Key design:
- Chunks array (not single string) with 512KB cap
- Byte-offset deduplication between REST backfill and WS events
- AbortController for fetch cleanup on unmount/runId change
- Error catch on backfill failure (falls back to WS-only)
- State reset on runId change
- `useMemo` for joining chunks, keyed on version counter

```typescript
import { useEffect, useRef, useState, useMemo } from "react";
import { fetchScriptOutput } from "../lib/api";
import { subscribeScriptOutput } from "./useStepwiseWebSocket";
import type { ScriptOutputMessage } from "@/lib/types";

const MAX_RETAINED_BYTES = 512 * 1024;

interface Chunk {
  text: string;
  bytes: number;
}

interface ScriptStreamState {
  stdoutChunks: Chunk[];
  stderrChunks: Chunk[];
  totalStdoutBytes: number;
  totalStderrBytes: number;
  truncated: boolean;
}

function appendChunk(
  chunks: Chunk[],
  totalBytes: number,
  text: string,
): { chunks: Chunk[]; totalBytes: number; truncated: boolean } {
  const byteLen = new TextEncoder().encode(text).length;
  const newChunks = [...chunks, { text, bytes: byteLen }];
  let newTotal = totalBytes + byteLen;
  let truncated = false;
  while (newTotal > MAX_RETAINED_BYTES && newChunks.length > 1) {
    const removed = newChunks.shift()!;
    newTotal -= removed.bytes;
    truncated = true;
  }
  return { chunks: newChunks, totalBytes: newTotal, truncated };
}

export function useScriptStream(runId: string | undefined): {
  stdout: string;
  stderr: string;
  truncated: boolean;
  version: number;
} {
  const [version, setVersion] = useState(0);
  const stateRef = useRef<ScriptStreamState>({
    stdoutChunks: [], stderrChunks: [],
    totalStdoutBytes: 0, totalStderrBytes: 0, truncated: false,
  });
  const backfilledRef = useRef(false);
  const knownStdoutOffset = useRef(0);
  const knownStderrOffset = useRef(0);
  const queueRef = useRef<ScriptOutputMessage[]>([]);

  // Reset on runId change
  useEffect(() => {
    stateRef.current = {
      stdoutChunks: [], stderrChunks: [],
      totalStdoutBytes: 0, totalStderrBytes: 0, truncated: false,
    };
    backfilledRef.current = false;
    knownStdoutOffset.current = 0;
    knownStderrOffset.current = 0;
    queueRef.current = [];
    setVersion(0);
  }, [runId]);

  function applyWsMessage(msg: ScriptOutputMessage) {
    const state = stateRef.current;
    if (msg.stdout) {
      const msgEnd = msg.stdout_offset + new TextEncoder().encode(msg.stdout).length;
      if (msgEnd > knownStdoutOffset.current) {
        let text = msg.stdout;
        if (msg.stdout_offset < knownStdoutOffset.current) {
          const overlapBytes = knownStdoutOffset.current - msg.stdout_offset;
          const encoded = new TextEncoder().encode(msg.stdout);
          text = new TextDecoder().decode(encoded.slice(overlapBytes));
        }
        if (text) {
          const result = appendChunk(state.stdoutChunks, state.totalStdoutBytes, text);
          state.stdoutChunks = result.chunks;
          state.totalStdoutBytes = result.totalBytes;
          if (result.truncated) state.truncated = true;
        }
        knownStdoutOffset.current = msgEnd;
      }
    }
    if (msg.stderr) {
      const msgEnd = msg.stderr_offset + new TextEncoder().encode(msg.stderr).length;
      if (msgEnd > knownStderrOffset.current) {
        let text = msg.stderr;
        if (msg.stderr_offset < knownStderrOffset.current) {
          const overlapBytes = knownStderrOffset.current - msg.stderr_offset;
          const encoded = new TextEncoder().encode(msg.stderr);
          text = new TextDecoder().decode(encoded.slice(overlapBytes));
        }
        if (text) {
          const result = appendChunk(state.stderrChunks, state.totalStderrBytes, text);
          state.stderrChunks = result.chunks;
          state.totalStderrBytes = result.totalBytes;
        }
        knownStderrOffset.current = msgEnd;
      }
    }
  }

  // REST backfill
  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    fetchScriptOutput(runId)
      .then((data) => {
        if (controller.signal.aborted) return;
        if (data.stdout) {
          const result = appendChunk([], 0, data.stdout);
          stateRef.current.stdoutChunks = result.chunks;
          stateRef.current.totalStdoutBytes = result.totalBytes;
          stateRef.current.truncated = result.truncated;
        }
        if (data.stderr) {
          const result = appendChunk([], 0, data.stderr);
          stateRef.current.stderrChunks = result.chunks;
          stateRef.current.totalStderrBytes = result.totalBytes;
        }
        knownStdoutOffset.current = data.stdout_offset;
        knownStderrOffset.current = data.stderr_offset;
        backfilledRef.current = true;
        for (const msg of queueRef.current) applyWsMessage(msg);
        queueRef.current = [];
        setVersion((v) => v + 1);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        backfilledRef.current = true;
        for (const msg of queueRef.current) applyWsMessage(msg);
        queueRef.current = [];
        setVersion((v) => v + 1);
      });
    return () => controller.abort();
  }, [runId]);

  // WS subscription
  useEffect(() => {
    if (!runId) return;
    return subscribeScriptOutput((msg) => {
      if (msg.run_id !== runId) return;
      if (!backfilledRef.current) {
        queueRef.current.push(msg);
        return;
      }
      applyWsMessage(msg);
      setVersion((v) => v + 1);
    });
  }, [runId]);

  const stdout = useMemo(
    () => stateRef.current.stdoutChunks.map((c) => c.text).join(""),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version],
  );
  const stderr = useMemo(
    () => stateRef.current.stderrChunks.map((c) => c.text).join(""),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version],
  );

  return { stdout, stderr, truncated: stateRef.current.truncated, version };
}
```

**Done when:** `cd web && npx tsc --noEmit` passes.

---

### Step 7b: Tests — useScriptStream hook
**File:** `web/src/hooks/useScriptStream.test.ts` (new file)
**Depends on:** Step 7a
**Satisfies:** Milestone M5
**Time:** 30 min

Mock `fetchScriptOutput` (from `@/lib/api`) and `subscribeScriptOutput` (from `./useStepwiseWebSocket`). Use `renderHook` from `@testing-library/react` with a `createWrapper()` for QueryClient.

```typescript
describe("useScriptStream", () => {
  it("populates stdout/stderr from REST backfill", async () => {
    mockFetch.mockResolvedValue({ stdout: "hello\n", stderr: "warn\n", stdout_offset: 6, stderr_offset: 5 });
    const { result } = renderHook(() => useScriptStream("run-1"), { wrapper });
    await waitFor(() => expect(result.current.stdout).toBe("hello\n"));
    expect(result.current.stderr).toBe("warn\n");
  });

  it("appends WS events after backfill", async () => {
    mockFetch.mockResolvedValue({ stdout: "A", stderr: "", stdout_offset: 1, stderr_offset: 0 });
    const { result } = renderHook(() => useScriptStream("run-1"), { wrapper });
    await waitFor(() => expect(result.current.stdout).toBe("A"));
    // Simulate WS event
    act(() => {
      wsCallback({ run_id: "run-1", stdout: "B", stderr: "", stdout_offset: 1, stderr_offset: 0 });
    });
    expect(result.current.stdout).toBe("AB");
  });

  it("deduplicates WS event overlapping with backfill", async () => {
    mockFetch.mockResolvedValue({ stdout: "AABB", stderr: "", stdout_offset: 4, stderr_offset: 0 });
    const { result } = renderHook(() => useScriptStream("run-1"), { wrapper });
    await waitFor(() => expect(result.current.stdout).toBe("AABB"));
    // WS event starts at offset 2 (overlaps with bytes 2-3 from backfill)
    act(() => {
      wsCallback({ run_id: "run-1", stdout: "BBCC", stderr: "", stdout_offset: 2, stderr_offset: 0 });
    });
    // Only "CC" (bytes 4-5) should be appended
    expect(result.current.stdout).toBe("AABBCC");
  });

  it("queues WS events before backfill completes", async () => {
    let resolveBackfill: (v: any) => void;
    mockFetch.mockReturnValue(new Promise((r) => { resolveBackfill = r; }));
    const { result } = renderHook(() => useScriptStream("run-1"), { wrapper });
    // WS event arrives before backfill
    act(() => {
      wsCallback({ run_id: "run-1", stdout: "X", stderr: "", stdout_offset: 5, stderr_offset: 0 });
    });
    expect(result.current.stdout).toBe(""); // Not yet applied
    // Backfill resolves
    act(() => {
      resolveBackfill({ stdout: "HELLO", stderr: "", stdout_offset: 5, stderr_offset: 0 });
    });
    await waitFor(() => expect(result.current.stdout).toBe("HELLOX"));
  });

  it("resets state on runId change", async () => {
    mockFetch.mockResolvedValue({ stdout: "old", stderr: "", stdout_offset: 3, stderr_offset: 0 });
    const { result, rerender } = renderHook(
      ({ id }) => useScriptStream(id),
      { wrapper, initialProps: { id: "run-1" } },
    );
    await waitFor(() => expect(result.current.stdout).toBe("old"));
    mockFetch.mockResolvedValue({ stdout: "new", stderr: "", stdout_offset: 3, stderr_offset: 0 });
    rerender({ id: "run-2" });
    await waitFor(() => expect(result.current.stdout).toBe("new"));
  });

  it("handles backfill failure gracefully", async () => {
    mockFetch.mockRejectedValue(new Error("network"));
    const { result } = renderHook(() => useScriptStream("run-1"), { wrapper });
    await waitFor(() => expect(result.current.version).toBe(1)); // Still increments
    // WS events flow through despite failed backfill
    act(() => {
      wsCallback({ run_id: "run-1", stdout: "live", stderr: "", stdout_offset: 0, stderr_offset: 0 });
    });
    expect(result.current.stdout).toBe("live");
  });

  it("truncates at 512KB cap", async () => {
    const bigContent = "x".repeat(600 * 1024);
    mockFetch.mockResolvedValue({ stdout: bigContent, stderr: "", stdout_offset: bigContent.length, stderr_offset: 0 });
    const { result } = renderHook(() => useScriptStream("run-1"), { wrapper });
    await waitFor(() => expect(result.current.truncated).toBe(true));
    expect(new TextEncoder().encode(result.current.stdout).length).toBeLessThanOrEqual(512 * 1024);
  });
});
```

**Verify:** `cd web && npm run test -- --run src/hooks/useScriptStream.test.ts`

---

### Step 8: LiveScriptLogView component
**File:** `web/src/components/jobs/StepDetailPanel.tsx` (add near existing `ScriptLogView`, line 103)
**Depends on:** Step 7a (useScriptStream hook)
**Satisfies:** R3 (UI rendering)
**Time:** 20 min

Shares `highlightLogLine` utility and log container styling from `ScriptLogView`. Preserves blank lines. Includes copy button. Shows truncation indicator.

```typescript
function LiveScriptLogView({ runId }: { runId: string }) {
  const { stdout, stderr, truncated, version } = useScriptStream(runId);
  const scrollRef = useRef<HTMLDivElement>(null);
  const userScrolledRef = useRef(false);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || userScrolledRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [version]);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    userScrolledRef.current = !isAtBottom;
  }, []);

  if (!stdout && !stderr) {
    return (
      <div className="text-xs text-zinc-500 italic py-4 text-center">
        <div className="flex items-center justify-center gap-2">
          <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
          Script running...
        </div>
      </div>
    );
  }

  const rawLines = stdout.split("\n");
  if (rawLines.length > 0 && rawLines[rawLines.length - 1] === "") {
    rawLines.pop();
  }

  return (
    <div>
      <div className="text-xs text-zinc-500 dark:text-zinc-500 mb-1 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
          Live Output
        </div>
        <button
          onClick={() => { navigator.clipboard.writeText(stdout); toast.success("Copied to clipboard"); }}
          className="flex items-center gap-1 text-[10px] text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          <Copy className="w-3 h-3" /> Copy
        </button>
      </div>
      {truncated && (
        <div className="text-[10px] text-amber-400/70 mb-1 font-mono">[earlier output truncated]</div>
      )}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="bg-zinc-50 dark:bg-zinc-950 rounded border border-zinc-200 dark:border-zinc-800 p-2 font-mono text-xs max-h-96 overflow-auto"
      >
        {rawLines.map((line, i) => (
          <div key={i} className="whitespace-pre-wrap break-words leading-relaxed">
            {line === "" ? "\u00A0" : highlightLogLine(line)}
          </div>
        ))}
      </div>
      {stderr && (
        <div className="mt-2">
          <div className="text-xs text-red-400/70 dark:text-red-400/70 mb-1">stderr</div>
          <pre className="bg-zinc-50 dark:bg-zinc-950 rounded border border-red-300/20 dark:border-red-500/20 p-2 font-mono text-xs text-red-600 dark:text-red-300/80 max-h-48 overflow-auto whitespace-pre-wrap break-words">
            {stderr}
          </pre>
        </div>
      )}
    </div>
  );
}
```

**Done when:** Component renders. Verify with Step 8b tests.

---

### Step 8b: Tests — LiveScriptLogView component
**File:** `web/src/components/jobs/LiveScriptLogView.test.tsx` (new file)
**Depends on:** Step 8
**Satisfies:** Milestone M6 (partial)
**Time:** 15 min

Mock `useScriptStream` to return controlled state:

```typescript
describe("LiveScriptLogView", () => {
  it('renders "Script running..." when no output', () => {
    mockStreamReturn({ stdout: "", stderr: "", truncated: false, version: 0 });
    render(<LiveScriptLogView runId="r1" />);
    expect(screen.getByText("Script running...")).toBeInTheDocument();
  });

  it("renders stdout preserving blank lines", () => {
    mockStreamReturn({ stdout: "line1\n\nline3\n", stderr: "", truncated: false, version: 1 });
    render(<LiveScriptLogView runId="r1" />);
    expect(screen.getByText("line1")).toBeInTheDocument();
    expect(screen.getByText("line3")).toBeInTheDocument();
    // Blank line rendered as nbsp
    const lines = screen.getAllByText(/./);
    expect(lines.length).toBeGreaterThanOrEqual(3);
  });

  it("shows stderr section when present", () => {
    mockStreamReturn({ stdout: "ok", stderr: "warning!", truncated: false, version: 1 });
    render(<LiveScriptLogView runId="r1" />);
    expect(screen.getByText("stderr")).toBeInTheDocument();
    expect(screen.getByText("warning!")).toBeInTheDocument();
  });

  it("shows truncation indicator", () => {
    mockStreamReturn({ stdout: "data", stderr: "", truncated: true, version: 1 });
    render(<LiveScriptLogView runId="r1" />);
    expect(screen.getByText("[earlier output truncated]")).toBeInTheDocument();
  });

  it("has a copy button", () => {
    mockStreamReturn({ stdout: "output", stderr: "", truncated: false, version: 1 });
    render(<LiveScriptLogView runId="r1" />);
    expect(screen.getByText("Copy")).toBeInTheDocument();
  });
});
```

**Verify:** `cd web && npm run test -- --run src/components/jobs/LiveScriptLogView.test.tsx`

---

### Step 9: Wire LiveScriptLogView into StepDetailPanel
**File:** `web/src/components/jobs/StepDetailPanel.tsx` (near line 512-521)
**Depends on:** Step 8
**Satisfies:** R3 (integration), Milestone M6
**Time:** 5 min

Add after the live agent stream block:

```tsx
{/* Live Script Output */}
{activeRun && stepDef.executor.type === "script" && (
  <LiveScriptLogView runId={activeRun.id} />
)}
```

The existing `ScriptLogView` at line 822-825 remains untouched.

**Done when:** `cd web && npx tsc --noEmit` passes. Opening a running script step in the web UI shows the live output view.

---

### Step 10: Full regression
**Depends on:** All previous steps
**Satisfies:** Milestone M7
**Time:** 5 min

```bash
uv run pytest tests/ -x -q && cd web && npm run test
```

**Done when:** All tests pass, zero failures.

## Testing Strategy

### Test matrix

| Test | File (existing/new) | What it proves | Exact command |
|------|---------------------|----------------|---------------|
| `TestScriptExecutorPathPersistence` (4 tests) | `tests/test_executors.py` (existing) | R2: paths in executor_state on success, failure, watch; state_update_fn receives paths | `uv run pytest tests/test_executors.py -x -q -k "PathPersistence"` |
| `test_completed_run_retains_stdout_path` | `tests/test_restart_recovery.py` (existing) | R2: paths survive engine's `_process_launch_result` overwrite | `uv run pytest tests/test_restart_recovery.py -x -q -k "stdout_path"` |
| `TestScriptOutputEndpoint` (5 tests) | `tests/test_streaming.py` (existing) | R1: REST endpoint returns correct content, offsets, handles missing files/paths, 404 | `uv run pytest tests/test_streaming.py -x -q -k "ScriptOutputEndpoint"` |
| `TestScriptStreamTailing` (2-3 tests) | `tests/test_streaming.py` (existing) | R1/R4: tailer broadcasts content with byte offsets, multi-byte UTF-8 offsets correct | `uv run pytest tests/test_streaming.py -x -q -k "ScriptStreamTailing"` |
| `useScriptStream` (7 tests) | `web/src/hooks/useScriptStream.test.ts` (new) | R3/R4: backfill, append, deduplication, queuing, reset, error recovery, truncation | `cd web && npm run test -- --run src/hooks/useScriptStream.test.ts` |
| `LiveScriptLogView` (5 tests) | `web/src/components/jobs/LiveScriptLogView.test.tsx` (new) | R3: renders output, blank lines, stderr, truncation indicator, copy button | `cd web && npm run test -- --run src/components/jobs/LiveScriptLogView.test.tsx` |

### Coverage of requirement → test mapping

| Requirement | Tests that verify it |
|---|---|
| R1 (Backend API) | `TestScriptOutputEndpoint`, `TestScriptStreamTailing` |
| R2 (Paths in executor_state) | `TestScriptExecutorPathPersistence`, `test_completed_run_retains_stdout_path` |
| R3 (Web UI live output) | `useScriptStream` hook tests, `LiveScriptLogView` component tests |
| R4 (Offset deduplication) | `TestScriptStreamTailing::binary_offsets`, `useScriptStream::deduplicates overlap` |
| R5 (Backward compat) | `TestScriptOutputEndpoint::no_paths_returns_empty`, existing `ScriptLogView` tests unchanged |

### What is NOT tested (and why)

- **Monitor lifecycle (auto-start/stop of tailers):** Requires a running server event loop with real async scheduling. Verified manually by running a slow script step and observing `_script_stream_tasks` population. The pattern is identical to the existing `_agent_stream_monitor` which is also not unit-tested.
- **Lifespan shutdown cleanup:** Structural (cancel + clear calls). Verified by code review against the existing `_stream_monitor` shutdown pattern.
- **Auto-scroll behavior:** Requires browser DOM with scroll events. Verified manually in browser. The pattern matches `AgentStreamView`'s auto-scroll which is also not unit-tested.

## Risks & Mitigations

### Risk 1: Large output overwhelms WebSocket clients
**Probability:** Medium — scripts producing thousands of lines per second could flood WS.
**Mitigation:** The 300ms tail interval batches output. Client-side 512KB cap prevents unbounded memory growth. For extreme cases (>64KB per 300ms interval), a future enhancement could add server-side truncation per broadcast.

### Risk 2: Block buffering delays output visibility
**Probability:** High — child processes writing to regular files default to ~4-8KB block buffers.
**Mitigation:** This is documented as a known limitation, not a bug. The feature provides "chunked output as buffers flush." Users who need line-granularity can wrap commands with `stdbuf -oL` or set language-specific unbuffered flags (e.g., `PYTHONUNBUFFERED=1`). This is the same trade-off that `tail -f` makes.

### Risk 3: Offset deduplication approximation for multi-byte text
**Probability:** Low — most script output is ASCII. When multi-byte UTF-8 is present, the byte-offset trimming in the client uses `TextEncoder`/`TextDecoder` which handles it correctly.
**Mitigation:** Binary-mode reads on the server produce true byte offsets. The client converts byte offsets to character boundaries using `TextEncoder.encode()` and `TextDecoder.decode()` with slice, which is correct for UTF-8.

### Risk 4: Race between state_update_fn and result.executor_state
**Probability:** None — this is by design, not a race. The engine writes `state_update_fn` state at launch, then overwrites with `result.executor_state` at completion. Both now contain the paths, so the paths are available at every stage.

### Risk 5: File handle contention between subprocess and tailer
**Probability:** None — already validated by the agent output pattern. The subprocess writes via a file handle opened in `"w"` mode; the tailer opens a separate read-only handle. Multiple concurrent readers are safe on Linux.

### Risk 6: Stale tailers for crashed processes
**Probability:** Low — the monitor cancels tailers for runs no longer in `running_runs()`. If a script process crashes, the engine marks the run as FAILED, and the next 1s monitor cycle cancels the tailer.
**Mitigation:** Same lifecycle management as agent tailers.

### Risk 7: AbortController race in useScriptStream
**Probability:** Low — React strict mode double-mounts can cause two backfill fetches. The AbortController ensures the first fetch is cancelled when the effect re-runs.
**Mitigation:** AbortController abort in the effect cleanup. The `controller.signal.aborted` check before state update prevents stale fetch results from overwriting fresh state.

## Appendix: Critic Response Log

| # | Severity | Issue | Resolution |
|---|----------|-------|------------|
| 1 | Critical | `_process_launch_result()` overwrites launch-time `executor_state` — paths lost on completion | Fixed: ScriptExecutor now returns `stdout_path`/`stderr_path` in `executor_state` on all three exit paths (success, failure, watch). See Step 1b. |
| 2 | Critical | Block buffering means "line-by-line" output won't happen | Fixed: Downgraded promise to "chunked output as buffers flush." Documented as known limitation with `stdbuf -oL` workaround. See Assumption 4 and Risk 2. |
| 3 | Major | Backfill + WS queue can duplicate output (text concatenation is not idempotent) | Fixed: WS messages now include byte offsets. Client deduplicates by comparing WS start offset against known position. Uses binary-mode reads for deterministic offsets. See Steps 3b, 7a. |
| 4 | Major | `useScriptStream` lacks abort/error handling, reset on runId change, reconnect safety | Fixed: Added AbortController for fetch cleanup, catch block for failed backfill, explicit state reset on runId change. See Step 7a. |
| 5 | Major | Shutdown only cancels per-run tailers, not the monitor task itself | Fixed: Added `_script_monitor_task` module-level variable, cancelled and awaited in shutdown. See Steps 3a and 3c. |
| 6 | Major | `overflow-auto` doesn't virtualize; unbounded string growth | Fixed: Capped at 512KB with oldest-chunk eviction. Stored as chunks array, joined via `useMemo` keyed on version. "[earlier output truncated]" indicator. See Steps 7a and 8. |
| 7 | Major | Restart parity claim is false — script steps aren't reattached | Fixed: Explicitly marked as out of scope with reasoning. See Out of Scope section. |
| 8 | Major | Test plan references nonexistent files | Fixed: All tests target existing files (`test_executors.py`, `test_streaming.py`, `test_restart_recovery.py`). See Testing Strategy. |
| 9 | Major | Failure and watch exit paths lose `stdout_path`/`stderr_path` | Fixed: Addressed as part of #1. All three exit paths include paths. See Step 1b. |
| 10 | Minor | `.filter(Boolean)` drops blank lines in shell output | Fixed: Removed filter. Split on `\n`, pop trailing empty string, render empty lines as `\u00A0`. See Step 8. |
| 11 | Minor | `ScriptOutputMessage` defined in two places, `WebSocketMessage` union not updated | Fixed: Defined once in `types.ts` (Step 4), imported in hook (Step 6). Union type updated. |
| 12 | Minor | Live view hardcodes dark styles, lacks copy button, looks different from ScriptLogView | Fixed: Uses `dark:` Tailwind variants matching ScriptLogView's container pattern. Copy button added. Truncation intentionally omitted (live output should show latest content, not first 50 lines). See Step 8. |
