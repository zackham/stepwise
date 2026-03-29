# Plan: Job History on the Flows Page

## Overview

Add a "Recent Jobs" section to the flow detail preview panel on the Flows page so users can see which jobs have run from a selected flow without navigating to the Jobs page.

The flow detail panel (right side of FlowsPage when a local flow is selected) currently shows: flow name, description, metadata badges, Run/Edit buttons, and a DAG preview. This plan adds a collapsible "Recent Jobs" list between the metadata bar and the DAG preview, showing the most recent jobs created from that flow.

## Requirements

### R1: Backend endpoint for flow-specific jobs
- **New endpoint**: `GET /api/flows/{flow_path}/jobs?limit=N`
- Returns jobs whose `workflow.source_dir` matches the resolved absolute path of the given flow
- Default limit: 10, max: 50
- Response: lightweight job summaries (no full workflow definition)
- **Acceptance**: Endpoint returns correct jobs filtered by flow path; returns empty array for flows with no jobs; respects limit parameter

### R2: Recent Jobs section in flow detail panel
- Appears in the flow preview panel on FlowsPage (both desktop inline and mobile full-screen)
- Located between the metadata/action bar and the DAG preview
- Collapsible (default expanded if jobs exist, collapsed if empty)
- Shows up to 5 jobs by default
- **Acceptance**: Section visible when a flow is selected; hidden or shows "No jobs yet" for flows without history

### R3: Job row display
- Each row shows: status dot (colored by status), job name (or objective fallback), relative time ("5m ago"), and duration for completed jobs
- Status dot uses existing `status-colors.ts` palette with animated pulse for running jobs
- Clicking a row navigates to `/jobs/$jobId`
- **Acceptance**: All fields render correctly; click navigates to job detail; running jobs show pulse animation

### R4: "See all" link
- Shown when the flow has more jobs than the displayed limit
- Links to Jobs page — since there's no flow filter on the Jobs page yet, navigate to `/jobs` (future: could add `?flow=` query param)
- **Acceptance**: Link appears only when total count exceeds displayed count; navigates to Jobs page

### R5: Real-time updates
- Job list refreshes when WebSocket broadcasts a job change
- New jobs appear at the top when a flow is run from the Flows page
- **Acceptance**: Starting a job from the Run button shows the new job in the list within a few seconds without manual refresh

## Assumptions

1. **Flow-to-job linkage uses `workflow.source_dir`** — Verified: `_serialize_job()` at `server.py:374` emits `flow_file: getattr(job.workflow, "source_dir", None)`, and the existing `get_flow_stats()` endpoint at `server.py:2051` groups by `json_extract(workflow, '$.source_dir')`. This is the established pattern.

2. **`source_dir` stores an absolute path** — Verified: `WorkflowDefinition.source_dir` is set to the absolute directory path when loading from YAML. The `get_flow_stats()` endpoint strips the project prefix to produce a relative `flow_dir`. We'll do the same.

3. **FlowsPage identifies selected flow by `LocalFlow.path`** — Verified: `FlowsPage.tsx:97` uses `selectedLocalFlow?.path` which is a relative path like `flows/plan/FLOW.yaml`. The `flowDirKey()` helper at line 63 strips the filename to get the directory portion for matching against `flow_dir` from stats.

4. **The store doesn't need a new table or index** — The `json_extract(workflow, '$.source_dir')` query pattern is already used by `get_flow_stats()`. For a limit-10 query on a typical project's job count (<1000), a table scan with JSON extraction is adequate. If performance becomes an issue later, a generated column + index can be added.

5. **Summary serialization exists** — `_serialize_job(job, summary=True)` already returns a lightweight format without the full workflow details. However, it still includes the workflow dict. For the flow jobs endpoint, we'll use an even lighter format that omits `workflow` entirely to minimize payload size.

## Implementation Steps

### Step 1: Add backend endpoint `GET /api/flows/{flow_path}/jobs`

**File**: `src/stepwise/server.py`

Add a new endpoint after the existing `get_flow_stats()` endpoint (~line 2073):

