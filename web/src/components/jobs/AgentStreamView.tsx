import { useEffect, useMemo } from "react";
import { useAgentStream, buildSegmentsFromEvents } from "@/hooks/useAgentStream";
import { useAgentOutput } from "@/hooks/useStepwise";
import { SegmentList } from "./StreamSegments";
import { formatCost } from "@/lib/utils";

interface AgentStreamViewProps {
  runId: string;
  isLive: boolean;
  startedAt?: string | null;
  costUsd?: number | null;
  billingMode?: string;
  onUsage?: (usage: { used: number; size: number } | null) => void;
  compact?: boolean;
}

export function AgentStreamView({ runId, isLive, startedAt, costUsd, billingMode, onUsage, compact }: AgentStreamViewProps) {
  // Always fetch historical output — for live mode this provides backfill,
  // for historical mode this is the only data source.
  const { data: historyData } = useAgentOutput(runId, isLive ? { staleTime: 0 } : undefined);

  // Live stream with backfill from REST API
  const { streamState } = useAgentStream(
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

  // Report usage to parent
  useEffect(() => {
    onUsage?.(usage);
  }, [usage, onUsage]);

  if (!isLive && !replayState && !historyData) return null;

  if (segments.length === 0 && isLive) {
    const backfillLoading = !historyData;
    return (
      <div className="p-4">
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
              : formatCost(costUsd!)}
          </span>
        )}
      </div>
    );
  }

  if (segments.length === 0) return null;

  const hasRunningTool = segments.some(
    (s) => s.type === "tool" && s.tool.status === "running",
  );

  return (
    <div>
      <div className={compact ? "px-3 pt-1 pb-3" : "p-3"}>
        <SegmentList segments={segments} />
        {isLive && !hasRunningTool && (
          <span className="inline-block w-0.5 h-4 bg-blue-400 animate-pulse align-middle ml-0.5" />
        )}
      </div>
    </div>
  );
}
