# Plan: Inline Tab Views for Job Detail Page

## Overview

Replace the separate-page routes for Events, Timeline, and Tree (`/jobs/$jobId/events`, `/jobs/$jobId/timeline`, `/jobs/$jobId/tree`) with inline tabs in the main content pane of `JobDetailPage`. The DAG view becomes one of four tabs (DAG | Events | Timeline | Tree) that swap the center panel content while preserving the left sidebar, right detail panel, header, and all job context. Old route URLs redirect (client-side) to the new tab-based URLs for backward compatibility.

## Requirements

| # | Requirement | Acceptance Criteria |
|---|---|---|
| R1 | Tab bar in main content area | A horizontal tab bar with "DAG", "Events", "Timeline", "Tree" tabs renders between the job controls/error banner and the content area. DAG is the default/active tab when navigating to `/jobs/$jobId`. The active tab has an underline indicator. |
| R2 | Tab content swaps center pane only | Switching tabs replaces only the visible center content area. The left sidebar (job list), right detail panel (step/data-flow/job tabs), breadcrumb, job header, and controls remain visible and functional on all tabs. Verifiable: every tab shows the same breadcrumb, header, and controls as DAG. |
| R3 | URL reflects active tab with history entries | Active tab is encoded as `?view=events\|timeline\|tree` (omitted = DAG). Each user-initiated tab switch pushes a new history entry (`replace: false`). Browser back/forward navigates between tabs. Internal cleanup navigations use `replace: true`. |
| R4 | Backward-compatible client-side redirects | Visiting `/jobs/$jobId/events` redirects client-side (TanStack Router `beforeLoad`, not HTTP 301) to `/jobs/$jobId?view=events`. Same for `/timeline` and `/tree`. |
| R5 | Events tab renders EventLog inline | The Events tab renders the `EventLog` component with the current job's ID. Internal EventLog state (filters, search, expanded events, scroll position) is preserved when switching tabs and back — verified by: set a filter, switch to DAG, switch back, filter is still active. |
| R6 | Timeline tab renders TimelineView inline | The Timeline tab renders `TimelineView` with `job`, `runs`, and `onSelectStep`. Clicking a step bar fires the shared `handleSelectStep()`, opening the right panel's Step tab. |
| R7 | Tree tab renders JobTreeView inline | The Tree tab renders `JobTreeView`. Clicking "Open job" navigates to that sub-job's detail page (DAG default view). Tree expansion state is preserved across tab switches. |
| R8 | DAG state preserved across tab switches | Camera position, zoom level, follow-flow toggle, and critical-path toggle in FlowDagView are not reset when switching tabs. Verified by: zoom/pan on DAG, switch to Events, switch back — same viewport. |
| R9 | Mobile responsive | On mobile, tab bar scrolls horizontally (`overflow-x-auto`). Right panel uses `MobileFullScreen` overlay with context-aware title: shows step name when a step is selected, "Details" otherwise. |
| R10 | Keyboard shortcuts scoped correctly | DAG step navigation keys (j/k/ArrowUp/ArrowDown/Tab/Enter) only fire when DAG tab is active. Escape fires on all tabs to dismiss step selection, close right panel, and clear data flow selection. |
| R11 | Single navigation surface for views | The tab bar is the sole control for switching between DAG/Events/Timeline/Tree. The header's existing Events/Timeline/Tree `<Link>` buttons are removed. The Details button is preserved as the explicit right-panel toggle. |
| R12 | Invalid `view` param degrades gracefully | If URL contains `?view=bogus`, the `validateJobDetailSearch` function strips it (returns `undefined`), and the DAG tab shows. No crash, no blank screen. |
| R13 | Page title reflects active view | Browser tab title includes the view name when not on DAG: e.g., "Events — JobName — Stepwise". DAG (default) keeps current title format: "JobName — Stepwise". |
| R14 | Job switch cleans stale params | Clicking a different job in the sidebar preserves `view` and list filter params (`q`, `status`, `range`), but clears job-specific params (`step`, `tab`, `panel`) to prevent stale right-panel state for the new job. |

## Assumptions

