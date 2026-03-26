import { useMemo, useRef, useCallback, useEffect, useLayoutEffect, useState } from "react";
import { computeHierarchicalLayout } from "@/lib/dag-layout";
import type { DagSelection } from "@/lib/dag-layout";
import { useLayoutTransition } from "@/lib/layout-transition";
import { DagCamera } from "@/lib/dag-camera";
import type { Rect } from "@/lib/dag-camera";
import { StepNode } from "./StepNode";
import { DagEdges } from "./DagEdges";
import type { HoveredLabelInfo } from "./DagEdges";
import { ExpandedStepContainer } from "./ExpandedStepContainer";
import { ForEachExpandedContainer } from "./ForEachExpandedContainer";
import { FlowPortNode } from "./FlowPortNode";
import { ExternalInputPanel, getWatchProps } from "./ExternalInputPanel";
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
  // Touch state refs
  const touchStartRef = useRef<{ id: number; x: number; y: number; time: number } | null>(null);
  const pinchStartRef = useRef<{ dist: number; scale: number; mx: number; my: number } | null>(null);
  const [zoomDisplay, setZoomDisplay] = useState(100);
  const hasCenteredRef = useRef(false);
  const [followFlow, setFollowFlow] = useState(true);
  const followAnimRef = useRef<number | null>(null);
  const cameraRef = useRef(new DagCamera());
  const lastFrameTimeRef = useRef<number | null>(null);

  const rawLayout = useMemo(
    () => computeHierarchicalLayout(workflow, expandedSteps, jobTree),
    [workflow, expandedSteps, jobTree],
  );
  const layout = useLayoutTransition(rawLayout);
  const prevRawLayoutRef = useRef(rawLayout);

  // When the raw layout changes (expand/collapse, new sub-jobs), clear
  // camera spring velocities so stale momentum doesn't cause jank
  useEffect(() => {
    if (prevRawLayoutRef.current !== rawLayout) {
      prevRawLayoutRef.current = rawLayout;
      cameraRef.current.onLayoutChange();
    }
  }, [rawLayout]);

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

  // Re-center only when the workflow changes (new job), not on expand/collapse
  useEffect(() => {
    hasCenteredRef.current = false;
    setFollowFlow(true);
  }, [workflow]);

  // Fit-to-view: compute bounding box of all nodes and scale/pan to fit in viewport
  const initView = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;

    if (layout.nodes.length === 0) {
      // No nodes — just center
      const x = rect.width / 2;
      transformRef.current = { x, y: 32, scale: 1 };
      cameraRef.current.syncFromManualInput(x, 32, 1);
      applyTransform();
      setZoomDisplay(100);
      hasCenteredRef.current = true;
      return;
    }

    // Compute bounding box of all nodes
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const node of layout.nodes) {
      minX = Math.min(minX, node.x);
      minY = Math.min(minY, node.y);
      maxX = Math.max(maxX, node.x + node.width);
      maxY = Math.max(maxY, node.y + node.height);
    }

    const contentWidth = maxX - minX;
    const contentHeight = maxY - minY;
    const padding = 40;

    const scaleX = (rect.width - padding * 2) / contentWidth;
    const scaleY = (rect.height - padding * 2) / contentHeight;
    // Fit within viewport but cap at 100% — don't over-zoom small DAGs
    const scale = Math.min(scaleX, scaleY, 1);

    // Center the content in the viewport
    const x = (rect.width - contentWidth * scale) / 2 - minX * scale;
    const y = (rect.height - contentHeight * scale) / 2 - minY * scale;

    transformRef.current = { x, y, scale };
    cameraRef.current.syncFromManualInput(x, y, scale);
    applyTransform();
    setZoomDisplay(Math.round(scale * 100));
    hasCenteredRef.current = true;
  }, [layout, applyTransform]);

  useLayoutEffect(() => {
    if (hasCenteredRef.current) return;
    initView();
  }, [initView]);

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

  // Fallback screen-pixel height of the ExternalInputPanel popover
  const EXTERNAL_PANEL_SCREEN_HEIGHT_FALLBACK = 280;
  const EXTERNAL_PANEL_GAP = 12; // gap between node bottom and panel top

  // Measure the actual input panel height (screen pixels) so follow-flow
  // can account for the full dialog rather than a static estimate.
  // Keyed on selectedStep so the observer re-attaches when the panel
  // appears/disappears (the ref target changes).
  const [measuredPanelHeight, setMeasuredPanelHeight] = useState(0);
  useEffect(() => {
    const el = inputPanelRef.current;
    if (!el) {
      setMeasuredPanelHeight(0);
      return;
    }
    const measure = () => {
      // getBoundingClientRect gives screen-pixel size (post-transform).
      // The panel is counter-scaled by 1/scale, so its screen size equals
      // its CSS size regardless of zoom — exactly what we need.
      const h = el.getBoundingClientRect().height;
      setMeasuredPanelHeight(h);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedStep]);

  // Collect active step IDs (stable identity, independent of layout positions)
  const activeStepInfo = useMemo(() => {
    const activeNodeIds: string[] = [];
    const suspendedIds = new Set<string>();
    for (const [name, run] of Object.entries(latestRuns)) {
      if (run.status === "running" || run.status === "suspended" || run.status === "delegated") {
        activeNodeIds.push(name);
        if (run.status === "suspended") suspendedIds.add(name);
      }
    }
    if (jobTree) {
      for (const subJob of jobTree.sub_jobs) {
        for (const run of subJob.runs) {
          if (run.status === "running" || run.status === "suspended") {
            const parentRun = runs.find((r) =>
              r.sub_job_id === subJob.job.id ||
              (r.executor_state?.for_each === true &&
                (r.executor_state?.sub_job_ids as string[] | undefined)?.includes(subJob.job.id))
            );
            if (parentRun && !activeNodeIds.includes(parentRun.step_name)) {
              activeNodeIds.push(parentRun.step_name);
            }
            break;
          }
        }
      }
    }
    // Key based on step names — only changes on actual step transitions,
    // not when positions shift due to layout recalculation (expand/collapse)
    const key = activeNodeIds.sort().join(",");
    return { activeNodeIds, suspendedIds, key };
  }, [latestRuns, jobTree, runs]);

  // Build rects from layout positions (changes on layout recalc too)
  const activeRects = useMemo(() => {
    const { activeNodeIds, suspendedIds } = activeStepInfo;
    const scale = transformRef.current.scale;
    const panelScreenH = measuredPanelHeight > 0
      ? measuredPanelHeight
      : EXTERNAL_PANEL_SCREEN_HEIGHT_FALLBACK;
    const rects: Rect[] = [];
    for (const n of layout.nodes) {
      if (activeNodeIds.includes(n.id)) {
        const hasPopover = selectedStep === n.id && suspendedIds.has(n.id);
        const popoverExtra = hasPopover
          ? EXTERNAL_PANEL_GAP + panelScreenH / scale
          : 0;
        rects.push({
          x: n.x,
          y: n.y,
          width: n.width,
          height: n.height + popoverExtra,
        });
      }
    }
    return rects;
  }, [layout, activeStepInfo, selectedStep, measuredPanelHeight]);

  // Feed active rects to camera whenever they change
  useEffect(() => {
    if (!followFlow) return;
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;
    cameraRef.current.setActiveNodes(
      activeRects,
      { width: rect.width, height: rect.height },
      activeStepInfo.key,
    );
  }, [followFlow, activeRects, activeStepInfo.key]);

  // Animation loop: spring physics, independent of data changes
  useEffect(() => {
    if (!followFlow) {
      if (followAnimRef.current) cancelAnimationFrame(followAnimRef.current);
      lastFrameTimeRef.current = null;
      return;
    }

    const animate = (timestamp: number) => {
      const lastTime = lastFrameTimeRef.current;
      lastFrameTimeRef.current = timestamp;
      if (lastTime === null) {
        followAnimRef.current = requestAnimationFrame(animate);
        return;
      }
      const dt = (timestamp - lastTime) / 1000;
      const { x, y, scale, settled } = cameraRef.current.tick(dt);
      transformRef.current = { x, y, scale };
      applyTransform();
      setZoomDisplay(Math.round(scale * 100));

      if (!settled) {
        followAnimRef.current = requestAnimationFrame(animate);
      } else {
        followAnimRef.current = null;
        lastFrameTimeRef.current = null;
      }
    };

    followAnimRef.current = requestAnimationFrame(animate);
    return () => {
      if (followAnimRef.current) cancelAnimationFrame(followAnimRef.current);
      lastFrameTimeRef.current = null;
    };
  }, [followFlow, activeRects, applyTransform]);

  // Keep reference for fitToView (used by Reset button)
  const fitToView = initView;

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
      cameraRef.current.syncFromManualInput(t.x, t.y, t.scale);
      applyTransform();
      setZoomDisplay(Math.round(newScale * 100));
      setFollowFlow(false);
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
        setFollowFlow(false);
      }
      transformRef.current.x = dragStart.current.tx + dx;
      transformRef.current.y = dragStart.current.ty + dy;
      const t = transformRef.current;
      cameraRef.current.syncFromManualInput(t.x, t.y, t.scale);
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

  // Touch event handlers for mobile pan/zoom
  const handleTouchStart = useCallback(
    (e: React.TouchEvent) => {
      if (inputPanelRef.current?.contains(e.target as Node)) return;
      if (e.touches.length === 1) {
        const t = e.touches[0];
        const tr = transformRef.current;
        touchStartRef.current = { id: t.identifier, x: t.clientX, y: t.clientY, time: Date.now() };
        dragStart.current = { x: t.clientX, y: t.clientY, tx: tr.x, ty: tr.y };
        isDraggingRef.current = true;
        didDragRef.current = false;
        pinchStartRef.current = null;
      } else if (e.touches.length === 2) {
        e.preventDefault();
        isDraggingRef.current = false;
        const [a, b] = [e.touches[0], e.touches[1]];
        const dist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        const container = containerRef.current;
        if (!container) return;
        const rect = container.getBoundingClientRect();
        pinchStartRef.current = {
          dist,
          scale: transformRef.current.scale,
          mx: (a.clientX + b.clientX) / 2 - rect.left,
          my: (a.clientY + b.clientY) / 2 - rect.top,
        };
      }
    },
    []
  );

  const handleTouchMove = useCallback(
    (e: React.TouchEvent) => {
      if (pinchStartRef.current && e.touches.length === 2) {
        e.preventDefault();
        const [a, b] = [e.touches[0], e.touches[1]];
        const newDist = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
        const ratio = newDist / pinchStartRef.current.dist;
        const oldScale = pinchStartRef.current.scale;
        const newScale = Math.min(Math.max(oldScale * ratio, 0.3), 3);
        const t = transformRef.current;
        const mx = pinchStartRef.current.mx;
        const my = pinchStartRef.current.my;
        t.x = mx - (mx - t.x) * (newScale / t.scale);
        t.y = my - (my - t.y) * (newScale / t.scale);
        t.scale = newScale;
        cameraRef.current.syncFromManualInput(t.x, t.y, t.scale);
        applyTransform();
        setZoomDisplay(Math.round(newScale * 100));
        setFollowFlow(false);
        didDragRef.current = true;
        return;
      }
      if (!isDraggingRef.current || e.touches.length !== 1) return;
      const t = e.touches[0];
      const dx = t.clientX - dragStart.current.x;
      const dy = t.clientY - dragStart.current.y;
      if (!didDragRef.current && Math.abs(dx) + Math.abs(dy) < DRAG_THRESHOLD) return;
      if (!didDragRef.current) {
        didDragRef.current = true;
        setFollowFlow(false);
      }
      transformRef.current.x = dragStart.current.tx + dx;
      transformRef.current.y = dragStart.current.ty + dy;
      const tr = transformRef.current;
      cameraRef.current.syncFromManualInput(tr.x, tr.y, tr.scale);
      applyTransform();
    },
    [applyTransform]
  );

  const handleTouchEnd = useCallback(
    (e: React.TouchEvent) => {
      if (e.touches.length === 0) {
        isDraggingRef.current = false;
        pinchStartRef.current = null;
        // Tap detection: short duration, no drag
        if (!didDragRef.current && touchStartRef.current) {
          const elapsed = Date.now() - touchStartRef.current.time;
          if (elapsed < 300) {
            // Let the click event fire naturally for step selection
          }
        }
        touchStartRef.current = null;
      } else if (e.touches.length === 1) {
        // Went from 2 -> 1 finger: reset to single-touch pan
        pinchStartRef.current = null;
        const t = e.touches[0];
        const tr = transformRef.current;
        touchStartRef.current = { id: t.identifier, x: t.clientX, y: t.clientY, time: Date.now() };
        dragStart.current = { x: t.clientX, y: t.clientY, tx: tr.x, ty: tr.y };
        isDraggingRef.current = true;
      }
    },
    []
  );

  // Build a map of step_name -> sub-job tree node(s) (runtime data)
  // Arrays: length 1 for standard sub-jobs, length N for for_each
  const subJobMap = useMemo(() => {
    if (!jobTree) return new Map<string, JobTreeNode[]>();
    const map = new Map<string, JobTreeNode[]>();
    // Build lookup by sub-job ID
    const subJobById = new Map<string, JobTreeNode>();
    for (const sj of jobTree.sub_jobs) {
      subJobById.set(sj.job.id, sj);
    }
    // Find latest run per step with sub-job info
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
    for (const [sName, run] of latestByStep) {
      if (run.executor_state?.for_each === true) {
        const ids = (run.executor_state?.sub_job_ids as string[]) ?? [];
        const nodes: JobTreeNode[] = [];
        for (const id of ids) {
          const n = subJobById.get(id);
          if (n) nodes.push(n);
        }
        if (nodes.length > 0) map.set(sName, nodes);
      } else if (run.sub_job_id) {
        const subJobByParentRunId = new Map<string, JobTreeNode>();
        for (const sj of jobTree.sub_jobs) {
          if (sj.job.parent_step_run_id) {
            subJobByParentRunId.set(sj.job.parent_step_run_id, sj);
          }
        }
        const subTree = subJobByParentRunId.get(run.id);
        if (subTree) map.set(sName, [subTree]);
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
      className="relative w-full h-full overflow-hidden bg-zinc-950/50 rounded-lg touch-none"
      onWheel={handleWheel}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
      onClickCapture={handleClickCapture}
      onTouchStart={handleTouchStart}
      onTouchMove={handleTouchMove}
      onTouchEnd={handleTouchEnd}
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
            latestRuns={latestRuns}
          />
        ))}

        {layout.nodes.map((node) => {
          const stepDef = workflow.steps[node.id];
          if (!stepDef) return null;
          const subTrees = subJobMap.get(node.id);
          const subFlowDef = subFlowDefs.get(node.id);

          if (node.isForEach && node.forEachChildren && subTrees) {
            return (
              <div key={node.id} data-step-node>
                <ForEachExpandedContainer
                  node={node}
                  stepName={node.id}
                  instances={node.forEachChildren}
                  subTrees={subTrees}
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

          if (node.isExpanded && node.childLayout) {
            // Runtime sub-job tree takes priority, fall back to design-time sub_flow
            const firstTree = subTrees?.[0] ?? null;
            const childWorkflow = firstTree?.job.workflow ?? subFlowDef ?? { steps: {} };
            const childRuns = firstTree?.runs ?? [];
            return (
              <div key={node.id} data-step-node>
                <ExpandedStepContainer
                  node={node}
                  stepName={node.id}
                  childLayout={node.childLayout}
                  childWorkflow={childWorkflow}
                  childRuns={childRuns}
                  childJobTree={firstTree}
                  childStatus={firstTree?.job.status ?? null}
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
                latestRuns={latestRuns}
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
                childJobStatus={subTrees?.[0]?.job.status ?? null}
                x={node.x}
                y={node.y}
                width={node.width}
                height={node.height}
              />
            </div>
          );
        })}

        {/* Inline external input panel — counter-scaled to stay at screen size */}
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
              <ExternalInputPanel
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

      {/* Zoom controls + follow flow */}
      <div className="absolute bottom-3 left-3 flex items-center gap-3 z-10">
        <label className="flex items-center gap-1.5 bg-zinc-900/80 rounded-md border border-zinc-700/50 px-2 py-1 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={followFlow}
            onChange={(e) => {
              setFollowFlow(e.target.checked);
            }}
            className="accent-blue-500 w-3 h-3"
          />
          <span className="text-zinc-400 text-xs">Follow flow</span>
        </label>
        <div className="flex items-center gap-1 bg-zinc-900/80 rounded-md border border-zinc-700/50 px-2 py-1">
          <button
            onClick={() => {
              transformRef.current.scale = Math.min(transformRef.current.scale * 1.2, 3);
              const t = transformRef.current;
              cameraRef.current.syncFromManualInput(t.x, t.y, t.scale);
              applyTransform();
              setZoomDisplay(Math.round(t.scale * 100));
              setFollowFlow(false);
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
              const t = transformRef.current;
              cameraRef.current.syncFromManualInput(t.x, t.y, t.scale);
              applyTransform();
              setZoomDisplay(Math.round(t.scale * 100));
              setFollowFlow(false);
            }}
            className="text-zinc-400 hover:text-foreground text-sm px-1"
          >
            -
          </button>
          <button
            onClick={() => {
              initView();
              setFollowFlow(true);
            }}
            className="text-zinc-500 hover:text-zinc-300 text-xs ml-1"
          >
            Reset
          </button>
        </div>
      </div>
    </div>
  );
}
