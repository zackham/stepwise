---
title: "Implementation Plan: Add stepwise uninstall command"
date: "2026-03-20T16:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: `stepwise uninstall`

## Overview

Add a `stepwise uninstall` CLI subcommand that cleanly removes stepwise from a project by deleting `.stepwise/`, optionally removing the CLI tool itself, and printing a summary of what was removed. The command checks for running jobs and a running server before proceeding, and uses interactive confirmation prompts for destructive actions.

## Requirements

### R1: Running job detection
- Before removing anything, open the project's SQLite store and check for RUNNING jobs via `store.active_jobs()` (`store.py:212-217`).
- If active jobs exist, print a warning listing job IDs and exit with `EXIT_USAGE_ERROR` unless `--force` is passed.
- **Acceptance criteria:** `stepwise uninstall` with a RUNNING job prints the job IDs and exits 2. With `--force`, it proceeds. With no active jobs, it continues normally.

### R2: Stop running server
- If a server is running for this project (detected via `detect_server(project.dot_dir)` from `server_detect.py:13-50`), stop it before removing `.stepwise/`.
- Use the same SIGTERM→wait→SIGKILL pattern as `_server_stop()` (`cli.py:517-553`), but inlined to avoid redundant `_find_project_or_exit()` call.
- **Acceptance criteria:** A running server is stopped before directory removal. No error if no server is running. Server PID file is cleaned up.

### R3: Remove `.stepwise/` directory
- Delete the entire `.stepwise/` directory tree via `shutil.rmtree(project.dot_dir)`.
- Confirm with the user via `io.prompt_confirm()` before deletion unless `--yes` is passed.
- **Acceptance criteria:** After uninstall, `.stepwise/` no longer exists. Without `--yes`, user is prompted with `default=False` (must explicitly type "y"). Declining skips removal and exits 0.

### R4: Optionally remove `flows/` directory
- If `flows/` exists under the project root, offer to remove it.
- `--remove-flows` flag removes without prompting. Without the flag, prompt the user (default=False).
- **Acceptance criteria:** `flows/` is only removed when user explicitly confirms or passes `--remove-flows`. If `flows/` doesn't exist, no prompt shown.

### R5: Optionally uninstall CLI tool
- `--cli` flag triggers removal of the `stepwise` CLI binary via `_detect_install_method()` (`cli.py:2955-2976`).
- Uninstall commands: `uv tool uninstall stepwise-run`, `pipx uninstall stepwise-run`, `pip uninstall -y stepwise-run`.
- Without `--cli`, only the project-local `.stepwise/` is removed.
- **Acceptance criteria:** `--cli` runs the correct uninstall subprocess for the detected install method. Without `--cli`, the binary remains. Subprocess failure prints an error but doesn't change the exit code (prior removals already succeeded).

### R6: Clean up `.gitignore` entries
- Remove the three lines added by `init_project()` (`project.py:112`): `.stepwise/`, `config.local.yaml`, `*.config.local.yaml`.
- Read the outer `.gitignore` (`project.root / ".gitignore"`), filter out exact matches for these entries, write back.
- Preserve all other lines and trailing newline.
- **Acceptance criteria:** After uninstall, `.gitignore` no longer contains the three stepwise entries. Other lines are untouched. If `.gitignore` doesn't exist, no error.

### R7: Print summary and goodbye
- Track each action taken in a list. Print summary at the end listing removed items.
- Print a friendly goodbye message.
- **Acceptance criteria:** Output clearly lists each removed item. Exit code 0 on success.

## Assumptions

1. **`find_project()` walks up from cwd and raises `ProjectNotFoundError`.**
   - Verified at `src/stepwise/project.py:44-61`. The `ProjectNotFoundError` class is at `project.py:25-26`.
   - In `cmd_uninstall`, we catch this gracefully — if no project exists and `--cli` isn't set, there's nothing to do.

2. **`SQLiteStore(path).active_jobs()` returns RUNNING jobs.**
   - Verified at `src/stepwise/store.py:212-217`: `SELECT * FROM jobs WHERE status = ?` with `JobStatus.RUNNING.value`.
   - `SQLiteStore` constructor takes a string path (`store.py:66-80`). The DB file is at `project.db_path` (`project.py:35`).

