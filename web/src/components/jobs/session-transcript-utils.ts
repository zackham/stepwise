import type { StreamSegment } from "@/hooks/useAgentStream";
import type { SessionBoundary } from "@/lib/types";

/** Compute a duration string between two ISO timestamps */
export function durationBetween(start: string | null, end: string | null): string {
  if (!start) return "";
  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : Date.now();
  const ms = endMs - startMs;
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3600000) return `${(ms / 60000).toFixed(1)}m`;
  return `${(ms / 3600000).toFixed(1)}h`;
}

/**
 * Build a map of boundary index -> segment range [startSegIdx, endSegIdx).
 * Also returns the mapping of segment index -> boundary for rendering.
 */
export function buildBoundarySegmentMap(
  boundaries: SessionBoundary[],
  segments: StreamSegment[],
  eventToSegment?: number[],
) {
  const boundaryAtSegment = new Map<number, SessionBoundary>();
  // segmentRangeForBoundary: Map<boundaryIdx, [startSeg, endSeg)>
  const segmentRangeForBoundary = new Map<number, [number, number]>();

  if (boundaries.length > 0 && segments.length > 0) {
    const boundarySegStarts: number[] = [];

    for (const b of boundaries) {
      let segIdx: number;
      if (eventToSegment && b.event_index < eventToSegment.length) {
        // Use precise mapping from raw event index to segment index
        segIdx = eventToSegment[b.event_index];
        // If the event didn't create a segment (-1), find the next valid one
        if (segIdx < 0) {
          for (let j = b.event_index + 1; j < eventToSegment.length; j++) {
            if (eventToSegment[j] >= 0) { segIdx = eventToSegment[j]; break; }
          }
          if (segIdx < 0) segIdx = segments.length;
        }
      } else if (eventToSegment) {
        segIdx = segments.length;
      } else {
        // Fallback: assume 1:1 (legacy, may be inaccurate)
        segIdx = Math.min(b.event_index, segments.length);
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
