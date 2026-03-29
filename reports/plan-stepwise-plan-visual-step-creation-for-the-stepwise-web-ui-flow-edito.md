# Plan: Visual Step Creation for the Stepwise Web UI Flow Editor

## Overview

Add a floating "+" button to the flow editor's DAG view that opens a visual step type picker. Selecting a type creates a minimal YAML step definition via the existing server API, updates the DAG, and auto-selects the new step for inspection in the right panel. The button is owned by EditorPage (not FlowDagView) so it works even on empty flows.

## Requirements

### R1: Floating "Add Step" Button on DAG Canvas
- A "+" button is visible over the DAG canvas area when the editor is in "Flow" (DAG) view
- Button is positioned at bottom-right of the DAG container, rendered by EditorPage as an overlay — not inside FlowDagView (see Architecture for rationale)
- Button uses the existing shadcn/ui Button component with the same frosted-glass styling as existing DAG controls
- **Acceptance criteria:** Button visible on desktop and mobile when `centerTab === "flow"`, including when the flow has zero steps. Not visible in source/YAML view or prompt editor view.

### R2: Step Type Picker Dialog
- Clicking the "+" button opens a dialog with step type options: Script, LLM, Agent, Human/External, Poll
- Each option shows the executor icon (from `executor-utils.tsx`) and a brief description
- Selecting a type pre-fills the executor, then asks for a step name
- **Acceptance criteria:** Dialog opens on button click, shows 5 executor types with icons, selecting one transitions to name input, cancellation closes dialog cleanly

### R3: Step Name Input with Validation
- User enters a step name after selecting a type
- Validates: not empty, not duplicate of existing step names (exact match)
- No format enforcement — the server accepts any valid string (underscores, mixed case, etc.)
- **Acceptance criteria:** Empty name disables submit, duplicate name shows inline error, non-duplicate submits successfully regardless of casing or separator style

### R4: YAML Generation and DAG Sync
- Submitting flushes any pending autosave timer, then calls the `useAddStep` mutation → `POST /api/flows/add-step`
- Server generates minimal YAML, returns updated `raw_yaml` + `flow` via `ParseResult`
- `applyVisualResult()` syncs both YAML editor and parsed flow state
- DAG re-renders with the new disconnected node
- **Acceptance criteria:** New step appears in DAG within 500ms, YAML editor reflects the addition, no parse errors, no stale autosave overwrites the new step

### R5: Auto-Select New Step for Inspection
- After successful creation, the new step is auto-selected via `handleSelectStep(name)` (sets both `selectedStep` and `stepContext`)
- Right panel opens showing the StepDefinitionPanel for the new step
- StepDefinitionPanel is read-only — user inspects the generated defaults, then edits via YAML editor, chat, or prompt editor (via `onViewSource`)
- **Acceptance criteria:** Right panel opens with new step selected, `stepContext` is also updated so chat targets the new step

### R6: Editor-Only Scope
- The "+" button only appears in the editor page, not in job detail, flows page, or any other FlowDagView consumer
- **Acceptance criteria:** Button absent from JobDetailPage, FlowsPage, and any other page using FlowDagView

## Assumptions (Verified Against Code)

1. **`AddStepDialog` exists but is unused** — `web/src/components/editor/AddStepDialog.tsx` is fully implemented (90 lines, name input + executor select) but is not imported anywhere. Grep for `AddStepDialog` returns only its own file. We replace it with `StepPalette`.

2. **`useAddStep` hook exists but is incomplete** — `web/src/hooks/useEditor.ts:103-115`: bare `useMutation` with no `onSuccess`/`onError` and no cache invalidation. Compare with `useSaveFlow` (lines 56-70) which calls `queryClient.invalidateQueries` for both `["localFlows"]` and `["localFlow"]` keys plus `toast.success`/`toast.error`. Both `toast` (line 2) and `useQueryClient` (line 1) are already imported. Similarly `useDeleteStep` (lines 117-127) is also missing cache invalidation — noted but not in scope for this plan.

