# Plan: Live-Updating Script Source Display

## Overview

When viewing a step's script source in the StepDetailPanel while an agent job is running, if the agent modifies that script file, the displayed source stays stale. This plan adds live-updating file content display to the step detail panel.

**Two dimensions of the problem:**

1. **Referenced script file changes** (primary): A step uses `run: ./test.sh` and an agent modifies `test.sh`. Currently there's no mechanism to display the file's contents at all — the panel only shows the command string (`./test.sh`), not the script body. Live updating requires first *showing* file contents, then keeping them fresh.

2. **Flow YAML changes** (secondary): If an agent modifies the flow YAML itself, the step definition is stale because it was frozen into the Job at creation time. This is rare mid-run and lower priority.

**Approach:** Add a backend endpoint for reading workspace files with mtime, a frontend hook that polls while the job is active, and a `ScriptSourceView` component with visual change indication.

---

## Requirements

### R1: Display referenced script file contents
- When a script step's `command` references a file path (e.g., `./test.sh`, `python3 analyze.py`), display the file's contents below the command string.
- **Acceptance:** Script file contents appear in a collapsible section labeled "Script Source" with syntax-appropriate formatting (monospace, line numbers).

### R2: Auto-refresh file contents while job is running
- Poll file content at 4-second intervals when the job is active; stop when terminal.
- **Acceptance:** If a file changes on disk while viewing the step detail, content updates within 5 seconds without manual action.

### R3: Visual indication of source update
- Show a brief highlight animation when content changes.
- **Acceptance:** A subtle ring/border pulse plays on the source block when new content arrives, plus a transient "Updated" badge visible for ~2 seconds.

### R4: No polling when job is terminal
- Stop polling when job status is completed/failed/cancelled.
- **Acceptance:** Network tab shows no file-read requests for completed jobs.

---

## Assumptions (verified against source)

| # | Assumption | Verified at |
|---|---|---|
| 1 | Script commands stored in `executor.config.command` | `src/stepwise/yaml_loader.py:313` — `ExecutorRef("script", {"command": command})` |
| 2 | `source_dir` serialized by backend but not typed on frontend | `src/stepwise/models.py:1342` sends it; `web/src/lib/types.ts` `FlowDefinition` omits it |
| 3 | `readFlowFile` endpoint exists but requires flow-relative path | `src/stepwise/server.py:2356-2373`, `web/src/lib/api.ts:405-409` |
| 4 | StepDetailPanel reads command from `stepDef.executor.config.command` | `web/src/components/jobs/StepDetailPanel.tsx:319-326` |
| 5 | `workspace_path` available on Job | `web/src/lib/types.ts:234`, `src/stepwise/models.py:1640` |
| 6 | No file-watching infrastructure exists | Confirmed via codebase search — only completion sentinel in `runner.py` |
| 7 | WebSocket tick events fire on job state changes, not file changes | `web/src/hooks/useStepwiseWebSocket.ts`, `src/stepwise/server.py` WS handlers |

---

## Implementation Steps

### Step 1: Add `source_dir` to frontend `FlowDefinition` type

**File:** `web/src/lib/types.ts`

Add `source_dir?: string;` to the `FlowDefinition` interface. The backend already sends this field; the frontend just needs to type it.

---

### Step 2: Create backend endpoint to read workspace files

**File:** `src/stepwise/server.py`

Add `GET /api/jobs/{job_id}/file-content` endpoint:

```python
@app.get("/api/jobs/{job_id}/file-content")
def get_job_file_content(
    job_id: str,
    path: str = Query(...),
    base: str = Query(default="flow"),  # "flow" | "workspace"
):
```

Behavior:
- Resolves `path` relative to `job.workflow.source_dir` (for `base="flow"`) or `job.workspace_path` (for `base="workspace"`).
- **Security:** validates resolved path is within base directory (rejects `../` traversal).
- Returns `{ "path": str, "content": str, "mtime": float }`.
- Returns 404 for non-existent files.
- Truncates at `MAX_FILE_SIZE` (already defined in `server.py`).

---

### Step 3: Add frontend API function

**File:** `web/src/lib/api.ts`

```typescript
export function fetchJobFileContent(
  jobId: string,
  path: string,
  base: "flow" | "workspace" = "flow",
): Promise<{ path: string; content: string; mtime: number }> {
  const params = new URLSearchParams({ path, base });
  return request(`/jobs/${jobId}/file-content?${params}`);
}
```

---

### Step 4: Create `extractScriptPath` utility

**File:** `web/src/lib/script-utils.ts` (new)

```typescript
export function extractScriptPath(command: string): string | null
```

Logic:
- If command contains newlines → return `null` (inline script).
- Split by whitespace.
- If first token is an interpreter (`python3`, `python`, `bash`, `sh`, `node`, `ruby`, `perl`) → return second token.
- If first token looks like a file path (starts with `./`, `../`, or has a recognizable file extension like `.sh`, `.py`, `.js`) → return first token.
- Otherwise → return `null`.

This mirrors `ScriptExecutor._resolve_command()` from `src/stepwise/executors.py:259-302`.

---

### Step 5: Create `useScriptSource` hook

**File:** `web/src/hooks/useScriptSource.ts` (new)

