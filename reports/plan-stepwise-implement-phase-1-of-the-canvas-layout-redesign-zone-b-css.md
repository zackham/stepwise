# Plan: Phase 1 Canvas Layout Redesign — Zone B CSS Grid for Independent Jobs

## Overview

Replace the current binary layout in `CanvasPage.tsx` — which renders *all* jobs in Dagre DAG mode or *all* in grid mode based on whether any `depends_on` edge exists — with a hybrid layout. Independent jobs (no relationship to any dependency edge or parent-child link) flow into a responsive CSS Grid (Zone B). Jobs participating in dependency chains stay in the existing Dagre DAG (Zone A). Both zones render simultaneously when mixed. Status-based sort ensures running/active jobs appear first in the grid.

## Requirements

| # | Requirement | Acceptance Criteria |
|---|-------------|-------------------|
| R1 | Independent jobs render in a responsive CSS grid | Jobs with no dependency relationships appear in a `grid-template-columns: repeat(auto-fill, minmax(300px, 1fr))` container with `gap: 16px`. Verified by: at 1920px viewport width the grid produces 6 columns (1920 - 48px padding = 1872px available, `floor(1872 / 316) = 5` columns minimum with the `1fr` expansion filling remaining space). |
| R2 | Dependent jobs remain in existing DAG layout | Jobs connected by `depends_on` or `parent_job_id` edges render with Dagre absolute positioning and SVG bezier arrows via `DependencyArrows`. The card lookup in the DAG section uses `dependentJobs` (not `visibleJobs`) so `.find()` searches the correct partition. |
| R3 | Both zones visible simultaneously | When the job list contains a mix of dependent and independent jobs, Zone A (Dagre DAG) renders above Zone B (CSS grid) in a single vertical scroll container (`h-full overflow-y-auto`, as currently set on the root `<div>` at `CanvasPage.tsx:144`). |
| R4 | Status-based sort order in Zone B | Grid jobs sorted by status priority: running (0) > paused (1) > pending (2) > failed (3) > staged (4) > completed (5) > cancelled (6) > archived (7). Tiebreaker: descending `created_at` (most recent first). Sort applies within each group section and to ungrouped jobs. |
| R5 | Cards unchanged | `JobCard` component (`web/src/components/canvas/JobCard.tsx`) receives the same props (`job`, `runs`, `dependencyNames`, `isGroupQueued`). No prop additions, no card CSS changes. |
| R6 | Group sections preserved in Zone B | Independent jobs with non-null `job_group` render in per-group sections with dashed border, completion counter, and `+`/`-` concurrency controls — identical to the current grid path at `CanvasPage.tsx:252-306`. |
| R7 | "Hide completed" toggle applies globally | The existing `hideCompleted` state at `CanvasPage.tsx:16` filters `visibleJobs` from which both `dependentJobs` and `independentJobs` are derived. Both zones reflect the filter. |
| R8 | Empty zones hidden | Zone A renders only when `dependentJobs.length > 0`. Zone B renders only when `independentJobs.length > 0`. When both are empty (all filtered out), the existing "No jobs yet" empty state shows. |
| R9 | Partition correctness: both ends of edges stay in Zone A | If Job A has empty `depends_on` but Job B has `depends_on: [A.id]`, both A and B must appear in Zone A. Same for `parent_job_id` links: if Job C has `parent_job_id: D.id`, both C and D appear in Zone A. No dangling arrows. |

## Assumptions (verified against actual code)

