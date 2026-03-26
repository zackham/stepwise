import { useState, useMemo, useRef, useEffect, useCallback } from "react";
import { escapeRegex } from "@/lib/log-search";

export interface LogSearchState {
  query: string;
  setQuery: (q: string) => void;
  caseSensitive: boolean;
  toggleCaseSensitive: () => void;
  regexMode: boolean;
  toggleRegexMode: () => void;
  regexError: boolean;
  compiledRegex: RegExp | null;
  matchCount: number;
  setMatchCount: (n: number) => void;
  searchInputRef: React.RefObject<HTMLInputElement | null>;
}

export function useLogSearch(containerRef?: React.RefObject<HTMLElement | null>): LogSearchState {
  const [query, setQuery] = useState("");
  const [caseSensitive, setCaseSensitive] = useState(false);
  const [regexMode, setRegexMode] = useState(false);
  const [matchCount, setMatchCount] = useState(0);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  const toggleCaseSensitive = useCallback(() => setCaseSensitive((v) => !v), []);
  const toggleRegexMode = useCallback(() => setRegexMode((v) => !v), []);

  const { compiledRegex, regexError } = useMemo(() => {
    if (!query) return { compiledRegex: null, regexError: false };

    const flags = `g${caseSensitive ? "" : "i"}`;
    try {
      const pattern = regexMode ? query : escapeRegex(query);
      return { compiledRegex: new RegExp(pattern, flags), regexError: false };
    } catch {
      return { compiledRegex: null, regexError: true };
    }
  }, [query, caseSensitive, regexMode]);

  // Keyboard shortcut: Ctrl+F / Cmd+F to focus, Escape to clear
  useEffect(() => {
    const container = containerRef?.current;
    if (!container) return;

    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "f") {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
      if (e.key === "Escape" && document.activeElement === searchInputRef.current) {
        setQuery("");
        searchInputRef.current?.blur();
      }
    };

    container.addEventListener("keydown", handler);
    return () => container.removeEventListener("keydown", handler);
  }, [containerRef]);

  return {
    query,
    setQuery,
    caseSensitive,
    toggleCaseSensitive,
    regexMode,
    toggleRegexMode,
    regexError,
    compiledRegex,
    matchCount,
    setMatchCount,
    searchInputRef,
  };
}
