import { useMemo } from "react";
import { JOB_STATUS_COLORS } from "@/lib/status-colors";
import type { Job, JobStatus } from "@/lib/types";

const DISPLAY_ORDER: JobStatus[] = [
  "running",
  "paused",
  "pending",
  "staged",
  "completed",
  "failed",
  "cancelled",
];

interface JobSummaryBarProps {
  jobs: Job[];
}

export function JobSummaryBar({ jobs }: JobSummaryBarProps) {
  const counts = useMemo(() => {
    const map: Partial<Record<JobStatus, number>> = {};
    for (const job of jobs) {
      map[job.status] = (map[job.status] ?? 0) + 1;
    }
    return map;
  }, [jobs]);

  if (jobs.length === 0) return null;

  return (
    <div className="flex items-center gap-3 px-3 h-8 border-b border-border text-xs text-zinc-400">
      <span className="font-medium text-zinc-300">{jobs.length} jobs</span>
      <span className="text-zinc-700">|</span>
      {DISPLAY_ORDER.filter((s) => counts[s]).map((status) => (
        <span key={status} className="flex items-center gap-1.5">
          <span
            className={`inline-block w-2 h-2 rounded-full ${JOB_STATUS_COLORS[status].dot}`}
          />
          <span>{counts[status]} {status}</span>
        </span>
      ))}
    </div>
  );
}