| # | Assumption | Verified in | How verified |
|---|-----------|-------------|-------------|
| A1 | `Job.depends_on` is `string[]`, always present (may be `[]`) | `web/src/lib/types.ts:247` | Read the interface: `depends_on: string[]` — not optional, no `?` |
| A2 | `Job.parent_job_id` is `string \| null` | `web/src/lib/types.ts:235` | Read the interface: `parent_job_id: string \| null` |
| A3 | `Job.job_group` is `string \| null` | `web/src/lib/types.ts:246` | Read the interface: `job_group: string \| null` |
| A4 | `CanvasPage` is the sole component at `/canvas` route | `web/src/router.tsx` | Route definition maps `/canvas` → lazy `CanvasPage` import |
| A5 | `computeCanvasLayout` builds edges from both `depends_on` and `parent_job_id` | `web/src/components/canvas/CanvasLayout.tsx:71-92` | Lines 73-79 iterate `depends_on`; lines 84-91 add `parent_job_id` edges |
| A6 | The grid CSS class `grid-cols-[repeat(auto-fill,minmax(300px,1fr))]` already exists in the codebase | `CanvasPage.tsx:289,302` | Used in the current grid fallback branch |
| A7 | `JobCard` is a pure display component — does not read context, router params, or layout ancestors | `web/src/components/canvas/JobCard.tsx` | It's a `memo()` function component accepting 4 props, rendering a `<Link>` |
| A8 | `runsQueries` fetches runs for all `visibleJobs` and `runsMap` indexes by job ID | `CanvasPage.tsx:47-62` | Both derived from `visibleJobs`, so both partitions get runs data without changes |
| A9 | `JobStatus` type has exactly 8 values | `web/src/lib/types.ts:3-11` | `staged \| pending \| running \| paused \| completed \| failed \| cancelled \| archived` |

## Architecture

### Current architecture (before change)

```
CanvasPage
├── visibleJobs = jobs filtered by hideCompleted
├── computeCanvasLayout(visibleJobs) → Dagre positions for ALL jobs
├── hasDeps = layout.edges.length > 0
└── TERNARY:
    ├── hasDeps=true  → DAG view (absolute-positioned cards + SVG arrows)
    └── hasDeps=false → Grid view (CSS grid, grouped by job_group)
```

**Problem:** The ternary means a *single* dependency edge among 100 jobs forces all 100 into Dagre — no grid for the 98 independent ones.

### Target architecture (after change)

```
CanvasPage
├── visibleJobs = jobs filtered by hideCompleted          (unchanged)
├── partitionJobs(visibleJobs) → { dependentJobs, independentJobs }  (NEW)
│   └── Uses connected-component approach: both ends of every
│       depends_on + parent_job_id edge go into dependentJobs
├── computeCanvasLayout(dependentJobs) → Dagre positions  (input changed)
├── sortByStatus(independentJobs) → sorted for grid       (NEW)
├── groupByJobGroup(sortedIndependent) → grouped/ungrouped (input changed)
└── SEQUENTIAL:
    ├── Zone A: dependentJobs.length > 0 → DAG view       (conditional)
    └── Zone B: independentJobs.length > 0 → Grid view    (conditional)
```

### Why partition logic lives in `CanvasPage.tsx` (not a utility)

The partition is a view concern — it determines *which rendering path* a job takes. Following the existing pattern in this file where all data derivations are inline `useMemo` hooks (see `groupSettings`, `groupInfoMap`, `groupQueuedSet`, `runsMap`, `grouped`/`ungrouped` — all at `CanvasPage.tsx:19-110`), the partition memo belongs here. Extracting it to a utility file would:
1. Break the colocation pattern every other memo in this file follows
2. Be premature — the function is ~15 lines with no reuse elsewhere

If the partition logic later needs sharing (e.g., a canvas minimap), extract then.

### Why `computeCanvasLayout` is called with only `dependentJobs`

`computeCanvasLayout` (`CanvasLayout.tsx:54`) creates Dagre nodes for every job passed in. Independent jobs passed to Dagre would become disconnected nodes, wasting space in the graph layout (Dagre allocates a full row/column per disconnected node). By partitioning first, Dagre's bounding box tightly wraps only the connected subgraph.

### Data flow integrity

```
visibleJobs ──┬── runsQueries (fetches runs for ALL visible)
              ├── runsMap     (indexes by job ID for ALL visible)
              ├── groupQueuedSet (checks ALL visible for group limits)
              ├── jobNameMap  (maps ALL job IDs to names)
              │
              ├── dependentJobs ──→ computeCanvasLayout() ──→ Zone A render
              └── independentJobs ──→ sortByStatus ──→ groupByJobGroup ──→ Zone B render
```

