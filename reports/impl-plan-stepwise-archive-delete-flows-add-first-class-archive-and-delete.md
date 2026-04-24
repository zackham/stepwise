---
title: "Implementation Plan: Archive & Delete Flows"
date: "2026-04-24T00:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Archive & Delete Flows

## Overview

Add `archived: true` YAML flag for flows with filtering across all listing surfaces (CLI, API, web UI, agent help, catalog), plus a `stepwise delete` command with typed-confirmation safety belt. Archive is visibility-only — archived flows remain on disk and fully runnable.

## Escalation

>>>ESCALATE: The spec calls for top-level `stepwise archive <flow>` and `stepwise unarchive <flow>` commands, but those names are already taken by job archive/unarchive commands. Evidence:

- `cmd_archive` at `src/stepwise/cli.py:3342` — "Archive completed/failed/cancelled jobs." Takes `job_ids` (nargs `"*"`), `--status`, `--group`.
- `cmd_unarchive` at `src/stepwise/cli.py:3419` — "Restore archived jobs." Takes `job_ids` (nargs `"+"`).
- Parser registration: `p_archive` at `cli.py:5487`, `p_unarchive` at `cli.py:5494`.
- Handler dict entries: `"archive": cmd_archive` at `cli.py:7373`, `"unarchive": cmd_unarchive` at `cli.py:7374`.
- Documented in `docs/cli.md:492-545` under "Job Lifecycle Commands".

Three options:

1. **Namespace under `flow`**: `stepwise flow archive <name>`, `stepwise flow unarchive <name>`, `stepwise flow delete <name>`. Mirrors the existing `stepwise job {create|show|run|approve|dep|cancel|rm}` pattern (`cli.py:5620-5678`, dispatcher at `cli.py:6543-6560`). The spec rejected `stepwise flows …` (plural) but didn't mention a singular `flow` subcommand.
2. **Make existing commands dual-purpose**: detect whether argument is a UUID (job) or a flow name. Fragile — flow names and truncated job IDs could collide. Job IDs are `_gen_id()` UUIDs (`models.py`) but the archive parser accepts bare positional args with no type enforcement.
3. **Rename to `archive-flow` / `unarchive-flow`**: top-level but hyphenated. No precedent in codebase (all existing commands are single words or nested subcommands).

**Recommendation**: Option 1. It reuses a proven pattern, avoids ambiguity, and `stepwise delete` (available top-level name) can be a convenience alias.

**Proceeding with Option 1.** Only Step 3 (CLI parser + dispatcher) is affected by the naming choice — all helpers, API, web, and tests are name-agnostic. If the human picks differently, the plan adjusts in one step.

---

## Requirements

### R1: Archive/unarchive via CLI
- `stepwise flow archive <flow>` sets `archived: true` at the YAML top level.
- `stepwise flow unarchive <flow>` removes the `archived` key entirely.
- Both idempotent: archiving an already-archived flow prints "already archived" and exits 0; unarchiving a non-archived flow prints "not archived" and exits 0.
- YAML round-trip preserves comments, key ordering, and formatting.
- Flow resolved via `resolve_flow()` (`flow_resolution.py:57`) — same resolution path as `stepwise run`.
- **AC-1a**: After archive, the flow YAML contains `archived: true` at the top level. All other bytes are identical.
- **AC-1b**: After unarchive, the `archived` key is absent. File is byte-identical to its pre-archive state.
- **AC-1c**: Both commands return `EXIT_SUCCESS` (0) on success, including idempotent no-ops.
- **AC-1d**: Both commands accept the same flow identifiers `stepwise run` does (name, path, kit/flow).

### R2: Delete via CLI
- `stepwise flow delete <flow>` resolves the flow, prints the resolved absolute path, then prompts: "Type the flow name to confirm deletion:".
- Typed input must exactly match the flow name (the `name:` field in YAML, or the directory/file stem if no name). Mismatch → print error → exit 1 → file untouched.
- `--yes` / `-y` bypasses the prompt entirely.
- Single-file flow (`foo.flow.yaml`): `Path.unlink()`. Directory flow (`flows/foo/`): `shutil.rmtree()`.
- No job database changes. No running-job checks.
- **AC-2a**: After delete with correct confirmation, the file/directory is gone. `Path.exists()` returns `False`.
- **AC-2b**: After delete with wrong confirmation, file/directory still exists and exit code is 1.
- **AC-2c**: `--yes` flag removes file/directory without prompting.
- **AC-2d**: Job records in SQLite referencing the deleted flow name are untouched.

### R3: `stepwise flows` hides archived by default
- Default output omits flows where the YAML contains `archived: true`.
- `--include-archived` / `-a` flag: include archived flows alongside non-archived, with `[archived]` appended to the name column.
- `--archived-only` flag: show only archived flows.
- The two flags are mutually exclusive (argparse mutually exclusive group).
- **AC-3a**: A flow with `archived: true` does not appear in `stepwise flows` output (no flags).
- **AC-3b**: With `--include-archived`, archived flows appear with `[archived]` marker; non-archived flows appear without it.
- **AC-3c**: With `--archived-only`, only archived flows appear (with `[archived]` marker).
- **AC-3d**: `--include-archived` and `--archived-only` together produce an argparse error.

### R4: API endpoint filters archived
- `GET /api/local-flows` response includes an `archived: boolean` field on every flow dict.
- Default: response omits flows where `archived == true`.
- `?include_archived=true`: response includes all flows (archived + non-archived).
- `?archived_only=true`: response includes only archived flows.
- New endpoints: `POST /api/flows/local/{path}/archive` and `.../unarchive`.
- **AC-4a**: Default GET response contains no flow with `archived: true`.
- **AC-4b**: `?include_archived=true` returns all flows, each with an `archived` boolean field.
- **AC-4c**: POST archive endpoint sets `archived: true` in the YAML and returns `{"status": "archived", "name": "..."}`.
- **AC-4d**: POST unarchive endpoint removes the `archived` key and returns `{"status": "unarchived", "name": "..."}`.

