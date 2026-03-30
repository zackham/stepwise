import { Plus, Maximize2, ZoomIn, Compass } from "lucide-react";
import type { ActionDefinition } from "./types";

export const CANVAS_ACTIONS: ActionDefinition<Record<string, never>>[] = [
  // ── actions (0) ──
  {
    id: "canvas.create-job",
    label: "Create New Job",
    icon: Plus,
    group: "actions",
    groupOrder: 0,
    isAvailable: () => true,
    execute: (_entity, ctx) => ctx.sideEffects.onCreateJob?.(),
  },

  // ── view (10) ──
  {
    id: "canvas.fit-to-screen",
    label: "Fit to Screen",
    icon: Maximize2,
    group: "view",
    groupOrder: 10,
    isAvailable: () => true,
    execute: (_entity, ctx) => ctx.sideEffects.onFitToView?.(),
  },
  {
    id: "canvas.reset-zoom",
    label: "Reset Zoom",
    icon: ZoomIn,
    group: "view",
    groupOrder: 10,
    isAvailable: () => true,
    execute: (_entity, ctx) => ctx.sideEffects.onResetZoom?.(),
  },
  {
    id: "canvas.toggle-follow",
    label: "Toggle Follow Flow",
    icon: Compass,
    group: "view",
    groupOrder: 10,
    isAvailable: () => true,
    execute: (_entity, ctx) => ctx.sideEffects.onToggleFollowFlow?.(),
  },
];
