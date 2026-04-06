# Stepwise Kits — Implementation Plan (Phases 1 + 2: Backend + Web UI)

**Date**: 2026-04-05
**Design doc**: ~/work/vita/data/reports/2026-04-04-stepwise-kits-design.md
**Scope**: Phase 1 (backend core + API) and Phase 2 (web UI). Registry and dogfooding deferred.

---

## Overview

Add kit support to Stepwise: a kit is a directory with `KIT.yaml` containing flow subdirectories. This plan covers KIT.yaml parsing, kit-aware flow discovery, namespaced `kit/flow` resolution with strict-with-hints, three new API endpoints, CLI updates, and a web UI that groups flows by kit with collapsible sections. No changes to the engine, executor, or job model — kits are purely a discovery/presentation layer.

---

## Requirements

### R1: KIT.yaml Parsing
- Parse `KIT.yaml` files with fields: `name` (required), `description` (required), `author`, `category`, `usage`, `include`, `defaults`, `tags`.
- Validate: `name` must match directory name, `name` matches `FLOW_NAME_PATTERN`.
- **AC**: `load_kit_yaml("path/to/KIT.yaml")` returns a `KitDefinition` dataclass. Errors raise `KitLoadError` with clear messages. Invalid name or name/dir mismatch raises error.

### R2: Kit Discovery
- Detect kits in all flow discovery directories (`project_root`, `flows/`, `.stepwise/flows/`, `~/.stepwise/flows/`).
- A directory is a kit if it contains `KIT.yaml`. A directory is a flow if it contains `FLOW.yaml`. They are mutually exclusive at the same level.
- Kit member flows are the subdirectories within a kit directory that contain `FLOW.yaml`.
- **AC**: `discover_kits(project_dir)` returns `list[KitInfo]` with kit metadata and member flow names. `discover_flows()` returns flows with `kit_name` populated for kit members. No duplicate flows between kit and standalone discovery.

### R3: Namespaced Flow Resolution
- `resolve_flow("swdev/plan-light", project_dir)` resolves to `flows/swdev/plan-light/FLOW.yaml`.
- Bare names like `plan-light` do **not** resolve into kits. If bare name fails, the error message includes hints: "did you mean swdev/plan-light?".
- `FLOW_NAME_PATTERN` is NOT modified — kit-qualified names use a new code path before the regex check.
- **AC**: `resolve_flow("swdev/plan-light")` works. `resolve_flow("plan-light")` raises `FlowResolutionError` with hint when the flow exists only in a kit. `resolve_flow("swdev/plan-light")` fails clearly when kit or flow doesn't exist. All 15 existing `resolve_flow()` call sites in `cli.py` and `yaml_loader.py` work with kit/flow syntax with zero changes (the function signature is unchanged).

### R4: All Existing CLI Commands Work with Kit/Flow Syntax
- Because `resolve_flow()` is the single resolution point, all commands that accept a flow name gain kit support automatically: `stepwise run`, `stepwise validate`, `stepwise schema`, `stepwise check`, and flow-step sub-commands in `yaml_loader.py`.
- `stepwise run swdev` (kit name only, no flow) resolves to a directory containing `KIT.yaml` but no `FLOW.yaml` — raises `FlowResolutionError("'swdev' is a kit, not a flow. Use 'swdev/<flow-name>' to run a specific flow.")`.
- `flow_display_name()` for kit member flows returns the bare flow name (e.g., `plan-light` from `flows/swdev/plan-light/FLOW.yaml`) — this is correct since `parent.name` is the flow dir name. No change needed.
- **AC**: `stepwise validate swdev/plan-light` works. `stepwise run swdev` gives a clear kit-not-flow error. `flow_display_name` returns bare name for kit flows.

### R5: API Endpoints
- `GET /api/kits` — returns all kits with metadata, member flow count, and member flow names.
- `GET /api/kits/{kit_name}` — returns kit detail with usage field and full member flow info (same shape as `/api/local-flows` entries). Returns 404 for unknown kit.
- `GET /api/local-flows` extended with `kit_name: string | null` per flow.
- Reuse `_build_flow_graph()` (server.py existing helper) for kit member flow graph generation in the detail endpoint — no duplication of graph-building logic.
- **AC**: API returns correct JSON shapes per the response shapes section below. Kit members appear in both `/api/kits` and `/api/local-flows`. Non-kit flows have `kit_name: null`.

### R6: CLI `stepwise flows` Kit Grouping
- `stepwise flows` groups flows by kit. Kit header shows name, description, flow count. Standalone flows listed separately under "Standalone" header.
- Refactored to use `discover_flows()` + `discover_kits()` instead of manual scanning (current `cmd_flows()` at `cli.py:1397` does its own `iterdir()` which would miss kit member flows).
- `--visibility` filter applies across all sections.
- **AC**: `stepwise flows` output shows kit groupings. `stepwise flows --visibility internal` filters across both kit and standalone flows.

### R7: Web UI — FlowsPage Kit Grouping
- Flows grouped by kit in collapsible sections. Kit header: name, description, flow count, category badge.
- Standalone flows in "Ungrouped" section at the bottom.
- Click kit header to expand/collapse. All expanded by default.
- Search filter applies to both kit names and flow names within kits. If search matches kit name, show all flows in that kit.
- **AC**: FlowsPage renders kit sections. Collapse/expand works. Search filters across kit and flow names. Empty kit sections (after filtering) are hidden. Sorting works within sections.

---

## Assumptions (verified against codebase)

### A1: `discover_flows()` currently does one-level-deep directory scanning in `flows/`
**Verified**: `flow_resolution.py:116-128` — iterates `search_dir.iterdir()`, checks `child / FLOW_DIR_MARKER`. For `flows/` it also recurses via `rglob` (lines 146-165), but only finds `.flow.yaml` files and `FLOW.yaml` markers — it does NOT skip intermediate directories. This means `flows/swdev/plan-light/FLOW.yaml` would currently be discovered as a flow named `plan-light` (parent.name). **Kit-aware discovery must intercept directories with `KIT.yaml` and treat their children as kit members instead of top-level flows.**

### A2: `FLOW_NAME_PATTERN` rejects `/` characters
**Verified**: `flow_resolution.py:11` — `re.compile(r"^[a-zA-Z0-9_.+-]+$")`. The `resolve_flow` function (line 46) also short-circuits `"/" in name_or_path` to treat it as a path lookup, raising `FlowResolutionError`. **Kit-qualified names need a new code path inserted between the exact-path check (line 39) and the slash short-circuit (line 46). No regex change needed.**

### A3: `FlowInfo` has no kit_name field
**Verified**: `flow_resolution.py:14-19` — fields are `name`, `path`, `is_directory` only. **Adding `kit_name: str | None = None` is safe since it's optional with a default. All existing callers unpack positionally or by attribute — the new field won't break anything.**

### A4: `LocalFlow` TypeScript interface has no kit_name field
**Verified**: `types.ts:360-372` — no kit-related fields. **Adding `kit_name?: string | null` is a compatible extension — existing destructuring patterns won't break.**

### A5: `cmd_flows()` does its own scanning, separate from `discover_flows()`
**Verified**: `cli.py:1397-1472` — manually iterates `flows/*/FLOW.yaml` and `*.flow.yaml`. Does NOT call `discover_flows()`. This means it would NOT discover kit member flows without changes. **Must be refactored to use `discover_flows()` + `discover_kits()` for consistency.**

### A6: Server's `list_local_flows()` uses `discover_flows()`
**Verified**: `server.py:2829` — `flows = discover_flows(_project_dir)`. **Extending `FlowInfo` with `kit_name` propagates through naturally to the API response.**

### A7: No existing `KIT.yaml` or kit-related code exists
**Verified**: Grep for `KIT.yaml`, `kit_name`, `KitDefinition` returns zero matches in `src/`. **Clean greenfield addition.**

### A8: FlowMetadata is the only flow-level metadata model
**Verified**: `models.py:519-554`. **KitDefinition is a new, separate dataclass — not a subclass of FlowMetadata.**

### A9: `resolve_flow()` is the sole resolution choke-point for all CLI commands
**Verified**: 15 call sites across `cli.py` (lines 885, 991, 1027, 1618, 1689, 1784, 2042, 2204, 3249, 3419, 3612, 4982) and `yaml_loader.py` (line 521). All follow the same pattern: `flow_path = resolve_flow(args.flow, _project_dir(args))`. **Updating `resolve_flow()` once gives all commands kit/flow support for free.**

### A10: `flow_display_name()` uses `parent.name` for directory flows
**Verified**: `flow_resolution.py:177-178` — `if flow_path.name == FLOW_DIR_MARKER: return flow_path.parent.name`. For `flows/swdev/plan-light/FLOW.yaml`, this returns `plan-light` (the immediate parent). **This is correct behavior — no change needed.** Used by `runner.py` (6 call sites) for job objective/naming.

---

## Implementation Steps

### Step 1: `KitDefinition` dataclass in `models.py`
**File**: `src/stepwise/models.py` (insert after `FlowMetadata` class, ~line 555)
**Depends on**: Nothing
**Time**: 30 min

Add `KitDefinition` dataclass after `FlowMetadata`:

```python
@dataclass
class KitDefinition:
    """Metadata parsed from KIT.yaml."""
    name: str = ""
    description: str = ""
    author: str = ""
    category: str = ""
    usage: str = ""
    include: list[str] = field(default_factory=list)
    defaults: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d: dict = {}
        if self.name:
            d["name"] = self.name
        if self.description:
            d["description"] = self.description
        if self.author:
            d["author"] = self.author
        if self.category:
            d["category"] = self.category
        if self.usage:
            d["usage"] = self.usage
        if self.include:
            d["include"] = self.include
        if self.defaults:
            d["defaults"] = self.defaults
        if self.tags:
            d["tags"] = self.tags
        return d

    @classmethod
    def from_dict(cls, d: dict) -> KitDefinition:
        return cls(
            name=d.get("name", ""),
            description=d.get("description", ""),
            author=d.get("author", ""),
            category=d.get("category", ""),
            usage=d.get("usage", ""),
            include=d.get("include", []),
            defaults=d.get("defaults", {}),
            tags=d.get("tags", []),
        )
```

Follow existing pattern: `to_dict()` omits falsy/default values, `from_dict()` uses `.get()` with defaults. This matches `FlowMetadata.to_dict()` at line 529.

**Verification**: `uv run python -c "from stepwise.models import KitDefinition; k = KitDefinition(name='test', description='Test'); assert KitDefinition.from_dict(k.to_dict()).name == 'test'"`

---

### Step 2: KIT.yaml loader in `yaml_loader.py`
**File**: `src/stepwise/yaml_loader.py` (add after existing `YAMLLoadError` class)
**Depends on**: Step 1 (imports `KitDefinition`)
**Time**: 45 min

Add `load_kit_yaml(path: str | Path) -> KitDefinition` function and `KitLoadError` exception:

```python
class KitLoadError(Exception):
    """Error loading a KIT.yaml file."""
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("; ".join(errors))


def load_kit_yaml(path: str | Path) -> KitDefinition:
    """Parse a KIT.yaml file into a KitDefinition.

    Validates: name required, description required, name matches dir name,
    name matches FLOW_NAME_PATTERN.
    """
    path = Path(path)
    import yaml as _yaml
    from stepwise.flow_resolution import FLOW_NAME_PATTERN

    errors: list[str] = []
    try:
        data = _yaml.safe_load(path.read_text()) or {}
    except Exception as e:
        raise KitLoadError([f"Failed to parse {path}: {e}"])

    if not isinstance(data, dict):
        raise KitLoadError([f"{path}: expected a YAML mapping, got {type(data).__name__}"])

    name = data.get("name", "")
    if not name:
        errors.append("'name' is required")
    elif not FLOW_NAME_PATTERN.match(name):
        errors.append(f"Invalid kit name '{name}': must match [a-zA-Z0-9_.+-]+")

    if not data.get("description"):
        errors.append("'description' is required")

    # Name must match directory name
    if name and path.parent.name != name:
        errors.append(
            f"Kit name '{name}' does not match directory name '{path.parent.name}'"
        )

    if errors:
        raise KitLoadError(errors)

    # Validate types for optional fields
    include = data.get("include", [])
    if not isinstance(include, list):
        raise KitLoadError(["'include' must be a list"])

    tags = data.get("tags", [])
    if not isinstance(tags, list):
        raise KitLoadError(["'tags' must be a list"])

    return KitDefinition(
        name=name,
        description=data.get("description", ""),
        author=data.get("author", ""),
        category=data.get("category", ""),
        usage=data.get("usage", ""),
        include=[str(i) for i in include],
        defaults=data.get("defaults", {}),
        tags=[str(t) for t in tags],
    )
```

Unknown top-level keys are tolerated (not validated) — same behavior as `_parse_metadata()`. Uses `yaml.safe_load()` like existing `_parse_metadata()` at line 1114.

**Verification**: `uv run pytest tests/test_kit_discovery.py::TestLoadKitYaml -x -q`

---

### Step 3: `KitInfo` dataclass, `discover_kits()`, and updated `discover_flows()` in `flow_resolution.py`
**File**: `src/stepwise/flow_resolution.py`
**Depends on**: Step 1 (only needs `KIT_DIR_MARKER` constant, not `KitDefinition` — no cross-module import needed)
**Time**: 1.5 hr

**3a: Add constant and dataclass**

```python
KIT_DIR_MARKER = "KIT.yaml"

@dataclass
class KitInfo:
    """A discovered kit."""
    name: str
    path: Path              # path to KIT.yaml
    flow_names: list[str]   # member flow names (bare, not kit-qualified)
    flow_paths: list[Path]  # paths to member FLOW.yaml files
```

**3b: Add `kit_name` field to `FlowInfo`**

```python
@dataclass
class FlowInfo:
    name: str
    path: Path
    is_directory: bool
    kit_name: str | None = None  # populated for kit member flows
```

**3c: Add `_find_kit_dirs()` helper** (used by both `discover_kits` and `discover_flows`)

```python
def _find_kit_dirs(project_dir: Path) -> dict[Path, str]:
    """Find all kit directories across discovery paths.
    Returns {resolved_kit_dir: kit_name}.
    """
    kit_dirs: dict[Path, str] = {}
    for search_dir in _discovery_dirs(project_dir):
        if not search_dir.is_dir():
            continue
        for child in search_dir.iterdir():
            if child.is_dir() and (child / KIT_DIR_MARKER).is_file():
                resolved = child.resolve()
                if resolved not in kit_dirs:
                    kit_dirs[resolved] = child.name
    return kit_dirs
```

**3d: Add `discover_kits()`**

```python
def discover_kits(project_dir: Path) -> list[KitInfo]:
    """Find all kits in a project directory."""
    kit_dirs = _find_kit_dirs(project_dir)
    kits: list[KitInfo] = []
    for resolved_dir, kit_name in sorted(kit_dirs.items(), key=lambda x: x[1]):
        kit_yaml = resolved_dir / KIT_DIR_MARKER
        flow_names: list[str] = []
        flow_paths: list[Path] = []
        for child in sorted(resolved_dir.iterdir()):
            if child.is_dir():
                marker = child / FLOW_DIR_MARKER
                if marker.is_file():
                    flow_names.append(child.name)
                    flow_paths.append(marker)
        kits.append(KitInfo(
            name=kit_name,
            path=kit_yaml,
            flow_names=flow_names,
            flow_paths=flow_paths,
        ))
    return kits
```

**3e: Update `discover_flows()`** — critical change

The current `discover_flows()` has three phases per search dir:
1. Direct children with `FLOW.yaml` (lines 116-128)
2. Direct `*.flow.yaml` files (lines 131-143)
3. Recursive rglob for `.flow.yaml` and `FLOW.yaml` in non-root search dirs (lines 146-165)

