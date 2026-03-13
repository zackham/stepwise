import { useState } from "react";
import type { DagEdge, LoopEdge } from "@/lib/dag-layout";
import type { StepRun } from "@/lib/types";

interface SelectedLabel {
  fromStep: string;
  toStep: string;
  fieldName: string;
}

interface DagEdgesProps {
  edges: DagEdge[];
  loopEdges: LoopEdge[];
  width: number;
  height: number;
  onClickLabel?: (from: string, to: string, field: string) => void;
  selectedLabel?: SelectedLabel | null;
  latestRuns?: Record<string, StepRun>;
}

function formatPreviewValue(value: unknown, maxLen: number): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return String(value);
  if (typeof value === "number") return String(value);
  if (typeof value === "string") {
    if (value.length <= maxLen) return `"${value}"`;
    return `"${value.slice(0, maxLen - 2)}…"`;
  }
  if (Array.isArray(value)) return `[${value.length}]`;
  if (typeof value === "object") return `{${Object.keys(value as Record<string, unknown>).length}}`;
  return String(value);
}

function formatTooltipValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  return JSON.stringify(value, null, 2);
}

function buildPath(points: Array<{ x: number; y: number }>): string {
  if (points.length < 2) return "";

  const [start, ...rest] = points;
  let d = `M ${start.x} ${start.y}`;

  if (rest.length === 1) {
    d += ` L ${rest[0].x} ${rest[0].y}`;
  } else if (rest.length === 2) {
    d += ` Q ${rest[0].x} ${rest[0].y} ${rest[1].x} ${rest[1].y}`;
  } else {
    // Use smooth curve through points
    for (let i = 0; i < rest.length; i++) {
      const p = rest[i];
      if (i === 0) {
        const mid = {
          x: (start.x + p.x) / 2,
          y: (start.y + p.y) / 2,
        };
        d += ` Q ${start.x} ${start.y} ${mid.x} ${mid.y}`;
      } else {
        const prev = rest[i - 1];
        const mid = {
          x: (prev.x + p.x) / 2,
          y: (prev.y + p.y) / 2,
        };
        d += ` Q ${prev.x} ${prev.y} ${mid.x} ${mid.y}`;
      }
    }
    // Final line to last point
    const last = rest[rest.length - 1];
    d += ` L ${last.x} ${last.y}`;
  }

  return d;
}

function edgeMidpoint(
  points: Array<{ x: number; y: number }>
): { x: number; y: number } {
  if (points.length === 0) return { x: 0, y: 0 };
  if (points.length === 1) return points[0];
  const mid = Math.floor(points.length / 2);
  return points[mid];
}

const LABEL_LINE_HEIGHT = 14;

