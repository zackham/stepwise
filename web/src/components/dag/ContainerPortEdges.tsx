import { useState } from "react";
import type { ContainerPort, HierarchicalDagNode } from "@/lib/dag-layout";
import type { StepRun } from "@/lib/types";
import { tryParseJsonValue } from "@/lib/utils";
import { useTheme } from "@/hooks/useTheme";

function formatPreview(rawValue: unknown, maxLen: number = 30): string {
  const value = tryParseJsonValue(rawValue);
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return String(value);
  if (typeof value === "number") return String(value);
  if (typeof value === "string") {
    if (value.length <= maxLen) return value;
    return value.slice(0, maxLen - 1) + "\u2026";
  }
  if (Array.isArray(value)) return `[${value.length} items]`;
  if (typeof value === "object") return `{${Object.keys(value as Record<string, unknown>).length} keys}`;
  return String(value);
}

function formatTooltipValue(rawValue: unknown): string {
  const value = tryParseJsonValue(rawValue);
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  return JSON.stringify(value, null, 2);
}

interface ContainerPortEdgesProps {
  containerPorts: ContainerPort[];
  nodes: HierarchicalDagNode[];
  layoutWidth: number;
  layoutHeight: number;
  jobInputs?: Record<string, unknown>;
  latestRuns?: Record<string, StepRun>;
  /** Total height of the outer container (for positioning output labels) */
  containerHeight?: number;
  /** Height of the container header bar */
  headerHeight?: number;
  /** Top offset of the child layout content area within the container */
  contentTop?: number;
}

interface HoverInfo {
  field: string;
  value: unknown;
  x: number;
  y: number;
}

