import { useState, useCallback, useMemo, useEffect } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { CreateFlowDialog } from "@/components/editor/CreateFlowDialog";
import {
  useLocalFlows,
  useDeleteFlow,
  useForkFlow,
  useRegistrySearch,
  useInstallFlow,
  useFlowStats,
} from "@/hooks/useEditor";
import { useStepwiseMutations } from "@/hooks/useStepwise";
import {
  Check,
  Download,
  Eye,
  GitFork,
  Hand,
  FileText,
  FolderOpen,
  Globe,
  LayoutGrid,
  List,
  Loader2,
  Plus,
  Search,
  User,
  WifiOff,
} from "lucide-react";
import { ActionContextProvider } from "@/components/menus/ActionContextProvider";
import { EntityContextMenu } from "@/components/menus/EntityContextMenu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/utils";
import type { LocalFlow, RegistryFlow } from "@/lib/types";

type Tab = "local" | "registry";
type SortBy = "name" | "most-used" | "recent";
type VisibilityFilter = "all" | "interactive" | "background" | "internal";

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
  const [visibilityFilter, setVisibilityFilter] = useState<VisibilityFilter>("all");
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [viewMode, setViewMode] = useState<"cards" | "list">("cards");

  // Data
  const { data: flows = [] } = useLocalFlows();
  const { data: flowStats = [] } = useFlowStats();
  const deleteFlowMutation = useDeleteFlow();
  const forkFlowMutation = useForkFlow();
  const mutations = useStepwiseMutations();

  // Local selection (for delete side-effect tracking)
  const { selected: selectedFlowName } = useSearch({ from: "/flows" });
  const [selectedLocalFlow, setSelectedLocalFlow] = useState<LocalFlow | null>(null);

  useEffect(() => {
    if (!selectedFlowName) {
      setSelectedLocalFlow(null);
      return;
    }
    const match = flows.find((flow) => flow.name === selectedFlowName) ?? null;
    setSelectedLocalFlow((current) => (current?.path === match?.path ? current : match));
  }, [selectedFlowName, flows]);

  // Registry
  const [registryQuery, setRegistryQuery] = useState("");
  const [registrySort, setRegistrySort] = useState<"downloads" | "newest">("downloads");
  const { data: registryData, isLoading: registryLoading, isError: registryError } = useRegistrySearch(registryQuery, registrySort);
  const registryFlows = registryData?.flows ?? [];
  const installMutation = useInstallFlow();
  const [installedSlugs, setInstalledSlugs] = useState<Map<string, string>>(new Map());

  // Map registry slugs to their local flow names so we can detect already-installed flows.
  // Keys are bare slugs (e.g. "my-flow") so lookups match RegistryFlow.slug directly.
  // We check two sources:
  //   1. Cached registry flows (source=registry) whose registry_ref is "@author:slug"
  //   2. Locally installed flows whose name matches a slug (installed via /registry/install)
  const localRegistryMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const f of flows) {
      if (f.source === "registry" && f.registry_ref) {
        // Extract bare slug from "@author:slug" format
        const colonIdx = f.registry_ref.indexOf(":");
        const slug = colonIdx >= 0 ? f.registry_ref.substring(colonIdx + 1) : f.registry_ref;
        map.set(slug, f.name);
      }
    }
    // Also detect flows installed via /registry/install — they live in flows/<slug>/
    // and appear as source=local with name equal to the slug.
    // We add them only if not already covered by registry_ref mapping above.
    for (const f of flows) {
      if (f.source === "local" && !map.has(f.name)) {
        // Will be matched when a registry flow's slug equals this local flow's name
        map.set(f.name, f.name);
      }
    }
    return map;
  }, [flows]);

  const statsMap = useMemo(
    () => new Map(flowStats.map((s) => [s.flow_dir, s])),
    [flowStats]
  );

  const filtered = useMemo(() => {
    let result = filter
      ? flows.filter((f) => f.name.toLowerCase().includes(filter.toLowerCase()))
      : [...flows];

    if (visibilityFilter === "all") {
      result = result.filter((f) => (f.visibility ?? "interactive") !== "internal");
    } else {
      result = result.filter((f) => (f.visibility ?? "interactive") === visibilityFilter);
    }

    result.sort((a, b) => {
      if (sortBy === "name") return a.name.localeCompare(b.name);
      const sa = statsMap.get(flowDirKey(a.path));
      const sb = statsMap.get(flowDirKey(b.path));
      if (sortBy === "most-used") {
        const diff = (sb?.job_count ?? 0) - (sa?.job_count ?? 0);
        return diff !== 0 ? diff : a.name.localeCompare(b.name);
      }
      const ta = sa?.last_run_at ?? "";
      const tb = sb?.last_run_at ?? "";
      if (ta === tb) return a.name.localeCompare(b.name);
      if (!ta) return 1;
      if (!tb) return -1;
      return tb.localeCompare(ta);
    });

    return result;
  }, [flows, filter, sortBy, visibilityFilter, statsMap]);

  const handleSelectLocalFlow = useCallback(
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

  const handleFlowCreated = useCallback(
    (result: { path: string; name: string }) => {
      navigate({ to: "/flows/$flowName", params: { flowName: result.name } });
    },
    [navigate]
  );

  const handleRegistryFlowClick = useCallback((flow: RegistryFlow) => {
    // If already installed locally, navigate directly to editor
    const localName = localRegistryMap.get(flow.slug) ?? installedSlugs.get(flow.slug);
    if (localName) {
      navigate({ to: "/flows/$flowName", params: { flowName: localName } });
      return;
    }
    // Otherwise install, then navigate
    installMutation.mutate(flow.slug, {
      onSuccess: (result) => {
        setInstalledSlugs((prev) => new Map([...prev, [flow.slug, result.name]]));
        navigate({ to: "/flows/$flowName", params: { flowName: result.name } });
      },
    });
  }, [installMutation, navigate, localRegistryMap, installedSlugs]);

  // Fork
  const [showForkDialog, setShowForkDialog] = useState(false);
  const [forkName, setForkName] = useState("");
  const [forkSource, setForkSource] = useState<LocalFlow | null>(null);

  const handleFork = useCallback(
    (flow: LocalFlow) => {
      setForkSource(flow);
      setForkName(flow.name);
      setShowForkDialog(true);
    },
    []
  );

  const handleForkSubmit = useCallback(() => {
    if (!forkSource || !forkName.trim()) return;
    forkFlowMutation.mutate(
      { sourcePath: forkSource.path, name: forkName.trim() },
      {
        onSuccess: (result) => {
          setShowForkDialog(false);
          setForkSource(null);
          setForkName("");
          navigate({ to: "/flows/$flowName", params: { flowName: result.name } });
        },
      }
    );
  }, [forkSource, forkName, forkFlowMutation, navigate]);

  return (
    <>
      <TooltipProvider>
        <div className="h-full flex flex-col">
          {/* Header */}
          <div className="flex items-center gap-2 sm:gap-4 px-4 sm:px-6 py-3 border-b border-border shrink-0">
            <div className="flex items-center gap-1 rounded-lg border border-border p-0.5 bg-zinc-100/50 dark:bg-zinc-900/50">
              <button
                onClick={() => setTab("local")}
                className={cn(
                  "flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors",
                  tab === "local"
                    ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                    : "text-zinc-500 hover:text-foreground"
                )}
              >
                <FolderOpen className="w-3.5 h-3.5" />
                Local
              </button>
              <button
                onClick={() => setTab("registry")}
                className={cn(
                  "flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors",
                  tab === "registry"
                    ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                    : "text-zinc-500 hover:text-foreground"
                )}
              >
                <Globe className="w-3.5 h-3.5" />
                Registry
              </button>
            </div>

            {tab === "local" && (
              <>
                <div className="relative flex-1 max-w-sm">
                  <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500" />
                  <Input
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    placeholder="Search flows..."
                    className="pl-8 h-8 text-sm bg-zinc-50 dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700"
                  />
                </div>
                <Select value={visibilityFilter} onValueChange={(v) => setVisibilityFilter(v as VisibilityFilter)}>
                  <SelectTrigger className="w-28 h-8 text-xs bg-zinc-50 dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700">
                    <Eye className="w-3 h-3 mr-1 shrink-0" />
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All</SelectItem>
                    <SelectItem value="interactive">Interactive</SelectItem>
                    <SelectItem value="background">Background</SelectItem>
                    <SelectItem value="internal">Internal</SelectItem>
                  </SelectContent>
                </Select>
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
                <div className="flex items-center gap-1 rounded-lg border border-border p-0.5 bg-zinc-100/50 dark:bg-zinc-900/50">
                  <button
                    onClick={() => setViewMode("cards")}
                    className={cn(
                      "flex items-center gap-1.5 px-2 py-1 text-xs rounded-md transition-colors",
                      viewMode === "cards"
                        ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                        : "text-zinc-500 hover:text-foreground"
                    )}
                  >
                    <LayoutGrid className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => setViewMode("list")}
                    className={cn(
                      "flex items-center gap-1.5 px-2 py-1 text-xs rounded-md transition-colors",
                      viewMode === "list"
                        ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                        : "text-zinc-500 hover:text-foreground"
                    )}
                  >
                    <List className="w-3.5 h-3.5" />
                  </button>
                </div>
                <div className="flex-1" />
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
            <ActionContextProvider
              sideEffects={{
                onRunFlow: handleRun,
                onAfterDeleteFlow: (flow) => {
                  if (selectedLocalFlow?.path === flow.path) {
                    setSelectedLocalFlow(null);
                    navigate({ to: "/flows", search: {}, replace: true });
                  }
                },
              }}
              extraMutations={{ deleteFlow: deleteFlowMutation }}
            >
              <div className="flex-1 overflow-y-auto">
                {filtered.length === 0 ? (
                  <div className="flex flex-col items-center justify-center h-full px-4 max-w-sm mx-auto text-center">
                    {flows.length === 0 ? (
                      <>
                        <img src="/stepwise-icon-64.png" alt="Stepwise" className="w-12 h-12 opacity-40 mb-3" />
                        <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400 mb-1">Create your first flow</p>
                        <p className="text-xs text-zinc-500 dark:text-zinc-600 mb-4">
                          Flows define multi-step workflows for agents and humans.
                        </p>
                        <div className="flex gap-2">
                          <Button size="sm" className="text-xs" onClick={() => setShowCreateDialog(true)}>
                            <Plus className="w-3 h-3 mr-1" /> Create Flow
                          </Button>
                          <Button variant="outline" size="sm" className="text-xs" onClick={() => setTab("registry")}>
                            <Globe className="w-3 h-3 mr-1" /> Browse Registry
                          </Button>
                        </div>
                      </>
                    ) : (
                      <>
                        <FileText className="w-8 h-8 mb-2 opacity-40 text-zinc-500 dark:text-zinc-600" />
                        <p className="text-xs text-zinc-500 dark:text-zinc-600">No matching flows</p>
                      </>
                    )}
                  </div>
                ) : viewMode === "cards" ? (
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3 p-4 sm:p-6">
                    {filtered.map((flow) => {
                      const stats = statsMap.get(flowDirKey(flow.path));
                      const jobCount = stats?.job_count ?? 0;
                      const lastRun = stats?.last_run_at;

                      return (
                        <EntityContextMenu key={flow.path} type="flow" data={flow}>
                          <button
                            onClick={() => handleSelectLocalFlow(flow)}
                            className="w-full text-left rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white/80 dark:bg-zinc-900/80 hover:border-zinc-300 dark:hover:border-zinc-700 hover:bg-white dark:hover:bg-zinc-900 transition-all p-4 flex flex-col gap-2.5 group"
                          >
                            {/* Header: icon + name + badges */}
                            <div className="flex items-center gap-2 min-w-0">
                              {flow.source === "registry" ? (
                                <Globe className="w-4 h-4 text-violet-400 shrink-0" />
                              ) : flow.is_directory ? (
                                <FolderOpen className="w-4 h-4 text-blue-400 shrink-0" />
                              ) : (
                                <FileText className="w-4 h-4 text-zinc-500 shrink-0" />
                              )}
                              <span className="text-sm font-medium text-foreground group-hover:text-blue-500 dark:group-hover:text-blue-400 truncate transition-colors">
                                {flow.name}
                              </span>
                              {flow.executor_types?.includes("external") && (
                                <Hand className="w-3 h-3 text-amber-400 shrink-0" />
                              )}
                            </div>

                            {/* Description */}
                            <p className="text-xs text-zinc-500 dark:text-zinc-500 line-clamp-2 min-h-[2lh]">
                              {flow.description || flow.registry_ref || "No description"}
                            </p>

                            {/* Badges row */}
                            <div className="flex flex-wrap gap-1">
                              {flow.source === "registry" && (
                                <span className="text-[9px] px-1.5 py-0.5 rounded bg-violet-100 dark:bg-violet-900/40 text-violet-600 dark:text-violet-400 uppercase tracking-wider">
                                  Registry
                                </span>
                              )}
                              {flow.visibility && flow.visibility !== "interactive" && (
                                <span className="text-[9px] px-1.5 py-0.5 rounded bg-zinc-200 dark:bg-zinc-700 text-zinc-500 dark:text-zinc-400 uppercase tracking-wider">
                                  {flow.visibility}
                                </span>
                              )}
                              {(flow.executor_types ?? []).map((t) => (
                                <span
                                  key={t}
                                  className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 font-mono"
                                >
                                  {t}
                                </span>
                              ))}
                            </div>

                            {/* Stats footer */}
                            <div className="flex items-center gap-3 text-[11px] text-zinc-400 dark:text-zinc-500 pt-1 border-t border-zinc-100 dark:border-zinc-800">
                              <span>{flow.steps_count} step{flow.steps_count !== 1 ? "s" : ""}</span>
                              <span>{jobCount > 0 ? `${jobCount} job${jobCount !== 1 ? "s" : ""}` : "no jobs"}</span>
                              <span className="ml-auto">{lastRun ? formatRelativeTime(lastRun) : "never run"}</span>
                            </div>
                          </button>
                        </EntityContextMenu>
                      );
                    })}
                  </div>
                ) : (
                  <div className="divide-y divide-border">
                    {filtered.map((flow) => {
                      const stats = statsMap.get(flowDirKey(flow.path));
                      const jobCount = stats?.job_count ?? 0;
                      const lastRun = stats?.last_run_at;

                      return (
                        <EntityContextMenu key={flow.path} type="flow" data={flow}>
                          <button
                            onClick={() => handleSelectLocalFlow(flow)}
                            className="w-full text-left px-4 sm:px-6 py-3 flex items-start gap-3 transition-colors hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40 group"
                          >
                            <div className="mt-0.5 shrink-0">
                              {flow.source === "registry" ? (
                                <Globe className="w-4 h-4 text-violet-400" />
                              ) : flow.is_directory ? (
                                <FolderOpen className="w-4 h-4 text-blue-400" />
                              ) : (
                                <FileText className="w-4 h-4 text-zinc-500" />
                              )}
                            </div>
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center gap-2">
                                <span className="text-sm font-medium text-foreground group-hover:text-blue-500 dark:group-hover:text-blue-400 truncate transition-colors">
                                  {flow.name}
                                </span>
                                {flow.source === "registry" && (
                                  <span className="text-[9px] px-1 py-0.5 rounded bg-violet-100 dark:bg-violet-900/40 text-violet-600 dark:text-violet-400 uppercase tracking-wider shrink-0">
                                    Registry
                                  </span>
                                )}
                                {flow.visibility && flow.visibility !== "interactive" && (
                                  <span className="text-[9px] px-1 py-0.5 rounded bg-zinc-200 dark:bg-zinc-700 text-zinc-500 dark:text-zinc-400 uppercase tracking-wider shrink-0">
                                    {flow.visibility}
                                  </span>
                                )}
                                {flow.executor_types?.includes("external") && (
                                  <Hand className="w-3 h-3 text-amber-400 shrink-0" />
                                )}
                              </div>
                              {(flow.description || flow.registry_ref) && (
                                <p className="text-xs text-zinc-500 dark:text-zinc-500 truncate mt-0.5">
                                  {flow.registry_ref ?? flow.description}
                                </p>
                              )}
                            </div>
                            <div className="hidden sm:flex items-center gap-6 shrink-0 text-[11px] text-zinc-500 dark:text-zinc-500 tabular-nums">
                              {(flow.executor_types?.length ?? 0) > 0 && (
                                <div className="flex gap-1 w-28 justify-end">
                                  {(flow.executor_types ?? []).slice(0, 3).map((t) => (
                                    <span key={t} className="px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 text-[10px] font-mono">
                                      {t}
                                    </span>
                                  ))}
                                </div>
                              )}
                              <span className="w-12 text-right">{flow.steps_count} step{flow.steps_count !== 1 ? "s" : ""}</span>
                              <span className="w-14 text-right">{jobCount > 0 ? `${jobCount} job${jobCount !== 1 ? "s" : ""}` : "—"}</span>
                              <span className="w-16 text-right">{lastRun ? formatRelativeTime(lastRun) : "never"}</span>
                            </div>
                          </button>
                        </EntityContextMenu>
                      );
                    })}
                  </div>
                )}
              </div>
            </ActionContextProvider>
          ) : (
            /* Registry tab */
            <div className="flex-1 overflow-y-auto">
              {/* Registry toolbar */}
              <div className="flex items-center gap-3 px-4 sm:px-6 py-3 border-b border-border">
                <div className="relative flex-1 max-w-sm">
                  <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500" />
                  <Input
                    value={registryQuery}
                    onChange={(e) => setRegistryQuery(e.target.value)}
                    placeholder="Search registry..."
                    className="pl-8 h-8 text-sm bg-zinc-50 dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700"
                  />
                </div>
                <div className="flex items-center gap-1 rounded-lg border border-border p-0.5 bg-zinc-100/50 dark:bg-zinc-900/50">
                  <button
                    onClick={() => setRegistrySort("downloads")}
                    className={cn(
                      "px-2.5 py-1 text-xs rounded-md transition-colors",
                      registrySort === "downloads"
                        ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                        : "text-zinc-500 hover:text-foreground"
                    )}
                  >
                    Popular
                  </button>
                  <button
                    onClick={() => setRegistrySort("newest")}
                    className={cn(
                      "px-2.5 py-1 text-xs rounded-md transition-colors",
                      registrySort === "newest"
                        ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                        : "text-zinc-500 hover:text-foreground"
                    )}
                  >
                    Newest
                  </button>
                </div>
                <div className="flex items-center gap-1 rounded-lg border border-border p-0.5 bg-zinc-100/50 dark:bg-zinc-900/50">
                  <button
                    onClick={() => setViewMode("cards")}
                    className={cn(
                      "flex items-center gap-1.5 px-2 py-1 text-xs rounded-md transition-colors",
                      viewMode === "cards"
                        ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                        : "text-zinc-500 hover:text-foreground"
                    )}
                  >
                    <LayoutGrid className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => setViewMode("list")}
                    className={cn(
                      "flex items-center gap-1.5 px-2 py-1 text-xs rounded-md transition-colors",
                      viewMode === "list"
                        ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                        : "text-zinc-500 hover:text-foreground"
                    )}
                  >
                    <List className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>

              {registryError ? (
                <div className="flex flex-col items-center justify-center h-64 text-zinc-500">
                  <WifiOff className="w-8 h-8 mb-3 opacity-40" />
                  <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">Registry not configured</p>
                  <p className="text-xs text-zinc-600 mt-1 text-center max-w-xs">
                    The flow registry provides shared, reusable workflows.
                  </p>
                </div>
              ) : registryLoading ? (
                <div className="flex items-center justify-center h-64">
                  <Loader2 className="w-5 h-5 animate-spin text-zinc-500" />
                </div>
              ) : registryFlows.length === 0 ? (
                <div className="flex items-center justify-center h-64 text-xs text-zinc-500">
                  {registryQuery ? "No flows found" : "No flows in registry"}
                </div>
              ) : (
                <div className={cn(
                  viewMode === "cards"
                    ? "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3 p-4 sm:p-6"
                    : "divide-y divide-border"
                )}>
                  {registryFlows.map((flow) => {
                    const isInstalled = localRegistryMap.has(flow.slug) || installedSlugs.has(flow.slug);

                    return viewMode === "cards" ? (
                      <button
                        key={flow.slug}
                        onClick={() => handleRegistryFlowClick(flow)}
                        disabled={installMutation.isPending && !isInstalled}
                        className="w-full text-left rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white/80 dark:bg-zinc-900/80 hover:border-zinc-300 dark:hover:border-zinc-700 hover:bg-white dark:hover:bg-zinc-900 transition-all p-4 flex flex-col gap-2.5 group"
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <Globe className="w-4 h-4 text-violet-400 shrink-0" />
                          <span className="text-sm font-medium text-foreground group-hover:text-blue-500 dark:group-hover:text-blue-400 truncate transition-colors">{flow.name}</span>
                          {flow.featured && (
                            <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-400 uppercase tracking-wider shrink-0">
                              Featured
                            </span>
                          )}
                          {isInstalled && (
                            <span className="flex items-center gap-0.5 text-[9px] px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-400 uppercase tracking-wider shrink-0">
                              <Check className="w-2.5 h-2.5" />Installed
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-zinc-500 line-clamp-2 min-h-[2lh]">
                          {flow.description || "No description"}
                        </p>
                        <div className="flex flex-wrap gap-1">
                          {flow.executor_types.map((t) => (
                            <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 font-mono">
                              {t}
                            </span>
                          ))}
                        </div>
                        <div className="flex items-center gap-3 text-[11px] text-zinc-400 dark:text-zinc-500 pt-1 border-t border-zinc-100 dark:border-zinc-800">
                          <span className="flex items-center gap-1"><User className="w-3 h-3" />{flow.author}</span>
                          <span>{flow.steps} step{flow.steps !== 1 ? "s" : ""}</span>
                          <span>{flow.downloads} dl{flow.downloads !== 1 ? "s" : ""}</span>
                          {!isInstalled && (
                            <span className="ml-auto flex items-center gap-1 text-blue-500 dark:text-blue-400 font-medium">
                              <Download className="w-3 h-3" />Install
                            </span>
                          )}
                        </div>
                      </button>
                    ) : (
                      <button
                        key={flow.slug}
                        onClick={() => handleRegistryFlowClick(flow)}
                        disabled={installMutation.isPending && !isInstalled}
                        className="w-full text-left px-4 sm:px-6 py-3 flex items-start gap-3 transition-colors hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40 group"
                      >
                        <Globe className="w-4 h-4 text-violet-400 shrink-0 mt-0.5" />
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-foreground group-hover:text-blue-500 dark:group-hover:text-blue-400 truncate transition-colors">{flow.name}</span>
                            {flow.featured && (
                              <span className="text-[9px] px-1 py-0.5 rounded bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-400 uppercase tracking-wider shrink-0">
                                Featured
                              </span>
                            )}
                            {isInstalled && (
                              <span className="flex items-center gap-0.5 text-[9px] px-1 py-0.5 rounded bg-green-100 dark:bg-green-900/40 text-green-600 dark:text-green-400 uppercase tracking-wider shrink-0">
                                <Check className="w-2.5 h-2.5" />Installed
                              </span>
                            )}
                          </div>
                          <p className="text-xs text-zinc-500 truncate mt-0.5">{flow.description}</p>
                        </div>
                        <div className="hidden sm:flex items-center gap-6 shrink-0 text-[11px] text-zinc-500 tabular-nums">
                          <span className="flex items-center gap-1"><User className="w-3 h-3" />{flow.author}</span>
                          <span className="w-12 text-right">{flow.steps} step{flow.steps !== 1 ? "s" : ""}</span>
                          <span className="w-12 text-right">{flow.downloads} dls</span>
                          {!isInstalled && (
                            <span className="flex items-center gap-1 text-blue-500 dark:text-blue-400 font-medium">
                              <Download className="w-3 h-3" />Install
                            </span>
                          )}
                        </div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </TooltipProvider>

      {/* Fork Dialog */}
      <Dialog open={showForkDialog} onOpenChange={setShowForkDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Fork Registry Flow</DialogTitle>
            <DialogDescription>
              Copy {forkSource?.registry_ref ?? forkSource?.name} to your local flows directory for editing.
            </DialogDescription>
          </DialogHeader>
          <div className="py-4">
            <Label htmlFor="fork-name">Flow name</Label>
            <Input
              id="fork-name"
              value={forkName}
              onChange={(e) => setForkName(e.target.value)}
              placeholder="my-flow"
              className="mt-1.5"
              onKeyDown={(e) => {
                if (e.key === "Enter") handleForkSubmit();
              }}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowForkDialog(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleForkSubmit}
              disabled={!forkName.trim() || forkFlowMutation.isPending}
            >
              <GitFork className="w-3.5 h-3.5 mr-1.5" />
              {forkFlowMutation.isPending ? "Forking..." : "Fork"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
