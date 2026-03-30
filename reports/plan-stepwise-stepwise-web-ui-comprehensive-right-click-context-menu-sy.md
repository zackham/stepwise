# Implementation Plan: Comprehensive Right-Click Context Menu System

Build an entity-driven action registry that powers right-click context menus, kebab/triple-dot dropdown menus, and keyboard shortcuts from a single source of truth — so every entity (Job, Step, Flow) has consistent actions regardless of where it's surfaced.

---

## Critique Response Log

> **#1 (critical) — Scope inconsistent: Edge entity type claimed but missing from EntityType; Canvas JobCard not in integration list.**
> Resolution: Removed `edge` from EntityType and requirements. It's aspirational — no backing selection model exists for edges today. Added explicit Canvas `JobCard` integration step (Step 14). Canvas background actions remain.

> **#2 (critical) — Confirmation flow unmounts when menu closes.**
> Resolution: Redesigned. `pendingAction` state lives in `EntityContextMenu`/`EntityDropdownMenu` wrapper (above the menu root), not inside `ActionMenuItems`. The `ConfirmDialog` is rendered as a sibling of the menu, not a child of menu content. Menu closes → dialog stays open. See Step 7 and Step 8.

> **#3 (critical) — Keyboard shortcut model assumes focus/ancestor model that doesn't match actual focus model.**
> Resolution: Removed the `data-entity-type` ancestor approach. Entity shortcuts are driven by page-level selection state: `JobList` uses its existing `focusedIndex`/`selectedJobId`, `JobDetailPage` uses its existing `selectedStep` from URL search params, `FlowsPage` uses `selectedLocalFlow`. Each page registers entity shortcuts via `useEntityShortcuts(type, selectedEntity)` which is enabled only when that entity is selected. Existing j/k/Enter/Escape navigation in JobList and JobDetailPage is untouched.

> **#4 (critical) — Step identity not unique for nested DAGs.**
> Resolution: V1 context menus attach only to top-level steps in the current job's DAG (the `StepNode` instances rendered by `FlowDagView` in `JobDetailPage`). Nested sub-job steps inside `ExpandedStepContainer` and `ForEachExpandedContainer` are **excluded** — they receive step identity from a different job and the selection model (`DagSelection.stepName`) doesn't disambiguate them. A future V2 can introduce `StepInstanceIdentity = { jobId, stepName, instanceIndex? }` once the selection model supports it.

> **#5 (major) — "Synchronous, O(1), tree-shakeable, lazy-loaded" are contradictory.**
> Resolution: Dropped lazy loading. The registry uses synchronous static imports. Each entity file is ~2-4KB — all four entity files together are smaller than a single shadcn component. Tree-shakeability means dead entity files are not bundled, but there is no runtime async loading.

> **#6 (major) — Existing action surfaces (JobControls, CanvasJobControls) have actions the catalog omits: Take Over, Inject Context, completed-job Retry.**
> Resolution: Added all missing actions to the Job catalog: `job.take-over` (stale job adoption), `job.inject-context` (requires dialog), `job.reset` (reset to re-run). Updated integration plan to migrate `JobControls.tsx` (Step 15) and `CanvasJobControls.tsx` (Step 14) to consume the shared registry. `JobControls` becomes a thin bar rendering `ActionButton` components from the registry; `CanvasJobControls` renders the same actions in compact icon-only mode.

> **#7 (major) — FlowFileList is the wrong target; the real flow list is in FlowsPage.tsx.**
> Resolution: Retargeted. Primary integration is `FlowsPage.tsx` (lines 393-450 flow list, lines 678-702 delete dialog). `FlowFileList.tsx` in the editor sidebar is secondary — it's used inside `EditorPage` for the file tree, and its existing single-action context menu will also be upgraded, but `FlowsPage` is the P0 surface.

> **#8 (major) — Flow delete/run actions need page-level side effects that a generic registry can't model.**
> Resolution: Actions now accept an optional `sideEffects` object injected by the page via `ActionContextProvider`. Flow delete calls `sideEffects.onAfterDeleteFlow?.(flow)` which FlowsPage implements to clear selection and reset URL. Flow run calls `sideEffects.onRunFlow?.(flow)` which FlowsPage implements to call `createJob.mutate()` with navigation, and EditorPage implements to open its run config dialog. The registry defines *what* actions exist and *when* they're available; the page decides *how* side effects manifest.

> **#9 (major) — Navigation actions are underspecified or wrong.**
> Resolution: "Open in New Tab" now explicitly uses `window.open(url, '_blank')` with the URL built via TanStack Router's `buildLocation`. "View in Canvas" is **removed** — CanvasPage has no job selection/focus mechanism, so the action has no meaningful target.

> **#10 (major) — Step action catalog invents behavior without backing state.**
> Resolution: Cut V1 step actions to those with real backing: Rerun, Cancel Run, Copy Step Name, Copy Step Config, View Output (navigates to step tab). Removed: Select Upstream/Downstream (DagSelection is single-select, no multi-select), Trace Inputs (no backing panel), View Prompt (no dedicated view).

> **#11 (major) — useEntityActions() would instantiate full mutation bundle per row.**
> Resolution: Introduced `ActionContextProvider` (React context) at the page level. It calls `useStepwiseMutations()` once, `useNavigate()` once, and provides the shared `ActionContext` to all descendant menus via `useContext`. Menu components consume context — zero mutation hooks per instance.

> **#12 (major) — Testing plan misses critical failure modes.**
> Resolution: Added test cases for: (a) dialog survival after menu close, (b) delete-triggered navigation/selection reset in FlowsPage and JobList, (c) nested DAG step exclusion (no context menu on sub-job steps), (d) stopPropagation behavior ensuring right-click doesn't trigger row selection, (e) keyboard shortcuts not firing inside input/textarea elements, (f) canvas JobCard context menu coexistence with Link navigation.

> **#13 (minor) — Plan says "button" but rows are `<div role="option">`.**
> Resolution: Fixed. Integration steps reference `<div role="option" tabIndex={-1}>`. Added explicit note about `stopPropagation` on context menu item clicks to prevent triggering row selection/navigation (matching the existing pattern in `JobActions` at `JobList.tsx:213,221,231,244,253,265`).

> **#14 (minor) — Step 7 uses ContextMenuSub but Step 17 adds those primitives.**
> Resolution: Moved ContextMenu sub-menu primitives and ContextMenuShortcut to Step 4 (before the shared renderer that needs them).

---

## Requirements

### Functional

1. **Action Registry** — A centralized registry maps entity types to their available actions. Each action has: id, label, icon, keyboard shortcut (optional), availability condition, handler, variant, and group separator placement.
   - *Acceptance:* `getActionsForEntity("job", jobData)` returns only actions whose `isAvailable(jobData)` returns true.

2. **Entity Context Menus** — Right-click on any entity surfaces its actions in a context menu. Supported entity surfaces: Job (list rows, canvas cards, tree view, detail sidebar), Step (top-level DAG nodes), Flow (FlowsPage list, editor sidebar), Canvas background.
   - *Acceptance:* Right-clicking a job row in JobList shows Retry/Cancel/Pause/Resume/Archive/Delete actions matching current job status.

3. **Unified Kebab + Context Menus** — The existing `JobActions` dropdown and `FlowsPage` inline list both consume the same action definitions as context menus. One source of truth for action availability logic.
   - *Acceptance:* `JobActions` kebab menu and right-click on the same job row produce identical menu items.

4. **Confirmation for Destructive Actions** — Delete, Cancel, and other destructive actions show an `AlertDialog` confirmation before executing. The dialog renders as a sibling of the menu (not inside menu content) so it survives menu close.
   - *Acceptance:* Right-click → Delete → menu closes → confirmation dialog appears → Cancel dismisses → Confirm calls `deleteJob.mutate()`.

5. **Copy Actions** — Copy Job ID, Copy Name, Copy Step Config, Copy Flow Path use `navigator.clipboard.writeText()` with toast feedback.
   - *Acceptance:* Right-click job → "Copy Job ID" copies UUID to clipboard, shows "Copied to clipboard" toast.

6. **Page-Level Side Effects** — Actions that need page-specific behavior (e.g., clear selection after delete, open run config dialog) receive side-effect callbacks from the page via `ActionContextProvider`.
   - *Acceptance:* Deleting a flow in FlowsPage clears `selectedLocalFlow` and resets URL to `/flows`.

7. **Sub-menus** — "Copy" actions grouped under a "Copy ▸" sub-menu.
   - *Acceptance:* Job context menu shows "Copy ▸" that expands to show Copy ID, Copy Name, Copy Inputs.

8. **Keyboard Shortcuts** — Entity-scoped shortcuts driven by page-level selection state. Actions with shortcuts display the shortcut hint in menu items. Existing j/k/Enter/Escape navigation is untouched.
   - *Acceptance:* With a job selected in JobList, pressing `d` triggers delete confirmation. The shortcut label "D" appears right-aligned in the Delete menu item.

### Non-Functional

9. **Performance** — Action availability evaluation is synchronous. Menu components create zero mutation hooks — they consume a shared `ActionContext` from a page-level React context provider.
   - *Acceptance:* Menu opens within one frame (~16ms) of right-click. Profiling shows one `useStepwiseMutations()` call per page, not per row.

---

