# Unified Plan: Web UI IA Refactor + Session Viewer

## Overview

Merge the separate "Jobs" and "Canvas" top-level navigation into a single "Jobs" area with List/Grid view toggles, then add a "Session" tab to the job detail right panel that stitches per-step agent outputs into a unified session transcript with step boundary markers and live streaming.

---

## Requirements

### IA Refactor

**R-IA1: Unified Jobs page with view toggle**
- The Jobs page shows a segmented control in its header area: `[ ≡ List ] [ ⊞ Grid ]`
- List view = current `JobDashboard` component (sidebar list + detail placeholder)
- Grid view = current `CanvasPage` component (job cards, dependency arrows, groups, bulk actions)
- **Acceptance:** Only one "Jobs" link in the top nav. Segmented control switches views without page reload. Both views render identically to their current standalone pages. The segmented control uses the shadcn/ui button-group pattern (rounded container with active/inactive button styling matching the existing theme).

**R-IA2: View persistence**
- Selected view persists in localStorage under key `stepwise-job-view` (matching existing convention: `stepwise-theme`, `stepwise-job-sort`)
- URL reflects view: `/jobs` (default=list) or `/jobs?view_mode=grid`
- When URL has no `view_mode` param, fall back to localStorage; if localStorage is empty, default to `list`
- `view_mode=list` is normalized to absent (omitted from URL) to keep list view URLs clean
- **Acceptance:** Refresh preserves view. Direct URL with `?view_mode=grid` loads grid view. Opening `/jobs` without params uses localStorage. The `view_mode` param is preserved when navigating to `/jobs/$jobId` and back.

**R-IA3: Filter/search preservation across views**
- Switching between List and Grid views preserves `q`, `status`, `range` search params
- Grid view currently has its own `hideCompleted` toggle (local state in `CanvasPage`). This remains independent — it is not serialized into URL params and does not conflict with the `status` filter.
- **Acceptance:** Set `?status=running` in list view, switch to grid, switch back — `?status=running` still present. The `q` and `range` params are also preserved.

**R-IA4: Legacy route redirect**
- `/canvas` redirects to `/jobs?view_mode=grid` via TanStack Router `beforeLoad` + `throw redirect()`
- **Acceptance:** Navigating to `/canvas` (bookmark, link, typing URL) lands on Jobs page in grid view. Browser history replaces `/canvas` with `/jobs?view_mode=grid`.

**R-IA5: Remove Canvas from navigation**
- Top nav becomes: `Stepwise | project | Jobs | Flows | Settings` (4 nav items, down from 5)
- Remove: Canvas `<Link>` (`AppLayout.tsx:334-336`), `isCanvasActive` boolean (`:193`), canvas routeKey branch (`:200-202`), canvas title branch (`:267-268`), `Network` icon import (`:18`), `isCanvasActive` in useEffect deps (`:282`)
- **Acceptance:** No "Canvas" link in top nav. Canvas keyboard shortcut does not exist (verified: no `g,c` binding in `AppLayout.tsx:235-256`). Title never shows "Canvas — Stepwise".

**R-IA6: Grid view job navigation to detail**
- Clicking a job card in grid view navigates to `/jobs/$jobId` (job detail page). This already works via `<Link to="/jobs/$jobId">` in `JobCard.tsx:62-74`.
- The `view_mode=grid` param should be preserved in the navigation so pressing Back returns to grid view.
- **Acceptance:** Click job card in grid view → job detail page opens. Press Back → returns to grid view (not list view).

### Session Viewer

**R-SV1: Session discovery for a job**
- New endpoint `GET /api/jobs/{job_id}/sessions` returns `{"sessions": [{session_name, step_names, run_ids, is_active, started_at, latest_at}]}`
- Sessions are derived by grouping all StepRun records for the job by `executor_state.session_name` (`models.py:1572`, type `dict | None`)
- Runs without `session_name` in executor_state are excluded
- **Acceptance:** Endpoint returns correctly grouped sessions sorted by `started_at`. Job with only script steps returns `{"sessions": []}`. Job with 3 agent runs across 2 sessions returns 2 session entries with correct run_id/step_name grouping.

**R-SV2: Session transcript assembly**
- New endpoint `GET /api/jobs/{job_id}/sessions/{session_name}/transcript` returns `{"events": [...], "boundaries": [...]}`
- Events are a flat `AgentStreamEvent[]` (same format as `_parse_ndjson_events()` output, `server.py:273-314`)
- Boundaries are `[{event_index, step_name, attempt, run_id, started_at, status}]` — index-based markers pointing into the events array
- Each run's output is read from `executor_state.output_path` (`server.py:1842` pattern) and parsed via `_parse_ndjson_events()`
- Runs sorted by `started_at` (`StepRun.started_at`, `models.py:1576`, type `datetime | None`)
- **Acceptance:** Events from 3 runs concatenated chronologically. Boundaries at correct indices (e.g., boundary[0].event_index=0, boundary[1].event_index=47). Missing output files produce empty segments with boundary still present.

**R-SV3: Live streaming for active sessions**
- WebSocket `agent_output` messages (`server.py:330-334`) include `run_id` but not `session_name`
- Frontend session viewer maps `run_id → session_name` using the session info from R-SV1 to route live events
- Uses same backfill+live queue pattern as `useAgentStream` (`useAgentStream.ts:108-150`): queue WebSocket events until REST backfill arrives, then replay
- **Acceptance:** While an agent step in a session runs, new output appears live in the session transcript with <200ms latency. Switching to the session tab mid-run shows backfill + live continuation without gaps or duplication.

