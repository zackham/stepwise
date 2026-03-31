import type { StepDefinition } from "@/lib/types";
import { JsonView } from "@/components/JsonView";
import { executorIcon, executorLabel } from "@/lib/executor-utils";
import { cn, safeRenderValue } from "@/lib/utils";
import { useState } from "react";
import { ContentModal } from "@/components/ui/content-modal";
import { Gauge } from "lucide-react";

interface StepConfigViewProps {
  stepDef: StepDefinition;
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-medium text-zinc-500 uppercase tracking-wider">
      {children}
    </div>
  );
}

const ACTION_COLORS: Record<string, string> = {
  advance: "text-emerald-600 dark:text-emerald-400",
  loop: "text-purple-600 dark:text-purple-400",
  escalate: "text-red-600 dark:text-red-400",
  abandon: "text-red-600 dark:text-red-500",
};

const AGENT_REF_RE = /(\$[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)/g;
const AGENT_REF_TOKEN_RE = /^\$[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$/;

function renderAgentPrompt(prompt: string): React.ReactNode {
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

export function StepConfigView({ stepDef }: StepConfigViewProps) {
  const [promptModalOpen, setPromptModalOpen] = useState(false);
  const isAgent = stepDef.executor.type === "agent";
  const isScript = stepDef.executor.type === "script";
  const isExternal = stepDef.executor.type === "external";
  const hasPrompt = (isAgent || isExternal) && typeof stepDef.executor.config.prompt === "string" && stepDef.executor.config.prompt.trim();
  const hasCommand = isScript && typeof stepDef.executor.config.command === "string" && stepDef.executor.config.command.trim();

  const promptTemplate = typeof stepDef.executor.config.prompt === "string" ? stepDef.executor.config.prompt.trim() : "";
  const commandTemplate = typeof stepDef.executor.config.command === "string" ? stepDef.executor.config.command.trim() : "";

  return (
    <div className="p-3 space-y-4 animate-step-fade">
      {/* 1. Header — step name + type + description */}
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-zinc-800 dark:text-zinc-100">
            {stepDef.name}
          </h3>
          <span className="flex items-center gap-1 text-[10px] font-mono text-zinc-400 bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 rounded px-1.5 py-0.5">
            {executorIcon(stepDef.executor.type, "w-2.5 h-2.5")}
            {executorLabel(stepDef.executor.type)}
          </span>
        </div>
        {stepDef.description && (
          <p className="text-xs text-muted-foreground">{stepDef.description}</p>
        )}
      </div>

      {/* 2. Input bindings */}
      {stepDef.inputs.length > 0 && (
        <div className="space-y-1.5">
          <SectionHeading>Inputs</SectionHeading>
          <div className="space-y-1">
            {stepDef.inputs.map((b) => (
              <div key={b.local_name} className="text-xs font-mono bg-zinc-100/50 dark:bg-zinc-900/50 rounded px-2 py-1">
                <span className="text-cyan-600 dark:text-cyan-400">{b.local_name}</span>
                <span className="text-zinc-500"> &larr; </span>
                <span className="text-zinc-500 dark:text-zinc-400">
                  {b.source_step === "$job"
                    ? `$job.${b.source_field}`
                    : b.any_of_sources?.length
                      ? `any_of(${b.any_of_sources.map((s) => `${s.step}.${s.field}`).join(", ")})`
                      : `${b.source_step}.${b.source_field}`}
                </span>
                {b.any_of_sources?.length === 0 && b.source_step !== "$job" && (
                  <span className="text-zinc-600 ml-1">(optional)</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 3. Output fields */}
      {stepDef.outputs.length > 0 && (
        <div className="space-y-1.5">
          <SectionHeading>Outputs</SectionHeading>
          <div className="flex flex-wrap gap-1.5">
            {stepDef.outputs.map((output) => (
              <span
                key={output}
                className="text-[11px] font-mono text-zinc-500 bg-zinc-100/70 dark:bg-zinc-900/70 border border-zinc-200 dark:border-zinc-800 rounded px-2 py-1"
              >
                {output}
                {stepDef.output_schema?.[output] && (
                  <span className="text-zinc-500 ml-1">: {stepDef.output_schema[output].type}</span>
                )}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* 4. Prompt template */}
      {hasPrompt && (
        <div className="space-y-1.5">
          <SectionHeading>Prompt Template</SectionHeading>
          <div
            onClick={() => setPromptModalOpen(true)}
            className={cn(
              "text-xs font-mono rounded border p-2 whitespace-pre-wrap break-all cursor-pointer hover:bg-zinc-50/80 dark:hover:bg-zinc-900/70 transition-colors line-clamp-4",
              isAgent
                ? "bg-blue-500/5 border-blue-500/20 text-zinc-700 dark:text-zinc-200"
                : "bg-zinc-50/50 dark:bg-zinc-900/50 border-zinc-200 dark:border-zinc-800 text-zinc-700 dark:text-zinc-300"
            )}
          >
            {isAgent ? renderAgentPrompt(promptTemplate) : promptTemplate}
          </div>
          <ContentModal
            open={promptModalOpen}
            onOpenChange={setPromptModalOpen}
            title="Prompt Template"
            copyContent={promptTemplate}
          >
            <pre className="whitespace-pre-wrap text-sm text-zinc-300 font-mono p-2">
              {promptTemplate}
            </pre>
          </ContentModal>
        </div>
      )}

      {/* 5. When condition */}
      {stepDef.when && (
        <div className="space-y-1.5">
          <SectionHeading>When</SectionHeading>
          <code className="text-xs font-mono text-amber-400/80 bg-zinc-900/50 px-1.5 py-0.5 rounded">
            {stepDef.when}
          </code>
        </div>
      )}

      {/* 6. Exit rules */}
      {stepDef.exit_rules.length > 0 && (
        <div className="space-y-1.5">
          <SectionHeading>Exit Rules</SectionHeading>
          <div className="space-y-1">
            {stepDef.exit_rules.map((rule) => {
              const action = safeRenderValue(rule.config.action ?? rule.type);
              const condition = rule.config.condition != null
                ? safeRenderValue(rule.config.condition)
                : rule.config.field != null
                  ? `${safeRenderValue(rule.config.field)} == ${JSON.stringify(rule.config.value)}`
                  : safeRenderValue(rule.type);
              const target = rule.config.target ? ` ${safeRenderValue(rule.config.target)}` : "";
              const maxIter = action === "loop" && rule.config.max_iterations != null
                ? ` (max ${safeRenderValue(rule.config.max_iterations)})`
                : "";

              return (
                <div
                  key={rule.name}
                  className="text-xs font-mono bg-zinc-100/50 dark:bg-zinc-900/50 rounded px-2 py-1 space-y-0.5"
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-zinc-700 dark:text-zinc-200">{safeRenderValue(rule.name)}:</span>
                    <span className="text-zinc-500">{condition}</span>
                    <span className="text-zinc-600">&rarr;</span>
                    <span className={cn("font-medium", ACTION_COLORS[action] ?? "text-zinc-400")}>
                      {action}{target}{maxIter}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 7. Executor config */}
      {hasCommand && (
        <div className="space-y-1.5">
          <SectionHeading>Command</SectionHeading>
          <div className="text-xs font-mono bg-zinc-50/50 dark:bg-zinc-900/50 border border-zinc-200 dark:border-zinc-800 rounded p-2 text-zinc-700 dark:text-zinc-300 whitespace-pre-wrap break-all">
            {commandTemplate}
          </div>
        </div>
      )}

      {isAgent && (
        <div className="space-y-1.5">
          <SectionHeading>Agent Config</SectionHeading>
          <div className="flex flex-wrap gap-3 text-xs">
            {Boolean(stepDef.executor.config.output_mode) && (
              <span className="text-zinc-500">
                Mode: <span className="text-zinc-400 font-mono">{safeRenderValue(stepDef.executor.config.output_mode)}</span>
              </span>
            )}
            {Boolean(stepDef.executor.config.model) && (
              <span className="text-zinc-500">
                Model: <span className="text-zinc-400 font-mono">{safeRenderValue(stepDef.executor.config.model)}</span>
              </span>
            )}
            {Boolean(stepDef.executor.config.permission_mode) && (
              <span className="text-zinc-500">
                Perms: <span className="text-zinc-400 font-mono">{safeRenderValue(stepDef.executor.config.permission_mode)}</span>
              </span>
            )}
          </div>
        </div>
      )}

      {/* Generic executor config for non-standard types */}
      {!["script", "external", "agent"].includes(stepDef.executor.type) &&
        Object.keys(stepDef.executor.config).length > 0 && (
          <div className="space-y-1.5">
            <SectionHeading>Config</SectionHeading>
            <JsonView data={stepDef.executor.config} defaultExpanded={false} />
          </div>
        )}

      {/* 8. Limits */}
      {stepDef.limits && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <Gauge className="w-3 h-3 text-zinc-500" />
            <SectionHeading>Limits</SectionHeading>
          </div>
          <div className="grid grid-cols-2 gap-1 text-xs font-mono">
            {stepDef.limits.max_cost_usd != null && (
              <>
                <span className="text-zinc-500">Max Cost</span>
                <span className="text-zinc-400">${stepDef.limits.max_cost_usd}</span>
              </>
            )}
            {stepDef.limits.max_duration_minutes != null && (
              <>
                <span className="text-zinc-500">Max Duration</span>
                <span className="text-zinc-400">{stepDef.limits.max_duration_minutes}m</span>
              </>
            )}
            {stepDef.limits.max_iterations != null && (
              <>
                <span className="text-zinc-500">Max Iterations</span>
                <span className="text-zinc-400">{stepDef.limits.max_iterations}</span>
              </>
            )}
          </div>
        </div>
      )}

      {/* 9. For-each */}
      {stepDef.for_each && (
        <div className="space-y-1.5">
          <SectionHeading>For Each</SectionHeading>
          <div className="text-xs font-mono bg-zinc-100/50 dark:bg-zinc-900/50 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300">
            for <span className="text-cyan-400">{stepDef.for_each.item_var}</span>
            {" "}in <span className="text-zinc-400">{stepDef.for_each.source_step}.{stepDef.for_each.source_field}</span>
          </div>
        </div>
      )}
    </div>
  );
}
