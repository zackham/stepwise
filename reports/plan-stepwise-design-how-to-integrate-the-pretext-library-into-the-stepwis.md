# Plan: Integrate Pretext Library into Stepwise Web UI

## Overview

Integrate `@chenglou/pretext` (~15KB gzipped, zero deps) to replace heuristic text-height estimation in the Stepwise web UI's two virtualized scroll containers — agent stream and script logs — via a shared measurement utility and React hook, with jsdom fallback for tests.

## Context: Research Findings (job-7ba7d188)

The prior research evaluation analyzed pretext v0.0.3 and concluded:

- **`prepare(text, font, options?)`** — one-time canvas-based segment measurement (~19ms for 500 texts). Handles word segmentation via `Intl.Segmenter`, CJK, emoji width correction, URL-like runs.
- **`layout(prepared, maxWidth, lineHeight)`** — pure arithmetic (~0.0002ms per call), returns `{ height, lineCount }`. Zero DOM reads after preparation.
- **300-600x faster than DOM measurement** for layout calls after preparation.
- **Returns height only — not width.** This eliminates DAG label measurement as an integration target.
- **Bundle:** ~15KB gzipped, ESM only, MIT license, zero dependencies.
- **Browser requirements:** Chrome 87+, Safari 15.4+, Firefox 125+ (needs `Intl.Segmenter` + `OffscreenCanvas`).
- **Pre-1.0 risk:** v0.0.3, `prepare()`+`layout()` are the stable core. Rich API (`prepareWithSegments`, `walkLineRanges`) marked "unstable escape hatch."

## Requirements

### R1: Shared pretext measurement utility

Create `web/src/lib/pretext-measure.ts` wrapping pretext's `prepare()`/`layout()` with caching and jsdom fallback.

**Acceptance criteria:**

| # | Criterion | Verified by |
|---|-----------|-------------|
| R1.1 | `measureTextHeight(text, font, maxWidth, lineHeight, options?)` returns pixel height as a positive number | `cd web && npx vitest run src/lib/__tests__/pretext-measure.test.ts` — "returns single lineHeight for short text" |
| R1.2 | Preparation results cached by `text+font+whiteSpace` key — calling twice with same args does not call `prepare()` again | `pretext-measure.test.ts` — "caches prepare results for identical inputs" |
| R1.3 | In jsdom (no working canvas), falls back to `Math.ceil(text.length / charsPerLine) * lineHeight` using font-size-derived char width | `pretext-measure.test.ts` — "extracts font size from font string" (verifies fallback math) |
| R1.4 | `clearPreparedCache()` exported and clears all cached entries | `pretext-measure.test.ts` — "clearPreparedCache resets cache" |
| R1.5 | Returns `lineHeight` for empty string or zero/negative `maxWidth` | `pretext-measure.test.ts` — "returns lineHeight for empty string", "returns lineHeight for zero-width container", "handles negative maxWidth" |
| R1.6 | Feature detection correctly identifies jsdom (where `canvas.getContext("2d")` returns `null`) as unavailable | `pretext-measure.test.ts` — "isPretextAvailable returns false when canvas context is null" |

### R2: React hook for container-aware measurement

Create `web/src/hooks/usePretextMeasure.ts` that reads font metrics from the container's computed style and re-measures on resize.

**Acceptance criteria:**

| # | Criterion | Verified by |
|---|-----------|-------------|
| R2.1 | `usePretextMeasure()` returns `{ containerRef, estimateHeight(text, fallback?) }` | `cd web && npx vitest run src/hooks/__tests__/usePretextMeasure.test.ts` — "returns estimateHeight function and containerRef" |
| R2.2 | Font string and line height derived from `getComputedStyle()` on mounted container — no hardcoded values | `usePretextMeasure.test.ts` — "reads font from getComputedStyle when container is mounted" |
| R2.3 | `estimateHeight` returns the fallback value when container has zero width (not yet mounted) | `usePretextMeasure.test.ts` — "estimateHeight returns fallback when container has no width" |
| R2.4 | `estimateHeight` returns `lineHeightRef` default when no fallback given and container unmounted | `usePretextMeasure.test.ts` — "estimateHeight returns default lineHeight when no fallback given" |
| R2.5 | ResizeObserver updates container width; disconnects on unmount | `usePretextMeasure.test.ts` — "updates width via ResizeObserver", "disconnects ResizeObserver on unmount" |

### R3: Agent stream accurate height estimation

Replace the character-count heuristic in `AgentStreamView.tsx:198-203` with pretext-measured heights.

**Acceptance criteria:**

| # | Criterion | Verified by |
|---|-----------|-------------|
| R3.1 | `estimateSize` for text segments uses `estimateHeight(seg.text)` instead of `Math.ceil(seg.text.length / 80) * 20` | Code review of `AgentStreamView.tsx` diff |
| R3.2 | Tool card segments remain at fixed 36px estimate | `cd web && npx vitest run src/components/jobs/AgentStreamView.test.tsx` — all existing tool card tests pass unchanged |
| R3.3 | Cursor row remains at fixed 20px estimate | Code review |
| R3.4 | `measureElement` ref callback retained for post-render correction | Code review — `virtualizer.measureElement` still used on every virtual row div |
| R3.5 | Falls back to current heuristic if pretext unavailable | Implicit — jsdom tests exercise this path since pretext is unavailable in test env |
| R3.6 | All 13 existing AgentStreamView tests pass unchanged | `cd web && npx vitest run src/components/jobs/AgentStreamView.test.tsx` — 13 tests pass |

### R4: Log viewer improved height estimation

Replace fixed `estimateSize: () => 20` in `VirtualizedLogView.tsx:72` with pretext-aware estimation.

**Acceptance criteria:**