## Assumptions (verified against codebase)

1. **@base-ui/react ContextMenu + Menu primitives are installed and working.**
   Verified: `web/src/components/ui/context-menu.tsx:4` wraps `@base-ui/react/context-menu`. `web/src/components/ui/dropdown-menu.tsx:4` wraps `@base-ui/react/menu`. Both support `variant="destructive"`, separators, labels.

2. **@base-ui/react v1.2.0 includes context menu submenu primitives.**
   Verified: `node_modules/@base-ui/react/esm/context-menu/index.parts.d.ts:17-18` exports `ContextMenu.SubmenuRoot` and `ContextMenu.SubmenuTrigger` (backed by `menu/submenu-root/MenuSubmenuRoot.js` and `menu/submenu-trigger/MenuSubmenuTrigger.js`). No third-party package needed.

3. **DropdownMenu has sub-menu and shortcut primitives; ContextMenu wrappers do not yet.**
   Verified: `dropdown-menu.tsx:99-149` has `DropdownMenuSub`, `DropdownMenuSubTrigger`, `DropdownMenuSubContent`. `dropdown-menu.tsx:239-253` has `DropdownMenuShortcut`. The context-menu.tsx wrapper exports neither yet, but the underlying @base-ui primitives are available to wrap.

4. **`useStepwiseMutations()` aggregates all job/step mutations including adoption and context injection.**
   Verified: `useStepwise.ts:142-391` exports `adoptJob` (line 339), `injectContext` (line 245), `resetJob` (line 205), plus all lifecycle/archive/delete mutations.

5. **`useHotkeys` is sequence-based and intentionally filters out modifier keys.**
   Verified: `useHotkeys.ts:78-86` returns early if `event.metaKey || event.ctrlKey || event.altKey`. Entity shortcuts will use single-letter keys (e.g., `d` for delete, `r` for retry) matching the existing vim-style navigation pattern.

6. **JobList rows are `<div role="option" tabIndex={-1}>` in a `role="listbox"` container, with existing global j/k keyboard handlers.**
   Verified: `JobList.tsx:327` uses `role="option"`, `JobList.tsx:681` uses `role="listbox"`. Lines 629-671 register global `window` keydown for j/k navigation. Lines 600-627 handle ArrowUp/Down/Enter/Escape via React onKeyDown.

7. **JobDetailPage has global keyboard navigation for steps via j/k/Tab/Enter/Escape.**
   Verified: `JobDetailPage.tsx:351-400+` registers `window` keydown handler with topologically-sorted step navigation. Step selection stored in URL search params (`searchParams.step`).

8. **JobControls.tsx provides actions not in the original catalog: Take Over, Inject Context, Restart Selected Step, completed-job Retry.**
   Verified: `JobControls.tsx:205-223` (Take Over for stale jobs), `JobControls.tsx:246-298` (Inject Context dialog), `JobControls.tsx:226-241` (Restart step), `JobControls.tsx:166-177` (Retry for completed jobs).

9. **CanvasJobControls.tsx is a separate action surface with compact icon+label buttons.**
   Verified: `CanvasJobControls.tsx:37-148` — absolute-positioned overlay on canvas job cards with Pause/Resume/Cancel/Retry actions conditional on `jobStatus`.

10. **FlowsPage.tsx renders its own flow list (not FlowFileList) with page-owned delete confirmation and selection/URL management.**
    Verified: `FlowsPage.tsx:316-453` renders flow rows directly. `FlowsPage.tsx:678-702` has `AlertDialog` for delete confirmation. `FlowsPage.tsx:212-224` clears selection and resets URL after delete.

11. **DagSelection is a single-select discriminated union.**
    Verified: `dag-layout.ts:10-15` — `{ kind: "step"; stepName: string } | { kind: "edge-field"; ... } | ... | null`. No multi-select support.

12. **`AlertDialog` component is available and already used in FlowsPage.**
    Verified: `FlowsPage.tsx:678-702` uses `AlertDialog`, `AlertDialogContent`, `AlertDialogTitle`, `AlertDialogDescription`, `AlertDialogAction`, `AlertDialogCancel`.

13. **`sonner` toast is the notification mechanism.**
    Verified: Used via `toast.success()`/`toast.error()` across hooks and components.

14. **Test infrastructure uses vitest + jsdom + @testing-library/react with specific patterns.**
    Verified: `JobList.test.tsx` uses `vi.mock("@/hooks/useStepwise")` with `{ mutate: vi.fn() }` shapes, `makeJob()` factory with auto-incrementing IDs, `createWrapper()` for `QueryClientProvider`, and `ResizeObserver` polyfill. `FlowFileList.test.tsx` uses inline `LocalFlow` objects. `CommandPalette.test.tsx` tests keyboard via `fireEvent.keyDown(document, { key, metaKey })`.

---

## Out of Scope

- **Edge entity actions** — `DagSelection` supports edge-field selection but there's no action model for edges. Deferred until edge inspection is designed.
- **Nested sub-job step menus** — Steps inside `ExpandedStepContainer` and `ForEachExpandedContainer` belong to child jobs with different IDs. The current `DagSelection` doesn't disambiguate them. V1 attaches context menus only to top-level steps.
- **Multi-select step operations** — `DagSelection` is single-select. "Select Upstream/Downstream" requires multi-select support.
- **Group entity actions** — Canvas groups exist in `CanvasPage` but have no entity model beyond concurrency limit buttons.
- **Event Log Entry actions** — Events view is a flat read-only list.
- **Registry card actions** — Read-only (search + install button already exists).
- **Mobile/touch long-press** — Touch users use kebab menus.
- **View in Canvas** — `CanvasPage` has no job selection/focus mechanism. Removed until canvas supports focusing a specific job.

---

## Architecture

### Action Registry Design

```
web/src/lib/actions/
├── types.ts          — Action, ActionGroup, EntityType interfaces
├── job-actions.ts    — Job action definitions (incl. Take Over, Inject Context)
├── step-actions.ts   — Step action definitions (V1: Rerun, Cancel, Copy)
├── flow-actions.ts   — Flow action definitions
├── canvas-actions.ts — Canvas background actions
└── index.ts          — getActionsForEntity() dispatcher
```

Each action file exports an array of `ActionDefinition` objects. The registry is a pure function — no global state, no React context needed for definitions. Actions receive an `ActionContext` (mutations, navigate, toast, clipboard, side-effect callbacks) so they can fire effects.

### Component Wrappers

```
web/src/components/menus/
├── ActionContextProvider.tsx — Page-level React context: calls useStepwiseMutations() once
├── EntityContextMenu.tsx     — Right-click wrapper (children + ContextMenu)
├── EntityDropdownMenu.tsx    — Kebab trigger (MoreVertical icon + DropdownMenu)
├── ActionMenuItems.tsx       — Shared renderer: actions → menu items (both menu types)
├── ConfirmDialog.tsx         — Reusable destructive action confirmation
└── useEntityShortcuts.ts     — Page-level keyboard shortcuts for selected entity
```

### Confirmation Flow (addresses critique #2)

```
EntityContextMenu
├── <ContextMenu>
│   ├── <ContextMenuTrigger>{children}</ContextMenuTrigger>
│   └── <ContextMenuContent>
│       └── <ActionMenuItems ... onActionRequiringConfirm={setPendingAction} />
│   </ContextMenuContent>
├── <ConfirmDialog                         ← SIBLING of ContextMenu, not child
│     open={!!pendingAction}
│     title={pendingAction.confirm.title}
│     onConfirm={() => { pendingAction.execute(...); setPendingAction(null); }}
│     onCancel={() => setPendingAction(null)}
│   />
```

State `pendingAction` lives in `EntityContextMenu` component — above the menu root. When a destructive action is clicked, the menu item sets `pendingAction` instead of executing. The menu closes (its normal behavior on item select). The `ConfirmDialog` is a sibling portal that remains mounted because `pendingAction` is held in the parent's state.

### Shared ActionContext (addresses critique #11)

```tsx
// Page level (e.g., JobDashboard.tsx):
<ActionContextProvider
  sideEffects={{
    onAfterDeleteJob: (job) => { /* clear selection, etc. */ },
  }}
>
  <JobList ... />
</ActionContextProvider>

// Inside EntityContextMenu/EntityDropdownMenu:
const ctx = useActionContext();  // reads from React context — zero hooks created
```

`ActionContextProvider` calls `useStepwiseMutations()` once, `useNavigate()` once, constructs the `ActionContext`, and provides it via `React.createContext`. All menu instances on the page share the single context value.

### Integration Points

| Surface | Component | Integration | Priority |
|---|---|---|---|
| Job list rows | `JobList.tsx` | Wrap `<div role="option">` in `<EntityContextMenu type="job">`, replace `<JobActions>` with `<EntityDropdownMenu>` | P0 |
| Job detail sidebar | `JobDetailSidebar.tsx` | Add `<EntityDropdownMenu type="job">` in header | P0 |
| Job controls bar | `JobControls.tsx` | Refactor to consume action registry for button rendering | P1 |
| Canvas job cards | `JobCard.tsx` | Wrap `<Link>` child content in `<EntityContextMenu type="job">` | P1 |
| Canvas job controls | `CanvasJobControls.tsx` | Refactor to consume action registry | P1 |
| Job tree view | `JobTreeView.tsx` | Wrap tree entries in `<EntityContextMenu type="job">` | P1 |
| Step nodes (top-level) | `StepNode.tsx` | Wrap node in `<EntityContextMenu type="step">` | P0 |
| Flow list (FlowsPage) | `FlowsPage.tsx:393-450` | Wrap flow rows in `<EntityContextMenu type="flow">` | P0 |
| Flow sidebar (Editor) | `FlowFileList.tsx` | Upgrade inline ContextMenu to `<EntityContextMenu type="flow">` | P1 |
| Canvas background | `FlowDagView.tsx` | Wrap background in `<EntityContextMenu type="canvas">` | P1 |

