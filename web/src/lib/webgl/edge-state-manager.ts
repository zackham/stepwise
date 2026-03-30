import type { DagEdge, LoopEdge } from "@/lib/dag-layout";
import type { StepRun } from "@/lib/types";

/** Edge animation states */
export const EdgeState = {
  IDLE: 0,
  SURGE: 1,
  FLOW: 2,
  COMPLETED: 3,
  FAILED: 4,
} as const;

export type EdgeStateValue = (typeof EdgeState)[keyof typeof EdgeState];

export interface EdgeAnimState {
  edgeKey: string;
  currentState: EdgeStateValue;
  surgeProgress: number;
  flashIntensity: number;
  transitionTime: number;
}

/** Uniform values for a single edge mesh */
export interface EdgeUniforms {
  state: EdgeStateValue;
  surgeProgress: number;
  flash: number;
}

const SURGE_DURATION = 0.8; // seconds
const FLASH_DURATION = 0.4; // seconds

/**
 * Manages per-edge animation state, mapping step run statuses to
 * shader uniform values. Stateful — tracks timing across frames.
 */
export class EdgeStateManager {
  private states = new Map<string, EdgeAnimState>();
  private prevRunStatuses = new Map<string, string>();

  private getOrCreate(key: string): EdgeAnimState {
    let s = this.states.get(key);
    if (!s) {
      s = {
        edgeKey: key,
        currentState: EdgeState.IDLE,
        surgeProgress: 0,
        flashIntensity: 0,
        transitionTime: 0,
      };
      this.states.set(key, s);
    }
    return s;
  }

  /**
   * Determine target state for an edge based on source/target step run status.
   * Precedence: active (SURGE) > completed > failed > idle
   */
  deriveTargetState(
    sourceStatus: string | undefined,
    targetStatus: string | undefined,
  ): EdgeStateValue {
    // Priority 1 (highest): target is active → SURGE/FLOW
    if (targetStatus === "running" || targetStatus === "delegated" || targetStatus === "suspended") {
      return EdgeState.SURGE; // will transition to FLOW after surge
    }
    // Priority 2: both source and target completed
    if (sourceStatus === "completed" && targetStatus === "completed") {
      return EdgeState.COMPLETED;
    }
    // Priority 3: either side failed
    if (targetStatus === "failed" || sourceStatus === "failed") {
      return EdgeState.FAILED;
    }
    // Priority 4: everything else
    return EdgeState.IDLE;
  }

  /**
   * Update all edge states for the current frame.
   * Returns a map of edgeKey -> uniform values.
   */
  update(
    edges: DagEdge[],
    loopEdges: LoopEdge[],
    latestRuns: Record<string, StepRun>,
    deltaTime: number,
  ): Map<string, EdgeUniforms> {
    const result = new Map<string, EdgeUniforms>();

    // Process data edges
    for (const edge of edges) {
      const key = `${edge.from}->${edge.to}`;
      const sourceStatus = latestRuns[edge.from]?.status;
      const targetStatus = latestRuns[edge.to]?.status;
      this.updateEdge(key, sourceStatus, targetStatus, edge.to, deltaTime);
      const s = this.states.get(key)!;
      result.set(key, {
        state: s.currentState,
        surgeProgress: s.surgeProgress,
        flash: s.flashIntensity,
      });
    }

    // Process loop edges (keyed with loopIndex for uniqueness)
    for (const le of loopEdges) {
      const key = `loop:${le.from}->${le.to}:${le.loopIndex}`;
      const sourceStatus = latestRuns[le.from]?.status;
      const targetStatus = latestRuns[le.to]?.status;
      this.updateEdge(key, sourceStatus, targetStatus, le.to, deltaTime);
      const s = this.states.get(key)!;
      result.set(key, {
        state: s.currentState,
        surgeProgress: s.surgeProgress,
        flash: s.flashIntensity,
      });
    }

    // Update previous run statuses
    this.prevRunStatuses.clear();
    for (const [name, run] of Object.entries(latestRuns)) {
      this.prevRunStatuses.set(name, run.status);
    }

    return result;
  }

  private updateEdge(
    key: string,
    sourceStatus: string | undefined,
    targetStatus: string | undefined,
    targetStep: string,
    deltaTime: number,
  ) {
    const s = this.getOrCreate(key);
    const targetState = this.deriveTargetState(sourceStatus, targetStatus);
    const prevTargetStatus = this.prevRunStatuses.get(targetStep);

    // Detect state transitions
    if (targetState !== s.currentState) {
      // If target just started running, always do surge
      if (
        targetState === EdgeState.SURGE &&
        s.currentState !== EdgeState.FLOW
      ) {
        // Only trigger surge if the step just began running
        if (prevTargetStatus !== targetStatus) {
          s.currentState = EdgeState.SURGE;
          s.surgeProgress = 0;
          s.transitionTime = 0;
        } else if (s.currentState === EdgeState.IDLE) {
          // Step is already running but we just appeared — go to flow
          s.currentState = EdgeState.FLOW;
        }
      } else if (targetState === EdgeState.COMPLETED || targetState === EdgeState.FAILED) {
        s.currentState = targetState;
        s.flashIntensity = 1.0;
        s.transitionTime = 0;
      } else if (targetState === EdgeState.IDLE) {
        s.currentState = EdgeState.IDLE;
      } else {
        s.currentState = targetState;
      }
    }

    // Animate current state
    s.transitionTime += deltaTime;

    if (s.currentState === EdgeState.SURGE) {
      s.surgeProgress += deltaTime / SURGE_DURATION;
      if (s.surgeProgress >= 1.0) {
        // Transition to flow
        s.currentState = EdgeState.FLOW;
        s.surgeProgress = 1.0;
      }
    }

    if (
      s.currentState === EdgeState.COMPLETED ||
      s.currentState === EdgeState.FAILED
    ) {
      s.flashIntensity = Math.max(0, s.flashIntensity - deltaTime / FLASH_DURATION);
    }
  }

  /** Check if any edge has an active animation (not all idle/settled). */
  hasActiveAnimations(): boolean {
    for (const s of this.states.values()) {
      if (s.currentState === EdgeState.SURGE) return true;
      if (s.currentState === EdgeState.FLOW) return true;
      if (s.flashIntensity > 0.01) return true;
    }
    return false;
  }

  /** Clean up edges that no longer exist. */
  cleanup(activeKeys: Set<string>): void {
    for (const key of this.states.keys()) {
      if (!activeKeys.has(key)) {
        this.states.delete(key);
      }
    }
  }

  /** Reset all state — call on unmount/navigation to prevent stale animation on remount. */
  reset(): void {
    this.states.clear();
    this.prevRunStatuses.clear();
  }
}
