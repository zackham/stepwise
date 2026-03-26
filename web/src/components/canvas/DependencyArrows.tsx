import { memo } from "react";
import type { CardEdge } from "./CanvasLayout";

interface DependencyArrowsProps {
  edges: CardEdge[];
  width: number;
  height: number;
}

export const DependencyArrows = memo(function DependencyArrows({
  edges,
  width,
  height,
}: DependencyArrowsProps) {
  if (edges.length === 0) return null;

  return (
    <svg
      width={width}
      height={height}
      className="absolute inset-0 pointer-events-none"
      style={{ overflow: "visible" }}
    >
      <defs>
        <marker
          id="canvas-arrow"
          viewBox="0 0 10 6"
          refX="10"
          refY="3"
          markerWidth="8"
          markerHeight="6"
          orient="auto"
        >
          <path d="M 0 0 L 10 3 L 0 6 z" fill="#52525b" />
        </marker>
      </defs>
      {edges.map((edge) => {
        const dx = edge.toPos.x - edge.fromPos.x;
        const cpOffset = Math.max(Math.abs(dx) * 0.4, 40);
        const path = `M ${edge.fromPos.x} ${edge.fromPos.y} C ${edge.fromPos.x + cpOffset} ${edge.fromPos.y}, ${edge.toPos.x - cpOffset} ${edge.toPos.y}, ${edge.toPos.x} ${edge.toPos.y}`;
        return (
          <path
            key={`${edge.from}->${edge.to}`}
            d={path}
            fill="none"
            stroke="#3f3f46"
            strokeWidth={1.5}
            strokeDasharray="6 4"
            markerEnd="url(#canvas-arrow)"
            opacity={0.6}
          />
        );
      })}
    </svg>
  );
});
