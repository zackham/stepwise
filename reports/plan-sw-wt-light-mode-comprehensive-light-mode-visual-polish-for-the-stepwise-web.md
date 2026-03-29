# Comprehensive Light Mode Visual Polish for the Stepwise Web UI

## Overview

The dark theme is production-quality. Light mode has received two rounds of fixes (commits cc227a8 + 49c9855) but still has systematic gaps: hardcoded dark colors in SVG/canvas rendering, missing `dark:` variant pairs on ~55 files, a hardcoded dark CodeMirror theme, and dark-only form inputs in several editor panels. This plan audits every page and component, catalogs every remaining issue, and provides an ordered fix sequence that brings light mode to parity with dark without regressing the dark theme.

The fix pattern is consistent throughout: light-mode color comes first (the default), then `dark:` prefix for the dark variant. For SVG/canvas hardcodes, we either use CSS custom properties or pass the current theme as a prop/context to compute the right color at render time.

---

## Requirements

### R1: Text contrast meets WCAG AA
- **Acceptance criteria:** All normal text achieves >= 4.5:1 contrast ratio against its background; large text (18px+ or 14px+ bold) achieves >= 3:1.
- Labels using `text-zinc-400` or `text-zinc-300` on white backgrounds fail this. Must become `text-zinc-500 dark:text-zinc-400` or darker.

### R2: Backgrounds use light palette in light mode
- **Acceptance criteria:** No `bg-zinc-800`, `bg-zinc-900`, `bg-zinc-950` classes appear without a preceding light-mode class (e.g., `bg-white`, `bg-zinc-50`, `bg-zinc-100`).
- 47 occurrences of bare `bg-zinc-900` and 44 of `bg-zinc-800` across 55 files.

### R3: Borders and dividers are visible but subtle
- **Acceptance criteria:** All `border-zinc-700` and `border-zinc-800` have light counterparts (`border-zinc-200` or `border-zinc-300`).
- 55 bare `border-zinc-700` and many `border-zinc-800` occurrences lack light variants.

### R4: SVG/Canvas colors adapt to theme
- **Acceptance criteria:** DAG edges, arrows, node strokes, MiniDag visualization, and DependencyArrows all render with appropriate contrast on both light and dark backgrounds.
- Currently all hardcoded hex/oklch values tuned for dark backgrounds.

### R5: CodeMirror editor supports light theme
- **Acceptance criteria:** YamlEditor uses a light CodeMirror theme when the app is in light mode, and oneDark when in dark mode.

### R6: Scrollbar styling adapts to theme
- **Acceptance criteria:** Light mode scrollbars are subtle gray on white; dark mode scrollbars remain as-is.
- Current light-mode scrollbar (`oklch(0.552)`) is too dark for white backgrounds.

### R7: DAG image export respects current theme
- **Acceptance criteria:** PNG export from FlowDagView uses light backgrounds/text when in light mode.

### R8: Dark theme is not regressed
- **Acceptance criteria:** All changes use the `light-first dark:override` pattern. No existing `dark:` classes are removed. Visual inspection of dark mode shows no regressions.

---

## Assumptions (verified against files)

1. **Theme toggle is working** — `AppLayout.tsx` manages `.dark` class on `<html>`, persists to localStorage, respects `prefers-color-scheme`. (Verified: lines 30-39, 150-156.)

2. **CSS custom properties are properly defined for both modes** — `:root` (light) and `.dark` in `index.css` both define the full set of semantic tokens (`--background`, `--foreground`, `--border`, etc.). (Verified: lines 9-76.)

3. **Tailwind `dark:` variant works** — Custom variant `@custom-variant dark (&:is(.dark *))` is defined. (Verified: `index.css` line 6.)

4. **shadcn/ui primitives are theme-aware** — All `components/ui/` files use semantic tokens (`bg-background`, `text-foreground`, etc.) and are already dual-themed. (Verified by exploration of button, input, dialog, select, badge, tabs, textarea.)

5. **The established pattern is `light-class dark:dark-class`** — Prior fix commits (2e0b77e, cc227a8, 49c9855) all follow this pattern. We continue it.

