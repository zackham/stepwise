# Plan: DAG Visualization Improvements (DAG-04 & DAG-05)

## Overview

Two visual enhancements to the stepwise DAG view:

1. **DAG-04 — Animated loop-back edges during layout transitions.** Currently, `lerpLoopEdge()` in `layout-transition.ts` interpolates only the label position and snaps the SVG path to the target layout's value (line 73: `path: next.path`). Data edges interpolate smoothly because dagre provides point arrays that can be lerped element-wise. Loop edges are hand-computed from node positions in `dag-layout.ts` (lines 664-702), so the fix is to recompute the path at each interpolation frame from the already-interpolated node positions.

2. **DAG-05 — Critical path highlighting for completed/failed jobs.** Compute the longest wall-clock duration path through the DAG using `started_at`/`completed_at` on `StepRun`. Render highlighted edges and expose a toggle checkbox. Only meaningful for terminal (completed/failed) jobs.

---

## Requirements

### DAG-04: Loop Edge Animation

| # | Requirement | Acceptance Criteria |
|---|---|---|
| 1 | Loop edge SVG path animates smoothly during layout transitions | Expanding/collapsing a sub-flow shows the loop edge curving continuously rather than snapping |
| 2 | Loop edge label position continues to interpolate (existing behavior preserved) | Label slides in sync with the path |
| 3 | No visual regression on static layouts | Loop edges render identically when no transition is active (t=1) |
| 4 | Performance: no measurable frame drops | Transition still completes in 300ms at 60fps with multiple loop edges |

### DAG-05: Critical Path Highlighting

| # | Requirement | Acceptance Criteria |
|---|---|---|
| 1 | Critical path computed as longest wall-clock path through the DAG | Standard DP on topological order using `completed_at - started_at` per step |
| 2 | Highlighted edges: 3px stroke, amber-400 color | Visually distinct from default (1.5-2px) and active (blue/orange) edges |
| 3 | "Show critical path" toggle checkbox next to "Follow flow" | Checkbox only visible when job is terminal (completed or failed) |
| 4 | Toggle persists within session but defaults to off | State resets on page navigation; no localStorage |
| 5 | Critical path nodes get a subtle amber ring or border | Steps on the critical path are visually identifiable |
| 6 | Works correctly with for-each fan-out and sub-flows | Critical path considers expanded sub-job timing if available |

---

## Assumptions (verified against code)

| Assumption | Verified |
|---|---|
| `lerpLoopEdge()` currently snaps `path` to `next.path` | Yes — `layout-transition.ts:73` has comment "for now just crossfade by using next's path" |
| Loop edge path computation uses `fromNode.{x,y,width,height}` and `toNode.{x,y,width,height}` | Yes — `dag-layout.ts:684-691` |
| `lerpNode()` already provides interpolated `x`, `y`, `width`, `height` at each frame | Yes — `layout-transition.ts:29-31` |
| `lerpLayout()` processes nodes before loop edges in same function | Yes — `layout-transition.ts:100-113` (nodes at 100, loopEdges at 110) |
| `StepRun` has `started_at: string \| null` and `completed_at: string \| null` | Yes — `types.ts:192-193` |
| `latestRuns` map (keyed by step name) is available in `FlowDagView` and passed to `DagEdges` | Yes — `FlowDagView.tsx:377-386`, passed at line 865 |
| "Follow flow" checkbox pattern exists at `FlowDagView.tsx:1019-1029` | Yes — reusable UI pattern for the new toggle |
| `jobStatus` is available as a prop on `FlowDagView` | Yes — `FlowDagView.tsx:70` |
| `DagEdges` receives `edges: DagEdge[]` with point arrays and `loopEdges: LoopEdge[]` with SVG path strings | Yes — `DagEdges.tsx` props interface |
| Loop edges store `from`/`to` step names matching node IDs | Yes — `LoopEdge` interface at `dag-layout.ts:33-39` |

---

## Implementation Steps

### DAG-04: Loop Edge Path Animation

#### Step 1: Extract loop edge path computation into a reusable function

**File:** `web/src/lib/dag-layout.ts`

Extract the path computation logic from lines 682-698 into a standalone exported function:

```typescript
export function computeLoopEdgePath(
  fromNode: { x: number; y: number; width: number; height: number },
  toNode: { x: number; y: number; width: number; height: number },
  loopIndex: number,
): { path: string; labelPos: { x: number; y: number } } {
  const offset = 60 + loopIndex * 30;
  const startX = fromNode.x + fromNode.width;
  const startY = fromNode.y + fromNode.height * 0.35;
  const endX = toNode.x + toNode.width;
  const endY = toNode.y + toNode.height * 0.65;
  const midX = Math.max(startX, endX) + offset;
  const midY = (startY + endY) / 2;
  const path = `M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}`;
  return { path, labelPos: { x: midX + 4, y: midY } };
}
```

