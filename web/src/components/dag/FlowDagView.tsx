import { useMemo, useRef, useCallback, useEffect, useLayoutEffect, useState } from "react";
import { computeHierarchicalLayout } from "@/lib/dag-layout";
import type { DagSelection } from "@/lib/dag-layout";
import { StepNode } from "./StepNode";
import { DagEdges } from "./DagEdges";
import type { HoveredLabelInfo } from "./DagEdges";
import { ExpandedStepContainer } from "./ExpandedStepContainer";
import { FlowPortNode } from "./FlowPortNode";
import { HumanInputPanel, getWatchProps } from "./HumanInputPanel";
import type { FlowDefinition, StepRun, JobTreeNode } from "@/lib/types";

function formatTooltipValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  return JSON.stringify(value, null, 2);
}

interface FlowDagViewProps {
  workflow: FlowDefinition;
  runs: StepRun[];
  jobTree: JobTreeNode | null;
  expandedSteps: Set<string>;
  onToggleExpand: (stepName: string) => void;
  selectedStep: string | null;
  onSelectStep: (stepName: string | null) => void;
  onNavigateSubJob?: (subJobId: string) => void;
  onFulfillWatch?: (runId: string, payload: Record<string, unknown>) => void;
  isFulfilling?: boolean;
  selection?: DagSelection;
  onSelectDataFlow?: (selection: DagSelection) => void;
}

