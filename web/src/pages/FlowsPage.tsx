import { useState, useCallback, useMemo, useEffect } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { RegistryBrowser } from "@/components/editor/RegistryBrowser";
import { FlowInfoPanel } from "@/components/editor/FlowInfoPanel";
import { CreateFlowDialog } from "@/components/editor/CreateFlowDialog";
import { FlowDagView } from "@/components/dag/FlowDagView";
import {
  useLocalFlows,
  useLocalFlow,
  useDeleteFlow,
  useRegistryFlow,
  useInstallFlow,
  useFlowStats,
} from "@/hooks/useEditor";
import { useStepwiseMutations } from "@/hooks/useStepwise";
import {
  Hand,
  FileText,
  FolderOpen,
  Globe,
  Pencil,
  Play,
  Plus,
  Search,
  Trash2,
  User,
} from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { MobileFullScreen } from "@/components/layout/MobileFullScreen";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/utils";
import type { LocalFlow, RegistryFlow } from "@/lib/types";

const EMPTY_RUNS: never[] = [];

type Tab = "local" | "registry";
type SortBy = "name" | "most-used" | "recent";

function flowDirKey(flowPath: string): string {
  const lastSlash = flowPath.lastIndexOf("/");
  return lastSlash >= 0 ? flowPath.substring(0, lastSlash) : flowPath;
}

