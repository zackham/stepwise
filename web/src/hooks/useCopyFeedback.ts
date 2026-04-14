import { useState, useCallback, useRef } from "react";

/**
 * Fallback copy using a temporary textarea and execCommand.
 * Works in non-secure contexts and older browsers where
 * navigator.clipboard is unavailable.
 */
function fallbackCopy(text: string): boolean {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  // Avoid scrolling to bottom
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "-9999px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    // execCommand can throw in some environments
  }
  document.body.removeChild(textarea);
  return ok;
}

/**
 * Copy text to clipboard with automatic fallback.
 * Uses navigator.clipboard when available, falls back to execCommand('copy').
 * Safe to call in any context (non-secure, inside modals, etc.).
 */
export async function copyToClipboard(text: string): Promise<void> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
  } catch {
    // Async clipboard API can reject (e.g. permissions, focus lost).
  }
  fallbackCopy(text);
}

/**
 * Hook for clipboard copy with visual feedback.
 * `copy(text)` writes to clipboard and sets `justCopied` true for 1.5 seconds.
 * Uses navigator.clipboard when available, falls back to execCommand('copy').
 */
export function useCopyFeedback() {
  const [justCopied, setJustCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const copy = useCallback(async (text: string) => {
    await copyToClipboard(text);
    setJustCopied(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setJustCopied(false), 1500);
  }, []);

  return { copy, justCopied };
}
