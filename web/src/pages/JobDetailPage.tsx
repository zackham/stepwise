import { useState, useEffect, useMemo, useCallback, useRef } from "react";
import { useParams, useNavigate, useSearch, Link } from "@tanstack/react-router";
import { toast } from "sonner";
import type { JobDetailSearch } from "@/router";
import { useJob, useRuns, useEvents, useJobTree, useJobOutput, useStepwiseMutations, useJobSessions } from "@/hooks/useStepwise";
import { SessionTab } from "@/components/jobs/SessionTab";
import { StepSessionView } from "@/components/jobs/StepSessionView";
import { FlowDagView } from "@/components/dag/FlowDagView";
import { TimelineView } from "@/components/jobs/TimelineView";
import { RunView } from "@/components/jobs/RunView";
import { StepDefinitionPanel } from "@/components/editor/StepDefinitionPanel";
import { JobOverview } from "@/components/jobs/JobOverview";
import type { DagSelection } from "@/lib/dag-layout";
import { useAutoSelectSuspended } from "@/hooks/useAutoSelectSuspended";
import { useAutoExpand } from "@/hooks/useAutoExpand";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { MobileFullScreen } from "@/components/layout/MobileFullScreen";
import { useIsMobile } from "@/hooks/useMediaQuery";
import {
  AlertTriangle,
  X,
  Workflow,
  ScrollText,
  GanttChart,
} from "lucide-react";
import { ResizablePanel } from "@/components/ui/ResizablePanel";
import type { JobTreeNode, StepDefinition } from "@/lib/types";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { usePanelRegister } from "@/hooks/usePanelRegister";


function extractErrorMessage(error: string): string {
  const firstLine = error.split("\n")[0];
  const jsonMatch = firstLine.match(/\{.*"message"\s*:\s*"([^"]+)"/);
  if (jsonMatch) {
    const jsonStart = firstLine.indexOf("{");
    const prefix = firstLine.slice(0, jsonStart).replace(/:\s*$/, "");
    return `${prefix}: ${jsonMatch[1]}`;
  }
  return firstLine;
}


