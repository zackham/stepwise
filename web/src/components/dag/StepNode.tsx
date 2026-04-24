import { type KeyboardEvent, type ReactNode, useState, useRef, useCallback } from "react";
import { createPortal } from "react-dom";
import {
  CirclePause,
  RotateCw,
  ChevronDown,
  Clock,
  ArrowRight,
  ArrowLeft,
  Layers,
  RefreshCw,
  XCircle,
  Link2,
  Copy,
  Check,
} from "lucide-react";
import { ContentModal } from "@/components/ui/content-modal";
import { copyToClipboard } from "@/hooks/useCopyFeedback";
import { StepStatusBadge } from "@/components/StatusBadge";
import {
  STEP_STATUS_COLORS,
  STEP_PENDING_COLORS,
  STEP_DISPLAY_COLORS,
} from "@/lib/status-colors";
import { EntityContextMenu } from "@/components/menus/EntityContextMenu";
import type { StepEntity } from "@/lib/actions/step-actions";
import type {
  ExitRule,
  StepDefinition,
  StepDisplayStatus,
  StepRun,
} from "@/lib/types";
import { cn, safeRenderValue } from "@/lib/utils";
import { LiveDuration } from "@/components/LiveDuration";
import { executorIcon, executorLabel } from "@/lib/executor-utils";

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        copyToClipboard(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="text-zinc-500 hover:text-zinc-300 transition-colors cursor-pointer shrink-0 p-0.5"
      title="Copy to clipboard"
    >
      {copied ? <Check className="w-3 h-3 text-green-400" /> : <Copy className="w-3 h-3" />}
    </button>
  );
}

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
  isMultiSelected?: boolean;
  onClick: () => void;
  onMultiSelectToggle?: () => void;
  onRerunStep?: (stepName: string) => void;
  onCancelRun?: (runId: string) => void;
  onNavigateSubJob?: (subJobId: string) => void;
  onToggleExpand?: () => void;
  childStepCount?: number;
  childJobStatus?: string | null;
  flowStatus?: string;
  isCritical?: boolean;
  isNested?: boolean;
  jobId?: string;
  zoomScale?: number;
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
      const agent = typeof config.agent === "string" ? config.agent : undefined;
      return agent ? `Agent · ${agent}` : "Agent";
    }
    default:
      return type;
  }
}

const ACTION_COLORS: Record<string, string> = {
  advance: "text-emerald-600 dark:text-emerald-400",
  loop: "text-purple-600 dark:text-purple-400",
  escalate: "text-red-600 dark:text-red-400",
  abandon: "text-red-600 dark:text-red-500",
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
      <div className="text-[10px] font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wide mb-1">
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

/* ── Port tooltip / popover helpers ────────────────────────────── */

/** Interactive port dot with hover tooltip and click modal */
function PortDot({
  position,
  colorClasses,
  tooltipContent,
  popoverContent,
  modalTitle,
  zoomScale = 1,
}: {
  position: "top" | "bottom";
  colorClasses: string;
  tooltipContent: ReactNode | null;
  popoverContent: ReactNode | null;
  modalTitle?: string;
  zoomScale?: number;
}) {
  const [hovered, setHovered] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const hoverTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const leaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleEnter = useCallback(() => {
    if (modalOpen) return;
    if (leaveTimer.current) { clearTimeout(leaveTimer.current); leaveTimer.current = null; }
    hoverTimer.current = setTimeout(() => setHovered(true), 250);
  }, [modalOpen]);

  const handleLeave = useCallback(() => {
    if (hoverTimer.current) {
      clearTimeout(hoverTimer.current);
      hoverTimer.current = null;
    }
    setHovered(false);
  }, []);

  const modalClosedAt = useRef(0);
  const handleClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    if (!popoverContent) return;
    // Prevent immediate reopen after modal close
    if (Date.now() - modalClosedAt.current < 300) return;
    setModalOpen(true);
    setHovered(false);
  }, [popoverContent]);
  const handleModalChange = useCallback((open: boolean) => {
    setModalOpen(open);
    if (!open) modalClosedAt.current = Date.now();
  }, []);

  const isTop = position === "top";
  const hasInteraction = !!tooltipContent || !!popoverContent;

  const dotRef = useRef<HTMLDivElement>(null);

  // Get screen position of the dot for portaled tooltip
  const getTooltipStyle = useCallback((): React.CSSProperties => {
    const el = dotRef.current;
    if (!el) return { display: "none" };
    const rect = el.getBoundingClientRect();
    const left = rect.left + rect.width / 2;
    if (isTop) {
      return { position: "fixed", left, bottom: window.innerHeight - rect.top + 6, transform: "translateX(-50%)", zIndex: 99999 };
    }
    return { position: "fixed", left, top: rect.bottom + 6, transform: "translateX(-50%)", zIndex: 99999 };
  }, [isTop]);

  return (
    <div
      ref={dotRef}
      className={cn(
        "absolute left-1/2 -translate-x-1/2 w-2.5 h-2.5 rounded-full border-2",
        isTop ? "-top-1.5" : "-bottom-1.5",
        colorClasses,
        hasInteraction && "cursor-pointer hover:brightness-125 transition-colors duration-100"
      )}
      onMouseEnter={hasInteraction ? handleEnter : undefined}
      onMouseLeave={hasInteraction ? handleLeave : undefined}
      onClick={hasInteraction ? handleClick : undefined}
    >
      {/* Hover tooltip — portaled to body to escape stacking contexts */}
      {hovered && tooltipContent && !modalOpen && createPortal(
        <div
          className="pointer-events-none"
          style={getTooltipStyle()}
        >
          <div className="bg-zinc-900 border border-zinc-700 rounded-md shadow-xl p-2 min-w-[300px] max-w-[500px]">
            <div
              className="max-h-[230px] overflow-hidden"
              style={{ maskImage: "linear-gradient(to bottom, black 75%, transparent 100%)", WebkitMaskImage: "linear-gradient(to bottom, black 75%, transparent 100%)" }}
            >
              {tooltipContent}
            </div>
          </div>
        </div>,
        document.body,
      )}

      {/* Click modal */}
      <ContentModal
        open={modalOpen}
        onOpenChange={handleModalChange}
        title={modalTitle ?? (isTop ? "Inputs" : "Outputs")}
      >
        <div className="p-3">
          {popoverContent}
        </div>
      </ContentModal>
    </div>
  );
}