Update both call sites (flat layout ~line 212 and hierarchical layout ~line 668) to call this function instead of inlining the math.

Also add a `loopIndex` field to the `LoopEdge` interface so the transition code can recompute paths with the correct offset:

```typescript
export interface LoopEdge {
  from: string;
  to: string;
  label: string;
  path: string;
  labelPos: { x: number; y: number };
  loopIndex: number; // NEW — horizontal offset index
}
```

#### Step 2: Update `lerpLoopEdge()` to recompute path from interpolated nodes

**File:** `web/src/lib/layout-transition.ts`

Change `lerpLoopEdge()` to accept the interpolated node map and recompute the path:

```typescript
import { computeLoopEdgePath } from "./dag-layout";

function lerpLoopEdge(
  prev: LoopEdge,
  next: LoopEdge,
  t: number,
  nodeMap: Map<string, HierarchicalDagNode>,
): LoopEdge {
  const fromNode = nodeMap.get(next.from);
  const toNode = nodeMap.get(next.to);

  if (!fromNode || !toNode) {
    // Fallback: snap to next (same as current behavior)
    return { ...next, labelPos: {
      x: lerp(prev.labelPos.x, next.labelPos.x, t),
      y: lerp(prev.labelPos.y, next.labelPos.y, t),
    }};
  }

  const { path, labelPos } = computeLoopEdgePath(fromNode, toNode, next.loopIndex);
  return { ...next, path, labelPos };
}
```

Remove the old comment about crossfading.

#### Step 3: Thread interpolated nodes into `lerpLayout()` loop edge processing

**File:** `web/src/lib/layout-transition.ts`

In `lerpLayout()`, the `nodes` array is already interpolated before loop edges are processed (line 100-103 before line 110-113). Build the node map from the interpolated nodes and pass it:

```typescript
// After interpolating nodes (existing code)
const nodes = next.nodes.map(n => { ... });

// Build map from INTERPOLATED nodes for loop edge path recomputation
const interpNodeMap = new Map(nodes.map(n => [n.id, n]));

const loopEdges = next.loopEdges.map(e => {
  const pe = prevLoopMap.get(`${e.from}->${e.to}`);
  return pe ? lerpLoopEdge(pe, e, t, interpNodeMap) : e;
});
```

This is the key insight: nodes are lerped first, then loop edges derive their paths from those interpolated positions — exactly mirroring how `computeHierarchicalLayout` computes loop edges from final node positions.

---

### DAG-05: Critical Path Highlighting

#### Step 4: Create `critical-path.ts` computation module

**File:** `web/src/lib/critical-path.ts` (new)

```typescript
import type { StepRun } from "./types";
import type { FlowDefinition } from "./types";

export interface CriticalPathResult {
  /** Step names on the critical path, in topological order */
  steps: Set<string>;
  /** Edges on the critical path as "from->to" keys */
  edges: Set<string>;
  /** Total wall-clock duration in ms */
  totalDurationMs: number;
}

export function computeCriticalPath(
  workflow: FlowDefinition,
  latestRuns: Record<string, StepRun>,
): CriticalPathResult | null
```

**Algorithm:**
1. Build adjacency list from `workflow.steps` (inputs define edges: each `InputBinding.source_step` → current step).
2. Topological sort the step names.
3. For each step in topo order, compute `duration = Date.parse(completed_at) - Date.parse(started_at)`. Skip steps without both timestamps.
4. DP: `longestTo[step] = max(longestTo[dep] + duration[step])` for all deps. Track predecessor for backtracking.
5. Find terminal step with max `longestTo` value.
6. Backtrack predecessors to build the critical path set of steps and edges.
7. Return `null` if fewer than 2 steps have timing data (not meaningful).

