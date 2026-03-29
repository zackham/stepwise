# Plan: Two Web UI Refactoring Tasks (CROSS-04, CROSS-09)

## Overview

Two independent refactoring tasks for the stepwise web UI:

1. **CROSS-04**: Extract `useDagCamera` hook from `FlowDagView.tsx` (currently 1161 lines). Move ~250 lines of camera management (refs, mouse/touch/wheel handlers, RAF animation loop, DagCamera interaction, zoom/follow state, fitToView) into `web/src/hooks/useDagCamera.ts`. FlowDagView shrinks to ~900 lines.

2. **CROSS-09**: Replace `useState` for `selection`, `activeTab`, `rightPanelOpen` in `JobDetailPage.tsx` with TanStack Router search params (`?step=name&tab=step&panel=open`). Enables URL-shareable deep links to specific step views.

---

## CROSS-04: Extract useDagCamera Hook

### Requirements

- **R1**: New hook `useDagCamera.ts` encapsulates all camera state and interaction handlers.
- **R2**: FlowDagView passes `containerRef`, `canvasRef`, `inputPanelRef`, `edgeTooltipRef`, layout, runs data, and follow-flow preferences to the hook. The hook returns event handlers and state.
- **R3**: No behavior change — pan, zoom, touch, pinch, follow-flow, fit-to-view, keyboard-nav scroll-into-view all work identically.
- **R4**: FlowDagView.tsx shrinks by ~250 lines.

### Acceptance Criteria

- [ ] `web/src/hooks/useDagCamera.ts` exists and exports `useDagCamera`
- [ ] FlowDagView imports and calls `useDagCamera`, no longer contains camera logic inline
- [ ] All existing DAG interaction tests pass (`npm run test`)
- [ ] Manual: pan, zoom (wheel + pinch), follow-flow toggle, fit-to-view reset, keyboard step nav scrolling all work

### Assumptions (verified against code)

1. **Camera state is fully internal to FlowDagView** — no camera refs/state leak to parent components. Verified: `FlowDagViewProps` has no camera-related props. The `transformRef`, `cameraRef`, `followAnimRef`, `lastFrameTimeRef`, `isDraggingRef`, `didDragRef`, `dragStart`, `touchStartRef`, `pinchStartRef`, `hasCenteredRef` are all local refs.

2. **`applyTransform` touches DOM refs** — it writes to `canvasRef.current.style.transform` and counter-scales `inputPanelRef` and `edgeTooltipRef`. These refs must be passed into the hook.

3. **`followFlow` and `zoomDisplay` are useState** — these are the only React state variables owned by camera logic. `followFlow` is read by the animation loop and by the follow-flow checkbox in the JSX. `zoomDisplay` drives the zoom percentage display.

4. **`showCriticalPath` is NOT camera logic** — it's visualization state that stays in FlowDagView.

5. **`activeStepInfo` and `activeRects` computations feed the camera** — they depend on `latestRuns`, `jobTree`, `layout`, and `selectedStep`. These are computed in FlowDagView and passed to the hook as inputs.

### Extraction Boundary

**Moves to `useDagCamera`** (lines from FlowDagView.tsx):

| Lines | What |
|---|---|
| 95-109 | Refs: `transformRef`, `isDraggingRef`, `didDragRef`, `dragStart`, `touchStartRef`, `pinchStartRef`, `zoomDisplay`, `hasCenteredRef`, `followFlow`, `followAnimRef`, `cameraRef`, `lastFrameTimeRef` |
| 296-303 | Layout change effect (clear camera velocities) |
| 305-319 | `applyTransform` callback |
| 322-377 | `initView` / `fitToView` callback + centering useLayoutEffect |
| 399-426 | `measuredPanelHeight` state + ResizeObserver |
| 428-498 | `activeStepInfo` memo, `activeRects` memo, feed-active-rects effect |
| 500-568 | Animation loop effect, pan-to-selected effect |
| 573-596 | `handleWheel` |
| 598-646 | `handleMouseDown`, `handleMouseMove`, `handleMouseUp`, `handleClickCapture` |
| 648-743 | `handleTouchStart`, `handleTouchMove`, `handleTouchEnd` |

