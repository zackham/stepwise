import { useState, useEffect, useMemo, useCallback } from "react";
import { useParams, useNavigate, Link } from "@tanstack/react-router";
import { useJob, useRuns, useJobTree, useJobOutput, useJobCost, useStepwiseMutations } from "@/hooks/useStepwise";
import { useConfig } from "@/hooks/useConfig";
import { JobList } from "@/components/jobs/JobList";
import { CreateJobDialog } from "@/components/jobs/CreateJobDialog";
import { FlowDagView } from "@/components/dag/FlowDagView";
import { StepDetailPanel } from "@/components/jobs/StepDetailPanel";
import { DataFlowPanel } from "@/components/dag/DataFlowPanel";
import { JobControls } from "@/components/jobs/JobControls";
import { JobStatusBadge } from "@/components/StatusBadge";
import { JsonView } from "@/components/JsonView";
import type { DagSelection } from "@/lib/dag-layout";
import { useAutoSelectSuspended } from "@/hooks/useAutoSelectSuspended";
import { useAutoExpand } from "@/hooks/useAutoExpand";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { MobileFullScreen } from "@/components/layout/MobileFullScreen";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { Breadcrumb } from "@/components/layout/Breadcrumb";
import {
  PanelRightClose,
  PanelLeftClose,
  PanelRightOpen,
  ScrollText,
  GitBranch,
  GanttChart,
  Package,
  Clock,
  Info,
  Terminal,
  Monitor,
  AlertTriangle,
  DollarSign,
} from "lucide-react";
import type { JobTreeNode, StepDefinition } from "@/lib/types";
import { Skeleton } from "@/components/ui/skeleton";
import { formatDuration } from "@/lib/utils";


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

function parseJsonString(value: string): unknown {
  const trimmed = value.trimStart();
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      return JSON.parse(value);
    } catch {
      // Leave invalid JSON-like strings unchanged.
    }
  }
  return value;
}

function normalizeOutputValue(value: unknown): unknown {
  if (typeof value === "string") {
    const parsed = parseJsonString(value);
    return parsed === value ? value : normalizeOutputValue(parsed);
  }

  if (Array.isArray(value)) {
    return value.map((item) => normalizeOutputValue(item));
  }

  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [
        key,
        normalizeOutputValue(item),
      ])
    );
  }

  return value;
}

