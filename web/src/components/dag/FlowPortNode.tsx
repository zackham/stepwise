import { ArrowDown, CheckCircle2 } from "lucide-react";
import type { FlowPortNode as FlowPortNodeType, DagSelection } from "@/lib/dag-layout";
import type { StepRun } from "@/lib/types";
import { cn, tryParseJsonValue } from "@/lib/utils";

interface FlowPortNodeProps {
  port: FlowPortNodeType;
  selection: DagSelection;
  onSelect: (selection: DagSelection) => void;
  latestRuns?: Record<string, StepRun>;
}

function formatPreview(rawValue: unknown, maxLen: number = 20): string {
  const value = tryParseJsonValue(rawValue);
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return String(value);
  if (typeof value === "number") return String(value);
  if (typeof value === "string") {
    if (value.length <= maxLen) return value;
    return value.slice(0, maxLen - 1) + "…";
  }
  if (Array.isArray(value)) return `[${value.length}]`;
  if (typeof value === "object") return `{${Object.keys(value as Record<string, unknown>).length}}`;
  return String(value);
}

export function FlowPortNode({ port, selection, onSelect, latestRuns }: FlowPortNodeProps) {
  const isInput = port.type === "input";
  const isOutput = port.type === "output";

  // Check if all output sources have completed
  const outputValues: Record<string, unknown> = {};
  let allOutputsReady = false;
  if (isOutput && latestRuns && port.fieldSources) {
    allOutputsReady = true;
    for (const [field, stepName] of Object.entries(port.fieldSources)) {
      const run = latestRuns[stepName];
      if (run?.status === "completed" && run.result?.artifact?.[field] !== undefined) {
        outputValues[field] = run.result.artifact[field];
      } else {
        allOutputsReady = false;
      }
    }
  }

  const title = isInput ? "Inputs" : "Outputs";

  return (
    <div
      className="absolute"
      style={{
        left: port.x,
        top: port.y,
        width: allOutputsReady ? Math.max(port.width, 240) : port.width,
        marginLeft: allOutputsReady ? Math.min(0, (port.width - 240) / 2) : 0,
      }}
    >
      <div
        className={cn(
          "flex flex-col items-center justify-center rounded-md border px-3 py-2",
          allOutputsReady
            ? "border-emerald-500/40 bg-emerald-950/40 shadow-lg shadow-emerald-500/5"
            : "border-dashed border-zinc-600/50 bg-zinc-900/30 px-2 py-1",
        )}
      >
        <div className={cn(
          "flex items-center gap-1 text-[10px] mb-1",
          allOutputsReady ? "text-emerald-400" : "text-zinc-500",
        )}>
          {allOutputsReady ? (
            <CheckCircle2 className="w-3 h-3" />
          ) : (
            <ArrowDown className="w-3 h-3" />
          )}
          <span className="font-medium">{title}</span>
        </div>

        <div className={cn(
          "flex flex-col gap-1 w-full",
          !allOutputsReady && "flex-row flex-wrap justify-center items-center",
        )}>
          {port.labels.map((field) => {
            const isSelected = isInput
              ? selection?.kind === "flow-input" && selection.fieldName === field
              : selection?.kind === "flow-output" && selection.fieldName === field;
            const value = outputValues[field];
            const hasValue = value !== undefined;

            return (
              <button
                key={field}
                onClick={(e) => {
                  e.stopPropagation();
                  if (isInput) {
                    onSelect({ kind: "flow-input", fieldName: field });
                  } else {
                    const stepName = port.fieldSources?.[field] ?? "";
                    onSelect({ kind: "flow-output", stepName, fieldName: field });
                  }
                }}
                className={cn(
                  "px-1.5 py-0.5 rounded text-[9px] font-mono leading-4 transition-colors text-left",
                  isSelected
                    ? "bg-blue-500/20 text-blue-400 border border-blue-500/30"
                    : hasValue
                      ? "bg-emerald-500/10 text-emerald-300/80 border border-emerald-500/20 hover:text-emerald-200 hover:border-emerald-500/40"
                      : "bg-zinc-800/60 text-zinc-400 border border-zinc-700/40 hover:text-zinc-200 hover:border-zinc-600/60",
                )}
              >
                {hasValue ? (
                  <>
                    <span className="text-emerald-400/70">{field}: </span>
                    <span className="text-emerald-200/60">{formatPreview(value, 24)}</span>
                  </>
                ) : (
                  field
                )}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
