import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { useParams, useNavigate, Link } from "@tanstack/react-router";
import { useJob, useRuns, useJobTree, useJobOutput, useJobCost, useStepwiseMutations } from "@/hooks/useStepwise";
import { useConfig } from "@/hooks/useConfig";
import { JobList } from "@/components/jobs/JobList";
import { CreateJobDialog } from "@/components/jobs/CreateJobDialog";
import { FlowDagView } from "@/components/dag/FlowDagView";
import { StepDetailPanel, StepDetailSkeleton } from "@/components/jobs/StepDetailPanel";
import { DataFlowPanel } from "@/components/dag/DataFlowPanel";
import { HumanControls } from "@/components/jobs/HumanControls";
import { JobStatusBadge } from "@/components/StatusBadge";
import { JsonView } from "@/components/JsonView";
import type { DagSelection } from "@/lib/dag-layout";
import { useAutoSelectSuspended } from "@/hooks/useAutoSelectSuspended";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { useIsMobile } from "@/hooks/useMediaQuery";
import {
  PanelRightClose,
  PanelLeftClose,
  PanelRightOpen,
  ScrollText,
  GitBranch,
  GanttChart,
  ChevronRight,
  Package,
  Clock,
  Info,
  Terminal,
  Monitor,
  AlertTriangle,
  DollarSign,
  ArrowLeft,
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
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());
  const [rightPanelOpen, setRightPanelOpen] = useState<boolean | null>(null);
  const [expandedStep, setExpandedStep] = useState(false);
  const mutations = useStepwiseMutations();

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

  const handleSelectStep = useCallback((stepName: string | null) => {
    setSelection(stepName ? { kind: "step", stepName } : null);
  }, []);

  const handleSelectDataFlow = useCallback((sel: DagSelection) => {
    setSelection(sel);
  }, []);

  const toggleExpand = useCallback((stepName: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepName)) next.delete(stepName);
      else next.add(stepName);
      return next;
    });
  }, []);

  // Auto-select newly suspended external steps
  useAutoSelectSuspended(runs, selection, handleSelectStep);

  // Auto-expand steps that have sub-jobs (runtime or design-time)
  // Walks the full job tree recursively so sub-sub-jobs also auto-expand
  const prevSubJobKeysRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const stepsToExpand: string[] = [];
    const currentKeys = new Set<string>();

    function scanTree(treeNode: JobTreeNode | null) {
      if (!treeNode) return;
      const nodeRuns = treeNode.runs;
      const workflow = treeNode.job.workflow;

      // Runtime sub-jobs: runs with sub_job_id or for_each sub_job_ids
      for (const run of nodeRuns) {
        if (run.sub_job_id) {
          const key = `run:${run.id}`;
          currentKeys.add(key);
          if (!prevSubJobKeysRef.current.has(key)) {
            stepsToExpand.push(run.step_name);
          }
        }
        if (run.executor_state?.for_each === true) {
          const key = `fe:${run.id}`;
          currentKeys.add(key);
          if (!prevSubJobKeysRef.current.has(key)) {
            stepsToExpand.push(run.step_name);
          }
        }
      }

      // Design-time sub-flows: steps with sub_flow that have started running
      for (const [name, step] of Object.entries(workflow.steps)) {
        if (step.sub_flow) {
          const hasRun = nodeRuns.some((r) => r.step_name === name);
          if (hasRun) {
            const key = `def:${treeNode.job.id}:${name}`;
            currentKeys.add(key);
            if (!prevSubJobKeysRef.current.has(key)) {
              stepsToExpand.push(name);
            }
          }
        }
      }

      // Recurse into sub-jobs
      for (const child of treeNode.sub_jobs) {
        scanTree(child);
      }
    }

    scanTree(jobTree ?? null);
    // Also scan top-level runs/job for the case where jobTree hasn't loaded yet
    if (!jobTree && job) {
      for (const run of runs) {
        if (run.sub_job_id) {
          const key = `run:${run.id}`;
          currentKeys.add(key);
          if (!prevSubJobKeysRef.current.has(key)) {
            stepsToExpand.push(run.step_name);
          }
        }
      }
    }

    prevSubJobKeysRef.current = currentKeys;
    if (stepsToExpand.length > 0) {
      setExpandedSteps((prev) => {
        const next = new Set(prev);
        for (const name of stepsToExpand) next.add(name);
        return next;
      });
    }
  }, [runs, job, jobTree]);

  // Reset state when switching jobs
  useEffect(() => {
    setExpandedSteps(new Set());
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
  const hasOutputs = outputs && Object.keys(outputs).length > 0;
  const stale = job.status === "running" && job.created_by !== "server" &&
    (!job.heartbeat_at || Date.now() - new Date(job.heartbeat_at).getTime() > 60_000);

  // Determine what the right panel shows
  const isDataFlowSelection =
    selection?.kind === "edge-field" ||
    selection?.kind === "flow-input" ||
    selection?.kind === "flow-output";
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
                navigate({ to: "/jobs/$jobId", params: { jobId: id } })
              }
            />
          </div>
        </div>
      )}

      {/* Collapse toggle when sidebar is hidden (desktop only) */}
      {!isMobile && sidebarCollapsed && (
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
          {isMobile && (
            <Link
              to="/jobs"
              className="flex items-center gap-1 text-xs text-zinc-500 hover:text-foreground shrink-0 min-w-[44px] min-h-[44px] justify-center"
            >
              <ArrowLeft className="w-4 h-4" />
            </Link>
          )}
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
              className="flex items-center gap-1 text-xs text-zinc-500 hover:text-foreground px-2 py-1 rounded hover:bg-zinc-800/50"
            >
              <ScrollText className="w-3.5 h-3.5" />
              Events
            </Link>
            <Link
              to="/jobs/$jobId/timeline"
              params={{ jobId }}
              className="flex items-center gap-1 text-xs text-zinc-500 hover:text-foreground px-2 py-1 rounded hover:bg-zinc-800/50"
            >
              <GanttChart className="w-3.5 h-3.5" />
              Timeline
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
        <HumanControls job={job} selectedStep={selectedStep} runs={runs} />

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
          />
        </div>
      </div>

      {/* Right sidebar: step details, data flow, or job details */}
      {(() => {
        const panelContent = showRightPanel ? (
          resolvedStep ? (
            <StepDetailPanel
              jobId={resolvedStep.jobId}
              stepDef={resolvedStep.stepDef}
              onClose={() => setSelection(null)}
              onExpand={() => setExpandedStep(true)}
            />
          ) : isDataFlowSelection && selection ? (
            <DataFlowPanel
              selection={selection}
              job={job}
              latestRuns={latestRuns}
              outputs={outputs ?? null}
              onClose={() => setSelection(null)}
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
          )
        ) : null;

        if (isMobile) {
          return (
            <Sheet
              open={showRightPanel}
              onOpenChange={(open) => {
                if (!open) {
                  setSelection(null);
                  setRightPanelOpen(false);
                }
              }}
            >
              <SheetContent side="right" showCloseButton={false} className="w-[85vw] sm:max-w-sm p-0 overflow-y-auto">
                {panelContent}
              </SheetContent>
            </Sheet>
          );
        }

        return showRightPanel ? (
          <div className="w-80 border-l border-border shrink-0 flex flex-col overflow-hidden" style={{ maxHeight: 'calc(100vh - 3rem)' }}>
            {panelContent}
          </div>
        ) : null;
      })()}

      {/* Expanded step overlay */}
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
    </div>
  );
}
