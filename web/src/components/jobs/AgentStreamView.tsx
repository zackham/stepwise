import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useAgentStream, buildSegmentsFromEvents } from "@/hooks/useAgentStream";
import { useAgentOutput } from "@/hooks/useStepwise";
import { usePretextMeasure } from "@/hooks/usePretextMeasure";
import type { StreamSegment } from "@/hooks/useAgentStream";
import { useLogSearch } from "@/hooks/useLogSearch";
import { LogSearchBar } from "@/components/logs/LogSearchBar";
import { cn } from "@/lib/utils";
import { SegmentRow, VIRTUAL_THRESHOLD } from "./StreamSegments";

interface AgentStreamViewProps {
  runId: string;
  isLive: boolean;
  startedAt?: string | null;
  costUsd?: number | null;
  billingMode?: string;
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
    (s) => s.type === "tool" && s.tool.status === "running",
  );

  return (
    <>
      {segments.map((seg, i) => (
        <SegmentRow
          key={seg.type === "tool" ? seg.tool.id : i}
          segment={seg}
          searchRegex={searchRegex}
          hasActiveSearch={hasActiveSearch}
        />
      ))}
      {showCursor && !hasRunningTool && (
        <span className="inline-block w-0.5 h-4 bg-blue-400 animate-pulse align-middle ml-0.5" />
      )}
    </>
  );
}

function VirtualizedSegments({
  segments,
  showCursor,
  searchRegex,
  hasActiveSearch,
  version,
}: {
  segments: StreamSegment[];
  showCursor: boolean;
  searchRegex?: RegExp | null;
  hasActiveSearch?: boolean;
  version: number;
}) {
  const parentRef = useRef<HTMLDivElement>(null);
  const { containerRef: measureRef, estimateHeight } = usePretextMeasure();
  const userScrolledRef = useRef(false);

  const setContainerRef = useCallback((el: HTMLDivElement | null) => {
    (parentRef as React.MutableRefObject<HTMLDivElement | null>).current = el;
    (measureRef as React.MutableRefObject<HTMLDivElement | null>).current = el;
  }, []);

  const hasRunningTool = segments.length > 0 &&
    segments[segments.length - 1].type === "tool" &&
    (segments[segments.length - 1] as { type: "tool"; tool: ToolCallState }).tool.status === "running";

  const virtualizer = useVirtualizer({
    count: segments.length + (showCursor && !hasRunningTool ? 1 : 0),
    getScrollElement: () => parentRef.current,
    estimateSize: (index) => {
      if (index >= segments.length) return 20; // cursor row
      const seg = segments[index];
      if (seg.type === "tool") return 36;
      return estimateHeight(
        seg.text,
        Math.max(1, Math.ceil(seg.text.length / 80)) * 20,
      );
    },
    overscan: 10,
  });

  // Auto-scroll to bottom when new content arrives
  useEffect(() => {
    if (userScrolledRef.current) return;
    const count = segments.length + (showCursor && !hasRunningTool ? 1 : 0);
    if (count > 0) {
      virtualizer.scrollToIndex(count - 1, { align: "end" });
    }
  }, [version, segments.length]);

  const handleScroll = useCallback(() => {
    const el = parentRef.current;
    if (!el) return;
    const nearBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 50;
    userScrolledRef.current = !nearBottom;
  }, []);

  return (
    <div
      ref={setContainerRef}
      onScroll={handleScroll}
      className="max-h-96 overflow-y-auto"
    >
      <div
        style={{
          height: virtualizer.getTotalSize(),
          width: "100%",
          position: "relative",
        }}
      >
        {virtualizer.getVirtualItems().map((virtualRow) => {
          if (virtualRow.index >= segments.length) {
            // Cursor row
            return (
              <div
                key="cursor"
                ref={virtualizer.measureElement}
                data-index={virtualRow.index}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  transform: `translateY(${virtualRow.start}px)`,
                }}
                className="px-3"
              >
                <span className="inline-block w-0.5 h-4 bg-blue-400 animate-pulse align-middle ml-0.5" />
              </div>
            );
          }
          const seg = segments[virtualRow.index];
          return (
            <div
              key={seg.type === "tool" ? seg.tool.id : virtualRow.index}
              ref={virtualizer.measureElement}
              data-index={virtualRow.index}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${virtualRow.start}px)`,
              }}
              className="px-3"
            >
              <SegmentRow
                segment={seg}
                searchRegex={searchRegex}
                hasActiveSearch={hasActiveSearch}
              />
            </div>
          );
        })}
      </div>
    </div>
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

  // Always fetch historical output — for live mode this provides backfill,
  // for historical mode this is the only data source.
  // Use staleTime: 0 for live mode so re-mounts get fresh backfill.
  const { data: historyData } = useAgentOutput(runId, isLive ? { staleTime: 0 } : undefined);

  // Live stream with backfill from REST API
  const { streamState, version } = useAgentStream(
    isLive ? runId : undefined,
    isLive ? (historyData?.events ?? null) : null,
  );

  // Historical replay (non-live)
  const replayState = useMemo(() => {
    if (isLive || !historyData?.events?.length) return null;
    return buildSegmentsFromEvents(historyData.events);
  }, [isLive, historyData]);

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
  const useVirtual = segments.length > VIRTUAL_THRESHOLD;

  // Auto-scroll for flat (non-virtual) mode
  useEffect(() => {
    if (useVirtual) return;
    const el = scrollRef.current;
    if (!el || userScrolledRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [version, segments.length, useVirtual]);

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
    const backfillLoading = !historyData;
    return (
      <div className="bg-zinc-50/50 dark:bg-zinc-950/50 border border-zinc-300/50 dark:border-zinc-800/50 rounded-lg p-4">
        <div className="flex items-center gap-2">
          <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
          <span className="text-sm text-blue-600 dark:text-blue-300">
            {backfillLoading ? "Loading output..." : "Agent starting..."}
          </span>
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
      {useVirtual ? (
        <VirtualizedSegments
          segments={segments}
          showCursor={isLive}
          searchRegex={search.compiledRegex}
          hasActiveSearch={hasActiveSearch}
          version={version}
        />
      ) : (
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
      )}
      {usage && <UsageBar usage={usage} />}
    </div>
  );
}
