import type { DagEdge, LoopEdge } from "@/lib/dag-layout";

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

export function DagEdges({ edges, loopEdges, width, height, onClickLabel, selectedLabel }: DagEdgesProps) {
  return (
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
              // Estimate text width (~6px per char at 10px mono)
              const textW = field.length * 6.5 + 12;
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
                        : onClickLabel
                          ? "fill-zinc-500 text-[10px] hover:fill-zinc-300"
                          : "fill-zinc-500 text-[10px]"
                    }
                    style={{ fontFamily: "monospace" }}
                  >
                    {field}
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
  );
}
