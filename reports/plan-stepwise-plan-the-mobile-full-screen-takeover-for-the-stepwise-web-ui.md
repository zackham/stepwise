# Plan: Mobile Full-Screen Takeover

## Overview

Replace all Sheet/slide-over/peek sidebar patterns in the mobile UI with full-screen takeover views. On mobile (`< 768px`), every "detail" level occupies the entire viewport with a back button to return to the list. No split panels, no partial overlays, no sliding drawers.

The current implementation uses `<Sheet side="right">` components that slide in at `w-[90vw]` on mobile — these must all become route-based or state-based full-screen views. Desktop layout (`>= 768px`) is unchanged.

---

## Requirements

### R1: Jobs — Full-Screen List → Full-Screen Detail
- **Mobile**: `/jobs` shows the job list full-screen (no sidebar placeholder). Tapping a job navigates to `/jobs/$jobId` which renders the detail view full-screen with a Back button to `/jobs`.
- **Desktop**: Unchanged (left sidebar list + center DAG + right panel).
- **Acceptance criteria**:
  - At `< 768px`, no `<Sheet>` component renders on the jobs pages.
  - Job list fills the viewport on `/jobs`.
  - Job detail fills the viewport on `/jobs/$jobId` (no visible job list behind it).
  - Back button in the detail header navigates to `/jobs`.
  - Right panel content (step detail, data flow, job info) renders inline below the DAG or in a scrollable section — no Sheet.

### R2: Flows — Full-Screen List → Full-Screen Detail
- **Mobile**: `/flows` shows the flow list full-screen. Selecting a flow shows a full-screen preview (DAG + metadata + Run/Edit buttons). Registry tab: selecting a flow shows full-screen `FlowInfoPanel`.
- **Desktop**: Unchanged.
- **Acceptance criteria**:
  - At `< 768px`, no `<Sheet>` renders on the flows page.
  - Local flow preview is full-screen (replaces the list, with Back to return).
  - Registry flow detail is full-screen (replaces the list, with Back to return).

### R3: Editor — Full-Screen Panels
- **Mobile**: File tree, step inspector, and chat sidebar each become full-screen views (not Sheets).
- **Desktop/Compact**: Unchanged (Sheets at `< 1024px` are fine since the spec targets `< 768px`).
- **Acceptance criteria**:
  - At `< 768px`, file tree / step inspector / chat are full-screen overlays with Back/Close button, not sliding Sheets.

### R4: Job Timeline — Full-Screen Step Detail
- **Mobile**: Step detail panel renders full-screen (not Sheet).
- **Acceptance criteria**:
  - At `< 768px`, selecting a step shows full-screen detail with Back button.

### R5: Navigation
- **Current**: Top header bar with icon-only nav items on mobile — this is fine and can stay.
- **Acceptance criteria**:
  - Navigation remains accessible on all full-screen views.
  - Back buttons do not conflict with top nav.

### R6: No Remaining Sheet/Peek/Sidebar on Mobile
- **Acceptance criteria**:
  - `grep -r "Sheet" web/src/pages/` shows zero mobile-conditional Sheet usage.
  - `useIsMobile()` is never used to gate `<Sheet>` rendering.
  - The expanded step overlay Sheet in `JobDetailPage` (line 678) is also replaced with a full-screen view on mobile.

---

## Assumptions (verified against code)

| Assumption | Verified |
|---|---|
| Mobile breakpoint is `max-width: 767px` via `useIsMobile()` | Yes — `web/src/hooks/useMediaQuery.ts:21` |
| `JobDashboard` already navigates to `/jobs/$jobId` on mobile (no split) | Yes — `JobDashboard.tsx:36` navigates, line 41 hides right placeholder with `hidden md:flex` |
| `JobDetailPage` uses `<Sheet>` for right panel on mobile | Yes — `JobDetailPage.tsx:652-667` |
| `JobDetailPage` uses `<Sheet>` for expanded step overlay (all viewports) | Yes — `JobDetailPage.tsx:678-690` |
| `FlowsPage` uses `<Sheet>` for registry detail on mobile | Yes — `FlowsPage.tsx:544-562` |
| `FlowsPage` hides the DAG preview on mobile for local flows | Yes — `!isMobile` gates the preview panel |
| `EditorPage` uses `<Sheet>` for file tree, step inspector, and chat on compact/mobile | Yes — uses `useMediaQuery("(max-width: 1023px)")` |
| `JobTimelinePage` uses `<Sheet>` for step detail on mobile | Yes — confirmed by search |
| Top nav stays visible on all pages (no bottom tab bar needed) | Yes — `AppLayout.tsx:272` fixed header |
| TanStack Router is used for all routing | Yes — `@tanstack/react-router` |

