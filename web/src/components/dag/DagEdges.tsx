import type { DagEdge, LoopEdge } from "@/lib/dag-layout";
import type { StepRun } from "@/lib/types";
import type { CriticalPathResult } from "@/lib/critical-path";
import { tryParseJsonValue } from "@/lib/utils";
import { useTheme } from "@/hooks/useTheme";

interface SelectedLabel {
  fromStep: string;
  toStep: string;
  fieldName: string;
}

export interface HoveredLabelInfo {
  x: number;
  y: number;
  field: string;
  value: unknown;
}

interface DagEdgesProps {
  edges: DagEdge[];
  loopEdges: LoopEdge[];
  width: number;
  height: number;
  onClickLabel?: (from: string, to: string, field: string) => void;
  selectedLabel?: SelectedLabel | null;
  latestRuns?: Record<string, StepRun>;
  onHoverLabel?: (info: HoveredLabelInfo) => void;
  onLeaveLabel?: () => void;
  criticalPath?: CriticalPathResult | null;
  webglActive?: boolean;
}

function formatPreviewValue(rawValue: unknown, maxLen: number): string {
  const value = tryParseJsonValue(rawValue);
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
const LABEL_FONT = "10px monospace";
const LABEL_PADDING_X = 12;
const labelWidthCache = new Map<string, number>();
let labelMeasureContext:
  | CanvasRenderingContext2D
  | OffscreenCanvasRenderingContext2D
  | null
  | undefined;

function estimateLabelWidth(text: string): number {
  return text.length * 6.5 + LABEL_PADDING_X;
}

function getLabelMeasureContext() {
  if (labelMeasureContext !== undefined) return labelMeasureContext;

  if (typeof OffscreenCanvas !== "undefined") {
    labelMeasureContext = new OffscreenCanvas(1, 1).getContext("2d");
  } else if (typeof document !== "undefined") {
    labelMeasureContext = document.createElement("canvas").getContext("2d");
  } else {
    labelMeasureContext = null;
  }

  if (labelMeasureContext) {
    labelMeasureContext.font = LABEL_FONT;
  }
  return labelMeasureContext;
}

function measureLabelWidth(text: string): number {
  const cached = labelWidthCache.get(text);
  if (cached != null) return cached;

  const context = getLabelMeasureContext();
  let width = estimateLabelWidth(text);

  if (context) {
    context.font = LABEL_FONT;
    const measured = context.measureText(text).width;
    if (isFinite(measured) && measured > 0) {
      width = Math.ceil(measured) + LABEL_PADDING_X;
    }
  }

  labelWidthCache.set(text, width);
  return width;
}

export function DagEdges({ edges, loopEdges, width, height, onClickLabel, selectedLabel, latestRuns, onHoverLabel, onLeaveLabel, criticalPath, webglActive }: DagEdgesProps) {
  const theme = useTheme();
  const isDark = theme === "dark";

  const inactiveEdge = isDark ? "oklch(0.35 0 0)" : "oklch(0.7 0 0)";
  const inactiveEdgeData = isDark ? "oklch(0.4 0 0)" : "oklch(0.65 0 0)";
  const completedEdge = isDark ? "oklch(0.5 0.1 160)" : "oklch(0.35 0.12 160)";
  const arrowFill = isDark ? "oklch(0.5 0 0)" : "oklch(0.7 0 0)";
  const completedArrowFill = isDark ? "oklch(0.5 0.1 160)" : "oklch(0.35 0.12 160)";
  const selectedLabelBg = isDark ? "oklch(0.25 0.08 250)" : "oklch(0.92 0.04 250)";

  return (
    <svg
      className="absolute inset-0 pointer-events-none"
      width={isFinite(width) ? width : 0}
      height={isFinite(height) ? height : 0}
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
            fill={arrowFill}
            opacity="0.6"
          />
        </marker>
        <marker
          id="arrowhead-active"
          markerWidth="8"
          markerHeight="6"
          refX="7"
          refY="3"
          orient="auto"
          markerUnits="strokeWidth"
        >
          <polygon
            points="0 0, 8 3, 0 6"
            fill="oklch(0.65 0.15 250)"
          />
        </marker>
        <marker
          id="arrowhead-suspended"
          markerWidth="8"
          markerHeight="6"
          refX="7"
          refY="3"
          orient="auto"
          markerUnits="strokeWidth"
        >
          <polygon
            points="0 0, 8 3, 0 6"
            fill="oklch(0.7 0.15 85)"
          />
        </marker>
        <marker
          id="arrowhead-completed"
          markerWidth="8"
          markerHeight="6"
          refX="7"
          refY="3"
          orient="auto"
          markerUnits="strokeWidth"
        >
          <polygon
            points="0 0, 8 3, 0 6"
            fill={completedArrowFill}
            opacity="0.7"
          />
        </marker>
        <marker
          id="arrowhead-critical"
          markerWidth="8"
          markerHeight="6"
          refX="7"
          refY="3"
          orient="auto"
          markerUnits="strokeWidth"
        >
          <polygon
            points="0 0, 8 3, 0 6"
            fill="oklch(0.8 0.15 85)"
            opacity="0.8"
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
            fill="oklch(0.65 0.15 55)"
            opacity="0.7"
          />
        </marker>
      </defs>
      <style>{`
        @keyframes dash-flow {
          to { stroke-dashoffset: -20; }
        }
        .edge-active {
          animation: dash-flow 0.8s linear infinite;
        }
      `}</style>
      {edges.map((edge, i) => {
        const mid = edgeMidpoint(edge.points);
        const isSequencingOnly = edge.labels.length === 0;
        const totalHeight = edge.labels.length * LABEL_LINE_HEIGHT;
        const startY = mid.y - 6 - totalHeight / 2 + LABEL_LINE_HEIGHT / 2;

        // Check source and target status for edge state
        const sourceStatus = latestRuns?.[edge.from]?.status;
        const targetStatus = latestRuns?.[edge.to]?.status;
        const isRunning = targetStatus === "running" || targetStatus === "delegated";
        const isSuspended = targetStatus === "suspended";
        const isActive = isRunning || isSuspended;
        const isCompleted = sourceStatus === "completed" && (
          targetStatus === "completed" || targetStatus === "running" || targetStatus === "delegated"
        );

        // Data edges are always blue when active (loop edges are orange)
        const activeColor = "oklch(0.6 0.15 250)";
        const completedColor = completedEdge;
        const isCritical = criticalPath?.edges.has(`${edge.from}->${edge.to}`) ?? false;

        const pathD = buildPath(edge.points);

        return (
          <g key={`${edge.from}-${edge.to}-${i}`}>
            {/* SVG paths — skip when WebGL handles edge rendering */}
            {!webglActive && (
              <>
                {/* Critical path highlight layer */}
                {isCritical && (
                  <path
                    d={pathD}
                    fill="none"
                    stroke="oklch(0.8 0.15 85)"
                    strokeWidth={3}
                    opacity={0.7}
                    strokeLinecap="round"
                    markerEnd="url(#arrowhead-critical)"
                  />
                )}
                {/* Glow layer for active edges */}
                {isActive && (
                  <path
                    d={pathD}
                    fill="none"
                    stroke={activeColor}
                    strokeWidth={6}
                    opacity={0.15}
                    strokeLinecap="round"
                  />
                )}
                {/* Subtle glow for completed edges */}
                {isCompleted && !isActive && (
                  <path
                    d={pathD}
                    fill="none"
                    stroke={completedColor}
                    strokeWidth={4}
                    opacity={0.08}
                    strokeLinecap="round"
                  />
                )}
                <path
                  d={pathD}
                  fill="none"
                  stroke={
                    isActive
                      ? activeColor
                      : isCompleted
                        ? completedColor
                        : isSequencingOnly ? inactiveEdge : inactiveEdgeData
                  }
                  strokeWidth={isActive ? 2 : isCompleted ? 1.5 : isSequencingOnly ? 1 : 1.5}
                  strokeDasharray={isActive ? "8 12" : isSequencingOnly ? "4 3" : "none"}
                  markerEnd={
                    isActive ? "url(#arrowhead-active)"
                      : isCompleted ? "url(#arrowhead-completed)"
                        : "url(#arrowhead)"
                  }
                  opacity={isActive ? 0.8 : isCompleted ? 0.7 : isSequencingOnly ? 0.4 : 0.5}
                  className={isActive ? "edge-active" : undefined}
                />
              </>
            )}
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
              const textW = measureLabelWidth(`${field}${valuePreview}`);
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
                    if (hasValue && onHoverLabel) onHoverLabel({ x: mid.x, y: ly, field, value: artifactValue });
                  }}
                  onMouseLeave={() => onLeaveLabel?.()}
                >
                  <rect
                    x={mid.x - textW / 2}
                    y={ly - textH / 2 - 1}
                    width={textW}
                    height={textH}
                    rx={3}
                    fill={isSelected ? selectedLabelBg : "transparent"}
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

      {/* Loop-back edges — always orange (distinct from blue data edges) */}
      {loopEdges.map((le) => {
        const sourceRun = latestRuns?.[le.from];
        const sourceHasRun = sourceRun && (sourceRun.status === "completed" || sourceRun.status === "failed");
        const targetStatus = latestRuns?.[le.to]?.status;
        const isRunning = targetStatus === "running";
        const isSuspended = targetStatus === "suspended";
        const isActive = sourceHasRun && (isRunning || isSuspended);

        // Loop edges stay orange when active (data edges go blue)
        const activeColor = "oklch(0.7 0.15 55)";
        const inactiveColor = "oklch(0.55 0.12 55)";
        const isLoopCritical = criticalPath?.edges.has(`${le.from}->${le.to}`) ?? false;

        return (
          <g key={`loop-${le.from}-${le.to}`}>
            {!webglActive && (
              <>
                {isLoopCritical && (
                  <path
                    d={le.path}
                    fill="none"
                    stroke="oklch(0.8 0.15 85)"
                    strokeWidth={3}
                    opacity={0.7}
                    strokeLinecap="round"
                  />
                )}
                {isActive && (
                  <path
                    d={le.path}
                    fill="none"
                    stroke={activeColor}
                    strokeWidth={6}
                    opacity={0.15}
                    strokeLinecap="round"
                  />
                )}
                <path
                  d={le.path}
                  fill="none"
                  stroke={isActive ? activeColor : inactiveColor}
                  strokeWidth={isActive ? 2 : 1.5}
                  strokeDasharray={isActive ? "8 12" : "6 3"}
                  markerEnd="url(#loop-arrow)"
                  opacity={isActive ? 0.85 : 0.5}
                  className={isActive ? "edge-active" : undefined}
                />
              </>
            )}
            <text
              x={le.labelPos.x}
              y={le.labelPos.y}
              textAnchor="start"
              className="fill-orange-400/80 text-[10px] font-medium"
              style={{ fontFamily: "monospace" }}
            >
              {le.label}
            </text>
          </g>
        );
      })}
    </svg>
  );
}