---

## TypeScript Interfaces

```typescript
// web/src/lib/actions/types.ts

import type { LucideIcon } from "lucide-react";

export type EntityType = "job" | "step" | "flow" | "canvas";

export interface ActionDefinition<T = unknown> {
  id: string;                                    // unique within entity, e.g. "job.retry"
  label: string;                                 // display text
  icon?: LucideIcon;                             // from lucide-react
  shortcut?: string;                             // display hint, e.g. "D", "R"
  shortcutKeys?: string[];                       // for useHotkeys, e.g. ["d"]
  variant?: "default" | "destructive";           // destructive = red styling
  group: string;                                 // grouping key for separators
  groupOrder: number;                            // sort order for group (lower = higher)
  isAvailable: (entity: T) => boolean;           // when to show
  isEnabled?: (entity: T) => boolean;            // shown but grayed if false
  confirm?: {                                    // if set, show AlertDialog before executing
    title: string;
    description: string | ((entity: T) => string);
    confirmLabel?: string;                       // default: action label
  };
  execute: (entity: T, ctx: ActionContext) => void;
  children?: ActionDefinition<T>[];              // sub-menu items (for "Copy ▸" etc.)
}

export interface ActionContext {
  mutations: ReturnType<typeof useStepwiseMutations>;
  navigate: ReturnType<typeof useNavigate>;
  clipboard: (text: string, label?: string) => void;
  sideEffects: SideEffects;
}

export interface SideEffects {
  // Job side effects
  onAfterDeleteJob?: (job: Job) => void;
  onAfterArchiveJob?: (job: Job) => void;
  // Flow side effects
  onRunFlow?: (flow: LocalFlow) => void;         // page decides: direct run vs. open dialog
  onAfterDeleteFlow?: (flow: LocalFlow) => void;  // clear selection, reset URL
  onDuplicateFlow?: (flow: LocalFlow) => void;
  // Step side effects
  onViewStepOutput?: (stepName: string) => void;  // open right panel to step tab
  // Canvas side effects
  onCreateJob?: () => void;
  onFitToView?: () => void;
  onResetZoom?: () => void;
  onToggleFollowFlow?: () => void;
  // Inject context (requires dialog state owned by page)
  onInjectContext?: (jobId: string) => void;
}
```

---

## Complete Action Catalog

### Job Actions

| ID | Label | Icon | Group (order) | Shortcut | Availability | Confirm? |
|---|---|---|---|---|---|---|
| `job.start` | Start | `Play` | lifecycle (0) | — | `status === "staged" \|\| status === "pending"` | No |
| `job.pause` | Pause | `Pause` | lifecycle (0) | — | `status === "running"` | No |
| `job.resume` | Resume | `RotateCcw` | lifecycle (0) | — | `status === "paused"` | No |
| `job.retry` | Retry | `RefreshCw` | lifecycle (0) | `r` | `status in ["paused", "failed", "completed"]` | No |
| `job.cancel` | Cancel | `XCircle` | lifecycle (0) | — | `status in ["running", "paused"]` | Yes: "Cancel this job?" |
| `job.reset` | Reset | `RotateCcw` | lifecycle (0) | — | `status in ["completed", "failed", "cancelled"]` | Yes: "Reset all step runs?" |
| `job.take-over` | Take Over | `UserCheck` | lifecycle (0) | — | `isStale(job)` (running >60s without heartbeat, CLI-owned) | No |
| `job.inject-context` | Inject Context | `MessageSquarePlus` | lifecycle (0) | — | `status === "running"` | No (opens page-owned dialog via `sideEffects.onInjectContext`) |
| `job.archive` | Archive | `Archive` | organize (10) | — | `status in ["completed", "failed", "cancelled"]` | No |
| `job.unarchive` | Unarchive | `ArchiveRestore` | organize (10) | — | `status === "archived"` | No |
| `job.copy` | Copy ▸ | `Copy` | copy (20) | — | Always (sub-menu parent) | No |
| `job.copy.id` | Copy Job ID | `Copy` | — | — | Always | No |
| `job.copy.name` | Copy Name | `Copy` | — | — | `job.name` exists | No |
| `job.copy.inputs` | Copy Inputs | `Copy` | — | — | `job.inputs` non-empty | No |
| `job.open-detail` | Open Job | `ExternalLink` | navigate (30) | `Enter` | Always | No |
| `job.open-new-tab` | Open in New Tab | `ExternalLink` | navigate (30) | — | Always (uses `window.open()`) | No |
| `job.delete` | Delete | `Trash2` | danger (100) | `d` | Always | Yes: "Delete job '{name}' permanently?" |

**Groups order:** lifecycle (0) → organize (10) → copy (20, sub-menu) → navigate (30) → danger (100)

### Step Actions (V1 — top-level steps only)

| ID | Label | Icon | Group (order) | Shortcut | Availability | Confirm? |
|---|---|---|---|---|---|---|
| `step.rerun` | Rerun Step | `RefreshCw` | lifecycle (0) | `r` | Latest run status in `[completed, failed, cancelled, skipped]` or no run | No |
| `step.cancel-run` | Cancel Run | `XCircle` | lifecycle (0) | — | Latest run status in `[running, suspended]` | Yes: "Cancel this step run?" |
| `step.copy-name` | Copy Step Name | `Copy` | copy (10) | — | Always | No |
| `step.copy-config` | Copy Step Config | `Clipboard` | copy (10) | — | Always (copies JSON of StepDefinition) | No |
| `step.view-output` | View Output | `FileOutput` | inspect (20) | — | Has completed run with artifact | No (calls `sideEffects.onViewStepOutput`) |

### Flow Actions

| ID | Label | Icon | Group (order) | Shortcut | Availability | Confirm? |
|---|---|---|---|---|---|---|
| `flow.run` | Run Flow | `Play` | lifecycle (0) | — | Always (calls `sideEffects.onRunFlow` — page decides dialog vs. direct) | No |
| `flow.edit` | Edit in Editor | `PenLine` | navigate (10) | `Enter` | Always | No |
| `flow.view-jobs` | View Recent Jobs | `List` | navigate (10) | — | Always | No |
| `flow.copy` | Copy ▸ | `Copy` | copy (20) | — | Always (sub-menu parent) | No |
| `flow.copy.path` | Copy Flow Path | `Copy` | — | — | Always | No |
| `flow.copy.name` | Copy Flow Name | `Copy` | — | — | Always | No |
| `flow.duplicate` | Duplicate Flow | `CopyPlus` | organize (30) | — | Always (calls `sideEffects.onDuplicateFlow`) | No |
| `flow.export-yaml` | Export as YAML | `Download` | organize (30) | — | Always | No |
| `flow.delete` | Delete Flow | `Trash2` | danger (100) | `d` | Always | Yes: "Delete flow '{name}'? This cannot be undone." |

### Canvas Background Actions

| ID | Label | Icon | Group (order) | Availability |
|---|---|---|---|---|
| `canvas.create-job` | Create New Job | `Plus` | actions (0) | Always (calls `sideEffects.onCreateJob`) |
| `canvas.fit-to-screen` | Fit to Screen | `Maximize2` | view (10) | Always (calls `sideEffects.onFitToView`) |
| `canvas.reset-zoom` | Reset Zoom | `ZoomIn` | view (10) | Always (calls `sideEffects.onResetZoom`) |
| `canvas.toggle-follow` | Toggle Follow Flow | `Compass` | view (10) | Always (calls `sideEffects.onToggleFollowFlow`) |

---

## Keyboard Shortcut Mapping

| Scope | Keys | Action | Implementation |
|---|---|---|---|
| Global (existing) | `g` → `j` | Navigate to Jobs | Untouched (AppLayout.tsx) |
| Global (existing) | `g` → `f` | Navigate to Flows | Untouched (AppLayout.tsx) |
| Global (existing) | `g` → `s` | Navigate to Settings | Untouched (AppLayout.tsx) |
| Global (existing) | `/` | Focus search | Untouched (AppLayout.tsx + JobList.tsx) |
| Global (existing) | `?` | Show shortcuts dialog | Untouched (AppLayout.tsx) |
| JobList (existing) | `j` / `k` | Navigate jobs | Untouched (JobList.tsx:629-671) |
| JobList (existing) | `Enter` | Open selected job | Untouched (JobList.tsx:611-614) |
| JobList (new) | `d` | Delete selected job (confirm) | `useEntityShortcuts("job", selectedJob)` — only fires when `selectedJobId` is set |
| JobList (new) | `r` | Retry selected job | Only fires when selected job is failed/paused/completed |
| JobDetailPage (existing) | `j` / `k` / `Tab` | Navigate steps | Untouched (JobDetailPage.tsx:351-400) |
| JobDetailPage (existing) | `Enter` | Open step panel | Untouched (JobDetailPage.tsx) |
| JobDetailPage (new) | `r` | Rerun selected step | Only fires when `selectedStep` is set and step is rerunnable |
| FlowsPage (new) | `d` | Delete selected flow (confirm) | Only fires when `selectedLocalFlow` is set |
| FlowsPage (new) | `Enter` | Open selected flow in editor | Only fires when `selectedLocalFlow` is set |

