# Plan: Popout/Expand Mechanism for Long Content in Job Detail Sidebar

## Overview

Add a `ContentExpandDialog` component that lets users click an expand button on any data section in the job detail sidebar to open a wide modal with the content rendered at readable size. Requires propagating a `modalMode` flag through `JsonView`/`BlockString` to disable internal collapse thresholds, adding a `className` prop to `AgentStreamView` and `ScriptLogView` to remove hardcoded height constraints, and restructuring `HandoffEnvelopeView` section headers to avoid nested interactive elements. Expand buttons only appear in the narrow sidebar context (`expanded={false}`); they are hidden when the panel is already in the wide Sheet or MobileFullScreen view.

## Requirements

| # | Requirement | Acceptance Criteria |
|---|-------------|-------------------|
| R1 | Expand button on truncated sections | Every section in `StepDetailPanel` that currently truncates content (prompts, inputs, outputs, config, logs, agent output) shows an expand icon button in its header row **only when `expanded={false}`** (narrow sidebar) |
| R2 | Modal opens at ~80% viewport width | `ContentExpandDialog` renders a centered `Dialog` with class `sm:max-w-[80vw] max-h-[85vh]`, stacking above both the base sidebar and the Sheet overlay via `z-[60]` on both the overlay and popup |
| R3 | Supports raw text content | Pre-formatted text (prompts, commands, logs) renders in a monospace `<pre>` block with `whitespace-pre-wrap`, no height constraint, inside a `ScrollArea` |
| R4 | Supports JSON with full expansion | JSON data renders via `JsonView` with a new `modalMode` prop that forces all depths open and disables `BlockString`'s 12-line collapse. Copy button in modal copies `JSON.stringify(data, null, 2)` |
| R5 | Supports agent stream (live + replay + raw) | Agent output renders via `AgentStreamView` with a `className` override removing `max-h-96`. The modal mirrors the current view mode toggle (stream/raw) and works for both live and historical runs |
| R6 | Close with Escape or X button | Dialog closes on Escape (built into `@base-ui/react/dialog`) and via close button. Escape does not propagate to parent Sheet. |
| R7 | Copy-to-clipboard in modal | Copy button uses type-appropriate serialization: raw strings copy as-is, JSON copies via `JSON.stringify(data, null, 2)`, no copy button on stream views |
| R8 | Title reflects content source | Modal title shows the section name (e.g., "Resolved Prompt", "Artifact", "Agent Output") |
| R9 | No layout disruption | Opening/closing the modal does not affect sidebar scroll position or DAG state |
| R10 | Output expansion is mandatory | `HandoffEnvelopeView` sections (Artifact, Sidecar, Executor Meta) have expand buttons — this is not optional |
| R11 | Accessible expand buttons | Expand buttons have `aria-label`, are keyboard-focusable, and are visible on keyboard focus (`group-focus-within:opacity-100`) and coarse pointers (`@media (pointer: coarse)`) |

## Assumptions (verified against code)

