# Plan: Full Agent Session Viewer

## Overview

Add a "Session" tab to the job detail right panel that shows the full agent conversation across all steps sharing a session. When steps use `continue_session: true` or pass `_session_id` between them, their individual agent output streams are currently siloed per-step in the Run tab. This feature stitches those streams together into a unified transcript with step boundary markers, supporting both historical replay and live streaming.

**Architecture approach:** The session data already exists — `executor_state.session_name` on each StepRun identifies which session a run belongs to. The implementation groups runs by session name, concatenates their agent output chronologically, and renders them in a new tab. No database schema changes required.

---

## Requirements

### R1: Session discovery for a job
- Given a job ID, compute all unique agent sessions by scanning `executor_state.session_name` across all runs
- **Acceptance:** API returns `[{session_name, step_names: [...], run_ids: [...], is_active: bool, started_at, latest_at}]` for a job

### R2: Session transcript assembly
- Given a session name + job ID, return the concatenated agent output events from all runs in that session, ordered by run start time, with step boundary markers injected between runs
- **Acceptance:** API returns `{segments: [...], boundaries: [{after_event_index, step_name, attempt, run_id}]}` — events are a flat array of `AgentStreamEvent`, boundaries mark where one step's output ends and the next begins

### R3: Live streaming for active sessions
- When a session has a running step, new agent output events stream via WebSocket in real time
- **Acceptance:** WebSocket `agent_output` messages for runs in the active session are consumed by the session viewer; the viewer uses the same backfill+live pattern as `AgentStreamView`

### R4: Session tab in job detail
- New "Session" tab appears in the right panel when the job has at least one agent session
- If only one session exists, show it directly. If multiple, show a session picker then the transcript
- **Acceptance:** Tab visible, session list rendered, transcript scrollable with step boundary markers

### R5: Step boundary markers in transcript
- Visual dividers between step outputs showing step name, attempt number, and timestamp
- Clicking a marker navigates to that step's Run tab
- **Acceptance:** Markers render between step segments, click navigates via URL search params

### R6: Session indicator on DAG nodes
- Steps sharing a session show a subtle visual indicator (e.g., matching colored dot or chain icon) on their DAG node
- **Acceptance:** Agent steps with `continue_session: true` or `_session_id` input show a session indicator; hovering shows session name

---

## Assumptions (verified against code)

1. **Session name is stored in `executor_state.session_name`** on every agent StepRun. Verified in `agent.py` lines 1304-1315: the state dict includes `session_name` and `session_id`. This is the stable identifier (not the UUID).

2. **`_session_id` propagates via artifact, not executor_state.** Verified in `agent.py` lines 1357-1362: `envelope.artifact["_session_id"] = process.session_name`. Downstream steps receive it as an input binding.

3. **Agent output files are per-run**, stored at `executor_state.output_path`. Verified in `server.py` line 1842. Each run has its own NDJSON file. To build a session transcript, we read multiple files in chronological order.

4. **The existing `_parse_ndjson_events()` function** (server.py lines 273-314) converts raw Claude API events to the condensed `AgentStreamEvent` format. We reuse this for session transcript assembly.

5. **WebSocket agent output messages include `run_id`** but not `session_name`. Verified in `useStepwiseWebSocket.ts`. The frontend must map run_id → session_name to route live events to the session viewer.

6. **The right panel tab system** uses URL search params (`?tab=run|step`). Verified in `router.tsx` lines 50-66 and `JobDetailPage.tsx` lines 706-762. Adding a new tab requires updating `JOB_DETAIL_TAB_VALUES`, `JobDetailSearch` type, `RightPanelTab` type, and adding `TabsTrigger`/`TabsContent`.

7. **`runs_for_job()` returns all runs for a job** including executor_state. Verified in `store.py` line 598-603. The existing `/api/jobs/{job_id}/runs` endpoint already returns this data. Session grouping can happen client-side or server-side.

8. **No database migration needed.** Session names are already in `executor_state` JSON. We just need query logic to group by them.