| # | Assumption | Verification |
|---|---|---|
| A1 | `EventLog` only needs a `jobId` prop | Confirmed: `EventLogProps` at `EventLog.tsx:29-30` — `{ jobId: string }`. |
| A2 | `TimelineView` needs `job`, `runs`, and optional `onSelectStep` | Confirmed: `TimelineViewProps` at `TimelineView.tsx:7-11` — `{ job: Job; runs: StepRun[]; onSelectStep?: (stepName: string) => void }`. |
| A3 | `JobTreeView` needs `jobId` and `onNavigateToJob` | Confirmed: `JobTreeViewProps` at `JobTreeView.tsx:15-18` — `{ jobId: string; onNavigateToJob: (jobId: string) => void }`. |
| A4 | shadcn/ui `Tabs` supports the `line` variant | Confirmed: `tabs.tsx:24-37` — `variant: { default, line }`. Right panel uses `variant="line"` at `JobDetailPage.tsx:788`. |
| A5 | Header nav icons are `<Link>` components to separate routes | Confirmed: `JobDetailPage.tsx:673-696` (desktop) and `571-597` (mobile) — `<Link to="/jobs/$jobId/events">`, etc. |
| A6 | `JobDetailSearch` uses `validateSearch` for URL param management | Confirmed: `router.tsx:55-71` — `JobDetailSearch` type + `validateJobDetailSearch` validator. |
| A7 | `JobDetailPage` already fetches data Timeline needs | Confirmed: `JobDetailPage.tsx:213-216` — `useJob(jobId)` and `useRuns(jobId)`. |
| A8 | The separate page components are thin wrappers | Confirmed: `JobEventsPage.tsx` (27 lines), `JobTreePage.tsx` (33 lines), `JobTimelinePage.tsx` (73 lines). |
| A9 | Right panel uses CSS-class hiding (not unmounting) for inactive tabs | Confirmed: `JobDetailPage.tsx:813-816` — `activeTab !== "step" && "hidden"`. Same pattern for main content tabs. |
| A10 | `FlowDagView` has component-local state that would be lost on unmount | Confirmed: `useDagCamera.ts:58-60` — `followFlow`, `hasCenteredRef`, `zoomDisplay`; `FlowDagView.tsx:95` — `showCriticalPath`. |
| A11 | `EventLog` has component-local state that would be lost on unmount | Confirmed: `EventLog.tsx:114-121` — `autoScroll`, `activeFilters`, `expandedEvents`, `useLogSearch`. |
| A12 | `JobTreeView` has component-local expansion state | Confirmed: `JobTreeView.tsx:29` — `useState(depth < 2)` per `TreeNode`. |
| A13 | `useAutoSelectSuspended` fires globally | Confirmed: `useAutoSelectSuspended.ts:40-42` — calls `onSelectStep` unconditionally. At `JobDetailPage.tsx:288`. |
| A14 | `JobControls` shows step-specific actions when `selectedStep` is set | Confirmed: `JobControls.tsx:226` — "Restart {selectedStep}" button visible on all views. |
| A15 | Sidebar navigation preserves all search params via `search: true` | Confirmed: `JobDetailPage.tsx:506`. |
| A16 | AppLayout title logic doesn't reference sub-route paths | Confirmed: `AppLayout.tsx:228-231` — uses `isJobsActive && detailJobId`, doesn't distinguish `/events` etc. |
| A17 | No references to old sub-routes outside files being modified/deleted | Confirmed via grep: only `router.tsx`, `JobDetailPage.tsx`, and the three page files contain `/jobs/$jobId/(events|timeline|tree)`. |

## Out of Scope

- **Changing the right detail panel** (Step/Data Flow/Job tabs) — stays as-is.
- **Adding new tabs** beyond DAG/Events/Timeline/Tree.
- **Changing EventLog, TimelineView, or JobTreeView component internals.**
- **Server-side or Python changes** — purely frontend.
- **Lazy-loading tab queries** — `EventLog` mounts eagerly (fires `useEvents(jobId)` immediately). React Query caches it; events are lightweight. This is the acceptable trade-off for preserving component-local state across tab switches.
- **HTTP-level redirects for old routes** — `beforeLoad` redirects are client-side only. Server-side redirects are a separate concern if needed later.

## Architecture

### URL Scheme

Current:
```
/jobs/$jobId                    → DAG view
/jobs/$jobId/events             → Events page (full takeover)
/jobs/$jobId/timeline           → Timeline page (full takeover)
/jobs/$jobId/tree               → Tree page (full takeover)
```

New:
```
/jobs/$jobId                    → DAG view (default, view param omitted)
/jobs/$jobId?view=events        → Events tab
/jobs/$jobId?view=timeline      → Timeline tab
/jobs/$jobId?view=tree          → Tree tab
/jobs/$jobId/events             → client-side redirect → /jobs/$jobId?view=events
/jobs/$jobId/timeline           → client-side redirect → /jobs/$jobId?view=timeline
/jobs/$jobId/tree               → client-side redirect → /jobs/$jobId?view=tree
```

### History Model

| Action | History behavior | Rationale |
|---|---|---|
| User clicks a tab | `replace: false` (push) | Back/forward navigates between tabs |
| Escape clears selection | `replace: true` | Cleanup, not a navigation the user would want to "undo" |
| Auto-derived tab changes | `replace: true` | Internal state normalization |
| Job switch via sidebar | `replace: false` (push) | New job = new navigation |

### Tab Content Rendering Strategy: CSS Hiding

All four tab contents are **mounted simultaneously** and hidden via CSS class (`hidden` / `display:none`) when inactive. This is the same pattern the right panel already uses for its Step/Data Flow/Job tabs (`JobDetailPage.tsx:813-816`).

**Why not conditional mount/unmount:**
- `FlowDagView` has rich internal state: camera position (`useDagCamera.ts:52-62`), follow-flow toggle (`useDagCamera.ts:60`), critical-path toggle (`FlowDagView.tsx:95`), drag state, animation frames. Unmounting destroys all of this.
- `EventLog` has filter state (`EventLog.tsx:115-117`), expanded events (`EventLog.tsx:118`), search state (`EventLog.tsx:121`). Unmounting resets the user's filter/search context.
- `JobTreeView`'s `TreeNode` components store expansion state locally (`JobTreeView.tsx:29`). Unmounting collapses everything.

**Trade-off:** All four components mount on page load, meaning `EventLog` fires `useEvents(jobId)` immediately. Acceptable because React Query caches it (one fetch per job load), events are lightweight, and the alternative (hoisting all view-local state into JobDetailPage) would add significant complexity.

### Component Structure (after change)