**Collision avoidance:** `useEntityShortcuts` uses `useHotkeys` with `enabled` gated on selection state — when no entity is selected, shortcuts are disabled. `d`/`r` don't overlap with existing `j`/`k` navigation keys.

---

## Migration Plan

### JobActions (JobList.tsx:197-276)

**Before:** Inline `JobActions` component with hardcoded `DropdownMenu` items and local `canCancel`/`canRetry`/`canArchive` helpers (lines 185-195).

**After:**
1. Extract `canCancel`, `canRetry`, `canArchive` plus new `canPause`, `canStart` to `web/src/lib/actions/job-actions.ts`.
2. Replace `JobActions` component body with `<EntityDropdownMenu type="job" data={job} />`.
3. Wrap each `<div role="option">` row with `<EntityContextMenu type="job" data={job}>`.
4. Preserve `e.stopPropagation()` on the kebab trigger to prevent row selection (matching existing pattern at `JobList.tsx:213`).
5. Delete the old `JobActions` function.

### FlowsPage flow list (FlowsPage.tsx:393-450)

**Before:** Plain `<button>` rows with click/double-click handlers. Delete via page-owned `AlertDialog` (lines 678-702).

**After:**
1. Wrap each flow `<button>` with `<EntityContextMenu type="flow" data={flow}>`.
2. The context menu's delete action (with `confirm`) replaces the page-owned `AlertDialog`. The `sideEffects.onAfterDeleteFlow` callback handles selection clearing and URL reset.
3. Remove the page-level `pendingDeleteFlow` state and `AlertDialog` — the menu system handles confirmation.
4. The "Run Flow" action calls `sideEffects.onRunFlow(flow)` which the page implements with its existing `handleRun` logic.

### FlowFileList (editor sidebar, FlowFileList.tsx:111-125)

**Before:** Inline `<ContextMenu>` with single "Delete flow" action.

**After:** Replace with `<EntityContextMenu type="flow" data={flow}>`. The editor page provides `sideEffects.onAfterDeleteFlow` and `sideEffects.onRunFlow` specific to the editor context.

### JobControls (JobControls.tsx:74-298)

**Before:** Hardcoded conditional button rendering with inline mutation calls, stale detection, and Inject Context dialog.

**After:**
1. Consume job actions from registry via `getActionsForEntity("job", job)`, filtered to the `lifecycle` group.
2. Render available lifecycle actions as `<Button>` elements (not a menu — this is a toolbar).
3. "Inject Context" remains dialog-based but triggered via `sideEffects.onInjectContext(job.id)` — the dialog itself stays in `JobDetailPage` which owns the state.
4. "Restart Step" (step-specific, not job-level) remains as a direct button keyed off `selectedStep`.

### CanvasJobControls (CanvasJobControls.tsx:37-148)

**Before:** Prop-driven action buttons with callback props (`onPause`, `onResume`, `onCancel`, `onRetry`).

**After:** Consume job actions from registry. Render available lifecycle actions as compact icon+label buttons in the absolute-positioned overlay. The parent provides mutations via `ActionContextProvider`.

---

## Step Dependency Graph

```
Step 1 (types)
├─► Step 2 (job actions)      ─► Step 2T (job action tests)
├─► Step 3 (step/flow/canvas) ─► Step 3T (step/flow/canvas tests)
└─► Step 4 (UI primitives)
     └─► Step 5 (ActionContextProvider)
          ├─► Step 6 (ConfirmDialog)     ─► Step 6T (ConfirmDialog tests)
          ├─► Step 7 (ActionMenuItems)
          │    ├─► Step 8 (EntityContextMenu)  ─► Step 8T (context menu tests)
          │    └─► Step 9 (EntityDropdownMenu) ─► Step 9T (dropdown menu tests)
          └─► Step 16 (useEntityShortcuts)

Steps 8+9 unlock all integration steps (can run in parallel):
├─► Step 10 (JobList integration)      ─► Step 10T
├─► Step 11 (FlowsPage integration)    ─► Step 11T
├─► Step 12 (StepNode integration)     ─► Step 12T
├─► Step 13 (FlowDagView canvas bg)
├─► Step 14 (Canvas JobCard + CanvasJobControls)
├─► Step 15 (JobControls migration)
├─► Step 17 (shortcuts help dialog)
└─► Step 18 (JobDetailSidebar + EditorPage FlowFileList)
```

**Parallelizable groups:**
- Steps 2, 3, 4 can run in parallel (no interdependencies)
- Steps 2T, 3T can run as soon as their action file is written
- Steps 6, 7, 16 can run in parallel (all depend only on Step 5)
- Steps 8, 9 can run in parallel (both depend on Steps 5+7)
- Steps 10–18 can ALL run in parallel (all depend only on Steps 8+9 existing)

---

## Implementation Steps

### Step 1: Action type definitions and registry core (~20 min)

**Depends on:** Nothing
**Creates:** `web/src/lib/actions/types.ts`, `web/src/lib/actions/index.ts`
**Produces:** `EntityType`, `ActionDefinition<T>`, `ActionContext`, `SideEffects` interfaces; `getActionsForEntity()` and `groupActions()` functions.

**Create** `web/src/lib/actions/types.ts`:
- Define interfaces as specified in the TypeScript Interfaces section above.

**Create** `web/src/lib/actions/index.ts`:
- `getActionsForEntity<T>(type: EntityType, data: T): ActionDefinition<T>[]` — switch on type, import the entity-specific array, filter by `isAvailable(data)`, return sorted by `groupOrder` then array order.
- `groupActions<T>(actions: ActionDefinition<T>[]): { group: string; actions: ActionDefinition<T>[] }[]` — groups actions by `group` key, preserves sort, used by renderers to insert separators.

**Verification:** `npx tsc --noEmit` passes. Both files import correctly from each other. No runtime behavior to test yet — types only.

---

### Step 2: Job action definitions (~30 min)

**Depends on:** Step 1
**Creates:** `web/src/lib/actions/job-actions.ts`
**Produces:** `JOB_ACTIONS` array, exported status predicate functions.

**Create** `web/src/lib/actions/job-actions.ts`:
- Export `canCancel(status)`, `canRetry(status)`, `canArchive(status)`, `canPause(status)`, `canStart(status)` — pure functions, same logic as `JobList.tsx:185-195` plus additions.
- Export `isStale(job)` — checks `created_by.startsWith("cli:")` + `heartbeat_at` >60s ago, matching `JobControls.tsx:37-42`.
- Export `JOB_ACTIONS: ActionDefinition<Job>[]` — all 17 actions from the catalog table.
- Each action's `execute` is a function like `(job, ctx) => ctx.mutations.resumeJob.mutate(job.id)`.
- `job.open-new-tab` uses `window.open(\`/jobs/${job.id}\`, '_blank')`.
- `job.copy.id` uses `ctx.clipboard(job.id, "Job ID")`.

**Verification:** `npx tsc --noEmit` passes. Write Step 2T (below) and run it.

---

### Step 2T: Job action tests (~30 min)

**Depends on:** Step 2
**Creates:** `web/src/lib/actions/__tests__/job-actions.test.ts`

**Test fixture — reuse existing `makeJob` pattern from `JobList.test.tsx:107-127`:**
```typescript
let jobCounter = 0;
function makeJob(overrides: Partial<Job> = {}): Job {
  jobCounter++;
  return {
    id: `job-${jobCounter}`,
    name: null,
    objective: `test-job-${jobCounter}`,
    status: "completed",
    inputs: {},
    parent_job_id: null, parent_step_run_id: null,
    workspace_path: "/tmp",
    config: { max_sub_job_depth: 3, timeout_minutes: null, metadata: {} },
    workflow: { steps: {} },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    created_by: "server", runner_pid: null, heartbeat_at: null,
    has_suspended_steps: false, job_group: null, depends_on: [],
    ...overrides,
  };
}
```

**Test cases (no mocks needed — pure functions):**

