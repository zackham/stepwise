import { RefreshCw, XCircle, Copy, Clipboard, FileOutput } from "lucide-react";
import type { StepDefinition, StepRun, StepRunStatus } from "@/lib/types";
import type { ActionDefinition } from "./types";

export interface StepEntity {
  jobId: string;
  stepDef: StepDefinition;
  latestRun: StepRun | null;
}

const RERUNNABLE_STATUSES: StepRunStatus[] = ["completed", "failed", "cancelled", "skipped"];

export const STEP_ACTIONS: ActionDefinition<StepEntity>[] = [
  // ── lifecycle (0) ──
  {
    id: "step.rerun",
    label: "Rerun Step",
    icon: RefreshCw,
    shortcut: "R",
    shortcutKeys: ["r"],
    group: "lifecycle",
    groupOrder: 0,
    isAvailable: (s) =>
      s.latestRun === null || RERUNNABLE_STATUSES.includes(s.latestRun.status),
    execute: (s, ctx) =>
      ctx.mutations.rerunStep.mutate({ jobId: s.jobId, stepName: s.stepDef.name }),
  },
  {
    id: "step.cancel-run",
    label: "Cancel Run",
    icon: XCircle,
    group: "lifecycle",
    groupOrder: 0,
    isAvailable: (s) =>
      s.latestRun !== null &&
      (s.latestRun.status === "running" || s.latestRun.status === "suspended"),
    confirm: {
      title: "Cancel this step run?",
      description: (s) => `This will cancel the current run of "${s.stepDef.name}".`,
    },
    execute: (s, ctx) => {
      if (s.latestRun) ctx.mutations.cancelRun.mutate(s.latestRun.id);
    },
  },

  // ── copy (10) ──
  {
    id: "step.copy-name",
    label: "Copy Step Name",
    icon: Copy,
    group: "copy",
    groupOrder: 10,
    isAvailable: () => true,
    execute: (s, ctx) => ctx.clipboard(s.stepDef.name, "Step name"),
  },
  {
    id: "step.copy-config",
    label: "Copy Step Config",
    icon: Clipboard,
    group: "copy",
    groupOrder: 10,
    isAvailable: () => true,
    execute: (s, ctx) =>
      ctx.clipboard(JSON.stringify(s.stepDef, null, 2), "Step config"),
  },

  // ── inspect (20) ──
  {
    id: "step.view-output",
    label: "View Output",
    icon: FileOutput,
    group: "inspect",
    groupOrder: 20,
    isAvailable: (s) =>
      s.latestRun !== null &&
      s.latestRun.status === "completed" &&
      s.latestRun.result !== null &&
      s.latestRun.result.artifact !== null,
    execute: (s, ctx) => ctx.sideEffects.onViewStepOutput?.(s.stepDef.name),
  },
];