6. **CodeMirror `@codemirror/theme-one-dark` is the only theme imported** — `YamlEditor.tsx` imports `oneDark` with no light alternative. (Verified: line 6.)

7. **No React context for theme exists** — Components that need the current theme for non-CSS purposes (canvas rendering, SVG computation) must read it from `document.documentElement.classList.contains('dark')` or accept it as a prop.

---

## Implementation Steps

### Phase 1: Foundation & Tooling (Steps 1-2)

#### Step 1: Add theme-aware CSS custom properties for SVG/canvas use
**File:** `web/src/index.css`

Add new custom properties that SVG and canvas rendering can reference:

```css
:root {
  /* ...existing... */
  --dag-edge-inactive: oklch(0.75 0 0);
  --dag-edge-completed: oklch(0.4 0.1 160);
  --dag-node-pending-stroke: oklch(0.7 0 0);
  --dag-node-pending-bg: oklch(0.92 0 0);
  --dag-canvas-bg: #ffffff;
  --dag-canvas-border: #e4e4e7;
  --dag-canvas-text: #18181b;
  --dag-canvas-muted: #a1a1aa;
}

.dark {
  /* ...existing... */
  --dag-edge-inactive: oklch(0.35 0 0);
  --dag-edge-completed: oklch(0.5 0.1 160);
  --dag-node-pending-stroke: oklch(0.4 0 0);
  --dag-node-pending-bg: oklch(0.25 0 0);
  --dag-canvas-bg: #09090b;
  --dag-canvas-border: #27272a;
  --dag-canvas-text: #fafafa;
  --dag-canvas-muted: #52525b;
}
```

Also fix scrollbar colors for light mode:
```css
/* Light mode scrollbars - softer */
* {
  scrollbar-color: oklch(0.8 0 0) transparent;
}
.dark * {
  scrollbar-color: oklch(0.4 0 0) transparent;
}
::-webkit-scrollbar-thumb {
  background: oklch(0.8 0 0);
}
::-webkit-scrollbar-thumb:hover {
  background: oklch(0.7 0 0);
}
```

#### Step 2: Create a `useTheme` hook for imperative theme access
**File:** `web/src/hooks/useTheme.ts` (new file)

A minimal hook that components needing imperative theme access (canvas, SVG color computation) can use:

```typescript
export function useTheme(): "dark" | "light" {
  // Read from DOM — cheap, no context needed
  const [theme, setTheme] = useState<"dark" | "light">(() =>
    document.documentElement.classList.contains("dark") ? "dark" : "light"
  );
  useEffect(() => {
    const observer = new MutationObserver(() => {
      setTheme(document.documentElement.classList.contains("dark") ? "dark" : "light");
    });
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return theme;
}
```

This avoids threading theme through props everywhere — components that render to canvas or compute SVG colors call `useTheme()`.

---

### Phase 2: SVG & Canvas Hardcodes (Steps 3-7) — CRITICAL

#### Step 3: Fix DagEdges.tsx SVG colors
**File:** `web/src/components/dag/DagEdges.tsx`

**Issues:**
- Marker fill colors at lines 162, 177, 191, 205, 220, 235 are hardcoded oklch
- Edge stroke colors at lines 265-266, 315 are hardcoded
- Selected label background at line 361 is very dark oklch

**Fix approach:**
- Use `useTheme()` hook to get current theme
- Define color maps for light/dark:
  - Inactive edges: `oklch(0.75 0 0)` (light) / `oklch(0.35 0 0)` (dark)
  - Completed edges: `oklch(0.4 0.12 160)` (light) / `oklch(0.5 0.1 160)` (dark)
  - Selected label bg: `oklch(0.9 0.04 250)` (light) / `oklch(0.25 0.08 250)` (dark)
- Active/suspended/critical/loop colors are vibrant enough to work on both backgrounds — verify and adjust luminance if needed

#### Step 4: Fix MiniDag.tsx hex colors
**File:** `web/src/components/canvas/MiniDag.tsx`

