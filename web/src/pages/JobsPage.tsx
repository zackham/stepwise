import { useState, useMemo, useCallback, useRef, memo } from "react";
import { useSearch, useNavigate } from "@tanstack/react-router";
import { CanvasPage } from "./CanvasPage";
import {
  ChevronRight,
  List,
  LayoutGrid,
  Search,
  Terminal,
  Monitor,
  AlertTriangle,
  CirclePause,
  ArrowUpDown,
  Check,
  Minus,
} from "lucide-react";
import { cn, formatDuration, formatCost } from "@/lib/utils";
import { useJobs } from "@/hooks/useStepwise";
import { useLocalFlows } from "@/hooks/useEditor";
import { useQueries } from "@tanstack/react-query";
import { fetchJobCost } from "@/lib/api";
import { JobStatusBadge } from "@/components/StatusBadge";
import { LiveDuration } from "@/components/LiveDuration";
import { EntityContextMenu } from "@/components/menus/EntityContextMenu";
import { ActionContextProvider } from "@/components/menus/ActionContextProvider";
import { CreateJobDialog } from "@/components/jobs/CreateJobDialog";
import { Input } from "@/components/ui/input";
import { ComboBox } from "@/components/ui/ComboBox";
import { BulkActionBar } from "@/components/canvas/BulkActionBar";
import type { Job, JobStatus } from "@/lib/types";
import { JOB_STATUS_COLORS } from "@/lib/status-colors";

// ── Helpers ────────────────────────────────────────────────────────────

function isStale(job: Job): boolean {
  if (job.status !== "running" || job.created_by === "server") return false;
  if (!job.heartbeat_at) return true;
  return Date.now() - new Date(job.heartbeat_at).getTime() > 60_000;
}

function isCliOwned(created_by: string): boolean {
  return created_by.startsWith("cli:");
}

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

const STATUS_ORDER: Record<string, number> = {
  running: 0,
  awaiting_input: 1,
  awaiting_approval: 1,
  paused: 2,
  pending: 3,
  staged: 4,
  completed: 5,
  failed: 6,
  cancelled: 7,
  archived: 8,
};

const ACTIVE_STATUSES = new Set(["running", "paused", "pending", "staged", "awaiting_input", "awaiting_approval"]);
const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled", "archived"]);

const DISPLAY_ORDER: JobStatus[] = [
  "running",
  "paused",
  "pending",
  "staged",
  "awaiting_approval",
  "completed",
  "failed",
  "cancelled",
  "archived",
];

type TimeRange = "today" | "7d" | "30d" | undefined;

const TIME_RANGE_LABELS: Record<string, string> = {
  all: "All time",
  today: "Today",
  "7d": "7 days",
  "30d": "30 days",
};

// ── Filter logic ───────────────────────────────────────────────────────

function filterByTimeRange(jobs: Job[], range: TimeRange): Job[] {
  if (!range) return jobs;
  const now = Date.now();
  const cutoff =
    range === "today"
      ? now - 24 * 60 * 60 * 1000
      : range === "7d"
        ? now - 7 * 24 * 60 * 60 * 1000
        : now - 30 * 24 * 60 * 60 * 1000;
  return jobs.filter((j) => new Date(j.updated_at).getTime() >= cutoff);
}

function filterBySearch(jobs: Job[], q: string): Job[] {
  if (!q) return jobs;
  const lower = q.toLowerCase();
  return jobs.filter(
    (j) =>
      (j.name || "").toLowerCase().includes(lower) ||
      (j.objective || "").toLowerCase().includes(lower),
  );
}

function filterByStatuses(jobs: Job[], statuses: Set<string>): Job[] {
  if (statuses.size === 0) return jobs;
  return jobs.filter((j) => statuses.has(j.status));
}

function sortJobs(jobs: Job[]): Job[] {
  return [...jobs].sort((a, b) => {
    const pa = STATUS_ORDER[a.status] ?? 99;
    const pb = STATUS_ORDER[b.status] ?? 99;
    if (pa !== pb) return pa - pb;
    return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
  });
}

// ── Step progress helper ───────────────────────────────────────────────