function resolveStep(
  stepName: string,
  jobId: string,
  workflow: { steps: Record<string, StepDefinition> },
  jobTree: JobTreeNode | null,
): { stepDef: StepDefinition; jobId: string } | null {
  const forEachMatch = stepName.match(/^forEach:([^:]+):(.+)$/);
  if (forEachMatch) {
    const [, instanceJobId, childStepName] = forEachMatch;
    if (jobTree) {
      const target = findJobTreeById(jobTree, instanceJobId);
      if (target) {
        return resolveStep(childStepName, target.job.id, target.job.workflow, target);
      }
    }
    return null;
  }
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

function findJobTreeById(tree: JobTreeNode, targetId: string): JobTreeNode | null {
  if (tree.job.id === targetId) return tree;
  for (const child of tree.sub_jobs) {
    const found = findJobTreeById(child, targetId);
    if (found) return found;
  }
  return null;
}

type LeftPanelTab = "overview" | "session";
type RightPanelTab = "run" | "step" | "session";

export function JobDetailPage() {
  const { jobId } = useParams({ from: "/jobs/$jobId" });
  const navigate = useNavigate({ from: "/jobs/$jobId" });
  const isMobile = useIsMobile();
  const { data: job, isLoading } = useJob(jobId);
  const { data: parentJob } = useJob(job?.parent_job_id ?? undefined);
  const { data: jobTree } = useJobTree(jobId);
  const { data: runs = [] } = useRuns(jobId);
  const { data: events = [] } = useEvents(jobId);
  const { data: sessionData } = useJobSessions(jobId);
  const hasSessions = (sessionData?.sessions?.length ?? 0) > 0;
  const searchParams = useSearch({ from: "/jobs/$jobId" }) as JobDetailSearch;
  const [dataFlowSelection, setDataFlowSelection] = useState<DagSelection>(null);
  const [leftPanelCollapsed, setLeftPanelCollapsed] = useState(false);
  const [expandedStep, setExpandedStep] = useState(false);
  const [autoOpenedPanel, setAutoOpenedPanel] = useState(false);
  const [leftTab, setLeftTab] = useState<LeftPanelTab>("overview");
  const [leftSessionFocus, setLeftSessionFocus] = useState<string | null>(null);
  const [focusRunId, setFocusRunId] = useState<string | undefined>();
  const mutations = useStepwiseMutations();
  const { expandedSteps, toggleExpand } = useAutoExpand(jobId, runs, job, jobTree ?? null);

  const selectedStep = searchParams.step ?? null;
  const resolvedStepForPanel = selectedStep && job
    ? resolveStep(selectedStep, job.id, job.workflow, jobTree ?? null)
    : null;
  const showRightPanel = !!resolvedStepForPanel;

  usePanelRegister({
    leftPanel: isMobile ? undefined : {
      visible: !leftPanelCollapsed,
      toggle: () => setLeftPanelCollapsed((c) => !c),
    },
    rightPanel: isMobile ? undefined : {
      visible: showRightPanel,
      toggle: () => {
        if (showRightPanel) {
          setDataFlowSelection(null);
          navigate({
            search: (prev: JobDetailSearch) => ({ ...prev, step: undefined, tab: undefined, panel: undefined }),
            replace: true,
          });
        }
      },
      disabled: !showRightPanel,
      label: showRightPanel ? "Hide right panel" : "Select a step to show details",
    },
  });

  const isTerminal =
    job?.status === "completed" || job?.status === "failed" || job?.status === "cancelled";
  useJobOutput(job?.id, isTerminal);
  const failedRun = useMemo(() => {
    if (job?.status !== "failed") return null;
    return runs.find((r) => r.status === "failed") ?? null;
  }, [job?.status, runs]);

  // Toast on job status transition to terminal state
  const prevJobStatusRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    const currentStatus = job?.status;
    const prevStatus = prevJobStatusRef.current;
    prevJobStatusRef.current = currentStatus;

    // Only fire on a real transition (prev was non-terminal, current is terminal)
    if (!prevStatus || !currentStatus) return;
    const wasTerminal = prevStatus === "completed" || prevStatus === "failed" || prevStatus === "cancelled";
    if (wasTerminal) return;

    if (currentStatus === "completed") {
      toast.success("Job completed");
    } else if (currentStatus === "failed") {
      toast.error("Job failed");
    } else if (currentStatus === "cancelled") {
      toast.info("Job cancelled");
    }
  }, [job?.status]);

  const selection: DagSelection = dataFlowSelection ?? (selectedStep ? { kind: "step", stepName: selectedStep } : null);

  const activeTab: RightPanelTab = searchParams.tab ?? "run";

  useEffect(() => {
    if (activeTab !== "session") setFocusRunId(undefined);
  }, [activeTab]);

  const viewMode = searchParams.view ?? "dag";

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

  useAutoSelectSuspended(runs, selection, handleSelectStep);

  useEffect(() => {
    setDataFlowSelection(null);
    setExpandedStep(false);
    setAutoOpenedPanel(false);
    setLeftTab("overview");
  }, [jobId]);

  useEffect(() => {
    if (searchParams.panel || autoOpenedPanel) return;
    if (job) {
      const terminal =
        job.status === "completed" || job.status === "failed" || job.status === "cancelled";
      if (terminal) setAutoOpenedPanel(true);
    }
  }, [job, searchParams.panel, autoOpenedPanel]);

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

  const resolvedStep = resolvedStepForPanel;

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 flex min-h-0">
        {!isMobile && !leftPanelCollapsed && (
          <ResizablePanel
            storageKey="stepwise-job-left-panel-width"
            defaultWidth={320}
            min={240}
            max={480}
            side="left"
            onCollapse={() => setLeftPanelCollapsed(true)}
          >
            <Tabs value={leftTab} onValueChange={(v) => setLeftTab(v as LeftPanelTab)} className="flex flex-col h-full gap-0">
              <div className="flex items-center justify-between border-b border-border bg-zinc-50/50 dark:bg-zinc-950/50 shrink-0">
                <TabsList variant="line" className="px-1">
                  <TabsTrigger value="overview" className="text-xs gap-1 px-2.5">
                    Overview
                  </TabsTrigger>
                  {hasSessions && (
                    <TabsTrigger value="session" className="text-xs gap-1 px-2.5">
                      Sessions
                    </TabsTrigger>
                  )}
                </TabsList>
              </div>
              <TabsContent value="overview" className={cn("flex-1 min-h-0 overflow-y-auto", leftTab !== "overview" && "hidden")}>
                <JobOverview job={job} />
              </TabsContent>
              {hasSessions && (
                <TabsContent value="session" className={cn("flex-1 min-h-0 overflow-y-auto", leftTab !== "session" && "hidden")}>
                  <SessionTab
                    jobId={jobId}
                    initialSession={leftSessionFocus}
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
          </ResizablePanel>
        )}

        <div className="flex-1 flex flex-col min-w-0">
          <Tabs
            value={viewMode}
            onValueChange={(v) =>
              navigate({
                search: (prev: JobDetailSearch) => ({
                  ...prev,
                  view: v === "dag" ? undefined : (v as JobDetailSearch["view"]),
                }),
                replace: true,
              })
            }
            className="flex flex-col flex-1 min-h-0 gap-0"
          >
            <div className="flex items-center border-b border-border bg-zinc-50/50 dark:bg-zinc-900/50 shrink-0">
              <TabsList variant="line" className="px-1">
                <TabsTrigger value="dag" className="text-xs gap-1 px-2.5">
                  <Workflow className="w-3.5 h-3.5" />
                  {!isMobile && "Flow"}
                </TabsTrigger>
                <TabsTrigger value="timeline" className="text-xs gap-1 px-2.5">
                  <GanttChart className="w-3.5 h-3.5" />
                  {!isMobile && "Timeline"}
                </TabsTrigger>
                <TabsTrigger value="events" className="text-xs gap-1 px-2.5">
                  <ScrollText className="w-3.5 h-3.5" />
                  {!isMobile && "Events"}
                </TabsTrigger>
              </TabsList>
            </div>

            {failedRun && (
              <div className="flex items-center gap-2 px-4 py-2 border-b border-red-300/50 dark:border-red-900/50 bg-red-100/30 dark:bg-red-950/30 text-xs">
                <AlertTriangle className="w-3.5 h-3.5 text-red-500 dark:text-red-400 shrink-0" />
                <div className="flex-1 min-w-0 truncate">
                  <span className="text-red-700 dark:text-red-300 font-medium">
                    Step "{failedRun.step_name}" failed
                  </span>
                  {failedRun.error && (
                    <span className="text-red-500/70 dark:text-red-400/70 ml-2">
                      — {extractErrorMessage(failedRun.error)}
                    </span>
                  )}
                </div>
                <button
                  type="button"
                  className="text-amber-600 dark:text-amber-400 hover:text-amber-500 dark:hover:text-amber-300 text-xs font-medium whitespace-nowrap shrink-0 cursor-pointer"
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
                  className="text-red-400 hover:text-red-300 underline underline-offset-2 whitespace-nowrap shrink-0 cursor-pointer"
                  onClick={() => handleSelectStep(failedRun.step_name)}
                >
                  View Details
                </button>
              </div>
            )}

            <TabsContent value="dag" className={cn("flex-1 min-h-0", viewMode !== "dag" && "hidden")}>
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
                jobInputs={job.inputs ?? null}
              />
            </TabsContent>
            <TabsContent value="timeline" className={cn("flex-1 min-h-0", viewMode !== "timeline" && "hidden")}>
              <TimelineView job={job} runs={runs} onSelectStep={handleSelectStep} selectedStep={selectedStep} />
            </TabsContent>
            <TabsContent value="events" className={cn("flex-1 min-h-0", viewMode !== "events" && "hidden")}>
              <ScrollArea className="h-full">
                <div className="p-4 space-y-1">
                  {events.length === 0 ? (
                    <div className="text-center text-xs text-zinc-500 py-8">No events</div>
                  ) : (
                    events.map((event) => (
                      <div
                        key={event.id}
                        className={cn(
                          "flex items-start gap-3 px-3 py-1.5 rounded text-xs font-mono hover:bg-zinc-100/50 dark:hover:bg-zinc-800/30",
                          !!event.data?.step && "cursor-pointer",
                        )}
                        onClick={() => {
                          const step = event.data?.step as string | undefined;
                          if (step) handleSelectStep(step);
                        }}
                      >
                        <span className="text-zinc-500 shrink-0 tabular-nums w-20">
                          {new Date(event.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                        </span>
                        <span className={cn(
                          "shrink-0 w-32 truncate",
                          event.type.includes("failed") && "text-red-400",
                          event.type.includes("completed") && "text-emerald-400",
                          event.type.includes("started") && "text-blue-400",
                          event.type.includes("suspended") && "text-amber-400",
                        )}>
                          {event.type}
                        </span>
                        <span className="text-zinc-400 truncate flex-1">
                          {!!event.data?.step && <span className="text-zinc-300">{String(event.data.step)}</span>}
                          {!!event.data?.error && <span className="text-red-400 ml-2">{String(event.data.error)}</span>}
                          {!!event.data?.rule && <span className="ml-2">{String(event.data.rule)} → {String(event.data.action)}</span>}
                        </span>
                      </div>
                    ))
                  )}
                </div>
              </ScrollArea>
            </TabsContent>
          </Tabs>
        </div>

      {(() => {
        const deselectStep = () => {
          setDataFlowSelection(null);
          navigate({ search: (prev: JobDetailSearch) => ({ ...prev, step: undefined, tab: undefined, panel: undefined }), replace: true });
        };

        if (resolvedStep) {
          const panelContent = (
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
                  {hasSessions && resolvedStep.stepDef.executor.type !== "agent" && (
                    <TabsTrigger value="session" className="text-xs gap-1 px-2.5">
                      Session
                    </TabsTrigger>
                  )}
                </TabsList>
                <button
                  onClick={deselectStep}
                  className="text-zinc-500 dark:text-zinc-600 hover:text-zinc-700 dark:hover:text-zinc-300 p-0.5 mr-2 cursor-pointer"
                  title="Close step details"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>

              <TabsContent
                value="run"
                className={cn(
                  "flex-1 min-h-0",
                  activeTab !== "run" && "hidden"
                )}
              >
                <div key={selectedStep} className="animate-step-fade h-full">
                  <RunView
                    jobId={resolvedStep.jobId}
                    stepDef={resolvedStep.stepDef}
                    hasLiveSource={!!job?.flow_source_path}
                    onSelectStep={handleSelectStep}
                    onViewFullSession={(sessionName) => {
                      setLeftSessionFocus(sessionName);
                      setLeftTab("session");
                    }}
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
                  <StepDefinitionPanel
                    stepDef={resolvedStep.stepDef}
                    onClose={() => handleSelectStep(null)}
                    onSelectStep={handleSelectStep}
                  />
                </div>
              </TabsContent>

              {hasSessions && resolvedStep.stepDef.executor.type !== "agent" && (
                <TabsContent
                  value="session"
                  className={cn(
                    "flex-1 min-h-0 flex flex-col",
                    activeTab !== "session" && "hidden"
                  )}
                >
                  {(() => {
                    const stepSession = sessionData?.sessions?.find(s =>
                      s.step_names.includes(resolvedStep.stepDef.name)
                    );
                    if (stepSession) {
                      return (
                        <StepSessionView
                          jobId={jobId}
                          sessionName={stepSession.session_name}
                          sessionInfo={stepSession}
                          stepName={resolvedStep.stepDef.name}
                          focusRunId={focusRunId}
                          onNavigateToStep={(stepName, tab) =>
                            navigate({
                              search: (prev: JobDetailSearch) => ({
                                ...prev,
                                step: stepName,
                                tab: (tab ?? "run") as RightPanelTab,
                                panel: "open" as const,
                              }),
                              replace: true,
                            })
                          }
                          onViewFullSession={() => {
                            setLeftSessionFocus(stepSession.session_name);
                            setLeftTab("session");
                          }}
                        />
                      );
                    }
                    return (
                      <div className="p-4 text-xs text-zinc-500 text-center">No session for this step</div>
                    );
                  })()}
                </TabsContent>
              )}
            </Tabs>
          );

          if (isMobile) {
            return (
              <MobileFullScreen
                open={true}
                onClose={deselectStep}
                title={resolvedStep.stepDef.name}
              >
                {panelContent}
              </MobileFullScreen>
            );
          }

          return (
            <ResizablePanel storageKey="stepwise-job-right-panel-width">
              {panelContent}
            </ResizablePanel>
          );
        }

        return null;
      })()}

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
              onSelectStep={handleSelectStep}
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
                onSelectStep={handleSelectStep}
              />
            )}
          </SheetContent>
        </Sheet>
      )}
      </div>
    </div>
  );
}
