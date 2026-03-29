# Plan: Visual Dependency Arrows Between Job Cards on Canvas

## Overview

The Canvas view (`/canvas`) currently renders job cards in a CSS grid with no visual indication of execution order or blocking relationships. Jobs have two relationship types — `depends_on` (explicit inter-job dependencies) and `parent_job_id` (delegation/sub-job links) — but neither is visualized as edges. With a multi-job DAG (e.g., 17-job dispatch), users can't see what's blocking what.

The codebase already contains two unused files that partially scaffold this feature:
- `web/src/components/canvas/CanvasLayout.tsx` — dagre-based spatial layout (only wires `parent_job_id`, ignores `depends_on`)
- `web/src/components/canvas/DependencyArrows.tsx` — SVG cubic Bézier edge rendering (static gray, no status awareness)

The plan is to activate and extend these files, then integrate them into `CanvasPage.tsx`, replacing the CSS grid with a dagre-positioned canvas that draws status-colored arrows between dependent job cards.

## Requirements

### R1: Draw dependency edges between job cards
- **Acceptance**: For every `depends_on` relationship between visible jobs, an SVG arrow is rendered from the upstream card to the downstream card.
- **Direction**: Arrow points from dependency → dependent (upstream → downstream).

### R2: Support both relationship types
- **Acceptance**: Both `depends_on` edges and `parent_job_id` edges are drawn. `depends_on` edges use solid styling; parent-child edges use dashed styling to distinguish delegation from explicit deps.

### R3: Status-aware edge coloring
- **Acceptance**: Edge color reflects the combined status of source and target jobs:
  - **Pending/staged**: `oklch(0.4 0 0)` (zinc gray) — neither job has started
  - **Waiting**: `oklch(0.5 0 0)` (lighter gray) — upstream running, downstream pending
  - **Active**: `oklch(0.6 0.15 250)` (blue) with animated dash — upstream completed, downstream running
  - **Completed**: `oklch(0.5 0.1 160)` (emerald) — both jobs completed
  - **Failed**: `oklch(0.6 0.15 25)` (red) — upstream or downstream failed
  - **Blocked**: `oklch(0.6 0.12 55)` (amber) — downstream paused/suspended

### R4: Dagre-positioned layout replaces CSS grid
- **Acceptance**: Cards are positioned by dagre based on the dependency graph. Jobs with no edges are still visible (placed in a row at the bottom or side). The canvas auto-sizes to fit all cards.

### R5: Group clusters preserved
- **Acceptance**: Jobs with `job_group` are visually grouped with a labeled bounding box, same as today's dashed-border sections. Dagre clusters or post-layout bounding boxes handle this.

### R6: Arrows don't obscure card content
- **Acceptance**: Arrows connect at card edges (right side → left side for LR layout, or bottom → top for TB). Edges route around cards via dagre's built-in edge routing. The SVG layer sits behind cards (z-index).

### R7: Pan and zoom for large DAGs
- **Acceptance**: When the laid-out canvas exceeds viewport, user can pan (click-drag on background) and zoom (scroll wheel). A "fit all" button resets the view. Follows existing `useDagCamera.ts` spring-physics pattern from FlowDagView.

### R8: Toggle between grid and DAG views
- **Acceptance**: A toggle in the toolbar switches between the existing grid layout (for simple browsing) and the new DAG layout (for dependency visualization). Preference persists to localStorage.

## Assumptions (verified against code)

1. **`depends_on` is a `string[]` of job IDs on every Job object** — Confirmed in `models.py:Job.depends_on`, `types.ts:Job.depends_on`, and `_serialize_job()` includes it.

2. **`parent_job_id` links sub-jobs to parents** — Confirmed in `models.py`. Canvas fetches `top_level=true` which excludes sub-jobs, so parent-child edges will only appear if we also fetch child jobs or relax the filter.

3. **`CanvasLayout.tsx` uses dagre and returns `CardPosition[]`, `CardEdge[]`, `GroupCluster[]`** — Confirmed. Currently only wires `parent_job_id` edges; needs `depends_on` added.

4. **`DependencyArrows.tsx` renders SVG cubic Bézier paths with arrowhead markers** — Confirmed. Uses a single static gray color; needs status-aware multi-layer rendering.