Changes needed:
- **Phase 1**: When iterating direct children, skip any child that has `KIT.yaml` (it's a kit, not a flow). Instead, enumerate the kit's member flows and add them with `kit_name` set.
- **Phase 3 (rglob)**: Skip any `FLOW.yaml` whose path is under a kit directory. Use `_find_kit_dirs()` to get the set of kit directories, then filter rglob results.

The key logic change in phase 1:

```python
for child in sorted(search_dir.iterdir()):
    if not child.is_dir():
        continue
    # NEW: check if this is a kit directory
    if (child / KIT_DIR_MARKER).is_file():
        # It's a kit — enumerate member flows
        for member in sorted(child.iterdir()):
            if member.is_dir():
                marker = member / FLOW_DIR_MARKER
                if marker.is_file():
                    resolved = marker.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        flows.append(FlowInfo(
                            name=member.name,
                            path=marker,
                            is_directory=True,
                            kit_name=child.name,
                        ))
        continue  # skip the FLOW_DIR_MARKER check for the kit dir itself
    # existing: check for FLOW.yaml in directory
    marker = child / FLOW_DIR_MARKER
    if marker.is_file():
        # ... existing code ...
```

For the rglob phase, build a set of resolved kit dir paths and skip any result that falls under one:

```python
kit_dir_set = set(_find_kit_dirs(project_dir).keys())

# In rglob loop:
for child in sorted(search_dir.rglob(FLOW_DIR_MARKER)):
    resolved = child.resolve()
    if resolved in seen:
        continue
    # Skip if this FLOW.yaml is inside a kit directory
    if any(resolved.is_relative_to(kd) for kd in kit_dir_set):
        continue  # already discovered as kit member in phase 1
    # ... existing code ...
```

**Verification**: `uv run pytest tests/test_kit_discovery.py::TestDiscoverKits tests/test_kit_discovery.py::TestDiscoverFlowsWithKits -x -q`

**Regression**: `uv run pytest tests/test_flow_resolution.py -x -q` (all existing discovery/resolution tests must pass unchanged)

---

### Step 4: Kit-qualified flow resolution and strict-with-hints
**File**: `src/stepwise/flow_resolution.py` (modify `resolve_flow()`)
**Depends on**: Step 3 (`_find_kit_dirs`, `discover_kits`)
**Time**: 1 hr

**4a: Insert kit-qualified resolution before the slash short-circuit**

Current code at lines 45-47:
```python
    # If it looks like a path (contains / or ends with .yaml), don't do name resolution
    if "/" in name_or_path or name_or_path.endswith((".yaml", ".yml")):
        raise FlowResolutionError(f"Flow not found: {name_or_path}")
```

Replace with:
```python
    # Kit-qualified name: "kit/flow" (exactly one slash, no yaml extension)
    if "/" in name_or_path:
        if name_or_path.endswith((".yaml", ".yml")):
            raise FlowResolutionError(f"Flow not found: {name_or_path}")
        parts = name_or_path.split("/")
        if len(parts) == 2:
            return _resolve_kit_flow(parts[0], parts[1], project_dir or Path.cwd())
        raise FlowResolutionError(
            f"Invalid flow reference '{name_or_path}': "
            f"use 'kit/flow' format (one level only, no nesting)"
        )
```

**4b: Add `_resolve_kit_flow()` helper**

```python
def _resolve_kit_flow(kit_name: str, flow_name: str, project_dir: Path) -> Path:
    """Resolve kit/flow to the FLOW.yaml path. Raises FlowResolutionError."""
    search_dirs = _discovery_dirs(project_dir)
    kit_found = False

    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        kit_dir = search_dir / kit_name
        if not kit_dir.is_dir():
            continue
        kit_marker = kit_dir / KIT_DIR_MARKER
        if not kit_marker.is_file():
            continue
        kit_found = True

        flow_dir = kit_dir / flow_name
        flow_marker = flow_dir / FLOW_DIR_MARKER
        if flow_marker.is_file():
            return flow_marker

    if kit_found:
        raise FlowResolutionError(
            f"Flow '{flow_name}' not found in kit '{kit_name}'. "
            f"Available flows: {_list_kit_flows(kit_name, project_dir)}"
        )

    # Kit dir exists but no KIT.yaml — it's a regular directory, not a kit
    for search_dir in search_dirs:
        kit_dir = search_dir / kit_name
        if kit_dir.is_dir() and not (kit_dir / KIT_DIR_MARKER).is_file():
            raise FlowResolutionError(
                f"'{kit_name}' is a directory but not a kit (no KIT.yaml). "
                f"Did you mean to run a flow by path?"
            )

    raise FlowResolutionError(f"Kit '{kit_name}' not found.")


def _list_kit_flows(kit_name: str, project_dir: Path) -> str:
    """List flow names in a kit for error messages."""
    for search_dir in _discovery_dirs(project_dir):
        kit_dir = search_dir / kit_name
        if kit_dir.is_dir() and (kit_dir / KIT_DIR_MARKER).is_file():
            names = sorted(
                c.name for c in kit_dir.iterdir()
                if c.is_dir() and (c / FLOW_DIR_MARKER).is_file()
            )
            return ", ".join(names) if names else "(empty kit)"
    return "(unknown)"
```

**4c: Add hints to bare-name resolution failure**

At the end of `resolve_flow()`, where the `FlowResolutionError` is raised for "not found" (line 84-88), add kit hint scanning:

```python
    if not matches:
        # Check if the name exists inside any kit
        hint = _kit_hint_for_bare_name(name_or_path, project_dir)
        msg = f"Flow '{name_or_path}' not found."
        if hint:
            msg += f" {hint}"
        else:
            msg += f" Searched: {', '.join(str(d) for d in search_dirs if d.is_dir())}"
        raise FlowResolutionError(msg)


def _kit_hint_for_bare_name(name: str, project_dir: Path) -> str | None:
    """If a bare flow name exists inside kits, suggest the kit-qualified form."""
    kit_dirs = _find_kit_dirs(project_dir)
    matches: list[str] = []
    for resolved_dir, kit_name in kit_dirs.items():
        flow_dir = resolved_dir / name
        if flow_dir.is_dir() and (flow_dir / FLOW_DIR_MARKER).is_file():
            matches.append(f"{kit_name}/{name}")
    if len(matches) == 1:
        return f"Did you mean '{matches[0]}'?"
    if len(matches) > 1:
        return f"Did you mean: {', '.join(sorted(matches))}?"
    return None
```

**4d: Handle `stepwise run swdev` (kit name without flow)**

When `resolve_flow("swdev")` is called and `swdev` is a directory with `KIT.yaml` but no `FLOW.yaml`, the current code at line 41-44 checks for `FLOW_DIR_MARKER`. Since it won't find `FLOW.yaml`, it falls through to bare-name resolution which also fails. We need to intercept this case.

Add after the exact directory check (line 41-44):

```python
    if candidate.is_dir():
        marker = candidate / FLOW_DIR_MARKER
        if marker.is_file():
            return marker
        # NEW: Check if it's a kit directory
        if (candidate / KIT_DIR_MARKER).is_file():
            raise FlowResolutionError(
                f"'{name_or_path}' is a kit, not a flow. "
                f"Use '{candidate.name}/<flow-name>' to run a specific flow."
            )
```

And similarly in the bare-name resolution search loop, when a directory is found with `KIT.yaml` but no `FLOW.yaml`:

```python
    for search_dir in search_dirs:
        if not search_dir.is_dir():
            continue
        dir_flow = search_dir / name_or_path / FLOW_DIR_MARKER
        if dir_flow.is_file():
            matches.append(dir_flow)
            continue
        # NEW: Check if it's a kit
        kit_marker = search_dir / name_or_path / KIT_DIR_MARKER
        if kit_marker.is_file():
            raise FlowResolutionError(
                f"'{name_or_path}' is a kit, not a flow. "
                f"Use '{name_or_path}/<flow-name>' to run a specific flow. "
                f"Available flows: {_list_kit_flows(name_or_path, project_dir)}"
            )
```

**Verification**: `uv run pytest tests/test_kit_discovery.py::TestResolveKitFlow -x -q`

**Regression**: `uv run pytest tests/test_flow_resolution.py -x -q` (existing tests must pass — the new code paths only activate on `/` or kit detection)

---

### Step 5: Server API endpoints
**File**: `src/stepwise/server.py`
**Depends on**: Steps 2 + 3 + 4 (needs `load_kit_yaml`, `discover_kits`, updated `discover_flows` with `kit_name`)
**Time**: 1.5 hr

**5a: Extend `list_local_flows()` response** (modify existing function at line 2827)

Add `kit_name` field to each flow dict in the result list:

```python
result.append({
    # ... existing fields (path, name, description, etc.) ...
    "kit_name": flow_info.kit_name,  # str | None — new field
})
```

This propagates naturally from the updated `FlowInfo.kit_name` set in Step 3.

**5b: Add `_build_flow_info_dict()` helper** (extract from `list_local_flows` to avoid duplication with kit detail endpoint)

```python
def _build_flow_info_dict(flow_path: Path, flow_name: str, kit_name: str | None = None) -> dict:
    """Build the standard flow info dict used by /api/local-flows and /api/kits/{name}."""
    from stepwise.yaml_loader import load_workflow_yaml, YAMLLoadError

    # ... mtime, YAML parsing, graph building — same logic as current list_local_flows ...
    return {
        "path": ..., "name": flow_name, "description": ...,
        "steps_count": ..., "modified_at": ..., "is_directory": True,
        "executor_types": ..., "visibility": ..., "source": "local",
        "kit_name": kit_name, "graph": ...,
    }
```

Refactor `list_local_flows()` to call `_build_flow_info_dict()` for each flow, replacing the inline parsing block (~lines 2834-2882).

**5c: `GET /api/kits` endpoint** (add after `list_local_flows` endpoint)

```python
@app.get("/api/kits")
def list_kits():
    """List all discovered kits with metadata."""
    from stepwise.flow_resolution import discover_kits
    from stepwise.yaml_loader import load_kit_yaml, KitLoadError

    kits = discover_kits(_project_dir)
    result = []
    for kit_info in kits:
        kit_def = None
        try:
            kit_def = load_kit_yaml(kit_info.path)
        except (KitLoadError, Exception):
            pass
        result.append({
            "name": kit_info.name,
            "description": kit_def.description if kit_def else "",
            "author": kit_def.author if kit_def else "",
            "category": kit_def.category if kit_def else "",
            "usage": kit_def.usage if kit_def else "",
            "tags": kit_def.tags if kit_def else [],
            "flow_count": len(kit_info.flow_names),
            "flow_names": kit_info.flow_names,
        })
    return result
```

**5d: `GET /api/kits/{kit_name}` endpoint**

```python
@app.get("/api/kits/{kit_name}")
def get_kit_detail(kit_name: str):
    """Get full kit detail including member flows."""
    from stepwise.flow_resolution import discover_kits
    from stepwise.yaml_loader import load_kit_yaml, KitLoadError

    kits = discover_kits(_project_dir)
    kit = next((k for k in kits if k.name == kit_name), None)
    if not kit:
        raise HTTPException(status_code=404, detail=f"Kit '{kit_name}' not found")

    try:
        kit_def = load_kit_yaml(kit.path)
    except KitLoadError as e:
        raise HTTPException(status_code=500, detail=f"Error parsing KIT.yaml: {e}")

    flows = []
    for flow_name, flow_path in zip(kit.flow_names, kit.flow_paths):
        flows.append(_build_flow_info_dict(flow_path, flow_name, kit_name=kit_name))

    return {
        "name": kit_def.name,
        "description": kit_def.description,
        "author": kit_def.author,
        "category": kit_def.category,
        "usage": kit_def.usage,
        "tags": kit_def.tags,
        "include": kit_def.include,
        "defaults": kit_def.defaults,
        "flows": flows,
    }
```

**Verification**: `uv run pytest tests/test_editor_api.py::TestKitEndpoints -x -q`

**Regression**: `uv run pytest tests/test_editor_api.py -x -q` (existing endpoint tests must pass)

---

### Step 6: CLI `stepwise flows` kit grouping
**File**: `src/stepwise/cli.py` (rewrite `cmd_flows()` at line 1397)
**Depends on**: Steps 2 + 3 (needs `discover_flows` with `kit_name`, `discover_kits`, `load_kit_yaml`)
**Time**: 1 hr

Refactor `cmd_flows()` to use `discover_flows()` and `discover_kits()` instead of manual scanning:

```python
def cmd_flows(args: argparse.Namespace) -> int:
    """List available flows in the current project."""
    import yaml as _yaml
    from stepwise.flow_resolution import discover_flows, discover_kits
    from stepwise.yaml_loader import load_kit_yaml, KitLoadError

    io = _io(args)
    project_dir = Path(args.project_dir).resolve() if args.project_dir else Path.cwd().resolve()

    flows = discover_flows(project_dir)
    kits = discover_kits(project_dir)

    # Build per-flow metadata (description, step count, visibility)
    def _flow_row(flow_info):
        try:
            raw = _yaml.safe_load(flow_info.path.read_text()) or {}
        except Exception:
            raw = {}
        name = raw.get("name") or flow_info.name
        desc = (raw.get("description", "") or "")[:50]
        visibility = raw.get("visibility", "interactive")
        steps = len(raw.get("steps") or {})
        return {"name": name, "description": desc, "steps": steps, "visibility": visibility}

    # Apply visibility filter
    vis_filter = getattr(args, "visibility", None)
    def _vis_ok(vis):
        if vis_filter and vis_filter != "all":
            return vis == vis_filter
        if not vis_filter:
            return vis != "internal"
        return True

    # Group flows by kit
    kit_flows: dict[str, list] = {}
    standalone: list = []
    for f in flows:
        row = _flow_row(f)
        if not _vis_ok(row["visibility"]):
            continue
        if f.kit_name:
            kit_flows.setdefault(f.kit_name, []).append(row)
        else:
            standalone.append(row)

    has_output = False

    # Print kit sections
    kit_meta = {}
    for kit in kits:
        try:
            kit_def = load_kit_yaml(kit.path)
            kit_meta[kit.name] = kit_def.description
        except (KitLoadError, Exception):
            kit_meta[kit.name] = ""

    for kit_name in sorted(kit_flows.keys()):
        members = sorted(kit_flows[kit_name], key=lambda r: r["name"].lower())
        desc = kit_meta.get(kit_name, "")
        io.log("info", f"\n{kit_name} — {desc} ({len(members)} flows)" if desc else f"\n{kit_name} ({len(members)} flows)")
        rows = [[r["name"], r["description"], str(r["steps"]),
                 r["visibility"] if r["visibility"] != "interactive" else ""] for r in members]
        io.table(["NAME", "DESCRIPTION", "STEPS", "VISIBILITY"], rows)
        has_output = True

    # Print standalone section
    if standalone:
        standalone.sort(key=lambda r: r["name"].lower())
        if kit_flows:
            io.log("info", f"\nStandalone ({len(standalone)} flows)")
        rows = [[r["name"], r["description"], str(r["steps"]),
                 r["visibility"] if r["visibility"] != "interactive" else ""] for r in standalone]
        io.table(["NAME", "DESCRIPTION", "STEPS", "VISIBILITY"], rows)
        has_output = True

    if not has_output:
        io.log("info", "No flows found. Create one with: stepwise new <name>")

    return EXIT_SUCCESS
```

The table columns (NAME, DESCRIPTION, STEPS, VISIBILITY) remain identical to the current output. The only structural change is that kit members appear under a kit header line.

**Verification**: `uv run pytest tests/test_flows_command.py -x -q`

---

### Step 7: TypeScript types
**File**: `web/src/lib/types.ts` (add `Kit` after `LocalFlow`, extend `LocalFlow`)
**Depends on**: Step 5 (must match API response shapes)
**Time**: 15 min

Add `Kit` interface after the `LocalFlowDetail` interface (~line 402):

```typescript
// ── Kits ──────────────────────────���───────────────────────────────────

export interface Kit {
  name: string;
  description: string;
  author: string;
  category: string;
  usage: string;
  tags: string[];
  flow_count: number;
  flow_names: string[];
}

export interface KitDetail extends Kit {
  include: string[];
  defaults: Record<string, unknown>;
  flows: LocalFlow[];
}
```

Extend `LocalFlow` (add field at end of interface):

```typescript
export interface LocalFlow {
  // ... existing fields ...
  kit_name?: string | null;
}
```

**Verification**: `cd web && npx tsc --noEmit` (type-check passes)

---

### Step 8: API client functions
**File**: `web/src/lib/api.ts` (add after `fetchLocalFlows`)
**Depends on**: Step 7 (imports `Kit`, `KitDetail` types)
**Time**: 15 min

Add:

```typescript
export function fetchKits(): Promise<Kit[]> {
  return request<Kit[]>("/kits");
}

export function fetchKitDetail(kitName: string): Promise<KitDetail> {
  return request<KitDetail>(`/kits/${encodeURIComponent(kitName)}`);
}
```

**Verification**: `cd web && npx tsc --noEmit`

---

### Step 9: React Query hooks
**File**: `web/src/hooks/useEditor.ts` (add after `useLocalFlows`)
**Depends on**: Step 8 (calls `api.fetchKits`, `api.fetchKitDetail`)
**Time**: 20 min

Add:

```typescript
export function useKits() {
  return useQuery({
    queryKey: ["kits"],
    queryFn: api.fetchKits,
    staleTime: 10000, // match useLocalFlows staleTime
  });
}

export function useKitDetail(kitName: string | null) {
  return useQuery({
    queryKey: ["kitDetail", kitName],
    queryFn: () => api.fetchKitDetail(kitName!),
    enabled: !!kitName,
    staleTime: 30000,
  });
}
```

**Verification**: `cd web && npx tsc --noEmit`

---

### Step 10: FlowsPage kit grouping
**File**: `web/src/pages/FlowsPage.tsx`
**Depends on**: Steps 7 + 8 + 9 (needs `Kit` type, `useKits()` hook, `kit_name` on `LocalFlow`)
**Time**: 2 hr

This is the largest UI change. The current FlowsPage renders a flat list/grid of flows. The new version groups flows by kit.

**Component structure**:

```
FlowsPage
├── Toolbar (search, visibility filter, view mode, time range — unchanged)
├── KitSection (one per kit that has matching flows)
│   ├── KitHeader (chevron + name + description + category badge + flow count)
│   └── FlowTable/FlowGrid (same rendering as current, scoped to kit members)
├── StandaloneSection (flows with kit_name === null, header only shown if kits exist)
└── RegistryTab (unchanged)
```

**New `KitSection` component** (defined inline in FlowsPage.tsx — no new file since it's tightly coupled to FlowsPage state):

```tsx
function KitSection({
  kitName,
  kitDescription,
  kitCategory,
  flows,
  expanded,
  onToggle,
  // pass through selection, sort, view mode, action handlers
  ...tableProps
}: {
  kitName: string;
  kitDescription: string;
  kitCategory: string;
  flows: LocalFlow[];
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="mb-2">
      <button
        onClick={onToggle}
        className="flex items-center gap-2 w-full px-3 py-2 rounded-md hover:bg-muted/50 transition-colors"
      >
        <ChevronRight
          className={cn(
            "h-4 w-4 shrink-0 transition-transform duration-200",
            expanded && "rotate-90"
          )}
        />
        <span className="font-semibold text-sm">{kitName}</span>
        <span className="text-muted-foreground text-xs truncate">{kitDescription}</span>
        {kitCategory && (
          <Badge variant="outline" className="text-[10px] px-1.5 py-0">
            {kitCategory}
          </Badge>
        )}
        <span className="text-muted-foreground text-xs ml-auto shrink-0">
          {flows.length} {flows.length === 1 ? "flow" : "flows"}
        </span>
      </button>
      {expanded && (
        <div className="ml-6">
          {/* Reuse existing flow row/grid rendering */}
        </div>
      )}
    </div>
  );
}
```

**State management**:

```tsx
// All kits expanded by default — initialized when kits data loads
const [expandedKits, setExpandedKits] = useState<Set<string>>(new Set());
const kitsQuery = useKits();

useEffect(() => {
  if (kitsQuery.data) {
    setExpandedKits(new Set(kitsQuery.data.map((k) => k.name)));
  }
}, [kitsQuery.data]);

const toggleKit = useCallback((name: string) => {
  setExpandedKits((prev) => {
    const next = new Set(prev);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    return next;
  });
}, []);
```

**Grouping logic** (in the component body, after fetching):

```tsx
const { kitGroups, standaloneFlows } = useMemo(() => {
  const kits = kitsQuery.data ?? [];
  const filtered = filteredFlows; // already filtered by search/visibility/time

  // Build kit name → Kit lookup
  const kitMap = new Map(kits.map((k) => [k.name, k]));

  // Group flows
  const groups = new Map<string, LocalFlow[]>();
  const standalone: LocalFlow[] = [];

  for (const flow of filtered) {
    if (flow.kit_name && kitMap.has(flow.kit_name)) {
      const arr = groups.get(flow.kit_name) ?? [];
      arr.push(flow);
      groups.set(flow.kit_name, arr);
    } else {
      standalone.push(flow);
    }
  }

  // Filter: if search matches kit name, include all its flows
  // (This requires checking the raw search against kit names separately)

  return {
    kitGroups: Array.from(groups.entries())
      .map(([name, flows]) => ({ kit: kitMap.get(name)!, flows }))
      .filter((g) => g.flows.length > 0)
      .sort((a, b) => a.kit.name.localeCompare(b.kit.name)),
    standaloneFlows: standalone,
  };
}, [filteredFlows, kitsQuery.data]);
```

**Search behavior**: The existing search filter (`filter`) is a text match on flow names. Extend it: if the search string matches a kit name (case-insensitive substring), include ALL flows in that kit regardless of individual flow name match. This ensures typing "swdev" shows all swdev flows.

**Rendering** (in the JSX, replacing the current flat flow list when kits exist):

```tsx
{kitGroups.length > 0 || standaloneFlows.length > 0 ? (
  <>
    {kitGroups.map(({ kit, flows }) => (
      <KitSection
        key={kit.name}
        kitName={kit.name}
        kitDescription={kit.description}
        kitCategory={kit.category}
        flows={flows}
        expanded={expandedKits.has(kit.name)}
        onToggle={() => toggleKit(kit.name)}
      />
    ))}
    {standaloneFlows.length > 0 && (
      <>
        {kitGroups.length > 0 && (
          <div className="px-3 py-2 text-sm text-muted-foreground font-medium">
            Standalone ({standaloneFlows.length} flows)
          </div>
        )}
        {/* Render standalone flows using existing flow row/grid code */}
      </>
    )}
  </>
) : (
  /* existing empty state */
)}
```

When no kits exist, the page renders identically to today (flat list, no section headers) — the grouping UI only activates when `kitsQuery.data` has entries.

**Verification**: `cd web && npm run test -- --run FlowsPage`

---

## Dependency Graph

```
Step 1: KitDefinition dataclass
  ↓
  ├──→ Step 2: KIT.yaml loader (imports KitDefinition from models.py)
  ↓
  └──→ Step 3: discover_kits + updated discover_flows (uses KIT_DIR_MARKER constant,
  ↓       FlowInfo.kit_name field — no import from models.py needed)
  ↓
  Step 4: Kit-qualified resolution (uses _find_kit_dirs from Step 3)
  ↓
  ├──→ Step 5: Server API (imports load_kit_yaml from Step 2, discover_kits from Step 3,
  │       uses kit_name on FlowInfo from Step 3)
  │
  └──→ Step 6: CLI flows (imports discover_flows from Step 3, discover_kits from Step 3,
         load_kit_yaml from Step 2)
         ↓
  Step 7: TypeScript types (must match Step 5 API response shapes)
         ↓
  Step 8: API client functions (imports types from Step 7)
         ↓
  Step 9: React Query hooks (calls functions from Step 8)
         ↓
  Step 10: FlowsPage (uses hooks from Step 9, types from Step 7)
```

**Why this order matters**:
- Steps 1-4 are the backend foundation. Each step builds on the previous one's exports.
- Steps 2 and 3 both depend on Step 1 but are independent of each other — they could be done in parallel.
- Step 4 depends on Step 3 specifically because `_resolve_kit_flow` and `_kit_hint_for_bare_name` use `_find_kit_dirs()` from Step 3.
- Steps 5 and 6 are the backend consumers and are independent of each other. Both need Steps 2-4 complete. **This is the natural point to write and run all backend tests before moving to frontend.**
- Steps 7-9 form a strict chain (types → api client → hooks) with each step consuming the previous step's exports.
- Step 10 depends on all of 7-9 and is the only frontend step that requires significant logic/testing.

**Backend/Frontend handoff boundary**: After Steps 1-6 are complete and all backend tests pass (`uv run pytest tests/ -x -q`), the backend API contract is frozen. Steps 7-10 are purely frontend and can be implemented against the running server or mocked API responses. The API response shapes defined in this plan (see reference section below) are the contract between the two phases.

---

## Testing Strategy

### Test file: `tests/test_kit_discovery.py` (new)

Run: `uv run pytest tests/test_kit_discovery.py -x -v`

This is the primary test file covering all new kit functionality. Each test class maps to a specific implementation step.

**Shared test fixtures and helpers** (top of file):

```python
"""Tests for kit discovery, KIT.yaml parsing, and kit-aware flow resolution."""

import pytest
from pathlib import Path

from stepwise.flow_resolution import (
    FlowInfo,
    FlowResolutionError,
    KitInfo,
    discover_flows,
    discover_kits,
    resolve_flow,
)
from stepwise.models import KitDefinition
from stepwise.yaml_loader import KitLoadError, load_kit_yaml


SIMPLE_FLOW = """\
name: test
steps:
  hello:
    run: 'echo "hello"'
    outputs: [msg]
"""

MINIMAL_KIT = """\
name: {name}
description: "Test kit"
"""

FULL_KIT = """\
name: {name}
description: "Full test kit"
author: tester
category: testing
usage: |
  ## When to use
  Always.
include:
  - "@community:helper@^1.0"
tags: [test, demo]
defaults:
  model: test-model
"""


def _make_kit(project: Path, kit_name: str, flow_names: list[str],
              kit_yaml: str | None = None) -> Path:
    """Create a kit directory with member flows under flows/."""
    kit_dir = project / "flows" / kit_name
    kit_dir.mkdir(parents=True, exist_ok=True)
    yaml = kit_yaml or MINIMAL_KIT.format(name=kit_name)
    (kit_dir / "KIT.yaml").write_text(yaml)
    for flow_name in flow_names:
        flow_dir = kit_dir / flow_name
        flow_dir.mkdir(exist_ok=True)
        (flow_dir / "FLOW.yaml").write_text(
            SIMPLE_FLOW.replace("name: test", f"name: {flow_name}")
        )
    return kit_dir


def _make_standalone_flow(project: Path, name: str) -> Path:
    """Create a standalone directory flow under flows/."""
    flow_dir = project / "flows" / name
    flow_dir.mkdir(parents=True, exist_ok=True)
    (flow_dir / "FLOW.yaml").write_text(
        SIMPLE_FLOW.replace("name: test", f"name: {name}")
    )
    return flow_dir
```

**Class 1: `TestKitDefinition`** (validates Step 1)

```python
class TestKitDefinition:
    """KitDefinition dataclass serialization."""

    def test_round_trip_minimal(self):
        k = KitDefinition(name="test", description="A test kit")
        d = k.to_dict()
        assert d == {"name": "test", "description": "A test kit"}
        k2 = KitDefinition.from_dict(d)
        assert k2.name == "test"
        assert k2.description == "A test kit"
        assert k2.author == ""
        assert k2.tags == []

    def test_round_trip_full(self):
        k = KitDefinition(
            name="swdev", description="Dev kit", author="zack",
            category="development", usage="Use wisely",
            include=["@bob:review"], defaults={"model": "gpt-4"},
            tags=["dev", "plan"],
        )
        k2 = KitDefinition.from_dict(k.to_dict())
        assert k2.name == k.name
        assert k2.include == k.include
        assert k2.defaults == k.defaults
        assert k2.tags == k.tags

    def test_to_dict_omits_defaults(self):
        k = KitDefinition(name="test", description="Test")
        d = k.to_dict()
        assert "author" not in d
        assert "tags" not in d
        assert "include" not in d

    def test_from_dict_missing_keys_uses_defaults(self):
        k = KitDefinition.from_dict({"name": "test"})
        assert k.description == ""
        assert k.tags == []
        assert k.defaults == {}
```

**Class 2: `TestLoadKitYaml`** (validates Step 2)

```python
class TestLoadKitYaml:
    """KIT.yaml parsing and validation."""

    def test_minimal_kit(self, tmp_path):
        kit_dir = tmp_path / "mykit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text("name: mykit\ndescription: Test\n")
        result = load_kit_yaml(kit_dir / "KIT.yaml")
        assert result.name == "mykit"
        assert result.description == "Test"

    def test_full_kit(self, tmp_path):
        kit_dir = tmp_path / "swdev"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text(FULL_KIT.format(name="swdev"))
        result = load_kit_yaml(kit_dir / "KIT.yaml")
        assert result.name == "swdev"
        assert result.author == "tester"
        assert result.category == "testing"
        assert "Always" in result.usage
        assert result.include == ["@community:helper@^1.0"]
        assert result.tags == ["test", "demo"]
        assert result.defaults == {"model": "test-model"}

    def test_missing_name_errors(self, tmp_path):
        kit_dir = tmp_path / "bad"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text("description: No name\n")
        with pytest.raises(KitLoadError, match="name.*required"):
            load_kit_yaml(kit_dir / "KIT.yaml")

    def test_missing_description_errors(self, tmp_path):
        kit_dir = tmp_path / "bad"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text("name: bad\n")
        with pytest.raises(KitLoadError, match="description.*required"):
            load_kit_yaml(kit_dir / "KIT.yaml")

    def test_name_dir_mismatch_errors(self, tmp_path):
        kit_dir = tmp_path / "actual-dir"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text("name: wrong-name\ndescription: Test\n")
        with pytest.raises(KitLoadError, match="does not match directory"):
            load_kit_yaml(kit_dir / "KIT.yaml")

    def test_invalid_name_errors(self, tmp_path):
        kit_dir = tmp_path / "bad kit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text("name: bad kit\ndescription: Test\n")
        with pytest.raises(KitLoadError, match="Invalid kit name"):
            load_kit_yaml(kit_dir / "KIT.yaml")

    def test_unknown_keys_tolerated(self, tmp_path):
        kit_dir = tmp_path / "mykit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text(
            "name: mykit\ndescription: Test\nfuture_field: value\n"
        )
        result = load_kit_yaml(kit_dir / "KIT.yaml")
        assert result.name == "mykit"  # no error from unknown key

    def test_include_must_be_list(self, tmp_path):
        kit_dir = tmp_path / "mykit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text(
            "name: mykit\ndescription: Test\ninclude: not-a-list\n"
        )
        with pytest.raises(KitLoadError, match="include.*must be a list"):
            load_kit_yaml(kit_dir / "KIT.yaml")

    def test_malformed_yaml_errors(self, tmp_path):
        kit_dir = tmp_path / "mykit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text(": : : invalid yaml :::")
        with pytest.raises(KitLoadError, match="Failed to parse"):
            load_kit_yaml(kit_dir / "KIT.yaml")

    def test_empty_yaml_errors(self, tmp_path):
        kit_dir = tmp_path / "mykit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text("")
        with pytest.raises(KitLoadError, match="name.*required"):
            load_kit_yaml(kit_dir / "KIT.yaml")
```

**Class 3: `TestDiscoverKits`** (validates Step 3, discovery half)

```python
class TestDiscoverKits:
    """discover_kits() finds all kits in a project."""

    def test_finds_kit_in_flows_dir(self, tmp_path):
        _make_kit(tmp_path, "mykit", ["flow-a", "flow-b"])
        kits = discover_kits(tmp_path)
        assert len(kits) == 1
        assert kits[0].name == "mykit"

    def test_kit_members_discovered(self, tmp_path):
        _make_kit(tmp_path, "mykit", ["alpha", "beta"])
        kits = discover_kits(tmp_path)
        assert sorted(kits[0].flow_names) == ["alpha", "beta"]
        assert len(kits[0].flow_paths) == 2
        assert all(p.name == "FLOW.yaml" for p in kits[0].flow_paths)

    def test_empty_kit_has_no_members(self, tmp_path):
        _make_kit(tmp_path, "empty-kit", [])
        kits = discover_kits(tmp_path)
        assert len(kits) == 1
        assert kits[0].flow_names == []

    def test_multiple_kits(self, tmp_path):
        _make_kit(tmp_path, "alpha-kit", ["f1"])
        _make_kit(tmp_path, "beta-kit", ["f2"])
        kits = discover_kits(tmp_path)
        names = [k.name for k in kits]
        assert sorted(names) == ["alpha-kit", "beta-kit"]

    def test_kit_in_project_root(self, tmp_path):
        """Kit can exist at project root, not just flows/."""
        kit_dir = tmp_path / "rootkit"
        kit_dir.mkdir()
        (kit_dir / "KIT.yaml").write_text("name: rootkit\ndescription: Test\n")
        flow_dir = kit_dir / "myflow"
        flow_dir.mkdir()
        (flow_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        kits = discover_kits(tmp_path)
        assert any(k.name == "rootkit" for k in kits)

    def test_kit_and_standalone_coexist(self, tmp_path):
        _make_kit(tmp_path, "mykit", ["kit-flow"])
        _make_standalone_flow(tmp_path, "solo")
        kits = discover_kits(tmp_path)
        assert len(kits) == 1
        assert kits[0].name == "mykit"
        # solo is a flow, not a kit — shouldn't appear

    def test_dir_with_flow_yaml_is_not_kit(self, tmp_path):
        """A directory with FLOW.yaml but no KIT.yaml is a flow, not a kit."""
        _make_standalone_flow(tmp_path, "not-a-kit")
        kits = discover_kits(tmp_path)
        assert len(kits) == 0

    def test_deduplicates_by_resolved_path(self, tmp_path):
        """Same kit reachable from multiple search dirs is only listed once."""
        # Kit in flows/ (reachable from project root scan AND flows/ scan)
        _make_kit(tmp_path, "mykit", ["f1"])
        kits = discover_kits(tmp_path)
        assert sum(1 for k in kits if k.name == "mykit") == 1
```

**Class 4: `TestDiscoverFlowsWithKits`** (validates Step 3, flow discovery half)

```python
class TestDiscoverFlowsWithKits:
    """discover_flows() correctly handles kit member flows."""

    def test_kit_members_have_kit_name(self, tmp_path):
        _make_kit(tmp_path, "mykit", ["flow-a", "flow-b"])
        flows = discover_flows(tmp_path)
        kit_flows = [f for f in flows if f.kit_name == "mykit"]
        assert len(kit_flows) == 2
        assert sorted(f.name for f in kit_flows) == ["flow-a", "flow-b"]

    def test_standalone_has_null_kit_name(self, tmp_path):
        _make_standalone_flow(tmp_path, "solo")
        flows = discover_flows(tmp_path)
        solo = [f for f in flows if f.name == "solo"]
        assert len(solo) == 1
        assert solo[0].kit_name is None

    def test_kit_members_not_duplicated(self, tmp_path):
        """Kit member flows don't also appear as standalone flows."""
        _make_kit(tmp_path, "mykit", ["plan", "implement"])
        _make_standalone_flow(tmp_path, "solo")
        flows = discover_flows(tmp_path)
        plan_flows = [f for f in flows if f.name == "plan"]
        assert len(plan_flows) == 1  # only the kit member, not a standalone dupe
        assert plan_flows[0].kit_name == "mykit"

    def test_rglob_skips_kit_internals(self, tmp_path):
        """rglob phase doesn't independently discover kit member flows."""
        _make_kit(tmp_path, "mykit", ["deep-flow"])
        flows = discover_flows(tmp_path)
        deep = [f for f in flows if f.name == "deep-flow"]
        assert len(deep) == 1  # exactly one, from kit discovery
        assert deep[0].kit_name == "mykit"

    def test_mixed_kit_and_standalone(self, tmp_path):
        """Both kit member flows and standalone flows appear in results."""
        _make_kit(tmp_path, "mykit", ["kit-flow"])
        _make_standalone_flow(tmp_path, "standalone")
        flows = discover_flows(tmp_path)
        names = {f.name for f in flows}
        assert names == {"kit-flow", "standalone"}

    def test_dir_with_both_kit_and_flow_yaml_treated_as_kit(self, tmp_path):
        """If a directory has both KIT.yaml and FLOW.yaml, KIT.yaml wins."""
        weird_dir = tmp_path / "flows" / "ambiguous"
        weird_dir.mkdir(parents=True)
        (weird_dir / "KIT.yaml").write_text("name: ambiguous\ndescription: Test\n")
        (weird_dir / "FLOW.yaml").write_text(SIMPLE_FLOW)
        # Add a member flow to make it a meaningful kit
        member = weird_dir / "child"
        member.mkdir()
        (member / "FLOW.yaml").write_text(SIMPLE_FLOW)
        flows = discover_flows(tmp_path)
        # "ambiguous" itself should not appear as a flow
        assert not any(f.name == "ambiguous" and f.kit_name is None for f in flows)
        # "child" should appear as a kit member
        assert any(f.name == "child" and f.kit_name == "ambiguous" for f in flows)

    def test_existing_discovery_tests_still_pass(self, tmp_path):
        """Basic non-kit discovery still works (regression guard)."""
        flows_dir = tmp_path / "flows"
        flows_dir.mkdir()
        df = flows_dir / "dir-flow"
        df.mkdir()
        (df / "FLOW.yaml").write_text(SIMPLE_FLOW)
        (flows_dir / "file-flow.flow.yaml").write_text(SIMPLE_FLOW)

        result = discover_flows(tmp_path)
        names = {f.name for f in result}
        assert names == {"dir-flow", "file-flow"}
        assert all(f.kit_name is None for f in result)
```

**Class 5: `TestResolveKitFlow`** (validates Step 4)

```python
class TestResolveKitFlow:
    """Kit-qualified flow resolution and strict-with-hints."""

    def test_kit_slash_flow_resolves(self, tmp_path):
        _make_kit(tmp_path, "mykit", ["my-flow"])
        result = resolve_flow("mykit/my-flow", project_dir=tmp_path)
        expected = tmp_path / "flows" / "mykit" / "my-flow" / "FLOW.yaml"
        assert result == expected

    def test_bare_name_doesnt_resolve_kit_member(self, tmp_path):
        """Bare 'my-flow' does NOT resolve into a kit."""
        _make_kit(tmp_path, "mykit", ["my-flow"])
        with pytest.raises(FlowResolutionError):
            resolve_flow("my-flow", project_dir=tmp_path)

    def test_bare_name_hint_single_kit(self, tmp_path):
        """Error message suggests kit-qualified name when flow exists in one kit."""
        _make_kit(tmp_path, "mykit", ["plan"])
        with pytest.raises(FlowResolutionError, match="Did you mean.*mykit/plan"):
            resolve_flow("plan", project_dir=tmp_path)

    def test_bare_name_hint_multiple_kits(self, tmp_path):
        """Error suggests multiple options when flow exists in multiple kits."""
        _make_kit(tmp_path, "alpha", ["shared"])
        _make_kit(tmp_path, "beta", ["shared"])
        with pytest.raises(FlowResolutionError, match="Did you mean.*alpha/shared.*beta/shared"):
            resolve_flow("shared", project_dir=tmp_path)

    def test_nonexistent_kit_errors(self, tmp_path):
        (tmp_path / "flows").mkdir(exist_ok=True)
        with pytest.raises(FlowResolutionError, match="Kit.*not found"):
            resolve_flow("nokit/flow", project_dir=tmp_path)

    def test_nonexistent_flow_in_kit_errors(self, tmp_path):
        _make_kit(tmp_path, "mykit", ["real-flow"])
        with pytest.raises(FlowResolutionError, match="not found in kit.*mykit"):
            resolve_flow("mykit/no-such-flow", project_dir=tmp_path)

    def test_nonexistent_flow_lists_available(self, tmp_path):
        """Error for missing flow in kit lists available flows."""
        _make_kit(tmp_path, "mykit", ["alpha", "beta"])
        with pytest.raises(FlowResolutionError, match="alpha.*beta"):
            resolve_flow("mykit/missing", project_dir=tmp_path)

    def test_nested_slash_rejected(self, tmp_path):
        """'a/b/c' is rejected (no nested kits)."""
        with pytest.raises(FlowResolutionError, match="one level only"):
            resolve_flow("a/b/c", project_dir=tmp_path)

    def test_existing_standalone_not_affected(self, tmp_path):
        """Standalone flow resolution still works when kits exist."""
        _make_kit(tmp_path, "mykit", ["kit-only-flow"])
        _make_standalone_flow(tmp_path, "solo")
        result = resolve_flow("solo", project_dir=tmp_path)
        assert result == tmp_path / "flows" / "solo" / "FLOW.yaml"

    def test_kit_name_as_bare_name_gives_kit_error(self, tmp_path):
        """'stepwise run swdev' when swdev is a kit gives helpful error."""
        _make_kit(tmp_path, "swdev", ["plan", "implement"])
        with pytest.raises(FlowResolutionError, match="is a kit.*not a flow"):
            resolve_flow("swdev", project_dir=tmp_path)

    def test_kit_name_as_bare_name_lists_flows(self, tmp_path):
        """Error for kit-as-flow includes available flow names."""
        _make_kit(tmp_path, "swdev", ["plan", "implement"])
        with pytest.raises(FlowResolutionError, match="plan.*implement"):
            resolve_flow("swdev", project_dir=tmp_path)

    def test_yaml_extension_still_rejects_with_slash(self, tmp_path):
        """'kit/flow.yaml' is still treated as path lookup, not kit ref."""
        with pytest.raises(FlowResolutionError, match="not found"):
            resolve_flow("kit/flow.yaml", project_dir=tmp_path)

    def test_dir_without_kit_yaml_not_treated_as_kit(self, tmp_path):
        """A directory with subdir/FLOW.yaml but no KIT.yaml is not a kit."""
        parent = tmp_path / "flows" / "notkit"
        parent.mkdir(parents=True)
        child = parent / "child"
        child.mkdir()
        (child / "FLOW.yaml").write_text(SIMPLE_FLOW)
        with pytest.raises(FlowResolutionError, match="not a kit.*no KIT.yaml"):
            resolve_flow("notkit/child", project_dir=tmp_path)
```

### Extending existing test files

**`tests/test_editor_api.py`** — add `TestKitEndpoints` class:

Run: `uv run pytest tests/test_editor_api.py::TestKitEndpoints -x -v`

```python
class TestKitEndpoints:
    """Tests for /api/kits and /api/kits/{name} endpoints."""

    @pytest.fixture
    def kit_project(self, tmp_path):
        """Project dir with a kit and a standalone flow."""
        # Kit with two flows
        kit_dir = tmp_path / "flows" / "testkit"
        kit_dir.mkdir(parents=True)
        (kit_dir / "KIT.yaml").write_text(
            "name: testkit\ndescription: A test kit\nauthor: tester\ncategory: testing\n"
        )
        for name in ("alpha", "beta"):
            fd = kit_dir / name
            fd.mkdir()
            (fd / "FLOW.yaml").write_text(
                f"name: {name}\nsteps:\n  s:\n    run: echo '{{}}'\n    outputs: [x]\n"
            )
        # Standalone flow
        solo = tmp_path / "flows" / "solo"
        solo.mkdir(parents=True, exist_ok=True)
        (solo / "FLOW.yaml").write_text(
            "name: solo\nsteps:\n  s:\n    run: echo '{}'\n    outputs: [x]\n"
        )
        return tmp_path

    @pytest.fixture
    def kit_client(self, kit_project):
        old_env = os.environ.copy()
        os.environ["STEPWISE_PROJECT_DIR"] = str(kit_project)
        os.environ["STEPWISE_DB"] = ":memory:"
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
        os.environ.clear()
        os.environ.update(old_env)

    def test_list_kits_empty(self, client):
        """No kits → empty list."""
        resp = client.get("/api/kits")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_kits_with_kit(self, kit_client):
        resp = kit_client.get("/api/kits")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        kit = data[0]
        assert kit["name"] == "testkit"
        assert kit["description"] == "A test kit"
        assert kit["author"] == "tester"
        assert kit["category"] == "testing"
        assert kit["flow_count"] == 2
        assert sorted(kit["flow_names"]) == ["alpha", "beta"]

    def test_kit_detail_returns_flows(self, kit_client):
        resp = kit_client.get("/api/kits/testkit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "testkit"
        assert data["usage"] == ""
        assert len(data["flows"]) == 2
        flow_names = sorted(f["name"] for f in data["flows"])
        assert flow_names == ["alpha", "beta"]
        # Each flow has standard fields
        for f in data["flows"]:
            assert "steps_count" in f
            assert f["kit_name"] == "testkit"

    def test_kit_detail_not_found(self, kit_client):
        resp = kit_client.get("/api/kits/nonexistent")
        assert resp.status_code == 404

    def test_local_flows_includes_kit_name(self, kit_client):
        resp = kit_client.get("/api/local-flows")
        assert resp.status_code == 200
        flows = resp.json()
        kit_flows = [f for f in flows if f.get("kit_name") == "testkit"]
        assert len(kit_flows) == 2
        solo_flows = [f for f in flows if f["name"] == "solo"]
        assert len(solo_flows) == 1
        assert solo_flows[0].get("kit_name") is None
```

**`tests/test_flows_command.py`** — add `TestFlowsKitGrouping` class:

Run: `uv run pytest tests/test_flows_command.py::TestFlowsKitGrouping -x -v`

```python
def _make_kit(project: Path, kit_name: str, flow_names: list[str]) -> Path:
    """Create a kit with member flows."""
    kit_dir = project / "flows" / kit_name
    kit_dir.mkdir(parents=True, exist_ok=True)
    (kit_dir / "KIT.yaml").write_text(
        f"name: {kit_name}\ndescription: Test kit\n"
    )
    for name in flow_names:
        fd = kit_dir / name
        fd.mkdir(exist_ok=True)
        (fd / "FLOW.yaml").write_text(
            f"name: {name}\nsteps:\n  s:\n    run: echo '{{}}'\n    outputs: [x]\n"
        )
    return kit_dir


class TestFlowsKitGrouping:
    """Kit grouping in `stepwise flows` output."""

    def test_kit_header_appears(self, tmp_path, capsys, monkeypatch):
        project = _make_project(tmp_path)
        _make_kit(project, "mykit", ["flow-a"])
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        assert "mykit" in combined
        assert "flow-a" in combined

    def test_standalone_separate_from_kit(self, tmp_path, capsys, monkeypatch):
        project = _make_project(tmp_path)
        _make_kit(project, "mykit", ["kit-flow"])
        _make_flow_dir(project, "solo", "name: solo\nsteps:\n  s:\n    run: echo '{}'\n")
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        assert "mykit" in combined
        assert "kit-flow" in combined
        assert "solo" in combined

    def test_visibility_filter_applies_to_kit_flows(self, tmp_path, capsys, monkeypatch):
        project = _make_project(tmp_path)
        kit_dir = project / "flows" / "mykit"
        kit_dir.mkdir(parents=True)
        (kit_dir / "KIT.yaml").write_text("name: mykit\ndescription: Test\n")
        fd = kit_dir / "hidden"
        fd.mkdir()
        (fd / "FLOW.yaml").write_text(
            "name: hidden\nvisibility: internal\nsteps:\n  s:\n    run: echo '{}'\n"
        )
        rc, combined = _run_flows(monkeypatch, tmp_path, capsys)
        assert rc == EXIT_SUCCESS
        # Internal flows hidden by default
        assert "hidden" not in combined
```

### Web tests: `web/src/pages/FlowsPage.test.tsx`

Run: `cd web && npm run test -- --run FlowsPage`

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
// ... imports for FlowsPage, wrapper, mocks ...

const mockKits = [
  {
    name: "swdev",
    description: "Software development",
    author: "zack",
    category: "development",
    usage: "",
    tags: [],
    flow_count: 2,
    flow_names: ["plan", "implement"],
  },
];

const mockFlows = [
  { name: "plan", kit_name: "swdev", /* ... other fields */ },
  { name: "implement", kit_name: "swdev", /* ... other fields */ },
  { name: "welcome", kit_name: null, /* ... other fields */ },
];

describe("FlowsPage kit grouping", () => {
  it("renders kit sections with headers", () => {
    // Mock useKits and useLocalFlows
    render(<FlowsPage />, { wrapper });
    expect(screen.getByText("swdev")).toBeInTheDocument();
    expect(screen.getByText("Software development")).toBeInTheDocument();
  });

  it("collapses and expands kit sections", () => {
    render(<FlowsPage />, { wrapper });
    const header = screen.getByText("swdev");
    fireEvent.click(header);
    // After collapse, member flows should be hidden
    expect(screen.queryByText("plan")).not.toBeInTheDocument();
    fireEvent.click(header);
    // After expand, member flows visible again
    expect(screen.getByText("plan")).toBeInTheDocument();
  });

  it("search filters across kit names and flow names", () => {
    render(<FlowsPage />, { wrapper });
    const search = screen.getByPlaceholderText(/search/i);
    fireEvent.change(search, { target: { value: "swdev" } });
    // Kit flows should appear (kit name matches)
    expect(screen.getByText("plan")).toBeInTheDocument();
    expect(screen.getByText("implement")).toBeInTheDocument();
  });

  it("hides empty kit sections after filtering", () => {
    render(<FlowsPage />, { wrapper });
    const search = screen.getByPlaceholderText(/search/i);
    fireEvent.change(search, { target: { value: "welcome" } });
    // swdev section should be hidden (no matching flows)
    expect(screen.queryByText("swdev")).not.toBeInTheDocument();
    // welcome should appear
    expect(screen.getByText("welcome")).toBeInTheDocument();
  });

  it("renders flat list when no kits exist", () => {
    // Mock useKits returning empty, useLocalFlows with standalone flows only
    render(<FlowsPage />, { wrapper });
    // No kit headers, just flow list
    expect(screen.queryByText("Standalone")).not.toBeInTheDocument();
    expect(screen.getByText("welcome")).toBeInTheDocument();
  });
});
```

### Full regression test commands

After each implementation step, run the targeted test command listed in that step's **Verification** line. After all steps are complete, run the full suite:

```bash
# Backend — all tests
uv run pytest tests/ -x -q

# Backend — only kit-related tests
uv run pytest tests/test_kit_discovery.py tests/test_editor_api.py::TestKitEndpoints tests/test_flows_command.py::TestFlowsKitGrouping -x -v

# Frontend — all tests
cd web && npm run test

# Frontend — only FlowsPage tests
cd web && npm run test -- --run FlowsPage

# Type check frontend
cd web && npx tsc --noEmit
```

---

## Risks & Mitigations

### Risk 1: `discover_flows()` rglob picks up kit member flows as standalone
**Impact**: High — duplicate flows in UI, ambiguous resolution
**Mitigation**: Step 3 builds a `kit_dir_set` from `_find_kit_dirs()` and filters rglob results with `is_relative_to()`. The phase-1 scan also checks for `KIT.yaml` before `FLOW.yaml`.
**Test**: `TestDiscoverFlowsWithKits::test_kit_members_not_duplicated`, `test_rglob_skips_kit_internals`

### Risk 2: Breaking existing bare flow name resolution
**Impact**: High — `stepwise run myflow` stops working
**Mitigation**: Kit-qualified resolution is a new code path, triggered only when `"/" in name_or_path` and name doesn't end with `.yaml`. Bare name resolution is unchanged. Hint generation is additive (only in the error message when name not found).
**Test**: `TestResolveKitFlow::test_existing_standalone_not_affected`, plus full `tests/test_flow_resolution.py` regression

### Risk 3: `cmd_flows()` refactor changes output format
**Impact**: Medium — CLI users/scripts may depend on table format
**Mitigation**: Keep identical table columns (NAME, DESCRIPTION, STEPS, VISIBILITY). Kit headers are added lines above each group — no existing lines are removed or reformatted.
**Test**: `TestFlowsCommand::test_table_headers_present` (existing, still passes), `TestFlowsKitGrouping::test_kit_header_appears`

### Risk 4: Performance regression from kit scanning
**Impact**: Low — kit detection adds one `KIT.yaml` stat call per directory in discovery paths
**Mitigation**: `_find_kit_dirs()` is called once and cached within the same function call. No YAML parsing during discovery — only `Path.is_file()` checks.

### Risk 5: Directory with both `KIT.yaml` and `FLOW.yaml`
**Impact**: Low — ambiguous state
**Mitigation**: `KIT.yaml` takes precedence (checked first in the scan loop). If both exist, treat as kit. No separate warning needed — the `FLOW.yaml` in the kit root is simply ignored.
**Test**: `TestDiscoverFlowsWithKits::test_dir_with_both_kit_and_flow_yaml_treated_as_kit`

### Risk 6: `_find_kit_dirs()` called multiple times per request
**Impact**: Low — redundant filesystem scanning
**Mitigation**: In `discover_flows()`, call `_find_kit_dirs()` once at the top and pass the result to both the phase-1 scan and the rglob filter. In `resolve_flow()`, `_find_kit_dirs()` is only called on the error path (bare-name hint), never on the happy path.

---

## Deferred / Out of Scope

- **`include` resolution** — KIT.yaml `include` field is parsed and stored but not resolved. No registry fetching or re-export semantics.
- **`defaults` inheritance** — KIT.yaml `defaults` field is parsed and stored but not applied to member flows.
- **`stepwise catalog`** — auto-generated SKILL.md catalog section. Separate task.
- **`stepwise agent-help` refactor** — L0/L1/L2 progressive disclosure. Separate task.
- **Registry kit publishing/install** — Phase 4 per design doc.
- **Kit detail view in web UI** — clicking kit header could open a detail panel with usage text. Deferred to follow-up.
- **Dogfooding (moving vita flows into kits)** — Phase 2 in design doc, separate task after this implementation.
- **`stepwise new` inside a kit** — `stepwise new swdev/new-flow` could create a flow inside an existing kit. Not in scope; can be added later by extending `cmd_new()` to detect the `kit/flow` syntax.
- **Kit-level input/config inheritance** — flows inheriting `config_vars` or `input_vars` from KIT.yaml `defaults`. Requires engine-level plumbing.

---

## KIT.yaml Schema Reference

```yaml
# Required
name: swdev                    # must match directory name, FLOW_NAME_PATTERN
description: "Software dev"   # one-line description

# Optional
author: zack
category: development          # for L0 catalog grouping
usage: |                       # NL composition instructions for agents
  ## When to use this kit
  ...
include:                       # external flow references (parsed, not resolved in v1)
  - "@community:code-review@^1.0"
defaults:                      # kit-level defaults (parsed, not applied in v1)
  model: anthropic/claude-sonnet-4-20250514
tags: [planning, implementation]
```

## API Response Shapes

### `GET /api/kits`
```json
[
  {
    "name": "swdev",
    "description": "Software development planning and implementation",
    "author": "zack",
    "category": "development",
    "usage": "## When to use this kit\n...",
    "tags": ["planning", "implementation"],
    "flow_count": 7,
    "flow_names": ["plan-light", "plan", "plan-strong", "implement", "fast-implement", "code-review", "test-fix"]
  }
]
```

### `GET /api/kits/{kit_name}`
```json
{
  "name": "swdev",
  "description": "Software development planning and implementation",
  "author": "zack",
  "category": "development",
  "usage": "## When to use this kit\n...",
  "tags": ["planning", "implementation"],
  "include": ["@community:code-review@^1.0"],
  "defaults": {"model": "anthropic/claude-sonnet-4-20250514"},
  "flows": [
    {
      "path": "flows/swdev/plan-light/FLOW.yaml",
      "name": "plan-light",
      "description": "Lightweight planning for bounded tasks",
      "steps_count": 3,
      "modified_at": "2026-04-05T12:00:00",
      "is_directory": true,
      "executor_types": ["agent"],
      "visibility": "interactive",
      "source": "local",
      "kit_name": "swdev",
      "graph": null
    }
  ]
}
```

### `GET /api/local-flows` (extended)
```json
[
  {
    "path": "flows/swdev/plan-light/FLOW.yaml",
    "name": "plan-light",
    "kit_name": "swdev",
    "description": "...",
    "steps_count": 3,
    "modified_at": "2026-04-05T12:00:00",
    "is_directory": true,
    "executor_types": ["agent"],
    "visibility": "interactive",
    "source": "local",
    "graph": null
  },
  {
    "path": "flows/welcome/FLOW.yaml",
    "name": "welcome",
    "kit_name": null,
    "description": "...",
    "steps_count": 1,
    "modified_at": "2026-04-05T12:00:00",
    "is_directory": true,
    "executor_types": ["script"],
    "visibility": "interactive",
    "source": "local",
    "graph": null
  }
]
```