```python
@app.get("/api/flows/{flow_path:path}/jobs")
def get_flow_jobs(flow_path: str, limit: int = 10):
    """Return recent jobs created from a specific flow."""
    engine = _get_engine()
    abs_path = str((_project_dir / flow_path).resolve())
    # flow_path may point to FLOW.yaml or the directory; normalize to directory
    if abs_path.endswith(".yaml") or abs_path.endswith(".yml"):
        abs_path = str(Path(abs_path).parent)

    limit = min(max(limit, 1), 50)
    rows = engine.store._conn.execute(
        """SELECT id, name, objective, status, created_at, updated_at, created_by,
                  parent_job_id
           FROM jobs
           WHERE json_extract(workflow, '$.source_dir') = ?
             AND parent_job_id IS NULL
           ORDER BY created_at DESC
           LIMIT ?""",
        [abs_path, limit + 1],  # fetch one extra to detect "has more"
    ).fetchall()

    has_more = len(rows) > limit
    jobs = rows[:limit]

    return {
        "jobs": [
            {
                "id": r["id"],
                "name": r["name"],
                "objective": r["objective"],
                "status": r["status"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "created_by": r["created_by"],
            }
            for r in jobs
        ],
        "has_more": has_more,
    }
```

Key decisions:
- Filter `parent_job_id IS NULL` to exclude sub-jobs (same as top-level filtering in `all_jobs()`)
- Fetch `limit + 1` rows to determine `has_more` without a separate COUNT query
- Return raw column values (no deserialization to Job objects) for performance — avoids parsing the full workflow JSON blob
- Normalize path to directory since `source_dir` stores the directory, not the YAML file path

### Step 2: Add frontend API function

**File**: `web/src/lib/api.ts`

Add after the `fetchFlowStats()` function (~line 265):

```typescript
export interface FlowJobSummary {
  id: string;
  name: string | null;
  objective: string;
  status: JobStatus;
  created_at: string;
  updated_at: string;
  created_by: string;
}

export interface FlowJobsResponse {
  jobs: FlowJobSummary[];
  has_more: boolean;
}

export function fetchFlowJobs(flowPath: string, limit = 10): Promise<FlowJobsResponse> {
  return request<FlowJobsResponse>(`/flows/${encodeURIComponent(flowPath)}/jobs?limit=${limit}`);
}
```

### Step 3: Add React Query hook

**File**: `web/src/hooks/useEditor.ts`

Add after the `useFlowStats()` hook (~line 19):

```typescript
export function useFlowJobs(flowPath: string | undefined, limit = 5) {
  return useQuery({
    queryKey: ["flowJobs", flowPath, limit],
    queryFn: () => api.fetchFlowJobs(flowPath!, limit),
    enabled: !!flowPath,
    staleTime: 15000,
  });
}
```

### Step 4: Add WebSocket invalidation for flow jobs

**File**: `web/src/hooks/useStepwiseWebSocket.ts`

In the existing WebSocket message handler that invalidates job queries, also invalidate `flowJobs`:

```typescript
// Existing: queryClient.invalidateQueries({ queryKey: ["jobs"] });
queryClient.invalidateQueries({ queryKey: ["flowJobs"] });
```

This ensures the flow jobs list refreshes when any job state changes. The query key includes `flowPath`, so only the active flow's query will refetch.

### Step 5: Create `FlowRecentJobs` component

**File**: `web/src/components/editor/FlowRecentJobs.tsx` (new file)

A small, focused component that receives a flow path and renders the recent jobs list:

```tsx
interface FlowRecentJobsProps {
  flowPath: string;
}
```

Component internals:
- Calls `useFlowJobs(flowPath)` to fetch data
- Renders a compact list with:
  - Status dot (colored circle, uses `getStatusColor()` from `lib/status-colors.ts`, animated pulse for "running")
  - Job name or objective (truncated, single line)
  - Relative time on the right (reuse `formatRelativeTime` pattern from FlowsPage or extract to shared utility)
