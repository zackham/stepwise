import { useEffect, useRef, useMemo } from "react";
import { useAgentStream, buildSegmentsFromEvents } from "@/hooks/useAgentStream";
import { useAgentOutput } from "@/hooks/useStepwise";
import type { StreamSegment } from "@/hooks/useAgentStream";
import {
  Search,
  FileText,
  Pencil,
  Terminal,
  Cog,
  Check,
  Loader2,
} from "lucide-react";
import { useLogSearch } from "@/hooks/useLogSearch";
import { LogSearchBar } from "@/components/logs/LogSearchBar";
import { highlightMatches, countMatches } from "@/lib/log-search";
import { cn } from "@/lib/utils";

interface AgentStreamViewProps {
  runId: string;
  isLive: boolean;
  startedAt?: string | null;
  costUsd?: number | null;
  billingMode?: string;
}

function toolIcon(kind: string) {
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

function ToolCard({ tool }: { tool: { id: string; title: string; kind: string; status: "running" | "completed" } }) {
  const isRunning = tool.status === "running";
  return (
    <div
      className={`flex items-center gap-1.5 rounded px-2 py-1 my-1 text-xs font-mono ${
        isRunning
          ? "bg-zinc-100 dark:bg-zinc-900 border border-blue-500/30"
          : "bg-zinc-100/50 dark:bg-zinc-900/50 border border-zinc-300/30 dark:border-zinc-700/30"
      }`}
    >
      <span className={isRunning ? "text-blue-400" : "text-zinc-500"}>
        {toolIcon(tool.kind)}
      </span>
      <span className={isRunning ? "text-blue-300" : "text-zinc-500"}>
        {tool.title || tool.kind}
      </span>
      <span className="ml-auto">
        {isRunning ? (
          <Loader2 className="w-3 h-3 text-blue-400 animate-spin" />
        ) : (
          <Check className="w-3 h-3 text-emerald-400" />
        )}
      </span>
    </div>
  );
}

function SegmentRenderer({
  segments,
  showCursor,
  searchRegex,
  hasActiveSearch,
}: {
  segments: StreamSegment[];
  showCursor: boolean;
  searchRegex?: RegExp | null;
  hasActiveSearch?: boolean;
}) {
  const hasRunningTool = segments.some(
    (s) => s.type === "tool" && s.tool.status === "running"
  );

  return (
    <>
      {segments.map((seg, i) => {
        if (seg.type === "text") {
          const matches = searchRegex ? countMatches(seg.text, searchRegex) > 0 : true;
          return (
            <span
              key={i}
              className={cn(
                "whitespace-pre-wrap text-sm font-mono text-zinc-700 dark:text-zinc-300 leading-relaxed",
                hasActiveSearch && !matches && "opacity-40"
              )}
            >
              {searchRegex ? highlightMatches(seg.text, searchRegex) : seg.text}
            </span>
          );
        }
        return <ToolCard key={seg.tool.id} tool={seg.tool} />;
      })}
      {showCursor && !hasRunningTool && (
        <span className="inline-block w-0.5 h-4 bg-blue-400 animate-pulse align-middle ml-0.5" />
      )}
    </>
  );
}

function UsageBar({ usage }: { usage: { used: number; size: number } }) {
  const pct = usage.size > 0 ? (usage.used / usage.size) * 100 : 0;
  return (
    <div className="border-t border-zinc-300/50 dark:border-zinc-800/50 px-3 py-1 flex items-center justify-between">
      <span className="text-[10px] text-zinc-600 font-mono">
        {usage.used.toLocaleString()} / {usage.size.toLocaleString()} tokens
      </span>
      <div className="w-16 h-1 bg-zinc-300 dark:bg-zinc-800 rounded-full overflow-hidden">
        <div
          className="h-full bg-blue-500/50 rounded-full transition-all"
          style={{ width: `${Math.min(pct, 100)}%` }}
        />
      </div>
    </div>
  );
}

export function AgentStreamView({ runId, isLive, startedAt, costUsd, billingMode }: AgentStreamViewProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const userScrolledRef = useRef(false);
  const search = useLogSearch(containerRef);

  // Live stream
  const { streamState, version } = useAgentStream(isLive ? runId : undefined);

  // Historical replay
  const { data: historyData } = useAgentOutput(isLive ? undefined : runId);

  const replayState = useMemo(() => {
    if (!historyData?.events?.length) return null;
    return buildSegmentsFromEvents(historyData.events);
  }, [historyData]);

  const state = isLive ? streamState : replayState;
  const segments = state?.segments ?? [];
  const usage = state?.usage ?? null;

  // Compute match count from text segments
  const totalMatches = useMemo(() => {
    if (!search.compiledRegex) return 0;
    return segments.reduce((sum, seg) => {
      if (seg.type === "text") return sum + countMatches(seg.text, search.compiledRegex);
      return sum;
    }, 0);
  }, [segments, search.compiledRegex]);

  useEffect(() => {
    search.setMatchCount(totalMatches);
  }, [totalMatches]);

  const hasActiveSearch = search.query.length > 0 && !search.regexError;

  // Auto-scroll
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || userScrolledRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [version, segments.length]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 50;
    userScrolledRef.current = !nearBottom;
  };

  if (!isLive && !replayState && !historyData) {
    return null;
  }

  if (segments.length === 0 && isLive) {
    return (
      <div className="bg-zinc-50/50 dark:bg-zinc-950/50 border border-zinc-300/50 dark:border-zinc-800/50 rounded-lg p-4">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
          <span className="text-sm text-blue-600 dark:text-blue-300">Agent starting...</span>
          {startedAt && (
            <span className="text-xs text-zinc-600 ml-auto font-mono">
              {new Date(startedAt).toLocaleTimeString()}
            </span>
          )}
        </div>
        {(billingMode === "subscription" || (costUsd != null && costUsd > 0)) && (
          <span className="text-xs font-mono text-zinc-600 mt-1 block">
            {billingMode === "subscription"
              ? "$0 (Max)"
              : `$${costUsd! < 0.01 ? costUsd!.toFixed(4) : costUsd!.toFixed(2)}`}
          </span>
        )}
      </div>
    );
  }

  if (segments.length === 0) {
    return null;
  }

  return (
    <div ref={containerRef} tabIndex={-1} className="bg-zinc-50/50 dark:bg-zinc-950/50 border border-zinc-300/50 dark:border-zinc-800/50 rounded-lg overflow-hidden">
      <LogSearchBar search={search} />
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="max-h-96 overflow-y-auto p-3"
      >
        <SegmentRenderer
          segments={segments}
          showCursor={isLive}
          searchRegex={search.compiledRegex}
          hasActiveSearch={hasActiveSearch}
        />
      </div>
      {usage && <UsageBar usage={usage} />}
    </div>
  );
}