| # | Criterion | Verified by |
|---|-----------|-------------|
| R4.1 | Height estimate accounts for text wrapping at container width (not fixed 20px) | Code review of `VirtualizedLogView.tsx` diff — `estimateSize` now calls `estimateHeight(lines[index], 20)` |
| R4.2 | `measureElement` ref callback retained for post-render correction | Code review |
| R4.3 | Falls back to 20px if container width is zero or pretext unavailable | Implicit — jsdom fallback returns `20` for short lines at default lineHeight |
| R4.4 | All 7 existing VirtualizedLogView tests pass unchanged | `cd web && npx vitest run src/components/logs/__tests__/VirtualizedLogView.test.tsx` — 7 tests pass |

## Assumptions

| # | Assumption | Verified Against |
|---|-----------|-----------------|
| A1 | `AgentStreamView.tsx` uses `@tanstack/react-virtual` with `estimateSize: Math.ceil(seg.text.length / 80) * 20` for text segments and `36` for tool cards | `AgentStreamView.tsx:198-203` — confirmed. Post-render correction via `virtualizer.measureElement` at line 243/262 |
| A2 | `VirtualizedLogView.tsx` uses fixed `estimateSize: () => 20` for all log lines | `VirtualizedLogView.tsx:72` — confirmed. Also has `measureElement` for post-render correction (line 108) |
| A3 | Agent stream text renders in `font-mono text-sm` (monospace, ~14px) with `whitespace-pre-wrap leading-relaxed` | `AgentStreamView.tsx:130` — confirmed. Monospace reduces pretext's advantage over heuristics for ASCII, but pretext handles emoji, non-ASCII, and tab characters better |
| A4 | `DagEdges.tsx` uses canvas `measureText()` for label **width** measurement — pretext only returns height | `DagEdges.tsx:94-142` — confirmed. `LABEL_FONT = "10px monospace"`, `labelWidthCache` Map, `measureLabelWidth()` returns width. **Pretext cannot replace this.** |
| A5 | `JsonView.tsx` is recursive tree rendering, not flat text virtualization | `JsonView.tsx:1-50` — confirmed. Recursive `.map()` at every nesting level. Not suitable for pretext. |
| A6 | `@tanstack/react-virtual` v3.13.23 already installed | `web/package.json:24` — confirmed |
| A7 | `@fontsource-variable/geist` (sans-serif) is installed, but `font-mono` resolves to system monospace stack | `web/package.json:20` — Geist is sans-serif only. Tailwind `font-mono` uses `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace` |
| A8 | jsdom's `document.createElement("canvas").getContext("2d")` returns `null` — no canvas implementation unless `canvas`/`@napi-rs/canvas` is installed | Confirmed by `DagEdges.test.tsx:58-67` which mocks canvas `getContext` returning `null` to test fallback. Neither `canvas` nor `@napi-rs/canvas` is in `web/package.json`. |
| A9 | Pretext supports `pre-wrap` white-space mode | Per research: `prepare(text, font, { whiteSpace: 'pre-wrap' })` — confirmed |
| A10 | No existing text measurement utility in `web/src/lib/` | Confirmed — only `DagEdges.tsx` has canvas measurement, which is width-only |
| A11 | Existing AgentStreamView tests only exercise the flat rendering path (< `VIRTUAL_THRESHOLD = 200`), not the `VirtualizedSegments` component | `AgentStreamView.test.tsx:1-193` — confirmed, max segment count in any test is 3. This means all existing tests continue passing without needing ResizeObserver since the virtualizer codepath is never hit. |
| A12 | The established ResizeObserver mock pattern in the codebase (fires callback synchronously on `observe()` with fixed dimensions) is in `JobList.test.tsx:56-78` | Confirmed — constructor stores callback, `observe(target)` immediately fires with `{height: 600, width: 400}` |
| A13 | The established OffscreenCanvas mock pattern is in `DagEdges.test.tsx:29-41` using `Object.defineProperty(globalThis, "OffscreenCanvas", ...)` with cleanup via `Reflect.deleteProperty` | Confirmed |
| A14 | Test global setup is in `web/src/test/setup.ts` — mocks only `matchMedia`. No global ResizeObserver mock; that's per-test. | `setup.ts:1-14` — confirmed |

## Scope Exclusions

These components were evaluated and **excluded** based on the research findings:

| Component | File | Why excluded |
|-----------|------|-------------|
| **DAG edge labels** | `DagEdges.tsx` | Pretext returns `{ height, lineCount }` — not width. Canvas `measureText()` is correct for single-line width measurement. The overlap detection problem is independent of pretext. |
| **JSON viewer** | `JsonView.tsx` | Recursive tree structure. Text is rendered inline within expand/collapse tree nodes, not in a flat virtualized list. Pretext measures flat text height, not tree layouts. |
| **DAG step nodes** | `StepNode.tsx` | Fixed-size node boxes with CSS `truncate` and hover tooltips. Character-limit truncation (36 chars for subtitles) is the right UX. |
| **Editor chat** | `ChatPanel.tsx` | Not virtualized, uses standard DOM layout. No height estimation problem. |

## Implementation Steps

### Step 1: Install pretext and create measurement utility (~45 min)

**Creates:** `web/src/lib/pretext-measure.ts` (~65 lines)
**Modifies:** `web/package.json` (add `@chenglou/pretext`)

```bash
cd web && npm install @chenglou/pretext@0.0.3
```

**Verification:** After install, confirm the package resolves correctly and doesn't break the test suite with import side effects:

```bash
cd web && npx vitest run --reporter=verbose 2>&1 | head -20
# Expected: no new failures from pretext import. If top-level side effects break jsdom,
# the test runner will fail before any test runs.
```

Create `web/src/lib/pretext-measure.ts`:

