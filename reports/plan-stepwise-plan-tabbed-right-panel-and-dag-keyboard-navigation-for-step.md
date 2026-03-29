# Plan: Tabbed Right Panel & DAG Keyboard Navigation

## Overview

Two changes to the job detail page:

1. **DETAIL-03**: Replace the mutually exclusive conditional rendering of the right panel (StepDetailPanel / DataFlowPanel / job details) with a shadcn `Tabs` component. Three tabs: "Step", "Data Flow", "Job". Selection-driven auto-switching with manual override always available.

2. **DETAIL-05**: DAG keyboard navigation. j/k and arrow keys move step selection in topological order. Tab cycles. Enter opens detail. Escape clears. Visible focus ring.

Both features are scoped to `JobDetailPage.tsx` and `FlowDagView.tsx` with no new files required.

---

## Requirements

### DETAIL-03: Tabbed Right Panel

| # | Requirement | Acceptance Criteria |
|---|---|---|
| 1 | Three tabs: "Step", "Data Flow", "Job" | All three visible in tab bar when panel is open |
| 2 | Auto-switch on step click | Clicking a step node → "Step" tab active |
| 3 | Auto-switch on edge click | Clicking an edge label → "Data Flow" tab active |
| 4 | Auto-switch on Escape | Pressing Escape with selection → clears selection, switches to "Job" tab |
| 5 | Manual tab switch always works | Clicking any tab shows its content regardless of selection state |
| 6 | "Step" tab disabled when no step selected | Tab trigger disabled, grayed out |
| 7 | "Data Flow" tab disabled when no edge selected | Tab trigger disabled, grayed out |
| 8 | Mobile: Sheet drawer preserved | Mobile rendering still uses `<Sheet>` with tabbed content inside |
| 9 | Panel open/close unchanged | `rightPanelOpen` toggle and auto-open for terminal jobs still work |
| 10 | Expanded step overlay unchanged | The `<Sheet>` overlay for expanded StepDetailPanel remains as-is |

### DETAIL-05: DAG Keyboard Navigation

| # | Requirement | Acceptance Criteria |
|---|---|---|
| 1 | j / ArrowDown moves selection to next step (topological order) | From step N, selection moves to step N+1; wraps to first if at end |
| 2 | k / ArrowUp moves selection to previous step | From step N, selection moves to step N-1; wraps to last if at start |
| 3 | Tab cycles forward through steps | Same as j but using Tab key |
| 4 | Shift+Tab cycles backward | Same as k but using Shift+Tab |
| 5 | Enter opens detail panel | Sets `rightPanelOpen = true`, auto-switches to "Step" tab |
| 6 | Escape clears selection | Resets selection to null, switches to "Job" tab |
| 7 | Visible focus ring on selected step | Selected step has a distinct ring/outline in the DAG |
| 8 | No-op when DAG is not focused | Keys only activate when focus is within the DAG area or no editable element is focused |
| 9 | First press with no selection selects first step | j/k/ArrowDown/ArrowUp with null selection → select first step |

---

## Assumptions (Verified)

| Assumption | Verified In |
|---|---|
| shadcn `Tabs` component exists with `variant="line"` support | `web/src/components/ui/tabs.tsx` — exports `Tabs`, `TabsList`, `TabsTrigger`, `TabsContent` with line variant |
| Existing Tabs usage pattern (controlled, with disabled triggers) | `web/src/components/editor/RightSidebar.tsx:54-128` — proven pattern with `value`/`onValueChange` |
| Right panel content is three mutually exclusive branches | `JobDetailPage.tsx:525-649` — nested ternary: DataFlowPanel / StepDetailPanel / job details JSX |
| Selection state is `DagSelection` union type | `dag-layout.ts:10-15` — `{ kind: "step" } | { kind: "edge-field" } | { kind: "flow-input" } | { kind: "flow-output" } | null` |
| Only keyboard handler is Escape | `JobDetailPage.tsx:163-170` — single `useEffect` with global keydown listener |
| Topological order not precomputed as array | `dag-layout.ts` — Dagre computes layout positions but no exported topo-sorted step list; nodes can be sorted by `y` coordinate |
| StepNode already has `tabIndex={0}` and Enter/Space handlers | `StepNode.tsx:283-343` — `role="button"`, `tabIndex={0}`, `onKeyDown` for Enter/Space |
| `useHotkeys` hook exists for key sequence handling | `web/src/hooks/useHotkeys.ts` — supports single keys, respects editable targets |
| Job details content is inline JSX (not a component) | `JobDetailPage.tsx:543-648` — raw JSX block for stats, inputs, outputs |

