# Plan: Comprehensive CLI Audit for Pre-1.0 Polish

**Goal:** Pre-1.0 quality pass across all Stepwise CLI commands — help text, error messages, output formatting, naming, and discoverability.

**Scope:** 40+ commands in `src/stepwise/cli.py` (~5670 lines), the `IOAdapter` system in `src/stepwise/io.py` (~1031 lines), and error paths in `runner.py`, `api_client.py`, `flow_resolution.py`.

---

## Overview

The Stepwise CLI has grown organically to 40+ commands. While the core `IOAdapter` abstraction provides a clean foundation (TerminalAdapter with Rich, PlainAdapter for pipes, QuietAdapter for silent mode), the audit reveals three systemic issues:

1. **Mixed error output patterns** — 65 raw `print("Error: ...", file=sys.stderr)` calls coexist with 258 `io.log("error", ...)` calls. The `io.log` path gives consistent styling (icons, colors, TTY-awareness); the `print` path is unstyled and bypasses quiet mode. 11 command handlers skip `io = _io(args)` entirely.

2. **Underused formatting infrastructure** — `io.table()` exists and works well but is used only 6 times. Commands like `extensions` (manual column-width calc, lines 4718-4732), `cache stats`, `docs` topics, and `templates` hand-roll formatting with raw `print()`.

3. **Missing guidance in error messages** — Many errors say *what* went wrong but not *how to fix it*. "Job not found: abc123" should suggest `stepwise jobs`. "No OpenRouter API key" should suggest `stepwise config set`.

The plan produces two deliverables: (1) a findings report (`reports/cli-audit-findings.md`) with a `| Command | Issue | Severity | Suggested Fix |` table, and (2) the fixes themselves, committed per-phase.

---

## Requirements

### R1: Findings Catalog
**Acceptance criteria:**
- Every command handler audited for: help text, error paths, output formatting, naming
- Findings in `reports/cli-audit-findings.md` with table format, categorized P0/P1/P2
- P0 = broken/confusing, P1 = rough/inconsistent, P2 = nice-to-have polish

### R2: Consistent Error Output
**Acceptance criteria:**
- All user-facing errors go through `io.log("error", msg)` — zero raw `print("Error: ...", file=sys.stderr)` in command handlers
- Error messages follow pattern: `"<what went wrong>. <how to fix>"` (suggestion present for common mistakes)
- No raw stack traces reach the user for expected error paths
- Consistent `✗` prefix via IOAdapter, not bare "Error: " strings
- Machine-facing output (`--output json`) continues using `print(json.dumps(...))` to stdout

### R3: Consistent Table/Structured Output
**Acceptance criteria:**
- All tabular output uses `io.table()` — zero hand-rolled column alignment
- `--output json` available on all list/status commands: `extensions`, `flows`, `templates`, `cache stats`, `server status`, `config get`
- When `--output json` is active, only JSON goes to stdout; decorative output goes to stderr

### R4: Help Text Quality
**Acceptance criteria:**
- `stepwise --help` groups commands logically (Execution, Jobs, Server, Project, Registry, Advanced, System)
- Every command has a concise imperative `help=` string (no trailing period)
- Complex commands (`run`, `fulfill`, `output`, `config`, `rm`) include usage examples via `epilog=`
- Deprecated flags are `SUPPRESS`'d with no dangling references
- Module docstring (lines 1-38) matches actual command set

### R5: Naming & Discoverability
**Acceptance criteria:**
- Overlapping commands clarified: `check` vs `validate` vs `preflight` (consolidate or differentiate clearly)
- Common aliases registered: `ls` → `jobs`, `log` → `logs`
- Parameter naming uniform: `--output` for format, `--input` everywhere

