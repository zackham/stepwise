# P0-3: Improve Job Detail Sidebar for Inspection

## Overview

The StepDetailPanel (right sidebar on JobDetailPage) is currently a fixed 320px (`w-80`) panel that makes it difficult to inspect agent prompts, structured outputs, and long-form content. This plan adds: (1) interpolated prompt display per run, (2) collapsible structured JSON output views, (3) an expand-to-overlay mode for full inspection, and (4) raw/rendered toggle for agent output.

The approach is frontend-first with one small backend change: persisting the interpolated executor config on each step run so the frontend can display the actual prompt sent to the executor (not just the template with `$variable` placeholders).

---

## Requirements & Acceptance Criteria

### R1: Show interpolated prompt per run
- **AC1.1**: Each run accordion entry shows the actual prompt sent to the executor, with all `$variable` references resolved to their runtime values.
- **AC1.2**: The template prompt (from step definition) remains visible in the Definition section at the top.
- **AC1.3**: The interpolated prompt is labeled "Resolved Prompt" and visually distinct from the template (different border color or background tint).
- **AC1.4**: If the interpolated prompt is identical to the template (no variables), it is not shown redundantly.

### R2: Structured outputs in collapsible JSON view
- **AC2.1**: The artifact section of HandoffEnvelopeView defaults to expanded for the latest run and collapsed for older runs.
- **AC2.2**: Each output field key is individually expandable/collapsible when the value is an object or array.
- **AC2.3**: Large string values (>12 lines) in artifacts use the existing BlockString component with show-more/show-less.
- **AC2.4**: A "Copy JSON" button is available at the artifact level.

### R3: Expandable panel for full inspection
- **AC3.1**: An "expand" button in the StepDetailPanel header opens a full-width overlay (slide-over from right, ~70vw on desktop).
- **AC3.2**: The expanded view shows the same content as the sidebar but with more horizontal space — prompts, outputs, and agent stream all benefit from wider rendering.
- **AC3.3**: Pressing Escape or clicking the backdrop closes the overlay and returns to the sidebar view.
- **AC3.4**: On mobile, the expanded view takes the full screen (already handled by the Sheet component at 85vw).

### R4: Raw and rendered views of agent output
- **AC4.1**: For completed agent runs, a toggle switch offers "Stream" (default) and "Raw" views.
- **AC4.2**: "Stream" view is the existing AgentStreamView (tool cards, formatted text segments).
- **AC4.3**: "Raw" view shows the NDJSON events as a scrollable monospace text block, one JSON object per line.
- **AC4.4**: The raw view has a "Copy All" button to copy the entire NDJSON content.

---

## Assumptions (verified against codebase)

| # | Assumption | Verified |
|---|-----------|----------|
| A1 | The interpolated config is NOT currently persisted — `_interpolate_config()` is called at launch time in engine.py:1609 but the result is only passed to the executor, not saved to the run. | Yes — `engine.py:1609-1614`. The `run.inputs` dict IS saved, so frontend-side interpolation is possible but fragile. Backend persistence is the cleaner path. |
| A2 | `StepRun.inputs` contains the fully resolved input values (after dot-path navigation). | Yes — `engine.py:_resolve_inputs()` stores resolved values in `run.inputs` before persisting. |
| A3 | `StepRun.executor_state` is an opaque `dict | None` that's persisted to the DB and available via the API. It's used for session tracking but has spare capacity. | Yes — `models.py:StepRun.executor_state`, stored in `step_runs.executor_state` JSON column. |
| A4 | The right panel is fixed at `w-80` (320px) on desktop, rendered as a `Sheet` on mobile. No resizable panel library is installed. | Yes — `JobDetailPage.tsx:592`. |
| A5 | shadcn Sheet component exists and supports `side="right"` with custom width classes. | Yes — used for mobile panel at `JobDetailPage.tsx:575-587`. |
| A6 | `JsonView` component already handles collapse/expand, copy, block strings, and nested JSON-in-strings. | Yes — `web/src/components/JsonView.tsx`. |
| A7 | `useAgentOutput(runId)` hook returns `{ events: AgentStreamEvent[] }` for replay. | Yes — `useStepwise.ts`. This provides the raw NDJSON data needed for the raw view. |
| A8 | The `HandoffEnvelopeView` uses a `Section` component with `Collapsible` — artifact is always `defaultOpen={true}`. | Yes — `HandoffEnvelopeView.tsx:63`. |

---

## Implementation Steps

### Step 1: Backend — Persist interpolated config on step run

**File**: `src/stepwise/engine.py`

In `AsyncEngine._launch_step()` (around line 1609-1614), after computing the interpolated config, save it to `run.executor_state` under a dedicated key so it's available via the API:

```python
# After line 1614 (the interpolation block)
if interpolated != exec_ref.config:
    # Persist interpolated config for frontend inspection
    state = run.executor_state or {}
    state["_interpolated_config"] = interpolated
    run.executor_state = state
    self.store.update_run(run)
```

Do the same in `Engine._launch_step()` (the legacy tick-based engine, around the equivalent interpolation block) for test compatibility.

