import { useRuns, useEvents, useRunCost, useStepwiseMutations } from "@/hooks/useStepwise";
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
  Terminal,
  User,
  Brain,
  Cog,
  AlertTriangle,
  Eye,
  Bot,
  DollarSign,
  StopCircle,
  Gauge,
} from "lucide-react";
import { useState } from "react";
import { FulfillWatchDialog } from "./FulfillWatchDialog";
import { cn } from "@/lib/utils";

interface StepDetailPanelProps {
  jobId: string;
  stepDef: StepDefinition;
  onClose: () => void;
}

function executorIcon(type: string) {
  switch (type) {
    case "script":
      return <Terminal className="w-4 h-4" />;
    case "human":
      return <User className="w-4 h-4" />;
    case "mock_llm":
    case "llm":
      return <Brain className="w-4 h-4" />;
    case "agent":
      return <Bot className="w-4 h-4" />;
    default:
      return <Cog className="w-4 h-4" />;
  }
}

function formatCost(cost: number | null | undefined): string {
  if (cost == null || cost === 0) return "-";
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(2)}`;
}

function formatDuration(startedAt: string | null, completedAt: string | null): string {
  if (!startedAt) return "-";
  const start = new Date(startedAt).getTime();
  const end = completedAt ? new Date(completedAt).getTime() : Date.now();
  const ms = end - start;
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

function formatTimestamp(ts: string | null): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleTimeString();
}

export function StepDetailPanel({
  jobId,
  stepDef,
  onClose,
}: StepDetailPanelProps) {
  const { data: runs = [] } = useRuns(jobId, stepDef.name);
  const { data: events = [] } = useEvents(jobId);
  const mutations = useStepwiseMutations();
  const [fulfillDialogOpen, setFulfillDialogOpen] = useState(false);

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

  const canRerun =
    !latestRun ||
    latestRun.status === "completed" ||
    latestRun.status === "failed";

  const isSuspended =
    latestRun?.status === "suspended" && latestRun?.watch?.mode === "human";

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-start justify-between p-4 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-zinc-400">
            {executorIcon(stepDef.executor.type)}
          </span>
          <h3 className="font-semibold text-foreground">{stepDef.name}</h3>
        </div>
        <button
          onClick={onClose}
          className="text-zinc-500 hover:text-foreground"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      <ScrollArea className="flex-1 min-h-0">
        <div className="p-4 space-y-4">
          {/* Step Definition */}
          <div className="space-y-2">
            <h4 className="text-xs font-medium text-zinc-500 uppercase tracking-wide">
              Definition
            </h4>
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div className="text-zinc-500">Executor</div>
              <div className="text-foreground font-mono text-xs">
                {stepDef.executor.type}
              </div>
              <div className="text-zinc-500">Outputs</div>
              <div className="text-foreground font-mono text-xs">
                {stepDef.outputs.join(", ") || "-"}
              </div>
              {stepDef.sequencing.length > 0 && (
                <>
                  <div className="text-zinc-500">Sequencing</div>
                  <div className="text-foreground font-mono text-xs">
                    {stepDef.sequencing.join(", ")}
                  </div>
                </>
              )}
            </div>

            {/* Executor Config */}
            {stepDef.executor.type === "script" &&
              Boolean(stepDef.executor.config.command) && (
                <div className="mt-2">
                  <div className="text-zinc-500 text-sm mb-1">Command</div>
                  <pre className="text-xs font-mono bg-zinc-900 border border-zinc-800 rounded p-2 text-green-400 whitespace-pre-wrap break-all">
                    {String(stepDef.executor.config.command)}
                  </pre>
                </div>
              )}
            {stepDef.executor.type === "human" &&
              Boolean(stepDef.executor.config.prompt) && (
                <div className="mt-2">
                  <div className="text-zinc-500 text-sm mb-1">Prompt</div>
                  <pre className="text-xs font-mono bg-zinc-900 border border-amber-500/20 rounded p-2 text-amber-300 whitespace-pre-wrap break-words max-h-32 overflow-auto">
{String(stepDef.executor.config.prompt).trim()}
                  </pre>
                </div>
              )}
            {stepDef.executor.type === "agent" && (
              <div className="mt-2 space-y-2">
                {Boolean(stepDef.executor.config.prompt) && (
                  <div>
                    <div className="text-zinc-500 text-sm mb-1">Agent Prompt</div>
                    <pre className="text-xs font-mono bg-zinc-900 border border-blue-500/20 rounded p-2 text-blue-300 whitespace-pre-wrap break-all max-h-32 overflow-auto">
                      {String(stepDef.executor.config.prompt)}
                    </pre>
                  </div>
                )}
                <div className="flex gap-3 text-xs">
                  {Boolean(stepDef.executor.config.output_mode) && (
                    <span className="text-zinc-500">
                      Mode: <span className="text-zinc-400 font-mono">{String(stepDef.executor.config.output_mode)}</span>
                    </span>
                  )}
                  {Boolean(stepDef.executor.config.model) && (
                    <span className="text-zinc-500">
                      Model: <span className="text-zinc-400 font-mono">{String(stepDef.executor.config.model)}</span>
                    </span>
                  )}
                  {Boolean(stepDef.executor.config.permission_mode) && (
                    <span className="text-zinc-500">
                      Perms: <span className="text-zinc-400 font-mono">{String(stepDef.executor.config.permission_mode)}</span>
                    </span>
                  )}
                </div>
              </div>
            )}

            {stepDef.inputs.length > 0 && (
              <div className="mt-2">
                <div className="text-zinc-500 text-sm mb-1">Input Bindings</div>
                <div className="space-y-1">
                  {stepDef.inputs.map((b) => (
                    <div
                      key={b.local_name}
                      className="text-xs font-mono bg-zinc-900/50 rounded px-2 py-1"
                    >
                      <span className="text-blue-400">{b.local_name}</span>
                      <span className="text-zinc-600"> &larr; </span>
                      <span className="text-zinc-400">
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
                      className="text-xs font-mono bg-zinc-900/50 rounded px-2 py-1"
                    >
                      <span className="text-amber-400">{r.name}</span>
                      <span className="text-zinc-600"> ({r.type})</span>
                      {r.config.action != null && (
                        <span className="text-zinc-400">
                          {" "}
                          &rarr; {String(r.config.action)}
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
            />
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

          {/* Actions */}
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={!canRerun || mutations.rerunStep.isPending}
              onClick={() =>
                mutations.rerunStep.mutate({
                  jobId,
                  stepName: stepDef.name,
                })
              }
            >
              <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
              Rerun
            </Button>
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
            {isSuspended && latestRun && (
              <Button
                variant="outline"
                size="sm"
                className="border-amber-500/30 text-amber-400 hover:bg-amber-500/10"
                onClick={() => setFulfillDialogOpen(true)}
              >
                <Eye className="w-3.5 h-3.5 mr-1.5" />
                Fulfill Watch
              </Button>
            )}
          </div>

          <Separator />

          {/* Run History */}
          <div>
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
                        <span className="text-zinc-400">
                          Attempt #{run.attempt}
                        </span>
                        <span className="text-zinc-600 text-xs flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {formatDuration(run.started_at, run.completed_at)}
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
                          <span className="text-zinc-400 font-mono">
                            {formatTimestamp(run.started_at)}
                          </span>
                          <span className="text-zinc-500">Completed</span>
                          <span className="text-zinc-400 font-mono">
                            {formatTimestamp(run.completed_at)}
                          </span>
                          <span className="text-zinc-500">Run ID</span>
                          <span className="text-zinc-600 font-mono text-[10px]">
                            {run.id}
                          </span>
                        </div>

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
                            </div>
                            <div className="text-red-300/80 text-xs font-mono whitespace-pre-wrap">
                              {run.error}
                            </div>
                          </div>
                        )}

                        {/* Cost (from executor_meta) */}
                        {run.result?.executor_meta?.cost_usd != null &&
                          (run.result.executor_meta.cost_usd as number) > 0 && (
                          <div className="flex items-center gap-1.5 text-xs">
                            <DollarSign className="w-3 h-3 text-emerald-500" />
                            <span className="text-zinc-500">Cost:</span>
                            <span className="font-mono text-emerald-400">
                              {formatCost(run.result.executor_meta.cost_usd as number)}
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

                        {/* Agent Output Replay */}
                        {run.result && isAgent && (
                          <div>
                            <div className="text-xs text-zinc-500 mb-1">
                              Agent Output
                            </div>
                            <AgentStreamView
                              runId={run.id}
                              isLive={false}
                            />
                          </div>
                        )}

                        {/* Result */}
                        {run.result && (
                          <div>
                            <div className="text-xs text-zinc-500 mb-1">
                              Output
                            </div>
                            <HandoffEnvelopeView envelope={run.result} />
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

      {/* Fulfill Watch Dialog */}
      {latestRun && latestRun.watch && (
        <FulfillWatchDialog
          open={fulfillDialogOpen}
          onOpenChange={setFulfillDialogOpen}
          run={latestRun}
          onFulfill={(payload) => {
            mutations.fulfillWatch.mutate(
              { runId: latestRun.id, payload },
              { onSuccess: () => setFulfillDialogOpen(false) }
            );
          }}
          isPending={mutations.fulfillWatch.isPending}
        />
      )}
    </div>
  );
}
