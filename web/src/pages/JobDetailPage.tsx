import { useState, useEffect, useCallback } from "react";
import { useParams, useNavigate, Link } from "@tanstack/react-router";
import { useJob, useRuns, useJobTree } from "@/hooks/useStepwise";
import { JobList } from "@/components/jobs/JobList";
import { CreateJobDialog } from "@/components/jobs/CreateJobDialog";
import { FlowDagView } from "@/components/dag/FlowDagView";
import { StepDetailPanel } from "@/components/jobs/StepDetailPanel";
import { HumanControls } from "@/components/jobs/HumanControls";
import { JobStatusBadge } from "@/components/StatusBadge";
import {
  PanelRightClose,
  PanelLeftClose,
  ScrollText,
  GitBranch,
  ChevronRight,
} from "lucide-react";

export function JobDetailPage() {
  const { jobId } = useParams({ from: "/jobs/$jobId" });
  const navigate = useNavigate();
  const { data: job, isLoading } = useJob(jobId);
  const { data: parentJob } = useJob(job?.parent_job_id ?? undefined);
  const { data: jobTree } = useJobTree(jobId);
  const { data: runs = [] } = useRuns(jobId);
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());

  const toggleExpand = useCallback((stepName: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepName)) next.delete(stepName);
      else next.add(stepName);
      return next;
    });
  }, []);

  // Reset expansion state when switching jobs
  useEffect(() => {
    setExpandedSteps(new Set());
    setSelectedStep(null);
  }, [jobId]);

  // Escape key deselects step
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && selectedStep) setSelectedStep(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [selectedStep]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500">
        Loading...
      </div>
    );
  }

  if (!job) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500">
        Job not found
      </div>
    );
  }

  const selectedStepDef = selectedStep
    ? job.workflow.steps[selectedStep]
    : null;

  return (
    <div className="flex h-full">
      {/* Left sidebar: job list */}
      {!sidebarCollapsed && (
        <div className="w-72 border-r border-border flex flex-col shrink-0 overflow-hidden" style={{ maxHeight: 'calc(100vh - 3rem)' }}>
          <div className="flex items-center justify-between p-2 border-b border-border">
            <CreateJobDialog
              onCreated={(id) =>
                navigate({ to: "/jobs/$jobId", params: { jobId: id } })
              }
            />
            <button
              onClick={() => setSidebarCollapsed(true)}
              className="text-zinc-500 hover:text-foreground p-1"
            >
              <PanelLeftClose className="w-4 h-4" />
            </button>
          </div>
          <div className="flex-1 overflow-hidden">
            <JobList
              selectedJobId={jobId}
              onSelectJob={(id) =>
                navigate({ to: "/jobs/$jobId", params: { jobId: id } })
              }
            />
          </div>
        </div>
      )}

      {/* Collapse toggle when sidebar is hidden */}
      {sidebarCollapsed && (
        <button
          onClick={() => setSidebarCollapsed(false)}
          className="w-8 border-r border-border flex items-center justify-center text-zinc-500 hover:text-foreground hover:bg-zinc-800/50 shrink-0"
        >
          <PanelRightClose className="w-4 h-4" />
        </button>
      )}

      {/* Center: DAG view */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Sub-job breadcrumb */}
        {job.parent_job_id && (
          <div className="flex items-center gap-1 px-4 py-1.5 border-b border-border bg-purple-500/5 text-xs">
            <Link
              to="/jobs/$jobId"
              params={{ jobId: job.parent_job_id }}
              className="text-purple-400 hover:text-purple-300 truncate max-w-[200px]"
            >
              {parentJob?.objective || job.parent_job_id}
            </Link>
            <ChevronRight className="w-3 h-3 text-zinc-600 shrink-0" />
            <span className="text-zinc-400 truncate">
              {job.objective || "Sub-job"}
            </span>
          </div>
        )}

        {/* Job header */}
        <div className="flex items-center gap-3 px-4 py-2 border-b border-border bg-zinc-950/30">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold truncate text-foreground">
                {job.objective || "Untitled Job"}
              </h2>
              <JobStatusBadge status={job.status} />
            </div>
            <div className="text-[10px] font-mono text-zinc-600 mt-0.5">
              {job.id}
            </div>
          </div>

          <div className="flex items-center gap-1">
            <Link
              to="/jobs/$jobId/events"
              params={{ jobId }}
              className="flex items-center gap-1 text-xs text-zinc-500 hover:text-foreground px-2 py-1 rounded hover:bg-zinc-800/50"
            >
              <ScrollText className="w-3.5 h-3.5" />
              Events
            </Link>
            <Link
              to="/jobs/$jobId/tree"
              params={{ jobId }}
              className="flex items-center gap-1 text-xs text-zinc-500 hover:text-foreground px-2 py-1 rounded hover:bg-zinc-800/50"
            >
              <GitBranch className="w-3.5 h-3.5" />
              Tree
            </Link>
          </div>
        </div>

        {/* Controls */}
        <HumanControls job={job} />

        {/* DAG */}
        <div className="flex-1 overflow-hidden">
          <FlowDagView
            workflow={job.workflow}
            runs={runs}
            jobTree={jobTree ?? null}
            expandedSteps={expandedSteps}
            onToggleExpand={toggleExpand}
            selectedStep={selectedStep}
            onSelectStep={setSelectedStep}
          />
        </div>
      </div>

      {/* Right: Step detail panel */}
      {selectedStepDef && (
        <div className="w-96 border-l border-border shrink-0 flex flex-col overflow-hidden" style={{ height: 'calc(100vh - 3rem)' }}>
          <StepDetailPanel
            jobId={jobId}
            stepDef={selectedStepDef}
            onClose={() => setSelectedStep(null)}
          />
        </div>
      )}
    </div>
  );
}