| # | Assumption | Evidence |
|---|-----------|----------|
| A1 | `Dialog` supports custom width via `className` on `DialogContent` | `dialog.tsx:55-57` — `className` spreads into `cn()`, Tailwind merge will override `sm:max-w-sm` |
| A2 | `DialogContent` has built-in close button + Escape | `dialog.tsx:42-81` — `showCloseButton` defaults `true`; `@base-ui/react/dialog` handles Escape natively |
| A3 | `JsonView` depth-gates expansion at `depth < 1` and `BlockString` collapses at 12 lines | `JsonView.tsx:262,324` — `defaultExpanded={depth < 1}`. `BlockString` line 29: `COLLAPSE_THRESHOLD = 12`, line 34: `useState(!isLong)`. Neither respects any "force expand" flag — a new `modalMode` prop must be threaded through |
| A4 | `AgentStreamView` hardcodes `max-h-96` with no override mechanism | `AgentStreamView.tsx:220` — `className="max-h-96 overflow-y-auto p-3"`. No `className` prop on the component interface (lines 19-25). Must add one. |
| A5 | `ScriptLogView` hardcodes both 50-line truncation AND `max-h-96` CSS | Lines 101-102: `showAll` state controls 50-line truncation. Line 159: `<pre className="... max-h-96 overflow-auto ...">`. A modal variant needs to bypass both constraints. |
| A6 | `HandoffEnvelopeView` `Section` wraps the entire header row in `CollapsibleTrigger` (a `<button>`) | `HandoffEnvelopeView.tsx:38` — the trigger is the full row. Adding a nested `<button>` would be invalid HTML and would toggle collapse on expand click. Must restructure to separate trigger from action area. |
| A7 | Both Sheet and Dialog overlays use `z-50` | `dialog.tsx:34`, `sheet.tsx:29` — both hardcode `z-50`. The expand dialog needs `z-[60]` on *both* overlay and popup to stack above the Sheet. |
| A8 | When `expanded={true}`, `StepDetailPanel` is already inside a wide Sheet (`w-[70vw]`) or MobileFullScreen | `JobDetailPage.tsx:903-917` (Sheet, `expanded={true}`), lines 887-902 (MobileFullScreen, `expanded={true}`). Content is already unconstrained — section expand buttons are redundant here. |
| A9 | No existing test files for `StepDetailPanel` or `HandoffEnvelopeView` | Only `StepDetailSkeleton.test.tsx` (3 skeleton-specific tests) and `JsonView.test.tsx` (JSON string badge tests) exist. No integration tests for the panel. |
| A10 | Copy pattern is established but serialization varies by data type | `JsonView.tsx:178` copies JSON. `ScriptLogView` lines 120-121 copies raw `fullText`. `BlockString` lines 37-38 copies raw string. Consistent UX (icon + Check + timeout) but serialization is already type-specific. |

## Out of Scope

- **Resizable modal** — fixed 80vw is sufficient; no drag-to-resize.
- **Markdown rendering library** — prompts are preformatted text. No `react-markdown`.
- **DataFlowPanel expansion** — compact summary data, no truncation.
- **Keyboard shortcuts beyond Escape** — no Ctrl+E or other hotkeys.
- **Persisting expand state** — modal is ephemeral.
- **Changes to the existing whole-panel Maximize2 expand** — that feature remains as-is.
- **Input Bindings section** — these are short one-line-per-binding lists that don't truncate in practice. Not instrumented. (Removed from the target list vs. v1 of this plan to avoid scope creep on non-truncating content.)
- **Template Command block** (script executor `Command` at line 318-327) — this is the raw YAML command, typically 1-3 lines. Only the *resolved* command (which can be long after interpolation) gets an expand button.

## Architecture

### Component Changes

#### 1. New: `ContentExpandDialog` (`web/src/components/ContentExpandDialog.tsx`)

```tsx
interface ContentExpandDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: React.ReactNode;
  /** Raw string for copy-to-clipboard. Omit to hide copy button. */
  copyText?: string;
}
```

Wraps `Dialog`/`DialogContent` with:
- Width override: `sm:max-w-[80vw] max-h-[85vh]`
- **Z-index override**: passes `className="z-[60]"` to a custom `DialogOverlay`, and `z-[60]` to `DialogContent`. This requires extending `DialogContent` to accept an `overlayClassName` prop (one line in `dialog.tsx`) OR rendering the overlay/popup manually inside `ContentExpandDialog` using `DialogPrimitive` imports directly (avoids modifying the shared primitive).
- `ScrollArea` wrapping children with `max-h-[calc(85vh-4rem)]`
- Copy button in header (established pattern)

**Decision on z-index approach**: Render `DialogPrimitive.Portal` + `DialogPrimitive.Backdrop` + `DialogPrimitive.Popup` directly inside `ContentExpandDialog`, applying `z-[60]` to both backdrop and popup. This avoids modifying `dialog.tsx` (shared primitive). The expand dialog is the only consumer that needs elevated z-index.

Also exports:

```tsx
/** Small icon button for triggering expand. */
function ExpandButton({
  onClick,
  label,
}: {
  onClick: () => void;
  label: string;  // used for aria-label
}) { ... }
```

Styling: `opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 [@media(pointer:coarse)]:opacity-100 transition-opacity`. Uses `Maximize2` icon at `w-3 h-3`. Has `aria-label={label}` and is keyboard-focusable (`tabIndex={0}`).