/** Build tooltip + popover content for the input port (top dot) */
function useInputPortContent(
  stepDef: StepDefinition,
  latestRun: StepRun | null,
  latestRuns?: Record<string, StepRun>,
) {
  const inputs = stepDef.inputs;
  if (inputs.length === 0) return { tooltipContent: null, popoverContent: null };

  // Check if any realized input values exist
  const realizedInputs: Record<string, unknown> = {};
  const bindingLines: string[] = [];
  for (const inp of inputs) {
    const binding = inp.any_of_sources
      ? `any_of(${inp.any_of_sources.map((s) => `${s.step}.${s.field}`).join(", ")})`
      : `${inp.source_step}.${inp.source_field}`;
    bindingLines.push(`${inp.local_name} ← ${binding}`);

    // Check realized value from upstream run artifacts
    const sourceRun = inp.source_step && inp.source_step !== "$job"
      ? latestRuns?.[inp.source_step]
      : null;
    const val = sourceRun?.result?.artifact?.[inp.source_field];
    if (val !== undefined) realizedInputs[inp.local_name] = val;

    // Also check latestRun.inputs for job-level inputs
    if (inp.source_step === "$job" && latestRun?.inputs) {
      const jobVal = latestRun.inputs[inp.source_field];
      if (jobVal !== undefined) realizedInputs[inp.local_name] = jobVal;
    }
  }

  const tooltipContent = (
    <div className="flex flex-col gap-2">
      {inputs.slice(0, 4).map((inp) => {
        const binding = inp.any_of_sources
          ? `any_of(${inp.any_of_sources.map((s) => `${s.step}.${s.field}`).join(", ")})`
          : `${inp.source_step}.${inp.source_field}`;
        const realized = realizedInputs[inp.local_name];
        return (
          <div key={inp.local_name}>
            <div className="text-[10px] font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wide mb-1">
              {inp.local_name}
              <span className="normal-case font-normal text-zinc-600 dark:text-zinc-500 ml-1.5">← {binding}</span>
            </div>
            {realized !== undefined && (
              <pre className="text-[11px] font-mono text-zinc-800 dark:text-zinc-200 whitespace-pre-wrap break-words max-w-[280px] max-h-[200px] overflow-auto m-0">
                {typeof realized === "string" ? realized : JSON.stringify(realized, null, 2)}
              </pre>
            )}
          </div>
        );
      })}
      {inputs.length > 4 && (
        <div className="text-[10px] text-zinc-500">+{inputs.length - 4} more</div>
      )}
    </div>
  );

  // Modal: full detail
  const popoverContent = (
    <div className="p-3 space-y-3">
      {inputs.map((inp) => {
        const binding = inp.any_of_sources
          ? `any_of(${inp.any_of_sources.map((s) => `${s.step}.${s.field}`).join(", ")})`
          : `${inp.source_step}.${inp.source_field}`;
        const realized = realizedInputs[inp.local_name];
        const valueStr = realized !== undefined
          ? (typeof realized === "string" ? realized : JSON.stringify(realized, null, 2))
          : null;
        return (
          <div key={inp.local_name}>
            <div className="flex items-center gap-2 text-xs text-zinc-400 mb-1">
              <span className="font-medium text-zinc-200">{inp.local_name}</span>
              <span className="text-zinc-600">←</span>
              <span>{binding}</span>
              {valueStr && <span className="ml-auto"><CopyButton text={valueStr} /></span>}
            </div>
            {valueStr && (
              <pre className="text-xs font-mono text-zinc-300 bg-zinc-950 rounded p-2 overflow-auto max-h-[200px] whitespace-pre-wrap break-words">
                {valueStr}
              </pre>
            )}
          </div>
        );
      })}
    </div>
  );

  return { tooltipContent, popoverContent };
}