**R-SV4: Session tab in job detail**
- New "Session" tab appears in the right panel tab bar (`JobDetailPage.tsx:716-724`) when the job has >= 1 agent session
- Tab value added to `JOB_DETAIL_TAB_VALUES` set (`router.tsx:50`) and `RightPanelTab` type (`JobDetailPage.tsx:77`)
- If one session: show transcript directly. If multiple: session picker (list or dropdown) + transcript for selected session
- The Session tab is available both when a step is selected AND when no step is selected (it's job-level, not step-level)
- **Acceptance:** Tab visible when `useJobSessions` returns >= 1 session. Tab hidden when 0 sessions. Tab navigable via URL `?tab=session`. Clicking tab shows transcript.

**R-SV5: Step boundary markers in transcript**
- Visual horizontal divider between step outputs: step name pill, attempt number (if >1), timestamp
- Styled as a centered pill on a horizontal rule, matching the existing dark theme (zinc background, border colors)
- Clicking a marker navigates to that step: updates URL to `?step={stepName}&tab=run&panel=open`
- **Acceptance:** Markers render at correct positions matching boundary indices. Click changes URL params. Marker for attempt 3 shows "#3" badge. Markers for attempt 1 show no badge.

**R-SV6: Session indicator on DAG nodes**
- Steps participating in a session show a small chain/link icon on their DAG `StepNode` (`components/dag/StepNode.tsx`)
- Detection: `stepDef.executor?.config?.continue_session === true` OR step has `_session_id` in its inputs array
- Tooltip on hover shows session name from `latestRun.executor_state.session_name`
- **Acceptance:** Icon appears on qualifying nodes. Non-session nodes have no icon. Tooltip shows correct session name.

**R-SV7: Highlight current step's boundary in session transcript**
- When the Session tab is active and a step is selected (`searchParams.step`), the transcript auto-scrolls to that step's first boundary marker and highlights it with a ring/background accent
- **Acceptance:** Select step "review" in DAG, switch to Session tab → transcript scrolls to "review" boundary. The boundary has a visible highlight (e.g., ring-violet-500/30 background).

---

## Assumptions (verified against code)

1. **Router uses TanStack Router with search param validation.** `router.tsx:28-73` defines `JobsRouteSearch` type (`:28-32`), `JobDetailSearch` type (`:53-58`), `validateJobsSearch()` (`:34-48`), and `validateJobDetailSearch()` (`:60-73`). Search params are the established UI state pattern.

2. **CanvasPage is self-contained with no shared state.** `CanvasPage.tsx` (468 lines) imports exclusively from `components/canvas/`, `hooks/useStepwise`, and `lib/api`. No cross-dependencies with `JobDashboard`. Can be conditionally rendered without refactoring.

3. **JobDashboard is a thin 66-line wrapper.** `JobDashboard.tsx:10-66` — sidebar with `JobList` + static placeholder. The real detail view is `JobDetailPage` at `/jobs/$jobId`. This makes the merge trivial: `JobsPage` wraps both and toggles between them.

4. **Canvas components are isolated in `components/canvas/`.** 5 files: `JobCard.tsx`, `BulkActionBar.tsx`, `CanvasLayout.tsx`, `DependencyArrows.tsx`, `MiniDag.tsx`. No naming conflicts with `components/jobs/` or `components/dag/`.

5. **AppLayout hardcodes 4 nav links.** `AppLayout.tsx:325-345` has inline `<Link>` elements for Jobs, Canvas, Flows, Settings. Canvas link at `:334-336` with `isCanvasActive` at `:193`. Removal is a direct deletion.

6. **JobCard already navigates to detail.** `JobCard.tsx:62-74` wraps the card in `<Link to="/jobs/$jobId" params={{ jobId: job.id }}>`. Grid view job clicks already work — we just need to ensure `view_mode` is preserved in the link.

7. **localStorage is the persistence convention.** Theme: `stepwise-theme` (`AppLayout.tsx:33`), sort: `stepwise-job-sort` (in `JobList`). View: `stepwise-job-view` follows the pattern.

8. **`view_mode` avoids param collision.** `JobDetailSearch` already uses `view` for `"dag" | "events" | "timeline" | "tree"` (`router.tsx:57`). Using `view_mode` for list/grid is orthogonal and won't collide since `JobDetailSearch` extends `JobsRouteSearch`.

9. **Session name stored in `executor_state.session_name`.** `agent.py:1222-1232` — `context.state_update_fn()` call sets `session_name` in the executor_state dict. This is stored in the `executor_state` JSON column of the `step_runs` table (`store.py:590` reads it back via `json.loads()`).

10. **`_session_id` propagates via artifact, not executor_state.** `agent.py:1357-1362` — `envelope.artifact["_session_id"] = process.session_name`. Downstream steps receive it as an input binding, not from executor_state.

11. **Agent output files are per-run at `executor_state.output_path`.** `agent.py:1225` stores the path. The existing `get_agent_output` endpoint (`server.py:1835-1850`) reads from this path and parses via `_parse_ndjson_events()`. The transcript endpoint follows the same pattern.

12. **WebSocket broadcasts include `run_id` only.** `_tail_agent_output()` (`server.py:317-339`) broadcasts `{"type": "agent_output", "run_id": run_id, "events": [...]}`. No `session_name` in the message. Frontend must maintain a `run_id → session` mapping.

13. **Right panel tab system uses URL search params with validation.** `JOB_DETAIL_TAB_VALUES` set (`router.tsx:50`), `RightPanelTab` type (`JobDetailPage.tsx:77`), `activeTab` derived from `searchParams.tab` (`JobDetailPage.tsx:110`). Adding "session" requires updating all three.

14. **No database migration needed.** Session names already exist in the `executor_state` JSON column. The session endpoints group in application logic, not SQL.

15. **Backend API test pattern uses TestClient.** `test_jobs_api.py:11-23` — fixture creates `TestClient(app)` with `STEPWISE_DB=":memory:"` and temp dirs. Tests make HTTP calls and assert on response status/JSON. Session endpoint tests follow this pattern.

16. **Frontend hook test pattern uses vi.mock + renderHook.** `useStepwise.test.ts:9-18` mocks `@/lib/api`, then `renderHook()` with `createWrapper()` (QueryClient with `retry: false, gcTime: 0`), then `waitFor(() => expect(result.current.isSuccess).toBe(true))`.

17. **`buildSegmentsFromEvents()` is exported and reusable.** `useAgentStream.ts:26-57` — pure function taking `AgentStreamEvent[]`, returning `AgentStreamState { segments, usage }`. Types `StreamSegment`, `ToolCallState`, `AgentStreamState` all exported (`:7-22`). The session stream hook reuses these directly.

18. **`SegmentRow` and `ToolCard` are internal to `AgentStreamView.tsx`.** `SegmentRow` (`:117-140`) and `ToolCard` (`:52-115`) are not exported. The session transcript view needs either: (a) extract them to a shared file, or (b) import `AgentStreamView` patterns and re-implement minimal rendering. Option (a) is cleaner.

---

## Implementation Steps

### Phase 1: IA Navigation Refactor (Steps 1-5)

No backend changes. All frontend.

---

#### Step 1: Add `view_mode` param to Jobs route search

**File:** `web/src/router.tsx`

1. Add validation set after `JOB_ROUTE_RANGE_VALUES` (line 26):
   ```typescript
   const JOB_VIEW_MODE_VALUES = new Set(["list", "grid"]);
   ```

2. Extend `JobsRouteSearch` type (line 28-32) — add `view_mode`:
   ```typescript
   type JobsRouteSearch = {
     q?: string;
     status?: "running" | "awaiting_input" | "paused" | "completed" | "failed" | "pending" | "cancelled";
     range?: "today" | "7d" | "30d";
     view_mode?: "list" | "grid";
   };
   ```

3. Extend `validateJobsSearch()` (line 34-48) — add `view_mode` parsing after `range`:
   ```typescript
   view_mode:
     typeof search.view_mode === "string" && JOB_VIEW_MODE_VALUES.has(search.view_mode)
       ? (search.view_mode as "list" | "grid")
       : undefined,
   ```

`JobDetailSearch` (line 53-58) inherits `view_mode` automatically via `& JobsRouteSearch`.

---

#### Step 2: Create unified `JobsPage` component

**File:** `web/src/pages/JobsPage.tsx` (new)

Create a thin shell that reads `view_mode` from search params and conditionally renders `JobDashboard` or `CanvasPage`:

```typescript
import { useSearch, useNavigate } from "@tanstack/react-router";
import { JobDashboard } from "./JobDashboard";
import { CanvasPage } from "./CanvasPage";
import { List, LayoutGrid } from "lucide-react";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "stepwise-job-view";

function getStoredView(): "list" | "grid" {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "grid" ? "grid" : "list";
}

export function JobsPage() {
  const searchParams = useSearch({ from: "/jobs" });
  const navigate = useNavigate();
  const viewMode = searchParams.view_mode ?? getStoredView();

  const setViewMode = (mode: "list" | "grid") => {
    localStorage.setItem(STORAGE_KEY, mode);
    navigate({
      search: (prev) => ({
        ...prev,
        view_mode: mode === "list" ? undefined : mode,
      }),
      replace: true,
    });
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center px-4 py-2 border-b border-border shrink-0">
        <div className="flex items-center gap-1 rounded-lg border border-border p-0.5 bg-zinc-100/50 dark:bg-zinc-900/50">
          <button
            onClick={() => setViewMode("list")}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors",
              viewMode === "list"
                ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                : "text-zinc-500 hover:text-foreground"
            )}
          >
            <List className="w-3.5 h-3.5" />
            List
          </button>
          <button
            onClick={() => setViewMode("grid")}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors",
              viewMode === "grid"
                ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                : "text-zinc-500 hover:text-foreground"
            )}
          >
            <LayoutGrid className="w-3.5 h-3.5" />
            Grid
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {viewMode === "grid" ? <CanvasPage /> : <JobDashboard />}
      </div>
    </div>
  );
}
```

`JobDashboard.tsx` and `CanvasPage.tsx` are preserved as-is — they become child components.

**Height note:** `CanvasPage` has `className="h-full overflow-y-auto"` at its root div (`:267`). The `flex-1 min-h-0` wrapper ensures `h-full` resolves correctly within the flex column.

---

#### Step 3: Wire `JobsPage` into router, redirect `/canvas`

**File:** `web/src/router.tsx`

1. Replace import (line 8):
   ```typescript
   // Before:
   import { JobDashboard } from "@/pages/JobDashboard";
   // After:
   import { JobsPage } from "@/pages/JobsPage";
   ```

2. Remove `CanvasPage` import (line 13):
   ```typescript
   // Delete:
   import { CanvasPage } from "@/pages/CanvasPage";
   ```

3. Update `jobsRoute` component (line 94):
   ```typescript
   component: JobsPage,  // was: JobDashboard
   ```

4. Change `canvasRoute` (lines 166-170) to a redirect:
   ```typescript
   const canvasRoute = createRoute({
     getParentRoute: () => rootRoute,
     path: "/canvas",
     beforeLoad: () => {
       throw redirect({ to: "/jobs", search: { view_mode: "grid" as const } });
     },
   });
   ```

---

#### Step 4: Remove Canvas from `AppLayout` navigation

**File:** `web/src/components/layout/AppLayout.tsx`

1. Delete `isCanvasActive` (line 193):
   ```typescript
   // Delete: const isCanvasActive = currentPath === "/canvas";
   ```

2. Delete the Canvas `<Link>` block (lines 334-336):
   ```tsx
   // Delete:
   <Link to="/canvas" className={navItemClass(isCanvasActive)}>
     <Network className="h-4 w-4 md:mr-1.5 md:h-3.5 md:w-3.5" />
     <span className="hidden md:inline">Canvas</span>
   </Link>
   ```

3. Remove canvas branch from `routeKey` derivation (lines 200-202):
   ```typescript
   // Delete the "canvas" case from the ternary chain
   ```

4. Remove canvas branch from title effect (lines 267-268):
   ```typescript
   // Delete: } else if (isCanvasActive) { title = "Canvas — Stepwise"; }
   ```

5. Remove `isCanvasActive` from `useEffect` deps (line 282).

6. Remove `Network` from lucide-react import (line 18) if unused elsewhere. Check via grep first.

---

#### Step 5: Preserve `view_mode` in `JobCard` navigation

**File:** `web/src/components/canvas/JobCard.tsx`

The existing `<Link to="/jobs/$jobId" params={{ jobId: job.id }}>` (line 62-74) does not forward search params. Update to preserve `view_mode`:

```tsx
<Link
  to="/jobs/$jobId"
  params={{ jobId: job.id }}
  search={(prev) => ({ ...prev })}
  className={cn(...)}
>
```

This ensures the `view_mode=grid` param persists when navigating to detail and back.

---

### Phase 2: Session Viewer Backend (Steps 6-7)

Fully independent of Phase 1. Can be built in parallel.

---

#### Step 6: Backend — Session listing endpoint

**File:** `src/stepwise/server.py`

Add after the last job endpoint (`get_job_outputs_alias`, line 1993):

```python
@app.get("/api/jobs/{job_id}/sessions")
def get_job_sessions(job_id: str):
    """List agent sessions for a job, grouped by executor_state.session_name."""
    engine = _get_engine()
    try:
        engine.store.load_job(job_id)  # verify job exists
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    runs = engine.store.runs_for_job(job_id)
    sessions: dict[str, dict] = {}
    for run in runs:
        sname = (run.executor_state or {}).get("session_name")
        if not sname:
            continue
        if sname not in sessions:
            sessions[sname] = {
                "session_name": sname,
                "run_ids": [],
                "step_names": [],
                "is_active": False,
                "started_at": None,
                "latest_at": None,
            }
        s = sessions[sname]
        s["run_ids"].append(run.id)
        if run.step_name not in s["step_names"]:
            s["step_names"].append(run.step_name)
        if run.status == StepRunStatus.RUNNING:
            s["is_active"] = True
        ts = run.started_at.isoformat() if run.started_at else None
        if ts and (not s["started_at"] or ts < s["started_at"]):
            s["started_at"] = ts
        end_ts = (run.completed_at or run.started_at)
        end_iso = end_ts.isoformat() if end_ts else None
        if end_iso and (not s["latest_at"] or end_iso > s["latest_at"]):
            s["latest_at"] = end_iso
    return {"sessions": sorted(sessions.values(), key=lambda x: x["started_at"] or "")}
```

Follows the existing endpoint pattern: `_get_engine()`, `try/except KeyError` for 404, `runs_for_job()` call (`store.py:598-603`), returns plain dict (JSON serializable).

---

#### Step 7: Backend — Session transcript endpoint

**File:** `src/stepwise/server.py`

Add after the sessions listing endpoint:

```python
@app.get("/api/jobs/{job_id}/sessions/{session_name}/transcript")
def get_session_transcript(job_id: str, session_name: str):
    """Get concatenated agent output events for a session with step boundary markers."""
    engine = _get_engine()
    try:
        engine.store.load_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    runs = engine.store.runs_for_job(job_id)
    session_runs = [
        r for r in runs
        if (r.executor_state or {}).get("session_name") == session_name
    ]
    if not session_runs:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_name}")
    session_runs.sort(key=lambda r: r.started_at or datetime.min)

    all_events: list[dict] = []
    boundaries: list[dict] = []
    for run in session_runs:
        boundaries.append({
            "event_index": len(all_events),
            "step_name": run.step_name,
            "attempt": run.attempt,
            "run_id": run.id,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "status": run.status.value,
        })
        output_path = (run.executor_state or {}).get("output_path")
        if output_path:
            try:
                with open(output_path) as f:
                    raw = f.read()
                all_events.extend(_parse_ndjson_events(raw))
            except FileNotFoundError:
                pass
    return {"events": all_events, "boundaries": boundaries}
```

This follows the exact pattern of `get_agent_output()` (`server.py:1835-1850`): reads `executor_state.output_path`, opens file, handles `FileNotFoundError`, parses via `_parse_ndjson_events()`.

---

### Phase 3: Session Viewer Frontend (Steps 8-14)

Depends on Phase 2 backend endpoints. Types/hooks (steps 8-10) can be stubbed and tested independently.

---

#### Step 8: Frontend — API functions and types

**File:** `web/src/lib/types.ts`

Add after existing type definitions:

```typescript
export interface SessionInfo {
  session_name: string;
  run_ids: string[];
  step_names: string[];
  is_active: boolean;
  started_at: string | null;
  latest_at: string | null;
}

export interface SessionBoundary {
  event_index: number;
  step_name: string;
  attempt: number;
  run_id: string;
  started_at: string | null;
  status: string;
}

export interface SessionTranscript {
  events: AgentStreamEvent[];
  boundaries: SessionBoundary[];
}
```

**File:** `web/src/lib/api.ts`

Add after `fetchAgentOutput()` (line 237), following the same `request<T>()` pattern:

```typescript
export function fetchJobSessions(
  jobId: string
): Promise<{ sessions: SessionInfo[] }> {
  return request<{ sessions: SessionInfo[] }>(`/jobs/${jobId}/sessions`);
}

export function fetchSessionTranscript(
  jobId: string,
  sessionName: string
): Promise<SessionTranscript> {
  return request<SessionTranscript>(
    `/jobs/${jobId}/sessions/${encodeURIComponent(sessionName)}/transcript`
  );
}
```

Import `SessionInfo`, `SessionTranscript` from `@/lib/types`.

---

#### Step 9: Frontend — React Query hooks

**File:** `web/src/hooks/useStepwise.ts`

Add after `useAgentOutput()` (line 117), following the identical pattern:

```typescript
export function useJobSessions(jobId: string | undefined) {
  return useQuery({
    queryKey: ["sessions", jobId],
    queryFn: () => api.fetchJobSessions(jobId!),
    enabled: !!jobId,
  });
}

export function useSessionTranscript(
  jobId: string | undefined,
  sessionName: string | undefined,
) {
  return useQuery({
    queryKey: ["sessionTranscript", jobId, sessionName],
    queryFn: () => api.fetchSessionTranscript(jobId!, sessionName!),
    enabled: !!jobId && !!sessionName,
    staleTime: Infinity,
  });
}
```

---

#### Step 10: WebSocket invalidation for session queries

**File:** `web/src/hooks/useStepwiseWebSocket.ts`

In the tick handler's `for (const jobId of msg.changed_jobs)` loop (lines 99-104), add after `queryClient.invalidateQueries({ queryKey: ["jobTree", jobId] })` (line 103):

```typescript
queryClient.invalidateQueries({ queryKey: ["sessions", jobId] });
queryClient.invalidateQueries({ queryKey: ["sessionTranscript", jobId] });
```

---

#### Step 11: Extract shared segment rendering components

**File:** `web/src/components/jobs/AgentStreamView.tsx`

Extract `SegmentRow` (lines 117-140) and `ToolCard` (lines 52-115) into a new shared file:

**File:** `web/src/components/jobs/StreamSegments.tsx` (new)

Move `ToolCard` and `SegmentRow` here, export both. Update `AgentStreamView.tsx` to import from `./StreamSegments`. This enables reuse by `SessionTranscriptView` without duplication.

Also export `toolIcon()` helper (lines 31-50) and `VIRTUAL_THRESHOLD` constant (line 174).

---

#### Step 12: Frontend — Session stream hook

**File:** `web/src/hooks/useSessionStream.ts` (new)

Extends the backfill+live pattern from `useAgentStream.ts` (lines 61-156) to work across multiple run IDs:

```typescript
import { useEffect, useRef, useState, useCallback } from "react";
import { subscribeAgentOutput } from "./useStepwiseWebSocket";
import { buildSegmentsFromEvents, type StreamSegment, type AgentStreamState } from "./useAgentStream";
import type { AgentStreamEvent, SessionBoundary } from "@/lib/types";

export interface SessionStreamState {
  segments: StreamSegment[];
  boundaries: SessionBoundary[];
  usage: { used: number; size: number } | null;
}

export function useSessionStream(
  runIds: string[],
  backfillEvents: AgentStreamEvent[] | null | undefined,
  backfillBoundaries: SessionBoundary[] | null | undefined,
  isLive: boolean,
): { state: SessionStreamState; version: number } {
  // Same pattern as useAgentStream:
  // 1. stateRef for mutable state, version counter for re-renders
  // 2. Subscribe to WebSocket agent_output, filter by run_id in runIds set
  // 3. Queue live events until backfill arrives
  // 4. On backfill: buildSegmentsFromEvents(backfillEvents), store boundaries, replay queue
  // 5. Live events append to last segment group (after last boundary)
}
```

Key differences from `useAgentStream`:
- Filters by `run_id ∈ new Set(runIds)` instead of single `run_id` match
- Manages boundary positions alongside segments
- Only subscribes when `isLive` is true (optimization: don't listen for completed sessions)

---

#### Step 13: Frontend — `SessionTranscriptView` component

**File:** `web/src/components/jobs/SessionTranscriptView.tsx` (new)

```typescript
interface SessionTranscriptViewProps {
  jobId: string;
  sessionName: string;
  runIds: string[];
  isLive: boolean;
  highlightStep?: string | null;
  onNavigateToStep: (stepName: string) => void;
}
```

Structure:
- Uses `useSessionTranscript(jobId, sessionName)` for REST backfill
- Uses `useSessionStream(runIds, backfillEvents, backfillBoundaries, isLive)` for live state
- Renders segments via imported `SegmentRow` from `StreamSegments.tsx` (Step 11)
- At boundary positions, renders `StepBoundaryMarker`:
  ```tsx
  function StepBoundaryMarker({ boundary, isHighlighted, onClick }: {
    boundary: SessionBoundary;
    isHighlighted: boolean;
    onClick: () => void;
  }) {
    return (
      <div className={cn(
        "flex items-center gap-2 py-2 px-3 my-1",
        isHighlighted && "ring-1 ring-violet-500/30 rounded-md bg-violet-500/5"
      )}>
        <div className="flex-1 h-px bg-border" />
        <button
          onClick={onClick}
          className="text-[10px] font-medium text-zinc-500 hover:text-zinc-300 bg-zinc-800 border border-border rounded-full px-2.5 py-0.5 transition-colors"
        >
          {boundary.step_name}
          {boundary.attempt > 1 && <span className="text-zinc-600 ml-1">#{boundary.attempt}</span>}
        </button>
        <div className="flex-1 h-px bg-border" />
      </div>
    );
  }
  ```
- Virtualization via `@tanstack/react-virtual` when segments > `VIRTUAL_THRESHOLD` (200)
- Auto-scroll to `highlightStep` boundary via `useEffect` + `scrollIntoView()`
- Search integration via `useLogSearch` (same import as `AgentStreamView.tsx:316`)

---

#### Step 14: Frontend — `SessionTab` and wiring into `JobDetailPage`

**File:** `web/src/components/jobs/SessionTab.tsx` (new)

```typescript
interface SessionTabProps {
  jobId: string;
  highlightStep?: string | null;
  onNavigateToStep: (stepName: string) => void;
}
```

Logic:
1. `useJobSessions(jobId)` → session list
2. No sessions → "No agent sessions for this job" empty state
3. One session → render `SessionTranscriptView` directly
4. Multiple → session picker + transcript. Default selection: first `is_active` session, or first by `started_at`

**File:** `web/src/router.tsx`

Update `JOB_DETAIL_TAB_VALUES` (line 50):
```typescript
const JOB_DETAIL_TAB_VALUES = new Set(["run", "step", "session"]);
```

Update `JobDetailSearch` type (line 55):
```typescript
tab?: "run" | "step" | "session";
```

**File:** `web/src/pages/JobDetailPage.tsx`

1. Update `RightPanelTab` type (line 77):
   ```typescript
   type RightPanelTab = "run" | "step" | "session";
   ```

2. Add session query near other data fetches:
   ```typescript
   const { data: sessionData } = useJobSessions(jobId);
   const hasSessions = (sessionData?.sessions?.length ?? 0) > 0;
   ```

3. Add `TabsTrigger` after "Step" trigger (line 721-723):
   ```tsx
   {hasSessions && (
     <TabsTrigger value="session" className="text-xs gap-1 px-2.5">
       Session
     </TabsTrigger>
   )}
   ```

4. Add `TabsContent` after "Step" content (after line 760):
   ```tsx
   <TabsContent
     value="session"
     className={cn("flex-1 min-h-0 overflow-y-auto", activeTab !== "session" && "hidden")}
   >
     <SessionTab
       jobId={jobId}
       highlightStep={selectedStep}
       onNavigateToStep={(stepName) =>
         navigate({
           search: (prev: JobDetailSearch) => ({
             ...prev,
             step: stepName,
             tab: "run" as const,
             panel: "open" as const,
           }),
           replace: true,
         })
       }
     />
   </TabsContent>
   ```

5. When no step is selected but `tab=session` is in URL, show the `SessionTab` in the overview panel position (the `if (!resolvedStep)` branch around line 685). Add a conditional: if `hasSessions && activeTab === "session"`, render a tab bar with "Overview" and "Session" tabs instead of the static `JobOverview`.

---

### Phase 4: Polish (Steps 15-16)

Depends on Phases 1 + 3 being complete.

---

#### Step 15: DAG session indicator on `StepNode`

**File:** `web/src/components/dag/StepNode.tsx`

The `StepNodeProps` interface (lines 36-52) already receives `stepDef: StepDefinition` and `latestRun: StepRun | null`.

In the node header area, add after the executor type label:

```tsx
import { Link2 } from "lucide-react";

// Detect session participation:
const hasSession =
  stepDef.executor?.config?.continue_session === true ||
  stepDef.inputs?.some((i) => i.field === "_session_id");
const sessionName = latestRun?.executor_state?.session_name;

// Render (in the header flex row):
{hasSession && (
  <span className="text-violet-400/60" title={sessionName ? `Session: ${sessionName}` : "Session step"}>
    <Link2 className="w-2.5 h-2.5 inline" />
  </span>
)}
```

---

#### Step 16: Frontend — add `g,g` hotkey for view toggle

**File:** `web/src/components/layout/AppLayout.tsx`

Add a `g,g` keyboard shortcut to toggle between list and grid views when on the Jobs page. This provides keyboard-accessible view switching consistent with the existing `g,j`/`g,f`/`g,s` pattern (lines 237-248).

This is low priority and can be deferred.

---

## Dependency Graph

```
Phase 1 (IA Refactor)              Phase 2 (Session Backend)
├─ Step 1: Router search params     ├─ Step 6: Session listing endpoint
├─ Step 2: JobsPage component       └─ Step 7: Transcript endpoint
├─ Step 3: Wire router + redirect        │
├─ Step 4: Remove Canvas from nav        │
└─ Step 5: Preserve view_mode            │
                                          │
                              Phase 3 (Session Frontend)
                              ├─ Step 8: API types + functions
                              ├─ Step 9: React Query hooks
                              ├─ Step 10: WebSocket invalidation
                              ├─ Step 11: Extract shared components
                              ├─ Step 12: Session stream hook
                              ├─ Step 13: SessionTranscriptView
                              └─ Step 14: SessionTab + wiring
                                          │
                              Phase 4 (Polish)
                              ├─ Step 15: DAG session indicator
                              └─ Step 16: View toggle hotkey
```

**Parallelism:** Phase 1 and Phase 2 are fully independent. Phase 3 steps 8-10 (types/hooks/WS) can start while backend is being built. Phase 4 depends on both Phase 1 and Phase 3.

---

## Testing Strategy

### Backend Tests

**File:** `tests/test_session_api.py` (new)

Uses the established TestClient pattern from `tests/test_jobs_api.py:11-23`:

```python
import json
import os
import tempfile
import pytest
from datetime import datetime, timezone
from starlette.testclient import TestClient

from stepwise.server import app
from stepwise.models import StepRun, StepRunStatus, HandoffEnvelope, Sidecar

NDJSON_SAMPLE = '{"params":{"update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"Hello"}}}}\n'


@pytest.fixture
def client(tmp_path):
    old_env = os.environ.copy()
    os.environ["STEPWISE_PROJECT_DIR"] = str(tmp_path)
    os.environ["STEPWISE_DB"] = ":memory:"
    os.environ["STEPWISE_TEMPLATES"] = str(tmp_path / "_templates")
    os.environ["STEPWISE_JOBS_DIR"] = str(tmp_path / "_jobs")
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    os.environ.clear()
    os.environ.update(old_env)


def _create_job_with_session_runs(client, tmp_path, session_configs):
    """Helper: create a job, then insert runs with crafted executor_state.

    session_configs: list of dicts with keys:
      step_name, session_name, status, output_content (optional NDJSON string)
    """
    # Create job via API
    resp = client.post("/api/jobs", json={
        "flow_name": "test",
        "workflow": {"steps": {"s": {"name": "s", "executor": {"type": "agent"}}}},
    })
    job_id = resp.json()["id"]

    # Insert runs directly via engine store
    import stepwise.server as srv
    engine = srv._engine
    for i, cfg in enumerate(session_configs):
        output_path = None
        if cfg.get("output_content"):
            p = tmp_path / f"output-{i}.ndjson"
            p.write_text(cfg["output_content"])
            output_path = str(p)
        run = StepRun(
            id=f"run-{i}",
            job_id=job_id,
            step_name=cfg["step_name"],
            attempt=cfg.get("attempt", 1),
            status=StepRunStatus(cfg.get("status", "completed")),
            executor_state={
                "session_name": cfg["session_name"],
                "output_path": output_path,
            },
            started_at=datetime(2024, 1, 1, 0, i, tzinfo=timezone.utc),
            completed_at=datetime(2024, 1, 1, 0, i + 1, tzinfo=timezone.utc)
                if cfg.get("status", "completed") == "completed" else None,
        )
        engine.store.save_run(run)
    return job_id
```

**Test cases:**

```python
class TestSessionListing:
    def test_no_sessions_returns_empty(self, client, tmp_path):
        """Job with no agent runs → empty sessions list."""
        resp = client.post("/api/jobs", json={
            "flow_name": "test",
            "workflow": {"steps": {"s": {"name": "s", "executor": {"type": "script"}}}},
        })
        job_id = resp.json()["id"]
        resp = client.get(f"/api/jobs/{job_id}/sessions")
        assert resp.status_code == 200
        assert resp.json() == {"sessions": []}

    def test_single_session_two_runs(self, client, tmp_path):
        """Two runs with same session_name → one session entry."""
        job_id = _create_job_with_session_runs(client, tmp_path, [
            {"step_name": "plan", "session_name": "sess-1", "status": "completed"},
            {"step_name": "impl", "session_name": "sess-1", "status": "completed"},
        ])
        resp = client.get(f"/api/jobs/{job_id}/sessions")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["session_name"] == "sess-1"
        assert sessions[0]["run_ids"] == ["run-0", "run-1"]
        assert sessions[0]["step_names"] == ["plan", "impl"]
        assert sessions[0]["is_active"] is False

    def test_multiple_sessions(self, client, tmp_path):
        """Three runs across two sessions → two entries."""
        job_id = _create_job_with_session_runs(client, tmp_path, [
            {"step_name": "plan", "session_name": "sess-1", "status": "completed"},
            {"step_name": "impl", "session_name": "sess-1", "status": "completed"},
            {"step_name": "review", "session_name": "sess-2", "status": "completed"},
        ])
        resp = client.get(f"/api/jobs/{job_id}/sessions")
        sessions = resp.json()["sessions"]
        assert len(sessions) == 2
        assert sessions[0]["session_name"] == "sess-1"
        assert sessions[1]["session_name"] == "sess-2"

    def test_active_session_detection(self, client, tmp_path):
        """Running run in session → is_active: true."""
        job_id = _create_job_with_session_runs(client, tmp_path, [
            {"step_name": "plan", "session_name": "sess-1", "status": "completed"},
            {"step_name": "impl", "session_name": "sess-1", "status": "running"},
        ])
        resp = client.get(f"/api/jobs/{job_id}/sessions")
        sessions = resp.json()["sessions"]
        assert sessions[0]["is_active"] is True

    def test_nonexistent_job_returns_404(self, client):
        resp = client.get("/api/jobs/nonexistent/sessions")
        assert resp.status_code == 404


class TestSessionTranscript:
    def test_transcript_ordering_and_boundaries(self, client, tmp_path):
        """Events concatenated in started_at order with correct boundary indices."""
        job_id = _create_job_with_session_runs(client, tmp_path, [
            {"step_name": "plan", "session_name": "sess-1",
             "output_content": NDJSON_SAMPLE},
            {"step_name": "impl", "session_name": "sess-1",
             "output_content": NDJSON_SAMPLE + NDJSON_SAMPLE},
        ])
        resp = client.get(f"/api/jobs/{job_id}/sessions/sess-1/transcript")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 3  # 1 from plan + 2 from impl
        assert len(data["boundaries"]) == 2
        assert data["boundaries"][0]["event_index"] == 0
        assert data["boundaries"][0]["step_name"] == "plan"
        assert data["boundaries"][1]["event_index"] == 1
        assert data["boundaries"][1]["step_name"] == "impl"

    def test_missing_output_file(self, client, tmp_path):
        """Run with output_path pointing to deleted file → empty events, boundary present."""
        job_id = _create_job_with_session_runs(client, tmp_path, [
            {"step_name": "plan", "session_name": "sess-1",
             "output_content": NDJSON_SAMPLE},
        ])
        # Delete the output file
        import stepwise.server as srv
        runs = srv._engine.store.runs_for_job(job_id)
        os.unlink(runs[0].executor_state["output_path"])

        resp = client.get(f"/api/jobs/{job_id}/sessions/sess-1/transcript")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 0
        assert len(data["boundaries"]) == 1

    def test_nonexistent_session_returns_404(self, client, tmp_path):
        resp = client.post("/api/jobs", json={
            "flow_name": "test",
            "workflow": {"steps": {"s": {"name": "s", "executor": {"type": "agent"}}}},
        })
        job_id = resp.json()["id"]
        resp = client.get(f"/api/jobs/{job_id}/sessions/nonexistent/transcript")
        assert resp.status_code == 404

    def test_url_encoded_session_name(self, client, tmp_path):
        """Session names with special chars work."""
        job_id = _create_job_with_session_runs(client, tmp_path, [
            {"step_name": "plan", "session_name": "step-job/123-plan-1",
             "output_content": NDJSON_SAMPLE},
        ])
        resp = client.get(f"/api/jobs/{job_id}/sessions/step-job%2F123-plan-1/transcript")
        assert resp.status_code == 200
```

**Run command:**
```bash
uv run pytest tests/test_session_api.py -x -q
```

### Frontend Tests

#### Hook Tests

**File:** `web/src/hooks/useStepwise.test.ts` (extend existing file)

Add tests following the exact pattern at lines 43-70 (mock API, `renderHook`, `waitFor`):

```typescript
// Add to vi.mock block (line 9-18):
//   fetchJobSessions: vi.fn(),
//   fetchSessionTranscript: vi.fn(),

describe("useJobSessions", () => {
  it("fetches sessions for a job", async () => {
    const data = {
      sessions: [{
        session_name: "sess-1",
        run_ids: ["r1", "r2"],
        step_names: ["plan", "impl"],
        is_active: false,
        started_at: "2024-01-01T00:00:00Z",
        latest_at: "2024-01-01T00:01:00Z",
      }],
    };
    mockedApi.fetchJobSessions.mockResolvedValueOnce(data);

    const { result } = renderHook(() => useJobSessions("j1"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(data);
    expect(mockedApi.fetchJobSessions).toHaveBeenCalledWith("j1");
  });

  it("does not fetch when jobId is undefined", () => {
    const { result } = renderHook(() => useJobSessions(undefined), {
      wrapper: createWrapper(),
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(mockedApi.fetchJobSessions).not.toHaveBeenCalled();
  });
});

describe("useSessionTranscript", () => {
  it("fetches transcript for a session", async () => {
    const data = {
      events: [{ t: "text", text: "Hello" }],
      boundaries: [{ event_index: 0, step_name: "plan", attempt: 1, run_id: "r1", started_at: null, status: "completed" }],
    };
    mockedApi.fetchSessionTranscript.mockResolvedValueOnce(data);

    const { result } = renderHook(
      () => useSessionTranscript("j1", "sess-1"),
      { wrapper: createWrapper() },
    );

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(data);
    expect(mockedApi.fetchSessionTranscript).toHaveBeenCalledWith("j1", "sess-1");
  });

  it("does not fetch when sessionName is undefined", () => {
    const { result } = renderHook(
      () => useSessionTranscript("j1", undefined),
      { wrapper: createWrapper() },
    );
    expect(result.current.fetchStatus).toBe("idle");
  });
});
```

**Run command:**
```bash
cd web && npx vitest run src/hooks/useStepwise.test.ts
```

#### Session Stream Hook Tests

**File:** `web/src/hooks/useSessionStream.test.ts` (new)

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import type { AgentStreamEvent, SessionBoundary } from "@/lib/types";

// Mock the WebSocket subscription
vi.mock("./useStepwiseWebSocket", () => {
  const listeners = new Set<(msg: any) => void>();
  return {
    subscribeAgentOutput: vi.fn((fn) => {
      listeners.add(fn);
      return () => listeners.delete(fn);
    }),
    __broadcast: (msg: any) => {
      for (const fn of listeners) fn(msg);
    },
    __listeners: listeners,
  };
});

import { useSessionStream } from "./useSessionStream";
import { __broadcast, __listeners } from "./useStepwiseWebSocket";

beforeEach(() => {
  __listeners.clear();
});

describe("useSessionStream", () => {
  it("builds segments from backfill events with boundaries", () => {
    const events: AgentStreamEvent[] = [
      { t: "text", text: "Step 1 output" },
      { t: "text", text: "Step 2 output" },
    ];
    const boundaries: SessionBoundary[] = [
      { event_index: 0, step_name: "plan", attempt: 1, run_id: "r1", started_at: null, status: "completed" },
      { event_index: 1, step_name: "impl", attempt: 1, run_id: "r2", started_at: null, status: "completed" },
    ];

    const { result } = renderHook(() =>
      useSessionStream(["r1", "r2"], events, boundaries, false)
    );

    expect(result.current.state.segments.length).toBe(2);
    expect(result.current.state.boundaries).toEqual(boundaries);
  });

  it("appends live WebSocket events for matching run_id", () => {
    const backfillEvents: AgentStreamEvent[] = [{ t: "text", text: "Initial" }];
    const boundaries: SessionBoundary[] = [
      { event_index: 0, step_name: "plan", attempt: 1, run_id: "r1", started_at: null, status: "running" },
    ];

    const { result } = renderHook(() =>
      useSessionStream(["r1"], backfillEvents, boundaries, true)
    );

    act(() => {
      __broadcast({ type: "agent_output", run_id: "r1", events: [{ t: "text", text: " more" }] });
    });

    // Text segments concatenate
    const textSeg = result.current.state.segments.find((s) => s.type === "text");
    expect(textSeg?.type === "text" && textSeg.text).toContain("more");
  });

  it("ignores live events for non-matching run_id", () => {
    const backfillEvents: AgentStreamEvent[] = [{ t: "text", text: "Initial" }];
    const boundaries: SessionBoundary[] = [
      { event_index: 0, step_name: "plan", attempt: 1, run_id: "r1", started_at: null, status: "completed" },
    ];

    const { result } = renderHook(() =>
      useSessionStream(["r1"], backfillEvents, boundaries, true)
    );

    const versionBefore = result.current.version;

    act(() => {
      __broadcast({ type: "agent_output", run_id: "r99", events: [{ t: "text", text: "nope" }] });
    });

    expect(result.current.version).toBe(versionBefore);
  });
});
```

**Run command:**
```bash
cd web && npx vitest run src/hooks/useSessionStream.test.ts
```

#### Component Tests

**File:** `web/src/components/jobs/SessionTab.test.tsx` (new)

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import type { ReactNode } from "react";

// Mock the hooks
vi.mock("@/hooks/useStepwise", () => ({
  useJobSessions: vi.fn(),
  useSessionTranscript: vi.fn(),
}));

vi.mock("./SessionTranscriptView", () => ({
  SessionTranscriptView: ({ sessionName }: { sessionName: string }) =>
    createElement("div", { "data-testid": "transcript" }, `Transcript: ${sessionName}`),
}));

import { useJobSessions } from "@/hooks/useStepwise";
import { SessionTab } from "./SessionTab";

const mockedUseJobSessions = vi.mocked(useJobSessions);

function createWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
}

describe("SessionTab", () => {
  it("shows empty state when no sessions", () => {
    mockedUseJobSessions.mockReturnValue({
      data: { sessions: [] },
      isLoading: false,
    } as any);

    render(
      createElement(SessionTab, { jobId: "j1", onNavigateToStep: vi.fn() }),
      { wrapper: createWrapper() },
    );

    expect(screen.getByText(/no agent sessions/i)).toBeTruthy();
  });

  it("renders transcript directly for single session", () => {
    mockedUseJobSessions.mockReturnValue({
      data: {
        sessions: [{
          session_name: "sess-1",
          run_ids: ["r1"],
          step_names: ["plan"],
          is_active: false,
          started_at: "2024-01-01T00:00:00Z",
          latest_at: "2024-01-01T00:01:00Z",
        }],
      },
      isLoading: false,
    } as any);

    render(
      createElement(SessionTab, { jobId: "j1", onNavigateToStep: vi.fn() }),
      { wrapper: createWrapper() },
    );

    expect(screen.getByTestId("transcript")).toBeTruthy();
    expect(screen.getByText("Transcript: sess-1")).toBeTruthy();
  });

  it("renders session picker for multiple sessions", () => {
    mockedUseJobSessions.mockReturnValue({
      data: {
        sessions: [
          { session_name: "sess-1", run_ids: ["r1"], step_names: ["plan"], is_active: false, started_at: "2024-01-01T00:00:00Z", latest_at: null },
          { session_name: "sess-2", run_ids: ["r2"], step_names: ["review"], is_active: true, started_at: "2024-01-01T00:01:00Z", latest_at: null },
        ],
      },
      isLoading: false,
    } as any);

    render(
      createElement(SessionTab, { jobId: "j1", onNavigateToStep: vi.fn() }),
      { wrapper: createWrapper() },
    );

    // Should show both session names in picker
    expect(screen.getByText(/sess-1/)).toBeTruthy();
    expect(screen.getByText(/sess-2/)).toBeTruthy();
  });
});
```

**Run command:**
```bash
cd web && npx vitest run src/components/jobs/SessionTab.test.tsx
```

#### IA Refactor Tests

**File:** `web/src/pages/JobsPage.test.tsx` (new)

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import type { ReactNode } from "react";

// Mock router
const mockNavigate = vi.fn();
let mockSearch: Record<string, string> = {};

vi.mock("@tanstack/react-router", () => ({
  useSearch: () => mockSearch,
  useNavigate: () => mockNavigate,
}));

// Mock child components to avoid deep rendering
vi.mock("./JobDashboard", () => ({
  JobDashboard: () => createElement("div", { "data-testid": "list-view" }, "List View"),
}));
vi.mock("./CanvasPage", () => ({
  CanvasPage: () => createElement("div", { "data-testid": "grid-view" }, "Grid View"),
}));

import { JobsPage } from "./JobsPage";

function createWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: qc }, children);
}

