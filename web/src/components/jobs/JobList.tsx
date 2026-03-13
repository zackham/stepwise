import { useState } from "react";
import { useJobs } from "@/hooks/useStepwise";
import { JobStatusBadge } from "@/components/StatusBadge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Briefcase, Clock } from "lucide-react";
import { cn } from "@/lib/utils";
import type { JobStatus } from "@/lib/types";

interface JobListProps {
  selectedJobId: string | null;
  onSelectJob: (jobId: string) => void;
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
  const { data: jobs = [], isLoading } = useJobs(
    statusFilter === "all" ? undefined : statusFilter
  );

  const sortedJobs = [...jobs].sort(
    (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
  );

  return (
    <div className="flex flex-col h-full">
      {/* Filter */}
      <div className="p-3 border-b border-border">
        <Select value={statusFilter} onValueChange={(v) => { if (v !== null) setStatusFilter(v); }}>
          <SelectTrigger className="h-8 text-xs">
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
                    <JobStatusBadge status={job.status} />
                    <span className="text-[10px] text-zinc-600 flex items-center gap-0.5">
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
