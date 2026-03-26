# Plan: Add Sorting to the Flows Page

## Overview

The flows page (`web/src/pages/FlowsPage.tsx`) currently lists local flows in filesystem discovery order with only a text filter for searching by name. This plan adds sort controls so users can sort flows by **job count** (most used), **most recently run**, and **alphabetical** (name).

The main challenge is that job count and last-run-time are not currently available on the `LocalFlow` object — they live in the jobs database. The plan introduces a lightweight server endpoint to aggregate per-flow job stats, and a frontend sort control in the flow list header.

## Requirements

### R1: Sort by Alphabetical (name)
- **Acceptance criteria:** Clicking "Name" sorts flows A-Z by `flow.name`. Clicking again reverses to Z-A.
- **Data source:** Already available on `LocalFlow.name` — pure frontend sort.

### R2: Sort by Job Count (most used)
- **Acceptance criteria:** Clicking "Most Used" sorts flows by total number of jobs created from that flow, descending. Flows with zero jobs appear at the bottom. Ties broken alphabetically.
- **Data source:** Requires server-side aggregation from the jobs table.

### R3: Sort by Most Recently Run
- **Acceptance criteria:** Clicking "Recent" sorts flows by the `updated_at` timestamp of their most recent job, descending. Flows with no jobs appear at the bottom. Ties broken alphabetically.
- **Data source:** Requires server-side aggregation from the jobs table.

### R4: Sort persists within the session
- **Acceptance criteria:** Selected sort option persists while navigating away from and back to the flows page within a single browser session. Default sort is alphabetical on first load.

### R5: Sort control UX
- **Acceptance criteria:** A compact sort selector appears in the flow list header bar, next to the existing filter input. Uses shadcn/ui `Select` component. Does not add visual clutter on mobile.

## Assumptions

### A1: `source_dir` on jobs reliably maps to flow paths
- **Verified:** `server.py:329` — `_serialize_job()` exposes `flow_file: getattr(job.workflow, "source_dir", None)` for every job summary. When a job is created via `flow_path` (line 803-811), `load_workflow_yaml()` sets `source_dir` to the flow file's parent directory (absolute path). This is the only linkage between jobs and flows.
- **Caveat:** `source_dir` is an absolute directory path (e.g., `/home/user/project/.stepwise/flows/my-flow`), not a relative path like `LocalFlow.path` (e.g., `.stepwise/flows/my-flow/FLOW.yaml`). The server-side aggregation must normalize by extracting the flow directory and comparing against discovered flow paths.

### A2: Jobs created without `flow_path` (inline workflow) have no `source_dir`
- **Verified:** `server.py:801-802` — `WorkflowDefinition.from_dict(req.workflow)` does not set `source_dir` unless the dict includes it. These jobs won't map to any local flow, which is correct — they aren't flow-originated.

### A3: No schema migration needed
- **Verified:** The `workflow` column in the jobs table stores the full JSON-serialized `WorkflowDefinition`, which includes `source_dir`. We can query it via `json_extract(workflow, '$.source_dir')` in SQLite. No new columns needed.

### A4: shadcn/ui `Select` component is available
- **Verified:** `web/src/components/ui/select.tsx` exists and is ready to use.

### A5: Flow list is small enough for client-side sorting
- **Verified:** `useLocalFlows()` fetches all flows at once (no pagination). The sort is applied to the already-filtered list in a `useMemo`. Server returns aggregated stats; sorting happens client-side.

## Implementation Steps

### Step 1: Add server endpoint for per-flow job stats
**File:** `src/stepwise/server.py`

Add `GET /api/flow-stats` endpoint that returns job count and last-run timestamp per flow path.

```python
@app.get("/api/flow-stats")
def get_flow_stats():
    """Return job_count and last_run_at per flow source_dir."""
    engine = _get_engine()
    rows = engine.store._conn.execute(
        """SELECT json_extract(workflow, '$.source_dir') as source_dir,
                  COUNT(*) as job_count,
                  MAX(updated_at) as last_run_at
           FROM jobs
           WHERE json_extract(workflow, '$.source_dir') IS NOT NULL
           GROUP BY source_dir"""
    ).fetchall()
    return [
        {
            "source_dir": row["source_dir"],  # absolute path
            "job_count": row["job_count"],
            "last_run_at": row["last_run_at"],
        }
        for row in rows
    ]
```

This uses SQLite's `json_extract` on the already-stored workflow JSON. No schema changes.

**Alternative considered:** Adding a denormalized `flow_path` column to the jobs table. Rejected because it requires a migration, and `json_extract` is sufficient for the scale of data (local project flows — dozens to hundreds of jobs, not millions).