### R5: Web UI archived filter toggle
- FlowsPage toolbar includes a toggle control (after the existing visibility ComboBox at `FlowsPage.tsx:930`).
- Default: off (archived flows hidden via client-side filter in the `filtered` useMemo at `FlowsPage.tsx:595`).
- When on: archived flows visible with dimmed styling and `[archived]` badge.
- Context menu gains Archive/Unarchive actions (following `flow-actions.ts` pattern).
- **AC-5a**: With toggle off, no archived flow appears in the list.
- **AC-5b**: With toggle on, archived flows appear with visual distinction (opacity + badge).
- **AC-5c**: Right-click on non-archived flow shows "Archive Flow" action; on archived flow shows "Unarchive Flow".

### R6: Agent help & catalog exclude archived
- `_build_flow_entries()` in `agent_help.py:21` skips flows where `is_archived(flow_path)` is `True`.
- `cmd_catalog()` in `cli.py:1422` skips archived flows in the standalone filter loop (`cli.py:1448`).
- `resolve_flow()` (`flow_resolution.py:57`) continues to resolve archived flows — no change to the resolver.
- **AC-6a**: `stepwise agent-help` output does not contain the name of an archived flow.
- **AC-6b**: `stepwise catalog` output does not list an archived flow in the standalone section.
- **AC-6c**: `stepwise run <archived-flow-name>` succeeds (flow resolves and executes normally).

### R7: Shared `is_archived()` helper
- Single `is_archived(flow_path: Path) -> bool` function in `flow_resolution.py`.
- All listing surfaces import and call this function. No inline `yaml.safe_load(...).get("archived")` elsewhere.
- **AC-7a**: `grep -rn 'get("archived"' src/stepwise/` returns only `flow_resolution.py`.
- **AC-7b**: Every listing surface verified in R3–R6 imports `is_archived` from `flow_resolution`.

---

## Assumptions