---

## Implementation Steps

### Step 1: Create a `MobileFullScreen` wrapper component

**File**: `web/src/components/layout/MobileFullScreen.tsx` (new)

A simple wrapper that renders its children as a full-screen overlay on mobile, with a back button header. This replaces the Sheet pattern across all pages.

```tsx
interface MobileFullScreenProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
}
```

When `open` is true and viewport is mobile: renders an absolutely positioned `div` covering the full viewport below the header (`top: 3rem` or `inset-0` depending on whether nav should remain visible — nav should remain visible). Includes a back-arrow + title header row. When `open` is false or viewport is desktop: renders nothing (desktop code paths handle their own layout).

### Step 2: JobDetailPage — Replace right panel Sheet with inline/full-screen

**File**: `web/src/pages/JobDetailPage.tsx`

**Changes**:
1. **Right panel (lines 652–667)**: Replace the `isMobile ? <Sheet>` branch with `isMobile ? <MobileFullScreen>`. The panel content (StepDetailPanel, DataFlowPanel, job info) renders full-screen over the DAG.
2. **Expanded step overlay (lines 678–690)**: On mobile, replace `<Sheet>` with `<MobileFullScreen>`. On desktop, keep the Sheet (it's useful for the wide overlay).
3. **Left sidebar**: Already hidden on mobile — no change needed.
4. **Back button**: Already exists via `<Breadcrumb>` with "Jobs" link. Verify it's tappable (44px target).

**Mobile flow**: User sees full-screen DAG → taps a step → full-screen StepDetailPanel appears with back/close button → close returns to DAG.

### Step 3: FlowsPage — Replace registry Sheet + add local flow full-screen preview

**File**: `web/src/pages/FlowsPage.tsx`

**Changes**:
1. **Registry tab (lines 544–562)**: Replace `isMobile && <Sheet>` with `isMobile && <MobileFullScreen>`. When a registry flow is selected on mobile, show `FlowInfoPanel` full-screen.
2. **Local flows tab**: Currently the DAG preview is simply hidden on mobile (`!isMobile`). Instead, when a local flow is selected on mobile, show a full-screen preview view (flow name + metadata + DAG + Run/Edit buttons) via `<MobileFullScreen>`. Close returns to the flow list.
3. Add state: `showMobileFlowPreview` to track whether the mobile full-screen preview is open.

**Mobile flow**: User sees full-screen flow list → taps a flow → full-screen preview with DAG, metadata, Run/Edit → back returns to list.

### Step 4: EditorPage — Full-screen panels on mobile

**File**: `web/src/pages/EditorPage.tsx`

**Changes**:
1. The editor uses `useMediaQuery("(max-width: 1023px)")` for its compact breakpoint. For the `< 768px` case specifically, replace Sheet usage with `<MobileFullScreen>`:
   - **File tree** (left Sheet): Full-screen file browser with back button.
   - **Step inspector** (right Sheet): Full-screen step definition panel with back button.
   - **Chat sidebar** (right Sheet): Full-screen chat with back button.
2. Add `useIsMobile()` alongside the existing `isCompact` query. When `isMobile`, use `MobileFullScreen` instead of `Sheet`. When `isCompact && !isMobile` (768px–1023px), keep the existing Sheet behavior.

### Step 5: JobTimelinePage — Full-screen step detail on mobile

**File**: `web/src/pages/JobTimelinePage.tsx`

**Changes**:
1. Replace the `isMobile ? <Sheet>` for step detail with `<MobileFullScreen>`.
2. When a step is selected on mobile, the detail panel covers the full viewport. Close returns to timeline.

### Step 6: Audit and remove remaining mobile Sheet patterns

**Files**: All pages in `web/src/pages/`

1. Search for all `isMobile` + `<Sheet>` patterns. Ensure none remain.
2. The `<Sheet>` component itself stays — it's still used on desktop and for dialogs. Only the mobile-conditional usage is removed.
3. Check `CanvasPage.tsx` and `SettingsPage.tsx` for any Sheet patterns (unlikely but verify).

### Step 7: Verify navigation consistency

**File**: `web/src/components/layout/AppLayout.tsx`

1. Ensure the top nav header remains visible in all full-screen takeover views. The `MobileFullScreen` component should position below the header (use `fixed inset-x-0 top-12 bottom-0` or equivalent).
2. Verify back buttons don't overlap with the nav bar.
3. Verify breadcrumbs still work on mobile detail pages (they already collapse via `Breadcrumb.tsx`).

---

## Testing Strategy

### Manual testing (primary — visual/interaction)

```bash
# Start dev server
cd web && npm run dev
# Start backend
uv run stepwise server start
```

1. Open Chrome DevTools → toggle device toolbar → select a mobile preset (iPhone 14, 390px wide).
2. **Jobs flow**: Navigate to `/jobs` → verify full-screen list → click a job → verify full-screen detail → click a step → verify full-screen step panel → close → back to DAG → breadcrumb "Jobs" → back to list.
3. **Flows flow**: Navigate to `/flows` → verify full-screen flow list → click a local flow → verify full-screen preview with DAG → back to list. Switch to Registry tab → click a flow → verify full-screen FlowInfoPanel → back to list.
4. **Editor flow**: Navigate to a flow editor → toggle file tree → verify full-screen → close. Click a step → verify full-screen inspector → close. Toggle chat → verify full-screen → close.
5. **Timeline flow**: Navigate to `/jobs/$jobId/timeline` → click a step → verify full-screen detail → close.
6. **Resize test**: Start at mobile width, verify full-screen behavior. Resize to `> 768px`, verify desktop layout returns (no stuck full-screen views).
7. **No Sheet remnants**: At mobile width, verify no sliding panels appear anywhere. All detail views should animate in place (no slide-from-right).

### Automated tests

```bash
cd web && npm run test
```

No new component tests are strictly needed — the `MobileFullScreen` component is simple enough that manual testing covers it. Existing tests should continue to pass since desktop behavior is unchanged.

### Lint

```bash
cd web && npm run lint
```

### Grep verification

```bash
# After implementation, verify no mobile-conditional Sheet usage remains in pages
cd web && grep -rn "isMobile.*Sheet\|Sheet.*isMobile" src/pages/
# Should return zero results
```

---

## Files Changed (Summary)

| File | Change |
|---|---|
| `web/src/components/layout/MobileFullScreen.tsx` | **New** — full-screen overlay wrapper |
| `web/src/pages/JobDetailPage.tsx` | Replace Sheet→MobileFullScreen for right panel + expanded step |
| `web/src/pages/FlowsPage.tsx` | Replace Sheet→MobileFullScreen for registry detail; add full-screen local flow preview |
| `web/src/pages/EditorPage.tsx` | Replace Sheet→MobileFullScreen for file tree, step inspector, chat (at `< 768px` only) |
| `web/src/pages/JobTimelinePage.tsx` | Replace Sheet→MobileFullScreen for step detail |
| `web/src/components/layout/AppLayout.tsx` | Possibly minor z-index or positioning adjustments to ensure full-screen views layer correctly |

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Full-screen views feel jarring without transitions | Add a simple `animate-fade-in` or slide-up transition to `MobileFullScreen` |
| DAG becomes unreachable behind full-screen panels | Back button always visible; pressing hardware back or nav items also closes panel |
| Scroll position lost when toggling full-screen views | Panels manage their own scroll; parent scroll position is preserved via `overflow-hidden` |
| EditorPage compact breakpoint (1024px) conflicts with mobile breakpoint (768px) | Use `useIsMobile()` separately — MobileFullScreen only activates at `< 768px`, Sheet stays for 768–1023px range |
