---
title: "Implementation Plan: E4 CLI Extension Protocol CLI Commands"
date: "2026-03-22T00:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# E4: CLI Extension Protocol CLI Commands

## Overview

Add `stepwise extensions list` and `stepwise extensions info <name>` commands that discover CLI extensions via PATH scanning (`stepwise-ext-*` binaries) and project-local `.stepwise/extensions/`, with a JSON manifest cache for fast subsequent lookups.

## Requirements

### R1: `stepwise extensions list`
- Scan `$PATH` for executables matching `stepwise-ext-*` pattern
- Scan project `.stepwise/extensions/` for local extension scripts
- For each discovered extension, invoke `<binary> --manifest` to get metadata
- Display a table: name, version, description, source (PATH / project), capabilities
- **Acceptance criteria:** Running `stepwise extensions list` with a `stepwise-ext-test` binary on PATH shows it in the output with correct manifest fields. Running with no extensions shows "No extensions found."

### R2: `stepwise extensions info <name>`
- Show full manifest details for a named extension
- Look up by name (e.g., `stepwise extensions info telegram` finds `stepwise-ext-telegram`)
- Show: name, version, description, capabilities, config_keys, binary path
- **Acceptance criteria:** `stepwise extensions info telegram` with `stepwise-ext-telegram` on PATH prints all manifest fields. Non-existent name prints error and returns non-zero exit code.

### R3: Extension manifest caching
- Cache discovered manifest data so `list` is fast on repeat calls
- Cache location: `~/.cache/stepwise/extensions.json`
- Cache invalidation: keyed by `(binary_path, mtime)` — if binary changes, re-invoke `--manifest`
- TTL: 1 hour (consistent with script executor cache default)
- `--refresh` flag on `list` to force cache bypass
- **Acceptance criteria:** Second `stepwise extensions list` call doesn't invoke `--manifest` on unchanged binaries. Modified binary triggers fresh `--manifest` call. `--refresh` always re-scans.

## Assumptions

1. **Extensions use `stepwise-ext-*` prefix** (not `stepwise-*` as in docs/extensions.md line 139). The docs say `stepwise-<name>` but that conflicts with the core `stepwise` command itself. Using `stepwise-ext-*` avoids ambiguity.

   >>>ESCALATE: The docs at docs/extensions.md line 139 say extensions are named `stepwise-<name>` (e.g., `stepwise-telegram`), but this would clash with any future core subcommand named `telegram`. Should extensions use `stepwise-ext-<name>` prefix instead, or keep `stepwise-<name>` as documented?

2. **`--manifest` output is JSON** matching the schema in docs/extensions.md lines 146–153: `{name, version, description, capabilities, config_keys}`. Verified at docs/extensions.md:145–153.

3. **CLI subcommand pattern follows `cache`** — uses `add_subparsers(dest="ext_action")` for `list`/`info` sub-actions, matching the pattern at cli.py:3953–3963.

4. **No project required for `list`** — PATH-based extensions are global. Project-local extensions are additive when a `.stepwise/` project exists. This matches how `stepwise --version` and `stepwise update` work without a project.

5. **`shutil.which` is already used** in the codebase for binary discovery (registry_factory.py:70, cli_llm_client.py:33, editor_llm.py:242). PATH scanning will use the same `os.environ["PATH"]` that `shutil.which` uses.

6. **Cache uses `~/.cache/stepwise/`** — already established by version check cache at cli.py:85.

## Out of Scope

- **Extension installation/uninstall commands** — this is discovery only, not an installer
- **Extension execution/dispatch** — running `stepwise telegram` by delegating to `stepwise-ext-telegram` is a separate feature
- **Server API endpoints for extensions** — no REST/WebSocket API additions
- **Web UI for extensions** — no frontend changes
- **Webhook or shell hook configuration** — already handled by existing systems (docs/extensions.md tiers 1–2)
- **Extension authoring scaffolding** — no `stepwise extensions new` command

## Architecture

### New module: `src/stepwise/extensions.py`

Discovery and caching logic lives in a dedicated module, keeping `cli.py` thin (consistent with how `cache.py` backs `cmd_cache`, `hooks.py` backs hook functionality, etc.).

