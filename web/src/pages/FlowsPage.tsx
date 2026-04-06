import { useState, useCallback, useMemo, useEffect, useRef } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { CreateFlowDialog } from "@/components/editor/CreateFlowDialog";
import { MiniFlowDag } from "@/components/canvas/MiniFlowDag";
import {
  useLocalFlows,
  useKits,
  useDeleteFlow,
  useForkFlow,
  useRegistrySearch,
  useInstallFlow,
  useFlowStats,
} from "@/hooks/useEditor";
import { useStepwiseMutations } from "@/hooks/useStepwise";
import {
  Check,
  ChevronRight,
  Download,
  Eye,
  GitFork,
  FileText,
  FolderOpen,
  Globe,
  Info,
  LayoutGrid,
  List,
  Loader2,
  Minus,
  Package,
  Plus,
  Search,
  Trash2,
  User,
  WifiOff,
  X,
} from "lucide-react";
import { ActionContextProvider } from "@/components/menus/ActionContextProvider";
import { EntityContextMenu } from "@/components/menus/EntityContextMenu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { ComboBox } from "@/components/ui/ComboBox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ConfirmDialog } from "@/components/menus/ConfirmDialog";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Kit, LocalFlow, RegistryFlow } from "@/lib/types";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

type Tab = "local" | "registry";
type VisibilityFilter = "all" | "interactive" | "background" | "internal";
type TimeRange = "today" | "7d" | "30d" | undefined;
type FlowSortCol = "name" | "steps" | "jobs" | "last_run" | "updated";
type RegistrySortCol = "name" | "author" | "steps" | "downloads" | "updated";

const VISIBILITY_OPTIONS = [
  { value: "all", label: "All" },
  { value: "interactive", label: "Interactive" },
  { value: "background", label: "Background" },
  { value: "internal", label: "Internal" },
];

const TIME_RANGE_OPTIONS = [
  { value: "all", label: "All time" },
  { value: "today", label: "Today" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
];

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

function filterByTimeRange(flows: LocalFlow[], range: TimeRange): LocalFlow[] {
  if (!range) return flows;
  const now = Date.now();
  const cutoff =
    range === "today"
      ? now - 86400000
      : range === "7d"
        ? now - 7 * 86400000
        : now - 30 * 86400000;
  return flows.filter((f) => new Date(f.modified_at).getTime() >= cutoff);
}

function SortHeader<T extends string>({ col, label, current, asc, onSort, className }: {
  col: T;
  label: string;
  current: T;
  asc: boolean;
  onSort: (col: T) => void;
  className?: string;
}) {
  const active = current === col;
  return (
    <button
      onClick={() => onSort(col)}
      className={cn(
        "flex items-center gap-0.5 hover:text-foreground transition-colors cursor-pointer",
        active ? "text-foreground" : "text-zinc-500",
        className,
      )}
    >
      <span className={cn(className?.includes("text-right") && "ml-auto")}>{label}</span>
      {active && (
        <span className="text-[8px]">{asc ? "▲" : "▼"}</span>
      )}
    </button>
  );
}

function KitSectionHeader({ kit, flowCount, expanded, onToggle, onInfo }: {
  kit: Kit;
  flowCount: number;
  expanded: boolean;
  onToggle: () => void;
  onInfo: () => void;
}) {
  return (
    <div className="flex items-center gap-2 w-full px-3 sm:px-4 py-2 bg-zinc-50/80 dark:bg-zinc-900/50">
      <button
        onClick={onToggle}
        className="flex items-center gap-2 flex-1 min-w-0 hover:text-foreground transition-colors"
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 transition-transform duration-200 text-zinc-400",
            expanded && "rotate-90"
          )}
        />
        <Package className="h-3.5 w-3.5 shrink-0 text-zinc-400" />
        <span className="text-xs font-semibold text-foreground truncate">{kit.name}</span>
        <span className="text-[11px] text-zinc-500 truncate hidden sm:inline">{kit.description}</span>
        {kit.category && (
          <Badge variant="outline" className="text-[9px] px-1.5 py-0 shrink-0">
            {kit.category}
          </Badge>
        )}
      </button>
      <button
        onClick={(e) => { e.stopPropagation(); onInfo(); }}
        className="p-1 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors shrink-0"
        title="Kit details"
      >
        <Info className="w-3.5 h-3.5" />
      </button>
      <span className="text-[10px] text-zinc-400 shrink-0 tabular-nums">
        {flowCount} flow{flowCount !== 1 ? "s" : ""}
      </span>
    </div>
  );
}

