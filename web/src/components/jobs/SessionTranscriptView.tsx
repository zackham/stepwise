import { useEffect, useRef, useState, useMemo } from "react";
import { useSessionTranscript } from "@/hooks/useStepwise";
import { useSessionStream } from "@/hooks/useSessionStream";
import { SegmentRow } from "./StreamSegments";
import type { StreamSegment } from "@/hooks/useAgentStream";
import type { SessionBoundary } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ChevronRight } from "lucide-react";
import { StepStatusBadge } from "@/components/StatusBadge";
import type { StepRunStatus } from "@/lib/types";

interface SessionTranscriptViewProps {
  jobId: string;
  sessionName: string;
  runIds: string[];
  isLive: boolean;
  highlightStep?: string | null;
  onNavigateToStep: (stepName: string) => void;
  collapsibleBoundaries?: boolean;
  defaultExpanded?: boolean;
  focusStep?: string;
  onSelectStep?: (stepName: string) => void;
}

/** Compute a duration string between two ISO timestamps */
function durationBetween(start: string | null, end: string | null): string {
  if (!start) return "";
  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : Date.now();
  const ms = endMs - startMs;
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3600000) return `${(ms / 60000).toFixed(1)}m`;
  return `${(ms / 3600000).toFixed(1)}h`;
}

function CollapsibleBoundaryHeader({
  boundary,
  isHighlighted,
  isExpanded,
  onToggle,
  onNavigate,
  onSelect,
  duration,
}: {
  boundary: SessionBoundary;
  isHighlighted: boolean;
  isExpanded: boolean;
  onToggle: () => void;
  onNavigate: () => void;
  onSelect?: () => void;
  duration: string;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 py-1.5 px-3 my-0.5 cursor-pointer select-none group",
        "hover:bg-zinc-800/50 rounded-md transition-colors",
        isHighlighted && "ring-1 ring-violet-500/30 rounded-md bg-violet-500/5"
      )}
      data-step-boundary={boundary.step_name}
      onClick={onToggle}
    >
      <ChevronRight
        className={cn(
          "w-3 h-3 text-zinc-500 transition-transform shrink-0",
          isExpanded && "rotate-90"
        )}
      />
      <button
        onClick={(e) => {
          e.stopPropagation();
          if (onSelect) onSelect();
          else onNavigate();
        }}
        className="text-[11px] font-medium text-blue-400 hover:text-blue-300 transition-colors truncate"
      >
        {boundary.step_name}
      </button>
      {boundary.attempt > 1 && (
        <span className="text-[10px] text-zinc-600 bg-zinc-800 border border-zinc-700 rounded px-1 shrink-0">
          #{boundary.attempt}
        </span>
      )}
      {boundary.status && (
        <StepStatusBadge status={boundary.status as StepRunStatus} />
      )}
      {duration && (
        <span className="text-[10px] text-zinc-600 ml-auto shrink-0">{duration}</span>
      )}
    </div>
  );
}

function StepBoundaryMarker({
  boundary,
  isHighlighted,
  onClick,
}: {
  boundary: SessionBoundary;
  isHighlighted: boolean;
  onClick: () => void;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 py-2 px-3 my-1",
        isHighlighted && "ring-1 ring-violet-500/30 rounded-md bg-violet-500/5"
      )}
      data-step-boundary={boundary.step_name}
    >
      <div className="flex-1 h-px bg-border" />
      <button
        onClick={onClick}
        className="text-[10px] font-medium text-zinc-500 hover:text-zinc-300 bg-zinc-800 border border-border rounded-full px-2.5 py-0.5 transition-colors"
      >
        {boundary.step_name}
        {boundary.attempt > 1 && (
          <span className="text-zinc-600 ml-1">#{boundary.attempt}</span>
        )}
      </button>
      <div className="flex-1 h-px bg-border" />
    </div>
  );
}

/**
 * Build a map of boundary index -> segment range [startSegIdx, endSegIdx).
 * Also returns the mapping of segment index -> boundary for rendering.
 */