**Functions:**

```
discover_extensions(project_dir, refresh) -> list[ExtensionInfo]
get_extension_info(name, project_dir) -> ExtensionInfo | None
```

**Data model:**

```python
@dataclass
class ExtensionInfo:
    name: str
    version: str
    description: str
    capabilities: list[str]
    config_keys: list[str]
    binary_path: str
    source: str  # "path" or "project"
```

With `to_dict()`/`from_dict()` pair per project convention (models.py pattern).

### CLI registration in `cli.py`

Follows the `cache` subparser pattern (cli.py:3953–3963):

```python
p_ext = sub.add_parser("extensions", help="Manage extensions")
ext_sub = p_ext.add_subparsers(dest="ext_action")
ext_sub.add_parser("list", ...)
ext_info = ext_sub.add_parser("info", ...)
ext_info.add_argument("name", ...)
```

Handler `cmd_extensions(args)` dispatches on `args.ext_action`, mirroring `cmd_cache` at cli.py:3991.

### Cache file format (`~/.cache/stepwise/extensions.json`)

```json
{
  "version": 1,
  "entries": {
    "/usr/local/bin/stepwise-ext-telegram": {
      "mtime": 1711100000.0,
      "manifest": {"name": "telegram", "version": "0.1.0", ...},
      "cached_at": 1711100500.0
    }
  }
}
```

Keyed by absolute binary path. Entry is valid when `mtime` matches current file mtime AND `cached_at` is within TTL (3600s).

## Implementation Steps

### Step 1: Create `src/stepwise/extensions.py` (~45 min)

New module with:

1. `ExtensionInfo` dataclass with `to_dict()`/`from_dict()` pair
2. `_scan_path_extensions() -> list[Path]` — iterate `os.environ["PATH"].split(os.pathsep)`, use `os.scandir()` on each dir, filter files starting with `stepwise-ext-` that are executable (`os.access(p, os.X_OK)`). Skip non-existent dirs. Deduplicate by `Path.resolve()`.
3. `_scan_project_extensions(project_dir: Path) -> list[Path]` — list `.stepwise/extensions/` executables matching `stepwise-ext-*`. Guard with `is_dir()` check.
4. `_invoke_manifest(binary_path: Path) -> dict | None` — `subprocess.run([str(binary_path), "--manifest"], capture_output=True, timeout=5)`. Parse JSON stdout. Return None on failure (non-zero exit, invalid JSON, timeout). Use `logging.warning()` for errors (no print — per guardrail #2).
5. `_cache_path() -> Path` — returns `~/.cache/stepwise/extensions.json`
6. `_load_cache() -> dict` — read cache file, return empty dict on missing/corrupt (try/except pattern matching cli.py:88–95)
7. `_save_cache(data: dict)` — write cache file, `mkdir(parents=True, exist_ok=True)` for parent dir
8. `_is_entry_valid(entry: dict, current_mtime: float) -> bool` — check mtime match + `cached_at` within TTL (3600s)
9. `discover_extensions(project_dir: Path | None = None, refresh: bool = False) -> list[ExtensionInfo]` — orchestrates: scan PATH + project → load cache → for each binary, use cached manifest or invoke `--manifest` → save cache → return list
10. `get_extension_info(name: str, project_dir: Path | None = None) -> ExtensionInfo | None` — calls `discover_extensions()` and filters by name

### Step 2: Add CLI commands in `cli.py` (~20 min)

1. Add subparser group after `cache` block (after line 3964):
   - `p_ext = sub.add_parser("extensions", help="Discover installed extensions")`
   - `ext_sub = p_ext.add_subparsers(dest="ext_action")`
   - `list` sub-action with `--refresh` and `--json` flags
   - `info` sub-action with positional `name` argument

2. Add `cmd_extensions(args)` handler:
   - `list` action: call `discover_extensions()`, format as aligned table (NAME, VERSION, DESCRIPTION, SOURCE) or JSON array. Print "No extensions found." if empty.
   - `info` action: call `get_extension_info()`, print all fields, or print error and return `EXIT_USAGE_ERROR` if not found.
   - No action: print "Usage: stepwise extensions {list|info}" to stderr, return `EXIT_USAGE_ERROR`.

3. Register `"extensions": cmd_extensions` in handlers dict (around line 4208, after `"cache"` entry).

4. Update module docstring (lines 1–33) to include `stepwise extensions list|info`.

### Step 3: Write tests in `tests/test_extensions.py` (~45 min)

Tests using `tmp_path`, `monkeypatch`, and real subprocess calls against tiny shell scripts:

**Discovery tests:**
- `test_scan_path_finds_executables` — temp dir with `stepwise-ext-foo` executable on PATH
- `test_scan_path_skips_non_executable` — non-executable file ignored
- `test_scan_path_deduplicates` — same binary in two PATH dirs → listed once
- `test_scan_project_extensions` — `.stepwise/extensions/stepwise-ext-bar` found
- `test_scan_project_no_dir` — no `.stepwise/extensions/` → empty list (no error)

**Manifest tests:**
- `test_manifest_valid_json` — script outputs valid manifest → correct ExtensionInfo
- `test_manifest_invalid_json` — script outputs garbage → None returned
- `test_manifest_timeout` — script that sleeps → graceful skip
- `test_manifest_nonzero_exit` — script exits 1 → None returned

**Cache tests:**
- `test_cache_roundtrip` — save then load → data matches
- `test_cache_hit_on_unchanged` — second discover call doesn't invoke subprocess
- `test_cache_miss_on_mtime_change` — touch binary → re-invokes manifest
- `test_cache_miss_on_ttl_expiry` — old `cached_at` → re-invokes
- `test_cache_refresh_flag` — `refresh=True` → always re-invokes

**CLI integration tests:**
- `test_cli_extensions_list_empty` — `main(["extensions", "list"])` → EXIT_SUCCESS, "No extensions found."
- `test_cli_extensions_list_with_extension` — real extension script → table output
- `test_cli_extensions_list_json` — `--json` → valid JSON array on stdout
- `test_cli_extensions_info_found` — prints manifest fields
- `test_cli_extensions_info_not_found` — `main(["extensions", "info", "x"])` → EXIT_USAGE_ERROR
- `test_cli_extensions_no_action` — `main(["extensions"])` → usage message

## Testing Strategy

### Run extension tests

```bash
uv run pytest tests/test_extensions.py -v
```

### Full regression

```bash
uv run pytest tests/ -x
```

### Manual smoke test

```bash
# Create a test extension
echo '#!/bin/sh
if [ "$1" = "--manifest" ]; then
  echo "{\"name\":\"test\",\"version\":\"0.1.0\",\"description\":\"Test extension\",\"capabilities\":[],\"config_keys\":[]}"
fi' > /tmp/stepwise-ext-test && chmod +x /tmp/stepwise-ext-test

PATH=/tmp:$PATH stepwise extensions list
PATH=/tmp:$PATH stepwise extensions info test
PATH=/tmp:$PATH stepwise extensions list --json
```

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| PATH scanning slow on systems with many dirs | `list` feels sluggish | Cache makes subsequent calls instant. First scan bounded by PATH entries (typically <20 dirs). `os.scandir()` with prefix filter is fast. Skip non-existent dirs. |
| `--manifest` subprocess hangs | CLI blocks | 5-second timeout per binary. Timed-out binaries logged as warnings, skipped gracefully. |
| Cache file corruption | Crash on load | `_load_cache()` wraps in try/except, returns empty dict on any error (same pattern as version check at cli.py:88–95). |
| No `.stepwise/extensions/` dir exists | Error on project scan | Guard with `is_dir()` check, return empty list. Dir is opt-in; not created by `init_project()`. |
| Duplicate extensions across PATH and project | Confusing output | Deduplicate by resolved path. PATH takes precedence over project. `list` shows SOURCE column for disambiguation. |
| Malicious binary on PATH matching prefix | Security concern | Read-only: only runs `--manifest`. Same trust model as git's `git-*` pattern. Users audit their PATH. |
