import type { DagEdge, LoopEdge } from "@/lib/dag-layout";

interface DagEdgesProps {
  edges: DagEdge[];
  loopEdges: LoopEdge[];
  width: number;
  height: number;
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

export function DagEdges({ edges, loopEdges, width, height }: DagEdgesProps) {
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
        const label = edge.labels.length > 0 ? edge.labels.join(", ") : null;
        const isSequencingOnly = edge.labels.length === 0;
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
            {label && (
              <text
                x={mid.x}
                y={mid.y - 6}
                textAnchor="middle"
                className="fill-zinc-500 text-[10px]"
                style={{ fontFamily: "monospace" }}
              >
                {label}
              </text>
            )}
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