export function JobDetailPage() {
  const { jobId } = useParams({ from: "/jobs/$jobId" });
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const { data: job, isLoading } = useJob(jobId);
  const { data: parentJob } = useJob(job?.parent_job_id ?? undefined);
  const { data: jobTree } = useJobTree(jobId);
  const { data: runs = [] } = useRuns(jobId);
  const { data: costData } = useJobCost(jobId);
  const { data: configData } = useConfig();
  const [selection, setSelection] = useState<DagSelection>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [rightPanelOpen, setRightPanelOpen] = useState<boolean | null>(null);
  const [expandedStep, setExpandedStep] = useState(false);
  const mutations = useStepwiseMutations();
  const { expandedSteps, toggleExpand } = useAutoExpand(jobId, runs, job, jobTree ?? null);

  const isTerminal =
    job?.status === "completed" || job?.status === "failed" || job?.status === "cancelled";
  const { data: outputs } = useJobOutput(job?.id, isTerminal);
  // Find the first failed step run for the error summary banner
  const failedRun = useMemo(() => {
    if (job?.status !== "failed") return null;
    return runs.find((r) => r.status === "failed") ?? null;
  }, [job?.status, runs]);

  // Derive selectedStep from selection for backward compatibility
  const selectedStep = selection?.kind === "step" ? selection.stepName : null;

  // Build latestRuns map for DataFlowPanel
  const latestRuns = useMemo(() => {
    const map: Record<string, (typeof runs)[number]> = {};
    for (const run of runs) {
      const existing = map[run.step_name];
      if (!existing || run.attempt > existing.attempt) {
        map[run.step_name] = run;
      }
    }
    return map;
  }, [runs]);
  const normalizedOutputs = useMemo(
    () => (outputs ? normalizeOutputValue(outputs) as Record<string, unknown> : null),
    [outputs]
  );

  const handleSelectStep = useCallback((stepName: string | null) => {
    setSelection(stepName ? { kind: "step", stepName } : null);
  }, []);

  const handleSelectDataFlow = useCallback((sel: DagSelection) => {
    setSelection(sel);
  }, []);

  // Auto-select newly suspended external steps
  useAutoSelectSuspended(runs, selection, handleSelectStep);

  // Reset state when switching jobs
  useEffect(() => {
    setSelection(null);
    setRightPanelOpen(null);
    setExpandedStep(false);
  }, [jobId]);

  // Auto-open right panel for terminal jobs (only on initial load)
  useEffect(() => {
    if (rightPanelOpen === null && job) {
      const terminal =
        job.status === "completed" || job.status === "failed" || job.status === "cancelled";
      setRightPanelOpen(terminal);
    }
  }, [job, rightPanelOpen]);

  // Escape key clears any selection
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape" && selection) setSelection(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [selection]);

  if (isLoading) {
    return (
      <div className="flex h-full" data-testid="job-detail-skeleton">
        {/* Sidebar skeleton */}
        <div className="hidden md:flex w-72 border-r border-border flex-col shrink-0">
          <div className="p-3 border-b border-border">
            <Skeleton className="h-5 w-24" />
          </div>
          <div className="p-2 space-y-1">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="px-3 py-1.5">
                <div className="flex items-start gap-2">
                  <Skeleton className="w-3.5 h-3.5 mt-0.5 rounded shrink-0" />
                  <div className="flex-1 space-y-1.5">
                    <Skeleton className="h-4 w-3/4" />
                    <Skeleton className="h-3 w-1/2" />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
        {/* Main content skeleton */}
        <div className="flex-1 flex flex-col">
          <div className="h-10 border-b border-border flex items-center px-4 gap-3">
            <Skeleton className="h-4 w-48" />
            <div className="flex-1" />
            <Skeleton className="h-5 w-16 rounded-full" />
          </div>
          <div className="flex-1 flex items-center justify-center">
            <div className="space-y-3 text-center">
              <Skeleton className="h-32 w-64 mx-auto rounded-lg" />
              <Skeleton className="h-4 w-40 mx-auto" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-3 text-zinc-500">
        <span className="text-sm">Job not found</span>
        <Link
          to="/jobs"
          className="text-sm text-blue-400 hover:text-blue-300 underline underline-offset-2"
        >
          Back to Jobs
        </Link>
      </div>
    );
  }

  const resolvedStep = selectedStep
    ? resolveStep(selectedStep, job.id, job.workflow, jobTree ?? null)
    : null;

  const stepCount = Object.keys(job.workflow.steps).length;
  const hasInputs = job.inputs && Object.keys(job.inputs).length > 0;
  const hasOutputs = normalizedOutputs && Object.keys(normalizedOutputs).length > 0;
  const stale = job.status === "running" && job.created_by !== "server" &&
    (!job.heartbeat_at || Date.now() - new Date(job.heartbeat_at).getTime() > 60_000);

  // Determine what the right panel shows
  const isDataFlowSelection =
    selection?.kind === "edge-field" ||
    selection?.kind === "flow-input" ||
    selection?.kind === "flow-output";
  const isRootWorkflowStepSelection =
    selection?.kind === "step" &&
    Boolean(job.workflow.steps[selection.stepName]);
  const showRightPanel = rightPanelOpen || !!resolvedStep || isDataFlowSelection;

  return (
    <div className="flex h-full">
      {/* Left sidebar: job list (hidden on mobile) */}
      {!isMobile && !sidebarCollapsed && (
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
                navigate({ to: "/jobs/$jobId", params: { jobId: id }, search: true })
              }
            />
          </div>
        </div>
      )}

      {/* Collapse toggle when sidebar is hidden (desktop only) */}
      {!isMobile && sidebarCollapsed && (
        <button
          onClick={() => setSidebarCollapsed(false)}
          className="w-8 border-r border-border flex items-center justify-center text-zinc-500 hover:text-foreground hover:bg-zinc-200/50 dark:hover:bg-zinc-800/50 shrink-0"
        >
          <PanelRightClose className="w-4 h-4" />
        </button>
      )}

      {/* Center: header + controls + DAG */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Breadcrumb */}
        <Breadcrumb
          segments={[
            { label: "Jobs", to: "/jobs" },
            ...(job.parent_job_id
              ? [{ label: parentJob?.name || parentJob?.objective || job.parent_job_id, to: "/jobs/$jobId", params: { jobId: job.parent_job_id } }]
              : []),
            { label: job.name || job.objective || "Untitled Job" },
          ]}
        />

        {/* Job header */}
        <div className="px-4 py-2 border-b border-border bg-zinc-50/30 dark:bg-zinc-950/30 shrink-0">
          {isMobile ? (
            <>
              {/* Mobile Row 1: name + status */}
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold truncate text-foreground flex-1 min-w-0">
                  {job.name || job.objective || "Untitled Job"}
                </h2>
                <JobStatusBadge status={job.status} />
                {stale && (
                  <span className="flex items-center gap-0.5 text-amber-500 text-[10px]">
                    <AlertTriangle className="w-3 h-3" />
                  </span>
                )}
              </div>
              {/* Mobile Row 2: duration + cost | route icons */}
              <div className="flex items-center justify-between mt-1">
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-zinc-600 flex items-center gap-0.5">
                    <Clock className="w-2.5 h-2.5" />
                    {formatDuration(job.created_at, job.updated_at)}
                  </span>
                  {costData && (costData.cost_usd > 0 || costData.billing_mode === "subscription") && (
                    <span className="text-[10px] text-zinc-600 flex items-center gap-0.5">
                      <DollarSign className="w-2.5 h-2.5" />
                      {costData.billing_mode === "subscription"
                        ? "$0 (Max)"
                        : `$${costData.cost_usd.toFixed(4)}`}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-1">
                  <Link
                    to="/jobs/$jobId/events"
                    params={{ jobId }}
                    className="flex items-center justify-center min-w-[44px] min-h-[44px] text-xs text-zinc-500 hover:text-foreground rounded hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50"
                    aria-label="Events"
                    title="Events"
                  >
                    <ScrollText className="w-3.5 h-3.5" />
                  </Link>
                  <Link
                    to="/jobs/$jobId/timeline"
                    params={{ jobId }}
                    className="flex items-center justify-center min-w-[44px] min-h-[44px] text-xs text-zinc-500 hover:text-foreground rounded hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50"
                    aria-label="Timeline"
                    title="Timeline"
                  >
                    <GanttChart className="w-3.5 h-3.5" />
                  </Link>
                  <Link
                    to="/jobs/$jobId/tree"
                    params={{ jobId }}
                    className="flex items-center justify-center min-w-[44px] min-h-[44px] text-xs text-zinc-500 hover:text-foreground rounded hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50"
                    aria-label="Tree"
                    title="Tree"
                  >
                    <GitBranch className="w-3.5 h-3.5" />
                  </Link>
                  {!showRightPanel && (
                    <button
                      onClick={() => setRightPanelOpen(true)}
                      className="flex items-center justify-center min-w-[44px] min-h-[44px] text-xs text-zinc-500 hover:text-foreground rounded hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50"
                      aria-label="Details"
                      title="Details"
                    >
                      <Info className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="flex items-center gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <h2 className="text-sm font-semibold truncate text-foreground">
                    {job.name || job.objective || "Untitled Job"}
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
                  {costData && (costData.cost_usd > 0 || costData.billing_mode === "subscription") && (
                    <span
                      className="text-[10px] text-zinc-600 flex items-center gap-0.5"
                      title={costData.billing_mode === "subscription"
                        ? "Claude Max subscription — no API cost"
                        : "API cost"}
                    >
                      <DollarSign className="w-2.5 h-2.5" />
                      {costData.billing_mode === "subscription"
                        ? "$0 (Max)"
                        : `$${costData.cost_usd.toFixed(4)}`}
                    </span>
                  )}
                </div>
                <div className="text-[10px] font-mono text-zinc-600 mt-0.5 break-all flex items-center gap-2 flex-wrap">
                  {job.name && job.objective && (
                    <span className="font-sans text-zinc-500">{job.objective}</span>
                  )}
                  {job.workflow.metadata?.name && (
                    <Link
                      to="/flows/$flowName"
                      params={{ flowName: job.workflow.metadata.name }}
                      className="font-sans text-blue-400 hover:text-blue-300 underline underline-offset-2"
                    >
                      {job.workflow.metadata.name}
                    </Link>
                  )}
                  <span>{job.id}</span>
                </div>
              </div>

              <div className="flex items-center gap-1 shrink-0">
                <Link
                  to="/jobs/$jobId/events"
                  params={{ jobId }}
                  className="flex items-center gap-1 text-xs text-zinc-500 hover:text-foreground px-2 py-1 rounded hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50"
                >
                  <ScrollText className="w-3.5 h-3.5" />
                  Events
                </Link>
                <Link
                  to="/jobs/$jobId/timeline"
                  params={{ jobId }}
                  className="flex items-center gap-1 text-xs text-zinc-500 hover:text-foreground px-2 py-1 rounded hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50"
                >
                  <GanttChart className="w-3.5 h-3.5" />
                  Timeline
                </Link>
                <Link
                  to="/jobs/$jobId/tree"
                  params={{ jobId }}
                  className="flex items-center gap-1 text-xs text-zinc-500 hover:text-foreground px-2 py-1 rounded hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50"
                >
                  <GitBranch className="w-3.5 h-3.5" />
                  Tree
                </Link>
                {!showRightPanel && (
                  <button
                    onClick={() => setRightPanelOpen(true)}
                    className="flex items-center gap-1 text-xs text-zinc-500 hover:text-foreground px-2 py-1 rounded hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50"
                  >
                    <Info className="w-3.5 h-3.5" />
                    Details
                  </button>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Controls */}
        <JobControls job={job} selectedStep={selectedStep} runs={runs} />

        {/* Error summary banner */}
        {failedRun && (
          <div className="flex items-center gap-2 px-4 py-2 border-b border-red-900/50 bg-red-950/30 text-xs">
            <AlertTriangle className="w-3.5 h-3.5 text-red-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <span className="text-red-300 font-medium">
                Step "{failedRun.step_name}" failed
              </span>
              {failedRun.error && (
                <span className="text-red-400/70 font-mono ml-2 truncate">
                  — {failedRun.error.split("\n")[0]}
                </span>
              )}
            </div>
            <button
              type="button"
              className="text-amber-400 hover:text-amber-300 text-xs font-medium whitespace-nowrap shrink-0"
              onClick={() =>
                mutations.rerunStep.mutate({
                  jobId: job.id,
                  stepName: failedRun.step_name,
                })
              }
              disabled={mutations.rerunStep.isPending}
            >
              Retry Step
            </button>
            <button
              type="button"
              className="text-red-400 hover:text-red-300 underline underline-offset-2 whitespace-nowrap shrink-0"
              onClick={() => handleSelectStep(failedRun.step_name)}
            >
              View Details
            </button>
          </div>
        )}

        {/* DAG */}
        <div className="flex-1 overflow-hidden">
          <FlowDagView
            workflow={job.workflow}
            runs={runs}
            jobTree={jobTree ?? null}
            expandedSteps={expandedSteps}
            onToggleExpand={toggleExpand}
            selectedStep={selectedStep}
            onSelectStep={handleSelectStep}
            onNavigateSubJob={(subJobId) =>
              navigate({ to: "/jobs/$jobId", params: { jobId: subJobId } })
            }
            onFulfillWatch={(runId, payload) =>
              mutations.fulfillWatch.mutate({ runId, payload })
            }
            isFulfilling={mutations.fulfillWatch.isPending}
            selection={selection}
            onSelectDataFlow={handleSelectDataFlow}
            flowName={job.workflow.metadata?.name || job.name || job.objective || "Flow"}
            jobStatus={job.status}
          />
        </div>
      </div>

      {/* Right sidebar: step details, data flow, or job details */}
      {(() => {
        const panelContent = showRightPanel ? (
          (isDataFlowSelection || isRootWorkflowStepSelection) && selection ? (
            <DataFlowPanel
              selection={selection}
              job={job}
              latestRuns={latestRuns}
              outputs={normalizedOutputs}
              onClose={() => setSelection(null)}
            />
          ) : resolvedStep ? (
            <StepDetailPanel
              jobId={resolvedStep.jobId}
              stepDef={resolvedStep.stepDef}
              onClose={() => setSelection(null)}
              onExpand={() => setExpandedStep(true)}
            />
          ) : (
            <>
              {/* Job details header */}
              <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-zinc-50/50 dark:bg-zinc-950/50 shrink-0">
                <span className="text-xs font-medium text-zinc-600 dark:text-zinc-400">Job Details</span>
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
                    <span className="font-mono text-zinc-700 dark:text-zinc-300">{stepCount}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-zinc-500 w-16">Duration</span>
                    <span className="font-mono text-zinc-400">
                      {formatDuration(job.created_at, job.updated_at)}
                    </span>
                  </div>
                  {costData && (costData.cost_usd > 0 || costData.billing_mode === "subscription") && (
                    <div className="flex items-center gap-2">
                      <span className="text-zinc-500 w-16">Cost</span>
                      <span className="font-mono text-zinc-400">
                        {costData.billing_mode === "subscription"
                          ? "$0 (Max)"
                          : `$${costData.cost_usd.toFixed(4)}`}
                      </span>
                    </div>
                  )}
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
                    <div className="max-h-40 overflow-y-auto bg-zinc-50/50 dark:bg-zinc-900/50 rounded border border-zinc-200 dark:border-zinc-800 p-2">
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
                    <div className="max-h-40 overflow-y-auto bg-zinc-50/50 dark:bg-zinc-900/50 rounded border border-zinc-200 dark:border-zinc-800 p-2">
                      <JsonView data={normalizedOutputs} defaultExpanded={false} />
                    </div>
                  </div>
                )}
              </div>
            </>
          )
        ) : null;

        if (isMobile) {
          return (
            <MobileFullScreen
              open={showRightPanel}
              onClose={() => {
                setSelection(null);
                setRightPanelOpen(false);
              }}
              title={
                isDataFlowSelection
                  ? "Data Flow"
                  : resolvedStep
                    ? resolvedStep.stepDef.name
                    : "Job Details"
              }
            >
              {panelContent}
            </MobileFullScreen>
          );
        }

        return showRightPanel ? (
          <div className="w-80 border-l border-border shrink-0 flex flex-col overflow-hidden" style={{ maxHeight: 'calc(100vh - 3rem)' }}>
            {panelContent}
          </div>
        ) : null;
      })()}

      {/* Expanded step overlay */}
      {isMobile ? (
        <MobileFullScreen
          open={expandedStep && !!resolvedStep}
          onClose={() => { setExpandedStep(false); setSelection(null); }}
          title={resolvedStep?.stepDef.name ?? "Step Detail"}
        >
          {resolvedStep && (
            <StepDetailPanel
              jobId={resolvedStep.jobId}
              stepDef={resolvedStep.stepDef}
              onClose={() => { setExpandedStep(false); setSelection(null); }}
              onExpand={() => setExpandedStep(false)}
              expanded={true}
            />
          )}
        </MobileFullScreen>
      ) : (
        <Sheet open={expandedStep && !!resolvedStep} onOpenChange={(open) => !open && setExpandedStep(false)}>
          <SheetContent side="right" showCloseButton={false} className="w-[70vw] max-w-4xl p-0 overflow-y-auto">
            {resolvedStep && (
              <StepDetailPanel
                jobId={resolvedStep.jobId}
                stepDef={resolvedStep.stepDef}
                onClose={() => { setExpandedStep(false); setSelection(null); }}
                onExpand={() => setExpandedStep(false)}
                expanded={true}
              />
            )}
          </SheetContent>
        </Sheet>
      )}
    </div>
  );
}
