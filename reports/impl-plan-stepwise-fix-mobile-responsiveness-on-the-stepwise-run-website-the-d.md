---
title: "Implementation Plan: Fix Mobile Responsiveness on Stepwise Web UI"
date: "2026-03-20T00:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Fix Mobile Responsiveness on Stepwise Web UI

## Overview

Fix horizontal overflow and readability issues on mobile viewports (320px–768px) across the Stepwise React web UI. The app already has a mature responsive shell — Sheet overlays, `useIsMobile`/`useMediaQuery` hooks, `md:` breakpoint toggles — but content inside panels overflows: `<pre>` blocks without wrap/scroll constraints, `JsonView` with compounding left margins, long `font-mono` strings in grids, and overly wide header padding. All fixes are Tailwind class additions on existing components; no new files, dependencies, or CSS.

## Requirements

### R1: No horizontal scroll on any page at 320px–768px viewport width
**Acceptance criteria:** At viewports 320px (iPhone SE), 375px (iPhone 12), and 768px (iPad portrait), `document.documentElement.scrollWidth <= document.documentElement.clientWidth` is true on every route: `/jobs`, `/jobs/:id`, `/flows`, `/flows/:name`, `/settings`.

### R2: Code/YAML blocks are readable and scrollable on mobile
**Acceptance criteria:** Every `<pre>` block displaying YAML, scripts, commands, or prompts either wraps text (`whitespace-pre-wrap`) or has contained horizontal scroll (`overflow-x-auto`) — never pushing the page-level scrollbar. Verified visually on the FlowFileViewer (`/flows/:name` → Source tab), StepDetailPanel (via job detail), and ChatMessages code blocks.

### R3: JSON tree views don't overflow on deeply nested data
**Acceptance criteria:** A 6-level-deep JSON object rendered by `JsonView` stays within the panel's horizontal bounds. At depth 6 with current `ml-4 pl-3` (28px/level = 168px), content on a 320px screen would overflow without constraint. After fix, the `JsonView` root container clips or scrolls.

### R4: Long monospace strings (run IDs, model names, file paths) wrap or truncate
**Acceptance criteria:** UUID run IDs in `StepDetailPanel`, model names like `anthropic/claude-sonnet-4-20250514` in `KV` rows, and long output lists in grid cells don't push their parent container beyond viewport width. Each either uses `break-all`/`break-words` or `truncate`.

### R5: FlowsPage header usable at 320px
**Acceptance criteria:** Tab buttons ("Local"/"Registry"), the "New Flow" button/input, and filter input are all visible and tappable at 320px width without horizontal overflow or overlapping.

## Assumptions (each verified against source)

1. **Viewport meta tag is set correctly.**
   Verified: `web/index.html:7` — `<meta name="viewport" content="width=device-width, initial-scale=1.0" />`. No zoom override.

2. **Tailwind 4 is the sole styling mechanism (no CSS files, no inline styles).**
   Verified: `web/src/index.css` — only Tailwind imports + CSS custom properties for the dark theme. CLAUDE.md guardrail #8: "No CSS files or inline styles in web — Tailwind classes and shadcn/ui components only."

3. **`useIsMobile()` returns true at ≤767px; `useMediaQuery("(max-width: 1023px)")` returns true at ≤1023px.**
   Verified: `web/src/hooks/useMediaQuery.ts:3-22`. `useIsMobile` wraps `useMediaQuery("(max-width: 767px)")`. The `isCompact` flag in `EditorPage.tsx:89` uses the 1023px breakpoint.

4. **Sheet-based mobile overlays already function correctly for panel-level layout.**
   Verified: `FlowsPage.tsx:411-429` (registry Sheet), `EditorPage.tsx:441-476` (step Sheet), `EditorPage.tsx:503-520` (chat Sheet), `JobDetailPage.tsx:567-582` (right panel Sheet). These all use `w-[85vw] sm:max-w-sm` and `overflow-y-auto`. The problem is content *inside* these Sheets, not the Sheets themselves.

5. **`FlowFileViewer.tsx:108` is the single worst overflow offender.**
   Verified: `web/src/components/editor/FlowFileViewer.tsx:108-109` — `<pre className="text-xs text-zinc-300 p-3 font-mono leading-relaxed">` has **zero** overflow or wrapping classes, unlike every other `<pre>` in the codebase (all others have at least `whitespace-pre-wrap` or `overflow-x-auto`).