function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function FlowsPage() {
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const [tab, setTab] = useState<Tab>("local");
  const [filter, setFilter] = useState("");
  const [sortBy, setSortBy] = useState<SortBy>("name");
  const [showCreateDialog, setShowCreateDialog] = useState(false);

  // Data
  const { data: flows = [] } = useLocalFlows();
  const { data: flowStats = [] } = useFlowStats();
  const deleteFlowMutation = useDeleteFlow();
  const mutations = useStepwiseMutations();

  // Local selection
  const { selected: selectedFlowName } = useSearch({ from: "/flows" });
  const [selectedLocalFlow, setSelectedLocalFlow] = useState<LocalFlow | null>(null);
  const [pendingDeleteFlow, setPendingDeleteFlow] = useState<LocalFlow | null>(null);
  const { data: localFlowDetail } = useLocalFlow(selectedLocalFlow?.path);

  useEffect(() => {
    if (!selectedFlowName) {
      setSelectedLocalFlow(null);
      return;
    }

    const match = flows.find((flow) => flow.name === selectedFlowName) ?? null;
    setSelectedLocalFlow((current) => (current?.path === match?.path ? current : match));
  }, [selectedFlowName, flows]);
  const [localExpandedSteps, setLocalExpandedSteps] = useState<Set<string>>(new Set());
  const toggleLocalExpand = useCallback((stepName: string) => {
    setLocalExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepName)) next.delete(stepName);
      else next.add(stepName);
      return next;
    });
  }, []);

  // Registry
  const [selectedRegistryFlow, setSelectedRegistryFlow] = useState<RegistryFlow | null>(null);
  const { data: registryFlowDetail } = useRegistryFlow(selectedRegistryFlow?.slug);
  const installMutation = useInstallFlow();
  const [installedSlugs, setInstalledSlugs] = useState<Set<string>>(new Set());
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());
  const toggleExpand = useCallback((stepName: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepName)) next.delete(stepName);
      else next.add(stepName);
      return next;
    });
  }, []);

  const statsMap = useMemo(
    () => new Map(flowStats.map((s) => [s.flow_dir, s])),
    [flowStats]
  );

  const filtered = useMemo(() => {
    const result = filter
      ? flows.filter((f) => f.name.toLowerCase().includes(filter.toLowerCase()))
      : [...flows];

    result.sort((a, b) => {
      if (sortBy === "name") {
        return a.name.localeCompare(b.name);
      }
      const sa = statsMap.get(flowDirKey(a.path));
      const sb = statsMap.get(flowDirKey(b.path));
      if (sortBy === "most-used") {
        const diff = (sb?.job_count ?? 0) - (sa?.job_count ?? 0);
        return diff !== 0 ? diff : a.name.localeCompare(b.name);
      }
      // "recent"
      const ta = sa?.last_run_at ?? "";
      const tb = sb?.last_run_at ?? "";
      if (ta === tb) return a.name.localeCompare(b.name);
      if (!ta) return 1;
      if (!tb) return -1;
      return tb.localeCompare(ta);
    });

    return result;
  }, [flows, filter, sortBy, statsMap]);

  const handleSelectLocalFlow = useCallback(
    (flow: LocalFlow) => {
      setSelectedLocalFlow(flow);
      navigate({ to: "/flows", search: { selected: flow.name }, replace: true });
    },
    [navigate]
  );

  const handleEdit = useCallback(
    (flow: LocalFlow) => {
      navigate({ to: "/flows/$flowName", params: { flowName: flow.name } });
    },
    [navigate]
  );

  const handleRun = useCallback(
    (flow: LocalFlow) => {
      mutations.createJob.mutate(
        { objective: flow.name, workflow: null as never, inputs: {}, workspace_path: undefined, flow_path: flow.path },
        {
          onSuccess: (job) => {
            navigate({ to: "/jobs/$jobId", params: { jobId: job.id } });
          },
        }
      );
    },
    [mutations, navigate]
  );

  const handleDelete = useCallback(
    (flow: LocalFlow) => {
      setPendingDeleteFlow(flow);
    },
    []
  );

  const handleConfirmDelete = useCallback(() => {
    if (!pendingDeleteFlow) return;

    deleteFlowMutation.mutate(pendingDeleteFlow.path, {
      onSuccess: () => {
        if (selectedLocalFlow?.path === pendingDeleteFlow.path) {
          setSelectedLocalFlow(null);
          navigate({ to: "/flows", search: {}, replace: true });
        }
        setPendingDeleteFlow(null);
      },
    });
  }, [deleteFlowMutation, navigate, pendingDeleteFlow, selectedLocalFlow]);

  const handleDeleteDialogChange = useCallback(
    (open: boolean) => {
      if (!open && !deleteFlowMutation.isPending) {
        setPendingDeleteFlow(null);
      }
    },
    [deleteFlowMutation.isPending]
  );

  const handleFlowCreated = useCallback(
    (result: { path: string; name: string }) => {
      navigate({ to: "/flows/$flowName", params: { flowName: result.name } });
    },
    [navigate]
  );

  const handleInstall = useCallback(() => {
    if (!selectedRegistryFlow) return;
    installMutation.mutate(selectedRegistryFlow.slug, {
      onSuccess: (result) => {
        setInstalledSlugs((prev) => new Set([...prev, selectedRegistryFlow.slug]));
        setSelectedRegistryFlow(null);
        setTab("local");
        navigate({ to: "/flows/$flowName", params: { flowName: result.name } });
      },
    });
  }, [selectedRegistryFlow, installMutation, navigate]);

  const registryDagWorkflow = registryFlowDetail?.flow ?? { steps: {} };
  const localDagWorkflow = localFlowDetail?.flow ?? { steps: {} };
  const localMetadata = localFlowDetail?.flow.metadata;
  const localDescription =
    localMetadata?.description?.trim() || selectedLocalFlow?.description || "No description provided.";
  const localHasSteps = !!localFlowDetail && Object.keys(localFlowDetail.flow.steps).length > 0;

  return (
    <>
      <TooltipProvider>
        <div className="h-full flex flex-col">
          {/* Header */}
          <div className="flex items-center gap-2 sm:gap-4 px-3 sm:px-6 py-3 sm:py-4 border-b border-border shrink-0">
            <div className="flex items-center gap-2">
              <button
                onClick={() => { setTab("local"); setSelectedRegistryFlow(null); }}
                className={cn(
                  "px-3 py-1.5 text-sm rounded-md transition-colors",
                  tab === "local"
                    ? "bg-zinc-200 dark:bg-zinc-800 text-foreground"
                    : "text-zinc-500 hover:text-foreground hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50"
                )}
              >
                <FolderOpen className="w-3.5 h-3.5 inline mr-1.5" />
                Local
              </button>
              <button
                onClick={() => setTab("registry")}
                className={cn(
                  "px-3 py-1.5 text-sm rounded-md transition-colors",
                  tab === "registry"
                    ? "bg-zinc-200 dark:bg-zinc-800 text-foreground"
                    : "text-zinc-500 hover:text-foreground hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50"
                )}
              >
                <Globe className="w-3.5 h-3.5 inline mr-1.5" />
                Registry
              </button>
            </div>

            <div className="flex-1" />

            {tab === "local" && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowCreateDialog(true)}
                  className="h-8"
                >
                  <Plus className="w-3.5 h-3.5 mr-1.5" />
                  New Flow
                </Button>
                <CreateFlowDialog
                  open={showCreateDialog}
                  onOpenChange={setShowCreateDialog}
                  onCreated={handleFlowCreated}
                />
              </>
            )}
          </div>

          {/* Content */}
          {tab === "local" ? (
            <div className="flex-1 flex min-h-0">
              {/* Flow list */}
              <div className="w-full md:w-72 md:border-r border-border md:shrink-0 flex flex-col">
                <div className="p-3 border-b border-border flex gap-2">
                  <div className="relative flex-1">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500" />
                    <Input
                      value={filter}
                      onChange={(e) => setFilter(e.target.value)}
                      placeholder="Filter flows..."
                      className="pl-8 h-8 text-sm bg-zinc-50 dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700"
                    />
                  </div>
                  <Select value={sortBy} onValueChange={(v) => setSortBy(v as SortBy)}>
                    <SelectTrigger className="w-28 h-8 text-xs bg-zinc-50 dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="name">Name</SelectItem>
                      <SelectItem value="most-used">Most Used</SelectItem>
                      <SelectItem value="recent">Recent</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex-1 overflow-y-auto py-1">
                  {filtered.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full text-zinc-600 px-3">
                      <FileText className="w-8 h-8 mb-2 opacity-40" />
                      <p className="text-xs text-center">
                        {flows.length === 0 ? "No flows yet" : "No matching flows"}
                      </p>
                      {flows.length === 0 && (
                        <div className="flex flex-col gap-2 mt-3">
                          <Button
                            size="sm"
                            className="text-xs"
                            onClick={() => setShowCreateDialog(true)}
                          >
                            <Plus className="w-3 h-3 mr-1" />
                            New Flow
                          </Button>
                          <Button
                            variant="outline"
                            size="sm"
                            className="text-xs"
                            onClick={() => setTab("registry")}
                          >
                            <Globe className="w-3 h-3 mr-1" />
                            Browse Registry
                          </Button>
                        </div>
                      )}
                    </div>
                  ) : (
                    filtered.map((flow) => (
                      <button
                        key={flow.path}
                        onClick={() => handleSelectLocalFlow(flow)}
                        onDoubleClick={() => handleEdit(flow)}
                        className={cn(
                          "w-full text-left px-3 py-2 text-sm flex items-center gap-2 transition-colors",
                          selectedLocalFlow?.path === flow.path
                            ? "bg-zinc-100 dark:bg-zinc-800 text-foreground"
                            : "text-zinc-600 dark:text-zinc-400 hover:text-foreground hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50"
                        )}
                      >
                        {flow.is_directory ? (
                          <FolderOpen className="w-3.5 h-3.5 shrink-0 text-blue-400" />
                        ) : (
                          <FileText className="w-3.5 h-3.5 shrink-0 text-zinc-500" />
                        )}
                        <div className="flex flex-col min-w-0 flex-1">
                          <span className="truncate inline-flex items-center gap-1.5">
                            <span className="truncate">{flow.name}</span>
                            {flow.executor_types?.includes("external") && (
                              <Tooltip>
                                <TooltipTrigger
                                  render={<span />}
                                  className="inline-flex items-center shrink-0"
                                  aria-label="Requires human input"
                                >
                                  <Hand className="w-3 h-3 text-amber-400" />
                                </TooltipTrigger>
                                <TooltipContent>Requires human input</TooltipContent>
                              </Tooltip>
                            )}
                          </span>
                          {flow.description && (
                            <span className="text-[10px] text-zinc-600 truncate leading-tight">
                              {flow.description}
                            </span>
                          )}
                        </div>
                        <div className="ml-auto flex items-center gap-2 shrink-0">
                          {(statsMap.get(flowDirKey(flow.path))?.job_count ?? 0) > 0 && (
                            <span className="text-[10px] text-zinc-600">
                              {statsMap.get(flowDirKey(flow.path))!.job_count} jobs
                            </span>
                          )}
                          <span className="text-xs text-zinc-600">
                            {sortBy === "recent"
                              ? statsMap.get(flowDirKey(flow.path))?.last_run_at
                                ? formatRelativeTime(statsMap.get(flowDirKey(flow.path))!.last_run_at!)
                                : "never"
                              : flow.steps_count}
                          </span>
                        </div>
                      </button>
                    ))
                  )}
                </div>
              </div>

              {/* Local flow preview — desktop: inline panel, mobile: full-screen takeover */}
              {!isMobile && (
                selectedLocalFlow ? (
                  <div className="flex-1 min-w-0 overflow-y-auto p-4 sm:p-6">
                    <div className="flex h-full min-h-0 flex-col gap-4">
                      <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-4">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <h2 className="text-lg font-semibold text-zinc-100 break-words">
                              {selectedLocalFlow.name}
                            </h2>
                            <p className="mt-1 text-sm text-zinc-400">
                              {localDescription}
                            </p>
                          </div>
                          <Button
                            onClick={() => handleDelete(selectedLocalFlow)}
                            variant="ghost"
                            size="icon-sm"
                            className="shrink-0 text-red-400 hover:text-red-300"
                            aria-label={`Delete ${selectedLocalFlow.name}`}
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </Button>
                        </div>
                        <div className="mt-4 flex flex-wrap items-center gap-2">
                          {localMetadata?.author && (
                            <span className="inline-flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-950/70 px-2 py-1 text-xs text-zinc-300">
                              <User className="w-3 h-3" />
                              {localMetadata.author}
                            </span>
                          )}
                          {localMetadata?.version && (
                            <span className="inline-flex items-center rounded-md border border-zinc-700 bg-zinc-950/70 px-2 py-1 text-xs text-zinc-300">
                              v{localMetadata.version}
                            </span>
                          )}
                          <div className="flex-1" />
                          <Button onClick={() => handleRun(selectedLocalFlow)} size="sm">
                            <Play className="w-3.5 h-3.5 mr-1.5" />
                            Run
                          </Button>
                          <Button
                            onClick={() => handleEdit(selectedLocalFlow)}
                            variant="outline"
                            size="sm"
                          >
                            <Pencil className="w-3.5 h-3.5 mr-1.5" />
                            Edit
                          </Button>
                        </div>
                      </div>

                      <div className="flex-1 min-h-0 overflow-hidden rounded-lg border border-zinc-800 bg-zinc-950/30">
                        {localHasSteps ? (
                          <FlowDagView
                            workflow={localDagWorkflow}
                            runs={EMPTY_RUNS}
                            jobTree={null}
                            expandedSteps={localExpandedSteps}
                            onToggleExpand={toggleLocalExpand}
                            selectedStep={null}
                            onSelectStep={() => {}}
                          />
                        ) : (
                          <div className="flex items-center justify-center h-full text-zinc-600 text-sm">
                            {localFlowDetail ? "No steps defined" : "Loading preview..."}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm">
                    Select a flow to preview
                  </div>
                )
              )}
              {isMobile && (
                <MobileFullScreen
                  open={!!selectedLocalFlow}
                  onClose={() => {
                    setSelectedLocalFlow(null);
                    navigate({ to: "/flows", search: {}, replace: true });
                  }}
                  title={selectedLocalFlow?.name ?? "Flow Preview"}
                >
                  {selectedLocalFlow && (
                    <div className="flex flex-col h-full">
                      <div className="p-4 space-y-3">
                        <p className="text-sm text-zinc-400">{localDescription}</p>
                        <div className="flex flex-wrap items-center gap-2">
                          {localMetadata?.author && (
                            <span className="inline-flex items-center gap-1 rounded-md border border-zinc-700 bg-zinc-950/70 px-2 py-1 text-xs text-zinc-300">
                              <User className="w-3 h-3" />
                              {localMetadata.author}
                            </span>
                          )}
                          {localMetadata?.version && (
                            <span className="inline-flex items-center rounded-md border border-zinc-700 bg-zinc-950/70 px-2 py-1 text-xs text-zinc-300">
                              v{localMetadata.version}
                            </span>
                          )}
                        </div>
                        <div className="flex gap-2">
                          <Button onClick={() => handleRun(selectedLocalFlow)} size="sm" className="flex-1">
                            <Play className="w-3.5 h-3.5 mr-1.5" />
                            Run
                          </Button>
                          <Button onClick={() => handleEdit(selectedLocalFlow)} variant="outline" size="sm" className="flex-1">
                            <Pencil className="w-3.5 h-3.5 mr-1.5" />
                            Edit
                          </Button>
                          <Button
                            onClick={() => handleDelete(selectedLocalFlow)}
                            variant="ghost"
                            size="icon-sm"
                            className="shrink-0 text-red-400 hover:text-red-300"
                            aria-label={`Delete ${selectedLocalFlow.name}`}
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </Button>
                        </div>
                      </div>
                      <div className="flex-1 min-h-0 border-t border-border">
                        {localHasSteps ? (
                          <FlowDagView
                            workflow={localDagWorkflow}
                            runs={EMPTY_RUNS}
                            jobTree={null}
                            expandedSteps={localExpandedSteps}
                            onToggleExpand={toggleLocalExpand}
                            selectedStep={null}
                            onSelectStep={() => {}}
                          />
                        ) : (
                          <div className="flex items-center justify-center h-full text-zinc-600 text-sm">
                            {localFlowDetail ? "No steps defined" : "Loading preview..."}
                          </div>
                        )}
                      </div>
                    </div>
                  )}
                </MobileFullScreen>
              )}
            </div>
          ) : (
            <div className="flex-1 flex min-h-0">
              <div className="w-full md:w-72 md:border-r border-border md:shrink-0">
                <RegistryBrowser
                  selectedSlug={selectedRegistryFlow?.slug}
                  onSelect={(flow) => {
                    setSelectedRegistryFlow(flow);
                  }}
                />
              </div>
              {!isMobile && (
                selectedRegistryFlow ? (
                  <>
                    <div className="flex-1 min-w-0">
                      {registryFlowDetail?.flow ? (
                        <FlowDagView
                          workflow={registryDagWorkflow}
                          runs={EMPTY_RUNS}
                          jobTree={null}
                          expandedSteps={expandedSteps}
                          onToggleExpand={toggleExpand}
                          selectedStep={null}
                          onSelectStep={() => {}}
                        />
                      ) : (
                        <div className="flex items-center justify-center h-full text-zinc-600 text-sm">
                          Loading preview...
                        </div>
                      )}
                    </div>
                    <div className="w-80 border-l border-border shrink-0">
                      <FlowInfoPanel
                        flow={selectedRegistryFlow}
                        onInstall={handleInstall}
                        isInstalling={installMutation.isPending}
                        isInstalled={installedSlugs.has(selectedRegistryFlow.slug)}
                      />
                    </div>
                  </>
                ) : (
                  <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm">
                    Select a flow from the registry
                  </div>
                )
              )}
              {/* Registry detail as full-screen on mobile */}
              {isMobile && (
                <MobileFullScreen
                  open={!!selectedRegistryFlow}
                  onClose={() => setSelectedRegistryFlow(null)}
                  title={selectedRegistryFlow?.name ?? "Flow Details"}
                >
                  {selectedRegistryFlow && (
                    <FlowInfoPanel
                      flow={selectedRegistryFlow}
                      onInstall={handleInstall}
                      isInstalling={installMutation.isPending}
                      isInstalled={installedSlugs.has(selectedRegistryFlow.slug)}
                    />
                  )}
                </MobileFullScreen>
              )}
            </div>
          )}
        </div>
      </TooltipProvider>

      <AlertDialog
        open={!!pendingDeleteFlow}
        onOpenChange={handleDeleteDialogChange}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete flow?</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDeleteFlow
                ? `Delete flow "${pendingDeleteFlow.name}"? This cannot be undone.`
                : "This cannot be undone."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteFlowMutation.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmDelete}
              variant="destructive"
              disabled={deleteFlowMutation.isPending}
            >
              {deleteFlowMutation.isPending ? "Deleting..." : "Delete flow"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