function getStepProgress(job: Job): { completed: number; total: number } {
  const steps = job.workflow?.steps ?? {};
  const total = Object.keys(steps).length;
  // current_step tells us the latest step — we infer completed from status
  // For a more accurate count, check if the job has completed/failed steps
  // by looking at the step statuses from the current_step info
  if (job.status === "completed") return { completed: total, total };
  if (job.status === "staged" || job.status === "pending") return { completed: 0, total };
  // For running jobs, estimate from current step position
  const stepNames = Object.keys(steps);
  if (job.current_step) {
    const idx = stepNames.indexOf(job.current_step.name);
    if (idx !== -1) {
      const completed = job.current_step.status === "completed" ? idx + 1 : idx;
      return { completed: Math.min(completed, total), total };
    }
  }
  return { completed: 0, total };
}

// ── Status Filter Pills ────────────────────────────────────────────────

function StatusFilterPills({
  jobs,
  activeStatuses,
  onToggle,
}: {
  jobs: Job[];
  activeStatuses: Set<string>;
  onToggle: (status: string) => void;
}) {
  const counts = useMemo(() => {
    const map: Partial<Record<JobStatus, number>> = {};
    for (const job of jobs) {
      map[job.status] = (map[job.status] ?? 0) + 1;
    }
    return map;
  }, [jobs]);

  const visibleStatuses = DISPLAY_ORDER.filter((s) => counts[s]);

  if (visibleStatuses.length === 0) return null;

  return (
    <div className="flex items-center gap-1">
      {visibleStatuses.map((status) => {
        const isActive = activeStatuses.has(status);
        const colors = JOB_STATUS_COLORS[status];
        return (
          <button
            key={status}
            onClick={() => onToggle(status)}
            className={cn(
              "flex items-center gap-1.5 h-8 px-2.5 text-xs rounded-md border transition-colors",
              isActive
                ? `${colors.bg} ${colors.text} border-current/20`
                : "bg-transparent border-transparent text-foreground/70 hover:text-foreground hover:bg-zinc-800/30",
            )}
          >
            <span
              className={cn(
                "w-1.5 h-1.5 rounded-full",
                colors.dot,
              )}
            />
            {counts[status]} {status === "completed" ? "done" : status}
          </button>
        );
      })}
    </div>
  );
}

// ── Summary Bar (inline) ───────────────────────────────────────────────

// ── Time Range Dropdown ────────────────────────────────────────────────

