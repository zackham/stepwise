import { useState, useMemo, useEffect, useRef } from "react";
import { useSessionTranscript } from "@/hooks/useStepwise";
import { useSessionStream } from "@/hooks/useSessionStream";
import { SegmentList } from "./StreamSegments";
import {
  buildBoundarySegmentMap,
  durationBetween,
} from "./SessionTranscriptView";
import type { SessionInfo, SessionBoundary } from "@/lib/types";
import type { StreamSegment } from "@/hooks/useAgentStream";
import { cn } from "@/lib/utils";
import { ChevronRight, ArrowDown, ArrowUp } from "lucide-react";
import { StepStatusBadge } from "@/components/StatusBadge";
import type { StepRunStatus } from "@/lib/types";
import { useAutoScroll } from "@/hooks/useAutoScroll";

interface StepSessionViewProps {
  jobId: string;
  sessionName: string;
  sessionInfo: SessionInfo;
  stepName: string;
  focusRunId?: string;
  onNavigateToStep: (stepName: string, tab?: string) => void;
  onViewFullSession: () => void;
}

function formatTokens(tokens: number | undefined): string {
  if (tokens == null || tokens === 0) return "";
  if (tokens >= 1000) return `${(tokens / 1000).toFixed(1)}k tok`;
  return `${tokens} tok`;
}

function computeBoundarySummary(segments: StreamSegment[], startSeg: number, endSeg: number): string {
  let toolCount = 0;
  let lineCount = 0;
  for (let i = startSeg; i < endSeg; i++) {
    const seg = segments[i];
    if (seg.type === "tool") toolCount++;
    else if (seg.type === "text") lineCount += seg.text.split("\n").length;
  }
  const parts: string[] = [];
  if (lineCount > 0) parts.push(`${lineCount} lines`);
  if (toolCount > 0) parts.push(`${toolCount} tool${toolCount !== 1 ? "s" : ""}`);
  return parts.join(", ");
}

function StepBoundaryHeader({
  boundary,
  isExpanded,
  onToggle,
  duration,
  summary,
}: {
  boundary: SessionBoundary;
  isExpanded: boolean;
  onToggle: () => void;
  duration: string;
  summary?: string;
}) {
  const tokenStr = formatTokens(boundary.tokens_used);

  return (
    <div
      className="flex items-center gap-2 py-2 px-3 cursor-pointer select-none group hover:bg-zinc-100 dark:hover:bg-zinc-800/30 rounded-md transition-colors"
      onClick={onToggle}
    >
      <ChevronRight
        className={cn(
          "w-3 h-3 text-zinc-500 transition-transform shrink-0",
          isExpanded && "rotate-90"
        )}
      />
      <span className="text-[11px] font-medium text-zinc-700 dark:text-zinc-300">
        #{boundary.attempt}
      </span>
      {boundary.status && (
        <StepStatusBadge status={boundary.status as StepRunStatus} />
      )}

      {/* Spacer */}
      <span className="flex-1" />

      {/* Right side: summary, duration, tokens */}
      {!isExpanded && summary && (
        <span className="text-[10px] text-zinc-600">{summary}</span>
      )}
      {duration && (
        <span className="text-[10px] text-zinc-500 shrink-0">{duration}</span>
      )}
      {tokenStr && (
        <span className="text-[10px] text-zinc-600 shrink-0">{tokenStr}</span>
      )}
    </div>
  );
}