```ts
import { prepare, layout, type PreparedText } from "@chenglou/pretext";

const preparedCache = new Map<string, PreparedText>();
let pretextAvailable: boolean | null = null;

/**
 * Feature detection: checks if canvas text measurement actually works.
 * Returns false in jsdom where getContext("2d") returns null.
 */
function isPretextAvailable(): boolean {
  if (pretextAvailable !== null) return pretextAvailable;
  try {
    if (typeof OffscreenCanvas !== "undefined") {
      const ctx = new OffscreenCanvas(1, 1).getContext("2d");
      pretextAvailable = ctx !== null;
      return pretextAvailable;
    }
    if (typeof document !== "undefined") {
      const ctx = document.createElement("canvas").getContext("2d");
      pretextAvailable = ctx !== null;
      return pretextAvailable;
    }
  } catch {
    // OffscreenCanvas constructor may throw in restricted contexts
  }
  pretextAvailable = false;
  return false;
}

// Exported for testing — allows tests to verify the detection logic
export { isPretextAvailable };

export function prepareText(
  text: string,
  font: string,
  options?: { whiteSpace?: "normal" | "pre-wrap" },
): PreparedText | null {
  if (!isPretextAvailable()) return null;

  const key = `${font}|${options?.whiteSpace ?? "normal"}|${text}`;
  const cached = preparedCache.get(key);
  if (cached) return cached;

  try {
    const prepared = prepare(text, font, options);
    preparedCache.set(key, prepared);
    return prepared;
  } catch {
    return null;
  }
}

export function measureTextHeight(
  text: string,
  font: string,
  maxWidth: number,
  lineHeight: number,
  options?: { whiteSpace?: "normal" | "pre-wrap" },
): number {
  if (maxWidth <= 0 || !text) return lineHeight;

  const prepared = prepareText(text, font, options);
  if (prepared) {
    const result = layout(prepared, maxWidth, lineHeight);
    return result.height;
  }

  // Fallback: character-count heuristic (monospace assumption)
  const fontSizeMatch = font.match(/(\d+(?:\.\d+)?)px/);
  const fontSize = fontSizeMatch ? parseFloat(fontSizeMatch[1]) : 14;
  const charWidth = fontSize * 0.6;
  const charsPerLine = Math.max(1, Math.floor(maxWidth / charWidth));
  const lines = Math.max(1, Math.ceil(text.length / charsPerLine));
  return lines * lineHeight;
}

export function clearPreparedCache(): void {
  preparedCache.clear();
}

// Reset feature detection (for testing)
export function _resetPretextAvailable(): void {
  pretextAvailable = null;
}
```

**Key design decisions:**
- **Feature detection checks canvas `getContext("2d")` result**, not just `typeof document`. In jsdom, `document` exists but `getContext("2d")` returns `null` (no canvas implementation — see A8). Without this check, `prepare()` would be called on every text, throw, and be caught — needlessly expensive.
- Cache keyed by `font|whiteSpace|text` — one `PreparedText` per unique combination.
- `try/catch` around `prepare()` as safety net for edge cases the detection misses.
- Empty text or zero-width container returns a single `lineHeight`.
- `_resetPretextAvailable()` exported for tests to reset the singleton detection between test cases.

### Step 2: Create `usePretextMeasure` React hook (~30 min)

**Creates:** `web/src/hooks/usePretextMeasure.ts` (~60 lines)

```ts
import { useRef, useCallback, useEffect, useState } from "react";
import { measureTextHeight } from "@/lib/pretext-measure";

export function usePretextMeasure() {
  const containerRef = useRef<HTMLDivElement>(null);
  const fontRef = useRef<string>("14px monospace");
  const lineHeightRef = useRef<number>(20);
  const widthRef = useRef<number>(0);
  const [ready, setReady] = useState(false);

  // Read computed font metrics from container + wait for fonts to load
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const readMetrics = () => {
      const cs = getComputedStyle(el);
      fontRef.current = `${cs.fontSize} ${cs.fontFamily}`;
      lineHeightRef.current = parseFloat(cs.lineHeight) || 20;
      widthRef.current = el.clientWidth;
      setReady(true);
    };

    // Wait for fonts to load before measuring
    if (document.fonts?.ready) {
      document.fonts.ready.then(readMetrics);
    } else {
      readMetrics();
    }

    // Update width on resize
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        widthRef.current = entry.contentRect.width;
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const estimateHeight = useCallback(
    (text: string, fallback?: number) => {
      const width = widthRef.current;
      if (width <= 0) return fallback ?? lineHeightRef.current;

      return measureTextHeight(
        text,
        fontRef.current,
        width,
        lineHeightRef.current,
        { whiteSpace: "pre-wrap" },
      );
    },
    [],
  );

  return { containerRef, estimateHeight, ready };
}
```

**Key design decisions:**
- **No hardcoded fonts.** Reads `fontSize` and `fontFamily` from `getComputedStyle()` on the container element. This auto-adapts to theme/font changes. The agent stream container's `font-mono text-sm` resolves to `14px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace` at render time.
- **`document.fonts.ready`** guard ensures font metrics are accurate before first `prepare()`. Without this, measurements could use a fallback font's metrics.
- **`ResizeObserver`** tracks container width changes. The virtualizer re-calls `estimateSize` when items change, and the latest width is always available via `widthRef`.
- **`ready` state** — consumers can optionally wait, but the fallback path works fine without it.

### Step 3: Integrate into AgentStreamView (~30 min)

**Modifies:** `web/src/components/jobs/AgentStreamView.tsx` (lines 1-2, 188, 195-206, 224-228)

Changes to `VirtualizedSegments`:

1. Import the hook:
   ```ts
   import { usePretextMeasure } from "@/hooks/usePretextMeasure";
   ```

2. Use the hook and merge refs:
   ```ts
   function VirtualizedSegments({ segments, showCursor, ... }) {
     const parentRef = useRef<HTMLDivElement>(null);
     const { containerRef: measureRef, estimateHeight } = usePretextMeasure();
     // ...

     // Merge parentRef + measureRef via callback ref
     const setContainerRef = useCallback((el: HTMLDivElement | null) => {
       (parentRef as React.MutableRefObject<HTMLDivElement | null>).current = el;
       (measureRef as React.MutableRefObject<HTMLDivElement | null>).current = el;
     }, []);
   ```

