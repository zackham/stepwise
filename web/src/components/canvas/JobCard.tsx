import { memo, useCallback } from "react";
import { Link } from "@tanstack/react-router";
import { Check, Clock } from "lucide-react";
import { cn } from "@/lib/utils";
import { MiniDag } from "./MiniDag";
import { JobStatusBadge } from "@/components/StatusBadge";
import { LiveDuration } from "@/components/LiveDuration";
import { EntityContextMenu } from "@/components/menus/EntityContextMenu";
import type { Job, StepRun } from "@/lib/types";

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

export interface JobCardProps {
  job: Job;
  runs: StepRun[];
  dependencyNames?: string[];
  isGroupQueued?: boolean;
  isSelected?: boolean;
  isSelectionActive?: boolean;
  onToggleSelect?: (jobId: string, shiftKey: boolean) => void;
  highlightAs?: "dependency" | "dependent" | null;
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
}

const DAG_W = 268;
const DAG_H = 90;

export const JobCard = memo(function JobCard({ job, runs, dependencyNames, isGroupQueued, isSelected, isSelectionActive, onToggleSelect, highlightAs, onMouseEnter, onMouseLeave }: JobCardProps) {
  const isCompleted = job.status === "completed";
  const isFailed = job.status === "failed";
  const isActive = job.status === "running" || job.status === "paused";

  // Flow name from workflow metadata
  const flowName = job.workflow?.metadata?.name ?? null;
  const displayName = job.name || job.objective;

  // Find current running step
  const currentStep = job.current_step ?? null;

  const handleCheckboxClick = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    onToggleSelect?.(job.id, e.shiftKey);
  }, [job.id, onToggleSelect]);

  return (
    <EntityContextMenu type="job" data={job}>
    <div
      className={cn(
        "relative group/card rounded-lg transition-all duration-200",
        highlightAs === "dependency" && "ring-2 ring-blue-500/50 shadow-lg shadow-blue-500/10",
      )}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      {/* Dependency highlight label */}
      {highlightAs === "dependency" && (
        <div className="absolute -top-6 left-1/2 -translate-x-1/2 z-10 px-2 py-0.5 rounded-full bg-blue-500 text-white text-[10px] font-medium whitespace-nowrap shadow-md">
          dependency
        </div>
      )}
      {/* Selection checkbox */}
      {onToggleSelect && (
        <button
          onClick={handleCheckboxClick}
          className={cn(
            "absolute top-1.5 left-1.5 z-10 w-5 h-5 rounded border flex items-center justify-center transition-all duration-150",
            isSelected
              ? "bg-blue-500 border-blue-500 text-white"
              : "border-zinc-400 dark:border-zinc-600 bg-white/90 dark:bg-zinc-800/90 hover:border-blue-400",
            isSelectionActive || isSelected
              ? "opacity-100"
              : "opacity-0 group-hover/card:opacity-100",
          )}
        >
          {isSelected && <Check className="w-3 h-3" />}
        </button>
      )}
    <Link
      to="/jobs/$jobId"
      params={{ jobId: job.id }}
      search={((prev: Record<string, unknown>) => ({ ...prev, sidebar: "0" })) as never}
      className={cn(
        "block w-full rounded-lg border transition-all duration-200 overflow-hidden",
        "bg-white/80 hover:bg-blue-50 dark:bg-zinc-900/80 dark:hover:bg-blue-950/40",
        "border-zinc-300 hover:border-zinc-400 dark:border-zinc-800 dark:hover:border-zinc-700",
        isFailed && "border-red-900/60 shadow-[0_0_12px_rgba(239,68,68,0.15)]",
        isActive && "border-blue-900/40",
        isSelected && "ring-2 ring-blue-500",
      )}
    >
      {/* Header */}
      <div className="px-3 pt-2.5 pb-1 flex items-start gap-2">
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100 truncate leading-tight">
            {displayName}
          </p>
          {flowName && (
            <p className="text-[11px] text-zinc-500 truncate leading-tight mt-0.5">
              {flowName}
            </p>
          )}
        </div>
        <div className="flex items-center gap-1">
          {isGroupQueued && (
            <span className="flex items-center gap-0.5 text-[10px] text-amber-500 dark:text-amber-400" title="Queued — group at concurrency limit">
              <Clock className="w-3 h-3" />
              queued
            </span>
          )}
          <div className="flex flex-col items-end">
            <JobStatusBadge status={job.status} />
            {(isCompleted || isFailed || job.status === "cancelled") && (
              <span className="text-[11px] text-zinc-500 mt-0.5">{timeAgo(job.updated_at)}</span>
            )}
          </div>
        </div>
      </div>

      {/* Mini DAG */}
      <div className="flex justify-center px-2">
        {job.workflow?.steps ? (
          <MiniDag
            workflow={job.workflow}
            runs={runs}
            width={DAG_W}
            height={DAG_H}
          />
        ) : null}
      </div>

      {/* Footer */}
      <div className="px-3 pb-2 pt-0.5 space-y-0.5">
        <div className="flex items-center text-[11px]">
          <div className="flex-1 min-w-0">
            {currentStep ? (
              <p className="text-zinc-400 truncate">
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
              <span className="text-zinc-600">
                {Object.keys(job.workflow?.steps ?? {}).length} steps
              </span>
            )}
          </div>
          <span className="text-zinc-500 shrink-0 mx-2">
            <LiveDuration
              startTime={isActive || isCompleted || isFailed || job.status === "cancelled" ? job.created_at : null}
              endTime={isCompleted || isFailed || job.status === "cancelled" ? job.updated_at : null}
            />
          </span>
          <div className="flex-1 min-w-0 text-right">
            <span className="text-zinc-600 text-[10px]">started {timeAgo(job.created_at)}</span>
          </div>
        </div>
        {dependencyNames && dependencyNames.length > 0 && (
          <div className="flex items-center gap-1 flex-wrap">
            <span className="inline-flex items-center gap-1 text-[10px] font-medium text-blue-400 bg-blue-500/10 rounded-full px-2 py-0.5">
              depends on {dependencyNames.join(", ")}
            </span>
          </div>
        )}
      </div>
    </Link>
    </div>
    </EntityContextMenu>
  );
});
