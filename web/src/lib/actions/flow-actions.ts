import {
  Play,
  PenLine,
  List,
  Copy,
  CopyPlus,
  Download,
  Trash2,
} from "lucide-react";
import type { LocalFlow } from "@/lib/types";
import type { ActionDefinition } from "./types";

export const FLOW_ACTIONS: ActionDefinition<LocalFlow>[] = [
  // ── lifecycle (0) ──
  {
    id: "flow.run",
    label: "Run Flow",
    icon: Play,
    group: "lifecycle",
    groupOrder: 0,
    isAvailable: () => true,
    execute: (flow, ctx) => ctx.sideEffects.onRunFlow?.(flow),
  },

  // ── navigate (10) ──
  {
    id: "flow.edit",
    label: "Edit in Editor",
    icon: PenLine,
    shortcut: "Enter",
    shortcutKeys: ["Enter"],
    group: "navigate",
    groupOrder: 10,
    isAvailable: () => true,
    execute: (flow, ctx) =>
      ctx.navigate({ to: "/editor", search: { flow: flow.path } }),
  },
  {
    id: "flow.view-jobs",
    label: "View Recent Jobs",
    icon: List,
    group: "navigate",
    groupOrder: 10,
    isAvailable: () => true,
    execute: (flow, ctx) =>
      ctx.navigate({ to: "/", search: { flow: flow.name } }),
  },

  // ── copy (20) — sub-menu ──
  {
    id: "flow.copy",
    label: "Copy",
    icon: Copy,
    group: "copy",
    groupOrder: 20,
    isAvailable: () => true,
    execute: () => {},
    children: [
      {
        id: "flow.copy.path",
        label: "Copy Flow Path",
        icon: Copy,
        group: "",
        groupOrder: 0,
        isAvailable: () => true,
        execute: (flow, ctx) => ctx.clipboard(flow.path, "Flow path"),
      },
      {
        id: "flow.copy.name",
        label: "Copy Flow Name",
        icon: Copy,
        group: "",
        groupOrder: 0,
        isAvailable: () => true,
        execute: (flow, ctx) => ctx.clipboard(flow.name, "Flow name"),
      },
    ],
  },

  // ── organize (30) ──
  {
    id: "flow.duplicate",
    label: "Duplicate Flow",
    icon: CopyPlus,
    group: "organize",
    groupOrder: 30,
    isAvailable: () => true,
    execute: (flow, ctx) => ctx.sideEffects.onDuplicateFlow?.(flow),
  },
  {
    id: "flow.export-yaml",
    label: "Export as YAML",
    icon: Download,
    group: "organize",
    groupOrder: 30,
    isAvailable: () => true,
    execute: (flow) => {
      // Trigger browser download of the flow path
      const a = document.createElement("a");
      a.href = `/api/flows/local/${encodeURIComponent(flow.path)}/raw`;
      a.download = `${flow.name}.flow.yaml`;
      a.click();
    },
  },

  // ── danger (100) ──
  {
    id: "flow.delete",
    label: "Delete Flow",
    icon: Trash2,
    shortcut: "D",
    shortcutKeys: ["d"],
    variant: "destructive",
    group: "danger",
    groupOrder: 100,
    isAvailable: () => true,
    confirm: {
      title: "Delete flow?",
      description: (flow) =>
        `Delete flow "${flow.name}"? This cannot be undone.`,
    },
    execute: (flow, ctx) => {
      ctx.extraMutations?.deleteFlow?.mutate(flow.path);
      ctx.sideEffects.onAfterDeleteFlow?.(flow);
    },
  },
];
