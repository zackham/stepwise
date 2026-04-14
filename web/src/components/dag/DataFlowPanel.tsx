import { useState, type ReactNode } from "react";
import { X, Copy, Check, ArrowRight, ArrowDown, ArrowUp } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { copyToClipboard } from "@/hooks/useCopyFeedback";
import { JsonView } from "@/components/JsonView";
import type { DagSelection } from "@/lib/dag-layout";
import type { ExitRule, InputBinding, Job, StepDefinition, StepRun } from "@/lib/types";
import { executorIcon, executorLabel } from "@/lib/executor-utils";
import { cn, safeRenderValue } from "@/lib/utils";

interface DataFlowPanelProps {
  selection: NonNullable<DagSelection>;
  job: Job;
  latestRuns: Record<string, StepRun>;
  outputs: Record<string, unknown> | null;
  onClose: () => void;
}

const ACTION_COLORS: Record<string, string> = {
  advance: "text-emerald-600 dark:text-emerald-400",
  loop: "text-purple-600 dark:text-purple-400",
  escalate: "text-red-600 dark:text-red-400",
  abandon: "text-red-600 dark:text-red-500",
};

const AGENT_REF_RE = /(\$[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)/g;
const AGENT_REF_TOKEN_RE = /^\$[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$/;

function CopyButton({ value }: { value: unknown }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        copyToClipboard(
          typeof value === "string"
            ? value
            : JSON.stringify(value, null, 2) ?? String(value),
        );
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 p-0.5"
      title="Copy value"
    >
      {copied ? (
        <Check className="w-3 h-3 text-emerald-400" />
      ) : (
        <Copy className="w-3 h-3" />
      )}
    </button>
  );
}

function PanelHeader({
  title,
  badge,
  onClose,
}: {
  title: string;
  badge?: { label: string; icon?: ReactNode } | null;
  onClose: () => void;
}) {
  return (
    <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-zinc-50/50 dark:bg-zinc-950/50 shrink-0">
      <div className="flex items-center gap-2 min-w-0">
        <ArrowRight className="w-3.5 h-3.5 text-zinc-500 shrink-0" />
        <span className="text-xs font-mono font-medium text-zinc-700 dark:text-zinc-300 truncate">
          {title}
        </span>
        {badge && (
          <span className="flex items-center gap-0.5 text-[10px] text-blue-400 bg-blue-500/10 border border-blue-500/20 rounded px-1 py-0 shrink-0">
            {badge.icon}
            {badge.label}
          </span>
        )}
      </div>
      <button
        onClick={onClose}
        className="text-zinc-600 hover:text-zinc-900 dark:hover:text-zinc-300 p-0.5 shrink-0"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-1.5">
      <div className="text-[10px] font-medium text-zinc-500 uppercase tracking-wide">
        {title}
      </div>
      {children}
    </section>
  );
}

function truncateText(text: string, limit = 200): string {
  if (text.length <= limit) return text;
  return `${text.slice(0, limit - 1)}…`;
}

function getStepPreview(stepDef: StepDefinition): {
  label: string;
  text: string;
} | null {
  const prompt = typeof stepDef.executor.config.prompt === "string"
    ? stepDef.executor.config.prompt.trim()
    : "";
  if (stepDef.executor.type === "agent" && prompt) {
    return { label: "Agent Prompt", text: prompt };
  }

  const command = typeof stepDef.executor.config.command === "string"
    ? stepDef.executor.config.command.trim()
    : "";
  if (command) return { label: "Command Preview", text: command };

  if (prompt) return { label: "Prompt Preview", text: prompt };

  return null;
}

function renderAgentPrompt(prompt: string): ReactNode {
  const parts = prompt.split(AGENT_REF_RE);
  return parts.map((part, index) => {
    if (!part) return null;
    if (AGENT_REF_TOKEN_RE.test(part)) {
      return (
        <span
          key={`${part}-${index}`}
          className="text-blue-700 dark:text-blue-300 bg-blue-500/10 rounded px-0.5"
        >
          {part}
        </span>
      );
    }
    return <span key={index}>{part}</span>;
  });
}

function formatBindingSource(binding: InputBinding): string {
  if (binding.any_of_sources?.length) {
    return `any_of(${binding.any_of_sources.map((source) => `${source.step}.${source.field}`).join(", ")})`;
  }
  return `${binding.source_step}.${binding.source_field}`;
}

function bindingSourceStep(binding: InputBinding): string {
  if (binding.any_of_sources?.length) return "any_of";
  return binding.source_step;
}

function bindingSourceField(binding: InputBinding): string {
  if (binding.any_of_sources?.length) {
    return binding.any_of_sources.map((source) => `${source.step}.${source.field}`).join(", ");
  }
  return binding.source_field;
}

function hasOwnKey(
  value: Record<string, unknown> | null | undefined,
  key: string,
): value is Record<string, unknown> {
  return Boolean(value) && Object.prototype.hasOwnProperty.call(value, key);
}

function formatResolvedPreview(value: unknown, maxLen = 80): string {
  const rendered = typeof value === "string" ? value : safeRenderValue(value);
  if (rendered.length <= maxLen) return rendered;
  return `${rendered.slice(0, maxLen - 1)}…`;
}

function isStructuredValue(value: unknown): boolean {
  return Array.isArray(value) || (typeof value === "object" && value !== null);
}

function describeExitRule(rule: ExitRule): { condition: string; action: string } {
  const condition = rule.config.condition != null
    ? safeRenderValue(rule.config.condition)
    : rule.config.field != null
      ? `${safeRenderValue(rule.config.field)} == ${JSON.stringify(rule.config.value)}`
      : safeRenderValue(rule.type);

  const action = safeRenderValue(rule.config.action ?? rule.type);
  const target = rule.config.target != null ? ` -> ${safeRenderValue(rule.config.target)}` : "";
  const maxIterations =
    action === "loop" && rule.config.max_iterations != null
      ? ` (max ${safeRenderValue(rule.config.max_iterations)})`
      : "";

  return {
    condition,
    action: `${action}${target}${maxIterations}`,
  };
}

function StepSummaryPanel({
  stepDef,
  latestRun,
}: {
  stepDef: StepDefinition;
  latestRun: StepRun | null;
}) {
  const preview = getStepPreview(stepDef);
  const isAgent = stepDef.executor.type === "agent";
  const latestInputs = latestRun?.inputs;
  const latestArtifact = latestRun?.result?.artifact;

  return (
    <div className="p-3 space-y-4">
      <div className="space-y-1">
        <h2 className="text-sm font-semibold text-zinc-800 dark:text-zinc-100">
          {stepDef.name}
        </h2>
        {stepDef.description && (
          <p className="text-sm text-muted-foreground">{stepDef.description}</p>
        )}
        <div className="flex items-center gap-1.5 text-xs text-zinc-500">
          <span className="text-zinc-400">
            {executorIcon(stepDef.executor.type, "w-3.5 h-3.5")}
          </span>
          <span>{executorLabel(stepDef.executor.type)}</span>
        </div>
      </div>

      {preview && (
        <Section title={isAgent ? "Agent Prompt" : preview.label}>
          <div
            className={cn(
              "rounded border p-2.5 text-xs font-mono whitespace-pre-wrap break-words",
              isAgent
                ? "bg-blue-500/5 border-blue-500/20 text-zinc-700 dark:text-zinc-200"
                : "bg-zinc-50/50 dark:bg-zinc-900/50 border-zinc-200 dark:border-zinc-800 text-zinc-700 dark:text-zinc-300",
            )}
          >
            {isAgent ? renderAgentPrompt(preview.text) : truncateText(preview.text)}
          </div>
        </Section>
      )}

      <Section title="Input Bindings">
        {stepDef.inputs.length === 0 ? (
          <div className="text-xs text-zinc-600 italic">No input bindings</div>
        ) : (
          <div className="space-y-2">
            {stepDef.inputs.map((binding) => {
              const hasResolvedValue = hasOwnKey(latestInputs, binding.local_name);
              const resolvedValue = hasResolvedValue
                ? latestInputs[binding.local_name]
                : undefined;

              return (
                <div
                  key={binding.local_name}
                  className="rounded border border-zinc-200 dark:border-zinc-800 bg-zinc-50/50 dark:bg-zinc-900/50 p-2"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1 flex flex-wrap items-baseline gap-1 text-[11px] font-mono">
                      <span className="text-cyan-600 dark:text-cyan-400">{binding.local_name}</span>
                      <span className="text-zinc-600">←</span>
                      <span className="text-zinc-500 break-all">
                        {formatBindingSource(binding)}
                      </span>
                      {hasResolvedValue && (
                        <>
                          <span className="text-zinc-600">=</span>
                          <span
                            className="text-emerald-500 dark:text-emerald-400 break-all"
                            title={safeRenderValue(resolvedValue)}
                          >
                            {formatResolvedPreview(resolvedValue)}
                          </span>
                        </>
                      )}
                    </div>
                    {hasResolvedValue && <CopyButton value={resolvedValue} />}
                  </div>
                  <div className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 gap-y-1 text-[10px] font-mono">
                    <span className="text-zinc-500">local_name</span>
                    <span className="text-cyan-400 break-all">{binding.local_name}</span>
                    <span className="text-zinc-500">source_step</span>
                    <span className="text-zinc-500 dark:text-zinc-400 break-all">{bindingSourceStep(binding)}</span>
                    <span className="text-zinc-500">source_field</span>
                    <span className="text-zinc-500 dark:text-zinc-400 break-all">{bindingSourceField(binding)}</span>
                  </div>
                  {hasResolvedValue && isStructuredValue(resolvedValue) && (
                    <div className="mt-2 rounded border border-zinc-200 dark:border-zinc-800 bg-zinc-100/30 dark:bg-zinc-950/30 p-2 overflow-x-auto">
                      <JsonView data={resolvedValue} defaultExpanded={true} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Section>

      <Section title="Outputs">
        {stepDef.outputs.length === 0 ? (
          <div className="text-xs text-zinc-600 italic">No declared outputs</div>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {stepDef.outputs.map((output) => {
              const hasValue = hasOwnKey(latestArtifact ?? null, output);
              return (
                <span
                  key={output}
                  className={cn(
                    "text-[11px] font-mono rounded px-2 py-1 border",
                    hasValue
                      ? "text-emerald-600 dark:text-emerald-400 bg-emerald-100 dark:bg-emerald-500/10 border-emerald-500/20"
                      : "text-zinc-500 bg-zinc-100/70 dark:bg-zinc-900/70 border-zinc-200 dark:border-zinc-800",
                  )}
                >
                  {output}
                </span>
              );
            })}
          </div>
        )}
      </Section>

      <Section title="Exit Rules">
        {stepDef.exit_rules.length === 0 ? (
          <div className="text-xs text-zinc-600 italic">No explicit exit rules</div>
        ) : (
          <div className="space-y-2">
            {stepDef.exit_rules.map((rule) => {
              const summary = describeExitRule(rule);
              const action = safeRenderValue(rule.config.action ?? rule.type);
              return (
                <div
                  key={rule.name}
                  className="rounded border border-zinc-200 dark:border-zinc-800 bg-zinc-50/50 dark:bg-zinc-900/50 p-2 space-y-1"
                >
                  <div className="flex items-center gap-2 flex-wrap text-[11px] font-mono">
                    <span className="text-zinc-700 dark:text-zinc-200">
                      {safeRenderValue(rule.name)}
                    </span>
                    <span className={cn("font-medium", ACTION_COLORS[action] ?? "text-zinc-400")}>
                      {summary.action}
                    </span>
                  </div>
                  <div className="text-[11px] font-mono text-zinc-500 break-all">
                    when: {summary.condition}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Section>
    </div>
  );
}

export function DataFlowPanel({
  selection,
  job,
  latestRuns,
  outputs,
  onClose,
}: DataFlowPanelProps) {
  if (selection.kind === "step") {
    const stepDef = job.workflow.steps[selection.stepName];
    const latestRun = latestRuns[selection.stepName] ?? null;

    return (
      <>
        <PanelHeader
          title="Step Inspector"
          badge={
            stepDef
              ? {
                  label: executorLabel(stepDef.executor.type),
                  icon: executorIcon(stepDef.executor.type, "w-2.5 h-2.5"),
                }
              : null
          }
          onClose={onClose}
        />
        <ScrollArea className="flex-1">
          {stepDef ? (
            <StepSummaryPanel stepDef={stepDef} latestRun={latestRun} />
          ) : (
            <div className="p-3 text-xs text-zinc-600 italic">
              Step definition not found in this workflow
            </div>
          )}
        </ScrollArea>
      </>
    );
  }

  let title = "";
  let badge: { label: string; icon?: ReactNode } | null = null;
  let context = "";
  let value: unknown = undefined;
  let noDataMessage = "";

  if (selection.kind === "edge-field") {
    title = selection.fieldName;
    const isFromFlowInput = selection.fromStep === "__flow_input__";
    context = isFromFlowInput
      ? `Job Input -> ${selection.toStep}`
      : `${selection.fromStep} -> ${selection.toStep}`;
    if (isFromFlowInput) {
      if (job.inputs && selection.fieldName in job.inputs) {
        value = job.inputs[selection.fieldName];
      } else {
        noDataMessage = "Input not provided";
      }
    } else {
      const run = latestRuns[selection.fromStep];
      if (run?.result?.artifact) {
        value = run.result.artifact[selection.fieldName];
      } else {
        noDataMessage = "Source step has not completed";
      }
    }
  } else if (selection.kind === "flow-input") {
    title = selection.fieldName;
    badge = {
      label: "Job Input",
      icon: <ArrowDown className="w-2.5 h-2.5" />,
    };
    if (job.inputs && selection.fieldName in job.inputs) {
      value = job.inputs[selection.fieldName];
    } else {
      noDataMessage = "Input not provided";
    }
  } else if (selection.kind === "flow-output") {
    title = selection.fieldName;
    badge = {
      label: "Flow Output",
      icon: <ArrowUp className="w-2.5 h-2.5" />,
    };
    context = `from ${selection.stepName}`;
    const run = latestRuns[selection.stepName];
    if (run?.result?.artifact) {
      value = run.result.artifact[selection.fieldName];
    } else if (outputs && selection.fieldName in outputs) {
      value = outputs[selection.fieldName];
    } else {
      const isTerminal =
        job.status === "completed" ||
        job.status === "failed" ||
        job.status === "cancelled";
      noDataMessage = isTerminal
        ? "No output data available"
        : "Job has not completed";
    }
  }

  return (
    <>
      <PanelHeader title={title} badge={badge} onClose={onClose} />

      <ScrollArea className="flex-1">
        <div className="p-3 space-y-3">
          {context && (
            <div className="text-[10px] text-zinc-500 font-mono">{context}</div>
          )}

          {noDataMessage ? (
            <div className="text-xs text-zinc-600 italic py-4 text-center">
              {noDataMessage}
            </div>
          ) : (
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <span className="text-[10px] font-medium text-zinc-500 uppercase tracking-wide">
                  Value
                </span>
                <CopyButton value={value} />
              </div>
              <div className="bg-zinc-50/50 dark:bg-zinc-900/50 rounded border border-zinc-200 dark:border-zinc-800 p-2 overflow-x-auto">
                <JsonView data={value} defaultExpanded={true} />
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
    </>
  );
}
