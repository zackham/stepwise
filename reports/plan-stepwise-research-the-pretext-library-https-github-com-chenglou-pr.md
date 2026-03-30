# Pretext Integration Evaluation for Stepwise Web UI

**Date:** 2026-03-30 (updated from 2026-03-29 initial draft)
**Library:** [@chenglou/pretext](https://github.com/chenglou/pretext) v0.0.3 (~15KB, zero deps, MIT)
**Author:** Cheng Lou (Midjourney, ex-Meta — creator of react-motion, ReasonML/ReScript contributor)
**Stars:** 16,600+ (in 4 days since release 2026-03-26)

---

## Overview

Pretext is a pure JavaScript library for multiline text measurement and layout that calculates text height and line breaks **without touching the DOM**. It replaces expensive DOM measurement operations (`getBoundingClientRect`, `offsetHeight`) — which trigger synchronous layout reflow — with a two-phase approach:

1. **`prepare(text, font)`** — one-time canvas-based segment measurement (~19ms for 500 texts). Uses `Intl.Segmenter` for word boundaries, handles CJK per-character breaking, Arabic/Thai/Myanmar/Khmer scripts, emoji width correction (Chrome/Firefox inflate emoji widths at small sizes on macOS), soft hyphens, URL-like runs, and kinsoku shori.
2. **`layout(prepared, maxWidth, lineHeight)`** — pure arithmetic, ~0.0002ms per call. Returns `{ height, lineCount }`. Zero DOM reads, zero canvas calls, zero string operations.

The `layout()` call is **300-600x faster** than DOM measurement. This makes it viable to call on every resize frame, scroll event, or container width change without layout thrash.

**Bundle:** ~15KB gzipped, zero dependencies, ESM only. MIT license.
**Browser support:** Chrome 87+, Safari 15.4+, Firefox 125+ (requires `Intl.Segmenter` + `OffscreenCanvas`).
**Accuracy:** Validated across 3 browsers × 4 fonts × 8 sizes × 8 widths × 30 texts, plus long-form corpus across Arabic, Chinese, Japanese, Korean, Thai, Khmer, Myanmar, Hindi, Urdu, and Hebrew.

### API Surface

```ts
// Fast path: height prediction (opaque prepared handle)
prepare(text: string, font: string, options?: { whiteSpace?: 'normal' | 'pre-wrap' }): PreparedText
layout(prepared: PreparedText, maxWidth: number, lineHeight: number): { height: number, lineCount: number }

// Rich path: line-by-line layout for custom rendering
prepareWithSegments(text, font, options?): PreparedTextWithSegments
layoutWithLines(prepared, maxWidth, lineHeight): { height, lineCount, lines: LayoutLine[] }
walkLineRanges(prepared, maxWidth, onLine: (line: LayoutLineRange) => void): number
layoutNextLine(prepared, start: LayoutCursor, maxWidth): LayoutLine | null

// Utilities
clearCache(): void
setLocale(locale?: string): void
```

**Performance (500-text batch):**

| Operation | Chrome | Safari |
|-----------|--------|--------|
| `prepare()` | 18.85ms | 18.00ms |
| `layout()` | 0.09ms | 0.12ms |
| DOM measurement | 4.05ms | 87.00ms |
| DOM interleaved | 43.50ms | 149.00ms |

### Architecture Internals (from source code review)

5 source files totaling ~94KB:

| File | Lines | Purpose |
|------|-------|---------|
| `analysis.ts` | ~750 | Text normalization, `Intl.Segmenter` word segmentation, CJK kinsoku, punctuation merging, URL/numeric run merging, Arabic/Myanmar/Khmer specializations, soft hyphen support |
| `measurement.ts` | ~200 | Canvas `measureText()`, per-font metric caching (`Map<font, Map<segment, SegmentMetrics>>`), emoji width correction, engine profile detection |
| `line-break.ts` | ~700 | CSS line-breaking: greedy algorithm with simple fast path (single-chunk) and chunk-based walker (pre-wrap/multi-chunk). Browser-specific tolerances: 0.005 Chromium/Gecko, 1/64 Safari/WebKit |
| `layout.ts` | ~500 | Public API orchestration, line text materialization, cursor management |
| `bidi.ts` | ~150 | Simplified bidirectional text metadata (UAX #9 subset, forked from pdf.js) |

Key design: parallel arrays (cache-friendly), opaque `PreparedText` via branded type, two preparation tiers (minimal for height-only, rich for rendering).

### CSS Behavior Targeted

Matches: `white-space: normal | pre-wrap`, `word-break: normal`, `overflow-wrap: break-word`, `line-break: auto`.

**Not supported:** `break-all`, `keep-all`, `strict`, `loose`, `anywhere`, `pre`, custom `tab-size`.

### Limitations

- Browser-only (requires `OffscreenCanvas` or DOM canvas) — no SSR
- `system-ui` font unsafe on macOS (canvas/DOM resolve to different SF Pro variants)
- Single font per `prepare()` call — no mixed inline formatting
- Font must be loaded before `prepare()` (`document.fonts.ready`)
- No automatic hyphenation (only manual soft hyphens `\u00AD`)
- No vertical text layout
- v0.0.3, pre-1.0 — API may evolve; `PreparedTextWithSegments` described as "unstable escape hatch"

### External Coverage

- Simon Willison wrote about it on simonwillison.net (March 29, 2026) with an interactive explainer
- Hacker News front page (item 47556290)
- VectoSolve: "15KB Library That Makes Text Layout 300x Faster"
- Cloudmagazin analysis: "Solving the 30-Year Browser Problem or Just Hype?"

---

## Assumptions (verified against codebase)

| # | Assumption | Verified |
|---|-----------|----------|
| A1 | AgentStreamView uses `@tanstack/react-virtual` with heuristic height estimation | Yes — `estimateSize: Math.ceil(seg.text.length / 80) * 20`, tool cards at 36px |
| A2 | VirtualizedLogView uses fixed 20px per line | Yes — `estimateSize: () => 20` in VirtualizedLogView.tsx:72 |
| A3 | DAG edges already use canvas `measureText()` with caching | Yes — `DagEdges.tsx:107-141`, `LABEL_FONT = "10px monospace"` |
| A4 | DAG nodes use fixed dimensions with CSS truncation | Yes — `NODE_WIDTH=240, NODE_HEIGHT=88` in `dag-layout.ts`, `truncate` class on labels |
| A5 | JsonView renders all children recursively without virtualization | Yes — `.map()` at every nesting level |
| A6 | `@tanstack/react-virtual` is already installed | Yes — `^3.13.23` in `package.json` |
| A7 | Monospace font used for code/logs | Yes — `font-mono` class throughout |
| A8 | No existing text measurement utility in `lib/` | Correct — only `DagEdges.tsx` has canvas measurement |
| A9 | Light/dark toggle exists | Yes — `useTheme.ts`. Font rendering unaffected. |

---

## Integration Point Analysis

### 1. Agent Stream Output Virtualization — HIGH PRIORITY

**Files:** `components/jobs/AgentStreamView.tsx:198-206`, `hooks/useAgentStream.ts`

**Current state:** Uses `@tanstack/react-virtual` with a character-count height estimator:
```ts
estimateSize: () => Math.max(1, Math.ceil(seg.text.length / 80)) * 20
```
Tool cards estimated at 36px. Post-render correction via `measureElement`.

**Problem:** Text segments with URLs, code identifiers, or long words don't wrap at character boundaries. The virtualizer overshoots or undershoots, causing visible jumps when scrolling. Search highlighting can reveal misalignment.

**How pretext helps:**
- `prepare()` each text segment as it arrives (amortized in streaming — one prepare per segment)
- `layout(prepared, containerWidth, lineHeight)` on mount and resize → exact pixel height
- Pass measured heights to virtualizer's `estimateSize`
- Use `pre-wrap` mode for agent output (whitespace is significant in code/logs)

**Complexity:** Medium
- Need `usePretextMeasure()` hook tracking container width via `ResizeObserver`
- Segments contain mixed content (prose in proportional font, code blocks in monospace) — need per-segment font detection
- Markdown rendering changes effective line width
- `prepare()` cost paid once per segment; `layout()` is free on resize

**Impact:** High — agent output is the primary view during job execution. Smooth scrolling directly affects perceived quality.

**Feasibility:** High — streaming architecture already processes segments individually, maps cleanly to pretext's per-text `prepare()` model.

**Caveat:** Markdown rendering means final DOM structure doesn't perfectly match raw text line breaks. The existing `measureElement` fallback corrects post-render, so pretext would be an improvement to the *estimate* quality, not a replacement for DOM measurement.

**Recommendation:** **Integrate.** The highest user-facing impact. Even imperfect estimates from pretext are better than character-count heuristics for proportional text.

---

### 2. Script/Log Virtualization — MEDIUM PRIORITY

**Files:** `components/logs/VirtualizedLogView.tsx:69-74`, `components/jobs/StepDetailPanel.tsx`

**Current state:** Fixed `estimateSize: () => 20` for all log lines. Rendered with `font-mono text-sm whitespace-pre-wrap`.

**Problem:** Multi-line logs (stack traces, JSON dumps, long paths) are severely underestimated. Auto-scroll-to-bottom overshoots. Wrapped lines cause visible layout shifts.

**How pretext helps:**
- `prepare()` each log line with monospace font string + `pre-wrap` mode
- `layout()` with container width → actual wrapped height
- Monospace is the simplest case (no kerning/ligature variance)

**Complexity:** Low-Medium — log lines are immutable once appended, `prepare()` runs once per line.

**Impact:** Medium — log viewing is secondary, but broken scroll anchoring during "follow tail" is annoying.

**Feasibility:** High — but **pretext isn't strictly necessary for monospace text.** For monospace, `Math.ceil(lineLength / charsPerRow) * lineHeight` where `charsPerRow = containerWidth / charWidth` is already very accurate. Pretext's sophisticated segmentation targets proportional fonts.

**Recommendation:** **Pretext not required.** Improve the existing fixed-20px estimator with a simple monospace character-width calculation. Pretext adds ~15KB for negligible accuracy benefit over arithmetic for monospace text. However, if pretext is already loaded for agent stream (P1), using it here adds zero bundle cost and handles edge cases (tab characters, emoji in logs, non-ASCII characters) more robustly.

---

### 3. DAG Edge Labels — MEDIUM-HIGH PRIORITY (cleanup)

**Files:** `components/dag/DagEdges.tsx:107-141`, `lib/dag-layout.ts`

**Current state:** Canvas `measureText()` with caching:
```ts
const LABEL_FONT = "10px monospace"
// Fallback: text.length * 6.5 + 12
```
Labels positioned at edge midpoints. No overlap detection.

**Problem:** When multiple fields flow on the same edge, labels overlap and become unreadable. Cache never invalidated (even on font/theme changes). Fallback estimate crude for variable-width text.

**How pretext helps:**
- Replace `measureLabelWidth()` with pretext `prepare()` + `layout()` for width-constrained labels
- Use `layoutWithLines()` to detect when a label needs multiple lines → trigger abbreviated display
- Compute bounding boxes for overlap detection → offset vertically when labels collide

**Complexity:** Low — the existing canvas measurement pattern is directly replaceable. Overlap detection is the real work, independent of pretext.

**Impact:** Medium-High — DAG readability is critical. Overlapping labels are a real problem on complex flows.

**Feasibility:** High — current code already does canvas measurement; pretext is a strict upgrade.

**Recommendation:** **Integrate if pretext is adopted for P1.** If already in the bundle, replacing the hand-rolled canvas measurement with pretext is cleaner and more robust. If pretext is not adopted, the existing code works and a direct fix for overlap detection doesn't require pretext.

---

### 4. Step Node Text — LOW-MEDIUM PRIORITY

**Files:** `components/dag/StepNode.tsx:57-99`

**Current state:** Hardcoded character limits:
- Script commands: 36 chars
- When conditions: 30 chars
- Executor subtitles: varies

CSS `truncate` class as safety net.

**Problem:** Fixed character limits don't account for glyph width. "iiiiiii" truncates at the same point as "WWWWWWW".

**Recommendation:** **Skip.** Fixed-size nodes produce cleaner layouts. Most step names are short and fit. Hover tooltip shows full name. If truncation becomes a complaint, a direct canvas `measureText()` call is simpler.

---

### 5. JSON/Data Panels — LOW PRIORITY

**Files:** `components/JsonView.tsx:16-29`, `components/jobs/HandoffEnvelopeView.tsx`

**Current state:** Block strings collapse if >12 lines. Max height `max-h-[16rem]` with gradient fade.

**Recommendation:** **Skip.** Current heuristics work. If large arrays become a pain point, "show first 50 items" with "load more" is much simpler and handles 99% of cases.

---

### 6. Editor Chat Messages — LOW PRIORITY

**Files:** `components/editor/ChatMessages.tsx`

**Current state:** Lightweight markdown renderer, no virtualization, typically <100 messages.

**Recommendation:** **Skip.** Not performance-critical, no measurement bottleneck.

---

## Requirements with Acceptance Criteria

### R1: Pretext integration hook

Create `web/src/hooks/usePretextMeasure.ts` wrapping the pretext API for React.

**Acceptance criteria:**
- [ ] Hook accepts `(texts: string[], font: string, options?)` and returns `{ measure: (index, maxWidth, lineHeight) => { height, lineCount } }`
- [ ] `prepare()` called once per unique text (memoized by content hash)
- [ ] `layout()` called on container resize (via `ResizeObserver`)
- [ ] Handles font loading race (re-prepares after `document.fonts.ready`)
- [ ] `clearCache()` exposed for memory management
- [ ] Works in test environment (jsdom) with graceful fallback to character-count estimation

### R2: Agent stream accurate virtualization

Replace character-count estimator in `AgentStreamView.tsx` with pretext-measured heights.

**Acceptance criteria:**
- [ ] Each text segment measured via pretext on arrival
- [ ] Heights recalculated on container resize without re-preparing
- [ ] Scroll position preserved during resize
- [ ] No visible scroll jank when scrolling through mixed text/tool-card content
- [ ] Performance: <5ms total for 500 segments on resize (layout-only)
- [ ] Fallback to current estimator if pretext fails to load

### R3: Log viewer height improvement

Improve height estimation in `VirtualizedLogView.tsx` (pretext optional — monospace arithmetic may suffice).

**Acceptance criteria:**
- [ ] Each log line height estimated based on content length and container width (not fixed 20px)
- [ ] "Follow tail" auto-scroll anchors correctly for wrapped lines
- [ ] Heights recalculated on container resize
- [ ] Performance: <2ms for 1000 log lines on resize

### R4: DAG label measurement upgrade

Replace `measureLabelWidth()` in `DagEdges.tsx` with pretext or improved measurement.

**Acceptance criteria:**
- [ ] Label width measured accurately (replacing crude `text.length * 6.5 + 12` fallback)
- [ ] Label overlap detection: when two labels on the same edge collide, offset vertically
- [ ] Visual regression: labels render identically for non-overlapping cases
- [ ] Cache invalidation on font/theme changes

---

## Implementation Steps

### Phase 1: Foundation

**Step 1: Install pretext and create measurement hook**
- `cd web && npm install @chenglou/pretext`
- Create `web/src/hooks/usePretextMeasure.ts` — React hook wrapping prepare/layout with ResizeObserver integration
- Create `web/src/lib/pretext-utils.ts` — singleton prepare cache, font string builder from computed styles
- Files: `web/src/hooks/usePretextMeasure.ts` (new), `web/src/lib/pretext-utils.ts` (new)

**Step 2: Add test infrastructure**
- Mock pretext in jsdom (no `OffscreenCanvas`) — fallback to character-count estimation
- Tests for the hook: prepare caching, resize re-layout, fallback behavior
- Files: `web/src/test/pretext-mock.ts` (new), `web/src/hooks/__tests__/usePretextMeasure.test.ts` (new)

### Phase 2: High-Impact Integration

**Step 3: Agent stream virtualization**
- Import `usePretextMeasure` in `AgentStreamView.tsx`
- Replace `estimateSize` callback with pretext-measured heights
- Add `ResizeObserver` to recalculate on container width change
- Preserve scroll position on resize
- Files: `web/src/components/jobs/AgentStreamView.tsx` (edit)

**Step 4: Log viewer height estimation**
- Improve `VirtualizedLogView.tsx` — either use pretext (if already loaded for agent stream, zero added bundle cost) or implement monospace character-width arithmetic
- Fix "follow tail" scroll anchoring with accurate heights
- Files: `web/src/components/logs/VirtualizedLogView.tsx` (edit)

### Phase 3: DAG Polish

**Step 5: DAG edge label measurement**
- Replace `measureLabelWidth()` in `DagEdges.tsx` with pretext-based measurement
- Add label bounding box computation for overlap detection
- Implement vertical offset for overlapping labels on same edge
- Files: `web/src/components/dag/DagEdges.tsx` (edit)

### Phase 4: Optional (defer unless time permits)

**Step 6:** Step node width-aware truncation (`StepNode.tsx`)
**Step 7:** JSON panel height-based collapse threshold (`JsonView.tsx`)

---

## Testing Strategy

### Unit tests
```bash
cd web && npm run test -- --run src/hooks/__tests__/usePretextMeasure.test.ts
```
- Prepare cache deduplication (same text → same prepared handle)
- Layout returns correct height for known inputs (mocked in jsdom)
- Fallback estimator activates when canvas unavailable
- Font string builder matches CSS declaration format

### Component tests
```bash
cd web && npm run test -- --run src/components/jobs/__tests__/AgentStreamView.test.tsx
cd web && npm run test -- --run src/components/logs/__tests__/VirtualizedLogView.test.tsx
```
- Virtualizer receives measured heights (not fixed estimates)
- Resize triggers re-layout without re-prepare
- Scroll position stable after resize

### Integration / visual verification
```bash
cd web && npm run dev
# Open localhost:5173, run a job with agent output, verify:
# - Smooth scrolling through long agent output
# - No visible jumps when resizing browser window
# - Log viewer follows tail correctly for wrapped lines
# - DAG labels don't overlap on complex flows
```

### Regression
```bash
cd web && npm run test          # all vitest tests pass
cd web && npm run lint          # no lint errors
uv run pytest tests/            # backend unaffected
```

---

## Decision Matrix

| Integration Point | Complexity | Impact | Priority | Recommendation |
|---|---|---|---|---|
| Agent stream virtualization | Medium | High | **P1** | **Integrate** — highest user-facing impact |
| Log viewer height estimation | Low-Med | Medium | **P2** | **Improve** — pretext optional, monospace arithmetic may suffice |
| DAG edge labels | Low | Med-High | **P3** | **Integrate if P1 adopted** — replaces existing canvas code |
| Step node truncation | Low | Low-Med | **P4** | Defer |
| JSON panel collapse | Low | Low | **P5** | Skip |
| Editor chat | Low | Low | **—** | Skip |

---

## Overall Recommendation

**Adopt pretext for agent stream virtualization (P1), then leverage across other integration points.**

The rationale:

1. **Agent stream is the highest-impact target.** It's the primary view during job execution, uses proportional text (where pretext excels over heuristics), and already has the virtualizer infrastructure. The character-count estimator produces visible scroll jank that pretext eliminates.

2. **Once in the bundle (~15KB), marginal cost of additional uses is zero.** Log viewer and DAG labels can use pretext for free. Even where monospace arithmetic would suffice (logs), pretext handles edge cases (emoji, tabs, non-ASCII) more robustly.

3. **The existing canvas measurement in DagEdges.tsx is a natural replacement target.** Pretext's caching and segmentation are more robust than the hand-rolled solution.

4. **Risk is manageable.** Wrap pretext in our own hook with a fallback estimator for jsdom/test environments. Pin the version. The API surface we need (`prepare` + `layout`) is the stable core, not the "unstable escape hatch" rich path.

### When to reconsider scope

- If pretext reaches v1.0 with the rich path stabilized → consider `layoutWithLines()` for chat bubble shrinkwrap or editorial layouts
- If the web UI adds rich text editing → pretext becomes essential (its sweet spot)
- If agent output regularly exceeds 1000+ segments → pretext's sub-microsecond layout becomes critical for scroll performance

---

## Appendix: Pretext Demo Highlights

From [chenglou.me/pretext](https://chenglou.me/pretext/):

1. **Chat bubble shrinkwrap** — Binary search with `walkLineRanges()` to find tightest width maintaining same line count. Eliminates wasted whitespace in message bubbles.
2. **Masonry layout** — Card heights from `layout()` instead of DOM reads, with scroll-based virtualization.
3. **Editorial engine** — Multi-column text flow around SVG obstacles using `layoutNextLine()` with variable widths per line. Zero DOM measurements.
4. **Dynamic layout** — Fixed-height editorial spread with obstacle-aware title routing, live reflow on rotation.
5. **Justification comparison** — CSS vs greedy hyphenation vs Knuth-Plass paragraph layout side-by-side.
6. **Rich text** — Inline formatting (code spans, links, chips) with pill-aware wrapping.

Community demos at somnai-dreams.github.io/pretext-demos/.

### Development methodology
Built by iterating with AI (Claude and Codex) against browser ground-truth verification loops. Automated accuracy checks at various widths across every script, emoji, and RTL combination using actual browser rendering as the oracle. Based on Sebastian Markbage's text-layout research prototype.
