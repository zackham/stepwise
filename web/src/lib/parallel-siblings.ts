import type { StepRun } from "@/lib/types";

/** Minimum genuine interval overlap before two steps are flagged as
 *  having run in parallel. Close start times alone don't qualify — a
 *  fast sequential chain (A finishes in 70ms, B starts immediately)
 *  must NOT be flagged. */
export const PARALLEL_MIN_OVERLAP_MS = 100;

/**
 * Compute, for every step with a run, the names of sibling steps whose
 * execution intervals genuinely overlapped (>= PARALLEL_MIN_OVERLAP_MS).
 * Exposes scheduler-level races that the static DAG hides — see the
 * gumball text-quality-check race (cycle 91 stepwise review).
 *
 * Hoisted out of StepNode so the whole map is built once per latestRuns
 * change (2N Date parses) instead of O(N²) Date parsing per node per
 * render.
 */
export function computeParallelSiblings(
  latestRuns: Record<string, StepRun>,
): Record<string, string[]> {
  const now = Date.now();
  const intervals: Array<{ name: string; start: number; end: number }> = [];
  for (const [name, run] of Object.entries(latestRuns)) {
    if (!run?.started_at) continue;
    const start = new Date(run.started_at).getTime();
    let end: number;
    if (run.completed_at) {
      end = new Date(run.completed_at).getTime();
    } else if (
      run.status === "running" ||
      run.status === "suspended" ||
      run.status === "delegated"
    ) {
      end = now;
    } else {
      end = start;
    }
    intervals.push({ name, start, end });
  }
  const result: Record<string, string[]> = {};
  for (const self of intervals) {
    const siblings: string[] = [];
    for (const other of intervals) {
      if (other.name === self.name) continue;
      const overlap =
        Math.min(self.end, other.end) - Math.max(self.start, other.start);
      if (overlap >= PARALLEL_MIN_OVERLAP_MS) {
        siblings.push(other.name);
      }
    }
    if (siblings.length > 0) result[self.name] = siblings;
  }
  return result;
}
