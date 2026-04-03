import { useState, useEffect, useRef, useCallback } from "react";
import { measureTextHeight } from "@/lib/pretext-measure";

/**
 * Generic adaptive line distribution hook.
 *
 * Measures a container's available vertical space and distributes lines across
 * multiple text slots so they fill the space without overflow. All measurement
 * happens in an offscreen clone — the real DOM is never mutated during
 * measurement, so there is no flicker.
 *
 * Algorithm:
 *   1. Clone the container (cloneNode(true)), position offscreen.
 *   2. In the clone, collapse all [data-adaptive-value] elements to 1 line.
 *   3. Measure clone's scrollHeight → chrome + (numSlots × lineHeight).
 *   4. Derive chromeHeight = scrollHeight − (numSlots × lineHeight).
 *   5. Available height = viewport bottom − container top.
 *   6. Available lines = floor((availableHeight − chromeHeight) / lineHeight).
 *   7. If availableLines < numSlots → scroll mode:
 *        Set clone overflow-y: scroll, read narrower clientWidth,
 *        recompute natural line counts at that width.
 *        Return 1 line per slot, needsScroll: true.
 *   8. Else → expand mode:
 *        Round-robin distribute extra lines to slots that need them.
 *        Return allocated lines, needsScroll: false.
 *   9. Remove clone. Apply result in one setState.
 *
 * Triggers: ResizeObserver on container (catches sidebar drag + browser resize),
 * and slot content changes.
 */

export interface AdaptiveSlotValue {
  value: string;
  /** Minimum number of lines to display for this slot (default 1). */
  minLines?: number;
}

export interface AdaptiveResult {
  /** Number of visible lines per slot. */
  allocations: number[];
  /** Natural (unwrapped) line count per slot — used to detect truncation. */
  naturalLines: number[];
  /** Whether the container needs scrolling (content doesn't fit at 1 line each). */
  needsScroll: boolean;
  /** Measured content width (accounts for scrollbar if needsScroll). */
  contentWidth: number;
  /** Measured mono font string for pretext. */
  font: string;
  /** Measured line height in pixels. */
  lineHeight: number;
  /** True once the first measurement is complete. */
  ready: boolean;
}

const DATA_ATTR = "data-adaptive-value";

/**
 * Measure the actual rendered line height of a mono text element by
 * creating a probe inside the given element.
 */
function measureLineHeight(container: HTMLElement): number {
  const probe = document.createElement("span");
  probe.className = "text-xs font-mono leading-relaxed";
  probe.style.visibility = "hidden";
  probe.style.position = "absolute";
  probe.textContent = "X";
  container.appendChild(probe);
  const h = probe.getBoundingClientRect().height;
  container.removeChild(probe);
  return h > 0 ? h : 19.5; // fallback to 12px * 1.625
}

/**
 * Measure the actual font string from a mono probe element.
 */
function measureFont(container: HTMLElement): string {
  const probe = document.createElement("span");
  probe.className = "text-xs font-mono";
  probe.style.visibility = "hidden";
  probe.style.position = "absolute";
  probe.textContent = "X";
  container.appendChild(probe);
  const cs = getComputedStyle(probe);
  const font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  container.removeChild(probe);
  return font;
}

/**
 * Measure the content width inside a container (clientWidth minus padding).
 */
function getContentWidth(el: HTMLElement): number {
  const cs = getComputedStyle(el);
  const padLeft = parseFloat(cs.paddingLeft) || 0;
  const padRight = parseFloat(cs.paddingRight) || 0;
  return el.clientWidth - padLeft - padRight;
}

/**
 * Compute natural line counts for each slot at a given width.
 */
function computeNaturalLines(
  slots: AdaptiveSlotValue[],
  font: string,
  width: number,
  lineHeight: number,
): number[] {
  if (width <= 0) return slots.map(() => 1);
  return slots.map((slot) => {
    if (!slot.value) return 1;
    const h = measureTextHeight(slot.value, font, width, lineHeight, {
      whiteSpace: "pre-wrap",
    });
    return Math.max(1, Math.ceil(h / lineHeight));
  });
}

/**
 * Distribute `budget` extra lines across slots proportionally to need.
 * Each slot starts at its minLines (capped to its natural count) and can
 * grow up to its natural line count.
 */
function distribute(
  naturalLines: number[],
  totalAvailableLines: number,
  minLinesPerSlot: number[],
): number[] {
  const n = naturalLines.length;
  // Start each slot at min(minLines, naturalLines) — don't allocate more than content has
  const alloc = minLinesPerSlot.map((min, i) => Math.min(min, naturalLines[i]));
  let remaining = totalAvailableLines - alloc.reduce((a, b) => a + b, 0);

  // Round-robin: give one extra line at a time to slots that need it
  let changed = true;
  while (remaining > 0 && changed) {
    changed = false;
    for (let i = 0; i < n; i++) {
      if (remaining <= 0) break;
      if (alloc[i] < naturalLines[i]) {
        alloc[i]++;
        remaining--;
        changed = true;
      }
    }
  }

  return alloc;
}