3. **Server add-step endpoint generates YAML per type** — `src/stepwise/server.py:2771-2779`: branches on `req.executor` for `"script"` → `{"run": "echo hello", "outputs": ["result"]}`, `"llm"/"agent"` → `{"executor": req.executor, "prompt": "TODO", "outputs": ["result"]}`, `"external"` → same pattern, `else` → `{"executor": req.executor, "outputs": ["result"]}`. No `"poll"` branch exists — falls through to `else`, producing a step without `check_command` or `interval_seconds`.

4. **`applyVisualResult` is the established mutation callback** — `web/src/pages/EditorPage.tsx:241-254`: sets `yamlContent` from `result.raw_yaml`, sets `parsedFlow` from `result.flow`, clears `parseErrors`. Used by both `handlePatchStep` (line 261) and `handleDeleteStep` (line 274).

5. **FlowDagView returns early on empty flows** — `web/src/components/dag/FlowDagView.tsx:446-452`: `if (Object.keys(workflow.steps).length === 0)` returns a centered "No steps in flow" div. Any UI placed inside FlowDagView will not render for empty flows. The "+" button must be rendered outside FlowDagView.

6. **StepDefinitionPanel is read-only** — Script command at line 304 rendered via `<CodeBlock>`. Poll fields at line 437 rendered via `<CodeBlock>` and `<KV>`. Inputs/outputs at line 481 rendered via `<span>` badges. Only interactive elements: `onViewSource(field)` (opens prompt editor for prompt/system/command) and `onViewFile(path)`. No inline editing.

7. **`handleSelectStep` sets both state variables** — `web/src/pages/EditorPage.tsx:396-399`: `setSelectedStep(stepName); if (stepName) setStepContext(stepName);`. Any code selecting a step must call this function, not raw `setSelectedStep`.

8. **Autosave debounce uses `parseTimerRef`** — `web/src/pages/EditorPage.tsx:212`: `const parseTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);`. The `handleYamlChange` callback (lines 216-234) calls `clearTimeout(parseTimerRef.current)` then sets a 500ms `setTimeout` that fires `parseMutation.mutate` → on success → `saveMutationRef.current.mutate`. A visual mutation must clear this timer to prevent stale overwrites.

9. **DAG controls layout** — Bottom-left (`absolute bottom-3 left-3 z-10`, lines 650-711): follow-flow checkbox, critical path checkbox, zoom controls (+/−/reset/%). Bottom-right (`absolute bottom-3 right-3 z-10`, lines 714-741): share/clipboard + download buttons.

10. **FlowDagView props interface has no editor-specific props** — `web/src/components/dag/FlowDagView.tsx:57-72`: props are `workflow`, `runs`, `jobTree`, `expandedSteps`, `onToggleExpand`, `selectedStep`, `onSelectStep`, `onNavigateSubJob?`, `onFulfillWatch?`, `isFulfilling?`, `selection?`, `onSelectDataFlow?`, `flowName?`, `jobStatus?`. No `onAddStep`. We will NOT add one — the button lives in EditorPage.

11. **The server accepts any step name** — `server.py:2768`: only checks `req.name in data["steps"]` for exact duplicate. `test_visual_editing_api.py:117` uses `new_step` (underscores). No format constraint.

12. **Poll fulfillment triggers on any JSON dict from stdout** — `engine.py:2699-2724`: if `check_command` exits 0 and stdout parses as a JSON dict via `json.loads`, the poll is fulfilled with that dict as the artifact. `echo '{}'` would fulfill immediately with `{}` — wrong because `outputs: ["result"]` expects a `result` key.

13. **Existing test coverage for add-step** — `tests/test_visual_editing_api.py:116-157`: `TestAddStep` has 5 tests covering script, llm, external, duplicate (409), and nonexistent flow (404). No poll test.

14. **Web test conventions** — `web/src/components/editor/__tests__/StepDefinitionPanel.test.tsx` shows the pattern: `createWrapper()` for `QueryClientProvider`, `renderWithQuery()` helper, `makeStepDef()` factory, `vi.fn()` for callbacks, `screen.getByText()`/`fireEvent.click()` assertions. Vitest + React Testing Library + jsdom.