#### 2. Modified: `JsonView` (`web/src/components/JsonView.tsx`)

Add `modalMode?: boolean` prop threaded through the component tree:

- **`JsonViewProps`**: add `modalMode?: boolean` (default `false`)
- **`BlockString`**: accept `modalMode` prop. When `true`, initialize `expanded` to `true` regardless of `isLong` (bypass `COLLAPSE_THRESHOLD`).
- **`JsonStringWrapper`**: forward `modalMode` to nested `JsonView`.
- **Recursive `JsonView` calls** (lines 259-264, 321-326): pass `modalMode` through. When `modalMode`, override `defaultExpanded` to `true` at all depths (not just `depth < 1`).

This is additive — existing callers are unaffected (default `false`).

#### 3. Modified: `AgentStreamView` (`web/src/components/jobs/AgentStreamView.tsx`)

Add optional `className` prop to `AgentStreamViewProps`:

```tsx
interface AgentStreamViewProps {
  runId: string;
  isLive: boolean;
  startedAt?: string | null;
  costUsd?: number | null;
  billingMode?: string;
  className?: string;  // NEW: override for the scroll container
}
```

Apply it to the scroll container div (line 220): `cn("max-h-96 overflow-y-auto p-3", className)`. The modal caller passes `className="max-h-none"` to remove the height constraint.

#### 4. Modified: `ScriptLogView` (inline in `StepDetailPanel.tsx`)

Add `variant?: "inline" | "modal"` prop:

- `"inline"` (default): current behavior — 50-line truncation + `max-h-96` on the `<pre>`
- `"modal"`: no truncation (`showAll` forced `true`), no `max-h-96` on the `<pre>` (replaced with `max-h-none`)

This is cleaner than separate `showAll` + `maxHeight` props because these constraints are conceptually coupled.

#### 5. Modified: `HandoffEnvelopeView` Section header (`HandoffEnvelopeView.tsx`)

Restructure the `Section` component header from:

```tsx
// BEFORE: entire row is a CollapsibleTrigger (button)
<CollapsibleTrigger className="flex items-center gap-1.5 ...">
  <ChevronRight ... />
  <span>{title}</span>
</CollapsibleTrigger>
```

To:

```tsx
// AFTER: split into trigger and action area
<div className="flex items-center gap-1.5 w-full py-1">
  <CollapsibleTrigger className="flex items-center gap-1.5 ... flex-1">
    <ChevronRight ... />
    <span>{title}</span>
  </CollapsibleTrigger>
  {!expanded && (
    <ExpandButton onClick={() => setExpandOpen(true)} label={`Expand ${title}`} />
  )}
</div>
```

The trigger still controls collapse; the expand button is a sibling, not a child. No nested buttons.

The `Section` component receives `expanded?: boolean` prop from the parent `HandoffEnvelopeView`, which receives it from `StepDetailPanel` (already has `expanded` in its props).

#### 6. Behavior: expand buttons hidden when `expanded={true}`

`StepDetailPanel` already receives `expanded?: boolean`. All expand buttons are conditionally rendered:

```tsx
{!expanded && <ExpandButton ... />}
```

When `expanded={true}` (Sheet or MobileFullScreen), the content is already wide — `max-h` constraints are already removed by existing `!expanded && "max-h-32"` guards. No expand buttons shown. No dialog-on-top-of-Sheet stacking issue.

This eliminates the z-index problem for the common case. The `z-[60]` on `ContentExpandDialog` is a safety net for any edge case where expand is triggered from the narrow sidebar while a Sheet is somehow present.

### Integration Pattern

Each expandable section follows:

```tsx
// Within a section that has `group` on its container:
const [expandOpen, setExpandOpen] = useState(false);

<div className="group flex items-center justify-between mb-1">
  <div className="text-xs text-zinc-500">Resolved Prompt</div>
  {!expanded && (
    <ExpandButton onClick={() => setExpandOpen(true)} label="Expand resolved prompt" />
  )}
</div>
<pre className={cn("...", !expanded && "max-h-48 overflow-auto")}>
  {resolvedPrompt}
</pre>
{expandOpen && (
  <ContentExpandDialog
    open={expandOpen}
    onOpenChange={setExpandOpen}
    title="Resolved Prompt"
    copyText={resolvedPrompt}
  >
    <pre className="text-sm font-mono whitespace-pre-wrap break-words p-4">
      {resolvedPrompt}
    </pre>
  </ContentExpandDialog>
)}
```

