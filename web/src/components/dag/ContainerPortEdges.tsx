import type { ContainerPort, HierarchicalDagNode } from "@/lib/dag-layout";

interface ContainerPortEdgesProps {
  containerPorts: ContainerPort[];
  nodes: HierarchicalDagNode[];
  layoutWidth: number;
  layoutHeight: number;
}

export function ContainerPortEdges({
  containerPorts,
  nodes,
  layoutWidth,
  layoutHeight,
}: ContainerPortEdgesProps) {
  if (containerPorts.length === 0) return null;

  const nodeMap = new Map(nodes.map((n) => [n.id, n]));

  return (
    <svg
      className="absolute top-0 left-0 pointer-events-none"
      width={layoutWidth}
      height={layoutHeight}
      style={{ overflow: "visible" }}
    >
      {containerPorts.map((port) => {
        const node = nodeMap.get(port.stepName);
        if (!node) return null;

        const stepCx = node.x + node.width / 2;
        const containerCx = layoutWidth / 2;
        const label = port.labels.join(", ");

        if (port.type === "input") {
          // Line from top of layout area down to top of step
          const startY = -4;
          const endY = node.y;
          const midY = (startY + endY) / 2;
          const path = `M ${containerCx} ${startY} C ${containerCx} ${midY}, ${stepCx} ${midY}, ${stepCx} ${endY}`;

          return (
            <g key={`input-${port.stepName}`}>
              <path
                d={path}
                fill="none"
                stroke="rgb(168 85 247 / 0.3)"
                strokeWidth={1.5}
                strokeDasharray="4 3"
              />
              {/* Label at midpoint */}
              <text
                x={(containerCx + stepCx) / 2}
                y={midY - 4}
                textAnchor="middle"
                className="fill-zinc-500"
                fontSize={9}
                fontFamily="ui-monospace, monospace"
              >
                {label}
              </text>
            </g>
          );
        } else {
          // Line from bottom of step down to bottom of layout area
          const startY = node.y + node.height;
          const endY = layoutHeight + 4;
          const midY = (startY + endY) / 2;
          const path = `M ${stepCx} ${startY} C ${stepCx} ${midY}, ${containerCx} ${midY}, ${containerCx} ${endY}`;

          return (
            <g key={`output-${port.stepName}`}>
              <path
                d={path}
                fill="none"
                stroke="rgb(168 85 247 / 0.3)"
                strokeWidth={1.5}
                strokeDasharray="4 3"
              />
              <text
                x={(containerCx + stepCx) / 2}
                y={midY - 4}
                textAnchor="middle"
                className="fill-zinc-500"
                fontSize={9}
                fontFamily="ui-monospace, monospace"
              >
                {label}
              </text>
            </g>
          );
        }
      })}
    </svg>
  );
}