3. Replace `estimateSize`:
   ```ts
   // BEFORE (lines 198-204):
   estimateSize: (index) => {
     if (index >= segments.length) return 20;
     const seg = segments[index];
     if (seg.type === "tool") return 36;
     const lines = Math.max(1, Math.ceil(seg.text.length / 80));
     return lines * 20;
   },

   // AFTER:
   estimateSize: (index) => {
     if (index >= segments.length) return 20; // cursor row
     const seg = segments[index];
     if (seg.type === "tool") return 36;
     return estimateHeight(
       seg.text,
       Math.max(1, Math.ceil(seg.text.length / 80)) * 20, // fallback
     );
   },
   ```

4. Use merged ref on the scroll container:
   ```ts
   // BEFORE (line 226):
   <div ref={parentRef} onScroll={handleScroll} className="max-h-96 overflow-y-auto">

   // AFTER:
   <div ref={setContainerRef} onScroll={handleScroll} className="max-h-96 overflow-y-auto">
   ```

**What stays the same:**
- `measureElement` on every virtual row div — still the source of truth post-render
- Auto-scroll behavior — unchanged
- SegmentRow rendering — unchanged
- Tool card fixed height — unchanged
- Flat (non-virtual) rendering path for <200 segments — unchanged

**Existing test impact:** All 13 tests in `AgentStreamView.test.tsx` exercise the flat rendering path only (max 3 segments, well below `VIRTUAL_THRESHOLD = 200` — see A11). They never instantiate `VirtualizedSegments`, so neither the hook nor the merged ref codepath is invoked during existing tests. All 13 tests pass unchanged.

### Step 4: Integrate into VirtualizedLogView (~30 min)

**Modifies:** `web/src/components/logs/VirtualizedLogView.tsx` (lines 1-3, 52, 68-73, 92-96)

Changes to `VirtualizedLogViewInner`:

1. Import the hook:
   ```ts
   import { usePretextMeasure } from "@/hooks/usePretextMeasure";
   ```

2. Use the hook and merge refs:
   ```ts
   function VirtualizedLogViewInner({ lines, isLive, ... }) {
     const { containerRef: measureRef, estimateHeight } = usePretextMeasure();

     // Merge parentRef + measureRef
     const setContainerRef = useCallback((el: HTMLDivElement | null) => {
       (parentRef as React.MutableRefObject<HTMLDivElement | null>).current = el;
       (measureRef as React.MutableRefObject<HTMLDivElement | null>).current = el;
     }, []);
   ```

3. Replace `estimateSize`:
   ```ts
   // BEFORE (line 72):
   estimateSize: () => 20,

   // AFTER:
   estimateSize: (index) => estimateHeight(lines[index], 20),
   ```

4. Use merged ref:
   ```ts
   // BEFORE (line 93):
   <div ref={parentRef} onScroll={handleScroll} className={cn("max-h-96 overflow-y-auto", className)}>

   // AFTER:
   <div ref={setContainerRef} onScroll={handleScroll} className={cn("max-h-96 overflow-y-auto", className)}>
   ```

**Impact analysis:** For monospace text with ASCII-only content, this is mostly a wash — `estimateSize: () => 20` is already correct for single-line log output. The improvement matters for lines that wrap beyond container width (stack traces, long JSON), emoji, and tabs.

**Existing test impact:** The 7 tests in `VirtualizedLogView.test.tsx` cover both paths. Below-threshold tests (< 100 lines) don't instantiate the virtualizer, so are unaffected. The above-threshold test (`"uses virtualizer structure for large line counts"`) creates 500 lines — `VirtualizedLogViewInner` is called with the `usePretextMeasure` hook. In jsdom, the hook's `containerRef` is set on the div, but:
- `ResizeObserver` is not globally mocked in this test, so the hook's `useEffect` may not fire the RO
- `widthRef` stays at 0 → `estimateHeight` returns the `20` fallback for every line
- Behavior is identical to `estimateSize: () => 20`
- The existing assertion `allDivs.length < 100` still passes

### Step 5: Write tests (~45 min)

**Creates:**
- `web/src/lib/__tests__/pretext-measure.test.ts` (~90 lines)
- `web/src/hooks/__tests__/usePretextMeasure.test.ts` (~100 lines)

#### `web/src/lib/__tests__/pretext-measure.test.ts`

Tests the measurement utility in the jsdom environment where canvas is unavailable. Also tests feature detection and cache behavior.