`runsMap`, `groupQueuedSet`, and `jobNameMap` remain derived from `visibleJobs` (superset), so both zones have access to shared data. No prop changes to `JobCard`.

## Implementation Steps

### Step 1: Extract partition logic (~15 min)

**File:** `web/src/pages/CanvasPage.tsx`

**Location:** Insert after the `visibleJobs` memo (line 44) and before `runsQueries` (line 47).

Add a `useMemo` that computes the set of all job IDs participating in any dependency or parent-child relationship, then splits `visibleJobs`:

```typescript
// Partition: jobs in any dependency/parent edge → Zone A (DAG), rest → Zone B (grid)
const { independentJobs, dependentJobs } = useMemo(() => {
  const visibleIds = new Set(visibleJobs.map((j) => j.id));
  const inDag = new Set<string>();

  // Both ends of depends_on edges
  for (const job of visibleJobs) {
    for (const depId of job.depends_on ?? []) {
      if (visibleIds.has(depId)) {
        inDag.add(depId);
        inDag.add(job.id);
      }
    }
    // Both ends of parent_job_id edges
    if (job.parent_job_id && visibleIds.has(job.parent_job_id)) {
      inDag.add(job.parent_job_id);
      inDag.add(job.id);
    }
  }

  const dependent: Job[] = [];
  const independent: Job[] = [];
  for (const job of visibleJobs) {
    (inDag.has(job.id) ? dependent : independent).push(job);
  }
  return { independentJobs: independent, dependentJobs: dependent };
}, [visibleJobs]);
```

**Correctness invariant:** Every job ID that appears in *any* edge (as source or target) in `computeCanvasLayout`'s edge-building logic (`CanvasLayout.tsx:71-92`) is included in `dependentJobs`. This means `computeCanvasLayout(dependentJobs)` will never produce an edge referencing a job not in its input array.

### Step 2: Add status-priority sort for Zone B (~10 min)

**File:** `web/src/pages/CanvasPage.tsx`

**Location:** After the partition memo from Step 1.

```typescript
const STATUS_PRIORITY: Record<string, number> = {
  running: 0,
  paused: 1,
  pending: 2,
  failed: 3,
  staged: 4,
  completed: 5,
  cancelled: 6,
  archived: 7,
};

const sortedIndependentJobs = useMemo(() => {
  return [...independentJobs].sort((a, b) => {
    const pa = STATUS_PRIORITY[a.status] ?? 9;
    const pb = STATUS_PRIORITY[b.status] ?? 9;
    if (pa !== pb) return pa - pb;
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });
}, [independentJobs]);
```

The `STATUS_PRIORITY` constant is defined at module scope (outside the component) since it's static. All 8 `JobStatus` values (`types.ts:3-11`) are covered — any unknown status falls to priority 9 (sorts last).

### Step 3: Redirect layout and grouping inputs (~10 min)

**File:** `web/src/pages/CanvasPage.tsx`

Three targeted changes to existing memos:

**3a.** Change `computeCanvasLayout` input (line 80):
```typescript
// Before:
const layout = useMemo(() => computeCanvasLayout(visibleJobs, groupSettings), [visibleJobs, groupSettings]);
// After:
const layout = useMemo(() => computeCanvasLayout(dependentJobs, groupSettings), [dependentJobs, groupSettings]);
```

**3b.** Remove the `hasDeps` derived boolean (line 83) — no longer needed; replaced by `dependentJobs.length > 0`.

**3c.** Change `grouped`/`ungrouped` memo input (lines 95-110):
```typescript
// Before: iterates visibleJobs
// After: iterates sortedIndependentJobs
const { grouped, ungrouped } = useMemo(() => {
  const groupMap = new Map<string, Job[]>();
  const ungrouped: Job[] = [];
  for (const job of sortedIndependentJobs) {
    // ... (body unchanged)
  }
  return { grouped: Array.from(groupMap.entries()), ungrouped };
}, [sortedIndependentJobs]);
```

### Step 4: Replace ternary with dual-zone render (~20 min)

**File:** `web/src/pages/CanvasPage.tsx`

Replace the `{hasDeps ? (...) : (...)}` block (lines 163-308) with two conditional blocks:

```tsx
{/* Zone A: Dependent jobs — Dagre DAG layout */}
{dependentJobs.length > 0 && (
  <div className="p-6">
    <div
      className="relative"
      style={{ width: layout.width, height: layout.height }}
    >
      <DependencyArrows
        edges={layout.edges}
        width={layout.width}
        height={layout.height}
      />
      {/* Group clusters — EXISTING CODE from lines 176-220, unchanged */}
      {layout.groups.map((group) => (
        /* ... identical to current ... */
      ))}
      {/* Job cards — lookup from dependentJobs instead of visibleJobs */}
      {layout.cards.map((card) => {
        const job = dependentJobs.find((j) => j.id === card.jobId);
        if (!job) return null;
        return (
          <div
            key={card.jobId}
            className="absolute"
            style={{ left: card.x, top: card.y, width: card.width }}
          >
            <JobCard
              job={job}
              runs={runsMap.get(job.id) ?? []}
              dependencyNames={
                job.depends_on
                  ?.map((id) => jobNameMap.get(id))
                  .filter(Boolean) as string[] | undefined
              }
              isGroupQueued={groupQueuedSet.has(job.id)}
            />
          </div>
        );
      })}
    </div>
  </div>
)}

{/* Zone B: Independent jobs — CSS Grid layout */}
{independentJobs.length > 0 && (
  <div className="p-6 space-y-8">
    {/* EXISTING GROUP SECTIONS from lines 253-305, unchanged */}
    {grouped.map(([groupLabel, groupJobs]) => {
      /* ... identical to current ... */
    })}

    {ungrouped.length > 0 && (
      <section>
        {grouped.length > 0 && (
          <h2 className="mb-3 text-sm font-medium text-zinc-500 dark:text-zinc-400">
            Other jobs
          </h2>
        )}
        <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">
          {ungrouped.map(renderCard)}
        </div>
      </section>
    )}
  </div>
)}
```

**Key change in Zone A:** `visibleJobs.find()` → `dependentJobs.find()` (line 223 equivalent). This is important because `layout.cards` only contains `dependentJobs`, so the `.find()` on the correct partition is O(n) over a smaller array.

### Step 5: Verify shared data hooks are unchanged (~5 min)

**File:** `web/src/pages/CanvasPage.tsx`

Confirm these remain derived from `visibleJobs` (the superset) — no code changes, just review:

| Hook | Source | Why it must stay on `visibleJobs` |
|------|--------|----------------------------------|
| `runsQueries` (line 47) | `visibleJobs.map(...)` | Both zones need runs data for `JobCard` mini-DAGs |
| `runsMap` (line 56) | `visibleJobs.forEach(...)` | Indexed by job ID, consumed by both zones |
| `groupQueuedSet` (line 65) | iterates `visibleJobs` | Concurrency queuing applies across both zones |
| `jobNameMap` (line 86) | `jobs` (unfiltered) | Dependency name labels need all job names |

### Step 6: Build and deploy (~5 min)

```bash
cd ~/work/stepwise/web && npm run build && cp -r dist/* ../src/stepwise/_web/
```

## Testing Strategy

### 1. New automated tests

**File to create:** `web/src/pages/__tests__/CanvasPage.test.tsx`

**Run command:** `cd ~/work/stepwise/web && npx vitest run src/pages/__tests__/CanvasPage.test.tsx`

The test file follows the project's established patterns: inline `makeJob()` factory, `vi.mock()` for API/hooks, `createWrapper()` for React Query context, `@testing-library/react` for assertions. Pattern sourced from `web/src/components/jobs/__tests__/JobList.test.tsx`.

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Job, JobStatus } from "@/lib/types";

// ── Mocks ──────────────────────────────────────────────────────────────

vi.mock("@/hooks/useStepwise", () => ({
  useJobs: vi.fn(),
  useGroups: vi.fn(() => ({ data: [] })),
  useStepwiseMutations: vi.fn(() => ({ updateGroupLimit: { mutate: vi.fn() } })),
}));

