import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useAutoSelectSuspended } from "../useAutoSelectSuspended";
import type { StepRun } from "@/lib/types";

function makeRun(overrides: Partial<StepRun> = {}): StepRun {
  return {
    id: "run-1",
    job_id: "job-1",
    step_name: "step-a",
    attempt: 1,
    status: "running",
    inputs: null,
    dep_run_ids: null,
    result: null,
    error: null,
    error_category: null,
    executor_state: null,
    watch: null,
    sub_job_id: null,
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

describe("useAutoSelectSuspended", () => {
  it("auto-selects first newly suspended human step when nothing selected", () => {
    const onSelect = vi.fn();
    const runs: StepRun[] = [
      makeRun({
        id: "run-1",
        step_name: "review",
        status: "suspended",
        watch: {
          mode: "human",
          config: {},
          fulfillment_outputs: ["decision"],
        },
      }),
    ];

    renderHook(() => useAutoSelectSuspended(runs, null, onSelect));

    expect(onSelect).toHaveBeenCalledWith("review");
  });

  it("does not auto-select when something is already selected", () => {
    const onSelect = vi.fn();
    const runs: StepRun[] = [
      makeRun({
        id: "run-1",
        step_name: "review",
        status: "suspended",
        watch: {
          mode: "human",
          config: {},
          fulfillment_outputs: ["decision"],
        },
      }),
    ];

    renderHook(() =>
      useAutoSelectSuspended(
        runs,
        { kind: "step", stepName: "other" },
        onSelect,
      ),
    );

    expect(onSelect).not.toHaveBeenCalled();
  });

  it("does not re-trigger for already-known suspended steps", () => {
    const onSelect = vi.fn();
    const runs: StepRun[] = [
      makeRun({
        id: "run-1",
        step_name: "review",
        status: "suspended",
        watch: {
          mode: "human",
          config: {},
          fulfillment_outputs: ["decision"],
        },
      }),
    ];

    const { rerender } = renderHook(
      ({ r, sel }) => useAutoSelectSuspended(r, sel, onSelect),
      { initialProps: { r: runs, sel: null as null } },
    );

    expect(onSelect).toHaveBeenCalledTimes(1);

    // Re-render with same runs but now something is selected
    rerender({ r: runs, sel: { kind: "step", stepName: "review" } as const });

    // Back to null selection — should NOT auto-select since step is already known
    rerender({ r: runs, sel: null });

    // Still only 1 call
    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});