6. **`JsonView`'s nested indentation compounds without container constraint.**
   Verified: `web/src/components/JsonView.tsx:179` and `:241` — each nesting level adds `ml-4` (16px) + `pl-3` (12px) = 28px. Neither the root `<div>` nor any wrapper in consumers (`HandoffEnvelopeView.tsx:46`, `StepDetailPanel.tsx:424-428`, `JobDetailPage.tsx:544-546`) sets `overflow-x-auto` on the JsonView container.

7. **AgentStreamView text segments already wrap correctly.**
   Verified: `web/src/components/jobs/AgentStreamView.tsx:82` — `whitespace-pre-wrap text-sm font-mono`. Container at `:177-184` has `overflow-hidden` + `overflow-y-auto`. No fix needed here.

8. **Existing `<pre>` blocks in StepDetailPanel (jobs) have wrapping but some lack `break-words`.**
   Verified: `StepDetailPanel.tsx:172` uses `break-all` (script command — OK), `:181` uses `break-words` (human prompt — good), `:191` uses `break-all` (agent prompt — OK). The error text at `:399` has `whitespace-pre-wrap` but no `break-words`, which could overflow on very long unbroken error messages.

## Out of Scope

- **Light mode / theme toggle** — Dark-only per guardrail #9. Not related to overflow.
- **Navigation bar redesign** — `AppLayout.tsx` nav is already responsive: icon-only at mobile, text at `md:`. Touch targets meet 44×44px. No overflow issues observed.
- **DAG canvas responsiveness** — `FlowDagView` uses a `<div>` with internal pan/zoom via pointer events and transforms. The canvas content is never laid out in document flow, so it can't cause horizontal page overflow.
- **SettingsPage responsiveness** — Already uses `min-w-0` (`:290`, `:393`) on flex containers. Model search results are in a dropdown overlay. No pre/code blocks.
- **New components or abstractions** — All fixes are class additions to existing elements.
- **Performance or bundle size** — Strictly visual/layout fixes.

## Architecture

### Why Tailwind-only constraints shape this plan

Per guardrail #8 (no CSS files or inline styles), every fix must be a Tailwind utility class. This constrains the approach:

- **No `@media` queries in CSS** — use responsive prefixes (`sm:`, `md:`, `lg:`) or non-responsive utilities that apply at all widths.
- **No global reset stylesheet** — the only global insertion point is `web/src/index.css`'s `@layer base` block (`:119-128`), where Tailwind's `@apply` directive is used.
- **No `style` props on elements** — except two existing instances in `JobDetailPage.tsx` (`:261`, `:586`) using `maxHeight` for viewport calc. We won't add more.

### How the existing responsive system works

The app uses a two-tier responsive strategy:

1. **Layout tier** (panel visibility, sidebar collapse): Controlled by `useIsMobile()` (≤767px) and `useMediaQuery("(max-width: 1023px)")` (≤1023px). At mobile widths, sidebars become `Sheet` overlays. This tier already works correctly.

2. **Content tier** (text wrapping, code block overflow, grid sizing): This is what's broken. Individual components render content in `<pre>`, `font-mono` spans, and nested `JsonView` trees without width constraints. The fixes belong here.

### Existing overflow-handling patterns in the codebase

The codebase already uses three patterns for text overflow. The plan applies these consistently where they're missing:

| Pattern | Where used | Tailwind classes |
|---|---|---|
| **Wrap + break** | `StepDefinitionPanel.tsx:126` CodeBlock, `AgentStreamView.tsx:82`, `HumanInputPanel.tsx:149` | `whitespace-pre-wrap break-words` |
| **Contained scroll** | `ChatMessages.tsx:207`, `FlowDagView.tsx:733`, `DataFlowPanel.tsx:151` | `overflow-x-auto` on `<pre>` |
| **Truncate** | `RegistryFlowCard.tsx:42`, `FlowsPage.tsx:310`, `AppLayout.tsx:34` | `truncate` on inline text, `min-w-0` on flex parent |