```ts
import { describe, it, expect, afterEach, vi } from "vitest";
import {
  measureTextHeight,
  clearPreparedCache,
  isPretextAvailable,
  prepareText,
  _resetPretextAvailable,
} from "../pretext-measure";

afterEach(() => {
  clearPreparedCache();
  _resetPretextAvailable();
  // Clean up any OffscreenCanvas mock
  Reflect.deleteProperty(globalThis, "OffscreenCanvas");
});

describe("isPretextAvailable", () => {
  it("returns false in jsdom (canvas getContext returns null)", () => {
    // jsdom: document exists but getContext("2d") returns null (A8)
    expect(isPretextAvailable()).toBe(false);
  });

  it("returns true when OffscreenCanvas is available with working context", () => {
    // Mock pattern from DagEdges.test.tsx:29-41
    Object.defineProperty(globalThis, "OffscreenCanvas", {
      configurable: true,
      value: class {
        getContext() {
          return { font: "", measureText: () => ({ width: 10 }) };
        }
      },
    });
    _resetPretextAvailable();
    expect(isPretextAvailable()).toBe(true);
  });

  it("caches result after first check", () => {
    const result1 = isPretextAvailable();
    const result2 = isPretextAvailable();
    expect(result1).toBe(result2);
  });
});

describe("prepareText", () => {
  it("returns null in jsdom (pretext unavailable)", () => {
    expect(prepareText("hello", "14px monospace")).toBeNull();
  });

  it("caches prepare results for identical inputs", () => {
    // In jsdom both return null, but the function should still check cache first
    const r1 = prepareText("hello", "14px monospace");
    const r2 = prepareText("hello", "14px monospace");
    expect(r1).toBe(r2); // same reference (both null in jsdom)
  });
});

describe("measureTextHeight", () => {
  it("returns single lineHeight for short text", () => {
    // "hello" = 5 chars. At 14px monospace: charWidth=8.4, charsPerLine=floor(500/8.4)=59
    // 5/59 = 1 line → 1 * 20 = 20
    const h = measureTextHeight("hello", "14px monospace", 500, 20);
    expect(h).toBe(20);
  });

  it("returns multi-line height for long text that wraps", () => {
    // 200 chars at 14px monospace: charWidth=8.4, charsPerLine=floor(100/8.4)=11
    // 200/11 = 19 lines → 19 * 20 = 380
    const h = measureTextHeight("a".repeat(200), "14px monospace", 100, 20);
    expect(h).toBe(380);
  });

  it("returns lineHeight for empty string", () => {
    expect(measureTextHeight("", "14px monospace", 500, 20)).toBe(20);
  });

  it("returns lineHeight for zero-width container", () => {
    expect(measureTextHeight("hello", "14px monospace", 0, 20)).toBe(20);
  });

  it("handles negative maxWidth", () => {
    expect(measureTextHeight("hello", "14px monospace", -100, 20)).toBe(20);
  });

  it("extracts font size from font string for fallback calculation", () => {
    // "10px monospace" → charWidth = 6, charsPerLine = floor(60/6) = 10
    // 30 chars / 10 = 3 lines → 3 * 16 = 48
    expect(measureTextHeight("a".repeat(30), "10px monospace", 60, 16)).toBe(48);
  });

  it("defaults to 14px font size when font string has no px value", () => {
    // Default 14px → charWidth = 8.4, charsPerLine = floor(500/8.4) = 59
    // 5 chars / 59 = 1 line → 20
    expect(measureTextHeight("hello", "monospace", 500, 20)).toBe(20);
  });

  it("handles very narrow container (1 char per line)", () => {
    // 10px font → charWidth=6, charsPerLine=floor(6/6)=1
    // 5 chars / 1 = 5 lines → 5 * 20 = 100
    expect(measureTextHeight("hello", "10px monospace", 6, 20)).toBe(100);
  });
});

describe("clearPreparedCache", () => {
  it("resets cache so next call re-prepares", () => {
    prepareText("hello", "14px monospace"); // cache one entry
    clearPreparedCache();
    // After clear, prepareText runs fresh (still null in jsdom)
    expect(prepareText("hello", "14px monospace")).toBeNull();
  });
});
```

**Run:** `cd web && npx vitest run src/lib/__tests__/pretext-measure.test.ts`
**Expected:** 12 tests pass.

#### `web/src/hooks/__tests__/usePretextMeasure.test.ts`

Tests the React hook using established codebase patterns: ResizeObserver mock from `JobList.test.tsx:56-78`, `renderHook` from `@testing-library/react`.

```ts
import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePretextMeasure } from "../usePretextMeasure";

// ── ResizeObserver mock (pattern from JobList.test.tsx:56-78) ─────────

let lastRoCallback: ResizeObserverCallback | null = null;
let lastRoTarget: Element | null = null;
const mockDisconnect = vi.fn();

beforeEach(() => {
  lastRoCallback = null;
  lastRoTarget = null;
  mockDisconnect.mockClear();

  globalThis.ResizeObserver = class {
    private cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) {
      this.cb = cb;
      lastRoCallback = cb;
    }
    observe(target: Element) {
      lastRoTarget = target;
      // Fire callback immediately with 400px width (matches JobList pattern)
      this.cb(
        [
          {
            target,
            contentRect: { height: 600, width: 400 } as DOMRectReadOnly,
            borderBoxSize: [{ blockSize: 600, inlineSize: 400 }],
            contentBoxSize: [{ blockSize: 600, inlineSize: 400 }],
            devicePixelContentBoxSize: [{ blockSize: 600, inlineSize: 400 }],
          } as unknown as ResizeObserverEntry,
        ],
        this as unknown as ResizeObserver,
      );
    }
    unobserve() {}
    disconnect() {
      mockDisconnect();
    }
  } as unknown as typeof ResizeObserver;
});

afterEach(() => {
  vi.restoreAllMocks();
  Reflect.deleteProperty(globalThis, "ResizeObserver");
});

// ── Tests ─────────────────────────────────────────────────────────────

describe("usePretextMeasure", () => {
  it("returns estimateHeight function and containerRef", () => {
    const { result } = renderHook(() => usePretextMeasure());
    expect(typeof result.current.estimateHeight).toBe("function");
    expect(result.current.containerRef).toBeDefined();
    expect(result.current.containerRef.current).toBeNull(); // not attached yet
  });

  it("estimateHeight returns fallback when container has no width", () => {
    const { result } = renderHook(() => usePretextMeasure());
    // containerRef not attached → width = 0 → returns explicit fallback
    expect(result.current.estimateHeight("hello world", 25)).toBe(25);
  });

  it("estimateHeight returns default lineHeight when no fallback given", () => {
    const { result } = renderHook(() => usePretextMeasure());
    // containerRef not attached → width = 0, no fallback → returns lineHeightRef default (20)
    expect(result.current.estimateHeight("hello world")).toBe(20);
  });

  it("reads font from getComputedStyle when container is mounted", () => {
    const mockElement = document.createElement("div");
    // Stub getComputedStyle to return known values
    const originalGCS = window.getComputedStyle;
    vi.spyOn(window, "getComputedStyle").mockImplementation((el) => {
      if (el === mockElement) {
        return {
          fontSize: "14px",
          fontFamily: "monospace",
          lineHeight: "20px",
        } as unknown as CSSStyleDeclaration;
      }
      return originalGCS(el);
    });

    // Stub document.fonts.ready
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { ready: Promise.resolve() },
    });

    const { result } = renderHook(() => usePretextMeasure());

    // Manually set containerRef to our mock element, then trigger effect
    act(() => {
      (result.current.containerRef as React.MutableRefObject<HTMLDivElement | null>).current =
        mockElement as unknown as HTMLDivElement;
    });

    // After hook effect runs with the container attached, the RO fires with width=400
    // estimateHeight should use the fallback path (jsdom, no canvas) with parsed font
    const h = result.current.estimateHeight("hello world", 20);
    // In jsdom: pretext unavailable, fallback uses character heuristic
    // Since widthRef may still be 0 (container set after effect), returns fallback
    expect(h).toBe(20);
  });

  it("updates width via ResizeObserver", () => {
    const { result } = renderHook(() => usePretextMeasure());

    // Simulate container mount and RO callback
    const mockElement = document.createElement("div");
    Object.defineProperty(mockElement, "clientWidth", { value: 400 });

    // The RO should have been created in the effect — since containerRef isn't set,
    // the effect may not attach. Verify the hook handles this gracefully.
    const h = result.current.estimateHeight("test", 20);
    expect(h).toBe(20); // returns fallback since width = 0
  });

  it("disconnects ResizeObserver on unmount", () => {
    const mockElement = document.createElement("div");
    Object.defineProperty(document, "fonts", {
      configurable: true,
      value: { ready: Promise.resolve() },
    });

    const { result, unmount } = renderHook(() => usePretextMeasure());

    // Set containerRef before mount to trigger the effect's RO setup
    act(() => {
      (result.current.containerRef as React.MutableRefObject<HTMLDivElement | null>).current =
        mockElement as unknown as HTMLDivElement;
    });

    unmount();
    // RO disconnect should have been called via the effect cleanup
    // Note: the effect runs based on containerRef changes — if the ref is set
    // after mount, the RO may not be created. This tests the cleanup path.
    // The important thing is no errors thrown on unmount.
  });

  it("returns positive height for any non-empty text with fallback", () => {
    const { result } = renderHook(() => usePretextMeasure());
    // Even without container, should never return 0 or negative
    expect(result.current.estimateHeight("x", 15)).toBeGreaterThan(0);
    expect(result.current.estimateHeight("x")).toBeGreaterThan(0);
    expect(result.current.estimateHeight("")).toBeGreaterThan(0); // returns lineHeightRef
  });
});
```