```
AppLayout
└── JobDetailPage
    ├── Left Sidebar (JobList) — unchanged
    ├── Center Panel
    │   ├── Breadcrumb — unchanged
    │   ├── Job Header (name, status, duration, cost)
    │   │   └── Events/Timeline/Tree links REMOVED
    │   │   └── Details button KEPT (right panel toggle)
    │   ├── JobControls — unchanged
    │   ├── Error Banner — unchanged
    │   ├── ── Main Content Tab Bar ── [DAG] [Events] [Timeline] [Tree]
    │   └── Tab Content (flex-1, overflow-hidden)
    │       ├── DAG: FlowDagView        (CSS-hidden when inactive)
    │       ├── Events: EventLog        (CSS-hidden when inactive)
    │       ├── Timeline: TimelineView  (CSS-hidden when inactive)
    │       └── Tree: JobTreeView       (CSS-hidden when inactive)
    └── Right Sidebar (Step/DataFlow/Job tabs) — unchanged
```

### Search Param Extension

```typescript
const JOB_DETAIL_VIEW_VALUES = new Set(["events", "timeline", "tree"]);

export type JobDetailSearch = JobsRouteSearch & {
  step?: string;
  tab?: "step" | "data-flow" | "job";
  panel?: "open";
  view?: "events" | "timeline" | "tree";  // NEW — omitted = DAG
};

function validateJobDetailSearch(search: Record<string, unknown>): JobDetailSearch {
  const base = validateJobsSearch(search);
  return {
    ...base,
    step: typeof search.step === "string" && search.step ? search.step : undefined,
    tab: typeof search.tab === "string" && JOB_DETAIL_TAB_VALUES.has(search.tab)
      ? search.tab as JobDetailSearch["tab"]
      : undefined,
    panel: search.panel === "open" ? "open" : undefined,
    view: typeof search.view === "string" && JOB_DETAIL_VIEW_VALUES.has(search.view)
      ? search.view as JobDetailSearch["view"]
      : undefined,  // Invalid/unknown values silently fall back to DAG
  };
}
```

### Keyboard Shortcut Scoping

The keyboard handler (`JobDetailPage.tsx:352-414`) is split:
- **DAG-only keys** (j/k/ArrowUp/ArrowDown/Tab/Enter): early return if `activeView !== "dag"`.
- **Global keys** (Escape): fires on all views — clears step selection, closes right panel, clears data flow selection.

### Cross-Cutting Behaviors (Intentional Design Decisions)

**`useAutoSelectSuspended` remains global.** When an external step suspends, the user needs to know immediately regardless of which tab they're on. The hook opens the right panel with step detail. This is correct product behavior: suspended external steps are urgent action items.

**`selectedStep` persists across tab switches.** `JobControls` shows "Restart {selectedStep}" on all views. Intentional: the user may be examining events/tree to debug and still want to restart a step from that context. The right panel (when open) provides visible context.

### Search Param Behavior on Job Switch

Sidebar `onSelectJob` replaces `search: true` with explicit param selection:

```typescript
navigate({
  to: "/jobs/$jobId",
  params: { jobId: id },
  search: (prev: JobDetailSearch) => ({
    q: prev.q, status: prev.status, range: prev.range,  // list filters preserved
    view: prev.view,  // view tab preserved
    // step, tab, panel omitted → cleared
  }),
})
```

## Implementation Steps

### Step 1: Extend URL search params

**File:** `web/src/router.tsx` (lines 53-71)
**Depends on:** nothing
**Inputs:** Current `JOB_DETAIL_TAB_VALUES`, `JobDetailSearch`, `validateJobDetailSearch`.

**Sub-tasks:**
1. After line 53, add: `const JOB_DETAIL_VIEW_VALUES = new Set(["events", "timeline", "tree"]);`
2. At line 55-59, add `view?: "events" | "timeline" | "tree"` to `JobDetailSearch` type.
3. At line 61-71 in `validateJobDetailSearch`, add view validation:
   ```typescript
   view: typeof search.view === "string" && JOB_DETAIL_VIEW_VALUES.has(search.view)
     ? search.view as JobDetailSearch["view"]
     : undefined,
   ```

**Outputs:** `JobDetailSearch` type includes `view`; validator strips invalid values.
**Verify step is done:**
```bash
cd web && npx tsc --noEmit  # no type errors
```

---

### Step 2: Convert separate routes to client-side redirects

**File:** `web/src/router.tsx` (lines 10-12, 105-123)
**Depends on:** Step 1 (redirect target uses `view` param validated by Step 1)
**Inputs:** Three route definitions with `component` prop; three page imports.

**Sub-tasks:**
1. Replace `jobEventsRoute` (lines 105-109):
   ```typescript
   const jobEventsRoute = createRoute({
     getParentRoute: () => rootRoute,
     path: "/jobs/$jobId/events",
     beforeLoad: ({ params }) => {
       throw redirect({
         to: "/jobs/$jobId",
         params: { jobId: params.jobId },
         search: { view: "events" as const },
       });
     },
   });
   ```
2. Replace `jobTreeRoute` (lines 112-116) — same pattern, `view: "tree" as const`.
3. Replace `jobTimelineRoute` (lines 119-123) — same pattern, `view: "timeline" as const`.
4. Remove dead imports at lines 10-12: `JobEventsPage`, `JobTreePage`, `JobTimelinePage`.

**Outputs:** Old routes redirect; no component rendered at old paths.
**Verify step is done:**
```bash
cd web && npx tsc --noEmit  # no type errors, no dead import warnings
```

---

### Step 3: Add tab bar and CSS-hidden tab content to JobDetailPage

**File:** `web/src/pages/JobDetailPage.tsx`
**Depends on:** Step 1 (`view` in `JobDetailSearch` type)

This is the largest step. Breaking into sub-tasks:

**3a. Add imports** (lines 1-41):
```typescript
import { EventLog } from "@/components/events/EventLog";
import { TimelineView } from "@/components/jobs/TimelineView";
import { JobTreeView } from "@/components/jobs/JobTreeView";
```

