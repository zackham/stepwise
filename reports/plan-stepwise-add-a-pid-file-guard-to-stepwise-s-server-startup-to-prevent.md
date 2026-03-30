# Plan: PID-File Guard for Server Startup

## Overview

Prevent duplicate stepwise server processes from running against the same project. Currently, launching `stepwise server start` or `stepwise server_bg` twice creates two processes fighting over the SQLite DB (WAL mode doesn't save you from two full servers with engines, WebSockets, and thread pools), causing "Executor task lost" thread pool crashes and cascade failures.

The fix: add a PID-file-based guard at two layers — early in the `server.py` lifespan (deepest defense), and in `server_bg.py` (fast-fail before uvicorn even starts). Signal handlers ensure cleanup on non-graceful termination.

## Current State

**What exists:**
- `cli.py:_server_start()` (line 599-606) calls `detect_server()` before launching — catches the common case of a user running `stepwise server start` twice
- `write_pidfile()` / `remove_pidfile()` in `server_detect.py` — called by both foreground (cli.py:656) and detached (server_bg.py:60) startup paths
- `finally` blocks in both paths remove the pidfile on clean exit
- `detect_server()` checks global registry + pidfile + health probe

**What's missing (the gaps that cause crashes):**
1. **No guard in `server_bg.py`**: If invoked directly (systemd, cron, manual) or if the CLI check races, it writes the pidfile unconditionally over any existing one
2. **No guard in `server.py:lifespan()`**: The FastAPI app opens the SQLite DB and starts the engine without checking for an existing server process — this is the critical gap
3. **Race window in CLI path**: `detect_server()` check at line 599 and `write_pidfile()` at line 656 are ~50 lines apart with port finding, uvicorn import, env setup, and browser launch in between — plenty of time for a second process to slip through
4. **No signal handler cleanup**: SIGTERM from `kill` or system shutdown doesn't remove the pidfile unless uvicorn's shutdown propagates to the `finally` block (it usually does for SIGTERM, but SIGINT from Ctrl-C in foreground may not)
5. **Stale pidfile after crash/SIGKILL**: The `_detect_server_from_pidfile()` function handles this for detection purposes, but `write_pidfile()` itself doesn't check — it blindly overwrites

## Requirements

### R1: Guard in `server.py` lifespan
- **What**: At the very top of `lifespan()`, before opening the SQLite store, check for an existing live server process
- **Acceptance criteria**:
  - If `.stepwise/server.pid` exists and that PID is alive → log clear error message and raise `SystemExit(1)` (prevents the app from starting)
  - If `.stepwise/server.pid` exists but PID is dead → log warning, remove stale file, proceed
  - If no pidfile exists → proceed normally
  - The check uses `os.kill(pid, 0)` for liveness (same as existing `_pid_alive()`)

### R2: Guard in `server_bg.py`
- **What**: Before writing the pidfile and calling uvicorn, check for an existing live server
- **Acceptance criteria**:
  - Same check-and-fail logic as R1
  - Exits with code 1 and writes error to log file
  - Prevents the detached process from silently overwriting a live pidfile

### R3: Signal handler cleanup
- **What**: Register SIGTERM and SIGINT handlers in `server_bg.py` that remove the pidfile
- **Acceptance criteria**:
  - `SIGTERM` → remove pidfile → re-raise (let uvicorn's shutdown proceed)
  - `SIGINT` → remove pidfile → re-raise
  - Handlers don't interfere with uvicorn's own signal handling (install handlers before `uvicorn.run()`, uvicorn replaces them, so we clean up in the `finally` block which is already there — or use `atexit`)
  - After SIGKILL: pidfile remains (unavoidable), caught by stale-PID detection on next startup

### R4: Atomic guard function in `server_detect.py`
- **What**: Extract the check-pidfile-guard logic into a reusable function
- **Acceptance criteria**:
  - `acquire_pidfile_guard(project_dir, port, log_file=None) → Path` — checks for existing live server, cleans stale pidfile, writes new pidfile, returns path
  - Raises `ServerAlreadyRunning` (new exception) if a live server is detected
  - Used by both `server_bg.py` and `server.py:lifespan()`
  - The function is the single source of truth for the check-and-write sequence

### R5: Tests
- **What**: Unit tests for the guard function and integration test for the duplicate-start scenario
- **Acceptance criteria**:
  - Test: stale pidfile (dead PID) → cleaned up, new pidfile written
  - Test: live pidfile (current PID) → `ServerAlreadyRunning` raised
  - Test: no pidfile → pidfile written
  - Test: corrupt/unreadable pidfile → treated as absent

## Assumptions

- **Verified**: `_pid_alive()` already exists in `server_detect.py:169` and uses `os.kill(pid, 0)` — we reuse it
- **Verified**: `write_pidfile()` and `remove_pidfile()` already exist — we wrap them
- **Verified**: `server_bg.py` is the only detached entry point (spawned by `_server_start_detached` in cli.py:686-708)
- **Verified**: `lifespan()` in server.py runs before any request handling — raising `SystemExit` there prevents the app from serving
- **Verified**: The `finally` block in both cli.py (line 664-665) and server_bg.py (line 70-72) already calls `remove_pidfile()` — signal handlers are belt-and-suspenders
- **Assumption**: `fcntl.flock()` file locking is unnecessary — the PID check + write has a tiny race window (milliseconds) that's acceptable for this use case. The guard is defense-in-depth behind the CLI-level `detect_server()` check. If we ever need stricter atomicity, we can add flock later.

## Implementation Steps

### Step 1: Add `ServerAlreadyRunning` exception and `acquire_pidfile_guard()` to `server_detect.py`

**File**: `src/stepwise/server_detect.py`

Add a new exception class and a guard function that combines check + cleanup + write:

```python
class ServerAlreadyRunning(Exception):
    """Raised when a server is already running for this project."""
    def __init__(self, pid: int, url: str):
        self.pid = pid
        self.url = url
        super().__init__(f"Server already running (PID {pid}) at {url}")


def acquire_pidfile_guard(
    project_dir: Path,
    port: int,
    *,
    pid: int | None = None,
    log_file: str | None = None,
) -> Path:
    """Check for existing server, clean stale pidfile, write new one.

    Raises ServerAlreadyRunning if a live server is detected.
    Returns path to the newly written pidfile.
    """
    existing = read_pidfile(project_dir)
    if existing:
        existing_pid = existing.get("pid")
        existing_url = existing.get("url", "unknown")
        if existing_pid and _pid_alive(existing_pid):
            raise ServerAlreadyRunning(existing_pid, existing_url)
        else:
            # Stale pidfile — clean up
            import logging
            logging.getLogger("stepwise.server_detect").warning(
                "Stale server.pid (PID %s dead) — cleaning up.",
                existing_pid,
            )
            remove_pidfile(project_dir)

    return write_pidfile(project_dir, port, pid=pid, log_file=log_file)
```

**Key decisions:**
- Reuses `read_pidfile()` and `_pid_alive()` — no duplicated logic
- Returns the pidfile path (same as `write_pidfile()`) for callers that need it
- Exception carries `pid` and `url` for clear error messages
- Stale cleanup logs at WARNING level (visible in server.log)

### Step 2: Add guard to `server.py:lifespan()`

**File**: `src/stepwise/server.py`, in the `lifespan()` function

Insert the guard check at the very top of `lifespan()`, before `ThreadSafeStore(db_path)`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _engine, _engine_task, _event_loop, _templates_dir, _project_dir

    _event_loop = asyncio.get_running_loop()
    db_path = os.environ.get("STEPWISE_DB", "stepwise.db")
    # ... existing env var reads ...

    dot_dir = _project_dir / ".stepwise"

    # ── PID-file guard: prevent duplicate server processes ──
    from stepwise.server_detect import acquire_pidfile_guard, ServerAlreadyRunning, remove_pidfile
    _port = int(os.environ.get("STEPWISE_PORT", "8340"))
    try:
        acquire_pidfile_guard(dot_dir, _port)
    except ServerAlreadyRunning as e:
        logger.error("Cannot start: %s", e)
        raise SystemExit(1) from e

    # ... rest of lifespan continues ...
```

**Key decisions:**
- Placed before `ThreadSafeStore(db_path)` — prevents opening the DB if another server is live
- Uses `SystemExit(1)` which FastAPI/uvicorn handles as a clean shutdown signal
- The `dot_dir` computation must be moved up (currently at line 942) — just need to reorder 2 lines
- The `_port` read is moved up from line 981 (currently used for `register_server()`)
- The lifespan's shutdown section already calls `unregister_server()` — we add `remove_pidfile(dot_dir)` alongside it for defense-in-depth (the caller's finally block usually handles this, but the lifespan is the right place for the server.py-level cleanup)

**Note on pidfile ownership**: In the foreground path, `cli.py` writes the pidfile at line 656 BEFORE calling `uvicorn.run()`. So by the time `lifespan()` runs, the pidfile already exists with the current PID. The guard must not reject its own pidfile. Fix: compare `existing_pid == os.getpid()` — if they match, it's our own pidfile from the caller, skip the check. Update `acquire_pidfile_guard()` to handle this:

```python
if existing_pid and existing_pid != (pid or os.getpid()) and _pid_alive(existing_pid):
    raise ServerAlreadyRunning(existing_pid, existing_url)
```

This is important: in the foreground path, `cli.py:656` writes the pidfile, then `uvicorn.run()` starts, then `lifespan()` runs in the same process. Without the self-PID check, the guard would reject itself.

### Step 3: Add guard to `server_bg.py`

**File**: `src/stepwise/server_bg.py`

Replace the bare `write_pidfile()` call with `acquire_pidfile_guard()`:

```python
def main() -> int:
    # ... existing arg parsing and logging setup ...

    from stepwise.server_detect import acquire_pidfile_guard, ServerAlreadyRunning, remove_pidfile

    dot_dir = Path(args.dot_dir)

    try:
        acquire_pidfile_guard(dot_dir, args.port, log_file=str(log_path))
    except ServerAlreadyRunning as e:
        logging.getLogger("stepwise").error("Cannot start: %s", e)
        return 1

    try:
        import uvicorn
        uvicorn.run(...)
    finally:
        remove_pidfile(dot_dir)
        log_fd.close()

    return 0
```

**Key decisions:**
- Guard runs after logging is set up (so the error message goes to the log file)
- Returns 1 instead of `SystemExit` — this is a `main()` function that returns exit codes
- The `finally` block cleanup is preserved as-is

### Step 4: Add `atexit` handler as belt-and-suspenders cleanup

**File**: `src/stepwise/server_bg.py`

After `acquire_pidfile_guard()` succeeds, register an `atexit` handler:

```python
import atexit
atexit.register(remove_pidfile, dot_dir)
```

This catches cases where the `finally` block doesn't run (e.g., `os._exit()` from a library, some signal edge cases). `atexit` handlers run on normal interpreter shutdown, including after SIGTERM if the default handler raises `SystemExit`. They do NOT run on SIGKILL (nothing does).

### Step 5: Update `lifespan()` shutdown to remove pidfile

**File**: `src/stepwise/server.py`

In the shutdown phase of `lifespan()` (after `yield`), add pidfile removal alongside the existing `unregister_server()` call:

```python
    store.close()
    unregister_server(str(_project_dir))
    # Defense-in-depth: remove pidfile from lifespan too
    # (caller's finally block usually does this, but cover all paths)
    from stepwise.server_detect import remove_pidfile
    remove_pidfile(_project_dir / ".stepwise")
```

### Step 6: Avoid double-guard in foreground path

**File**: `src/stepwise/cli.py`

The foreground path currently calls `write_pidfile()` at line 656. Since `lifespan()` now calls `acquire_pidfile_guard()`, and the foreground path has the self-PID check (Step 2), this still works. However, we should also update the foreground path to use `acquire_pidfile_guard()` for consistency:

```python
# Line 654-656: Replace write_pidfile with acquire_pidfile_guard
from stepwise.server_detect import acquire_pidfile_guard, ServerAlreadyRunning, remove_pidfile
log_file = str(project.logs_dir / "server.log")
try:
    acquire_pidfile_guard(project.dot_dir, port, log_file=log_file)
except ServerAlreadyRunning as e:
    io.log("error", f"Cannot start: {e}")
    return EXIT_JOB_FAILED
```

This makes the foreground path consistent with the detached path. The existing `detect_server()` check at line 599 is still the primary user-facing check (returns EXIT_SUCCESS with helpful message). The `acquire_pidfile_guard()` is a safety net for races.

### Step 7: Tests

**File**: `tests/test_pidfile_guard.py` (new file)

```python
"""Tests for PID-file guard preventing duplicate server processes."""
import json
import os
import tempfile
from pathlib import Path

import pytest

from stepwise.server_detect import (
    acquire_pidfile_guard,
    ServerAlreadyRunning,
    read_pidfile,
    remove_pidfile,
    write_pidfile,
)


@pytest.fixture
def project_dir():
    """Create a temporary .stepwise/ directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        dot_dir = Path(tmpdir) / ".stepwise"
        dot_dir.mkdir()
        yield dot_dir


class TestAcquirePidfileGuard:
    def test_no_existing_pidfile(self, project_dir):
        """Fresh start — no pidfile exists."""
        path = acquire_pidfile_guard(project_dir, 8340)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["pid"] == os.getpid()
        assert data["port"] == 8340

    def test_stale_pidfile_cleaned_up(self, project_dir):
        """Pidfile with dead PID is cleaned up and new one written."""
        # Write a pidfile with a PID that definitely doesn't exist
        write_pidfile(project_dir, 9999, pid=99999)
        path = acquire_pidfile_guard(project_dir, 8340)
        data = json.loads(path.read_text())
        assert data["pid"] == os.getpid()  # new pidfile written

    def test_live_pidfile_raises(self, project_dir):
        """Pidfile with live PID raises ServerAlreadyRunning."""
        # Use current PID but with a DIFFERENT pid arg to simulate "another process"
        write_pidfile(project_dir, 8340, pid=os.getpid())
        # acquire_pidfile_guard with a different pid= should see the existing one as live
        with pytest.raises(ServerAlreadyRunning) as exc_info:
            acquire_pidfile_guard(project_dir, 8340, pid=os.getpid() + 1)
        assert exc_info.value.pid == os.getpid()

    def test_own_pidfile_allowed(self, project_dir):
        """Pidfile written by the same process is allowed (self-PID check)."""
        write_pidfile(project_dir, 8340, pid=os.getpid())
        # Same PID should pass (foreground path scenario)
        path = acquire_pidfile_guard(project_dir, 8340)
        assert path.exists()

    def test_corrupt_pidfile_treated_as_absent(self, project_dir):
        """Corrupt pidfile is treated as if no pidfile exists."""
        pid_file = project_dir / "server.pid"
        pid_file.write_text("not json at all {{{")
        path = acquire_pidfile_guard(project_dir, 8340)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["pid"] == os.getpid()

    def test_log_file_preserved(self, project_dir):
        """log_file parameter is written to the pidfile."""
        path = acquire_pidfile_guard(project_dir, 8340, log_file="/tmp/server.log")
        data = json.loads(path.read_text())
        assert data["log_file"] == "/tmp/server.log"
```

## Testing Strategy

**Unit tests** (Step 7 above):
```bash
uv run pytest tests/test_pidfile_guard.py -v
```

**Existing test suite** (ensure no regressions):
```bash
uv run pytest tests/ -x -q
```

**Manual smoke test** (do NOT run against production — use a temp project):
```bash
# Create isolated temp project
mkdir /tmp/test-stepwise && cd /tmp/test-stepwise
mkdir -p .stepwise

# Start server in foreground
stepwise server start --no-detach --port 9999 &
PID1=$!

# Try to start a second server (should fail with clear error)
stepwise server start --no-detach --port 9998
# Expected: error message about existing server

# Kill first server
kill $PID1

# Stale pidfile should allow restart
stepwise server start --no-detach --port 9999
# Expected: starts successfully after cleaning stale pidfile
```

## File Change Summary

| File | Change |
|------|--------|
| `src/stepwise/server_detect.py` | Add `ServerAlreadyRunning` exception, `acquire_pidfile_guard()` function |
| `src/stepwise/server.py` | Add PID guard at top of `lifespan()`, add pidfile removal in shutdown |
| `src/stepwise/server_bg.py` | Replace `write_pidfile()` with `acquire_pidfile_guard()`, add `atexit` handler |
| `src/stepwise/cli.py` | Replace `write_pidfile()` with `acquire_pidfile_guard()` in foreground path |
| `tests/test_pidfile_guard.py` | New: unit tests for guard function |

## Risks & Mitigations

- **Self-PID false positive**: The foreground path writes the pidfile before `lifespan()` runs in the same process. Mitigated by the `existing_pid != current_pid` check in `acquire_pidfile_guard()`.
- **SIGKILL leaves stale pidfile**: Unavoidable — no handler runs on SIGKILL. Mitigated by stale-PID detection (already exists, plus the guard checks `_pid_alive()`).
- **Tiny race window**: Two processes could both read "no pidfile" before either writes. Mitigated by: (a) the CLI-level `detect_server()` check is the primary guard, (b) the pidfile guard is defense-in-depth, (c) even if both write, the second `lifespan()` will see the first server's global registry entry. If stricter atomicity is needed later, add `fcntl.flock()`.
