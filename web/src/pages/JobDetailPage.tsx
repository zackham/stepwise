import { useState, useEffect, useCallback, useMemo } from "react";
import { useParams, useNavigate, Link } from "@tanstack/react-router";
import { useJob, useRuns, useJobTree, useJobOutput } from "@/hooks/useStepwise";
import { JobList } from "@/components/jobs/JobList";
import { CreateJobDialog } from "@/components/jobs/CreateJobDialog";
import { FlowDagView } from "@/components/dag/FlowDagView";
import { StepDetailPanel } from "@/components/jobs/StepDetailPanel";
import { HumanControls } from "@/components/jobs/HumanControls";
import { JobStatusBadge } from "@/components/StatusBadge";
import { JsonView } from "@/components/JsonView";
import {
  PanelRightClose,
  PanelLeftClose,
  PanelRightOpen,
  ScrollText,
  GitBranch,
  ChevronRight,
  Package,
  Clock,
  Info,
  Terminal,
  Monitor,
  AlertTriangle,
} from "lucide-react";
import type { JobTreeNode, StepDefinition } from "@/lib/types";
import { cn } from "@/lib/utils";

function resolveStep(
  stepName: string,
  jobId: string,
  workflow: { steps: Record<string, StepDefinition> },
  jobTree: JobTreeNode | null,
): { stepDef: StepDefinition; jobId: string } | null {
  if (workflow.steps[stepName]) {
    return { stepDef: workflow.steps[stepName], jobId };
  }
  if (jobTree) {
    for (const child of jobTree.sub_jobs) {
      const found = resolveStep(stepName, child.job.id, child.job.workflow, child);
      if (found) return found;
    }
  }
  return null;
}

