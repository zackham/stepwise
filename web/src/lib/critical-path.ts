import type { StepRun } from "./types";
import type { FlowDefinition } from "./types";

export interface CriticalPathResult {
  /** Step names on the critical path */
  steps: Set<string>;
  /** Edges on the critical path as "from->to" keys */
  edges: Set<string>;
  /** Total wall-clock duration in ms */
  totalDurationMs: number;
}

/**
 * Compute the critical (longest wall-clock duration) path through a DAG.
 * Uses DP on topological order with step durations from StepRun timing.
 * Returns null if fewer than 2 steps have timing data.
 */
export function computeCriticalPath(
  workflow: FlowDefinition,
  latestRuns: Record<string, StepRun>,
): CriticalPathResult | null {
  const stepNames = Object.keys(workflow.steps);
  if (stepNames.length < 2) return null;

  // Build adjacency: deps[step] = list of predecessor step names
  const deps: Record<string, string[]> = {};
  const successors: Record<string, string[]> = {};
  for (const name of stepNames) {
    deps[name] = [];
    successors[name] = [];
  }

  for (const [name, step] of Object.entries(workflow.steps)) {
    for (const binding of step.inputs) {
      if (binding.any_of_sources) {
        for (const src of binding.any_of_sources) {
          if (src.step && src.step !== "$job" && deps[name] && !deps[name].includes(src.step)) {
            deps[name].push(src.step);
            if (successors[src.step]) successors[src.step].push(name);
          }
        }
      } else if (binding.source_step && binding.source_step !== "$job" && deps[name]) {
        if (!deps[name].includes(binding.source_step)) {
          deps[name].push(binding.source_step);
          if (successors[binding.source_step]) successors[binding.source_step].push(name);
        }
      }
    }
    for (const seq of step.after) {
      if (deps[name] && !deps[name].includes(seq)) {
        deps[name].push(seq);
        if (successors[seq]) successors[seq].push(name);
      }
    }
  }

  // Topological sort (Kahn's algorithm)
  const inDegree: Record<string, number> = {};
  for (const name of stepNames) {
    inDegree[name] = deps[name].length;
  }
  const queue: string[] = [];
  for (const name of stepNames) {
    if (inDegree[name] === 0) queue.push(name);
  }
  const topoOrder: string[] = [];
  while (queue.length > 0) {
    const node = queue.shift()!;
    topoOrder.push(node);
    for (const succ of (successors[node] ?? [])) {
      inDegree[succ]--;
      if (inDegree[succ] === 0) queue.push(succ);
    }
  }

  // Compute durations from step runs
  const duration: Record<string, number> = {};
  let stepsWithTiming = 0;
  for (const name of stepNames) {
    const run = latestRuns[name];
    if (run?.started_at && run?.completed_at) {
      const d = Date.parse(run.completed_at) - Date.parse(run.started_at);
      duration[name] = d > 0 ? d : 0;
      stepsWithTiming++;
    } else {
      duration[name] = 0;
    }
  }

  if (stepsWithTiming < 2) return null;

  // DP: longestTo[step] = longest path duration ending at step
  const longestTo: Record<string, number> = {};
  const predecessor: Record<string, string | null> = {};
  for (const name of stepNames) {
    longestTo[name] = 0;
    predecessor[name] = null;
  }

  for (const step of topoOrder) {
    const stepDur = duration[step];
    // Best arrival from any predecessor
    let best = 0;
    let bestPred: string | null = null;
    for (const dep of (deps[step] ?? [])) {
      if (longestTo[dep] > best) {
        best = longestTo[dep];
        bestPred = dep;
      }
    }
    longestTo[step] = best + stepDur;
    predecessor[step] = bestPred;
  }

  // Find terminal step with max longestTo
  let maxStep = topoOrder[0];
  for (const step of topoOrder) {
    if (longestTo[step] > longestTo[maxStep]) {
      maxStep = step;
    }
  }

  // Backtrack to build path
  const pathSteps: string[] = [];
  let current: string | null = maxStep;
  while (current !== null) {
    pathSteps.unshift(current);
    current = predecessor[current];
  }

  if (pathSteps.length < 2) return null;

  const steps = new Set(pathSteps);
  const edges = new Set<string>();
  for (let i = 0; i < pathSteps.length - 1; i++) {
    edges.add(`${pathSteps[i]}->${pathSteps[i + 1]}`);
  }

  return {
    steps,
    edges,
    totalDurationMs: longestTo[maxStep],
  };
}