Note: `expandedContent` is always **explicit** — we never fall through to `children` because inline children carry their own truncation state and would re-render constrained inside the modal.

### Sections to Instrument

All in `StepDetailPanel.tsx` unless noted:

| Section | Lines | Content Type | Modal Content | Copy Behavior |
|---------|-------|-------------|---------------|---------------|
| External Prompt (template) | 330-335 | `pre` text | Full `<pre>`, no max-h | Raw string |
| Agent Prompt (template) | 340-344 | `pre` text | Full `<pre>`, no max-h | Raw string |
| Executor Config (other types) | 367-372 | JSON | `<JsonView modalMode />` | `JSON.stringify(data, null, 2)` |
| Run Inputs | 621-631 | JSON | `<JsonView modalMode />` | `JSON.stringify(data, null, 2)` |
| Resolved Prompt | 645-651 | `pre` text | Full `<pre>`, no max-h | Raw string |
| Resolved Command | 653-659 | `pre` text | Full `<pre>`, no max-h | Raw string |
| Resolved Check Command | 661-667 | `pre` text | Full `<pre>`, no max-h | Raw string |
| Agent Output (live, line 421) | 420-429 | stream | `<AgentStreamView className="max-h-none">` with same props | No copy |
| Agent Output (replay, line 703) | 673-708 | stream + toggle | Modal includes stream/raw toggle, both views unconstrained | No copy |
| Script Logs | 711-713 | log text | `<ScriptLogView variant="modal" />` | Raw `fullText` |
| Output (HandoffEnvelope) | 716-727 | JSON sections | See HandoffEnvelopeView below | Per-section |
| Watch State | 729-736 | JSON | `<JsonView modalMode />` | `JSON.stringify(data, null, 2)` |

In `HandoffEnvelopeView.tsx`:

| Section | Lines | Content Type | Modal Content | Copy Behavior |
|---------|-------|-------------|---------------|---------------|
| Artifact | 70 | JSON | `<JsonView data={artifact} modalMode />` | `typeof data === 'string' ? data : JSON.stringify(data, null, 2)` |
| Sidecar | 71 | JSON | `<JsonView data={sidecar} modalMode />` | Same |
| Executor Meta | 72 | JSON | `<JsonView data={meta} modalMode />` | Same |
| Workspace | 74 | string | `<pre>` | Raw string |

## Implementation Steps

### Step 1: Add `modalMode` prop to `JsonView` and `BlockString` (~20 min)

**File:** `web/src/components/JsonView.tsx`

1. Add `modalMode?: boolean` to `JsonViewProps` interface (line 155)
2. In `BlockString` (line 31): accept `modalMode` param. Change line 34 from `useState(!isLong)` to `useState(modalMode || !isLong)`
3. In `JsonStringWrapper` (line 86): accept and forward `modalMode` to the nested `JsonView` call
4. In the recursive `JsonView` calls at lines 259-264 and 321-326: when `modalMode`, pass `defaultExpanded={true}` regardless of depth. Forward `modalMode` to children.
5. Pass `modalMode` through the `BlockString` call at line 208: `<BlockString value={data} name={name} modalMode={modalMode} />`

### Step 2: Add `className` prop to `AgentStreamView` (~10 min)

**File:** `web/src/components/jobs/AgentStreamView.tsx`

1. Add `className?: string` to `AgentStreamViewProps` (line 25)
2. At line 220, change `className="max-h-96 overflow-y-auto p-3"` to `className={cn("max-h-96 overflow-y-auto p-3", className)}`
3. Import `cn` from `@/lib/utils` if not already imported

### Step 3: Add `variant` prop to `ScriptLogView` (~15 min)

**File:** `web/src/components/jobs/StepDetailPanel.tsx` (inline function at line 101)

1. Change signature from `({ run }: { run: StepRun })` to `({ run, variant = "inline" }: { run: StepRun; variant?: "inline" | "modal" })`
2. Line 102: change `useState(false)` to `useState(variant === "modal")`
3. Line 117: gate truncation on variant: `const truncated = variant !== "modal" && !showAll && lines.length > LOG_INITIAL_LINES`
4. Line 159: change hardcoded `max-h-96` to `cn("...", variant === "modal" ? "max-h-none" : "max-h-96 overflow-auto")`