No new patterns are introduced — we're extending existing ones to components that missed them.

### Component dependency map (what renders what)

Understanding the render tree clarifies why fixing `JsonView` propagates to multiple consumers:

```
JobDetailPage
 ├─ StepDetailPanel (Sheet on mobile)
 │   ├─ <pre> blocks (script, prompt, agent prompt) ← already wrapped
 │   ├─ JsonView (run inputs) ← NEEDS FIX
 │   ├─ HandoffEnvelopeView
 │   │   └─ JsonView (artifact, sidecar, meta) ← NEEDS FIX
 │   └─ AgentStreamView ← already wrapped
 └─ Job Details panel
     └─ JsonView (job inputs/outputs) ← NEEDS FIX

EditorPage
 ├─ FlowFileViewer (Source tab) ← <pre> NEEDS FIX
 ├─ StepDefinitionPanel (Sheet on mobile)
 │   ├─ CodeBlock ← already wrapped
 │   └─ KV, OutputSchemaTable ← NEEDS FIX (long mono strings)
 └─ ChatSidebar
     └─ ChatMessages ← <pre> has overflow-x-auto, user msg needs break-words

FlowsPage
 ├─ FlowInfoPanel (Sheet on mobile) ← flow name needs break-words
 └─ LocalFlowInfoPanel (desktop only) ← flow name needs break-words
```

## Implementation Steps

### Dependency ordering rationale

Steps are ordered from broadest impact to narrowest:

1. **Global safety net first** (Step 1) — prevents any remaining overflow from leaking to page-level scroll. Must come first because subsequent steps' manual testing assumes no page-level horizontal scroll.
2. **Shared components next** (Steps 2–3) — `JsonView` and `FlowFileViewer` are reused across many pages. Fixing them first means multiple consumers benefit.
3. **Page-specific fixes last** (Steps 4–8) — target individual pages/panels. These can be done in any order relative to each other, but depend on the global + shared fixes being in place for accurate testing.

---

### Step 1: Add global `overflow-x: hidden` safety net
**File:** `web/src/index.css:127`
**Depends on:** nothing (first step)
**Why first:** This is the containment layer. Without it, any `<pre>` or `JsonView` that still overflows after component-level fixes would create a page-level scrollbar. With it, those cases are clipped to the viewport and we can fix them incrementally. The DAG canvas, CodeMirror editor, and Sheet overlays all use their own internal scroll containers, so they're unaffected.

**Change:** In `@layer base`, modify the `html` rule from:
```css
html { @apply font-sans; }
```
to:
```css
html { @apply font-sans overflow-x-hidden; }
```

**Verification:** Open any page at 320px → confirm no horizontal scroll appears even before other fixes.

---

### Step 2: Fix FlowFileViewer `<pre>` block — the worst offender
**File:** `web/src/components/editor/FlowFileViewer.tsx:108-109`
**Depends on:** Step 1 (safety net active for testing)
**Why second:** This is the single most visible bug. The YAML file viewer's `<pre>` tag has **zero** overflow handling — it's the only `<pre>` in the entire codebase without `whitespace-pre-wrap`, `overflow-x-auto`, or `break-words`. It directly causes horizontal overflow when viewing any YAML file with lines > ~40 characters on mobile.

**Change:** On line 108, add wrapping classes to the `<pre>` tag:
```
Before: <pre className="text-xs text-zinc-300 p-3 font-mono leading-relaxed">
After:  <pre className="text-xs text-zinc-300 p-3 font-mono leading-relaxed whitespace-pre-wrap break-words">
```

This matches the pattern used by `StepDefinitionPanel.tsx:126` (`CodeBlock` component).

**Verification:** Navigate to `/flows/<name>` → Source tab → view a YAML file with long lines at 320px → text wraps within the container.

---

### Step 3: Fix JsonView container overflow (shared component — affects 6+ consumers)
**File:** `web/src/components/JsonView.tsx`
**Depends on:** Step 1 (safety net)
**Why third:** `JsonView` is consumed by `StepDetailPanel` (run inputs, run watch), `HandoffEnvelopeView` (artifact, sidecar, meta), `JobDetailPage` (job inputs/outputs), and `DataFlowPanel`. Fixing the shared component once propagates to all consumers.