### R6: Graceful Degradation
**Acceptance criteria:**
- Missing project → `"Not a stepwise project. Run 'stepwise init' to create one"`
- Server unreachable → `"Server not running. Start with: stepwise server start"`
- Missing API key → `"No OpenRouter API key. Set with: stepwise config set openrouter_api_key <key>"`
- Missing optional deps (graphviz) → platform-specific install instructions
- `--quiet` suppresses all non-essential output (verify QuietAdapter coverage)

---

## Assumptions (verified against codebase)

1. **argparse is the CLI framework** — No click/typer. All parsing in `build_parser()` at line 4334. Help customization: `description`, `help`, `epilog`, custom `HelpFormatter`. Grouping requires a custom formatter. *Verified.*

2. **IOAdapter is the correct abstraction** — `io.py:69-142`. Three implementations: TerminalAdapter (Rich), PlainAdapter (plain text), QuietAdapter (silent). Factory: `create_adapter()` at line 1002. Adapter created per-command via `_io(args)`. *Verified.*

3. **65 raw `print(stderr)` error calls exist** — Heaviest offenders: `cmd_config` (12), `cmd_run` (8), `cmd_share` (6), `cmd_cache` (4), `cmd_get` (4), `cmd_help` (3). *Verified by grep.*

4. **11 handlers skip `io = _io(args)` entirely** — `cmd_config`, `cmd_run` (late), `cmd_share`, `cmd_get`, `cmd_search`, `cmd_info`, `cmd_help`, `cmd_docs`, `cmd_extensions`, `cmd_cache`, `cmd_schema`. *Verified by reading each handler.*

5. **Exit codes are well-defined** — SUCCESS(0), JOB_FAILED(1), USAGE_ERROR(2), CONFIG_ERROR(3), PROJECT_ERROR(4), SUSPENDED(5). Used across ~149 return statements. *Verified: lines 58-64.*

6. **`io.table()` is underused** — Only 6 calls. `cmd_extensions` (manual column-width calc), `cmd_cache` stats, `_list_doc_topics`, `cmd_templates` all hand-roll. *Verified.*

7. **`--output json` is ad-hoc** — Present on: `jobs`, `status`, `cancel`, `archive`, `unarchive`, `rm`, `list`, `search`, all `job` subcommands. Missing: `extensions`, `flows`, `templates`, `cache stats`, `server status`, `config get`. *Verified.*

8. **`check`/`validate`/`preflight` overlap** — `validate` (line 876): YAML syntax + warnings + auto-fix. `check` (line 1682): structure + model resolution. `preflight` (line 1775): config + requirements + models. *Verified.*

9. **`flow_resolution.py` uses raw `print()` for warnings** — Line ~86-90: `print(f"Warning: ...", file=sys.stderr)` instead of `logging.warning()`. *Verified.*

---

## Implementation Steps

### Phase 1: Discovery — Audit & Findings Report

**Step 1.1: Audit every command systematically**
- Read every `cmd_*` handler and `add_parser()` registration
- For each command check: help text quality, error path consistency (`io.log` vs `print`), output formatting (`io.table` vs manual), `--output json` support, naming clarity
- File: `src/stepwise/cli.py`

**Step 1.2: Write findings document**
- Compile into `reports/cli-audit-findings.md`
- Table: `| Command | Issue | Severity | Suggested Fix |`
- Group by severity: P0 first, then P1, then P2

**Preliminary findings from code review:**