### Step 4: Create `ContentExpandDialog` and `ExpandButton` (~30 min)

**File:** `web/src/components/ContentExpandDialog.tsx` (new)

1. Import `Dialog as DialogPrimitive` from `@base-ui/react/dialog`, `ScrollArea`, `Button`, `Maximize2`, `Copy`, `Check`, `X` from lucide-react, `cn` from utils
2. Create `ContentExpandDialog`:
   - Uses `DialogPrimitive.Root`, `DialogPrimitive.Portal`, `DialogPrimitive.Backdrop` (with `z-[60]`), `DialogPrimitive.Popup` (with `z-[60] sm:max-w-[80vw] max-h-[85vh]`)
   - Header with title (bold text) + copy button (if `copyText`) + close button
   - `ScrollArea` body wrapping `children`
3. Create `ExpandButton`:
   - `Maximize2` icon, `w-3 h-3`
   - Classes: `opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 [@media(pointer:coarse)]:opacity-100 transition-opacity text-zinc-500 hover:text-foreground`
   - `aria-label={label}`, focusable
4. Export both

### Step 5: Restructure `HandoffEnvelopeView` Section header (~20 min)

**File:** `web/src/components/jobs/HandoffEnvelopeView.tsx`

1. Add `expanded?: boolean` prop to `Section` and `HandoffEnvelopeView`
2. Split the `CollapsibleTrigger` row (line 38-46) into a wrapper `<div>` containing:
   - `CollapsibleTrigger` (flex-1, has chevron + title)
   - `ExpandButton` sibling (outside the trigger, guarded by `!expanded`)
3. Add `expandOpen` state to `Section`. When open, render `ContentExpandDialog` with `<JsonView data={data} modalMode defaultExpanded />` inside.
4. Compute `copyText` with type-aware serialization: `typeof data === 'string' ? data : JSON.stringify(data, null, 2)`
5. Thread `expanded` from `HandoffEnvelopeView` parent through to each `Section`

### Step 6: Instrument prompt and text sections in `StepDetailPanel` (~30 min)

**File:** `web/src/components/jobs/StepDetailPanel.tsx`

1. Import `ContentExpandDialog`, `ExpandButton` from `@/components/ContentExpandDialog`
2. Thread `expanded` prop to `HandoffEnvelopeView` call at line 722 (add `expanded={expanded}` prop)
3. For each text section (external prompt, agent prompt, resolved prompt/command/check-command):
   - Add `group` class to the section's container `<div>`
   - Replace the label `<div>` with a flex row containing label + `{!expanded && <ExpandButton ... />}`
   - Add `expandOpen` state + `ContentExpandDialog` with a clean `<pre>` (no max-h, same color scheme)
   - `copyText` = the raw string value
4. For the template script Command block (lines 318-327): **no expand button** — these are typically 1-3 lines

### Step 7: Instrument JSON and stream sections in `StepDetailPanel` (~30 min)

**File:** `web/src/components/jobs/StepDetailPanel.tsx`

1. **Executor Config** (line 367-372): Add expand button to "Config" label. Modal renders `<JsonView data={config} modalMode defaultExpanded />`. Copy = `JSON.stringify`.
2. **Run Inputs** (line 621-631): Add expand button to "Inputs" label. Modal renders `<JsonView data={run.inputs} modalMode defaultExpanded />`. Copy = `JSON.stringify`.
3. **Watch State** (line 729-736): Add expand button to "Watch" label. Same pattern.
4. **Live Agent Stream** (line 420-429): Add expand button next to the agent stream header area. Modal renders `<AgentStreamView ... className="max-h-none" />` with same `runId`, `isLive`, `startedAt`, `costUsd`, `billingMode` props. No copy button.
5. **Historical Agent Output** (line 673-708): Add expand button next to "Agent Output" label. Modal includes the same stream/raw mode toggle. Modal renders whichever mode is active, both at full height (`className="max-h-none"` for stream, `variant="modal"` equivalent for raw). Manage `agentViewMode` state so it's shared between inline and modal views.
6. **Script Logs** (line 711-713): Add expand button next to `ScriptLogView`'s header. Modal renders `<ScriptLogView run={run} variant="modal" />`. Copy = raw `fullText` (already available in ScriptLogView — extract to parent or pass as `copyText`).

