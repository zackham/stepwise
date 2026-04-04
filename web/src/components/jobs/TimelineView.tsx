import { useMemo, useState, useEffect, useRef } from "react";
import type { StepRun, Job, StepRunStatus } from "@/lib/types";
import { STEP_STATUS_COLORS } from "@/lib/status-colors";
import { cn, formatCost } from "@/lib/utils";
import { LiveDuration } from "@/components/LiveDuration";

interface TimelineViewProps {
  job: Job;
  runs: StepRun[];
  onSelectStep?: (stepName: string) => void;
}

interface TimelineRow {
  run: StepRun;
  startPct: number;
  widthPct: number;
  durationMs: number;
  isRunning: boolean;
}

// Solid bar colors for each status (no opacity — these are the actual bar fills)
const BAR_COLORS: Record<StepRunStatus, string> = {
  running: "bg-blue-500",
  completed: "bg-emerald-500",
  failed: "bg-red-500",
  suspended: "bg-amber-500",
  delegated: "bg-purple-500",
  cancelled: "bg-zinc-500",
  skipped: "bg-zinc-600",
  throttled: "bg-orange-500",
  waiting_reset: "bg-amber-600",
};

const BAR_GLOW: Record<StepRunStatus, string> = {
  running: "shadow-[0_0_8px_rgba(59,130,246,0.4)]",
  completed: "",
  failed: "shadow-[0_0_6px_rgba(239,68,68,0.3)]",
  suspended: "shadow-[0_0_6px_rgba(245,158,11,0.3)]",
  delegated: "shadow-[0_0_6px_rgba(168,85,247,0.3)]",
  cancelled: "",
  skipped: "",
  throttled: "shadow-[0_0_6px_rgba(249,115,22,0.3)]",
  waiting_reset: "shadow-[0_0_6px_rgba(217,119,6,0.3)]",
};

function formatMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3_600_000) return `${(ms / 60_000).toFixed(1)}m`;
  return `${(ms / 3_600_000).toFixed(1)}h`;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

// Generate nice time axis ticks
function generateTicks(rangeMs: number, count: number): number[] {
  if (rangeMs <= 0) return [0];
  const raw = rangeMs / count;
  // Snap to nice intervals
  const niceIntervals = [100, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000, 120000, 300000, 600000, 1800000, 3600000];
  const interval = niceIntervals.find((n) => n >= raw) ?? raw;
  const ticks: number[] = [];
  for (let t = 0; t <= rangeMs; t += interval) {
    ticks.push(t);
  }
  return ticks;
}

function executorLabel(type: string): string {
  switch (type) {
    case "agent": return "Agent";
    case "llm": return "LLM";
    case "script": return "Script";
    case "external": return "External";
    case "poll": return "Poll";
    default: return type;
  }
}

