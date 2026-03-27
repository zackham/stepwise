import type { JobStatus, StepRunStatus } from "./types";

export const JOB_STATUS_COLORS: Record<
  JobStatus,
  { bg: string; text: string; ring: string; dot: string }
> = {
  staged: {
    bg: "bg-violet-500/10",
    text: "text-violet-400",
    ring: "ring-violet-500/30",
    dot: "bg-violet-400",
  },
  pending: {
    bg: "bg-zinc-500/10",
    text: "text-zinc-400",
    ring: "ring-zinc-500/30",
    dot: "bg-zinc-400",
  },
  running: {
    bg: "bg-blue-500/10",
    text: "text-blue-400",
    ring: "ring-blue-500/30",
    dot: "bg-blue-400",
  },
  paused: {
    bg: "bg-amber-500/10",
    text: "text-amber-400",
    ring: "ring-amber-500/30",
    dot: "bg-amber-400",
  },
  completed: {
    bg: "bg-emerald-500/10",
    text: "text-emerald-400",
    ring: "ring-emerald-500/30",
    dot: "bg-emerald-400",
  },
  failed: {
    bg: "bg-red-500/10",
    text: "text-red-400",
    ring: "ring-red-500/30",
    dot: "bg-red-400",
  },
  cancelled: {
    bg: "bg-zinc-500/10",
    text: "text-zinc-500",
    ring: "ring-zinc-500/30",
    dot: "bg-zinc-500",
  },
};

export const STEP_STATUS_COLORS: Record<
  StepRunStatus,
  { bg: string; text: string; border: string; ring: string }
> = {
  running: {
    bg: "bg-blue-500/15",
    text: "text-blue-400",
    border: "border-blue-500/40",
    ring: "ring-blue-500/50",
  },
  suspended: {
    bg: "bg-amber-500/15",
    text: "text-amber-400",
    border: "border-amber-500/40",
    ring: "ring-amber-500/50",
  },
  delegated: {
    bg: "bg-purple-500/15",
    text: "text-purple-400",
    border: "border-purple-500/40",
    ring: "ring-purple-500/50",
  },
  completed: {
    bg: "bg-emerald-500/15",
    text: "text-emerald-400",
    border: "border-emerald-500/40",
    ring: "ring-emerald-500/50",
  },
  failed: {
    bg: "bg-red-500/15",
    text: "text-red-400",
    border: "border-red-500/40",
    ring: "ring-red-500/50",
  },
  cancelled: {
    bg: "bg-zinc-500/15",
    text: "text-zinc-500",
    border: "border-zinc-500/40",
    ring: "ring-zinc-500/50",
  },
  skipped: {
    bg: "bg-zinc-500/10",
    text: "text-zinc-600",
    border: "border-zinc-600/30",
    ring: "ring-zinc-600/40",
  },
};

// For steps with no run yet (pending)
export const STEP_PENDING_COLORS = {
  bg: "bg-zinc-200/50 dark:bg-zinc-800/50",
  text: "text-zinc-500",
  border: "border-zinc-300/50 dark:border-zinc-700/50",
  ring: "ring-zinc-500/30",
};