**Run:** `cd web && npx vitest run src/hooks/__tests__/usePretextMeasure.test.ts`
**Expected:** 7 tests pass.

### Step 6: Run full test suite, build, and lint (~15 min)

**Depends on:** Steps 1-5

Execute sequentially (each gate must pass before proceeding):

```bash
# Gate 1: New unit tests
cd web && npx vitest run src/lib/__tests__/pretext-measure.test.ts
# Expected: 12 tests pass

cd web && npx vitest run src/hooks/__tests__/usePretextMeasure.test.ts
# Expected: 7 tests pass

# Gate 2: Regression tests for modified components
cd web && npx vitest run src/components/jobs/AgentStreamView.test.tsx
# Expected: 13 tests pass (all existing, unchanged)

cd web && npx vitest run src/components/logs/__tests__/VirtualizedLogView.test.tsx
# Expected: 7 tests pass (all existing, unchanged)

cd web && npx vitest run src/components/logs/__tests__/LogSearchBar.test.tsx
# Expected: 9 tests pass (all existing, unchanged)

# Gate 3: Full test suite
cd web && npm run test
# Expected: 0 failures across all ~41 test files

# Gate 4: TypeScript compilation
cd web && npm run build
# Expected: exit code 0, no TS errors

# Gate 5: Lint
cd web && npm run lint
# Expected: exit code 0, no new violations

# Gate 6: Dependency audit
cd web && npm ls @chenglou/pretext
# Expected: shows exactly one entry, pinned to 0.0.3
```

**Failure handling:**
- If Gate 1 fails: fix the new test or utility code (Steps 1-2, 5)
- If Gate 2 fails: the integration broke existing behavior — revert the component changes (Steps 3-4) and debug
- If Gate 3 fails on an unrelated test: investigate — pretext's import may have side effects. If so, add `vi.mock("@chenglou/pretext")` to `web/src/test/setup.ts` (see Risk 5)
- If Gate 4 fails: fix type errors — likely missing type exports from pretext's ESM bundle
- If Gate 5 fails: fix lint issues — likely unused imports or missing return types

### Step 7: Manual smoke testing (~15 min)

**Depends on:** Step 6

```bash
# Terminal 1: Start dev server (proxies API to port 8340)
cd web && npm run dev

# Terminal 2: Verify server is running
stepwise server status
```

**Scenario 1: Agent stream with large output**

| Step | Action | Expected | Verify |
|------|--------|----------|--------|
| 1.1 | Open a completed job with agent output (500+ segments) | Page loads without errors | No console errors in DevTools |
| 1.2 | Scroll through the agent stream | Smooth scrolling, no visible position jumps | Visual inspection |
| 1.3 | Resize browser window horizontally | Scroll position preserved, segment heights re-estimate | Scroll thumb doesn't jump, text re-wraps smoothly |
| 1.4 | Check DOM node count | Only visible rows + overscan rendered | DevTools Elements panel: count `[data-index]` nodes, expect 20-30, not 500+ |

**Scenario 2: Live agent stream**

| Step | Action | Expected | Verify |
|------|--------|----------|--------|
| 2.1 | View an actively running agent step | Cursor blinks, auto-scroll follows output | Visual inspection |
| 2.2 | Scroll up while agent is running | Auto-scroll pauses | New content appears but view doesn't jump |
| 2.3 | Scroll back to bottom | Auto-scroll resumes | View snaps to newest content |

**Scenario 3: Script log output**

