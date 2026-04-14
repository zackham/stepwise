import { useState, useMemo, useCallback, useRef, useEffect, memo } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
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
import { useSchedules } from "@/hooks/useSchedules";

// ── localStorage keys ─────────────────────────────────────────────────
const LS_STATUS_FILTER = "stepwise-jobs-status-filter";
const LS_TIME_RANGE = "stepwise-jobs-time-range";

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

// Hex colors for inline checkbox accent — mirrors the dot colors from JOB_STATUS_COLORS
const STATUS_HEX: Record<string, string> = {
  running: "#60a5fa",     // blue-400
  paused: "#fbbf24",      // amber-400
  pending: "#a1a1aa",     // zinc-400
  staged: "#a78bfa",      // violet-400
  awaiting_approval: "#fbbf24",
  completed: "#34d399",   // emerald-400
  failed: "#f87171",      // red-400
  cancelled: "#71717a",   // zinc-500
  archived: "#52525b",    // zinc-600
};

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
  const total = job.step_count ?? Object.keys(job.workflow?.steps ?? {}).length;
  // Use the backend-computed completed_steps count when available —
  // it only counts steps with status COMPLETED (not SKIPPED or FAILED).
  if (job.completed_steps != null) {
    return { completed: job.completed_steps, total };
  }
  // Fallback for older API responses without completed_steps
  if (job.status === "completed") return { completed: total, total };
  if (job.status === "staged" || job.status === "pending") return { completed: 0, total };
  return { completed: 0, total };
}

// ── Status Filter Checkboxes ───────────────────────────────────────────

