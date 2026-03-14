// ── Critically damped spring camera for DAG auto-follow ────────────

// Spring frequencies (higher = faster settle)
export const PAN_OMEGA = 6.0; // ~170ms to settle
export const ZOOM_OMEGA = 3.0; // ~330ms to settle (intentionally lazier)

// Dead zone: viewport inset fraction on each edge
export const DEAD_ZONE_FRACTION = 0.15; // 15% inset → 70% comfort rectangle

// Smoothstep blend duration when active node set changes
export const TARGET_BLEND_MS = 400;

// Max dt per tick to prevent jumps after tab switch
export const DT_CAP = 0.05; // 50ms

// Zoom hysteresis: only zoom when outside this range relative to "ideal"
const ZOOM_IN_THRESHOLD = 0.4; // zoom in when nodes use < 40% of viewport
const ZOOM_OUT_PADDING = 60; // px padding when zooming out to fit

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface SpringState {
  pos: number;
  vel: number;
}

/** Smoothstep interpolation (ease in-out) */
function smoothstep(t: number): number {
  const c = Math.max(0, Math.min(1, t));
  return c * c * (3 - 2 * c);
}

/**
 * Critically damped spring step.
 * Fastest convergence without oscillation.
 * x'' = -2*omega*x' - omega^2*(x - target)
 */
function springStep(
  state: SpringState,
  target: number,
  omega: number,
  dt: number,
): SpringState {
  const delta = state.pos - target;
  const exp = Math.exp(-omega * dt);
  const newPos = target + (delta + (state.vel + omega * delta) * dt) * exp;
  const newVel = (state.vel - omega * (state.vel + omega * delta) * dt) * exp;
  return { pos: newPos, vel: newVel };
}

/** Check if spring has settled (close enough to target with negligible velocity) */
function isSettled(state: SpringState, target: number, posTol: number, velTol: number): boolean {
  return Math.abs(state.pos - target) < posTol && Math.abs(state.vel) < velTol;
}

/**
 * Compute the bounding box of a set of rects.
 * Returns null if the array is empty.
 */
function boundingBox(rects: Rect[]): { minX: number; minY: number; maxX: number; maxY: number } | null {
  if (rects.length === 0) return null;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const r of rects) {
    minX = Math.min(minX, r.x);
    minY = Math.min(minY, r.y);
    maxX = Math.max(maxX, r.x + r.width);
    maxY = Math.max(maxY, r.y + r.height);
  }
  return { minX, minY, maxX, maxY };
}

export class DagCamera {
  // Current spring states
  private panX: SpringState = { pos: 0, vel: 0 };
  private panY: SpringState = { pos: 0, vel: 0 };
  private zoom: SpringState = { pos: 1, vel: 0 };

  // Zoom target (computed in setActiveNodes, stable between calls)
  private targetZoom = 1;

  // Target blending state
  private prevPanX = 0;
  private prevPanY = 0;
  private blendStartTime = 0;
  private isBlending = false;

  // Active node tracking
  private activeNodeKey = "";

  // Stored inputs for per-frame target recomputation
  private activeRects: Rect[] = [];
  private viewport = { width: 0, height: 0 };
  private activeBounds: { minX: number; minY: number; maxX: number; maxY: number } | null = null;

  // Whether the camera has a valid target (any active nodes exist)
  private hasTarget = false;

  // Settled state — when true, no animation needed
  private settled = true;

  // Clock for blend timing (uses performance.now by default)
  private getNow: () => number;

  constructor(getNow?: () => number) {
    this.getNow = getNow ?? (() => performance.now());
  }

  /**
   * Sync camera position from external source (manual drag, init, reset).
   * Immediately snaps — no spring animation.
   */
  syncFromManualInput(x: number, y: number, scale: number): void {
    this.panX = { pos: x, vel: 0 };
    this.panY = { pos: y, vel: 0 };
    this.zoom = { pos: scale, vel: 0 };
    this.targetZoom = scale;
    this.settled = true;
    this.isBlending = false;
  }

