import type { StepDefinition, StepRun } from "@/lib/types";
import { StepStatusBadge } from "@/components/StatusBadge";
import { JsonView } from "@/components/JsonView";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Clock, AlertTriangle, DollarSign } from "lucide-react";
import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { cn, formatDuration } from "@/lib/utils";

interface RunComparisonViewProps {
  runs: StepRun[];
  stepDef: StepDefinition;
  exitResolutions: Record<number, { rule: string; action: string }>;
  expanded?: boolean;
  isSubscription: boolean;
}

function formatTimestamp(ts: string | null): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleTimeString();
}

function formatCost(cost: number | null | undefined): string {
  if (cost == null || cost === 0) return "-";
  if (cost < 0.01) return `$${cost.toFixed(4)}`;
  return `$${cost.toFixed(2)}`;
}

// ── Diff helpers ──────────────────────────────────────────────────────

function DiffJsonView({
  data,
  compareData,
  side,
}: {
  data: Record<string, unknown>;
  compareData: Record<string, unknown> | null;
  side: "left" | "right";
}) {
  if (!compareData) {
    return <JsonView data={data} defaultExpanded />;
  }

  const allKeys = [...new Set([...Object.keys(data), ...Object.keys(compareData)])];
  const changedKeys = allKeys.filter(
    (k) => JSON.stringify(data[k]) !== JSON.stringify(compareData[k])
  );
  const unchangedKeys = allKeys.filter(
    (k) => JSON.stringify(data[k]) === JSON.stringify(compareData[k])
  );

  if (changedKeys.length === 0) {
    return <JsonView data={data} defaultExpanded />;
  }

  const borderColor = side === "left" ? "border-amber-500/50" : "border-blue-500/50";
  const bgColor = side === "left" ? "bg-amber-500/5" : "bg-blue-500/5";

  return (
    <div className="space-y-1">
      {changedKeys.map((key) => (
        <div key={key} className={cn("border-l-2 pl-2 rounded-r", borderColor, bgColor)}>
          <JsonView data={data[key]} name={key} defaultExpanded />
        </div>
      ))}
      {unchangedKeys
        .filter((k) => k in data)
        .map((key) => (
          <div key={key}>
            <JsonView data={data[key]} name={key} defaultExpanded />
          </div>
        ))}
    </div>
  );
}

function DiffTextBlock({
  text,
  compareText,
  side,
  className,
}: {
  text: string;
  compareText: string | null;
  side: "left" | "right";
  className?: string;
}) {
  const isDiff = compareText !== null && text !== compareText;
  const borderColor = side === "left" ? "border-l-amber-500/50" : "border-l-blue-500/50";
  const bgColor = side === "left" ? "bg-amber-500/5" : "bg-blue-500/5";

  return (
    <div className="relative">
      {isDiff && (
        <Badge
          variant="outline"
          className={cn(
            "absolute top-1 right-1 text-[9px] py-0 px-1",
            side === "left"
              ? "text-amber-400 border-amber-500/30"
              : "text-blue-400 border-blue-500/30"
          )}
        >
          Changed
        </Badge>
      )}
      <pre
        className={cn(
          "text-xs font-mono bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded p-2 whitespace-pre-wrap break-words",
          isDiff && `border-l-2 ${borderColor} ${bgColor}`,
          className
        )}
      >
        {text.trim()}
      </pre>
    </div>
  );
}

function DiffScalar({
  label,
  value,
  compareValue,
  side,
}: {
  label: string;
  value: string;
  compareValue: string | null;
  side: "left" | "right";
}) {
  const isDiff = compareValue !== null && value !== compareValue;
  const tintColor = side === "left" ? "bg-amber-500/10" : "bg-blue-500/10";

  return (
    <>
      <span className="text-zinc-500">{label}</span>
      <span
        className={cn(
          "text-zinc-500 dark:text-zinc-400 font-mono",
          isDiff && `rounded px-1 ${tintColor}`
        )}
      >
        {value}
      </span>
    </>
  );
}

// ── Column ────────────────────────────────────────────────────────────

