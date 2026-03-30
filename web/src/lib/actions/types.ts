import type { LucideIcon } from "lucide-react";
import type { useStepwiseMutations } from "@/hooks/useStepwise";
import type { useDeleteFlow } from "@/hooks/useEditor";
import type { Job, LocalFlow } from "@/lib/types";

export type EntityType = "job" | "step" | "flow" | "canvas";

export interface ActionDefinition<T = unknown> {
  id: string;
  label: string;
  icon?: LucideIcon;
  shortcut?: string;
  shortcutKeys?: string[];
  variant?: "default" | "destructive";
  group: string;
  groupOrder: number;
  isAvailable: (entity: T) => boolean;
  isEnabled?: (entity: T) => boolean;
  confirm?: {
    title: string;
    description: string | ((entity: T) => string);
    confirmLabel?: string;
  };
  execute: (entity: T, ctx: ActionContext) => void;
  children?: ActionDefinition<T>[];
}

export interface ActionContext {
  mutations: ReturnType<typeof useStepwiseMutations>;
  navigate: (opts: { to: string; search?: Record<string, unknown>; replace?: boolean }) => void;
  clipboard: (text: string, label?: string) => void;
  sideEffects: SideEffects;
  extraMutations?: { deleteFlow?: ReturnType<typeof useDeleteFlow> };
}

export interface SideEffects {
  onAfterDeleteJob?: (job: Job) => void;
  onAfterArchiveJob?: (job: Job) => void;
  onRunFlow?: (flow: LocalFlow) => void;
  onAfterDeleteFlow?: (flow: LocalFlow) => void;
  onDuplicateFlow?: (flow: LocalFlow) => void;
  onViewStepOutput?: (stepName: string) => void;
  onCreateJob?: () => void;
  onFitToView?: () => void;
  onResetZoom?: () => void;
  onToggleFollowFlow?: () => void;
  onInjectContext?: (jobId: string) => void;
}
