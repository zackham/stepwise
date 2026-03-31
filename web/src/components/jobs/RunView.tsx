import { useState, useMemo, useEffect, useRef } from "react";
import { useRuns, useEvents, useRunCost, useStepwiseMutations } from "@/hooks/useStepwise";
import { useConfig } from "@/hooks/useConfig";
import type { StepDefinition, StepRun } from "@/lib/types";
import { StepStatusBadge } from "@/components/StatusBadge";
import { AgentStreamView } from "./AgentStreamView";
import { HandoffEnvelopeView } from "./HandoffEnvelopeView";
import { JsonView } from "@/components/JsonView";
import { Button } from "@/components/ui/button";
import {
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  AlertTriangle,
  DollarSign,
  StopCircle,
  Copy,
  Check,
  Terminal,
} from "lucide-react";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { ContentModal } from "@/components/ui/content-modal";
import { useLiveSource } from "@/hooks/useLiveSource";
import { useAgentOutput } from "@/hooks/useStepwise";
import { useScriptStream } from "@/hooks/useScriptStream";
import { toast } from "sonner";
import { cn, safeRenderValue } from "@/lib/utils";
import { VirtualizedLogView } from "@/components/logs/VirtualizedLogView";
import { LiveDuration } from "@/components/LiveDuration";

interface RunViewProps {
  jobId: string;
  stepDef: StepDefinition;
  hasLiveSource?: boolean;
}

/* ── Helpers ───────────────────────────────────────────────────────── */

function formatCost(cost: number | null | undefined): string {
  if (cost == null || cost === 0) return "-";
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(2)}`;
}

function formatTimestamp(ts: string | null): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleTimeString();
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-medium text-zinc-500 uppercase tracking-wider">
      {children}
    </div>
  );
}

/* ── Log highlighting ──────────────────────────────────────────────── */

function highlightLogLine(line: string): React.ReactNode {
  const timestampRe = /^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*Z?|\[\d{2}:\d{2}:\d{2}\]|\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})/;
  const errorRe = /\b(ERROR|FATAL|CRITICAL|PANIC)\b/i;
  const warnRe = /\b(WARN|WARNING)\b/i;
  const infoRe = /\b(INFO)\b/i;
  const debugRe = /\b(DEBUG|TRACE)\b/i;

  let className = "text-zinc-700 dark:text-zinc-300";
  if (errorRe.test(line)) className = "text-red-400";
  else if (warnRe.test(line)) className = "text-amber-400";
  else if (infoRe.test(line)) className = "text-blue-400";
  else if (debugRe.test(line)) className = "text-zinc-500 dark:text-zinc-400";

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

/* ── Script log viewer (completed runs) ───────────────────────────── */

function ScriptLogView({ run }: { run: StepRun }) {
  const [copied, setCopied] = useState(false);
  const stdout = (run.result?.executor_meta?.stdout as string) ?? "";
  const stderr = (run.result?.executor_meta?.stderr as string) ?? "";
  const returnCode = run.result?.executor_meta?.return_code as number | undefined;
  if (!stdout && !stderr) return null;

  const fullText = [stdout, stderr ? `--- stderr ---\n${stderr}` : ""].filter(Boolean).join("\n");
  const lines = fullText.split("\n");

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
          onClick={() => {
            navigator.clipboard.writeText(fullText);
            setCopied(true);
            toast.success("Copied to clipboard");
            setTimeout(() => setCopied(false), 2000);
          }}
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

/* ── Live script log viewer (running) ─────────────────────────────── */

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
  if (rawLines.length > 0 && rawLines[rawLines.length - 1] === "") rawLines.pop();

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

/* ── Agent raw event viewer ───────────────────────────────────────── */

function AgentRawView({ runId }: { runId: string }) {
  const { data } = useAgentOutput(runId);
  const [copied, setCopied] = useState(false);
  const text = (data?.events ?? []).map((e: unknown) => JSON.stringify(e)).join("\n");

  if (!text) return <div className="text-xs text-zinc-600">No output</div>;

  return (
    <div>
      <div className="flex justify-end mb-1">
        <button
          onClick={() => {
            navigator.clipboard.writeText(text);
            setCopied(true);
            toast.success("Copied to clipboard");
            setTimeout(() => setCopied(false), 2000);
          }}
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

/* ── Truncated prompt with click-to-expand modal ──────────────────── */

function PromptBlock({ prompt }: { prompt: string }) {
  const [modalOpen, setModalOpen] = useState(false);
  const lines = prompt.trim().split("\n");
  const isTruncated = lines.length > 4;

  return (
    <div>
      <div
        onClick={() => setModalOpen(true)}
        className={cn(
          "text-xs font-mono bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded p-2 whitespace-pre-wrap break-all cursor-pointer hover:bg-zinc-50/80 dark:hover:bg-zinc-900/70 transition-colors",
          "text-emerald-700 dark:text-emerald-300",
          isTruncated && "line-clamp-4"
        )}
      >
        {prompt.trim()}
      </div>
      <ContentModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        title="Prompt"
        copyContent={prompt.trim()}
      >
        <pre className="whitespace-pre-wrap text-sm text-zinc-300 font-mono p-2">
          {prompt.trim()}
        </pre>
      </ContentModal>
    </div>
  );
}

/* ── Run navigator header ─────────────────────────────────────────── */

function RunNavigator({
  runs,
  currentIndex,
  onSelect,
  stepName,
}: {
  runs: StepRun[];
  currentIndex: number;
  onSelect: (idx: number) => void;
  stepName: string;
}) {
  const run = runs[currentIndex];
  if (!run) return null;

  const total = runs.length;
  // runs are sorted newest-first, so attempt number = total - currentIndex
  const canPrev = currentIndex < total - 1;
  const canNext = currentIndex > 0;

  return (
    <div className="flex items-center gap-2 text-xs">
      <div className="flex items-center gap-0.5">
        <button
          onClick={() => canPrev && onSelect(currentIndex + 1)}
          disabled={!canPrev}
          className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 disabled:opacity-30 disabled:cursor-default"
          aria-label="Previous attempt"
        >
          <ChevronLeft className="w-3.5 h-3.5" />
        </button>
        <span className="text-zinc-500 font-mono text-[10px] min-w-[2rem] text-center">
          {total === 1 ? `#${run.attempt}` : `${run.attempt}/${total}`}
        </span>
        <button
          onClick={() => canNext && onSelect(currentIndex - 1)}
          disabled={!canNext}
          className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 disabled:opacity-30 disabled:cursor-default"
          aria-label="Next attempt"
        >
          <ChevronRight className="w-3.5 h-3.5" />
        </button>
      </div>
      <span className="text-zinc-500 truncate">{stepName}</span>
      <StepStatusBadge status={run.status} />
      <span className="text-zinc-600 text-[10px] flex items-center gap-0.5 ml-auto shrink-0">
        <LiveDuration startTime={run.started_at} endTime={run.completed_at} />
      </span>
    </div>
  );
}

