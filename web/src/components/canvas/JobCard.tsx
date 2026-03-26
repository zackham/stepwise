import { memo } from "react";
import { Link } from "@tanstack/react-router";
import { cn } from "@/lib/utils";
import { MiniDag } from "./MiniDag";
import { JobStatusBadge } from "@/components/StatusBadge";
import { LiveDuration } from "@/components/LiveDuration";
import type { Job, StepRun } from "@/lib/types";

export interface JobCardProps {
  job: Job;
  runs: StepRun[];
  width: number;
  height: number;
}

export const JobCard = memo(function JobCard({ job, runs, width, height }: JobCardProps) {
  const isCompleted = job.status === "completed";
  const isFailed = job.status === "failed";
  const isActive = job.status === "running" || job.status === "paused";

  // Flow name from workflow metadata
  const flowName = job.workflow?.metadata?.name ?? null;
  const displayName = job.name || job.objective;

  // Find current running step
  const currentStep = job.current_step ?? null;

  // Reserve space for header/footer
  const headerH = 44;
  const footerH = 36;
  const dagW = width - 16; // 8px padding each side
  const dagH = Math.max(height - headerH - footerH - 8, 40);

  return (
    <Link
      to="/jobs/$jobId"
      params={{ jobId: job.id }}
      className={cn(
        "block rounded-lg border transition-all duration-200 overflow-hidden",
        "bg-zinc-900/80 hover:bg-zinc-900",
        "border-zinc-800 hover:border-zinc-700",
        isCompleted && "opacity-45 hover:opacity-70",
        isFailed && "border-red-900/60 shadow-[0_0_12px_rgba(239,68,68,0.15)]",
        isActive && "border-blue-900/40",
      )}
      style={{ width, height }}
    >
      {/* Header */}
      <div className="px-3 pt-2.5 pb-1 flex items-start gap-2 min-h-[44px]">
        <div className="flex-1 min-w-0">
          <p className="text-[13px] font-medium text-zinc-100 truncate leading-tight">
            {displayName}
          </p>
          {flowName && (
            <p className="text-[11px] text-zinc-500 truncate leading-tight mt-0.5">
              {flowName}
            </p>
          )}
        </div>
        <JobStatusBadge status={job.status} />
      </div>

      {/* Mini DAG */}
      <div className="flex justify-center px-2">
        <MiniDag
          workflow={job.workflow}
          runs={runs}
          width={dagW}
          height={dagH}
        />
      </div>

      {/* Footer */}
      <div className="px-3 pb-2 pt-0.5 flex items-center justify-between min-h-[36px]">
        <div className="flex-1 min-w-0">
          {currentStep ? (
            <p className="text-[11px] text-zinc-400 truncate">
              <span className="text-zinc-500">{currentStep.name}</span>
              {currentStep.started_at && (
                <span className="text-zinc-600 ml-1.5">
                  <LiveDuration
                    startTime={currentStep.started_at}
                    endTime={currentStep.completed_at ?? null}
                  />
                </span>
              )}
            </p>
          ) : (
            <span className="text-[11px] text-zinc-600">
              {Object.keys(job.workflow?.steps ?? {}).length} steps
            </span>
          )}
        </div>
        <span className="text-[11px] text-zinc-600 shrink-0 ml-2">
          <LiveDuration
            startTime={job.created_at}
            endTime={isCompleted || isFailed || job.status === "cancelled" ? job.updated_at : null}
          />
        </span>
      </div>
    </Link>
  );
});