---

## Implementation Steps

### Step 1: Backend — Session listing endpoint

**File:** `src/stepwise/server.py`

Add `GET /api/jobs/{job_id}/sessions` endpoint that:
1. Calls `engine.store.runs_for_job(job_id)` to get all runs
2. Groups runs by `executor_state.session_name` (skip runs without one)
3. For each session group, computes:
   - `session_name`: the stable session identifier
   - `run_ids`: ordered list of run IDs (by `started_at`)
   - `step_names`: ordered list of step names
   - `is_active`: whether any run in the group has `status == "running"`
   - `started_at`: earliest run's `started_at`
   - `latest_at`: latest run's `completed_at` or `started_at`
4. Returns the list sorted by `started_at`

```python
@app.get("/api/jobs/{job_id}/sessions")
def get_job_sessions(job_id: str):
    engine = _get_engine()
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
        # track timestamps...
    return {"sessions": sorted(sessions.values(), key=lambda s: s["started_at"] or "")}
```

**Why server-side:** Keeps session grouping logic centralized, avoids sending all executor_state to the frontend just for grouping.

### Step 2: Backend — Session transcript endpoint

**File:** `src/stepwise/server.py`

Add `GET /api/jobs/{job_id}/sessions/{session_name}/transcript` endpoint that:
1. Gets all runs for the job with matching `executor_state.session_name`
2. Sorts runs by `started_at`
3. For each run, reads `executor_state.output_path` and parses via `_parse_ndjson_events()`
4. Returns a flat event array with boundary markers:

```python
@app.get("/api/jobs/{job_id}/sessions/{session_name}/transcript")
def get_session_transcript(job_id: str, session_name: str):
    engine = _get_engine()
    runs = engine.store.runs_for_job(job_id)
    session_runs = [
        r for r in runs
        if (r.executor_state or {}).get("session_name") == session_name
    ]
    session_runs.sort(key=lambda r: r.started_at or datetime.min)

    all_events: list[dict] = []
    boundaries: list[dict] = []
    for run in session_runs:
        boundary_index = len(all_events)
        boundaries.append({
            "event_index": boundary_index,
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

**Design note:** Boundaries are indices into the flat events array, not inline markers. This keeps the event stream pure (reusable by `buildSegmentsFromEvents()`) and lets the frontend inject visual dividers at render time.

### Step 3: Frontend — API functions

**File:** `web/src/lib/api.ts`

Add two fetch functions:

```typescript
export interface SessionInfo {
  session_name: string;
  run_ids: string[];
  step_names: string[];
  is_active: boolean;
  started_at: string | null;
  latest_at: string | null;
}

export function fetchJobSessions(jobId: string): Promise<{ sessions: SessionInfo[] }> {
  return request<{ sessions: SessionInfo[] }>(`/jobs/${jobId}/sessions`);
}

