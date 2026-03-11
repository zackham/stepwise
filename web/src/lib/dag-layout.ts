import dagre from "dagre";
import type { WorkflowDefinition, JobTreeNode, StepRun } from "./types";

export interface DagNode {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface DagEdge {
  from: string;
  to: string;
  points: Array<{ x: number; y: number }>;
  labels: string[]; // data field names flowing through this edge
}

export interface LoopEdge {
  from: string;
  to: string;
  label: string;
  path: string; // SVG path data
  labelPos: { x: number; y: number };
}

export interface DagLayout {
  nodes: DagNode[];
  edges: DagEdge[];
  loopEdges: LoopEdge[];
  width: number;
  height: number;
}

// Hierarchical layout types for expand-in-place sub-jobs
export interface HierarchicalDagNode extends DagNode {
  isExpanded: boolean;
  childLayout: HierarchicalDagLayout | null;
  childJobId: string | null;
  childStepCount: number;
  containerPadding: { top: number; left: number; right: number; bottom: number };
}

export interface HierarchicalDagLayout extends DagLayout {
  nodes: HierarchicalDagNode[];
}

export interface ExpandedNodeData {
  subTree: JobTreeNode;
}

export const NODE_WIDTH = 240;
export const NODE_HEIGHT = 88;
const CONTAINER_HEADER = 44;
const CONTAINER_PAD_X = 24;
const CONTAINER_PAD_BOTTOM = 16;

export function computeDagLayout(workflow: WorkflowDefinition): DagLayout {
  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: "TB",
    nodesep: 60,
    ranksep: 80,
    marginx: 40,
    marginy: 40,
  });
  g.setDefaultEdgeLabel(() => ({}));

  const stepNames = Object.keys(workflow.steps);

  // Add nodes
  for (const name of stepNames) {
    g.setNode(name, { width: NODE_WIDTH, height: NODE_HEIGHT });
  }

  // Add edges from input bindings and sequencing
  const edgeSet = new Set<string>();
  const edgeLabels: Record<string, string[]> = {};
  for (const [name, step] of Object.entries(workflow.steps)) {
    for (const binding of step.inputs) {
      if (binding.source_step !== "$job") {
        const key = `${binding.source_step}->${name}`;
        if (!edgeSet.has(key)) {
          g.setEdge(binding.source_step, name);
          edgeSet.add(key);
          edgeLabels[key] = [];
        }
        edgeLabels[key].push(binding.source_field);
      }
    }
    for (const seq of step.sequencing) {
      const key = `${seq}->${name}`;
      if (!edgeSet.has(key)) {
        g.setEdge(seq, name);
        edgeSet.add(key);
        edgeLabels[key] = [];
      }
    }
  }

  dagre.layout(g);

  const nodes: DagNode[] = [];
  for (const name of stepNames) {
    const node = g.node(name);
    if (node) {
      nodes.push({
        id: name,
        x: node.x - NODE_WIDTH / 2,
        y: node.y - NODE_HEIGHT / 2,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
      });
    }
  }

  const edges: DagEdge[] = [];
  for (const e of g.edges()) {
    const edge = g.edge(e);
    if (edge && edge.points) {
      const key = `${e.v}->${e.w}`;
      edges.push({
        from: e.v,
        to: e.w,
        points: edge.points.map((p: { x: number; y: number }) => ({
          x: p.x,
          y: p.y,
        })),
        labels: edgeLabels[key] ?? [],
      });
    }
  }

  // Compute loop-back edges from exit rules
  const nodeMap: Record<string, DagNode> = {};
  for (const n of nodes) nodeMap[n.id] = n;

  const loopEdges: LoopEdge[] = [];
  for (const [name, step] of Object.entries(workflow.steps)) {
    for (const rule of step.exit_rules) {
      if (rule.config.action !== "loop") continue;
      const target = rule.config.target as string | undefined;
      if (!target || !nodeMap[name] || !nodeMap[target]) continue;

      const fromNode = nodeMap[name];
      const toNode = nodeMap[target];
      const offset = 50; // how far right the curve extends

      // Start from right side of `from` node, curve right and up to right side of `to` node
      const startX = fromNode.x + fromNode.width;
      const startY = fromNode.y + fromNode.height / 2;
      const endX = toNode.x + toNode.width;
      const endY = toNode.y + toNode.height / 2;
      const midX = Math.max(startX, endX) + offset;
      const midY = (startY + endY) / 2;

      const path = `M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}`;

      loopEdges.push({
        from: name,
        to: target,
        label: rule.name,
        path,
        labelPos: { x: midX + 4, y: midY },
      });
    }
  }

  const graphMeta = g.graph();
  const loopExtraWidth = loopEdges.length > 0 ? 120 : 0;
  return {
    nodes,
    edges,
    loopEdges,
    width: (graphMeta?.width ?? 600) + 80 + loopExtraWidth,
    height: (graphMeta?.height ?? 400) + 80,
  };
}