export function useAdaptiveLines(
  containerRef: React.RefObject<HTMLElement | null>,
  slots: AdaptiveSlotValue[],
): AdaptiveResult {
  const [result, setResult] = useState<AdaptiveResult>({
    allocations: slots.map((s) => s.minLines ?? 1),
    naturalLines: new Array(slots.length).fill(1),
    needsScroll: false,
    contentWidth: 300,
    font: "12px monospace",
    lineHeight: 19.5,
    ready: false,
  });

  // Cache font + lineHeight — these don't change unless the page styles change
  const fontRef = useRef<string | null>(null);
  const lineHeightRef = useRef<number>(0);

  const compute = useCallback(() => {
    const el = containerRef.current;
    if (!el || slots.length === 0) return;

    // Measure font/lineHeight once
    if (!fontRef.current || lineHeightRef.current <= 0) {
      fontRef.current = measureFont(el);
      lineHeightRef.current = measureLineHeight(el);
    }
    const font = fontRef.current;
    const lh = lineHeightRef.current;

    // 1. Clone the container offscreen
    const clone = el.cloneNode(true) as HTMLElement;
    clone.style.position = "fixed";
    clone.style.visibility = "hidden";
    clone.style.pointerEvents = "none";
    clone.style.zIndex = "-9999";
    // Match the real element's position and size exactly
    const rect = el.getBoundingClientRect();
    clone.style.top = `${rect.top}px`;
    clone.style.left = `${rect.left}px`;
    clone.style.width = `${rect.width}px`;
    // Remove any max-height / overflow constraints so we can measure natural height
    clone.style.maxHeight = "none";
    clone.style.overflow = "visible";
    clone.style.height = "auto";

    document.body.appendChild(clone);

    // 2. Collapse all value elements to minLines (default 1)
    const valueEls = clone.querySelectorAll<HTMLElement>(`[${DATA_ATTR}]`);
    let totalMinLineHeight = 0;
    const slotMinLines = slots.map((s) => s.minLines ?? 1);
    let veIdx = 0;
    for (const ve of valueEls) {
      const min = slotMinLines[veIdx] ?? 1;
      ve.style.height = `${lh * min}px`;
      ve.style.overflow = "hidden";
      ve.style.maskImage = "none";
      ve.style.webkitMaskImage = "none";
      totalMinLineHeight += lh * min;
      veIdx++;
    }

    // 3. Measure chrome height
    const skeletonHeight = clone.scrollHeight;
    const chromeHeight = skeletonHeight - totalMinLineHeight;

    // 4. Available height = viewport bottom - container top
    //    Can't use el.clientHeight because it reflects current content size,
    //    not the maximum available space (content grows based on allocation).
    const availableHeight = window.innerHeight - rect.top;

    // 5. Content width (no scrollbar)
    const fullWidth = getContentWidth(clone);

    // 6. How many lines can we fit?
    const availableLines = Math.floor((availableHeight - chromeHeight) / lh);

    let needsScroll: boolean;
    let contentWidth: number;
    let allocations: number[];
    let natLines: number[];

    // Per-slot minimum lines (default 1)
    const minLinesPerSlot = slots.map((s) => s.minLines ?? 1);
    const totalMinLines = minLinesPerSlot.reduce((a, b) => a + b, 0);

    if (availableLines < totalMinLines) {
      // Scroll mode: not enough space for even minLines per slot
      needsScroll = true;
      // Measure width with scrollbar present
      clone.style.overflowY = "scroll";
      clone.style.height = `${availableHeight}px`;
      contentWidth = getContentWidth(clone);
      natLines = computeNaturalLines(slots, font, contentWidth, lh);
      // In scroll mode, give each slot its minLines (capped to natural)
      allocations = minLinesPerSlot.map((min, i) => Math.min(min, natLines[i]));
    } else {
      // Expand mode: distribute lines starting from minLines
      needsScroll = false;
      contentWidth = fullWidth;
      natLines = computeNaturalLines(slots, font, contentWidth, lh);
      allocations = distribute(natLines, availableLines, minLinesPerSlot);
    }

    // 7. Clean up clone
    document.body.removeChild(clone);

    // 8. Apply result in one shot
    setResult({
      allocations,
      naturalLines: natLines,
      needsScroll,
      contentWidth,
      font,
      lineHeight: lh,
      ready: true,
    });
  }, [containerRef, slots]);

  // Run on mount and when slots change — double-rAF ensures React has painted new content
  useEffect(() => {
    const run = () => requestAnimationFrame(() => requestAnimationFrame(compute));
    if (document.fonts?.ready) {
      document.fonts.ready.then(run);
    } else {
      run();
    }
  }, [compute]);

  // ResizeObserver: catches sidebar drag (container width changes)
  // window resize: catches browser resize (viewport height changes)
  // Uses rAF to coalesce to one computation per frame — no jank during drag
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    let rafId = 0;
    const scheduleCompute = () => {
      if (rafId) return; // already scheduled
      rafId = requestAnimationFrame(() => {
        rafId = 0;
        compute();
      });
    };

    const ro = new ResizeObserver(scheduleCompute);
    ro.observe(el);
    window.addEventListener("resize", scheduleCompute);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", scheduleCompute);
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, [containerRef, compute]);

  // Reset allocations array length when slot count changes
  useEffect(() => {
    setResult((prev) => ({
      ...prev,
      allocations: slots.map((s) => s.minLines ?? 1),
      ready: false,
    }));
  }, [slots.length]);

  return result;
}
