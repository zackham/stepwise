import { type KeyboardEvent, useState, useRef } from "react";
import {
  CirclePause,
  RotateCw,
  ChevronDown,
  Clock,
  ArrowRight,
  ArrowLeft,
  Layers,
} from "lucide-react";
import { StepStatusBadge } from "@/components/StatusBadge";
import { STEP_STATUS_COLORS, STEP_PENDING_COLORS } from "@/lib/status-colors";
import type { ExitRule, StepDefinition, StepRun, StepRunStatus } from "@/lib/types";
import { cn, safeRenderValue } from "@/lib/utils";
import { LiveDuration } from "@/components/LiveDuration";
import { executorIcon, executorLabel } from "@/lib/executor-utils";

/** Color accents for executor type — left-border visual clustering */
const EXECUTOR_ACCENT: Record<string, string> = {
  script: "border-l-cyan-500/60",
  agent: "border-l-violet-500/60",
  llm: "border-l-blue-500/60",
  mock_llm: "border-l-blue-400/40",
  external: "border-l-amber-500/60",
  poll: "border-l-indigo-500/60",
};

function getExecutorAccent(type: string): string {
  return EXECUTOR_ACCENT[type] ?? "border-l-zinc-500/40";
}

interface StepNodeProps {
  stepDef: StepDefinition;
  latestRun: StepRun | null;
  latestRuns?: Record<string, StepRun>;
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

function executorSubtitle(stepDef: StepDefinition): string {
  const { type, config } = stepDef.executor;
  const limit = 36;
  switch (type) {
    case "script": {
      const cmd = typeof config.command === "string" ? config.command : undefined;
      if (!cmd) return "script";
      const pyInline = cmd.match(/python3?\s+-c\s+["'](.+)/);
      if (pyInline) {
        const outputs = stepDef.outputs;
        if (outputs.length > 0) return `script → ${outputs.join(", ")}`;
        return "python script";
      }
      const simple = cmd.replace(/^(bash|sh)\s+-c\s+["']?/, "").trim();
      return simple.length > limit
        ? simple.slice(0, limit - 2) + "..."
        : simple;
    }
    case "external": {
      const prompt = typeof config.prompt === "string" ? config.prompt : undefined;
      if (!prompt) return "external input";
      return prompt.length > limit
        ? prompt.slice(0, limit - 2) + "..."
        : prompt;
    }
    case "mock_llm":
      return "LLM simulation";
    case "llm": {
      const model = typeof config.model === "string" ? config.model : undefined;
      return model ? `LLM: ${model}` : "LLM";
    }
    case "agent": {
      const mode = typeof config.output_mode === "string" ? config.output_mode : undefined;
      const model = typeof config.model === "string" ? config.model : undefined;
      const parts = ["Agent"];
      if (model) parts.push(model);
      if (mode && mode !== "effect") parts.push(`(${mode})`);
      return parts.join(" ");
    }
    default:
      return type;
  }
}

const ACTION_COLORS: Record<string, string> = {
  advance: "text-emerald-400",
  loop: "text-purple-400",
  escalate: "text-red-400",
  abandon: "text-red-500",
};

function formatTooltipValue(value: unknown, maxLen = 60): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  if (typeof value === "string") {
    if (value.length <= maxLen) return `"${value}"`;
    return `"${value.slice(0, maxLen - 2)}..."`;
  }
  if (Array.isArray(value)) return `Array[${value.length}]`;
  if (typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>);
    return `{${keys.slice(0, 3).join(", ")}${keys.length > 3 ? ", ..." : ""}}`;
  }
  return String(value);
}

function ExitRulesSection({ rules }: { rules: ExitRule[] }) {
  return (
    <>
      <div className="text-[10px] font-medium text-zinc-400 uppercase tracking-wide mb-1">
        Exit Rules
      </div>
      <table className="w-full text-[10px]">
        <tbody>
          {rules.map((rule) => {
            const action = safeRenderValue(rule.config.action);
            const condition = rule.config.condition != null ? safeRenderValue(rule.config.condition) : undefined;
            const field = rule.config.field != null ? safeRenderValue(rule.config.field) : undefined;
            const value = rule.config.value;
            const target = rule.config.target != null ? safeRenderValue(rule.config.target) : undefined;
            const condText = condition ?? (field ? `${field} == ${JSON.stringify(value)}` : safeRenderValue(rule.type));
            return (
              <tr key={rule.name} className="border-t border-zinc-200 dark:border-zinc-800 first:border-t-0">
                <td className="py-0.5 pr-2 font-mono text-zinc-700 dark:text-zinc-300 whitespace-nowrap">
                  {safeRenderValue(rule.name)}
                </td>
                <td className="py-0.5 pr-2 font-mono text-zinc-500 max-w-[180px] truncate">
                  {condText}
                </td>
                <td className={cn("py-0.5 whitespace-nowrap font-medium", ACTION_COLORS[action] ?? "text-zinc-400")}>
                  {action}
                  {target && <span className="text-zinc-500"> → {target}</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

function StepTooltip({
  stepDef,
  latestRun,
  latestRuns,
}: {
  stepDef: StepDefinition;
  latestRun: StepRun | null;
  latestRuns?: Record<string, StepRun>;
}) {
  const hasInputs = stepDef.inputs.length > 0;
  const hasOutputs = stepDef.outputs.length > 0;
  const hasExitRules = stepDef.exit_rules.length > 0;

  return (
    <div className="absolute left-0 top-full mt-1 z-50 bg-white/95 dark:bg-zinc-900/95 backdrop-blur-sm border border-zinc-300 dark:border-zinc-700 rounded-lg shadow-2xl p-2.5 min-w-[280px] max-w-[400px]">
      {/* Header: executor type */}
      <div className="flex items-center gap-1.5 mb-2 pb-1.5 border-b border-zinc-200 dark:border-zinc-800">
        <span className="text-zinc-500 dark:text-zinc-400">
          {executorIcon(stepDef.executor.type, "w-3.5 h-3.5")}
        </span>
        <span className="text-[11px] font-medium text-zinc-700 dark:text-zinc-300">
          {executorLabel(stepDef.executor.type)}
        </span>
        {stepDef.when && (
          <span className="ml-auto text-[9px] font-mono text-amber-400/80 bg-amber-500/10 rounded px-1 py-0.5">
            when: {stepDef.when.length > 30 ? stepDef.when.slice(0, 28) + "..." : stepDef.when}
          </span>
        )}
      </div>

      {/* Inputs */}
      {hasInputs && (
        <div className="mb-2">
          <div className="flex items-center gap-1 text-[10px] font-medium text-zinc-400 uppercase tracking-wide mb-0.5">
            <ArrowRight className="w-2.5 h-2.5" />
            Inputs
          </div>
          <div className="space-y-0.5">
            {stepDef.inputs.map((input) => {
              const sourceStep = input.source_step;
              const sourceField = input.source_field;
              const sourceRun = sourceStep && sourceStep !== "$job" ? latestRuns?.[sourceStep] : null;
              const resolvedValue = sourceRun?.result?.artifact?.[sourceField];
              const hasValue = resolvedValue !== undefined;

              return (
                <div key={input.local_name} className="flex items-baseline gap-1 text-[10px] font-mono">
                  <span className="text-cyan-400/80">{input.local_name}</span>
                  <span className="text-zinc-600">←</span>
                  <span className="text-zinc-500 truncate">
                    {input.any_of_sources
                      ? `any_of(${input.any_of_sources.map((s) => `${s.step}.${s.field}`).join(", ")})`
                      : `${sourceStep}.${sourceField}`}
                  </span>
                  {hasValue && (
                    <span className="text-zinc-600 truncate max-w-[120px]" title={String(resolvedValue)}>
                      = {formatTooltipValue(resolvedValue, 20)}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Outputs */}
      {hasOutputs && (
        <div className="mb-2">
          <div className="flex items-center gap-1 text-[10px] font-medium text-zinc-400 uppercase tracking-wide mb-0.5">
            <ArrowLeft className="w-2.5 h-2.5" />
            Outputs
          </div>
          <div className="flex flex-wrap gap-1">
            {stepDef.outputs.map((output) => {
              const outputValue = latestRun?.result?.artifact?.[output];
              const hasValue = outputValue !== undefined;
              return (
                <span
                  key={output}
                  className={cn(
                    "text-[10px] font-mono rounded px-1.5 py-0.5",
                    hasValue
                      ? "text-emerald-400 bg-emerald-500/10"
                      : "text-zinc-500 bg-zinc-200 dark:bg-zinc-800"
                  )}
                  title={hasValue ? `${output} = ${formatTooltipValue(outputValue)}` : output}
                >
                  {output}
                  {hasValue && (
                    <span className="text-emerald-500/60 ml-1">
                      {formatTooltipValue(outputValue, 16)}
                    </span>
                  )}
                </span>
              );
            })}
          </div>
        </div>
      )}

      {/* Exit rules */}
      {hasExitRules && (
        <div className={cn(hasInputs || hasOutputs ? "pt-1.5 border-t border-zinc-200 dark:border-zinc-800" : "")}>
          <ExitRulesSection rules={stepDef.exit_rules} />
        </div>
      )}
    </div>
  );
}

export function StepNode({
  stepDef,
  latestRun,
  latestRuns,
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
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onClick();
    }
  };

  const status: StepRunStatus | "pending" = latestRun?.status ?? "pending";
  const subJobId = latestRun?.sub_job_id ?? null;
  const colors =
    status === "pending"
      ? STEP_PENDING_COLORS
      : STEP_STATUS_COLORS[status];

  const isSuspended =
    latestRun?.status === "suspended" &&
    latestRun?.watch?.mode === "external";

  const attempt = latestRun?.attempt ?? 0;
  const showAttemptBadge = attempt > 1 || (maxAttempts != null && attempt >= 1);

  const hasTooltipContent =
    stepDef.exit_rules.length > 0 ||
    stepDef.inputs.length > 0 ||
    stepDef.outputs.length > 0 ||
    !!stepDef.when;

  const handleMouseEnter = () => {
    if (!hasTooltipContent) return;
    hoverTimerRef.current = setTimeout(() => setShowTooltip(true), 300);
  };

  const handleMouseLeave = () => {
    if (hoverTimerRef.current) {
      clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = null;
    }
    setShowTooltip(false);
  };

  return (
    <div
      className={cn(
        "absolute cursor-pointer border border-l-[3px] rounded-lg p-3 pl-2.5",
        "transition-all duration-200",
        colors.bg,
        colors.border,
        getExecutorAccent(stepDef.executor.type),
        isSelected && `ring-2 ${colors.ring} shadow-lg`,
        !isSelected && "hover:shadow-md hover:brightness-110 focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:shadow-lg",
        status === "running" && "shadow-blue-500/20 shadow-md"
      )}
      role="button"
      tabIndex={0}
      style={{ left: x, top: y, width, height }}
      onClick={onClick}
      onKeyDown={handleKeyDown}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {/* Top handle — color-matched to status */}
      <div className={cn(
        "absolute -top-1.5 left-1/2 -translate-x-1/2 w-2.5 h-2.5 rounded-full border-2",
        status === "pending" ? "bg-zinc-700 border-zinc-600" :
        status === "running" ? "bg-blue-500/60 border-blue-400/60" :
        status === "completed" ? "bg-emerald-500/60 border-emerald-400/60" :
        status === "failed" ? "bg-red-500/60 border-red-400/60" :
        status === "suspended" ? "bg-amber-500/60 border-amber-400/60" :
        "bg-zinc-700 border-zinc-600"
      )} />

      {/* Content */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className={cn("shrink-0", colors.text)}>
            {executorIcon(stepDef.executor.type, "w-3.5 h-3.5")}
          </span>
          <span className="text-sm font-medium truncate text-foreground">
            {stepDef.name}
          </span>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {showAttemptBadge && (
            <span className="flex items-center gap-0.5 text-[10px] text-zinc-500 bg-zinc-200 dark:bg-zinc-800 rounded px-1 py-0.5">
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
          <LiveDuration startTime={latestRun.started_at} endTime={latestRun.completed_at} />
        </div>
      )}

      {/* Description */}
      {stepDef.description && (
        <div className="mt-1 text-[11px] text-zinc-400 truncate leading-tight">
          {stepDef.description}
        </div>
      )}

      {/* Executor subtitle */}
      <div className={cn("text-[10px] text-zinc-500 truncate font-mono leading-tight", !stepDef.description && "mt-1")}>
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
            <CirclePause className="w-2.5 h-2.5" />
            Awaiting fulfillment
          </span>
        ) : (
          executorSubtitle(stepDef)
        )}
      </div>

      {/* Bottom handle — color-matched to status */}
      <div className={cn(
        "absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-2.5 h-2.5 rounded-full border-2",
        status === "pending" ? "bg-zinc-700 border-zinc-600" :
        status === "running" ? "bg-blue-500/60 border-blue-400/60" :
        status === "completed" ? "bg-emerald-500/60 border-emerald-400/60" :
        status === "failed" ? "bg-red-500/60 border-red-400/60" :
        status === "suspended" ? "bg-amber-500/60 border-amber-400/60" :
        "bg-zinc-700 border-zinc-600"
      )} />

      {/* Rich step tooltip on hover */}
      {showTooltip && hasTooltipContent && (
        <StepTooltip
          stepDef={stepDef}
          latestRun={latestRun}
          latestRuns={latestRuns}
        />
      )}
    </div>
  );
}
