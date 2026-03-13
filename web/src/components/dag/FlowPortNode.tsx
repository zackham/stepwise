import { ArrowDown, ArrowUp } from "lucide-react";
import type { FlowPortNode as FlowPortNodeType, DagSelection } from "@/lib/dag-layout";
import { cn } from "@/lib/utils";

interface FlowPortNodeProps {
  port: FlowPortNodeType;
  selection: DagSelection;
  onSelect: (selection: DagSelection) => void;
}

export function FlowPortNode({ port, selection, onSelect }: FlowPortNodeProps) {
  const isInput = port.type === "input";
  const Icon = isInput ? ArrowDown : ArrowUp;
  const title = isInput ? "Inputs" : "Outputs";

  return (
    <div
      className="absolute"
      style={{
        left: port.x,
        top: port.y,
        width: port.width,
        height: port.height,
      }}
    >
      <div className="flex flex-col items-center justify-center h-full rounded-md border border-dashed border-zinc-600/50 bg-zinc-900/30 px-2 py-1">
        <div className="flex items-center gap-1 text-[10px] text-zinc-500 mb-0.5">
          <Icon className="w-3 h-3" />
          <span className="font-medium">{title}</span>
        </div>
        <div className="flex flex-wrap gap-1 justify-center">
          {port.labels.map((field) => {
            const isSelected = isInput
              ? selection?.kind === "flow-input" && selection.fieldName === field
              : selection?.kind === "flow-output" && selection.fieldName === field;
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
                  "px-1.5 py-0 rounded text-[9px] font-mono leading-4 transition-colors",
                  isSelected
                    ? "bg-blue-500/20 text-blue-400 border border-blue-500/30"
                    : "bg-zinc-800/60 text-zinc-400 border border-zinc-700/40 hover:text-zinc-200 hover:border-zinc-600/60",
                )}
              >
                {field}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
