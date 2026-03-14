import dagre from "dagre";
import type { FlowDefinition, JobTreeNode, StepRun } from "./types";

// ── Selection model ─────────────────────────────────────────────────
export type DagSelection =
  | { kind: "step"; stepName: string }
  | { kind: "edge-field"; fromStep: string; toStep: string; fieldName: string }
  | { kind: "flow-input"; fieldName: string }
  | { kind: "flow-output"; stepName: string; fieldName: string }
  | null;

// ── Layout types ────────────────────────────────────────────────────
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
export interface ForEachInstance {
  index: number;
  jobId: string;
  status: string | null;
  layout: HierarchicalDagLayout;
}

export interface HierarchicalDagNode extends DagNode {
  isExpanded: boolean;
  hasSubFlow: boolean;
  childLayout: HierarchicalDagLayout | null;
  childJobId: string | null;
  childStepCount: number;
  containerPadding: { top: number; left: number; right: number; bottom: number };
  isForEach: boolean;
  forEachChildren: ForEachInstance[] | null;
}

export interface FlowPortNode {
  id: string; // "__flow_input__" or "__flow_output__"
  x: number;
  y: number;
  width: number;
  height: number;
  labels: string[]; // field names
  type: "input" | "output";
  /** For output ports, maps field name -> step that produces it */
  fieldSources?: Record<string, string>;
}

/** Edge from container boundary to an entry/terminal step (no box, just a line + label) */
export interface ContainerPort {
  stepName: string;
  labels: string[];
  type: "input" | "output";
}

export interface HierarchicalDagLayout extends DagLayout {
  nodes: HierarchicalDagNode[];
  flowPorts: FlowPortNode[];
  containerPorts: ContainerPort[];
}

export interface ExpandedNodeData {
  subTree: JobTreeNode;
}

export const NODE_WIDTH = 240;
export const NODE_HEIGHT = 88;
export const NODE_HEIGHT_WITH_DESC = 108;
const CONTAINER_HEADER = 44;
const CONTAINER_PAD_X = 24;
const CONTAINER_PAD_BOTTOM = 16;
const FLOW_PORT_WIDTH = 160;
const FLOW_PORT_HEIGHT = 40;
const FOR_EACH_INSTANCE_HEADER = 28;
const FOR_EACH_INSTANCE_GAP = 8;

export function nodeHeight(workflow: FlowDefinition, stepName: string): number {
  const step = workflow.steps[stepName];
  return step?.description ? NODE_HEIGHT_WITH_DESC : NODE_HEIGHT;
}