| # | Test | Input | Expected |
|---|---|---|---|
| 1 | `canRetry` includes completed | `"completed"` | `true` |
| 2 | `canRetry` includes failed | `"failed"` | `true` |
| 3 | `canRetry` excludes running | `"running"` | `false` |
| 4 | `canCancel` includes running | `"running"` | `true` |
| 5 | `canCancel` excludes completed | `"completed"` | `false` |
| 6 | `canPause` only running | `"running"` → `true`, `"paused"` → `false` |
| 7 | `canArchive` includes completed/failed/cancelled | each → `true` |
| 8 | `canArchive` excludes running/paused | each → `false` |
| 9 | `isStale` true for CLI + old heartbeat | `{ created_by: "cli:123", heartbeat_at: 120s ago, status: "running" }` | `true` |
| 10 | `isStale` false for server-owned | `{ created_by: "server", heartbeat_at: 120s ago }` | `false` |
| 11 | `isStale` false for recent heartbeat | `{ created_by: "cli:1", heartbeat_at: 5s ago }` | `false` |
| 12 | Running job → actions include pause, cancel, inject-context | `getActionsForEntity("job", makeJob({status:"running"}))` | IDs include `job.pause`, `job.cancel`, `job.inject-context` |
| 13 | Running job → no retry, no archive | same | IDs exclude `job.retry`, `job.archive` |
| 14 | Failed job → retry, archive, reset, delete | `status: "failed"` | IDs include all four |
| 15 | Completed job → retry available | `status: "completed"` | IDs include `job.retry` |
| 16 | Stale job → take-over available | `isStale` conditions | IDs include `job.take-over` |
| 17 | Archived job → unarchive, delete only (plus copy/nav) | `status: "archived"` | lifecycle group has only `job.unarchive` |
| 18 | Action grouping order | any job | groups appear in order: lifecycle, organize, copy, navigate, danger |
| 19 | Sub-menu children | `job.copy` action | `.children` has 3 items with IDs `job.copy.id`, `job.copy.name`, `job.copy.inputs` |
| 20 | `job.copy.name` hidden when name is null | `makeJob({ name: null })` | `job.copy.name` child `isAvailable` returns false |

```bash
cd web && npx vitest run src/lib/actions/__tests__/job-actions.test.ts
```

---

### Step 3: Step, flow, and canvas action definitions (~30 min)

**Depends on:** Step 1
**Creates:** `web/src/lib/actions/step-actions.ts`, `web/src/lib/actions/flow-actions.ts`, `web/src/lib/actions/canvas-actions.ts`

**`step-actions.ts`:**
- Export `STEP_ACTIONS: ActionDefinition<StepEntity>[]` where `StepEntity = { stepDef: StepDefinition; latestRun: StepRun | null; jobId: string }`.
- 5 actions from catalog. `step.rerun` calls `ctx.mutations.rerunStep.mutate(...)`. `step.view-output` calls `ctx.sideEffects.onViewStepOutput?.(...)`.

**`flow-actions.ts`:**
- Export `FLOW_ACTIONS: ActionDefinition<LocalFlow>[]` — 9 actions including Copy sub-menu children.
- `flow.run` calls `ctx.sideEffects.onRunFlow?.(flow)`.
- `flow.delete` needs the delete mutation injected via context — execute calls a flow-delete function exposed on `ActionContext` (see Step 5 for how `ActionContextProvider` injects `useDeleteFlow()`).
- `flow.export-yaml` creates a Blob download.

**`canvas-actions.ts`:**
- Export `CANVAS_ACTIONS: ActionDefinition<Record<string, never>>[]` — 4 actions, all delegate to `sideEffects`.

**Verification:** `npx tsc --noEmit` passes. Write Step 3T.

---

### Step 3T: Step/flow/canvas action tests (~20 min)

**Depends on:** Step 3
**Creates:** `web/src/lib/actions/__tests__/step-actions.test.ts`, `web/src/lib/actions/__tests__/flow-actions.test.ts`

**Step test fixture:**
```typescript
function makeStepEntity(overrides: Partial<StepEntity> = {}): StepEntity {
  return {
    jobId: "job-1",
    stepDef: { name: "my-step", executor: { type: "script", config: {} }, inputs: [], outputs: ["result"] },
    latestRun: null,
    ...overrides,
  };
}
```

**Step test cases:**

| # | Test | Input | Expected |
|---|---|---|---|
| 1 | No run → rerun available | `latestRun: null` | `step.rerun` available |
| 2 | Completed → rerun available | `latestRun.status: "completed"` | `step.rerun` available |
| 3 | Running → cancel available, rerun not | `latestRun.status: "running"` | `step.cancel-run` yes, `step.rerun` no |
| 4 | Suspended → cancel available | `latestRun.status: "suspended"` | `step.cancel-run` available |
| 5 | Copy name always available | any | `step.copy-name` available |
| 6 | View output only with artifact | `latestRun.result.artifact: {...}` | `step.view-output` available |
| 7 | View output hidden without run | `latestRun: null` | `step.view-output` not available |

**Flow test cases:**

| # | Test | Input | Expected |
|---|---|---|---|
| 1 | All actions available for standard flow | `LocalFlow` | all 9 action IDs present |
| 2 | Copy sub-menu has path and name | — | `flow.copy.children` length 2 |
| 3 | Delete is destructive variant | — | `flow.delete.variant === "destructive"` |

```bash
cd web && npx vitest run src/lib/actions/__tests__/
```

---

### Step 4: ContextMenu sub-menu and shortcut primitives (~20 min)

**Depends on:** Nothing (UI primitive layer, no action dependency)
**Modifies:** `web/src/components/ui/context-menu.tsx`

Add four new components mirroring the DropdownMenu equivalents:

- `ContextMenuShortcut` — `<span>` with `ml-auto text-xs tracking-widest text-muted-foreground`. Mirrors `DropdownMenuShortcut` (`dropdown-menu.tsx:239-253`).
- `ContextMenuSub` — wraps `ContextMenuPrimitive.SubmenuRoot` (confirmed available in @base-ui/react v1.2.0 as `ContextMenu.SubmenuRoot`).
- `ContextMenuSubTrigger` — wraps `ContextMenuPrimitive.SubmenuTrigger` with `ChevronRightIcon` suffix. Mirrors `DropdownMenuSubTrigger` (`dropdown-menu.tsx:103-125`).
- `ContextMenuSubContent` — positioned sub-menu popup, wraps Positioner + Popup. Mirrors `DropdownMenuSubContent` (`dropdown-menu.tsx:127-149`).

Export all four alongside existing exports.

**Verification:** Manual: create a throwaway test page with a ContextMenu containing a sub-menu. Right-click → sub-menu expands on hover → items inside are clickable. Then delete test page. Also: `npx tsc --noEmit`.

---

### Step 5: ActionContextProvider (~20 min)

**Depends on:** Step 1 (types)
**Creates:** `web/src/components/menus/ActionContextProvider.tsx`

```typescript
const ActionCtx = React.createContext<ActionContext | null>(null);

interface ActionContextProviderProps {
  sideEffects?: Partial<SideEffects>;
  extraMutations?: { deleteFlow?: ReturnType<typeof useDeleteFlow> };
  children: React.ReactNode;
}

export function ActionContextProvider({ sideEffects = {}, extraMutations, children }: ActionContextProviderProps) {
  const mutations = useStepwiseMutations();
  const navigate = useNavigate();

  const clipboard = useCallback((text: string, label?: string) => {
    navigator.clipboard.writeText(text);
    toast.success(label ? `Copied ${label}` : "Copied to clipboard");
  }, []);

  const ctx = useMemo<ActionContext>(() => ({
    mutations,
    navigate,
    clipboard,
    sideEffects: sideEffects as SideEffects,
    extraMutations,
  }), [mutations, navigate, clipboard, sideEffects, extraMutations]);

  return <ActionCtx.Provider value={ctx}>{children}</ActionCtx.Provider>;
}

export function useActionContext(): ActionContext {
  const ctx = React.useContext(ActionCtx);
  if (!ctx) throw new Error("useActionContext must be used within ActionContextProvider");
  return ctx;
}
```

**Verification:** `npx tsc --noEmit`. No visual output yet — consumed by Steps 7-9.

---

### Step 6: ConfirmDialog component (~15 min)

**Depends on:** Step 5 (only for co-location; no code dependency)
**Creates:** `web/src/components/menus/ConfirmDialog.tsx`

```typescript
interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel?: string;
  variant?: "default" | "destructive";
  onConfirm: () => void;
  onCancel: () => void;
}
```

Uses `AlertDialog` with controlled `open` prop (not trigger-based). `AlertDialogAction` gets `variant="destructive"` when appropriate. Mirrors the pattern already in `FlowsPage.tsx:678-702`.

**Verification:** Write Step 6T, then run it.

---

### Step 6T: ConfirmDialog tests (~15 min)

**Depends on:** Step 6
**Creates:** `web/src/components/menus/__tests__/ConfirmDialog.test.tsx`

**No mocks needed** — pure presentational.

| # | Test | Action | Expected |
|---|---|---|---|
| 1 | Renders when open | `open={true}` | Title and description visible |
| 2 | Hidden when closed | `open={false}` | Nothing rendered |
| 3 | Confirm fires callback | Click confirm button | `onConfirm` called once |
| 4 | Cancel fires callback | Click cancel button | `onCancel` called once |
| 5 | Destructive variant styling | `variant="destructive"` | Confirm button has `data-variant="destructive"` |

```bash
cd web && npx vitest run src/components/menus/__tests__/ConfirmDialog.test.tsx
```

---

### Step 7: ActionMenuItems shared renderer (~30 min)

**Depends on:** Steps 4 (ContextMenuSub/Shortcut primitives), 5 (ActionContext types)
**Creates:** `web/src/components/menus/ActionMenuItems.tsx`

Props: `actions: ActionDefinition<T>[]`, `entity: T`, `context: ActionContext`, `menuType: "context" | "dropdown"`, `onRequestConfirm: (action: ActionDefinition<T>) => void`.