vi.mock("@/lib/api", () => ({
  fetchRuns: vi.fn(() => Promise.resolve([])),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, ...props }: any) => createElement("a", props, children),
}));

vi.mock("@/hooks/useTheme", () => ({
  useTheme: () => "dark",
}));

import { useJobs } from "@/hooks/useStepwise";
const mockedUseJobs = vi.mocked(useJobs);

import { CanvasPage } from "../CanvasPage";

// ── Helpers ────────────────────────────────────────────────────────────

let jobCounter = 0;

function makeJob(overrides: Partial<Job> = {}): Job {
  jobCounter++;
  return {
    id: `job-${jobCounter}`,
    name: `Job ${jobCounter}`,
    objective: `Objective ${jobCounter}`,
    status: "running" as JobStatus,
    inputs: {},
    parent_job_id: null,
    parent_step_run_id: null,
    workspace_path: "/tmp",
    config: { max_sub_job_depth: 3, timeout_minutes: null, metadata: {} },
    workflow: { steps: {} },
    created_at: new Date(Date.now() - jobCounter * 60000).toISOString(),
    updated_at: new Date(Date.now() - jobCounter * 60000).toISOString(),
    created_by: "server",
    runner_pid: null,
    heartbeat_at: null,
    has_suspended_steps: false,
    job_group: null,
    depends_on: [],
    ...overrides,
  };
}

function createWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
}

function renderCanvas(jobs: Job[]) {
  mockedUseJobs.mockReturnValue({ data: jobs, isLoading: false } as any);
  return render(createElement(CanvasPage), { wrapper: createWrapper() });
}

// ── Tests ──────────────────────────────────────────────────────────────

beforeEach(() => {
  jobCounter = 0;
  vi.clearAllMocks();
});

describe("CanvasPage zone partitioning", () => {
  it("renders all independent jobs in a CSS grid (no DAG arrows)", () => {
    const jobs = [makeJob(), makeJob(), makeJob()];
    const { container } = renderCanvas(jobs);

    // Grid container exists with the expected CSS grid class
    const grid = container.querySelector('[class*="grid-cols-"]');
    expect(grid).not.toBeNull();

    // No SVG (DAG arrows only render as SVG)
    expect(container.querySelector("svg")).toBeNull();

    // All 3 jobs visible
    expect(screen.getByText("Job 1")).toBeInTheDocument();
    expect(screen.getByText("Job 2")).toBeInTheDocument();
    expect(screen.getByText("Job 3")).toBeInTheDocument();
  });

  it("renders dependent jobs in DAG layout (absolute positioned with SVG arrows)", () => {
    const a = makeJob({ id: "a", name: "Alpha" });
    const b = makeJob({ id: "b", name: "Beta", depends_on: ["a"] });
    const { container } = renderCanvas([a, b]);

    // SVG arrows present (DependencyArrows renders an <svg>)
    expect(container.querySelector("svg")).not.toBeNull();

    // Absolute-positioned cards (DAG uses style={{ left, top, width }})
    const absCards = container.querySelectorAll('[style*="left"]');
    expect(absCards.length).toBeGreaterThanOrEqual(2);

    // No CSS grid container (since all jobs are dependent)
    const grid = container.querySelector('[class*="grid-cols-"]');
    expect(grid).toBeNull();
  });

  it("renders both zones when jobs are mixed", () => {
    const a = makeJob({ id: "a", name: "Alpha" });
    const b = makeJob({ id: "b", name: "Beta", depends_on: ["a"] });
    const c = makeJob({ id: "c", name: "Charlie" }); // independent
    const { container } = renderCanvas([a, b, c]);

    // Zone A: SVG arrows + absolute cards
    expect(container.querySelector("svg")).not.toBeNull();

    // Zone B: CSS grid
    const grid = container.querySelector('[class*="grid-cols-"]');
    expect(grid).not.toBeNull();

    // All jobs visible
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByText("Charlie")).toBeInTheDocument();
  });

  it("places depended-upon job (no own deps) in Zone A, not Zone B", () => {
    // A has no depends_on, but B depends_on A — both should be in DAG
    const a = makeJob({ id: "a", name: "Root" });
    const b = makeJob({ id: "b", name: "Child", depends_on: ["a"] });
    const c = makeJob({ id: "c", name: "Indie" }); // truly independent
    const { container } = renderCanvas([a, b, c]);

    // CSS grid should only contain Indie, not Root
    const grid = container.querySelector('[class*="grid-cols-"]');
    expect(grid).not.toBeNull();
    expect(grid!.textContent).toContain("Indie");
    expect(grid!.textContent).not.toContain("Root");
  });

  it("places parent/child jobs in Zone A via parent_job_id", () => {
    const parent = makeJob({ id: "p", name: "Parent" });
    const child = makeJob({ id: "ch", name: "Child", parent_job_id: "p" });
    const indie = makeJob({ id: "i", name: "Indie" });
    const { container } = renderCanvas([parent, child, indie]);

    // Grid should only contain Indie
    const grid = container.querySelector('[class*="grid-cols-"]');
    expect(grid).not.toBeNull();
    expect(grid!.textContent).toContain("Indie");
    expect(grid!.textContent).not.toContain("Parent");
    expect(grid!.textContent).not.toContain("Child");
  });
});