  /**
   * Notify the camera that the layout has changed (e.g. node expand/collapse).
   * Clears spring velocities so stale momentum doesn't fight the new layout,
   * but preserves positions so the camera smoothly adjusts from where it is.
   */
  onLayoutChange(): void {
    this.panX.vel = 0;
    this.panY.vel = 0;
    this.zoom.vel = 0;
    this.isBlending = false;
    this.settled = false;
  }

  /**
   * Update active nodes. Stores rects for per-frame target computation
   * and determines zoom target.
   *
   * @param activeRects - bounding rects of active nodes in canvas coords
   * @param viewport - viewport dimensions { width, height }
   * @param activeKey - identity key for the active set (e.g. sorted step names).
   *   Target blending only triggers when this key changes, not when
   *   node positions shift due to layout recalculation.
   */
  setActiveNodes(
    activeRects: Rect[],
    viewport: { width: number; height: number },
    activeKey?: string,
  ): void {
    if (activeRects.length === 0) {
      this.hasTarget = false;
      this.activeRects = [];
      this.activeBounds = null;
      return;
    }
    this.hasTarget = true;
    this.activeRects = activeRects;
    this.viewport = viewport;
    this.activeBounds = boundingBox(activeRects);

    // Check if the set of active steps changed (for target blending).
    // Use the caller-provided key (step names) so layout recalculations
    // don't trigger blending — only actual step transitions do.
    const newKey = activeKey ?? activeRects.map(r => `${r.x},${r.y}`).sort().join("|");
    if (newKey !== this.activeNodeKey) {
      const hadPreviousTarget = this.activeNodeKey !== "";
      this.activeNodeKey = newKey;
      if (hadPreviousTarget) {
        // Snapshot current pan position as blend origin
        this.prevPanX = this.panX.pos;
        this.prevPanY = this.panY.pos;
        this.blendStartTime = this.getNow();
        this.isBlending = true;
      }
    }

    // ── Zoom target ──
    // Only change zoom when nodes overflow viewport or are very small
    const bounds = this.activeBounds!;
    const contentW = (bounds.maxX - bounds.minX) + ZOOM_OUT_PADDING * 2;
    const contentH = (bounds.maxY - bounds.minY) + ZOOM_OUT_PADDING * 2;
    const fitScale = Math.min(viewport.width / contentW, viewport.height / contentH);
    const currentScale = this.zoom.pos;

    let newZoom = this.targetZoom;
    if (fitScale < currentScale) {
      // Nodes overflow at current zoom: zoom out to fit
      newZoom = Math.max(0.3, Math.min(1.0, fitScale));
    } else {
      // Check if nodes are very small relative to viewport
      const usedFractionX = contentW * currentScale / viewport.width;
      const usedFractionY = contentH * currentScale / viewport.height;
      if (usedFractionX < ZOOM_IN_THRESHOLD && usedFractionY < ZOOM_IN_THRESHOLD) {
        newZoom = Math.min(1.0, fitScale * 0.8);
      }
    }
    this.targetZoom = newZoom;

    // Wake up the animation loop
    this.settled = false;
  }

  /**
   * Compute the pan target for this frame using the current zoom spring
   * position. This ensures pan and zoom stay coherent as zoom animates.
   */
  private computePanTarget(currentScale: number): { x: number; y: number } {
    const bounds = this.activeBounds;
    if (!bounds) return { x: this.panX.pos, y: this.panY.pos };

    const { minX, minY, maxX, maxY } = bounds;
    const vw = this.viewport.width;
    const vh = this.viewport.height;

    // Transform active node bounds to screen space using current zoom
    const screenMinX = minX * currentScale + this.panX.pos;
    const screenMinY = minY * currentScale + this.panY.pos;
    const screenMaxX = maxX * currentScale + this.panX.pos;
    const screenMaxY = maxY * currentScale + this.panY.pos;

    // Dead zone boundaries in screen space
    const dzLeft = vw * DEAD_ZONE_FRACTION;
    const dzRight = vw * (1 - DEAD_ZONE_FRACTION);
    const dzTop = vh * DEAD_ZONE_FRACTION;
    const dzBottom = vh * (1 - DEAD_ZONE_FRACTION);

    let dx = 0;
    let dy = 0;

    const dzWidth = dzRight - dzLeft;
    const dzHeight = dzBottom - dzTop;
    const nodesScreenW = screenMaxX - screenMinX;
    const nodesScreenH = screenMaxY - screenMinY;

    if (nodesScreenW > dzWidth || nodesScreenH > dzHeight) {
      // Nodes span more than the comfort zone — center them
      const cx = (minX + maxX) / 2;
      const cy = (minY + maxY) / 2;
      dx = vw / 2 - cx * currentScale - this.panX.pos;
      dy = vh / 2 - cy * currentScale - this.panY.pos;
    } else {
      // Minimal nudge to keep nodes inside comfort zone
      if (screenMinX < dzLeft) dx = dzLeft - screenMinX;
      else if (screenMaxX > dzRight) dx = dzRight - screenMaxX;

      if (screenMinY < dzTop) dy = dzTop - screenMinY;
      else if (screenMaxY > dzBottom) dy = dzBottom - screenMaxY;
    }

    return { x: this.panX.pos + dx, y: this.panY.pos + dy };
  }