function formatDuration(createdAt: string, updatedAt: string): string {
  const start = new Date(createdAt).getTime();
  const end = new Date(updatedAt).getTime();
  const ms = end - start;
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3600000) return `${(ms / 60000).toFixed(1)}m`;
  return `${(ms / 3600000).toFixed(1)}h`;
}

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
  const [rightPanelOpen, setRightPanelOpen] = useState<boolean | null>(null);

  const isTerminal =
    job?.status === "completed" || job?.status === "failed" || job?.status === "cancelled";
  const { data: outputs } = useJobOutput(job?.id, isTerminal);

  const toggleExpand = useCallback((stepName: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepName)) next.delete(stepName);
      else next.add(stepName);
      return next;
    });
  }, []);

  // Reset state when switching jobs
  useEffect(() => {
    setExpandedSteps(new Set());
    setSelectedStep(null);
    setRightPanelOpen(null);
  }, [jobId]);

  // Auto-open right panel for terminal jobs (only on initial load)
  useEffect(() => {
    if (rightPanelOpen === null && job) {
      const terminal =
        job.status === "completed" || job.status === "failed" || job.status === "cancelled";
      setRightPanelOpen(terminal);
    }
  }, [job, rightPanelOpen]);

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

  const resolvedStep = useMemo(
    () => selectedStep ? resolveStep(selectedStep, job.id, job.workflow, jobTree ?? null) : null,
    [selectedStep, job, jobTree],
  );

  const stepCount = Object.keys(job.workflow.steps).length;
  const hasInputs = job.inputs && Object.keys(job.inputs).length > 0;
  const hasOutputs = outputs && Object.keys(outputs).length > 0;
  const stale = job.status === "running" && job.created_by !== "server" &&
    (!job.heartbeat_at || Date.now() - new Date(job.heartbeat_at).getTime() > 60_000);

  // Right panel shows step details when a step is selected, otherwise job details
  const showRightPanel = rightPanelOpen || !!resolvedStep;

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

      {/* Center: header + controls + DAG */}
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
        <div className="flex items-center gap-3 px-4 py-2 border-b border-border bg-zinc-950/30 shrink-0">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold truncate text-foreground">
                {job.objective || "Untitled Job"}
              </h2>
              <JobStatusBadge status={job.status} />
              {stale && (
                <span className="flex items-center gap-0.5 text-amber-500 text-[10px]">
                  <AlertTriangle className="w-3 h-3" />
                  Stale
                </span>
              )}
              <span className="text-[10px] text-zinc-600 flex items-center gap-0.5">
                <Clock className="w-2.5 h-2.5" />
                {formatDuration(job.created_at, job.updated_at)}
              </span>
            </div>
            <div className="text-[10px] font-mono text-zinc-600 mt-0.5">
              {job.id}
            </div>
          </div>

          <div className="flex items-center gap-1 shrink-0">
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
            {!showRightPanel && (
              <button
                onClick={() => setRightPanelOpen(true)}
                className="flex items-center gap-1 text-xs text-zinc-500 hover:text-foreground px-2 py-1 rounded hover:bg-zinc-800/50"
              >
                <Info className="w-3.5 h-3.5" />
                Details
              </button>
            )}
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
            onNavigateSubJob={(subJobId) =>
              navigate({ to: "/jobs/$jobId", params: { jobId: subJobId } })
            }
          />
        </div>
      </div>

      {/* Right sidebar: step details or job details */}
      {showRightPanel && (
        <div className="w-80 border-l border-border shrink-0 flex flex-col overflow-hidden" style={{ maxHeight: 'calc(100vh - 3rem)' }}>
          {resolvedStep ? (
            <StepDetailPanel
              jobId={resolvedStep.jobId}
              stepDef={resolvedStep.stepDef}
              onClose={() => setSelectedStep(null)}
            />
          ) : (
            <>
              {/* Job details header */}
              <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-zinc-950/50 shrink-0">
                <span className="text-xs font-medium text-zinc-400">Job Details</span>
                <button
                  onClick={() => setRightPanelOpen(false)}
                  className="text-zinc-600 hover:text-zinc-300 p-0.5"
                >
                  <PanelRightOpen className="w-3.5 h-3.5" />
                </button>
              </div>

              {/* Job details content */}
              <div className="flex-1 overflow-y-auto p-3 space-y-4">
                {/* Stats */}
                <div className="text-xs space-y-1.5">
                  <div className="flex items-center gap-2">
                    <span className="text-zinc-500 w-16">Status</span>
                    <JobStatusBadge status={job.status} />
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-zinc-500 w-16">Steps</span>
                    <span className="font-mono text-zinc-300">{stepCount}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-zinc-500 w-16">Duration</span>
                    <span className="font-mono text-zinc-400">
                      {formatDuration(job.created_at, job.updated_at)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-zinc-500 w-16">Created</span>
                    <span className="font-mono text-zinc-500 text-[10px]">
                      {new Date(job.created_at).toLocaleString()}
                    </span>
                  </div>
                  {isTerminal && (
                    <div className="flex items-center gap-2">
                      <span className="text-zinc-500 w-16">Finished</span>
                      <span className="font-mono text-zinc-500 text-[10px]">
                        {new Date(job.updated_at).toLocaleString()}
                      </span>
                    </div>
                  )}
                  <div className="flex items-center gap-2">
                    <span className="text-zinc-500 w-16">Source</span>
                    <span className="flex items-center gap-1 text-zinc-400 text-[10px] font-mono">
                      {job.created_by.startsWith("cli:") ? (
                        <>
                          <Terminal className="w-3 h-3" />
                          CLI (PID {job.runner_pid ?? job.created_by.slice(4)})
                        </>
                      ) : (
                        <>
                          <Monitor className="w-3 h-3" />
                          Server
                        </>
                      )}
                    </span>
                  </div>
                  {job.heartbeat_at && (
                    <div className="flex items-center gap-2">
                      <span className="text-zinc-500 w-16">Heartbeat</span>
                      <span className="font-mono text-zinc-500 text-[10px]">
                        {new Date(job.heartbeat_at).toLocaleString()}
                      </span>
                    </div>
                  )}
                </div>

                {/* Inputs */}
                {hasInputs && (
                  <div>
                    <div className="text-[10px] font-medium text-zinc-500 uppercase tracking-wide mb-1.5">
                      Inputs
                    </div>
                    <div className="max-h-40 overflow-y-auto bg-zinc-900/50 rounded border border-zinc-800 p-2">
                      <JsonView data={job.inputs} defaultExpanded={false} />
                    </div>
                  </div>
                )}

                {/* Outputs */}
                {isTerminal && hasOutputs && (
                  <div>
                    <div className="text-[10px] font-medium text-zinc-500 uppercase tracking-wide mb-1.5 flex items-center gap-1">
                      <Package className="w-3 h-3" />
                      Outputs
                    </div>
                    <div className="max-h-40 overflow-y-auto bg-zinc-900/50 rounded border border-zinc-800 p-2">
                      <JsonView data={outputs} defaultExpanded={false} />
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