**Issues:**
- `statusColor()` function (lines 27-43): `#71717a` and `#52525b` too dark for light bg
- Edge strokes at lines 181, 194: `#3f3f46` invisible on light bg
- Pending stroke at line 217: `#52525b`

**Fix approach:**
- Use `useTheme()` hook
- For status colors, use theme-branched values: pending/cancelled → `#a1a1aa` (light) / `#71717a` (dark), default → `#d4d4d8` (light) / `#52525b` (dark)
- Edge strokes: `#d4d4d8` (light) / `#3f3f46` (dark)

#### Step 5: Fix DependencyArrows.tsx
**File:** `web/src/components/canvas/DependencyArrows.tsx`

**Issues:**
- Lines 34, 58: `#3f3f46` fill/stroke invisible on light bg

**Fix approach:**
- Use `useTheme()` hook
- Inactive arrow: `#a1a1aa` (light) / `#3f3f46` (dark)
- Active arrow (`#f59e0b`) works on both — keep as-is

#### Step 6: Fix FlowDagView.tsx canvas export
**File:** `web/src/components/dag/FlowDagView.tsx`

**Issues:**
- Lines 186-284: PNG export uses hardcoded dark colors (`#09090b`, `#27272a`, `#fafafa`, `#52525b`)
- STATUS_COLORS object (lines 38-47): gray status colors too dim for light bg

**Fix approach:**
- Use `useTheme()` or read `getComputedStyle(document.documentElement)` to get CSS custom properties at export time
- Replace hardcoded hex with theme-aware values:
  - Background: `--dag-canvas-bg`
  - Borders: `--dag-canvas-border`
  - Text: `--dag-canvas-text`
  - Muted text: `--dag-canvas-muted`
- STATUS_COLORS: branch on theme for gray values (pending, cancelled, archived)

#### Step 7: Fix ContainerPortEdges.tsx
**File:** `web/src/components/dag/ContainerPortEdges.tsx`

**Issues:**
- Lines 112, 128: `stroke="rgb(168 85 247 / 0.25)"` — low-opacity purple barely visible on light bg

**Fix approach:**
- Increase opacity for light mode: `rgb(168 85 247 / 0.4)` (light) / `rgb(168 85 247 / 0.25)` (dark)
- Use `useTheme()` or CSS variable

---

### Phase 3: CodeMirror Theme (Step 8)

#### Step 8: Add light CodeMirror theme to YamlEditor
**File:** `web/src/components/editor/YamlEditor.tsx`

**Issues:**
- Line 6-28: Always uses `oneDark` — no light alternative

**Fix approach:**
- Import a light theme. Options:
  - Use `@codemirror/theme-one-dark` for dark + the default light theme (no import needed — just omit the theme extension)
  - Or use `@uiw/codemirror-theme-github` for a polished light theme
- Simplest: conditionally include `oneDark` only in dark mode. The default CodeMirror appearance is a serviceable light theme.
- Accept `theme` prop or use `useTheme()` hook
- Recreate the EditorView when theme changes (or use compartments for dynamic reconfiguration)

```typescript
import { useTheme } from "@/hooks/useTheme";
import { Compartment } from "@codemirror/state";

const themeCompartment = new Compartment();

// In the effect:
const extensions = [
  basicSetup,
  yaml(),
  themeCompartment.of(isDark ? oneDark : []),
  // ... rest
];

// On theme change:
view.dispatch({ effects: themeCompartment.reconfigure(isDark ? oneDark : []) });
```

---

### Phase 4: Component-by-Component Fixes (Steps 9-20)

Each step follows the same pattern: find bare dark-only classes, add light-mode counterpart before the `dark:` variant. Listed in priority order (most-visible first).

#### Step 9: FlowConfigPanel.tsx — ALL form fields dark-only
**File:** `web/src/components/editor/FlowConfigPanel.tsx`