**Problem:** Each nesting level adds `ml-4 pl-3` (28px). At depth 6: 168px of left margin. On a 320px screen inside a `w-[85vw]` Sheet (272px), the remaining content width is 104px — not enough for even a short key-value pair.

**Sub-step 3a:** Add `overflow-x-auto` to the root-level `JsonView` wrapper.

For the object branch (line 207), array branch (line 162), and top-level renders — add `overflow-x-auto` to the outermost `<div>` when `depth === 0`. This confines horizontal scroll to the JsonView container rather than the page.

Specifically, at lines 207 and 162, when rendering the expandable object/array `<div>`, conditionally add `overflow-x-auto` at depth 0:
```tsx
<div className={cn(depth === 0 && "overflow-x-auto")}>
```

**Sub-step 3b:** Add `min-w-0` to nested `<div>` containers.

The nested tree containers at lines 179 and 241 (`ml-4 border-l ... pl-3`) are flex children. Without `min-w-0`, flex items won't shrink below their content width. Add `min-w-0` to these containers:
```
Before: <div className="ml-4 border-l border-zinc-700/50 pl-3 mt-1 space-y-0.5">
After:  <div className="ml-4 border-l border-zinc-700/50 pl-3 mt-1 space-y-0.5 min-w-0">
```

**Verification:** In `StepDetailPanel`, expand a run with deeply nested JSON artifact → at 320px, content stays within the Sheet bounds or scrolls horizontally within the JsonView container.

---

### Step 4: Fix StepDetailPanel run ID and error text overflow
**File:** `web/src/components/jobs/StepDetailPanel.tsx`
**Depends on:** Step 3 (JsonView fixes — because StepDetailPanel contains JsonView)

**Sub-step 4a:** Run ID at line 382-383.
The run ID is a 36-character UUID rendered as `font-mono text-[10px]` inside a `grid-cols-2` cell. On mobile, the grid column is ~136px wide (half of a ~272px Sheet minus padding). A UUID at 10px monospace is ~180px.

**Change** at line 382:
```
Before: <span className="text-zinc-600 font-mono text-[10px]">
After:  <span className="text-zinc-600 font-mono text-[10px] break-all">
```

**Sub-step 4b:** Error text at line 399.
Has `whitespace-pre-wrap` but no `break-words`. Long unbroken error strings (e.g., stack traces with deep module paths) can overflow.

**Change** at line 399:
```
Before: <div className="text-red-300/80 text-xs font-mono whitespace-pre-wrap">
After:  <div className="text-red-300/80 text-xs font-mono whitespace-pre-wrap break-words">
```

**Sub-step 4c:** Definition grid values at lines 150, 154, 160.
These `font-mono text-xs` cells in a `grid-cols-2` can overflow with long output lists (e.g., `analysis, quality_score, detailed_breakdown, recommendations`).

**Change** at each value cell (lines 150, 154, 160):
```
Before: <div className="text-foreground font-mono text-xs">
After:  <div className="text-foreground font-mono text-xs break-all">
```

**Verification:** Open `/jobs/<id>` → select a step with long outputs list, a failed run with error, or any run with UUID visible → at 375px, all text wraps within the grid/panel.

---

### Step 5: Fix FlowsPage header spacing for narrow viewports
**File:** `web/src/pages/FlowsPage.tsx`
**Depends on:** Step 1 (safety net)

**Problem:** Line 159 uses `px-6` (24px per side = 48px total) and `gap-4` (16px). On a 320px screen, the tab buttons, spacer, and "New Flow" button need ~48px less horizontal space than available.

**Sub-step 5a:** Reduce header padding on mobile.
Line 159:
```
Before: <div className="flex items-center gap-4 px-6 py-4 border-b border-border shrink-0">
After:  <div className="flex items-center gap-2 sm:gap-4 px-3 sm:px-6 py-3 sm:py-4 border-b border-border shrink-0">
```

**Sub-step 5b:** Narrow the new flow input on mobile.
Line 201:
```
Before: className="w-40 h-8 text-sm bg-zinc-900 border-zinc-700"
After:  className="w-28 sm:w-40 h-8 text-sm bg-zinc-900 border-zinc-700"
```