export interface SessionTranscript {
  events: AgentStreamEvent[];
  boundaries: {
    event_index: number;
    step_name: string;
    attempt: number;
    run_id: string;
    started_at: string | null;
    status: string;
  }[];
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

**File:** `web/src/lib/types.ts`

Add `SessionInfo` and `SessionBoundary` interfaces (or export from api.ts — follow existing pattern where types used only by api.ts stay there, shared types go in types.ts).

### Step 4: Frontend — React Query hooks

**File:** `web/src/hooks/useStepwise.ts`

Add two hooks:

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
  options?: { staleTime?: number }
) {
  return useQuery({
    queryKey: ["sessionTranscript", jobId, sessionName],
    queryFn: () => api.fetchSessionTranscript(jobId!, sessionName!),
    enabled: !!jobId && !!sessionName,
    staleTime: options?.staleTime ?? Infinity,
  });
}
```

Invalidation: add `["sessions", jobId]` and `["sessionTranscript"]` to the WebSocket tick handler in `useStepwiseWebSocket.ts` so session data refreshes when runs change.

### Step 5: Frontend — Session transcript streaming hook

**File:** `web/src/hooks/useSessionStream.ts` (new file)

This hook extends the backfill+live pattern from `useAgentStream.ts` to work across multiple run IDs:

```typescript
export function useSessionStream(
  jobId: string | undefined,
  sessionName: string | undefined,
  runIds: string[],  // all run IDs in the session
  isLive: boolean
): SessionStreamState {
  // 1. Fetch backfill via useSessionTranscript (REST)
  // 2. Subscribe to WebSocket agent_output, filtering by run_id ∈ runIds
  // 3. Queue live events until backfill arrives
  // 4. Build segments with boundary injection
  // 5. Return { segments, boundaries, usage }
}
```

Key difference from `useAgentStream`: the hook must track which run_id each live event belongs to, and maintain the boundary positions as new events arrive for the active (last) run.

The boundary-aware segment builder:
- Takes `events[]` and `boundaries[]` from the transcript endpoint
- Builds `StreamSegment[]` via the existing `buildSegmentsFromEvents()` logic
- Injects `{ type: "boundary", ...boundaryInfo }` segments at the correct positions
- For live events on the active run, appends after the last boundary

### Step 6: Frontend — SessionTranscriptView component

**File:** `web/src/components/jobs/SessionTranscriptView.tsx` (new file)

Renders the full session conversation:

```typescript
interface SessionTranscriptViewProps {
  jobId: string;
  sessionName: string;
  runIds: string[];
  isLive: boolean;
  onNavigateToStep: (stepName: string) => void;
}
```

Structure:
- Uses `useSessionStream()` for data
- Renders segments via `SegmentRenderer` (reused from `AgentStreamView`)
- At boundary positions, renders a `StepBoundaryMarker` component:
  - Horizontal rule with step name pill, attempt badge, timestamp
  - Click handler calls `onNavigateToStep(stepName)` which updates URL to `?step=X&tab=run`
- Same auto-scroll behavior as `AgentStreamView` (track user scroll, auto-scroll when at bottom)
- Same virtualization for long sessions (>200 segments)
- Search integration via `useLogSearch` (same pattern as `AgentStreamView`)

The `StepBoundaryMarker` component:
```tsx
function StepBoundaryMarker({ boundary, onClick }: { boundary: SessionBoundary; onClick: () => void }) {
  return (
    <div className="flex items-center gap-2 py-2 px-3 my-1">
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

### Step 7: Frontend — SessionTab component

**File:** `web/src/components/jobs/SessionTab.tsx` (new file)

The container component for the "Session" tab:

```typescript
interface SessionTabProps {
  jobId: string;
  onNavigateToStep: (stepName: string) => void;
}
```

Logic:
1. Calls `useJobSessions(jobId)` to get session list
2. If no sessions → render empty state ("No agent sessions for this job")
3. If one session → render `SessionTranscriptView` directly
4. If multiple sessions → render a session picker (dropdown or small list) + transcript for selected session
5. Track selected session in component state (default: first active session, or first session)

Session picker item shows: session name (formatted: strip `step-` prefix, humanize), step count, active badge if live.

### Step 8: Frontend — Wire into JobDetailPage

**File:** `web/src/router.tsx`

Update the tab type and validation:
```typescript
const JOB_DETAIL_TAB_VALUES = new Set(["run", "step", "session"]);

export type JobDetailSearch = JobsRouteSearch & {
  tab?: "run" | "step" | "session";
  // ...
};
```

**File:** `web/src/pages/JobDetailPage.tsx`

1. Update `RightPanelTab` type: `type RightPanelTab = "run" | "step" | "session";`

2. Add session data query at the top of the component:
```typescript
const { data: sessionData } = useJobSessions(jobId);
const hasSessions = (sessionData?.sessions?.length ?? 0) > 0;
```

3. Add the tab trigger (conditionally rendered when sessions exist):
```tsx
{hasSessions && (
  <TabsTrigger value="session" className="text-xs gap-1 px-2.5">
    Session
  </TabsTrigger>
)}
```

4. Add the tab content:
```tsx
<TabsContent
  value="session"
  className={cn(
    "flex-1 min-h-0 overflow-y-auto",
    activeTab !== "session" && "hidden"
  )}
>
  <SessionTab
    jobId={jobId}
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

**Design decision:** The Session tab is job-level, not step-level. It appears alongside Run/Step when any step is selected, OR when no step is selected but the panel is open. This makes sense because a session spans multiple steps — viewing it while a specific step is selected still works (the transcript shows the full session, with the current step's boundary highlighted).

### Step 9: Frontend — Highlight current step's boundary

When the Session tab is active and a step is selected (via `searchParams.step`), pass `highlightStep={selectedStep}` to `SessionTranscriptView`. The component auto-scrolls to that step's first boundary marker and applies a highlight ring/background to it.

```tsx
// In SessionTranscriptView
useEffect(() => {
  if (highlightStep && boundaryRefs.current[highlightStep]) {
    boundaryRefs.current[highlightStep].scrollIntoView({ behavior: "smooth", block: "center" });
  }
}, [highlightStep]);
```

### Step 10: WebSocket invalidation for session queries

**File:** `web/src/hooks/useStepwiseWebSocket.ts`

In the tick message handler, add session query invalidation:

```typescript
if (msg.type === "tick" && msg.changed_jobs) {
  for (const jobId of msg.changed_jobs) {
    // existing invalidations...
    queryClient.invalidateQueries({ queryKey: ["sessions", jobId] });
    queryClient.invalidateQueries({ queryKey: ["sessionTranscript", jobId] });
  }
}
```

This ensures the session list and transcript refresh when runs start/complete.

### Step 11 (optional, R6): DAG session indicator

**File:** `web/src/components/dag/StepNode.tsx`

Add a subtle session icon to agent steps that have `continue_session: true` in their step definition or `_session_id` in their inputs:

```tsx
const hasSession = stepDef.executor?.config?.continue_session === true
  || stepDef.inputs?.some(i => i.field === "_session_id");

// In the node render:
{hasSession && (
  <span className="text-violet-400/60 text-[9px]" title={`Session: ${sessionName}`}>
    <Link2 className="w-2.5 h-2.5 inline" />
  </span>
)}
```

The session name for the tooltip comes from the run's `executor_state.session_name` (available in the `runs` prop passed to DAG).

---

## Data Flow Summary

```
                        Backend                                          Frontend
                        ───────                                          ────────

  GET /api/jobs/{id}/sessions                    useJobSessions(jobId)
  ┌──────────────────────────┐                   ┌──────────────────────┐
  │ runs_for_job(job_id)     │  ─── JSON ──────► │ SessionTab           │
  │ group by session_name    │                   │   session picker     │
  │ return [{session_name,   │                   │   ↓ select           │
  │   run_ids, is_active}]   │                   └──────────────────────┘
  └──────────────────────────┘                              │
                                                            ▼
  GET /api/jobs/{id}/sessions/{name}/transcript   useSessionStream(jobId, name, runIds, isLive)
  ┌──────────────────────────┐                   ┌──────────────────────────────────────────────┐
  │ filter runs by session   │  ─── JSON ──────► │ REST backfill (events + boundaries)          │
  │ read each output_path    │                   │ + WebSocket live events (filter by run_ids)  │
  │ parse NDJSON → events    │                   │ → build segments with boundary markers       │
  │ compute boundaries       │                   │ → SessionTranscriptView renders              │
  └──────────────────────────┘                   └──────────────────────────────────────────────┘

  WebSocket /ws
  ┌──────────────────────────┐                   ┌──────────────────────┐
  │ _tail_agent_output()     │  ── agent_output─►│ subscribeAgentOutput │
  │ (existing, unchanged)    │    {run_id, ...}  │ filter: run_id ∈ set │
  └──────────────────────────┘                   └──────────────────────┘
```

---

## File Change Summary

| File | Change | Type |
|------|--------|------|
| `src/stepwise/server.py` | Add `GET /api/jobs/{id}/sessions` and `GET /api/jobs/{id}/sessions/{name}/transcript` endpoints | Modify |
| `web/src/lib/api.ts` | Add `fetchJobSessions()` and `fetchSessionTranscript()` functions + types | Modify |
| `web/src/lib/types.ts` | Add `SessionInfo`, `SessionBoundary` interfaces | Modify |
| `web/src/hooks/useStepwise.ts` | Add `useJobSessions()` and `useSessionTranscript()` hooks | Modify |
| `web/src/hooks/useStepwiseWebSocket.ts` | Add session query invalidation on tick | Modify |
| `web/src/hooks/useSessionStream.ts` | New hook: backfill+live streaming for multi-run session transcript | Create |
| `web/src/components/jobs/SessionTranscriptView.tsx` | New component: renders session transcript with boundary markers | Create |
| `web/src/components/jobs/SessionTab.tsx` | New component: session picker + transcript container | Create |
| `web/src/router.tsx` | Add `"session"` to `JOB_DETAIL_TAB_VALUES` and `JobDetailSearch` type | Modify |
| `web/src/pages/JobDetailPage.tsx` | Add Session tab trigger/content, wire `useJobSessions` | Modify |
| `web/src/components/dag/StepNode.tsx` | Optional: session indicator icon | Modify |

---

## Testing Strategy

### Backend tests

**File:** `tests/test_session_endpoints.py` (new)

```bash
uv run pytest tests/test_session_endpoints.py -x -q
```

Test cases:
1. **No sessions** — job with only script steps returns `{"sessions": []}`
2. **Single session** — two agent runs with same `session_name` in executor_state → one session entry with both run_ids
3. **Multiple sessions** — three runs across two sessions → two session entries, correctly grouped
4. **Active session detection** — one running run in session → `is_active: true`
5. **Transcript ordering** — three runs in a session → events concatenated in started_at order with correct boundary indices
6. **Transcript with missing output file** — run has output_path but file deleted → graceful empty events for that segment, boundary still present
7. **Session name from executor_state** — verify the endpoint reads from `executor_state.session_name`, not from artifact `_session_id`

Test pattern: use `store.save_run()` to create StepRun objects with crafted `executor_state` containing `session_name` and `output_path`. Create temp NDJSON files for output. No engine needed — these are pure API tests.

### Frontend tests

**File:** `web/src/components/jobs/SessionTab.test.tsx` (new)

```bash
cd web && npm run test -- --run src/components/jobs/SessionTab.test.tsx
```

Test cases:
1. **Empty state** — no sessions → renders "No agent sessions" message
2. **Single session auto-select** — one session → directly renders transcript view (no picker)
3. **Multiple sessions** — renders session picker, selecting one shows transcript
4. **Boundary click navigation** — clicking step boundary calls `onNavigateToStep` with correct step name

**File:** `web/src/hooks/useSessionStream.test.ts` (new)

```bash
cd web && npm run test -- --run src/hooks/useSessionStream.test.ts
```

Test cases:
1. **Backfill only** — non-live session builds segments from REST data with boundaries injected
2. **Live event append** — live WebSocket event for matching run_id appends to segments after last boundary
3. **Non-matching run_id filtered** — live event for unrelated run_id is ignored

### Integration test

```bash
uv run pytest tests/ -x -q  # full suite — verify no regressions
cd web && npm run test        # full frontend suite
cd web && npm run lint        # no lint errors
```

### Manual verification

1. Run a flow with `continue_session: true` across 2+ steps
2. Open job detail page → verify Session tab appears
3. Click Session tab → verify full transcript with step boundary markers
4. While a session step is running → verify live streaming in session view
5. Click a boundary marker → verify navigation to that step's Run tab
6. Run a flow without agent sessions → verify Session tab does not appear
