# Plan: Surfacing Flow Configuration in the Stepwise Web UI

## Overview

Flows declare typed configuration variables via the `config:` block in FLOW.yaml. These variables (`ConfigVar`) define the inputs a user must supply at job creation time — types, defaults, descriptions, choices, sensitivity flags. Today, config vars are only surfaced in two places: (1) the `RunConfigDialog` at job launch, and (2) raw YAML editing. There is no dedicated view or editor for config vars in the flow editor.

This plan adds a **Config Panel** to the flow editor that lets users browse, add, edit, and delete config variables visually — without touching raw YAML. Changes are saved back to FLOW.yaml via round-trip YAML editing (ruamel.yaml), preserving formatting and comments.

### What "config files" means in this codebase

Stepwise flows do **not** use external config files (config.yaml, config.json). All configuration is declared inline in FLOW.yaml's `config:` block and parsed into `ConfigVar` objects. This plan surfaces that existing system. If external config file support is wanted later, it would be a separate feature built on the flow file tree infrastructure.

---

## Requirements

### R1: Display config variables in the flow editor
**Acceptance criteria:**
- When a flow has `config:` declarations, a "Config" tab/panel appears in the editor
- Each config var shows: name, type, default value, description, required/optional, options (for choice), sensitive flag
- Empty state shown when flow has no config vars
- Config panel is read-only by default, with an edit mode toggle

### R2: Add new config variables from the UI
**Acceptance criteria:**
- "Add variable" button opens an inline form
- Form fields: name (identifier validation), type (dropdown: str/text/number/bool/choice), default, description, required, options (for choice), sensitive
- Validation: name must be a valid Python identifier, no duplicate names, choice type requires options
- On save, variable is appended to the `config:` block in FLOW.yaml via round-trip YAML editing

### R3: Edit existing config variables
**Acceptance criteria:**
- Click a config var to enter edit mode for that variable
- All fields editable: type, default, description, required, options, sensitive
- Name is editable but warns about breaking existing `$job.{name}` references in steps
- Changes saved via round-trip YAML editing (preserves comments/formatting)

### R4: Delete config variables
**Acceptance criteria:**
- Delete button on each config var (with confirmation)
- Shows warning listing steps that reference `$job.{name}` as inputs
- Deletion removes the key from the `config:` block via round-trip YAML editing

### R5: Validate config changes before saving
**Acceptance criteria:**
- Client-side validation: identifier names, non-empty choice options, type consistency
- Server-side validation: full YAML re-parse via `load_workflow_yaml()` after patching (same pattern as `patch-step`)
- Validation errors displayed inline in the form

### R6: Config variable cross-references
**Acceptance criteria:**
- Config panel shows which steps reference each variable (via `$job.{name}` input bindings)
- Clicking a reference navigates to that step in the DAG or step inspector

---

## Assumptions (verified against codebase)

| Assumption | Verified in | Status |
|---|---|---|
| Config vars are declared in `config:` block within FLOW.yaml | `yaml_loader.py:1028-1069` (`_parse_config`) | Confirmed |
| ConfigVar has fields: name, type, default, required, description, example, options, sensitive | `models.py:304-349` (ConfigVar dataclass) | Confirmed |
| Valid types: str, text, number, bool, choice | `yaml_loader.py:1046-1051` (VALID_FIELD_TYPES check) | Confirmed |
| Config vars are surfaced as `config_vars` on `FlowDefinition` | `models.py:WorkflowDefinition.config_vars`, `types.ts:123` | Confirmed |
| Round-trip YAML editing uses ruamel.yaml | `server.py:2690-2716` (`_ruamel_load_and_patch`) | Confirmed |
| Existing patch pattern: load → mutate → dump → validate → atomic write | `server.py:2719-2747` (`patch_step`) | Confirmed |
| TypedField component renders typed form fields (used by RunConfigDialog) | `components/dag/TypedField.tsx`, `RunConfigDialog.tsx:24-31` | Confirmed |
| No external config files exist in any flow directory | Searched all flow dirs | Confirmed |
| `FlowDefinition.config_vars` is already in the TypeScript types | `types.ts:107-123` | Confirmed |
| Config vars map to `$job.*` input bindings in steps | `yaml_loader.py` input parsing, CLAUDE.md | Confirmed |

---

## Implementation Steps

### Step 1: Backend — Config patch endpoint

**File:** `src/stepwise/server.py`

Add a new endpoint `POST /api/flows/patch-config` that applies CRUD operations to the `config:` block using ruamel.yaml round-trip editing.

```
Request model (Pydantic):
  ConfigPatchRequest:
    flow_path: str
    action: "add" | "update" | "delete"
    name: str                          # config var name
    spec: dict | None                  # {type, default, description, required, options, sensitive, example}

Response: same as patch-step — {raw_yaml, flow, graph, errors}
```