**Verification:** Navigate to `/flows` at 320px → tab buttons and "New Flow" button/input all visible without overflow. Click "New Flow" → input fits within header width.

---

### Step 6: Fix StepDefinitionPanel (editor) KV and grid overflow
**File:** `web/src/components/editor/StepDefinitionPanel.tsx`
**Depends on:** Step 1 (safety net)

**Sub-step 6a:** `KV` component (lines 156-162) — long model names.
The KV component renders labels and values in a flex row. The value span has no overflow handling. Model names like `anthropic/claude-sonnet-4-20250514` (39 chars) at monospace font exceed the ~200px available in a Sheet panel.

**Change** at line 160:
```
Before: <span className="text-zinc-300 font-mono">{children}</span>
After:  <span className="text-zinc-300 font-mono break-all min-w-0">{children}</span>
```

**Sub-step 6b:** For-Each grid values (lines 610-624).
Long step names in `grid-cols-2` values can overflow. Add `break-all` to the zinc-400 value spans at lines 612-613, 617-618, 621-622:

```
Before: <span className="text-zinc-400">
After:  <span className="text-zinc-400 break-all">
```

**Verification:** In the editor, select a step with `executor: llm` and a long model name → KV row wraps cleanly. Select a for-each step with long source step names → grid wraps.

---

### Step 7: Fix ChatMessages user messages and inline code
**File:** `web/src/components/editor/ChatMessages.tsx`
**Depends on:** Step 1 (safety net)

**Sub-step 7a:** User message at line 179.
Has `whitespace-pre-wrap` but no `break-words`. Long URLs or unbroken strings in user messages will overflow.

**Change** at line 179:
```
Before: <span className="whitespace-pre-wrap">{msg.content}</span>
After:  <span className="whitespace-pre-wrap break-words">{msg.content}</span>
```

**Sub-step 7b:** Inline `<code>` elements at line 81.
Rendered by `renderInline()`. Long code tokens (e.g., long class names, file paths) can push the line width.

**Change** at line 81:
```
Before: parts.push(<code key={key++} className="px-1 py-0.5 rounded bg-zinc-800 text-zinc-300 font-mono text-[11px]">{match[3]}</code>);
After:  parts.push(<code key={key++} className="px-1 py-0.5 rounded bg-zinc-800 text-zinc-300 font-mono text-[11px] break-all">{match[3]}</code>);
```

**Verification:** In the editor chat, send a message containing a long URL → wraps within bubble. Receive a response with long `code` spans → wraps within line.

---

### Step 8: Fix FlowInfoPanel and LocalFlowInfoPanel flow name overflow
**Files:** `web/src/components/editor/FlowInfoPanel.tsx:23`, `web/src/components/editor/LocalFlowInfoPanel.tsx:223`
**Depends on:** Step 1 (safety net)

**Problem:** Flow names use `<h3 className="font-semibold text-foreground">`. A flow named `my-very-long-multi-purpose-data-pipeline-flow` would exceed the panel width (w-80 = 320px desktop, or Sheet w-[85vw] = ~272px at 320px viewport).

**Change** in `FlowInfoPanel.tsx:23`:
```
Before: <h3 className="font-semibold text-foreground">{flow.name}</h3>
After:  <h3 className="font-semibold text-foreground break-words">{flow.name}</h3>
```

**Change** in `LocalFlowInfoPanel.tsx:223`:
```
Before: <h3 className="font-semibold text-foreground">{flow.name}</h3>
After:  <h3 className="font-semibold text-foreground break-words">{flow.name}</h3>
```

**Verification:** Create or view a flow with a very long name → name wraps in the info panel instead of overflowing.

---

### Step 9: Verify JobDetailPage header and job ID display
**File:** `web/src/pages/JobDetailPage.tsx`
**Depends on:** Steps 1, 3, 4

This step is verification + minor fixup. The JobDetailPage header already has good patterns (`truncate` on job name at `:327`, `min-w-0` on flex parents at `:297`, `:325`, `:397`). But the job ID display at line 353-358 needs inspection:

**Line 357:** `{job.id}` is rendered in `text-[10px] font-mono text-zinc-600` without `break-all`. A UUID in this context is on a line that also contains the objective text. At narrow widths, this could overflow.