/* ── Main RunView ─────────────────────────────────────────────────── */

export function RunView({ jobId, stepDef, hasLiveSource }: RunViewProps) {
  const { data: runs = [] } = useRuns(jobId, stepDef.name);
  const { data: events = [] } = useEvents(jobId);
  const mutations = useStepwiseMutations();
  const [agentViewMode, setAgentViewMode] = useState<"stream" | "raw">("stream");
  const [runIndex, setRunIndex] = useState(0);

  const { data: configData } = useConfig();
  const isSubscription = configData?.billing_mode === "subscription";

  const isAgent = stepDef.executor.type === "agent";
  const isExternal = stepDef.executor.type === "external";
  const isScript = stepDef.executor.type === "script";

  const sortedRuns = useMemo(
    () => [...runs].sort((a, b) => b.attempt - a.attempt),
    [runs],
  );

  // Reset index when step changes or new runs come in
  useEffect(() => { setRunIndex(0); }, [stepDef.name]);
  useEffect(() => {
    if (runIndex >= sortedRuns.length && sortedRuns.length > 0) setRunIndex(0);
  }, [sortedRuns.length, runIndex]);

  const run = sortedRuns[runIndex] ?? null;
  const isRunning = run?.status === "running";
  const isCompleted = run?.status === "completed";
  const isFailed = run?.status === "failed";
  const isSuspended = run?.status === "suspended";

  // Live cost for running agent steps
  const { data: costData } = useRunCost(isRunning ? run?.id : undefined);

  // Exit resolutions
  const exitResolutions = useMemo(() => {
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
  }, [events, stepDef.name]);

  // Get interpolated prompt for a run
  const getRunPrompt = (r: StepRun): string | undefined => {
    if (isExternal) {
      const wp = r.watch?.config?.prompt;
      return typeof wp === "string" ? wp : undefined;
    }
    const ic = r.executor_state?._interpolated_config as Record<string, unknown> | undefined;
    return typeof ic?.prompt === "string" ? ic.prompt : undefined;
  };

  // Input provenance: map each input field to its source step/field
  const inputSources = useMemo(() => {
    const map: Record<string, string> = {};
    for (const b of stepDef.inputs) {
      if (b.source_step === "$job") {
        map[b.local_name] = "job input";
      } else if (b.any_of_sources?.length) {
        map[b.local_name] = b.any_of_sources.map((s) => `${s.step}.${s.field}`).join(" | ");
      } else {
        map[b.local_name] = `${b.source_step}.${b.source_field}`;
      }
    }
    return map;
  }, [stepDef.inputs]);

  const { copy: copyRunId, justCopied: runIdCopied } = useCopyFeedback();

  const canRerun =
    !run ||
    run.status === "completed" ||
    run.status === "failed" ||
    run.status === "cancelled" ||
    run.status === "skipped";

  if (sortedRuns.length === 0) {
    return (
      <div className="p-4 space-y-3 animate-step-fade">
        <div className="text-xs text-zinc-500 italic text-center py-8">
          No runs yet
        </div>
        {canRerun && (
          <Button
            variant="outline"
            size="sm"
            disabled={mutations.rerunStep.isPending}
            onClick={() => mutations.rerunStep.mutate({ jobId, stepName: stepDef.name })}
          >
            <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
            Run Step
          </Button>
        )}
      </div>
    );
  }

  const runPrompt = run ? getRunPrompt(run) : undefined;
  const exitRes = run ? exitResolutions[run.attempt] : undefined;

  return (
    <div className="p-3 space-y-4 animate-step-fade">
      {/* 1. Header bar — run navigator */}
      <div className="sticky top-0 z-10 -mx-3 -mt-3 px-3 py-2 border-b border-border bg-zinc-50/80 dark:bg-zinc-950/80 backdrop-blur-sm">
        <RunNavigator
          runs={sortedRuns}
          currentIndex={runIndex}
          onSelect={setRunIndex}
          stepName={stepDef.name}
        />
      </div>

      {/* 2. Timing line */}
      {run && (
        <div className="flex items-center gap-1.5 text-[10px] text-zinc-500 font-mono flex-wrap">
          <span>Started {formatTimestamp(run.started_at)}</span>
          {run.completed_at && (
            <>
              <span className="text-zinc-600">&middot;</span>
              <span>Completed {formatTimestamp(run.completed_at)}</span>
            </>
          )}
          <span className="text-zinc-600">&middot;</span>
          <span
            onClick={() => copyRunId(run.id)}
            className={cn(
              "cursor-pointer hover:text-blue-400 transition-colors",
              runIdCopied ? "text-green-400" : ""
            )}
            title="Click to copy run ID"
          >
            {run.id}
          </span>
        </div>
      )}

      {/* 3. Inputs */}
      {run?.inputs && Object.keys(run.inputs).length > 0 && (
        <div className="space-y-1.5">
          <SectionHeading>Inputs</SectionHeading>
          <div className="space-y-2">
            {Object.entries(run.inputs).map(([field, value]) => {
              const isPrimitive = typeof value === "string" || typeof value === "number" || typeof value === "boolean";
              const preview = isPrimitive
                ? (typeof value === "string" && value.length > 80
                    ? `"${value.slice(0, 77)}..."`
                    : JSON.stringify(value))
                : null;

              return (
                <div key={field} className="space-y-0.5">
                  <div className="flex items-baseline gap-1.5 text-xs">
                    <span className="font-medium text-zinc-700 dark:text-zinc-300">{field}</span>
                    <span className="text-zinc-600">=</span>
                    {preview != null ? (
                      <span className="font-mono text-emerald-600 dark:text-emerald-400 break-all">
                        {preview}
                      </span>
                    ) : (
                      <span className="font-mono text-zinc-500 text-[10px]">[object]</span>
                    )}
                  </div>
                  {inputSources[field] && (
                    <div className="text-[10px] font-mono text-zinc-500 pl-2">
                      &nbsp;&nbsp;&#9492;&#9472; {inputSources[field]}
                    </div>
                  )}
                  {value != null && typeof value === "object" && (
                    <div className="ml-4 mt-1">
                      <JsonView data={value} defaultExpanded={false} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* 4. Prompt (interpolated) */}
      {runPrompt && (
        <div className="space-y-1.5">
          <SectionHeading>Prompt</SectionHeading>
          <PromptBlock prompt={runPrompt} />
        </div>
      )}

      {/* 5. Output area */}
      {/* Live agent stream */}
      {run && isRunning && isAgent && (
        <AgentStreamView
          runId={run.id}
          isLive={true}
          startedAt={run.started_at}
          costUsd={costData?.cost_usd}
          billingMode={costData?.billing_mode}
        />
      )}

      {/* Live script output */}
      {run && isRunning && isScript && (
        <LiveScriptLogView runId={run.id} />
      )}

      {/* Usage limit waiting */}
      {run?.status === "running" && !!(run?.executor_state as Record<string, unknown> | undefined)?.usage_limit_waiting && (() => {
        const es = run.executor_state as Record<string, unknown>;
        const resetAt = es.reset_at ? String(es.reset_at) : null;
        const limitMsg = es.usage_limit_message ? String(es.usage_limit_message) : null;
        return (
          <div className="rounded-md bg-amber-100 dark:bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300 border border-amber-500/20">
            <div className="font-medium">Usage limit reached</div>
            {resetAt && <div>Resuming at {new Date(resetAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>}
            {limitMsg && <div className="mt-1 text-xs opacity-75">{limitMsg}</div>}
          </div>
        );
      })()}

      {/* Suspended external — fulfillment status */}
      {run && isSuspended && isExternal && (
        <div className="rounded-md bg-blue-100 dark:bg-blue-500/10 p-3 text-sm text-blue-700 dark:text-blue-300 border border-blue-500/20">
          <div className="font-medium">Waiting for input</div>
          <div className="text-xs mt-1 opacity-75">
            This step is suspended and waiting for external fulfillment.
          </div>
        </div>
      )}

      {/* Completed agent replay */}
      {run?.result && isAgent && (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <SectionHeading>Agent Output</SectionHeading>
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

      {/* Completed script logs */}
      {run?.result && isScript && <ScriptLogView run={run} />}

      {/* Result artifact */}
      {run?.result && (
        <div className="space-y-1.5">
          <SectionHeading>Result</SectionHeading>
          <HandoffEnvelopeView envelope={run.result} isLatest={runIndex === 0} />
        </div>
      )}

      {/* Fulfillment notes */}
      {run?.result?.artifact?._fulfillment_notes != null && (
        <div className="flex items-start gap-1.5 text-xs bg-zinc-100/50 dark:bg-zinc-800/50 rounded p-2">
          <span className="text-zinc-500 dark:text-zinc-400 whitespace-pre-wrap">
            {String(run.result.artifact._fulfillment_notes)}
          </span>
        </div>
      )}

      {/* 6. Exit resolution */}
      {exitRes && (
        <div className="space-y-1">
          <SectionHeading>Exit</SectionHeading>
          <div className="text-xs font-mono">
            <span className="text-zinc-400">&ldquo;</span>
            <span className={cn(
              "font-medium",
              exitRes.action === "advance" && "text-emerald-400",
              exitRes.action === "loop" && "text-purple-400",
              exitRes.action === "escalate" && "text-red-400",
              exitRes.action === "abandon" && "text-red-500",
            )}>
              {exitRes.rule}
            </span>
            <span className="text-zinc-400">&rdquo;</span>
            <span className="text-zinc-500"> &rarr; {exitRes.action}</span>
          </div>
        </div>
      )}

      {/* 7. Error */}
      {run && isFailed && run.error && (
        <div className="space-y-1.5">
          <SectionHeading>Error</SectionHeading>
          <div className="bg-red-500/10 border border-red-500/20 rounded p-2 text-sm">
            <div className="flex items-center gap-1.5 text-red-400 mb-1">
              <AlertTriangle className="w-3.5 h-3.5" />
              <span className="font-medium">Error</span>
              {run.error_category && (
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-red-500/20 text-red-300">
                  {run.error_category}
                </span>
              )}
            </div>
            <div className="text-red-300/80 text-xs font-mono whitespace-pre-wrap break-words">
              {run.error}
            </div>
          </div>
          {run.traceback && (
            <details className="text-xs">
              <summary className="text-zinc-500 cursor-pointer hover:text-zinc-400">Traceback</summary>
              <pre className="mt-1 text-[10px] font-mono text-red-300/60 whitespace-pre-wrap break-all bg-zinc-950 rounded p-2 max-h-48 overflow-auto">
                {run.traceback}
              </pre>
            </details>
          )}
          <Button
            variant="outline"
            size="sm"
            className="border-amber-500/30 text-amber-400 hover:bg-amber-500/10"
            disabled={mutations.rerunStep.isPending}
            onClick={() => mutations.rerunStep.mutate({ jobId, stepName: stepDef.name })}
          >
            <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
            Retry
          </Button>
        </div>
      )}

      {/* 8. Cost */}
      {run && (isAgent || stepDef.executor.type === "llm") && (
        isSubscription || ((run.result?.executor_meta?.cost_usd as number) > 0)
      ) && (
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

      {/* Actions for non-failed runs */}
      {run && !isFailed && (
        <div className="flex gap-2">
          {canRerun && (
            <Button
              variant="outline"
              size="sm"
              disabled={mutations.rerunStep.isPending}
              onClick={() => mutations.rerunStep.mutate({ jobId, stepName: stepDef.name })}
            >
              <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
              Restart
            </Button>
          )}
          {isRunning && (
            <Button
              variant="outline"
              size="sm"
              className="border-red-500/30 text-red-400 hover:bg-red-500/10"
              disabled={mutations.cancelRun.isPending}
              onClick={() => mutations.cancelRun.mutate(run.id)}
            >
              <StopCircle className="w-3.5 h-3.5 mr-1.5" />
              Cancel
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
