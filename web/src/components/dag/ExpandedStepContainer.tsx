import { ChevronUp, Layers } from "lucide-react";
import { JobStatusBadge } from "@/components/StatusBadge";
import { StepNode } from "./StepNode";
import { DagEdges } from "./DagEdges";
import type { HierarchicalDagLayout, HierarchicalDagNode } from "@/lib/dag-layout";
import type { FlowDefinition, StepRun, JobTreeNode, JobStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

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
  depth,
}: ExpandedStepContainerProps) {
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

  // Build sub-job map for child's children (runtime data)
  const childSubJobMap = new Map<string, JobTreeNode>();
  if (childJobTree) {
    for (const subJob of childJobTree.sub_jobs) {
      if (subJob.job.parent_step_run_id) {
        for (const run of childRuns) {
          if (run.sub_job_id === subJob.job.id) {
            childSubJobMap.set(run.step_name, subJob);
          }
        }
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
        "absolute border rounded-lg overflow-hidden",
        borderColor,
        bgColor,
      )}
      style={{ left: node.x, top: node.y, width: node.width, height: node.height }}
    >
      {/* Header bar */}
      <div
        className="flex items-center gap-2 px-3 h-10 bg-purple-500/10 border-b border-purple-500/20 cursor-pointer"
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
          <DagEdges
            edges={childLayout.edges}
            loopEdges={childLayout.loopEdges}
            width={childLayout.width}
            height={childLayout.height}
          />

          {childLayout.nodes.map((childNode) => {
            const stepDef = childWorkflow.steps[childNode.id];
            if (!stepDef) return null;
            const subTree = childSubJobMap.get(childNode.id);
            const subFlowDef = childSubFlowDefs.get(childNode.id);

            if (childNode.isExpanded && childNode.childLayout) {
              const nestedWorkflow = subTree?.job.workflow ?? subFlowDef ?? { steps: {} };
              const nestedRuns = subTree?.runs ?? [];
              return (
                <div key={childNode.id} data-step-node>
                  <ExpandedStepContainer
                    node={childNode}
                    stepName={childNode.id}
                    childLayout={childNode.childLayout}
                    childWorkflow={nestedWorkflow}
                    childRuns={nestedRuns}
                    childJobTree={subTree ?? null}
                    childStatus={subTree?.job.status ?? null}
                    expandedSteps={expandedSteps}
                    selectedStep={selectedStep}
                    onSelectStep={onSelectStep}
                    onToggleExpand={onToggleExpand}
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
                  maxAttempts={maxAttemptsMap[childNode.id] ?? null}
                  isSelected={selectedStep === childNode.id}
                  onClick={() =>
                    onSelectStep(selectedStep === childNode.id ? null : childNode.id)
                  }
                  onToggleExpand={
                    childNode.hasSubFlow ? () => onToggleExpand(childNode.id) : undefined
                  }
                  childStepCount={childNode.childStepCount}
                  childJobStatus={subTree?.job.status ?? null}
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
