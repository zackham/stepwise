import { useState, useMemo, useCallback, useRef, useEffect } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useJobs, useStepwiseMutations } from "@/hooks/useStepwise";
import { JobStatusBadge } from "@/components/StatusBadge";
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
import { AlertTriangle, Briefcase, CirclePause, Clock, Monitor, Terminal, Trash2, Search, X, MoreVertical, XCircle, RefreshCw, ArrowUpDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { LiveDuration } from "@/components/LiveDuration";
import { Skeleton } from "@/components/ui/skeleton";
import type { Job } from "@/lib/types";

type SortOption = "recent" | "oldest" | "name" | "duration" | "status";

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
  /** Controlled text filter (synced to URL in JobDashboard) */
  query?: string;
  statusFilter?: string | null;
  onQueryChange?: (value: string) => void;
  onStatusFilterChange?: (value: string | null) => void;
}

const STATUS_OPTIONS: { value: string; label: string }[] = [
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

type TimeGroup = "Today" | "Yesterday" | "This Week" | "Older";

function getTimeGroup(ts: string): TimeGroup {
  const now = new Date();
  const date = new Date(ts);

  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const startOfYesterday = new Date(startOfToday.getTime() - 86400000);

  // Start of this week (Monday)
  const dayOfWeek = now.getDay();
  const mondayOffset = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
  const startOfWeek = new Date(startOfToday.getTime() - mondayOffset * 86400000);

  if (date >= startOfToday) return "Today";
  if (date >= startOfYesterday) return "Yesterday";
  if (date >= startOfWeek) return "This Week";
  return "Older";
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
        className="p-1 rounded hover:bg-zinc-700/50 text-zinc-500 hover:text-zinc-300 transition-colors min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 flex items-center justify-center"
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
            <button
              key={job.id}
              id={`job-${job.id}`}
              ref={virtualizer.measureElement}
              data-index={index}
              role="option"
              aria-selected={selectedJobId === job.id}
              onClick={() => onSelectJob(job.id)}
              onFocus={() => setFocusedIndex(index)}
              className={cn(
                "absolute left-2 right-2 text-left px-3 py-1.5 rounded-md transition-colors",
                "border-b border-zinc-800/50",
                "hover:bg-zinc-800/50",
                selectedJobId === job.id
                  ? "bg-zinc-800 ring-1 ring-zinc-700"
                  : "bg-transparent",
                focusedIndex === index && selectedJobId !== job.id
                  && "bg-zinc-800/30 ring-1 ring-zinc-700/50",
              )}
              style={{
                top: virtualRow.start,
              }}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-start gap-2 min-w-0 flex-1">
                  <Briefcase className="w-3.5 h-3.5 text-zinc-500 mt-0.5 shrink-0" />
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
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function JobList({
  selectedJobId,
  onSelectJob,
  query: controlledQuery,
  statusFilter: controlledStatusFilter,
  onQueryChange,
  onStatusFilterChange,
}: JobListProps) {
  const [localQuery, setLocalQuery] = useState("");
  const [localStatusFilter, setLocalStatusFilter] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<SortOption>(getSavedSort);

  const isControlled = onQueryChange !== undefined;
  const query = isControlled ? (controlledQuery ?? "") : localQuery;
  const statusFilter = isControlled ? (controlledStatusFilter ?? null) : localStatusFilter;
  const setQuery = isControlled ? onQueryChange! : setLocalQuery;
  const setStatusFilter = isControlled ? onStatusFilterChange! : setLocalStatusFilter;
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [focusedIndex, setFocusedIndex] = useState<number>(-1);
  const { data: jobs = [], isLoading } = useJobs();
  const mutations = useStepwiseMutations();
  const listRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Count jobs per status (from full, unfiltered list) for filter pill badges
  const statusCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const job of jobs) {
      counts[job.status] = (counts[job.status] || 0) + 1;
      if (job.has_suspended_steps) {
        counts["awaiting_input"] = (counts["awaiting_input"] || 0) + 1;
      }
    }
    return counts;
  }, [jobs]);

  // Filter jobs by query (matches name or objective) and status toggle, then sort
  const filteredJobs = useMemo(() => {
    const q = query.toLowerCase().trim();
    const filtered = jobs.filter((job) => {
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
  }, [jobs, query, statusFilter, sortBy]);

  // Reset focused index and scroll position when filtered list changes
  useEffect(() => {
    setFocusedIndex(-1);
    scrollRef.current?.scrollTo({ top: 0 });
  }, [filteredJobs.length, query, statusFilter, sortBy]);

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

  const hasActiveFilter = !!query || !!statusFilter;

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
      <div className="p-2 border-b border-border space-y-1.5">
        <div className="flex items-center gap-1.5">
          <div className="relative flex-1">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-600" />
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter jobs..."
              className="w-full h-7 pl-7 pr-7 rounded-md border border-zinc-800 bg-zinc-900/50 text-xs text-foreground placeholder:text-zinc-600 focus:outline-none focus:border-zinc-600 transition-colors"
            />
            {hasActiveFilter && (
              <button
                onClick={() => { setQuery(""); setStatusFilter(null); }}
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-zinc-600 hover:text-zinc-400 p-1"
              >
                <X className="w-3 h-3" />
              </button>
            )}
          </div>
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
              className="text-zinc-600 hover:text-red-400 p-1 rounded hover:bg-zinc-800/50 transition-colors shrink-0 min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 flex items-center justify-center"
              title="Delete all jobs"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        {/* Awaiting Fulfillment priority filter */}
        {(statusCounts["awaiting_input"] ?? 0) > 0 && (
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
              {statusCounts["awaiting_input"]}
            </span>
          </button>
        )}

        {/* Status pills + sort */}
        <div className="flex items-center gap-1.5">
          <div className="flex gap-1 flex-1 overflow-x-auto md:flex-wrap md:overflow-x-visible scrollbar-none">
            {STATUS_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setStatusFilter(statusFilter === opt.value ? null : opt.value)}
                className={cn(
                  "px-1.5 py-0.5 rounded text-[10px] transition-colors shrink-0 min-h-[44px] md:min-h-0",
                  statusFilter === opt.value
                    ? "bg-zinc-700 text-foreground"
                    : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50",
                )}
              >
                {opt.label}
                {statusCounts[opt.value] ? (
                  <span className="text-zinc-500"> ({statusCounts[opt.value]})</span>
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
      </div>

      {/* Job list */}
      {isLoading ? (
        <div className="text-zinc-500 text-sm text-center py-8">
          Loading...
        </div>
      ) : filteredJobs.length === 0 ? (
        hasActiveFilter ? (
          <div className="text-zinc-500 text-sm text-center py-8">
            No matching jobs
          </div>
        ) : (
          <div className="flex flex-col items-center justify-center py-12 px-4 text-center space-y-4">
            <img src="/logo.png" alt="Stepwise" className="w-10 h-10 opacity-60" />
            <div className="space-y-1">
              <p className="text-sm font-medium text-zinc-400">No jobs yet</p>
              <p className="text-xs text-zinc-600">
                Run your first workflow from the terminal:
              </p>
            </div>
            <code className="text-[11px] bg-zinc-800/80 text-zinc-400 px-3 py-1.5 rounded-md border border-zinc-700/50">
              stepwise run &lt;flow&gt; --watch
            </code>
            <a
              href="https://github.com/zackham/stepwise#readme"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[11px] text-zinc-600 hover:text-zinc-400 underline underline-offset-2 transition-colors"
            >
              View docs
            </a>
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
