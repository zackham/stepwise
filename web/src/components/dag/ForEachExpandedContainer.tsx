import { useState, useMemo, Suspense, lazy } from "react";
import { ChevronDown, ChevronUp, Layers } from "lucide-react";
import { JobStatusBadge } from "@/components/StatusBadge";
import { StepNode } from "./StepNode";
import { DagEdges } from "./DagEdges";
import { ContainerPortEdges } from "./ContainerPortEdges";
import { ExpandedStepContainer } from "./ExpandedStepContainer";
import type { HierarchicalDagNode, ForEachInstance } from "@/lib/dag-layout";
import type { FlowDefinition, StepRun, JobTreeNode, JobStatus } from "@/lib/types";
import { JOB_STATUS_COLORS } from "@/lib/status-colors";
import { cn } from "@/lib/utils";
import { useTheme } from "@/hooks/useTheme";
import { canUseWebGL } from "@/lib/webgl/webgl-utils";

const WebGLEdgeLayer = lazy(() => import("./WebGLEdgeLayer"));

interface ForEachExpandedContainerProps {
  node: HierarchicalDagNode;
  stepName: string;
  instances: ForEachInstance[];
  subTrees: JobTreeNode[];
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

function InstanceStatusBadge({ status }: { status: string | null }) {
  if (!status) return null;
  return <JobStatusBadge status={status as JobStatus} />;
}

function InstanceStatusDot({ status }: { status: string | null }) {
  const s = (status ?? "pending") as JobStatus;
  const colors = JOB_STATUS_COLORS[s] ?? JOB_STATUS_COLORS.pending;
  return (
    <span
      className={cn("w-2 h-2 rounded-full shrink-0", colors.dot, s === "running" && "animate-pulse")}
      title={s}
    />
  );
}

export function ForEachExpandedContainer({
  node,
  stepName,
  instances,
  subTrees,
  expandedSteps,
  selectedStep,
  onSelectStep,
  onToggleExpand,
  onNavigateSubJob,
  depth,
}: ForEachExpandedContainerProps) {
  const theme = useTheme();
  const isDark = theme === "dark";
  const [collapsed, setCollapsed] = useState(false);
  const borderColor = DEPTH_BORDER_COLORS[Math.min(depth, DEPTH_BORDER_COLORS.length - 1)];
  const bgColor = DEPTH_BG_COLORS[Math.min(depth, DEPTH_BG_COLORS.length - 1)];

  // Build sub-tree lookup by job id
  const subTreeById = new Map<string, JobTreeNode>();
  for (const st of subTrees) {
    subTreeById.set(st.job.id, st);
  }

  // Aggregate status counts for header summary
  const statusSummary = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const inst of instances) {
      const s = inst.status ?? "pending";
      counts[s] = (counts[s] || 0) + 1;
    }
    return counts;
  }, [instances]);

  const ChevronIcon = collapsed ? ChevronDown : ChevronUp;

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
          setCollapsed(!collapsed);
        }}
      >
        <Layers className="w-3.5 h-3.5 text-purple-400 shrink-0" />
        <span className="text-sm font-medium text-foreground truncate">
          {stepName}
        </span>
        {/* Aggregate status badges */}
        <div className="flex items-center gap-1.5">
          {Object.entries(statusSummary).map(([status, count]) => (
            <span key={status} className="flex items-center gap-0.5">
              <JobStatusBadge status={status as JobStatus} />
              {count > 1 && (
                <span className="text-[10px] text-zinc-500">&times;{count}</span>
              )}
            </span>
          ))}
        </div>
        <span className="text-[10px] text-zinc-500 ml-auto mr-1">
          {instances.length} instances
        </span>
        <ChevronIcon className="w-3.5 h-3.5 text-zinc-500 shrink-0" />
      </div>

      {collapsed ? (
        /* Collapsed view: compact instance status grid */
        <div className="px-3 py-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            {instances.map((instance) => {
              const subTree = subTreeById.get(instance.jobId) ?? null;
              const itemLabel =
                subTree?.job.objective?.match(/\[(\d+)\]/)?.[0] ??
                `[${instance.index}]`;
              return (
                <div
                  key={instance.jobId}
                  className="flex items-center gap-1.5 text-[11px]"
                >
                  <span className="text-zinc-500 font-mono">{itemLabel}</span>
                  <InstanceStatusDot status={instance.status} />
                </div>
              );
            })}
          </div>
        </div>
      ) : (
      /* Expanded view: instances — horizontal layout */
      <div
        className="flex"
        style={{
          position: "relative",
          left: node.containerPadding.left,
          top: 4,
          gap: 8,
        }}
      >
        {instances.map((instance, idx) => {
          const subTree = subTreeById.get(instance.jobId) ?? null;
          const childWorkflow = subTree?.job.workflow ?? { steps: {} };
          const childRuns = subTree?.runs ?? [];

          // Build latest runs map for this instance
          const latestRuns: Record<string, StepRun> = {};
          for (const run of childRuns) {
            const existing = latestRuns[run.step_name];
            if (!existing || run.attempt > existing.attempt) {
              latestRuns[run.step_name] = run;
            }
          }

          // Build max attempts map
          const maxAttemptsMap: Record<string, number> = {};
          for (const step of Object.values(childWorkflow.steps)) {
            for (const rule of step.exit_rules) {
              const action = rule.config.action as string | undefined;
              const target = rule.config.target as string | undefined;
              if (action === "loop" && target && typeof rule.config.max_iterations === "number") {
                maxAttemptsMap[target] = rule.config.max_iterations as number;
              }
            }
          }

          // Build sub-job map for this instance's children
          const childSubJobMap = new Map<string, JobTreeNode>();
          if (subTree) {
            for (const sj of subTree.sub_jobs) {
              if (sj.job.parent_step_run_id) {
                for (const run of childRuns) {
                  if (run.sub_job_id === sj.job.id) {
                    childSubJobMap.set(run.step_name, sj);
                  }
                }
              }
            }
          }

          const childSubFlowDefs = new Map<string, FlowDefinition>();
          for (const [name, step] of Object.entries(childWorkflow.steps)) {
            if (step.sub_flow && !childSubJobMap.has(name)) {
              childSubFlowDefs.set(name, step.sub_flow);
            }
          }

          // Get an item label from the sub-job's inputs if available
          const itemLabel = subTree?.job.objective?.match(/\[(\d+)\]/)?.[0] ?? `[${instance.index}]`;

          return (
            <div
              key={instance.jobId}
              className={cn(idx > 0 && "border-l border-purple-500/15 pl-2")}
            >
              {/* Instance header */}
              <div className="flex items-center gap-2 px-2 h-7 text-[11px]">
                <span className="text-zinc-500 dark:text-zinc-400 font-mono">{itemLabel}</span>
                <InstanceStatusBadge status={instance.status} />
              </div>

              {/* Instance DAG */}
              <div
                style={{
                  position: "relative",
                  width: instance.layout.width,
                  height: instance.layout.height,
                }}
              >
                {canUseWebGL() && isDark && (
                  <Suspense fallback={null}>
                    <WebGLEdgeLayer
                      layout={instance.layout}
                      latestRuns={latestRuns}
                      onReady={() => {}}
                      onLost={() => {}}
                    />
                  </Suspense>
                )}
                <DagEdges
                  edges={instance.layout.edges}
                  loopEdges={instance.layout.loopEdges}
                  width={instance.layout.width}
                  height={instance.layout.height}
                  latestRuns={latestRuns}
                />

                {/* Container port edges — inputs only; outputs show on the parent container */}
                <ContainerPortEdges
                  containerPorts={instance.layout.containerPorts.filter((p) => p.type === "input")}
                  nodes={instance.layout.nodes}
                  layoutWidth={instance.layout.width}
                  layoutHeight={instance.layout.height}
                  jobInputs={subTree?.job.inputs}
                  latestRuns={latestRuns}
                />

                {instance.layout.nodes.map((childNode) => {
                  const stepDef = childWorkflow.steps[childNode.id];
                  if (!stepDef) return null;
                  const cSubTree = childSubJobMap.get(childNode.id);
                  const cSubFlowDef = childSubFlowDefs.get(childNode.id);

                  if (childNode.isExpanded && childNode.childLayout) {
                    const nestedWorkflow = cSubTree?.job.workflow ?? cSubFlowDef ?? { steps: {} };
                    const nestedRuns = cSubTree?.runs ?? [];
                    return (
                      <div key={childNode.id} data-step-node>
                        <ExpandedStepContainer
                          node={childNode}
                          stepName={childNode.id}
                          childLayout={childNode.childLayout}
                          childWorkflow={nestedWorkflow}
                          childRuns={nestedRuns}
                          childJobTree={cSubTree ?? null}
                          childStatus={cSubTree?.job.status ?? null}
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
                        childJobStatus={cSubTree?.job.status ?? null}
                        x={childNode.x}
                        y={childNode.y}
                        width={childNode.width}
                        height={childNode.height}
                      />
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
      )}

      {/* Top/bottom handles for parent edges */}
      <div className="absolute -top-1.5 left-1/2 -translate-x-1/2 w-3 h-3 rounded-full bg-purple-400 dark:bg-purple-700 border-2 border-purple-500/50" />
      <div className="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-3 h-3 rounded-full bg-purple-400 dark:bg-purple-700 border-2 border-purple-500/50" />
    </div>
  );
}