export function computeDagLayout(workflow: FlowDefinition): DagLayout {
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

  // Add nodes (variable height based on description)
  const nodeHeights: Record<string, number> = {};
  for (const name of stepNames) {
    const h = nodeHeight(workflow, name);
    nodeHeights[name] = h;
    g.setNode(name, { width: NODE_WIDTH, height: h });
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
      const h = nodeHeights[name];
      nodes.push({
        id: name,
        x: node.x - NODE_WIDTH / 2,
        y: node.y - h / 2,
        width: NODE_WIDTH,
        height: h,
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
 * Build a map of step_name -> sub-job tree node(s) by matching
 * step runs with sub_job_id (or for_each sub_job_ids) to their
 * corresponding sub-tree entries.
 *
 * Returns arrays: length 1 for standard sub-jobs, length N for for_each.
 */
function buildSubJobMap(
  runs: StepRun[],
  subJobs: JobTreeNode[],
): Map<string, JobTreeNode[]> {
  const map = new Map<string, JobTreeNode[]>();
  // Build lookup: sub_job.id -> sub-tree node
  const subJobById = new Map<string, JobTreeNode>();
  // Build lookup: parent_step_run_id -> sub-tree nodes
  const subJobsByParentRunId = new Map<string, JobTreeNode[]>();
  for (const sj of subJobs) {
    subJobById.set(sj.job.id, sj);
    if (sj.job.parent_step_run_id) {
      const list = subJobsByParentRunId.get(sj.job.parent_step_run_id) ?? [];
      list.push(sj);
      subJobsByParentRunId.set(sj.job.parent_step_run_id, list);
    }
  }

  // Find latest run per step (prefer for_each runs, then sub_job_id runs)
  const latestByStep = new Map<string, StepRun>();
  for (const run of runs) {
    const isForEach = run.executor_state?.for_each === true;
    const hasSub = !!run.sub_job_id;
    if (!isForEach && !hasSub) continue;
    const existing = latestByStep.get(run.step_name);
    if (!existing || run.attempt > existing.attempt) {
      latestByStep.set(run.step_name, run);
    }
  }

  for (const [stepName, run] of latestByStep) {
    const isForEach = run.executor_state?.for_each === true;
    if (isForEach) {
      // for_each: gather sub-jobs by their IDs stored in executor_state
      const subJobIds = (run.executor_state?.sub_job_ids as string[]) ?? [];
      const nodes: JobTreeNode[] = [];
      for (const id of subJobIds) {
        const node = subJobById.get(id);
        if (node) nodes.push(node);
      }
      if (nodes.length > 0) {
        map.set(stepName, nodes);
      }
    } else {
      // Standard single sub-job
      const subTree = subJobsByParentRunId.get(run.id);
      if (subTree && subTree.length > 0) {
        map.set(stepName, [subTree[0]]);
      }
    }
  }
  return map;
}

/**
 * Compute a hierarchical DAG layout that supports expand-in-place sub-jobs.
 * Recursive: child layouts are computed first (bottom-up), then the parent
 * layout uses inflated node dimensions for expanded nodes.
 *
 * Sub-flow structure comes from two sources (in priority order):
 * 1. JobTreeNode data (runtime — has actual sub-job runs)
 * 2. StepDefinition.sub_flow (design-time — baked workflow definition)
 */
export function computeHierarchicalLayout(
  workflow: FlowDefinition,
  expandedSteps: Set<string>,
  jobTree: JobTreeNode | null,
  depth: number = 0,
): HierarchicalDagLayout {
  // Build sub-job map from tree data (arrays: length 1 for standard, N for for_each)
  const subJobMap = jobTree
    ? buildSubJobMap(jobTree.runs, jobTree.sub_jobs)
    : new Map<string, JobTreeNode[]>();

  // Build a map of step_name -> sub_flow from step definitions (design-time fallback)
  const subFlowDefs = new Map<string, FlowDefinition>();
  for (const [name, step] of Object.entries(workflow.steps)) {
    if (step.sub_flow && !subJobMap.has(name)) {
      subFlowDefs.set(name, step.sub_flow);
    }
  }

  // Pass 1: compute child layouts for expanded nodes (bottom-up)
  const childLayouts = new Map<string, HierarchicalDagLayout>();
  // For for_each nodes, store per-instance layouts
  const forEachLayouts = new Map<string, ForEachInstance[]>();

  for (const stepName of expandedSteps) {
    const subTrees = subJobMap.get(stepName);
    const subFlowDef = subFlowDefs.get(stepName);
    if (!subTrees && !subFlowDef) continue;

    // Build child expanded steps — pass all through, child will only match its own
    const childExpandedSteps = new Set(expandedSteps);

    if (subTrees && subTrees.length > 1) {
      // for_each: compute a layout for each instance
      const instances: ForEachInstance[] = [];
      for (let i = 0; i < subTrees.length; i++) {
        const tree = subTrees[i];
        const instanceLayout = computeHierarchicalLayout(
          tree.job.workflow,
          childExpandedSteps,
          tree,
          depth + 1,
        );
        instances.push({
          index: i,
          jobId: tree.job.id,
          status: tree.job.status,
          layout: instanceLayout,
        });
      }
      forEachLayouts.set(stepName, instances);
    } else {
      // Standard single sub-job or design-time
      const firstTree = subTrees?.[0] ?? null;
      const childWorkflow = firstTree ? firstTree.job.workflow : subFlowDef!;
      const childLayout = computeHierarchicalLayout(
        childWorkflow,
        childExpandedSteps,
        firstTree,
        depth + 1,
      );
      childLayouts.set(stepName, childLayout);
    }
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
    const feInstances = forEachLayouts.get(name);
    if (feInstances) {
      // for_each expanded: laid out horizontally — width = sum, height = max
      const totalW = feInstances.reduce(
        (sum, inst, idx) => sum + inst.layout.width + (idx > 0 ? FOR_EACH_INSTANCE_GAP : 0),
        0,
      );
      const maxH = Math.max(...feInstances.map((i) => FOR_EACH_INSTANCE_HEADER + i.layout.height));
      const w = totalW + CONTAINER_PAD_X * 2;
      const h = maxH + CONTAINER_HEADER + CONTAINER_PAD_BOTTOM;
      nodeSizes.set(name, { width: Math.max(w, NODE_WIDTH), height: h });
    } else if (childLayout) {
      // Standard expanded: inflate to contain child DAG
      const w = childLayout.width + CONTAINER_PAD_X * 2;
      const h = childLayout.height + CONTAINER_HEADER + CONTAINER_PAD_BOTTOM;
      nodeSizes.set(name, { width: Math.max(w, NODE_WIDTH), height: h });
    } else {
      nodeSizes.set(name, { width: NODE_WIDTH, height: nodeHeight(workflow, name) });
    }
    const size = nodeSizes.get(name)!;
    g.setNode(name, { width: size.width, height: size.height });
  }

  // Add edges (same logic as flat layout)
  const edgeSet = new Set<string>();
  const edgeLabels: Record<string, string[]> = {};
  // Track which steps consume $job inputs (for flow input port)
  const jobInputConsumers = new Map<string, Set<string>>(); // stepName -> set of source_field
  for (const [name, step] of Object.entries(workflow.steps)) {
    for (const binding of step.inputs) {
      if (binding.source_step === "$job") {
        if (!jobInputConsumers.has(name)) jobInputConsumers.set(name, new Set());
        jobInputConsumers.get(name)!.add(binding.source_field);
      } else {
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

  // Compute entry/terminal step info (used for flow ports at depth 0 and container ports at depth > 0)
  const FLOW_INPUT_ID = "__flow_input__";
  const FLOW_OUTPUT_ID = "__flow_output__";
  let hasFlowInput = false;
  let hasFlowOutput = false;
  const allJobInputFields = new Set<string>();
  const terminalStepOutputs = new Map<string, string[]>();

  for (const fields of jobInputConsumers.values()) {
    for (const f of fields) allJobInputFields.add(f);
  }

  const referencedAsSource = new Set<string>();
  for (const step of Object.values(workflow.steps)) {
    for (const binding of step.inputs) {
      if (binding.source_step !== "$job") referencedAsSource.add(binding.source_step);
    }
    for (const seq of step.sequencing) referencedAsSource.add(seq);
  }
  for (const name of stepNames) {
    if (!referencedAsSource.has(name)) {
      const step = workflow.steps[name];
      if (step.outputs.length > 0) {
        terminalStepOutputs.set(name, step.outputs);
      }
    }
  }

  // Flow port dagre nodes (top-level only — boxes with edges)
  if (depth === 0) {
    if (allJobInputFields.size > 0) {
      hasFlowInput = true;
      g.setNode(FLOW_INPUT_ID, { width: FLOW_PORT_WIDTH, height: FLOW_PORT_HEIGHT });
      for (const [stepName, fields] of jobInputConsumers) {
        const key = `${FLOW_INPUT_ID}->${stepName}`;
        g.setEdge(FLOW_INPUT_ID, stepName);
        edgeSet.add(key);
        edgeLabels[key] = [...fields];
      }
    }
    if (terminalStepOutputs.size > 0) {
      hasFlowOutput = true;
      g.setNode(FLOW_OUTPUT_ID, { width: FLOW_PORT_WIDTH, height: FLOW_PORT_HEIGHT });
      for (const [stepName, outputs] of terminalStepOutputs) {
        const key = `${stepName}->${FLOW_OUTPUT_ID}`;
        g.setEdge(stepName, FLOW_OUTPUT_ID);
        edgeSet.add(key);
        edgeLabels[key] = outputs;
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
    const feInstances = forEachLayouts.get(name) ?? null;
    const subTrees = subJobMap.get(name);
    const subFlowDef = subFlowDefs.get(name);
    const hasSubFlow = !!(subTrees || subFlowDef);
    const isForEach = (subTrees && subTrees.length > 1) || false;
    const childStepCount = subTrees
      ? Object.keys(subTrees[0].job.workflow.steps).length
      : subFlowDef
        ? Object.keys(subFlowDef.steps).length
        : 0;

    nodes.push({
      id: name,
      x: node.x - size.width / 2,
      y: node.y - size.height / 2,
      width: size.width,
      height: size.height,
      isExpanded: childLayout !== null || feInstances !== null,
      hasSubFlow,
      childLayout,
      childJobId: subTrees?.[0]?.job.id ?? null,
      childStepCount,
      containerPadding: {
        top: CONTAINER_HEADER,
        left: CONTAINER_PAD_X,
        right: CONTAINER_PAD_X,
        bottom: CONTAINER_PAD_BOTTOM,
      },
      isForEach,
      forEachChildren: feInstances,
    });
  }

  // Extract flow port nodes
  const flowPorts: FlowPortNode[] = [];
  if (hasFlowInput) {
    const node = g.node(FLOW_INPUT_ID);
    if (node) {
      flowPorts.push({
        id: FLOW_INPUT_ID,
        x: node.x - FLOW_PORT_WIDTH / 2,
        y: node.y - FLOW_PORT_HEIGHT / 2,
        width: FLOW_PORT_WIDTH,
        height: FLOW_PORT_HEIGHT,
        labels: [...allJobInputFields],
        type: "input",
      });
    }
  }
  if (hasFlowOutput) {
    const node = g.node(FLOW_OUTPUT_ID);
    if (node) {
      // Build field -> source step mapping
      const fieldSources: Record<string, string> = {};
      for (const [stepName, outputs] of terminalStepOutputs) {
        for (const field of outputs) {
          fieldSources[field] = stepName;
        }
      }
      flowPorts.push({
        id: FLOW_OUTPUT_ID,
        x: node.x - FLOW_PORT_WIDTH / 2,
        y: node.y - FLOW_PORT_HEIGHT / 2,
        width: FLOW_PORT_WIDTH,
        height: FLOW_PORT_HEIGHT,
        labels: Object.keys(fieldSources),
        type: "output",
        fieldSources,
      });
    }
  }

  // Build edges (skip synthetic port node edges — they're in flowPorts)
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

  // Container ports: lightweight edge-line metadata for child layouts (depth > 0)
  const containerPorts: ContainerPort[] = [];
  if (depth > 0) {
    for (const [stepName, fields] of jobInputConsumers) {
      containerPorts.push({ stepName, labels: [...fields], type: "input" });
    }
    for (const [stepName, outputs] of terminalStepOutputs) {
      containerPorts.push({ stepName, labels: outputs, type: "output" });
    }
  }

  const graphMeta = g.graph();
  const loopExtraWidth = loopEdges.length > 0 ? 120 : 0;
  const outerPad = isChild ? 0 : 80;
  return {
    nodes,
    edges,
    loopEdges,
    flowPorts,
    containerPorts,
    width: (graphMeta?.width ?? 600) + outerPad + loopExtraWidth,
    height: (graphMeta?.height ?? 400) + outerPad,
  };
}
