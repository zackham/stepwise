import { useEffect, useRef } from "react";

export interface HotkeyBinding {
  keys: string[];
  onTrigger: () => void;
  preventDefault?: boolean;
  allowInEditable?: boolean;
}

interface UseHotkeysOptions {
  enabled?: boolean;
  timeoutMs?: number;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false;
  }

  return Boolean(
    target.closest(
      "input, textarea, select, [contenteditable='true'], [role='textbox']"
    )
  );
}

function normalizeKey(key: string): string {
  return key.length === 1 ? key.toLowerCase() : key.toLowerCase();
}

function isPrefix(candidate: string[], keys: string[]): boolean {
  if (candidate.length > keys.length) {
    return false;
  }

  return candidate.every((key, index) => key === keys[index]);
}

export function useHotkeys(
  bindings: HotkeyBinding[],
  options: UseHotkeysOptions = {}
) {
  const { enabled = true, timeoutMs = 1000 } = options;
  const bindingsRef = useRef(bindings);
  const sequenceRef = useRef<string[]>([]);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    bindingsRef.current = bindings;
  }, [bindings]);

  useEffect(() => {
    if (!enabled) {
      sequenceRef.current = [];
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      return;
    }

    const resetSequence = () => {
      sequenceRef.current = [];
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
    };

    const restartTimeout = () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
      timeoutRef.current = setTimeout(resetSequence, timeoutMs);
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        event.repeat ||
        event.metaKey ||
        event.ctrlKey ||
        event.altKey
      ) {
        return;
      }

      const key = normalizeKey(event.key);
      const editable = isEditableTarget(event.target);
      const eligibleBindings = bindingsRef.current.filter(
        (binding) => !editable || binding.allowInEditable
      );

      if (!eligibleBindings.length) {
        resetSequence();
        return;
      }

      const nextSequence = [...sequenceRef.current, key];
      let matchingBindings = eligibleBindings.filter((binding) =>
        isPrefix(nextSequence, binding.keys)
      );
      let activeSequence = nextSequence;

      if (!matchingBindings.length) {
        activeSequence = [key];
        matchingBindings = eligibleBindings.filter((binding) =>
          isPrefix(activeSequence, binding.keys)
        );
      }

      if (!matchingBindings.length) {
        resetSequence();
        return;
      }

      const exactBinding = matchingBindings.find(
        (binding) => binding.keys.length === activeSequence.length
      );
      const shouldPreventDefault = matchingBindings.some(
        (binding) => binding.preventDefault !== false
      );

      if (shouldPreventDefault) {
        event.preventDefault();
        event.stopPropagation();
      }

      if (exactBinding) {
        resetSequence();
        exactBinding.onTrigger();
        return;
      }

      sequenceRef.current = activeSequence;
      restartTimeout();
    };

    window.addEventListener("keydown", handleKeyDown, true);
    return () => {
      window.removeEventListener("keydown", handleKeyDown, true);
      resetSequence();
    };
  }, [enabled, timeoutMs]);
}
