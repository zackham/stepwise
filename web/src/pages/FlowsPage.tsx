import { useState, useCallback, useMemo } from "react";
import { useNavigate } from "@tanstack/react-router";
import { RegistryBrowser } from "@/components/editor/RegistryBrowser";
import { FlowInfoPanel } from "@/components/editor/FlowInfoPanel";
import { LocalFlowInfoPanel } from "@/components/editor/LocalFlowInfoPanel";
import { FlowDagView } from "@/components/dag/FlowDagView";
import {
  useLocalFlows,
  useLocalFlow,
  useCreateFlow,
  useDeleteFlow,
  useRegistryFlow,
  useInstallFlow,
  usePatchFlowMetadata,
  useFlowStats,
} from "@/hooks/useEditor";
import { useStepwiseMutations } from "@/hooks/useStepwise";
import {
  FileText,
  FolderOpen,
  Globe,
  Plus,
  Search,
} from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
  const [showNewFlowInput, setShowNewFlowInput] = useState(false);
  const [newFlowName, setNewFlowName] = useState("");

  // Data
  const { data: flows = [] } = useLocalFlows();
  const { data: flowStats = [] } = useFlowStats();
  const createFlowMutation = useCreateFlow();
  const deleteFlowMutation = useDeleteFlow();
  const mutations = useStepwiseMutations();
  const patchMetadataMutation = usePatchFlowMetadata();

  // Local selection
  const [selectedLocalFlow, setSelectedLocalFlow] = useState<LocalFlow | null>(null);
  const { data: localFlowDetail } = useLocalFlow(selectedLocalFlow?.path);
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
      if (!confirm(`Delete flow "${flow.name}"? This cannot be undone.`)) return;
      deleteFlowMutation.mutate(flow.path, {
        onSuccess: () => {
          if (selectedLocalFlow?.path === flow.path) {
            setSelectedLocalFlow(null);
          }
        },
      });
    },
    [deleteFlowMutation, selectedLocalFlow]
  );

  const handleCreate = useCallback(() => {
    const name = newFlowName.trim();
    if (!name) return;
    createFlowMutation.mutate(name, {
      onSuccess: (result) => {
        setShowNewFlowInput(false);
        setNewFlowName("");
        navigate({ to: "/flows/$flowName", params: { flowName: result.name } });
      },
    });
  }, [newFlowName, createFlowMutation, navigate]);

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

  const handlePatchMetadata = useCallback(
    (metadata: Parameters<typeof patchMetadataMutation.mutate>[0]["metadata"]) => {
      if (!selectedLocalFlow) return;
      patchMetadataMutation.mutate({ path: selectedLocalFlow.path, metadata });
    },
    [selectedLocalFlow, patchMetadataMutation]
  );

  const registryDagWorkflow = registryFlowDetail?.flow ?? { steps: {} };
  const localDagWorkflow = localFlowDetail?.flow ?? { steps: {} };

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 sm:gap-4 px-3 sm:px-6 py-3 sm:py-4 border-b border-border shrink-0">
        <div className="flex items-center gap-2">
          <button
            onClick={() => { setTab("local"); setSelectedRegistryFlow(null); }}
            className={cn(
              "px-3 py-1.5 text-sm rounded-md transition-colors",
              tab === "local"
                ? "bg-zinc-800 text-foreground"
                : "text-zinc-500 hover:text-foreground hover:bg-zinc-800/50"
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
                ? "bg-zinc-800 text-foreground"
                : "text-zinc-500 hover:text-foreground hover:bg-zinc-800/50"
            )}
          >
            <Globe className="w-3.5 h-3.5 inline mr-1.5" />
            Registry
          </button>
        </div>

        <div className="flex-1" />

        {tab === "local" && (
          <>
            {showNewFlowInput ? (
              <form
                onSubmit={(e) => { e.preventDefault(); handleCreate(); }}
                className="flex gap-1.5"
              >
                <Input
                  autoFocus
                  value={newFlowName}
                  onChange={(e) => setNewFlowName(e.target.value)}
                  placeholder="flow-name"
                  className="w-28 sm:w-40 h-8 text-sm bg-zinc-900 border-zinc-700"
                  onKeyDown={(e) => {
                    if (e.key === "Escape") {
                      setShowNewFlowInput(false);
                      setNewFlowName("");
                    }
                  }}
                />
                <Button
                  type="submit"
                  size="sm"
                  disabled={!newFlowName.trim() || createFlowMutation.isPending}
                  className="h-8"
                >
                  {createFlowMutation.isPending ? "..." : "Create"}
                </Button>
              </form>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowNewFlowInput(true)}
                className="h-8"
              >
                <Plus className="w-3.5 h-3.5 mr-1.5" />
                New Flow
              </Button>
            )}
            {createFlowMutation.isError && (
              <span className="text-xs text-red-400">
                {(createFlowMutation.error as Error).message?.includes("409")
                  ? "Already exists"
                  : "Failed"}
              </span>
            )}
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
                  className="pl-8 h-8 text-sm bg-zinc-900 border-zinc-700"
                />
              </div>
              <Select value={sortBy} onValueChange={(v) => setSortBy(v as SortBy)}>
                <SelectTrigger className="w-28 h-8 text-xs bg-zinc-900 border-zinc-700">
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
                        onClick={() => setShowNewFlowInput(true)}
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
                    onClick={() => {
                      if (isMobile) {
                        handleEdit(flow);
                      } else {
                        setSelectedLocalFlow(flow);
                      }
                    }}
                    onDoubleClick={() => handleEdit(flow)}
                    className={cn(
                      "w-full text-left px-3 py-2 text-sm flex items-center gap-2 transition-colors",
                      selectedLocalFlow?.path === flow.path
                        ? "bg-zinc-800 text-foreground"
                        : "text-zinc-400 hover:text-foreground hover:bg-zinc-800/50"
                    )}
                  >
                    {flow.is_directory ? (
                      <FolderOpen className="w-3.5 h-3.5 shrink-0 text-blue-400" />
                    ) : (
                      <FileText className="w-3.5 h-3.5 shrink-0 text-zinc-500" />
                    )}
                    <div className="flex flex-col min-w-0 flex-1">
                      <span className="truncate">{flow.name}</span>
                      {flow.description && (
                        <span className="text-[10px] text-zinc-600 truncate leading-tight">
                          {flow.description}
                        </span>
                      )}
                    </div>
                    <span className="ml-auto text-xs text-zinc-600 shrink-0">
                      {sortBy === "most-used"
                        ? `${statsMap.get(flowDirKey(flow.path))?.job_count ?? 0} jobs`
                        : sortBy === "recent"
                          ? statsMap.get(flowDirKey(flow.path))?.last_run_at
                            ? formatRelativeTime(statsMap.get(flowDirKey(flow.path))!.last_run_at!)
                            : "never"
                          : flow.steps_count}
                    </span>
                  </button>
                ))
              )}
            </div>
          </div>

          {/* DAG preview + detail panel (desktop only) */}
          {!isMobile && (
            selectedLocalFlow ? (
              <>
                <div className="flex-1 min-w-0">
                  {localFlowDetail?.flow && Object.keys(localFlowDetail.flow.steps).length > 0 ? (
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
                <div className="w-80 border-l border-border shrink-0">
                  <LocalFlowInfoPanel
                    flow={selectedLocalFlow}
                    detail={localFlowDetail}
                    onEdit={() => handleEdit(selectedLocalFlow)}
                    onRun={() => handleRun(selectedLocalFlow)}
                    onDelete={() => handleDelete(selectedLocalFlow)}
                    onPatchMetadata={handlePatchMetadata}
                  />
                </div>
              </>
            ) : (
              <div className="flex-1 flex items-center justify-center text-zinc-600 text-sm">
                Select a flow to preview
              </div>
            )
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
          {/* Registry detail as Sheet on mobile */}
          {isMobile && (
            <Sheet
              open={!!selectedRegistryFlow}
              onOpenChange={(open) => {
                if (!open) setSelectedRegistryFlow(null);
              }}
            >
              <SheetContent side="right" showCloseButton={false} className="w-[85vw] sm:max-w-sm p-0 overflow-y-auto">
                {selectedRegistryFlow && (
                  <FlowInfoPanel
                    flow={selectedRegistryFlow}
                    onInstall={handleInstall}
                    isInstalling={installMutation.isPending}
                    isInstalled={installedSlugs.has(selectedRegistryFlow.slug)}
                  />
                )}
              </SheetContent>
            </Sheet>
          )}
        </div>
      )}
    </div>
  );
}
