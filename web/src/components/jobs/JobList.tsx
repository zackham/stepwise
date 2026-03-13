import { useState } from "react";
import { useJobs, useStepwiseMutations } from "@/hooks/useStepwise";
import { JobStatusBadge } from "@/components/StatusBadge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { AlertTriangle, Briefcase, Clock, Monitor, Terminal, Trash2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { JobStatus } from "@/lib/types";

interface JobListProps {
  selectedJobId: string | null;
  onSelectJob: (jobId: string) => void;
}

function isStale(job: { status: string; created_by: string; heartbeat_at: string | null }): boolean {
  if (job.status !== "running" || job.created_by === "server") return false;
  if (!job.heartbeat_at) return true;
  const age = Date.now() - new Date(job.heartbeat_at).getTime();
  return age > 60_000; // 60 seconds
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
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const { data: jobs = [], isLoading } = useJobs(
    statusFilter === "all" ? undefined : statusFilter
  );
  const mutations = useStepwiseMutations();

  const sortedJobs = [...jobs].sort(
    (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
  );

  return (
    <div className="flex flex-col h-full">
      {/* Filter */}
      <div className="p-3 border-b border-border flex items-center gap-2">
        <Select value={statusFilter} onValueChange={(v) => { if (v !== null) setStatusFilter(v); }}>
          <SelectTrigger className="h-8 text-xs flex-1">
            <SelectValue placeholder="Filter by status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All Jobs</SelectItem>
            <SelectItem value="pending">Pending</SelectItem>
            <SelectItem value="running">Running</SelectItem>
            <SelectItem value="paused">Paused</SelectItem>
            <SelectItem value="completed">Completed</SelectItem>
            <SelectItem value="failed">Failed</SelectItem>
            <SelectItem value="cancelled">Cancelled</SelectItem>
          </SelectContent>
        </Select>
        {confirmDelete ? (
          <div className="flex items-center gap-1">
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
            className="text-zinc-600 hover:text-red-400 p-1.5 rounded hover:bg-zinc-800/50 transition-colors shrink-0"
            title="Delete all jobs"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* Job list */}
      <ScrollArea className="flex-1 min-h-0">
        <div className="p-2 space-y-1">
          {isLoading ? (
            <div className="text-zinc-500 text-sm text-center py-8">
              Loading...
            </div>
          ) : sortedJobs.length === 0 ? (
            <div className="text-zinc-500 text-sm text-center py-8">
              No jobs found
            </div>
          ) : (
            sortedJobs.map((job) => (
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
                        {job.objective || "Untitled Job"}
                      </div>
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
