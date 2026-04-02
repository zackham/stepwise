import { useState, useRef, useEffect, useCallback } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, Search, Check } from "lucide-react";
import { cn } from "@/lib/utils";

export interface ComboBoxOption {
  value: string;
  label: string;
  sublabel?: string;
  /** Used for sort-by-recent. ISO timestamp or comparable string. */
  sortKey?: string;
}

interface ComboBoxProps {
  value: string;
  onChange: (value: string) => void;
  options: ComboBoxOption[];
  placeholder?: string;
  searchPlaceholder?: string;
  className?: string;
  /** Show sort toggle (Recent / A-Z) when options have sortKey */
  sortable?: boolean;
}

export function ComboBox({
  value,
  onChange,
  options,
  placeholder = "Select...",
  searchPlaceholder = "Search...",
  className,
  sortable = false,
}: ComboBoxProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [sortMode, setSortMode] = useState<"recent" | "az">("recent");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const posRef = useRef({ top: 0, left: 0 });

  const filtered = (() => {
    let result = query
      ? options.filter((o) => o.label.toLowerCase().includes(query.toLowerCase()))
      : [...options];
    if (sortable && sortMode === "az") {
      // Keep "all" option first, sort the rest alphabetically
      const first = result.find((o) => o.value === "all");
      const rest = result.filter((o) => o.value !== "all");
      rest.sort((a, b) => a.label.localeCompare(b.label));
      result = first ? [first, ...rest] : rest;
    }
    return result;
  })();

  const selected = options.find((o) => o.value === value);

  const handleSelect = useCallback((val: string) => {
    onChange(val);
    setOpen(false);
    setQuery("");
  }, [onChange]);

  const handleToggle = useCallback(() => {
    if (!open && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect();
      posRef.current = { top: rect.bottom + 4, left: rect.left };
    }
    setOpen((o) => !o);
  }, [open]);

  useEffect(() => {
    if (open) setTimeout(() => inputRef.current?.focus(), 0);
  }, [open]);

  const hasSublabels = options.some((o) => o.sublabel);

  return (
    <div className={cn("relative", className)}>
      <button
        ref={triggerRef}
        onClick={handleToggle}
        className="flex items-center gap-1.5 h-8 px-2.5 text-sm rounded-lg border border-border bg-background hover:bg-muted hover:text-foreground dark:border-input dark:bg-input/30 dark:hover:bg-input/50 transition-all"
      >
        <span className="truncate max-w-[120px]">{selected?.label ?? placeholder}</span>
        <ChevronDown className="w-3 h-3 shrink-0 opacity-50" />
      </button>
      {open && createPortal(
        <>
          <div className="fixed inset-0 z-[99]" onClick={() => { setOpen(false); setQuery(""); }} />
          <div
            className={cn(
              "fixed z-[100] bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-md shadow-2xl overflow-hidden",
              hasSublabels ? "w-72" : "w-56",
            )}
            style={{ top: posRef.current.top, left: posRef.current.left }}
          >
            <div className="flex items-center gap-1.5 px-2.5 py-2 border-b border-zinc-200 dark:border-zinc-800">
              <Search className="w-3.5 h-3.5 text-zinc-500 shrink-0" />
              <input
                ref={inputRef}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={searchPlaceholder}
                className="flex-1 text-xs bg-transparent text-zinc-800 dark:text-zinc-200 placeholder-zinc-500 outline-none"
              />
            </div>
            {sortable && (
              <div className="flex items-center gap-1 px-2.5 py-1.5 border-b border-zinc-200 dark:border-zinc-800">
                <button
                  onClick={() => setSortMode("recent")}
                  className={cn(
                    "px-2 py-0.5 text-[10px] rounded transition-colors",
                    sortMode === "recent"
                      ? "bg-zinc-200 dark:bg-zinc-700 text-zinc-800 dark:text-zinc-200"
                      : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
                  )}
                >
                  Recent
                </button>
                <button
                  onClick={() => setSortMode("az")}
                  className={cn(
                    "px-2 py-0.5 text-[10px] rounded transition-colors",
                    sortMode === "az"
                      ? "bg-zinc-200 dark:bg-zinc-700 text-zinc-800 dark:text-zinc-200"
                      : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
                  )}
                >
                  A–Z
                </button>
              </div>
            )}
            <div className="max-h-64 overflow-y-auto py-1">
              {filtered.length === 0 ? (
                <div className="px-3 py-2 text-xs text-zinc-500">No results</div>
              ) : (
                filtered.map((option) => {
                  const isActive = option.value === value;
                  return (
                    <button
                      key={option.value}
                      onClick={() => handleSelect(option.value)}
                      className={cn(
                        "w-full flex items-center gap-2.5 text-left transition-colors",
                        hasSublabels ? "px-3 py-2" : "px-3 py-1.5",
                        isActive
                          ? "bg-zinc-100 dark:bg-zinc-800"
                          : "hover:bg-zinc-50 dark:hover:bg-zinc-800/50",
                      )}
                    >
                      <Check className={cn("w-3 h-3 shrink-0", isActive ? "text-blue-500 opacity-100" : "opacity-0")} />
                      <div className="min-w-0 flex-1">
                        <div className={cn("text-xs truncate", isActive ? "text-zinc-200" : "text-zinc-600 dark:text-zinc-300")}>
                          {option.label}
                        </div>
                        {option.sublabel && (
                          <div className="text-[10px] text-zinc-500 truncate mt-0.5">
                            {option.sublabel}
                          </div>
                        )}
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </div>
        </>,
        document.body
      )}
    </div>
  );
}
