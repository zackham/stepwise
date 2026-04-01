import { useState, useCallback, useRef, useEffect } from "react";

const DEFAULT_MIN = 280;
const DEFAULT_MAX = 700;
const COLLAPSE_THRESHOLD = 80; // pixels past min to trigger collapse

interface UseResizableWidthOptions {
  storageKey: string;
  defaultWidth: number;
  min?: number;
  max?: number;
  /** Which side of the viewport the panel is on. Affects drag direction. */
  side?: "left" | "right";
  /** Called when user drags past min width — signals intent to collapse */
  onCollapse?: () => void;
}

export function useResizableWidth({
  storageKey,
  defaultWidth,
  min = DEFAULT_MIN,
  max = DEFAULT_MAX,
  side = "right",
  onCollapse,
}: UseResizableWidthOptions) {
  const [width, setWidth] = useState(() => {
    const stored = localStorage.getItem(storageKey);
    if (stored) {
      const n = parseInt(stored, 10);
      if (!isNaN(n) && n >= min && n <= max) return n;
    }
    return defaultWidth;
  });

  const dragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(0);
  const rafId = useRef(0);
  const shouldCollapse = useRef(false);
  const onCollapseRef = useRef(onCollapse);
  onCollapseRef.current = onCollapse;

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      dragging.current = true;
      shouldCollapse.current = false;
      startX.current = e.clientX;
      startWidth.current = width;
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [width],
  );

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!dragging.current) return;
      cancelAnimationFrame(rafId.current);
      rafId.current = requestAnimationFrame(() => {
        const delta = side === "right"
          ? startX.current - e.clientX
          : e.clientX - startX.current;
        const raw = startWidth.current + delta;

        if (onCollapseRef.current && raw < min - COLLAPSE_THRESHOLD) {
          // Dragged well past min — mark for collapse on mouseup
          shouldCollapse.current = true;
          setWidth(min);
        } else {
          shouldCollapse.current = false;
          setWidth(Math.min(max, Math.max(min, raw)));
        }
      });
    };

    const onMouseUp = () => {
      if (!dragging.current) return;
      dragging.current = false;
      cancelAnimationFrame(rafId.current);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";

      if (shouldCollapse.current && onCollapseRef.current) {
        shouldCollapse.current = false;
        onCollapseRef.current();
      } else {
        setWidth((w) => {
          localStorage.setItem(storageKey, String(w));
          return w;
        });
      }
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      cancelAnimationFrame(rafId.current);
    };
  }, [storageKey, min, max, side]);

  return { width, onMouseDown };
}
