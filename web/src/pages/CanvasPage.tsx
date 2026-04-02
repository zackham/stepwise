import { useMemo, useCallback, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { useGroups, useStepwiseMutations } from "@/hooks/useStepwise";
import { JobCard } from "@/components/canvas/JobCard";
import { fetchRuns } from "@/lib/api";
import { Archive, ChevronRight, Minus, Plus, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Job, StepRun } from "@/lib/types";

const STATUS_PRIORITY: Record<string, number> = {
  running: 0,
  paused: 1,
  pending: 2,
  staged: 3,
  failed: 4,
  completed: 5,
  cancelled: 6,
  archived: 7,
};

const ACTIVE_STATUSES = new Set(["running", "paused", "pending", "staged", "awaiting_input", "awaiting_approval"]);

export interface CanvasPageProps {
  jobs: Job[];
}

export function CanvasPage({ jobs: visibleJobs }: CanvasPageProps) {
  const { data: groups = [] } = useGroups();
  const { updateGroupLimit, archiveJobs } = useStepwiseMutations();
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const [hoveredJobId, setHoveredJobId] = useState<string | null>(null);

  const toggleGroup = useCallback((group: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(group)) {
        next.delete(group);
      } else {
        next.add(group);
      }
      return next;
    });
  }, []);

  // Build group_name -> GroupInfo lookup for rendering
  const groupInfoMap = useMemo(() => {
    const map = new Map<string, { max_concurrent: number; active_count: number; pending_count: number }>();
    for (const g of groups) {
      map.set(g.group, { max_concurrent: g.max_concurrent, active_count: g.active_count, pending_count: g.pending_count });
    }
    return map;
  }, [groups]);

  const handleUpdateLimit = useCallback((group: string, newLimit: number) => {
    updateGroupLimit.mutate({ group, maxConcurrent: Math.max(0, newLimit) });
  }, [updateGroupLimit]);

  // Fetch runs for all visible jobs
  const runsQueries = useQueries({
    queries: visibleJobs.map((job) => ({
      queryKey: ["runs", job.id, undefined],
      queryFn: () => fetchRuns(job.id),
      staleTime: 5_000,
    })),
  });

  // Build jobId -> runs map
  const runsMap = useMemo(() => {
    const map = new Map<string, StepRun[]>();
    visibleJobs.forEach((job, i) => {
      map.set(job.id, runsQueries[i]?.data ?? []);
    });
    return map;
  }, [visibleJobs, runsQueries]);

  // Compute which PENDING jobs are queued due to group concurrency limit
  const groupQueuedSet = useMemo(() => {
    const set = new Set<string>();
    for (const g of groups) {
      if (g.max_concurrent > 0 && g.active_count >= g.max_concurrent) {
        for (const job of visibleJobs) {
          if (job.job_group === g.group && job.status === "pending") {
            set.add(job.id);
          }
        }
      }
    }
    return set;
  }, [groups, visibleJobs]);

  // Build job name lookup for dependency text
  const jobNameMap = useMemo(() => {
    const map = new Map<string, string>();
    for (const job of visibleJobs) {
      map.set(job.id, job.name || job.objective);
    }
    return map;
  }, [visibleJobs]);

  // Build dependency maps: dependsOn[jobId] = set of job IDs it depends on,
  // dependedBy[jobId] = set of job IDs that depend on it
  const { dependsOnMap, dependedByMap } = useMemo(() => {
    const dependsOn = new Map<string, Set<string>>();
    const dependedBy = new Map<string, Set<string>>();
    for (const job of visibleJobs) {
      if (job.depends_on && job.depends_on.length > 0) {
        dependsOn.set(job.id, new Set(job.depends_on));
        for (const depId of job.depends_on) {
          if (!dependedBy.has(depId)) dependedBy.set(depId, new Set());
          dependedBy.get(depId)!.add(job.id);
        }
      }
    }
    return { dependsOnMap: dependsOn, dependedByMap: dependedBy };
  }, [visibleJobs]);

  // Compute highlight state: only highlight upstream dependencies (parents)
  const highlightMap = useMemo(() => {
    const map = new Map<string, "dependency">();
    if (!hoveredJobId) return map;
    const deps = dependsOnMap.get(hoveredJobId);
    if (deps) {
      for (const id of deps) map.set(id, "dependency");
    }
    return map;
  }, [hoveredJobId, dependsOnMap, dependedByMap]);

  // Sort all jobs by status priority then recency
  const sortedJobs = useMemo(() => {
    // Topological sort: parents before children, then by status priority + recency
    const jobIds = new Set(visibleJobs.map((j) => j.id));
    const depCount = new Map<string, number>();
    const children = new Map<string, string[]>();
    for (const job of visibleJobs) {
      depCount.set(job.id, 0);
    }
    for (const job of visibleJobs) {
      for (const depId of job.depends_on ?? []) {
        if (jobIds.has(depId)) {
          depCount.set(job.id, (depCount.get(job.id) ?? 0) + 1);
          if (!children.has(depId)) children.set(depId, []);
          children.get(depId)!.push(job.id);
        }
      }
    }
    // BFS topological order
    const queue = visibleJobs.filter((j) => (depCount.get(j.id) ?? 0) === 0);
    queue.sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
    const result: Job[] = [];
    const jobMap = new Map(visibleJobs.map((j) => [j.id, j]));
    const visited = new Set<string>();
    let i = 0;
    while (i < queue.length) {
      const job = queue[i++];
      if (visited.has(job.id)) continue;
      visited.add(job.id);
      result.push(job);
      for (const childId of children.get(job.id) ?? []) {
        const count = (depCount.get(childId) ?? 1) - 1;
        depCount.set(childId, count);
        if (count === 0) {
          const child = jobMap.get(childId);
          if (child) queue.push(child);
        }
      }
    }
    // Add any remaining (cycles) at the end
    for (const job of visibleJobs) {
      if (!visited.has(job.id)) result.push(job);
    }
    return result;
  }, [visibleJobs]);

  // Group ALL jobs by job_group
  const { grouped, ungrouped } = useMemo(() => {
    const groupMap = new Map<string, Job[]>();
    const ungrouped: Job[] = [];
    for (const job of sortedJobs) {
      if (job.job_group) {
        if (!groupMap.has(job.job_group)) groupMap.set(job.job_group, []);
        groupMap.get(job.job_group)!.push(job);
      } else {
        ungrouped.push(job);
      }
    }
    return {
      grouped: Array.from(groupMap.entries()),
      ungrouped,
    };
  }, [sortedJobs]);

  // Separate ungrouped into active and terminal for visual separator
  const { ungroupedActive, ungroupedTerminal } = useMemo(() => {
    const active: Job[] = [];
    const terminal: Job[] = [];
    for (const job of ungrouped) {
      if (ACTIVE_STATUSES.has(job.status)) {
        active.push(job);
      } else {
        terminal.push(job);
      }
    }
    return { ungroupedActive: active, ungroupedTerminal: terminal };
  }, [ungrouped]);

  if (visibleJobs.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500 text-sm">
        No matching jobs
      </div>
    );
  }

  const renderCard = (job: Job) => (
    <div
      key={job.id}
      className={cn(
        "min-w-0",
      )}
    >
      <JobCard
        job={job}
        runs={runsMap.get(job.id) ?? []}
        dependencyNames={
          job.depends_on
            ?.map((id) => jobNameMap.get(id))
            .filter(Boolean) as string[] | undefined
        }
        isGroupQueued={groupQueuedSet.has(job.id)}
        highlightAs={highlightMap.get(job.id) ?? null}
        onMouseEnter={() => setHoveredJobId(job.id)}
        onMouseLeave={() => setHoveredJobId(null)}
      />
    </div>
  );

  return (
    <div className="h-full overflow-y-auto">
      <div className="p-6 space-y-6">
        {/* Grouped job sections */}
        {grouped.map(([groupLabel, groupJobs]) => {
          const completedCount = groupJobs.filter((j) => j.status === "completed").length;
          const gInfo = groupInfoMap.get(groupLabel);
          const isCollapsed = collapsedGroups.has(groupLabel);
          return (
            <section key={groupLabel}>
              <button
                onClick={() => toggleGroup(groupLabel)}
                className="w-full mb-3 flex items-center gap-2 text-left group"
              >
                <ChevronRight
                  className={cn(
                    "w-4 h-4 text-zinc-400 transition-transform",
                    !isCollapsed && "rotate-90",
                  )}
                />
                <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-300">{groupLabel}</h2>
                <span className="text-xs text-zinc-400 dark:text-zinc-600">
                  {completedCount}/{groupJobs.length} complete
                </span>
                {gInfo && gInfo.max_concurrent > 0 && (
                  <span className="text-xs text-zinc-400 dark:text-zinc-600">
                    · {gInfo.active_count}/{gInfo.max_concurrent} running
                  </span>
                )}
                <div
                  className="flex items-center gap-1.5 ml-auto"
                  onClick={(e) => e.stopPropagation()}
                >
                  {gInfo && gInfo.max_concurrent > 0 ? (
                    <span className="flex items-center gap-0.5 text-[10px] text-zinc-500 dark:text-zinc-400">
                      <span className="mr-0.5">Limit: {gInfo.max_concurrent}</span>
                      <button
                        onClick={() => handleUpdateLimit(groupLabel, gInfo.max_concurrent - 1)}
                        className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors"
                        title="Decrease concurrency limit"
                      >
                        <Minus className="w-3 h-3" />
                      </button>
                      <button
                        onClick={() => handleUpdateLimit(groupLabel, gInfo.max_concurrent + 1)}
                        className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors"
                        title="Increase concurrency limit"
                      >
                        <Plus className="w-3 h-3" />
                      </button>
                      <button
                        onClick={() => handleUpdateLimit(groupLabel, 0)}
                        className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors"
                        title="Remove concurrency limit"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </span>
                  ) : (
                    <button
                      onClick={() => handleUpdateLimit(groupLabel, 1)}
                      className="text-[10px] text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors"
                    >
                      Set concurrency limit
                    </button>
                  )}
                  <button
                    onClick={() => {
                      const ids = groupJobs
                        .filter((j) => j.status === "completed" || j.status === "failed" || j.status === "cancelled")
                        .map((j) => j.id);
                      if (ids.length > 0) archiveJobs.mutate(ids);
                    }}
                    className="flex items-center gap-1 p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors text-[10px] ml-1"
                    title="Archive completed jobs in this group"
                  >
                    <Archive className="w-3 h-3" />
                    <span>Archive</span>
                  </button>
                </div>
              </button>
              {!isCollapsed && (
                <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">
                  {groupJobs.map(renderCard)}
                </div>
              )}
            </section>
          );
        })}

        {/* Ungrouped jobs: "Other jobs" section */}
        {ungrouped.length > 0 && (
          <section>
            {grouped.length > 0 && (
              <button
                onClick={() => toggleGroup("__ungrouped__")}
                className="w-full mb-3 flex items-center gap-2 text-left group"
              >
                <ChevronRight
                  className={cn(
                    "w-4 h-4 text-zinc-400 transition-transform",
                    !collapsedGroups.has("__ungrouped__") && "rotate-90",
                  )}
                />
                <h2 className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
                  Other jobs
                </h2>
                <span className="text-xs text-zinc-400 dark:text-zinc-600">
                  {ungroupedTerminal.length}/{ungrouped.length} complete
                </span>
              </button>
            )}
            {!collapsedGroups.has("__ungrouped__") && (
              <>
                {/* Active ungrouped */}
                {ungroupedActive.length > 0 && (
                  <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">
                    {ungroupedActive.map(renderCard)}
                  </div>
                )}
                {/* Separator between active and terminal */}
                {ungroupedActive.length > 0 && ungroupedTerminal.length > 0 && (
                  <div className="flex items-center gap-3 my-4">
                    <div className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" />
                    <span className="text-[10px] uppercase tracking-wider text-zinc-400 dark:text-zinc-600 font-medium">
                      Completed
                    </span>
                    <div className="h-px flex-1 bg-zinc-200 dark:bg-zinc-800" />
                  </div>
                )}
                {/* Terminal ungrouped */}
                {ungroupedTerminal.length > 0 && (
                  <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">
                    {ungroupedTerminal.map(renderCard)}
                  </div>
                )}
              </>
            )}
          </section>
        )}
      </div>
    </div>
  );
}
