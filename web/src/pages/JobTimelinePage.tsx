import { useState, useCallback } from "react";
import { useParams, Link, useNavigate } from "@tanstack/react-router";
import { useJob, useRuns } from "@/hooks/useStepwise";
import { TimelineView } from "@/components/jobs/TimelineView";
import { StepDetailPanel } from "@/components/jobs/StepDetailPanel";
import { JobStatusBadge } from "@/components/StatusBadge";
import { ArrowLeft } from "lucide-react";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { useIsMobile } from "@/hooks/useMediaQuery";
import type { StepDefinition } from "@/lib/types";

export function JobTimelinePage() {
  const { jobId } = useParams({ from: "/jobs/$jobId/timeline" });
  const navigate = useNavigate();
  const { data: job } = useJob(jobId);
  const { data: runs = [] } = useRuns(jobId);
  const isMobile = useIsMobile();
  const [selectedStep, setSelectedStep] = useState<string | null>(null);

  const handleSelectStep = useCallback((stepName: string) => {
    setSelectedStep(stepName);
  }, []);

  const stepDef: StepDefinition | undefined =
    selectedStep ? job?.workflow.steps[selectedStep] : undefined;

  const panel = selectedStep && stepDef ? (
    <StepDetailPanel
      jobId={jobId}
      stepDef={stepDef}
      onClose={() => setSelectedStep(null)}
    />
  ) : null;

  return (
    <div className="flex h-full">
      <div className="flex-1 flex flex-col min-w-0">
        <div className="flex items-center gap-3 px-4 py-2 border-b border-border bg-zinc-950/30">
          <Link
            to="/jobs/$jobId"
            params={{ jobId }}
            className="text-zinc-500 hover:text-foreground"
          >
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold truncate text-foreground">
                {job?.name || job?.objective || "..."} — Timeline
              </h2>
              {job && <JobStatusBadge status={job.status} />}
            </div>
            <div className="text-[10px] font-mono text-zinc-600 mt-0.5">
              {jobId}
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-hidden">
          {job ? (
            <TimelineView job={job} runs={runs} onSelectStep={handleSelectStep} />
          ) : (
            <div className="flex items-center justify-center h-full text-zinc-500">
              Loading...
            </div>
          )}
        </div>
      </div>

      {/* Step detail panel */}
      {isMobile ? (
        <Sheet
          open={!!panel}
          onOpenChange={(open) => { if (!open) setSelectedStep(null); }}
        >
          <SheetContent side="right" showCloseButton={false} className="w-[85vw] sm:max-w-sm p-0 overflow-y-auto">
            {panel}
          </SheetContent>
        </Sheet>
      ) : panel ? (
        <div className="w-80 border-l border-border shrink-0 flex flex-col overflow-hidden" style={{ maxHeight: "calc(100vh - 3rem)" }}>
          {panel}
        </div>
      ) : null}
    </div>
  );
}