const TIME_RANGE_OPTIONS = [
  { value: "all", label: "All time" },
  { value: "today", label: "Today" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
];

type SortCol = "name" | "deps" | "steps" | "cost" | "duration" | "status" | "time";

function SortHeader({ col, label, current, asc, onSort, className }: {
  col: SortCol;
  label: string;
  current: SortCol | null;
  asc: boolean;
  onSort: (col: SortCol) => void;
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

// ── Memoized Job Row ──────────────────────────────────────────────────

const JobListRow = memo(function JobListRow({
  job,
  cost,
  depNames,
  selected,
  isHighlighted,
  isSelectionActive,
  onToggleSelect,
  onHover,
}: {
  job: Job;
  cost?: number;
  depNames?: string[];
  selected: boolean;
  isHighlighted: boolean;
  isSelectionActive: boolean;
  onToggleSelect: (jobId: string, shiftKey: boolean) => void;
  onHover: (jobId: string | null) => void;
}) {
  const navigate = useNavigate();
  const progress = getStepProgress(job);
  const flowName = job.workflow?.metadata?.name ?? null;
  const hasDeps = (depNames?.length ?? 0) > 0;

  return (
    <EntityContextMenu type="job" data={job}>
      <div
        data-job-row={job.id}
        onClick={(e) => {
          // Let checkbox and flow link handle their own clicks
          if ((e.target as HTMLElement).closest("[data-job-link]")) return;
          if ((e.target as HTMLElement).closest("[data-job-checkbox]")) return;
          navigate({ to: "/jobs/$jobId", params: { jobId: job.id } });
        }}
        onMouseEnter={() => onHover(job.id)}
        onMouseLeave={() => onHover(null)}
        className={cn(
          "w-full text-left px-4 sm:px-6 py-3 flex items-center gap-3 transition-none hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40 group cursor-pointer",
          selected && "bg-blue-50/50 dark:bg-blue-950/20",
          isHighlighted && "bg-blue-950/20 border-l-2 border-l-blue-500",
        )}
      >
        {/* Checkbox — subtle, visible on hover or when selected */}
        <button
          data-job-checkbox
          onClick={(e) => {
            e.stopPropagation();
            onToggleSelect(job.id, e.shiftKey);
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

        {/* Name + details */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-foreground truncate">
              {job.name || job.objective || "Untitled Job"}
            </span>
            {isStale(job) && (
              <AlertTriangle className="w-3 h-3 text-amber-500 shrink-0" />
            )}
            {job.has_suspended_steps && job.status !== "cancelled" && job.status !== "completed" && job.status !== "failed" && (
              <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/30 shrink-0">
                <CirclePause className="w-2.5 h-2.5" />
              </span>
            )}
          </div>
          <div className="text-xs text-zinc-500 truncate mt-0.5">
            {flowName && (
              <a
                data-job-link
                onClick={(e) => {
                  e.stopPropagation();
                  navigate({ to: "/flows/$flowName", params: { flowName } });
                }}
                className="text-zinc-600 hover:text-blue-400 cursor-pointer transition-colors"
              >{flowName}</a>
            )}
            {flowName && job.current_step && <span className="text-zinc-700"> · </span>}
            {job.current_step && (
              <span className={cn(
                job.current_step.status === "running" && "text-blue-400",
                job.current_step.status === "failed" && "text-red-400",
              )}>
                {job.current_step.name}
                {job.current_step.started_at && (
                  <span className="text-zinc-600">
                    {" · "}
                    <LiveDuration
                      startTime={job.current_step.started_at}
                      endTime={job.current_step.completed_at ?? null}
                    />
                  </span>
                )}
              </span>
            )}
          </div>
          {/* Mobile meta row — shows all info hidden on desktop */}
          <div className="flex items-center gap-2 mt-1 sm:hidden text-[10px] text-zinc-500 flex-wrap">
            <JobStatusBadge status={job.status} />
            <span>{progress.completed}/{progress.total} steps</span>
            {cost != null && cost > 0 && (
              <span>{formatCost(cost)}</span>
            )}
            <span>
              {job.status === "staged" || job.status === "pending"
                ? "—"
                : formatDuration(job.created_at, job.updated_at)}
            </span>
            <span>{timeAgo(job.updated_at)}</span>
            {hasDeps && depNames && (
              <span className="truncate max-w-[120px]" title={depNames.join(", ")}>
                dep: {depNames.join(", ")}
              </span>
            )}
          </div>
        </div>

        {/* Dependencies column */}
        <span className="hidden sm:inline-block w-40 shrink-0 text-right text-[11px] text-zinc-500 truncate">
          {isHighlighted ? (
            <span className="inline-flex items-center px-1.5 py-0.5 rounded-full bg-blue-500 text-white text-[9px] font-medium">
              dependency
            </span>
          ) : hasDeps && depNames ? (
            depNames.join(", ")
          ) : null}
        </span>

        {/* Right columns */}
        <div className="hidden sm:flex items-center gap-4 shrink-0 text-[11px] text-zinc-500 tabular-nums">
          <span className="w-14 text-right">
            {progress.completed}/{progress.total} steps
          </span>
          <span className="w-16 text-right">
            {cost != null && cost > 0 ? formatCost(cost) : ""}
          </span>
          <span className="w-16 text-right">
            {job.status === "staged" || job.status === "pending"
              ? "—"
              : formatDuration(job.created_at, job.updated_at)}
          </span>
          <span className="w-20 text-right">
            <JobStatusBadge status={job.status} />
          </span>
          <span className="w-14 text-right">{timeAgo(job.updated_at)}</span>
        </div>
      </div>
    </EntityContextMenu>
  );
});

// ── Job List View ──────────────────────────────────────────────────────

function JobListView({ jobs }: { jobs: Job[] }) {
  const navigate = useNavigate();

  // Sort state
  const [sortCol, setSortCol] = useState<SortCol | null>(null);
  const [sortAsc, setSortAsc] = useState(false);

  // Group expand state — tracks which collapsed groups user has manually expanded
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const toggleGroup = useCallback((group: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  }, []);

  const handleSort = useCallback((col: SortCol) => {
    if (sortCol === col) {
      setSortAsc((a) => !a);
    } else {
      setSortCol(col);
      setSortAsc(col === "name"); // name defaults asc, everything else desc
    }
  }, [sortCol]);

  // Selection state — local to the list view
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const lastSelectedRef = useRef<string | null>(null);

  const isSelectionActive = selectedIds.size > 0;

  const handleClearSelection = useCallback(() => {
    setSelectedIds(new Set());
    lastSelectedRef.current = null;
  }, []);

  const handleSelectAll = useCallback(() => {
    setSelectedIds(new Set(jobs.map((j) => j.id)));
  }, [jobs]);

  // Build ordered job ID list for shift+click range selection
  const orderedJobIds = useMemo(() => jobs.map((j) => j.id), [jobs]);

  const handleToggleSelect = useCallback(
    (jobId: string, shiftKey: boolean) => {
      setSelectedIds((prev) => {
        const next = new Set(prev);
        if (shiftKey && lastSelectedRef.current) {
          const startIdx = orderedJobIds.indexOf(lastSelectedRef.current);
          const endIdx = orderedJobIds.indexOf(jobId);
          if (startIdx !== -1 && endIdx !== -1) {
            const [lo, hi] = startIdx < endIdx ? [startIdx, endIdx] : [endIdx, startIdx];
            for (let i = lo; i <= hi; i++) {
              next.add(orderedJobIds[i]);
            }
          } else {
            next.has(jobId) ? next.delete(jobId) : next.add(jobId);
          }
        } else {
          if (next.has(jobId)) {
            next.delete(jobId);
          } else {
            next.add(jobId);
          }
        }
        lastSelectedRef.current = jobId;
        return next;
      });
    },
    [orderedJobIds],
  );

  // Dependency maps + hover state
  const [hoveredJobId, setHoveredJobId] = useState<string | null>(null);

  const { dependsOnMap, jobNameMap } = useMemo(() => {
    const depsOn = new Map<string, Set<string>>();
    const names = new Map<string, string>();
    for (const job of jobs) {
      names.set(job.id, job.name || job.objective || job.id.slice(0, 8));
      if (job.depends_on && job.depends_on.length > 0) {
        depsOn.set(job.id, new Set(job.depends_on));
      }
    }
    return { dependsOnMap: depsOn, jobNameMap: names };
  }, [jobs]);

  const highlightedDeps = useMemo(() => {
    if (!hoveredJobId) return new Set<string>();
    return dependsOnMap.get(hoveredJobId) ?? new Set<string>();
  }, [hoveredJobId, dependsOnMap]);

  // Fetch costs for all visible jobs
  const costQueries = useQueries({
    queries: jobs.map((job) => ({
      queryKey: ["jobCost", job.id],
      queryFn: () => fetchJobCost(job.id),
      staleTime: 30_000,
      enabled: job.status !== "staged" && job.status !== "pending",
    })),
  });

  const costMap = useMemo(() => {
    const map = new Map<string, number>();
    jobs.forEach((job, i) => {
      const data = costQueries[i]?.data;
      if (data?.cost_usd != null) map.set(job.id, data.cost_usd);
    });
    return map;
  }, [jobs, costQueries]);

  // Sort and separate active from terminal jobs
  const { activeJobs, terminalJobs } = useMemo(() => {
    let sorted: Job[];

    if (sortCol === "deps") {
      // Topological sort: involved jobs (with deps or are depended on) first, parents before children
      const jobIds = new Set(jobs.map((j) => j.id));
      const involved = new Set<string>();
      for (const job of jobs) {
        if (job.depends_on?.some((d) => jobIds.has(d))) {
          involved.add(job.id);
          for (const d of job.depends_on!) if (jobIds.has(d)) involved.add(d);
        }
      }
      // Topo sort involved jobs
      const depCount = new Map<string, number>();
      const childrenMap = new Map<string, string[]>();
      for (const id of involved) depCount.set(id, 0);
      for (const job of jobs) {
        if (!involved.has(job.id)) continue;
        for (const depId of job.depends_on ?? []) {
          if (involved.has(depId)) {
            depCount.set(job.id, (depCount.get(job.id) ?? 0) + 1);
            if (!childrenMap.has(depId)) childrenMap.set(depId, []);
            childrenMap.get(depId)!.push(job.id);
          }
        }
      }
      const queue = jobs.filter((j) => involved.has(j.id) && (depCount.get(j.id) ?? 0) === 0);
      queue.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
      const topoResult: Job[] = [];
      const jobMap = new Map(jobs.map((j) => [j.id, j]));
      const visited = new Set<string>();
      let qi = 0;
      while (qi < queue.length) {
        const job = queue[qi++];
        if (visited.has(job.id)) continue;
        visited.add(job.id);
        topoResult.push(job);
        for (const cid of childrenMap.get(job.id) ?? []) {
          const c = (depCount.get(cid) ?? 1) - 1;
          depCount.set(cid, c);
          if (c === 0) { const ch = jobMap.get(cid); if (ch) queue.push(ch); }
        }
      }
      // Uninvolved jobs after, sorted by updated_at desc
      const rest = jobs.filter((j) => !involved.has(j.id))
        .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
      sorted = [...topoResult, ...rest];
    } else if (sortCol) {
      // Column sort
      sorted = [...jobs].sort((a, b) => {
        const dir = sortAsc ? 1 : -1;
        switch (sortCol) {
          case "name": return dir * (a.name || a.objective || "").localeCompare(b.name || b.objective || "");
          case "steps": return dir * (Object.keys(a.workflow?.steps ?? {}).length - Object.keys(b.workflow?.steps ?? {}).length);
          case "cost": return dir * ((costMap.get(a.id) ?? 0) - (costMap.get(b.id) ?? 0));
          case "duration": {
            const da = new Date(a.updated_at).getTime() - new Date(a.created_at).getTime();
            const db = new Date(b.updated_at).getTime() - new Date(b.created_at).getTime();
            return dir * (da - db);
          }
          case "status": return dir * (a.status.localeCompare(b.status));
          case "time": return dir * (new Date(a.updated_at).getTime() - new Date(b.updated_at).getTime());
          default: return 0;
        }
      });
    } else {
      // Default: topological sort (parents before children), then by updated_at desc
      const jobIds = new Set(jobs.map((j) => j.id));
      const depCount = new Map<string, number>();
      const children = new Map<string, string[]>();
      for (const job of jobs) depCount.set(job.id, 0);
      for (const job of jobs) {
        for (const depId of job.depends_on ?? []) {
          if (jobIds.has(depId)) {
            depCount.set(job.id, (depCount.get(job.id) ?? 0) + 1);
            if (!children.has(depId)) children.set(depId, []);
            children.get(depId)!.push(job.id);
          }
        }
      }
      const queue = jobs.filter((j) => (depCount.get(j.id) ?? 0) === 0);
      queue.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
      sorted = [];
      const jobMap = new Map(jobs.map((j) => [j.id, j]));
      const visited = new Set<string>();
      let i = 0;
      while (i < queue.length) {
        const job = queue[i++];
        if (visited.has(job.id)) continue;
        visited.add(job.id);
        sorted.push(job);
        const childIds = children.get(job.id) ?? [];
        const readyChildren: Job[] = [];
        for (const childId of childIds) {
          const c = (depCount.get(childId) ?? 1) - 1;
          depCount.set(childId, c);
          if (c === 0) {
            const child = jobMap.get(childId);
            if (child) readyChildren.push(child);
          }
        }
        readyChildren.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
        queue.push(...readyChildren);
      }
      for (const job of jobs) {
        if (!visited.has(job.id)) sorted.push(job);
      }
    }
    const active: Job[] = [];
    const terminal: Job[] = [];
    for (const job of sorted) {
      if (ACTIVE_STATUSES.has(job.status)) {
        active.push(job);
      } else {
        terminal.push(job);
      }
    }
    return { activeJobs: active, terminalJobs: terminal };
  }, [jobs, sortCol, sortAsc, costMap]);

  if (jobs.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-xs text-zinc-500">
        No matching jobs
      </div>
    );
  }

  const isOlderThan24h = (job: Job) =>
    Date.now() - new Date(job.updated_at).getTime() > 24 * 60 * 60 * 1000;

  const renderRow = (job: Job) => (
    <JobListRow
      key={job.id}
      job={job}
      cost={costMap.get(job.id)}
      depNames={job.depends_on?.map((id) => jobNameMap.get(id)).filter(Boolean) as string[] | undefined}
      selected={selectedIds.has(job.id)}
      isHighlighted={highlightedDeps.has(job.id)}
      isSelectionActive={isSelectionActive}
      onToggleSelect={handleToggleSelect}
      onHover={setHoveredJobId}
    />
  );

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="divide-y divide-border">
        {/* Header row */}
        <div className="hidden sm:flex items-center px-4 sm:px-6 py-2 gap-3 text-[10px] uppercase tracking-wider text-zinc-500 font-medium select-none">
          <button
            onClick={() => {
              const allJobs = [...activeJobs, ...terminalJobs];
              if (selectedIds.size === allJobs.length && allJobs.length > 0) {
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
            {selectedIds.size > 0 && selectedIds.size === [...activeJobs, ...terminalJobs].length
              ? <Check className="w-2.5 h-2.5" />
              : selectedIds.size > 0
                ? <Minus className="w-2.5 h-2.5" />
                : null}
          </button>
          <SortHeader col="name" label="Name" current={sortCol} asc={sortAsc} onSort={handleSort} className="flex-1" />
          <SortHeader col="deps" label="Dependencies" current={sortCol} asc={sortAsc} onSort={handleSort} className="w-40 text-right" />
          <SortHeader col="steps" label="Steps" current={sortCol} asc={sortAsc} onSort={handleSort} className="w-14 text-right" />
          <SortHeader col="cost" label="Cost" current={sortCol} asc={sortAsc} onSort={handleSort} className="w-16 text-right" />
          <SortHeader col="duration" label="Duration" current={sortCol} asc={sortAsc} onSort={handleSort} className="w-16 text-right" />
          <SortHeader col="status" label="Status" current={sortCol} asc={sortAsc} onSort={handleSort} className="w-20 text-right" />
          <SortHeader col="time" label="Updated" current={sortCol} asc={sortAsc} onSort={handleSort} className="w-14 text-right" />
        </div>
        {(() => {
          // Build unified interleaved list: each item is a group or a standalone job
          const allSorted = [...activeJobs, ...terminalJobs];
          const groups = new Map<string, Job[]>();
          const ungrouped: Job[] = [];
          for (const job of allSorted) {
            if (job.job_group) {
              if (!groups.has(job.job_group)) groups.set(job.job_group, []);
              groups.get(job.job_group)!.push(job);
            } else {
              ungrouped.push(job);
            }
          }

          // Build interleaved list sorted by most recent updated_at
          type ListItem = { type: "group"; name: string; jobs: Job[]; sortKey: number } | { type: "job"; job: Job; sortKey: number };
          const items: ListItem[] = [];

          for (const [groupName, groupJobs] of groups.entries()) {
            const sortKey = Math.max(...groupJobs.map((j) => new Date(j.updated_at).getTime()));
            items.push({ type: "group", name: groupName, jobs: groupJobs, sortKey });
          }
          for (const job of ungrouped) {
            items.push({ type: "job", job, sortKey: new Date(job.updated_at).getTime() });
          }

          // Sort descending by recency (or by name if name sort active)
          if (sortCol === "name") {
            const dir = sortAsc ? 1 : -1;
            items.sort((a, b) => {
              const aName = a.type === "group" ? a.name : (a.job.name || a.job.objective || "");
              const bName = b.type === "group" ? b.name : (b.job.name || b.job.objective || "");
              return dir * aName.localeCompare(bName);
            });
          } else {
            items.sort((a, b) => b.sortKey - a.sortKey);
          }

          return (
            <>
              {items.map((item) => {
                if (item.type === "job") {
                  return renderRow(item.job);
                }
                // Group rendering
                const groupJobs = item.jobs;
                const groupName = item.name;
                const isTerminal = groupJobs.every((j) => TERMINAL_STATUSES.has(j.status));
                const isExpanded = isTerminal ? expandedGroups.has(groupName) : !expandedGroups.has(groupName);

                // Rolled-up status
                const hasRunning = groupJobs.some((j) => j.status === "running");
                const hasFailed = groupJobs.some((j) => j.status === "failed");
                const allCompleted = groupJobs.every((j) => j.status === "completed");
                const rolledStatus: JobStatus = hasRunning ? "running" : hasFailed ? "failed" : allCompleted ? "completed" : "completed";

                // Rolled-up duration (total)
                const totalDurationMs = groupJobs.reduce((sum, j) => {
                  if (j.status === "staged" || j.status === "pending") return sum;
                  return sum + (new Date(j.updated_at).getTime() - new Date(j.created_at).getTime());
                }, 0);
                const durationStr = totalDurationMs < 1000 ? "—" : totalDurationMs < 60000 ? `${(totalDurationMs / 1000).toFixed(1)}s` : totalDurationMs < 3600000 ? `${(totalDurationMs / 60000).toFixed(1)}m` : `${(totalDurationMs / 3600000).toFixed(1)}h`;

                // Jobs summary
                const completedCount = groupJobs.filter((j) => TERMINAL_STATUSES.has(j.status)).length;
                const jobsSummary = `${completedCount} of ${groupJobs.length} done`;

                // Most recent updated_at
                const mostRecent = groupJobs.reduce((latest, j) => j.updated_at > latest ? j.updated_at : latest, groupJobs[0].updated_at);

                return (
                  <div key={`group-${groupName}`}>
                    <button
                      onClick={() => toggleGroup(groupName)}
                      className="w-full flex items-center gap-3 px-4 sm:px-6 py-3 bg-zinc-50/30 dark:bg-zinc-900/20 hover:bg-zinc-100/50 dark:hover:bg-zinc-800/30 transition-colors text-left"
                    >
                      {/* Checkbox placeholder for alignment */}
                      <span className="w-4 shrink-0" />
                      <ChevronRight
                        className={cn(
                          "w-3.5 h-3.5 text-zinc-400 transition-transform shrink-0",
                          isExpanded && "rotate-90",
                        )}
                      />
                      {/* Name */}
                      <div className="flex-1 min-w-0">
                        <span className="text-sm font-medium text-zinc-400 dark:text-zinc-400 truncate">{groupName}</span>
                      </div>
                      {/* Right columns — match job row layout */}
                      <div className="hidden sm:flex items-center gap-4 shrink-0 text-[11px] text-zinc-500 tabular-nums">
                        <span className="w-14 text-right">{jobsSummary}</span>
                        <span className="w-16 text-right"></span>
                        <span className="w-16 text-right">{durationStr}</span>
                        <span className="w-20 text-right">
                          <JobStatusBadge status={rolledStatus} />
                        </span>
                        <span className="w-14 text-right">{timeAgo(mostRecent)}</span>
                      </div>
                      {/* Mobile meta */}
                      <div className="flex items-center gap-2 sm:hidden text-[10px] text-zinc-500">
                        <JobStatusBadge status={rolledStatus} />
                        <span>{jobsSummary}</span>
                      </div>
                    </button>
                    {isExpanded && groupJobs.map(renderRow)}
                  </div>
                );
              })}
            </>
          );
        })()}
      </div>

      {/* Bulk action bar */}
      <BulkActionBar
        selectedIds={selectedIds}
        jobs={jobs}
        onClearSelection={handleClearSelection}
      />
    </div>
  );
}

// ── Main JobsPage ──────────────────────────────────────────────────────

export function JobsPage() {
  const searchParams = useSearch({ from: "/jobs" });
  const navigate = useNavigate();

  const viewMode = searchParams.view_mode ?? "list";
  const searchQuery = searchParams.q ?? "";
  const timeRange = searchParams.range as TimeRange;

  // Parse active status filters from comma-separated URL param
  const activeStatuses = useMemo(() => {
    if (!searchParams.status) return new Set<string>();
    return new Set(searchParams.status.split(","));
  }, [searchParams.status]);

  const [showArchived, setShowArchived] = useState(false);
  const { data: jobsResponse, isLoading } = useJobs(undefined, true, showArchived);
  const allJobs = jobsResponse?.jobs ?? [];
  const totalJobCount = jobsResponse?.total ?? allJobs.length;
  const { data: flows = [] } = useLocalFlows();
  const [flowFilter, setFlowFilter] = useState("all");

  const flowOptions = useMemo(() => {
    const stats = new Map<string, { count: number; lastRun: string }>();
    for (const job of allJobs) {
      const name = job.workflow?.metadata?.name;
      if (!name) continue;
      const existing = stats.get(name);
      if (!existing) {
        stats.set(name, { count: 1, lastRun: job.updated_at });
      } else {
        existing.count++;
        if (job.updated_at > existing.lastRun) existing.lastRun = job.updated_at;
      }
    }
    return [
      { value: "all", label: "All flows", sublabel: `${stats.size} flows` },
      ...Array.from(stats.entries())
        .sort(([, a], [, b]) => b.lastRun.localeCompare(a.lastRun))
        .map(([name, s]) => ({
          value: name,
          label: name,
          sublabel: `${s.count} job${s.count !== 1 ? "s" : ""} · last ${timeAgo(s.lastRun)}`,
          sortKey: s.lastRun,
        })),
    ];
  }, [allJobs]);

  // URL-synced setters
  const updateSearch = useCallback(
    (updates: Record<string, unknown>) => {
      navigate({
        search: ((prev: Record<string, unknown>) => {
          const next = { ...prev, ...updates };
          // Remove undefined/empty values to keep URL clean
          for (const key of Object.keys(next)) {
            if (next[key] === undefined || next[key] === "" || next[key] === null) {
              delete next[key];
            }
          }
          return next;
        }) as never,
        replace: true,
      });
    },
    [navigate],
  );

  const setViewMode = useCallback(
    (mode: "list" | "grid") => {
      updateSearch({ view_mode: mode === "list" ? undefined : mode });
    },
    [updateSearch],
  );

  const setSearchQuery = useCallback(
    (q: string) => {
      updateSearch({ q: q || undefined });
    },
    [updateSearch],
  );

  const setTimeRange = useCallback(
    (range: TimeRange) => {
      updateSearch({ range });
    },
    [updateSearch],
  );

  const toggleStatus = useCallback(
    (status: string) => {
      const next = new Set(activeStatuses);
      if (next.has(status)) {
        next.delete(status);
      } else {
        next.add(status);
      }
      updateSearch({
        status: next.size > 0 ? Array.from(next).join(",") : undefined,
      });
    },
    [activeStatuses, updateSearch],
  );

  // Filtering pipeline — summary uses unfiltered, views use filtered
  const filteredJobs = useMemo(() => {
    let result = allJobs;
    result = filterByTimeRange(result, timeRange);
    result = filterBySearch(result, searchQuery);
    result = filterByStatuses(result, activeStatuses);
    if (flowFilter !== "all") {
      result = result.filter((j) => j.workflow?.metadata?.name === flowFilter);
    }
    result = sortJobs(result);
    return result;
  }, [allJobs, timeRange, searchQuery, activeStatuses, flowFilter]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500 text-sm">
        Loading jobs...
      </div>
    );
  }

  if (allJobs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full px-4 max-w-sm mx-auto text-center">
        <img
          src="/stepwise-icon-64.png"
          alt="Stepwise"
          className="w-12 h-12 opacity-40 mb-3"
        />
        <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400 mb-1">
          No jobs yet
        </p>
        <p className="text-xs text-zinc-500 dark:text-zinc-600 mb-4">
          Create a job from a flow or the CLI.
        </p>
      </div>
    );
  }

  return (
    <ActionContextProvider>
      <div className="flex flex-col h-full">
        {/* Unified Toolbar */}
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

          {/* Search */}
          <div className="relative flex-1 sm:flex-none">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500" />
            <Input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Filter..."
              className="pl-8 h-8 w-full sm:w-40 text-sm bg-background border-border dark:border-input dark:bg-input/30"
            />
          </div>

          {/* Create job — moved next to search on mobile */}
          <div className="sm:order-last sm:ml-auto">
            <CreateJobDialog
              onCreated={(jobId) =>
                navigate({ to: "/jobs/$jobId", params: { jobId } })
              }
            />
          </div>

          {/* Flow filter */}
          <ComboBox
            value={flowFilter}
            onChange={setFlowFilter}
            options={flowOptions}
            placeholder="All flows"
            searchPlaceholder="Filter by flow..."
            sortable
          />

          {/* Time range */}
          <ComboBox
            value={timeRange ?? "all"}
            onChange={(v) => setTimeRange(v === "all" ? undefined : v as TimeRange)}
            options={TIME_RANGE_OPTIONS}
            placeholder="All time"
            searchPlaceholder="Time range..."
          />

          {/* Status filters + total */}
          <div className="flex items-center gap-1 flex-wrap">
            <StatusFilterPills
              jobs={allJobs}
              activeStatuses={activeStatuses}
              onToggle={toggleStatus}
            />
          </div>
          <span className="text-xs text-zinc-500 whitespace-nowrap">
            {totalJobCount > allJobs.length
              ? `${allJobs.length} of ${totalJobCount}`
              : `${allJobs.length} total`}
          </span>
          <button
            onClick={() => setShowArchived((s) => !s)}
            className={cn(
              "text-xs transition-colors whitespace-nowrap",
              showArchived ? "text-foreground/70 hover:text-foreground" : "text-zinc-600 hover:text-zinc-400"
            )}
          >
            {showArchived ? "Hide archived" : "Show archived"}
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 min-h-0 flex flex-col">
          {viewMode === "grid" ? (
            <CanvasPage
              jobs={filteredJobs}
            />
          ) : (
            <JobListView jobs={filteredJobs} />
          )}
        </div>
      </div>
    </ActionContextProvider>
  );
}
