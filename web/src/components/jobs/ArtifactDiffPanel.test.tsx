import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ArtifactDiffPanel } from "./ArtifactDiffPanel";
import type { StepRun } from "@/lib/types";

function makeRun(
  attempt: number,
  artifact: Record<string, unknown> | null
): StepRun {
  return {
    id: `run-${attempt}`,
    job_id: "job-1",
    step_name: "test-step",
    attempt,
    status: "completed",
    inputs: null,
    dep_run_ids: null,
    result: artifact
      ? {
          artifact,
          sidecar: {
            decisions_made: [],
            assumptions: [],
            open_questions: [],
            constraints_discovered: [],
          },
          executor_meta: {},
          workspace: "",
          timestamp: new Date().toISOString(),
        }
      : null,
    error: null,
    error_category: null,
    executor_state: null,
    watch: null,
    sub_job_id: null,
    started_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
  };
}

describe("ArtifactDiffPanel", () => {
  it("shows field tabs when outputs are declared", () => {
    const runs = [
      makeRun(1, { a: 1, b: 2 }),
      makeRun(2, { a: 10, b: 20 }),
    ];
    render(
      <ArtifactDiffPanel
        runs={runs}
        currentRun={runs[1]}
        outputs={["a", "b"]}
      />
    );
    expect(screen.getByText("All fields")).toBeInTheDocument();
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("b")).toBeInTheDocument();
  });

  it("defaults to comparing current vs previous attempt", () => {
    const runs = [
      makeRun(1, { x: "old" }),
      makeRun(2, { x: "new" }),
    ];
    // sorted desc like StepDetailPanel provides
    const sortedRuns = [...runs].sort((a, b) => b.attempt - a.attempt);
    render(
      <ArtifactDiffPanel runs={sortedRuns} currentRun={runs[1]} />
    );
    expect(screen.getByText("Attempt #1 vs #2")).toBeInTheDocument();
  });

  it("shows attempt selectors for 3+ runs with results", () => {
    const runs = [
      makeRun(1, { x: 1 }),
      makeRun(2, { x: 2 }),
      makeRun(3, { x: 3 }),
    ];
    const sortedRuns = [...runs].sort((a, b) => b.attempt - a.attempt);
    render(
      <ArtifactDiffPanel runs={sortedRuns} currentRun={runs[2]} />
    );
    // Should have two <select> elements
    const selects = screen.getAllByRole("combobox");
    expect(selects.length).toBe(2);
  });

  it("filters diff to single field when field tab clicked", async () => {
    const runs = [
      makeRun(1, { score: 0.5, text: "hello" }),
      makeRun(2, { score: 0.9, text: "hello" }),
    ];
    const sortedRuns = [...runs].sort((a, b) => b.attempt - a.attempt);
    render(
      <ArtifactDiffPanel
        runs={sortedRuns}
        currentRun={runs[1]}
        outputs={["score", "text"]}
      />
    );

    // Click the "text" field tab — text is identical so should show identical message
    await userEvent.click(screen.getByText("text"));
    expect(screen.getByText("Outputs are identical")).toBeInTheDocument();

    // Click score tab — should show diff
    await userEvent.click(screen.getByText("score"));
    expect(screen.queryByText("Outputs are identical")).not.toBeInTheDocument();
  });

  it("does not show field tabs when only one output", () => {
    const runs = [
      makeRun(1, { result: "a" }),
      makeRun(2, { result: "b" }),
    ];
    const sortedRuns = [...runs].sort((a, b) => b.attempt - a.attempt);
    render(
      <ArtifactDiffPanel
        runs={sortedRuns}
        currentRun={runs[1]}
        outputs={["result"]}
      />
    );
    expect(screen.queryByText("All fields")).not.toBeInTheDocument();
  });
});