function buildBoundarySegmentMap(
  boundaries: SessionBoundary[],
  segments: StreamSegment[],
) {
  const boundaryAtSegment = new Map<number, SessionBoundary>();
  // segmentRangeForBoundary: Map<boundaryIdx, [startSeg, endSeg)>
  const segmentRangeForBoundary = new Map<number, [number, number]>();

  if (boundaries.length > 0 && segments.length > 0) {
    let segIdx = 0;
    let evtCount = 0;
    const boundarySegStarts: number[] = [];

    for (const b of boundaries) {
      while (segIdx < segments.length && evtCount < b.event_index) {
        evtCount++;
        segIdx++;
      }
      boundaryAtSegment.set(segIdx, b);
      boundarySegStarts.push(segIdx);
    }

    // Build ranges
    for (let i = 0; i < boundarySegStarts.length; i++) {
      const start = boundarySegStarts[i];
      const end = i + 1 < boundarySegStarts.length ? boundarySegStarts[i + 1] : segments.length;
      segmentRangeForBoundary.set(i, [start, end]);
    }
  }

  // If no boundaries mapped, put first boundary at start
  if (boundaries.length > 0 && boundaryAtSegment.size === 0) {
    boundaryAtSegment.set(0, boundaries[0]);
    segmentRangeForBoundary.set(0, [0, segments.length]);
  }

  return { boundaryAtSegment, segmentRangeForBoundary };
}