**Stays in FlowDagView**:
- Props interface and component signature
- Layout computation (`rawLayout`, `layout`, `useLayoutTransition`)
- `latestRuns` memo, `criticalPath` memo
- `subJobMap`, `subFlowDefs`, `maxAttemptsMap` memos
- `shareState` + `captureDAG` (image export)
- `hoveredLabel` state + handlers
- `selectedLabel` memo, `handleClickLabel`
- All JSX rendering

### Hook Interface

```typescript
interface UseDagCameraOptions {
  containerRef: RefObject<HTMLDivElement | null>;
  canvasRef: RefObject<HTMLDivElement | null>;
  inputPanelRef: RefObject<HTMLDivElement | null>;
  edgeTooltipRef: RefObject<HTMLDivElement | null>;
  layout: HierarchicalDagLayout;
  rawLayout: HierarchicalDagLayout;
  runs: StepRun[];
  jobTree: JobTreeNode | null;
  workflow: FlowDefinition;
  selectedStep: string | null;
}

interface UseDagCameraReturn {
  // State
  followFlow: boolean;
  setFollowFlow: (v: boolean) => void;
  zoomDisplay: number;
  transformRef: MutableRefObject<{ x: number; y: number; scale: number }>;

  // Event handlers (attach to container div)
  handleWheel: (e: React.WheelEvent) => void;
  handleMouseDown: (e: React.MouseEvent) => void;
  handleMouseMove: (e: React.MouseEvent) => void;
  handleMouseUp: () => void;
  handleClickCapture: (e: React.MouseEvent) => void;
  handleTouchStart: (e: React.TouchEvent) => void;
  handleTouchMove: (e: React.TouchEvent) => void;
  handleTouchEnd: (e: React.TouchEvent) => void;

  // Actions
  fitToView: () => void;
  initView: () => void;
}
```

### Implementation Steps

**Step 1**: Create `web/src/hooks/useDagCamera.ts`
- Define `UseDagCameraOptions` and `UseDagCameraReturn` interfaces
- Move all ref declarations (`transformRef`, `cameraRef`, `isDraggingRef`, `didDragRef`, `dragStart`, `touchStartRef`, `pinchStartRef`, `hasCenteredRef`, `followAnimRef`, `lastFrameTimeRef`)
- Move `followFlow`/`setFollowFlow` and `zoomDisplay`/`setZoomDisplay` useState
- Move `measuredPanelHeight` state + ResizeObserver effect
- Move `applyTransform` callback
- Move `initView` / `fitToView` callback + centering useLayoutEffect
- Move layout-change velocity-clearing effect
- Move `activeStepInfo` memo, `activeRects` memo, feed-active-rects effect
- Move animation loop effect
- Move pan-to-selected effect
- Move all event handlers (`handleWheel`, `handleMouseDown/Move/Up`, `handleClickCapture`, `handleTouchStart/Move/End`)
- Return the interface above

**Step 2**: Update `FlowDagView.tsx`
- Import `useDagCamera` from `@/hooks/useDagCamera`
- Remove all moved code
- Call `useDagCamera({ containerRef, canvasRef, inputPanelRef, edgeTooltipRef, layout, rawLayout, runs, jobTree, workflow, selectedStep })`
- Destructure returned values and wire into JSX (event handlers on container div, `followFlow`/`zoomDisplay` in controls overlay, `transformRef.current.scale` for counter-scale overlays, `fitToView` for reset button)
- Keep `rawLayout` variable since `useLayoutTransition` needs both

**Step 3**: Verify
- `cd web && npm run test` — all existing tests pass
- `cd web && npm run lint` — no lint errors
- Manual smoke test of DAG interactions

### Testing Strategy

```bash
cd web && npm run test          # existing vitest suite
cd web && npm run lint          # eslint check
```

No new test file needed — this is a pure extraction refactor. The hook's behavior is exercised through FlowDagView's existing integration in tests and manual interaction. If FlowDagView has component tests that render it, they continue to pass unchanged since the external API (props) doesn't change.

---

## CROSS-09: URL-Based State for Selections

### Requirements