**16 occurrences.** Every form input, label, and container is dark-only:
- Labels: `text-zinc-400` → `text-zinc-600 dark:text-zinc-400`
- Descriptions: `text-zinc-600` → `text-zinc-500 dark:text-zinc-600`
- Inputs: `bg-zinc-900 border-zinc-700` → `bg-white border-zinc-300 dark:bg-zinc-900 dark:border-zinc-700`
- Container: `border-zinc-800 bg-zinc-900/50` → `border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900/50`
- Header border: `border-zinc-800` → `border-zinc-200 dark:border-zinc-800`
- Toggle button: `hover:bg-zinc-800` → `hover:bg-zinc-100 dark:hover:bg-zinc-800`
- Raw YAML textarea: `bg-zinc-950 border-zinc-700` → `bg-zinc-50 border-zinc-300 dark:bg-zinc-950 dark:border-zinc-700`

#### Step 10: StepDefinitionPanel.tsx — 29 occurrences
**File:** `web/src/components/editor/StepDefinitionPanel.tsx`

**29 dark-only pattern occurrences.** Fix all form fields, section headers, borders, and code blocks. Same pattern as FlowConfigPanel.

#### Step 11: FlowsPage.tsx — Flow detail panels
**File:** `web/src/pages/FlowsPage.tsx`

**19 occurrences.** Key fixes:
- Flow info panels: `border-zinc-800 bg-zinc-900/50` → `border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900/50`
- Metadata badges: `border-zinc-700 bg-zinc-950/70 text-zinc-300` → `border-zinc-300 bg-zinc-100 text-zinc-600 dark:border-zinc-700 dark:bg-zinc-950/70 dark:text-zinc-300`
- DAG preview: `border-zinc-800 bg-zinc-950/30` → `border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-950/30`
- Section headers and labels

#### Step 12: StepNode.tsx — DAG node styling
**File:** `web/src/components/dag/StepNode.tsx`

**19 occurrences.** Key fixes:
- Pending node: `bg-zinc-700 border-zinc-600` → `bg-zinc-200 border-zinc-300 dark:bg-zinc-700 dark:border-zinc-600`
- Labels: `text-zinc-400` → `text-zinc-500 dark:text-zinc-400`
- Status text colors
- Port styling

#### Step 13: DataFlowPanel.tsx — 14 occurrences
**File:** `web/src/components/dag/DataFlowPanel.tsx`

Fix panel backgrounds, borders, labels, and data field styling.

#### Step 14: StepDetailPanel.tsx — 34 occurrences
**File:** `web/src/components/jobs/StepDetailPanel.tsx`

**Highest count in jobs/.** Fix all section containers, labels, tab content areas, code blocks, and metadata displays.

#### Step 15: JobList.tsx — 21 occurrences
**File:** `web/src/components/jobs/JobList.tsx`

Fix job cards, status indicators, metadata labels, and empty-state text.

#### Step 16: SettingsPage.tsx — 32 occurrences
**File:** `web/src/pages/SettingsPage.tsx`

Fix form fields, model label badges (`bg-violet-950 text-violet-400` → `bg-violet-100 text-violet-700 dark:bg-violet-950 dark:text-violet-400`), delete buttons, section containers.

#### Step 17: AgentStreamView.tsx — 11 occurrences
**File:** `web/src/components/jobs/AgentStreamView.tsx`

Fix stream output backgrounds, tool call blocks, and inline code styling.

#### Step 18: FlowDagView.tsx — 16 occurrences (Tailwind classes)
**File:** `web/src/components/dag/FlowDagView.tsx`

In addition to the canvas export fix (Step 6), fix Tailwind classes for controls, labels, and overlays.

#### Step 19: CommandPalette.tsx — 15 occurrences
**File:** `web/src/components/CommandPalette.tsx`

Fix group headings, item hover states, search input, and keyboard shortcut hints.

#### Step 20: Remaining files (batch)

Fix the remaining ~35 files with lower occurrence counts. Group by directory:

