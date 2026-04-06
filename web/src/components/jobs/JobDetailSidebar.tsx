import { useJobOutput, useJobCost, useRuns } from "@/hooks/useStepwise";
import { JsonView } from "@/components/JsonView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { JobStatusBadge } from "@/components/StatusBadge";
import { EntityDropdownMenu } from "@/components/menus/EntityDropdownMenu";
import { X, Package, CheckCircle2, XCircle, AlertTriangle } from "lucide-react";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import type { Job } from "@/lib/types";
import { cn, formatDuration, formatCost } from "@/lib/utils";
import { useMemo } from "react";

interface JobDetailSidebarProps {
  job: Job;
  onClose: () => void;
}

export function JobDetailSidebar({ job, onClose }: JobDetailSidebarProps) {
  const { copy: copyId, justCopied: idCopied } = useCopyFeedback();
  const isTerminal =
    job.status === "completed" || job.status === "failed" || job.status === "cancelled";
  const { data: outputs, isLoading } = useJobOutput(job.id, isTerminal);
  const { data: costData } = useJobCost(job.id);
  const { data: runs = [] } = useRuns(job.id);

  const stepCount = Object.keys(job.workflow.steps).length;
  const hasOutputs = outputs && Object.keys(outputs).length > 0;

  // Compute terminal banner info
  const terminalInfo = useMemo(() => {
    if (!isTerminal) return null;

    // Duration: created_at to latest completed_at across runs
    const completedTimes = runs
      .filter((r) => r.completed_at)
      .map((r) => new Date(r.completed_at!).getTime());
    const endTime = completedTimes.length > 0
      ? new Date(Math.max(...completedTimes)).toISOString()
      : job.updated_at;

    const failedRun = job.status === "failed"
      ? runs.find((r) => r.status === "failed") ?? null
      : null;

    return {
      duration: formatDuration(job.created_at, endTime),
      cost: costData?.cost_usd ?? null,
      failedRun,
      outputKeys: outputs ? Object.keys(outputs) : [],
      outputs,
    };
  }, [isTerminal, runs, job, costData, outputs]);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-border">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-foreground text-sm truncate">
              {job.objective || "Untitled Job"}
            </h3>
            <JobStatusBadge status={job.status} />
            <EntityDropdownMenu type="job" data={job} />
          </div>
          <div className="mt-1">
            <span
              onClick={() => copyId(job.id)}
              className={`text-[10px] font-mono cursor-pointer hover:text-blue-400 transition-colors ${idCopied ? "text-green-400" : "text-zinc-600"}`}
              title="Click to copy"
            >
              {job.id}
            </span>
          </div>
        </div>
        <button
          onClick={onClose}
          className="text-zinc-500 hover:text-foreground ml-2 shrink-0"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <ScrollArea className="flex-1 min-h-0">
        <div className="p-4 space-y-4">
          {/* Terminal state banner */}
          {terminalInfo && (
            <div className={cn(
              "rounded-md border p-3 space-y-2 text-xs",
              job.status === "completed" && "border-emerald-500/30 bg-emerald-500/5",
              job.status === "failed" && "border-red-500/30 bg-red-500/5",
              job.status === "cancelled" && "border-zinc-500/30 bg-zinc-500/5",
            )}>
              <div className="flex items-center gap-2">
                {job.status === "completed" && (
                  <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
                )}
                {job.status === "failed" && (
                  <XCircle className="w-4 h-4 text-red-500 shrink-0" />
                )}
                {job.status === "cancelled" && (
                  <AlertTriangle className="w-4 h-4 text-zinc-500 shrink-0" />
                )}
                <span className={cn(
                  "font-medium",
                  job.status === "completed" && "text-emerald-500",
                  job.status === "failed" && "text-red-500",
                  job.status === "cancelled" && "text-zinc-500",
                )}>
                  {job.status === "completed" ? "Completed" : job.status === "failed" ? "Failed" : "Cancelled"}
                </span>
              </div>
              <div className="flex items-center gap-3 text-zinc-500">
                <span>Duration: <span className="text-foreground font-mono">{terminalInfo.duration}</span></span>
                {terminalInfo.cost != null && terminalInfo.cost > 0 && (
                  <span>Cost: <span className="text-foreground font-mono">{formatCost(terminalInfo.cost)}</span></span>
                )}
              </div>
              {job.status === "completed" && terminalInfo.outputKeys.length > 0 && (
                <div className="text-zinc-500 space-y-0.5">
                  {terminalInfo.outputKeys.map((key) => {
                    const val = terminalInfo.outputs?.[key];
                    const preview = val === undefined ? "null"
                      : typeof val === "string" ? (val.length > 60 ? `"${val.slice(0, 57)}..."` : `"${val}"`)
                      : typeof val === "object" ? JSON.stringify(val)?.slice(0, 60) + (JSON.stringify(val)?.length > 60 ? "..." : "")
                      : String(val);
                    return (
                      <div key={key} className="truncate">
                        <span className="text-zinc-400 font-mono">{key}</span>
                        <span className="text-zinc-600 ml-1 font-mono">{preview}</span>
                      </div>
                    );
                  })}
                </div>
              )}
              {job.status === "failed" && terminalInfo.failedRun && (
                <div className="text-red-400 space-y-0.5">
                  <div>
                    Step "<span className="font-medium">{terminalInfo.failedRun.step_name}</span>" failed
                  </div>
                  {terminalInfo.failedRun.error && (
                    <div className="text-red-400/70 truncate font-mono">
                      {terminalInfo.failedRun.error.split("\n")[0].slice(0, 120)}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Summary */}
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="text-zinc-500">Steps</div>
            <div className="text-foreground font-mono">{stepCount}</div>
            <div className="text-zinc-500">Duration</div>
            <div className="text-foreground font-mono">
              {job.status === "staged" || job.status === "pending"
                ? "-"
                : formatDuration(job.created_at, job.updated_at)}
            </div>
            <div className="text-zinc-500">Created</div>
            <div className="text-zinc-500 dark:text-zinc-400 font-mono text-[10px]">
              {new Date(job.created_at).toLocaleString()}
            </div>
            {isTerminal && (
              <>
                <div className="text-zinc-500">Finished</div>
                <div className="text-zinc-500 dark:text-zinc-400 font-mono text-[10px]">
                  {new Date(job.updated_at).toLocaleString()}
                </div>
              </>
            )}
          </div>

          {/* Inputs */}
          {job.inputs && Object.keys(job.inputs).length > 0 && (
            <div>
              <h4 className="text-xs font-medium text-zinc-500 uppercase tracking-wide mb-2">
                Inputs
              </h4>
              <JsonView data={job.inputs} defaultExpanded />
            </div>
          )}

          {/* Outputs */}
          {isTerminal && (
            <div>
              <h4 className="text-xs font-medium text-zinc-500 uppercase tracking-wide mb-2 flex items-center gap-1.5">
                <Package className="w-3 h-3" />
                Outputs
              </h4>
              {isLoading ? (
                <div className="text-zinc-500 text-xs py-2">Loading outputs...</div>
              ) : hasOutputs ? (
                <JsonView data={outputs} defaultExpanded />
              ) : (
                <div className="text-zinc-600 text-xs py-2">No outputs</div>
              )}
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