Implementation pattern (mirrors `_ruamel_load_and_patch`):
1. Load YAML with ruamel.yaml (round-trip mode)
2. For `add`: validate name is unique, insert into `config:` mapping (create mapping if absent)
3. For `update`: validate name exists, replace spec fields
4. For `delete`: validate name exists, remove key from `config:`; remove empty `config:` block
5. Dump back to YAML string
6. Validate via `load_workflow_yaml()`
7. Atomic write (tmp → rename)
8. Return updated flow + graph

Also add `GET /api/flows/config-refs?flow_path=...&var_name=...` that returns a list of steps referencing `$job.{var_name}` — useful for the delete warning and cross-reference display.

### Step 2: Backend — Config reference scanner

**File:** `src/stepwise/server.py`

Add a helper `_find_config_refs(workflow: WorkflowDefinition, var_name: str) -> list[dict]` that scans all steps' `InputBinding` lists for `source_step == "$job"` and `source_field == var_name`. Returns `[{step_name, input_name}]`.

Wire this into:
- The `config-refs` endpoint (Step 1)
- The `patch-config` delete action (include refs in the response so the frontend can show them before confirming)

### Step 3: Frontend — API client functions

**File:** `web/src/lib/api.ts`

Add:
```typescript
export async function patchConfig(flowPath: string, action: "add" | "update" | "delete", name: string, spec?: Record<string, unknown>): Promise<ParseResult>
export async function fetchConfigRefs(flowPath: string, varName: string): Promise<{refs: {step_name: string, input_name: string}[]}>
```

### Step 4: Frontend — React Query hooks

**File:** `web/src/hooks/useEditor.ts`

Add:
```typescript
export function usePatchConfig()     // Mutation wrapping patchConfig()
export function useConfigRefs(flowPath: string, varName: string)  // Query wrapping fetchConfigRefs()
```

Invalidation: on success, invalidate `["localFlow", path]` and `["flowStats"]` (same as `usePatchStep`).

### Step 5: Frontend — ConfigPanel component

**File:** `web/src/components/editor/ConfigPanel.tsx` (new)

A panel component that displays and edits config variables. Layout:

```
┌─ Config Variables ──────────────────────────────────┐
│ [+ Add variable]                                     │
│                                                      │
│ ┌─ team_name ──────────────────────────────────────┐ │
│ │ Type: str    Default: "acme-eng"    Required: no │ │
│ │ Description: Team name for notifications         │ │
│ │ Used by: notify-team, generate-report            │ │
│ │                                    [Edit] [Delete]│ │
│ └──────────────────────────────────────────────────┘ │
│                                                      │
│ ┌─ reviewer_style ─────────────────────────────────┐ │
│ │ Type: choice   Options: thorough, quick          │ │
│ │ Default: thorough     Required: no               │ │
│ │ Used by: run-review                              │ │
│ │                                    [Edit] [Delete]│ │
│ └──────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

Sub-components:
- **ConfigVarCard** — displays one config var with name, type badge, default, description, references
- **ConfigVarForm** — inline add/edit form using TypedField-style inputs (name, type dropdown, default, description, required checkbox, options list, sensitive checkbox)

State management:
- `editingVar: string | null` — name of var being edited (null = view mode)
- `addingVar: boolean` — whether add form is open
- Form state local to the form component, submitted via `usePatchConfig` mutation

Validation (client-side, before submission):
- Name: must be a valid identifier (`/^[a-zA-Z_][a-zA-Z0-9_]*$/`), no duplicates
- Choice type: requires at least one option
- Type change: warn if default value becomes incompatible

### Step 6: Frontend — ConfigVarForm component

**File:** `web/src/components/editor/ConfigVarForm.tsx` (new)

Inline form for adding/editing a config variable. Fields:

| Field | Component | Validation |
|---|---|---|
| name | `<Input>` | identifier regex, unique check |
| type | `<Select>` from str/text/number/bool/choice | required |
| default | TypedField (type-aware) | type-compatible |
| description | `<Input>` | optional |
| required | `<Switch>` | — |
| example | `<Input>` | optional |
| options | comma-separated `<Input>` (shown only for choice) | non-empty for choice |
| sensitive | `<Switch>` | — |

Action buttons: Save / Cancel. Save calls `usePatchConfig` with action="add" or "update".

### Step 7: Frontend — Integrate ConfigPanel into EditorPage

**File:** `web/src/pages/EditorPage.tsx`

Add ConfigPanel as a tab option in the center pane (alongside "flow" DAG view and "source" YAML view):

- Add `"config"` to the `centerTab` state union type
- Add a tab button in the editor toolbar or tab bar: "Config" (with badge showing count of config vars)
- When `centerTab === "config"`, render `<ConfigPanel>` in the center pane
- Pass `parsedFlow.config_vars`, `flowPath`, and step reference data

Alternative integration: ConfigPanel as a collapsible section above the step list in the right sidebar (StepDefinitionPanel area). This keeps it visible alongside the DAG. Decision depends on screen real estate — tab approach is simpler to implement and doesn't crowd the sidebar.

**Recommended approach:** Tab in center pane. The config panel needs enough width for the form fields and the var cards, which fits better as a center pane than a narrow sidebar section.

### Step 8: Frontend — Delete confirmation with references

When the user clicks Delete on a config var:
1. Fetch references via `useConfigRefs(flowPath, varName)`
2. If references exist, show a confirmation dialog listing affected steps: "This variable is used by steps: notify-team (input: team_name), generate-report (input: team). Deleting it will break these input bindings. Continue?"
3. On confirm, call `usePatchConfig` with action="delete"

### Step 9: Frontend — Cross-reference links

In `ConfigVarCard`, display "Used by: step-a, step-b" as clickable links. Clicking a step name:
1. Switches center tab to "flow" (DAG view)
2. Sets `selectedStep` to the clicked step name
3. Opens StepDefinitionPanel for that step

This reuses existing `handleSelectStep()` in EditorPage.

### Step 10: Tests — Backend

**File:** `tests/test_config_patch.py` (new)

Test cases:
1. `test_add_config_var` — adds a new config var, verifies YAML round-trip, validates parsed result
2. `test_add_config_var_duplicate` — returns 409 for duplicate name
3. `test_add_config_var_invalid_name` — returns 400 for non-identifier names
4. `test_update_config_var` — changes type/default/description, verifies round-trip
5. `test_update_config_var_not_found` — returns 404
6. `test_delete_config_var` — removes var, verifies YAML
7. `test_delete_config_var_removes_empty_block` — config block removed when last var deleted
8. `test_config_refs` — verifies step reference scanning
9. `test_choice_requires_options` — validation for choice type without options
10. `test_round_trip_preserves_comments` — YAML comments survive config patching

```bash
uv run pytest tests/test_config_patch.py -v
```

### Step 11: Tests — Frontend

**File:** `web/src/components/editor/__tests__/ConfigPanel.test.tsx` (new)

Test cases:
1. Renders config vars from parsed flow
2. Empty state when no config vars
3. Add form opens and validates identifier names
4. Edit mode populates form with existing values
5. Delete shows confirmation with step references
6. Type-aware default field (number input for number type, etc.)
7. Choice type shows options field

```bash
cd web && npm run test -- ConfigPanel
```

---

## Testing Strategy

### Unit tests (backend)
```bash
# Run config patch tests
uv run pytest tests/test_config_patch.py -v

