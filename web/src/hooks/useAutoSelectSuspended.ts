import { useRef, useEffect } from "react";
import type { StepRun } from "@/lib/types";
import type { DagSelection } from "@/lib/dag-layout";

/**
 * Auto-select the first newly-suspended human step when nothing is selected.
 *
 * Tracks the set of suspended step names across renders. When a new step
 * enters suspended state and the current selection is null, calls
 * onSelectStep with the first new step.
 */
export function useAutoSelectSuspended(
  runs: StepRun[],
  selection: DagSelection,
  onSelectStep: (stepName: string) => void,
) {
  const prevSuspendedRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const currentSuspended = new Set(
      runs
        .filter(
          (r) =>
            r.status === "suspended" &&
            r.watch?.mode === "human",
        )
        .map((r) => r.step_name),
    );

    const newlySuspended: string[] = [];
    for (const name of currentSuspended) {
      if (!prevSuspendedRef.current.has(name)) {
        newlySuspended.push(name);
      }
    }

    prevSuspendedRef.current = currentSuspended;

    if (selection === null && newlySuspended.length > 0) {
      onSelectStep(newlySuspended[0]);
    }
  }, [runs, selection, onSelectStep]);
}
