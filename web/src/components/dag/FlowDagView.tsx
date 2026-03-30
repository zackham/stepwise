import { useMemo, useRef, useCallback, useEffect, useState, Suspense, lazy } from "react";
import { computeHierarchicalLayout } from "@/lib/dag-layout";
import type { DagSelection } from "@/lib/dag-layout";
import { useLayoutTransition } from "@/lib/layout-transition";
import { useDagCamera } from "@/hooks/useDagCamera";
import { StepNode } from "./StepNode";
import { DagEdges } from "./DagEdges";
import type { HoveredLabelInfo } from "./DagEdges";
import { ExpandedStepContainer } from "./ExpandedStepContainer";
import { ForEachExpandedContainer } from "./ForEachExpandedContainer";
import { FlowPortNode } from "./FlowPortNode";
import { ExternalInputPanel, getWatchProps } from "./ExternalInputPanel";
import { CanvasJobControls } from "./CanvasJobControls";
import type { FlowDefinition, StepRun, JobTreeNode, JobStatus } from "@/lib/types";
import { Share2, Download, Check, RefreshCw, XCircle } from "lucide-react";
import { computeCriticalPath } from "@/lib/critical-path";
import type { CriticalPathResult } from "@/lib/critical-path";
import { toBlob } from "html-to-image";
import { useTheme } from "@/hooks/useTheme";
import { canUseWebGL } from "@/lib/webgl/webgl-utils";

const WebGLEdgeLayer = lazy(() => import("./WebGLEdgeLayer"));

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function formatTooltipValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  return JSON.stringify(value, null, 2);
}

function getStatusColors(isDark: boolean): Record<JobStatus, string> {
  return {
    staged: "#8b5cf6",
    pending: isDark ? "#71717a" : "#a1a1aa",
    running: "#3b82f6",
    paused: "#f59e0b",
    completed: "#10b981",
    failed: "#ef4444",
    cancelled: isDark ? "#71717a" : "#a1a1aa",
    archived: isDark ? "#52525b" : "#71717a",
  };
}

const STATUS_LABELS: Record<JobStatus, string> = {
  staged: "Staged",
  pending: "Pending",
  running: "Running",
  paused: "Paused",
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  archived: "Archived",
};

