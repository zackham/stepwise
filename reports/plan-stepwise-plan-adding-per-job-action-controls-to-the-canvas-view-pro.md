# Plan: Per-Job Action Controls on Canvas View

## Overview

The Canvas view (`CanvasPage.tsx`) currently displays job cards as read-only links to the detail page. Users must navigate away or use the CLI to pause, resume, cancel, or retry jobs. This plan adds per-job action controls directly on the canvas (hover-to-reveal buttons) and bulk actions via multi-select, keeping users in the orchestration overview while managing job lifecycle.

The existing `JobControls` component and `useStepwiseMutations` hook already implement all needed mutations (pause, resume, cancel, retry/start) with toast notifications and query invalidation. The job action menu in `JobList.tsx` provides a lighter-weight pattern (dropdown with `canCancel`/`canRetry` helpers). We'll build on both patterns.

---

## Requirements & Acceptance Criteria

### R1: Per-Job Hover Controls
- **AC1.1**: Hovering over a `JobCard` reveals a small action button toolbar (overlaid on the card, not replacing content)
- **AC1.2**: Buttons are context-aware per status:
  | Status | Buttons |
  |---|---|
  | `pending` | Start, Cancel |
  | `running` | Pause, Cancel |
  | `paused` | Resume, Cancel |
  | `completed` | Retry |
  | `failed` | Retry, Cancel |
  | `cancelled` | Retry |
  | `staged` | _(no actions)_ |
- **AC1.3**: Clicking an action button does NOT navigate to the job detail page (stops event propagation from the `<Link>`)
- **AC1.4**: Buttons show loading spinners during mutation pending state

### R2: Confirmation for Destructive Actions
- **AC2.1**: Cancel action shows a confirmation dialog before executing
- **AC2.2**: Dialog identifies the job by name and warns about in-flight steps being terminated
- **AC2.3**: Other actions (pause, resume, retry, start) execute immediately without confirmation

### R3: Visual Feedback
- **AC3.1**: After a mutation succeeds, the card's status badge and border colors update reactively (already handled by query invalidation + WebSocket)
- **AC3.2**: During mutation pending state, the triggered button shows a spinner and other buttons are disabled
- **AC3.3**: Toast notifications appear on success/failure (already handled by `useStepwiseMutations`)

### R4: Bulk Selection & Actions
- **AC4.1**: Toolbar gains a "Select" toggle that enters multi-select mode
- **AC4.2**: In select mode, clicking a card toggles its selection (checkbox overlay) instead of navigating
- **AC4.3**: A floating action bar appears when ≥1 job is selected, showing: count, available bulk actions, "Clear selection"
- **AC4.4**: Bulk actions are the intersection of actions valid for ALL selected jobs (e.g., if all are running → Pause, Cancel; mixed → only shared actions)
- **AC4.5**: Bulk cancel shows confirmation listing all affected job names
- **AC4.6**: Actions fire in parallel (`Promise.all`) with per-job error handling; partial failures show a summary toast

### R5: Keyboard & Accessibility
- **AC5.1**: Hover toolbar is also accessible via right-click context menu on each card
- **AC5.2**: Bulk select supports Shift+click for range selection within a group

---

## Assumptions (Verified Against Code)

| # | Assumption | Verified in |
|---|---|---|
| A1 | All job action API endpoints exist: start, pause, resume, cancel (no batch endpoint) | `web/src/lib/api.ts:149-164`, `server.py` routes |
| A2 | `useStepwiseMutations()` already has mutations for all needed actions with toast + invalidation | `hooks/useStepwise.ts:134-210` |
| A3 | `JobCard` is currently a `<Link>` wrapping entire card — must become conditionally non-linking in select mode | `components/canvas/JobCard.tsx:30-106` |
| A4 | No batch/bulk API endpoints exist — bulk actions must fan out client-side | Grep for "batch\|bulk" in `api.ts` and `server.py` returned no matches |
| A5 | The canvas toolbar already exists (sticky header with "Hide done" toggle) — bulk action bar can extend it | `CanvasPage.tsx:97-112` |
| A6 | `JobControls.tsx` has status-aware button rendering patterns we can extract and reuse | `components/jobs/JobControls.tsx:79-203` |
| A7 | `canCancel`/`canRetry` helpers already exist in `JobList.tsx` — should be extracted to shared utility | `components/jobs/JobList.tsx:182-188` |
| A8 | shadcn/ui `AlertDialog` is available for confirmation dialogs | Used elsewhere in codebase (`Dialog` is imported in JobControls) |
| A9 | `deleteJob` API exists but is not needed for canvas controls (delete is too destructive for hover) | `api.ts:162` |

