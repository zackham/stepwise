import { useRuns, useEvents, useRunCost, useStepwiseMutations } from "@/hooks/useStepwise";
import { useConfig } from "@/hooks/useConfig";
import type { StepDefinition, StepRun } from "@/lib/types";
import { StepStatusBadge } from "@/components/StatusBadge";
import { HandoffEnvelopeView } from "./HandoffEnvelopeView";
import { AgentStreamView } from "./AgentStreamView";
import { JsonView } from "@/components/JsonView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Separator } from "@/components/ui/separator";
import {
  X,
  RefreshCw,
  Clock,
  AlertTriangle,
  DollarSign,
  StopCircle,
  Gauge,
  Copy,
  Check,
  Terminal,
  StickyNote,
} from "lucide-react";
import { useState, useEffect, useRef, useMemo } from "react";
import { useLiveSource } from "@/hooks/useLiveSource";
import { useAgentOutput } from "@/hooks/useStepwise";
import { useScriptStream } from "@/hooks/useScriptStream";
import { toast } from "sonner";
import { cn, safeRenderValue } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import { VirtualizedLogView } from "@/components/logs/VirtualizedLogView";
import { LiveDuration } from "@/components/LiveDuration";
import { executorIcon } from "@/lib/executor-utils";

interface StepDetailPanelProps {
  jobId: string;
  stepDef: StepDefinition;
  onClose: () => void;
  expanded?: boolean;
  hasLiveSource?: boolean;
}

export function StepDetailSkeleton() {
  return (
    <div data-testid="step-detail-skeleton" className="animate-fade-in p-3 space-y-3">
      <Skeleton className="h-5 w-32" />
      <Skeleton className="h-4 w-48" />
      <Skeleton className="h-4 w-24" />
    </div>
  );
}