- **R1**: `selectedStep` (via `selection`), `activeTab`, and `rightPanelOpen` are driven by URL search params on the `/jobs/$jobId` route.
- **R2**: URL format: `?step=step-name&tab=step|data-flow|job&panel=open`. All params optional; absence = default state.
- **R3**: Browser back/forward navigates between selection states.
- **R4**: Sharing a URL with `?step=analyze&tab=step` opens that step's detail panel.
- **R5**: Existing internal navigation (clicking steps, keyboard nav, auto-select suspended) updates the URL without full page reload.

### Acceptance Criteria

- [ ] Route `/jobs/$jobId` has `validateSearch` defining `step`, `tab`, `panel` params
- [ ] JobDetailPage reads selection state from search params, not useState
- [ ] Clicking a step updates URL to `?step=step-name&tab=step`
- [ ] Keyboard nav (j/k/Tab) updates URL
- [ ] Escape clears `step` from URL
- [ ] Right panel toggle updates `panel` param
- [ ] Browser back/forward navigates selection history
- [ ] Auto-select suspended step updates URL
- [ ] Data-flow selection (edge clicks) reflected in URL (stretch — can use `df=from:to:field` format or skip for v1)
- [ ] All existing tests pass

### Assumptions (verified against code)

1. **TanStack Router search params already in use** — `jobsRoute` and `flowsRoute` both use `validateSearch`. Pattern: define a type, write a validator function, use `useSearch({ from: "/route" })` to read, `navigate({ search: ... })` to write.

2. **`jobDetailRoute` already inherits `validateSearch: validateJobsSearch`** — for the `q`/`status`/`range` params used by JobList. New params must be added alongside these, not replace them.

3. **`selection` state is a DagSelection union** — `null | { kind: "step", stepName } | { kind: "edge-field", fromStep, toStep, fieldName } | { kind: "flow-input", ... } | { kind: "flow-output", ... }`. For URL encoding, only `step` kind maps cleanly. Edge-field and flow-input/output can be a stretch goal.

4. **`activeTab` auto-switches on selection change** — the `useEffect` at line 281-288 sets tab based on selection kind. This logic stays but reads from URL.

5. **`rightPanelOpen` has tri-state init** — `null` (unset) → auto-open for terminal jobs. URL `panel=open` maps to `true`, absence maps to the auto-detect behavior.

6. **JobList search params (`q`, `status`, `range`) must be preserved** — when navigating to a step, the existing search params from the sidebar filters must not be lost.

### URL Schema

```
/jobs/:jobId?step=step-name&tab=step&panel=open
```

| Param | Values | Default |
|---|---|---|
| `step` | Step name string | (none — no selection) |
| `tab` | `step`, `data-flow`, `job` | `job` (or auto-derived from selection) |
| `panel` | `open` | (auto-detect based on job status) |
| `q` | (existing) search query | |
| `status` | (existing) status filter | |
| `range` | (existing) range filter | |

### Implementation Steps

**Step 1**: Update route definition in `web/src/router.tsx`
- Extend `JobsRouteSearch` type (or create a new `JobDetailSearch` type) with optional `step?: string`, `tab?: "step" | "data-flow" | "job"`, `panel?: "open"`
- Add validation for new params in `validateSearch` for `jobDetailRoute`
- Keep existing `q`/`status`/`range` params intact

```typescript
type JobDetailSearch = JobsRouteSearch & {
  step?: string;
  tab?: "step" | "data-flow" | "job";
  panel?: "open";
};

function validateJobDetailSearch(search: Record<string, unknown>): JobDetailSearch {
  const base = validateJobsSearch(search);
  const TAB_VALUES = new Set(["step", "data-flow", "job"]);
  return {
    ...base,
    step: typeof search.step === "string" && search.step ? search.step : undefined,
    tab: typeof search.tab === "string" && TAB_VALUES.has(search.tab)
      ? search.tab as JobDetailSearch["tab"]
      : undefined,
    panel: search.panel === "open" ? "open" : undefined,
  };
}
```

Update `jobDetailRoute` to use `validateSearch: validateJobDetailSearch`.

