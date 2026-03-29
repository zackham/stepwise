import { useState, useMemo, useCallback, useRef, useEffect } from "react";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useJobs, useStepwiseMutations } from "@/hooks/useStepwise";
import { JobStatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { AlertTriangle, CirclePause, Clock, Monitor, Terminal, Trash2, Search, X, MoreVertical, XCircle, RefreshCw, ArrowUpDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { LiveDuration } from "@/components/LiveDuration";
import { Skeleton } from "@/components/ui/skeleton";
import { CreateJobDialog } from "@/components/jobs/CreateJobDialog";
import type { Job, JobStatus } from "@/lib/types";
import { JOB_STATUS_COLORS } from "@/lib/status-colors";
import { useWsStatus } from "@/hooks/useStepwiseWebSocket";

type SortOption = "recent" | "oldest" | "name" | "duration" | "status";
type JobListStatusFilter = "running" | "awaiting_input" | "paused" | "completed" | "failed" | "pending" | "cancelled";
type JobListDateRange = "today" | "7d" | "30d" | "all";

const SORT_OPTIONS: { value: SortOption; label: string }[] = [
  { value: "recent", label: "Recent" },
  { value: "oldest", label: "Oldest" },
  { value: "name", label: "Name A-Z" },
  { value: "duration", label: "Duration" },
  { value: "status", label: "Status" },
];

const STATUS_ORDER: Record<string, number> = {
  running: 0,
  awaiting_input: 1,
  paused: 2,
  pending: 3,
  completed: 4,
  failed: 5,
  cancelled: 6,
};

const STATUS_FILTER_VALUES = new Set<JobListStatusFilter>([
  "running",
  "awaiting_input",
  "paused",
  "completed",
  "failed",
  "pending",
  "cancelled",
]);

const DATE_RANGE_VALUES = new Set<JobListDateRange>(["today", "7d", "30d", "all"]);

const DATE_RANGE_OPTIONS: { value: JobListDateRange; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "all", label: "All" },
];

function getDateRangeStart(range: JobListDateRange): Date | null {
  const now = new Date();
  switch (range) {
    case "today":
      return new Date(now.getFullYear(), now.getMonth(), now.getDate());
    case "7d":
      return new Date(now.getTime() - 7 * 86400000);
    case "30d":
      return new Date(now.getTime() - 30 * 86400000);
    default:
      return null;
  }
}

function getSavedSort(): SortOption {
  try {
    const saved = localStorage.getItem("stepwise-job-sort");
    if (saved && SORT_OPTIONS.some((o) => o.value === saved)) return saved as SortOption;
  } catch {}
  return "recent";
}

function sortJobs(jobs: Job[], sort: SortOption): Job[] {
  return [...jobs].sort((a, b) => {
    switch (sort) {
      case "recent":
        return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
      case "oldest":
        return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
      case "name": {
        const nameA = (a.name || a.objective || "").toLowerCase();
        const nameB = (b.name || b.objective || "").toLowerCase();
        return nameA.localeCompare(nameB);
      }
      case "duration": {
        const durA = new Date(a.updated_at).getTime() - new Date(a.created_at).getTime();
        const durB = new Date(b.updated_at).getTime() - new Date(b.created_at).getTime();
        return durB - durA;
      }
      case "status":
        return (STATUS_ORDER[a.status] ?? 99) - (STATUS_ORDER[b.status] ?? 99);
    }
  });
}

interface JobListProps {
  selectedJobId: string | null;
  onSelectJob: (jobId: string) => void;
}

const STATUS_OPTIONS: { value: JobListStatusFilter; label: string }[] = [
  { value: "running", label: "Running" },
  { value: "awaiting_input", label: "Awaiting Fulfillment" },
  { value: "paused", label: "Paused" },
  { value: "completed", label: "Completed" },
  { value: "failed", label: "Failed" },
  { value: "pending", label: "Pending" },
  { value: "cancelled", label: "Cancelled" },
];

function isStale(job: { status: string; created_by: string; heartbeat_at: string | null }): boolean {
  if (job.status !== "running" || job.created_by === "server") return false;
  if (!job.heartbeat_at) return true;
  const age = Date.now() - new Date(job.heartbeat_at).getTime();
  return age > 60_000;
}

function isCliOwned(created_by: string): boolean {
  return created_by.startsWith("cli:");
}

function timeAgo(ts: string): string {
  const now = Date.now();
  const then = new Date(ts).getTime();
  const diff = now - then;
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

function readQueryParam(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function readStatusFilter(value: unknown): JobListStatusFilter | null {
  return typeof value === "string" && STATUS_FILTER_VALUES.has(value as JobListStatusFilter)
    ? value as JobListStatusFilter
    : null;
}

function readDateRange(value: unknown): JobListDateRange {
  return typeof value === "string" && DATE_RANGE_VALUES.has(value as JobListDateRange)
    ? value as JobListDateRange
    : "all";
}

function formatLastUpdated(timestamp: number): string {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(timestamp);
}

function canCancel(status: string): boolean {
  return status === "running" || status === "paused";
}

function canRetry(status: string): boolean {
  return status === "paused" || status === "failed";
}

function JobActions({
  job,
  mutations,
}: {
  job: Job;
  mutations: ReturnType<typeof useStepwiseMutations>;
}) {
  const showCancel = canCancel(job.status);
  const showRetry = canRetry(job.status);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className="p-1 rounded hover:bg-zinc-200/50 dark:hover:bg-zinc-700/50 text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 transition-colors min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 flex items-center justify-center"
        onClick={(e) => e.stopPropagation()}
      >
        <MoreVertical className="w-3.5 h-3.5" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" side="bottom" sideOffset={4}>
        {showRetry && (
          <DropdownMenuItem
            onClick={(e) => {
              e.stopPropagation();
              mutations.resumeJob.mutate(job.id);
            }}
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Retry
          </DropdownMenuItem>
        )}
        {showCancel && (
          <DropdownMenuItem
            onClick={(e) => {
              e.stopPropagation();
              mutations.cancelJob.mutate(job.id);
            }}
          >
            <XCircle className="w-3.5 h-3.5" />
            Cancel
          </DropdownMenuItem>
        )}
        {(showRetry || showCancel) && <DropdownMenuSeparator />}
        <DropdownMenuItem
          variant="destructive"
          onClick={(e) => {
            e.stopPropagation();
            mutations.deleteJob.mutate(job.id);
          }}
        >
          <Trash2 className="w-3.5 h-3.5" />
          Delete
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const JOB_ROW_HEIGHT = 68; // estimated row height in px

function VirtualJobList({
  filteredJobs,
  selectedJobId,
  focusedIndex,
  onSelectJob,
  setFocusedIndex,
  mutations,
  scrollRef,
}: {
  filteredJobs: Job[];
  selectedJobId: string | null;
  focusedIndex: number;
  onSelectJob: (jobId: string) => void;
  setFocusedIndex: (index: number) => void;
  mutations: ReturnType<typeof useStepwiseMutations>;
  scrollRef: React.RefObject<HTMLDivElement | null>;
}) {
  const virtualizer = useVirtualizer({
    count: filteredJobs.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => JOB_ROW_HEIGHT,
    overscan: 5,
  });

  // Scroll focused item into view
  useEffect(() => {
    if (focusedIndex >= 0) {
      virtualizer.scrollToIndex(focusedIndex, { align: "auto" });
    }
  }, [focusedIndex, virtualizer]);

  return (
    <div ref={scrollRef} className="flex-1 min-h-0 overflow-auto">
      <div
        className="relative p-2"
        style={{ height: virtualizer.getTotalSize() + 16 }}
      >
        {virtualizer.getVirtualItems().map((virtualRow) => {
          const job = filteredJobs[virtualRow.index];
          const index = virtualRow.index;
          return (
            <div
              key={job.id}
              id={`job-${job.id}`}
              ref={virtualizer.measureElement}
              data-index={index}
              role="option"
              tabIndex={-1}
              aria-selected={selectedJobId === job.id}
              onClick={() => onSelectJob(job.id)}
              onFocus={() => setFocusedIndex(index)}
              className={cn(
                "absolute left-2 right-2 text-left px-3 py-1.5 rounded-md transition-colors",
                "border-b border-zinc-200/50 dark:border-zinc-800/50",
                "hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50 cursor-pointer",
                selectedJobId === job.id
                  ? "bg-zinc-100 dark:bg-zinc-800 ring-1 ring-zinc-300 dark:ring-zinc-700"
                  : "bg-transparent",
                focusedIndex === index && selectedJobId !== job.id
                  && "bg-zinc-100/30 dark:bg-zinc-800/30 ring-1 ring-zinc-300/50 dark:ring-zinc-700/50",
              )}
              style={{
                top: virtualRow.start,
              }}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-start gap-2 min-w-0 flex-1">
                  <span
                    className={cn(
                      "w-2.5 h-2.5 rounded-full mt-1 shrink-0",
                      JOB_STATUS_COLORS[job.status as JobStatus]?.dot ?? "bg-zinc-400",
                      job.status === "running" && "animate-pulse",
                    )}
                  />
                  <div className="min-w-0">
                    <div className="text-sm text-foreground truncate">
                      {job.name || job.objective || "Untitled Job"}
                    </div>
                    {job.current_step ? (
                      <div className="text-[11px] text-zinc-500 truncate mt-0.5">
                        {job.name && job.objective && (
                          <span>{job.objective} · </span>
                        )}
                        <span className={cn(
                          job.current_step.status === "running" && "text-blue-400",
                          job.current_step.status === "failed" && "text-red-400",
                        )}>
                          {job.current_step.name}
                        </span>
                        {job.current_step.started_at && (
                          <span className="text-zinc-600">
                            {" · "}
                            <LiveDuration
                              startTime={job.current_step.started_at}
                              endTime={job.current_step.completed_at ?? null}
                            />
                          </span>
                        )}
                      </div>
                    ) : job.name && job.objective ? (
                      <div className="text-[11px] text-zinc-500 truncate">
                        {job.objective}
                      </div>
                    ) : null}
                  </div>
                </div>
                <div className="flex items-start gap-1 shrink-0">
                  <div className="flex flex-col items-end gap-1">
                    <div className="flex items-center gap-1">
                      {isStale(job) && (
                        <AlertTriangle className="w-3 h-3 text-amber-500" />
                      )}
                      <JobStatusBadge status={job.status} />
                      {job.has_suspended_steps && (
                        <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/30">
                          <CirclePause className="w-2.5 h-2.5" />
                          Awaiting Fulfillment
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] text-zinc-600 flex items-center gap-0.5">
                      {isCliOwned(job.created_by) ? (
                        <Terminal className="w-2.5 h-2.5" />
                      ) : (
                        <Monitor className="w-2.5 h-2.5" />
                      )}
                      <Clock className="w-2.5 h-2.5" />
                      {timeAgo(job.updated_at)}
                    </span>
                  </div>
                  <JobActions job={job} mutations={mutations} />
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function JobList({
  selectedJobId,
  onSelectJob,
}: JobListProps) {
  const navigate = useNavigate();
  const search = useSearch({ strict: false }) as {
    q?: unknown;
    status?: unknown;
    range?: unknown;
  };
  const [sortBy, setSortBy] = useState<SortOption>(getSavedSort);
  const query = readQueryParam(search.q);
  const statusFilter = readStatusFilter(search.status);
  const dateRange = readDateRange(search.range);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState<number>(-1);
  const { data: jobs = [], isLoading, isFetching, dataUpdatedAt } = useJobs();
  const mutations = useStepwiseMutations();
  const wsStatus = useWsStatus();
  const queryClient = useQueryClient();
  const listRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const lastUpdatedLabel = dataUpdatedAt > 0
    ? formatLastUpdated(dataUpdatedAt)
    : "Not refreshed yet";

  const updateSearch = useCallback((updater: (prev: {
    q?: string;
    status?: JobListStatusFilter;
    range?: Exclude<JobListDateRange, "all">;
  }) => {
    q?: string;
    status?: JobListStatusFilter;
    range?: Exclude<JobListDateRange, "all">;
  }) => {
    navigate({
      search: updater as never,
      replace: true,
    });
  }, [navigate]);

  const setQuery = useCallback((value: string) => {
    updateSearch((prev) => ({
      ...prev,
      q: value || undefined,
    }));
  }, [updateSearch]);

  const setStatusFilter = useCallback((value: JobListStatusFilter | null) => {
    updateSearch((prev) => ({
      ...prev,
      status: value || undefined,
    }));
  }, [updateSearch]);

  const setDateRange = useCallback((value: JobListDateRange) => {
    updateSearch((prev) => ({
      ...prev,
      range: value === "all" ? undefined : value,
    }));
  }, [updateSearch]);

  const refreshJobs = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["jobs"] });
  }, [queryClient]);

  // Jobs filtered by date range first (used for all downstream filtering)
  const dateFilteredJobs = useMemo(() => {
    if (dateRange === "all") return jobs;
    const rangeStart = getDateRangeStart(dateRange);
    if (!rangeStart) return jobs;
    return jobs.filter((job) => new Date(job.created_at) >= rangeStart);
  }, [jobs, dateRange]);

  // Count jobs per status (from date-filtered list) for filter pill badges
  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const job of dateFilteredJobs) {
      counts[job.status] = (counts[job.status] || 0) + 1;
      if (job.has_suspended_steps) {
        counts["awaiting_input"] = (counts["awaiting_input"] || 0) + 1;
      }
    }
    return counts;
  }, [dateFilteredJobs]);

  // When a text query is active, recount statuses from query-matched jobs only
  const filteredStatusCounts = useMemo(() => {
    const q = query.toLowerCase().trim();
    if (!q) return statusCounts;
    const counts: Record<string, number> = {};
    for (const job of dateFilteredJobs) {
      const nameMatch = (job.name || "").toLowerCase().includes(q);
      const objMatch = (job.objective || "").toLowerCase().includes(q);
      if (!nameMatch && !objMatch) continue;
      counts[job.status] = (counts[job.status] || 0) + 1;
      if (job.has_suspended_steps) {
        counts["awaiting_input"] = (counts["awaiting_input"] || 0) + 1;
      }
    }
    return counts;
  }, [dateFilteredJobs, query, statusCounts]);

  const displayCounts = query.trim() ? filteredStatusCounts : statusCounts;

  // Filter jobs by query (matches name or objective) and status toggle, then sort
  const filteredJobs = useMemo(() => {
    const q = query.toLowerCase().trim();
    const filtered = dateFilteredJobs.filter((job) => {
      if (statusFilter) {
        if (statusFilter === "awaiting_input") {
          if (!job.has_suspended_steps) return false;
        } else if (job.status !== statusFilter) {
          return false;
        }
      }
      if (q) {
        const nameMatch = (job.name || "").toLowerCase().includes(q);
        const objMatch = (job.objective || "").toLowerCase().includes(q);
        if (!nameMatch && !objMatch) return false;
      }
      return true;
    });
    return sortJobs(filtered, sortBy);
  }, [dateFilteredJobs, query, statusFilter, sortBy]);

  // Reset focused index and scroll position when filtered list changes
  useEffect(() => {
    setFocusedIndex(-1);
    scrollRef.current?.scrollTo({ top: 0 });
  }, [filteredJobs.length, query, statusFilter, dateRange, sortBy]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const len = filteredJobs.length;
      if (len === 0) return;

      switch (e.key) {
        case "ArrowDown":
          e.preventDefault();
          setFocusedIndex((prev) => (prev < len - 1 ? prev + 1 : prev));
          break;
        case "ArrowUp":
          e.preventDefault();
          setFocusedIndex((prev) => (prev > 0 ? prev - 1 : prev));
          break;
        case "Enter":
          e.preventDefault();
          if (focusedIndex >= 0 && focusedIndex < len) {
            onSelectJob(filteredJobs[focusedIndex].id);
          }
          break;
        case "Escape":
          e.preventDefault();
          setFocusedIndex(-1);
          break;
      }
    },
    [filteredJobs, focusedIndex, onSelectJob],
  );

  // Global keyboard shortcuts: j/k to navigate, / to focus search, Escape to clear
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (
        target instanceof HTMLInputElement ||
        target instanceof HTMLTextAreaElement ||
        target.isContentEditable
      ) return;

      const len = filteredJobs.length;

      if (e.key === "j") {
        e.preventDefault();
        if (len === 0) return;
        setFocusedIndex((prev) => {
          const next = prev < len - 1 ? prev + 1 : prev;
          if (next >= 0 && next < len) onSelectJob(filteredJobs[next].id);
          return next;
        });
      } else if (e.key === "k") {
        e.preventDefault();
        if (len === 0) return;
        setFocusedIndex((prev) => {
          const next = prev > 0 ? prev - 1 : 0;
          if (next >= 0 && next < len) onSelectJob(filteredJobs[next].id);
          return next;
        });
      } else if (e.key === "/") {
        e.preventDefault();
        searchInputRef.current?.focus();
      } else if (e.key === "Escape") {
        if (query || statusFilter || dateRange !== "all") {
          e.preventDefault();
          setQuery("");
          setStatusFilter(null);
          setDateRange("all");
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [filteredJobs, onSelectJob, query, statusFilter, dateRange, setQuery, setStatusFilter, setDateRange]);

  const hasActiveFilter = !!query || !!statusFilter || dateRange !== "all";
  const liveStatusLabel = wsStatus === "connected"
    ? "Live"
    : wsStatus === "reconnecting"
      ? "Reconnecting"
      : "Offline";

  return (
    <div
      className="flex flex-col h-full"
      ref={listRef}
      onKeyDown={handleKeyDown}
      role="listbox"
      aria-label="Job list"
      aria-activedescendant={focusedIndex >= 0 && filteredJobs[focusedIndex] ? `job-${filteredJobs[focusedIndex].id}` : undefined}
      tabIndex={0}
    >
      {/* Search + delete */}
      <div className="px-2 py-1.5 border-b border-border space-y-1">
        <div className="flex items-center gap-1.5">
          <div className="relative flex-1">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-600" />
            <input
              ref={searchInputRef}
              data-hotkey-search-input="true"
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter jobs..."
              className="w-full h-7 pl-7 pr-7 rounded-md border border-zinc-300 dark:border-zinc-800 bg-zinc-50/50 dark:bg-zinc-900/50 text-xs text-foreground placeholder:text-zinc-500 dark:placeholder:text-zinc-600 focus:outline-none focus:border-zinc-400 dark:focus:border-zinc-600 transition-colors"
            />
            {hasActiveFilter && (
              <button
                onClick={() => {
                  setQuery("");
                  setStatusFilter(null);
                  setDateRange("all");
                }}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-zinc-600 hover:text-zinc-400 p-1"
              >
                <X className="w-3 h-3" />
              </button>
            )}
          </div>
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger
                aria-label={liveStatusLabel}
                className="shrink-0 inline-flex min-h-[44px] min-w-[44px] md:min-h-0 md:min-w-0 items-center justify-center rounded-md"
              >
                <span
                  className={cn(
                    "h-2.5 w-2.5 rounded-full",
                    wsStatus === "connected" ? "bg-emerald-400" : "bg-zinc-400",
                  )}
                />
              </TooltipTrigger>
              <TooltipContent>{liveStatusLabel}</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label="Refresh jobs"
                    onClick={refreshJobs}
                    className="text-zinc-600 hover:text-zinc-300"
                  >
                    <RefreshCw className={cn("w-3.5 h-3.5", isFetching && "animate-spin")} />
                  </Button>
                }
              />
              <TooltipContent>Last updated {lastUpdatedLabel}</TooltipContent>
            </Tooltip>
          </TooltipProvider>
          {confirmDelete ? (
            <div className="flex items-center gap-1 shrink-0">
              <button
                onClick={() => {
                  mutations.deleteAllJobs.mutate(undefined, {
                    onSuccess: () => setConfirmDelete(false),
                  });
                }}
                disabled={mutations.deleteAllJobs.isPending}
                className="text-[10px] text-red-400 hover:text-red-300 px-1.5 py-1 rounded border border-red-500/30 hover:bg-red-500/10 transition-colors"
              >
                Confirm
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                className="text-[10px] text-zinc-500 hover:text-zinc-300 px-1.5 py-1"
              >
                Cancel
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirmDelete(true)}
              className="text-zinc-600 hover:text-red-400 p-1 rounded hover:bg-zinc-200/50 dark:hover:bg-zinc-800/50 transition-colors shrink-0 min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 flex items-center justify-center"
              title="Delete all jobs"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        {/* Awaiting Fulfillment priority filter */}
        {(displayCounts["awaiting_input"] ?? 0) > 0 && (
          <button
            data-testid="awaiting-input-filter"
            onClick={() => setStatusFilter(statusFilter === "awaiting_input" ? null : "awaiting_input")}
            className={cn(
              "w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md text-xs font-medium transition-colors",
              statusFilter === "awaiting_input"
                ? "bg-amber-500/20 text-amber-300 ring-1 ring-amber-500/40"
                : "bg-amber-500/10 text-amber-400/90 hover:bg-amber-500/15",
            )}
          >
            <CirclePause className="w-3.5 h-3.5 shrink-0" />
            <span>Awaiting Fulfillment</span>
            <span className={cn(
              "ml-auto tabular-nums rounded-full px-1.5 py-0.5 text-[10px] font-semibold",
              statusFilter === "awaiting_input"
                ? "bg-amber-500/30 text-amber-200"
                : "bg-amber-500/20 text-amber-400",
            )}>
              {displayCounts["awaiting_input"]}
            </span>
          </button>
        )}

        {/* Status pills + sort */}
        <div className="flex items-center gap-1">
          <div className="flex gap-0.5 flex-1 overflow-x-auto md:flex-wrap md:overflow-x-visible scrollbar-none">
            {STATUS_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setStatusFilter(statusFilter === opt.value ? null : opt.value)}
                className={cn(
                  "px-1.5 py-0.5 rounded text-[10px] transition-colors shrink-0 min-h-[44px] md:min-h-0",
                  statusFilter === opt.value
                    ? "bg-zinc-200 dark:bg-zinc-700 text-foreground"
                    : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50",
                )}
              >
                {opt.label}
                {displayCounts[opt.value] ? (
                  <span className="text-zinc-500"> ({displayCounts[opt.value]})</span>
                ) : null}
              </button>
            ))}
          </div>
          <Select
            value={sortBy}
            onValueChange={(v) => {
              const val = v as SortOption;
              setSortBy(val);
              try { localStorage.setItem("stepwise-job-sort", val); } catch {}
            }}
          >
            <SelectTrigger className="h-5 w-auto gap-1 px-1.5 border-none bg-transparent text-[10px] text-zinc-500 hover:text-zinc-300 focus:ring-0 shadow-none min-h-[44px] md:min-h-0">
              <ArrowUpDown className="w-2.5 h-2.5 shrink-0" />
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SORT_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value} className="text-xs">
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="flex gap-0.5 overflow-x-auto scrollbar-none">
          {DATE_RANGE_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setDateRange(opt.value)}
              className={cn(
                "px-1.5 py-0.5 rounded text-[10px] transition-colors shrink-0 min-h-[44px] md:min-h-0",
                dateRange === opt.value
                  ? "bg-zinc-200 dark:bg-zinc-700 text-foreground"
                  : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Job list */}
      {isLoading ? (
        <div data-testid="job-list-skeleton" className="p-2 space-y-1">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="px-3 py-1.5">
              <div className="flex items-start gap-2">
                <Skeleton className="w-3.5 h-3.5 mt-0.5 rounded-full shrink-0" />
                <div className="flex-1 space-y-1.5">
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : filteredJobs.length === 0 ? (
        hasActiveFilter ? (
          <div className="text-zinc-500 text-sm text-center py-8">
            No matching jobs
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-12 px-4 text-center max-w-sm mx-auto space-y-4">
            <img src="/stepwise-icon-64.png" alt="Stepwise" className="w-12 h-12 opacity-40" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-zinc-400">Start your first workflow</p>
              <p className="text-xs text-zinc-600">
                Create a job from the UI or run one from the terminal.
              </p>
            </div>
            <CreateJobDialog onCreated={(jobId) => onSelectJob(jobId)} />
            <code className="text-[11px] bg-zinc-100/80 dark:bg-zinc-800/80 text-zinc-600 dark:text-zinc-400 px-3 py-1.5 rounded-md border border-zinc-300/50 dark:border-zinc-700/50">
              stepwise run &lt;flow&gt; --watch
            </code>
          </div>
        )
      ) : (
        <VirtualJobList
          filteredJobs={filteredJobs}
          selectedJobId={selectedJobId}
          focusedIndex={focusedIndex}
          onSelectJob={onSelectJob}
          setFocusedIndex={setFocusedIndex}
          mutations={mutations}
          scrollRef={scrollRef}
        />
      )}
    </div>
  );
}