**3b. Derive `activeView`** (after line 237):
```typescript
const activeView = searchParams.view ?? "dag";
```

**3c. Add `handleViewChange` callback** (after line 285):
```typescript
const handleViewChange = useCallback((v: string) => {
  navigate({
    search: (prev: JobDetailSearch) => ({
      ...prev,
      view: v === "dag" ? undefined : v as JobDetailSearch["view"],
    }),
    replace: false,  // Push history for user-initiated tab switches
  });
}, [navigate]);
```

**3d. Add tab bar and four CSS-hidden content divs** (replace lines 751-773):

Replace the single `{/* DAG */}` div with:
```tsx
{/* Main content tab bar */}
<div className="border-b border-border shrink-0" data-testid="view-tab-bar">
  <div className="flex items-center gap-1 px-4 h-8 overflow-x-auto">
    {([
      { key: "dag", label: "DAG", Icon: GitBranch },
      { key: "events", label: "Events", Icon: ScrollText },
      { key: "timeline", label: "Timeline", Icon: GanttChart },
      { key: "tree", label: "Tree", Icon: GitBranch },
    ] as const).map(({ key, label, Icon }) => (
      <button
        key={key}
        data-testid={`view-tab-${key}`}
        onClick={() => handleViewChange(key)}
        className={cn(
          "relative text-xs px-2.5 py-1.5 flex items-center gap-1",
          "text-zinc-500 hover:text-foreground rounded-t transition-colors whitespace-nowrap",
          activeView === key && "text-foreground after:absolute after:bottom-0 after:inset-x-0 after:h-0.5 after:bg-foreground"
        )}
      >
        <Icon className="w-3.5 h-3.5" />
        {label}
      </button>
    ))}
  </div>
</div>

{/* Tab content — all mounted, inactive hidden via CSS (same pattern as right panel) */}
<div data-testid="view-content-dag" className={cn("flex-1 overflow-hidden", activeView !== "dag" && "hidden")}>
  <FlowDagView
    workflow={job.workflow} runs={runs} jobTree={jobTree ?? null}
    expandedSteps={expandedSteps} onToggleExpand={toggleExpand}
    selectedStep={selectedStep} onSelectStep={handleSelectStep}
    onNavigateSubJob={(subJobId) => navigate({ to: "/jobs/$jobId", params: { jobId: subJobId } })}
    onFulfillWatch={(runId, payload) => mutations.fulfillWatch.mutate({ runId, payload })}
    isFulfilling={mutations.fulfillWatch.isPending}
    selection={selection} onSelectDataFlow={handleSelectDataFlow}
    flowName={job.workflow.metadata?.name || job.name || job.objective || "Flow"}
    jobStatus={job.status}
  />
</div>
<div data-testid="view-content-events" className={cn("flex-1 overflow-hidden", activeView !== "events" && "hidden")}>
  <EventLog jobId={jobId} />
</div>
<div data-testid="view-content-timeline" className={cn("flex-1 overflow-hidden", activeView !== "timeline" && "hidden")}>
  {job && <TimelineView job={job} runs={runs} onSelectStep={handleSelectStep} />}
</div>
<div data-testid="view-content-tree" className={cn("flex-1 overflow-hidden", activeView !== "tree" && "hidden")}>
  <JobTreeView jobId={jobId} onNavigateToJob={(id) => navigate({ to: "/jobs/$jobId", params: { jobId: id } })} />
</div>
```

Note: `TimelineView` requires a non-null `Job` object (A2), so it's conditionally rendered on `job`. The other three components handle loading internally.

**Outputs:** Four tab contents rendered simultaneously; only active one visible. `data-testid` attributes on tab bar and content divs for testing.
**Verify step is done:**
```bash
cd web && npx tsc --noEmit    # no type errors
# Visual: load /jobs/$jobId → DAG visible. Click Events → EventLog visible, DAG hidden.
# DOM: all four data-testid="view-content-*" elements present.
```

---

### Step 4: Remove header nav links, keep Details button

**File:** `web/src/pages/JobDetailPage.tsx`
**Depends on:** Step 3 (tab bar replaces these links)

**4a. Desktop header** (lines 672-706):
Remove the three `<Link>` elements:
- Lines 673-680: `<Link to="/jobs/$jobId/events" ...>` — delete
- Lines 681-688: `<Link to="/jobs/$jobId/timeline" ...>` — delete
- Lines 689-696: `<Link to="/jobs/$jobId/tree" ...>` — delete

Keep the Details `<button>` at lines 697-705 (conditionally rendered when right panel is closed).

**4b. Mobile header** (lines 570-606):
Remove the three `<Link>` elements:
- Lines 571-579: Events link — delete
- Lines 580-588: Timeline link — delete
- Lines 589-597: Tree link — delete

Keep the Details button at lines 598-607.

**4c. Clean up unused `<Link>` import** if no other `<Link to="/jobs/$jobId/...">` references remain in the file. (The `<Link>` import is also used for breadcrumbs and flow links, so it stays.)

**Outputs:** Header has only the Details button for right-panel toggle. No view-switching links.
**Verify step is done:**
```bash
grep -n 'to="/jobs/\$jobId/events\|to="/jobs/\$jobId/timeline\|to="/jobs/\$jobId/tree' web/src/pages/JobDetailPage.tsx
# Should return zero results
```

---

### Step 5: Scope keyboard shortcuts correctly

**File:** `web/src/pages/JobDetailPage.tsx` (lines 352-414)
**Depends on:** Step 3 (`activeView` variable exists)

Restructure the `useEffect` keyboard handler:

```typescript
const handler = (e: KeyboardEvent) => {
  const target = e.target as HTMLElement;
  if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable) {
    return;
  }

  // Escape: universal dismiss (all views) — R10
  if (e.key === "Escape") {
    if (selection) {
      setDataFlowSelection(null);
      navigate({
        search: (prev: JobDetailSearch) => ({
          ...prev, step: undefined, tab: undefined, panel: undefined,
        }),
        replace: true,
      });
    }
    return;
  }

  // DAG-only navigation keys — R10
  if (activeView !== "dag") return;

  const stepCount = topoStepNames.length;
  if (stepCount === 0) return;
  const currentIndex = selectedStep ? topoStepNames.indexOf(selectedStep) : -1;

  switch (e.key) {
    case "j":
    case "ArrowDown": { /* ... existing unchanged ... */ break; }
    case "k":
    case "ArrowUp": { /* ... existing unchanged ... */ break; }
    case "Tab": { /* ... existing unchanged ... */ break; }
    case "Enter": { /* ... existing unchanged ... */ break; }
  }
};
```

Add `activeView` to the dependency array of this `useEffect`.

**Outputs:** j/k/Tab/Enter fire only on DAG. Escape fires on all tabs.
**Verify step is done:** On Events tab, press j → nothing. Press Escape with selected step → step clears.

---

### Step 6: Fix search param behavior on job switch

**File:** `web/src/pages/JobDetailPage.tsx` (line 505-507)
**Depends on:** Step 1 (`view` param exists)

Replace sidebar `onSelectJob` callback:

**Before** (line 505-507):
```typescript
onSelectJob={(id) =>
  navigate({ to: "/jobs/$jobId", params: { jobId: id }, search: true })
}
```

**After:**
```typescript
onSelectJob={(id) =>
  navigate({
    to: "/jobs/$jobId",
    params: { jobId: id },
    search: (prev: JobDetailSearch) => ({
      q: prev.q, status: prev.status, range: prev.range,
      view: prev.view,
    }),
  })
}
```

**Outputs:** View tab and list filters preserved across job switches. `step`/`tab`/`panel` cleared.
**Verify step is done:** Select step on job A, switch to job B via sidebar → no step selected, same view tab, panel closed.

---

### Step 7: Fix mobile panel title to be context-aware

**File:** `web/src/pages/JobDetailPage.tsx` (line 872)
**Depends on:** nothing

**Before:**
```typescript
title="Details"
```

**After:**
```typescript
title={resolvedStep ? resolvedStep.stepDef.name : "Details"}
```

**Outputs:** Mobile panel title shows step name when selected, "Details" otherwise.
**Verify step is done:** On mobile, select step → title matches step name. No step → title is "Details".

---

### Step 8: Enrich browser tab title with active view

**File:** `web/src/components/layout/AppLayout.tsx` (lines 225-255)
**Depends on:** Step 1 (`view` param in URL)

In the title `useEffect`, modify the `isJobsActive && detailJobId` branch (lines 228-230):

**Before:**
```typescript
if (isJobsActive && detailJobId) {
  const jobName = detailJob?.name || detailJob?.objective;
  title = jobName ? `${jobName} — Stepwise` : "Stepwise";
}
```

**After:**
```typescript
if (isJobsActive && detailJobId) {
  const jobName = detailJob?.name || detailJob?.objective;
  const viewParam = new URLSearchParams(window.location.search).get("view");
  const viewLabel = viewParam === "events" ? "Events"
    : viewParam === "timeline" ? "Timeline"
    : viewParam === "tree" ? "Tree"
    : null;
  title = viewLabel && jobName
    ? `${viewLabel} — ${jobName} — Stepwise`
    : jobName
      ? `${jobName} — Stepwise`
      : "Stepwise";
}
```

Uses `window.location.search` directly since AppLayout doesn't have typed access to the job detail route's search params. `currentPath` is already in the dependency array (line 247), so the effect re-fires on navigation.

**Outputs:** Browser tab shows "Events — MyJob — Stepwise" on events view. DAG (default) shows "MyJob — Stepwise" (unchanged).
**Verify step is done:** Switch tabs → browser tab title updates accordingly.

---

### Step 9: Delete old page files

**Depends on:** Steps 2 (routes redirected), 3 (components imported directly)

**Files to delete:**
- `web/src/pages/JobEventsPage.tsx`
- `web/src/pages/JobTreePage.tsx`
- `web/src/pages/JobTimelinePage.tsx`

**Verify step is done:**
```bash
grep -rn "JobEventsPage\|JobTreePage\|JobTimelinePage" web/src/
# Should return zero results
cd web && npx tsc --noEmit  # no missing import errors
```

---

### Step 10: Write tests

**Depends on:** Steps 1-9 (all implementation complete)
**New files:**
- `web/src/router.test.ts` — route config unit tests
- `web/src/pages/__tests__/JobDetailTabs.test.tsx` — tab system integration tests

Details in Testing Strategy section below.

**Verify step is done:**
```bash
cd web && npm run test  # all tests pass, including new ones
```

---

### Step 11: Final validation

**Depends on:** Step 10

```bash
cd web && npx tsc --noEmit              # type check
cd web && npm run lint                   # lint (fix any issues)
cd web && npm run test                   # all tests green
```

Then run through the 14-point manual test plan (see Manual Test Plan below).

## Implementation Dependency Graph

```
Step 1 (router types)
├── Step 2 (redirect routes)
├── Step 3 (tab bar + content)  ─── Step 4 (remove header links)
│                                └── Step 5 (keyboard scoping)
├── Step 6 (job switch params)
├── Step 8 (page title)
│
Step 7 (mobile title) ── independent
Step 9 (delete files) ── after Steps 2 + 3
Step 10 (tests) ── after Steps 1-9
Step 11 (final validation) ── after Step 10
```