**Editor components** (7 files):
- `ChatInput.tsx` (9 occ) — input borders, dropdown menu, placeholder text
- `ChatMessages.tsx` (7 occ) — message backgrounds, assistant/user bubbles
- `FlowInfoPanel.tsx` (3 occ) — panel borders and labels
- `LocalFlowInfoPanel.tsx` (7 occ) — similar to FlowInfoPanel
- `FlowFileList.tsx` (4 occ), `FlowFileViewer.tsx` (4 occ), `FlowFileTree.tsx` (2 occ)
- `RegistryBrowser.tsx` (4 occ), `RegistryFlowCard.tsx` (3 occ)
- `CreateFlowDialog.tsx` (2 occ), `EditorToolbar.tsx` (1 occ), `StepPalette.tsx` (2 occ)

**Jobs components** (10 files):
- `TimelineView.tsx` (7 occ) — timeline bars and labels
- `JobTreeView.tsx` (6 occ) — tree indent lines, node labels
- `ErrorRecoverySuggestions.tsx` (7 occ) — suggestion cards
- `ArtifactDiffPanel.tsx` (4 occ) — diff backgrounds
- `QuickLaunch.tsx` (5 occ) — input and action buttons
- `CreateJobDialog.tsx` (4 occ) — form fields
- `RunComparisonView.tsx` (3 occ) — comparison headers
- `JobDetailSidebar.tsx` (2 occ), `JobSummaryBar.tsx` (4 occ), `JobControls.tsx` (2 occ)
- `HandoffEnvelopeView.tsx` (1 occ), `FulfillWatchDialog.tsx` (1 occ), `JobInputForm.tsx` (2 occ)

**DAG components** (4 files):
- `FlowPortNode.tsx` (2 occ), `TypedField.tsx` (5 occ)
- `ExternalInputPanel.tsx` (3 occ), `ForEachExpandedContainer.tsx` (1 occ)
- `CanvasJobControls.tsx` (1 occ)

**Canvas components** (1 file):
- `JobCard.tsx` (3 occ) — card backgrounds and borders

**Events/Logs** (2 files):
- `EventLog.tsx` (5 occ) — event row backgrounds and timestamps
- `LogSearchBar.tsx` — all text colors hardcoded for dark

**Other** (4 files):
- `JsonView.tsx` (17 occ) — JSON key/value colors, bracket colors
- `DiffView.tsx` (2 occ) — line number gutter
- `Breadcrumb.tsx` (2 occ) — separator and link colors
- `skeleton.tsx` (1 occ in ui/)

**Pages** (3 files):
- `JobDashboard.tsx` (1 occ), `JobDetailPage.tsx` (19 occ)
- `EditorPage.tsx` (10 occ), `CanvasPage.tsx` (10 occ)

---

### Phase 5: Status Colors & Semantic Tokens (Step 21)

#### Step 21: Audit status-colors.ts
**File:** `web/src/lib/status-colors.ts`

**4 occurrences of dark-only patterns.** The status color system uses Tailwind classes with opacity modifiers (`bg-blue-500/10`, `text-blue-400`). These need review:

- Some `text-*-400` colors are too pale on white. Switch to `text-*-600 dark:text-*-400`.
- Background opacity classes like `bg-*-500/10` may be too subtle on white. May need `bg-*-100 dark:bg-*-500/10` for better visibility.
- `STEP_PENDING_COLORS` already has some `dark:` variants — ensure consistency across all status types.

---

### Phase 6: Verification & Build (Steps 22-23)

#### Step 22: Visual verification
- Toggle between light and dark mode on every page
- Check each page against requirements R1-R8
- Use browser dev tools contrast checker on text elements
- Verify DAG edges, nodes, and canvas export in both modes
- Verify CodeMirror syntax highlighting in both modes
- Verify scrollbars on both modes
- Test modals/dialogs/dropdowns/tooltips in both modes

#### Step 23: Build and deploy
```bash
cd web && npm run build && cp -r dist/* ../src/stepwise/_web/
```

Commit with descriptive message.

---

## File Change Summary

