/* eslint-disable react-refresh/only-export-components */
import { useState } from "react";
import type { ToolCallState, StreamSegment } from "@/hooks/useAgentStream";
import {
  Search,
  FileText,
  Pencil,
  Terminal,
  Cog,
  Check,
  Loader2,
  ChevronRight,
  X,
} from "lucide-react";
import { highlightMatches, countMatches } from "@/lib/log-search";
import { cn } from "@/lib/utils";

export function toolIcon(kind: string) {
  switch (kind) {
    case "search":
    case "Grep":
    case "Glob":
      return <Search className="w-3 h-3" />;
    case "read":
    case "Read":
      return <FileText className="w-3 h-3" />;
    case "write":
    case "Write":
    case "Edit":
      return <Pencil className="w-3 h-3" />;
    case "execute":
    case "Bash":
      return <Terminal className="w-3 h-3" />;
    default:
      return <Cog className="w-3 h-3" />;
  }
}

export function ToolCard({ tool }: { tool: ToolCallState }) {
  const [expanded, setExpanded] = useState(false);
  const isRunning = tool.status === "running";
  const isFailed = tool.status === "failed";
  const hasOutput = !!tool.output;
  const canExpand = hasOutput && !isRunning;

  return (
    <div
      className={cn(
        "rounded my-1 text-xs font-mono",
        isRunning
          ? "bg-zinc-100 dark:bg-zinc-900 border border-blue-500/30"
          : isFailed
            ? "bg-zinc-100/50 dark:bg-zinc-900/50 border border-red-500/30"
            : "bg-zinc-100/50 dark:bg-zinc-900/50 border border-zinc-300/30 dark:border-zinc-700/30",
      )}
    >
      <button
        type="button"
        onClick={() => canExpand && setExpanded((v) => !v)}
        className={cn(
          "flex items-center gap-1.5 w-full px-2 py-1 text-left",
          canExpand && "cursor-pointer hover:bg-zinc-200/50 dark:hover:bg-zinc-800/50",
          !canExpand && "cursor-default",
        )}
      >
        {canExpand && (
          <ChevronRight
            className={cn(
              "w-3 h-3 text-zinc-500 transition-transform shrink-0",
              expanded && "rotate-90",
            )}
          />
        )}
        <span className={isRunning ? "text-blue-600 dark:text-blue-400" : isFailed ? "text-red-600 dark:text-red-400" : "text-zinc-500"}>
          {toolIcon(tool.kind)}
        </span>
        <span className={cn(
          "truncate",
          isRunning ? "text-blue-700 dark:text-blue-300" : isFailed ? "text-red-700 dark:text-red-300" : "text-zinc-500",
        )}>
          {tool.title || tool.kind}
        </span>
        <span className="ml-auto shrink-0">
          {isRunning ? (
            <Loader2 className="w-3 h-3 text-blue-400 animate-spin" />
          ) : isFailed ? (
            <X className="w-3 h-3 text-red-400" />
          ) : (
            <Check className="w-3 h-3 text-emerald-400" />
          )}
        </span>
      </button>
      {expanded && hasOutput && (
        <div className="px-2 pb-1.5 pt-0.5 border-t border-zinc-300/30 dark:border-zinc-700/30">
          <pre className="text-[11px] text-zinc-600 dark:text-zinc-400 whitespace-pre-wrap break-words max-h-40 overflow-y-auto">
            {tool.output}
          </pre>
        </div>
      )}
    </div>
  );
}

export function SegmentRow({
  segment,
  searchRegex,
  hasActiveSearch,
}: {
  segment: StreamSegment;
  searchRegex?: RegExp | null;
  hasActiveSearch?: boolean;
}) {
  if (segment.type === "text") {
    const matches = searchRegex ? countMatches(segment.text, searchRegex) > 0 : true;
    return (
      <span
        className={cn(
          "whitespace-pre-wrap text-sm font-mono text-zinc-700 dark:text-zinc-300 leading-relaxed",
          hasActiveSearch && !matches && "opacity-40",
        )}
      >
        {searchRegex ? highlightMatches(segment.text, searchRegex) : segment.text}
      </span>
    );
  }
  return <ToolCard tool={segment.tool} />;
}