---

## Implementation Steps

### Step 1: Extract shared action helpers

**File**: `web/src/lib/job-actions.ts` _(new)_

Extract `canCancel`, `canRetry`, and add new helpers from `JobList.tsx`'s inline functions:

```typescript
export function canStart(status: string): boolean {
  return status === "pending";
}
export function canPause(status: string): boolean {
  return status === "running";
}
export function canResume(status: string): boolean {
  return status === "paused";
}
export function canCancel(status: string): boolean {
  return status === "running" || status === "paused";
}
export function canRetry(status: string): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

export type JobAction = "start" | "pause" | "resume" | "cancel" | "retry";

export function availableActions(status: string): JobAction[] {
  const actions: JobAction[] = [];
  if (canStart(status)) actions.push("start");
  if (canPause(status)) actions.push("pause");
  if (canResume(status)) actions.push("resume");
  if (canRetry(status)) actions.push("retry");
  if (canCancel(status)) actions.push("cancel");
  return actions;
}

/** For bulk: intersection of actions valid for every job */
export function bulkAvailableActions(statuses: string[]): JobAction[] {
  if (statuses.length === 0) return [];
  const sets = statuses.map((s) => new Set(availableActions(s)));
  return availableActions(statuses[0]).filter((a) => sets.every((s) => s.has(a)));
}
```

**Then**: Update `JobList.tsx` and `JobControls.tsx` to import from `lib/job-actions.ts` instead of inline definitions. This keeps action logic DRY.

---

### Step 2: Create `JobCardActions` overlay component

**File**: `web/src/components/canvas/JobCardActions.tsx` _(new)_

A small overlay that renders context-aware action buttons for a single job card.

```
Props: { job: Job; onAction: (action: JobAction) => void; pendingAction: JobAction | null }
```

- Uses `availableActions(job.status)` to determine which buttons to show
- Renders icon-only buttons in a compact horizontal row (Lucide icons: Play, Pause, RotateCcw, XCircle)
- Each icon has a tooltip label
- `pendingAction` disables all buttons and shows spinner on the active one
- Positioned at top-right of card via absolute positioning
- Appears on hover with a short fade-in transition (`opacity-0 group-hover:opacity-100 transition-opacity`)

---

### Step 3: Create cancel confirmation dialog

**File**: `web/src/components/canvas/CancelConfirmDialog.tsx` _(new)_

Reusable `AlertDialog` for cancel confirmation. Accepts:

```
Props: {
  open: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  jobNames: string[];  // supports both single and bulk
}
```

- Single job: "Cancel **{name}**? Any in-flight steps will be terminated."
- Multiple jobs: "Cancel **{N} jobs**?" with a scrollable list of names
- Uses shadcn `AlertDialog` (import from `@/components/ui/alert-dialog`) — if not yet generated, run `npx shadcn@latest add alert-dialog`

---

### Step 4: Refactor `JobCard` to support hover overlay and select mode

**File**: `web/src/components/canvas/JobCard.tsx` _(modify)_

Changes:
1. Add props: `selectable?: boolean`, `selected?: boolean`, `onToggleSelect?: () => void`, `onAction?: (action: JobAction) => void`, `pendingAction?: JobAction | null`
2. Wrap in a `group` class div for hover detection
3. When `selectable` is true: replace `<Link>` wrapper with `<div>` + `onClick={onToggleSelect}`. Show a checkbox overlay (top-left) that reflects `selected` state. Add a blue ring when selected.
4. When `selectable` is false (default): keep `<Link>` behavior, add `<JobCardActions>` overlay that appears on hover. Actions call `onAction` which is handled by parent (stops propagation already handled since overlay is outside the Link click).
5. Wrap action buttons in `onClick={(e) => e.preventDefault(); e.stopPropagation()}` to prevent Link navigation

Structure:
```tsx
<div className="group relative">
  {selectable ? (
    <div onClick={onToggleSelect} className={cn(...)}>
      {/* checkbox overlay */}
      <Checkbox checked={selected} className="absolute top-2 left-2 z-10" />
      {/* existing card content */}
    </div>
  ) : (
    <Link to="/jobs/$jobId" params={{ jobId: job.id }} className={cn(...)}>
      {/* existing card content */}
    </Link>
  )}
  {/* Hover action overlay - shown when not in select mode */}
  {!selectable && onAction && (
    <JobCardActions job={job} onAction={onAction} pendingAction={pendingAction} />
  )}
</div>
```

