import { useState, useMemo } from "react";
import { Search, Loader2, WifiOff } from "lucide-react";
import { Input } from "@/components/ui/input";
import { useRegistrySearch } from "@/hooks/useEditor";
import { RegistryFlowCard } from "./RegistryFlowCard";
import type { RegistryFlow } from "@/lib/types";

interface RegistryBrowserProps {
  selectedSlug?: string;
  onSelect: (flow: RegistryFlow) => void;
}

export function RegistryBrowser({ selectedSlug, onSelect }: RegistryBrowserProps) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<"downloads" | "newest">("downloads");
  const { data, isLoading, isError } = useRegistrySearch(query, sort);

  const flows = data?.flows ?? [];

  if (isError) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-4 text-zinc-600">
        <WifiOff className="w-8 h-8 mb-2 opacity-50" />
        <p className="text-xs">Registry unavailable</p>
        <p className="text-xs text-zinc-700 mt-1">Check your connection</p>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="p-2 space-y-2">
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500" />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search flows..."
            className="pl-8 h-8 text-xs bg-white dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700"
          />
        </div>
        <div className="flex gap-1">
          <button
            onClick={() => setSort("downloads")}
            className={`text-[10px] px-2 py-0.5 rounded ${
              sort === "downloads"
                ? "bg-white dark:bg-zinc-700 text-zinc-800 dark:text-zinc-200 shadow-sm"
                : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
            }`}
          >
            Popular
          </button>
          <button
            onClick={() => setSort("newest")}
            className={`text-[10px] px-2 py-0.5 rounded ${
              sort === "newest"
                ? "bg-white dark:bg-zinc-700 text-zinc-800 dark:text-zinc-200 shadow-sm"
                : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
            }`}
          >
            Newest
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto min-h-0">
        {isLoading ? (
          <div className="flex items-center justify-center p-8">
            <Loader2 className="w-4 h-4 animate-spin text-zinc-500" />
          </div>
        ) : flows.length === 0 ? (
          <div className="p-4 text-center text-xs text-zinc-600">
            {query ? "No flows found" : "No flows in registry"}
          </div>
        ) : (
          <div className="px-2 pb-2 space-y-1">
            {flows.map((flow) => (
              <RegistryFlowCard
                key={flow.slug}
                flow={flow}
                isSelected={flow.slug === selectedSlug}
                onClick={() => onSelect(flow)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