3. **`_detect_install_method()` returns one of `"uv"`, `"pipx"`, `"pip"`.**
   - Verified at `src/stepwise/cli.py:2955-2976`. Checks executable path, then probes `uv tool list` / `pipx list --short`.

4. **`IOAdapter.prompt_confirm(message, default)` is implemented on all adapters.**
   - ABC at `src/stepwise/io.py:127`. PlainAdapter at `io.py:406-413`. TerminalAdapter at `io.py:931-936` (delegates to questionary).

5. **Server stop uses `read_pidfile`, `_pid_alive`, `remove_pidfile` from `server_detect.py`.**
   - Verified at `src/stepwise/server_detect.py:79-99` (read_pidfile), `102-108` (_pid_alive), `93-99` (remove_pidfile).
   - Cannot reuse `_server_stop()` directly because it calls `_find_project_or_exit(args)` internally (`cli.py:523`), duplicating project lookup and risking sys.exit. Must inline using the lower-level functions.

6. **`init_project()` adds exactly three entries to `.gitignore`: `.stepwise/`, `config.local.yaml`, `*.config.local.yaml`.**
   - Verified at `src/stepwise/project.py:112`: `entries = [f"{DOT_DIR_NAME}/", "config.local.yaml", "*.config.local.yaml"]`.
   - These are appended at `project.py:113-125`. Uninstall reverses by filtering these exact strings.

7. **CLI tests call `main(argv)` from `cli.py` and use `capsys` for output capture.**
   - Verified at `tests/test_cli.py:38-44`: `rc = main(["--project-dir", str(tmp_path), "init", "--no-skill"])` with `capsys.readouterr()`.

## Out of Scope

- **Removing agent skills** (`.claude/skills/stepwise/`, `.agents/skills/stepwise/`). These are installed per-project by `stepwise init --skill` and may be version-controlled. Users should delete them manually if desired.
- **Removing global user data** (`~/.cache/stepwise/`, `~/.config/stepwise/`). These are user-level, not project-scoped. The uninstall command is project-scoped by design. Global cleanup could be a future `--global` flag.
- **Web UI or API endpoint for uninstall.** This is a CLI-only destructive operation that requires terminal confirmation.
- **Undo/rollback mechanism.** Destructive by nature; confirmation prompts are the safety net.
- **Removing acpx (npm global).** Installed separately and may be used by other tools.

## Architecture

Follows the exact pattern of existing CLI commands in `src/stepwise/cli.py`:

1. **Handler function:** `cmd_uninstall(args: argparse.Namespace) -> int` — same signature as `cmd_init` (`cli.py:241`), `cmd_self_update` (`cli.py:2979`), etc.
2. **Subparser registration:** `sub.add_parser("uninstall", ...)` in `build_parser()` — placed after `update` (`cli.py:3272`) and `welcome` (`cli.py:3275`), keeping lifecycle commands grouped.
3. **Handler dispatch:** `"uninstall": cmd_uninstall` added to the `handlers` dict in `main()` (`cli.py:3332-3358`).
4. **IO via `_io(args)`** (`cli.py:3310-3312`) for `io.log()` and `io.prompt_confirm()`.
5. **Project lookup:** Uses `find_project()` directly (not `_find_project_or_exit`) so we can handle `ProjectNotFoundError` gracefully instead of `sys.exit`-ing.
6. **Store access:** `SQLiteStore(str(project.db_path))` — same pattern as `cmd_jobs` (`cli.py:1877-1879`).
7. **Server stop:** Inlines logic from `_server_stop()` using `read_pidfile()`, `_pid_alive()`, `remove_pidfile()` from `server_detect.py` — avoids the redundant `_find_project_or_exit()` call that `_server_stop` makes.
8. **Install detection:** Reuses `_detect_install_method()` (`cli.py:2955`).

No new modules. No changes to `models.py`, `executors.py`, `engine.py`, or `server.py`.

## Implementation Steps

Steps are ordered by dependency. Each step's rationale for ordering is stated.

### Step 1: Add `_stop_server_for_project()` helper (~10 min)