5. **The step-level `DagEdges.tsx` has the full edge rendering pattern** — Confirmed: multi-layer (glow + main + arrowhead), status detection, animated dashes, OKLch colors. This is the reference implementation to mirror.

6. **`useDagCamera.ts` implements spring-physics pan/zoom** — Confirmed. Can be reused directly for the canvas view.

7. **dagre is already a dependency** — Confirmed, used by `MiniDag.tsx`, `dag-layout.ts`, and `CanvasLayout.tsx`.

## Implementation Steps

### Step 1: Extend `CanvasLayout.tsx` to include `depends_on` edges
**File**: `web/src/components/canvas/CanvasLayout.tsx`

- Add `depends_on` edges alongside existing `parent_job_id` edges in `computeCanvasLayout()`.
- Tag each `CardEdge` with a `type` field: `"dependency"` | `"parent"` so the renderer can style them differently.
- Add `sourceStatus` and `targetStatus` fields to `CardEdge` for status-aware rendering.
- Change `rankdir` from `"LR"` to configurable (default `"TB"` for top-down, matching MiniDag).
- Increase `nodesep` and `ranksep` for the larger card sizes (cards are 280-300px wide).
- Compute edge connection points at card edges (bottom center → top center for TB layout) instead of right-center → left-center.

```typescript
export interface CardEdge {
  from: string;
  to: string;
  type: "dependency" | "parent";
  fromPos: { x: number; y: number };
  toPos: { x: number; y: number };
  points: Array<{ x: number; y: number }>; // dagre intermediate waypoints
}
```

### Step 2: Upgrade `DependencyArrows.tsx` with status-aware rendering
**File**: `web/src/components/canvas/DependencyArrows.tsx`

Mirror the multi-layer pattern from `DagEdges.tsx`:
- Add SVG `<defs>` with status-specific arrowhead markers: `canvas-arrow-active`, `canvas-arrow-completed`, `canvas-arrow-failed`, `canvas-arrow-blocked`.
- Add `@keyframes dash-flow` animation (identical to DagEdges).
- Accept a `jobs: Job[]` prop (or a `jobStatusMap`) to look up source/target status per edge.
- For each edge, render up to 3 layers:
  1. Glow layer (wide, low opacity) for active/failed edges
  2. Main stroke with status color, width, dash pattern
  3. Arrowhead marker matching status
- `"dependency"` edges: solid stroke. `"parent"` edges: dashed stroke (`6 4`).
- Use cubic Bézier (`C`) through dagre waypoints (reuse or adapt `buildPath()` from DagEdges).

Edge color function:
```typescript
function edgeColors(sourceStatus: JobStatus, targetStatus: JobStatus) {
  if (sourceStatus === "failed" || targetStatus === "failed")
    return { stroke: "oklch(0.6 0.15 25)", marker: "canvas-arrow-failed" };
  if (targetStatus === "running" || targetStatus === "paused")
    return { stroke: "oklch(0.6 0.15 250)", marker: "canvas-arrow-active", animate: true };
  if (sourceStatus === "completed" && targetStatus === "completed")
    return { stroke: "oklch(0.5 0.1 160)", marker: "canvas-arrow-completed" };
  // ... gray default
}
```

### Step 3: Create the DAG canvas container component
**File**: `web/src/components/canvas/CanvasDagView.tsx` (new)

This component composes `DependencyArrows` + absolutely-positioned `JobCard` components in a pan/zoom container:

- Accept `jobs: Job[]`, `runsMap: Map<string, StepRun[]>`, `jobNameMap: Map<string, string>`.
- Call `computeCanvasLayout(jobs)` → get `cards`, `edges`, `groups`, `width`, `height`.
- Render a `<div>` container with `position: relative`, sized to `width × height`.
- Render `<DependencyArrows>` as an absolute SVG overlay at z-0.
- Render group clusters as absolute `<div>` elements with dashed borders + label headers at z-1.
- Render each `JobCard` as an absolute `<div>` positioned at `(card.x, card.y)` at z-2.
- Wrap the whole thing in a pan/zoom container using `useDagCamera` from `web/src/hooks/useDagCamera.ts`.
- Add a "Fit All" button that calls `camera.fitAll()`.

### Step 4: Add view toggle to `CanvasPage.tsx`
**File**: `web/src/pages/CanvasPage.tsx`