function StatusFilterCheckboxes({
  jobs,
  activeStatuses,
  onToggle,
  onSolo,
  scheduledCount,
}: {
  jobs: Job[];
  activeStatuses: Set<string>;
  onToggle: (status: string) => void;
  onSolo: (status: string) => void;
  scheduledCount?: number;
}) {
  // When activeStatuses is empty, all are visible (no filter)
  const allVisible = activeStatuses.size === 0;

  const counts = useMemo(() => {
    const map: Partial<Record<JobStatus, number>> = {};
    for (const job of jobs) {
      map[job.status] = (map[job.status] ?? 0) + 1;
    }
    return map;
  }, [jobs]);

  const visibleStatuses = DISPLAY_ORDER.filter((s) => counts[s]);

  if (visibleStatuses.length === 0) return null;

  const displayLabel = (s: string) =>
    s === "completed" ? "done" : s === "awaiting_approval" ? "approval" : s;

  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {visibleStatuses.map((status) => {
        const isChecked = allVisible || activeStatuses.has(status);
        const hex = STATUS_HEX[status] ?? "#a1a1aa";
        return (
          <div key={status} className="flex items-center gap-1 group/cb">
            {/* Mini checkbox */}
            <button
              onClick={(e) => {
                e.stopPropagation();
                onToggle(status);
              }}
              title={`Toggle ${status}`}
              className="relative w-3 h-3 rounded-[3px] border transition-all duration-100 flex items-center justify-center shrink-0"
              style={{
                borderColor: isChecked ? hex : `${hex}50`,
                backgroundColor: isChecked ? hex : "transparent",
              }}
            >
              {isChecked && (
                <svg width="8" height="8" viewBox="0 0 8 8" fill="none" className="absolute">
                  <path d="M1.5 4L3.2 5.7L6.5 2.3" stroke="white" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
            </button>
            {/* Label — click for solo mode */}
            <button
              onClick={() => onSolo(status)}
              className={cn(
                "text-[11px] tabular-nums transition-colors leading-none",
                isChecked
                  ? "text-foreground/80 hover:text-foreground"
                  : "text-foreground/30 hover:text-foreground/50",
              )}
            >
              {counts[status]} {displayLabel(status)}
            </button>
          </div>
        );
      })}
      {/* Scheduled count (separate entity — not filterable, just info) */}
      {scheduledCount != null && scheduledCount > 0 && (
        <div className="flex items-center gap-1">
          <span
            className="w-3 h-3 rounded-[3px] border flex items-center justify-center shrink-0"
            style={{ borderColor: "#818cf850", backgroundColor: "#818cf830" }}
          >
            <svg width="8" height="8" viewBox="0 0 8 8" fill="none">
              <circle cx="4" cy="4" r="2" fill="#818cf8" />
            </svg>
          </span>
          <span className="text-[11px] tabular-nums text-indigo-400/70">
            {scheduledCount} scheduled
          </span>
        </div>
      )}
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
  isSelectionActive,
  onToggleSelect,
  onHoverDeps,
  onLeaveDeps,
}: {
  job: Job;
  cost?: number;
  depNames?: string[];
  selected: boolean;
  isSelectionActive: boolean;
  onToggleSelect: (jobId: string, shiftKey: boolean) => void;
  onHoverDeps: (jobId: string) => void;
  onLeaveDeps: () => void;
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
        {...(hasDeps ? { onMouseEnter: () => onHoverDeps(job.id), onMouseLeave: onLeaveDeps } : {})}
        className={cn(
          "w-full text-left px-4 sm:px-6 py-3 flex items-center gap-3 transition-none hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40 group cursor-pointer",
          selected && "bg-blue-50/50 dark:bg-blue-950/20",
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
          {hasDeps && depNames ? depNames.join(", ") : null}
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

  // Dependency maps (no hover state — highlighting done via DOM manipulation to avoid re-renders)
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

  // Pre-compute dep name arrays so row props are stable (memo-friendly)
  const depNamesMap = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const job of jobs) {
      if (job.depends_on && job.depends_on.length > 0) {
        const names = job.depends_on.map((id) => jobNameMap.get(id)).filter(Boolean) as string[];
        if (names.length > 0) m.set(job.id, names);
      }
    }
    return m;
  }, [jobs, jobNameMap]);

  // Ref for the scrollable container (used by virtualizer)
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  // DOM-based dep highlighting: add/remove CSS classes directly without React state
  const highlightedRowsRef = useRef<HTMLElement[]>([]);
  const handleHoverDeps = useCallback((jobId: string) => {
    const deps = dependsOnMap.get(jobId);
    if (!deps || deps.size === 0) return;
    const container = scrollContainerRef.current;
    if (!container) return;
    for (const depId of deps) {
      const el = container.querySelector<HTMLElement>(`[data-job-row="${depId}"]`);
      if (el) {
        el.classList.add("dep-highlighted");
        highlightedRowsRef.current.push(el);
      }
    }
  }, [dependsOnMap]);

  const handleLeaveDeps = useCallback(() => {
    for (const el of highlightedRowsRef.current) {
      el.classList.remove("dep-highlighted");
    }
    highlightedRowsRef.current = [];
  }, []);

  // Build cost map from inline cost_usd in the jobs list response
  const costMap = useMemo(() => {
    const map = new Map<string, number>();
    for (const job of jobs) {
      if (job.cost_usd != null) map.set(job.id, job.cost_usd);
    }
    return map;
  }, [jobs]);

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

  // Flatten jobs + groups into a flat row array for virtualization
  type FlatRow =
    | { type: "job"; job: Job; inGroup?: boolean }
    | { type: "group-header"; name: string; groupJobs: Job[]; isExpanded: boolean };

  const flatRows = useMemo(() => {
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

    type ListItem = { type: "group"; name: string; jobs: Job[]; sortKey: number } | { type: "job"; job: Job; sortKey: number };
    const items: ListItem[] = [];

    for (const [groupName, groupJobs] of groups.entries()) {
      const sortKey = Math.max(...groupJobs.map((j) => new Date(j.updated_at).getTime()));
      items.push({ type: "group", name: groupName, jobs: groupJobs, sortKey });
    }
    for (const job of ungrouped) {
      items.push({ type: "job", job, sortKey: new Date(job.updated_at).getTime() });
    }

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

    const rows: FlatRow[] = [];
    for (const item of items) {
      if (item.type === "job") {
        rows.push({ type: "job", job: item.job });
      } else {
        const groupJobs = item.jobs;
        const groupName = item.name;
        const isTerminal = groupJobs.every((j) => TERMINAL_STATUSES.has(j.status));
        const isExpanded = isTerminal ? expandedGroups.has(groupName) : !expandedGroups.has(groupName);
        rows.push({ type: "group-header", name: groupName, groupJobs, isExpanded });
        if (isExpanded) {
          for (const job of groupJobs) {
            rows.push({ type: "job", job, inGroup: true });
          }
        }
      }
    }
    return rows;
  }, [activeJobs, terminalJobs, sortCol, sortAsc, expandedGroups]);

  const JOB_ROW_HEIGHT = 64;
  const GROUP_HEADER_HEIGHT = 44;

  const virtualizer = useVirtualizer({
    count: flatRows.length,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: (index) => flatRows[index]?.type === "group-header" ? GROUP_HEADER_HEIGHT : JOB_ROW_HEIGHT,
    overscan: 8,
  });

  if (jobs.length === 0) {
    return (
      <div className="flex items-center justify-center h-32 text-xs text-zinc-500">
        No matching jobs
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Header row */}
      <div className="hidden sm:flex items-center px-4 sm:px-6 py-2 gap-3 text-[10px] uppercase tracking-wider text-zinc-500 font-medium select-none border-b border-border shrink-0">
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

      {/* Virtualized scrollable area */}
      <div ref={scrollContainerRef} className="flex-1 min-h-0 overflow-y-auto">
        <div
          style={{ height: virtualizer.getTotalSize(), position: "relative" }}
        >
          {virtualizer.getVirtualItems().map((virtualRow) => {
            const row = flatRows[virtualRow.index];
            if (!row) return null;

            if (row.type === "group-header") {
              const groupJobs = row.groupJobs;
              const groupName = row.name;
              const hasRunning = groupJobs.some((j) => j.status === "running");
              const hasFailed = groupJobs.some((j) => j.status === "failed");
              const allCompleted = groupJobs.every((j) => j.status === "completed");
              const rolledStatus: JobStatus = hasRunning ? "running" : hasFailed ? "failed" : allCompleted ? "completed" : "completed";
              const totalDurationMs = groupJobs.reduce((sum, j) => {
                if (j.status === "staged" || j.status === "pending") return sum;
                return sum + (new Date(j.updated_at).getTime() - new Date(j.created_at).getTime());
              }, 0);
              const durationStr = totalDurationMs < 1000 ? "\u2014" : totalDurationMs < 60000 ? `${(totalDurationMs / 1000).toFixed(1)}s` : totalDurationMs < 3600000 ? `${(totalDurationMs / 60000).toFixed(1)}m` : `${(totalDurationMs / 3600000).toFixed(1)}h`;
              const completedCount = groupJobs.filter((j) => TERMINAL_STATUSES.has(j.status)).length;
              const jobsSummary = `${completedCount} of ${groupJobs.length} done`;
              const mostRecent = groupJobs.reduce((latest, j) => j.updated_at > latest ? j.updated_at : latest, groupJobs[0].updated_at);

              return (
                <div
                  key={`group-${groupName}`}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    transform: `translateY(${virtualRow.start}px)`,
                  }}
                >
                  <button
                    onClick={() => toggleGroup(groupName)}
                    className="w-full flex items-center gap-3 px-4 sm:px-6 py-2.5 bg-zinc-100/50 dark:bg-zinc-800/60 hover:bg-zinc-200/50 dark:hover:bg-zinc-700/60 transition-colors text-left border-l-2 border-primary/70"
                  >
                    <span className="w-4 shrink-0" />
                    <ChevronRight
                      className={cn(
                        "w-3.5 h-3.5 text-zinc-400 transition-transform shrink-0",
                        row.isExpanded && "rotate-90",
                      )}
                    />
                    <div className="flex-1 min-w-0">
                      <span className="text-sm font-medium text-zinc-400 dark:text-zinc-400 truncate">{groupName}</span>
                    </div>
                    <div className="hidden sm:flex items-center gap-4 shrink-0 text-[11px] text-zinc-500 tabular-nums">
                      <span className="w-14 text-right">{jobsSummary}</span>
                      <span className="w-16 text-right"></span>
                      <span className="w-16 text-right">{durationStr}</span>
                      <span className="w-20 text-right">
                        <JobStatusBadge status={rolledStatus} />
                      </span>
                      <span className="w-14 text-right">{timeAgo(mostRecent)}</span>
                    </div>
                    <div className="flex items-center gap-2 sm:hidden text-[10px] text-zinc-500">
                      <JobStatusBadge status={rolledStatus} />
                      <span>{jobsSummary}</span>
                    </div>
                  </button>
                </div>
              );
            }

            // Job row
            const job = row.job;
            return (
              <div
                key={job.id}
                style={{
                  position: "absolute",
                  top: 0,
                  left: 0,
                  width: "100%",
                  transform: `translateY(${virtualRow.start}px)`,
                }}
                className={row.inGroup ? "border-l-2 border-primary/30 ml-6 bg-zinc-900/30" : undefined}
              >
                <JobListRow
                  job={job}
                  cost={costMap.get(job.id)}
                  depNames={depNamesMap.get(job.id)}
                  selected={selectedIds.has(job.id)}
                  isSelectionActive={isSelectionActive}
                  onToggleSelect={handleToggleSelect}
                  onHoverDeps={handleHoverDeps}
                  onLeaveDeps={handleLeaveDeps}
                />
              </div>
            );
          })}
        </div>
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

  // ── localStorage-backed defaults ──────────────────────────────────────
  // URL params take priority; localStorage provides defaults when URL is clean.
  const lsInitRef = useRef(false);
  const [lsDefaults] = useState(() => {
    try {
      const storedStatus = localStorage.getItem(LS_STATUS_FILTER);
      const storedRange = localStorage.getItem(LS_TIME_RANGE);
      return {
        status: storedStatus || undefined,
        range: storedRange || undefined,
      };
    } catch {
      return { status: undefined, range: undefined };
    }
  });

  const viewMode = searchParams.view_mode ?? "list";
  const searchQuery = searchParams.q ?? "";

  // Time range: URL param > localStorage default
  const timeRange = (searchParams.range ?? lsDefaults.range) as TimeRange;

  // Parse active status filters: URL param > localStorage default
  const activeStatuses = useMemo(() => {
    const raw = searchParams.status ?? lsDefaults.status;
    if (!raw) return new Set<string>();
    return new Set(raw.split(","));
  }, [searchParams.status, lsDefaults.status]);

  // Apply localStorage defaults to URL on first mount (so URL reflects the persisted state)
  useEffect(() => {
    if (lsInitRef.current) return;
    lsInitRef.current = true;
    const updates: Record<string, unknown> = {};
    if (!searchParams.status && lsDefaults.status) {
      updates.status = lsDefaults.status;
    }
    if (!searchParams.range && lsDefaults.range) {
      updates.range = lsDefaults.range;
    }
    if (Object.keys(updates).length > 0) {
      navigate({
        search: ((prev: Record<string, unknown>) => ({ ...prev, ...updates })) as never,
        replace: true,
      });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const [showArchived, setShowArchived] = useState(false);
  const { data: jobsResponse, isLoading } = useJobs(undefined, true, showArchived);
  const allJobs = jobsResponse?.jobs ?? [];
  const totalJobCount = jobsResponse?.total ?? allJobs.length;
  const { data: flows = [] } = useLocalFlows();
  const [flowFilter, setFlowFilter] = useState("all");

  // Fetch scheduled count from schedules API
  const { data: schedules } = useSchedules("active");
  const scheduledCount = schedules?.length ?? 0;

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
      // Persist to localStorage
      try {
        if (range) {
          localStorage.setItem(LS_TIME_RANGE, range);
        } else {
          localStorage.removeItem(LS_TIME_RANGE);
        }
      } catch { /* localStorage unavailable */ }
    },
    [updateSearch],
  );

  // Helper to persist status filter to localStorage
  const persistStatusFilter = useCallback((statuses: Set<string>) => {
    try {
      if (statuses.size > 0) {
        localStorage.setItem(LS_STATUS_FILTER, Array.from(statuses).join(","));
      } else {
        localStorage.removeItem(LS_STATUS_FILTER);
      }
    } catch { /* localStorage unavailable */ }
  }, []);

  // Compute the set of statuses that actually have jobs (for toggle-from-all logic)
  const presentStatuses = useMemo(() => {
    const s = new Set<string>();
    for (const job of allJobs) s.add(job.status);
    return s;
  }, [allJobs]);

  const toggleStatus = useCallback(
    (status: string) => {
      let next: Set<string>;
      if (activeStatuses.size === 0) {
        // Currently "show all" — unchecking one means "show all EXCEPT this one"
        next = new Set(presentStatuses);
        next.delete(status);
      } else if (activeStatuses.has(status)) {
        next = new Set(activeStatuses);
        next.delete(status);
      } else {
        next = new Set(activeStatuses);
        next.add(status);
      }
      // If everything is now selected, collapse back to empty (= show all)
      if (next.size >= presentStatuses.size && [...presentStatuses].every((s) => next.has(s))) {
        next = new Set<string>();
      }
      persistStatusFilter(next);
      updateSearch({
        status: next.size > 0 ? Array.from(next).join(",") : undefined,
      });
    },
    [activeStatuses, presentStatuses, updateSearch, persistStatusFilter],
  );

  // Solo mode: show ONLY this status (hide all others)
  const soloStatus = useCallback(
    (status: string) => {
      // If already soloed on this status, clear the filter (show all)
      if (activeStatuses.size === 1 && activeStatuses.has(status)) {
        persistStatusFilter(new Set());
        updateSearch({ status: undefined });
      } else {
        const next = new Set([status]);
        persistStatusFilter(next);
        updateSearch({ status: status });
      }
    },
    [activeStatuses, updateSearch, persistStatusFilter],
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

          {/* Status filter checkboxes + scheduled count + total */}
          <StatusFilterCheckboxes
            jobs={allJobs}
            activeStatuses={activeStatuses}
            onToggle={toggleStatus}
            onSolo={soloStatus}
            scheduledCount={scheduledCount}
          />
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