| Command | Issue | Severity | Suggested Fix |
|---------|-------|----------|---------------|
| `rm` (line 2828) | Uses `io.log("warning", ...)` — IOAdapter only recognizes `"warn"` | P0 | Fix to `io.log("warn", ...)` |
| `fulfill` | Outputs JSON errors to stdout even in human mode (lines 3629-3637) | P0 | Distinguish JSON vs human mode |
| `config` | 12 raw `print(stderr)` calls, no `io` adapter | P1 | Add `io = _io(args)`, migrate to `io.log` |
| `run` | 8 raw `print(stderr)` calls, `io` created late | P1 | Move `io` creation early |
| `share` | 6 raw `print(stderr)` calls, no `io` adapter | P1 | Add `io`, migrate |
| `extensions` | Manual ASCII table, no `io` adapter, no `--output json` | P1 | `io.table()` + JSON support |
| `cache stats` | Manual formatting, no `--output json` | P1 | `io.table()` + JSON support |
| `docs` | Raw `print()` for errors (lines 3877, 3913-3914) | P1 | Migrate errors to `io.log` |
| `help` | Raw `print(stderr)` for missing API key (line 3969) | P1 | `io.log` + config suggestion |
| `status`/`cancel`/`logs` | "Job not found" with no guidance | P1 | Add "Run 'stepwise jobs' to list jobs" |
| `list` | `--suspended` required but not obvious from bare `stepwise list` | P1 | Default to `--suspended` or show help |
| `cache`/`job` bare | Raw `print("Usage: ...")` instead of parser help | P1 | Show subcommand help via parser |
| `agent-help` | Hardcoded `✓` via `print(f"✓ ...", file=sys.stderr)` (line 3939) | P2 | Use `io.log("success", ...)` |
| `templates` | Uses `io.log("info")` per item, not a table | P2 | Could use `io.table` but list format is acceptable |
| `run --input` | Help doesn't say "(repeatable)" | P2 | Add to help string |
| `check` vs `validate` | Overlapping purpose, unclear to users | P2 | Clarify help or consolidate |
| All (flat help) | `stepwise --help` dumps 41 commands alphabetically | P2 | Group commands logically |

---

### Phase 2: Error Output Consistency (P0/P1)

**Step 2.1: Fix P0 bugs**
- `io.log("warning", ...)` → `io.log("warn", ...)` in `cmd_rm` (line 2828)
- `cmd_fulfill`: distinguish JSON vs human mode for error output (lines 3629-3637)
- File: `src/stepwise/cli.py`
- Test: `uv run pytest tests/ -x -q -k "rm or fulfill"`

**Step 2.2: Ensure all handlers create `io` adapter early**
Add `io = _io(args)` at the top of all 11 handlers that skip it:
- `cmd_config` (1477), `cmd_run` (2022, move early), `cmd_share` (3110), `cmd_get` (2950), `cmd_info` (3281), `cmd_search` (3237), `cmd_help` (3948), `cmd_docs` (3871), `cmd_extensions` (4694), `cmd_cache` (5358), `cmd_schema` (3465)
- File: `src/stepwise/cli.py`

**Step 2.3: Migrate all `print(stderr)` errors to `io.log("error")`**
Convert all 65 `print(f"Error: ...", file=sys.stderr)` → `io.log("error", ...)`. Drop the "Error: " prefix since `io.log("error")` adds the `✗` icon automatically.

Key locations:
| Handler | Count | Lines |
|---------|-------|-------|
| `cmd_config` | 12 | 1484-1678 |
| `cmd_run` | 8 | 2009-2276 |
| `cmd_share` | 6 | 3119-3138 |
| `cmd_get`/`cmd_info` | 5 | 2950-3461 |
| `cmd_cache` | 4 | 5410-5433 |
| `cmd_jobs`/`cmd_status` | 4 | 2409-2557 |
| `cmd_help` | 3 | 3969-4050 |
| `cmd_docs` | 2 | 3877-3914 |
| Others | ~20 | scattered |

- File: `src/stepwise/cli.py`
- Test: `uv run pytest tests/ -x -q`

**Step 2.4: Add actionable suggestions to error messages**
| Current | Improved |
|---------|----------|
| `"config set requires a key"` | `"config set requires a key. Usage: stepwise config set <key> <value>"` |
| `"Job not found: abc123"` | `"Job not found: abc123. Run 'stepwise jobs' to list jobs"` |
| `"File not found: path.yaml"` | `"Flow file not found: path.yaml. Run 'stepwise flows' to list flows"` |
| `"Could not connect to server"` | `"Server not running. Start with: stepwise server start"` |
| `"No OpenRouter API key found."` | `"No OpenRouter API key. Set with: stepwise config set openrouter_api_key <key>"` |
| `"Unknown config key 'foo'"` | `"Unknown config key 'foo'. Run 'stepwise config get' to see available keys"` |

