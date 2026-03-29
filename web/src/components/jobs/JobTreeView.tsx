import { useJobTree } from "@/hooks/useStepwise";
import { JobStatusBadge, StepStatusBadge } from "@/components/StatusBadge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ChevronRight, Briefcase } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { executorIcon } from "@/lib/executor-utils";
import type { JobTreeNode, StepRun } from "@/lib/types";

interface JobTreeViewProps {
  jobId: string;
  onNavigateToJob: (jobId: string) => void;
}

function TreeNode({
  node,
  depth,
  onNavigateToJob,
}: {
  node: JobTreeNode;
  depth: number;
  onNavigateToJob: (jobId: string) => void;
}) {
  const [expanded, setExpanded] = useState(depth < 2);

  // Group runs by step
  const stepRuns: Record<string, StepRun[]> = {};
  for (const run of node.runs) {
    if (!stepRuns[run.step_name]) {
      stepRuns[run.step_name] = [];
    }
    stepRuns[run.step_name].push(run);
  }

  // Find which runs have sub-jobs
  const subJobByRunId: Record<string, JobTreeNode> = {};
  for (const subJob of node.sub_jobs) {
    if (subJob.job.parent_step_run_id) {
      subJobByRunId[subJob.job.parent_step_run_id] = subJob;
    }
  }

  return (
    <div className={cn("relative", depth > 0 && "ml-4 pl-4")}>
      {depth > 0 && (
        <>
          <div className="absolute left-0 top-0 bottom-0 border-l border-zinc-300/30 dark:border-zinc-700/30" />
          <div className="absolute left-0 top-4 w-4 border-t border-zinc-300/30 dark:border-zinc-700/30" />
        </>
      )}
      <Collapsible open={expanded} onOpenChange={setExpanded}>
        <CollapsibleTrigger className="flex items-center gap-2 w-full py-1.5 hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50 rounded px-2 text-sm">
          <ChevronRight
            className={cn(
              "w-3.5 h-3.5 text-zinc-500 transition-transform shrink-0",
              expanded && "rotate-90"
            )}
          />
          <Briefcase className="w-3.5 h-3.5 text-zinc-500 shrink-0" />
          <span className="text-foreground truncate flex-1 text-left">
            {node.job.objective}
          </span>
          <JobStatusBadge status={node.job.status} />
        </CollapsibleTrigger>

        <CollapsibleContent>
          <div className="ml-2 mt-1 space-y-1">
            <button
              onClick={() => onNavigateToJob(node.job.id)}
              className="text-xs text-blue-400 hover:text-blue-300 mb-1"
            >
              Open job &rarr;
            </button>

            <div className="relative ml-3 border-l border-zinc-300/30 dark:border-zinc-700/30 pl-4 space-y-1">
              {Object.entries(stepRuns).map(([stepName, runs]) => {
                const latestRun = runs.reduce<StepRun | null>(
                  (currentLatest, run) =>
                    !currentLatest || run.attempt > currentLatest.attempt
                      ? run
                      : currentLatest,
                  null
                );
                const subJob = latestRun ? subJobByRunId[latestRun.id] : undefined;
                const stepDef = node.job.workflow.steps[stepName];

                return (
                  <div key={stepName} className="relative space-y-1">
                    <div className="absolute left-[-16px] top-3.5 w-4 border-t border-zinc-300/30 dark:border-zinc-700/30" />
                    <div className="flex items-center gap-2 py-0.5 text-sm min-w-0">
                      <span className="text-zinc-500 shrink-0">
                        {executorIcon(stepDef?.executor.type ?? "sub_flow", "w-3 h-3")}
                      </span>
                      <span className="text-zinc-500 dark:text-zinc-400 font-mono text-xs truncate flex-1">
                        {stepName}
                      </span>
                      {latestRun && <StepStatusBadge status={latestRun.status} />}
                    </div>
                    {subJob && (
                      <TreeNode
                        node={subJob}
                        depth={depth + 1}
                        onNavigateToJob={onNavigateToJob}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

export function JobTreeView({ jobId, onNavigateToJob }: JobTreeViewProps) {
  const { data: tree, isLoading } = useJobTree(jobId);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500">
        Loading...
      </div>
    );
  }

  if (!tree) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500">
        No data
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-4">
        <TreeNode node={tree} depth={0} onNavigateToJob={onNavigateToJob} />
      </div>
    </ScrollArea>
  );
}