describe("CanvasPage status sort in Zone B", () => {
  it("sorts grid jobs by status priority then recency", () => {
    const now = Date.now();
    const jobs = [
      makeJob({ id: "1", name: "Completed", status: "completed", created_at: new Date(now).toISOString() }),
      makeJob({ id: "2", name: "Running", status: "running", created_at: new Date(now - 1000).toISOString() }),
      makeJob({ id: "3", name: "Pending", status: "pending", created_at: new Date(now - 2000).toISOString() }),
      makeJob({ id: "4", name: "Failed", status: "failed", created_at: new Date(now - 3000).toISOString() }),
    ];
    const { container } = renderCanvas(jobs);

    // Get all job name elements in DOM order within the grid
    const grid = container.querySelector('[class*="grid-cols-"]');
    expect(grid).not.toBeNull();
    const names = Array.from(grid!.querySelectorAll("p"))
      .map((el) => el.textContent)
      .filter((t) => ["Running", "Pending", "Failed", "Completed"].includes(t ?? ""));

    // Status order: running → pending → failed → completed
    expect(names).toEqual(["Running", "Pending", "Failed", "Completed"]);
  });

  it("sorts same-status jobs by most recent created_at first", () => {
    const now = Date.now();
    const jobs = [
      makeJob({ id: "old", name: "Old Run", status: "running", created_at: new Date(now - 60000).toISOString() }),
      makeJob({ id: "new", name: "New Run", status: "running", created_at: new Date(now).toISOString() }),
    ];
    const { container } = renderCanvas(jobs);

    const grid = container.querySelector('[class*="grid-cols-"]');
    const names = Array.from(grid!.querySelectorAll("p"))
      .map((el) => el.textContent)
      .filter((t) => ["Old Run", "New Run"].includes(t ?? ""));

    // Most recent first
    expect(names).toEqual(["New Run", "Old Run"]);
  });
});

describe("CanvasPage edge cases", () => {
  it("renders empty state when no jobs", () => {
    renderCanvas([]);
    expect(screen.getByText(/no jobs yet/i)).toBeInTheDocument();
  });

  it("ignores depends_on references to jobs not in visible set", () => {
    // Job B depends on non-existent job "ghost" — should be treated as independent
    const b = makeJob({ id: "b", name: "Orphan", depends_on: ["ghost"] });
    const { container } = renderCanvas([b]);

    // Should render in grid (no SVG arrows)
    const grid = container.querySelector('[class*="grid-cols-"]');
    expect(grid).not.toBeNull();
    expect(container.querySelector("svg")).toBeNull();
  });
});
```

### 2. Existing test suites — must remain green

```bash
# Frontend: all existing tests pass
cd ~/work/stepwise/web && npx vitest run
# Expected: all ~43 test files pass (0 failures)