```typescript
export function useScriptSource(
  jobId: string,
  scriptPath: string | null,
  jobStatus: string,
): {
  content: string | null;
  isLoading: boolean;
  hasUpdated: boolean;
}
```

Implementation:
- Uses React Query with `queryKey: ["job-file-content", jobId, scriptPath]`.
- Sets `refetchInterval: 4000` when job status is active (RUNNING/SUSPENDED), `false` when terminal.
- Tracks previous content hash via `useRef`. When content changes, sets `hasUpdated = true` for 2 seconds via `setTimeout`.
- `enabled: scriptPath != null`.

---

### Step 6: Create `ScriptSourceView` component

**File:** `web/src/components/jobs/ScriptSourceView.tsx` (new)

A component that:
- Receives `jobId`, `command`, and `jobStatus` props.
- Calls `extractScriptPath(command)` — renders nothing if null.
- Calls `useScriptSource(jobId, scriptPath, jobStatus)`.
- Renders file content in a `<pre>` block with:
  - Header: file icon + script path + optional "Updated" badge (shown when `hasUpdated`).
  - Collapsible via shadcn Collapsible, default expanded.
  - Syntax styling matching existing command display patterns.
  - Brief `ring-2 ring-blue-500/50 transition-all` pulse when content updates (Tailwind animation, no CSS files).

---

### Step 7: Integrate into StepDetailPanel

**File:** `web/src/components/jobs/StepDetailPanel.tsx`

Changes:
1. Add `jobId` and `jobStatus` to props (or pass through existing context).
2. For script steps, render `<ScriptSourceView>` below the existing Command section.
3. Only render when `stepDef.executor?.type === "script"` and `stepDef.executor?.config?.command` exists.

---

### Step 8: Pass `jobId` and `jobStatus` from parent pages

**Files:**
- `web/src/pages/JobDetailPage.tsx` — pass `jobId={job.id}` and `jobStatus={job.status}` to StepDetailPanel (rendered at ~lines 819 and 894).
- `web/src/pages/JobTimelinePage.tsx` — same pattern.

---

## Architecture Decisions

**Polling vs. WebSocket push:** Polling is the right choice. Adding inotify/watchdog for arbitrary workspace files would be disproportionate complexity. The 4-second interval is adequate since agent tool calls take seconds to minutes. React Query's `refetchInterval` follows existing codebase patterns.

**Job-scoped file endpoint:** Better than extending the existing flow-file endpoint because: (1) it naturally handles workspace context, (2) can be secured via job ownership, (3) `mtime` enables efficient change detection.

**File path extraction on frontend:** Parsing the command string client-side avoids a backend round-trip. The logic is simple and unit-testable.

---

## Testing Strategy

### Backend tests

```bash
uv run pytest tests/test_server.py -k "file_content" -v
```

Test cases:
- Reads a file relative to job's `source_dir`
- Reads a file relative to `workspace_path` with `base=workspace`
- Returns 404 for non-existent files
- Rejects path traversal (`../../etc/passwd`)
- Returns correct `mtime`
- Truncates files exceeding `MAX_FILE_SIZE`

### Frontend unit tests

```bash
cd web && npm run test -- --run src/lib/script-utils.test.ts
cd web && npm run test -- --run src/hooks/useScriptSource.test.ts
```

`extractScriptPath` test cases:
| Input | Expected |
|---|---|
| `./test.sh` | `./test.sh` |
| `python3 analyze.py` | `analyze.py` |
| `bash scripts/run.sh --flag` | `scripts/run.sh` |
| `curl -s "$url" \| jq '.'` | `null` |
| multiline command | `null` |
| `echo "hello"` | `null` |

`useScriptSource` test cases:
- Returns content when file exists
- Sets `hasUpdated` when mtime changes
- Stops polling when job status is terminal
- Returns null when scriptPath is null

### Manual verification

```bash
# Start server
uv run stepwise server start

# Create a flow with a script step referencing a file
# Open web UI, select the step
# In another terminal, modify the script file
# Verify source updates in the panel within ~5 seconds
# Verify "Updated" badge appears briefly
# Stop/complete the job — verify polling stops
```

---

## Files Summary

| File | Action | Purpose |
|---|---|---|
| `web/src/lib/types.ts` | Edit | Add `source_dir` to `FlowDefinition` |
| `src/stepwise/server.py` | Edit | Add `GET /api/jobs/{job_id}/file-content` endpoint |
| `web/src/lib/api.ts` | Edit | Add `fetchJobFileContent` function |
| `web/src/lib/script-utils.ts` | Create | `extractScriptPath` utility |
| `web/src/hooks/useScriptSource.ts` | Create | Polling hook for script file content |
| `web/src/components/jobs/ScriptSourceView.tsx` | Create | Live-updating script source display component |
| `web/src/components/jobs/StepDetailPanel.tsx` | Edit | Integrate ScriptSourceView |
| `web/src/pages/JobDetailPage.tsx` | Edit | Pass jobId/jobStatus props |
| `web/src/pages/JobTimelinePage.tsx` | Edit | Pass jobId/jobStatus props |
| `web/src/lib/script-utils.test.ts` | Create | Unit tests for path extraction |
| `web/src/hooks/useScriptSource.test.ts` | Create | Unit tests for polling hook |
