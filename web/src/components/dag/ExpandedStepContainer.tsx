import { Suspense, lazy } from "react";
import { ChevronUp, Layers } from "lucide-react";
import { JobStatusBadge } from "@/components/StatusBadge";
import { StepNode } from "./StepNode";
import { DagEdges } from "./DagEdges";
import { ContainerPortEdges } from "./ContainerPortEdges";
import { ForEachExpandedContainer } from "./ForEachExpandedContainer";
import type { HierarchicalDagLayout, HierarchicalDagNode } from "@/lib/dag-layout";
import type { FlowDefinition, StepRun, JobTreeNode, JobStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { useTheme } from "@/hooks/useTheme";
import { canUseWebGL } from "@/lib/webgl/webgl-utils";

const WebGLEdgeLayer = lazy(() => import("./WebGLEdgeLayer"));

interface ExpandedStepContainerProps {
  node: HierarchicalDagNode;
  stepName: string;
  childLayout: HierarchicalDagLayout;
  childWorkflow: FlowDefinition;
  childRuns: StepRun[];
  childJobTree: JobTreeNode | null;
  childStatus: JobStatus | null;
  expandedSteps: Set<string>;
  selectedStep: string | null;
  onSelectStep: (key: string | null) => void;
  onToggleExpand: (stepName: string) => void;
  onNavigateSubJob?: (subJobId: string) => void;
  depth: number;
}

const DEPTH_BORDER_COLORS = [
  "border-purple-500/40",
  "border-purple-500/25",
  "border-purple-500/15",
  "border-purple-500/10",
];

const DEPTH_BG_COLORS = [
  "bg-purple-500/[0.04]",
  "bg-purple-500/[0.03]",
  "bg-purple-500/[0.02]",
  "bg-purple-500/[0.01]",
];

export function ExpandedStepContainer({
  node,
  stepName,
  childLayout,
  childWorkflow,
  childRuns,
  childJobTree,
  childStatus,
  expandedSteps,
  selectedStep,
  onSelectStep,
  onToggleExpand,
  onNavigateSubJob,
  depth,
}: ExpandedStepContainerProps) {
  const theme = useTheme();
  const isDark = theme === "dark";
  const borderColor = DEPTH_BORDER_COLORS[Math.min(depth, DEPTH_BORDER_COLORS.length - 1)];
  const bgColor = DEPTH_BG_COLORS[Math.min(depth, DEPTH_BG_COLORS.length - 1)];

  // Build latest runs map for child nodes
  const latestRuns: Record<string, StepRun> = {};
  for (const run of childRuns) {
    const existing = latestRuns[run.step_name];
    if (!existing || run.attempt > existing.attempt) {
      latestRuns[run.step_name] = run;
    }
  }

  // Build max attempts map for child workflow
  const maxAttemptsMap: Record<string, number> = {};
  for (const step of Object.values(childWorkflow.steps)) {
    for (const rule of step.exit_rules) {
      const action = rule.config.action as string | undefined;
      const target = rule.config.target as string | undefined;
      if (action !== "loop" || !target) continue;
      const mi = rule.config.max_iterations;
      if (typeof mi === "number") {
        maxAttemptsMap[target] = mi;
      }
    }
    for (const rule of step.exit_rules) {
      const action = rule.config.action as string | undefined;
      if (action !== "escalate" && action !== "abandon") continue;
      if (rule.type !== "expression") continue;
      const cond = rule.config.condition as string | undefined;
      if (!cond) continue;
      const match = cond.match(/attempt\s*>=\s*(\d+)/);
      if (match) {
        const loopTarget = step.exit_rules.find(
          (r) => r.config.action === "loop"
        )?.config.target as string | undefined;
        if (loopTarget) {
          maxAttemptsMap[loopTarget] = parseInt(match[1], 10);
        }
      }
    }
  }

  // Build sub-job map for child's children (runtime data) — arrays for for_each support
  const childSubJobMap = new Map<string, JobTreeNode[]>();
  if (childJobTree) {
    // Build sub-job lookup by ID
    const subJobById = new Map<string, JobTreeNode>();
    for (const sj of childJobTree.sub_jobs) {
      subJobById.set(sj.job.id, sj);
    }
    // Check runs for for_each or standard sub_job_id
    const latestRunByStep = new Map<string, StepRun>();
    for (const run of childRuns) {
      const isForEach = run.executor_state?.for_each === true;
      const hasSub = !!run.sub_job_id;
      if (!isForEach && !hasSub) continue;
      const existing = latestRunByStep.get(run.step_name);
      if (!existing || run.attempt > existing.attempt) {
        latestRunByStep.set(run.step_name, run);
      }
    }
    for (const [sName, run] of latestRunByStep) {
      if (run.executor_state?.for_each === true) {
        const ids = (run.executor_state?.sub_job_ids as string[]) ?? [];
        const nodes: JobTreeNode[] = [];
        for (const id of ids) {
          const n = subJobById.get(id);
          if (n) nodes.push(n);
        }
        if (nodes.length > 0) childSubJobMap.set(sName, nodes);
      } else if (run.sub_job_id) {
        const sj = subJobById.get(run.sub_job_id);
        if (sj) childSubJobMap.set(sName, [sj]);
      }
    }
  }

  // Design-time sub_flow fallback for child steps
  const childSubFlowDefs = new Map<string, FlowDefinition>();
  for (const [name, step] of Object.entries(childWorkflow.steps)) {
    if (step.sub_flow && !childSubJobMap.has(name)) {
      childSubFlowDefs.set(name, step.sub_flow);
    }
  }

  return (
    <div
      className={cn(
        "absolute border rounded-lg",
        borderColor,
        bgColor,
      )}
      style={{ left: node.x, top: node.y, width: node.width, height: node.height }}
    >
      {/* Header bar */}
      <div
        className="flex items-center gap-2 px-3 h-10 bg-purple-500/10 border-b border-purple-500/20 cursor-pointer rounded-t-lg overflow-hidden"
        onClick={(e) => {
          e.stopPropagation();
          onToggleExpand(stepName);
        }}
      >
        <Layers className="w-3.5 h-3.5 text-purple-400 shrink-0" />
        <span className="text-sm font-medium text-foreground truncate">
          {stepName}
        </span>
        {childStatus && <JobStatusBadge status={childStatus} />}
        <span className="text-[10px] text-zinc-500 ml-auto mr-1">
          {Object.keys(childWorkflow.steps).length} steps
        </span>
        <ChevronUp className="w-3.5 h-3.5 text-zinc-500 shrink-0" />
      </div>

      {/* Child DAG */}
      <div
        style={{
          position: "relative",
          left: node.containerPadding.left,
          top: 4,
          width: childLayout.width,
          height: childLayout.height,
        }}
      >
          {canUseWebGL() && isDark && (
            <Suspense fallback={null}>
              <WebGLEdgeLayer
                layout={childLayout}
                latestRuns={latestRuns}
                onReady={() => {}}
                onLost={() => {}}
              />
            </Suspense>
          )}
          <DagEdges
            edges={childLayout.edges}
            loopEdges={childLayout.loopEdges}
            width={childLayout.width}
            height={childLayout.height}
            latestRuns={latestRuns}
          />

          {/* Container port edges (lines + labels from container boundary to entry/terminal steps) */}
          <ContainerPortEdges
            containerPorts={childLayout.containerPorts}
            nodes={childLayout.nodes}
            layoutWidth={childLayout.width}
            layoutHeight={childLayout.height}
            jobInputs={childJobTree?.job.inputs}
            latestRuns={latestRuns}
            containerHeight={node.height}
            headerHeight={node.containerPadding.top}
            contentTop={node.containerPadding.top + 4}
          />

          {childLayout.nodes.map((childNode) => {
            const stepDef = childWorkflow.steps[childNode.id];
            if (!stepDef) return null;
            const subTrees = childSubJobMap.get(childNode.id);
            const subFlowDef = childSubFlowDefs.get(childNode.id);

            if (childNode.isForEach && childNode.forEachChildren && subTrees) {
              return (
                <div key={childNode.id} data-step-node>
                  <ForEachExpandedContainer
                    node={childNode}
                    stepName={childNode.id}
                    instances={childNode.forEachChildren}
                    subTrees={subTrees}
                    expandedSteps={expandedSteps}
                    selectedStep={selectedStep}
                    onSelectStep={onSelectStep}
                    onToggleExpand={onToggleExpand}
                    onNavigateSubJob={onNavigateSubJob}
                    depth={depth + 1}
                  />
                </div>
              );
            }

            if (childNode.isExpanded && childNode.childLayout) {
              const firstTree = subTrees?.[0] ?? null;
              const nestedWorkflow = firstTree?.job.workflow ?? subFlowDef ?? { steps: {} };
              const nestedRuns = firstTree?.runs ?? [];
              return (
                <div key={childNode.id} data-step-node>
                  <ExpandedStepContainer
                    node={childNode}
                    stepName={childNode.id}
                    childLayout={childNode.childLayout}
                    childWorkflow={nestedWorkflow}
                    childRuns={nestedRuns}
                    childJobTree={firstTree}
                    childStatus={firstTree?.job.status ?? null}
                    expandedSteps={expandedSteps}
                    selectedStep={selectedStep}
                    onSelectStep={onSelectStep}
                    onToggleExpand={onToggleExpand}
                    onNavigateSubJob={onNavigateSubJob}
                    depth={depth + 1}
                  />
                </div>
              );
            }

            return (
              <div key={childNode.id} data-step-node>
                <StepNode
                  stepDef={stepDef}
                  latestRun={latestRuns[childNode.id] ?? null}
                  latestRuns={latestRuns}
                  maxAttempts={maxAttemptsMap[childNode.id] ?? null}
                  isSelected={selectedStep === childNode.id}
                  onClick={() =>
                    onSelectStep(selectedStep === childNode.id ? null : childNode.id)
                  }
                  onNavigateSubJob={onNavigateSubJob}
                  onToggleExpand={
                    childNode.hasSubFlow ? () => onToggleExpand(childNode.id) : undefined
                  }
                  childStepCount={childNode.childStepCount}
                  childJobStatus={subTrees?.[0]?.job.status ?? null}
                  isNested
                  x={childNode.x}
                  y={childNode.y}
                  width={childNode.width}
                  height={childNode.height}
                />
              </div>
            );
          })}
        </div>

      {/* Top/bottom handles for parent edges */}
      <div className="absolute -top-1.5 left-1/2 -translate-x-1/2 w-3 h-3 rounded-full bg-purple-700 border-2 border-purple-500/50" />
      <div className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-3 h-3 rounded-full bg-purple-700 border-2 border-purple-500/50" />
    </div>
  );
}
