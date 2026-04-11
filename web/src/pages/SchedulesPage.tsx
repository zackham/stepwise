import { useState, useMemo, useCallback } from "react";
import { useNavigate } from "@tanstack/react-router";
import {
  Play,
  Pause,
  Trash2,
  Zap,
  Clock,
  Terminal,
  Timer,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { useSchedules, useScheduleMutations } from "@/hooks/useSchedules";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import type { Schedule, ScheduleStatus, ScheduleType } from "@/lib/schedule-types";
import cronstrue from "cronstrue";

// ── Helpers ────────────────────────────────────────────────────────────

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

function humanCron(expr: string, fallback?: string): string {
  if (!expr) return fallback || "-";
  try {
    return cronstrue.toString(expr, { use24HourTimeFormat: false });
  } catch {
    return fallback || expr;
  }
}

function flowNameFromPath(path: string): string {
  if (!path) return "-";
  const parts = path.split("/");
  return parts[parts.length - 1]?.replace(/\.ya?ml$/, "") || path;
}

const STATUS_COLORS: Record<ScheduleStatus, { dot: string; bg: string; text: string; ring: string }> = {
  active: {
    dot: "bg-emerald-400",
    bg: "bg-emerald-100 dark:bg-emerald-500/10",
    text: "text-emerald-600 dark:text-emerald-400",
    ring: "ring-emerald-500/30",
  },
  paused: {
    dot: "bg-zinc-400",
    bg: "bg-zinc-100 dark:bg-zinc-500/10",
    text: "text-zinc-500 dark:text-zinc-400",
    ring: "ring-zinc-500/30",
  },
};

const TYPE_COLORS: Record<ScheduleType, { bg: string; text: string; ring: string }> = {
  cron: {
    bg: "bg-blue-100 dark:bg-blue-500/10",
    text: "text-blue-600 dark:text-blue-400",
    ring: "ring-blue-500/30",
  },
  poll: {
    bg: "bg-violet-100 dark:bg-violet-500/10",
    text: "text-violet-600 dark:text-violet-400",
    ring: "ring-violet-500/30",
  },
};

// ── Filter Pills ──────────────────────────────────────────────────────

function FilterPill({
  label,
  active,
  dotColor,
  onClick,
}: {
  label: string;
  active: boolean;
  dotColor?: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 h-8 px-2.5 text-xs rounded-md border transition-colors cursor-pointer",
        active
          ? "bg-zinc-100 dark:bg-zinc-800 border-zinc-300 dark:border-zinc-600 text-foreground"
          : "bg-transparent border-transparent text-foreground/70 hover:text-foreground hover:bg-zinc-800/30",
      )}
    >
      {dotColor && <span className={cn("w-1.5 h-1.5 rounded-full", dotColor)} />}
      {label}
    </button>
  );
}

// ── Delete Confirmation ───────────────────────────────────────────────