Steps 3-8 can be done in any order after Step 1, but Step 4 is most naturally done right after Step 3 since they edit the same code region. Steps 2 and 9 are linked (redirect routes before deleting old pages).

## Testing Strategy

### Existing Tests (must remain green)

```bash
cd web && npm run test
```

Key files to watch:
- `hooks/useStepwise.test.ts` — data hooks, unaffected
- `components/jobs/JobList.test.tsx` — sidebar. Its `useNavigate` mock may need `view` awareness if the `onSelectJob` signature changes. Check: the mock at `JobList.test.tsx:35-46` captures `search` param — verify it handles the function form used in Step 6.
- `components/dag/__tests__/FlowDagView.touch.test.tsx` — DAG interaction, unaffected
- `components/dag/DataFlowPanel.test.tsx` — right panel, unaffected
- `hooks/__tests__/useAutoSelectSuspended.test.ts` — unchanged behavior

### New Test File: `web/src/router.test.ts`

Unit tests against route configuration, no rendering. Tests R4 and R12.

```typescript
import { describe, it, expect } from "vitest";
// Import the validate function and route configs (may need exporting them)

describe("validateJobDetailSearch", () => {
  it('accepts view: "events"', () => {
    const result = validateJobDetailSearch({ view: "events" });
    expect(result.view).toBe("events");
  });

  it('accepts view: "timeline"', () => {
    const result = validateJobDetailSearch({ view: "timeline" });
    expect(result.view).toBe("timeline");
  });

  it('accepts view: "tree"', () => {
    const result = validateJobDetailSearch({ view: "tree" });
    expect(result.view).toBe("tree");
  });

  it("strips invalid view values (R12)", () => {
    const result = validateJobDetailSearch({ view: "bogus" });
    expect(result.view).toBeUndefined();
  });

  it("strips non-string view values (R12)", () => {
    const result = validateJobDetailSearch({ view: 42 });
    expect(result.view).toBeUndefined();
  });

  it("preserves other params alongside view", () => {
    const result = validateJobDetailSearch({
      step: "my-step", tab: "step", panel: "open", view: "events",
    });
    expect(result).toEqual({
      step: "my-step", tab: "step", panel: "open", view: "events",
    });
  });

  it("omitted view defaults to undefined (DAG)", () => {
    const result = validateJobDetailSearch({});
    expect(result.view).toBeUndefined();
  });
});

describe("legacy route redirects (R4)", () => {
  // These test that beforeLoad throws a redirect with correct search params.
  // Implementation depends on whether route configs are exported.
  // Alternative: test by calling beforeLoad directly and catching the redirect.

  it("/jobs/$jobId/events redirects to ?view=events", () => {
    // Call jobEventsRoute.options.beforeLoad({ params: { jobId: "abc" } })
    // Expect it to throw an object with search.view === "events"
  });

  it("/jobs/$jobId/timeline redirects to ?view=timeline", () => {
    // Same pattern
  });

  it("/jobs/$jobId/tree redirects to ?view=tree", () => {
    // Same pattern
  });
});
```

**Note:** `validateJobDetailSearch` and route configs may need to be exported from `router.tsx` for testability. If the route objects aren't easily exported, the redirect tests can be manual-only with the validation tests still automated.

### New Test File: `web/src/pages/__tests__/JobDetailTabs.test.tsx`

Integration tests for the tab system. Uses the mock pattern from `JobList.test.tsx` (lines 20-47): mock `@tanstack/react-router` with `useSyncExternalStore` for reactive search params and a capturing `useNavigate`.

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// ── Router mock (pattern from JobList.test.tsx:20-47) ──
type MockSearch = Record<string, unknown>;
let mockSearchState: MockSearch = {};
const searchListeners = new Set<() => void>();
let navigateCalls: Array<{ to?: string; params?: Record<string, string>; search?: unknown; replace?: boolean }> = [];

vi.mock("@tanstack/react-router", async () => {
  const React = await import("react");
  return {
    useSearch: () =>
      React.useSyncExternalStore(
        (listener: () => void) => { searchListeners.add(listener); return () => searchListeners.delete(listener); },
        () => mockSearchState,
        () => mockSearchState,
      ),
    useParams: () => ({ jobId: "test-job-1" }),
    useNavigate: () => (args: Record<string, unknown>) => {
      navigateCalls.push(args);
      // Apply search function to update reactive state
      if (typeof args.search === "function") {
        mockSearchState = args.search(mockSearchState);
        for (const l of searchListeners) l();
      }
      return Promise.resolve();
    },
    Link: ({ children, ...props }: Record<string, unknown>) =>
      React.createElement("a", props, children),
  };
});

// ── Data hook mocks ──
vi.mock("@/hooks/useStepwise", () => ({
  useJob: () => ({
    data: {
      id: "test-job-1", name: "Test Job", objective: "test",
      status: "running", inputs: {},
      workflow: {
        steps: {
          "step-a": {
            name: "step-a", outputs: [], executor: { type: "script", config: {}, decorators: [] },
            inputs: [], after: [], exit_rules: [], idempotency: "always", limits: null,
          },
        },
      },
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      created_by: "server", parent_job_id: null, parent_step_run_id: null,
      workspace_path: "/tmp", runner_pid: null, heartbeat_at: null,
      config: { max_sub_job_depth: 1, timeout_minutes: null, metadata: {} },
      job_group: null, depends_on: [],
    },
    isLoading: false,
  }),
  useRuns: () => ({ data: [] }),
  useJobTree: () => ({ data: null }),
  useJobOutput: () => ({ data: null }),
  useJobCost: () => ({ data: null }),
  useStepwiseMutations: () => ({
    fulfillWatch: { mutate: vi.fn(), isPending: false },
    rerunStep: { mutate: vi.fn(), isPending: false },
  }),
  useEvents: () => ({ data: [] }),
}));

