import { useRef, useState, useEffect } from "react";
import type { HierarchicalDagLayout, HierarchicalDagNode, FlowPortNode, LoopEdge } from "./dag-layout";
import type { DagEdge } from "./dag-layout";
import { computeLoopEdgePath } from "./dag-layout";

// Duration of layout transition in ms
const TRANSITION_MS = 300;

function lerp(a: number, b: number, t: number): number {
  // Guard against NaN/Infinity from dagre or missing values
  if (!isFinite(a)) return b;
  if (!isFinite(b)) return a;
  return a + (b - a) * t;
}

/** Smoothstep ease (matches dag-camera) */
function ease(t: number): number {
  const c = Math.max(0, Math.min(1, t));
  return c * c * (3 - 2 * c);
}

/** Interpolate a single node's spatial properties */
function lerpNode(
  prev: HierarchicalDagNode,
  next: HierarchicalDagNode,
  t: number,
): HierarchicalDagNode {
  return {
    ...next,
    x: lerp(prev.x, next.x, t),
    y: lerp(prev.y, next.y, t),
    width: lerp(prev.width, next.width, t),
    height: lerp(prev.height, next.height, t),
    // Recursively interpolate child layouts
    childLayout: next.childLayout && prev.childLayout
      ? lerpLayout(prev.childLayout, next.childLayout, t)
      : next.childLayout,
    // Interpolate forEach children layouts
    forEachChildren: next.forEachChildren
      ? next.forEachChildren.map((inst, i) => {
          const prevInst = prev.forEachChildren?.[i];
          if (!prevInst) return inst;
          return {
            ...inst,
            layout: lerpLayout(prevInst.layout, inst.layout, t),
          };
        })
      : next.forEachChildren,
  };
}

/** Interpolate an edge's control points */
function lerpEdge(prev: DagEdge, next: DagEdge, t: number): DagEdge {
  // Match points arrays by index — if lengths differ, use next's
  const points = next.points.map((np, i) => {
    const pp = prev.points[i];
    if (!pp) return np;
    return { x: lerp(pp.x, np.x, t), y: lerp(pp.y, np.y, t) };
  });
  return { ...next, points };
}

/** Interpolate a loop edge, recomputing path from interpolated node positions */
function lerpLoopEdge(
  prev: LoopEdge,
  next: LoopEdge,
  t: number,
  nodeMap: Map<string, HierarchicalDagNode>,
): LoopEdge {
  const fromNode = nodeMap.get(next.from);
  const toNode = nodeMap.get(next.to);

  if (!fromNode || !toNode) {
    // Fallback: interpolate label position, snap path
    return {
      ...next,
      labelPos: {
        x: lerp(prev.labelPos.x, next.labelPos.x, t),
        y: lerp(prev.labelPos.y, next.labelPos.y, t),
      },
    };
  }

  const { path, labelPos } = computeLoopEdgePath(fromNode, toNode, next.loopIndex);
  return { ...next, path, labelPos };
}

/** Interpolate a flow port node */
function lerpFlowPort(prev: FlowPortNode, next: FlowPortNode, t: number): FlowPortNode {
  return {
    ...next,
    x: lerp(prev.x, next.x, t),
    y: lerp(prev.y, next.y, t),
    width: lerp(prev.width, next.width, t),
    height: lerp(prev.height, next.height, t),
  };
}

/** Interpolate an entire layout tree */
function lerpLayout(
  prev: HierarchicalDagLayout,
  next: HierarchicalDagLayout,
  t: number,
): HierarchicalDagLayout {
  // Build lookup maps for matching by ID
  const prevNodeMap = new Map(prev.nodes.map(n => [n.id, n]));
  const prevEdgeMap = new Map(prev.edges.map(e => [`${e.from}->${e.to}`, e]));
  const prevLoopMap = new Map(prev.loopEdges.map(e => [`${e.from}->${e.to}`, e]));
  const prevPortMap = new Map(prev.flowPorts.map(p => [p.id, p]));

  const nodes = next.nodes.map(n => {
    const pn = prevNodeMap.get(n.id);
    return pn ? lerpNode(pn, n, t) : n;
  });

  const edges = next.edges.map(e => {
    const pe = prevEdgeMap.get(`${e.from}->${e.to}`);
    return pe ? lerpEdge(pe, e, t) : e;
  });

  // Build map from interpolated nodes for loop edge path recomputation
  const interpNodeMap = new Map(nodes.map(n => [n.id, n]));

  const loopEdges = next.loopEdges.map(e => {
    const pe = prevLoopMap.get(`${e.from}->${e.to}`);
    return pe ? lerpLoopEdge(pe, e, t, interpNodeMap) : e;
  });

  const flowPorts = next.flowPorts.map(p => {
    const pp = prevPortMap.get(p.id);
    return pp ? lerpFlowPort(pp, p, t) : p;
  });

  return {
    ...next,
    nodes,
    edges,
    loopEdges,
    flowPorts,
    width: lerp(prev.width, next.width, t),
    height: lerp(prev.height, next.height, t),
  };
}

/**
 * Hook that wraps a layout and smoothly interpolates between consecutive
 * layout values over TRANSITION_MS. Returns the interpolated layout.
 *
 * On first render, returns the layout immediately (no animation).
 * On subsequent layout changes, lerps from old → new.
 */
export function useLayoutTransition(
  targetLayout: HierarchicalDagLayout,
): HierarchicalDagLayout {
  // The displayed (possibly mid-interpolation) layout. A ref mirror lets the
  // start-animation effect snapshot the current display without re-running.
  const [display, setDisplay] = useState(targetLayout);
  const displayRef = useRef(targetLayout);
  const prevTargetRef = useRef(targetLayout);
  const animRef = useRef<number | null>(null);

  useEffect(() => {
    if (prevTargetRef.current === targetLayout) return;

    // Snapshot the currently displayed (mid-interpolation) layout as "from",
    // so rapid layout changes blend from where we are, not where we started
    const fromLayout = displayRef.current;
    prevTargetRef.current = targetLayout;

    const startTime = performance.now();
    function animate(timestamp: number) {
      const elapsed = timestamp - startTime;
      const t = ease(Math.min(elapsed / TRANSITION_MS, 1));

      const interpolated = lerpLayout(fromLayout, targetLayout, t);
      displayRef.current = interpolated;
      setDisplay(interpolated);

      if (t < 1) {
        animRef.current = requestAnimationFrame(animate);
      } else {
        animRef.current = null;
      }
    }

    // Start animation
    if (animRef.current) cancelAnimationFrame(animRef.current);
    animRef.current = requestAnimationFrame(animate);

    return () => {
      if (animRef.current) cancelAnimationFrame(animRef.current);
    };
  }, [targetLayout]);

  return display;
}