**File:** `src/stepwise/cli.py`

**Why this is first:** Steps 3 and 4 depend on being able to stop the server. Extracting the server-stop logic into a helper that takes `(dot_dir, io)` instead of `args` avoids the `_find_project_or_exit` re-lookup and makes the logic testable independently.

**What to do:**
- Extract a `_stop_server_for_project(dot_dir: Path, io: IOAdapter) -> bool` function from the body of `_server_stop()` (`cli.py:517-553`).
- Returns `True` if a server was stopped, `False` if none was running.
- Uses `read_pidfile`, `_pid_alive`, `remove_pidfile` from `server_detect.py`, plus `os.kill(pid, signal.SIGTERM)` → wait loop → `SIGKILL` fallback.
- Refactor `_server_stop()` to call this new helper (reduces duplication).

**Dependencies:** None — this is a pure refactor with no behavioral change.

### Step 2: Register `uninstall` subparser in `build_parser()` (~10 min)

**File:** `src/stepwise/cli.py`

**Why second:** The parser must exist before the handler can be invoked via `main()`. Doing parser registration before the handler body ensures we can incrementally test argument parsing.

**What to do:**
- After `welcome` parser (`cli.py:3275`), add:

```python
p_uninstall = sub.add_parser("uninstall",
    help="Remove stepwise from this project")
p_uninstall.add_argument("--yes", "-y",
    action="store_true",
    help="Skip confirmation prompts")
p_uninstall.add_argument("--force",
    action="store_true",
    help="Proceed even with active jobs")
```

Plus `--remove-flows` and `--cli` flags.

- Add `"uninstall": cmd_uninstall` to the `handlers` dict in `main()` (`cli.py:3332`).
- Update module docstring (`cli.py:1-28`) with the new command.

**Dependencies:** None — the handler function doesn't need to exist yet (parser registration is independent).

### Step 3: Implement `cmd_uninstall()` — project removal core (~25 min)

**File:** `src/stepwise/cli.py`

**Why third:** Depends on Step 1 (server stop helper) and Step 2 (parser registration). This is the core handler.

**What to do:** Add `cmd_uninstall(args: argparse.Namespace) -> int` after `cmd_self_update` (~line 3070). Implementation flow:

1. **Find project** — `find_project()` in try/except `ProjectNotFoundError`. If not found and `--cli` not passed, log info ("No stepwise project found") and return `EXIT_SUCCESS`.
2. **Stop server** — call `_stop_server_for_project(project.dot_dir, io)` from Step 1. This must happen before directory removal because the server holds an open SQLite connection to the DB inside `.stepwise/` and writes to `server.pid` — removing the directory with the server running would cause the server to crash with unhandled I/O errors rather than shutting down cleanly.
3. **Check active jobs** — open `SQLiteStore(str(project.db_path))` in a try/except (DB might be locked or corrupt). Call `store.active_jobs()`. If any RUNNING jobs and not `--force`, print job IDs and return `EXIT_USAGE_ERROR`. This must happen after server stop but before directory removal because: (a) the store might be locked by the server process, and (b) we need the DB to still exist to query it.
4. **Confirm and remove `.stepwise/`** — `io.prompt_confirm(f"Remove {project.dot_dir} and all job history?", default=False)` unless `--yes`. Call `shutil.rmtree(project.dot_dir)`. Catch `OSError` and log warning if removal fails (e.g., permissions).
5. **Clean `.gitignore`** — must happen after `.stepwise/` removal so the state is consistent. Read `project.root / ".gitignore"`, filter out the three entries from `init_project()` (`project.py:112`), write back. Skip if file doesn't exist.

**Dependencies:** Step 1 (server stop helper), Step 2 (parser/handler registration).

### Step 4: Implement `cmd_uninstall()` — optional removals (~15 min)

**File:** `src/stepwise/cli.py`

**Why fourth:** Extends Step 3's handler with optional `flows/` removal and CLI uninstall. These are independent of the core removal and run after it, so they're a natural second pass.

**What to do:** Add to the end of `cmd_uninstall()`:

1. **Optional `flows/` removal** — check if `project.root / "flows"` exists. If yes and (`--remove-flows` or `io.prompt_confirm("Also remove flows/ directory?", default=False)`): `shutil.rmtree(flows_dir)`. This runs after `.stepwise/` removal because the user's flows are separate user content and the prompt should appear after the core removal is confirmed. Order matters for UX: core removal first, optional extras second.
2. **Optional CLI uninstall** — if `--cli`: call `_detect_install_method()`, build uninstall command, run via `subprocess.run()`. Uninstall commands per method:
   - `uv`: `["uv", "tool", "uninstall", "stepwise-run"]`
   - `pipx`: `["pipx", "uninstall", "stepwise-run"]`
   - `pip`: `[sys.executable, "-m", "pip", "uninstall", "-y", "stepwise-run"]`
   This must be last because once the CLI is uninstalled, the current process's imports may become invalid if Python cleans up eagerly (though in practice the process keeps its loaded modules).
3. **Print summary** — list all actions taken (tracked via a `removed: list[str]` accumulator). Print goodbye message.

**Dependencies:** Step 3 (core handler body).

### Step 5: Write tests — project removal (~20 min)

**File:** `tests/test_uninstall.py` (new)

**Why fifth:** Tests validate the implementation from Steps 1-4. Writing tests after implementation lets us test the real code paths.

**What to do:** Follow the pattern from `tests/test_cli.py:37-68` (TestInit class). Each test creates a temp dir, calls `main(argv)` with `--project-dir`, and checks filesystem state + exit code via `capsys`.

Test cases for core removal:

| Test | Setup | Call | Assert |
|---|---|---|---|
| `test_uninstall_removes_dot_dir` | `init_project(tmp)` | `main(["--project-dir", str(tmp), "uninstall", "--yes"])` | `.stepwise/` gone, rc=0 |
| `test_uninstall_no_project_exits_cleanly` | empty `tmp_path` | `main(["--project-dir", str(tmp), "uninstall"])` | rc=0, "No stepwise project" in output |
| `test_uninstall_aborts_on_running_jobs` | `init_project(tmp)`, create RUNNING job in store | `main(["--project-dir", str(tmp), "uninstall", "--yes"])` | rc=2, `.stepwise/` still exists |
| `test_uninstall_force_overrides_running_jobs` | `init_project(tmp)`, create RUNNING job | `main(["--project-dir", str(tmp), "uninstall", "--yes", "--force"])` | `.stepwise/` gone, rc=0 |
| `test_uninstall_cleans_gitignore` | `init_project(tmp)` (creates `.gitignore` with stepwise entries + add extra lines) | `main(["--project-dir", str(tmp), "uninstall", "--yes"])` | `.gitignore` exists, no stepwise lines, extra lines preserved |

Creating a RUNNING job: use `SQLiteStore(str(db_path))` + `store.save_job(Job(..., status=JobStatus.RUNNING))` per `store.py:189-196`.

**Dependencies:** Steps 1-4 (implementation complete).

### Step 6: Write tests — optional removals and server stop (~20 min)

**File:** `tests/test_uninstall.py` (continued)

**Why sixth:** Tests for the optional features from Step 4 and the server stop helper from Step 1.

Test cases:

| Test | Setup | Call | Assert |
|---|---|---|---|
| `test_uninstall_removes_flows_when_flagged` | `init_project(tmp)`, create `flows/test.flow.yaml` | `main([..., "--yes", "--remove-flows"])` | `flows/` gone, rc=0 |
| `test_uninstall_keeps_flows_by_default` | `init_project(tmp)`, create `flows/test.flow.yaml` | `main([..., "--yes"])` | `flows/` still exists |
| `test_uninstall_cli_flag_calls_subprocess` | `init_project(tmp)` | `main([..., "--yes", "--cli"])` with `patch("subprocess.run")` and `patch("stepwise.cli._detect_install_method", return_value="uv")` | `subprocess.run` called with `["uv", "tool", "uninstall", "stepwise-run"]` |
| `test_stop_server_for_project_sends_sigterm` | Write fake pidfile, mock `_pid_alive` to return False after SIGTERM | `_stop_server_for_project(dot_dir, io)` | `os.kill` called with SIGTERM, pidfile removed, returns True |
| `test_stop_server_for_project_no_server` | No pidfile | `_stop_server_for_project(dot_dir, io)` | Returns False, no errors |