- Add a `viewMode: "grid" | "dag"` state, persisted to `localStorage` key `"stepwise-canvas-view"`.
- Add toggle buttons in the toolbar (next to the existing hide-completed toggle): Grid icon and Network/DAG icon.
- When `viewMode === "grid"`: render the current grid layout (unchanged).
- When `viewMode === "dag"`: render the new `<CanvasDagView>` component.
- Pass the same `visibleJobs`, `runsMap`, `jobNameMap` data to both views.

### Step 5: Handle edge cases and polish

**Jobs with no edges**: Jobs with no `depends_on` and no `parent_job_id` appear as disconnected nodes. Dagre handles this — they get placed in their own rank. No special code needed.

**Hidden completed jobs**: When `hideCompleted` is on, recompute layout with only visible jobs. Edges to/from hidden jobs are excluded.

**Large DAGs (17+ jobs)**: The `cardSize()` function in `CanvasLayout.tsx` already scales down card dimensions. Combine with pan/zoom for navigation.

**Group clusters in DAG mode**: After dagre layout, compute bounding boxes for each `job_group` (already implemented in `computeCanvasLayout`). Render as labeled rounded rectangles behind the cards.

**Real-time updates**: Jobs and runs already refetch on WebSocket events (via React Query invalidation in `useStepwiseWebSocket`). Layout recomputes automatically since `computeCanvasLayout` is called inside `useMemo` with jobs as dependency.

### Step 6: Adjust edge attachment points for TB layout
**File**: `web/src/components/canvas/CanvasLayout.tsx`

For top-to-bottom layout, edges should connect:
- **Source**: bottom center of upstream card (`x + width/2, y + height`)
- **Target**: top center of downstream card (`x + width/2, y`)

Update the edge position computation in `computeCanvasLayout()` to use these points instead of the current right→left (LR) attachment.

Also use dagre's edge routing points (via `g.edge(from, to).points`) to get intermediate waypoints for smoother curves around other nodes.

## File Change Summary

| File | Action | Description |
|---|---|---|
| `web/src/components/canvas/CanvasLayout.tsx` | Modify | Add `depends_on` edges, edge type/status fields, TB attachment points, dagre waypoints |
| `web/src/components/canvas/DependencyArrows.tsx` | Modify | Multi-layer status-aware rendering, animated edges, multiple arrowhead markers |
| `web/src/components/canvas/CanvasDagView.tsx` | Create | Pan/zoom container composing layout + arrows + positioned cards + group clusters |
| `web/src/pages/CanvasPage.tsx` | Modify | Add grid/DAG toggle, render CanvasDagView in DAG mode |

## Testing Strategy

### Visual testing (manual)
```bash
# Start dev server
cd web && npm run dev
# Start backend with test jobs
uv run stepwise server start

# Create a multi-job DAG with dependencies:
uv run stepwise job create flows/example/FLOW.yaml --name "job-a" --staged --group "test-dag"
uv run stepwise job create flows/example/FLOW.yaml --name "job-b" --staged --group "test-dag"
uv run stepwise job create flows/example/FLOW.yaml --name "job-c" --staged --group "test-dag"
# Add deps: job-b depends on job-a, job-c depends on job-b
# (via API or CLI)

# Navigate to /canvas, toggle DAG view, verify:
# 1. Arrows from job-a → job-b → job-c
# 2. Gray arrows when pending
# 3. Blue animated arrows when running
# 4. Green arrows when completed
# 5. Group bounding box around all three
# 6. Pan/zoom works
# 7. Grid toggle reverts to current layout
```

### Unit tests
```bash
# Existing tests still pass
cd web && npm run test

# Layout computation test (add to existing test suite):
# - computeCanvasLayout with depends_on edges produces correct CardEdge entries
# - Edge types are correctly tagged (dependency vs parent)
# - Group clusters have correct bounding boxes
# - Empty job list returns empty layout
```

### Integration
```bash
# Full backend test suite still passes
uv run pytest tests/

# Lint passes
cd web && npm run lint
```

## Out of Scope

- **Edge labels** (e.g., showing which outputs flow between jobs) — job-level deps don't carry field-level data flow info like step-level edges do.
- **Drag-to-reposition cards** — dagre auto-layout only for now.
- **Edge click interactions** — no click-to-inspect behavior on job edges (cards themselves already link to detail pages).
- **Minimap/overview** — could be added later for very large DAGs.