beforeEach(() => {
  vi.clearAllMocks();
  mockSearch = {};
  localStorage.clear();
});

describe("JobsPage", () => {
  it("defaults to list view when no param and no localStorage", () => {
    render(createElement(JobsPage), { wrapper: createWrapper() });
    expect(screen.getByTestId("list-view")).toBeTruthy();
  });

  it("renders grid view when view_mode=grid in URL", () => {
    mockSearch = { view_mode: "grid" };
    render(createElement(JobsPage), { wrapper: createWrapper() });
    expect(screen.getByTestId("grid-view")).toBeTruthy();
  });

  it("uses localStorage fallback when no URL param", () => {
    localStorage.setItem("stepwise-job-view", "grid");
    render(createElement(JobsPage), { wrapper: createWrapper() });
    expect(screen.getByTestId("grid-view")).toBeTruthy();
  });

  it("switching to grid updates localStorage and navigates", () => {
    render(createElement(JobsPage), { wrapper: createWrapper() });
    fireEvent.click(screen.getByText("Grid"));
    expect(localStorage.getItem("stepwise-job-view")).toBe("grid");
    expect(mockNavigate).toHaveBeenCalled();
  });
});
```

**Run command:**
```bash
cd web && npx vitest run src/pages/JobsPage.test.tsx
```

### Full Regression Suite

```bash
uv run pytest tests/ -x -q          # all backend tests
cd web && npx vitest run             # all frontend tests
cd web && npx eslint src/            # lint check
```

### Manual Verification Checklist

**IA Refactor:**
1. Navigate to `/jobs` → list view with segmented control visible
2. Click "Grid" → grid view renders, URL shows `?view_mode=grid`
3. Refresh → grid view persists (localStorage)
4. Set `?status=running` filter → switch to grid → switch back → filter preserved
5. Navigate to `/canvas` → redirected to `/jobs?view_mode=grid`
6. Top nav shows: Jobs, Flows, Settings (no Canvas)
7. Click job card in grid → detail page. Press Back → grid view
8. Browser title never shows "Canvas"

**Session Viewer:**
1. Run a flow with `continue_session: true` across 2+ steps
2. Open job detail → Session tab appears in right panel
3. Click Session tab → transcript with step boundary markers
4. While agent running → live output streams into transcript
5. Click boundary marker → navigates to step's Run tab
6. Select step in DAG, switch to Session tab → auto-scrolls to that step's boundary
7. Job without sessions → no Session tab
8. DAG shows link icon on session steps, tooltip shows session name

---

## Risks & Mitigations

### R1: CanvasPage height under flex wrapper
**Risk:** `CanvasPage` root div has `className="h-full overflow-y-auto"` (line 267). When nested inside `JobsPage`'s flex column with a toggle bar, `h-full` could compute wrong.
**Mitigation:** The `JobsPage` wrapper uses `flex-1 min-h-0` on the content div, which constrains the child's `h-full` to the remaining flex space. This is the standard pattern for nested flex overflow. Verified by reading the CanvasPage root element.

### R2: Search param collision
**Risk:** Both `view` and `view_mode` exist as search params.
**Mitigation:** `view_mode` is on `JobsRouteSearch` (list/grid toggle), `view` is on `JobDetailSearch` (dag/events/timeline/tree). They're in different route scopes. `JobDetailSearch` extends `JobsRouteSearch`, so `view_mode` passes through but is ignored by the detail page. No collision because the validation functions (`:34-48`, `:60-73`) handle them independently.

### R3: Large session transcripts
**Risk:** Session spanning many steps with long outputs → thousands of events from REST.
**Mitigation:** `_parse_ndjson_events()` already produces condensed events (text chunks, tool start/end). Virtualization at >200 segments handles rendering. If perf becomes an issue, add cursor-based pagination (future, not in scope).

### R4: Agent output file cleanup
**Risk:** `executor_state.output_path` may point to deleted files after job completion.
**Mitigation:** Transcript endpoint handles `FileNotFoundError` gracefully (returns empty events, boundary still present). UI renders boundary markers regardless. Can add a "(output unavailable)" hint for empty segments between boundaries.

### R5: CanvasPage toolbar stacking
**Risk:** Grid view has its own sticky toolbar (select all, hide completed). Combined with the view toggle bar, there are two toolbars.
**Mitigation:** Acceptable in v1 — the toolbars serve different purposes (view toggle vs. canvas actions). Future polish could merge them.

### R6: Extracting SegmentRow/ToolCard
**Risk:** Extracting internal components from `AgentStreamView.tsx` could introduce regressions.
**Mitigation:** Components are pure presentational (no hooks, no side effects). Extract as-is, update imports in `AgentStreamView.tsx`, run `cd web && npx vitest run` to verify no breakage.

---

## File Change Summary

| File | Change | Phase |
|------|--------|-------|
| `web/src/router.tsx` | Add `view_mode` to search types/validation (P1); add `"session"` to tab values (P3); redirect `/canvas` (P1) | P1+P3 |
| `web/src/pages/JobsPage.tsx` | **New** — unified shell with list/grid toggle | P1 |
| `web/src/pages/JobDashboard.tsx` | No changes (child of JobsPage) | — |
| `web/src/pages/CanvasPage.tsx` | No changes (child of JobsPage) | — |
| `web/src/components/layout/AppLayout.tsx` | Remove Canvas nav link (`:334-336`), `isCanvasActive` (`:193`), title branch (`:267-268`), `Network` import | P1 |
| `web/src/components/canvas/JobCard.tsx` | Add `search` prop to `<Link>` for `view_mode` preservation (`:62-74`) | P1 |
| `web/src/pages/JobDetailPage.tsx` | Add `RightPanelTab = "session"` (`:77`), session tab trigger/content (after `:721`), `useJobSessions` query | P3 |
| `src/stepwise/server.py` | Add `GET /api/jobs/{id}/sessions` and `GET .../transcript` (after line 1993) | P2 |
| `web/src/lib/api.ts` | Add `fetchJobSessions()`, `fetchSessionTranscript()` (after `:237`) | P3 |
| `web/src/lib/types.ts` | Add `SessionInfo`, `SessionBoundary`, `SessionTranscript` interfaces | P3 |
| `web/src/hooks/useStepwise.ts` | Add `useJobSessions()`, `useSessionTranscript()` (after `:117`) | P3 |
| `web/src/hooks/useStepwiseWebSocket.ts` | Add session query invalidation in tick handler (after `:103`) | P3 |
| `web/src/hooks/useSessionStream.ts` | **New** — multi-run backfill+live streaming hook | P3 |
| `web/src/components/jobs/StreamSegments.tsx` | **New** — extracted `SegmentRow`, `ToolCard` from AgentStreamView | P3 |
| `web/src/components/jobs/AgentStreamView.tsx` | Import `SegmentRow`, `ToolCard` from `StreamSegments` (no behavior change) | P3 |
| `web/src/components/jobs/SessionTranscriptView.tsx` | **New** — session transcript with boundaries | P3 |
| `web/src/components/jobs/SessionTab.tsx` | **New** — session picker + transcript container | P3 |
| `web/src/components/dag/StepNode.tsx` | Add session indicator icon (after executor label area) | P4 |
| `tests/test_session_api.py` | **New** — 8 backend endpoint tests | P2 |
| `web/src/hooks/useStepwise.test.ts` | Extend with `useJobSessions`/`useSessionTranscript` tests | P3 |
| `web/src/hooks/useSessionStream.test.ts` | **New** — 3 stream hook tests | P3 |
| `web/src/components/jobs/SessionTab.test.tsx` | **New** — 3 component tests | P3 |
| `web/src/pages/JobsPage.test.tsx` | **New** — 4 view toggle tests | P1 |
