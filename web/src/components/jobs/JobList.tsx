import { useState, useMemo } from "react";
import { useJobs, useStepwiseMutations } from "@/hooks/useStepwise";
import { JobStatusBadge } from "@/components/StatusBadge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { AlertTriangle, Briefcase, Clock, Monitor, Terminal, Trash2, Search, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Job, JobStatus } from "@/lib/types";

interface JobListProps {
  selectedJobId: string | null;
  onSelectJob: (jobId: string) => void;
}

const STATUS_OPTIONS: { value: string; label: string }[] = [
  { value: "running", label: "Running" },
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

export function JobList({ selectedJobId, onSelectJob }: JobListProps) {
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { data: jobs = [], isLoading } = useJobs();
  const mutations = useStepwiseMutations();

  // Derive unique workflow names from objectives
  const workflowNames = useMemo(() => {
    const names = new Set<string>();
    for (const job of jobs) {
      if (job.objective) names.add(job.objective);
    }
    return [...names].sort();
  }, [jobs]);

  // Filter jobs by query (matches name or objective) and status toggle
  const filteredJobs = useMemo(() => {
    const q = query.toLowerCase().trim();
    return jobs
      .filter((job) => {
        if (statusFilter && job.status !== statusFilter) return false;
        if (q) {
          const nameMatch = (job.name || "").toLowerCase().includes(q);
          const objMatch = (job.objective || "").toLowerCase().includes(q);
          if (!nameMatch && !objMatch) return false;
        }
        return true;
      })
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  }, [jobs, query, statusFilter]);

  const hasActiveFilter = !!query || !!statusFilter;

  return (
    <div className="flex flex-col h-full">
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
                className="absolute right-1.5 top-1/2 -translate-y-1/2 text-zinc-600 hover:text-zinc-400"
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
              className="text-zinc-600 hover:text-red-400 p-1 rounded hover:bg-zinc-800/50 transition-colors shrink-0"
              title="Delete all jobs"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
        </div>

        {/* Status pills */}
        <div className="flex flex-wrap gap-1">
          {STATUS_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setStatusFilter(statusFilter === opt.value ? null : opt.value)}
              className={cn(
                "px-1.5 py-0.5 rounded text-[10px] transition-colors",
                statusFilter === opt.value
                  ? "bg-zinc-700 text-foreground"
                  : "text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800/50",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Job list */}
      <ScrollArea className="flex-1 min-h-0">
        <div className="p-2 space-y-1">
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
            filteredJobs.map((job) => (
              <button
                key={job.id}
                onClick={() => onSelectJob(job.id)}
                className={cn(
                  "w-full text-left px-3 py-1.5 rounded-md transition-colors",
                  "hover:bg-zinc-800/50",
                  selectedJobId === job.id
                    ? "bg-zinc-800 ring-1 ring-zinc-700"
                    : "bg-transparent"
                )}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-start gap-2 min-w-0 flex-1">
                    <Briefcase className="w-3.5 h-3.5 text-zinc-500 mt-0.5 shrink-0" />
                    <div className="min-w-0">
                      <div className="text-sm text-foreground truncate">
                        {job.name || job.objective || "Untitled Job"}
                      </div>
                      {job.name && job.objective && (
                        <div className="text-[11px] text-zinc-500 truncate">
                          {job.objective}
                        </div>
                      )}
                      <div className="flex items-center gap-1.5 mt-1">
                        <span className="text-[10px] font-mono text-zinc-600">
                          {job.id}
                        </span>
                      </div>
                    </div>
                  </div>
                  <div className="flex flex-col items-end gap-1 shrink-0">
                    <div className="flex items-center gap-1">
                      {isStale(job) && (
                        <AlertTriangle className="w-3 h-3 text-amber-500" />
                      )}
                      <JobStatusBadge status={job.status} />
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
                </div>
              </button>
            ))
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