---

### Step 5: Add selection state and bulk actions to `CanvasPage`

**File**: `web/src/pages/CanvasPage.tsx` _(modify)_

Add state:
```typescript
const [selectMode, setSelectMode] = useState(false);
const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
const [pendingActions, setPendingActions] = useState<Map<string, JobAction>>(new Map());
const [cancelTarget, setCancelTarget] = useState<{ ids: string[]; names: string[] } | null>(null);
```

Add handlers:
```typescript
const handleAction = async (jobId: string, action: JobAction) => {
  if (action === "cancel") {
    const job = visibleJobs.find(j => j.id === jobId);
    setCancelTarget({ ids: [jobId], names: [job?.name || job?.objective || jobId] });
    return;
  }
  setPendingActions(prev => new Map(prev).set(jobId, action));
  try {
    await executeAction(mutations, jobId, action);
  } finally {
    setPendingActions(prev => { const next = new Map(prev); next.delete(jobId); return next; });
  }
};

const handleBulkAction = async (action: JobAction) => {
  const ids = Array.from(selectedIds);
  if (action === "cancel") {
    const names = ids.map(id => {
      const j = visibleJobs.find(j => j.id === id);
      return j?.name || j?.objective || id;
    });
    setCancelTarget({ ids, names });
    return;
  }
  // Fire all in parallel
  ids.forEach(id => setPendingActions(prev => new Map(prev).set(id, action)));
  const results = await Promise.allSettled(ids.map(id => executeAction(mutations, id, action)));
  // Clear pending
  setPendingActions(prev => {
    const next = new Map(prev);
    ids.forEach(id => next.delete(id));
    return next;
  });
  // Report partial failures
  const failures = results.filter(r => r.status === "rejected");
  if (failures.length > 0) toast.error(`${failures.length}/${ids.length} jobs failed`);
  setSelectedIds(new Set());
};
```

Where `executeAction` is a small helper that calls the right mutation:
```typescript
function executeAction(mutations, jobId: string, action: JobAction): Promise<void> {
  switch (action) {
    case "start": return mutations.startJob.mutateAsync(jobId);
    case "pause": return mutations.pauseJob.mutateAsync(jobId);
    case "resume": return mutations.resumeJob.mutateAsync(jobId);
    case "cancel": return mutations.cancelJob.mutateAsync(jobId);
    case "retry": return mutations.resumeJob.mutateAsync(jobId);
  }
}
```

Toolbar changes:
- Add "Select" toggle button (checkbox-square icon from Lucide) next to "Hide done"
- When `selectMode` is on, show selection count and "Clear" button
- When exiting select mode, clear `selectedIds`

Bulk action bar:
- Render a fixed-position bar at bottom of canvas when `selectedIds.size > 0`
- Shows: `"{N} selected"`, action buttons from `bulkAvailableActions(selectedStatuses)`, "Clear selection"
- Style: `fixed bottom-6 left-1/2 -translate-x-1/2` with frosted glass background, rounded-xl, shadow

Pass to `renderCard`:
```tsx
<JobCard
  job={job}
  runs={runsMap.get(job.id) ?? []}
  dependencyNames={...}
  selectable={selectMode}
  selected={selectedIds.has(job.id)}
  onToggleSelect={() => toggleSelect(job.id)}
  onAction={(action) => handleAction(job.id, action)}
  pendingAction={pendingActions.get(job.id) ?? null}
/>
```

Cancel confirmation:
```tsx
<CancelConfirmDialog
  open={!!cancelTarget}
  jobNames={cancelTarget?.names ?? []}
  onCancel={() => setCancelTarget(null)}
  onConfirm={() => {
    const { ids } = cancelTarget!;
    setCancelTarget(null);
    // execute cancel for all ids
    ids.forEach(id => executeAction(mutations, id, "cancel"));
  }}
/>
```

---

### Step 6: Add `alert-dialog` shadcn component (if missing)

**Command**: `cd web && npx shadcn@latest add alert-dialog`

Check if it already exists first:
```bash
ls web/src/components/ui/alert-dialog.tsx
```

If missing, generate it. The `CancelConfirmDialog` depends on it.

---

### Step 7: Add `checkbox` shadcn component (if missing)

**Command**: `cd web && npx shadcn@latest add checkbox`

Needed for the selection checkbox overlay on cards in select mode.

---