Behavior:
- Groups actions via `groupActions()`, renders separator between groups.
- Per action: if `action.confirm` set → `onClick` calls `onRequestConfirm(action)`. Otherwise → calls `action.execute(entity, context)`.
- Selects ContextMenuItem vs DropdownMenuItem based on `menuType`.
- Renders icon as `<Icon className="w-3.5 h-3.5" />`, shortcut via Shortcut component.
- Actions with `children` → renders Sub/SubTrigger/SubContent with recursive items.
- Every `onClick` includes `e.stopPropagation()`.

**Verification:** `npx tsc --noEmit`. Visual testing deferred to Steps 8T/9T.

---

### Step 8: EntityContextMenu wrapper (~20 min)

**Depends on:** Steps 5, 6, 7
**Creates:** `web/src/components/menus/EntityContextMenu.tsx`

- `<EntityContextMenu type={EntityType} data={T}>{children}</EntityContextMenu>`
- Internal state: `const [pendingAction, setPendingAction] = useState<ActionDefinition<T> | null>(null)`
- Reads `useActionContext()`.
- Computes actions: `const actions = useMemo(() => getActionsForEntity(type, data), [type, data])`.
- Renders `<ContextMenu>` + `<ContextMenuTrigger asChild>` + `<ContextMenuContent>` with `<ActionMenuItems>`.
- **Sibling** `<ConfirmDialog>` with `open={!!pendingAction}` — survives menu close.
- `onConfirm` executes the action then clears pending. `onCancel` clears pending.

**Verification:** Write Step 8T, then run it.

---

### Step 8T: EntityContextMenu tests (~30 min)

**Depends on:** Step 8
**Creates:** `web/src/components/menus/__tests__/EntityContextMenu.test.tsx`

**Mock pattern — follows `JobList.test.tsx` conventions:**
```typescript
vi.mock("@/hooks/useStepwise", () => ({
  useStepwiseMutations: () => ({
    resumeJob: { mutate: vi.fn() },
    cancelJob: { mutate: vi.fn() },
    deleteJob: { mutate: mockDeleteMutate },
    archiveJob: { mutate: vi.fn() },
    // ... all mutations as { mutate: vi.fn() }
  }),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}));
```

**Render wrapper:**
```typescript
function renderWithContext(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ActionContextProvider>{ui}</ActionContextProvider>
    </QueryClientProvider>
  );
}
```

| # | Test | Action | Expected |
|---|---|---|---|
| 1 | Right-click opens menu | `fireEvent.contextMenu(trigger)` | Menu content visible with `Pause`, `Cancel` for running job |
| 2 | Running job shows correct actions | Right-click running job | Contains Pause, Cancel, Inject Context. No Retry, no Archive. |
| 3 | Failed job shows correct actions | Right-click failed job | Contains Retry, Archive, Reset. No Pause. |
| 4 | Non-destructive action executes immediately | Click "Pause" | `mutations.pauseJob.mutate` called with job ID |
| 5 | Destructive action opens ConfirmDialog | Click "Delete" | Menu closes. Dialog with "Delete job..." visible. |
| 6 | Confirm dialog survives menu close | After clicking Delete | Dialog still open (pendingAction state in parent) |
| 7 | Confirm executes then closes | Click confirm in dialog | `deleteJob.mutate` called. Dialog closes. |
| 8 | Cancel dismisses without mutation | Click cancel in dialog | No mutation called. Dialog closes. |
| 9 | Copy action calls clipboard | Click "Copy ▸" → "Copy Job ID" | `navigator.clipboard.writeText` called with job ID |
| 10 | Sub-menu renders | Right-click, hover "Copy ▸" | Sub-menu with Copy ID, Copy Name visible |

```bash
cd web && npx vitest run src/components/menus/__tests__/EntityContextMenu.test.tsx
```

---

### Step 9: EntityDropdownMenu wrapper (~15 min)

**Depends on:** Steps 5, 6, 7
**Creates:** `web/src/components/menus/EntityDropdownMenu.tsx`

Same as EntityContextMenu but `DropdownMenu` + `DropdownMenuTrigger` (MoreVertical icon). Same `pendingAction` + sibling `ConfirmDialog`. `triggerClassName` prop defaults to the styling from `JobList.tsx:212`. `onClick={(e) => e.stopPropagation()}` on trigger.

**Verification:** Write Step 9T.

---

### Step 9T: EntityDropdownMenu tests (~15 min)

**Depends on:** Step 9
**Creates:** `web/src/components/menus/__tests__/EntityDropdownMenu.test.tsx`

Same mock setup as Step 8T.

| # | Test | Action | Expected |
|---|---|---|---|
| 1 | Click kebab opens menu | Click trigger | Menu items match same running-job actions as context menu |
| 2 | Items identical to context menu | Compare action IDs | Same set, same order |
| 3 | Trigger stopPropagation | Click trigger, check parent | Parent `onClick` NOT called |
| 4 | Destructive confirm flow | Click Delete → Confirm | `deleteJob.mutate` called |

```bash
cd web && npx vitest run src/components/menus/__tests__/EntityDropdownMenu.test.tsx
```

---

### Step 10: Integrate into JobList (~30 min)

**Depends on:** Steps 8, 9
**Modifies:** `web/src/components/jobs/JobList.tsx`, `web/src/pages/JobDashboard.tsx`

**In `JobList.tsx`:**
- Delete `JobActions` function (lines 197-276) and `canCancel`/`canRetry`/`canArchive` helpers (lines 185-195).
- Import `EntityContextMenu`, `EntityDropdownMenu`.
- Wrap each `<div role="option">` with `<EntityContextMenu type="job" data={job}>`.
- Replace `<JobActions job={job} mutations={mutations} />` with `<EntityDropdownMenu type="job" data={job} />`.
- Remove `mutations` prop from the VirtualJobList component (menus get context internally).

**In `JobDashboard.tsx`:**
- Wrap `<JobList>` in `<ActionContextProvider sideEffects={{ onAfterDeleteJob: (job) => { if (selectedJobId === job.id) onSelectJob(null); } }}>`.

**Verification:** Run Step 10T. Also: `cd web && npm run lint` to catch unused imports.

---

### Step 10T: JobList integration tests (~30 min)

**Depends on:** Step 10
**Modifies:** `web/src/components/jobs/JobList.test.tsx`

Update the existing mock for `useStepwiseMutations` (already mocked at line 92). Add `ActionContextProvider` to the render wrapper.

| # | Test | Action | Expected |
|---|---|---|---|
| 1 | Right-click row opens context menu | `fireEvent.contextMenu` on job row | Menu visible with status-appropriate actions |
| 2 | Kebab menu opens dropdown | Click MoreVertical icon | Same actions as context menu |
| 3 | Right-click does NOT trigger row selection | `fireEvent.contextMenu` then check `onSelectJob` | `onSelectJob` NOT called |
| 4 | Kebab click does NOT trigger row selection | Click kebab trigger | `onSelectJob` NOT called (stopPropagation) |
| 5 | Delete → confirm → mutation fires | Right-click → Delete → Confirm | `deleteJob.mutate` called with job ID |
| 6 | Delete → cancel → no mutation | Right-click → Delete → Cancel | `deleteJob.mutate` NOT called |

```bash
cd web && npx vitest run src/components/jobs/JobList.test.tsx
```

---

### Step 11: Integrate into FlowsPage (~30 min)

**Depends on:** Steps 8, 9
**Modifies:** `web/src/pages/FlowsPage.tsx`

- Wrap flow list section in `<ActionContextProvider sideEffects={{ onRunFlow: handleRun, onAfterDeleteFlow: (flow) => { if (selectedLocalFlow?.path === flow.path) { setSelectedLocalFlow(null); navigate({ to: "/flows", search: {}, replace: true }); } } }} extraMutations={{ deleteFlow: deleteFlowMutation }}>`.
- Wrap each flow `<button>` row (lines 393-450) with `<EntityContextMenu type="flow" data={flow}>`.
- Remove `pendingDeleteFlow` state, `handleConfirmDelete`, `handleDeleteDialogChange`, and the `AlertDialog` block (lines 678-702).

**Verification:** Run Step 11T. Also: manual smoke test — right-click flow → Delete → confirm → flow removed, selection cleared.

---

### Step 11T: FlowsPage integration tests (~25 min)

**Depends on:** Step 11
**Creates:** `web/src/pages/__tests__/FlowsPage.test.tsx` (or modifies existing if present)

**Mock pattern:**
```typescript
vi.mock("@/hooks/useStepwise", () => ({
  useStepwiseMutations: () => mockMutations,
}));
vi.mock("@/hooks/useEditor", () => ({
  useLocalFlows: () => ({ data: mockFlows }),
  useDeleteFlow: () => ({ mutate: mockDeleteFlowMutate }),
  // ...
}));
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mockNavigate,
  useSearch: () => ({}),
  // ...
}));
```

| # | Test | Action | Expected |
|---|---|---|---|
| 1 | Right-click flow shows context menu | `fireEvent.contextMenu` on flow row | Menu with Run, Edit, Copy, Delete |
| 2 | Delete → confirm → mutation + side effects | Click Delete → Confirm | `deleteFlowMutate` called; `mockNavigate` called with `/flows` |
| 3 | Run flow calls sideEffect | Click "Run Flow" | `handleRun` (mocked) called with the flow |
| 4 | No page-level AlertDialog remains | After migration | No `AlertDialog` in rendered output |