| # | Assumption | Verified at | Evidence |
|---|-----------|-------------|----------|
| A1 | `ruamel.yaml` is a project dependency | `pyproject.toml` line ~25 | `"ruamel-yaml>=0.18"` in `[project.dependencies]` |
| A2 | ruamel.yaml round-trip pattern is established | `server.py:4750-4768` | `YAML()` → `preserve_quotes = True` → `load(raw)` → mutate → `dump(data, buf)` → atomic `.tmp` rename |
| A3 | `resolve_flow()` returns `Path` to the YAML file itself | `flow_resolution.py:57` | Signature: `resolve_flow(name_or_path: str, project_dir: Path \| None = None) -> Path`. For directory flows, returns `dir/FLOW.yaml`; for single-file, returns `name.flow.yaml` |
| A4 | `discover_flows()` returns `list[FlowInfo]` with `.path` pointing to YAML | `flow_resolution.py:571-636` | `FlowInfo(name=..., path=marker, is_directory=True)` where `marker = member / FLOW_DIR_MARKER` (line 599-604) |
| A5 | `FlowInfo.is_directory` distinguishes file from directory flows | `flow_resolution.py:603,616,632` | Directory flows: `is_directory=True`. Single-file: `is_directory=False` (line 632) |
| A6 | The web delete endpoint already exists (won't duplicate) | `server.py:4458-4484` | `DELETE /api/flows/local/{path:path}` → `delete_local_flow()`. Uses `shutil.rmtree` for dirs, `unlink` for files |
| A7 | The CLI `flows` command reads raw YAML per flow | `cli.py:1549-1550` | `_flow_row()` uses `yaml.safe_load(flow_info.path.read_text())` — not `load_workflow_yaml` |
| A8 | `cmd_catalog()` filters by visibility via `load_workflow_yaml` | `cli.py:1448-1455` | `wf = load_workflow_yaml(str(f.path))` then checks `wf.metadata.visibility == "interactive"` |
| A9 | `_build_flow_entries()` in `agent_help.py` already filters by visibility | `agent_help.py:47` | `if visibility_filter and wf.metadata.visibility not in visibility_filter: continue` |
| A10 | `list_local_flows()` API has no query params currently | `server.py:3859-3860` | Signature: `def list_local_flows():` — bare, no `Query()` params |
| A11 | `useDeleteFlow()` mutation is the pattern for flow mutations | `useEditor.ts:58-66` | `useMutation({ mutationFn: (path) => api.deleteFlow(path), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["localFlows"] }) })` |
| A12 | `ActionContext.extraMutations` is the wiring point for flow actions | `actions/types.ts:33` | `extraMutations?: { deleteFlow?: ReturnType<typeof useDeleteFlow> }` in `ActionContext` interface. Provider at `ActionContextProvider.tsx:14` |
| A13 | `cmd_job` dispatcher is the proven pattern for subcommand groups | `cli.py:6543-6560` | `action = getattr(args, "job_command", None)` → `handlers = {"create": ..., "show": ...}` → `handlers.get(action)` |
| A14 | Test helpers exist for flow CLI testing | `test_flows_command.py:9-38` | `_make_project(tmp_path)`, `_make_flow_dir(project, name, yaml)`, `_make_root_flow(project, filename, yaml)`, `_run_flows(monkeypatch, tmp_path, capsys)` |
| A15 | Server API test pattern exists with TestClient | `test_editor_api.py:82-104` | `client(project_dir)` fixture: sets `STEPWISE_PROJECT_DIR` env → `TestClient(app)` → yields client → restores env |
| A16 | `yaml_loader.py` ignores unknown top-level keys | `yaml_loader.py:2017` | Uses `yaml.safe_load()` then extracts known keys. No strict schema validation that would reject `archived`. |

---

## Out of Scope

| Item | Reason |
|------|--------|
| Soft-delete / trash directory | Spec: "Delete is hard rm, period." |
| Running-job gates on delete | Spec: "let delete always proceed." No `--force`, no check. |
| Bulk archive / archive-by-tag / auto-archive | Spec: explicit out-of-scope. |
| Migration of existing flows | Spec: "absence of the field = not archived." Nothing to migrate. |
| UI redesign beyond archived filter toggle | Spec: "Keep the toggle minimal." |
| Changes to `resolve_flow()` | Already resolves by name regardless of archive state — no filtering in resolver. |
| Adding `archived` to `FlowMetadata`/`WorkflowDefinition` | The engine never needs it. It's a discovery/listing concern only. Adding it to the model would require `to_dict()`/`from_dict()` updates and would pollute engine code. |
| `archived` field in `FlowInfo` dataclass | Discovery-time enrichment would require YAML parsing during `discover_flows()`, which currently only scans the filesystem. `is_archived()` is called lazily by listing surfaces instead. |
| Light/dark theme styling for archived badge | Tailwind classes handle both themes automatically via `dark:` variants. No separate design work needed. |

---

## Architecture

### Where `archived` lives

`archived: true` is a top-level YAML key, same level as `name:` and `steps:`. Example:

```yaml
name: my-flow
archived: true
description: An old flow
steps: ...
```

It is **not** added to `FlowMetadata` (`models.py:717`) or `WorkflowDefinition` (`models.py:963`). The engine never reads it. `yaml_loader.py` will silently ignore it (it only extracts known keys at line 2017). It's a discovery/listing concern only.

### Shared helpers in `flow_resolution.py`

**Why `flow_resolution.py`**: This module owns all flow discovery (`discover_flows` at line 571, `FlowInfo` dataclass, `resolve_flow` at line 57). Archive checking is a discovery concern — it belongs here, not in `yaml_loader.py` (engine parser) or `models.py` (data structures).

Two new functions:

1. **`is_archived(flow_path: Path) -> bool`** — lightweight `yaml.safe_load()` check. `pyyaml` is already imported in this module (used by `_read_kit_yaml` for kit parsing). Catches all exceptions → returns `False` (graceful on broken YAML, matching the pattern in `cmd_flows._flow_row()` at `cli.py:1549-1552`).

2. **`set_flow_archived(flow_path: Path, archived: bool) -> bool`** — ruamel.yaml round-trip edit. Returns `True` if the file was modified, `False` if already in desired state (caller uses this for idempotent messaging). Uses the exact pattern from `server.py:4750-4768`:
   - `ryaml = YAML()` + `ryaml.preserve_quotes = True` (line 4750-4751)
   - `data = ryaml.load(raw)` (line 4753, round-trip mode preserves comments/ordering)
   - Insert or delete `archived` key
   - `ryaml.dump(data, buf)` → `buf.getvalue()` (lines 4761-4763)
   - Atomic write via `.tmp` → `rename()` (lines 4766-4768)

### CLI wiring: `stepwise flow` subcommand

New `flow` subcommand group, mirroring the `job` subcommand pattern:

**Parser registration** (in `build_parser()`, after `p_job` block at `cli.py:5620`):
- `p_flow_cmd = sub.add_parser("flow", help="Archive, unarchive, or delete flows")`
- `flow_sub = p_flow_cmd.add_subparsers(dest="flow_command")`
- Three sub-parsers following `job create` pattern at `cli.py:5625`

**Dispatcher** (new `cmd_flow` function, following `cmd_job` at `cli.py:6543-6560`):
- `action = getattr(args, "flow_command", None)`
- Routes to `_cmd_flow_archive`, `_cmd_flow_unarchive`, `_cmd_flow_delete`
- No subcommand → print usage → `EXIT_USAGE_ERROR`

**Handler registration**: Add `"flow": cmd_flow` in handlers dict at `cli.py:7345` (adjacent to `"job": cmd_job` at line 7386).

**Flow resolution in handlers**: Each handler calls `resolve_flow(args.flow, _project_dir(args))` — same pattern as `cmd_run` at `cli.py:2531-2532`. `FlowResolutionError` caught → `io.log("error", str(e))` → `EXIT_USAGE_ERROR`.

**Delete confirmation**: Uses `input()` for typed-name prompt. Flow name derived from YAML `name:` field (via `yaml.safe_load`) or falls back to directory/file stem (matching `cmd_flows._flow_row()` logic at `cli.py:1553`). `monkeypatch.setattr("builtins.input", ...)` in tests.

### Listing surface updates (6 call sites)

| # | Surface | File:Line | Current filter | Change |
|---|---------|-----------|----------------|--------|
| 1 | `stepwise flows` | `cli.py:1533` (`cmd_flows`) | Visibility via `_vis_ok()` at line 1561 | Add archive filter in `for f in flows:` loop at line 1571. Read `archived` in `_flow_row()` from `raw.get("archived", False)` at line 1550. |
| 2 | `stepwise catalog` | `cli.py:1448` (`cmd_catalog`) | Visibility via `wf.metadata.visibility` | Add `if is_archived(f.path): continue` in `for f in standalone:` loop at line 1448. |
| 3 | `stepwise agent-help` | `agent_help.py:31-47` (`_build_flow_entries`) | Visibility at line 47 | Add `if is_archived(flow_path): continue` before `load_workflow_yaml` at line 41. |
| 4 | `GET /api/local-flows` | `server.py:3859` (`list_local_flows`) | None | Add `is_archived()` check + query params + `archived` field in response dict. |
| 5 | `FlowsPage.tsx` | `FlowsPage.tsx:595` (filtered useMemo) | Visibility at line 613 | Add `if (!showArchived) result = result.filter(f => !f.archived)` before visibility filter. |
| 6 | Flow actions | `flow-actions.ts:13` (FLOW_ACTIONS) | None | Add `flow.archive` and `flow.unarchive` action definitions. |

### API endpoints

**Existing**: `DELETE /api/flows/local/{path:path}` at `server.py:4458` — already handles hard delete for the web UI.

**New endpoints**:
- `POST /api/flows/local/{path:path}/archive` — validates path within `_project_dir` (same guard as `patch_flow_metadata` at `server.py:4740-4744`), calls `set_flow_archived(abs_path, True)`.
- `POST /api/flows/local/{path:path}/unarchive` — same, calls `set_flow_archived(abs_path, False)`.

**Modified**: `GET /api/local-flows` — add `include_archived: bool = Query(False)` and `archived_only: bool = Query(False)` params. Filter result list before returning.

### Web frontend wiring

**Types** (`types.ts:406`): Add `archived?: boolean` to `LocalFlow`.

**API** (`api.ts:415`): Modify `fetchLocalFlows` to accept optional `includeArchived`. Add `archiveFlow(path)` and `unarchiveFlow(path)` (POST, following `deleteFlow` at line 458).

**Hooks** (`useEditor.ts:58`): Add `useArchiveFlow()` and `useUnarchiveFlow()` following exact `useDeleteFlow` pattern (lines 58-66).

**Action types** (`actions/types.ts:33`): Extend `extraMutations` to include `archiveFlow` and `unarchiveFlow`. Update `ActionContextProvider.tsx:14` props interface.

**Actions** (`flow-actions.ts`): Add `flow.archive` (isAvailable: `!f.archived`) and `flow.unarchive` (isAvailable: `!!f.archived`) in "organize" group, between `flow.duplicate` (line 82) and `flow.export-yaml` (line 91).

---

## Implementation Steps

Steps are ordered by dependency. Each step states its prerequisites.

### Step 1: `is_archived()` and `set_flow_archived()` helpers (~20min)

**Depends on**: nothing (foundation for all subsequent steps).

**File**: `src/stepwise/flow_resolution.py`

1. Add `is_archived(flow_path: Path) -> bool` after the `FlowInfo` dataclass definition (~line 25):
   - `yaml.safe_load(flow_path.read_text()) or {}` → `.get("archived", False)` → `bool()`
   - Wrap in `try/except Exception: return False`

2. Add `set_flow_archived(flow_path: Path, archived: bool) -> bool` (returns `True` if modified):
   - Import `from ruamel.yaml import YAML` and `from io import StringIO`
   - `ryaml = YAML(); ryaml.preserve_quotes = True`
   - `data = ryaml.load(flow_path.read_text())`
   - If `archived=True` and `data.get("archived")`: return `False`
   - If `archived=True`: `data["archived"] = True` → write → return `True`
   - If `archived=False` and `"archived" not in data`: return `False`
   - If `archived=False`: `del data["archived"]` → write → return `True`
   - Atomic write: `tmp = flow_path.with_suffix(flow_path.suffix + ".tmp"); tmp.write_text(buf.getvalue()); tmp.rename(flow_path)`

### Step 2: Unit tests for helpers (~20min)

**Depends on**: Step 1.

**File**: `tests/test_flow_archive.py` (new)

Test the two helper functions directly (no CLI):

1. `test_is_archived_true` — write `"name: x\narchived: true\nsteps:\n  s:\n    run: echo '{}'\n"` → `is_archived(path)` returns `True`.
2. `test_is_archived_false_missing` — write YAML without `archived` → returns `False`.
3. `test_is_archived_false_explicit` — write `archived: false` → returns `False`.
4. `test_is_archived_broken_yaml` — write `: : : invalid :::` → returns `False`.
5. `test_set_archived_true` — set archived on clean flow → returns `True`, re-read YAML contains `archived: true`.
6. `test_set_archived_false` — set `archived: true`, then `set_flow_archived(path, False)` → returns `True`, key gone.
7. `test_set_archived_idempotent_true` — archive already-archived → returns `False`.
8. `test_set_archived_idempotent_false` — unarchive non-archived → returns `False`.
9. `test_round_trip_preserves_content` — write YAML with comments (`# important comment`) and specific ordering → archive → unarchive → compare bytes to original.

Pattern: `tmp_path` fixture, `Path.write_text()`, assert on `Path.read_text()`. No CLI dependency.

Run: `uv run pytest tests/test_flow_archive.py::TestHelpers -xvs`

### Step 3: CLI `flow` subcommand group (~30min)

**Depends on**: Step 1 (uses `resolve_flow`, `is_archived`, `set_flow_archived`).

**File**: `src/stepwise/cli.py`

1. **Parser** — in `build_parser()`, insert after `p_job` block (~line 5678):
   - `p_flow_cmd = sub.add_parser("flow", help="Archive, unarchive, or delete flows")`
   - `flow_sub = p_flow_cmd.add_subparsers(dest="flow_command")`
   - `p_flow_archive = flow_sub.add_parser("archive", help="Hide a flow from listings")`
   - `p_flow_archive.add_argument("flow", help="Flow name or path")`
   - Same for `unarchive` and `delete` (delete adds `--yes`/`-y` flag)

2. **Dispatcher** — new `cmd_flow(args)` after existing `cmd_job` at line 6560:
   - `action = getattr(args, "flow_command", None)`
   - `handlers = {"archive": _cmd_flow_archive, "unarchive": _cmd_flow_unarchive, "delete": _cmd_flow_delete}`
   - `handler = handlers.get(action)` → call or print usage

3. **`_cmd_flow_archive(args)`**:
   - `io = _io(args)`
   - `project_dir = _project_dir(args)` (helper at line 200)
   - `flow_path = resolve_flow(args.flow, project_dir)` (catch `FlowResolutionError`)
   - `changed = set_flow_archived(flow_path, True)`
   - If not changed: `io.log("info", "already archived")` → `EXIT_SUCCESS`
   - Else: `io.log("success", "Archived flow '...'")` → `EXIT_SUCCESS`

4. **`_cmd_flow_unarchive(args)`**: mirror with `set_flow_archived(flow_path, False)`.

5. **`_cmd_flow_delete(args)`**:
   - Resolve flow path via `resolve_flow(args.flow, project_dir)`
   - Read flow name: `yaml.safe_load(flow_path.read_text()).get("name", flow_path.parent.name if flow_path.name == "FLOW.yaml" else flow_path.stem.removesuffix(".flow"))`
   - Determine delete target: `flow_path.parent` if `flow_path.name == "FLOW.yaml"` else `flow_path`
   - `io.log("info", f"Will delete: {target}")` — show path before prompt
   - If not `args.yes`: `response = input(f"Type '{name}' to confirm: ")`
   - If mismatch: `io.log("error", "Confirmation mismatch")` → `EXIT_JOB_FAILED`
   - `shutil.rmtree(target)` or `target.unlink()` depending on `target.is_dir()`

6. **Register**: `"flow": cmd_flow` in handlers dict at line 7345.

### Step 4: CLI tests for archive/unarchive/delete (~30min)

**Depends on**: Steps 1-3.

**File**: `tests/test_flow_archive.py` (extend)

Import pattern from `test_flows_command.py`: `from stepwise.cli import main, EXIT_SUCCESS`. Use `_make_project`, `_make_flow_dir`, `_make_root_flow` helpers (copy or import).

Test cases (class `TestCLI`):

1. `test_cli_archive_sets_flag` — `_make_flow_dir(project, "my-flow", ...)` → `monkeypatch.chdir(project)` → `rc = main(["flow", "archive", "my-flow"])` → `assert rc == EXIT_SUCCESS` → read YAML → `"archived: true"` in content.
2. `test_cli_unarchive_removes_flag` — archive → `main(["flow", "unarchive", "my-flow"])` → YAML has no `archived` key.
3. `test_cli_archive_idempotent` — archive twice → `capsys.readouterr()` second call contains "already archived".
4. `test_cli_unarchive_idempotent` — unarchive non-archived → output contains "not archived".
5. `test_cli_delete_single_file_with_yes` — `_make_root_flow(project, "foo.flow.yaml", ...)` → `main(["flow", "delete", "foo", "--yes"])` → `assert rc == 0` → `assert not (project / "foo.flow.yaml").exists()`.
6. `test_cli_delete_directory_with_yes` — `_make_flow_dir(project, "bar", ...)` → `main(["flow", "delete", "bar", "--yes"])` → `assert not (project / "flows" / "bar").exists()`.
7. `test_cli_delete_confirmation_match` — `monkeypatch.setattr("builtins.input", lambda _: "my-flow")` → `main(["flow", "delete", "my-flow"])` → file gone.
8. `test_cli_delete_confirmation_mismatch` — `monkeypatch.setattr("builtins.input", lambda _: "wrong")` → exit non-zero → file still exists.
9. `test_cli_delete_nonexistent` — `main(["flow", "delete", "no-such"])` → exit code > 0.

Run: `uv run pytest tests/test_flow_archive.py::TestCLI -xvs`

### Step 5: Update `stepwise flows` listing (~20min)

**Depends on**: Step 1 (`is_archived`).

**File**: `src/stepwise/cli.py`

1. **Parser flags** — add to `p_flows` at line 5410, after the `--visibility` argument:
   - Create mutually exclusive group
   - `--include-archived` / `-a` (action `store_true`)
   - `--archived-only` (action `store_true`)

2. **`_flow_row()` change** (line 1548) — after `visibility = raw.get("visibility", "interactive")` at line 1555:
   - Add: `archived = bool(raw.get("archived", False))`
   - Add `"archived": archived` to return dict at line 1557

3. **Filter logic** — in `for f in flows:` loop at line 1571, after `row = _flow_row(f)`:
   - Read flags: `include_archived = getattr(args, "include_archived", False)`, `archived_only = getattr(args, "archived_only", False)`
   - If `archived_only and not row["archived"]`: `continue`
   - If `not include_archived and not archived_only and row["archived"]`: `continue`
   - If `row["archived"]` and (include_archived or archived_only): append ` [archived]` to `row["name"]`

### Step 6: Tests for `stepwise flows` filtering (~15min)

**Depends on**: Steps 1, 5.

**File**: `tests/test_flow_archive.py` (extend, class `TestFlowsListing`)

1. `test_flows_hides_archived_by_default` — create `active` and `hidden` flows (hidden has `archived: true` in YAML) → `main(["flows"])` → output contains "active", does NOT contain "hidden".
2. `test_flows_include_archived` — `main(["flows", "--include-archived"])` → output contains both; "hidden" has `[archived]`.
3. `test_flows_archived_only` — `main(["flows", "--archived-only"])` → only "hidden" in output.
4. `test_flows_flags_mutually_exclusive` — `main(["flows", "--include-archived", "--archived-only"])` → argparse error (exit 2).

Run: `uv run pytest tests/test_flow_archive.py::TestFlowsListing -xvs`

### Step 7: Update `agent_help.py` and `cmd_catalog` (~15min)

**Depends on**: Step 1 (`is_archived`).

**File**: `src/stepwise/agent_help.py`

1. Add import: `from stepwise.flow_resolution import is_archived`
2. In `_build_flow_entries()` at line 31, inside `for item in flows:`, after extracting `flow_path` (lines 34-37):
   - Add: `if is_archived(flow_path): continue`
   - Place before the `try: wf = load_workflow_yaml(...)` block at line 41

**File**: `src/stepwise/cli.py`

3. In `cmd_catalog()`, in the `for f in standalone:` loop at line 1448:
   - Add `from stepwise.flow_resolution import is_archived` (at function top)
   - Add `if is_archived(f.path): continue` before `try: wf = load_workflow_yaml(...)` at line 1450

### Step 8: Tests for agent help and catalog filtering (~15min)

**Depends on**: Steps 1, 7.

**File**: `tests/test_flow_archive.py` (extend, class `TestAgentHelp`)

1. `test_agent_help_excludes_archived` — create 2 interactive flows, archive one → `from stepwise.agent_help import generate_agent_help` → `output = generate_agent_help(project_dir)` → `assert "active-flow" in output` → `assert "archived-flow" not in output`.
2. `test_agent_help_includes_non_archived` — 1 non-archived flow → output contains it.
3. `test_resolve_flow_finds_archived` — create flow with `archived: true` → `resolve_flow("my-flow", project_dir)` returns the path → proves archived flows remain runnable.

Run: `uv run pytest tests/test_flow_archive.py::TestAgentHelp -xvs`

### Step 9: Update server API (~30min)

**Depends on**: Step 1 (`is_archived`, `set_flow_archived`).

**File**: `src/stepwise/server.py`

1. **Import**: `from stepwise.flow_resolution import is_archived, set_flow_archived`

2. **Modify `list_local_flows()`** at line 3859 — change signature:
   - `def list_local_flows(include_archived: bool = Query(False), archived_only: bool = Query(False)):`
   - Add `from fastapi import Query` to imports if not present
   - Inside `for flow_info in flows:` loop (line 3867): after building dict, add `"archived": is_archived(flow_info.path)`
   - After building complete `result` list: if `archived_only`: `result = [r for r in result if r.get("archived")]`; elif not `include_archived`: `result = [r for r in result if not r.get("archived")]`
   - For registry flows (second loop starting line 3921): add `"archived": False` (registry flows never archived)

3. **New `POST /api/flows/local/{path:path}/archive`** (place near existing flow endpoints, before the catch-all at line 4487):
   - Path validation: `abs_path = (_project_dir / path).resolve()` + `abs_path.relative_to(_project_dir)` guard (same as `patch_flow_metadata` lines 4740-4744)
   - If dir: `abs_path = abs_path / "FLOW.yaml"`
   - `changed = set_flow_archived(abs_path, True)`
   - Return `{"status": "archived" if changed else "already_archived"}`

4. **New `POST /api/flows/local/{path:path}/unarchive`**: mirror.

### Step 10: Server API tests (~20min)

**Depends on**: Steps 1, 9.

**File**: `tests/test_editor_api.py` (extend — this file already has `TestListLocalFlows` at line 110 with `client` and `project_dir` fixtures)

Add new class `TestFlowArchiveAPI`:

Setup: In `project_dir` fixture (line 57), or in individual tests, create a flow with `archived: true`:
```python
(tmp_path / "old.flow.yaml").write_text(
    "name: old\narchived: true\nsteps:\n  s:\n    run: echo '{}'\n"
)
```

Tests:
1. `test_list_excludes_archived_by_default` — `client.get("/api/local-flows")` → names don't include "old".
2. `test_list_includes_archived_with_param` — `client.get("/api/local-flows?include_archived=true")` → "old" in names, with `archived: True`.
3. `test_list_archived_only` — `client.get("/api/local-flows?archived_only=true")` → only "old".
4. `test_archive_endpoint` — `client.post("/api/flows/local/simple.flow.yaml/archive")` → 200 → re-read file → `archived: true` present.
5. `test_unarchive_endpoint` — archive then unarchive → file has no `archived` key.
6. `test_archive_nonexistent` — `client.post("/api/flows/local/nope/archive")` → 404.

Run: `uv run pytest tests/test_editor_api.py::TestFlowArchiveAPI -xvs`

### Step 11: Update web frontend types, API, hooks (~20min)

**Depends on**: Step 9 (API must be ready for type alignment).

**File**: `web/src/lib/types.ts` — add `archived?: boolean` to `LocalFlow` interface at line 418 (after `kit_name`).

**File**: `web/src/lib/api.ts`

1. Update `fetchLocalFlows` (line 415):
   ```typescript
   export function fetchLocalFlows(opts?: {
     includeArchived?: boolean;
   }): Promise<LocalFlow[]> {
     const params = opts?.includeArchived
       ? "?include_archived=true" : "";
     return request<LocalFlow[]>(`/local-flows${params}`);
   }
   ```

2. Add after `deleteFlow` at line 464:
   ```typescript
   export function archiveFlow(path: string) {
     return request(`/flows/local/${path}/archive`,
       { method: "POST" });
   }
   export function unarchiveFlow(path: string) {
     return request(`/flows/local/${path}/unarchive`,
       { method: "POST" });
   }
   ```

**File**: `web/src/hooks/useEditor.ts` — add after `useDeleteFlow` (line 66):
- `useArchiveFlow()` and `useUnarchiveFlow()`, each following the exact `useDeleteFlow` pattern: `useMutation({ mutationFn: (path: string) => api.archiveFlow(path), onSuccess: () => queryClient.invalidateQueries({ queryKey: ["localFlows"] }) })`.

### Step 12: Update FlowsPage UI (~30min)

**Depends on**: Step 11 (types + hooks must exist).

**File**: `web/src/pages/FlowsPage.tsx`

1. **State** — add near other state declarations (~line 471):
   `const [showArchived, setShowArchived] = useState(false);`

2. **Toggle UI** — in the toolbar after the visibility ComboBox (line 936), before the time range ComboBox (line 937):
   - Small toggle button matching the List/Grid toggle style at lines 849-874:
   ```tsx
   <button onClick={() => setShowArchived(v => !v)}
     className={cn("px-2.5 py-1 text-xs rounded-md",
       showArchived
         ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
         : "text-zinc-500 hover:text-foreground")}>
     Archived
   </button>
   ```

3. **API query** — update `useLocalFlows()` call to pass `{ includeArchived: showArchived }` so the API returns archived flows when the toggle is on.

4. **Client-side filter** — in `filtered` useMemo (line 595), before the visibility filter at line 613:
   ```typescript
   if (!showArchived) {
     result = result.filter(f => !f.archived);
   }
   ```

5. **Visual distinction** — in flow row rendering: when `flow.archived`, add `opacity-50` to the row and `<span className="text-xs text-zinc-500 ml-1">[archived]</span>` after the name.

6. **Add `showArchived` to useMemo deps** at line 657.

### Step 13: Update flow actions & action context (~20min)

**Depends on**: Step 11 (hooks must exist), Step 12 (FlowsPage wires actions).

**File**: `web/src/lib/actions/flow-actions.ts`

Add two actions in "organize" group between `flow.duplicate` (line 82) and `flow.export-yaml` (line 91):

- `flow.archive`: `{ id: "flow.archive", label: "Archive Flow", icon: Archive, group: "organize", groupOrder: 30, isAvailable: (f) => !f.archived, execute: (flow, ctx) => ctx.extraMutations?.archiveFlow?.mutate(flow.path) }`
- `flow.unarchive`: `{ id: "flow.unarchive", label: "Unarchive Flow", icon: ArchiveRestore, group: "organize", groupOrder: 30, isAvailable: (f) => !!f.archived, execute: (flow, ctx) => ctx.extraMutations?.unarchiveFlow?.mutate(flow.path) }`

Add `Archive, ArchiveRestore` to lucide-react imports at line 1.

**File**: `web/src/lib/actions/types.ts:33` — extend:
```typescript
extraMutations?: {
  deleteFlow?: ReturnType<typeof useDeleteFlow>;
  archiveFlow?: ReturnType<typeof useArchiveFlow>;
  unarchiveFlow?: ReturnType<typeof useUnarchiveFlow>;
};
```

Add imports for `useArchiveFlow`, `useUnarchiveFlow` from `@/hooks/useEditor`.

**File**: `web/src/components/menus/ActionContextProvider.tsx:14` — update `ActionContextProviderProps.extraMutations` type to match. Update FlowsPage to pass `archiveFlow` and `unarchiveFlow` mutations to `ActionContextProvider`.

### Step 14: Web tests (~20min)

**Depends on**: Steps 12-13.

**File**: `web/src/lib/actions/__tests__/flow-actions.test.ts` (extend)

Uses existing `makeFlow()` helper (line 6). Add `archived` to the defaults:

1. Update `makeFlow` to include `archived: false` in defaults.
2. `test_archive_available_for_non_archived` — `makeFlow()` → actions include `flow.archive`, exclude `flow.unarchive`.
3. `test_unarchive_available_for_archived` — `makeFlow({ archived: true })` → actions include `flow.unarchive`, exclude `flow.archive`.

Run: `cd web && npm run test`

### Step 15: Documentation updates (~20min)

**Depends on**: all implementation steps complete.

**File**: `pyproject.toml` — bump `version = "0.45.3"` → `"0.45.4"` (line 14).

**File**: `CHANGELOG.md` — add `## [0.45.4] — 2026-04-24` / `### Added` section after line 5. Entry covers: three CLI commands under `stepwise flow`, listing filter across all surfaces (CLI `flows`, API, web, agent-help, catalog), web UI toggle, API endpoints, test count. Match `[0.45.2]` density.

**File**: `docs/cli.md`
1. Update Overview table at line 9: add `| [Flow Lifecycle](#flow-lifecycle-commands) | \`flow archive\`, \`flow unarchive\`, \`flow delete\` |`
2. Add "Flow Lifecycle Commands" section after line 545 ("Job Lifecycle Commands" ends). Three subsections: `stepwise flow archive`, `stepwise flow unarchive`, `stepwise flow delete`. Each with usage, flag table, examples.

**File**: `README.md` — add to CLI snippet after line 147: `stepwise flow archive/unarchive/delete     Flow lifecycle management`

---

## Testing Strategy

### Run commands

```bash
# Step 2: Helper unit tests
uv run pytest tests/test_flow_archive.py::TestHelpers -xvs

# Step 4: CLI command tests
uv run pytest tests/test_flow_archive.py::TestCLI -xvs

# Step 6: Flows listing tests
uv run pytest tests/test_flow_archive.py::TestFlowsListing -xvs

# Step 8: Agent help tests
uv run pytest tests/test_flow_archive.py::TestAgentHelp -xvs

# Step 10: Server API tests
uv run pytest tests/test_editor_api.py::TestFlowArchiveAPI -xvs

# All new Python tests at once
uv run pytest tests/test_flow_archive.py tests/test_editor_api.py -xvs

# Full Python regression
uv run pytest tests/ -x -q

# Step 14: Web tests
cd web && npm run test

# Web lint
cd web && npm run lint
```

### Test matrix

| Test case | File | Class::method | Requirement |
|-----------|------|---------------|-------------|
| `is_archived` True for `archived: true` | `test_flow_archive.py` | `TestHelpers::test_is_archived_true` | R7 |
| `is_archived` False for missing key | `test_flow_archive.py` | `TestHelpers::test_is_archived_false_missing` | R7 |
| `is_archived` False for broken YAML | `test_flow_archive.py` | `TestHelpers::test_is_archived_broken_yaml` | R7 |
| `set_flow_archived` inserts key | `test_flow_archive.py` | `TestHelpers::test_set_archived_true` | R1 |
| `set_flow_archived` removes key | `test_flow_archive.py` | `TestHelpers::test_set_archived_false` | R1 |
| `set_flow_archived` idempotent (true) | `test_flow_archive.py` | `TestHelpers::test_set_archived_idempotent_true` | R1 |
| `set_flow_archived` idempotent (false) | `test_flow_archive.py` | `TestHelpers::test_set_archived_idempotent_false` | R1 |
| Round-trip preserves YAML bytes | `test_flow_archive.py` | `TestHelpers::test_round_trip_preserves` | R1 AC-1a/1b |
| CLI archive sets flag | `test_flow_archive.py` | `TestCLI::test_cli_archive_sets_flag` | R1 |
| CLI unarchive removes flag | `test_flow_archive.py` | `TestCLI::test_cli_unarchive_removes_flag` | R1 |
| CLI archive idempotent | `test_flow_archive.py` | `TestCLI::test_cli_archive_idempotent` | R1 AC-1c |
| CLI unarchive idempotent | `test_flow_archive.py` | `TestCLI::test_cli_unarchive_idempotent` | R1 AC-1c |
| CLI delete single-file --yes | `test_flow_archive.py` | `TestCLI::test_cli_delete_single_file_yes` | R2 AC-2a/2c |
| CLI delete directory --yes | `test_flow_archive.py` | `TestCLI::test_cli_delete_directory_yes` | R2 AC-2a |
| CLI delete confirmation match | `test_flow_archive.py` | `TestCLI::test_cli_delete_confirmation_match` | R2 AC-2a |
| CLI delete confirmation mismatch | `test_flow_archive.py` | `TestCLI::test_cli_delete_confirmation_mismatch` | R2 AC-2b |
| CLI delete nonexistent | `test_flow_archive.py` | `TestCLI::test_cli_delete_nonexistent` | R2 |
| `flows` hides archived by default | `test_flow_archive.py` | `TestFlowsListing::test_hides_archived` | R3 AC-3a |
| `flows --include-archived` | `test_flow_archive.py` | `TestFlowsListing::test_include_archived` | R3 AC-3b |
| `flows --archived-only` | `test_flow_archive.py` | `TestFlowsListing::test_archived_only` | R3 AC-3c |
| `flows` mutual exclusion | `test_flow_archive.py` | `TestFlowsListing::test_flags_mutual_exclusion` | R3 AC-3d |
| Agent help excludes archived | `test_flow_archive.py` | `TestAgentHelp::test_excludes_archived` | R6 AC-6a |
| `resolve_flow` finds archived | `test_flow_archive.py` | `TestAgentHelp::test_resolve_finds_archived` | R6 AC-6c |
| API default excludes archived | `test_editor_api.py` | `TestFlowArchiveAPI::test_list_excludes` | R4 AC-4a |
| API `?include_archived=true` | `test_editor_api.py` | `TestFlowArchiveAPI::test_list_includes` | R4 AC-4b |
| API `?archived_only=true` | `test_editor_api.py` | `TestFlowArchiveAPI::test_list_only` | R4 |
| API POST archive | `test_editor_api.py` | `TestFlowArchiveAPI::test_archive_endpoint` | R4 AC-4c |
| API POST unarchive | `test_editor_api.py` | `TestFlowArchiveAPI::test_unarchive_endpoint` | R4 AC-4d |
| API archive 404 | `test_editor_api.py` | `TestFlowArchiveAPI::test_archive_404` | R4 |
| Archive action for non-archived | `flow-actions.test.ts` | `archive available` | R5 AC-5c |
| Unarchive action for archived | `flow-actions.test.ts` | `unarchive available` | R5 AC-5c |

### Manual verification checklist

| # | Action | Expected | Automated? |
|---|--------|----------|------------|
| 1 | `stepwise flow archive <real-flow>` | YAML gains `archived: true`, rest untouched | Partially (synthetic YAML in test) |
| 2 | `stepwise flows` | Archived flow hidden | Yes |
| 3 | `stepwise flows -a` | Archived visible with `[archived]` | Yes |
| 4 | `stepwise flow unarchive` | Flow back in listing | Yes |
| 5 | `stepwise run <archived-flow>` | Runs normally | Yes (resolve test) |
| 6 | `stepwise flow delete` with mismatch | Abort, file intact | Yes |
| 7 | Web UI toggle | Shows/hides archived | Partially (action test; visual manual) |
| 8 | `stepwise agent-help` | Archived absent | Yes |
| 9 | `stepwise catalog` | Archived absent | Manual |

---

## Risks & Mitigations

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| **ruamel.yaml changes formatting** | Low | High — YAML mangled | Step 2 includes byte-identity round-trip test. `preserve_quotes=True` battle-tested in `server.py:4751`. |
| **`is_archived()` parse cost** | Low | Low — ~5ms/flow | Already pattern in `cmd_flows._flow_row()` (`cli.py:1550`). <100 flows in all deployments. |
| **Naming conflict resolved differently** | Medium | Low — localized | Only Step 3 changes. All other steps name-agnostic. |
| **Kit member archiving** | Low | Low | `is_archived()` per-flow, kit-agnostic. Kit sections show fewer members. |
| **Concurrent YAML edits** | Low | Medium — one write lost | Atomic `.tmp`→`rename()` (same as `server.py:4766`). Last writer wins. Low-frequency op. |
| **Delete kit member** | Low | Low — partial kit | Per spec: "delete is hard rm." Document in CLI help. |
| **`yaml_loader.py` rejects `archived` key** | Very Low | High — breaks loading | Verified: `yaml_loader.py:2017` uses `yaml.safe_load()` + extracts known keys. Unknown keys ignored. |
| **Web `fetchLocalFlows` stale after archive** | Low | Low — stale display | All mutation hooks invalidate `["localFlows"]` (`useEditor.ts:62`). Immediate refetch. |