export function TimelineView({ job, runs, onSelectStep }: TimelineViewProps) {
  const [hoveredRun, setHoveredRun] = useState<string | null>(null);
  const [hoverOffsetPx, setHoverOffsetPx] = useState(0); // mouse X offset within bar, in px
  const [now, setNow] = useState(Date.now());
  const containerRef = useRef<HTMLDivElement>(null);

  const jobIsRunning = job.status === "running" || job.status === "paused";

  // Tick for running bars
  useEffect(() => {
    if (!jobIsRunning) return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [jobIsRunning]);

  const { groups, timeOrigin, rangeMs, ticks } = useMemo(() => {
    // Only show runs that have started
    const startedRuns = runs.filter((r) => r.started_at);
    if (startedRuns.length === 0) {
      return { groups: [], timeOrigin: 0, rangeMs: 0, ticks: [0] };
    }

    const starts = startedRuns.map((r) => new Date(r.started_at!).getTime());
    const origin = Math.min(...starts);

    // End time: latest completed_at, or now for running jobs
    const ends = startedRuns.map((r) => {
      if (r.completed_at) return new Date(r.completed_at).getTime();
      if (r.status === "running") return now;
      return new Date(r.started_at!).getTime();
    });
    const maxEnd = Math.max(...ends);
    const range = Math.max(maxEnd - origin, 100); // min 100ms to avoid div-by-zero

    // Group runs by step name, preserving order of first appearance
    const stepOrder: string[] = [];
    const stepMap = new Map<string, StepRun[]>();
    for (const r of startedRuns) {
      if (!stepMap.has(r.step_name)) {
        stepOrder.push(r.step_name);
        stepMap.set(r.step_name, []);
      }
      stepMap.get(r.step_name)!.push(r);
    }

    // Sort runs within each group by attempt
    for (const group of stepMap.values()) {
      group.sort((a, b) => a.attempt - b.attempt);
    }

    const grouped = stepOrder.map((name) => ({
      stepName: name,
      rows: stepMap.get(name)!.map((run): TimelineRow => {
        const s = new Date(run.started_at!).getTime();
        const e = run.completed_at
          ? new Date(run.completed_at).getTime()
          : run.status === "running"
            ? now
            : s;
        return {
          run,
          startPct: ((s - origin) / range) * 100,
          widthPct: Math.max(((e - s) / range) * 100, 0.3), // min bar width
          durationMs: e - s,
          isRunning: run.status === "running",
        };
      }),
    }));

    return {
      groups: grouped,
      timeOrigin: origin,
      rangeMs: range,
      ticks: generateTicks(range, 5),
    };
  }, [runs, now]);

  if (groups.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-600 text-sm">
        No step runs yet
      </div>
    );
  }

  return (
    <div ref={containerRef} className="flex flex-col h-full overflow-auto">
      {/* Time axis header */}
      <div className="flex border-b border-zinc-200 dark:border-zinc-800 bg-zinc-50/50 dark:bg-zinc-950/50 sticky top-0 z-10">
        <div className="w-40 shrink-0 px-3 py-1.5 text-[10px] text-zinc-500 dark:text-zinc-600 uppercase tracking-wide border-r border-zinc-200 dark:border-zinc-800">
          Step
        </div>
        <div className="flex-1 relative h-7">
          {ticks.map((t) => (
            <div
              key={t}
              className="absolute top-0 h-full flex items-end pb-1"
              style={{ left: `${(t / rangeMs) * 100}%` }}
            >
              <span className="text-[9px] text-zinc-600 font-mono -translate-x-1/2">
                {formatMs(t)}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Rows */}
      <div className="flex-1">
        {groups.map(({ stepName, rows }, groupIdx) => (
          <div key={stepName} className="flex border-b border-zinc-200/50 dark:border-zinc-800/50 hover:bg-zinc-100/30 dark:hover:bg-zinc-900/30">
            {/* Step label */}
            <div
              className="w-40 shrink-0 px-3 py-2 border-r border-zinc-200 dark:border-zinc-800 cursor-pointer"
              onClick={() => onSelectStep?.(stepName)}
            >
              <span className="text-xs text-zinc-700 dark:text-zinc-300 truncate block" title={stepName}>
                {stepName}
              </span>
              {(() => {
                const stepDef = job.workflow.steps[stepName];
                if (!stepDef) return null;
                const parts: string[] = [executorLabel(stepDef.executor.type)];
                if (stepDef.executor.type === "llm") {
                  const model = stepDef.executor.config?.model as string | undefined;
                  if (model) parts.push(model);
                }
                if (stepDef.executor.type === "agent") {
                  const agent = stepDef.executor.config?.agent as string | undefined;
                  if (agent && agent !== "claude") parts.push(agent);
                }
                if (stepDef.session) parts.push(`session: ${stepDef.session}`);
                return (
                  <span className="text-[10px] text-zinc-500 truncate block">
                    {parts.join(" · ")}
                  </span>
                );
              })()}
            </div>

            {/* Bars area */}
            <div className="flex-1 relative">
              {/* Tick grid lines */}
              {ticks.map((t) => (
                <div
                  key={t}
                  className="absolute top-0 bottom-0 w-px bg-zinc-300/40 dark:bg-zinc-800/40"
                  style={{ left: `${(t / rangeMs) * 100}%` }}
                />
              ))}

              {rows.map((row) => {
                const isHovered = hoveredRun === row.run.id;
                return (
                  <div
                    key={row.run.id}
                    className="relative py-1.5 px-1"
                  >
                    <div
                      className={cn(
                        "relative h-6 rounded-sm cursor-pointer transition-opacity",
                        BAR_COLORS[row.run.status],
                        BAR_GLOW[row.run.status],
                        isHovered ? "opacity-100" : "opacity-80",
                        row.isRunning && "animate-pulse",
                      )}
                      style={{
                        marginLeft: `${row.startPct}%`,
                        width: `${row.widthPct}%`,
                        minWidth: "4px",
                      }}
                      onMouseEnter={(e) => {
                        setHoveredRun(row.run.id);
                        const barRect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                        setHoverOffsetPx(e.clientX - barRect.left);
                      }}
                      onMouseMove={(e) => {
                        const barRect = (e.currentTarget as HTMLElement).getBoundingClientRect();
                        setHoverOffsetPx(e.clientX - barRect.left);
                      }}
                      onMouseLeave={() => setHoveredRun(null)}
                      onClick={() => onSelectStep?.(stepName)}
                    >
                      {/* Bar label — show inside if wide enough */}
                      <div className="absolute inset-0 flex items-center px-1.5 overflow-hidden">
                        <span className="text-[10px] text-white/90 font-medium truncate whitespace-nowrap drop-shadow-sm">
                          {row.run.attempt > 1 && `#${row.run.attempt} · `}
                          <LiveDuration startTime={row.run.started_at} endTime={row.run.completed_at} />
                        </span>
                      </div>

                      {/* Tooltip — positioned above bar */}
                      {isHovered && (() => {
                        const stepDef = job.workflow.steps[stepName];
                        const execType = stepDef?.executor.type;
                        const model = execType === "llm" ? (stepDef?.executor.config?.model as string | undefined) : null;
                        const agent = execType === "agent" ? (stepDef?.executor.config?.agent as string | undefined) : null;
                        const costUsd = row.run.result?.executor_meta?.cost_usd as number | undefined;
                        return (
                          <div
                            className={cn(
                              "absolute z-20 bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-md px-3 py-2 shadow-xl pointer-events-none whitespace-nowrap -translate-x-1/2",
                              groupIdx === 0 ? "top-full mt-2" : "bottom-full mb-2",
                            )}
                            style={{ left: `${hoverOffsetPx}px` }}
                          >
                            <div className="text-xs font-medium text-zinc-800 dark:text-zinc-200">
                              {stepName}
                              {row.run.attempt > 1 && <span className="text-zinc-500 ml-1">(attempt {row.run.attempt})</span>}
                            </div>
                            <div className="flex items-center gap-3 mt-1 text-[10px]">
                              <span className={STEP_STATUS_COLORS[row.run.status].text}>{row.run.status}</span>
                              <span className="text-zinc-500">
                                <LiveDuration startTime={row.run.started_at} endTime={row.run.completed_at} />
                              </span>
                              {execType && <span className="text-zinc-600">{executorLabel(execType)}</span>}
                              {model && <span className="text-zinc-600">{model}</span>}
                              {agent && agent !== "claude" && <span className="text-zinc-600">{agent}</span>}
                              {costUsd != null && costUsd > 0 && (
                                <span className="text-emerald-400 font-mono">{formatCost(costUsd)}</span>
                              )}
                            </div>
                            {stepDef?.session && (
                              <div className="text-[10px] text-zinc-500 mt-0.5">session: {stepDef.session}</div>
                            )}
                            {row.run.started_at && (
                              <div className="text-[10px] text-zinc-600 mt-0.5">
                                {formatTime(row.run.started_at)}
                                {row.run.completed_at && <> → {formatTime(row.run.completed_at)}</>}
                              </div>
                            )}
                            {row.run.error && (
                              <div className="text-[10px] text-red-400 mt-1 max-w-xs truncate">
                                {row.run.error.split("\n")[0]}
                              </div>
                            )}
                          </div>
                        );
                      })()}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