### Step 2: Add API fetch function
**File:** `web/src/lib/api.ts`

```typescript
export interface FlowStats {
  source_dir: string;
  job_count: number;
  last_run_at: string | null;
}

export function fetchFlowStats(): Promise<FlowStats[]> {
  return request<FlowStats[]>("/flow-stats");
}
```

### Step 3: Add TypeScript type and React Query hook
**File:** `web/src/lib/types.ts` — Add `FlowStats` interface (or keep it in `api.ts` since it's API-specific).

**File:** `web/src/hooks/useEditor.ts` — Add hook:

```typescript
export function useFlowStats() {
  return useQuery({
    queryKey: ["flowStats"],
    queryFn: api.fetchFlowStats,
    staleTime: 30000, // 30s — stats don't change rapidly
  });
}
```

### Step 4: Add sort controls and sorting logic to FlowsPage
**File:** `web/src/pages/FlowsPage.tsx`

Changes:
1. Import `Select`, `SelectContent`, `SelectItem`, `SelectTrigger`, `SelectValue` from `@/components/ui/select`.
2. Import `useFlowStats` from `@/hooks/useEditor`.
3. Add state: `const [sortBy, setSortBy] = useState<"name" | "most-used" | "recent">("name")`.
4. Fetch stats: `const { data: flowStats = [] } = useFlowStats()`.
5. Build a lookup map in `useMemo`: `Map<string, { job_count: number; last_run_at: string | null }>` keyed by normalized flow directory path extracted from `LocalFlow.path`.
6. Replace the existing `filtered` `useMemo` (line 78-84) with a `filteredAndSorted` memo that first filters, then sorts based on `sortBy` + the stats lookup map.
7. Add `Select` component in the flow list header (line 245-255), between the search input and the list:

```tsx
<div className="p-3 border-b border-border flex gap-2">
  <div className="relative flex-1">
    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500" />
    <Input
      value={filter}
      onChange={(e) => setFilter(e.target.value)}
      placeholder="Filter flows..."
      className="pl-8 h-8 text-sm bg-zinc-900 border-zinc-700"
    />
  </div>
  <Select value={sortBy} onValueChange={(v) => setSortBy(v as typeof sortBy)}>
    <SelectTrigger className="w-28 h-8 text-xs bg-zinc-900 border-zinc-700">
      <SelectValue />
    </SelectTrigger>
    <SelectContent>
      <SelectItem value="name">Name</SelectItem>
      <SelectItem value="most-used">Most Used</SelectItem>
      <SelectItem value="recent">Recent</SelectItem>
    </SelectContent>
  </Select>
</div>
```

### Step 5: Path normalization for stats matching

The key challenge is matching `LocalFlow.path` (relative, e.g., `.stepwise/flows/my-flow/FLOW.yaml`) against `source_dir` from job stats (absolute, e.g., `/home/user/project/.stepwise/flows/my-flow`).

**Approach:** Normalize both sides to a comparable key. On the server side in Step 1, the `source_dir` is an absolute directory path. The `list_local_flows` endpoint (line 1778) computes `rel_path = str(flow_info.path.relative_to(_project_dir))`. We can include the resolved `source_dir` equivalent in the `/api/local-flows` response, OR we can normalize in the `/api/flow-stats` endpoint by stripping the `_project_dir` prefix.

**Chosen approach:** Normalize in the flow-stats endpoint — convert `source_dir` to a relative path by stripping the project directory prefix. Then match against `LocalFlow.path`'s directory component on the frontend.

Updated server endpoint:
```python
@app.get("/api/flow-stats")
def get_flow_stats():
    engine = _get_engine()
    rows = engine.store._conn.execute(
        """SELECT json_extract(workflow, '$.source_dir') as source_dir,
                  COUNT(*) as job_count,
                  MAX(updated_at) as last_run_at
           FROM jobs
           WHERE json_extract(workflow, '$.source_dir') IS NOT NULL
           GROUP BY source_dir"""
    ).fetchall()
    result = []
    project_prefix = str(_project_dir) + "/"
    for row in rows:
        sd = row["source_dir"]
        # Normalize to relative path
        rel = sd[len(project_prefix):] if sd.startswith(project_prefix) else sd
        result.append({
            "flow_dir": rel,
            "job_count": row["job_count"],
            "last_run_at": row["last_run_at"],
        })
    return result
```

Frontend matching: For a `LocalFlow` with `path = ".stepwise/flows/my-flow/FLOW.yaml"`, its directory is `.stepwise/flows/my-flow`. For a file flow like `flows/simple.flow.yaml`, its directory is `flows`. The lookup key is derived by stripping the filename:

```typescript
function flowDirKey(flowPath: string): string {
  // ".stepwise/flows/my-flow/FLOW.yaml" → ".stepwise/flows/my-flow"
  // "flows/simple.flow.yaml" → "flows"
  const lastSlash = flowPath.lastIndexOf("/");
  return lastSlash >= 0 ? flowPath.substring(0, lastSlash) : flowPath;
}
```

### Step 6: Sorting implementation

```typescript
const filteredAndSorted = useMemo(() => {
  let result = filter
    ? flows.filter((f) => f.name.toLowerCase().includes(filter.toLowerCase()))
    : [...flows];

  const statsMap = new Map(
    flowStats.map((s) => [s.flow_dir, s])
  );

  result.sort((a, b) => {
    if (sortBy === "name") {
      return a.name.localeCompare(b.name);
    }
    const sa = statsMap.get(flowDirKey(a.path));
    const sb = statsMap.get(flowDirKey(b.path));
    if (sortBy === "most-used") {
      const diff = (sb?.job_count ?? 0) - (sa?.job_count ?? 0);
      return diff !== 0 ? diff : a.name.localeCompare(b.name);
    }
    // "recent"
    const ta = sa?.last_run_at ?? "";
    const tb = sb?.last_run_at ?? "";
    if (ta === tb) return a.name.localeCompare(b.name);
    if (!ta) return 1;
    if (!tb) return -1;
    return tb.localeCompare(ta); // descending
  });

  return result;
}, [flows, filter, sortBy, flowStats]);
```

### Step 7: Invalidate flow stats when jobs change

**File:** `web/src/hooks/useStepwise.ts`

In the `useCreateJob` mutation's `onSuccess`, add invalidation of `["flowStats"]` so newly created jobs update the sort order:

```typescript
queryClient.invalidateQueries({ queryKey: ["flowStats"] });
```

Also invalidate on job deletion.

### Step 8: Optional enhancement — show stats in flow list items

As a small visual indicator, show the job count badge next to the step count when sorting by "Most Used", or the relative time when sorting by "Recent". This keeps the sort criteria visible. Add this inline in the flow list item rendering (line 286-321), conditionally based on `sortBy`:

```tsx
<span className="ml-auto text-xs text-zinc-600 shrink-0">
  {sortBy === "most-used"
    ? `${statsMap.get(flowDirKey(flow.path))?.job_count ?? 0} jobs`
    : sortBy === "recent"
      ? statsMap.get(flowDirKey(flow.path))?.last_run_at
        ? formatRelativeTime(statsMap.get(flowDirKey(flow.path))!.last_run_at!)
        : "never"
      : flow.steps_count}
</span>
```

This replaces the step count display contextually based on the active sort.

## File Change Summary

| File | Change |
|---|---|
| `src/stepwise/server.py` | Add `GET /api/flow-stats` endpoint (~20 lines) |
| `web/src/lib/api.ts` | Add `FlowStats` interface + `fetchFlowStats()` function (~8 lines) |
| `web/src/hooks/useEditor.ts` | Add `useFlowStats()` hook (~7 lines) |
| `web/src/hooks/useStepwise.ts` | Invalidate `flowStats` query on job create/delete (~2 lines) |
| `web/src/pages/FlowsPage.tsx` | Add sort state, `Select` control, `useMemo` sorting logic, contextual list item display (~50 lines changed) |

## Testing Strategy

### Backend

```bash
# Run existing tests to ensure no regressions
uv run pytest tests/

# Manual test: start server, create a few jobs from flows, verify endpoint
uv run stepwise server start
curl http://localhost:8340/api/flow-stats | python3 -m json.tool
```

Add a test in `tests/test_server.py` (or a new `tests/test_flow_stats.py`):
- Create 3 jobs from 2 different flows (via `flow_path`)
- Hit `GET /api/flow-stats`
- Assert correct job counts and `last_run_at` values
- Assert flows with no jobs don't appear in stats

### Frontend

```bash
cd web && npm run test
cd web && npm run lint
```

Add a test in `web/src/pages/FlowsPage.test.tsx` (or similar):
- Mock `useLocalFlows` to return 3 flows
- Mock `useFlowStats` to return stats mapping
- Render `FlowsPage`, click sort options, verify order changes
- Verify "Name" sorts A-Z, "Most Used" sorts by count descending, "Recent" sorts by timestamp descending

### Manual E2E

1. Start server: `uv run stepwise server start`
2. Open web UI, navigate to Flows page
3. Create 2+ flows, run jobs from them (varying counts)
4. Verify each sort option produces correct ordering
5. Verify filter + sort work together
6. Verify sort persists when clicking into a flow detail and back
7. Verify mobile layout — sort control doesn't overflow