// ── Hierarchical layout (expand-in-place sub-jobs) ─────────────────

/**
 * Build a map of step_name -> sub-job tree node by matching
 * step runs with sub_job_id to their corresponding sub-tree entries.
 */
function buildSubJobMap(
  runs: StepRun[],
  subJobs: JobTreeNode[],
): Map<string, JobTreeNode> {
  const map = new Map<string, JobTreeNode>();
  // Build run_id -> sub_job lookup
  const subJobByParentRunId = new Map<string, JobTreeNode>();
  for (const sj of subJobs) {
    if (sj.job.parent_step_run_id) {
      subJobByParentRunId.set(sj.job.parent_step_run_id, sj);
    }
  }
  // Find latest run per step that has a sub_job_id
  const latestByStep = new Map<string, StepRun>();
  for (const run of runs) {
    if (!run.sub_job_id) continue;
    const existing = latestByStep.get(run.step_name);
    if (!existing || run.attempt > existing.attempt) {
      latestByStep.set(run.step_name, run);
    }
  }
  for (const [stepName, run] of latestByStep) {
    const subTree = subJobByParentRunId.get(run.id);
    if (subTree) {
      map.set(stepName, subTree);
    }
  }
  return map;
}

/**
 * Compute a hierarchical DAG layout that supports expand-in-place sub-jobs.
 * Recursive: child layouts are computed first (bottom-up), then the parent
 * layout uses inflated node dimensions for expanded nodes.
 */