# Backend: unaffected (no Python changes)
cd ~/work/stepwise && uv run pytest tests/ -x -q
# Expected: all pass
```

### 3. TypeScript compilation check

```bash
cd ~/work/stepwise/web && npx tsc --noEmit
# Expected: 0 errors
```

### 4. Production build verification

```bash
cd ~/work/stepwise/web && npm run build
# Expected: exits 0, dist/ contains index.html + assets
ls -la ~/work/stepwise/web/dist/
```

### 5. Manual verification checklist

| # | Scenario | How to verify | Pass criteria |
|---|----------|--------------|---------------|
| M1 | All independent | Start server, create 5 jobs with no deps, open `/canvas` | Jobs in multi-column grid. No SVG arrows. Resize window: columns reflow. |
| M2 | All dependent | Create job A, then B with `depends_on: [A.id]`, open `/canvas` | Dagre DAG with arrow A → B. No grid section. |
| M3 | Mixed | Have both dependent chain and independent jobs | Zone A above Zone B, both visible. Single scroll. |
| M4 | Status sort | Create jobs: one running, one pending, one completed | Running is top-left, pending next, completed last (faded). |
| M5 | Hide completed | Toggle "Hide done" | Completed jobs disappear from both zones. |
| M6 | Groups in grid | Create independent jobs with `job_group` set | Grouped section with dashed border, concurrency controls work. |

## Risks & Mitigations

| # | Risk | Severity | Mitigation |
|---|------|----------|------------|
| R1 | Depended-upon job with empty `depends_on` misclassified as independent | **High** — DAG arrows point to missing node | Partition builds `inDag` set by scanning both ends of every `depends_on` and `parent_job_id` edge. Automated test: "places depended-upon job in Zone A". |
| R2 | `parent_job_id` edge not accounted for in partition | **High** — sub-job DAG breaks | Partition explicitly checks `parent_job_id` alongside `depends_on`. Automated test: "places parent/child jobs in Zone A via parent_job_id". |
| R3 | `depends_on` referencing a filtered-out (hidden) job | **Medium** — job incorrectly stays in Zone B | Partition only marks edges where *both* endpoints are in `visibleIds`. If the depended-upon job is hidden, the dependent job has no valid edge and falls to Zone B. This is correct behavior — the DAG can't draw an arrow to a hidden job. Automated test: "ignores depends_on references to jobs not in visible set". |
| R4 | Status sort causes grid reorder on every status change | **Low** — visual jitter | `useMemo` recomputes only when `independentJobs` identity changes (which only happens when `visibleJobs` changes). WebSocket ticks trigger React Query invalidation, which updates `jobs` → `visibleJobs` → partition → sort. This is the same frequency as the existing grid. Phase 5 (council plan) adds layout transition animations. |
| R5 | Group split across zones | **Low** — same `job_group` label appears in both Zone A cluster and Zone B section | Unlikely in practice (groups contain same-type jobs). If it occurs, both zones correctly show the group. No user confusion — the group label + concurrency controls appear where the jobs are. |
| R6 | Dagre bounding box too small with fewer nodes | **Low** — layout feels cramped | Dagre auto-adjusts with margins (`marginx: 60, marginy: 60` in `CanvasLayout.tsx:110-111`). Fewer nodes = smaller box = less scroll, which is better. |
| R7 | `.find()` performance in DAG card rendering | **Negligible** — O(n) per card | Existing code already does `visibleJobs.find()` per card. Switching to `dependentJobs.find()` makes n smaller. Not worth optimizing to a Map for typical job counts (<100). |

## Files Modified

| File | Change type | Description |
|------|------------|-------------|
| `web/src/pages/CanvasPage.tsx` | **Modified** | Add partition memo, status sort, redirect layout/group inputs, dual-zone render |
| `web/src/pages/__tests__/CanvasPage.test.tsx` | **New** | Automated tests for partition correctness, status sort, edge cases |

**Unchanged:** `JobCard.tsx`, `CanvasLayout.tsx`, `DependencyArrows.tsx`, `MiniDag.tsx`, `types.ts`, `status-colors.ts`, `dag-layout.ts`, `api.ts`, all backend files.

## Estimated Scope

~60 lines changed in `CanvasPage.tsx`, ~200 lines new test file. Pure frontend, no backend changes. Under 1 hour implementation + testing.