# Run all tests to verify no regressions
uv run pytest tests/ -x
```

### Unit tests (frontend)
```bash
# Run config panel tests
cd web && npm run test -- ConfigPanel

# Run all frontend tests
cd web && npm run test
```

### Integration testing (manual)
1. Start dev server: `uv run stepwise server start` + `cd web && npm run dev`
2. Open a flow with existing config vars (e.g., `welcome` flow)
3. Verify config panel displays all vars with correct types/defaults
4. Add a new config var → verify it appears in YAML source tab
5. Edit an existing var's type and default → verify round-trip preserves formatting
6. Delete a var that's referenced by steps → verify warning shows affected steps
7. Run the flow → verify RunConfigDialog still picks up updated config vars
8. Open a flow with no config vars → verify empty state with add button

### Lint
```bash
cd web && npm run lint
```

---

## File Change Summary

| File | Change |
|---|---|
| `src/stepwise/server.py` | Add `POST /api/flows/patch-config`, `GET /api/flows/config-refs`, helper `_find_config_refs()` |
| `web/src/lib/api.ts` | Add `patchConfig()`, `fetchConfigRefs()` |
| `web/src/hooks/useEditor.ts` | Add `usePatchConfig()`, `useConfigRefs()` |
| `web/src/components/editor/ConfigPanel.tsx` | New — config var list with add/edit/delete |
| `web/src/components/editor/ConfigVarForm.tsx` | New — inline form for config var CRUD |
| `web/src/pages/EditorPage.tsx` | Add "Config" center tab, wire ConfigPanel |
| `tests/test_config_patch.py` | New — backend tests for config patching |
| `web/src/components/editor/__tests__/ConfigPanel.test.tsx` | New — frontend component tests |

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| ruamel.yaml round-trip may reorder config keys | Use `CommentedMap` insertion to preserve order; test explicitly |
| Renaming a config var breaks `$job.{name}` references | Show warning with affected steps; optionally auto-update input bindings (stretch goal) |
| Config block doesn't exist yet in YAML | `patch-config` add action creates the `config:` block if absent |
| Concurrent edits (YAML editor + config panel) | Config panel reads from `parsedFlow` (same source of truth as YAML editor); mutations trigger re-parse which updates both views |

## Out of Scope

- External config files (config.yaml / config.json alongside FLOW.yaml) — the codebase doesn't use this pattern
- Config var inheritance or overrides across flows
- Config var validation beyond type checking (e.g., regex patterns, min/max)
- Auto-updating `$job.*` references when renaming a config var (show warning only)