Consider a helper for the common job-not-found case:
```python
def _job_not_found(io: IOAdapter, job_id: str) -> int:
    io.log("error", f"Job not found: {job_id}. Run 'stepwise jobs' to list jobs")
    return EXIT_JOB_FAILED
```
- File: `src/stepwise/cli.py`

**Step 2.5: Fix `flow_resolution.py` warning**
Replace `print(f"Warning: ...", file=sys.stderr)` with `logging.getLogger(__name__).warning(...)`.
- File: `src/stepwise/flow_resolution.py`

---

### Phase 3: Table/Output Consistency (P1)

**Step 3.1: Migrate hand-rolled tables to `io.table()`**

| Command | Current | Migration |
|---------|---------|-----------|
| `cmd_extensions` (4718-4732) | Manual column-width calc + `print()` | `io.table(["NAME", "VERSION", "DESCRIPTION", "PATH"], rows)` |
| `cmd_cache` stats | Manual formatting | `io.table()` or `io.note()` |
| `_list_doc_topics` | Manual alignment | `io.table(["TOPIC", "DESCRIPTION"], rows)` |

- File: `src/stepwise/cli.py`

**Step 3.2: Add `--output json` where missing**

| Command | JSON Output Shape |
|---------|-------------------|
| `extensions` | `[{"name": ..., "version": ..., "description": ..., "path": ...}]` |
| `flows` | `[{"name": ..., "path": ..., "steps": N, "tags": [...]}]` |
| `templates` | `{"builtin": [...], "project": [...]}` |
| `cache stats` | Stats dict |
| `server status` | `{"running": bool, "pid": ..., "port": ..., "uptime": ...}` |
| `config get` | `{key: value}` or `{key: value, ...}` for all |

For each: add `--output` to parser, add JSON branch at handler top.
- File: `src/stepwise/cli.py`

**Step 3.3: Verify JSON-only to stdout**
When `--output json` is active, audit for stray `print()` calls that would pollute JSON stdout.
- File: `src/stepwise/cli.py`

---

### Phase 4: Help Text Quality (P1/P2)

**Step 4.1: Group commands in `stepwise --help`**
Create a custom `_GroupedHelpFormatter` that renders grouped output:

```
Execution:
  run             Run a flow
  validate        Validate a flow file
  preflight       Pre-run check: config + requirements + models
  diagram         Generate a flow diagram

Jobs:
  jobs            List jobs (most recent 20; use --all for full list)
  status          Show job status with step breakdown
  cancel          Cancel a running job
  tail            Stream live events
  logs            Show full event history
  output          Retrieve job outputs
  wait            Block until job(s) complete or suspend
  fulfill         Satisfy a suspended external step

Server:
  server          Manage the server (start/stop/restart/status/log)

Project:
  init            Create .stepwise/ in current directory
  new             Create a new flow
  flows           List flows in this project
  config          Get/set configuration values
  templates       List available templates

Registry:
  search          Search the flow registry
  get             Download a flow
  share           Publish a flow
  info            Show flow details
  login           Log in via GitHub
  logout          Log out

Advanced:
  job             Stage, order, and batch-run jobs
  cache           Inspect and clear step result cache
  schema          Generate JSON tool contract
  agent-help      Generate agent instructions
  extensions      List discovered extensions
  docs            Browse reference docs

System:
  update          Upgrade to the latest version
  welcome         Interactive welcome demo
  help            Ask a question about Stepwise
  version         Print version
```

Implementation: define groups as ordered dict mapping group name → command names. Override `format_help()`.
- File: `src/stepwise/cli.py`