function FlowGridCard({ flow, statsMap, onSelect }: {
  flow: LocalFlow;
  statsMap: Map<string, { job_count: number; last_run_at?: string }>;
  onSelect: (flow: LocalFlow) => void;
}) {
  const stats = statsMap.get(flowDirKey(flow.path));
  const jobCount = stats?.job_count ?? 0;
  const lastRun = stats?.last_run_at;

  return (
    <EntityContextMenu type="flow" data={flow}>
      <button
        onClick={() => onSelect(flow)}
        className="w-full h-full text-left rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white/80 dark:bg-zinc-900/80 hover:border-zinc-300 dark:hover:border-zinc-700 hover:bg-white dark:hover:bg-zinc-900 transition-all overflow-hidden flex flex-col group"
      >
        <div className="px-3 pt-2.5 pb-1 flex items-start gap-2">
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-zinc-900 dark:text-zinc-100 truncate leading-tight">
              {flow.name}
            </p>
            {flow.visibility && flow.visibility !== "interactive" && (
              <p className="text-[11px] text-zinc-500 truncate leading-tight mt-0.5">
                {flow.visibility}
              </p>
            )}
          </div>
          {flow.source === "registry" && (
            <Badge variant="outline" className="text-xs font-mono uppercase tracking-wide bg-violet-500/10 text-violet-400 ring-1 ring-violet-500/30 border-transparent">
              Registry
            </Badge>
          )}
        </div>
        {flow.graph && flow.graph.nodes.length > 0 && (
          <div className="flex justify-center px-2">
            <MiniFlowDag graph={flow.graph} width={268} height={90} />
          </div>
        )}
        <div className="flex-1" />
        <div className="px-3 pb-2 pt-0.5 space-y-1">
          <p className="text-[11px] text-zinc-500 line-clamp-2">
            {flow.description || flow.registry_ref || "No description"}
          </p>
          <div className="flex items-center text-[11px] text-zinc-600 pt-1 border-t border-zinc-100 dark:border-zinc-800">
            <span>{flow.steps_count} step{flow.steps_count !== 1 ? "s" : ""}</span>
            <span className="mx-2 text-zinc-700">·</span>
            <span>{jobCount > 0 ? `${jobCount} job${jobCount !== 1 ? "s" : ""}` : "no jobs"}</span>
            <span className="ml-auto">{lastRun ? formatRelativeTime(lastRun) : "never run"}</span>
          </div>
        </div>
      </button>
    </EntityContextMenu>
  );
}

function FlowListRow({ flow, statsMap, selected, onSelect, onToggleSelect }: {
  flow: LocalFlow;
  statsMap: Map<string, { job_count: number; last_run_at?: string }>;
  selected: boolean;
  onSelect: (flow: LocalFlow) => void;
  onToggleSelect: (path: string, shiftKey: boolean) => void;
}) {
  const stats = statsMap.get(flowDirKey(flow.path));
  const jobCount = stats?.job_count ?? 0;
  const lastRun = stats?.last_run_at;

  return (
    <EntityContextMenu type="flow" data={flow}>
      <div
        onClick={(e) => {
          if ((e.target as HTMLElement).closest("[data-flow-checkbox]")) return;
          if ((e.target as HTMLElement).closest("[data-flow-link]")) return;
          onSelect(flow);
        }}
        className={cn(
          "w-full text-left px-4 sm:px-6 py-3 flex items-center gap-3 transition-none hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40 group cursor-pointer",
          selected && "bg-blue-50/50 dark:bg-blue-950/20",
        )}
      >
        <button
          data-flow-checkbox
          onClick={(e) => {
            e.stopPropagation();
            onToggleSelect(flow.path, e.shiftKey);
          }}
          className={cn(
            "w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-all duration-150",
            selected
              ? "bg-blue-500 border-blue-500 text-white opacity-100"
              : "border-zinc-400 dark:border-zinc-600 bg-white/90 dark:bg-zinc-800/90 hover:border-blue-400 opacity-0 group-hover:opacity-40 hover:!opacity-100",
          )}
        >
          {selected && <Check className="w-2.5 h-2.5" />}
        </button>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <a
              data-flow-link
              onClick={(e) => {
                e.stopPropagation();
                onSelect(flow);
              }}
              className="text-sm font-medium text-foreground hover:text-blue-500 dark:hover:text-blue-400 truncate transition-colors cursor-pointer"
            >
              {flow.name}
            </a>
            {flow.source === "registry" && (
              <Badge variant="outline" className="text-xs font-mono uppercase tracking-wide bg-violet-500/10 text-violet-400 ring-1 ring-violet-500/30 border-transparent">
                Registry
              </Badge>
            )}
            {flow.visibility && flow.visibility !== "interactive" && (
              <span className="text-[9px] px-1 py-0.5 rounded bg-zinc-200 dark:bg-zinc-700 text-zinc-500 dark:text-zinc-400 uppercase tracking-wider shrink-0">
                {flow.visibility}
              </span>
            )}
          </div>
          {(flow.description || flow.registry_ref) && (
            <p className="text-xs text-zinc-500 dark:text-zinc-500 truncate mt-0.5">
              {flow.description ?? flow.registry_ref}
            </p>
          )}
          <div className="flex items-center gap-2 mt-1 sm:hidden text-[10px] text-zinc-500 flex-wrap">
            <span>{flow.steps_count} step{flow.steps_count !== 1 ? "s" : ""}</span>
            <span>{jobCount > 0 ? `${jobCount} job${jobCount !== 1 ? "s" : ""}` : "no jobs"}</span>
            {lastRun && <span>ran {formatRelativeTime(lastRun)}</span>}
            <span>{flow.modified_at ? formatRelativeTime(flow.modified_at) : ""}</span>
          </div>
        </div>
        <div className="hidden sm:flex items-center gap-4 shrink-0 text-[11px] text-zinc-500 dark:text-zinc-500 tabular-nums">
          <span className="w-12 text-right">{flow.steps_count} step{flow.steps_count !== 1 ? "s" : ""}</span>
          <span className="w-14 text-right">{jobCount > 0 ? `${jobCount} job${jobCount !== 1 ? "s" : ""}` : "—"}</span>
          <span className="w-16 text-right">{lastRun ? formatRelativeTime(lastRun) : "—"}</span>
          <span className="w-16 text-right">{flow.modified_at ? formatRelativeTime(flow.modified_at) : "—"}</span>
        </div>
      </div>
    </EntityContextMenu>
  );
}