**Why `executor_state` instead of a new field**: Adding a new column to `step_runs` requires a migration. `executor_state` is already a JSON blob designed for opaque executor data, and the `_` prefix convention signals it's engine-internal. The frontend reads `executor_state` from the `StepRun` type already.

**Scope**: Only write the key when interpolation actually changed something (the `if interpolated != exec_ref.config` guard prevents noise on steps with no variables).

### Step 2: Frontend — Add interpolated prompt display to run accordion

**File**: `web/src/components/jobs/StepDetailPanel.tsx`

Inside each run's `AccordionContent` (after the Inputs section, around line 429), add a "Resolved Prompt" block:

```tsx
{/* Resolved Prompt (interpolated) */}
{run.executor_state?._interpolated_config?.prompt &&
  run.executor_state._interpolated_config.prompt !== stepDef.executor.config.prompt && (
    <div>
      <div className="text-xs text-zinc-500 mb-1">Resolved Prompt</div>
      <pre className="text-xs font-mono bg-zinc-900 border border-emerald-500/20 rounded p-2 text-emerald-300 whitespace-pre-wrap break-words">
        {String(run.executor_state._interpolated_config.prompt).trim()}
      </pre>
    </div>
  )}
```

Similarly show resolved `command` for script executors and resolved `check_command` for poll executors using the same pattern.

**File**: `web/src/lib/types.ts`

No changes needed — `executor_state` is already typed as `Record<string, unknown> | null` on `StepRun`.

### Step 3: Frontend — Improve HandoffEnvelopeView with per-run expand defaults

**File**: `web/src/components/jobs/HandoffEnvelopeView.tsx`

Add an `isLatest` prop to control default expand state:

```tsx
interface HandoffEnvelopeViewProps {
  envelope: HandoffEnvelope;
  isLatest?: boolean;  // new prop
}
```

Pass `defaultOpen={isLatest !== false}` to the Artifact section. Older runs get collapsed artifacts by default, reducing visual noise.

**File**: `web/src/components/jobs/StepDetailPanel.tsx`

When rendering `HandoffEnvelopeView` in the run accordion (line 450), pass `isLatest={run.id === sortedRuns[0]?.id}`.

### Step 4: Frontend — Add expanded overlay mode

**Files**: `web/src/components/jobs/StepDetailPanel.tsx`, `web/src/pages/JobDetailPage.tsx`

**4a. Add expand button to StepDetailPanel header:**

Add a `Maximize2` icon button next to the close button in the header (line 133-138). When clicked, it calls a new `onExpand` callback prop.

```tsx
interface StepDetailPanelProps {
  jobId: string;
  stepDef: StepDefinition;
  onClose: () => void;
  onExpand?: () => void;    // new
  expanded?: boolean;       // new — controls whether we're in overlay mode
}
```

When `expanded` is true, the header shows a `Minimize2` icon instead, calling `onExpand` to toggle back.

**4b. Create the expanded overlay in JobDetailPage:**

Add state: `const [expandedStep, setExpandedStep] = useState(false);`

When `expandedStep` is true and a step is selected, render a `Sheet` from the right side with width `w-[70vw] max-w-4xl` containing the `StepDetailPanel` with `expanded={true}`. This reuses the existing shadcn Sheet component (already available and used for mobile).

```tsx
{/* Expanded step overlay */}
<Sheet open={expandedStep && !!resolvedStep} onOpenChange={(open) => !open && setExpandedStep(false)}>
  <SheetContent side="right" showCloseButton={false} className="w-[70vw] max-w-4xl p-0 overflow-y-auto">
    {resolvedStep && (
      <StepDetailPanel
        jobId={resolvedStep.jobId}
        stepDef={resolvedStep.stepDef}
        onClose={() => { setExpandedStep(false); setSelection(null); }}
        onExpand={() => setExpandedStep(false)}
        expanded={true}
      />
    )}
  </SheetContent>
</Sheet>
```

The sidebar panel stays at `w-80` by default. The expand button opens the overlay. No drag-to-resize complexity needed — the two-mode approach (sidebar vs overlay) covers the use case with minimal implementation cost.

### Step 5: Frontend — Raw/rendered toggle for agent output

**File**: `web/src/components/jobs/StepDetailPanel.tsx`

For each agent run in the accordion, wrap the existing AgentStreamView with a toggle:

```tsx
{run.result && isAgent && (
  <div>
    <div className="flex items-center justify-between mb-1">
      <div className="text-xs text-zinc-500">Agent Output</div>
      <AgentViewToggle mode={agentViewMode} onChange={setAgentViewMode} />
    </div>
    {agentViewMode === "stream" ? (
      <AgentStreamView runId={run.id} isLive={false} />
    ) : (
      <AgentRawView runId={run.id} />
    )}
  </div>
)}
```

**New component** `AgentRawView` (inline in StepDetailPanel or small separate file):