**Step 4.2: Add epilog examples to complex commands**
Use `epilog=` with `RawDescriptionHelpFormatter` on: `run`, `fulfill`, `output`, `config`, `rm`.

Example for `run`:
```
Examples:
  stepwise run my-flow --input url=https://example.com
  stepwise run my-flow --watch              # server + browser UI
  stepwise run my-flow --wait               # block, JSON output
  stepwise run my-flow --async --name "impl: feature-x"
```
- File: `src/stepwise/cli.py`

**Step 4.3: Improve terse help strings**
| Current | Improved |
|---------|----------|
| `"List jobs"` | `"List jobs (most recent 20; --all for full list)"` |
| `"Show job detail"` | `"Show job status with step breakdown"` |
| `"Manage configuration"` | `"Get/set configuration values"` |
| `"List suspended steps or other items"` | `"List suspended steps awaiting input"` |
| `"Permanently delete jobs"` | `"Permanently delete jobs (irreversible)"` |
| `"Job staging and orchestration"` | `"Stage, order, and batch-run jobs"` |
| `"Manage step result cache"` | `"Inspect and clear step result cache"` |
| `"Print reference documentation"` | `"Browse reference docs (patterns, CLI, executors)"` |

- File: `src/stepwise/cli.py`

**Step 4.4: Update module docstring**
Ensure lines 1-38 match actual commands. Add missing: `preflight`, `test-fixture`, `cache`, `job` subcommands.
- File: `src/stepwise/cli.py`

---

### Phase 5: Naming & Discoverability (P2)

**Step 5.1: Consolidate `check`/`validate`/`preflight`**
- Keep `validate` as primary. Add `aliases=["check"]`.
- Add `--models` (what `check` does beyond `validate`) and `--preflight` flags.
- Keep `cmd_check`/`cmd_preflight` as thin wrappers with deprecation note.
- File: `src/stepwise/cli.py`

**Step 5.2: Add common aliases**
- `ls` → `jobs` (via `aliases=["ls"]`)
- `log` → `logs` (via `aliases=["log"]`)
- `extension` already exists as alias for `extensions`
- File: `src/stepwise/cli.py`

**Step 5.3: Improve bare subcommand handling**
- `cmd_cache` bare: show subparser help instead of raw `print("Usage: ...")`
- `cmd_job` bare: same pattern
- `cmd_list` bare: default to `--suspended` behavior
- File: `src/stepwise/cli.py`

---

### Phase 6: Graceful Degradation (P1)

**Step 6.1: Improve server-required error messages**
Commands: `tail`, `logs` (server path), `cancel` (server path).
Pattern: `io.log("error", "Server not running. Start with: stepwise server start")`
- File: `src/stepwise/cli.py`

**Step 6.2: Improve project-required error messages**
`_find_project_or_exit()`: change to `io.log("error", "Not a stepwise project. Run 'stepwise init' to create one")`
- File: `src/stepwise/cli.py`

**Step 6.3: Improve dependency error messages**
- `diagram` (graphviz): include platform-specific install commands
- `help` (API key): include `stepwise config set` command
- File: `src/stepwise/cli.py`

**Step 6.4: Add top-level exception handler**
Add try/except in `cli_main()` for unexpected exceptions. Clean message + suggest `--verbose`. In verbose mode, print full traceback.
- File: `src/stepwise/cli.py`

**Step 6.5: Verify `--quiet` suppresses all non-essential output**
After Steps 2.2-2.3 migrate raw `print()` calls, verify QuietAdapter covers everything.
- File: `src/stepwise/io.py`

---

## Testing Strategy

### Automated tests (run after every step)

```bash
# Full suite — must pass after each phase
uv run pytest tests/ -x -q

# CLI-specific tests
uv run pytest tests/test_cli.py tests/test_cli_jobs.py tests/test_cli_observability.py tests/test_cli_tools.py -x -q

# Targeted after specific command changes
uv run pytest tests/ -x -q -k "fulfill"
uv run pytest tests/ -x -q -k "config"
uv run pytest tests/ -x -q -k "rm or delete"
uv run pytest tests/ -x -q -k "extension"
```

