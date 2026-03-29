# Plan: Flow Visibility/Category System

## Overview

Add a `visibility` field to the FLOW.yaml schema so flows can declare whether they're meant for interactive CLI use, background automation, internal composition, or other contexts. This field gates which flows surface in agent-help instructions, CLI suggestions, and the web UI — solving the problem where all flows appear indiscriminately regardless of context.

The design uses a single `visibility` field (not free-form tags, which already exist) because the filtering need is a controlled vocabulary with clear behavioral implications: a flow is either surfaced to agents or it isn't, shown in CLI listings or it isn't. Tags remain available for user-defined categorization.

## Requirements

### R1: FLOW.yaml `visibility` field
- Add `visibility: str` to `FlowMetadata` with values: `"interactive"`, `"background"`, `"internal"`, `"unlisted"`
- Default: `"interactive"` (backward-compatible — existing flows surface everywhere they do today)
- Parse from top-level YAML field alongside `name`, `description`, `tags`, etc.
- **Acceptance criteria:** `load_workflow_yaml()` parses `visibility: background` from YAML. `FlowMetadata.to_dict()`/`from_dict()` round-trips the value. Invalid values produce a validation error.

### R2: Agent-help filters by visibility
- `generate_agent_help()` only includes flows with `visibility: interactive` (the default)
- `build_emit_flow_instructions()` shows `interactive` and `background` flows (agents composing sub-flows need to see background flows too)
- **Acceptance criteria:** A flow with `visibility: internal` does not appear in `stepwise agent-help` output. A flow with `visibility: background` does not appear in agent-help but does appear in emit-flow instructions.

### R3: CLI `stepwise flows` filters and labels
- Default listing shows all flows except `unlisted`
- Add `--visibility <value>` filter flag (e.g., `stepwise flows --visibility background`)
- Add `--all` flag to include unlisted flows
- Display visibility value in the output table as a column
- **Acceptance criteria:** `stepwise flows` hides `unlisted` flows. `stepwise flows --visibility background` shows only background flows. `stepwise flows --all` shows everything.

### R4: Web UI filtering
- FlowsPage shows visibility as a badge/label on each flow card
- Add filter chips or dropdown for visibility categories
- Default view shows all except `unlisted` (same as CLI)
- **Acceptance criteria:** Flows page shows visibility badges. Clicking a category filter narrows the list. `unlisted` flows only appear with explicit "Show all" toggle.

### R5: Validation
- `WorkflowDefinition.validate()` rejects unknown visibility values
- `stepwise validate` reports the flow's visibility in its output
- **Acceptance criteria:** `visibility: foo` produces a validation error listing valid values.

### R6: Documentation
- Update FLOW_REFERENCE.md (or equivalent) with visibility field docs
- Update CLAUDE.md YAML format section with visibility examples
- **Acceptance criteria:** The visibility field and its values are documented with examples showing when to use each.

## Visibility Values

| Value | Meaning | Surfaces in |
|---|---|---|
| `interactive` | Designed for direct use by humans or agents in CLI sessions | agent-help, CLI listing, web UI, emit-flow instructions |
| `background` | Long-running or batch automation — not useful in interactive contexts | CLI listing, web UI, emit-flow instructions |
| `internal` | Building block for composition only — not meant to be run directly | CLI listing (dimmed), web UI (with label) |
| `unlisted` | Hidden by default — test flows, deprecated flows, WIP | Only with `--all` flag or "Show all" toggle |

## Assumptions (verified against codebase)

1. **`FlowMetadata` already has `tags: list[str]`** — confirmed at `models.py:534`. Visibility is a separate concern from tags; tags are free-form, visibility is controlled vocabulary with behavioral implications.

2. **`FlowMetadata.to_dict()` only emits non-empty fields** — confirmed at `models.py:537-551`. We follow the same pattern: only emit `visibility` if it's not the default.

3. **`_parse_metadata()` in `yaml_loader.py` handles top-level fields** — confirmed. Adding `visibility` follows the same pattern as `tags` and `forked_from`.

4. **`discover_flows()` returns `FlowInfo` objects without metadata** — confirmed at `flow_resolution.py:14-19`. `FlowInfo` has `name`, `path`, `is_directory` only. Filtering by visibility requires loading the YAML, which `_build_flow_entries()` in `agent_help.py` already does. No need to add metadata to `FlowInfo`.

5. **`list_local_flows()` in `server.py` already loads each flow's YAML** to extract `description`, `steps_count`, `executor_types` — confirmed. Adding `visibility` to the response is trivial.

6. **The `LocalFlow` TypeScript interface** in `types.ts` maps to the `/api/local-flows` response. Adding `visibility` field is straightforward.

7. **FlowsPage** already has client-side filtering (name substring) and sorting — confirmed at `FlowsPage.tsx`. Adding visibility filter chips follows the existing pattern.

8. **`cmd_flows()` in `cli.py`** already displays a table with NAME, DESCRIPTION, STEPS, TAGS columns — confirmed. Adding a VISIBILITY column is straightforward.

## Implementation Steps

### Step 1: Add `visibility` to `FlowMetadata` model
**File:** `src/stepwise/models.py`

- Add `visibility: str = "interactive"` field to `FlowMetadata` dataclass (after `tags`)
- Add `VALID_VISIBILITIES = {"interactive", "background", "internal", "unlisted"}` module-level constant
- Update `to_dict()`: emit `visibility` only if not `"interactive"` (default)
- Update `from_dict()`: read `visibility` with default `"interactive"`

### Step 2: Parse `visibility` from YAML
**File:** `src/stepwise/yaml_loader.py`

