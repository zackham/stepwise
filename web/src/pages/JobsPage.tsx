import { useState, useMemo } from "react";
import { useSearch, useNavigate } from "@tanstack/react-router";
import { CanvasPage } from "./CanvasPage";
import { List, LayoutGrid, Search, Terminal, Monitor, AlertTriangle, CirclePause } from "lucide-react";
import { cn, formatDuration } from "@/lib/utils";
import { useJobs } from "@/hooks/useStepwise";
import { JobStatusBadge } from "@/components/StatusBadge";
import { LiveDuration } from "@/components/LiveDuration";
import { EntityContextMenu } from "@/components/menus/EntityContextMenu";
import { ActionContextProvider } from "@/components/menus/ActionContextProvider";
import { CreateJobDialog } from "@/components/jobs/CreateJobDialog";
import { Input } from "@/components/ui/input";
import type { Job, JobStatus } from "@/lib/types";
import { JOB_STATUS_COLORS } from "@/lib/status-colors";

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
  paused: 2,
  pending: 3,
  staged: 4,
  completed: 5,
  failed: 6,
  cancelled: 7,
  archived: 8,
};

function JobListView() {
  const navigate = useNavigate();
  const { data: jobs = [], isLoading } = useJobs(undefined, true);
  const [filter, setFilter] = useState("");

  const filtered = useMemo(() => {
    let result = filter
      ? jobs.filter(
          (j) =>
            (j.name || "").toLowerCase().includes(filter.toLowerCase()) ||
            (j.objective || "").toLowerCase().includes(filter.toLowerCase())
        )
      : [...jobs];

    result.sort((a, b) => {
      const pa = STATUS_ORDER[a.status] ?? 99;
      const pb = STATUS_ORDER[b.status] ?? 99;
      if (pa !== pb) return pa - pb;
      return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
    });
    return result;
  }, [jobs, filter]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500 text-sm">
        Loading jobs...
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-full px-4 max-w-sm mx-auto text-center">
        <img src="/stepwise-icon-64.png" alt="Stepwise" className="w-12 h-12 opacity-40 mb-3" />
        <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400 mb-1">No jobs yet</p>
        <p className="text-xs text-zinc-500 dark:text-zinc-600 mb-4">
          Create a job from a flow or the CLI.
        </p>
      </div>
    );
  }

  const stepCount = (job: Job) => Object.keys(job.workflow.steps).length;

  return (
    <ActionContextProvider>
      <div className="flex-1 flex flex-col min-h-0">
        {/* Search bar */}
        <div className="flex items-center gap-3 px-4 sm:px-6 py-3 border-b border-border shrink-0">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-zinc-500" />
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Search jobs..."
              className="pl-8 h-8 text-sm bg-zinc-50 dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700"
            />
          </div>
          <div className="flex-1" />
          <CreateJobDialog
            onCreated={(jobId) =>
              navigate({ to: "/jobs/$jobId", params: { jobId } })
            }
          />
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {filtered.length === 0 ? (
            <div className="flex items-center justify-center h-32 text-xs text-zinc-500">
              No matching jobs
            </div>
          ) : (
            <div className="divide-y divide-border">
              {filtered.map((job) => (
                <EntityContextMenu key={job.id} type="job" data={job}>
                  <button
                    onClick={() =>
                      navigate({
                        to: "/jobs/$jobId",
                        params: { jobId: job.id },
                      })
                    }
                    className="w-full text-left px-4 sm:px-6 py-3 flex items-start gap-3 transition-colors hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40 group"
                  >
                    {/* Status dot */}
                    <span
                      className={cn(
                        "w-2.5 h-2.5 rounded-full mt-1.5 shrink-0",
                        JOB_STATUS_COLORS[job.status as JobStatus]?.dot ?? "bg-zinc-400",
                        job.status === "running" && "animate-pulse"
                      )}
                    />

                    {/* Name + objective */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium text-foreground group-hover:text-blue-500 dark:group-hover:text-blue-400 truncate transition-colors">
                          {job.name || job.objective || "Untitled Job"}
                        </span>
                        <JobStatusBadge status={job.status} />
                        {isStale(job) && (
                          <span className="flex items-center gap-0.5 text-amber-500 text-[10px]">
                            <AlertTriangle className="w-3 h-3" />
                          </span>
                        )}
                        {job.has_suspended_steps && (
                          <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-500/15 text-amber-400 ring-1 ring-amber-500/30">
                            <CirclePause className="w-2.5 h-2.5" />
                          </span>
                        )}
                      </div>
                      {/* Second line: objective + current step */}
                      <div className="text-xs text-zinc-500 dark:text-zinc-500 truncate mt-0.5">
                        {job.name && job.objective ? job.objective : null}
                        {job.current_step && (
                          <span className={cn(
                            job.name && job.objective && "ml-1",
                            job.current_step.status === "running" && "text-blue-400",
                            job.current_step.status === "failed" && "text-red-400",
                          )}>
                            {job.name && job.objective ? " · " : ""}
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
                    </div>

                    {/* Right columns */}
                    <div className="hidden sm:flex items-center gap-6 shrink-0 text-[11px] text-zinc-500 dark:text-zinc-500 tabular-nums">
                      <span className="w-12 text-right">
                        {stepCount(job)} step{stepCount(job) !== 1 ? "s" : ""}
                      </span>
                      <span className="w-16 text-right">
                        {job.status === "staged" || job.status === "pending"
                          ? "—"
                          : formatDuration(job.created_at, job.updated_at)}
                      </span>
                      <span className="w-4 text-right">
                        {isCliOwned(job.created_by) ? (
                          <Terminal className="w-3 h-3 inline" />
                        ) : (
                          <Monitor className="w-3 h-3 inline" />
                        )}
                      </span>
                      <span className="w-16 text-right">{timeAgo(job.updated_at)}</span>
                    </div>
                  </button>
                </EntityContextMenu>
              ))}
            </div>
          )}
        </div>
      </div>
    </ActionContextProvider>
  );
}

export function JobsPage() {
  const searchParams = useSearch({ from: "/jobs" });
  const navigate = useNavigate();
  const viewMode = searchParams.view_mode ?? "grid";

  const setViewMode = (mode: "list" | "grid") => {
    navigate({
      search: (prev: Record<string, unknown>) => ({
        ...prev,
        view_mode: mode === "grid" ? undefined : mode,
      }),
      replace: true,
    });
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center px-4 py-2 border-b border-border shrink-0">
        <div className="flex items-center gap-1 rounded-lg border border-border p-0.5 bg-zinc-100/50 dark:bg-zinc-900/50">
          <button
            onClick={() => setViewMode("list")}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors",
              viewMode === "list"
                ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                : "text-zinc-500 hover:text-foreground"
            )}
          >
            <List className="w-3.5 h-3.5" />
            List
          </button>
          <button
            onClick={() => setViewMode("grid")}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors",
              viewMode === "grid"
                ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                : "text-zinc-500 hover:text-foreground"
            )}
          >
            <LayoutGrid className="w-3.5 h-3.5" />
            Grid
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {viewMode === "grid" ? <CanvasPage /> : <JobListView />}
      </div>
    </div>
  );
}
