import { useEffect, useRef, useCallback, useState } from "react";

/**
 * Auto-scroll to bottom on mount/content load, follow new content during streaming,
 * and show jump-to-top / jump-to-bottom buttons contextually.
 *
 * Only shows buttons when there's meaningful scroll distance (>= 2x viewport height).
 */
export function useAutoScroll(
  containerRef: React.RefObject<HTMLElement | null>,
  contentVersion: number,
  isLive: boolean,
) {
  const [position, setPosition] = useState<"top" | "middle" | "bottom" | "none">("none");
  const followRef = useRef(true);
  const hasScrolledInitially = useRef(false);
  const suppressScrollEvent = useRef(false);

  const computePosition = useCallback((el: HTMLElement): "top" | "middle" | "bottom" | "none" => {
    const scrollable = el.scrollHeight - el.clientHeight;
    // Only show FABs when there's meaningful scroll distance
    if (scrollable < el.clientHeight) return "none";

    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    const atTop = el.scrollTop < 40;

    if (atBottom) return "bottom";
    if (atTop) return "top";
    return "middle";
  }, []);

  const scrollToBottom = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    suppressScrollEvent.current = true;
    el.scrollTop = el.scrollHeight;
    followRef.current = true;
    setPosition(computePosition(el));
    requestAnimationFrame(() => { suppressScrollEvent.current = false; });
  }, [containerRef, computePosition]);

  const scrollToTop = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    suppressScrollEvent.current = true;
    el.scrollTop = 0;
    followRef.current = false;
    setPosition(computePosition(el));
    requestAnimationFrame(() => { suppressScrollEvent.current = false; });
  }, [containerRef, computePosition]);

  // Scroll to bottom when content first appears or changes while following
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        if (!hasScrolledInitially.current && el.scrollHeight > el.clientHeight) {
          hasScrolledInitially.current = true;
          scrollToBottom();
        } else if (followRef.current && contentVersion > 0) {
          suppressScrollEvent.current = true;
          el.scrollTop = el.scrollHeight;
          setPosition(computePosition(el));
          requestAnimationFrame(() => { suppressScrollEvent.current = false; });
        }
      });
    });
  }, [contentVersion, containerRef, scrollToBottom, computePosition]);

  // Detect scroll position
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const onScroll = () => {
      if (suppressScrollEvent.current) return;
      const pos = computePosition(el);
      setPosition(pos);
      followRef.current = pos === "bottom";
    };

    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [containerRef, computePosition]);

  // Reset when container changes
  useEffect(() => {
    hasScrolledInitially.current = false;
    followRef.current = true;
    setPosition("none");
  }, [containerRef]);

  return {
    showBackToBottom: position === "middle" || position === "top",
    showJumpToTop: position === "bottom",
    scrollToBottom,
    scrollToTop,
  };
}
