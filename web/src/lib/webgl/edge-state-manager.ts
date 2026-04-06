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
  flowAge: number;
}

/** Uniform values for a single edge mesh */
export interface EdgeUniforms {
  state: EdgeStateValue;
  surgeProgress: number;
  flash: number;
  flowAge: number;
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
        flowAge: 0,
      };
      this.states.set(key, s);
    }
    return s;
  }

  /**
   * Determine target state for an edge based on source/target step run status.
   * Loop edges require source to have completed/failed before activating
   * (matching SVG ant-march behavior).
   */
  deriveTargetState(
    sourceStatus: string | undefined,
    targetStatus: string | undefined,
    isLoop: boolean = false,
  ): EdgeStateValue {
    const targetActive = targetStatus === "running" || targetStatus === "delegated" || targetStatus === "suspended";

    if (isLoop) {
      // Loop edges only pulse when source has run AND target is active
      const sourceHasRun = sourceStatus === "completed" || sourceStatus === "failed";
      if (sourceHasRun && targetActive) {
        return EdgeState.SURGE;
      }
      return EdgeState.IDLE;
    }

    // Data edges — only pulse if source has actually executed (prevents false
    // pulse for any_of edges from unexecuted loopback sources)
    const sourceHasRun = sourceStatus === "completed" || sourceStatus === "failed";
    if (sourceHasRun && targetActive) {
      return EdgeState.SURGE;
    }
    if (sourceStatus === "completed" && targetStatus === "completed") {
      return EdgeState.COMPLETED;
    }
    if (targetStatus === "failed" || sourceStatus === "failed") {
      return EdgeState.FAILED;
    }
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
      this.updateEdge(key, sourceStatus, targetStatus, edge.to, deltaTime, false);
      const s = this.states.get(key)!;
      result.set(key, {
        state: s.currentState,
        surgeProgress: s.surgeProgress,
        flash: s.flashIntensity,
        flowAge: s.flowAge,
      });
    }

    // Process loop edges (keyed with loopIndex for uniqueness)
    for (const le of loopEdges) {
      const key = `loop:${le.from}->${le.to}:${le.loopIndex}`;
      const sourceStatus = latestRuns[le.from]?.status;
      const targetStatus = latestRuns[le.to]?.status;
      this.updateEdge(key, sourceStatus, targetStatus, le.to, deltaTime, true);
      const s = this.states.get(key)!;
      result.set(key, {
        state: s.currentState,
        surgeProgress: s.surgeProgress,
        flash: s.flashIntensity,
        flowAge: s.flowAge,
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
    isLoop: boolean,
  ) {
    const s = this.getOrCreate(key);
    const targetState = this.deriveTargetState(sourceStatus, targetStatus, isLoop);
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
        // Only flash if this is a real transition (not initial page load)
        const isRealTransition = prevTargetStatus !== undefined && prevTargetStatus !== targetStatus;
        if (isRealTransition) {
          s.currentState = targetState;
          s.flashIntensity = 1.0;
          s.transitionTime = 0;
        } else {
          // Already settled — go straight to IDLE (invisible)
          s.currentState = EdgeState.IDLE;
        }
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
        s.currentState = EdgeState.FLOW;
        s.surgeProgress = 1.0;
        s.flowAge = 0; // start warmup from zero
      }
    }

    if (s.currentState === EdgeState.FLOW) {
      s.flowAge += deltaTime;
    }

    if (
      s.currentState === EdgeState.COMPLETED ||
      s.currentState === EdgeState.FAILED
    ) {
      s.flashIntensity = Math.max(0, s.flashIntensity - deltaTime / FLASH_DURATION);
      // Once flash fades, go to IDLE so WebGL renders nothing (SVG handles settled state)
      if (s.flashIntensity <= 0) {
        s.currentState = EdgeState.IDLE;
      }
    }
  }

  /** Check if any edge has an active animation (not all idle/settled). */
  hasActiveAnimations(): boolean {
    for (const s of this.states.values()) {
      if (s.currentState === EdgeState.SURGE) return true;
      if (s.currentState === EdgeState.FLOW) return true; // includes warmup
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
