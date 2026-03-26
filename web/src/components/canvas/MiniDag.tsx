import { useMemo, memo } from "react";
import dagre from "dagre";
import type { FlowDefinition, StepRun, StepRunStatus } from "@/lib/types";

interface MiniDagProps {
  workflow: FlowDefinition;
  runs: StepRun[];
  width: number;
  height: number;
}

interface MiniNode {
  id: string;
  x: number;
  y: number;
  status: StepRunStatus | "pending";
}

interface MiniEdge {
  from: { x: number; y: number };
  to: { x: number; y: number };
  isLoop: boolean;
}

const NODE_RADIUS = 5;

function statusColor(status: StepRunStatus | "pending"): string {
  switch (status) {
    case "completed":
      return "#34d399"; // emerald-400
    case "running":
      return "#60a5fa"; // blue-400
    case "failed":
      return "#f87171"; // red-400
    case "suspended":
      return "#fbbf24"; // amber-400
    case "delegated":
      return "#a78bfa"; // purple-400
    case "skipped":
    case "cancelled":
      return "#71717a"; // zinc-500
    default:
      return "#52525b"; // zinc-600
  }
}

function computeMiniLayout(
  workflow: FlowDefinition,
  runs: StepRun[],
  width: number,
  height: number,
): { nodes: MiniNode[]; edges: MiniEdge[] } {
  if (!workflow?.steps) return { nodes: [], edges: [] };
  const stepNames = Object.keys(workflow.steps);
  if (stepNames.length === 0) return { nodes: [], edges: [] };

  // Build latest run status map
  const statusMap = new Map<string, StepRunStatus>();
  for (const run of runs) {
    const existing = statusMap.get(run.step_name);
    if (!existing || run.attempt > (runs.find((r) => r.step_name === run.step_name && r.status === existing)?.attempt ?? 0)) {
      statusMap.set(run.step_name, run.status);
    }
  }

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 16, ranksep: 20, marginx: 12, marginy: 12 });
  g.setDefaultEdgeLabel(() => ({}));

  for (const name of stepNames) {
    g.setNode(name, { width: NODE_RADIUS * 2, height: NODE_RADIUS * 2 });
  }

  // Track loop targets for edge styling
  const loopTargets = new Set<string>();
  const edgeSet = new Set<string>();
  for (const [name, step] of Object.entries(workflow.steps)) {
    for (const binding of step.inputs) {
      if (binding.any_of_sources) {
        for (const src of binding.any_of_sources) {
          if (!src.step || src.step === "$job") continue;
          const key = `${src.step}->${name}`;
          if (!edgeSet.has(key)) {
            g.setEdge(src.step, name);
            edgeSet.add(key);
          }
        }
      } else if (binding.source_step && binding.source_step !== "$job") {
        const key = `${binding.source_step}->${name}`;
        if (!edgeSet.has(key)) {
          g.setEdge(binding.source_step, name);
          edgeSet.add(key);
        }
      }
    }
    for (const seq of step.after) {
      const key = `${seq}->${name}`;
      if (!edgeSet.has(key)) {
        g.setEdge(seq, name);
        edgeSet.add(key);
      }
    }
    // Collect loop targets
    for (const rule of step.exit_rules) {
      if (rule.config.action === "loop" && rule.config.target) {
        loopTargets.add(`${name}->${rule.config.target}`);
      }
    }
  }

  dagre.layout(g);

  const graphMeta = g.graph();
  const gw = (graphMeta?.width as number) || 100;
  const gh = (graphMeta?.height as number) || 100;

  // Scale to fit
  const pad = 4;
  const availW = width - pad * 2;
  const availH = height - pad * 2;
  const scale = Math.min(availW / gw, availH / gh, 1.5);
  const offsetX = pad + (availW - gw * scale) / 2;
  const offsetY = pad + (availH - gh * scale) / 2;

  const nodeMap = new Map<string, { x: number; y: number }>();
  const nodes: MiniNode[] = [];
  for (const name of stepNames) {
    const node = g.node(name);
    if (!node) continue;
    const x = node.x * scale + offsetX;
    const y = node.y * scale + offsetY;
    nodeMap.set(name, { x, y });
    nodes.push({ id: name, x, y, status: statusMap.get(name) ?? "pending" });
  }

  const edges: MiniEdge[] = [];
  for (const e of g.edges()) {
    const fromPos = nodeMap.get(e.v);
    const toPos = nodeMap.get(e.w);
    if (fromPos && toPos) {
      edges.push({
        from: fromPos,
        to: toPos,
        isLoop: loopTargets.has(`${e.w}->${e.v}`) || loopTargets.has(`${e.v}->${e.w}`),
      });
    }
  }

  // Add visual loop-back edges
  for (const key of loopTargets) {
    const [from, to] = key.split("->");
    const fromPos = nodeMap.get(from);
    const toPos = nodeMap.get(to);
    if (fromPos && toPos && fromPos.y >= toPos.y) {
      edges.push({ from: fromPos, to: toPos, isLoop: true });
    }
  }

  return { nodes, edges };
}

export const MiniDag = memo(function MiniDag({ workflow, runs, width, height }: MiniDagProps) {
  const { nodes, edges } = useMemo(
    () => computeMiniLayout(workflow, runs, width, height),
    [workflow, runs, width, height],
  );

  return (
    <svg width={width} height={height} className="block">
      {/* Edges */}
      {edges.map((edge, i) => {
        if (edge.isLoop && edge.from.y >= edge.to.y) {
          // Backward loop — draw curve on the right
          const offset = 12;
          const midX = Math.max(edge.from.x, edge.to.x) + offset;
          return (
            <path
              key={`e-${i}`}
              d={`M ${edge.from.x} ${edge.from.y} C ${midX} ${edge.from.y}, ${midX} ${edge.to.y}, ${edge.to.x} ${edge.to.y}`}
              fill="none"
              stroke="#3f3f46"
              strokeWidth={1}
              opacity={0.6}
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
            stroke="#3f3f46"
            strokeWidth={1}
            opacity={0.6}
          />
        );
      })}
      {/* Nodes */}
      {nodes.map((node) => {
        const color = statusColor(node.status);
        const isPending = node.status === "pending";
        const isRunning = node.status === "running";
        return (
          <g key={node.id}>
            {isRunning && (
              <circle cx={node.x} cy={node.y} r={NODE_RADIUS + 3} fill={color} opacity={0.3}>
                <animate attributeName="opacity" values="0.3;0.1;0.3" dur="1.5s" repeatCount="indefinite" />
              </circle>
            )}
            <circle
              cx={node.x}
              cy={node.y}
              r={NODE_RADIUS}
              fill={isPending ? "none" : color}
              stroke={isPending ? "#52525b" : color}
              strokeWidth={isPending ? 1.5 : 0}
            />
          </g>
        );
      })}
    </svg>
  );
});
