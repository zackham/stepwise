import { useRef, useCallback, useEffect, useLayoutEffect, useState, useMemo } from "react";
import type { RefObject, MutableRefObject } from "react";
import { DagCamera } from "@/lib/dag-camera";
import type { Rect } from "@/lib/dag-camera";
import type { HierarchicalDagLayout } from "@/lib/dag-layout";
import type { FlowDefinition, StepRun, JobTreeNode } from "@/lib/types";

export interface UseDagCameraOptions {
  containerRef: RefObject<HTMLDivElement | null>;
  canvasRef: RefObject<HTMLDivElement | null>;
  inputPanelRef: RefObject<HTMLDivElement | null>;
  edgeTooltipRef: RefObject<HTMLDivElement | null>;
  layout: HierarchicalDagLayout;
  rawLayout: HierarchicalDagLayout;
  runs: StepRun[];
  jobTree: JobTreeNode | null;
  workflow: FlowDefinition;
  selectedStep: string | null;
}

export interface UseDagCameraReturn {
  followFlow: boolean;
  setFollowFlow: (v: boolean) => void;
  zoomDisplay: number;
  transformRef: MutableRefObject<{ x: number; y: number; scale: number }>;
  cameraRef: MutableRefObject<DagCamera>;
  applyTransform: () => void;
  handleWheel: (e: React.WheelEvent) => void;
  handleMouseDown: (e: React.MouseEvent) => void;
  handleMouseMove: (e: React.MouseEvent) => void;
  handleMouseUp: () => void;
  handleClickCapture: (e: React.MouseEvent) => void;
  handleTouchStart: (e: React.TouchEvent) => void;
  handleTouchMove: (e: React.TouchEvent) => void;
  handleTouchEnd: (e: React.TouchEvent) => void;
  fitToView: () => void;
  initView: () => void;
}