function formatCost(cost: number | null | undefined): string {
  if (cost == null || cost === 0) return "-";
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(2)}`;
}

function formatTimestamp(ts: string | null): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleTimeString();
}

function highlightLogLine(line: string): React.ReactNode {
  // Timestamp patterns: ISO, syslog-style, bracketed
  const timestampRe = /^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*Z?|\[\d{2}:\d{2}:\d{2}\]|\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})/;
  // Log level patterns
  const errorRe = /\b(ERROR|FATAL|CRITICAL|PANIC)\b/i;
  const warnRe = /\b(WARN|WARNING)\b/i;
  const infoRe = /\b(INFO)\b/i;
  const debugRe = /\b(DEBUG|TRACE)\b/i;

  let className = "text-zinc-700 dark:text-zinc-300";
  if (errorRe.test(line)) className = "text-red-400";
  else if (warnRe.test(line)) className = "text-amber-400";
  else if (infoRe.test(line)) className = "text-blue-400";
  else if (debugRe.test(line)) className = "text-zinc-500 dark:text-zinc-400";

  // Highlight timestamp portion
  const tsMatch = line.match(timestampRe);
  if (tsMatch) {
    return (
      <span className={className}>
        <span className="text-zinc-600">{tsMatch[0]}</span>
        {line.slice(tsMatch[0].length)}
      </span>
    );
  }
  return <span className={className}>{line}</span>;
}

function ScriptLogView({ run }: { run: StepRun }) {
  const [copied, setCopied] = useState(false);

  const stdout = (run.result?.executor_meta?.stdout as string) ?? "";
  const stderr = (run.result?.executor_meta?.stderr as string) ?? "";
  const returnCode = run.result?.executor_meta?.return_code as number | undefined;

  if (!stdout && !stderr) return null;

  const fullText = [
    stdout ? stdout : "",
    stderr ? `--- stderr ---\n${stderr}` : "",
  ].filter(Boolean).join("\n");

  const lines = fullText.split("\n");

  const handleCopy = () => {
    navigator.clipboard.writeText(fullText);
    setCopied(true);
    toast.success("Copied to clipboard");
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-1.5 text-xs text-zinc-500">
          <Terminal className="w-3 h-3" />
          <span>Logs</span>
          {returnCode != null && (
            <span className={cn(
              "font-mono text-[10px] px-1 py-0.5 rounded",
              returnCode === 0 ? "text-emerald-400 bg-emerald-500/10" : "text-red-400 bg-red-500/10"
            )}>
              exit {returnCode}
            </span>
          )}
        </div>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-[10px] text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
        >
          {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <div className="bg-zinc-50 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 rounded overflow-hidden">
        <VirtualizedLogView
          lines={lines}
          className="text-[11px] font-mono p-2 text-zinc-700 dark:text-zinc-300 whitespace-pre-wrap break-all leading-relaxed"
          renderLine={(line) => highlightLogLine(line)}
        />
      </div>
    </div>
  );
}

function LiveScriptLogView({ runId }: { runId: string }) {
  const { stdout, stderr, truncated, version } = useScriptStream(runId);

  if (!stdout && !stderr) {
    return (
      <div className="text-xs text-zinc-500 italic py-4 text-center">
        <div className="flex items-center justify-center gap-2">
          <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
          Script running...
        </div>
      </div>
    );
  }

  const rawLines = stdout.split("\n");
  if (rawLines.length > 0 && rawLines[rawLines.length - 1] === "") {
    rawLines.pop();
  }

  return (
    <div>
      <div className="text-xs text-zinc-500 dark:text-zinc-500 mb-1 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
          Live Output
        </div>
        <button
          onClick={() => { navigator.clipboard.writeText(stdout); toast.success("Copied to clipboard"); }}
          className="flex items-center gap-1 text-[10px] text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 transition-colors"
        >
          <Copy className="w-3 h-3" /> Copy
        </button>
      </div>
      {truncated && (
        <div className="text-[10px] text-amber-400/70 mb-1 font-mono">[earlier output truncated]</div>
      )}
      <div className="bg-zinc-50 dark:bg-zinc-950 rounded border border-zinc-200 dark:border-zinc-800">
        <VirtualizedLogView
          lines={rawLines}
          isLive={true}
          version={version}
          className="p-2 font-mono text-xs"
          renderLine={(line) => (
            <span className="whitespace-pre-wrap break-words leading-relaxed">
              {line === "" ? "\u00A0" : highlightLogLine(line)}
            </span>
          )}
        />
      </div>
      {stderr && (
        <div className="mt-2">
          <div className="text-xs text-red-400/70 dark:text-red-400/70 mb-1">stderr</div>
          <pre className="bg-zinc-50 dark:bg-zinc-950 rounded border border-red-300/20 dark:border-red-500/20 p-2 font-mono text-xs text-red-600 dark:text-red-300/80 max-h-48 overflow-auto whitespace-pre-wrap break-words">
            {stderr}
          </pre>
        </div>
      )}
    </div>
  );
}

function AgentRawView({ runId }: { runId: string }) {
  const { data } = useAgentOutput(runId);
  const [copied, setCopied] = useState(false);
  const text = (data?.events ?? []).map((e) => JSON.stringify(e)).join("\n");

  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    toast.success("Copied to clipboard");
    setTimeout(() => setCopied(false), 2000);
  };

  if (!text) return <div className="text-xs text-zinc-600">No output</div>;

  return (
    <div>
      <div className="flex justify-end mb-1">
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-[10px] text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
        >
          {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
          {copied ? "Copied" : "Copy All"}
        </button>
      </div>
      <pre className="text-[10px] font-mono bg-zinc-50 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 rounded p-2 text-zinc-600 dark:text-zinc-400 whitespace-pre-wrap break-all max-h-96 overflow-auto">
        {text}
      </pre>
    </div>
  );
}

export function StepDetailPanel({
  jobId,
  stepDef,
  onClose,
  expanded,
  hasLiveSource,
}: StepDetailPanelProps) {
  const { data: runs = [] } = useRuns(jobId, stepDef.name);
  const { data: events = [] } = useEvents(jobId);
  const mutations = useStepwiseMutations();
  const [agentViewMode, setAgentViewMode] = useState<"stream" | "raw">("stream");
  const [copiedErrorRunId, setCopiedErrorRunId] = useState<string | null>(null);

  const { liveSteps, hasUpdate, updatedAt } = useLiveSource(jobId, !!hasLiveSource);
  const liveStepDef = liveSteps?.[stepDef.name] ?? null;
  const [showLiveIndicator, setShowLiveIndicator] = useState(false);

  // Animate the "Updated" indicator when live source changes
  useEffect(() => {
    if (hasUpdate && updatedAt) {
      setShowLiveIndicator(true);
      const timer = setTimeout(() => setShowLiveIndicator(false), 3000);
      return () => clearTimeout(timer);
    }
  }, [hasUpdate, updatedAt]);

  const { data: configData } = useConfig();
  const isSubscription = configData?.billing_mode === "subscription";

  const sortedRunsForCost = [...runs].sort((a, b) => b.attempt - a.attempt);
  const activeRun = sortedRunsForCost.find((r) => r.status === "running");
  const { data: costData } = useRunCost(activeRun?.id);

  const isAgent = stepDef.executor.type === "agent";

  // Map exit rule resolutions to runs by step name
  // Events are ordered chronologically; we count exit.resolved events per step
  // to match them with attempt numbers
  const exitResolutions = (() => {
    const map: Record<number, { rule: string; action: string }> = {};
    let attemptCounter = 0;
    for (const e of events) {
      if (e.type === "exit.resolved" && e.data.step === stepDef.name) {
        attemptCounter++;
        map[attemptCounter] = {
          rule: e.data.rule as string,
          action: e.data.action as string,
        };
      }
    }
    return map;
  })();

  const sortedRuns = [...runs].sort((a, b) => b.attempt - a.attempt);
  const latestRun = sortedRuns[0] ?? null;
  const runHistoryRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to Run History when a failed step is selected
  useEffect(() => {
    if (latestRun?.status === "failed" && runHistoryRef.current) {
      runHistoryRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [stepDef.name, latestRun?.status]);

  const canRerun =
    !latestRun ||
    latestRun.status === "completed" ||
    latestRun.status === "failed" ||
    latestRun.status === "cancelled" ||
    latestRun.status === "skipped";

  const isWaitingReset =
    latestRun?.status === "running" &&
    !!(latestRun?.executor_state as Record<string, unknown> | undefined)?.usage_limit_waiting;

  // Compute effective source (prefer live over original) and detect changes
  const effectiveCommand = useMemo(() => {
    const original = String(stepDef.executor.config.command ?? "");
    const live = liveStepDef?.executor?.config?.command;
    if (live != null && String(live) !== original) {
      return { value: String(live), changed: true };
    }
    return { value: original, changed: false };
  }, [stepDef.executor.config.command, liveStepDef]);

  const effectivePrompt = useMemo(() => {
    const original = String(stepDef.executor.config.prompt ?? "");
    const live = liveStepDef?.executor?.config?.prompt;
    if (live != null && String(live) !== original) {
      return { value: String(live), changed: true };
    }
    return { value: original, changed: false };
  }, [stepDef.executor.config.prompt, liveStepDef]);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-zinc-500 dark:text-zinc-400">
            {executorIcon(stepDef.executor.type)}
          </span>
          <h3 className="font-semibold text-foreground">{stepDef.name}</h3>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={onClose}
            className="text-zinc-500 hover:text-foreground"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      <ScrollArea className="flex-1 min-h-0">
        <div className="p-4 space-y-4">
          {/* Step Definition */}
          <div className="space-y-2">
            <h4 className="text-xs font-medium text-zinc-500 uppercase tracking-wide">
              Definition
            </h4>
            {stepDef.description && (
              <p className="text-sm text-zinc-400 dark:text-zinc-500 mb-2">{stepDef.description}</p>
            )}
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div className="text-zinc-500">Executor</div>
              <div className="text-foreground font-mono text-xs min-w-0 break-all">
                {stepDef.executor.type}
              </div>
              <div className="text-zinc-500">Outputs</div>
              <div className="text-foreground font-mono text-xs min-w-0 break-all">
                {stepDef.outputs.join(", ") || "-"}
              </div>
              {stepDef.after.length > 0 && (
                <>
                  <div className="text-zinc-500">After</div>
                  <div className="text-foreground font-mono text-xs min-w-0 break-all">
                    {stepDef.after.join(", ")}
                  </div>
                </>
              )}
            </div>

            {/* Executor Config */}
            {stepDef.executor.type === "script" &&
              Boolean(stepDef.executor.config.command) && (
                <div className="mt-2">
                  <div className="flex items-center gap-2 text-zinc-500 text-sm mb-1">
                    Command
                    {effectiveCommand.changed && (
                      <span className={cn(
                        "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-cyan-500/10 text-cyan-400 border border-cyan-500/20",
                        showLiveIndicator && "animate-pulse"
                      )}>
                        <RefreshCw className="w-2.5 h-2.5" />
                        Updated
                      </span>
                    )}
                  </div>
                  <pre className={cn(
                    "text-xs font-mono bg-zinc-50 dark:bg-zinc-900 border rounded p-2 text-green-600 dark:text-green-400 whitespace-pre-wrap break-all",
                    effectiveCommand.changed
                      ? "border-cyan-500/30"
                      : "border-zinc-200 dark:border-zinc-800"
                  )}>
                    {effectiveCommand.value}
                  </pre>
                </div>
              )}
            {stepDef.executor.type === "external" &&
              Boolean(stepDef.executor.config.prompt) && (
                <div className="mt-2">
                  <div className="flex items-center gap-2 text-zinc-500 text-sm mb-1">
                    Prompt
                    {effectivePrompt.changed && (
                      <span className={cn(
                        "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-cyan-500/10 text-cyan-400 border border-cyan-500/20",
                        showLiveIndicator && "animate-pulse"
                      )}>
                        <RefreshCw className="w-2.5 h-2.5" />
                        Updated
                      </span>
                    )}
                  </div>
                  <pre className={cn(
                    "text-xs font-mono bg-zinc-50 dark:bg-zinc-900 rounded p-2 text-amber-700 dark:text-amber-300 whitespace-pre-wrap break-words",
                    effectivePrompt.changed
                      ? "border border-cyan-500/30"
                      : "border border-amber-500/20",
                    !expanded && "max-h-32 overflow-auto"
                  )}>
{effectivePrompt.value.trim()}
                  </pre>
                </div>
              )}
            {stepDef.executor.type === "agent" && (
              <div className="mt-2 space-y-2">
                {Boolean(stepDef.executor.config.prompt) && (
                  <div>
                    <div className="flex items-center gap-2 text-zinc-500 text-sm mb-1">
                      Agent Prompt
                      {effectivePrompt.changed && (
                        <span className={cn(
                          "inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-cyan-500/10 text-cyan-400 border border-cyan-500/20",
                          showLiveIndicator && "animate-pulse"
                        )}>
                          <RefreshCw className="w-2.5 h-2.5" />
                          Updated
                        </span>
                      )}
                    </div>
                    <pre className={cn(
                      "text-xs font-mono bg-zinc-50 dark:bg-zinc-900 rounded p-2 text-blue-700 dark:text-blue-300 whitespace-pre-wrap break-all",
                      effectivePrompt.changed
                        ? "border border-cyan-500/30"
                        : "border border-blue-500/20",
                      !expanded && "max-h-32 overflow-auto"
                    )}>
                      {safeRenderValue(effectivePrompt.value)}
                    </pre>
                  </div>
                )}
                <div className="flex gap-3 text-xs">
                  {Boolean(stepDef.executor.config.output_mode) && (
                    <span className="text-zinc-500">
                      Mode: <span className="text-zinc-500 dark:text-zinc-400 font-mono">{safeRenderValue(stepDef.executor.config.output_mode)}</span>
                    </span>
                  )}
                  {Boolean(stepDef.executor.config.model) && (
                    <span className="text-zinc-500">
                      Model: <span className="text-zinc-500 dark:text-zinc-400 font-mono">{safeRenderValue(stepDef.executor.config.model)}</span>
                    </span>
                  )}
                  {Boolean(stepDef.executor.config.permission_mode) && (
                    <span className="text-zinc-500">
                      Perms: <span className="text-zinc-500 dark:text-zinc-400 font-mono">{safeRenderValue(stepDef.executor.config.permission_mode)}</span>
                    </span>
                  )}
                </div>
              </div>
            )}
            {!["script", "external", "agent"].includes(stepDef.executor.type) &&
              Object.keys(stepDef.executor.config).length > 0 && (
                <div className="mt-2">
                  <div className="text-zinc-500 text-sm mb-1">Config</div>
                  <JsonView data={stepDef.executor.config} defaultExpanded={false} />
                </div>
              )}

            {stepDef.inputs.length > 0 && (
              <div className="mt-2">
                <div className="text-zinc-500 text-sm mb-1">Input Bindings</div>
                <div className="space-y-1">
                  {stepDef.inputs.map((b) => (
                    <div
                      key={b.local_name}
                      className="text-xs font-mono bg-zinc-100/50 dark:bg-zinc-900/50 rounded px-2 py-1"
                    >
                      <span className="text-blue-400">{b.local_name}</span>
                      <span className="text-zinc-600"> &larr; </span>
                      <span className="text-zinc-500 dark:text-zinc-400">
                        {b.source_step}.{b.source_field}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {stepDef.exit_rules.length > 0 && (
              <div className="mt-2">
                <div className="text-zinc-500 text-sm mb-1">Exit Rules</div>
                <div className="space-y-1">
                  {stepDef.exit_rules.map((r) => (
                    <div
                      key={r.name}
                      className="text-xs font-mono bg-zinc-100/50 dark:bg-zinc-900/50 rounded px-2 py-1"
                    >
                      <span className="text-amber-400">{safeRenderValue(r.name)}</span>
                      <span className="text-zinc-600"> ({safeRenderValue(r.type)})</span>
                      {r.config.action != null && (
                        <span className="text-zinc-500 dark:text-zinc-400">
                          {" "}
                          &rarr; {safeRenderValue(r.config.action)}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          <Separator />

          {/* Live Agent Stream */}
          {activeRun && isAgent && (
            <AgentStreamView
              runId={activeRun.id}
              isLive={true}
              startedAt={activeRun.started_at}
              costUsd={costData?.cost_usd}
              billingMode={costData?.billing_mode}
            />
          )}

          {/* Live Script Output */}
          {activeRun && stepDef.executor.type === "script" && (
            <LiveScriptLogView runId={activeRun.id} />
          )}

          {/* Step Limits */}
          {stepDef.limits && (
            <div className="space-y-1">
              <div className="flex items-center gap-1.5 text-xs text-zinc-500">
                <Gauge className="w-3 h-3" />
                <span>Limits</span>
              </div>
              <div className="grid grid-cols-2 gap-1 text-xs font-mono">
                {stepDef.limits.max_cost_usd != null && (
                  <>
                    <span className="text-zinc-500">Max Cost</span>
                    <span className="text-zinc-500 dark:text-zinc-400">${stepDef.limits.max_cost_usd}</span>
                  </>
                )}
                {stepDef.limits.max_duration_minutes != null && (
                  <>
                    <span className="text-zinc-500">Max Duration</span>
                    <span className="text-zinc-500 dark:text-zinc-400">{stepDef.limits.max_duration_minutes}m</span>
                  </>
                )}
                {stepDef.limits.max_iterations != null && (
                  <>
                    <span className="text-zinc-500">Max Iterations</span>
                    <span className="text-zinc-500 dark:text-zinc-400">{stepDef.limits.max_iterations}</span>
                  </>
                )}
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-2">
            {canRerun && (
              <Button
                variant="outline"
                size="sm"
                disabled={mutations.rerunStep.isPending}
                onClick={() =>
                  mutations.rerunStep.mutate({
                    jobId,
                    stepName: stepDef.name,
                  })
                }
              >
                <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
                Restart
              </Button>
            )}
            {activeRun && (
              <Button
                variant="outline"
                size="sm"
                className="border-red-500/30 text-red-400 hover:bg-red-500/10"
                disabled={mutations.cancelRun.isPending}
                onClick={() => mutations.cancelRun.mutate(activeRun.id)}
              >
                <StopCircle className="w-3.5 h-3.5 mr-1.5" />
                Cancel
              </Button>
            )}
          </div>

          {isWaitingReset && (() => {
            const es = latestRun?.executor_state as Record<string, unknown> | undefined;
            const resetAt = es?.reset_at ? String(es.reset_at) : null;
            const limitMsg = es?.usage_limit_message ? String(es.usage_limit_message) : null;
            return (
              <div className="rounded-md bg-amber-100 dark:bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300 border border-amber-500/20">
                <div className="font-medium">Usage limit reached</div>
                {resetAt && (
                  <div>{"Resuming at " + new Date(resetAt).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}</div>
                )}
                {limitMsg && (
                  <div className="mt-1 text-xs opacity-75">
                    {limitMsg}
                  </div>
                )}
              </div>
            );
          })()}

          <Separator />

          {/* Run History */}
          <div ref={runHistoryRef}>
            <h4 className="text-xs font-medium text-zinc-500 uppercase tracking-wide mb-2">
              Run History ({sortedRuns.length})
            </h4>

            {sortedRuns.length === 0 ? (
              <div className="text-zinc-500 text-sm">No runs yet</div>
            ) : (
              <Accordion
                key={stepDef.name}
                defaultValue={sortedRuns[0] ? [`run-${sortedRuns[0].id}`] : []}
              >
                {sortedRuns.map((run) => (
                  <AccordionItem key={run.id} value={`run-${run.id}`}>
                    <AccordionTrigger className="text-sm py-2">
                      <div className="flex items-center gap-2">
                        <StepStatusBadge status={run.status} />
                        <span className="text-zinc-500 dark:text-zinc-400">
                          Attempt #{run.attempt}
                        </span>
                        <span className="text-zinc-600 text-xs flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          <LiveDuration startTime={run.started_at} endTime={run.completed_at} />
                        </span>
                        {exitResolutions[run.attempt] && (
                          <span className={cn(
                            "text-[10px] font-mono px-1.5 py-0.5 rounded",
                            exitResolutions[run.attempt].action === "advance" && "text-emerald-400 bg-emerald-500/10",
                            exitResolutions[run.attempt].action === "loop" && "text-amber-400 bg-amber-500/10",
                            exitResolutions[run.attempt].action === "escalate" && "text-red-400 bg-red-500/10",
                            exitResolutions[run.attempt].action === "abandon" && "text-red-500 bg-red-500/10",
                          )}>
                            → {exitResolutions[run.attempt].rule}
                          </span>
                        )}
                      </div>
                    </AccordionTrigger>
                    <AccordionContent>
                      <div className="space-y-3 pb-2">
                        {/* Timestamps */}
                        <div className="grid grid-cols-2 gap-1 text-xs">
                          <span className="text-zinc-500">Started</span>
                          <span className="text-zinc-500 dark:text-zinc-400 font-mono">
                            {formatTimestamp(run.started_at)}
                          </span>
                          <span className="text-zinc-500">Completed</span>
                          <span className="text-zinc-500 dark:text-zinc-400 font-mono">
                            {formatTimestamp(run.completed_at)}
                          </span>
                          <span className="text-zinc-500">Run ID</span>
                          <span className="text-zinc-600 font-mono text-[10px] break-all">
                            {run.id}
                          </span>
                        </div>

                        {/* Fulfillment Notes */}
                        {run.result?.artifact?._fulfillment_notes != null && (
                          <div className="flex items-start gap-1.5 text-xs bg-zinc-100/50 dark:bg-zinc-800/50 rounded p-2">
                            <StickyNote className="w-3 h-3 mt-0.5 text-zinc-500 shrink-0" />
                            <span className="text-zinc-500 dark:text-zinc-400 whitespace-pre-wrap">
                              {String(run.result.artifact._fulfillment_notes)}
                            </span>
                          </div>
                        )}

                        {/* Error */}
                        {run.error && (
                          <div className="bg-red-500/10 border border-red-500/20 rounded p-2 text-sm">
                            <div className="flex items-center gap-1.5 text-red-400 mb-1">
                              <AlertTriangle className="w-3.5 h-3.5" />
                              <span className="font-medium">Error</span>
                              {run.error_category && (
                                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-red-500/20 text-red-300">
                                  {run.error_category}
                                </span>
                              )}
                              <button
                                onClick={() => {
                                  navigator.clipboard.writeText(run.error!);
                                  setCopiedErrorRunId(run.id);
                                  toast.success("Copied to clipboard");
                                  setTimeout(() => setCopiedErrorRunId(null), 2000);
                                }}
                                className="ml-auto flex items-center gap-1 text-[10px] text-red-400/60 hover:text-red-300 transition-colors"
                                title="Copy error"
                              >
                                {copiedErrorRunId === run.id ? (
                                  <><Check className="w-3 h-3" /> Copied!</>
                                ) : (
                                  <><Copy className="w-3 h-3" /> Copy</>
                                )}
                              </button>
                            </div>
                            <div className="text-red-300/80 text-xs font-mono whitespace-pre-wrap break-words">
                              {run.error}
                            </div>
                          </div>
                        )}

                        {/* Cost (from executor_meta) — only for agent/llm steps */}
                        {(stepDef.executor.type === "agent" || stepDef.executor.type === "llm") &&
                          (isSubscription || (run.result?.executor_meta?.cost_usd != null &&
                          (run.result.executor_meta.cost_usd as number) > 0)) && (
                          <div className="flex items-center gap-1.5 text-xs">
                            <DollarSign className="w-3 h-3 text-emerald-500" />
                            <span className="text-zinc-500">Cost:</span>
                            <span className="font-mono text-emerald-400">
                              {isSubscription
                                ? "$0 (Max)"
                                : formatCost(run.result?.executor_meta?.cost_usd as number)}
                            </span>
                          </div>
                        )}

                        {/* Inputs */}
                        {run.inputs &&
                          Object.keys(run.inputs).length > 0 && (
                            <div>
                              <div className="text-xs text-zinc-500 mb-1">
                                Inputs
                              </div>
                              <JsonView
                                data={run.inputs}
                                defaultExpanded={false}
                              />
                            </div>
                          )}

                        {/* Resolved Prompt (interpolated) */}
                        {(() => {
                          const ic = run.executor_state?._interpolated_config as Record<string, unknown> | undefined;
                          const resolvedPrompt = typeof ic?.prompt === 'string' ? ic.prompt : undefined;
                          const resolvedCommand = typeof ic?.command === 'string' ? ic.command : undefined;
                          const resolvedCheckCommand = typeof ic?.check_command === 'string' ? ic.check_command : undefined;
                          const templatePrompt = typeof stepDef.executor.config.prompt === 'string' ? stepDef.executor.config.prompt : undefined;
                          const templateCommand = typeof stepDef.executor.config.command === 'string' ? stepDef.executor.config.command : undefined;
                          const templateCheckCommand = typeof stepDef.executor.config.check_command === 'string' ? stepDef.executor.config.check_command : undefined;
                          return (
                            <>
                              {resolvedPrompt && resolvedPrompt !== templatePrompt && (
                                <div>
                                  <div className="text-xs text-zinc-500 mb-1">Resolved Prompt</div>
                                  <pre className={cn("text-xs font-mono bg-zinc-50 dark:bg-zinc-900 border border-emerald-500/20 rounded p-2 text-emerald-700 dark:text-emerald-300 whitespace-pre-wrap break-words", !expanded && "max-h-48 overflow-auto")}>
                                    {resolvedPrompt.trim()}
                                  </pre>
                                </div>
                              )}
                              {resolvedCommand && resolvedCommand !== templateCommand && (
                                <div>
                                  <div className="text-xs text-zinc-500 mb-1">Resolved Command</div>
                                  <pre className={cn("text-xs font-mono bg-zinc-50 dark:bg-zinc-900 border border-emerald-500/20 rounded p-2 text-emerald-700 dark:text-emerald-300 whitespace-pre-wrap break-words", !expanded && "max-h-48 overflow-auto")}>
                                    {resolvedCommand.trim()}
                                  </pre>
                                </div>
                              )}
                              {resolvedCheckCommand && resolvedCheckCommand !== templateCheckCommand && (
                                <div>
                                  <div className="text-xs text-zinc-500 mb-1">Resolved Check Command</div>
                                  <pre className={cn("text-xs font-mono bg-zinc-50 dark:bg-zinc-900 border border-emerald-500/20 rounded p-2 text-emerald-700 dark:text-emerald-300 whitespace-pre-wrap break-words", !expanded && "max-h-48 overflow-auto")}>
                                    {resolvedCheckCommand.trim()}
                                  </pre>
                                </div>
                              )}
                            </>
                          );
                        })()}

                        {/* Agent Output Replay */}
                        {run.result && isAgent && (
                          <div>
                            <div className="flex items-center justify-between mb-1">
                              <div className="text-xs text-zinc-500">Agent Output</div>
                              <div className="flex items-center gap-0.5 bg-zinc-200 dark:bg-zinc-800 rounded p-0.5">
                                <button
                                  onClick={() => setAgentViewMode("stream")}
                                  className={cn(
                                    "text-[10px] px-2 py-0.5 rounded transition-colors",
                                    agentViewMode === "stream"
                                      ? "bg-white dark:bg-zinc-700 text-zinc-800 dark:text-zinc-200"
                                      : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
                                  )}
                                >
                                  Stream
                                </button>
                                <button
                                  onClick={() => setAgentViewMode("raw")}
                                  className={cn(
                                    "text-[10px] px-2 py-0.5 rounded transition-colors",
                                    agentViewMode === "raw"
                                      ? "bg-white dark:bg-zinc-700 text-zinc-800 dark:text-zinc-200"
                                      : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
                                  )}
                                >
                                  Raw
                                </button>
                              </div>
                            </div>
                            {agentViewMode === "stream" ? (
                              <AgentStreamView runId={run.id} isLive={false} />
                            ) : (
                              <AgentRawView runId={run.id} />
                            )}
                          </div>
                        )}

                        {/* Script Logs */}
                        {run.result && stepDef.executor.type === "script" && (
                          <ScriptLogView run={run} />
                        )}

                        {/* Result */}
                        {run.result && (
                          <div>
                            <div className="text-xs text-zinc-500 mb-1">
                              Output
                            </div>
                            <HandoffEnvelopeView
                              envelope={run.result}
                              isLatest={run.id === sortedRuns[0]?.id}
                            />
                          </div>
                        )}

                        {/* Watch State */}
                        {run.watch && (
                          <div>
                            <div className="text-xs text-zinc-500 mb-1">
                              Watch
                            </div>
                            <JsonView data={run.watch} defaultExpanded />
                          </div>
                        )}
                      </div>
                    </AccordionContent>
                  </AccordionItem>
                ))}
              </Accordion>
            )}
          </div>
        </div>
      </ScrollArea>

    </div>
  );
}