export function FlowDagView({
  workflow,
  runs,
  jobTree,
  expandedSteps,
  onToggleExpand,
  selectedStep,
  onSelectStep,
  onNavigateSubJob,
  onFulfillWatch,
  isFulfilling,
  selection,
  onSelectDataFlow,
}: FlowDagViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const inputPanelRef = useRef<HTMLDivElement>(null);
  const edgeTooltipRef = useRef<HTMLDivElement>(null);
  const transformRef = useRef({ x: 0, y: 0, scale: 1 });
  const [hoveredLabel, setHoveredLabel] = useState<HoveredLabelInfo | null>(null);
  const isDraggingRef = useRef(false);
  const didDragRef = useRef(false);
  const dragStart = useRef({ x: 0, y: 0, tx: 0, ty: 0 });
  const [zoomDisplay, setZoomDisplay] = useState(100);
  const hasCenteredRef = useRef(false);

  const layout = useMemo(
    () => computeHierarchicalLayout(workflow, expandedSteps, jobTree),
    [workflow, expandedSteps, jobTree],
  );

  // Apply transform directly to DOM (no re-render)
  const applyTransform = useCallback(() => {
    const el = canvasRef.current;
    if (!el) return;
    const { x, y, scale } = transformRef.current;
    el.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
    // Counter-scale overlays so they stay at screen-pixel size
    const counterScale = `scale(${1 / scale})`;
    if (inputPanelRef.current) {
      inputPanelRef.current.style.transform = counterScale;
    }
    if (edgeTooltipRef.current) {
      edgeTooltipRef.current.style.transform = counterScale;
    }
  }, []);

  // Re-center when workflow or expansion state changes
  useEffect(() => {
    hasCenteredRef.current = false;
  }, [workflow, expandedSteps]);

  // Fit-to-view: runs synchronously before paint to avoid flash
  const fitToView = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    const pad = 64;
    const scale = Math.min(
      (rect.width - pad) / layout.width,
      (rect.height - pad) / layout.height,
      1.0
    );
    const x = (rect.width - layout.width * scale) / 2;
    const y = (rect.height - layout.height * scale) / 2;
    transformRef.current = { x, y, scale };
    applyTransform();
    setZoomDisplay(Math.round(scale * 100));
    hasCenteredRef.current = true;
  }, [layout, applyTransform]);

  useLayoutEffect(() => {
    if (hasCenteredRef.current) return;
    fitToView();
  }, [fitToView]);

  // Build a map of step_name -> latest run
  const latestRuns = useMemo(() => {
    const map: Record<string, StepRun> = {};
    for (const run of runs) {
      const existing = map[run.step_name];
      if (!existing || run.attempt > existing.attempt) {
        map[run.step_name] = run;
      }
    }
    return map;
  }, [runs]);

  // Wheel zoom centered on cursor position
  const handleWheel = useCallback(
    (e: React.WheelEvent) => {
      e.preventDefault();
      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      // Mouse position relative to the container
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const t = transformRef.current;
      const oldScale = t.scale;
      const newScale = Math.min(Math.max(oldScale * (e.deltaY > 0 ? 0.9 : 1.1), 0.3), 3);
      // Adjust translation so the point under the cursor stays fixed
      t.x = mx - (mx - t.x) * (newScale / oldScale);
      t.y = my - (my - t.y) * (newScale / oldScale);
      t.scale = newScale;
      applyTransform();
      setZoomDisplay(Math.round(newScale * 100));
    },
    [applyTransform]
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) return;
      // Don't start drag if clicking inside the input panel
      if (inputPanelRef.current?.contains(e.target as Node)) return;
      isDraggingRef.current = true;
      didDragRef.current = false;
      const t = transformRef.current;
      dragStart.current = { x: e.clientX, y: e.clientY, tx: t.x, ty: t.y };
    },
    []
  );

  const DRAG_THRESHOLD = 4;

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!isDraggingRef.current) return;
      const dx = e.clientX - dragStart.current.x;
      const dy = e.clientY - dragStart.current.y;
      if (!didDragRef.current && Math.abs(dx) + Math.abs(dy) < DRAG_THRESHOLD) return;
      if (!didDragRef.current) {
        didDragRef.current = true;
        if (containerRef.current) containerRef.current.style.cursor = "grabbing";
      }
      transformRef.current.x = dragStart.current.tx + dx;
      transformRef.current.y = dragStart.current.ty + dy;
      applyTransform();
    },
    [applyTransform]
  );

  const handleMouseUp = useCallback(() => {
    isDraggingRef.current = false;
    if (containerRef.current) containerRef.current.style.cursor = "grab";
  }, []);

  // Swallow click events after a drag so nodes don't get selected/toggled
  const handleClickCapture = useCallback((e: React.MouseEvent) => {
    // Never swallow clicks on the input panel
    if (inputPanelRef.current?.contains(e.target as Node)) return;
    if (didDragRef.current) {
      e.stopPropagation();
      didDragRef.current = false;
    }
  }, []);

  // Build a map of step_name -> sub-job tree node (runtime data)
  const subJobMap = useMemo(() => {
    if (!jobTree) return new Map<string, JobTreeNode>();
    const map = new Map<string, JobTreeNode>();
    const subJobByParentRunId = new Map<string, JobTreeNode>();
    for (const sj of jobTree.sub_jobs) {
      if (sj.job.parent_step_run_id) {
        subJobByParentRunId.set(sj.job.parent_step_run_id, sj);
      }
    }
    for (const run of runs) {
      if (!run.sub_job_id) continue;
      const subTree = subJobByParentRunId.get(run.id);
      if (subTree) {
        const existing = map.get(run.step_name);
        if (!existing) {
          map.set(run.step_name, subTree);
        }
      }
    }
    return map;
  }, [jobTree, runs]);

  // Build a map of step_name -> sub_flow definition (design-time fallback)
  const subFlowDefs = useMemo(() => {
    const map = new Map<string, FlowDefinition>();
    for (const [name, step] of Object.entries(workflow.steps)) {
      if (step.sub_flow && !subJobMap.has(name)) {
        map.set(name, step.sub_flow);
      }
    }
    return map;
  }, [workflow, subJobMap]);

  // Build a map of step_name -> max attempts (from loop rules targeting that step)
  const maxAttemptsMap = useMemo(() => {
    const map: Record<string, number> = {};
    for (const step of Object.values(workflow.steps)) {
      for (const rule of step.exit_rules) {
        const action = rule.config.action as string | undefined;
        const target = rule.config.target as string | undefined;
        if (action !== "loop" || !target) continue;
        const mi = rule.config.max_iterations;
        if (typeof mi === "number") {
          map[target] = mi;
          continue;
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
            map[loopTarget] = parseInt(match[1], 10);
          }
        }
      }
    }
    return map;
  }, [workflow]);

  // Derive selectedLabel from selection for DagEdges highlight
  const selectedLabel = useMemo(() => {
    if (!selection || selection.kind !== "edge-field") return null;
    return {
      fromStep: selection.fromStep,
      toStep: selection.toStep,
      fieldName: selection.fieldName,
    };
  }, [selection]);

  // Handle edge label click
  const handleClickLabel = useCallback(
    (from: string, to: string, field: string) => {
      if (!onSelectDataFlow) return;
      onSelectDataFlow({ kind: "edge-field", fromStep: from, toStep: to, fieldName: field });
    },
    [onSelectDataFlow],
  );

  // Handle edge label hover (tooltip)
  const handleHoverLabel = useCallback((info: HoveredLabelInfo) => {
    setHoveredLabel(info);
  }, []);
  const handleLeaveLabel = useCallback(() => {
    setHoveredLabel(null);
  }, []);

  if (Object.keys(workflow.steps).length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500">
        No steps in flow
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="relative w-full h-full overflow-hidden bg-zinc-950/50 rounded-lg"
      onWheel={handleWheel}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onClickCapture={handleClickCapture}
      style={{ cursor: "grab" }}
    >
      {/* Grid background */}
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage:
            "radial-gradient(circle, currentColor 1px, transparent 1px)",
          backgroundSize: "24px 24px",
        }}
      />

      <div
        ref={canvasRef}
        style={{
          transformOrigin: "0 0",
          width: layout.width,
          height: layout.height,
          position: "relative",
          willChange: "transform",
        }}
      >
        <DagEdges
          edges={layout.edges}
          loopEdges={layout.loopEdges}
          width={layout.width}
          height={layout.height}
          onClickLabel={onSelectDataFlow ? handleClickLabel : undefined}
          selectedLabel={selectedLabel}
          latestRuns={latestRuns}
          onHoverLabel={handleHoverLabel}
          onLeaveLabel={handleLeaveLabel}
        />

        {/* Flow port nodes (input/output) */}
        {layout.flowPorts.map((port) => (
          <FlowPortNode
            key={port.id}
            port={port}
            selection={selection ?? null}
            onSelect={onSelectDataFlow ?? (() => {})}
          />
        ))}

        {layout.nodes.map((node) => {
          const stepDef = workflow.steps[node.id];
          if (!stepDef) return null;
          const subTree = subJobMap.get(node.id);
          const subFlowDef = subFlowDefs.get(node.id);

          if (node.isExpanded && node.childLayout) {
            // Runtime sub-job tree takes priority, fall back to design-time sub_flow
            const childWorkflow = subTree?.job.workflow ?? subFlowDef ?? { steps: {} };
            const childRuns = subTree?.runs ?? [];
            return (
              <div key={node.id} data-step-node>
                <ExpandedStepContainer
                  node={node}
                  stepName={node.id}
                  childLayout={node.childLayout}
                  childWorkflow={childWorkflow}
                  childRuns={childRuns}
                  childJobTree={subTree ?? null}
                  childStatus={subTree?.job.status ?? null}
                  expandedSteps={expandedSteps}
                  selectedStep={selectedStep}
                  onSelectStep={onSelectStep}
                  onToggleExpand={onToggleExpand}
                  onNavigateSubJob={onNavigateSubJob}
                  depth={0}
                />
              </div>
            );
          }

          return (
            <div key={node.id} data-step-node>
              <StepNode
                stepDef={stepDef}
                latestRun={latestRuns[node.id] ?? null}
                maxAttempts={maxAttemptsMap[node.id] ?? null}
                isSelected={selectedStep === node.id}
                onClick={() =>
                  onSelectStep(selectedStep === node.id ? null : node.id)
                }
                onNavigateSubJob={onNavigateSubJob}
                onToggleExpand={
                  node.hasSubFlow ? () => onToggleExpand(node.id) : undefined
                }
                childStepCount={node.childStepCount}
                childJobStatus={subTree?.job.status ?? null}
                x={node.x}
                y={node.y}
                width={node.width}
                height={node.height}
              />
            </div>
          );
        })}

        {/* Inline human input panel — counter-scaled to stay at screen size */}
        {(() => {
          if (!selectedStep || !onFulfillWatch) return null;
          const run = latestRuns[selectedStep];
          if (!run || run.status !== "suspended") return null;
          const watchProps = getWatchProps(run.watch);
          if (!watchProps) return null;
          const node = layout.nodes.find((n) => n.id === selectedStep);
          if (!node) return null;
          const panelWidth = 320;
          return (
            <div
              ref={inputPanelRef}
              style={{
                position: "absolute",
                left: node.x + node.width / 2 - panelWidth / 2,
                top: node.y + node.height + 12,
                transformOrigin: "top center",
                transform: `scale(${1 / transformRef.current.scale})`,
                zIndex: 50,
              }}
            >
              <HumanInputPanel
                prompt={watchProps.prompt}
                outputs={watchProps.outputs}
                outputSchema={watchProps.outputSchema}
                onSubmit={(payload) => onFulfillWatch(run.id, payload)}
                isPending={isFulfilling ?? false}
              />
            </div>
          );
        })()}

        {/* Edge label value tooltip — counter-scaled to stay at screen size */}
        {hoveredLabel && (
          <div
            ref={edgeTooltipRef}
            className="pointer-events-none"
            style={{
              position: "absolute",
              left: hoveredLabel.x,
              top: hoveredLabel.y + 14,
              transformOrigin: "top center",
              transform: `scale(${1 / transformRef.current.scale})`,
              zIndex: 50,
            }}
          >
            <div className="bg-zinc-900 border border-zinc-700 rounded-md shadow-xl p-2 -translate-x-1/2">
              <div className="text-[10px] font-medium text-zinc-400 uppercase tracking-wide mb-1">
                {hoveredLabel.field}
              </div>
              <pre className="text-[11px] font-mono text-zinc-200 whitespace-pre-wrap break-words max-w-[280px] max-h-[200px] overflow-auto m-0">
                {formatTooltipValue(hoveredLabel.value)}
              </pre>
            </div>
          </div>
        )}
      </div>

      {/* Zoom controls */}
      <div className="absolute bottom-3 left-3 flex items-center gap-1 bg-zinc-900/80 rounded-md border border-zinc-700/50 px-2 py-1 z-10">
        <button
          onClick={() => {
            transformRef.current.scale = Math.min(transformRef.current.scale * 1.2, 3);
            applyTransform();
            setZoomDisplay(Math.round(transformRef.current.scale * 100));
          }}
          className="text-zinc-400 hover:text-foreground text-sm px-1"
        >
          +
        </button>
        <span className="text-zinc-500 text-xs min-w-[3rem] text-center">
          {zoomDisplay}%
        </span>
        <button
          onClick={() => {
            transformRef.current.scale = Math.max(transformRef.current.scale * 0.8, 0.3);
            applyTransform();
            setZoomDisplay(Math.round(transformRef.current.scale * 100));
          }}
          className="text-zinc-400 hover:text-foreground text-sm px-1"
        >
          -
        </button>
        <button
          onClick={() => fitToView()}
          className="text-zinc-500 hover:text-zinc-300 text-xs ml-1"
        >
          Reset
        </button>
      </div>
    </div>
  );
}