export function useDagCamera({
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
}: UseDagCameraOptions): UseDagCameraReturn {
  const transformRef = useRef({ x: 0, y: 0, scale: 1 });
  const isDraggingRef = useRef(false);
  const didDragRef = useRef(false);
  const dragStart = useRef({ x: 0, y: 0, tx: 0, ty: 0 });
  const touchStartRef = useRef<{ id: number; x: number; y: number; time: number } | null>(null);
  const pinchStartRef = useRef<{ dist: number; scale: number; mx: number; my: number } | null>(null);
  const [zoomDisplay, setZoomDisplay] = useState(100);
  const hasCenteredRef = useRef(false);
  const [followFlow, setFollowFlow] = useState(true);
  const followAnimRef = useRef<number | null>(null);
  const cameraRef = useRef(new DagCamera());
  const lastFrameTimeRef = useRef<number | null>(null);
  const prevRawLayoutRef = useRef(rawLayout);

  // When the raw layout changes (expand/collapse, new sub-jobs), clear
  // camera spring velocities so stale momentum doesn't cause jank
  useEffect(() => {
    if (prevRawLayoutRef.current !== rawLayout) {
      prevRawLayoutRef.current = rawLayout;
      cameraRef.current.onLayoutChange();
    }
  }, [rawLayout]);

  // Apply transform directly to DOM (no re-render) + sync zoom display
  const applyTransform = useCallback(() => {
    const el = canvasRef.current;
    if (!el) return;
    const { x, y, scale } = transformRef.current;
    el.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
    const counterScale = `scale(${1 / scale})`;
    if (inputPanelRef.current) {
      inputPanelRef.current.style.transform = counterScale;
    }
    if (edgeTooltipRef.current) {
      edgeTooltipRef.current.style.transform = counterScale;
    }
    setZoomDisplay(Math.round(scale * 100));
  }, [canvasRef, inputPanelRef, edgeTooltipRef]);

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
      const x = rect.width / 2;
      transformRef.current = { x, y: 32, scale: 1 };
      cameraRef.current.syncFromManualInput(x, 32, 1);
      applyTransform();
      hasCenteredRef.current = true;
      return;
    }

    // If follow flow is on, start at scale=1 centered on first node
    // and let the spring animation smoothly zoom to fit active steps
    if (followFlow) {
      const firstNode = layout.nodes[0];
      const x = rect.width / 2 - (firstNode.x + firstNode.width / 2);
      const y = rect.height / 2 - (firstNode.y + firstNode.height / 2);
      transformRef.current = { x, y, scale: 1 };
      cameraRef.current.syncFromManualInput(x, y, 1);
      applyTransform();
      hasCenteredRef.current = true;
      return;
    }

    // Fit all nodes when follow flow is off
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
    const scale = Math.min(scaleX, scaleY, 1);

    const x = (rect.width - contentWidth * scale) / 2 - minX * scale;
    const y = (rect.height - contentHeight * scale) / 2 - minY * scale;

    transformRef.current = { x, y, scale };
    cameraRef.current.syncFromManualInput(x, y, scale);
    applyTransform();
    hasCenteredRef.current = true;
  }, [layout, applyTransform, containerRef, followFlow]);

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
  // 300px scroll body + ~90px submit/chrome + 12px gap
  const EXTERNAL_PANEL_SCREEN_HEIGHT_FALLBACK = 400;
  const EXTERNAL_PANEL_GAP = 12;

  const [measuredPanelHeight, setMeasuredPanelHeight] = useState(0);
  useEffect(() => {
    const el = inputPanelRef.current;
    if (!el) {
      setMeasuredPanelHeight(0);
      return;
    }
    const measure = () => {
      const h = el.getBoundingClientRect().height;
      setMeasuredPanelHeight(h);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedStep, runs]);

  // Collect active step IDs (stable identity, independent of layout positions)
  const activeStepInfo = useMemo(() => {
    const activeNodeIds: string[] = [];
    const suspendedIds = new Set<string>();
    for (const [name, run] of Object.entries(latestRuns)) {
      if (run.status === "running" || run.status === "suspended" || run.status === "delegated") {
        activeNodeIds.push(name);
        if (run.status === "suspended" ||
            (run.status === "running" &&
             (run.executor_state as Record<string, unknown> | undefined)?.usage_limit_waiting)) {
          suspendedIds.add(name);
        }
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
    const key = activeNodeIds.sort().join(",");
    return { activeNodeIds, suspendedIds, key };
  }, [latestRuns, jobTree, runs]);

  // Build rects from layout positions (changes on layout recalc too)
  const activeRects = useMemo(() => {
    const { activeNodeIds, suspendedIds } = activeStepInfo;
    const scale = transformRef.current.scale;
    // Only account for the inline panel height when it's actually measured
    // (i.e., rendered in the DAG). Don't use a fallback — it causes the camera
    // to pan offscreen when the fulfillment UI is in the side panel instead.
    const panelScreenH = measuredPanelHeight;
    const rects: Rect[] = [];
    for (const n of layout.nodes) {
      if (activeNodeIds.includes(n.id)) {
        // Include form height for suspended steps — the fulfillment form
        // is always visible for suspended steps, not just when selected
        const hasPopover = suspendedIds.has(n.id);
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
  }, [followFlow, activeRects, activeStepInfo.key, containerRef]);

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

  // Pan to selected step if it's off-screen (keyboard navigation only).
  // Skip when follow flow is active — the spring camera handles framing.
  useEffect(() => {
    if (!selectedStep || followFlow) return;
    const node = layout.nodes.find((n) => n.id === selectedStep);
    if (!node) return;
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return;

    const { x: tx, y: ty, scale } = transformRef.current;
    const screenX = node.x * scale + tx;
    const screenY = node.y * scale + ty;
    const screenW = node.width * scale;
    const screenH = node.height * scale;

    const padding = 60;
    const isVisible =
      screenX >= padding &&
      screenY >= padding &&
      screenX + screenW <= rect.width - padding &&
      screenY + screenH <= rect.height - padding;

    if (!isVisible) {
      const centerX = rect.width / 2 - (node.x + node.width / 2) * scale;
      const centerY = rect.height / 2 - (node.y + node.height / 2) * scale;
      transformRef.current.x = centerX;
      transformRef.current.y = centerY;
      cameraRef.current.syncFromManualInput(centerX, centerY, scale);
      applyTransform();
    }
  }, [selectedStep, layout, applyTransform, containerRef, followFlow]);

  const fitToView = initView;

  // Wheel zoom centered on cursor position
  const handleWheel = useCallback(
    (e: React.WheelEvent) => {
      e.preventDefault();
      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      const t = transformRef.current;
      const oldScale = t.scale;
      const newScale = Math.min(Math.max(oldScale * (e.deltaY > 0 ? 0.9 : 1.1), 0.3), 3);
      t.x = mx - (mx - t.x) * (newScale / oldScale);
      t.y = my - (my - t.y) * (newScale / oldScale);
      t.scale = newScale;
      cameraRef.current.syncFromManualInput(t.x, t.y, t.scale);
      applyTransform();
      setFollowFlow(false);
    },
    [applyTransform, containerRef]
  );

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) return;
      if (inputPanelRef.current?.contains(e.target as Node)) return;
      isDraggingRef.current = true;
      didDragRef.current = false;
      const t = transformRef.current;
      dragStart.current = { x: e.clientX, y: e.clientY, tx: t.x, ty: t.y };
    },
    [inputPanelRef]
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
    [applyTransform, containerRef]
  );

  const handleMouseUp = useCallback(() => {
    isDraggingRef.current = false;
    if (containerRef.current) containerRef.current.style.cursor = "grab";
  }, [containerRef]);

  const handleClickCapture = useCallback((e: React.MouseEvent) => {
    if (inputPanelRef.current?.contains(e.target as Node)) return;
    if (didDragRef.current) {
      e.stopPropagation();
      didDragRef.current = false;
    }
  }, [inputPanelRef]);

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
    [inputPanelRef, containerRef]
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
        if (!didDragRef.current && touchStartRef.current) {
          const elapsed = Date.now() - touchStartRef.current.time;
          if (elapsed < 300) {
            // Let the click event fire naturally for step selection
          }
        }
        touchStartRef.current = null;
      } else if (e.touches.length === 1) {
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

  return {
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
    fitToView,
    initView,
  };
}