**Step 2**: Update `JobDetailPage.tsx` — read state from URL
- Import `useSearch` from `@tanstack/react-router`
- Read `{ step, tab, panel }` from `useSearch({ from: "/jobs/$jobId" })`
- Derive `selection` from `step` param: `step ? { kind: "step", stepName: step } : null`
- Derive `activeTab` from `tab` param (with fallback: if `step` is set and `tab` is absent, default to `"step"`)
- Derive `rightPanelOpen` from `panel` param (with auto-detect for terminal jobs when absent)
- Remove the three `useState` calls for `selection`, `activeTab`, `rightPanelOpen`
- Remove the `useEffect` that auto-switches tab on selection change (now derived from URL)
- Remove the `useEffect` that resets state on `jobId` change (URL params naturally reset)

**Step 3**: Update `JobDetailPage.tsx` — write state to URL
- Create a helper `updateSearch` that uses `navigate({ search: (prev) => ({ ...prev, ...updates }), replace: true })` for in-page state changes (step selection, tab switch) and non-replace for meaningful navigation events
- Replace `setSelection(...)` calls with `navigate({ search: ... })`:
  - `handleSelectStep(name)` → `navigate({ search: (prev) => ({ ...prev, step: name, tab: "step" }) })`
  - `handleSelectStep(null)` → `navigate({ search: (prev) => ({ ...prev, step: undefined, tab: "job" }) })`
  - `handleSelectDataFlow(sel)` → for edge-field selections, either encode or keep as local state for v1
  - Tab switch → `navigate({ search: (prev) => ({ ...prev, tab: newTab }) })`
  - Panel close → `navigate({ search: (prev) => ({ ...prev, panel: undefined, step: undefined }) })`
  - Panel open → `navigate({ search: (prev) => ({ ...prev, panel: "open" }) })`
- Use `replace: true` for tab switches and minor state changes to avoid polluting browser history
- Use default (push) for step selection changes so back button works

**Step 4**: Update `useAutoSelectSuspended` hook
- Currently calls `onSelectStep(stepName)`. This will now trigger a navigate. No change needed to the hook itself — only to the `handleSelectStep` callback it receives, which is already being updated in Step 3.

**Step 5**: Update keyboard navigation effect
- The keyboard handler at lines 335-391 calls `handleSelectStep(...)`, `setRightPanelOpen(true)`, `setActiveTab("step")`, `setSelection(null)`, `setActiveTab("job")`. Replace all with navigate calls.
- Use `replace: true` for keyboard nav to avoid flooding history.

**Step 6**: Handle data-flow selection (v1 decision)
- Data-flow selections (`edge-field`, `flow-input`, `flow-output`) have complex multi-field identity that's awkward in URLs.
- **V1 approach**: Keep `selection` for data-flow kinds as local state alongside URL-driven step selection. Only `step` kind is URL-encoded.
- The `selection` state becomes a computed value: if URL has `step`, that's the selection. If local data-flow state is set, that overrides. This hybrid approach means step selections are shareable via URL, and data-flow selections are transient.

**Step 7**: Verify and test

### Testing Strategy

```bash
cd web && npm run test          # existing vitest suite
cd web && npm run lint          # eslint check
```

Manual testing checklist:
1. Navigate to `/jobs/<id>` — no search params, default view
2. Click a step — URL updates to `?step=step-name&tab=step`
3. Press Escape — `step` param removed from URL
4. Use j/k to navigate steps — URL updates with `replace`
5. Press browser Back — returns to previous step selection
6. Share URL with `?step=analyze&tab=step` — opens directly to that step
7. Click "Details" button — `panel=open` appears in URL
8. Switch tabs — `tab` param updates
9. Job list filters (`q`, `status`, `range`) persist across step selections
10. Auto-select suspended step updates URL correctly

---

## Ordering

These tasks are independent and can be done in parallel. Neither touches the other's primary files:
- CROSS-04: `FlowDagView.tsx` + new `useDagCamera.ts`
- CROSS-09: `JobDetailPage.tsx` + `router.tsx`

If done sequentially, CROSS-04 first is slightly preferable since it reduces FlowDagView complexity, making any future changes to the DAG view cleaner.