**Change** at line 353:
```
Before: <div className="text-[10px] font-mono text-zinc-600 mt-0.5">
After:  <div className="text-[10px] font-mono text-zinc-600 mt-0.5 break-all">
```

**Verification:** Open `/jobs/<id>` at 320px → job ID wraps within the header area.

## Testing Strategy

### Automated tests (run before and after all changes)

```bash
# Frontend tests — should all pass unchanged (class-only additions don't affect behavior)
cd web && npm run test

# Frontend lint — catches any typos in class strings
cd web && npm run lint

# Python backend tests — sanity check, shouldn't be affected
uv run pytest tests/ -x -q
```

### Manual testing protocol

**Setup:**
```bash
cd web && npm run dev
# Open Chrome → DevTools → Device Toolbar
```

**Test matrix (each cell = one viewport × one scenario):**

| Viewport | Route / Scenario | What to check |
|---|---|---|
| 320px (iPhone SE) | `/flows` Local tab | Header fits, tab buttons visible, flow list scrolls, no horizontal scroll |
| 320px | `/flows` → click "New Flow" | Input fits in header row |
| 320px | `/flows` Registry tab → select flow | Sheet opens, flow name wraps, executor badges wrap |
| 320px | `/flows/<name>` → Source tab | YAML `<pre>` wraps long lines, no page-level overflow |
| 320px | `/flows/<name>` → Flow tab → click step | Step Sheet opens, CodeBlock wraps, KV model name wraps |
| 320px | `/flows/<name>` → Chat → send long URL | User message wraps within bubble |
| 320px | `/jobs/<id>` → select step | Step Sheet opens, UUID wraps, grid values wrap |
| 320px | `/jobs/<id>` → step with nested JSON output | JsonView stays within Sheet, deep nesting scrolls horizontally within container |
| 320px | `/jobs/<id>` → failed step with long error | Error text wraps |
| 375px (iPhone 14) | All above scenarios | Same checks, slightly more room |
| 768px (iPad) | `/flows` and `/jobs/<id>` | Desktop layout active, no changes should regress |

**Console verification on each page:**
```js
// Run in DevTools console — should return false
document.documentElement.scrollWidth > document.documentElement.clientWidth
```

### Regression testing
After each step, check that the adjacent desktop layout (at 1280px+) is visually unchanged:
- Sidebars still render at fixed widths (`w-72`, `w-80`)
- Sheet overlays only appear at mobile widths
- `CodeBlock` content still shows full-width pre-wrapped text, not truncated

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `overflow-x: hidden` on `html` masks future legitimate horizontal content | Low | Medium | Canary: the DAG canvas, CodeMirror editor, and Sheet overlays all use internal scroll containers (verified in source). The only thing clipped is page-level overflow, which is always a bug. If a future component needs page-level horizontal scroll, it should use its own `overflow-x-auto` container. |
| `break-all` on `font-mono` text breaks words at ugly boundaries (mid-identifier) | Medium | Low (cosmetic) | Use `break-words` (which prefers word boundaries) wherever possible. Only use `break-all` on UUIDs, hashes, and paths where there are no natural break points. Specific choices: `break-all` for run IDs (Step 4a), grid values (Step 4c), for-each values (Step 6b), job ID (Step 9). `break-words` for error text (Step 4b), user messages (Step 7a), flow names (Step 8). |
| `whitespace-pre-wrap` on FlowFileViewer changes YAML visual formatting | Low | Low | YAML indentation (leading spaces) is preserved by `pre-wrap` — it only wraps lines that exceed container width. The viewer is read-only (line 106-115: YAML files can't be edited here), so formatting changes don't affect the source file. |
| Push to master = immediate user release | — | High | Full test suite (`npm run test` + `npm run lint` + `pytest tests/`) must pass before any commit. Manual visual check on 320px and 375px for all affected pages. Each step is committed separately so individual changes can be reverted if needed. |
| `min-w-0` on JsonView nested containers breaks truncation in some consumer | Low | Medium | `min-w-0` is already used in 20+ places in the codebase (verified via grep). It enables flex shrinking, which is the desired behavior. No consumer relies on JsonView children maintaining a minimum width. |
