import { useJobOutput } from "@/hooks/useStepwise";
import { JsonView } from "@/components/JsonView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { JobStatusBadge } from "@/components/StatusBadge";
import { X, Package } from "lucide-react";
import type { Job } from "@/lib/types";
import { formatDuration } from "@/lib/utils";

interface JobDetailSidebarProps {
  job: Job;
  onClose: () => void;
}

export function JobDetailSidebar({ job, onClose }: JobDetailSidebarProps) {
  const isTerminal =
    job.status === "completed" || job.status === "failed" || job.status === "cancelled";
  const { data: outputs, isLoading } = useJobOutput(job.id, isTerminal);

  const stepCount = Object.keys(job.workflow.steps).length;
  const hasOutputs = outputs && Object.keys(outputs).length > 0;

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
          </div>
          <div className="text-[10px] font-mono text-zinc-600 mt-1">
            {job.id}
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
          {/* Summary */}
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="text-zinc-500">Steps</div>
            <div className="text-foreground font-mono">{stepCount}</div>
            <div className="text-zinc-500">Duration</div>
            <div className="text-foreground font-mono">{formatDuration(job.created_at, job.updated_at)}</div>
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