### Step 8: Update `JobList.tsx` to use shared helpers

**File**: `web/src/components/jobs/JobList.tsx` _(modify)_

Replace inline `canCancel`/`canRetry` with imports from `lib/job-actions.ts`. This is a small refactor to keep things DRY — no behavior change.

---

### Step 9: Update `JobControls.tsx` to use shared helpers

**File**: `web/src/components/jobs/JobControls.tsx` _(modify)_

Replace the inline status-checking conditionals with `canPause`, `canResume`, `canCancel`, `canRetry` from `lib/job-actions.ts`. This is a refactor for consistency — no behavior change.

---

## File Summary

| File | Action | Purpose |
|---|---|---|
| `web/src/lib/job-actions.ts` | **Create** | Shared action availability helpers + bulk intersection logic |
| `web/src/components/canvas/JobCardActions.tsx` | **Create** | Hover overlay with context-aware icon buttons |
| `web/src/components/canvas/CancelConfirmDialog.tsx` | **Create** | AlertDialog for cancel confirmation (single + bulk) |
| `web/src/components/canvas/JobCard.tsx` | **Modify** | Add select mode, hover overlay anchor, group wrapper |
| `web/src/pages/CanvasPage.tsx` | **Modify** | Selection state, bulk actions, toolbar toggle, floating bar |
| `web/src/components/jobs/JobList.tsx` | **Modify** | Import shared helpers (DRY refactor) |
| `web/src/components/jobs/JobControls.tsx` | **Modify** | Import shared helpers (DRY refactor) |
| `web/src/components/ui/alert-dialog.tsx` | **Generate** | shadcn component (if missing) |
| `web/src/components/ui/checkbox.tsx` | **Generate** | shadcn component (if missing) |

---

## Testing Strategy

### Unit Tests (Vitest)

**File**: `web/src/lib/__tests__/job-actions.test.ts` _(new)_

```bash
cd web && npm run test -- --run src/lib/__tests__/job-actions.test.ts
```

Test cases:
- `availableActions("running")` → `["pause", "cancel"]`
- `availableActions("completed")` → `["retry"]`
- `availableActions("failed")` → `["retry", "cancel"]`
- `availableActions("paused")` → `["resume", "retry", "cancel"]`
- `availableActions("pending")` → `["start", "cancel"]`
- `availableActions("staged")` → `[]`
- `bulkAvailableActions(["running", "running"])` → `["pause", "cancel"]`
- `bulkAvailableActions(["running", "paused"])` → `["cancel"]`
- `bulkAvailableActions(["completed", "failed"])` → `["retry"]`
- `bulkAvailableActions([])` → `[]`

### Component Tests (Vitest + Testing Library)

**File**: `web/src/components/canvas/__tests__/JobCardActions.test.tsx` _(new)_

```bash
cd web && npm run test -- --run src/components/canvas/__tests__/JobCardActions.test.tsx
```

Test cases:
- Renders correct buttons for each status
- Calls `onAction` with correct action string
- Shows spinner when `pendingAction` matches
- Disables all buttons when any action is pending

### Integration Tests (Manual)

1. Start dev server: `cd web && npm run dev` (with `stepwise server start` running)
2. Navigate to `/canvas`
3. Verify hover reveals action buttons on each card
4. Click Pause on a running job → confirm status changes to paused
5. Click Resume on paused job → confirm resumes
6. Click Cancel → confirm dialog appears → confirm → job cancelled
7. Toggle "Select" mode → click cards → checkboxes appear → selection count shows
8. With multiple running jobs selected → click bulk Pause → all pause
9. Bulk Cancel → confirmation lists all names → confirm → all cancel
10. Exit select mode → selection clears, hover controls return

### Existing Test Suites (Regression)

```bash
cd web && npm run test          # all vitest
cd web && npm run lint          # eslint
uv run pytest tests/            # backend (no changes, but verify no breakage)
```

---

## Out of Scope

- **Batch API endpoint**: Not adding a server-side batch endpoint. Client fans out individual requests. Can be optimized later if canvas scales to hundreds of concurrent jobs.
- **Drag-to-select**: Rectangle selection is a nice-to-have but adds significant complexity. Checkbox toggle + Shift-click is sufficient for v1.
- **Right-click context menu**: Listed in R5 but deprioritized — hover overlay covers the primary use case. Can add later with `onContextMenu`.
- **Keyboard-only navigation in canvas**: Cards are in a CSS grid, not a list — keyboard nav requires spatial awareness. Defer to v2.
