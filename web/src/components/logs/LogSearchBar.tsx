import { Search } from "lucide-react";
import { cn } from "@/lib/utils";
import type { LogSearchState } from "@/hooks/useLogSearch";

interface LogSearchBarProps {
  search: LogSearchState;
  className?: string;
}

export function LogSearchBar({ search, className }: LogSearchBarProps) {
  return (
    <div
      className={cn(
        "flex items-center gap-1.5 px-2 py-1 border-b border-zinc-800/50",
        className
      )}
    >
      <Search className="w-3 h-3 text-zinc-500 shrink-0" />
      <input
        ref={search.searchInputRef}
        type="text"
        value={search.query}
        onChange={(e) => search.setQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            search.setQuery("");
            (e.target as HTMLInputElement).blur();
          }
        }}
        placeholder="Search logs..."
        aria-invalid={search.regexError || undefined}
        className={cn(
          "flex-1 min-w-0 bg-transparent text-xs text-zinc-300 placeholder:text-zinc-600 outline-none",
          "border-none ring-0 focus:ring-0",
          search.regexError && "text-red-400"
        )}
      />
      <button
        type="button"
        onClick={search.toggleCaseSensitive}
        title="Case sensitive"
        className={cn(
          "text-[11px] font-mono px-1 py-0.5 rounded transition-colors shrink-0",
          search.caseSensitive
            ? "text-blue-400 bg-blue-500/10"
            : "text-zinc-600 hover:text-zinc-400"
        )}
      >
        Aa
      </button>
      <button
        type="button"
        onClick={search.toggleRegexMode}
        title="Regex mode"
        className={cn(
          "text-[11px] font-mono px-1 py-0.5 rounded transition-colors shrink-0",
          search.regexMode
            ? "text-blue-400 bg-blue-500/10"
            : "text-zinc-600 hover:text-zinc-400"
        )}
      >
        .*
      </button>
      {search.query && !search.regexError && (
        <span className="text-[10px] text-zinc-500 tabular-nums shrink-0">
          {search.matchCount} {search.matchCount === 1 ? "match" : "matches"}
        </span>
      )}
      {search.regexError && (
        <span className="text-[10px] text-red-400 shrink-0">invalid</span>
      )}
    </div>
  );
}
