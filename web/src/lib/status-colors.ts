import type { JobStatus, StepRunStatus } from "./types";

export const JOB_STATUS_COLORS: Record<
  JobStatus,
  { bg: string; text: string; ring: string; dot: string }
> = {
  staged: {
    bg: "bg-violet-100 dark:bg-violet-500/10",
    text: "text-violet-600 dark:text-violet-400",
    ring: "ring-violet-500/30",
    dot: "bg-violet-400",
  },
  awaiting_approval: {
    bg: "bg-amber-100 dark:bg-amber-500/10",
    text: "text-amber-600 dark:text-amber-400",
    ring: "ring-amber-500/30",
    dot: "bg-amber-400",
  },
  pending: {
    bg: "bg-zinc-100 dark:bg-zinc-500/10",
    text: "text-zinc-500 dark:text-zinc-400",
    ring: "ring-zinc-500/30",
    dot: "bg-zinc-400",
  },
  running: {
    bg: "bg-blue-100 dark:bg-blue-500/10",
    text: "text-blue-600 dark:text-blue-400",
    ring: "ring-blue-500/30",
    dot: "bg-blue-400",
  },
  paused: {
    bg: "bg-amber-100 dark:bg-amber-500/10",
    text: "text-amber-600 dark:text-amber-400",
    ring: "ring-amber-500/30",
    dot: "bg-amber-400",
  },
  completed: {
    bg: "bg-emerald-100 dark:bg-emerald-500/10",
    text: "text-emerald-600 dark:text-emerald-400",
    ring: "ring-emerald-500/30",
    dot: "bg-emerald-400",
  },
  failed: {
    bg: "bg-red-100 dark:bg-red-500/10",
    text: "text-red-600 dark:text-red-400",
    ring: "ring-red-500/30",
    dot: "bg-red-400",
  },
  cancelled: {
    bg: "bg-zinc-100 dark:bg-zinc-500/10",
    text: "text-zinc-500",
    ring: "ring-zinc-500/30",
    dot: "bg-zinc-500",
  },
  archived: {
    bg: "bg-zinc-100 dark:bg-zinc-500/10",
    text: "text-zinc-600",
    ring: "ring-zinc-500/20",
    dot: "bg-zinc-600",
  },
};

export const STEP_STATUS_COLORS: Record<
  StepRunStatus,
  { bg: string; text: string; border: string; ring: string }
> = {
  running: {
    bg: "bg-blue-100 dark:bg-blue-500/15",
    text: "text-blue-600 dark:text-blue-400",
    border: "border-blue-500/40",
    ring: "ring-blue-500/50",
  },
  suspended: {
    bg: "bg-amber-100 dark:bg-amber-500/15",
    text: "text-amber-600 dark:text-amber-400",
    border: "border-amber-500/40",
    ring: "ring-amber-500/50",
  },
  delegated: {
    bg: "bg-purple-100 dark:bg-purple-500/15",
    text: "text-purple-600 dark:text-purple-400",
    border: "border-purple-500/40",
    ring: "ring-purple-500/50",
  },
  completed: {
    bg: "bg-emerald-100 dark:bg-emerald-500/15",
    text: "text-emerald-600 dark:text-emerald-400",
    border: "border-emerald-500/40",
    ring: "ring-emerald-500/50",
  },
  failed: {
    bg: "bg-red-100 dark:bg-red-500/15",
    text: "text-red-600 dark:text-red-400",
    border: "border-red-500/40",
    ring: "ring-red-500/50",
  },
  cancelled: {
    bg: "bg-zinc-100 dark:bg-zinc-500/15",
    text: "text-zinc-500",
    border: "border-zinc-500/40",
    ring: "ring-zinc-500/50",
  },
  skipped: {
    bg: "bg-zinc-100 dark:bg-zinc-500/10",
    text: "text-zinc-600",
    border: "border-zinc-600/30",
    ring: "ring-zinc-600/40",
  },
  throttled: {
    bg: "bg-orange-100 dark:bg-orange-500/15",
    text: "text-orange-600 dark:text-orange-400",
    border: "border-orange-500/40",
    ring: "ring-orange-500/50",
  },
  waiting_reset: {
    bg: "bg-amber-100 dark:bg-amber-600/15",
    text: "text-amber-700 dark:text-amber-300",
    border: "border-amber-600/40",
    ring: "ring-amber-600/50",
  },
};

// For steps with no run yet (pending)
export const STEP_PENDING_COLORS = {
  bg: "bg-zinc-200/50 dark:bg-zinc-800/50",
  text: "text-zinc-500",
  border: "border-zinc-300/50 dark:border-zinc-700/50",
  ring: "ring-zinc-500/30",
};