### Step 8: Write tests (~40 min)

**File:** `web/src/components/ContentExpandDialog.test.tsx` (colocated, matching repo convention)

1. `ContentExpandDialog` renders dialog content when `open={true}`
2. `ContentExpandDialog` does not render when `open={false}`
3. Copy button calls `navigator.clipboard.writeText` with provided `copyText`
4. Copy button hidden when `copyText` is omitted
5. `ExpandButton` renders with correct `aria-label`
6. `ExpandButton` calls `onClick` on click

**File:** `web/src/components/JsonView.test.tsx` (extend existing)

7. `modalMode` forces `BlockString` to render expanded (no "Show more" button)
8. `modalMode` expands nested objects beyond depth 1

**File:** `web/src/components/jobs/StepDetailPanel.test.tsx` (new, colocated)

9. Expand buttons appear when `expanded={false}` on sections with content
10. Expand buttons are **hidden** when `expanded={true}`
11. Clicking expand button opens dialog with expected title
12. Agent output modal includes stream/raw toggle
13. Script log modal renders all lines (no truncation button)

**File:** `web/src/components/jobs/HandoffEnvelopeView.test.tsx` (new, colocated)

14. Expand button is a sibling of `CollapsibleTrigger`, not nested inside it (verify DOM structure)
15. Clicking expand button does NOT toggle collapsible state
16. Expand button hidden when `expanded={true}`

## Testing Strategy

### Automated Tests

```bash
cd web && npm run test
```

Covers: dialog open/close, copy serialization, button visibility by `expanded` prop, `modalMode` expansion in JsonView, DOM structure of HandoffEnvelopeView header, ScriptLogView variant behavior.

### Manual Testing

```bash
# Dev environment
cd web && npm run dev
# Backend
cd /home/zack/work/stepwise && uv run stepwise server start
```

**Narrow sidebar tests (expanded=false):**
1. Open a completed agent job → click step in DAG → sidebar opens
2. Verify expand icons on: Agent Prompt, Resolved Prompt, Inputs, Output (Artifact/Sidecar/Meta), Agent Output, Watch State
3. Click each expand icon → modal at ~80vw with fully expanded content
4. Verify Escape closes modal without closing sidebar
5. Verify copy button: text sections copy raw string, JSON sections copy pretty-printed JSON
6. Verify no expand icons on template Command (script steps) or Input Bindings

**Wide panel tests (expanded=true):**
7. Click Maximize2 (whole-panel expand) → Sheet opens → verify NO expand buttons visible
8. On mobile viewport → MobileFullScreen renders → verify NO expand buttons visible

**Agent output specific:**
9. With a running agent step, verify live stream expand works (same `isLive` behavior in modal)
10. With completed agent step, verify stream/raw toggle works in modal and both views are full-height

**Script log specific:**
11. With a script step that has >50 lines of output, verify modal shows all lines without "Show all" button

**Accessibility:**
12. Tab through sidebar sections → verify expand buttons gain focus and become visible on focus
13. On touch device (or Chrome DevTools touch simulation) → verify expand buttons are always visible

**Edge cases:**
14. Step with no runs yet → no expand buttons (nothing to expand)
15. Step with null/empty artifact → no expand button on Output section
16. Very large JSON artifact (1000+ keys) → modal renders, ScrollArea handles overflow

### Lint

