import {
  Play,
  Pause,
  RotateCcw,
  RefreshCw,
  XCircle,
  UserCheck,
  MessageSquarePlus,
  Archive,
  ArchiveRestore,
  Copy,
  ExternalLink,
  Trash2,
} from "lucide-react";
import type { Job, JobStatus } from "@/lib/types";
import type { ActionDefinition } from "./types";

// ── Status predicates ────────────────────────────────────────────────

export function canStart(status: JobStatus): boolean {
  return status === "staged" || status === "pending";
}

export function canPause(status: JobStatus): boolean {
  return status === "running";
}

export function canResume(status: JobStatus): boolean {
  return status === "paused";
}

export function canRetry(status: JobStatus): boolean {
  return status === "paused" || status === "failed" || status === "completed";
}

export function canCancel(status: JobStatus): boolean {
  return status === "running" || status === "paused";
}

export function canReset(status: JobStatus): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

export function canArchive(status: JobStatus): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

export function isStale(job: Pick<Job, "status" | "created_by" | "heartbeat_at">): boolean {
  if (job.status !== "running" || job.created_by === "server") return false;
  if (!job.heartbeat_at) return true;
  const age = Date.now() - new Date(job.heartbeat_at).getTime();
  return age > 60_000;
}

// ── Job actions ──────────────────────────────────────────────────────

export const JOB_ACTIONS: ActionDefinition<Job>[] = [
  // ── lifecycle (0) ──
  {
    id: "job.start",
    label: "Start",
    icon: Play,
    group: "lifecycle",
    groupOrder: 0,
    isAvailable: (job) => canStart(job.status),
    execute: (job, ctx) => ctx.mutations.startJob.mutate(job.id),
  },
  {
    id: "job.pause",
    label: "Pause",
    icon: Pause,
    group: "lifecycle",
    groupOrder: 0,
    isAvailable: (job) => canPause(job.status),
    execute: (job, ctx) => ctx.mutations.pauseJob.mutate(job.id),
  },
  {
    id: "job.resume",
    label: "Resume",
    icon: RotateCcw,
    group: "lifecycle",
    groupOrder: 0,
    isAvailable: (job) => canResume(job.status),
    execute: (job, ctx) => ctx.mutations.resumeJob.mutate(job.id),
  },
  {
    id: "job.retry",
    label: "Retry",
    icon: RefreshCw,
    shortcut: "R",
    shortcutKeys: ["r"],
    group: "lifecycle",
    groupOrder: 0,
    isAvailable: (job) => canRetry(job.status),
    execute: (job, ctx) => ctx.mutations.resumeJob.mutate(job.id),
  },
  {
    id: "job.cancel",
    label: "Cancel",
    icon: XCircle,
    group: "lifecycle",
    groupOrder: 0,
    isAvailable: (job) => canCancel(job.status),
    confirm: {
      title: "Cancel this job?",
      description: (job) =>
        `This will cancel job "${job.name || job.objective}". Running steps will be stopped.`,
    },
    execute: (job, ctx) => ctx.mutations.cancelJob.mutate(job.id),
  },
  {
    id: "job.reset",
    label: "Reset",
    icon: RotateCcw,
    group: "lifecycle",
    groupOrder: 0,
    isAvailable: (job) => canReset(job.status),
    confirm: {
      title: "Reset all step runs?",
      description: (job) =>
        `This will reset job "${job.name || job.objective}" and clear all step runs.`,
    },
    execute: (job, ctx) => ctx.mutations.resetJob.mutate(job.id),
  },
  {
    id: "job.take-over",
    label: "Take Over",
    icon: UserCheck,
    group: "lifecycle",
    groupOrder: 0,
    isAvailable: (job) => isStale(job),
    execute: (job, ctx) => ctx.mutations.adoptJob.mutate(job.id),
  },
  {
    id: "job.inject-context",
    label: "Inject Context",
    icon: MessageSquarePlus,
    group: "lifecycle",
    groupOrder: 0,
    isAvailable: (job) => job.status === "running",
    execute: (job, ctx) => ctx.sideEffects.onInjectContext?.(job.id),
  },

  // ── organize (10) ──
  {
    id: "job.archive",
    label: "Archive",
    icon: Archive,
    group: "organize",
    groupOrder: 10,
    isAvailable: (job) => canArchive(job.status),
    execute: (job, ctx) => {
      ctx.mutations.archiveJob.mutate(job.id);
      ctx.sideEffects.onAfterArchiveJob?.(job);
    },
  },
  {
    id: "job.unarchive",
    label: "Unarchive",
    icon: ArchiveRestore,
    group: "organize",
    groupOrder: 10,
    isAvailable: (job) => job.status === "archived",
    execute: (job, ctx) => ctx.mutations.unarchiveJob.mutate(job.id),
  },

  // ── copy (20) — sub-menu ──
  {
    id: "job.copy",
    label: "Copy",
    icon: Copy,
    group: "copy",
    groupOrder: 20,
    isAvailable: () => true,
    execute: () => {},
    children: [
      {
        id: "job.copy.id",
        label: "Copy Job ID",
        icon: Copy,
        group: "",
        groupOrder: 0,
        isAvailable: () => true,
        execute: (job, ctx) => ctx.clipboard(job.id, "Job ID"),
      },
      {
        id: "job.copy.name",
        label: "Copy Name",
        icon: Copy,
        group: "",
        groupOrder: 0,
        isAvailable: (job) => job.name != null && job.name !== "",
        execute: (job, ctx) => ctx.clipboard(job.name!, "Name"),
      },
      {
        id: "job.copy.inputs",
        label: "Copy Inputs",
        icon: Copy,
        group: "",
        groupOrder: 0,
        isAvailable: (job) => Object.keys(job.inputs).length > 0,
        execute: (job, ctx) => ctx.clipboard(JSON.stringify(job.inputs, null, 2), "Inputs"),
      },
    ],
  },

  // ── navigate (30) ──
  {
    id: "job.open-detail",
    label: "Open Job",
    icon: ExternalLink,
    shortcut: "Enter",
    shortcutKeys: ["Enter"],
    group: "navigate",
    groupOrder: 30,
    isAvailable: () => true,
    execute: (job, ctx) => ctx.navigate({ to: `/jobs/${job.id}` }),
  },
  {
    id: "job.open-new-tab",
    label: "Open in New Tab",
    icon: ExternalLink,
    group: "navigate",
    groupOrder: 30,
    isAvailable: () => true,
    execute: (job) => window.open(`/jobs/${job.id}`, "_blank"),
  },

  // ── danger (100) ──
  {
    id: "job.delete",
    label: "Delete",
    icon: Trash2,
    shortcut: "D",
    shortcutKeys: ["d"],
    variant: "destructive",
    group: "danger",
    groupOrder: 100,
    isAvailable: () => true,
    confirm: {
      title: "Delete job permanently?",
      description: (job) =>
        `Delete job "${job.name || job.objective}"? This cannot be undone.`,
    },
    execute: (job, ctx) => {
      ctx.mutations.deleteJob.mutate(job.id);
      ctx.sideEffects.onAfterDeleteJob?.(job);
    },
  },
];