export function StepSessionView({
  jobId,
  sessionName,
  sessionInfo,
  stepName,
  focusRunId,
  onNavigateToStep,
  onViewFullSession,
}: StepSessionViewProps) {
  const { data: transcript } = useSessionTranscript(jobId, sessionName);
  const { state, version } = useSessionStream(
    sessionInfo.run_ids,
    transcript?.events ?? null,
    transcript?.boundaries ?? null,
    sessionInfo.is_active
  );

  const { segments, boundaries, eventToSegment } = state;

  // Filter boundaries to only those for this step
  const stepBoundaries = useMemo(
    () => boundaries.filter((b) => b.step_name === stepName),
    [boundaries, stepName]
  );

  // Other steps that share this session
  const otherSteps = useMemo(
    () => sessionInfo.step_names.filter((s) => s !== stepName),
    [sessionInfo.step_names, stepName]
  );

  // Build boundary-to-segment mapping from ALL boundaries/segments
  const { segmentRangeForBoundary } = useMemo(
    () => buildBoundarySegmentMap(boundaries, segments, eventToSegment),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [boundaries, segments, eventToSegment, version]
  );

  // Map from stepBoundary -> its index in the full boundaries array -> segment range
  const stepBoundaryRanges = useMemo(() => {
    const ranges: Array<[number, number]> = [];
    for (let fullIdx = 0; fullIdx < boundaries.length; fullIdx++) {
      if (boundaries[fullIdx].step_name === stepName) {
        const range = segmentRangeForBoundary.get(fullIdx);
        ranges.push(range ?? [0, 0]);
      }
    }
    return ranges;
  }, [boundaries, stepName, segmentRangeForBoundary]);

  // Compute durations for step boundaries only
  const stepBoundaryDurations = useMemo(() => {
    // For each step boundary, find its full index and the next boundary (any step) after it
    let stepIdx = 0;
    const durations: string[] = [];
    for (let fullIdx = 0; fullIdx < boundaries.length; fullIdx++) {
      if (boundaries[fullIdx].step_name === stepName) {
        const next = boundaries[fullIdx + 1];
        durations.push(
          durationBetween(boundaries[fullIdx].started_at, next?.started_at ?? null)
        );
        stepIdx++;
      }
    }
    return durations;
  }, [boundaries, stepName]);

  // Expand/collapse state
  const buildExpandedSet = (bs: SessionBoundary[]): Set<number> => {
    if (focusRunId) {
      // Only expand the boundary matching focusRunId
      const idx = bs.findIndex((b) => b.run_id === focusRunId);
      return idx >= 0 ? new Set([idx]) : new Set(bs.map((_, i) => i));
    }
    // Default: expand all (step-scoped, usually few runs)
    return new Set(bs.map((_, i) => i));
  };

  const [expandedBoundaries, setExpandedBoundaries] = useState<Set<number>>(
    () => buildExpandedSet(stepBoundaries)
  );

  // When focusRunId changes, update expanded set
  const prevFocusRunId = useRef(focusRunId);
  useEffect(() => {
    if (prevFocusRunId.current !== focusRunId) {
      prevFocusRunId.current = focusRunId;
      setExpandedBoundaries(buildExpandedSet(stepBoundaries));
    }
  }, [focusRunId, stepBoundaries]); // eslint-disable-line react-hooks/exhaustive-deps

  // When boundaries load or grow, expand new ones
  const prevCountRef = useRef(stepBoundaries.length);
  useEffect(() => {
    if (stepBoundaries.length !== prevCountRef.current) {
      if (prevCountRef.current === 0 && stepBoundaries.length > 0) {
        setExpandedBoundaries(buildExpandedSet(stepBoundaries));
      } else if (stepBoundaries.length > prevCountRef.current) {
        setExpandedBoundaries((prev) => {
          const next = new Set(prev);
          for (let i = prevCountRef.current; i < stepBoundaries.length; i++) {
            next.add(i);
          }
          return next;
        });
      }
      prevCountRef.current = stepBoundaries.length;
    }
  }, [stepBoundaries]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleBoundary = (idx: number) => {
    setExpandedBoundaries((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const scrollRef = useRef<HTMLDivElement>(null);
  const { showBackToBottom, showJumpToTop, scrollToBottom, scrollToTop } = useAutoScroll(scrollRef, version, sessionInfo.is_active);

  return (
    <div className="flex-1 relative min-w-0 flex flex-col overflow-hidden">
      {/* Scroll FABs */}
      {showJumpToTop && (
        <div className="absolute top-2 left-0 right-0 z-10 flex justify-center pointer-events-none">
          <button
            onClick={scrollToTop}
            className="pointer-events-auto flex items-center gap-1 px-3 py-1 rounded-full bg-white dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700 text-xs text-zinc-600 dark:text-zinc-300 hover:text-zinc-900 dark:hover:text-white hover:bg-zinc-50 dark:hover:bg-zinc-700 shadow-lg transition-colors cursor-pointer"
          >
            <ArrowUp className="w-3 h-3" />
            Jump to top
          </button>
        </div>
      )}
      {showBackToBottom && (
        <div className="absolute bottom-2 left-0 right-0 z-10 flex justify-center pointer-events-none">
          <button
            onClick={scrollToBottom}
            className="pointer-events-auto flex items-center gap-1 px-3 py-1 rounded-full bg-white dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700 text-xs text-zinc-600 dark:text-zinc-300 hover:text-zinc-900 dark:hover:text-white hover:bg-zinc-50 dark:hover:bg-zinc-700 shadow-lg transition-colors cursor-pointer"
          >
            <ArrowDown className="w-3 h-3" />
            Jump to bottom
          </button>
        </div>
      )}
      <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-0">
      {/* Header: session name, shared steps, full session link (only if multi-step) */}
      {otherSteps.length > 0 && (
        <>
          <div className="px-2 py-1.5 space-y-1.5">
            <div className="text-[11px] font-medium text-zinc-700 dark:text-zinc-300">
              Session: {sessionName}
            </div>
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-[10px] text-zinc-500">Shared with:</span>
              {otherSteps.map((s) => (
                <button
                  key={s}
                  onClick={() => onNavigateToStep(s, "session")}
                  className="text-[10px] px-1.5 py-0.5 rounded-full bg-zinc-200 dark:bg-zinc-800 border border-zinc-300 dark:border-zinc-700 text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200 hover:border-zinc-400 dark:hover:border-zinc-500 transition-colors cursor-pointer"
                >
                  {s}
                </button>
              ))}
            </div>
            <button
              onClick={onViewFullSession}
              className="text-[10px] text-blue-600 dark:text-blue-400 hover:text-blue-500 dark:hover:text-blue-300 transition-colors cursor-pointer"
            >
              Open full session in job details
            </button>
          </div>
          <div className="h-px bg-border mx-1 my-1" />
        </>
      )}

      {/* Boundaries */}
      {stepBoundaries.length === 0 && (
        <p className="text-sm text-zinc-500 text-center py-8">
          No output yet
        </p>
      )}
      {stepBoundaries.map((boundary, sIdx) => {
        const isExpanded = expandedBoundaries.has(sIdx);
        const [startSeg, endSeg] = stepBoundaryRanges[sIdx] ?? [0, 0];

        return (
          <div key={`${boundary.run_id}-${sIdx}`}>
            <StepBoundaryHeader
              boundary={boundary}
              isExpanded={isExpanded}
              onToggle={() => toggleBoundary(sIdx)}
              duration={stepBoundaryDurations[sIdx]}
              summary={computeBoundarySummary(segments, startSeg, endSeg)}
            />
            {isExpanded && (
              <div className="pl-4 pr-1">
                <SegmentList segments={segments.slice(startSeg, endSeg)} />
                {startSeg === endSeg && (
                  <p className="text-[10px] text-zinc-600 py-1 px-3">
                    No output yet
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })}

    </div>
    </div>
  );
}
