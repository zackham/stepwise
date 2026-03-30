import { useState } from "react";
import { useStepwiseMutations } from "@/hooks/useStepwise";
import { getActionsForEntity } from "@/lib/actions";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Textarea } from "@/components/ui/textarea";
import type { Job, StepRun } from "@/lib/types";
import type { ActionContext } from "@/lib/actions/types";
import { RefreshCw, MessageSquare, AlertTriangle } from "lucide-react";
import { isStale } from "@/lib/actions/job-actions";

const TOOLTIP_MAP: Record<string, string> = {
  "job.pause": "Stop after the current step completes",
  "job.resume": "Continue from where the job was paused",
};

const STYLE_MAP: Record<string, string> = {
  "job.cancel": "border-red-500/30 text-red-400 hover:bg-red-500/10",
  "job.start": "border-blue-500/30 text-blue-400 hover:bg-blue-500/10",
  "job.resume": "border-blue-500/30 text-blue-400 hover:bg-blue-500/10",
  "job.retry": "border-blue-500/30 text-blue-400 hover:bg-blue-500/10",
  "job.take-over": "border-amber-500/30 text-amber-400 hover:bg-amber-500/10",
};

interface JobControlsProps {
  job: Job;
  selectedStep?: string | null;
  runs?: StepRun[];
}

export function JobControls({ job, selectedStep, runs }: JobControlsProps) {
  const mutations = useStepwiseMutations();
  const [contextDialogOpen, setContextDialogOpen] = useState(false);
  const [contextText, setContextText] = useState("");
  const stale = isStale(job);

  // Get lifecycle actions from registry (exclude inject-context — handled separately)
  const lifecycleActions = getActionsForEntity("job", job)
    .filter((a) => a.group === "lifecycle" && a.id !== "job.inject-context");

  // Rerun logic for selected step
  const selectedRun = selectedStep && runs
    ? runs.filter((r) => r.step_name === selectedStep).sort((a, b) => b.attempt - a.attempt)[0] ?? null
    : null;
  const canRerunStep = selectedStep && (
    !selectedRun ||
    selectedRun.status === "completed" ||
    selectedRun.status === "failed" ||
    selectedRun.status === "cancelled"
  );

  const handleInjectContext = () => {
    if (!contextText.trim()) return;
    mutations.injectContext.mutate(
      { jobId: job.id, context: contextText.trim() },
      {
        onSuccess: () => {
          setContextDialogOpen(false);
          setContextText("");
        },
      }
    );
  };

  // Minimal ActionContext for executing registry actions directly
  const ctx: ActionContext = {
    mutations,
    navigate: () => {},
    clipboard: () => {},
    sideEffects: {},
    extraMutations: undefined,
  };

  return (
    <>
      <div className="flex items-center gap-2 p-3 border-b border-border bg-zinc-50/50 dark:bg-zinc-900/50 overflow-x-auto flex-nowrap">
        <TooltipProvider>
          {/* Registry-driven lifecycle buttons */}
          {lifecycleActions.map((action) => {
            const Icon = action.icon;
            const tooltip = TOOLTIP_MAP[action.id];
            const style = STYLE_MAP[action.id] ?? "";

            const btn = (
              <Button
                key={action.id}
                variant="outline"
                size="sm"
                onClick={() => action.execute(job, ctx)}
                className={style}
              >
                {Icon && <Icon className="w-3.5 h-3.5 mr-1.5" />}
                {action.label}
              </Button>
            );

            if (tooltip) {
              return (
                <Tooltip key={action.id}>
                  <TooltipTrigger render={btn} />
                  <TooltipContent>{tooltip}</TooltipContent>
                </Tooltip>
              );
            }
            return btn;
          })}

          {/* Stale warning indicator */}
          {stale && (
            <div className="flex items-center gap-1.5 text-amber-500 text-xs">
              <AlertTriangle className="w-3.5 h-3.5" />
              Owner not responding
            </div>
          )}

          {/* Rerun selected step */}
          {selectedStep && canRerunStep && (
            <Button
              variant="outline"
              size="sm"
              disabled={mutations.rerunStep.isPending}
              onClick={() =>
                mutations.rerunStep.mutate({
                  jobId: job.id,
                  stepName: selectedStep,
                })
              }
            >
              <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
              Restart {selectedStep}
            </Button>
          )}

          <div className="flex-1" />

          {/* Inject Context — only for running jobs */}
          {job.status === "running" && (
            <Tooltip>
              <TooltipTrigger render={
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setContextDialogOpen(true)}
                  className="text-zinc-500 dark:text-zinc-400"
                >
                  <MessageSquare className="w-3.5 h-3.5 mr-1.5" />
                  Inject Context
                </Button>
              } />
              <TooltipContent>Add text to the agent's next prompt</TooltipContent>
            </Tooltip>
          )}
        </TooltipProvider>
      </div>

      {/* Inject Context Dialog */}
      <Dialog open={contextDialogOpen} onOpenChange={setContextDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Inject Context</DialogTitle>
            <DialogDescription>
              Add context information that will be available to future step
              executions.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            value={contextText}
            onChange={(e) => setContextText(e.target.value)}
            placeholder="Enter context information..."
            className="min-h-[100px]"
          />
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setContextDialogOpen(false)}
            >
              Cancel
            </Button>
            <Button
              onClick={handleInjectContext}
              disabled={
                !contextText.trim() || mutations.injectContext.isPending
              }
            >
              Inject
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