export function ContainerPortEdges({
  containerPorts,
  nodes,
  layoutWidth,
  layoutHeight,
  jobInputs,
  latestRuns,
  containerHeight,
  headerHeight,
  contentTop,
}: ContainerPortEdgesProps) {
  const [hover, setHover] = useState<HoverInfo | null>(null);
  const theme = useTheme();
  const isDark = theme === "dark";
  const portStroke = isDark ? "rgb(168 85 247 / 0.25)" : "rgb(168 85 247 / 0.45)";

  if (containerPorts.length === 0) return null;

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));

  // Group input ports together so we can stack their labels
  const inputPorts = containerPorts.filter((p) => p.type === "input");
  const outputPorts = containerPorts.filter((p) => p.type === "output");

  // Collect all input labels for a single stacked label block
  const allInputLabels: { field: string; stepName: string }[] = [];
  for (const port of inputPorts) {
    for (const field of port.labels) {
      allInputLabels.push({ field, stepName: port.stepName });
    }
  }

  const allOutputLabels: { field: string; stepName: string }[] = [];
  for (const port of outputPorts) {
    for (const field of port.labels) {
      allOutputLabels.push({ field, stepName: port.stepName });
    }
  }

  return (
    <>
      <svg
        className="absolute top-0 left-0"
        width={layoutWidth}
        height={layoutHeight}
        style={{ overflow: "visible", pointerEvents: "none" }}
      >
        {containerPorts.map((port) => {
          const node = nodeMap.get(port.stepName);
          if (!node) return null;

          const stepCx = node.x + node.width / 2;
          const containerCx = layoutWidth / 2;

          if (port.type === "input") {
            const startY = -8;
            const endY = node.y;
            const midY = (startY + endY) / 2;
            const path = `M ${containerCx} ${startY} C ${containerCx} ${midY}, ${stepCx} ${midY}, ${stepCx} ${endY}`;

            return (
              <path
                key={`input-${port.stepName}`}
                d={path}
                fill="none"
                stroke={portStroke}
                strokeWidth={1.5}
                strokeDasharray="4 3"
              />
            );
          } else {
            const startY = node.y + node.height;
            const endY = layoutHeight + 8;
            const midY = (startY + endY) / 2;
            const path = `M ${stepCx} ${startY} C ${stepCx} ${midY}, ${containerCx} ${midY}, ${containerCx} ${endY}`;

            return (
              <path
                key={`output-${port.stepName}`}
                d={path}
                fill="none"
                stroke={portStroke}
                strokeWidth={1.5}
                strokeDasharray="4 3"
              />
            );
          }
        })}
      </svg>

      {/* Input labels — positioned above the child layout, below the header */}
      {allInputLabels.length > 0 && (
        <div
          className="absolute flex flex-col items-center gap-0.5"
          style={{
            left: layoutWidth / 2,
            top: 2,
            transform: "translateX(-50%)",
          }}
        >
          {allInputLabels.map(({ field }) => {
            const value = jobInputs?.[field];
            const hasValue = value !== undefined;
            return (
              <span
                key={field}
                className={
                  hasValue
                    ? "px-1.5 py-0.5 rounded text-[9px] font-mono bg-purple-500/10 text-purple-300/80 border border-purple-500/20 cursor-default whitespace-nowrap"
                    : "px-1.5 py-0.5 rounded text-[9px] font-mono bg-zinc-200/60 dark:bg-zinc-800/60 text-zinc-500 border border-zinc-300/30 dark:border-zinc-700/30 whitespace-nowrap"
                }
                onMouseEnter={(e) => {
                  if (!hasValue) return;
                  const rect = e.currentTarget.getBoundingClientRect();
                  const parent = e.currentTarget.closest("[style]")?.parentElement;
                  const parentRect = parent?.getBoundingClientRect();
                  setHover({
                    field,
                    value,
                    x: rect.left - (parentRect?.left ?? 0) + rect.width / 2,
                    y: rect.bottom - (parentRect?.top ?? 0),
                  });
                }}
                onMouseLeave={() => setHover(null)}
              >
                {hasValue ? (
                  <>
                    <span className="text-purple-400/60">{field}: </span>
                    <span className="text-purple-200/70">{formatPreview(value, 20)}</span>
                  </>
                ) : (
                  field
                )}
              </span>
            );
          })}
        </div>
      )}

      {/* Output labels — pinned near bottom, stacked vertically */}
      {allOutputLabels.length > 0 && (
        <div
          className="absolute flex flex-col items-center gap-0.5"
          style={{
            left: layoutWidth / 2,
            top: layoutHeight + 2,
            transform: "translateX(-50%)",
          }}
        >
          {allOutputLabels.map(({ field, stepName }) => {
            const run = latestRuns?.[stepName];
            const value = run?.status === "completed" ? run.result?.artifact?.[field] : undefined;
            const hasValue = value !== undefined;
            return (
              <span
                key={field}
                className={
                  hasValue
                    ? "px-1.5 py-0.5 rounded text-[9px] font-mono bg-emerald-500/10 text-emerald-300/70 border border-emerald-500/20 cursor-default whitespace-nowrap"
                    : "px-1.5 py-0.5 rounded text-[9px] font-mono bg-zinc-200/60 dark:bg-zinc-800/60 text-zinc-500 border border-zinc-300/30 dark:border-zinc-700/30 whitespace-nowrap"
                }
                onMouseEnter={(e) => {
                  if (!hasValue) return;
                  const rect = e.currentTarget.getBoundingClientRect();
                  const parent = e.currentTarget.closest("[style]")?.parentElement;
                  const parentRect = parent?.getBoundingClientRect();
                  setHover({
                    field,
                    value,
                    x: rect.left - (parentRect?.left ?? 0) + rect.width / 2,
                    y: rect.bottom - (parentRect?.top ?? 0),
                  });
                }}
                onMouseLeave={() => setHover(null)}
              >
                {hasValue ? (
                  <>
                    <span className="text-emerald-400/60">{field}: </span>
                    <span className="text-emerald-200/60">{formatPreview(value, 20)}</span>
                  </>
                ) : (
                  field
                )}
              </span>
            );
          })}
        </div>
      )}

      {/* Hover tooltip */}
      {hover && (
        <div
          className="absolute pointer-events-none z-50"
          style={{
            left: hover.x,
            top: hover.y + 6,
            transform: "translateX(-50%)",
          }}
        >
          <div className="bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-md shadow-xl p-2">
            <div className="text-[10px] font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wide mb-1">
              {hover.field}
            </div>
            <pre className="text-[11px] font-mono text-zinc-800 dark:text-zinc-200 whitespace-pre-wrap break-words max-w-[280px] max-h-[200px] overflow-auto m-0">
              {formatTooltipValue(hover.value)}
            </pre>
          </div>
        </div>
      )}
    </>
  );
}