### New tests to add

**`tests/test_cli_errors.py`:**
```python
class TestErrorConsistency:
    def test_no_raw_stderr_prints_in_handlers(self):
        """Grep cli.py cmd_* function bodies for print(stderr) — should be zero."""

    def test_missing_project_suggests_init(self, tmp_path, capsys):
        """stepwise jobs in dir without .stepwise/ suggests init."""

    def test_job_not_found_suggests_jobs(self, capsys):
        """stepwise status badid includes 'stepwise jobs' suggestion."""

    def test_missing_api_key_suggests_config(self, capsys):
        """stepwise help without key suggests stepwise config set."""

    def test_piped_output_no_ansi(self, capsys):
        """PlainAdapter output contains no escape codes."""

class TestExitCodes:
    def test_usage_error_returns_2(self): ...
    def test_config_error_returns_3(self): ...
    def test_project_error_returns_4(self): ...
```

**`tests/test_cli_help.py`:**
```python
class TestHelpText:
    def test_all_commands_have_help(self):
        """Every registered command has non-empty help string."""

    def test_help_contains_groups(self):
        """Top-level --help contains all expected groups."""

    def test_no_deprecated_flags_in_help(self):
        """--var and other deprecated flags not visible in help."""
```

### Manual verification (post-implementation, safe — no server)

```bash
stepwise --help                     # Grouped command listing
stepwise run --help                 # Examples in epilog
stepwise config --help              # Examples in epilog
stepwise jobs --output json         # Clean JSON
stepwise status nonexistent-id      # Helpful error + suggestion
stepwise validate nonexistent.yaml  # Helpful error + suggestion
stepwise config set badkey val      # Helpful error + suggestion
stepwise extensions                 # io.table() formatting
stepwise | cat                      # No ANSI codes when piped
```

---

## Execution Order

| Phase | Steps | Priority | Key Changes |
|-------|-------|----------|-------------|
| 1. Discovery | 1.1-1.2 | First — informs all other work | New findings file only |
| 2. Error consistency | 2.1-2.5 | P0/P1 — most impactful | ~65 error sites in cli.py, 1 in flow_resolution.py |
| 3. Table/output | 3.1-3.3 | P1 | ~4 commands for tables, ~6 for --output json |
| 4. Help text | 4.1-4.4 | P1/P2 | Parser section, custom formatter |
| 5. Naming | 5.1-5.3 | P2 | Parser + handler consolidation |
| 6. Graceful degradation | 6.1-6.5 | P1 | ~10 error sites + top-level handler |

Phases 2 and 4 can run in parallel. Phase 3 depends on Phase 2's IOAdapter migration. Phase 5 depends on Phase 4's help grouping.

**Commit strategy:** One commit per step (or per phase if diff is small). Each commit must pass `uv run pytest tests/ -x -q`. Total: ~10-14 commits.

## Files Modified

| File | Changes |
|------|---------|
| `src/stepwise/cli.py` | Bulk: error paths, parser grouping, formatter, aliases, epilogs, --output json |
| `src/stepwise/io.py` | Minor: possible io.table() enhancements |
| `src/stepwise/flow_resolution.py` | One fix: print(stderr) → logging.warning() |
| `tests/test_cli_errors.py` | New — error consistency assertions |
| `tests/test_cli_help.py` | New — help text quality assertions |
| `tests/test_cli_jobs.py` | Extended — JSON output format tests |
| `reports/cli-audit-findings.md` | New — Phase 1 deliverable |

## Out of Scope

- Tab completion (shell-specific scripts — separate task)
- Man page generation
- CLI performance optimization
- New commands or features
- Changes to the web UI
- Changes to `runner.py` output formatting (has its own `_err()`/`_json_error()` — already well-structured)