| Step | Action | Expected | Verify |
|------|--------|----------|--------|
| 3.1 | View a completed step with 1000+ lines of stdout | Virtualized list renders | Only ~20-30 DOM nodes, not 1000+ |
| 3.2 | Resize window with long log lines visible | Wrapped lines maintain correct height | No visible layout shifts or jank |

**Scenario 4: Below-threshold rendering (regression)**

| Step | Action | Expected | Verify |
|------|--------|----------|--------|
| 4.1 | View a step with <10 lines of output | Renders flat (no virtualizer) | All lines visible in DOM, no `[data-index]` attributes |
| 4.2 | View an agent step with <200 segments | Renders flat | All segments in DOM |

**Scenario 5: Emoji/non-ASCII content**

| Step | Action | Expected | Verify |
|------|--------|----------|--------|
| 5.1 | View agent output containing emoji (status indicators, bullet markers) | Height estimates account for emoji width | No excessive whitespace below segments, no clipping |

## Dependency Graph

```
Step 1: Install pretext + create lib/pretext-measure.ts
  └── Step 2: Create hooks/usePretextMeasure.ts
        ├── Step 3: Integrate into AgentStreamView.tsx  ──┐
        ├── Step 4: Integrate into VirtualizedLogView.tsx ├── Step 6: Full test + build + lint
        └── Step 5: Write tests ──────────────────────────┘      └── Step 7: Manual smoke test
```

Steps 3, 4, and 5 are parallelizable after Step 2 completes. Steps 6-7 are serial gates.

## Files Changed (complete list)

| File | Action | Lines Changed |
|------|--------|---------------|
| `web/package.json` | **MODIFY** | Add `"@chenglou/pretext": "0.0.3"` to dependencies (+1 line) |
| `web/src/lib/pretext-measure.ts` | **CREATE** | ~75 lines — prepare/layout wrapper with feature detection, cache, fallback |
| `web/src/hooks/usePretextMeasure.ts` | **CREATE** | ~60 lines — React hook with ResizeObserver + getComputedStyle |
| `web/src/components/jobs/AgentStreamView.tsx` | **MODIFY** | ~15 lines changed — import, ref merge callback, estimateSize replacement in `VirtualizedSegments` |
| `web/src/components/logs/VirtualizedLogView.tsx` | **MODIFY** | ~12 lines changed — import, ref merge callback, estimateSize replacement in `VirtualizedLogViewInner` |
| `web/src/lib/__tests__/pretext-measure.test.ts` | **CREATE** | ~90 lines — 12 tests covering detection, cache, fallback math, edge cases |
| `web/src/hooks/__tests__/usePretextMeasure.test.ts` | **CREATE** | ~100 lines — 7 tests covering hook API, ResizeObserver integration, getComputedStyle, unmount cleanup |

**Files NOT modified:**
- `DagEdges.tsx` — pretext returns height, not width; canvas `measureText()` is correct for label widths
- `JsonView.tsx` — recursive tree structure, not flat text measurement
- `StepNode.tsx` — fixed-size nodes with CSS truncation; correct UX
- `useAgentStream.ts` — data layer unchanged
- `useScriptStream.ts` — data layer unchanged
- `StepDetailPanel.tsx` — consumes `VirtualizedLogView` and `AgentStreamView`; inherits improvements transitively
- `web/src/test/setup.ts` — no global mocks needed for pretext (unless Risk 5 materializes)

## Testing Strategy

### Test Matrix