export function SessionTranscriptView({
  jobId,
  sessionName,
  runIds,
  isLive,
  highlightStep,
  onNavigateToStep,
  collapsibleBoundaries = false,
  defaultExpanded = true,
  focusStep,
  onSelectStep,
}: SessionTranscriptViewProps) {
  const { data: transcript } = useSessionTranscript(jobId, sessionName);
  const { state, version } = useSessionStream(
    runIds,
    transcript?.events ?? null,
    transcript?.boundaries ?? null,
    isLive,
  );
  const containerRef = useRef<HTMLDivElement>(null);

  const { segments, boundaries } = state;

  // Compute initial expanded set
  const initialExpandedRef = useRef<Set<number> | null>(null);
  if (initialExpandedRef.current === null) {
    if (focusStep) {
      // Only expand boundaries matching focusStep
      const set = new Set<number>();
      boundaries.forEach((b, i) => {
        if (b.step_name === focusStep) set.add(i);
      });
      initialExpandedRef.current = set;
    } else if (defaultExpanded) {
      initialExpandedRef.current = new Set(boundaries.map((_, i) => i));
    } else {
      initialExpandedRef.current = new Set<number>();
    }
  }

  const [expandedBoundaries, setExpandedBoundaries] = useState<Set<number>>(
    () => initialExpandedRef.current!
  );

  // When focusStep changes, update expanded set
  useEffect(() => {
    if (focusStep) {
      const set = new Set<number>();
      boundaries.forEach((b, i) => {
        if (b.step_name === focusStep) set.add(i);
      });
      setExpandedBoundaries(set);
    }
  }, [focusStep, boundaries]);

  // When boundaries grow (new step starts during live session), auto-expand new ones
  const prevBoundaryCountRef = useRef(boundaries.length);
  useEffect(() => {
    if (boundaries.length > prevBoundaryCountRef.current) {
      setExpandedBoundaries((prev) => {
        const next = new Set(prev);
        for (let i = prevBoundaryCountRef.current; i < boundaries.length; i++) {
          // Auto-expand if defaultExpanded or if matches focusStep
          if (defaultExpanded || (focusStep && boundaries[i].step_name === focusStep)) {
            next.add(i);
          }
        }
        return next;
      });
    }
    prevBoundaryCountRef.current = boundaries.length;
  }, [boundaries.length, defaultExpanded, focusStep, boundaries]);

  // Auto-scroll to highlighted step boundary
  useEffect(() => {
    if (!highlightStep || !containerRef.current) return;
    const el = containerRef.current.querySelector(
      `[data-step-boundary="${highlightStep}"]`
    );
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }, [highlightStep, version]);

  // Build boundary-to-segment mapping
  const { boundaryAtSegment, segmentRangeForBoundary } = useMemo(
    () => buildBoundarySegmentMap(boundaries, segments),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [boundaries, segments, version],
  );

  // Compute durations between boundaries
  const boundaryDurations = useMemo(() => {
    return boundaries.map((b, i) => {
      const next = boundaries[i + 1];
      return durationBetween(b.started_at, next?.started_at ?? null);
    });
  }, [boundaries]);

  const toggleBoundary = (idx: number) => {
    setExpandedBoundaries((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) {
        next.delete(idx);
      } else {
        next.add(idx);
      }
      return next;
    });
  };

  // Non-collapsible rendering (original behavior)
  if (!collapsibleBoundaries) {
    return (
      <div ref={containerRef} className="p-4 space-y-0">
        {segments.length === 0 && boundaries.length === 0 && (
          <p className="text-sm text-zinc-500 text-center py-8">
            No output yet
          </p>
        )}
        {segments.map((seg: StreamSegment, i: number) => (
          <div key={i}>
            {boundaryAtSegment.has(i) && (
              <StepBoundaryMarker
                boundary={boundaryAtSegment.get(i)!}
                isHighlighted={boundaryAtSegment.get(i)!.step_name === highlightStep}
                onClick={() => onNavigateToStep(boundaryAtSegment.get(i)!.step_name)}
              />
            )}
            <SegmentRow segment={seg} />
          </div>
        ))}
        {/* Boundaries that point past the last segment */}
        {boundaries
          .filter((b) => {
            for (const [, mapped] of boundaryAtSegment) {
              if (mapped === b) return false;
            }
            return true;
          })
          .map((b) => (
            <StepBoundaryMarker
              key={b.run_id}
              boundary={b}
              isHighlighted={b.step_name === highlightStep}
              onClick={() => onNavigateToStep(b.step_name)}
            />
          ))}
      </div>
    );
  }

  // Collapsible rendering
  return (
    <div ref={containerRef} className="p-2 space-y-0">
      {segments.length === 0 && boundaries.length === 0 && (
        <p className="text-sm text-zinc-500 text-center py-8">
          No output yet
        </p>
      )}
      {boundaries.map((boundary, bIdx) => {
        const isExpanded = expandedBoundaries.has(bIdx);
        const range = segmentRangeForBoundary.get(bIdx);
        const [startSeg, endSeg] = range ?? [0, 0];

        return (
          <div key={`${boundary.run_id}-${bIdx}`}>
            <CollapsibleBoundaryHeader
              boundary={boundary}
              isHighlighted={boundary.step_name === highlightStep}
              isExpanded={isExpanded}
              onToggle={() => toggleBoundary(bIdx)}
              onNavigate={() => onNavigateToStep(boundary.step_name)}
              onSelect={onSelectStep ? () => onSelectStep(boundary.step_name) : undefined}
              duration={boundaryDurations[bIdx]}
            />
            {isExpanded && (
              <div className="pl-4 pr-1">
                {segments.slice(startSeg, endSeg).map((seg, i) => (
                  <SegmentRow key={startSeg + i} segment={seg} />
                ))}
                {startSeg === endSeg && (
                  <p className="text-[10px] text-zinc-600 py-1 px-3">No output yet</p>
                )}
              </div>
            )}
          </div>
        );
      })}
      {/* Segments before the first boundary (edge case) */}
      {boundaries.length > 0 && (() => {
        const firstBoundaryStart = segmentRangeForBoundary.get(0)?.[0] ?? 0;
        if (firstBoundaryStart > 0) {
          return segments.slice(0, firstBoundaryStart).map((seg, i) => (
            <SegmentRow key={`pre-${i}`} segment={seg} />
          ));
        }
        return null;
      })()}
      {/* If no boundaries but there are segments */}
      {boundaries.length === 0 && segments.map((seg, i) => (
        <SegmentRow key={i} segment={seg} />
      ))}
    </div>
  );
}