---

## Implementation Steps

### Step 1: Extract JobDetailsPanel component

**File:** `web/src/pages/JobDetailPage.tsx`

Extract the inline job details JSX (lines 543–648) into a `JobDetailsPanel` component defined in the same file. This keeps the tab content clean and consistent with StepDetailPanel/DataFlowPanel as discrete components.

Props: `{ job, costData, latestRuns, normalizedOutputs, isTerminal, onClose }`.

Move the stats/inputs/outputs JSX verbatim into the new component. Replace the inline block with `<JobDetailsPanel ... />`.

### Step 2: Add tab state and auto-switch logic

**File:** `web/src/pages/JobDetailPage.tsx`

Add state:
```typescript
type RightPanelTab = "step" | "data-flow" | "job";
const [activeTab, setActiveTab] = useState<RightPanelTab>("job");
```

Add auto-switch effect that responds to `selection` changes:
```typescript
useEffect(() => {
  if (!selection) return; // don't auto-switch on clear
  if (selection.kind === "step" && !job?.workflow.steps[selection.stepName]) {
    // Sub-job step — show in Step tab via resolvedStep
    setActiveTab("step");
  } else if (selection.kind === "step" && job?.workflow.steps[selection.stepName]) {
    // Root workflow step — DataFlowPanel shows step inspector
    setActiveTab("data-flow");
  } else if (selection.kind === "edge-field" || selection.kind === "flow-input" || selection.kind === "flow-output") {
    setActiveTab("data-flow");
  }
}, [selection, job]);
```

Wait — re-reading the current logic more carefully:

- `isRootWorkflowStepSelection` (root workflow step) → DataFlowPanel renders a "step inspector" view
- `resolvedStep` (sub-job step or root step with run data) → StepDetailPanel

The current conditional at line 527 is: if `(isDataFlowSelection || isRootWorkflowStepSelection) && selection` → DataFlowPanel, else if `resolvedStep` → StepDetailPanel, else job details.

With tabs, the auto-switch should be:
- Clicking any step → "Step" tab (StepDetailPanel shows run details, which is what users primarily want)
- Clicking edge/flow-input/flow-output → "Data Flow" tab
- Escape → "Job" tab

The `isRootWorkflowStepSelection → DataFlowPanel` behavior is the current default but the spec says clicking a step should go to "Step" tab. The DataFlowPanel's step inspector is a secondary view accessible via "Data Flow" tab. This is an improvement — users clicking a step want run details, not just the step definition.

Revised auto-switch:
```typescript
useEffect(() => {
  if (!selection) return;
  if (selection.kind === "step") {
    setActiveTab("step");
  } else {
    setActiveTab("data-flow");
  }
}, [selection]);
```

Update Escape handler to also set tab:
```typescript
if (e.key === "Escape" && selection) {
  setSelection(null);
  setActiveTab("job");
}
```

### Step 3: Replace conditional panel rendering with Tabs

**File:** `web/src/pages/JobDetailPage.tsx` (lines 524–674)

Replace the nested ternary `panelContent` block with:

```tsx
const panelContent = showRightPanel ? (
  <Tabs
    value={activeTab}
    onValueChange={(v) => setActiveTab(v as RightPanelTab)}
    className="flex flex-col flex-1 min-h-0"
  >
    <div className="flex items-center justify-between border-b border-border bg-zinc-50/50 dark:bg-zinc-950/50 shrink-0">
      <TabsList variant="line" className="px-1">
        <TabsTrigger value="step" disabled={!resolvedStep}>
          Step
        </TabsTrigger>
        <TabsTrigger value="data-flow" disabled={!selection || selection.kind === "step"}>
          Data Flow
        </TabsTrigger>
        <TabsTrigger value="job">Job</TabsTrigger>
      </TabsList>
      <button
        onClick={() => { setSelection(null); setRightPanelOpen(false); }}
        className="text-zinc-600 hover:text-zinc-300 p-0.5 mr-2"
      >
        <PanelRightOpen className="w-3.5 h-3.5" />
      </button>
    </div>

    <TabsContent value="step" className="flex-1 min-h-0 overflow-hidden">
      {resolvedStep && (
        <StepDetailPanel
          jobId={resolvedStep.jobId}
          stepDef={resolvedStep.stepDef}
          onClose={() => setSelection(null)}
          onExpand={() => setExpandedStep(true)}
        />
      )}
    </TabsContent>

    <TabsContent value="data-flow" className="flex-1 min-h-0 overflow-hidden">
      {selection && (
        <DataFlowPanel
          selection={selection}
          job={job}
          latestRuns={latestRuns}
          outputs={normalizedOutputs}
          onClose={() => setSelection(null)}
        />
      )}
    </TabsContent>

    <TabsContent value="job" className="flex-1 min-h-0 overflow-y-auto">
      <JobDetailsPanel
        job={job}
        costData={costData}
        normalizedOutputs={normalizedOutputs}
        isTerminal={isTerminal}
      />
    </TabsContent>
  </Tabs>
) : null;
```