/** Build tooltip + popover content for the output port (bottom dot) */
function useOutputPortContent(
  stepDef: StepDefinition,
  latestRun: StepRun | null,
) {
  const outputs = stepDef.outputs;
  if (outputs.length === 0) return { tooltipContent: null, popoverContent: null };

  const artifact = latestRun?.result?.artifact;
  const realizedOutputs: Record<string, unknown> = {};
  for (const name of outputs) {
    const val = artifact?.[name];
    if (val !== undefined) realizedOutputs[name] = val;
  }
  const tooltipContent = (
    <div className="flex flex-col gap-2">
      {outputs.slice(0, 4).map((name) => {
        const realized = realizedOutputs[name];
        return (
          <div key={name}>
            <div className="text-[10px] font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wide mb-1">
              {name}
            </div>
            {realized !== undefined ? (
              <pre className="text-[11px] font-mono text-zinc-800 dark:text-zinc-200 whitespace-pre-wrap break-words max-w-[280px] max-h-[200px] overflow-auto m-0">
                {typeof realized === "string" ? realized : JSON.stringify(realized, null, 2)}
              </pre>
            ) : (
              <span className="text-[11px] font-mono text-zinc-600">(pending)</span>
            )}
          </div>
        );
      })}
      {outputs.length > 4 && (
        <div className="text-[10px] text-zinc-500">+{outputs.length - 4} more</div>
      )}
    </div>
  );

  // Modal: full detail
  const popoverContent = (
    <div className="p-3 space-y-3">
      {outputs.map((name) => {
        const realized = realizedOutputs[name];
        const valueStr = realized !== undefined
          ? (typeof realized === "string" ? realized : JSON.stringify(realized, null, 2))
          : null;
        return (
          <div key={name}>
            <div className="flex items-center gap-2 text-xs mb-1">
              <span className="font-medium text-zinc-200">{name}</span>
              {valueStr && <span className="ml-auto"><CopyButton text={valueStr} /></span>}
            </div>
            {valueStr ? (
              <pre className="text-xs font-mono text-zinc-300 bg-zinc-950 rounded p-2 overflow-auto max-h-[200px] whitespace-pre-wrap break-words">
                {valueStr}
              </pre>
            ) : (
              <span className="text-xs text-zinc-600">(pending)</span>
            )}
          </div>
        );
      })}
    </div>
  );

  return { tooltipContent, popoverContent };
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
    <div className="absolute left-0 top-full mt-1 z-50 bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-md shadow-xl p-2 min-w-[280px] max-w-[400px]">
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
            when: {typeof stepDef.when === "string" ? (stepDef.when.length > 30 ? stepDef.when.slice(0, 28) + "..." : stepDef.when) : JSON.stringify(stepDef.when)}
          </span>
        )}
      </div>

      {/* Inputs */}
      {hasInputs && (
        <div className="mb-2">
          <div className="flex items-center gap-1 text-[10px] font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wide mb-0.5">
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
          <div className="flex items-center gap-1 text-[10px] font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wide mb-0.5">
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
  isMultiSelected,
  onClick,
  onMultiSelectToggle,
  onRerunStep,
  onCancelRun,
  onNavigateSubJob,
  onToggleExpand,
  childStepCount,
  childJobStatus,
  flowStatus,
  isCritical,
  isNested,
  jobId,
  zoomScale = 1,
  x,
  y,
  width,
  height,
}: StepNodeProps) {
  const [showTooltip, setShowTooltip] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onClick();
    }
  };

  const handleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if ((e.metaKey || e.ctrlKey) && onMultiSelectToggle) {
      e.stopPropagation();
      onMultiSelectToggle();
      return;
    }
    onClick();
  };

  const isWaitingReset =
    latestRun?.status === "running" &&
    !!(latestRun?.executor_state as Record<string, unknown> | undefined)?.usage_limit_waiting;

  // Derive the badge's display status — escalate + strand are decorations
  // on top of the engine's raw StepRunStatus. We check them in priority
  // order: STRANDED wins over ESCALATED (if somehow both held, the
  // stranded process is the more urgent hazard); ESCALATED wins over the
  // underlying completed status because the step's outcome caused the
  // job to pause and that matters more than "it finished."
  const rawStatus = latestRun?.status;
  const isStranded = latestRun?.is_stranded === true;
  const isEscalated = latestRun?.exit_rule?.action === "escalate";
  const status: StepDisplayStatus = isStranded
    ? "stranded"
    : isEscalated
      ? "escalated"
      : isWaitingReset
        ? "waiting_reset"
        : rawStatus ?? (flowStatus === "throttled" ? "throttled" : "pending");
  const subJobId = latestRun?.sub_job_id ?? null;
  const colors =
    status === "pending"
      ? STEP_PENDING_COLORS
      : status === "escalated" || status === "stranded"
        ? STEP_DISPLAY_COLORS[status]
        : STEP_STATUS_COLORS[status];

  // Temporal-ordering annotation: did this run start within 250ms of a
  // sibling run? Exposes parallel starts that the static DAG hides —
  // see the gumball text-quality-check race (issue surfaced in cycle 91
  // stepwise review). We keep the threshold small so only genuine
  // scheduler-level ties are flagged, not user-triggered concurrency.
  const PARALLEL_WINDOW_MS = 250;
  const startedAt = latestRun?.started_at
    ? new Date(latestRun.started_at).getTime()
    : null;
  const parallelSiblings: string[] = [];
  if (startedAt != null && latestRuns) {
    for (const [otherName, otherRun] of Object.entries(latestRuns)) {
      if (otherName === stepDef.name) continue;
      if (!otherRun?.started_at) continue;
      const otherStarted = new Date(otherRun.started_at).getTime();
      if (Math.abs(otherStarted - startedAt) <= PARALLEL_WINDOW_MS) {
        parallelSiblings.push(otherName);
      }
    }
  }

  const canRerun =
    !latestRun ||
    latestRun.status === "completed" ||
    latestRun.status === "failed" ||
    latestRun.status === "cancelled";
  const canCancelRun =
    latestRun?.status === "running" || latestRun?.status === "suspended";
  const showActions = isHovered && (canRerun || canCancelRun);

  const isSuspended =
    latestRun?.status === "suspended" &&
    latestRun?.watch?.mode === "external";

  const attempt = latestRun?.attempt ?? 0;
  const showAttemptBadge = attempt > 1 || (maxAttempts != null && attempt >= 1);

  const hasSession =
    !!stepDef.session ||
    stepDef.executor?.config?.continue_session === true ||
    stepDef.inputs?.some((i) => i.source_field === "_session_id");
  const sessionName = stepDef.session ?? (latestRun?.executor_state as Record<string, unknown> | undefined)?.session_name as string | undefined;

  const hasTooltipContent =
    stepDef.exit_rules.length > 0 ||
    stepDef.inputs.length > 0 ||
    stepDef.outputs.length > 0 ||
    !!stepDef.when;

  // Port dot color classes (shared by top and bottom handles)
  const portColorClasses =
    status === "pending" ? "bg-zinc-300 border-zinc-400 dark:bg-zinc-700 dark:border-zinc-600" :
    status === "running" ? "bg-blue-500/60 border-blue-400/60" :
    status === "completed" ? "bg-emerald-500/60 border-emerald-400/60" :
    status === "failed" ? "bg-red-500/60 border-red-400/60" :
    status === "suspended" ? "bg-amber-500/60 border-amber-400/60" :
    status === "waiting_reset" ? "bg-amber-600/60 border-amber-500/60" :
    status === "throttled" ? "bg-orange-500/60 border-orange-400/60" :
    status === "escalated" ? "bg-red-500/60 border-red-400/60" :
    status === "stranded" ? "bg-amber-500/60 border-amber-400/60" :
    "bg-zinc-300 border-zinc-400 dark:bg-zinc-700 dark:border-zinc-600";

  // Port hover/click content
  const inputPort = useInputPortContent(stepDef, latestRun, latestRuns);
  const outputPort = useOutputPortContent(stepDef, latestRun);

  const handleMouseEnter = () => {
    setIsHovered(true);
    if (!hasTooltipContent) return;
    hoverTimerRef.current = setTimeout(() => setShowTooltip(true), 300);
  };

  const handleMouseLeave = () => {
    setIsHovered(false);
    if (hoverTimerRef.current) {
      clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = null;
    }
    setShowTooltip(false);
  };

  const wrapWithContextMenu = !isNested && !!jobId;

  const nodeContent = (
    <div
      className={cn(
        !wrapWithContextMenu && "absolute",
        "cursor-pointer border border-l-[3px] rounded-lg p-3 pl-2.5",
        "transition-all duration-200",
        colors.bg,
        colors.border,
        getExecutorAccent(stepDef.executor.type),
        isSelected && `ring-2 ring-blue-500 dark:ring-blue-400 shadow-lg shadow-blue-500/20 dark:shadow-blue-400/20 brightness-110`,
        isMultiSelected && "ring-2 ring-purple-400/70 shadow-lg shadow-purple-500/10",
        !isSelected && !isMultiSelected && "hover:shadow-md hover:brightness-110 focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:shadow-lg",
        status === "running" && "step-running-glow",
        isCritical && !isSelected && !isMultiSelected && "ring-1 ring-amber-400/60"
      )}
      role="button"
      tabIndex={0}
      style={wrapWithContextMenu ? { width, height } : { left: x, top: y, width, height }}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      {/* Top handle (input port) — color-matched to status */}
      <PortDot
        position="top"
        colorClasses={portColorClasses}
        tooltipContent={inputPort.tooltipContent}
        popoverContent={inputPort.popoverContent}
        modalTitle={`${stepDef.name} — Inputs`}
        zoomScale={zoomScale}
      />

      {/* Content */}
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className={cn("shrink-0", colors.text)}>
            {executorIcon(stepDef.executor.type, "w-3.5 h-3.5")}
          </span>
          <span className="text-sm font-medium truncate text-foreground">
            {stepDef.name}
          </span>
          {hasSession && (
            <span className="text-violet-400/60 shrink-0" title={sessionName ? `Session: ${sessionName}` : "Session step"}>
              <Link2 className="w-2.5 h-2.5 inline" />
            </span>
          )}
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
      {latestRun && (status === "completed" || status === "failed" || status === "running" || status === "escalated" || status === "stranded") && (
        <div className="flex items-center gap-0.5 text-[9px] text-zinc-500 mt-0.5">
          <Clock className="w-2.5 h-2.5" />
          <LiveDuration startTime={latestRun.started_at} endTime={latestRun.completed_at} />
        </div>
      )}

      {/* ESCALATED annotation: the step caused the job to pause. */}
      {status === "escalated" && latestRun?.exit_rule && (
        <div
          className="mt-0.5 text-[9px] text-red-700 dark:text-red-300 font-mono truncate"
          title={`Exit rule '${latestRun.exit_rule.rule}' resolved with action=escalate — this paused the job.`}
        >
          ↑ escalated via rule: {latestRun.exit_rule.rule ?? "—"}
        </div>
      )}

      {/* STRANDED annotation: the job is paused but the run is still live. */}
      {status === "stranded" && (
        <div
          className="mt-0.5 text-[9px] text-amber-700 dark:text-amber-300 font-mono truncate"
          title="Job is paused; this run's completion won't be processed until the job is resumed. Any live executor process is idle."
        >
          ⚠ stranded — job paused, run orphaned
        </div>
      )}

      {/* TEMPORAL ORDER annotation: sibling started within ~250ms of this
          step. Exposes races that the static DAG topology otherwise
          hides (e.g. the gumball text-quality-check / initial-check
          race that caused the original escalate-mid-repair bug). */}
      {parallelSiblings.length > 0 && (
        <div
          className="mt-0.5 text-[9px] text-amber-600 dark:text-amber-400 font-mono truncate"
          title={`Started in parallel with: ${parallelSiblings.join(", ")}. If any of these is a dependency or prerequisite, the flow likely has a missing ordering constraint (after / after_resolved).`}
        >
          ⇉ parallel with {parallelSiblings.length === 1 ? parallelSiblings[0] : `${parallelSiblings.length} siblings`}
        </div>
      )}

      {/* Description */}
      {stepDef.description && (
        <div className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-400 truncate leading-tight">
          {stepDef.description}
        </div>
      )}

      {/* Executor subtitle */}
      <div className={cn("text-[10px] text-zinc-500 truncate font-mono leading-tight", !stepDef.description && "mt-1")}>
        {onToggleExpand ? (
          <button
            className="flex items-center gap-1 text-purple-400 hover:text-purple-300 transition-colors cursor-pointer"
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
            className="flex items-center gap-1 text-purple-400 hover:text-purple-300 transition-colors cursor-pointer"
            onClick={(e) => {
              e.stopPropagation();
              onNavigateSubJob(subJobId);
            }}
          >
            <Layers className="w-2.5 h-2.5" />
            Sub-job →
          </button>
        ) : status === "throttled" ? (
          <span className="flex items-center gap-1 text-orange-400">
            <Clock className="w-2.5 h-2.5" />
            Waiting for executor slot
          </span>
        ) : isWaitingReset ? (
          <span className="flex items-center gap-1 text-amber-500">
            <Clock className="w-2.5 h-2.5" />
            Resumes {(latestRun?.executor_state as Record<string, unknown>)?.reset_at
              ? new Date(String((latestRun?.executor_state as Record<string, unknown>).reset_at)).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})
              : "soon"}
          </span>
        ) : isSuspended ? (
          <span className="flex items-center gap-1 text-amber-400">
            <CirclePause className="w-2.5 h-2.5" />
            Awaiting fulfillment
          </span>
        ) : stepDef.session ? (
          <div className="flex flex-col">
            <span className="text-violet-400/80">({stepDef.session})</span>
            {stepDef.fork_from && <span className="text-violet-400/50 text-[9px]">forked from {stepDef.fork_from}</span>}
          </div>
        ) : (
          executorSubtitle(stepDef)
        )}
      </div>

      {/* Bottom handle (output port) — color-matched to status */}
      <PortDot
        position="bottom"
        colorClasses={portColorClasses}
        tooltipContent={outputPort.tooltipContent}
        popoverContent={outputPort.popoverContent}
        modalTitle={`${stepDef.name} — Outputs`}
        zoomScale={zoomScale}
      />

      {/* Hover action buttons */}
      {showActions && (
        <div
          className="absolute -top-8 right-0 flex items-center gap-0.5 bg-white/95 dark:bg-zinc-900/95 backdrop-blur-sm rounded-md border border-zinc-300 dark:border-zinc-700 shadow-lg px-0.5 py-0.5"
          onClick={(e) => e.stopPropagation()}
        >
          {canRerun && onRerunStep && (
            <button
              className="flex items-center gap-1 text-[10px] text-blue-400 hover:bg-blue-500/15 rounded px-1.5 py-0.5 transition-colors cursor-pointer"
              onClick={(e) => {
                e.stopPropagation();
                onRerunStep(stepDef.name);
              }}
              title="Rerun step"
            >
              <RefreshCw className="w-3 h-3" />
              Rerun
            </button>
          )}
          {canCancelRun && onCancelRun && latestRun && (
            <button
              className="flex items-center gap-1 text-[10px] text-red-400 hover:bg-red-500/15 rounded px-1.5 py-0.5 transition-colors cursor-pointer"
              onClick={(e) => {
                e.stopPropagation();
                onCancelRun(latestRun.id);
              }}
              title="Cancel run"
            >
              <XCircle className="w-3 h-3" />
              Cancel
            </button>
          )}
        </div>
      )}

      {/* Rich step tooltip on hover */}
      {showTooltip && hasTooltipContent && !showActions && (
        <StepTooltip
          stepDef={stepDef}
          latestRun={latestRun}
          latestRuns={latestRuns}
        />
      )}
    </div>
  );

  if (!wrapWithContextMenu) return nodeContent;

  const stepEntity: StepEntity = { jobId: jobId!, stepDef, latestRun };
  return (
    <EntityContextMenu type="step" data={stepEntity} className="absolute" style={{ left: x, top: y, width, height }} stopPropagation>
      {nodeContent}
    </EntityContextMenu>
  );
}