## Out of Scope

- **Drag-and-drop step positioning** — Steps are auto-positioned by Dagre layout
- **Visual edge creation** — Dependencies are edited via YAML, chat, or prompt editor
- **Step templates/presets** — Beyond the 5 executor types, no custom templates
- **Undo/redo** — Not part of this feature
- **For-each step creation** — Complex nested flow setup, handled separately
- **Renaming steps** — Separate feature with dependency rewiring
- **Making StepDefinitionPanel editable** — Separate feature; the panel is currently an inspector and this plan does not change that
- **Fixing `useDeleteStep` cache invalidation** — Same gap as `useAddStep` but not in scope

## Architecture

### Component Hierarchy (after changes)

```
EditorPage
├── [DAG container div] (relative positioning context — line 530)
│   ├── FlowDagView (UNCHANGED — no new props)
│   └── [floating "+" Button]  ← NEW: absolute overlay, visible even on empty flows
├── StepPalette (dialog)       ← NEW: step type picker + name input
└── StepDefinitionPanel        (existing, auto-selected after creation for inspection)
```

### Data Flow

```
User clicks "+"
  → setShowStepPalette(true) — state in EditorPage
  → User picks type → enters name
  → handleAddStep(name, executor):
      1. clearTimeout(parseTimerRef.current)  — kill pending autosave
      2. addStepMutation.mutate({ flowPath, name, executor })
  → Server: YAML insert → parse → return ParseResult
  → onSuccess:
      1. applyVisualResult(result)  — sets yamlContent + parsedFlow
      2. handleSelectStep(name)     — sets selectedStep + stepContext
      3. setShowStepPalette(false)  — closes dialog
  → DAG re-renders with new node (via parsedFlow change)
  → Right panel opens showing read-only inspector
  → Cache invalidated (localFlows + localFlow queries)
```

### Key Design Decisions

1. **"+" button owned by EditorPage, not FlowDagView** — FlowDagView returns early on empty flows (line 446), so a button inside it vanishes when most useful. The button is an absolute-positioned overlay in EditorPage's DAG container div (line 530). This avoids adding editor props to a shared component used by FlowsPage (lines 483, 554, 589) and JobDetailPage (line 716).

2. **Two-phase dialog** (type picker → name input) — more discoverable than a dropdown. Cards show icons and descriptions.

3. **Replace AddStepDialog entirely** — unused dead code; StepPalette is a superset.

4. **Autosave race protection** — `clearTimeout(parseTimerRef.current)` before mutation. `applyVisualResult` sets `yamlContent` to the server's authoritative YAML.

5. **No name format enforcement** — the server accepts any string. UI only validates non-empty and non-duplicate.

6. **Poll template uses `exit 1`** — prevents accidental self-fulfillment. `echo '{}'` would fulfill immediately with empty dict.

## Implementation Steps

### Step 1: Fix `useAddStep` hook — add cache invalidation and error handling

**File:** `web/src/hooks/useEditor.ts`
**Lines to change:** 103-115
**Depends on:** nothing
**Produces:** working mutation with cache invalidation + error toast

The current code (lines 103-115) is a bare `useMutation` with no side effects. Replace with the `useSaveFlow` pattern (lines 56-70). Both `useQueryClient` (line 1) and `toast` (line 2) are already imported.

**Exact change — replace lines 103-115:**
```typescript
// BEFORE:
export function useAddStep() {
  return useMutation({
    mutationFn: ({
      flowPath,
      name,
      executor,
    }: {
      flowPath: string;
      name: string;
      executor: string;
    }) => api.addStep(flowPath, name, executor),
  });
}

// AFTER:
export function useAddStep() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      flowPath,
      name,
      executor,
    }: {
      flowPath: string;
      name: string;
      executor: string;
    }) => api.addStep(flowPath, name, executor),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["localFlows"] });
      queryClient.invalidateQueries({ queryKey: ["localFlow"] });
    },
    onError: (error) => {
      toast.error("Failed to add step", { description: error.message });
    },
  });
}
```