Key decisions:
- "Step" tab disabled when `!resolvedStep` (no step selected or selection can't resolve)
- "Data Flow" tab disabled when selection is null or is a step-kind (DataFlowPanel handles edge-field/flow-input/flow-output, but also works for root workflow steps — keep it enabled for step selections too since DataFlowPanel renders a step inspector for root steps)
- Actually, simplify: "Data Flow" disabled when `!selection` (it needs *some* selection to render). When a step is selected, both Step and Data Flow tabs are available — Step shows runs, Data Flow shows the step inspector/data bindings.
- "Job" tab always enabled
- Close button in the tab bar header (replaces per-panel close buttons)
- StepDetailPanel `onClose` prop still clears selection (needed for internal close actions)

**StepDetailPanel adjustment:** Remove its own header close button since the tab bar now handles panel chrome. Or keep it — the panel has its own header with step name. The close (X) button in StepDetailPanel should clear the selection (switching to Job tab via the auto-switch effect that fires when selection becomes null). Actually, the auto-switch only fires when selection is non-null. For the Escape path, we explicitly set "job" tab. For StepDetailPanel's onClose, add `setActiveTab("job")` to the handler:

```typescript
onClose={() => { setSelection(null); setActiveTab("job"); }}
```

### Step 4: Compute topological step order

**File:** `web/src/pages/JobDetailPage.tsx`

Add a `useMemo` that produces a topologically-sorted array of step names from the layout. The simplest approach: sort layout nodes by y-coordinate (Dagre positions them top-to-bottom in topological order).

```typescript
const topoStepNames = useMemo(() => {
  if (!job?.workflow?.steps) return [];
  // Use Dagre layout's y-coordinate for topological order
  // Import computeLayout or sort Object.keys by dependency depth
  // Simpler: use the layout nodes from FlowDagView
  // But layout is computed inside FlowDagView, not accessible here.
  // → Compute a simple topo sort inline.
}, [job?.workflow]);
```

Since the layout is computed inside FlowDagView and not exposed, we need a lightweight topo sort. Kahn's algorithm on the step dependency graph:

```typescript
const topoStepNames = useMemo(() => {
  if (!job?.workflow?.steps) return [];
  const steps = job.workflow.steps;
  const names = Object.keys(steps);

  // Build adjacency: dep → step (dep must come before step)
  const inDegree: Record<string, number> = {};
  const outEdges: Record<string, string[]> = {};
  for (const name of names) {
    inDegree[name] = 0;
    outEdges[name] = [];
  }
  for (const name of names) {
    const step = steps[name];
    const deps = new Set<string>();
    for (const input of step.inputs ?? []) {
      if (input.source_step && input.source_step !== "$job") deps.add(input.source_step);
    }
    for (const after of step.after ?? []) deps.add(after);
    for (const dep of deps) {
      if (dep in inDegree) {
        inDegree[name]++;
        outEdges[dep].push(name);
      }
    }
  }

  // Kahn's algorithm
  const queue = names.filter(n => inDegree[n] === 0);
  const sorted: string[] = [];
  while (queue.length > 0) {
    // Stable sort: alphabetical among same-rank nodes
    queue.sort();
    const node = queue.shift()!;
    sorted.push(node);
    for (const next of outEdges[node]) {
      inDegree[next]--;
      if (inDegree[next] === 0) queue.push(next);
    }
  }
  // Append any remaining (cycles — shouldn't happen in valid flows)
  for (const name of names) {
    if (!sorted.includes(name)) sorted.push(name);
  }
  return sorted;
}, [job?.workflow?.steps]);
```

### Step 5: Add keyboard navigation handler

**File:** `web/src/pages/JobDetailPage.tsx`

Replace the existing Escape-only handler (lines 163–170) with a comprehensive keyboard handler:

```typescript
useEffect(() => {
  const handler = (e: KeyboardEvent) => {
    // Skip when typing in an input/textarea/contenteditable
    const target = e.target as HTMLElement;
    if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable) {
      return;
    }

    const stepCount = topoStepNames.length;
    if (stepCount === 0) return;

    const currentIndex = selectedStep ? topoStepNames.indexOf(selectedStep) : -1;

    switch (e.key) {
      case "j":
      case "ArrowDown": {
        e.preventDefault();
        const next = currentIndex < 0 ? 0 : (currentIndex + 1) % stepCount;
        handleSelectStep(topoStepNames[next]);
        break;
      }
      case "k":
      case "ArrowUp": {
        e.preventDefault();
        const prev = currentIndex < 0 ? stepCount - 1 : (currentIndex - 1 + stepCount) % stepCount;
        handleSelectStep(topoStepNames[prev]);
        break;
      }
      case "Tab": {
        // Only handle Tab when a step is selected (avoid hijacking normal tab navigation)
        if (selectedStep) {
          e.preventDefault();
          const delta = e.shiftKey ? -1 : 1;
          const next = (currentIndex + delta + stepCount) % stepCount;
          handleSelectStep(topoStepNames[next]);
        }
        break;
      }
      case "Enter": {
        if (selectedStep) {
          e.preventDefault();
          setRightPanelOpen(true);
          setActiveTab("step");
        }
        break;
      }
      case "Escape": {
        if (selection) {
          setSelection(null);
          setActiveTab("job");
        }
        break;
      }
    }
  };

  window.addEventListener("keydown", handler);
  return () => window.removeEventListener("keydown", handler);
}, [selection, selectedStep, topoStepNames, handleSelectStep]);
```

### Step 6: Add visible focus ring to selected step

**File:** `web/src/components/dag/StepNode.tsx`

The `StepNode` component receives `isSelected` prop. Find the outer `<div>` that renders the step card and ensure the selected state has a visible focus ring. Look for existing selected styling and enhance it.

Currently, `isSelected` likely controls a border/background highlight. Add a prominent ring:

```typescript
// In StepNode's outer div className:
isSelected && "ring-2 ring-blue-500/60 dark:ring-blue-400/60"
```

This gives a visible blue focus ring consistent with the shadcn focus-visible pattern. The ring should be visible regardless of whether the step was selected via click or keyboard.

### Step 7: Scroll selected step into view

**File:** `web/src/components/dag/FlowDagView.tsx`

When keyboard navigation changes the selected step, the DAG viewport should scroll/pan to keep the selected step visible. FlowDagView already handles pan/zoom — add an effect that adjusts the viewport when `selectedStep` changes:

```typescript
useEffect(() => {
  if (!selectedStep) return;
  // Find the node position in layout
  const node = layout.nodes.find(n => n.id === selectedStep);
  if (!node) return;
  // Scroll the node into view within the DAG container
  // Use the existing pan/zoom transform to center on the node
  // or use a ref to the node's DOM element and scrollIntoView
}, [selectedStep, layout]);
```

The exact implementation depends on how FlowDagView handles its viewport transform. If it uses CSS transforms for pan/zoom, we need to adjust the transform. If it uses a scrollable container, `scrollIntoView` on the node element works. Use a `data-step-name` attribute on each StepNode wrapper for DOM lookup.

A simpler approach: add `ref` forwarding or a `data-step-name` attribute to each step node div, and call `element.scrollIntoView({ behavior: 'smooth', block: 'nearest' })` on selection change. This works well if the DAG container has overflow scroll.

### Step 8: Update keyboard shortcuts display

**File:** `web/src/components/layout/AppLayout.tsx`

Add the new DAG navigation shortcuts to the `SHORTCUTS` array so they appear in the keyboard shortcuts help dialog:

```typescript
{ keys: ["j / ↓"], description: "Next step" },
{ keys: ["k / ↑"], description: "Previous step" },
{ keys: ["Enter"], description: "Open step detail" },
{ keys: ["Escape"], description: "Clear selection" },
```

Only add these if the shortcuts dialog is contextual (shows different shortcuts per page). If it's global, add them under a "Job Detail" section heading.

---

## Testing Strategy

### Manual Testing

```bash
# Start dev servers
cd /home/zack/work/stepwise && uv run stepwise server start
cd /home/zack/work/stepwise/web && npm run dev
```

**DETAIL-03 (Tabs):**
1. Open a job with multiple steps → verify three tabs visible: Step, Data Flow, Job
2. Click a step node → verify "Step" tab auto-activates, StepDetailPanel content shown
3. Click an edge label → verify "Data Flow" tab auto-activates, DataFlowPanel content shown
4. Press Escape → verify selection clears, "Job" tab shown
5. Manually click "Job" tab while step is selected → verify job details shown, step still selected
6. Manually click "Data Flow" tab while step is selected → verify step inspector shown in DataFlowPanel
7. With no step selected, verify "Step" tab is disabled (grayed, not clickable)
8. On mobile viewport (< 768px), verify Sheet drawer contains tabbed panel
9. Click expand button on StepDetailPanel → verify expanded Sheet overlay still works
10. For terminal jobs (completed/failed), verify panel auto-opens on load showing "Job" tab

**DETAIL-05 (Keyboard Nav):**
1. On job detail page, press `j` → first step selected, visible focus ring
2. Press `j` again → second step selected
3. Press `k` → back to first step
4. Press `ArrowDown`/`ArrowUp` → same behavior as j/k
5. At last step, press `j` → wraps to first step
6. At first step, press `k` → wraps to last step
7. Press `Enter` with step selected → right panel opens, "Step" tab active
8. Press `Escape` → selection cleared, "Job" tab
9. Press `Tab` with step selected → next step
10. Press `Shift+Tab` → previous step
11. Click into a text input on the page, press `j` → no step navigation (input handler skips)
12. Verify topological order matches DAG visual top-to-bottom order

### Automated Tests

```bash
cd /home/zack/work/stepwise/web && npm run test
```

**New test cases** (add to existing test file or create `web/src/pages/__tests__/JobDetailPage.test.tsx`):

1. **Tab rendering**: Mock job data, render JobDetailPage, verify three tab triggers present
2. **Tab auto-switch on step selection**: Simulate step click, verify active tab is "step"
3. **Tab auto-switch on edge selection**: Simulate edge click, verify active tab is "data-flow"
4. **Tab disable states**: No selection → "Step" and "Data Flow" disabled; step selected → only "Data Flow" may remain enabled
5. **Keyboard j/k navigation**: Fire keydown events, verify selection state changes in topological order
6. **Keyboard Enter**: Fire Enter with step selected, verify panel opens
7. **Keyboard Escape**: Fire Escape with selection, verify selection cleared
8. **Keyboard skip in inputs**: Focus a text input, fire `j`, verify no selection change
9. **Topo sort correctness**: Given a known DAG (A→B, A→C, B→D), verify sort is [A, B, C, D] or [A, C, B, D] (valid topo orders)

```bash
# Run specific test file
cd /home/zack/work/stepwise/web && npx vitest run src/pages/__tests__/JobDetailPage.test.tsx

# Run all tests
cd /home/zack/work/stepwise/web && npm run test

# Lint
cd /home/zack/work/stepwise/web && npm run lint
```

---

## File Change Summary

| File | Changes |
|---|---|
| `web/src/pages/JobDetailPage.tsx` | Add `activeTab` state, `topoStepNames` memo, keyboard handler, extract JobDetailsPanel, replace conditional rendering with Tabs |
| `web/src/components/dag/StepNode.tsx` | Add/enhance focus ring styling for `isSelected` state |
| `web/src/components/dag/FlowDagView.tsx` | Add scroll-into-view effect for selected step |
| `web/src/components/layout/AppLayout.tsx` | Add DAG keyboard shortcuts to SHORTCUTS array |
| `web/src/pages/__tests__/JobDetailPage.test.tsx` | New test file for tab behavior and keyboard navigation |

No new dependencies. No new files beyond the test file. All changes use existing shadcn Tabs component and patterns already established in the codebase.
