import { X, Copy, Check, ArrowRight, ArrowDown, ArrowUp } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { JsonView } from "@/components/JsonView";
import type { DagSelection } from "@/lib/dag-layout";
import type { Job, StepRun } from "@/lib/types";
import { useState } from "react";

interface DataFlowPanelProps {
  selection: NonNullable<DagSelection>;
  job: Job;
  latestRuns: Record<string, StepRun>;
  outputs: Record<string, unknown> | null;
  onClose: () => void;
}

function CopyButton({ value }: { value: unknown }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(
          typeof value === "string" ? value : JSON.stringify(value, null, 2),
        );
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="text-zinc-500 hover:text-zinc-300 p-0.5"
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

export function DataFlowPanel({
  selection,
  job,
  latestRuns,
  outputs,
  onClose,
}: DataFlowPanelProps) {
  if (selection.kind === "step") return null;

  let title = "";
  let badge: { label: string; icon: React.ReactNode } | null = null;
  let context = "";
  let value: unknown = undefined;
  let noDataMessage = "";

  if (selection.kind === "edge-field") {
    title = selection.fieldName;
    const isFromFlowInput = selection.fromStep === "__flow_input__";
    context = isFromFlowInput
      ? `Job Input → ${selection.toStep}`
      : `${selection.fromStep} → ${selection.toStep}`;
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
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border bg-zinc-950/50 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <ArrowRight className="w-3.5 h-3.5 text-zinc-500 shrink-0" />
          <span className="text-xs font-mono font-medium text-zinc-300 truncate">
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
          className="text-zinc-600 hover:text-zinc-300 p-0.5 shrink-0"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Content */}
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
              <div className="bg-zinc-900/50 rounded border border-zinc-800 p-2 overflow-x-auto">
                <JsonView data={value} defaultExpanded={true} />
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
    </>
  );
}