**Verification:** `cd web && npx tsc --noEmit` (type-check), `cd web && npm run test` (no regressions).

---

### Step 2: Add poll executor template to server

**File:** `src/stepwise/server.py`
**Lines to change:** 2774-2779
**Depends on:** nothing
**Produces:** poll-specific YAML template

The current `else` fallback (line 2779) produces `{"executor": req.executor, "outputs": ["result"]}` for poll — missing `check_command` and `interval_seconds`. Insert a `poll` branch before the `else`.

**Exact change — insert after the `external` branch (after line 2778), before `else` (line 2779):**
```python
    # BEFORE (line 2779):
    else:
        new_step = {"executor": req.executor, "outputs": ["result"]}

    # AFTER:
    elif req.executor == "poll":
        new_step = {
            "executor": "poll",
            "check_command": "exit 1  # replace with your check command",
            "interval_seconds": 30,
            "prompt": "Waiting for condition...",
            "outputs": ["result"],
        }
    else:
        new_step = {"executor": req.executor, "outputs": ["result"]}
```

**Why `exit 1`:** The engine (`engine.py:2699-2724`) fulfills a poll when `check_command` exits 0 and stdout is a non-empty JSON dict. `echo '{}'` exits 0 and produces `{}` → immediate fulfillment with empty artifact (no `result` key). `exit 1` keeps the step pending until edited.

**Verification:** `uv run pytest tests/test_visual_editing_api.py -v` (existing tests pass + new poll test from Step 3).

---

### Step 3: Add poll test to existing test file

**File:** `tests/test_visual_editing_api.py`
**Insert after:** line 157 (end of `TestAddStep` class)
**Depends on:** Step 2
**Produces:** test coverage for poll template

Insert new test methods into the existing `TestAddStep` class (lines 116-157):

```python
    def test_add_poll_step(self, client):
        resp = client.post(
            "/api/flows/add-step",
            json={"flow_path": FLOW_PATH, "name": "wait_for_it", "executor": "poll"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "wait_for_it" in data["raw_yaml"]
        assert "check_command" in data["raw_yaml"]
        assert "interval_seconds" in data["raw_yaml"]
        step_names = [n["id"] for n in data["graph"]["nodes"]]
        assert "wait_for_it" in step_names

    def test_add_agent_step(self, client):
        resp = client.post(
            "/api/flows/add-step",
            json={"flow_path": FLOW_PATH, "name": "plan", "executor": "agent"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "plan" in data["raw_yaml"]
        assert "prompt" in data["raw_yaml"]
```

**Verification:** `uv run pytest tests/test_visual_editing_api.py::TestAddStep -v`

---

### Step 4: Create StepPalette component

**File:** `web/src/components/editor/StepPalette.tsx` (new)
**Depends on:** nothing (can be built in parallel with Steps 1-3)
**Produces:** two-phase dialog component

**Props interface:**
```typescript
interface StepPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  existingStepNames: string[];
  onAdd: (name: string, executor: string) => void;
  isPending: boolean;
}
```

**Internal state:**
```typescript
const [selectedType, setSelectedType] = useState<string | null>(null); // phase 1 → phase 2
const [name, setName] = useState("");
```

