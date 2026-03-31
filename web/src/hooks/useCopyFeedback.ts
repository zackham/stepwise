import { useState, useCallback, useRef } from "react";

/**
 * Hook for clipboard copy with visual feedback.
 * `copy(text)` writes to clipboard and sets `justCopied` true for 1.5 seconds.
 */
export function useCopyFeedback() {
  const [justCopied, setJustCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const copy = useCallback((text: string) => {
    navigator.clipboard.writeText(text);
    setJustCopied(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setJustCopied(false), 1500);
  }, []);

  return { copy, justCopied };
}
