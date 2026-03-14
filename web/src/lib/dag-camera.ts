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

export class DagCamera {
  // Current spring states
  private panX: SpringState = { pos: 0, vel: 0 };
  private panY: SpringState = { pos: 0, vel: 0 };
  private zoom: SpringState = { pos: 1, vel: 0 };

  // Raw target (before blending)
  private rawTargetX = 0;
  private rawTargetY = 0;
  private targetZoom = 1;

  // Target blending state
  private prevTargetX = 0;
  private prevTargetY = 0;
  private blendStartTime = 0;
  private isBlending = false;

  // Active node tracking
  private activeNodeKey = "";

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
    this.rawTargetX = x;
    this.rawTargetY = y;
    this.targetZoom = scale;
    this.settled = true;
    this.isBlending = false;
  }

  /**
   * Update active nodes. Computes new camera target based on
   * dead zone logic and minimal displacement.
   *
   * @param activeRects - bounding rects of active nodes in canvas coords
   * @param viewport - viewport dimensions { width, height }
   */
  setActiveNodes(
    activeRects: Rect[],
    viewport: { width: number; height: number },
  ): void {
    if (activeRects.length === 0) {
      this.hasTarget = false;
      return;
    }
    this.hasTarget = true;

    // Compute bounding box of all active nodes
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const r of activeRects) {
      minX = Math.min(minX, r.x);
      minY = Math.min(minY, r.y);
      maxX = Math.max(maxX, r.x + r.width);
      maxY = Math.max(maxY, r.y + r.height);
    }

    // Check if active set changed (for target blending)
    const newKey = activeRects.map(r => `${r.x},${r.y}`).sort().join("|");
    if (newKey !== this.activeNodeKey) {
      const hadPreviousTarget = this.activeNodeKey !== "";
      this.activeNodeKey = newKey;
      if (hadPreviousTarget) {
        // Blend from current spring target (what we were chasing) to new target
        this.prevTargetX = this.rawTargetX;
        this.prevTargetY = this.rawTargetY;
        this.blendStartTime = this.getNow();
        this.isBlending = true;
      }
    }

    const scale = this.zoom.pos;
    const vw = viewport.width;
    const vh = viewport.height;

    // ── Zoom target ──
    // Only change zoom when nodes overflow viewport or are very small
    const contentW = (maxX - minX) + ZOOM_OUT_PADDING * 2;
    const contentH = (maxY - minY) + ZOOM_OUT_PADDING * 2;
    const fitScale = Math.min(vw / contentW, vh / contentH);

    let newZoom = this.targetZoom;
    if (fitScale < scale) {
      // Nodes overflow: zoom out to fit (clamped to 0.3)
      newZoom = Math.max(0.3, Math.min(1.0, fitScale));
    } else {
      // Check if nodes are very small relative to viewport
      const usedFractionX = contentW * scale / vw;
      const usedFractionY = contentH * scale / vh;
      if (usedFractionX < ZOOM_IN_THRESHOLD && usedFractionY < ZOOM_IN_THRESHOLD) {
        // Zoom in, but not past 1.0
        newZoom = Math.min(1.0, fitScale * 0.8); // 80% of fit to leave breathing room
      }
      // Otherwise keep current zoom (hysteresis)
    }
    this.targetZoom = newZoom;

    // ── Pan target (dead zone + minimal displacement) ──
    const useScale = newZoom; // use target zoom for pan computation

    // Transform active node bounds to screen space
    const screenMinX = minX * useScale + this.panX.pos;
    const screenMinY = minY * useScale + this.panY.pos;
    const screenMaxX = maxX * useScale + this.panX.pos;
    const screenMaxY = maxY * useScale + this.panY.pos;

    // Dead zone (comfort zone) boundaries in screen space
    const dzLeft = vw * DEAD_ZONE_FRACTION;
    const dzRight = vw * (1 - DEAD_ZONE_FRACTION);
    const dzTop = vh * DEAD_ZONE_FRACTION;
    const dzBottom = vh * (1 - DEAD_ZONE_FRACTION);

    // Compute minimal displacement to bring nodes inside dead zone
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
      dx = vw / 2 - cx * useScale - this.panX.pos;
      dy = vh / 2 - cy * useScale - this.panY.pos;
    } else {
      // Minimal nudge to keep nodes inside comfort zone
      if (screenMinX < dzLeft) dx = dzLeft - screenMinX;
      else if (screenMaxX > dzRight) dx = dzRight - screenMaxX;

      if (screenMinY < dzTop) dy = dzTop - screenMinY;
      else if (screenMaxY > dzBottom) dy = dzBottom - screenMaxY;
    }

    this.rawTargetX = this.panX.pos + dx;
    this.rawTargetY = this.panY.pos + dy;

    // If we have a non-zero displacement, we're not settled
    if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5 || Math.abs(newZoom - this.zoom.pos) > 0.001) {
      this.settled = false;
    }
  }

  /** Compute the effective pan target, applying blend interpolation if active */
  private effectiveTarget(): { x: number; y: number } {
    if (!this.isBlending) {
      return { x: this.rawTargetX, y: this.rawTargetY };
    }
    const elapsed = this.getNow() - this.blendStartTime;
    const t = smoothstep(elapsed / TARGET_BLEND_MS);
    if (t >= 1) {
      this.isBlending = false;
      return { x: this.rawTargetX, y: this.rawTargetY };
    }
    return {
      x: this.prevTargetX + (this.rawTargetX - this.prevTargetX) * t,
      y: this.prevTargetY + (this.rawTargetY - this.prevTargetY) * t,
    };
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

    // Get blended target for this frame
    const target = this.effectiveTarget();

    this.panX = springStep(this.panX, target.x, PAN_OMEGA, cappedDt);
    this.panY = springStep(this.panY, target.y, PAN_OMEGA, cappedDt);
    this.zoom = springStep(this.zoom, this.targetZoom, ZOOM_OMEGA, cappedDt);

    // Check if all springs have settled (only if blend is complete)
    const blendDone = !this.isBlending;
    const panSettled = blendDone &&
      isSettled(this.panX, target.x, 0.5, 0.5) &&
      isSettled(this.panY, target.y, 0.5, 0.5);
    const zoomSettled = isSettled(this.zoom, this.targetZoom, 0.001, 0.001);

    if (panSettled && zoomSettled) {
      // Snap to exact target
      this.panX = { pos: target.x, vel: 0 };
      this.panY = { pos: target.y, vel: 0 };
      this.zoom = { pos: this.targetZoom, vel: 0 };
      this.settled = true;
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
