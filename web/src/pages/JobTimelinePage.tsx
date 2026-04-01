import { useState, useCallback } from "react";
import { useParams } from "@tanstack/react-router";
import { useJob, useRuns } from "@/hooks/useStepwise";
import { TimelineView } from "@/components/jobs/TimelineView";
import { StepDetailPanel } from "@/components/jobs/StepDetailPanel";
import { Breadcrumb } from "@/components/layout/Breadcrumb";
import { MobileFullScreen } from "@/components/layout/MobileFullScreen";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { ResizablePanel } from "@/components/ui/ResizablePanel";
import type { StepDefinition } from "@/lib/types";

export function JobTimelinePage() {
  const { jobId } = useParams({ from: "/jobs/$jobId/timeline" });
  const { data: job } = useJob(jobId);
  const { data: runs = [] } = useRuns(jobId);
  const isMobile = useIsMobile();
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const jobName = job?.name || job?.objective || "...";

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
        <Breadcrumb
          segments={[
            { label: "Jobs", to: "/jobs" },
            { label: jobName, to: "/jobs/$jobId", params: { jobId } },
            { label: "Timeline" },
          ]}
        />

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
        <MobileFullScreen
          open={!!panel}
          onClose={() => setSelectedStep(null)}
          title={selectedStep ?? "Step Detail"}
        >
          {panel}
        </MobileFullScreen>
      ) : panel ? (
        <ResizablePanel storageKey="stepwise-job-right-panel-width">
          {panel}
        </ResizablePanel>
      ) : null}
    </div>
  );
}