**Edge cases:**
- Steps with `null` timing → duration 0, still participate in path if they have completed runs.
- For-each steps → use the longest-running instance's timing for the parent step.
- Steps not in `latestRuns` → skip (they weren't executed).

#### Step 5: Add critical path state and toggle to `FlowDagView.tsx`

**File:** `web/src/components/dag/FlowDagView.tsx`

1. Add state:
```typescript
const [showCriticalPath, setShowCriticalPath] = useState(false);
```

2. Compute critical path (memoized):
```typescript
const criticalPath = useMemo(() => {
  if (!showCriticalPath) return null;
  const isTerminal = jobStatus === "completed" || jobStatus === "failed";
  if (!isTerminal) return null;
  return computeCriticalPath(workflow, latestRuns);
}, [showCriticalPath, jobStatus, workflow, latestRuns]);
```

3. Pass `criticalPath` to `DagEdges` and `StepNode` components.

4. Add toggle checkbox next to "Follow flow" (line ~1019), conditionally rendered:
```tsx
{(jobStatus === "completed" || jobStatus === "failed") && (
  <label className="flex items-center gap-1.5 bg-white/80 dark:bg-zinc-900/80 rounded-md border border-zinc-300/50 dark:border-zinc-700/50 px-2 py-1 cursor-pointer select-none min-h-[44px] md:min-h-0">
    <input
      type="checkbox"
      checked={showCriticalPath}
      onChange={(e) => setShowCriticalPath(e.target.checked)}
      className="accent-amber-400 w-3 h-3"
    />
    <span className="text-zinc-400 text-xs">Critical path</span>
  </label>
)}
```

#### Step 6: Add critical path edge styling to `DagEdges.tsx`

**File:** `web/src/components/dag/DagEdges.tsx`

1. Add `criticalPath?: CriticalPathResult | null` to props.

2. For data edges: when an edge key (`${from}->${to}`) is in `criticalPath.edges`, render an additional amber highlight layer:
```tsx
{isCritical && (
  <path
    d={buildSmoothPath(edge.points)}
    fill="none"
    stroke="oklch(0.8 0.15 85)" // amber-400 equivalent
    strokeWidth={3}
    opacity={0.7}
    strokeLinecap="round"
  />
)}
```

Render this layer below the normal edge but above the glow, so it's visible but doesn't obscure edge labels.

3. For loop edges: same treatment — if `${le.from}->${le.to}` is in the critical path edges set, apply amber 3px stroke.

#### Step 7: Add critical path node indicator to `StepNode`

**File:** `web/src/components/dag/StepNode.tsx`

Add an optional `isCritical` prop. When true, apply a subtle amber border ring:

```tsx
className={cn(
  "...",
  isCritical && "ring-1 ring-amber-400/60"
)}
```

Pass from `FlowDagView`: `isCritical={criticalPath?.steps.has(node.id) ?? false}`.

---

## Testing Strategy

### DAG-04: Loop Edge Animation

**Manual verification (primary):**
1. Open a job with loop edges (any flow with `exits: action: loop`)
2. Expand/collapse a sub-flow step while loop edges are visible
3. Verify loop edge paths curve smoothly during the 300ms transition instead of snapping
4. Verify label position still animates in sync
5. Verify static render (no transition in progress) is pixel-identical to before

**Unit test:**
```bash
cd web && npm run test
```
Add a test in a new file `web/src/lib/__tests__/layout-transition.test.ts`:
- Test `computeLoopEdgePath()` produces correct SVG path for known node positions
- Test that calling it with interpolated positions (midpoint between two layouts) produces a path whose control points are between the two endpoints

### DAG-05: Critical Path Highlighting

**Unit tests** (new file `web/src/lib/__tests__/critical-path.test.ts`):

```bash
cd web && npm run test -- --run critical-path
```

Test cases:
1. **Linear DAG, 3 steps** — critical path is the only path, all 3 steps and 2 edges included
2. **Diamond DAG (A→B, A→C, B→D, C→D)** — critical path follows the slower branch
3. **Missing timing data** — steps without `completed_at` get duration 0, path still computed
4. **Single step** — returns `null` (not meaningful)
5. **No completed steps** — returns `null`

**Manual verification:**
1. Run a multi-step flow to completion
2. Open the job detail DAG view
3. Verify "Critical path" checkbox appears only after job completes
4. Toggle on — amber edges and node rings appear on the longest-duration path
5. Toggle off — visualization returns to normal
6. Navigate to a running job — verify checkbox is hidden

**Existing tests (regression):**
```bash
cd web && npm run test       # all frontend tests pass
cd web && npm run lint        # no lint errors
uv run pytest tests/          # backend unaffected
```

---

## File Change Summary

| File | Change Type | DAG |
|---|---|---|
| `web/src/lib/dag-layout.ts` | Edit — extract `computeLoopEdgePath()`, add `loopIndex` to `LoopEdge` | DAG-04 |
| `web/src/lib/layout-transition.ts` | Edit — recompute path in `lerpLoopEdge()` from interpolated nodes | DAG-04 |
| `web/src/lib/critical-path.ts` | **New** — `computeCriticalPath()` DP algorithm | DAG-05 |
| `web/src/components/dag/FlowDagView.tsx` | Edit — add `showCriticalPath` state, toggle UI, pass to children | DAG-05 |
| `web/src/components/dag/DagEdges.tsx` | Edit — amber edge highlight layer when critical path active | DAG-05 |
| `web/src/components/dag/StepNode.tsx` | Edit — optional amber ring for critical path nodes | DAG-05 |
| `web/src/lib/__tests__/layout-transition.test.ts` | **New** — unit test for `computeLoopEdgePath` | DAG-04 |
| `web/src/lib/__tests__/critical-path.test.ts` | **New** — unit tests for critical path computation | DAG-05 |

---

## Implementation Order

1. **DAG-04 first** — smaller scope, self-contained, no new files except test
2. **DAG-05 second** — builds on familiarity with edge rendering from DAG-04

Estimated diff: ~250 lines added, ~30 lines modified across 8 files.