```bash
cd web && npx vitest run src/pages/__tests__/FlowsPage.test.tsx
```

---

### Step 12: Integrate into StepNode (~25 min)

**Depends on:** Step 8
**Modifies:** `web/src/components/dag/StepNode.tsx`, `web/src/pages/JobDetailPage.tsx`

**In `StepNode.tsx`:**
- Add `isNested?: boolean` prop (default `false`).
- If `!isNested`, wrap the outer step node `<div>` with `<EntityContextMenu type="step" data={{ stepDef, latestRun, jobId }}>`.
- If `isNested`, render without context menu (browser default right-click).
- Keep existing hover Rerun/Cancel buttons.

**In `ExpandedStepContainer.tsx` and `ForEachExpandedContainer.tsx`:**
- Pass `isNested={true}` to child `<StepNode>` instances.

**In `JobDetailPage.tsx`:**
- Wrap the DAG area in `<ActionContextProvider sideEffects={{ onViewStepOutput: (stepName) => navigate({ search: (prev) => ({ ...prev, step: stepName, panel: "open", tab: "step" }), replace: true }) }}>`.

**Verification:** Run Step 12T.

---

### Step 12T: StepNode integration tests (~20 min)

**Depends on:** Step 12
**Modifies:** `web/src/components/dag/StepNode.test.tsx`

| # | Test | Action | Expected |
|---|---|---|---|
| 1 | Top-level step gets context menu | `fireEvent.contextMenu` on step node (isNested=false) | Menu with Rerun, Copy Name, etc. |
| 2 | Nested step gets NO context menu | `fireEvent.contextMenu` on step node (isNested=true) | No menu rendered (browser default) |
| 3 | Completed step shows View Output | Step with completed run + artifact | `step.view-output` in menu |
| 4 | Running step shows Cancel Run | Step with running run | `step.cancel-run` in menu, `step.rerun` absent |

```bash
cd web && npx vitest run src/components/dag/StepNode.test.tsx
```

---

### Step 13: Integrate into FlowDagView (canvas background) (~20 min)

**Depends on:** Step 8
**Modifies:** `web/src/components/dag/FlowDagView.tsx`

- Wrap the outer pan/zoom container with `<EntityContextMenu type="canvas" data={{}}>`.
- Add `onContextMenu={(e) => e.stopPropagation()}` on each `<StepNode>`'s `EntityContextMenu` trigger wrapper — prevents canvas menu from also opening when right-clicking a step.
- Canvas `sideEffects` provided by `JobDetailPage` via `ActionContextProvider`:
  - `onCreateJob`: open `CreateJobDialog`.
  - `onFitToView`: call `fitToView()` from `useDagCamera`.
  - `onResetZoom`: reset camera transform.
  - `onToggleFollowFlow`: toggle `followFlow` state.

**Verification:** Manual: right-click empty canvas → canvas menu. Right-click a step → step menu (canvas menu does NOT open). `npx tsc --noEmit`.

---

### Step 14: Integrate into Canvas JobCard + CanvasJobControls (~30 min)

**Depends on:** Steps 8, 9
**Modifies:** `web/src/components/canvas/JobCard.tsx`, `web/src/components/dag/CanvasJobControls.tsx`, `web/src/pages/CanvasPage.tsx`

**In `JobCard.tsx`:**
- Wrap the inner content (inside the `<Link>` wrapper) with `<EntityContextMenu type="job" data={job}>`.
- Right-click opens context menu; left-click navigates (Link handles `click` event, not `contextmenu`).

**In `CanvasJobControls.tsx`:**
- Import `getActionsForEntity` and `useActionContext`.
- Replace hardcoded conditional buttons: `const actions = getActionsForEntity("job", {...job, status: jobStatus}).filter(a => a.group === "lifecycle")`.
- Render each as a compact `<button>` preserving the existing absolute-positioned overlay styling.
- Remove the callback props (`onPause`, `onResume`, etc.) — actions execute via context.

**In `CanvasPage.tsx`:**
- Wrap canvas content in `<ActionContextProvider sideEffects={{...}}>`.

**Verification:** Manual smoke test: right-click JobCard → job context menu. CanvasJobControls buttons match context menu lifecycle actions. `npx tsc --noEmit`.

---

### Step 15: Migrate JobControls to registry (~25 min)

**Depends on:** Steps 2, 5
**Modifies:** `web/src/components/jobs/JobControls.tsx`

- Import `getActionsForEntity` and `useActionContext`.
- Replace hardcoded conditional buttons with: `const actions = getActionsForEntity("job", job).filter(a => a.group === "lifecycle")`.
- Render each as a `<Button>` with icon and label, preserving current styling from lines 74-75.
- Keep "Restart {stepName}" as a direct button (step-scoped, not in job registry).
- "Inject Context" uses `ctx.sideEffects.onInjectContext?.(job.id)` — the Inject Context dialog remains in `JobDetailPage`.
- "Take Over" renders automatically when `isStale(job)` returns true.

**Verification:** `npx tsc --noEmit`. Manual: open a running job's detail page → see same buttons as before. Open a stale job → see "Take Over". Open a completed job → see "Retry".

---

### Step 16: Entity-scoped keyboard shortcuts (~25 min)

**Depends on:** Steps 1, 5
**Creates:** `web/src/components/menus/useEntityShortcuts.ts`

```typescript
export function useEntityShortcuts<T>(
  type: EntityType,
  entity: T | null,
  ctx?: ActionContext,
): { pendingAction: ActionDefinition<T> | null; clearPending: () => void; confirmPending: () => void } {
  const [pendingAction, setPendingAction] = useState<ActionDefinition<T> | null>(null);
  const actionCtx = ctx ?? useActionContext();

  const actions = useMemo(
    () => (entity ? getActionsForEntity(type, entity).filter(a => a.shortcutKeys) : []),
    [type, entity]
  );

  const bindings = useMemo<HotkeyBinding[]>(
    () => actions.map(action => ({
      keys: action.shortcutKeys!,
      onTrigger: () => {
        if (action.confirm) setPendingAction(action);
        else action.execute(entity!, actionCtx);
      },
    })),
    [actions, entity, actionCtx]
  );

  useHotkeys(bindings, { enabled: !!entity });

  return {
    pendingAction,
    clearPending: () => setPendingAction(null),
    confirmPending: () => { pendingAction?.execute(entity!, actionCtx); setPendingAction(null); },
  };
}
```

**Integration into pages** (added alongside their ActionContextProvider):
- `JobDashboard.tsx`: `const { pendingAction, clearPending, confirmPending } = useEntityShortcuts("job", selectedJob)` + `<ConfirmDialog>` at page level.
- `JobDetailPage.tsx`: `useEntityShortcuts("step", selectedStepEntity)`.
- `FlowsPage.tsx`: `useEntityShortcuts("flow", selectedLocalFlow)`.

**Verification:** Run Step 16T.

---

### Step 16T: Keyboard shortcut tests (~25 min)

**Depends on:** Step 16
**Creates:** `web/src/components/menus/__tests__/useEntityShortcuts.test.ts`

**Uses `renderHook` from @testing-library/react.**

| # | Test | Action | Expected |
|---|---|---|---|
| 1 | `d` with selected job → pendingAction set | `fireEvent.keyDown(window, { key: "d" })` | `result.current.pendingAction.id === "job.delete"` |
| 2 | `r` with failed job → mutation called | `fireEvent.keyDown(window, { key: "r" })` | `resumeJob.mutate` called |
| 3 | `r` with running job → nothing | `fireEvent.keyDown(window, { key: "r" })` | No mutation (retry not available for running) |
| 4 | `d` with no entity → nothing | entity=null, keyDown `d` | `pendingAction` stays null |
| 5 | `d` inside input → nothing | Set `document.activeElement` to input, keyDown `d` | `pendingAction` stays null (useHotkeys editable guard) |
| 6 | `confirmPending` executes and clears | After `d` → `confirmPending()` | `deleteJob.mutate` called, `pendingAction` null |

```bash
cd web && npx vitest run src/components/menus/__tests__/useEntityShortcuts.test.ts
```

---

### Step 17: Update shortcuts help dialog (~10 min)

**Depends on:** Step 16
**Modifies:** `web/src/components/layout/AppLayout.tsx`

Add an "Entity Actions" section to the keyboard shortcuts dialog (lines 63-142):
- `D` — Delete selected item (with confirmation)
- `R` — Retry/Rerun selected item
- `Enter` — Open selected item (existing, already shown)

Note: "Active when an item is selected in a list or DAG."

**Verification:** Manual: press `?` → see new section. `npx tsc --noEmit`.

---

### Step 18: Integrate into JobDetailSidebar and EditorPage FlowFileList (~20 min)

**Depends on:** Steps 8, 9
**Modifies:** `web/src/components/jobs/JobDetailSidebar.tsx`, `web/src/components/editor/FlowFileList.tsx`, `web/src/pages/EditorPage.tsx`

**In `JobDetailSidebar.tsx`:**
- Add `<EntityDropdownMenu type="job" data={job} />` next to the job title/status area.