**Phase 1 — type picker grid:**
- 5 cards in `grid grid-cols-2 gap-3`, fifth card in `col-span-2` centered
- Each card is a `button` with: `executorIcon(type)` (from `@/lib/executor-utils`), label, 1-line description
- Card border accent from `EXEC_TYPE_COLORS` (import from `StepDefinitionPanel.tsx:30-37` or duplicate the 6-entry record — it's small enough to inline)
- Clicking sets `selectedType` → transitions to phase 2

**Phase 2 — name input:**
- Back button (arrow left) → clears `selectedType`, returns to phase 1
- Shows selected type icon + label as a header badge
- Name `<Input>` with `autoFocus`
- Duplicate detection: `const isDuplicate = existingStepNames.includes(name.trim())`
- If duplicate: red text below input: `A step named "${name.trim()}" already exists`
- Submit disabled if `!name.trim() || isDuplicate || isPending`
- Submit calls `onAdd(name.trim(), selectedType)`

**Reset on close:** `onOpenChange` wrapper clears `selectedType` and `name` when closing.

**Imports:**
```typescript
import { useState } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ArrowLeft } from "lucide-react";
import { executorIcon } from "@/lib/executor-utils";
```

**Patterns followed:**
- Same dialog structure as `AddStepDialog.tsx` (lines 44-89) and `CreateFlowDialog` in FlowsPage
- `sm:max-w-[420px]` dialog width
- Tailwind only, dark mode via `dark:` prefix
- No kebab-case enforcement — only empty + duplicate checks

**Verification:** `cd web && npx tsc --noEmit` + StepPalette unit tests (Step 6).

---

### Step 5: Wire up EditorPage — button overlay + palette + handler

**File:** `web/src/pages/EditorPage.tsx`
**Depends on:** Steps 1, 4
**Produces:** working end-to-end flow

#### 5a: Add imports (line 20 area)

Add to the existing `useEditor` import block (lines 13-21):
```typescript
// Add useAddStep to the import from "@/hooks/useEditor":
import {
  useLocalFlows,
  useLocalFlow,
  useParseYaml,
  useSaveFlow,
  usePatchStep,
  useDeleteStep,
  useFlowFiles,
  useAddStep,           // ← add
} from "@/hooks/useEditor";
```

Add new imports:
```typescript
import { StepPalette } from "@/components/editor/StepPalette";
import { Plus } from "lucide-react";
```

#### 5b: Add state and mutation (after line 238, near other mutations)

Insert after the existing `deleteStepMutation` declaration (line 239):
```typescript
const addStepMutation = useAddStep();
const [showStepPalette, setShowStepPalette] = useState(false);
```

#### 5c: Add handler (after line 280, near `handleDeleteStep`)

```typescript
const handleAddStep = useCallback(
  (name: string, executor: string) => {
    if (!selectedFlow?.path) return;
    // Flush pending autosave to prevent stale YAML overwriting the new step.
    // parseTimerRef (line 212) holds the 500ms debounce timer from handleYamlChange.
    clearTimeout(parseTimerRef.current);
    addStepMutation.mutate(
      { flowPath: selectedFlow.path, name, executor },
      {
        onSuccess: (result) => {
          applyVisualResult(result);
          // Use handleSelectStep (line 396) — NOT raw setSelectedStep —
          // to set both selectedStep and stepContext for chat targeting.
          handleSelectStep(name);
          setShowStepPalette(false);
        },
      }
    );
  },
  [selectedFlow?.path, addStepMutation, applyVisualResult, handleSelectStep]
);
```

#### 5d: Render "+" button as overlay in DAG container (inside the div at line 530)

The DAG container is `<div className="flex-1 min-w-0 min-h-0 relative">` (line 530). The FlowDagView is rendered inside conditionally (lines 546-555). Insert the "+" button as a sibling, inside the same `relative` div, just before the closing `</div>` at line 570:

```tsx
{/* Add step button — absolute overlay on DAG area, renders even when FlowDagView shows empty state */}
{centerTab === "flow" && !editingPrompt && (
  <button
    onClick={() => setShowStepPalette(true)}
    className="absolute bottom-14 right-3 z-20 flex items-center gap-1.5 bg-white/80 dark:bg-zinc-900/80 border border-zinc-300/50 dark:border-zinc-700/50 rounded-md px-2.5 py-1.5 text-zinc-400 hover:text-foreground text-xs shadow-sm hover:bg-white dark:hover:bg-zinc-800 transition-colors min-h-[44px] md:min-h-0"
    title="Add step"
  >
    <Plus className="w-3.5 h-3.5" />
    Add step
  </button>
)}
```

**Positioning: `bottom-14 right-3`** — sits above FlowDagView's share/download controls (`bottom-3 right-3`, lines 714-741) with comfortable spacing. `z-20` is above FlowDagView internals (`z-10`) but below dialogs (`z-50`). Frosted-glass styling matches the DAG controls pattern.

**Renders when:**
- `centerTab === "flow"` — only in DAG view, not YAML editor or file viewer
- `!editingPrompt` — hidden when full-screen prompt editor is active
- No dependency on FlowDagView rendering — works on empty flows

#### 5e: Render StepPalette dialog (after the run config dialog, around line 783)

```tsx
<StepPalette
  open={showStepPalette}
  onOpenChange={setShowStepPalette}
  existingStepNames={Object.keys(parsedFlow?.steps ?? {})}
  onAdd={handleAddStep}
  isPending={addStepMutation.isPending}
/>
```

**Verification:** Manual test of the full flow (see Testing section).

---

### Step 6: Write StepPalette unit tests

**File:** `web/src/components/editor/__tests__/StepPalette.test.tsx` (new)
**Depends on:** Step 4
**Produces:** component test coverage

Follow the pattern from `StepDefinitionPanel.test.tsx`: `createWrapper()`, `renderWithQuery()`, `vi.fn()`, `screen` queries.

```typescript
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import type { ReactNode } from "react";
import { StepPalette } from "../StepPalette";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function renderWithQuery(ui: React.ReactElement) {
  return render(ui, { wrapper: createWrapper() });
}

describe("StepPalette", () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    existingStepNames: ["fetch", "analyze"],
    onAdd: vi.fn(),
    isPending: false,
  };

  it("renders all 5 executor type cards in phase 1", () => {
    renderWithQuery(<StepPalette {...defaultProps} />);
    expect(screen.getByText("Script")).toBeInTheDocument();
    expect(screen.getByText("LLM")).toBeInTheDocument();
    expect(screen.getByText("Agent")).toBeInTheDocument();
    expect(screen.getByText("Human")).toBeInTheDocument();
    expect(screen.getByText("Poll")).toBeInTheDocument();
  });

  it("transitions to name input after selecting a type", () => {
    renderWithQuery(<StepPalette {...defaultProps} />);
    fireEvent.click(screen.getByText("Script"));
    expect(screen.getByPlaceholderText(/step name/i)).toBeInTheDocument();
  });

  it("back button returns to type selection", () => {
    renderWithQuery(<StepPalette {...defaultProps} />);
    fireEvent.click(screen.getByText("Script"));
    // Find and click the back button
    const backButton = screen.getByTitle(/back/i);
    fireEvent.click(backButton);
    // Should be back on type grid
    expect(screen.getByText("LLM")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/step name/i)).not.toBeInTheDocument();
  });

  it("shows error for duplicate step name", () => {
    renderWithQuery(<StepPalette {...defaultProps} />);
    fireEvent.click(screen.getByText("Script"));
    const input = screen.getByPlaceholderText(/step name/i);
    fireEvent.change(input, { target: { value: "fetch" } });
    expect(screen.getByText(/already exists/i)).toBeInTheDocument();
  });

  it("disables submit when name is empty", () => {
    renderWithQuery(<StepPalette {...defaultProps} />);
    fireEvent.click(screen.getByText("Script"));
    const submitButton = screen.getByRole("button", { name: /add step|create/i });
    expect(submitButton).toBeDisabled();
  });

  it("calls onAdd with correct name and executor on submit", () => {
    const onAdd = vi.fn();
    renderWithQuery(<StepPalette {...defaultProps} onAdd={onAdd} />);
    fireEvent.click(screen.getByText("Agent"));
    const input = screen.getByPlaceholderText(/step name/i);
    fireEvent.change(input, { target: { value: "plan-impl" } });
    const submitButton = screen.getByRole("button", { name: /add step|create/i });
    fireEvent.click(submitButton);
    expect(onAdd).toHaveBeenCalledWith("plan-impl", "agent");
  });

  it("resets state when dialog closes and reopens", () => {
    const { rerender } = renderWithQuery(<StepPalette {...defaultProps} />);
    fireEvent.click(screen.getByText("Script"));
    // Close
    rerender(<StepPalette {...defaultProps} open={false} />);
    // Reopen
    rerender(<StepPalette {...defaultProps} open={true} />);
    // Should be back on type grid, not name input
    expect(screen.getByText("Script")).toBeInTheDocument();
    expect(screen.getByText("LLM")).toBeInTheDocument();
  });

  it("does not render when open is false", () => {
    renderWithQuery(<StepPalette {...defaultProps} open={false} />);
    expect(screen.queryByText("Script")).not.toBeInTheDocument();
  });
});
```

**Verification:** `cd web && npm run test -- --run StepPalette`

---

### Step 7: Delete AddStepDialog.tsx

**File:** `web/src/components/editor/AddStepDialog.tsx`
**Depends on:** Steps 4-5 complete (StepPalette is the replacement)
**Produces:** dead code removal

Delete this file. Confirmed not imported anywhere (grep returns only the file itself).

**Verification:** `cd web && npx tsc --noEmit` (no broken imports), `cd web && npm run test`.

---

### Step 8: Integration test — empty flow creation

**File:** `tests/test_visual_editing_api.py`
**Depends on:** Step 2
**Produces:** coverage for adding a step when the flow has no existing steps

This tests the server-side behavior that supports the critical empty-flow UX. The `+` button renders in EditorPage regardless of FlowDagView's empty state, and submits to this endpoint. Add to `TestAddStep`:

```python
    def test_add_step_to_empty_flow(self, client, project_dir):
        """Adding a step to a flow with zero steps should succeed."""
        empty_flow = project_dir / "flows" / "empty" / "FLOW.yaml"
        empty_flow.parent.mkdir(parents=True, exist_ok=True)
        empty_flow.write_text("name: empty-flow\nsteps: {}\n")
        resp = client.post(
            "/api/flows/add-step",
            json={"flow_path": "flows/empty/FLOW.yaml", "name": "first", "executor": "script"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "first" in data["raw_yaml"]
        assert len(data["graph"]["nodes"]) == 1
```

**Verification:** `uv run pytest tests/test_visual_editing_api.py::TestAddStep::test_add_step_to_empty_flow -v`

---

## Testing Strategy

### Test Matrix

| Test | Type | File | What it validates | Command |
|------|------|------|-------------------|---------|
| Poll template fields | Server unit | `tests/test_visual_editing_api.py` | `check_command`, `interval_seconds` in YAML | `uv run pytest tests/test_visual_editing_api.py::TestAddStep::test_add_poll_step -v` |
| Agent template fields | Server unit | `tests/test_visual_editing_api.py` | `prompt` key present in YAML | `uv run pytest tests/test_visual_editing_api.py::TestAddStep::test_add_agent_step -v` |
| Add to empty flow | Server unit | `tests/test_visual_editing_api.py` | Step added when `steps: {}` | `uv run pytest tests/test_visual_editing_api.py::TestAddStep::test_add_step_to_empty_flow -v` |
| Type card rendering | Component | `web/.../StepPalette.test.tsx` | All 5 types shown | `cd web && npm run test -- --run StepPalette` |
| Phase transition | Component | `web/.../StepPalette.test.tsx` | Type click → name input | same |
| Back button | Component | `web/.../StepPalette.test.tsx` | Returns to type grid | same |
| Duplicate rejection | Component | `web/.../StepPalette.test.tsx` | Error shown, submit disabled | same |
| Empty name | Component | `web/.../StepPalette.test.tsx` | Submit disabled | same |
| onAdd callback | Component | `web/.../StepPalette.test.tsx` | Correct (name, executor) args | same |
| State reset on close | Component | `web/.../StepPalette.test.tsx` | Phase 1 on reopen | same |

### Manual Test Cases

| # | Scenario | Steps | Expected |
|---|----------|-------|----------|
| M1 | Happy path | Open editor → "Flow" tab → click "+" → pick Script → type "fetch-data" → Create | Step in DAG + YAML, right panel shows inspector, chat targets "fetch-data" |
| M2 | All 5 types | Repeat M1 for LLM, Agent, Human, Poll | Each creates valid YAML with type-appropriate fields (prompt, check_command, etc.) |
| M3 | Empty flow | Create a new flow (0 steps) → click "+" → add script step | Button visible over "No steps" message. Step appears, DAG renders. |
| M4 | Duplicate name | Create "foo" → click "+" → type "foo" | Inline error: "already exists", Create disabled |
| M5 | Autosave race | Type in YAML editor → within 500ms click "+" and create step | New step not overwritten by debounced save |
| M6 | Chat context | Create step "bar" | `stepContext` updated — chat sidebar targets "bar" |
| M7 | Cancel | Open palette → pick type → close dialog | No step created, no state change |
| M8 | Non-editor isolation | Open JobDetailPage with a job → inspect DAG | No "+" button visible |
| M9 | Non-editor isolation | Open FlowsPage → look at flow DAGs | No "+" button visible |
| M10 | Error handling | Stop stepwise server → try adding step | Error toast appears |
| M11 | Mobile | Resize to mobile viewport → use "+" button | Button tappable, dialog usable |

### Regression
```bash
uv run pytest tests/ -x                     # all Python tests
cd web && npm run test                       # all Vitest tests
cd web && npm run lint                       # ESLint
cd web && npx tsc --noEmit                   # TypeScript type check
```

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| New node appears disconnected (no edges) | Low — expected for new step with no inputs | User inspects in right panel, then uses YAML/chat to add connections. |
| "+" button overlaps FlowDagView's share controls | Medium — both target bottom-right corner | Offset to `bottom-14 right-3` (56px above share controls). Verified by visual inspection during M1. |
| Autosave timer fires between mutation call and response | Medium — stale YAML overwrites new step | `clearTimeout(parseTimerRef.current)` before mutation (Step 5c). `applyVisualResult` sets authoritative YAML from server. |
| Cache stale after creation | Medium — flows list shows wrong step count | `useAddStep` invalidates `localFlows`/`localFlow` on success (Step 1). |
| User confused inspector is read-only | Low — expects inline editing | `onViewSource` links in panel open prompt editor for prompt/command. YAML editor always available. Chat targets new step via `stepContext`. |
| Poll template `exit 1` confuses users | Low — non-obvious placeholder | Comment `# replace with your check command` in the generated YAML explains intent. |

## Critique Response Log

| # | Severity | Issue | Resolution |
|---|----------|-------|------------|
| 1 | critical | FlowDagView returns early on empty flows — "+" button inside it won't render | Moved "+" button to EditorPage as absolute overlay on DAG container div (line 530). FlowDagView unchanged. Added empty-flow server test (Step 8). |
| 2 | critical | Autosave race: 500ms debounce timer can overwrite new step | `handleAddStep` calls `clearTimeout(parseTimerRef.current)` before mutation (Step 5c). Manual test M5 covers this. |
| 3 | major | StepDefinitionPanel is read-only, not an editor | Updated R5, assumptions, and all text to accurately describe panel as inspector. Removed claims of inline editing. |
| 4 | major | `handleAddStep` only called `setSelectedStep`, missing `stepContext` | Changed to `handleSelectStep(name)` which sets both (Step 5c). Manual test M6 validates chat targeting. |
| 5 | major | Kebab-case is an invented constraint | Removed all format enforcement. Validation is only non-empty + non-duplicate exact match (Step 4). |
| 6 | major | `useAddStep` missing cache invalidation and error handling | Step 1 adds `onSuccess` (invalidate queries) and `onError` (toast). Exact before/after code provided. |
| 7 | major | `echo '{}'` poll template self-fulfills immediately | Changed to `exit 1  # replace with your check command` (Step 2). Engine fulfillment logic cited. |
| 8 | major | Testing plan targeted wrong file and missed real gaps | Poll/agent/empty-flow tests in existing `test_visual_editing_api.py`. 8 component tests with full implementations. 11 manual test scenarios covering race conditions, chat sync, isolation, error handling. |
| 9 | minor | Bottom controls layout was wrong (zoom left, share right) | Corrected in assumptions. Button is EditorPage overlay at `bottom-14 right-3`, above share controls. |
