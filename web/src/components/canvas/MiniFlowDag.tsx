import { useMemo, memo } from "react";
import dagre from "dagre";
import type { FlowGraph } from "@/lib/types";
import { useTheme } from "@/hooks/useTheme";

interface MiniFlowDagProps {
  graph: FlowGraph;
  width: number;
  height: number;
}

const NODE_RADIUS = 5;

const EXECUTOR_COLORS: Record<string, string> = {
  agent: "#60a5fa",   // blue-400
  llm: "#a78bfa",     // violet-400
  script: "#34d399",  // emerald-400
  external: "#fbbf24", // amber-400
  poll: "#22d3ee",    // cyan-400
  mock_llm: "#a78bfa",
};

function executorColor(type: string, isDark: boolean): string {
  return EXECUTOR_COLORS[type] ?? (isDark ? "#52525b" : "#d4d4d8");
}

function computeLayout(graph: FlowGraph, width: number, height: number) {
  if (graph.nodes.length === 0) return { nodes: [] as { id: string; x: number; y: number; color: string }[], edges: [] as { from: { x: number; y: number }; to: { x: number; y: number }; isLoop: boolean }[] };

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 16, ranksep: 20, marginx: 12, marginy: 12 });
  g.setDefaultEdgeLabel(() => ({}));

  for (const node of graph.nodes) {
    g.setNode(node.id, { width: NODE_RADIUS * 2, height: NODE_RADIUS * 2 });
  }

  const edgeSet = new Set<string>();
  for (const edge of graph.edges) {
    const key = `${edge.source}->${edge.target}`;
    if (!edgeSet.has(key)) {
      g.setEdge(edge.source, edge.target);
      edgeSet.add(key);
    }
  }

  dagre.layout(g);

  const graphMeta = g.graph();
  const gw = (graphMeta?.width as number) || 100;
  const gh = (graphMeta?.height as number) || 100;

  const pad = 4;
  const availW = width - pad * 2;
  const availH = height - pad * 2;
  const scale = Math.min(availW / gw, availH / gh, 1.5);
  const offsetX = pad + (availW - gw * scale) / 2;
  const offsetY = pad + (availH - gh * scale) / 2;

  const isDark = document.documentElement.classList.contains("dark");
  const executorMap = new Map(graph.nodes.map((n) => [n.id, n.executor_type]));
  const nodeMap = new Map<string, { x: number; y: number }>();
  const nodes = graph.nodes.map((node) => {
    const dagreNode = g.node(node.id);
    if (!dagreNode) return null;
    const x = dagreNode.x * scale + offsetX;
    const y = dagreNode.y * scale + offsetY;
    nodeMap.set(node.id, { x, y });
    return { id: node.id, x, y, color: executorColor(executorMap.get(node.id) ?? "", isDark) };
  }).filter(Boolean) as { id: string; x: number; y: number; color: string }[];

  const loopEdges = new Set(graph.edges.filter((e) => e.is_loop).map((e) => `${e.source}->${e.target}`));
  const edges = g.edges().map((e) => {
    const from = nodeMap.get(e.v);
    const to = nodeMap.get(e.w);
    if (!from || !to) return null;
    return { from, to, isLoop: loopEdges.has(`${e.v}->${e.w}`) || loopEdges.has(`${e.w}->${e.v}`) };
  }).filter(Boolean) as { from: { x: number; y: number }; to: { x: number; y: number }; isLoop: boolean }[];

  return { nodes, edges };
}

export const MiniFlowDag = memo(function MiniFlowDag({ graph, width, height }: MiniFlowDagProps) {
  const theme = useTheme();
  const isDark = theme === "dark";
  const edgeStroke = isDark ? "#3f3f46" : "#d4d4d8";

  const layout = useMemo(() => computeLayout(graph, width, height), [graph, width, height]);

  return (
    <svg width={width} height={height} className="block">
      {layout.edges.map((edge, i) => {
        if (edge.isLoop && edge.from.y >= edge.to.y) {
          const offset = 12;
          const midX = Math.max(edge.from.x, edge.to.x) + offset;
          return (
            <path
              key={`e-${i}`}
              d={`M ${edge.from.x} ${edge.from.y} C ${midX} ${edge.from.y}, ${midX} ${edge.to.y}, ${edge.to.x} ${edge.to.y}`}
              fill="none"
              stroke={edgeStroke}
              strokeWidth={1}
              strokeDasharray="3,3"
              opacity={0.5}
            />
          );
        }
        return (
          <line
            key={`e-${i}`}
            x1={edge.from.x}
            y1={edge.from.y}
            x2={edge.to.x}
            y2={edge.to.y}
            stroke={edgeStroke}
            strokeWidth={1}
          />
        );
      })}
      {layout.nodes.map((node) => (
        <circle
          key={node.id}
          cx={node.x}
          cy={node.y}
          r={NODE_RADIUS}
          fill={node.color}
          opacity={0.8}
        />
      ))}
    </svg>
  );
});
