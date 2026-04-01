import { useState, useEffect, useMemo, useCallback } from "react";
import { useParams, useNavigate, useSearch, Link } from "@tanstack/react-router";
import type { JobDetailSearch } from "@/router";
import { useJob, useRuns, useJobTree, useJobOutput, useJobCost, useStepwiseMutations, useJobSessions } from "@/hooks/useStepwise";
import { SessionTab } from "@/components/jobs/SessionTab";
import { JobList } from "@/components/jobs/JobList";
import { ActionContextProvider } from "@/components/menus/ActionContextProvider";
import { CreateJobDialog } from "@/components/jobs/CreateJobDialog";
import { FlowDagView } from "@/components/dag/FlowDagView";
import { RunView } from "@/components/jobs/RunView";
import { StepConfigView } from "@/components/jobs/StepConfigView";
import { JobOverview } from "@/components/jobs/JobOverview";
import { DataFlowPanel } from "@/components/dag/DataFlowPanel";
import { JobControls } from "@/components/jobs/JobControls";
import { JobStatusBadge } from "@/components/StatusBadge";
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
  Clock,
  Info,
  AlertTriangle,
  DollarSign,
  X,
} from "lucide-react";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import type { JobTreeNode, StepDefinition } from "@/lib/types";
import { Skeleton } from "@/components/ui/skeleton";
import { cn, formatDuration } from "@/lib/utils";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";


function CopyableId({ id }: { id: string }) {
  const { copy, justCopied } = useCopyFeedback();
  return (
    <span
      onClick={() => copy(id)}
      className={cn(
        "cursor-pointer hover:text-blue-400 transition-colors",
        justCopied && "text-green-400"
      )}
      title="Click to copy"
    >
      {id}
    </span>
  );
}

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

type RightPanelTab = "run" | "step" | "session";