```bash
cd web && npm run lint
```

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Z-index stacking when expand dialog opens from narrow sidebar while Sheet is somehow active | Dialog behind Sheet | Low (expand buttons are hidden when `expanded={true}`) | Safety net: `ContentExpandDialog` uses `z-[60]` on both backdrop and popup. Renders via `DialogPrimitive` directly, not the shared `DialogContent` wrapper. |
| Large JSON in `modalMode` renders all nodes expanded, causing jank | Slow modal open | Medium | `modalMode` forces expansion but users can still manually collapse nodes. For pathological cases (>5000 nodes), consider adding a depth cap — out of scope unless observed in practice. |
| `HandoffEnvelopeView` header restructure breaks click target for collapse | Users have to click precisely on the chevron/text area | Low | `CollapsibleTrigger` keeps `flex-1` so it fills available width. Only the small expand icon area is excluded. Visual hit target is nearly identical. |
| `ScriptLogView` in `variant="modal"` renders thousands of log lines at once | Modal opens slowly | Medium | Virtual scrolling would fix this but is out of scope. For now, the `<pre>` is already a single DOM node with `whitespace-pre-wrap` — browsers handle this efficiently. If >10k lines proves slow, add `@tanstack/react-virtual` in a follow-up (already in `package.json`). |
| Thread `modalMode` through `JsonView`'s recursive tree is error-prone | Some nested views don't expand | Low | Unit test (test case #8) explicitly verifies nested expansion with `modalMode`. Only 4 call sites need the prop added (BlockString, JsonStringWrapper, two recursive JsonView calls). |
| Expand button hover-reveal is not discoverable | Users don't know expand exists | Medium | (a) Copy button in `JsonView` already uses this pattern — users are trained. (b) Always visible on touch/coarse pointer. (c) Visible on keyboard focus. (d) Consider adding a tooltip on first render — out of scope but trivial follow-up. |

## Critique Response Log

| # | Issue | Severity | Resolution |
|---|-------|----------|------------|
| 1 | `JsonView`/`BlockString` don't actually expand fully with just `defaultExpanded` | Critical | Added `modalMode` prop that threads through entire tree, forces all depths expanded and bypasses `BlockString` collapse. See Step 1. |
| 2 | `AgentStreamView` hardcodes `max-h-96`, no override | Critical | Added `className` prop, modal passes `max-h-none`. See Step 2. |
| 3 | `ScriptLogView` has both 50-line truncation AND `max-h-96` CSS | Critical | Added `variant="modal"` that bypasses both. See Step 3. |
| 4 | `ExpandableSection` fallback to `children` re-renders constrained content | Critical | Removed `ExpandableSection` abstraction. Each site provides explicit modal content. No `children` fallback. |
| 5 | Z-index: both Dialog and Sheet overlays at `z-50` | Major | `ContentExpandDialog` renders `DialogPrimitive` directly with `z-[60]` on both backdrop and popup. Primary mitigation: expand buttons hidden when `expanded={true}`, so dialog-on-Sheet is rare. See Architecture §1. |
| 6 | Nested button in `CollapsibleTrigger` | Major | Restructured `Section` header: trigger and expand button are siblings in a flex row. See Step 5. |
| 7 | Plan missed live agent stream and raw view mode | Major | Modal now covers: live stream (same props forwarded), historical stream (full height), raw view (included in modal toggle). See Step 7 item 4-5. |
| 8 | Output expansion listed as "optional, lower priority" | Major | Made mandatory (R10). `HandoffEnvelopeView` is in Step 5, not a stretch goal. |
| 9 | Mobile/expanded layout stacking redundant dialog | Major | Expand buttons hidden when `expanded={true}`. Explicit in R1 and enforced by `{!expanded && <ExpandButton />}` guard. |
| 10 | Hover-reveal not accessible | Major | Added `group-focus-within:opacity-100` and `[@media(pointer:coarse)]:opacity-100`. Required `aria-label` on all expand buttons. See R11 and Architecture §1. |
| 11 | Copy serialization per type | Major | Defined per-section copy behavior in the sections table. Strings copy as raw strings. JSON copies via `JSON.stringify`. No copy on streams. See R7 and sections table. |
| 12 | Test plan inadequate | Major | Added colocated test files matching repo convention. Integration tests for StepDetailPanel (expand visibility by `expanded` prop), HandoffEnvelopeView (DOM structure, click isolation), JsonView `modalMode`, ScriptLogView variant. See Step 8. |
| 13 | Scope bookkeeping sloppy | Minor | Removed Input Bindings from target list (they don't truncate). Removed template Command. Added explicit "not instrumented" notes in Out of Scope. Test paths are colocated (e.g., `ContentExpandDialog.test.tsx` next to `ContentExpandDialog.tsx`). Reconciled sections table with implementation steps. |