| File | Category | Occurrences | Priority |
|---|---|---|---|
| `web/src/index.css` | Foundation | New vars + scrollbar fix | P0 |
| `web/src/hooks/useTheme.ts` | Foundation | New file | P0 |
| `web/src/components/dag/DagEdges.tsx` | SVG/Canvas | ~15 hardcoded oklch | P0 |
| `web/src/components/canvas/MiniDag.tsx` | SVG/Canvas | ~8 hardcoded hex | P0 |
| `web/src/components/canvas/DependencyArrows.tsx` | SVG/Canvas | 3 hardcoded hex | P0 |
| `web/src/components/dag/FlowDagView.tsx` | SVG/Canvas + TW | 16 TW + canvas export | P0 |
| `web/src/components/dag/ContainerPortEdges.tsx` | SVG | 2 low-opacity colors | P0 |
| `web/src/components/editor/YamlEditor.tsx` | CodeMirror | Theme hardcoded | P0 |
| `web/src/components/editor/FlowConfigPanel.tsx` | Tailwind | 16 | P1 |
| `web/src/components/editor/StepDefinitionPanel.tsx` | Tailwind | 29 | P1 |
| `web/src/pages/FlowsPage.tsx` | Tailwind | 19 | P1 |
| `web/src/components/dag/StepNode.tsx` | Tailwind | 19 | P1 |
| `web/src/components/jobs/StepDetailPanel.tsx` | Tailwind | 34 | P1 |
| `web/src/components/jobs/JobList.tsx` | Tailwind | 21 | P1 |
| `web/src/pages/SettingsPage.tsx` | Tailwind | 32 | P1 |
| `web/src/components/jobs/AgentStreamView.tsx` | Tailwind | 11 | P1 |
| `web/src/components/dag/DataFlowPanel.tsx` | Tailwind | 14 | P1 |
| `web/src/components/CommandPalette.tsx` | Tailwind | 15 | P1 |
| `web/src/components/JsonView.tsx` | Tailwind | 17 | P2 |
| `web/src/lib/status-colors.ts` | Semantic | 4 | P2 |
| ~35 remaining files | Tailwind | 1-10 each | P2 |

**Total files to modify:** ~55
**Total occurrences to fix:** ~419 dark-only Tailwind patterns + ~30 hardcoded SVG/canvas colors

---

## Testing Strategy

### Automated
```bash
# Ensure no build errors after all changes
cd web && npm run build

# Run existing frontend tests
cd web && npm run test

# Lint check
cd web && npm run lint
```

### Manual checklist (both light AND dark mode)

**Dashboard / Jobs list:**
- [ ] Job cards readable, borders visible, status badges have contrast
- [ ] Empty state text readable
- [ ] Hover states visible

**Job detail page:**
- [ ] Sidebar labels and values readable
- [ ] Tab headers and content areas properly themed
- [ ] Timeline bars and labels visible
- [ ] Agent stream output readable (tool calls, code blocks)
- [ ] Step detail panel fully themed

**DAG / Orchestrator view:**
- [ ] Edges visible (inactive, active, completed, loop)
- [ ] Node backgrounds and borders appropriate
- [ ] Pending nodes visible
- [ ] External input panel themed
- [ ] Data flow panel themed
- [ ] Canvas controls readable

**Flow editor:**
- [ ] YAML editor uses appropriate syntax theme
- [ ] Chat panel messages readable
- [ ] Step definition panel forms themed
- [ ] Flow config panel forms themed
- [ ] Registry browser cards themed
- [ ] File tree themed

**Flows page:**
- [ ] Flow list cards readable
- [ ] Flow detail panel and metadata badges themed
- [ ] DAG preview container themed

**Settings:**
- [ ] All form fields themed
- [ ] Model label badges readable
- [ ] Delete buttons visible

**Cross-cutting:**
- [ ] Modals/dialogs themed (check via Command Palette, Create Job, etc.)
- [ ] Dropdowns and selects themed
- [ ] Tooltips readable
- [ ] Scrollbars subtle and appropriate
- [ ] Focus rings visible
- [ ] PNG export uses correct theme colors

### Regression check
- [ ] Toggle to dark mode → verify no visual regressions on all pages above
- [ ] Verify theme persists across page reload
- [ ] Verify system preference detection on first load (clear localStorage)

### Build & bundle
```bash
cd web && npm run build && cp -r dist/* ../src/stepwise/_web/
```