export function DagEdges({ edges, loopEdges, width, height, onClickLabel, selectedLabel, latestRuns }: DagEdgesProps) {
  const [hoveredLabel, setHoveredLabel] = useState<{
    x: number;
    y: number;
    field: string;
    value: unknown;
  } | null>(null);

  return (
    <>
    <svg
      className="absolute inset-0 pointer-events-none"
      width={width}
      height={height}
      style={{ overflow: "visible" }}
    >
      <defs>
        <marker
          id="arrowhead"
          markerWidth="8"
          markerHeight="6"
          refX="7"
          refY="3"
          orient="auto"
          markerUnits="strokeWidth"
        >
          <polygon
            points="0 0, 8 3, 0 6"
            fill="oklch(0.5 0 0)"
            opacity="0.6"
          />
        </marker>
        <marker
          id="loop-arrow"
          markerWidth="8"
          markerHeight="6"
          refX="7"
          refY="3"
          orient="auto"
          markerUnits="strokeWidth"
        >
          <polygon
            points="0 0, 8 3, 0 6"
            fill="oklch(0.55 0.15 300)"
            opacity="0.7"
          />
        </marker>
      </defs>
      {edges.map((edge, i) => {
        const mid = edgeMidpoint(edge.points);
        const isSequencingOnly = edge.labels.length === 0;
        const totalHeight = edge.labels.length * LABEL_LINE_HEIGHT;
        const startY = mid.y - 6 - totalHeight / 2 + LABEL_LINE_HEIGHT / 2;
        return (
          <g key={`${edge.from}-${edge.to}-${i}`}>
            <path
              d={buildPath(edge.points)}
              fill="none"
              stroke={isSequencingOnly ? "oklch(0.35 0 0)" : "oklch(0.4 0 0)"}
              strokeWidth={isSequencingOnly ? 1 : 1.5}
              strokeDasharray={isSequencingOnly ? "4 3" : "none"}
              markerEnd="url(#arrowhead)"
              opacity={isSequencingOnly ? 0.4 : 0.5}
            />
            {edge.labels.map((field, fi) => {
              const isSelected =
                selectedLabel &&
                selectedLabel.fromStep === edge.from &&
                selectedLabel.toStep === edge.to &&
                selectedLabel.fieldName === field;
              const ly = startY + fi * LABEL_LINE_HEIGHT;

              // Look up artifact value for this field
              const artifactValue = latestRuns?.[edge.from]?.result?.artifact?.[field];
              const hasValue = artifactValue !== undefined;
              const valuePreview = hasValue ? `: ${formatPreviewValue(artifactValue, 12)}` : "";
              const displayLen = field.length + valuePreview.length;

              // Estimate text width (~6px per char at 10px mono)
              const textW = displayLen * 6.5 + 12;
              const textH = 14;
              return (
                <g
                  key={field}
                  style={{ pointerEvents: "auto", cursor: onClickLabel ? "pointer" : undefined }}
                  onClick={(e) => {
                    if (!onClickLabel) return;
                    e.stopPropagation();
                    onClickLabel(edge.from, edge.to, field);
                  }}
                  onMouseEnter={() => {
                    if (hasValue) setHoveredLabel({ x: mid.x, y: ly, field, value: artifactValue });
                  }}
                  onMouseLeave={() => setHoveredLabel(null)}
                >
                  <rect
                    x={mid.x - textW / 2}
                    y={ly - textH / 2 - 1}
                    width={textW}
                    height={textH}
                    rx={3}
                    fill={isSelected ? "oklch(0.25 0.08 250)" : "transparent"}
                    className={onClickLabel ? "hover:fill-zinc-800/60" : ""}
                  />
                  <text
                    x={mid.x}
                    y={ly}
                    textAnchor="middle"
                    dominantBaseline="central"
                    className={
                      isSelected
                        ? "fill-blue-400 text-[10px]"
                        : hasValue
                          ? "text-[10px]"
                          : onClickLabel
                            ? "fill-zinc-500 text-[10px] hover:fill-zinc-300"
                            : "fill-zinc-500 text-[10px]"
                    }
                    style={{ fontFamily: "monospace" }}
                  >
                    {hasValue && !isSelected ? (
                      <>
                        <tspan className="fill-zinc-400">{field}</tspan>
                        <tspan className="fill-zinc-600">{valuePreview}</tspan>
                      </>
                    ) : hasValue ? (
                      <>{field}{valuePreview}</>
                    ) : (
                      field
                    )}
                  </text>
                </g>
              );
            })}
          </g>
        );
      })}

      {/* Loop-back edges */}
      {loopEdges.map((le) => (
        <g key={`loop-${le.from}-${le.to}`}>
          <path
            d={le.path}
            fill="none"
            stroke="oklch(0.55 0.15 300)"
            strokeWidth={1.5}
            strokeDasharray="6 3"
            markerEnd="url(#loop-arrow)"
            opacity={0.6}
          />
          <text
            x={le.labelPos.x}
            y={le.labelPos.y}
            textAnchor="start"
            className="fill-purple-400/80 text-[10px] font-medium"
            style={{ fontFamily: "monospace" }}
          >
            {le.label}
          </text>
        </g>
      ))}
    </svg>

    {/* Hover tooltip for edge field values */}
    {hoveredLabel && (
      <div
        className="absolute pointer-events-none z-50"
        style={{ left: hoveredLabel.x, top: hoveredLabel.y + 14 }}
      >
        <div className="bg-zinc-900 border border-zinc-700 rounded-md shadow-xl p-2 -translate-x-1/2">
          <div className="text-[10px] font-medium text-zinc-400 uppercase tracking-wide mb-1">
            {hoveredLabel.field}
          </div>
          <pre className="text-[11px] font-mono text-zinc-200 whitespace-pre-wrap break-words max-w-[280px] max-h-[200px] overflow-auto m-0">
            {formatTooltipValue(hoveredLabel.value)}
          </pre>
        </div>
      </div>
    )}
    </>
  );
}