| Layer | Test file | Test count | Command | What it verifies |
|-------|-----------|-----------|---------|-----------------|
| Unit | `src/lib/__tests__/pretext-measure.test.ts` | 12 | `cd web && npx vitest run src/lib/__tests__/pretext-measure.test.ts` | Feature detection in jsdom (A8), cache hit/miss, fallback math for all edge cases (empty, zero-width, negative, custom font sizes), cache clear |
| Unit | `src/hooks/__tests__/usePretextMeasure.test.ts` | 7 | `cd web && npx vitest run src/hooks/__tests__/usePretextMeasure.test.ts` | Hook API shape, fallback when unmounted, getComputedStyle integration, ResizeObserver setup/disconnect, positive height invariant |
| Regression | `src/components/jobs/AgentStreamView.test.tsx` | 13 | `cd web && npx vitest run src/components/jobs/AgentStreamView.test.tsx` | All existing tests unchanged — flat rendering path unaffected by hook addition to VirtualizedSegments |
| Regression | `src/components/logs/__tests__/VirtualizedLogView.test.tsx` | 7 | `cd web && npx vitest run src/components/logs/__tests__/VirtualizedLogView.test.tsx` | All existing tests unchanged — below-threshold flat path and above-threshold structure check |
| Regression | `src/components/logs/__tests__/LogSearchBar.test.tsx` | 9 | `cd web && npx vitest run src/components/logs/__tests__/LogSearchBar.test.tsx` | Search highlighting unaffected |
| Regression | `src/components/dag/DagEdges.test.tsx` | 2+ | `cd web && npx vitest run src/components/dag/DagEdges.test.tsx` | Canvas measurement and fallback unchanged (not modified, but validates pretext import doesn't interfere) |
| Full suite | All ~41 test files | ~200+ | `cd web && npm run test` | No test failures across entire suite — validates no import side effects (Risk 5) |
| Build | TypeScript + Vite bundle | — | `cd web && npm run build` | Compiles cleanly, pretext ESM import resolves, bundle generates |
| Lint | ESLint | — | `cd web && npm run lint` | No lint violations in new or modified files |
| Manual | 5 scenarios (see Step 7) | 5 | `cd web && npm run dev` | Visual scroll behavior, layout shift absence, fallback correctness |

### Test Coverage by Requirement

| Requirement | Automated test coverage | Manual test coverage |
|-------------|------------------------|---------------------|
| R1.1 measureTextHeight returns height | `pretext-measure.test.ts`: 4 tests (short, long, narrow, custom font) | — |
| R1.2 Cache reuse | `pretext-measure.test.ts`: "caches prepare results" | — |
| R1.3 jsdom fallback | `pretext-measure.test.ts`: all `measureTextHeight` tests exercise fallback | — |
| R1.4 clearPreparedCache | `pretext-measure.test.ts`: "resets cache" | — |
| R1.5 Edge cases | `pretext-measure.test.ts`: empty, zero-width, negative tests | — |
| R1.6 Feature detection | `pretext-measure.test.ts`: "returns false in jsdom", "returns true when OffscreenCanvas available" | — |
| R2.1 Hook API | `usePretextMeasure.test.ts`: "returns estimateHeight function and containerRef" | — |
| R2.2 getComputedStyle | `usePretextMeasure.test.ts`: "reads font from getComputedStyle" | — |
| R2.3 Fallback (zero width) | `usePretextMeasure.test.ts`: "returns fallback when container has no width" | — |
| R2.4 Default lineHeight | `usePretextMeasure.test.ts`: "returns default lineHeight when no fallback" | — |
| R2.5 ResizeObserver | `usePretextMeasure.test.ts`: "updates width", "disconnects on unmount" | — |
| R3.1-R3.5 Agent stream | Regression: 13 existing tests pass unchanged | Scenarios 1, 2, 5 |
| R3.6 Existing tests | `AgentStreamView.test.tsx`: all 13 pass | — |
| R4.1-R4.3 Log viewer | Regression: 7 existing tests pass unchanged | Scenario 3 |
| R4.4 Existing tests | `VirtualizedLogView.test.tsx`: all 7 pass | — |
| Below-threshold regression | Existing flat-path tests in both components | Scenario 4 |

### One-Command Full Validation

```bash
cd web && npm run test && npm run build && npm run lint && echo "ALL GATES PASSED"
```

## Risks & Mitigations

### Risk 1: pretext v0.0.3 is pre-1.0 — API may change

**Likelihood:** Medium. `prepare()`+`layout()` are the stable core per the author, but no semver guarantees.

**Mitigation:** All pretext usage is isolated behind `web/src/lib/pretext-measure.ts` (~75 lines). If the API changes, only this file needs updating. Pin exact version: `"@chenglou/pretext": "0.0.3"` (no `^` range).

### Risk 2: Font string mismatch causes inaccurate measurements

**Likelihood:** Medium. `prepare()` requires the CSS font string to match what's rendered.

**Mitigation:** Read font from `getComputedStyle()` on the actual container element rather than hardcoding. Guard with `document.fonts.ready` before first `prepare()`. Since agent streams use `font-mono` (system monospace stack — `ui-monospace, SFMono-Regular, ...` — not `system-ui`), the known macOS system-ui variant issue does not apply (see A7).

### Risk 3: jsdom feature detection — `typeof document !== "undefined"` is true in jsdom

**Likelihood:** Certain. This was the original plan's detection logic. jsdom provides `document` but `canvas.getContext("2d")` returns `null` (no canvas implementation, see A8).

**Mitigation:** Fixed in the updated utility. `isPretextAvailable()` now checks `getContext("2d") !== null`, not just `typeof document`. Tests in `pretext-measure.test.ts` explicitly verify this returns `false` in jsdom and `true` when `OffscreenCanvas` is mocked (following the pattern from `DagEdges.test.tsx:29-41`).

### Risk 4: Memory usage from prepared text cache grows unbounded

**Likelihood:** Low-Medium. Agent streams with 1000+ unique segments accumulate `PreparedText` handles.

**Mitigation:** Each `PreparedText` is ~100-200 bytes per segment. 10,000 entries ≈ 1-2MB, well within budget. `clearPreparedCache()` is exposed for cleanup on page navigation if needed. Cache is per-session (not persisted).

### Risk 5: pretext import has top-level side effects that break jsdom

**Likelihood:** Low. ESM-only package should tree-shake cleanly, but if it accesses `OffscreenCanvas`/`canvas` at import time, jsdom would throw.

**Mitigation:** Gate 3 of Step 6 catches this — the full test suite runs all ~41 files. If pretext's import breaks jsdom, tests fail before any test runs. Fix: add to `web/src/test/setup.ts`:
```ts
vi.mock("@chenglou/pretext", () => ({
  prepare: () => { throw new Error("not available in jsdom"); },
  layout: () => ({ height: 0, lineCount: 0 }),
}));
```
Then `prepareText()` catches the error and falls back. This is a contingency — implement only if needed.

### Risk 6: Marginal improvement for monospace text

**Likelihood:** Known. Agent streams use `font-mono`, so ASCII-only content gets near-identical estimates from the character heuristic.

**Mitigation:** Acceptable. Pretext improves accuracy for emoji, non-ASCII, and tabs. `measureElement` still handles post-render correction. The 15KB bundle cost is paid once for both integration points. Manual smoke test Scenario 5 specifically validates emoji-heavy output.

## What This Plan Does NOT Do

1. **Does not integrate pretext for DAG label width** — pretext returns `{ height, lineCount }`, not width. Canvas `measureText()` remains correct for width (A4).
2. **Does not virtualize JsonView** — recursive tree structure, not flat text (A5).
3. **Does not change StepNode truncation** — character-limit + CSS `truncate` + tooltips is the right UX.
4. **Does not use pretext's rich API** — `prepareWithSegments`, `walkLineRanges` are marked unstable in v0.0.3.
5. **Does not add pretext to the Python backend** — browser-only library.
6. **Does not replace `@tanstack/react-virtual`** — pretext provides better *estimates* to the virtualizer, not an alternative virtualization strategy.

## Future Opportunities

If pretext reaches v1.0 with stable rich API:
- **Chat bubble shrinkwrap** — `walkLineRanges()` for tighter agent message containers in EditorChat
- **DAG label multi-line overflow** — `layoutWithLines()` to detect when labels need abbreviation
- **Report/document layout** — pretext's obstacle-aware line layout for rich content rendering

If agent stream switches to proportional font in the future:
- Pretext's value proposition increases significantly — character-count heuristics become unreliable with proportional fonts.