interface JobActionCallbacks {
  onPauseJob?: () => void;
  onResumeJob?: () => void;
  onCancelJob?: () => void;
  onRetryJob?: () => void;
  onStartJob?: () => void;
  onRerunStep?: (stepName: string) => void;
  onCancelRun?: (runId: string) => void;
  isPausePending?: boolean;
  isResumePending?: boolean;
  isCancelPending?: boolean;
  isRetryPending?: boolean;
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
  flowName?: string;
  jobStatus?: JobStatus;
  jobActions?: JobActionCallbacks;
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
  flowName,
  jobStatus,
  jobActions,
}: FlowDagViewProps) {
  const theme = useTheme();
  const isDark = theme === "dark";
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const inputPanelRef = useRef<HTMLDivElement>(null);
  const edgeTooltipRef = useRef<HTMLDivElement>(null);
  const [hoveredLabel, setHoveredLabel] = useState<HoveredLabelInfo | null>(null);
  const [showCriticalPath, setShowCriticalPath] = useState(false);
  const [multiSelected, setMultiSelected] = useState<Set<string>>(new Set());
  const [webglSupported] = useState(() => canUseWebGL());
  const [webglActive, setWebglActive] = useState(false);

  // Clear multi-select when clicking canvas background
  const handleCanvasClick = useCallback(() => {
    if (multiSelected.size > 0) {
      setMultiSelected(new Set());
    }
  }, [multiSelected]);

  const handleMultiSelectToggle = useCallback((stepName: string) => {
    setMultiSelected((prev) => {
      const next = new Set(prev);
      if (next.has(stepName)) {
        next.delete(stepName);
      } else {
        next.add(stepName);
      }
      return next;
    });
  }, []);

  const handleBulkRerun = useCallback(() => {
    if (!jobActions?.onRerunStep) return;
    for (const stepName of multiSelected) {
      jobActions.onRerunStep(stepName);
    }
    setMultiSelected(new Set());
  }, [multiSelected, jobActions]);

  const handleBulkCancelRun = useCallback(() => {
    if (!jobActions?.onCancelRun) return;
    for (const stepName of multiSelected) {
      const run = runs.find(
        (r) => r.step_name === stepName && (r.status === "running" || r.status === "suspended"),
      );
      if (run) jobActions.onCancelRun(run.id);
    }
    setMultiSelected(new Set());
  }, [multiSelected, jobActions, runs]);

  const [shareState, setShareState] = useState<"idle" | "capturing" | "copied">("idle");
  const logoRef = useRef<HTMLImageElement | null>(null);

  // Preload logo for watermark
  useEffect(() => {
    const img = new Image();
    img.src = "/stepwise-icon-64.png";
    img.onload = () => { logoRef.current = img; };
  }, []);

  const captureDAG = useCallback(async (mode: "clipboard" | "download") => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    setShareState("capturing");
    try {
      // Save current state
      const savedTransform = canvas.style.transform;
      const savedOverflow = container.style.overflow;

      // Remove transform and show full canvas for capture
      canvas.style.transform = "none";
      container.style.overflow = "visible";

      // Hide overlays during capture
      if (inputPanelRef.current) inputPanelRef.current.style.display = "none";
      if (edgeTooltipRef.current) edgeTooltipRef.current.style.display = "none";

      const computedStyle = getComputedStyle(document.documentElement);
      const canvasBg = computedStyle.getPropertyValue("--dag-canvas-bg").trim() || "#09090b";
      const canvasBorder = computedStyle.getPropertyValue("--dag-canvas-border").trim() || "#27272a";
      const canvasText = computedStyle.getPropertyValue("--dag-canvas-text").trim() || "#fafafa";
      const canvasMuted = computedStyle.getPropertyValue("--dag-canvas-muted").trim() || "#52525b";

      const pixelRatio = 2;
      const blob = await toBlob(canvas, {
        backgroundColor: canvasBg,
        pixelRatio,
        filter: (node: HTMLElement) => {
          // Filter out counter-scaled overlays
          if (node instanceof HTMLElement && node.dataset?.captureHide) return false;
          return true;
        },
      });

      // Restore state
      canvas.style.transform = savedTransform;
      container.style.overflow = savedOverflow;
      if (inputPanelRef.current) inputPanelRef.current.style.display = "";
      if (edgeTooltipRef.current) edgeTooltipRef.current.style.display = "";

      if (!blob) { setShareState("idle"); return; }

      // Load captured image
      const dagImg = new Image();
      dagImg.src = URL.createObjectURL(blob);
      await new Promise<void>((resolve) => { dagImg.onload = () => resolve(); });

      // Create branded composite
      const HEADER_H = 72 * pixelRatio;
      const FOOTER_H = 48 * pixelRatio;
      const PAD = 24 * pixelRatio;
      const imgW = dagImg.naturalWidth;
      const imgH = dagImg.naturalHeight;
      // Cap width for social sharing — scale down if DAG is huge
      const maxW = 2400 * pixelRatio;
      const scale = imgW > maxW ? maxW / imgW : 1;
      const scaledW = Math.round(imgW * scale);
      const scaledH = Math.round(imgH * scale);

      const outW = scaledW + PAD * 2;
      const outH = HEADER_H + scaledH + FOOTER_H + PAD;
      const offscreen = document.createElement("canvas");
      offscreen.width = outW;
      offscreen.height = outH;
      const ctx = offscreen.getContext("2d")!;

      // Background
      ctx.fillStyle = canvasBg;
      ctx.fillRect(0, 0, outW, outH);

      // Subtle border
      ctx.strokeStyle = canvasBorder;
      ctx.lineWidth = pixelRatio;
      ctx.strokeRect(0, 0, outW, outH);

      // Header
      const headerY = HEADER_H / 2;
      ctx.fillStyle = canvasText;
      ctx.font = `bold ${20 * pixelRatio}px system-ui, -apple-system, sans-serif`;
      ctx.textBaseline = "middle";
      const title = flowName || "Flow";
      ctx.fillText(title, PAD, headerY);

      // Status badge
      if (jobStatus) {
        const titleWidth = ctx.measureText(title).width;
        const badgeX = PAD + titleWidth + 12 * pixelRatio;
        const badgeColor = getStatusColors(isDark)[jobStatus];
        const badgeLabel = STATUS_LABELS[jobStatus];
        ctx.font = `${12 * pixelRatio}px system-ui, -apple-system, sans-serif`;
        const badgeTextW = ctx.measureText(badgeLabel).width;
        const badgePadX = 10 * pixelRatio;
        const badgePadY = 6 * pixelRatio;
        const badgeH = 12 * pixelRatio + badgePadY * 2;
        const badgeW = badgeTextW + badgePadX * 2;

        // Badge background
        ctx.fillStyle = badgeColor + "22";
        ctx.beginPath();
        const r = 4 * pixelRatio;
        ctx.roundRect(badgeX, headerY - badgeH / 2, badgeW, badgeH, r);
        ctx.fill();

        // Badge text
        ctx.fillStyle = badgeColor;
        ctx.fillText(badgeLabel, badgeX + badgePadX, headerY);
      }

      // Header divider
      ctx.strokeStyle = canvasBorder;
      ctx.lineWidth = pixelRatio;
      ctx.beginPath();
      ctx.moveTo(PAD, HEADER_H - pixelRatio);
      ctx.lineTo(outW - PAD, HEADER_H - pixelRatio);
      ctx.stroke();

      // DAG image
      ctx.drawImage(dagImg, PAD, HEADER_H, scaledW, scaledH);
      URL.revokeObjectURL(dagImg.src);

      // Footer watermark
      const footerY = HEADER_H + scaledH + FOOTER_H / 2;
      const logoSize = 20 * pixelRatio;
      ctx.fillStyle = canvasMuted;
      ctx.font = `${13 * pixelRatio}px system-ui, -apple-system, sans-serif`;
      ctx.textBaseline = "middle";
      const stepwiseTextW = ctx.measureText("stepwise").width;
      if (logoRef.current) {
        ctx.globalAlpha = 0.4;
        ctx.drawImage(
          logoRef.current,
          outW - PAD - logoSize - stepwiseTextW - 8 * pixelRatio,
          footerY - logoSize / 2,
          logoSize,
          logoSize,
        );
        ctx.globalAlpha = 1;
      }
      ctx.textAlign = "right";
      ctx.fillText("stepwise", outW - PAD, footerY);
      ctx.textAlign = "left";

      // Export
      const finalBlob = await new Promise<Blob | null>((resolve) =>
        offscreen.toBlob(resolve, "image/png"),
      );
      if (!finalBlob) { setShareState("idle"); return; }

      if (mode === "clipboard") {
        try {
          await navigator.clipboard.write([
            new ClipboardItem({ "image/png": finalBlob }),
          ]);
          setShareState("copied");
          setTimeout(() => setShareState("idle"), 2000);
        } catch {
          // Clipboard API not available — fallback to download
          downloadBlob(finalBlob, `${flowName || "flow"}-dag.png`);
          setShareState("copied");
          setTimeout(() => setShareState("idle"), 2000);
        }
      } else {
        downloadBlob(finalBlob, `${flowName || "flow"}-dag.png`);
        setShareState("copied");
        setTimeout(() => setShareState("idle"), 2000);
      }
    } catch (err) {
      console.error("DAG capture failed:", err);
      setShareState("idle");
    }
  }, [flowName, jobStatus, isDark]);

  const rawLayout = useMemo(
    () => computeHierarchicalLayout(workflow, expandedSteps, jobTree),
    [workflow, expandedSteps, jobTree],
  );
  const layout = useLayoutTransition(rawLayout);

  const {
    followFlow,
    setFollowFlow,
    zoomDisplay,
    transformRef,
    cameraRef,
    applyTransform,
    handleWheel,
    handleMouseDown,
    handleMouseMove,
    handleMouseUp,
    handleClickCapture,
    handleTouchStart,
    handleTouchMove,
    handleTouchEnd,
    initView,
  } = useDagCamera({
    containerRef,
    canvasRef,
    inputPanelRef,
    edgeTooltipRef,
    layout,
    rawLayout,
    runs,
    jobTree,
    workflow,
    selectedStep,
  });

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

  // Critical path computation (only for terminal jobs when toggled on)
  const criticalPath: CriticalPathResult | null = useMemo(() => {
    if (!showCriticalPath) return null;
    const isTerminal = jobStatus === "completed" || jobStatus === "failed";
    if (!isTerminal) return null;
    return computeCriticalPath(workflow, latestRuns);
  }, [showCriticalPath, jobStatus, workflow, latestRuns]);

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
      className="relative w-full h-full overflow-hidden bg-zinc-100/50 dark:bg-zinc-950/50 rounded-lg touch-none"
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
        onClick={handleCanvasClick}
        style={{
          backgroundImage:
            "radial-gradient(circle, currentColor 1px, transparent 1px)",
          backgroundSize: "24px 24px",
        }}
      />

      {/* Canvas job controls */}
      {jobStatus && jobActions && (
        <CanvasJobControls
          jobStatus={jobStatus}
          onPause={() => jobActions.onPauseJob?.()}
          onResume={() => (jobStatus === "pending" || jobStatus === "staged" ? jobActions.onStartJob?.() : jobActions.onResumeJob?.())}
          onCancel={() => jobActions.onCancelJob?.()}
          onRetry={() => jobActions.onRetryJob?.()}
          isPausePending={jobActions.isPausePending}
          isResumePending={jobActions.isResumePending}
          isCancelPending={jobActions.isCancelPending}
          isRetryPending={jobActions.isRetryPending}
        />
      )}

      <div
        ref={canvasRef}
        style={{
          transformOrigin: "0 0",
          width: layout.width,
          height: layout.height,
          position: "relative",
        }}
      >
        {/* WebGL energy pulse edge layer (dark mode only) */}
        {webglSupported && isDark && (
          <Suspense fallback={null}>
            <WebGLEdgeLayer
              layout={layout}
              latestRuns={latestRuns}
              onReady={() => setWebglActive(true)}
            />
          </Suspense>
        )}
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
          criticalPath={criticalPath}
          webglActive={webglActive && isDark}
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
                isMultiSelected={multiSelected.has(node.id)}
                onClick={() =>
                  onSelectStep(selectedStep === node.id ? null : node.id)
                }
                onMultiSelectToggle={() => handleMultiSelectToggle(node.id)}
                onRerunStep={jobActions?.onRerunStep}
                onCancelRun={jobActions?.onCancelRun}
                onNavigateSubJob={onNavigateSubJob}
                onToggleExpand={
                  node.hasSubFlow ? () => onToggleExpand(node.id) : undefined
                }
                childStepCount={node.childStepCount}
                childJobStatus={subTrees?.[0]?.job.status ?? null}
                isCritical={criticalPath?.steps.has(node.id) ?? false}
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
            <div className="bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded-md shadow-xl p-2 -translate-x-1/2">
              <div className="text-[10px] font-medium text-zinc-500 dark:text-zinc-400 uppercase tracking-wide mb-1">
                {hoveredLabel.field}
              </div>
              <pre className="text-[11px] font-mono text-zinc-800 dark:text-zinc-200 whitespace-pre-wrap break-words max-w-[280px] max-h-[200px] overflow-auto m-0">
                {formatTooltipValue(hoveredLabel.value)}
              </pre>
            </div>
          </div>
        )}
      </div>

      {/* Bulk action bar for multi-select */}
      {multiSelected.size > 0 && (
        <div
          className="absolute top-3 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2 bg-white/90 dark:bg-zinc-900/90 backdrop-blur-sm rounded-lg border border-purple-500/30 shadow-lg px-3 py-1.5"
          data-capture-hide
        >
          <span className="text-xs text-purple-400 font-medium">
            {multiSelected.size} step{multiSelected.size > 1 ? "s" : ""} selected
          </span>
          <div className="w-px h-4 bg-zinc-300/50 dark:bg-zinc-700/50" />
          {jobActions?.onRerunStep && (
            <button
              onClick={handleBulkRerun}
              className="flex items-center gap-1 text-xs text-blue-400 hover:bg-blue-500/15 rounded px-2 py-0.5 transition-colors"
            >
              <RefreshCw className="w-3 h-3" />
              Rerun All
            </button>
          )}
          {jobActions?.onCancelRun && (
            <button
              onClick={handleBulkCancelRun}
              className="flex items-center gap-1 text-xs text-red-400 hover:bg-red-500/15 rounded px-2 py-0.5 transition-colors"
            >
              <XCircle className="w-3 h-3" />
              Cancel All
            </button>
          )}
          <div className="w-px h-4 bg-zinc-300/50 dark:bg-zinc-700/50" />
          <button
            onClick={() => setMultiSelected(new Set())}
            className="text-xs text-zinc-400 hover:text-zinc-300 transition-colors"
          >
            Clear
          </button>
        </div>
      )}

      {/* Zoom controls + follow flow */}
      <div className="absolute bottom-3 left-3 flex items-center gap-3 z-10">
        <label className="flex items-center gap-1.5 bg-white/80 dark:bg-zinc-900/80 rounded-md border border-zinc-300/50 dark:border-zinc-700/50 px-2 py-1 cursor-pointer select-none min-h-[44px] md:min-h-0">
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
        {(jobStatus === "completed" || jobStatus === "failed") && (
          <label className="flex items-center gap-1.5 bg-white/80 dark:bg-zinc-900/80 rounded-md border border-zinc-300/50 dark:border-zinc-700/50 px-2 py-1 cursor-pointer select-none min-h-[44px] md:min-h-0">
            <input
              type="checkbox"
              checked={showCriticalPath}
              onChange={(e) => setShowCriticalPath(e.target.checked)}
              className="accent-amber-400 w-3 h-3"
            />
            <span className="text-zinc-400 text-xs">Critical path</span>
          </label>
        )}
        <div className="flex items-center gap-1 bg-white/80 dark:bg-zinc-900/80 rounded-md border border-zinc-300/50 dark:border-zinc-700/50 px-2 py-1">
          <button
            onClick={() => {
              transformRef.current.scale = Math.min(transformRef.current.scale * 1.2, 3);
              const t = transformRef.current;
              cameraRef.current.syncFromManualInput(t.x, t.y, t.scale);
              applyTransform();
              setFollowFlow(false);
            }}
            className="text-zinc-400 hover:text-foreground text-sm px-1 min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 flex items-center justify-center"
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
              setFollowFlow(false);
            }}
            className="text-zinc-400 hover:text-foreground text-sm px-1 min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 flex items-center justify-center"
          >
            -
          </button>
          <button
            onClick={() => {
              initView();
              setFollowFlow(true);
            }}
            className="text-zinc-500 hover:text-zinc-300 text-xs ml-1 min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 flex items-center justify-center"
          >
            Reset
          </button>
        </div>
      </div>

      {/* Share / export controls */}
      <div className="absolute bottom-3 right-3 flex items-center gap-1 z-10">
        <div className="flex items-center gap-0.5 bg-white/80 dark:bg-zinc-900/80 rounded-md border border-zinc-300/50 dark:border-zinc-700/50 px-1 py-1">
          <button
            onClick={() => captureDAG("clipboard")}
            disabled={shareState === "capturing"}
            className="flex items-center gap-1.5 text-zinc-400 hover:text-foreground text-xs px-2 py-0.5 rounded hover:bg-zinc-200/50 dark:hover:bg-zinc-800/50 disabled:opacity-50"
            title="Copy DAG image to clipboard"
          >
            {shareState === "copied" ? (
              <Check className="w-3.5 h-3.5 text-emerald-400" />
            ) : shareState === "capturing" ? (
              <Share2 className="w-3.5 h-3.5 animate-pulse" />
            ) : (
              <Share2 className="w-3.5 h-3.5" />
            )}
            {shareState === "copied" ? "Copied!" : "Share"}
          </button>
          <div className="w-px h-4 bg-zinc-300/50 dark:bg-zinc-700/50" />
          <button
            onClick={() => captureDAG("download")}
            disabled={shareState === "capturing"}
            className="text-zinc-400 hover:text-foreground px-1.5 py-0.5 rounded hover:bg-zinc-200/50 dark:hover:bg-zinc-800/50 disabled:opacity-50"
            title="Download DAG as PNG"
          >
            <Download className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