export function FlowsPage() {
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const [tab, setTab] = useState<Tab>("local");
  const [filter, setFilter] = useState("");
  const [visibilityFilter, setVisibilityFilter] = useState<VisibilityFilter>("all");
  const [timeRange, setTimeRange] = useState<TimeRange>(undefined);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [viewMode, setViewMode] = useState<"grid" | "list">("list");

  // Sort state for list view header
  const [sortCol, setSortCol] = useState<FlowSortCol>("last_run");
  const [sortAsc, setSortAsc] = useState(false);

  const handleSort = useCallback((col: FlowSortCol) => {
    if (sortCol === col) {
      setSortAsc((a) => !a);
    } else {
      setSortCol(col);
      setSortAsc(col === "name"); // name defaults asc, everything else desc
    }
  }, [sortCol]);

  // Selection state
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const lastSelectedRef = useRef<string | null>(null);
  const isSelectionActive = selectedIds.size > 0;

  const handleClearSelection = useCallback(() => {
    setSelectedIds(new Set());
    lastSelectedRef.current = null;
  }, []);

  // Data
  const { data: flows = [] } = useLocalFlows();
  const { data: kits = [] } = useKits();
  const { data: flowStats = [] } = useFlowStats();
  const deleteFlowMutation = useDeleteFlow();
  const forkFlowMutation = useForkFlow();
  const mutations = useStepwiseMutations();

  // Kit state
  const [kitFilter, setKitFilter] = useState<string>("all");
  const [expandedKits, setExpandedKits] = useState<Set<string>>(new Set());
  const [kitDetailName, setKitDetailName] = useState<string | null>(null);
  const kitDetailData = kits.find((k) => k.name === kitDetailName) ?? null;

  // Initialize all kits expanded
  useEffect(() => {
    if (kits.length > 0) {
      setExpandedKits((prev) => {
        if (prev.size > 0) return prev;
        return new Set(kits.map((k) => k.name));
      });
    }
  }, [kits]);

  const toggleKit = useCallback((name: string) => {
    setExpandedKits((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

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
  const [regSortCol, setRegSortCol] = useState<RegistrySortCol>("downloads");
  const [regSortAsc, setRegSortAsc] = useState(false);
  const [registryFilter, setRegistryFilter] = useState<"popular" | "featured" | "newest">("popular");
  const handleRegSort = useCallback((col: RegistrySortCol) => {
    if (regSortCol === col) {
      setRegSortAsc((a) => !a);
    } else {
      setRegSortCol(col);
      setRegSortAsc(col === "name" || col === "author");
    }
  }, [regSortCol]);

  const { data: registryData, isLoading: registryLoading, isError: registryError } = useRegistrySearch(registryQuery);
  const registryFlows = registryData?.flows ?? [];

  const sortedRegistryFlows = useMemo(() => {
    let result = registryFilter === "featured"
      ? registryFlows.filter((f) => f.featured)
      : [...registryFlows];
    result.sort((a, b) => {
      const dir = regSortAsc ? 1 : -1;
      switch (regSortCol) {
        case "name": return dir * a.name.localeCompare(b.name);
        case "author": return dir * a.author.localeCompare(b.author);
        case "steps": return dir * (a.steps - b.steps);
        case "downloads": return dir * (a.downloads - b.downloads);
        case "updated": return dir * ((a.updated_at ?? "").localeCompare(b.updated_at ?? ""));
        default: return 0;
      }
    });
    return result;
  }, [registryFlows, regSortCol, regSortAsc, registryFilter]);
  const installMutation = useInstallFlow();
  const [installedSlugs, setInstalledSlugs] = useState<Map<string, string>>(new Map());

  const localRegistryMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const f of flows) {
      if (f.source === "registry" && f.registry_ref) {
        const colonIdx = f.registry_ref.indexOf(":");
        const slug = colonIdx >= 0 ? f.registry_ref.substring(colonIdx + 1) : f.registry_ref;
        map.set(slug, f.name);
      }
    }
    for (const f of flows) {
      if (f.source === "local" && !map.has(f.name)) {
        map.set(f.name, f.name);
      }
    }
    return map;
  }, [flows]);

  const statsMap = useMemo(
    () => new Map(flowStats.map((s) => [s.flow_dir, s])),
    [flowStats]
  );

  const kitFilterOptions = useMemo(() => {
    const opts = [{ value: "all", label: "All kits" }];
    for (const k of kits) {
      opts.push({ value: k.name, label: k.name });
    }
    opts.push({ value: "_standalone", label: "Standalone" });
    return opts;
  }, [kits]);

  // Kit names that match the search filter (used to include all their flows)
  const matchedKitNames = useMemo(() => {
    if (!filter) return new Set<string>();
    const lc = filter.toLowerCase();
    return new Set(kits.filter((k) => k.name.toLowerCase().includes(lc)).map((k) => k.name));
  }, [filter, kits]);

  const filtered = useMemo(() => {
    let result = [...flows];

    // Kit filter
    if (kitFilter === "_standalone") {
      result = result.filter((f) => !f.kit_name);
    } else if (kitFilter !== "all") {
      result = result.filter((f) => f.kit_name === kitFilter);
    }

    // Text search (also matches kit names to include all their flows)
    if (filter) {
      result = result.filter((f) =>
        f.name.toLowerCase().includes(filter.toLowerCase()) ||
        (f.kit_name && matchedKitNames.has(f.kit_name))
      );
    }

    if (visibilityFilter === "all") {
      result = result.filter((f) => (f.visibility ?? "interactive") !== "internal");
    } else {
      result = result.filter((f) => (f.visibility ?? "interactive") === visibilityFilter);
    }

    result = filterByTimeRange(result, timeRange);

    // Sort by header column
    result.sort((a, b) => {
      const dir = sortAsc ? 1 : -1;
      const sa = statsMap.get(flowDirKey(a.path));
      const sb = statsMap.get(flowDirKey(b.path));
      switch (sortCol) {
        case "name":
          return dir * a.name.localeCompare(b.name);
        case "steps":
          return dir * (a.steps_count - b.steps_count);
        case "jobs": {
          const diff = (sa?.job_count ?? 0) - (sb?.job_count ?? 0);
          return diff !== 0 ? dir * diff : a.name.localeCompare(b.name);
        }
        case "last_run": {
          const ta = sa?.last_run_at ?? "";
          const tb = sb?.last_run_at ?? "";
          if (ta === tb) return a.name.localeCompare(b.name);
          if (!ta) return 1;
          if (!tb) return -1;
          return dir * ta.localeCompare(tb);
        }
        case "updated": {
          const ma = a.modified_at ?? "";
          const mb = b.modified_at ?? "";
          if (ma === mb) return a.name.localeCompare(b.name);
          if (!ma) return 1;
          if (!mb) return -1;
          return dir * ma.localeCompare(mb);
        }
        default:
          return 0;
      }
    });

    return result;
  }, [flows, filter, sortCol, sortAsc, visibilityFilter, timeRange, statsMap, matchedKitNames, kitFilter]);

  // Group filtered flows by kit
  const { kitGroups, standaloneFlows } = useMemo(() => {
    if (kits.length === 0) return { kitGroups: [] as { kit: Kit; flows: LocalFlow[] }[], standaloneFlows: filtered };

    const kitMap = new Map(kits.map((k) => [k.name, k]));
    const groups = new Map<string, LocalFlow[]>();
    const standalone: LocalFlow[] = [];

    for (const flow of filtered) {
      if (flow.kit_name && kitMap.has(flow.kit_name)) {
        const arr = groups.get(flow.kit_name) ?? [];
        arr.push(flow);
        groups.set(flow.kit_name, arr);
      } else {
        standalone.push(flow);
      }
    }

    return {
      kitGroups: Array.from(groups.entries())
        .map(([name, flows]) => ({ kit: kitMap.get(name)!, flows }))
        .filter((g) => g.flows.length > 0)
        .sort((a, b) => a.kit.name.localeCompare(b.kit.name)),
      standaloneFlows: standalone,
    };
  }, [filtered, kits]);

  const hasKits = kitGroups.length > 0;

  // Ordered flow IDs for shift+click range selection
  const orderedFlowPaths = useMemo(() => filtered.map((f) => f.path), [filtered]);

  const handleSelectAll = useCallback(() => {
    setSelectedIds(new Set(filtered.map((f) => f.path)));
  }, [filtered]);

  const handleToggleSelect = useCallback(
    (flowPath: string, shiftKey: boolean) => {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (shiftKey && lastSelectedRef.current) {
          const startIdx = orderedFlowPaths.indexOf(lastSelectedRef.current);
          const endIdx = orderedFlowPaths.indexOf(flowPath);
          if (startIdx !== -1 && endIdx !== -1) {
            const [lo, hi] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
            for (let i = lo; i <= hi; i++) {
              next.add(orderedFlowPaths[i]);
            }
          } else {
            next.has(flowPath) ? next.delete(flowPath) : next.add(flowPath);
          }
        } else {
          if (next.has(flowPath)) {
            next.delete(flowPath);
          } else {
            next.add(flowPath);
          }
        }
        lastSelectedRef.current = flowPath;
        return next;
      });
    },
    [orderedFlowPaths],
  );

  // Bulk delete
  const [showBulkDeleteConfirm, setShowBulkDeleteConfirm] = useState(false);
  const [isBulkDeleting, setIsBulkDeleting] = useState(false);

  const handleBulkDelete = useCallback(async () => {
    setIsBulkDeleting(true);
    const paths = Array.from(selectedIds);
    try {
      for (const path of paths) {
        await deleteFlowMutation.mutateAsync(path);
      }
      handleClearSelection();
    } finally {
      setIsBulkDeleting(false);
      setShowBulkDeleteConfirm(false);
    }
  }, [selectedIds, deleteFlowMutation, handleClearSelection]);

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
    const localName = localRegistryMap.get(flow.slug) ?? installedSlugs.get(flow.slug);
    if (localName) {
      navigate({ to: "/flows/$flowName", params: { flowName: localName } });
      return;
    }
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
          <div className="flex flex-wrap items-center gap-2 sm:gap-3 px-3 sm:px-4 py-2 border-b border-border shrink-0 bg-white/80 dark:bg-zinc-950/80 backdrop-blur-sm">
            {/* Grid/List toggle */}
            <div className="flex items-center gap-0.5 rounded-lg border border-border p-0.5 bg-zinc-100/50 dark:bg-zinc-900/50">
                  <button
                    onClick={() => setViewMode("list")}
                    className={cn(
                      "flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors",
                      viewMode === "list"
                        ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                        : "text-zinc-500 hover:text-foreground",
                    )}
                  >
                    <List className="w-3.5 h-3.5" />
                    <span className="hidden sm:inline">List</span>
                  </button>
                  <button
                    onClick={() => setViewMode("grid")}
                    className={cn(
                      "flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors",
                      viewMode === "grid"
                        ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                        : "text-zinc-500 hover:text-foreground",
                    )}
                  >
                    <LayoutGrid className="w-3.5 h-3.5" />
                    <span className="hidden sm:inline">Grid</span>
                  </button>
                </div>

                {/* Local/Registry toggle */}
                <div className="flex items-center gap-0.5 rounded-lg border border-border p-0.5 bg-zinc-100/50 dark:bg-zinc-900/50">
                  <button
                    onClick={() => setTab("local")}
                    className={cn(
                      "flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors",
                      tab === "local"
                        ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                        : "text-zinc-500 hover:text-foreground",
                    )}
                  >
                    Local
                  </button>
                  <button
                    onClick={() => setTab("registry")}
                    className={cn(
                      "flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors",
                      tab === "registry"
                        ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                        : "text-zinc-500 hover:text-foreground",
                    )}
                  >
                    Registry
                  </button>
                </div>

            {tab === "local" ? (
              <>
                <div className="relative flex-1 sm:flex-none sm:max-w-sm">
                  <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500" />
                  <Input
                    value={filter}
                    onChange={(e) => setFilter(e.target.value)}
                    placeholder="Search flows..."
                    className="pl-8 h-8 w-full sm:w-40 text-xs bg-background border-border dark:border-input dark:bg-input/30"
                  />
                </div>
                {/* New Flow — next to search on mobile */}
                <div className="sm:order-last sm:ml-auto">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setShowCreateDialog(true)}
                    className="h-8"
                  >
                    <Plus className="w-3.5 h-3.5 sm:mr-1.5" />
                    <span className="hidden sm:inline">New Flow</span>
                  </Button>
                </div>
                <CreateFlowDialog
                  open={showCreateDialog}
                  onOpenChange={setShowCreateDialog}
                  onCreated={handleFlowCreated}
                />
                <ComboBox
                  value={visibilityFilter}
                  onChange={(v) => setVisibilityFilter(v as VisibilityFilter)}
                  options={VISIBILITY_OPTIONS}
                  placeholder="All"
                  searchPlaceholder="Visibility..."
                />
                <ComboBox
                  value={timeRange ?? "all"}
                  onChange={(v) => setTimeRange(v === "all" ? undefined : v as TimeRange)}
                  options={TIME_RANGE_OPTIONS}
                  placeholder="All time"
                  searchPlaceholder="Time range..."
                />
                {kits.length > 0 && (
                  <ComboBox
                    value={kitFilter}
                    onChange={(v) => setKitFilter(v)}
                    options={kitFilterOptions}
                    placeholder="All kits"
                    searchPlaceholder="Kit..."
                  />
                )}
                <span className="text-xs text-zinc-500 whitespace-nowrap">{filtered.length} total</span>
              </>
            ) : (
              <>
                <div className="relative flex-1 max-w-sm">
                  <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500" />
                  <Input
                    value={registryQuery}
                    onChange={(e) => setRegistryQuery(e.target.value)}
                    placeholder="Search registry..."
                    className="pl-8 h-8 text-xs bg-background border-border dark:border-input dark:bg-input/30"
                  />
                </div>
                <div className="flex items-center gap-1 rounded-lg border border-border p-0.5 bg-zinc-100/50 dark:bg-zinc-900/50">
                  <button
                    onClick={() => { setRegSortCol("downloads"); setRegSortAsc(false); setRegistryFilter("popular"); }}
                    className={cn(
                      "px-2.5 py-1 text-xs rounded-md transition-colors",
                      registryFilter === "popular"
                        ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                        : "text-zinc-500 hover:text-foreground",
                    )}
                  >
                    Popular
                  </button>
                  <button
                    onClick={() => { setRegSortCol("downloads"); setRegSortAsc(false); setRegistryFilter("featured"); }}
                    className={cn(
                      "px-2.5 py-1 text-xs rounded-md transition-colors",
                      registryFilter === "featured"
                        ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                        : "text-zinc-500 hover:text-foreground",
                    )}
                  >
                    Featured
                  </button>
                  <button
                    onClick={() => { setRegSortCol("updated"); setRegSortAsc(false); setRegistryFilter("newest"); }}
                    className={cn(
                      "px-2.5 py-1 text-xs rounded-md transition-colors",
                      registryFilter === "newest"
                        ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                        : "text-zinc-500 hover:text-foreground",
                    )}
                  >
                    Newest
                  </button>
                </div>
                <span className="text-xs text-zinc-500">{sortedRegistryFlows.length} total</span>
                <div className="flex-1" />
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
                ) : viewMode === "grid" ? (
                  <div className="p-4 sm:p-6 space-y-6">
                    {kitGroups.map(({ kit, flows: kitFlows }) => (
                      <div key={kit.name}>
                        <KitSectionHeader
                          kit={kit}
                          flowCount={kitFlows.length}
                          expanded={expandedKits.has(kit.name)}
                          onToggle={() => toggleKit(kit.name)}
                          onInfo={() => setKitDetailName(kit.name)}
                        />
                        {expandedKits.has(kit.name) && (
                          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3 mt-2">
                            {kitFlows.map((flow) => (
                              <FlowGridCard key={flow.path} flow={flow} statsMap={statsMap} onSelect={handleSelectLocalFlow} />
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                    {standaloneFlows.length > 0 && (
                      <div>
                        {hasKits && (
                          <div className="flex items-center gap-2 px-1 py-1.5 mb-2">
                            <span className="text-xs font-medium text-zinc-500">Standalone</span>
                            <span className="text-[10px] text-zinc-400">{standaloneFlows.length} flow{standaloneFlows.length !== 1 ? "s" : ""}</span>
                          </div>
                        )}
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                          {standaloneFlows.map((flow) => (
                            <FlowGridCard key={flow.path} flow={flow} statsMap={statsMap} onSelect={handleSelectLocalFlow} />
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="flex-1 overflow-y-auto">
                    <div className="divide-y divide-border">
                      {/* Header row */}
                      <div className="hidden sm:flex items-center px-4 sm:px-6 py-2 gap-3 text-[10px] uppercase tracking-wider text-zinc-500 font-medium select-none">
                        <button
                          onClick={() => {
                            if (selectedIds.size === filtered.length && filtered.length > 0) {
                              handleClearSelection();
                            } else {
                              handleSelectAll();
                            }
                          }}
                          className={cn(
                            "w-4 h-4 rounded border flex items-center justify-center shrink-0 transition-all duration-150",
                            selectedIds.size > 0
                              ? "bg-blue-500 border-blue-500 text-white"
                              : "border-zinc-400 dark:border-zinc-600 hover:border-blue-400 opacity-40 hover:opacity-100",
                          )}
                        >
                          {selectedIds.size > 0 && selectedIds.size === filtered.length
                            ? <Check className="w-2.5 h-2.5" />
                            : selectedIds.size > 0
                              ? <Minus className="w-2.5 h-2.5" />
                              : null}
                        </button>
                        <SortHeader col="name" label="Name" current={sortCol} asc={sortAsc} onSort={handleSort} className="flex-1" />
                        <SortHeader col="steps" label="Steps" current={sortCol} asc={sortAsc} onSort={handleSort} className="w-12 text-right" />
                        <SortHeader col="jobs" label="Jobs" current={sortCol} asc={sortAsc} onSort={handleSort} className="w-14 text-right" />
                        <SortHeader col="last_run" label="Last Run" current={sortCol} asc={sortAsc} onSort={handleSort} className="w-16 text-right" />
                        <SortHeader col="updated" label="Updated" current={sortCol} asc={sortAsc} onSort={handleSort} className="w-16 text-right" />
                      </div>
                      {kitGroups.map(({ kit, flows: kitFlows }) => (
                        <div key={kit.name} className="divide-y divide-border">
                          <KitSectionHeader
                            kit={kit}
                            flowCount={kitFlows.length}
                            expanded={expandedKits.has(kit.name)}
                            onToggle={() => toggleKit(kit.name)}
                            onInfo={() => setKitDetailName(kit.name)}
                          />
                          {expandedKits.has(kit.name) && kitFlows.map((flow) => (
                            <FlowListRow
                              key={flow.path}
                              flow={flow}
                              statsMap={statsMap}
                              selected={selectedIds.has(flow.path)}
                              onSelect={handleSelectLocalFlow}
                              onToggleSelect={handleToggleSelect}
                            />
                          ))}
                        </div>
                      ))}
                      {standaloneFlows.length > 0 && (
                        <div className="divide-y divide-border">
                          {hasKits && (
                            <div className="flex items-center gap-2 px-4 sm:px-6 py-1.5 bg-zinc-50/50 dark:bg-zinc-900/30">
                              <span className="text-xs font-medium text-zinc-500">Standalone</span>
                              <span className="text-[10px] text-zinc-400">{standaloneFlows.length} flow{standaloneFlows.length !== 1 ? "s" : ""}</span>
                            </div>
                          )}
                          {standaloneFlows.map((flow) => (
                            <FlowListRow
                              key={flow.path}
                              flow={flow}
                              statsMap={statsMap}
                              selected={selectedIds.has(flow.path)}
                              onSelect={handleSelectLocalFlow}
                              onToggleSelect={handleToggleSelect}
                            />
                          ))}
                        </div>
                      )}
                    </div>

                    {/* Bulk action bar for flows */}
                    {isSelectionActive && (
                      <>
                        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50">
                          <div className="flex items-center gap-3 px-4 py-2.5 rounded-xl bg-zinc-900/95 backdrop-blur border border-zinc-700 shadow-2xl">
                            <span className="text-sm font-medium text-zinc-200 whitespace-nowrap">
                              {selectedIds.size} selected
                            </span>
                            <div className="w-px h-5 bg-zinc-700" />
                            <button
                              onClick={() => setShowBulkDeleteConfirm(true)}
                              disabled={isBulkDeleting}
                              className={cn(
                                "flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-md transition-colors",
                                "text-red-400 hover:text-red-300 hover:bg-red-950/50",
                                isBulkDeleting && "opacity-50 pointer-events-none",
                              )}
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                              Delete
                            </button>
                            <div className="w-px h-5 bg-zinc-700" />
                            <button
                              onClick={handleClearSelection}
                              className="p-1 rounded hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 transition-colors"
                              title="Deselect all"
                            >
                              <X className="w-4 h-4" />
                            </button>
                          </div>
                        </div>
                        <ConfirmDialog
                          open={showBulkDeleteConfirm}
                          title="Delete flows?"
                          description={`This will permanently delete ${selectedIds.size} flow(s). This cannot be undone.`}
                          confirmLabel="Delete"
                          variant="destructive"
                          onConfirm={handleBulkDelete}
                          onCancel={() => setShowBulkDeleteConfirm(false)}
                        />
                      </>
                    )}
                  </div>
                )}
              </div>
            </ActionContextProvider>
          ) : (
            /* Registry tab */
            <div className="flex-1 overflow-y-auto">
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
                  viewMode === "grid"
                    ? "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3 p-4 sm:p-6"
                    : "divide-y divide-border"
                )}>
                  {viewMode === "list" && (
                    <div className="hidden sm:flex items-center px-4 sm:px-6 py-2 gap-3 text-[10px] uppercase tracking-wider text-zinc-500 font-medium select-none">
                      <SortHeader col="name" label="Name" current={regSortCol} asc={regSortAsc} onSort={handleRegSort} className="flex-1" />
                      <SortHeader col="author" label="Author" current={regSortCol} asc={regSortAsc} onSort={handleRegSort} className="w-20 text-right" />
                      <SortHeader col="steps" label="Steps" current={regSortCol} asc={regSortAsc} onSort={handleRegSort} className="w-12 text-right" />
                      <SortHeader col="downloads" label="Downloads" current={regSortCol} asc={regSortAsc} onSort={handleRegSort} className="w-16 text-right" />
                      <SortHeader col="updated" label="Updated" current={regSortCol} asc={regSortAsc} onSort={handleRegSort} className="w-16 text-right" />
                      <span className="w-[4.5rem] text-right">Status</span>
                    </div>
                  )}
                  {sortedRegistryFlows.map((flow) => {
                    const isInstalled = localRegistryMap.has(flow.slug) || installedSlugs.has(flow.slug);

                    return viewMode === "grid" ? (
                      <button
                        key={flow.slug}
                        onClick={() => handleRegistryFlowClick(flow)}
                        disabled={installMutation.isPending && !isInstalled}
                        className="w-full h-full text-left rounded-lg border border-zinc-200 dark:border-zinc-800 bg-white/80 dark:bg-zinc-900/80 hover:border-zinc-300 dark:hover:border-zinc-700 hover:bg-white dark:hover:bg-zinc-900 transition-all overflow-hidden flex flex-col group"
                      >
                        <div className="px-3 pt-2.5 pb-1 flex items-center gap-2 min-w-0">
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
                        {/* Mini DAG */}
                        {flow.graph && flow.graph.nodes.length > 0 && (
                          <div className="flex justify-center px-2">
                            <MiniFlowDag graph={flow.graph} width={268} height={90} />
                          </div>
                        )}

                        {/* Spacer */}
                        <div className="flex-1" />

                        <div className="px-3 pb-2 pt-0.5 space-y-1">
                        <p className="text-[11px] text-zinc-500 line-clamp-2">
                          {flow.description || "No description"}
                        </p>
                        <div className="flex items-center text-[11px] text-zinc-600 pt-1 border-t border-zinc-100 dark:border-zinc-800">
                          <span className="flex items-center gap-1"><User className="w-3 h-3" />{flow.author}</span>
                          <span>{flow.steps} step{flow.steps !== 1 ? "s" : ""}</span>
                          <span>{flow.downloads} dl{flow.downloads !== 1 ? "s" : ""}</span>
                          {!isInstalled && (
                            <span className="ml-auto flex items-center gap-1 text-blue-500 dark:text-blue-400 font-medium">
                              <Download className="w-3 h-3" />Install
                            </span>
                          )}
                        </div>
                        </div>
                      </button>
                    ) : (
                      <div
                        key={flow.slug}
                        onClick={() => handleRegistryFlowClick(flow)}
                        className={cn(
                          "w-full text-left px-4 sm:px-6 py-3 flex items-center gap-3 transition-colors hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40 group cursor-pointer",
                          installMutation.isPending && !isInstalled && "opacity-50 pointer-events-none",
                        )}
                      >
                        {/* Name + details */}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium text-foreground group-hover:text-blue-500 dark:group-hover:text-blue-400 truncate transition-colors">{flow.name}</span>
                            {flow.featured && (
                              <span className="text-[9px] px-1 py-0.5 rounded bg-amber-100 dark:bg-amber-900/40 text-amber-600 dark:text-amber-400 uppercase tracking-wider shrink-0">
                                Featured
                              </span>
                            )}
                          </div>
                          {flow.description && (
                            <p className="text-xs text-zinc-500 dark:text-zinc-500 truncate mt-0.5">{flow.description}</p>
                          )}
                          {/* Mobile meta row */}
                          <div className="flex items-center gap-2 mt-1 sm:hidden text-[10px] text-zinc-500 flex-wrap">
                            <span>{flow.author}</span>
                            <span>{flow.steps} step{flow.steps !== 1 ? "s" : ""}</span>
                            <span>{flow.downloads} dl{flow.downloads !== 1 ? "s" : ""}</span>
                            {flow.updated_at && <span>{formatRelativeTime(flow.updated_at)}</span>}
                            {isInstalled ? (
                              <Badge variant="outline" className="text-[10px] px-1.5 py-0 bg-green-500/10 text-green-600 dark:text-green-400 ring-1 ring-green-500/30 border-transparent">
                                <Check className="w-2.5 h-2.5 mr-0.5" />Installed
                              </Badge>
                            ) : (
                              <span className="text-blue-400 font-medium flex items-center gap-0.5">
                                <Download className="w-2.5 h-2.5" />Install
                              </span>
                            )}
                          </div>
                        </div>

                        {/* Right columns */}
                        <div className="hidden sm:flex items-center gap-3 shrink-0 text-[11px] text-zinc-500 dark:text-zinc-500 tabular-nums">
                          <span className="w-20 text-right">{flow.author}</span>
                          <span className="w-12 text-right">{flow.steps} step{flow.steps !== 1 ? "s" : ""}</span>
                          <span className="w-16 text-right">{flow.downloads}</span>
                          <span className="w-16 text-right text-zinc-500">{flow.updated_at ? formatRelativeTime(flow.updated_at) : ""}</span>
                          <span className="w-[4.5rem] text-right">
                            {isInstalled ? (
                              <Badge variant="outline" className="text-[10px] px-1.5 py-0 bg-green-500/10 text-green-600 dark:text-green-400 ring-1 ring-green-500/30 border-transparent">
                                <Check className="w-2.5 h-2.5 mr-0.5" />Installed
                              </Badge>
                            ) : (
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-6 px-2 text-[10px] text-blue-500 dark:text-blue-400 border-blue-300 dark:border-blue-700 hover:bg-blue-50 dark:hover:bg-blue-950/50"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleRegistryFlowClick(flow);
                                }}
                              >
                                <Download className="w-3 h-3 mr-1" />Install
                              </Button>
                            )}
                          </span>
                        </div>
                      </div>
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

      {/* Kit Detail Sheet */}
      <Sheet open={!!kitDetailName} onOpenChange={(open) => { if (!open) setKitDetailName(null); }}>
        <SheetContent side="right" className="w-full sm:max-w-md overflow-y-auto">
          {kitDetailData && (
            <>
              <SheetHeader>
                <SheetTitle className="flex items-center gap-2">
                  <Package className="w-4 h-4" />
                  {kitDetailData.name}
                </SheetTitle>
              </SheetHeader>
              <div className="mt-4 space-y-4">
                <p className="text-sm text-muted-foreground">{kitDetailData.description}</p>

                <div className="flex flex-wrap gap-2">
                  {kitDetailData.author && (
                    <Badge variant="outline" className="text-xs">
                      <User className="w-3 h-3 mr-1" />
                      {kitDetailData.author}
                    </Badge>
                  )}
                  {kitDetailData.category && (
                    <Badge variant="outline" className="text-xs">{kitDetailData.category}</Badge>
                  )}
                  <Badge variant="secondary" className="text-xs">
                    {kitDetailData.flow_count} flow{kitDetailData.flow_count !== 1 ? "s" : ""}
                  </Badge>
                  {kitDetailData.tags.map((tag) => (
                    <Badge key={tag} variant="outline" className="text-[10px]">{tag}</Badge>
                  ))}
                </div>

                {kitDetailData.usage && (
                  <div className="pt-2 border-t border-border">
                    <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Usage</h4>
                    <div className="text-sm text-foreground whitespace-pre-wrap leading-relaxed bg-zinc-50 dark:bg-zinc-900/50 rounded-md p-3 border border-border">
                      {kitDetailData.usage}
                    </div>
                  </div>
                )}

                <div className="pt-2 border-t border-border">
                  <h4 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-2">Flows</h4>
                  <div className="space-y-1">
                    {kitDetailData.flow_names.map((name) => (
                      <button
                        key={name}
                        onClick={() => {
                          setKitDetailName(null);
                          navigate({ to: "/flows/$flowName", params: { flowName: name } });
                        }}
                        className="w-full text-left px-2 py-1.5 text-sm rounded hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors text-foreground"
                      >
                        {name}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