**Dependencies:** Step 5 (test file created).

### Step 7: Manual smoke test and cleanup (~10 min)

**Why last:** Final verification that everything works end-to-end in a real terminal.

**What to do:**
```bash
# Run full test suite
uv run pytest tests/ -x

# Manual smoke test
mkdir /tmp/sw-uninstall-test && cd /tmp/sw-uninstall-test
stepwise init --no-skill
mkdir -p flows && echo "name: test" > flows/test.flow.yaml
stepwise uninstall                    # prompts, decline
ls .stepwise/                         # still exists
stepwise uninstall --yes              # removes .stepwise/
ls .stepwise/                         # gone
cat .gitignore                        # no stepwise entries

# Verify --help
stepwise uninstall --help
```

**Dependencies:** Steps 1-6 complete.

## Testing Strategy

### Test commands

```bash
# Run only uninstall tests
uv run pytest tests/test_uninstall.py -v

# Run all CLI tests (regression)
uv run pytest tests/test_cli.py -v

# Full suite
uv run pytest tests/ -x

# Specific test
uv run pytest tests/test_uninstall.py::TestUninstall::test_uninstall_removes_dot_dir -v
```

### Test infrastructure

- **Project creation:** `init_project(tmp_path)` from `stepwise.project` — creates `.stepwise/` with all subdirs, `.gitignore`, config files (same as `tests/test_cli.py:38`).
- **Job creation for active-job tests:** `SQLiteStore(str(tmp_path / ".stepwise" / "stepwise.db"))` + `store.save_job(Job(id="test-1", status=JobStatus.RUNNING, ...))` — per `store.py:189-196`.
- **CLI invocation:** `main(["--project-dir", str(tmp_path), "uninstall", "--yes"])` — per pattern at `tests/test_cli.py:39`.
- **Output capture:** `capsys.readouterr()` for stdout/stderr checking.
- **Subprocess mocking:** `unittest.mock.patch("subprocess.run")` for CLI uninstall tests (don't actually uninstall stepwise during tests).
- **Server stop mocking:** `unittest.mock.patch("stepwise.server_detect.read_pidfile")` and `patch("stepwise.server_detect._pid_alive")` for server stop tests.

### Coverage targets

- Every flag combination: `--yes`, `--force`, `--remove-flows`, `--cli`
- Error paths: no project, running jobs without `--force`, DB locked/corrupt
- Edge cases: `.gitignore` with no stepwise entries, `.gitignore` missing, `flows/` missing

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| User accidentally deletes job data | Medium | High | `prompt_confirm` with `default=False` requires explicit "y". `--yes` is opt-in. Prompt shows absolute path of `.stepwise/` being deleted. |
| Server left running after `.stepwise/` removed | Low | High (server crashes with I/O errors) | Server is explicitly stopped (SIGTERM → 5s wait → SIGKILL) before any directory removal. Rationale: the server holds open SQLite connections and writes to files inside `.stepwise/`. |
| `.gitignore` cleanup corrupts file | Low | Low | Only filter exact matches for the three known entries (`project.py:112`). Preserve all other lines. Skip if file missing. Write atomically (read → filter → write). |
| `--cli` uninstall fails | Low | Low | Print error message with the failed command. Return `EXIT_SUCCESS` anyway — the project removal already succeeded and the user can run the uninstall command manually. |
| DB locked by server or another CLI process | Medium | Medium | Stop server first (Step 1 helper). Wrap `SQLiteStore()` open in try/except `sqlite3.OperationalError`. If locked, suggest `--force` to skip the active-job check and proceed anyway. |
| `shutil.rmtree` fails on permissions (e.g., root-owned files in `.stepwise/`) | Low | Medium | Catch `OSError`, log warning with the path and error, continue with remaining steps (`.gitignore` cleanup, summary). |
| Refactoring `_server_stop` to use the new helper introduces regression | Low | Medium | Existing server tests (`tests/test_cli.py` server tests if any, manual `stepwise server stop` test) verify behavior is preserved. The helper has identical logic. |