- Each row is a button/link that calls `navigate({ to: "/jobs/$jobId", params: { jobId } })`
- "See all jobs" link at bottom when `has_more` is true, navigates to `/jobs`
- Loading state: 3 skeleton rows (thin rectangles)
- Empty state: small muted text "No jobs yet"
- Collapsible header "Recent Jobs (N)" using a simple toggle state

### Step 6: Integrate into FlowsPage

**File**: `web/src/pages/FlowsPage.tsx`

Insert `<FlowRecentJobs flowPath={selectedLocalFlow.path} />` in both the desktop and mobile flow preview panels.

**Desktop** (between the metadata card and the DAG preview, ~line 480):
```tsx
{/* After the metadata/action card, before the DAG div */}
<FlowRecentJobs flowPath={selectedLocalFlow.path} />
```

**Mobile** (in the MobileFullScreen content, ~line 551):
```tsx
{/* After the action buttons div, before the DAG border-t div */}
<FlowRecentJobs flowPath={selectedLocalFlow.path} />
```

### Step 7: Invalidate flow jobs when creating a job from Run button

**File**: `web/src/pages/FlowsPage.tsx`

In the `handleRun` callback's `onSuccess`, invalidate the flow jobs query so the new job appears:

```typescript
onSuccess: (job) => {
  queryClient.invalidateQueries({ queryKey: ["flowJobs", flow.path] });
  navigate({ to: "/jobs/$jobId", params: { jobId: job.id } });
},
```

Note: The WebSocket invalidation from Step 4 should already handle this, but an explicit invalidation ensures immediate consistency before navigation.

## File Change Summary

| File | Change |
|---|---|
| `src/stepwise/server.py` | Add `GET /api/flows/{flow_path}/jobs` endpoint (~20 lines) |
| `web/src/lib/api.ts` | Add `FlowJobSummary` interface, `FlowJobsResponse` interface, `fetchFlowJobs()` function (~15 lines) |
| `web/src/hooks/useEditor.ts` | Add `useFlowJobs()` hook (~8 lines) |
| `web/src/hooks/useStepwiseWebSocket.ts` | Add `flowJobs` query invalidation (~1 line) |
| `web/src/components/editor/FlowRecentJobs.tsx` | **New file**: `FlowRecentJobs` component (~80-100 lines) |
| `web/src/pages/FlowsPage.tsx` | Import + render `FlowRecentJobs` in desktop and mobile panels (~4 lines) |

## Testing Strategy

### Backend

```bash
# Run all existing tests to verify no regressions
uv run pytest tests/

# Specifically test server endpoints if a server test file exists
uv run pytest tests/test_server.py -v -k "flow"
```

Manual verification:
1. Start server: `uv run stepwise server start`
2. Create a few jobs from a flow: `uv run stepwise run flows/some-flow/FLOW.yaml --name "test-1"`
3. Hit the endpoint: `curl http://localhost:8340/api/flows/flows/some-flow/FLOW.yaml/jobs`
4. Verify response contains the correct jobs, ordered by created_at DESC
5. Verify `has_more` is false when under limit, true when over
6. Verify flows with no jobs return `{"jobs": [], "has_more": false}`

### Frontend

```bash
cd web && npm run test
cd web && npm run lint
```

Manual verification:
1. `cd web && npm run dev` (with server running)
2. Navigate to Flows page, select a flow that has been run before
3. Verify "Recent Jobs" section appears with correct jobs
4. Verify status dots match job statuses
5. Click a job row — verify navigation to `/jobs/$jobId`
6. Click "Run" on a flow — verify the new job appears in the list
7. Select a flow that has never been run — verify "No jobs yet" message
8. Select a flow with >5 jobs — verify "See all" link appears
9. Test mobile layout via browser dev tools responsive mode

### Edge Cases to Verify

- Flow with no jobs: empty state renders cleanly
- Flow path is a directory flow (`flows/plan/FLOW.yaml`) vs single file (`flows/simple.flow.yaml`) — both resolve correctly
- Jobs created via CLI (`created_by: "cli:*"`) appear alongside server-created jobs
- Sub-jobs are excluded (only top-level jobs shown)
- Rapidly clicking between flows doesn't cause stale data display (React Query handles via `queryKey` including `flowPath`)