function RunComparisonColumn({
  run,
  compareRun,
  side,
  exitResolution,
  expanded,
  isSubscription,
  stepDef,
}: {
  run: StepRun;
  compareRun: StepRun;
  side: "left" | "right";
  exitResolution?: { rule: string; action: string };
  expanded?: boolean;
  isSubscription: boolean;
  stepDef: StepDefinition;
}) {
  const duration = formatDuration(run.started_at, run.completed_at);
  const compareDuration = formatDuration(compareRun.started_at, compareRun.completed_at);
  const cost = run.result?.executor_meta?.cost_usd as number | undefined;
  const compareCost = compareRun.result?.executor_meta?.cost_usd as number | undefined;

  // Resolved prompt/command from interpolated config
  const ic = run.executor_state?._interpolated_config as Record<string, unknown> | undefined;
  const resolvedPrompt = ic?.prompt as string | undefined;
  const resolvedCommand = ic?.command as string | undefined;
  const resolvedCheckCommand = ic?.check_command as string | undefined;

  const compareIc = compareRun.executor_state?._interpolated_config as Record<string, unknown> | undefined;
  const comparePrompt = compareIc?.prompt as string | undefined;
  const compareCommand = compareIc?.command as string | undefined;
  const compareCheckCommand = compareIc?.check_command as string | undefined;

  // Template values for "same as template" check
  const templatePrompt = stepDef.executor.config.prompt as string | undefined;
  const templateCommand = stepDef.executor.config.command as string | undefined;
  const templateCheckCommand = stepDef.executor.config.check_command as string | undefined;

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center gap-2 flex-wrap">
        <StepStatusBadge status={run.status} />
        <span className="text-zinc-500 dark:text-zinc-400 text-sm">Attempt #{run.attempt}</span>
        <span className="text-zinc-600 text-xs flex items-center gap-1">
          <Clock className="w-3 h-3" />
          <span
            className={cn(
              duration !== compareDuration &&
                (side === "left" ? "bg-amber-500/10 rounded px-1" : "bg-blue-500/10 rounded px-1")
            )}
          >
            {duration}
          </span>
        </span>
        {exitResolution && (
          <span
            className={cn(
              "text-[10px] font-mono px-1.5 py-0.5 rounded",
              exitResolution.action === "advance" && "text-emerald-400 bg-emerald-500/10",
              exitResolution.action === "loop" && "text-amber-400 bg-amber-500/10",
              exitResolution.action === "escalate" && "text-red-400 bg-red-500/10",
              exitResolution.action === "abandon" && "text-red-500 bg-red-500/10"
            )}
          >
            → {exitResolution.rule}
          </span>
        )}
      </div>

      {/* Timestamps */}
      <div className="grid grid-cols-2 gap-1 text-xs">
        <DiffScalar
          label="Started"
          value={formatTimestamp(run.started_at)}
          compareValue={formatTimestamp(compareRun.started_at)}
          side={side}
        />
        <DiffScalar
          label="Completed"
          value={formatTimestamp(run.completed_at)}
          compareValue={formatTimestamp(compareRun.completed_at)}
          side={side}
        />
        <span className="text-zinc-500">Run ID</span>
        <span className="text-zinc-600 font-mono text-[10px] break-all">{run.id}</span>
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
            {compareRun.error && run.error !== compareRun.error && (
              <Badge
                variant="outline"
                className={cn(
                  "text-[9px] py-0 px-1 ml-auto",
                  side === "left"
                    ? "text-amber-400 border-amber-500/30"
                    : "text-blue-400 border-blue-500/30"
                )}
              >
                Changed
              </Badge>
            )}
          </div>
          <div className="text-red-300/80 text-xs font-mono whitespace-pre-wrap break-words">
            {run.error}
          </div>
        </div>
      )}

      {/* Cost */}
      {(isSubscription ||
        (cost != null && cost > 0)) && (
        <div className="flex items-center gap-1.5 text-xs">
          <DollarSign className="w-3 h-3 text-emerald-500" />
          <span className="text-zinc-500">Cost:</span>
          <span
            className={cn(
              "font-mono text-emerald-400",
              formatCost(cost) !== formatCost(compareCost) &&
                (side === "left" ? "bg-amber-500/10 rounded px-1" : "bg-blue-500/10 rounded px-1")
            )}
          >
            {isSubscription ? "$0 (Max)" : formatCost(cost)}
          </span>
        </div>
      )}

      {/* Inputs */}
      {run.inputs && Object.keys(run.inputs).length > 0 && (
        <div>
          <div className="text-xs text-zinc-500 mb-1">Inputs</div>
          <DiffJsonView
            data={run.inputs}
            compareData={compareRun.inputs}
            side={side}
          />
        </div>
      )}

      {/* Resolved Prompt */}
      {resolvedPrompt && resolvedPrompt !== templatePrompt && (
        <div>
          <div className="text-xs text-zinc-500 mb-1">Resolved Prompt</div>
          <DiffTextBlock
            text={resolvedPrompt}
            compareText={comparePrompt ?? null}
            side={side}
            className={cn("text-emerald-300", !expanded && "max-h-48 overflow-auto")}
          />
        </div>
      )}

      {/* Resolved Command */}
      {resolvedCommand && resolvedCommand !== templateCommand && (
        <div>
          <div className="text-xs text-zinc-500 mb-1">Resolved Command</div>
          <DiffTextBlock
            text={resolvedCommand}
            compareText={compareCommand ?? null}
            side={side}
            className={cn("text-emerald-300", !expanded && "max-h-48 overflow-auto")}
          />
        </div>
      )}

      {/* Resolved Check Command */}
      {resolvedCheckCommand && resolvedCheckCommand !== templateCheckCommand && (
        <div>
          <div className="text-xs text-zinc-500 mb-1">Resolved Check Command</div>
          <DiffTextBlock
            text={resolvedCheckCommand}
            compareText={compareCheckCommand ?? null}
            side={side}
            className={cn("text-emerald-300", !expanded && "max-h-48 overflow-auto")}
          />
        </div>
      )}

      {/* Output Artifact */}
      {run.result?.artifact && Object.keys(run.result.artifact).length > 0 && (
        <div>
          <div className="text-xs text-zinc-500 mb-1">Output</div>
          <DiffJsonView
            data={run.result.artifact}
            compareData={compareRun.result?.artifact ?? null}
            side={side}
          />
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────

export function RunComparisonView({
  runs,
  stepDef,
  exitResolutions,
  expanded,
  isSubscription,
}: RunComparisonViewProps) {
  const sortedRuns = [...runs].sort((a, b) => b.attempt - a.attempt);
  const containerRef = useRef<HTMLDivElement>(null);

  const [leftAttempt, setLeftAttempt] = useState(
    sortedRuns.length >= 2 ? sortedRuns[1].attempt : sortedRuns[0].attempt
  );
  const [rightAttempt, setRightAttempt] = useState(sortedRuns[0].attempt);

  const leftRun = sortedRuns.find((r) => r.attempt === leftAttempt) ?? sortedRuns[1];
  const rightRun = sortedRuns.find((r) => r.attempt === rightAttempt) ?? sortedRuns[0];

  // Available attempts for cycling
  const attempts = useMemo(
    () => sortedRuns.map((r) => r.attempt).sort((a, b) => a - b),
    [sortedRuns]
  );

  const cycleAttempt = useCallback(
    (current: number, direction: 1 | -1, exclude: number): number => {
      const idx = attempts.indexOf(current);
      let next = idx;
      for (let i = 0; i < attempts.length; i++) {
        next = (next + direction + attempts.length) % attempts.length;
        if (attempts[next] !== exclude) return attempts[next];
      }
      return current;
    },
    [attempts]
  );

  // Keyboard navigation
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const handler = (e: KeyboardEvent) => {
      if (!container.contains(document.activeElement) && document.activeElement !== container) return;

      if (e.key === "ArrowLeft") {
        e.preventDefault();
        setLeftAttempt((prev) => cycleAttempt(prev, -1, rightAttempt));
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        setRightAttempt((prev) => cycleAttempt(prev, 1, leftAttempt));
      }
    };

    container.addEventListener("keydown", handler);
    return () => container.removeEventListener("keydown", handler);
  }, [cycleAttempt, leftAttempt, rightAttempt]);

  return (
    <div ref={containerRef} tabIndex={-1} className="outline-none space-y-3">
      {/* Attempt selectors */}
      <div className="flex flex-col sm:flex-row gap-2">
        <div className="flex-1 min-w-0">
          <div className="text-[10px] text-zinc-500 uppercase tracking-wide mb-1">Left</div>
          <Select
            value={leftAttempt}
            onValueChange={(v) => { if (v !== null) setLeftAttempt(v as number); }}
          >
            <SelectTrigger size="sm" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {sortedRuns.map((r) => (
                <SelectItem
                  key={r.attempt}
                  value={r.attempt}
                  disabled={r.attempt === rightAttempt}
                >
                  Attempt #{r.attempt} — {r.status}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-[10px] text-zinc-500 uppercase tracking-wide mb-1">Right</div>
          <Select
            value={rightAttempt}
            onValueChange={(v) => { if (v !== null) setRightAttempt(v as number); }}
          >
            <SelectTrigger size="sm" className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {sortedRuns.map((r) => (
                <SelectItem
                  key={r.attempt}
                  value={r.attempt}
                  disabled={r.attempt === leftAttempt}
                >
                  Attempt #{r.attempt} — {r.status}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Side-by-side columns */}
      <div className="flex flex-col md:flex-row gap-3">
        <div className="flex-1 min-w-0">
          <ScrollArea className="max-h-[70vh]">
            <RunComparisonColumn
              run={leftRun}
              compareRun={rightRun}
              side="left"
              exitResolution={exitResolutions[leftRun.attempt]}
              expanded={expanded}
              isSubscription={isSubscription}
              stepDef={stepDef}
            />
          </ScrollArea>
        </div>
        <Separator orientation="vertical" className="hidden md:block self-stretch" />
        <Separator className="md:hidden" />
        <div className="flex-1 min-w-0">
          <ScrollArea className="max-h-[70vh]">
            <RunComparisonColumn
              run={rightRun}
              compareRun={leftRun}
              side="right"
              exitResolution={exitResolutions[rightRun.attempt]}
              expanded={expanded}
              isSubscription={isSubscription}
              stepDef={stepDef}
            />
          </ScrollArea>
        </div>
      </div>
    </div>
  );
}