```tsx
function AgentRawView({ runId }: { runId: string }) {
  const { data } = useAgentOutput(runId);
  const text = (data?.events ?? []).map(e => JSON.stringify(e)).join("\n");
  // ... render as monospace pre block with Copy All button
}
```

**`AgentViewToggle`**: Two small tab-like buttons ("Stream" | "Raw") using Tailwind classes matching existing button patterns in the codebase. No new UI library needed.

State management: Add `const [agentViewMode, setAgentViewMode] = useState<"stream" | "raw">("stream");` at the component level. Since runs are inside an accordion, only one is visible at a time, so a single state variable suffices.

### Step 6: Polish — Prompt template max-height increase

**File**: `web/src/components/jobs/StepDetailPanel.tsx`

The current template prompt blocks have `max-h-32` (128px, ~8 lines). In expanded mode, remove this constraint:

```tsx
<pre className={cn(
  "text-xs font-mono bg-zinc-900 border border-blue-500/20 rounded p-2 text-blue-300 whitespace-pre-wrap break-all",
  !expanded && "max-h-32 overflow-auto"
)}>
```

This makes the expanded overlay truly useful for reading long prompts.

---

## Files Changed Summary

| File | Change |
|------|--------|
| `src/stepwise/engine.py` | Persist `_interpolated_config` in `executor_state` after interpolation (both `AsyncEngine._launch_step` and legacy `Engine._launch_step_internal`/`_launch_step`) |
| `web/src/components/jobs/StepDetailPanel.tsx` | Add resolved prompt display, expand/collapse toggle, agent raw/stream toggle, expanded-mode layout adjustments |
| `web/src/pages/JobDetailPage.tsx` | Add expanded overlay Sheet, pass `onExpand`/`expanded` props |
| `web/src/components/jobs/HandoffEnvelopeView.tsx` | Add `isLatest` prop to control default artifact expand state |

No new files required. No new dependencies.

---

## Testing Strategy

### Backend

```bash
# Run the full engine test suite to verify executor_state persistence doesn't break anything
uv run pytest tests/test_engine.py -v

# Run the async engine tests specifically
uv run pytest tests/test_async_engine.py -v

# Verify the interpolated config is saved correctly — add a targeted test:
uv run pytest tests/test_engine.py -k "interpolat" -v
```

**New test case** (in `tests/test_engine.py` or `tests/test_async_engine.py`):

```python
def test_interpolated_config_persisted(async_engine):
    """Verify that _interpolated_config is saved to executor_state when config has $variables."""
    register_step_fn("echo", lambda inputs: {"out": inputs["msg"]})
    wf = WorkflowDefinition(steps={
        "greet": StepDefinition(
            name="greet",
            executor=ExecutorRef(type="callable", config={"fn_name": "echo", "note": "Hello $name"}),
            inputs=[InputBinding("name", "$job", "name"), InputBinding("msg", "$job", "name")],
            outputs=["out"],
        ),
    })
    job = async_engine.create_job(objective="test", workflow=wf, inputs={"name": "World"})
    result = run_job_sync(async_engine, job.id)
    runs = async_engine.store.runs_for_job(job.id)
    assert runs[0].executor_state.get("_interpolated_config", {}).get("note") == "Hello World"
```

### Frontend

```bash
# Run all frontend tests
cd web && npm run test

# Run with filter for step detail panel tests
cd web && npx vitest run --reporter=verbose StepDetail

# Lint check
cd web && npm run lint
```

**Manual testing checklist** (against a running stepwise server with an agent flow):

1. Run a flow with `$variable` references in an agent prompt → verify "Resolved Prompt" appears in the run accordion with substituted values.
2. Run a flow where the prompt has no variables → verify "Resolved Prompt" section is NOT shown (no redundancy).
3. Click the expand button → verify the Sheet overlay opens at ~70vw.
4. Press Escape → verify overlay closes cleanly.
5. In expanded mode, verify long prompts are fully visible (no max-h-32 truncation).
6. Toggle between "Stream" and "Raw" views for an agent run → verify both render correctly.
7. Copy the raw NDJSON → verify valid JSON per line in clipboard.
8. Inspect artifact output on latest run → verify it defaults to expanded.
9. Inspect artifact on an older run → verify it defaults to collapsed.
10. Mobile: verify the expand button either opens full-screen or is hidden (Sheet already covers mobile).

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| `executor_state` bloat from large interpolated configs | Only store when interpolation actually changed values. The config dict is typically small (prompt + model + a few settings). |
| Existing tests that assert on `executor_state` exact contents | Use `_` prefix convention. No existing tests check for absence of extra keys in `executor_state`. |
| Sheet overlay z-index conflicts with other dialogs (FulfillWatchDialog) | shadcn Sheet and Dialog both use portal rendering with proper z-index stacking. No conflict expected. |

---

## Out of Scope

- Drag-to-resize panel width — the two-mode approach (sidebar + overlay) covers the inspection use case without the complexity of a resizable panel implementation.
- Storing full agent system prompts (the context chain compiled by `context.py`) — only the user-facing executor config prompt is persisted.
- Diff view between template and interpolated prompt — can be added later but not needed for initial release.