- In `_parse_metadata()`, extract `visibility` from top-level data dict
- Validate against `VALID_VISIBILITIES` — add to errors list if invalid
- Pass through to `FlowMetadata` constructor

### Step 3: Add validation
**File:** `src/stepwise/models.py`

- In `WorkflowDefinition.validate()`, check `self.metadata.visibility in VALID_VISIBILITIES`
- Add descriptive error: `f"Invalid visibility '{self.metadata.visibility}'. Valid: {sorted(VALID_VISIBILITIES)}"`

### Step 4: Filter agent-help output
**File:** `src/stepwise/agent_help.py`

- In `generate_agent_help()`, after building entries via `_build_flow_entries()`, filter to only `visibility == "interactive"` entries
- In `build_emit_flow_instructions()`, filter to `visibility in ("interactive", "background")` — agents composing flows need to see background flows
- `_build_flow_entries()` needs to include visibility in each entry dict (read from parsed `FlowMetadata`)

### Step 5: Update CLI `stepwise flows`
**File:** `src/stepwise/cli.py`

- Add `--visibility` argument to `flows` subparser (choices from `VALID_VISIBILITIES`)
- Add `--all` flag
- In `cmd_flows()`: load each flow's metadata to get visibility, filter based on flags
  - Default: exclude `unlisted`
  - `--visibility X`: show only flows with that visibility
  - `--all`: show everything
- Add VISIBILITY column to output table
- In `cmd_validate()`: print the flow's visibility in the summary output

### Step 6: Update server endpoint
**File:** `src/stepwise/server.py`

- In `list_local_flows()`: include `visibility` in each flow's response dict (read from parsed metadata, default `"interactive"`)
- Optionally add `?visibility=` query parameter for server-side filtering

### Step 7: Update TypeScript types and API
**Files:** `web/src/lib/types.ts`, `web/src/lib/api.ts`

- Add `visibility: string` to `LocalFlow` interface (with `"interactive"` as expected default)
- Add `visibility?: string` query parameter to `fetchLocalFlows()` if server-side filtering is added

### Step 8: Update FlowsPage UI
**File:** `web/src/pages/FlowsPage.tsx`

- Add visibility filter state (default: show all except `unlisted`)
- Add filter chips/buttons above the flow list: "All", "Interactive", "Background", "Internal" + "Show unlisted" toggle
- Display visibility as a subtle badge on each flow card (color-coded: interactive=default/no badge, background=blue, internal=gray, unlisted=dim)
- Update client-side filtering logic to respect visibility selection

### Step 9: Update `stepwise new` template
**File:** `src/stepwise/cli.py`

- Default template YAML includes `visibility: interactive` (commented or explicit)
- This teaches the field to new flow authors by example

### Step 10: Update existing flows
**Files:** `flows/*/FLOW.yaml`

- `flows/welcome/FLOW.yaml` → `visibility: interactive` (explicitly, as example)
- `flows/test-polling/FLOW.yaml` → `visibility: internal` (test infrastructure)
- `flows/test-concurrency/FLOW.yaml` → `visibility: internal`
- `flows/rapid-test.flow.yaml` → `visibility: unlisted`

### Step 11: Documentation
**Files:** `CLAUDE.md`, flow reference docs

- Add visibility to the YAML format section in CLAUDE.md with examples
- Document each visibility value and when to use it
- Add visibility to the FlowMetadata table

## Testing Strategy

### Python unit tests

**New file:** `tests/test_visibility.py`

```bash
uv run pytest tests/test_visibility.py -v
```

Tests:
1. **Parse visibility from YAML** — load YAML with `visibility: background`, verify `wf.metadata.visibility == "background"`
2. **Default visibility** — load YAML without visibility field, verify `metadata.visibility == "interactive"`
3. **Invalid visibility rejects** — load YAML with `visibility: secret`, verify validation error
4. **to_dict/from_dict round-trip** — verify default omits field, non-default preserves it
5. **Agent-help filtering** — call `generate_agent_help()` with a mix of visibilities, verify only interactive flows appear
6. **Emit-flow instructions filtering** — verify interactive + background flows appear, internal/unlisted don't

### CLI tests

```bash
uv run pytest tests/test_cli.py -v -k "flows"
```

- Verify `stepwise flows` excludes unlisted flows
- Verify `stepwise flows --visibility background` filters correctly
- Verify `stepwise flows --all` shows everything
- Verify `stepwise validate` reports visibility

### Web tests

```bash
cd web && npm run test
```

- Test that FlowsPage renders visibility badges
- Test that filter chips correctly narrow flow list
- Test that unlisted flows are hidden by default

### Integration validation

```bash
# Validate existing flows still pass after schema change
uv run stepwise validate flows/welcome/FLOW.yaml
uv run stepwise validate flows/research-proposal/FLOW.yaml

# Verify agent-help output excludes internal flows
uv run stepwise agent-help

# Verify CLI listing
uv run stepwise flows
uv run stepwise flows --all
```

## Migration & Backward Compatibility

- Default `"interactive"` means all existing flows behave exactly as before — no breaking change
- `to_dict()` omits the field when it's the default, so existing serialized flows don't grow
- The YAML parser accepts flows without the field (already the case since `_parse_metadata` uses `.get()` with defaults)
- Registry flows: the registry API already passes through arbitrary metadata fields, so `visibility` will propagate naturally when flows are published/fetched

## Non-goals

- **Tag-based filtering in agent-help** — tags are free-form and don't map cleanly to "should this agent see this flow". Visibility is the right lever.
- **Per-user visibility overrides** — not needed; visibility is a property of the flow, not the viewer
- **Visibility inheritance for sub-flows** — a `background` parent flow can compose `internal` sub-flows freely; visibility gates discovery, not execution
