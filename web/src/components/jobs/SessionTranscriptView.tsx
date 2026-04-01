import { useEffect, useRef } from "react";
import { useSessionTranscript } from "@/hooks/useStepwise";
import { useSessionStream } from "@/hooks/useSessionStream";
import { SegmentRow } from "./StreamSegments";
import type { StreamSegment } from "@/hooks/useAgentStream";
import type { SessionBoundary } from "@/lib/types";
import { cn } from "@/lib/utils";

interface SessionTranscriptViewProps {
  jobId: string;
  sessionName: string;
  runIds: string[];
  isLive: boolean;
  highlightStep?: string | null;
  onNavigateToStep: (stepName: string) => void;
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

export function SessionTranscriptView({
  jobId,
  sessionName,
  runIds,
  isLive,
  highlightStep,
  onNavigateToStep,
}: SessionTranscriptViewProps) {
  const { data: transcript } = useSessionTranscript(jobId, sessionName);
  const { state, version } = useSessionStream(
    runIds,
    transcript?.events ?? null,
    transcript?.boundaries ?? null,
    isLive,
  );
  const containerRef = useRef<HTMLDivElement>(null);

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

  // Build interleaved segments + boundaries for rendering
  const { segments, boundaries } = state;

  // Create a map of segment index → boundary for insertion
  // Boundaries point to event_index, which maps to segment positions
  // We render boundaries before their segment groups
  const boundaryAtSegment = new Map<number, SessionBoundary>();
  if (boundaries.length > 0 && segments.length > 0) {
    // Map event indices to approximate segment indices
    // Since buildSegmentsFromEvents merges adjacent text events,
    // we use a simpler approach: insert boundaries at their relative positions
    let segIdx = 0;
    let evtCount = 0;
    for (const b of boundaries) {
      // Walk segments until we reach the event index
      while (segIdx < segments.length && evtCount < b.event_index) {
        evtCount++;
        segIdx++;
      }
      boundaryAtSegment.set(segIdx, b);
    }
  }

  // If no boundaries mapped, just put first boundary at start
  if (boundaries.length > 0 && boundaryAtSegment.size === 0) {
    boundaryAtSegment.set(0, boundaries[0]);
  }

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
          // Render any boundary not yet rendered
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
