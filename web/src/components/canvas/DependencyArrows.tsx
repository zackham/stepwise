import { memo } from "react";
import type { CardEdge } from "./CanvasLayout";
import { useTheme } from "@/hooks/useTheme";

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
  const theme = useTheme();
  const isDark = theme === "dark";
  const satisfiedColor = isDark ? "#3f3f46" : "#a1a1aa";

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
          id="canvas-arrow-satisfied"
          viewBox="0 0 10 6"
          refX="10"
          refY="3"
          markerWidth="8"
          markerHeight="6"
          orient="auto"
        >
          <path d="M 0 0 L 10 3 L 0 6 z" fill={satisfiedColor} fillOpacity={0.5} />
        </marker>
        <marker
          id="canvas-arrow-blocking"
          viewBox="0 0 10 6"
          refX="10"
          refY="3"
          markerWidth="8"
          markerHeight="6"
          orient="auto"
        >
          <path d="M 0 0 L 10 3 L 0 6 z" fill="#f59e0b" fillOpacity={0.7} />
        </marker>
      </defs>
      {edges.map((edge) => {
        const dx = edge.toPos.x - edge.fromPos.x;
        const cpOffset = Math.max(Math.abs(dx) * 0.4, 40);
        const path = `M ${edge.fromPos.x} ${edge.fromPos.y} C ${edge.fromPos.x + cpOffset} ${edge.fromPos.y}, ${edge.toPos.x - cpOffset} ${edge.toPos.y}, ${edge.toPos.x} ${edge.toPos.y}`;
        const isSatisfied = edge.satisfied;
        return (
          <path
            key={`${edge.from}->${edge.to}`}
            d={path}
            fill="none"
            stroke={isSatisfied ? satisfiedColor : "#f59e0b"}
            strokeWidth={isSatisfied ? 1.5 : 2}
            strokeDasharray={isSatisfied ? "6 4" : undefined}
            markerEnd={`url(#canvas-arrow-${isSatisfied ? "satisfied" : "blocking"})`}
            opacity={isSatisfied ? 0.4 : 0.6}
          />
        );
      })}
    </svg>
  );
});
