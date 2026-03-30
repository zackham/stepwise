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
    if (typeof ResizeObserver === "undefined") return;
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