export function computeHierarchicalLayout(
  workflow: WorkflowDefinition,
  expandedSteps: Set<string>,
  jobTree: JobTreeNode | null,
  depth: number = 0,
): HierarchicalDagLayout {
  // Build sub-job map from tree data
  const subJobMap = jobTree
    ? buildSubJobMap(jobTree.runs, jobTree.sub_jobs)
    : new Map<string, JobTreeNode>();

  // Pass 1: compute child layouts for expanded nodes (bottom-up)
  const childLayouts = new Map<string, HierarchicalDagLayout>();
  for (const stepName of expandedSteps) {
    const subTree = subJobMap.get(stepName);
    if (!subTree) continue;

    // Build child expanded steps (filter to those scoped under this sub-job)
    const childExpandedSteps = new Set<string>();
    for (const key of expandedSteps) {
      // Child steps are tracked with their own step names in the child's scope
      // We pass all expanded steps through — the child will only match its own steps
      childExpandedSteps.add(key);
    }

    const childLayout = computeHierarchicalLayout(
      subTree.job.workflow,
      childExpandedSteps,
      subTree,
      depth + 1,
    );
    childLayouts.set(stepName, childLayout);
  }

  // Pass 2: dagre layout with inflated dimensions for expanded nodes
  // Tighter spacing for nested layouts
  const isChild = depth > 0;
  const g = new dagre.graphlib.Graph();
  g.setGraph({
    rankdir: "TB",
    nodesep: isChild ? 30 : 60,
    ranksep: isChild ? 40 : 80,
    marginx: isChild ? 16 : 40,
    marginy: isChild ? 12 : 40,
  });
  g.setDefaultEdgeLabel(() => ({}));

  const stepNames = Object.keys(workflow.steps);
  const nodeSizes = new Map<string, { width: number; height: number }>();

  for (const name of stepNames) {
    const childLayout = childLayouts.get(name);
    if (childLayout) {
      // Expanded: inflate to contain child DAG
      const w = childLayout.width + CONTAINER_PAD_X * 2;
      const h = childLayout.height + CONTAINER_HEADER + CONTAINER_PAD_BOTTOM;
      nodeSizes.set(name, { width: Math.max(w, NODE_WIDTH), height: h });
    } else {
      nodeSizes.set(name, { width: NODE_WIDTH, height: NODE_HEIGHT });
    }
    const size = nodeSizes.get(name)!;
    g.setNode(name, { width: size.width, height: size.height });
  }

  // Add edges (same logic as flat layout)
  const edgeSet = new Set<string>();
  const edgeLabels: Record<string, string[]> = {};
  for (const [name, step] of Object.entries(workflow.steps)) {
    for (const binding of step.inputs) {
      if (binding.source_step !== "$job") {
        const key = `${binding.source_step}->${name}`;
        if (!edgeSet.has(key)) {
          g.setEdge(binding.source_step, name);
          edgeSet.add(key);
          edgeLabels[key] = [];
        }
        edgeLabels[key].push(binding.source_field);
      }
    }
    for (const seq of step.sequencing) {
      const key = `${seq}->${name}`;
      if (!edgeSet.has(key)) {
        g.setEdge(seq, name);
        edgeSet.add(key);
        edgeLabels[key] = [];
      }
    }
  }

  dagre.layout(g);

  // Build nodes with hierarchical info
  const nodes: HierarchicalDagNode[] = [];
  for (const name of stepNames) {
    const node = g.node(name);
    if (!node) continue;
    const size = nodeSizes.get(name)!;
    const childLayout = childLayouts.get(name) ?? null;
    const subTree = subJobMap.get(name);
    const childStepCount = subTree
      ? Object.keys(subTree.job.workflow.steps).length
      : 0;

    nodes.push({
      id: name,
      x: node.x - size.width / 2,
      y: node.y - size.height / 2,
      width: size.width,
      height: size.height,
      isExpanded: childLayout !== null,
      childLayout,
      childJobId: subTree?.job.id ?? null,
      childStepCount,
      containerPadding: {
        top: CONTAINER_HEADER,
        left: CONTAINER_PAD_X,
        right: CONTAINER_PAD_X,
        bottom: CONTAINER_PAD_BOTTOM,
      },
    });
  }

  // Build edges
  const edges: DagEdge[] = [];
  for (const e of g.edges()) {
    const edge = g.edge(e);
    if (edge && edge.points) {
      const key = `${e.v}->${e.w}`;
      edges.push({
        from: e.v,
        to: e.w,
        points: edge.points.map((p: { x: number; y: number }) => ({
          x: p.x,
          y: p.y,
        })),
        labels: edgeLabels[key] ?? [],
      });
    }
  }

  // Loop-back edges
  const nodeMap: Record<string, HierarchicalDagNode> = {};
  for (const n of nodes) nodeMap[n.id] = n;

  const loopEdges: LoopEdge[] = [];
  for (const [name, step] of Object.entries(workflow.steps)) {
    for (const rule of step.exit_rules) {
      if (rule.config.action !== "loop") continue;
      const target = rule.config.target as string | undefined;
      if (!target || !nodeMap[name] || !nodeMap[target]) continue;

      const fromNode = nodeMap[name];
      const toNode = nodeMap[target];
      const offset = 50;

      const startX = fromNode.x + fromNode.width;
      const startY = fromNode.y + fromNode.height / 2;
      const endX = toNode.x + toNode.width;
      const endY = toNode.y + toNode.height / 2;
      const midX = Math.max(startX, endX) + offset;
      const midY = (startY + endY) / 2;

      const path = `M ${startX} ${startY} C ${midX} ${startY}, ${midX} ${endY}, ${endX} ${endY}`;

      loopEdges.push({
        from: name,
        to: target,
        label: rule.name,
        path,
        labelPos: { x: midX + 4, y: midY },
      });
    }
  }

  const graphMeta = g.graph();
  const loopExtraWidth = loopEdges.length > 0 ? 120 : 0;
  const outerPad = isChild ? 0 : 80;
  return {
    nodes,
    edges,
    loopEdges,
    width: (graphMeta?.width ?? 600) + outerPad + loopExtraWidth,
    height: (graphMeta?.height ?? 400) + outerPad,
  };
}