export function JobDetailPage() {
  const { jobId } = useParams({ from: "/jobs/$jobId" });
  const navigate = useNavigate({ from: "/jobs/$jobId" });
  const isMobile = useIsMobile();
  const { data: job, isLoading } = useJob(jobId);
  const { data: parentJob } = useJob(job?.parent_job_id ?? undefined);
  const { data: jobTree } = useJobTree(jobId);
  const { data: runs = [] } = useRuns(jobId);
  const { data: costData } = useJobCost(jobId);
  const { data: sessionData } = useJobSessions(jobId);
  const hasSessions = (sessionData?.sessions?.length ?? 0) > 0;
  const searchParams = useSearch({ from: "/jobs/$jobId" }) as JobDetailSearch;
  const [dataFlowSelection, setDataFlowSelection] = useState<DagSelection>(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [expandedStep, setExpandedStep] = useState(false);
  const [autoOpenedPanel, setAutoOpenedPanel] = useState(false);
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

  // Derive state from URL search params
  const selectedStep = searchParams.step ?? null;
  const selection: DagSelection = dataFlowSelection ?? (selectedStep ? { kind: "step", stepName: selectedStep } : null);

  // Derive activeTab: URL tab param > default "run" when step selected
  const activeTab: RightPanelTab = searchParams.tab ?? "run";

  // Derive rightPanelOpen: URL panel param > auto-open for terminal jobs > default closed
  const rightPanelOpen = searchParams.panel === "open"
    ? true
    : autoOpenedPanel
      ? true
      : false;

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
    setDataFlowSelection(null);
    if (stepName) {
      navigate({
        search: (prev: JobDetailSearch) => ({ ...prev, step: stepName, tab: "run" as const, panel: "open" as const }),
        replace: false,
      });
    } else {
      navigate({
        search: (prev: JobDetailSearch) => ({ ...prev, step: undefined, tab: undefined, panel: undefined }),
        replace: true,
      });
    }
  }, [navigate]);

  const handleSelectDataFlow = useCallback((sel: DagSelection) => {
    setDataFlowSelection(sel);
  }, []);

  // Auto-select newly suspended external steps
  useAutoSelectSuspended(runs, selection, handleSelectStep);

  // Reset local state when switching jobs
  useEffect(() => {
    setDataFlowSelection(null);
    setExpandedStep(false);
    setAutoOpenedPanel(false);
  }, [jobId]);

  // Auto-open right panel for terminal jobs (only on initial load, when no URL panel param)
  useEffect(() => {
    if (searchParams.panel || autoOpenedPanel) return;
    if (job) {
      const terminal =
        job.status === "completed" || job.status === "failed" || job.status === "cancelled";
      if (terminal) setAutoOpenedPanel(true);
    }
  }, [job, searchParams.panel, autoOpenedPanel]);

  // Topological step order for keyboard navigation
  const topoStepNames = useMemo(() => {
    if (!job?.workflow?.steps) return [];
    const steps = job.workflow.steps;
    const names = Object.keys(steps);

    const inDegree: Record<string, number> = {};
    const outEdges: Record<string, string[]> = {};
    for (const name of names) {
      inDegree[name] = 0;
      outEdges[name] = [];
    }
    for (const name of names) {
      const step = steps[name];
      const deps = new Set<string>();
      for (const input of step.inputs ?? []) {
        if (input.source_step && input.source_step !== "$job") deps.add(input.source_step);
      }
      for (const after of step.after ?? []) deps.add(after);
      for (const dep of deps) {
        if (dep in inDegree) {
          inDegree[name]++;
          outEdges[dep].push(name);
        }
      }
    }

    const queue = names.filter((n) => inDegree[n] === 0);
    const sorted: string[] = [];
    while (queue.length > 0) {
      queue.sort();
      const node = queue.shift()!;
      sorted.push(node);
      for (const next of outEdges[node]) {
        inDegree[next]--;
        if (inDegree[next] === 0) queue.push(next);
      }
    }
    for (const name of names) {
      if (!sorted.includes(name)) sorted.push(name);
    }
    return sorted;
  }, [job?.workflow?.steps]);

  // Keyboard navigation for DAG steps
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable) {
        return;
      }

      const stepCount = topoStepNames.length;
      if (stepCount === 0) return;

      const currentIndex = selectedStep ? topoStepNames.indexOf(selectedStep) : -1;

      switch (e.key) {
        case "j":
        case "ArrowDown": {
          e.preventDefault();
          const next = currentIndex < 0 ? 0 : (currentIndex + 1) % stepCount;
          handleSelectStep(topoStepNames[next]);
          break;
        }
        case "k":
        case "ArrowUp": {
          e.preventDefault();
          const prev = currentIndex < 0 ? stepCount - 1 : (currentIndex - 1 + stepCount) % stepCount;
          handleSelectStep(topoStepNames[prev]);
          break;
        }
        case "Tab": {
          if (selectedStep) {
            e.preventDefault();
            const delta = e.shiftKey ? -1 : 1;
            const next = (currentIndex + delta + stepCount) % stepCount;
            handleSelectStep(topoStepNames[next]);
          }
          break;
        }
        case "Enter": {
          if (selectedStep) {
            e.preventDefault();
            setDataFlowSelection(null);
            navigate({
              search: (prev: JobDetailSearch) => ({ ...prev, panel: "open" as const, tab: "run" as const }),
              replace: true,
            });
          }
          break;
        }
        case "Escape": {
          if (selection) {
            setDataFlowSelection(null);
            navigate({
              search: (prev: JobDetailSearch) => ({ ...prev, step: undefined, tab: undefined, panel: undefined }),
              replace: true,
            });
          }
          break;
        }
      }
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [selection, selectedStep, topoStepNames, handleSelectStep]);

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
          className="text-sm text-blue-600 dark:text-blue-400 hover:text-blue-500 dark:hover:text-blue-300 underline underline-offset-2"
        >
          Back to Jobs
        </Link>
      </div>
    );
  }

  const resolvedStep = selectedStep
    ? resolveStep(selectedStep, job.id, job.workflow, jobTree ?? null)
    : null;

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
            <ActionContextProvider>
              <JobList
                selectedJobId={jobId}
                onSelectJob={(id) =>
                  navigate({ to: "/jobs/$jobId", params: { jobId: id }, search: true })
                }
              />
            </ActionContextProvider>
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
                  {job.status !== "pending" && job.status !== "staged" && (
                    <span className="text-[10px] text-zinc-500 dark:text-zinc-600 flex items-center gap-0.5">
                      <Clock className="w-2.5 h-2.5" />
                      {formatDuration(job.created_at, job.updated_at)}
                    </span>
                  )}
                  {costData && (costData.cost_usd > 0 || costData.billing_mode === "subscription") && (
                    <span className="text-[10px] text-zinc-500 dark:text-zinc-600 flex items-center gap-0.5">
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
                      onClick={() => navigate({ search: (prev: JobDetailSearch) => ({ ...prev, panel: "open" as const }), replace: true })}
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
                  {job.status !== "pending" && job.status !== "staged" && (
                    <span className="text-[10px] text-zinc-500 dark:text-zinc-600 flex items-center gap-0.5">
                      <Clock className="w-2.5 h-2.5" />
                      {formatDuration(job.created_at, job.updated_at)}
                    </span>
                  )}
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
                <div className="text-[10px] font-mono text-zinc-500 dark:text-zinc-600 mt-0.5 break-all flex items-center gap-2 flex-wrap">
                  {job.name && job.objective && (
                    <span className="font-sans text-zinc-500">{job.objective}</span>
                  )}
                  {job.workflow.metadata?.name && (
                    <Link
                      to="/flows/$flowName"
                      params={{ flowName: job.workflow.metadata.name }}
                      className="font-sans text-blue-600 dark:text-blue-400 hover:text-blue-500 dark:hover:text-blue-300 underline underline-offset-2"
                    >
                      {job.workflow.metadata.name}
                    </Link>
                  )}
                  <CopyableId id={job.id} />
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
                    onClick={() => navigate({ search: (prev: JobDetailSearch) => ({ ...prev, panel: "open" as const }), replace: true })}
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
          <div className="flex items-center gap-2 px-4 py-2 border-b border-red-300/50 dark:border-red-900/50 bg-red-100/30 dark:bg-red-950/30 text-xs">
            <AlertTriangle className="w-3.5 h-3.5 text-red-500 dark:text-red-400 shrink-0" />
            <div className="flex-1 min-w-0">
              <span className="text-red-700 dark:text-red-300 font-medium">
                Step "{failedRun.step_name}" failed
              </span>
              {failedRun.error && (
                <span className="text-red-500/70 dark:text-red-400/70 font-mono ml-2 truncate">
                  — {failedRun.error.split("\n")[0]}
                </span>
              )}
            </div>
            <button
              type="button"
              className="text-amber-600 dark:text-amber-400 hover:text-amber-500 dark:hover:text-amber-300 text-xs font-medium whitespace-nowrap shrink-0"
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
            jobId={job.id}
            jobStatus={job.status}
            jobActions={{
              onPauseJob: () => mutations.pauseJob.mutate(job.id),
              onResumeJob: () => mutations.resumeJob.mutate(job.id),
              onCancelJob: () => mutations.cancelJob.mutate(job.id),
              onRetryJob: () => mutations.resumeJob.mutate(job.id),
              onStartJob: () => mutations.startJob.mutate(job.id),
              onRerunStep: (stepName) => mutations.rerunStep.mutate({ jobId: job.id, stepName }),
              onCancelRun: (runId) => mutations.cancelRun.mutate(runId),
              isPausePending: mutations.pauseJob.isPending,
              isResumePending: mutations.resumeJob.isPending || mutations.startJob.isPending,
              isCancelPending: mutations.cancelJob.isPending,
              isRetryPending: mutations.resumeJob.isPending,
            }}
          />
        </div>
      </div>

      {/* Right sidebar */}
      {(() => {
        const closePanel = () => {
          setDataFlowSelection(null);
          setAutoOpenedPanel(false);
          navigate({ search: (prev: JobDetailSearch) => ({ ...prev, step: undefined, tab: undefined, panel: undefined }), replace: true });
        };

        const deselectStep = () => {
          setDataFlowSelection(null);
          navigate({ search: (prev: JobDetailSearch) => ({ ...prev, step: undefined, tab: undefined }), replace: true });
        };

        // DataFlow selection takes priority (shown as overlay panel)
        if (isDataFlowSelection && selection) {
          const dfPanel = (
            <div className="flex flex-col flex-1 min-h-0">
              <DataFlowPanel
                selection={selection}
                job={job}
                latestRuns={latestRuns}
                outputs={outputs ? outputs as Record<string, unknown> : null}
                onClose={() => { setDataFlowSelection(null); }}
              />
            </div>
          );

          if (isMobile) {
            return (
              <MobileFullScreen open={true} onClose={() => setDataFlowSelection(null)} title="Data Flow">
                {dfPanel}
              </MobileFullScreen>
            );
          }
          return (
            <div className="w-80 border-l border-border shrink-0 flex flex-col overflow-y-auto" style={{ maxHeight: 'calc(100vh - 3rem)' }}>
              {dfPanel}
            </div>
          );
        }

        // No step selected: show JobOverview or Session tab
        let panelContent: React.ReactNode = null;
        if (showRightPanel && !resolvedStep) {
          const showSessionInOverview = hasSessions && activeTab === "session";
          panelContent = (
            <div className="flex flex-col flex-1 min-h-0">
              <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-zinc-50/50 dark:bg-zinc-950/50 shrink-0">
                {hasSessions ? (
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => navigate({ search: (prev: JobDetailSearch) => ({ ...prev, tab: undefined }), replace: true })}
                      className={cn(
                        "text-xs font-medium px-2 py-0.5 rounded transition-colors",
                        !showSessionInOverview ? "text-zinc-700 dark:text-zinc-300 bg-zinc-200/60 dark:bg-zinc-800/60" : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300",
                      )}
                    >
                      Overview
                    </button>
                    <button
                      onClick={() => navigate({ search: (prev: JobDetailSearch) => ({ ...prev, tab: "session" as const }), replace: true })}
                      className={cn(
                        "text-xs font-medium px-2 py-0.5 rounded transition-colors",
                        showSessionInOverview ? "text-zinc-700 dark:text-zinc-300 bg-zinc-200/60 dark:bg-zinc-800/60" : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300",
                      )}
                    >
                      Session
                    </button>
                  </div>
                ) : (
                  <span className="text-xs font-medium text-zinc-700 dark:text-zinc-300">Job Details</span>
                )}
                <button
                  onClick={closePanel}
                  className="text-zinc-500 dark:text-zinc-600 hover:text-zinc-700 dark:hover:text-zinc-300 p-0.5"
                >
                  <PanelRightOpen className="w-3.5 h-3.5" />
                </button>
              </div>
              {showSessionInOverview ? (
                <div className="flex-1 min-h-0 overflow-y-auto">
                  <SessionTab
                    jobId={jobId}
                    onNavigateToStep={(stepName) =>
                      navigate({
                        search: (prev: JobDetailSearch) => ({
                          ...prev,
                          step: stepName,
                          tab: "run" as const,
                          panel: "open" as const,
                        }),
                        replace: true,
                      })
                    }
                  />
                </div>
              ) : (
                <ScrollArea className="flex-1 min-h-0">
                  <JobOverview job={job} />
                </ScrollArea>
              )}
            </div>
          );
        }

        // Step selected: show Run/Step tabs
        if (showRightPanel && resolvedStep) {
          panelContent = (
            <Tabs
              value={activeTab}
              onValueChange={(v) => {
                navigate({ search: (prev: JobDetailSearch) => ({ ...prev, tab: v as RightPanelTab }), replace: true });
              }}
              className="flex flex-col flex-1 min-h-0 gap-0"
            >
              <div className="flex items-center justify-between border-b border-border bg-zinc-50/50 dark:bg-zinc-950/50 shrink-0">
                <TabsList variant="line" className="px-1">
                  <TabsTrigger value="run" className="text-xs gap-1 px-2.5">
                    Run
                  </TabsTrigger>
                  <TabsTrigger value="step" className="text-xs gap-1 px-2.5">
                    Step
                  </TabsTrigger>
                  {hasSessions && (
                    <TabsTrigger value="session" className="text-xs gap-1 px-2.5">
                      Session
                    </TabsTrigger>
                  )}
                </TabsList>
                <button
                  onClick={deselectStep}
                  className="text-zinc-500 dark:text-zinc-600 hover:text-zinc-700 dark:hover:text-zinc-300 p-0.5 mr-2"
                  title="Close step details"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>

              <TabsContent
                value="run"
                className={cn(
                  "flex-1 min-h-0 overflow-y-auto",
                  activeTab !== "run" && "hidden"
                )}
              >
                <div key={selectedStep} className="animate-step-fade">
                  <RunView
                    jobId={resolvedStep.jobId}
                    stepDef={resolvedStep.stepDef}
                    hasLiveSource={!!job?.flow_source_path}
                  />
                </div>
              </TabsContent>

              <TabsContent
                value="step"
                className={cn(
                  "flex-1 min-h-0 overflow-y-auto",
                  activeTab !== "step" && "hidden"
                )}
              >
                <div key={selectedStep} className="animate-step-fade">
                  <StepConfigView stepDef={resolvedStep.stepDef} />
                </div>
              </TabsContent>

              {hasSessions && (
                <TabsContent
                  value="session"
                  className={cn(
                    "flex-1 min-h-0 overflow-y-auto",
                    activeTab !== "session" && "hidden"
                  )}
                >
                  <SessionTab
                    jobId={jobId}
                    highlightStep={selectedStep}
                    onNavigateToStep={(stepName) =>
                      navigate({
                        search: (prev: JobDetailSearch) => ({
                          ...prev,
                          step: stepName,
                          tab: "run" as const,
                          panel: "open" as const,
                        }),
                        replace: true,
                      })
                    }
                  />
                </TabsContent>
              )}
            </Tabs>
          );
        }

        if (!showRightPanel) return null;

        if (isMobile) {
          return (
            <MobileFullScreen
              open={showRightPanel}
              onClose={closePanel}
              title={resolvedStep ? resolvedStep.stepDef.name : "Details"}
            >
              {panelContent}
            </MobileFullScreen>
          );
        }

        return (
          <div className="w-80 border-l border-border shrink-0 flex flex-col overflow-y-auto" style={{ maxHeight: 'calc(100vh - 3rem)' }}>
            {panelContent}
          </div>
        );
      })()}

      {/* Expanded step overlay */}
      {isMobile ? (
        <MobileFullScreen
          open={expandedStep && !!resolvedStep}
          onClose={() => { setExpandedStep(false); setDataFlowSelection(null); navigate({ search: (prev: JobDetailSearch) => ({ ...prev, step: undefined, tab: undefined, panel: undefined }), replace: true }); }}
          title={resolvedStep?.stepDef.name ?? "Step Detail"}
        >
          {resolvedStep && (
            <RunView
              jobId={resolvedStep.jobId}
              stepDef={resolvedStep.stepDef}
              hasLiveSource={!!job?.flow_source_path}
            />
          )}
        </MobileFullScreen>
      ) : (
        <Sheet open={expandedStep && !!resolvedStep} onOpenChange={(open) => !open && setExpandedStep(false)}>
          <SheetContent side="right" showCloseButton={false} className="w-[70vw] max-w-4xl p-0 overflow-y-auto">
            {resolvedStep && (
              <RunView
                jobId={resolvedStep.jobId}
                stepDef={resolvedStep.stepDef}
                hasLiveSource={!!job?.flow_source_path}
              />
            )}
          </SheetContent>
        </Sheet>
      )}
    </div>
  );
}