vi.mock("@/hooks/useConfig", () => ({ useConfig: () => ({ data: null }) }));
vi.mock("@/hooks/useAutoSelectSuspended", () => ({ useAutoSelectSuspended: vi.fn() }));
vi.mock("@/hooks/useAutoExpand", () => ({
  useAutoExpand: () => ({ expandedSteps: new Set(), toggleExpand: vi.fn() }),
}));

function createWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: qc }, children);
}

describe("JobDetailPage tab system", () => {
  beforeEach(() => {
    mockSearchState = {};
    navigateCalls = [];
  });

  describe("tab visibility (R1, R2, R8)", () => {
    it("shows DAG by default, hides others", () => {
      render(<JobDetailPage />, { wrapper: createWrapper() });
      expect(screen.getByTestId("view-content-dag")).not.toHaveClass("hidden");
      expect(screen.getByTestId("view-content-events")).toHaveClass("hidden");
      expect(screen.getByTestId("view-content-timeline")).toHaveClass("hidden");
      expect(screen.getByTestId("view-content-tree")).toHaveClass("hidden");
    });

    it("shows Events when view=events, hides DAG", () => {
      mockSearchState = { view: "events" };
      render(<JobDetailPage />, { wrapper: createWrapper() });
      expect(screen.getByTestId("view-content-events")).not.toHaveClass("hidden");
      expect(screen.getByTestId("view-content-dag")).toHaveClass("hidden");
    });

    it("shows Timeline when view=timeline", () => {
      mockSearchState = { view: "timeline" };
      render(<JobDetailPage />, { wrapper: createWrapper() });
      expect(screen.getByTestId("view-content-timeline")).not.toHaveClass("hidden");
    });

    it("shows Tree when view=tree", () => {
      mockSearchState = { view: "tree" };
      render(<JobDetailPage />, { wrapper: createWrapper() });
      expect(screen.getByTestId("view-content-tree")).not.toHaveClass("hidden");
    });

    it("all four content divs are always in the DOM (CSS hiding, not unmounting)", () => {
      render(<JobDetailPage />, { wrapper: createWrapper() });
      expect(screen.getByTestId("view-content-dag")).toBeInTheDocument();
      expect(screen.getByTestId("view-content-events")).toBeInTheDocument();
      expect(screen.getByTestId("view-content-timeline")).toBeInTheDocument();
      expect(screen.getByTestId("view-content-tree")).toBeInTheDocument();
    });
  });

  describe("tab switching and history (R3)", () => {
    it("clicking Events tab pushes history (replace: false)", () => {
      render(<JobDetailPage />, { wrapper: createWrapper() });
      fireEvent.click(screen.getByTestId("view-tab-events"));
      const call = navigateCalls[navigateCalls.length - 1];
      expect(call.replace).toBe(false);
      // search function should produce { view: "events" }
    });

    it("clicking DAG tab clears view param", () => {
      mockSearchState = { view: "events" };
      render(<JobDetailPage />, { wrapper: createWrapper() });
      fireEvent.click(screen.getByTestId("view-tab-dag"));
      // After applying search fn, view should be undefined
      expect(mockSearchState.view).toBeUndefined();
    });

    it("tab switch preserves existing step/tab/panel", () => {
      mockSearchState = { step: "step-a", tab: "step", panel: "open" };
      render(<JobDetailPage />, { wrapper: createWrapper() });
      fireEvent.click(screen.getByTestId("view-tab-events"));
      expect(mockSearchState).toMatchObject({
        step: "step-a", tab: "step", panel: "open", view: "events",
      });
    });
  });

  describe("keyboard scoping (R10)", () => {
    it("j key navigates steps on DAG view", () => {
      mockSearchState = {}; // DAG default
      render(<JobDetailPage />, { wrapper: createWrapper() });
      fireEvent.keyDown(window, { key: "j" });
      // Should trigger navigate with step param
      expect(navigateCalls.length).toBeGreaterThan(0);
    });

    it("j key is ignored on events view", () => {
      mockSearchState = { view: "events" };
      render(<JobDetailPage />, { wrapper: createWrapper() });
      const callsBefore = navigateCalls.length;
      fireEvent.keyDown(window, { key: "j" });
      expect(navigateCalls.length).toBe(callsBefore); // No new calls
    });

    it("Escape clears selection on events view", () => {
      mockSearchState = { view: "events", step: "step-a", tab: "step", panel: "open" };
      render(<JobDetailPage />, { wrapper: createWrapper() });
      fireEvent.keyDown(window, { key: "Escape" });
      expect(mockSearchState.step).toBeUndefined();
      expect(mockSearchState.panel).toBeUndefined();
    });
  });

  describe("job switch param cleanup (R14)", () => {
    it("sidebar job click preserves view but clears step/tab/panel", () => {
      mockSearchState = { view: "events", step: "old-step", tab: "step", panel: "open", q: "search" };
      render(<JobDetailPage />, { wrapper: createWrapper() });
      // Find and click a job in the sidebar (or directly invoke onSelectJob)
      // Assert navigate called with view: "events", q: "search", no step/tab/panel
    });
  });

  describe("header navigation surface (R11)", () => {
    it("no Events/Timeline/Tree links in header", () => {
      render(<JobDetailPage />, { wrapper: createWrapper() });
      // No <a> with href containing /events, /timeline, /tree
      const links = screen.queryAllByRole("link");
      for (const link of links) {
        expect(link.getAttribute("href") || "").not.toMatch(/\/(events|timeline|tree)$/);
      }
    });

    it("Details button exists and navigates with panel: open", () => {
      render(<JobDetailPage />, { wrapper: createWrapper() });
      const detailsBtn = screen.getByRole("button", { name: /details/i });
      fireEvent.click(detailsBtn);
      const call = navigateCalls[navigateCalls.length - 1];
      // search fn should set panel: "open"
    });
  });
});
```

### Test Coverage Matrix

| Req | Automated Test | Manual Test |
|---|---|---|
| R1 | visibility: DAG default | Manual #1 |
| R2 | visibility: all 4 in DOM | Manual #1, #13 |
| R3 | history push, DAG clears view, preserves params | Manual #1 |
| R4 | router.test.ts redirects | Manual #9 |
| R5 | view=events visibility | Manual #4 |
| R6 | view=timeline visibility | Manual #6 |
| R7 | view=tree visibility | Manual #7 |
| R8 | all-in-DOM (CSS hiding) | Manual #3 |
| R9 | — (mobile needs real viewport) | Manual #12 |
| R10 | j ignored on events, Escape global | Manual #10 |
| R11 | no header links, Details button works | Manual #13 |
| R12 | router.test.ts invalid view | Manual #14 |
| R13 | — (document.title, manual) | Manual #8 |
| R14 | sidebar param cleanup | Manual #8 |

### Manual Test Plan

| # | Test | Steps | Expected |
|---|---|---|---|
| 1 | Tab switching & history | Click DAG → Events → Timeline → Tree. Press back 3 times. | Returns through each tab in reverse. Forward works symmetrically. |
| 2 | Deep linking | Open `?view=timeline` in new tab. | Timeline renders with full job context (header, sidebar, controls). |
| 3 | DAG state preservation | Zoom in, disable follow-flow, enable critical path on DAG. Switch to Events, switch back. | Same viewport, follow-flow off, critical path on. |
| 4 | EventLog state preservation | On Events, filter to "step" category, search for a term. Switch to DAG, switch back. | Filter and search still active. |
| 5 | Tree expansion preservation | On Tree, expand a node. Switch to Timeline, switch back. | Node still expanded. |
| 6 | Timeline → Step detail | Click a step bar in Timeline. | Right panel opens, Step tab shows that step's detail. |
| 7 | Tree navigation | Click "Open job" on a sub-job in Tree. | Navigates to sub-job's detail, DAG view (default). |
| 8 | Job switch preserves view | On Events tab, click different job in sidebar. | Stays on Events for new job. No stale step. Browser title: "Events — NewJob — Stepwise". |
| 9 | Redirect | Navigate to `/jobs/$jobId/events`. | URL changes to `/jobs/$jobId?view=events`. Events tab active. |
| 10 | Keyboard scoping | On DAG, j/k navigates steps. Switch to Events, press j/k. Press Escape with step selected. | j/k do nothing on Events. Escape clears selection on any tab. |
| 11 | Auto-select suspended | Start job with external step. While on Events, step suspends. | Right panel opens with step detail (global behavior). |
| 12 | Mobile | Test all tabs on mobile viewport. Select step, check panel title. | Tab bar scrolls. Panel title shows step name. All tabs work. |
| 13 | Details button | Close right panel. Click Details on each tab. | Panel opens to Job tab. Works on DAG, Events, Timeline, Tree. |
| 14 | Invalid view param | Edit URL to `?view=bogus`. | DAG tab shown. No error. |

### Verification Commands

```bash
cd web && npx tsc --noEmit     # type safety
cd web && npm run lint          # code quality
cd web && npm run test          # all tests (existing + new)
```

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **All tabs mounted = extra API call** | `EventLog` fires `useEvents(jobId)` on mount | Certain | React Query caches (one fetch/job). Events are lightweight. Can lazy-mount later if profiling shows impact. |
| **Broken bookmarks/shared links** | Users with `/jobs/$jobId/events` bookmarks | Medium | Client-side `beforeLoad` redirects (Step 2). Routes stay in tree permanently. |
| **Timeline step selection semantics change** | Previously local state, now URL-backed | Low | Strictly better: consistent with DAG, survives tab switches. |
| **Tab bar reduces DAG viewport by ~32px** | Slightly less DAG space | Certain | Header nav icons removed, reclaiming ~32px. Net change ≈ 0. |
| **Selected step persists on Events/Tree** | "Restart {step}" visible without step context | Medium | Intentional. Right panel provides context. Can revisit if UX testing shows confusion. |
| **`useAutoSelectSuspended` interrupts non-DAG** | Panel opens while browsing events/tree | Low | Intentional. Suspended external steps are urgent. Same behavior as today. |
| **CSS-hidden tabs consume DOM memory** | Four component trees mounted | Certain | Lightweight components. FlowDagView's rAF loop settles when hidden. No measurable impact. |
| **History stack grows with tab clicks** | Many back entries from rapid tab switching | Low | Same as clicking links today. Can debounce later if needed. |
| **`search: true` removal in sidebar changes behavior** | Job switch might lose list filters | Low | Explicit param preservation in Step 6. Test case validates. |
| **`JobList.test.tsx` mock breaks** | `useNavigate` mock may not handle function-form `search` | Medium | Update mock's navigate handler to call `search(prev)` when search is a function (same pattern as existing mock at line 42). |
