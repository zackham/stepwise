import { useState, useMemo } from "react";
import {
  Terminal,
  User,
  Brain,
  Cog,
  Hand,
  RotateCw,
  Layers,
  ChevronDown,
  Bot,
  Clock,
} from "lucide-react";
import { StepStatusBadge } from "@/components/StatusBadge";
import { STEP_STATUS_COLORS, STEP_PENDING_COLORS } from "@/lib/status-colors";
import type { ExitRule, StepDefinition, StepRun, StepRunStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

interface StepNodeProps {
  stepDef: StepDefinition;
  latestRun: StepRun | null;
  maxAttempts: number | null;
  isSelected: boolean;
  onClick: () => void;
  onNavigateSubJob?: (subJobId: string) => void;
  onToggleExpand?: () => void;
  childStepCount?: number;
  childJobStatus?: string | null;
  x: number;
  y: number;
  width: number;
  height: number;
}

function executorIcon(type: string) {
  switch (type) {
    case "script":
      return <Terminal className="w-3.5 h-3.5" />;
    case "human":
      return <User className="w-3.5 h-3.5" />;
    case "mock_llm":
    case "llm":
      return <Brain className="w-3.5 h-3.5" />;
    case "agent":
      return <Bot className="w-3.5 h-3.5" />;
    case "sub_flow":
      return <Layers className="w-3.5 h-3.5" />;
    default:
      return <Cog className="w-3.5 h-3.5" />;
  }
}

function executorSubtitle(stepDef: StepDefinition): string {
  const { type, config } = stepDef.executor;
  const limit = 36;
  switch (type) {
    case "script": {
      const cmd = config.command as string | undefined;
      if (!cmd) return "script";
      // Extract meaningful summary from command
      // For python3 -c "..." commands, skip boilerplate
      const pyInline = cmd.match(/python3?\s+-c\s+["'](.+)/);
      if (pyInline) {
        // Show outputs for the step instead
        const outputs = stepDef.outputs;
        if (outputs.length > 0) return `script → ${outputs.join(", ")}`;
        return "python script";
      }
      // For simple commands, show the command itself
      const simple = cmd.replace(/^(bash|sh)\s+-c\s+["']?/, "").trim();
      return simple.length > limit
        ? simple.slice(0, limit - 2) + "..."
        : simple;
    }
    case "human": {
      const prompt = config.prompt as string | undefined;
      if (!prompt) return "human input";
      return prompt.length > limit
        ? prompt.slice(0, limit - 2) + "..."
        : prompt;
    }
    case "mock_llm":
      return "LLM simulation";
    case "llm": {
      const model = config.model as string | undefined;
      return model ? `LLM: ${model}` : "LLM";
    }
    case "agent": {
      const mode = config.output_mode as string | undefined;
      const model = config.model as string | undefined;
      const parts = ["Agent"];
      if (model) parts.push(model);
      if (mode && mode !== "effect") parts.push(`(${mode})`);
      return parts.join(" ");
    }
    default:
      return type;
  }
}

function formatDuration(startedAt: string | null, completedAt: string | null): string | null {
  if (!startedAt) return null;
  const start = new Date(startedAt).getTime();
  const end = completedAt ? new Date(completedAt).getTime() : Date.now();
  const ms = end - start;
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

function formatPreviewValue(value: unknown, maxLen: number = 20): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return String(value);
  if (typeof value === "number") return String(value);
  if (typeof value === "string") {
    if (value.length <= maxLen) return value;
    return value.slice(0, maxLen - 1) + "…";
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    return `[${value.length}]`;
  }
  if (typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>);
    if (keys.length === 0) return "{}";
    return `{${keys.length}}`;
  }
  return String(value);
}

const ACTION_COLORS: Record<string, string> = {
  advance: "text-emerald-400",
  loop: "text-purple-400",
  escalate: "text-red-400",
  abandon: "text-red-500",
};

function ExitRuleTooltip({ rules }: { rules: ExitRule[] }) {
  return (
    <div className="absolute left-0 top-full mt-1 z-50 bg-zinc-900 border border-zinc-700 rounded-md shadow-xl p-2 min-w-[280px] max-w-[400px]">
      <div className="text-[10px] font-medium text-zinc-400 uppercase tracking-wide mb-1.5">
        Exit Rules
      </div>
      <table className="w-full text-[10px]">
        <tbody>
          {rules.map((rule) => {
            const action = rule.config.action as string;
            const condition = rule.config.condition as string | undefined;
            const field = rule.config.field as string | undefined;
            const value = rule.config.value;
            const target = rule.config.target as string | undefined;
            const condText = condition ?? (field ? `${field} == ${JSON.stringify(value)}` : rule.type);
            return (
              <tr key={rule.name} className="border-t border-zinc-800 first:border-t-0">
                <td className="py-1 pr-2 font-mono text-zinc-300 whitespace-nowrap">
                  {rule.name}
                </td>
                <td className="py-1 pr-2 font-mono text-zinc-500 max-w-[180px] truncate">
                  {condText}
                </td>
                <td className={cn("py-1 whitespace-nowrap font-medium", ACTION_COLORS[action] ?? "text-zinc-400")}>
                  {action}
                  {target && <span className="text-zinc-500"> → {target}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ArtifactPreviewRow({ artifact }: { artifact: Record<string, unknown> }) {
  const [hover, setHover] = useState(false);
  const entries = Object.entries(artifact);

  // Build compact inline preview
  const parts: string[] = [];
  let len = 0;
  let shown = 0;
  for (const [key, val] of entries) {
    const formatted = `${key}: ${formatPreviewValue(val, 14)}`;
    if (len + formatted.length > 32 && parts.length > 0) break;
    parts.push(formatted);
    len += formatted.length + 3;
    shown++;
  }
  const remaining = entries.length - shown;

  return (
    <div
      className="relative"
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <div className="text-[10px] text-emerald-400/60 truncate font-mono leading-tight cursor-default">
        {parts.join(" · ")}
        {remaining > 0 && <span className="text-zinc-500"> +{remaining}</span>}
      </div>
      {hover && (
        <div className="absolute left-0 top-full mt-1 z-50 bg-zinc-900 border border-zinc-700 rounded-md shadow-xl p-2 min-w-[200px] max-w-[360px]">
          <div className="text-[10px] font-medium text-zinc-400 uppercase tracking-wide mb-1.5">
            Output
          </div>
          <table className="w-full text-[11px]">
            <tbody>
              {entries.map(([key, value]) => (
                <tr key={key} className="border-t border-zinc-800 first:border-t-0">
                  <td className="py-1 pr-3 font-mono text-zinc-400 whitespace-nowrap align-top">
                    {key}
                  </td>
                  <td className="py-1 font-mono text-zinc-200 break-words max-w-[240px]">
                    {formatPreviewValue(value, 120)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function StepNode({
  stepDef,
  latestRun,
  maxAttempts,
  isSelected,
  onClick,
  onNavigateSubJob,
  onToggleExpand,
  childStepCount,
  childJobStatus,
  x,
  y,
  width,
  height,
}: StepNodeProps) {
  const [showTooltip, setShowTooltip] = useState(false);
  const status: StepRunStatus | "pending" = latestRun?.status ?? "pending";
  const subJobId = latestRun?.sub_job_id ?? null;
  const colors =
    status === "pending"
      ? STEP_PENDING_COLORS
      : STEP_STATUS_COLORS[status];

  const isSuspended =
    latestRun?.status === "suspended" &&
    latestRun?.watch?.mode === "human";

  const attempt = latestRun?.attempt ?? 0;
  const showAttemptBadge = attempt > 1 || (maxAttempts != null && attempt >= 1);

  // Artifact preview for completed steps
  const visibleArtifact = useMemo(() => {
    if (status !== "completed" || !latestRun?.result?.artifact) return null;
    const filtered = Object.fromEntries(
      Object.entries(latestRun.result.artifact).filter(([k]) => !k.startsWith("_"))
    );
    return Object.keys(filtered).length > 0 ? filtered : null;
  }, [status, latestRun]);

  return (
    <div
      className={cn(
        "absolute cursor-pointer border rounded-lg p-3",
        "transition-shadow duration-200",
        colors.bg,
        colors.border,
        isSelected && `ring-2 ${colors.ring} shadow-lg`,
        !isSelected && "hover:shadow-md",
        status === "running" && "shadow-blue-500/20 shadow-md"
      )}
      role="button"
      tabIndex={0}
      style={{ left: x, top: y, width, height }}
      onClick={onClick}
      onMouseEnter={() => stepDef.exit_rules.length > 0 && setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      {/* Top handle */}
      <div className="absolute -top-1.5 left-1/2 -translate-x-1/2 w-3 h-3 rounded-full bg-zinc-700 border-2 border-zinc-600" />

      {/* Content */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className={cn("shrink-0", colors.text)}>
            {executorIcon(stepDef.executor.type)}
          </span>
          <span className="text-sm font-medium truncate text-foreground">
            {stepDef.name}
          </span>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {showAttemptBadge && (
            <span className="flex items-center gap-0.5 text-[10px] text-zinc-500 bg-zinc-800 rounded px-1 py-0.5">
              <RotateCw className="w-2.5 h-2.5" />
              {maxAttempts ? `${attempt}/${maxAttempts}` : attempt}
            </span>
          )}
          <StepStatusBadge status={status} />
        </div>
      </div>

      {/* Duration */}
      {latestRun && (status === "completed" || status === "failed" || status === "running") && (
        <div className="flex items-center gap-0.5 text-[9px] text-zinc-500 mt-0.5">
          <Clock className="w-2.5 h-2.5" />
          {formatDuration(latestRun.started_at, latestRun.completed_at)}
        </div>
      )}

      {/* Description */}
      {stepDef.description && (
        <div className="mt-1 text-[11px] text-zinc-400 truncate leading-tight">
          {stepDef.description}
        </div>
      )}

      {/* Executor subtitle / Artifact preview */}
      <div className={cn("text-[10px] font-mono leading-tight", !stepDef.description && "mt-1")}>
        {onToggleExpand ? (
          <button
            className="flex items-center gap-1 text-purple-400 hover:text-purple-300 transition-colors"
            onClick={(e) => {
              e.stopPropagation();
              onToggleExpand();
            }}
          >
            <Layers className="w-2.5 h-2.5" />
            {childStepCount ? `${childStepCount} steps` : "Sub-flow"}
            <ChevronDown className="w-2.5 h-2.5" />
          </button>
        ) : subJobId && onNavigateSubJob ? (
          <button
            className="flex items-center gap-1 text-purple-400 hover:text-purple-300 transition-colors"
            onClick={(e) => {
              e.stopPropagation();
              onNavigateSubJob(subJobId);
            }}
          >
            <Layers className="w-2.5 h-2.5" />
            Sub-job →
          </button>
        ) : isSuspended ? (
          <span className="flex items-center gap-1 text-amber-400">
            <Hand className="w-2.5 h-2.5" />
            Awaiting input
          </span>
        ) : visibleArtifact ? (
          <ArtifactPreviewRow artifact={visibleArtifact} />
        ) : (
          <span className="text-zinc-500 truncate block">
            {executorSubtitle(stepDef)}
          </span>
        )}
      </div>

      {/* Bottom handle */}
      <div className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-3 h-3 rounded-full bg-zinc-700 border-2 border-zinc-600" />

      {/* Exit rule tooltip */}
      {showTooltip && stepDef.exit_rules.length > 0 && (
        <ExitRuleTooltip rules={stepDef.exit_rules} />
      )}
    </div>
  );
}