function DeleteScheduleButton({
  schedule,
  onDelete,
}: {
  schedule: Schedule;
  onDelete: (id: string) => void;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger
        render={
          <button
            className="rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-red-500/10 hover:text-red-400 cursor-pointer"
            title="Delete schedule"
          />
        }
      >
        <Trash2 className="h-3.5 w-3.5" />
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete schedule</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete <strong>{schedule.name}</strong>? This action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={() => onDelete(schedule.id)}
          >
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// ── Schedule Row ──────────────────────────────────────────────────────

function ScheduleRow({
  schedule,
  onPauseResume,
  onTrigger,
  onDelete,
}: {
  schedule: Schedule;
  onPauseResume: (schedule: Schedule) => void;
  onTrigger: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const navigate = useNavigate();
  const statusColors = STATUS_COLORS[schedule.status];
  const typeColors = TYPE_COLORS[schedule.type];

  return (
    <div
      onClick={() => navigate({ to: "/schedules/$scheduleId", params: { scheduleId: schedule.id } })}
      className="w-full text-left px-4 sm:px-6 py-3 flex items-center gap-3 transition-none hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40 cursor-pointer"
    >
      {/* Status dot */}
      <span
        className={cn("w-2 h-2 rounded-full shrink-0", statusColors.dot)}
        title={schedule.status}
      />

      {/* Name + flow */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-foreground truncate">
            {schedule.name}
          </span>
          <Badge
            variant="outline"
            className={cn(
              "text-[10px] font-mono uppercase tracking-wide ring-1 border-transparent shrink-0",
              typeColors.bg,
              typeColors.text,
              typeColors.ring,
            )}
          >
            {schedule.type}
          </Badge>
        </div>
        <div className="text-xs text-zinc-500 truncate mt-0.5">
          <button
            onClick={(e) => {
              e.stopPropagation();
              const flowName = flowNameFromPath(schedule.flow_path);
              navigate({ to: "/flows/$flowName", params: { flowName } });
            }}
            className="text-zinc-600 hover:text-blue-400 transition-colors cursor-pointer"
          >
            {flowNameFromPath(schedule.flow_path)}
          </button>
          <span className="text-zinc-700"> · </span>
          <span>{humanCron(schedule.cron_expr, schedule.cron_description)}</span>
        </div>
      </div>

      {/* Last fired */}
      <div className="hidden md:block w-24 text-right text-xs text-zinc-500 shrink-0">
        {schedule.last_fired_at ? timeAgo(schedule.last_fired_at) : "never"}
      </div>

      {/* Actions */}
      <div
        className="flex items-center gap-0.5 shrink-0"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={() => onPauseResume(schedule)}
          className={cn(
            "rounded-md p-1.5 transition-colors cursor-pointer",
            schedule.status === "active"
              ? "text-zinc-500 hover:bg-amber-500/10 hover:text-amber-400"
              : "text-zinc-500 hover:bg-emerald-500/10 hover:text-emerald-400",
          )}
          title={schedule.status === "active" ? "Pause" : "Resume"}
        >
          {schedule.status === "active" ? (
            <Pause className="h-3.5 w-3.5" />
          ) : (
            <Play className="h-3.5 w-3.5" />
          )}
        </button>
        <button
          onClick={() => onTrigger(schedule.id)}
          className="rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-blue-500/10 hover:text-blue-400 cursor-pointer"
          title="Trigger now"
        >
          <Zap className="h-3.5 w-3.5" />
        </button>
        <DeleteScheduleButton schedule={schedule} onDelete={onDelete} />
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────

export function SchedulesPage() {
  const [statusFilter, setStatusFilter] = useState<ScheduleStatus | null>(null);
  const [typeFilter, setTypeFilter] = useState<ScheduleType | null>(null);
  const { data, isLoading } = useSchedules(
    statusFilter ?? undefined,
    typeFilter ?? undefined,
  );
  const mutations = useScheduleMutations();

  const schedules = useMemo(() => data?.schedules ?? [], [data]);

  const counts = useMemo(() => {
    const byStatus: Record<string, number> = { active: 0, paused: 0 };
    const byType: Record<string, number> = { cron: 0, poll: 0 };
    for (const s of schedules) {
      byStatus[s.status] = (byStatus[s.status] ?? 0) + 1;
      byType[s.type] = (byType[s.type] ?? 0) + 1;
    }
    return { byStatus, byType };
  }, [schedules]);

  const handlePauseResume = useCallback(
    (schedule: Schedule) => {
      if (schedule.status === "active") {
        mutations.pauseSchedule.mutate(schedule.id);
      } else {
        mutations.resumeSchedule.mutate(schedule.id);
      }
    },
    [mutations],
  );

  const handleTrigger = useCallback(
    (id: string) => {
      mutations.triggerSchedule.mutate(id);
    },
    [mutations],
  );

  const handleDelete = useCallback(
    (id: string) => {
      mutations.deleteSchedule.mutate(id);
    },
    [mutations],
  );

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="flex items-center gap-4 border-b border-border px-4 sm:px-6 py-3">
        <div className="flex items-center gap-2">
          <Clock className="h-4 w-4 text-zinc-500" />
          <h1 className="text-sm font-semibold">Schedules</h1>
          {data && (
            <span className="text-xs text-zinc-500">{data.total}</span>
          )}
        </div>

        <div className="flex-1" />

        {/* Status filters */}
        <div className="flex items-center gap-1">
          <FilterPill
            label={`${counts.byStatus.active ?? 0} active`}
            active={statusFilter === "active"}
            dotColor="bg-emerald-400"
            onClick={() => setStatusFilter(statusFilter === "active" ? null : "active")}
          />
          <FilterPill
            label={`${counts.byStatus.paused ?? 0} paused`}
            active={statusFilter === "paused"}
            dotColor="bg-zinc-400"
            onClick={() => setStatusFilter(statusFilter === "paused" ? null : "paused")}
          />
        </div>

        <div className="h-4 w-px bg-border" />

        {/* Type filters */}
        <div className="flex items-center gap-1">
          <FilterPill
            label="cron"
            active={typeFilter === "cron"}
            onClick={() => setTypeFilter(typeFilter === "cron" ? null : "cron")}
          />
          <FilterPill
            label="poll"
            active={typeFilter === "poll"}
            onClick={() => setTypeFilter(typeFilter === "poll" ? null : "poll")}
          />
        </div>
      </div>

      {/* Table header */}
      <div className="flex items-center gap-3 px-4 sm:px-6 py-2 border-b border-border/50 text-[11px] uppercase tracking-wider text-zinc-500 font-medium">
        <span className="w-2 shrink-0" />
        <span className="flex-1">Name</span>
        <span className="hidden md:block w-24 text-right">Last fired</span>
        <span className="w-[120px] shrink-0" />
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="space-y-1 p-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full rounded-md" />
            ))}
          </div>
        ) : schedules.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-zinc-500 gap-2">
            <Timer className="h-8 w-8 text-zinc-400" />
            <p className="text-sm">No schedules found</p>
            <p className="text-xs text-zinc-600">
              Create schedules via the CLI: <code className="bg-zinc-800 px-1.5 py-0.5 rounded text-zinc-300">stepwise schedule create</code>
            </p>
          </div>
        ) : (
          <div className="divide-y divide-border/50">
            {schedules.map((schedule) => (
              <ScheduleRow
                key={schedule.id}
                schedule={schedule}
                onPauseResume={handlePauseResume}
                onTrigger={handleTrigger}
                onDelete={handleDelete}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
