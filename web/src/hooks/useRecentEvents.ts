import { useState, useEffect, useRef, useCallback } from "react";
import { subscribeTickMessages } from "./useStepwiseWebSocket";
import { fetchJob, fetchJobs, fetchRuns } from "@/lib/api";
import type { Job, StepRun, JobStatus, StepRunStatus } from "@/lib/types";

export interface RecentEvent {
  id: string;
  jobId: string;
  jobName: string;
  kind: "job.completed" | "job.failed" | "job.started" | "step.failed" | "step.suspended";
  description: string;
  timestamp: string;
}

const MAX_EVENTS = 20;
const MAX_AGE_MS = 24 * 60 * 60 * 1000; // 24 hours

const MEANINGFUL_JOB_STATUSES: JobStatus[] = ["completed", "failed", "running"];
const MEANINGFUL_RUN_STATUSES: StepRunStatus[] = ["failed", "suspended"];

function pruneOld(events: RecentEvent[]): RecentEvent[] {
  const cutoff = Date.now() - MAX_AGE_MS;
  return events.filter((e) => new Date(e.timestamp).getTime() > cutoff);
}

/**
 * Subscribes to WebSocket tick messages and builds a local list of
 * meaningful events (job started/completed/failed, step failed/suspended).
 * No extra API endpoint needed — piggybacks on existing job/run fetches.
 */
export function useRecentEvents() {
  const [events, setEvents] = useState<RecentEvent[]>([]);
  const jobStatusRef = useRef<Map<string, JobStatus>>(new Map());
  const seenRunsRef = useRef<Set<string>>(new Set());
  const processingRef = useRef(false);

  const addEvents = useCallback((newEvents: RecentEvent[]) => {
    setEvents((prev) => {
      const combined = [...newEvents, ...prev];
      return pruneOld(combined).slice(0, MAX_EVENTS);
    });
  }, []);

  const clearEvents = useCallback(() => {
    setEvents([]);
  }, []);

  // Backfill: seed events from recent jobs on mount
  const backfilledRef = useRef(false);
  useEffect(() => {
    if (backfilledRef.current) return;
    backfilledRef.current = true;

    (async () => {
      try {
        const resp = await fetchJobs(undefined, true);
        const jobs = resp.jobs ?? resp;
        const cutoff = Date.now() - MAX_AGE_MS;
        const batch: RecentEvent[] = [];

        for (const job of jobs as Job[]) {
          const updatedAt = new Date(job.updated_at).getTime();
          if (updatedAt < cutoff) continue;

          const jobName = job.name || job.objective || job.id.slice(0, 8);
          jobStatusRef.current.set(job.id, job.status);

          if (job.status === "completed" || job.status === "failed") {
            batch.push({
              id: `${job.id}-${job.status}-backfill`,
              jobId: job.id,
              jobName,
              kind: job.status === "completed" ? "job.completed" : "job.failed",
              description: job.status === "completed" ? "Job completed" : "Job failed",
              timestamp: job.updated_at,
            });
          }
        }

        batch.sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
        if (batch.length > 0) {
          addEvents(batch.slice(0, MAX_EVENTS));
        }
      } catch {
        // Ignore backfill errors
      }
    })();
  }, [addEvents]);

  useEffect(() => {
    return subscribeTickMessages(async (msg) => {
      if (msg.changed_jobs.length === 0) return;
      // Prevent overlapping processing batches
      if (processingRef.current) return;
      processingRef.current = true;

      try {
        const batch: RecentEvent[] = [];
        const now = new Date().toISOString();

        for (const jobId of msg.changed_jobs) {
          try {
            const job: Job = await fetchJob(jobId);
            const jobName = job.name || job.objective || jobId.slice(0, 8);
            const prevStatus = jobStatusRef.current.get(jobId);

            // Detect job-level transitions
            if (prevStatus !== job.status && MEANINGFUL_JOB_STATUSES.includes(job.status)) {
              // Don't emit "started" if we already saw this job in a terminal state
              if (job.status === "running" && prevStatus && prevStatus !== "pending" && prevStatus !== "staged" && prevStatus !== "awaiting_approval") {
                // Skip — this is a re-run or something weird
              } else {
                const kind =
                  job.status === "completed"
                    ? "job.completed"
                    : job.status === "failed"
                      ? "job.failed"
                      : "job.started";
                batch.push({
                  id: `${jobId}-${job.status}-${now}`,
                  jobId,
                  jobName,
                  kind: kind as RecentEvent["kind"],
                  description:
                    kind === "job.completed"
                      ? "Job completed"
                      : kind === "job.failed"
                        ? "Job failed"
                        : "Job started",
                  timestamp: job.updated_at || now,
                });
              }
            }
            jobStatusRef.current.set(jobId, job.status);

            // Detect step-level events (suspended / failed runs)
            try {
              const runs: StepRun[] = await fetchRuns(jobId);
              for (const run of runs) {
                if (
                  MEANINGFUL_RUN_STATUSES.includes(run.status) &&
                  !seenRunsRef.current.has(`${run.id}-${run.status}`)
                ) {
                  seenRunsRef.current.add(`${run.id}-${run.status}`);
                  const kind =
                    run.status === "suspended" ? "step.suspended" : "step.failed";
                  batch.push({
                    id: `${run.id}-${run.status}-${now}`,
                    jobId,
                    jobName,
                    kind: kind as RecentEvent["kind"],
                    description:
                      kind === "step.suspended"
                        ? `Step "${run.step_name}" suspended`
                        : `Step "${run.step_name}" failed`,
                    timestamp: run.completed_at || run.started_at || now,
                  });
                }
              }
            } catch {
              // Ignore run fetch errors
            }
          } catch {
            // Ignore individual job fetch errors
          }
        }

        if (batch.length > 0) {
          addEvents(batch);
        }
      } finally {
        processingRef.current = false;
      }
    });
  }, [addEvents]);

  return { events, clearEvents };
}
