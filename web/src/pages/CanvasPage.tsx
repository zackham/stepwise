import { useState, useMemo, useCallback } from "react";
import { useQueries } from "@tanstack/react-query";
import { useJobs, useGroups, useStepwiseMutations } from "@/hooks/useStepwise";
import { JobCard } from "@/components/canvas/JobCard";
import { DependencyArrows } from "@/components/canvas/DependencyArrows";
import { computeCanvasLayout } from "@/components/canvas/CanvasLayout";
import { fetchRuns } from "@/lib/api";
import { Eye, EyeOff, Minus, Plus } from "lucide-react";
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

export function CanvasPage() {
  const { data: jobs = [], isLoading } = useJobs(undefined, true);
  const { data: groups = [] } = useGroups();
  const { updateGroupLimit } = useStepwiseMutations();
  const [hideCompleted, setHideCompleted] = useState(false);

  // Build group_name -> max_concurrent map for layout
  const groupSettings = useMemo(() => {
    const map: Record<string, number> = {};
    for (const g of groups) {
      map[g.group] = g.max_concurrent;
    }
    return map;
  }, [groups]);

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

  // Filter jobs
  const visibleJobs = useMemo(() => {
    if (hideCompleted) return jobs.filter((j) => j.status !== "completed");
    return jobs;
  }, [jobs, hideCompleted]);

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
    for (const job of jobs) {
      map.set(job.id, job.name || job.objective);
    }
    return map;
  }, [jobs]);

  // Partition visibleJobs into dependent (edges) and independent (no edges)
  const { dependentJobs, independentJobs } = useMemo(() => {
    const visibleIds = new Set(visibleJobs.map((j) => j.id));
    const edgeParticipants = new Set<string>();

    for (const job of visibleJobs) {
      // depends_on edges
      for (const depId of job.depends_on ?? []) {
        if (visibleIds.has(depId)) {
          edgeParticipants.add(job.id);
          edgeParticipants.add(depId);
        }
      }
      // parent_job_id as fallback edge
      if (job.parent_job_id && visibleIds.has(job.parent_job_id)) {
        edgeParticipants.add(job.id);
        edgeParticipants.add(job.parent_job_id);
      }
    }

    const dependent: Job[] = [];
    const independent: Job[] = [];
    for (const job of visibleJobs) {
      if (edgeParticipants.has(job.id)) {
        dependent.push(job);
      } else {
        independent.push(job);
      }
    }
    return { dependentJobs: dependent, independentJobs: independent };
  }, [visibleJobs]);

  // Compute dagre layout for dependent jobs only
  const layout = useMemo(() => computeCanvasLayout(dependentJobs, groupSettings), [dependentJobs, groupSettings]);

  // Sort independent jobs by status priority then recency
  const sortedIndependentJobs = useMemo(() => {
    return [...independentJobs].sort((a, b) => {
      const pa = STATUS_PRIORITY[a.status] ?? 99;
      const pb = STATUS_PRIORITY[b.status] ?? 99;
      if (pa !== pb) return pa - pb;
      // Recency tiebreaker: newer first
      return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
    });
  }, [independentJobs]);

  // Group independent jobs by job_group
  const { grouped, ungrouped } = useMemo(() => {
    const groupMap = new Map<string, Job[]>();
    const ungrouped: Job[] = [];
    for (const job of sortedIndependentJobs) {
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
  }, [sortedIndependentJobs]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500 text-sm">
        Loading jobs...
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500 text-sm">
        No jobs yet. Create one from the Jobs page.
      </div>
    );
  }

  const renderCard = (job: Job) => (
    <div key={job.id} className="min-w-0">
      <JobCard
        job={job}
        runs={runsMap.get(job.id) ?? []}
        dependencyNames={
          job.depends_on
            ?.map((id) => jobNameMap.get(id))
            .filter(Boolean) as string[] | undefined
        }
        isGroupQueued={groupQueuedSet.has(job.id)}
      />
    </div>
  );

  return (
    <div className="h-full overflow-y-auto">
      {/* Toolbar */}
      <div className="sticky top-0 z-10 flex items-center justify-end px-6 py-3 bg-white/80 dark:bg-zinc-950/80 backdrop-blur-sm border-b border-zinc-200 dark:border-zinc-800/50">
        <button
          onClick={() => setHideCompleted(!hideCompleted)}
          className={cn(
            "flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-md border transition-colors",
            hideCompleted
              ? "bg-zinc-200 dark:bg-zinc-800 border-zinc-300 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300"
              : "bg-white/80 dark:bg-zinc-900/80 border-zinc-300 dark:border-zinc-800 text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300",
          )}
          title={hideCompleted ? "Show completed jobs" : "Hide completed jobs"}
        >
          {hideCompleted ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
          <span className="hidden sm:inline">{hideCompleted ? "Show done" : "Hide done"}</span>
        </button>
      </div>

      {/* Content */}
      <div className="p-6 space-y-8">
        {/* Zone A: DAG layout for jobs with dependency edges */}
        {dependentJobs.length > 0 && (
          <section>
            <div
              className="relative"
              style={{ width: layout.width, height: layout.height }}
            >
              <DependencyArrows
                edges={layout.edges}
                width={layout.width}
                height={layout.height}
              />
              {/* Group clusters */}
              {layout.groups.map((group) => (
                <div
                  key={group.label}
                  className="absolute rounded-xl border border-dashed border-zinc-300/40 dark:border-zinc-800/40 bg-zinc-100/10 dark:bg-zinc-900/10"
                  style={{
                    left: group.x,
                    top: group.y,
                    width: group.width,
                    height: group.height,
                  }}
                >
                  <div className="px-3 pt-1.5 flex items-center gap-2">
                    <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
                      {group.label}
                    </span>
                    <span className="text-[10px] text-zinc-500 dark:text-zinc-600">
                      {group.completedCount}/{group.totalCount}
                    </span>
                    {group.maxConcurrent > 0 && (
                      <span className="text-[10px] text-zinc-400 dark:text-zinc-600">
                        · {group.activeCount}/{group.maxConcurrent} running
                      </span>
                    )}
                    <div className="flex items-center gap-0.5 ml-auto">
                      <button
                        onClick={() => handleUpdateLimit(group.label, group.maxConcurrent - 1)}
                        className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors"
                        title="Decrease concurrency limit"
                      >
                        <Minus className="w-3 h-3" />
                      </button>
                      <span className="text-[10px] text-zinc-500 dark:text-zinc-400 min-w-[20px] text-center">
                        {group.maxConcurrent || "∞"}
                      </span>
                      <button
                        onClick={() => handleUpdateLimit(group.label, group.maxConcurrent + 1)}
                        className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors"
                        title="Increase concurrency limit"
                      >
                        <Plus className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
              {/* Job cards */}
              {layout.cards.map((card) => {
                const job = dependentJobs.find((j) => j.id === card.jobId);
                if (!job) return null;
                return (
                  <div
                    key={card.jobId}
                    className="absolute"
                    style={{
                      left: card.x,
                      top: card.y,
                      width: card.width,
                    }}
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
                    />
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {/* Zone B: CSS grid for independent jobs (no dependency edges) */}
        {grouped.map(([groupLabel, groupJobs]) => {
          const completedCount = groupJobs.filter((j) => j.status === "completed").length;
          const gInfo = groupInfoMap.get(groupLabel);
          return (
            <section key={groupLabel}>
              <div className="mb-3 flex items-center gap-2">
                <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-300">{groupLabel}</h2>
                <span className="text-xs text-zinc-400 dark:text-zinc-600">
                  {completedCount}/{groupJobs.length} complete
                </span>
                {gInfo && gInfo.max_concurrent > 0 && (
                  <span className="text-xs text-zinc-400 dark:text-zinc-600">
                    · {gInfo.active_count}/{gInfo.max_concurrent} running
                  </span>
                )}
                <div className="flex items-center gap-0.5 ml-auto">
                  <button
                    onClick={() => handleUpdateLimit(groupLabel, (gInfo?.max_concurrent ?? 0) - 1)}
                    className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors"
                    title="Decrease concurrency limit"
                  >
                    <Minus className="w-3 h-3" />
                  </button>
                  <span className="text-[10px] text-zinc-500 dark:text-zinc-400 min-w-[20px] text-center">
                    {gInfo?.max_concurrent || "∞"}
                  </span>
                  <button
                    onClick={() => handleUpdateLimit(groupLabel, (gInfo?.max_concurrent ?? 0) + 1)}
                    className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300 transition-colors"
                    title="Increase concurrency limit"
                  >
                    <Plus className="w-3 h-3" />
                  </button>
                </div>
              </div>
              <div className="rounded-xl border border-dashed border-zinc-300/60 dark:border-zinc-800/60 bg-zinc-100/20 dark:bg-zinc-900/20 p-4">
                <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">
                  {groupJobs.map(renderCard)}
                </div>
              </div>
            </section>
          );
        })}

        {ungrouped.length > 0 && (
          <section>
            {(grouped.length > 0 || dependentJobs.length > 0) && (
              <h2 className="mb-3 text-sm font-medium text-zinc-500 dark:text-zinc-400">
                {dependentJobs.length > 0 ? "Independent jobs" : "Other jobs"}
              </h2>
            )}
            <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-4">
              {ungrouped.map(renderCard)}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