**In `FlowFileList.tsx`:**
- Replace the inline `<ContextMenu>` block (lines 111-125) with `<EntityContextMenu type="flow" data={flow}>`.
- Remove `onDelete` prop from the component interface.

**In `EditorPage.tsx`:**
- Wrap editor content in `<ActionContextProvider sideEffects={{ onRunFlow: () => setShowRunConfig(true), onAfterDeleteFlow: (flow) => { /* handle editor-specific cleanup */ } }} extraMutations={{ deleteFlow: deleteFlowMutation }}>`.

**Verification:** `cd web && npm run lint`. Run existing `FlowFileList.test.tsx` — update if it tests the old `onDelete` prop. Manual: right-click flow in editor sidebar → full flow menu.

---

## Testing Strategy

### Test Execution by Phase

**Phase 1 — Registry (Steps 2T, 3T):** Pure function tests, no DOM, no mocks.
```bash
cd web && npx vitest run src/lib/actions/__tests__/
```
Expected: 30+ passing tests covering all action availability predicates.

**Phase 2 — Components (Steps 6T, 8T, 9T):** Component rendering with mocked hooks.
```bash
cd web && npx vitest run src/components/menus/__tests__/
```
Expected: 25+ passing tests covering menu rendering, confirmation flow, clipboard, stopPropagation.

**Phase 3 — Integration (Steps 10T, 11T, 12T, 16T):** Full page-level tests with mocked API layer.
```bash
cd web && npx vitest run src/components/jobs/JobList.test.tsx
cd web && npx vitest run src/pages/__tests__/FlowsPage.test.tsx
cd web && npx vitest run src/components/dag/StepNode.test.tsx
cd web && npx vitest run src/components/menus/__tests__/useEntityShortcuts.test.ts
```
Expected: 20+ new passing test cases across integration points.

**Phase 4 — Full suite:**
```bash
cd web && npm run test
cd web && npm run lint
```
Expected: All existing tests still pass. Zero lint errors.

### Test Fixtures Summary

All tests reuse these shared fixture shapes (matching existing codebase patterns):

```typescript
// Job fixture — matches JobList.test.tsx:107-127 pattern
function makeJob(overrides: Partial<Job> = {}): Job { ... }

// Step entity fixture
function makeStepEntity(overrides: Partial<StepEntity> = {}): StepEntity { ... }

// Flow fixture — matches FlowFileList.test.tsx inline pattern
function makeFlow(overrides: Partial<LocalFlow> = {}): LocalFlow {
  return {
    path: "flows/test-flow", name: "test-flow", description: "",
    steps_count: 3, modified_at: new Date().toISOString(),
    is_directory: true, executor_types: ["script"],
    visibility: "interactive", ...overrides,
  };
}

// Mutation mock — matches JobList.test.tsx:84-91 pattern
const mockMutations = {
  resumeJob: { mutate: vi.fn() },
  cancelJob: { mutate: vi.fn() },
  deleteJob: { mutate: vi.fn() },
  pauseJob: { mutate: vi.fn() },
  startJob: { mutate: vi.fn() },
  resetJob: { mutate: vi.fn() },
  adoptJob: { mutate: vi.fn() },
  archiveJob: { mutate: vi.fn() },
  unarchiveJob: { mutate: vi.fn() },
  rerunStep: { mutate: vi.fn() },
  cancelRun: { mutate: vi.fn() },
  injectContext: { mutate: vi.fn() },
  // ... all mutations from useStepwiseMutations
};

// Render wrapper — matches JobList.test.tsx:130-137 pattern
function renderWithContext(ui: React.ReactElement) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <ActionContextProvider>{ui}</ActionContextProvider>
    </QueryClientProvider>
  );
}
```

### Critical Failure Mode Tests (addresses critique #12)

| Failure Mode | Test Location | Test Description |
|---|---|---|
| Dialog unmounts on menu close | Step 8T #5-6 | Click Delete → verify dialog renders after menu closes |
| Delete resets page selection | Step 11T #2 | Delete flow → verify `selectedLocalFlow` cleared, URL reset |
| Nested step gets no menu | Step 12T #2 | `isNested={true}` step → `fireEvent.contextMenu` → no menu content |
| Right-click doesn't select row | Step 10T #3 | `fireEvent.contextMenu` → verify `onSelectJob` NOT called |
| Shortcut in input field | Step 16T #5 | Focus input → press `d` → verify nothing happens |
| Shortcut with no selection | Step 16T #4 | entity=null → press `d` → verify nothing happens |
| Canvas step vs background | Manual | Right-click step → step menu. Right-click background → canvas menu. |
| JobCard Link + context menu | Manual | Right-click JobCard → context menu. Left-click → navigates. |

### Manual Smoke Tests

1. Right-click a running job in job list → see Pause, Cancel, Inject Context, Copy ▸, Open, Delete.
2. Right-click same job's kebab → identical menu.
3. Right-click a failed job → Retry, Archive, Reset, Copy ▸, Open, Delete.
4. Right-click → Delete → menu closes → confirmation dialog stays open → Cancel → nothing. Confirm → job deleted, toast.
5. Right-click → Copy ▸ → Copy Job ID → clipboard has UUID, toast.
6. Right-click a stale job → "Take Over" visible.
7. Right-click a step node in DAG → Rerun, Cancel Run (if running), Copy Name, Copy Config, View Output.
8. Right-click a nested sub-job step → no context menu (browser default).
9. Right-click a flow in FlowsPage → Run, Edit, Copy ▸, Duplicate, Export, Delete.
10. Right-click canvas background (in FlowDagView) → Create Job, Fit to Screen, Reset Zoom, Toggle Follow.
11. Right-click a JobCard on CanvasPage → job context menu; left-click still navigates.
12. Focus a job in list → press `d` → confirmation for delete. Press `r` on failed job → retry fires.
13. Focus search input → press `d` → nothing happens (editable guard).
14. No job selected → press `d` → nothing happens.

---

## Risks & Mitigations

### 1. @base-ui/react ContextMenu event conflicts with DAG pan/zoom

**Risk:** Right-click fires native `contextmenu` event which @base-ui intercepts. The DAG camera hook (`useDagCamera.ts`) uses mousedown/mousemove for panning.

**Mitigation:** `useDagCamera` should already ignore `button !== 0` (right-click is button 2). Verify this and add `if (e.button !== 0) return;` guard in `handleMouseDown` if missing. The `contextmenu` event is a separate event type from `mousedown` — they don't interfere.

### 2. Nested ContextMenu triggers (Step inside Canvas background)

**Risk:** Right-clicking a StepNode should open step menu, not canvas menu. ContextMenu events bubble.

**Mitigation:** Add `onContextMenu={(e) => e.stopPropagation()}` on the `EntityContextMenu` trigger wrapper for steps. This prevents the canvas background's context menu from also activating. Test explicitly: right-click step → step menu; right-click empty canvas → canvas menu.

### 3. JobCard Link + ContextMenu interaction on CanvasPage

**Risk:** `JobCard` is wrapped in `<Link>`. Right-click might trigger Link navigation in some browsers.

**Mitigation:** Right-click does not trigger Link's `onClick`. The `<Link>` responds to left-click (`click` event, button 0). The `contextmenu` event is separate. @base-ui's ContextMenu intercepts `contextmenu` and renders a portal — the Link is unaffected. Verify with manual testing.

### 4. Mutation hook proliferation (addressed in architecture)

**Risk:** Each menu wrapper calling `useStepwiseMutations()` would create N mutation hook sets for N visible items.

**Mitigation:** `ActionContextProvider` at page level calls mutations once. All menus consume via `useActionContext()`. Already designed into the architecture.

### 5. FlowsPage delete dialog replacement

**Risk:** Removing the page-level `AlertDialog` and replacing with per-menu `ConfirmDialog` changes behavior — the old dialog was page-level (one at a time), new one is per-menu-instance.

**Mitigation:** Only one context menu can be open at a time (browser enforces this — opening a new context menu closes the previous one). Therefore, only one `ConfirmDialog` can have `pendingAction` set at a time. No behavioral change in practice.

### 6. Existing keyboard shortcut collisions

**Risk:** New `d` and `r` shortcuts could conflict with existing `j`/`k` navigation or future shortcuts.

**Mitigation:** Entity shortcuts are registered via `useHotkeys` with `enabled` gated on `!!selectedEntity`. When navigating with `j`/`k`, the selection changes but the keypresses `j`/`k` are consumed by the navigation handler first (existing `window` listener registered in `useEffect`). `useHotkeys` registers on `window` with `capture: true` (line 139 of `useHotkeys.ts`), which fires first — but `d` and `r` don't conflict with `j`/`k`. The only risk is if someone adds a future global shortcut on `d` or `r`, which would need a prefix (e.g., `g` → `d`).

### 7. @base-ui/react ContextMenu submenu API mismatch

**Risk:** The submenu primitives (`ContextMenu.SubmenuRoot`, `ContextMenu.SubmenuTrigger`) may have a different API shape than the Menu equivalents used for DropdownMenu.

**Mitigation:** Verified that @base-ui/react v1.2.0 exports these from `context-menu/index.parts.d.ts:17-18`, backed by the same `menu/submenu-root/MenuSubmenuRoot.js` implementation. The API is shared — ContextMenu submenus and Menu submenus use identical props. Step 4 includes a manual verification gate before proceeding.