  /**
   * Advance the camera by one frame. Returns the new transform.
   * @param dt - time delta in seconds
   * @returns { x, y, scale, settled } — settled=true means no more animation needed
   */
  tick(dt: number): { x: number; y: number; scale: number; settled: boolean } {
    if (!this.hasTarget || this.settled) {
      return { x: this.panX.pos, y: this.panY.pos, scale: this.zoom.pos, settled: true };
    }

    // Cap dt to prevent huge jumps
    const cappedDt = Math.min(dt, DT_CAP);

    // Step zoom first so pan target uses the updated zoom
    this.zoom = springStep(this.zoom, this.targetZoom, ZOOM_OMEGA, cappedDt);

    // Recompute pan target using current (just-stepped) zoom
    const rawTarget = this.computePanTarget(this.zoom.pos);

    // Apply target blending if transitioning between active sets
    let targetX: number;
    let targetY: number;
    if (this.isBlending) {
      const elapsed = this.getNow() - this.blendStartTime;
      const t = smoothstep(elapsed / TARGET_BLEND_MS);
      if (t >= 1) {
        this.isBlending = false;
        targetX = rawTarget.x;
        targetY = rawTarget.y;
      } else {
        targetX = this.prevPanX + (rawTarget.x - this.prevPanX) * t;
        targetY = this.prevPanY + (rawTarget.y - this.prevPanY) * t;
      }
    } else {
      targetX = rawTarget.x;
      targetY = rawTarget.y;
    }

    this.panX = springStep(this.panX, targetX, PAN_OMEGA, cappedDt);
    this.panY = springStep(this.panY, targetY, PAN_OMEGA, cappedDt);

    // Check if all springs have settled (only if blend is complete)
    const blendDone = !this.isBlending;
    const panSettled = blendDone &&
      isSettled(this.panX, targetX, 0.5, 0.5) &&
      isSettled(this.panY, targetY, 0.5, 0.5);
    const zoomSettled = isSettled(this.zoom, this.targetZoom, 0.001, 0.001);

    // Pan can only truly settle when zoom is also done, since zoom changes
    // shift the pan target. Recheck dead zone at final zoom.
    if (panSettled && zoomSettled) {
      // Verify pan is still correct at settled zoom
      const finalTarget = this.computePanTarget(this.zoom.pos);
      const stillGood =
        Math.abs(this.panX.pos - finalTarget.x) < 1 &&
        Math.abs(this.panY.pos - finalTarget.y) < 1;

      if (stillGood) {
        this.panX = { pos: finalTarget.x, vel: 0 };
        this.panY = { pos: finalTarget.y, vel: 0 };
        this.zoom = { pos: this.targetZoom, vel: 0 };
        this.settled = true;
      }
    }

    return {
      x: this.panX.pos,
      y: this.panY.pos,
      scale: this.zoom.pos,
      settled: this.settled,
    };
  }

  /** Get current position without advancing */
  getTransform(): { x: number; y: number; scale: number } {
    return { x: this.panX.pos, y: this.panY.pos, scale: this.zoom.pos };
  }

  /** Whether the camera needs animation frames */
  isSettled(): boolean {
    return this.settled;
  }
}
