import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { StepNode } from "./StepNode";
import type { StepDefinition, StepRun } from "@/lib/types";

function makeStepDef(overrides: Partial<StepDefinition> = {}): StepDefinition {
  return {
    name: "test_step",
    description: "",
    outputs: ["result"],
    executor: { type: "script", config: {}, decorators: [] },
    inputs: [],
    after: [],
    exit_rules: [],
    idempotency: "idempotent",
    limits: null,
    ...overrides,
  };
}

function makeRun(overrides: Partial<StepRun> = {}): StepRun {
  return {
    id: "run-1",
    job_id: "job-1",
    step_name: "test_step",
    attempt: 1,
    status: "completed",
    inputs: null,
    dep_run_ids: null,
    result: null,
    error: null,
    error_category: null,
    traceback: null,
    executor_state: null,
    watch: null,
    sub_job_id: null,
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

const defaultProps = {
  maxAttempts: null as number | null,
  isSelected: false,
  onClick: vi.fn(),
  x: 0,
  y: 0,
  width: 200,
  height: 72,
};

describe("StepNode", () => {
  it("renders the step name", () => {
    render(
      <StepNode
        stepDef={makeStepDef({ name: "my_step" })}
        latestRun={null}
        {...defaultProps}
      />
    );
    expect(screen.getByText("my_step")).toBeInTheDocument();
  });

  it("shows 'pending' badge when no run exists", () => {
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={null}
        {...defaultProps}
      />
    );
    expect(screen.getByText("pending")).toBeInTheDocument();
  });

  it("shows the run status badge when a run exists", () => {
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={makeRun({ status: "completed" })}
        {...defaultProps}
      />
    );
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("shows the failed status badge", () => {
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={makeRun({ status: "failed" })}
        {...defaultProps}
      />
    );
    expect(screen.getByText("failed")).toBeInTheDocument();
  });

  it("shows 'Awaiting fulfillment' indicator for external-suspended steps", () => {
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={makeRun({
          status: "suspended",
          watch: {
            mode: "external",
            config: { prompt: "Please confirm" },
            fulfillment_outputs: ["confirmed"],
          },
        })}
        {...defaultProps}
      />
    );
    expect(screen.getByText("Awaiting fulfillment")).toBeInTheDocument();
  });

  it("does not show 'Awaiting fulfillment' for non-external suspended steps", () => {
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={makeRun({
          status: "suspended",
          watch: {
            mode: "poll",
            config: {},
            fulfillment_outputs: [],
          },
        })}
        {...defaultProps}
      />
    );
    expect(screen.queryByText("Awaiting fulfillment")).toBeNull();
  });

  it("calls onClick when clicked", () => {
    const onClick = vi.fn();
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={null}
        {...defaultProps}
        onClick={onClick}
      />
    );

    const element = screen.getByText("test_step").closest("[class*='cursor-pointer']");
    if (element) fireEvent.click(element);

    expect(onClick).toHaveBeenCalledOnce();
  });

  it("calls onClick on Enter key", () => {
    const onClick = vi.fn();
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={null}
        {...defaultProps}
        onClick={onClick}
      />
    );

    const node = screen.getByRole("button");
    fireEvent.keyDown(node, { key: "Enter" });

    expect(onClick).toHaveBeenCalledOnce();
  });

  it("calls onClick on Space key", () => {
    const onClick = vi.fn();
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={null}
        {...defaultProps}
        onClick={onClick}
      />
    );

    const node = screen.getByRole("button");
    fireEvent.keyDown(node, { key: " " });

    expect(onClick).toHaveBeenCalledOnce();
  });

  it("does not call onClick on other keys", () => {
    const onClick = vi.fn();
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={null}
        {...defaultProps}
        onClick={onClick}
      />
    );

    const node = screen.getByRole("button");
    fireEvent.keyDown(node, { key: "Tab" });
    fireEvent.keyDown(node, { key: "a" });

    expect(onClick).not.toHaveBeenCalled();
  });

  it("has focus-visible ring class for keyboard focus", () => {
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={null}
        {...defaultProps}
      />
    );

    const node = screen.getByRole("button");
    expect(node.className).toContain("focus-visible:ring-2");
  });

  it("shows attempt N/M badge when maxAttempts is set", () => {
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={makeRun({ attempt: 3 })}
        {...defaultProps}
        maxAttempts={10}
      />
    );
    expect(screen.getByText("3/10")).toBeInTheDocument();
  });

  it("shows attempt badge without denominator when maxAttempts is null", () => {
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={makeRun({ attempt: 5 })}
        {...defaultProps}
        maxAttempts={null}
      />
    );
    expect(screen.getByText("5")).toBeInTheDocument();
  });

  it("shows attempt 1/M badge for looping steps on first attempt", () => {
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={makeRun({ attempt: 1 })}
        {...defaultProps}
        maxAttempts={5}
      />
    );
    expect(screen.getByText("1/5")).toBeInTheDocument();
  });

  it("renders different executor icons based on type", () => {
    const { container: scriptContainer } = render(
      <StepNode
        stepDef={makeStepDef({ executor: { type: "script", config: {}, decorators: [] } })}
        latestRun={null}
        {...defaultProps}
      />
    );
    // Just verify it renders without crashing with each type
    expect(scriptContainer.firstChild).toBeTruthy();

    const { container: externalContainer } = render(
      <StepNode
        stepDef={makeStepDef({ executor: { type: "external", config: {}, decorators: [] } })}
        latestRun={null}
        {...defaultProps}
      />
    );
    expect(externalContainer.firstChild).toBeTruthy();

    const { container: llmContainer } = render(
      <StepNode
        stepDef={makeStepDef({ executor: { type: "mock_llm", config: {}, decorators: [] } })}
        latestRun={null}
        {...defaultProps}
      />
    );
    expect(llmContainer.firstChild).toBeTruthy();

    const { container: unknownContainer } = render(
      <StepNode
        stepDef={makeStepDef({ executor: { type: "custom_thing", config: {}, decorators: [] } })}
        latestRun={null}
        {...defaultProps}
      />
    );
    expect(unknownContainer.firstChild).toBeTruthy();
  });

  // ── Escalation / Stranded / Parallel-start annotations ────────────

  it("shows ESCALATED badge when latest exit rule action is escalate", () => {
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={makeRun({
          status: "completed",
          exit_rule: { rule: "still_broken", action: "escalate", at: null },
        })}
        {...defaultProps}
      />
    );
    expect(screen.getByText("escalated")).toBeInTheDocument();
    // Must NOT also render the underlying "completed" badge — the
    // escalated state replaces it so users don't misread a completed
    // badge as "fine, advancing."
    expect(screen.queryByText("completed")).toBeNull();
  });

  it("includes the escalating rule name in the tile body", () => {
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={makeRun({
          status: "completed",
          exit_rule: { rule: "still_broken", action: "escalate", at: null },
        })}
        {...defaultProps}
      />
    );
    expect(screen.getByText(/escalated via rule:/i)).toBeInTheDocument();
    expect(screen.getByText(/still_broken/)).toBeInTheDocument();
  });

  it("shows STRANDED badge + annotation when is_stranded is true", () => {
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={makeRun({ status: "running", is_stranded: true })}
        {...defaultProps}
      />
    );
    expect(screen.getByText("stranded")).toBeInTheDocument();
    expect(screen.getByText(/stranded — job paused/i)).toBeInTheDocument();
  });

  it("STRANDED wins over ESCALATED when both conditions somehow coincide", () => {
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={makeRun({
          status: "running",
          is_stranded: true,
          exit_rule: { rule: "whatever", action: "escalate", at: null },
        })}
        {...defaultProps}
      />
    );
    expect(screen.getByText("stranded")).toBeInTheDocument();
    expect(screen.queryByText("escalated")).toBeNull();
  });

  it("does NOT show escalated badge for advance/loop actions", () => {
    render(
      <StepNode
        stepDef={makeStepDef()}
        latestRun={makeRun({
          status: "completed",
          exit_rule: { rule: "all_clear", action: "advance", at: null },
        })}
        {...defaultProps}
      />
    );
    expect(screen.queryByText("escalated")).toBeNull();
    expect(screen.getByText("completed")).toBeInTheDocument();
  });

  it("shows parallel-with annotation when sibling intervals genuinely overlap", () => {
    // Both steps ran from ~T to ~T+5s — full interval overlap.
    const startA = "2026-04-23T19:35:03.790Z";
    const endA = "2026-04-23T19:35:08.790Z";
    const startB = "2026-04-23T19:35:03.795Z"; // 5ms after A
    const endB = "2026-04-23T19:35:08.500Z";
    render(
      <StepNode
        stepDef={makeStepDef({ name: "text-quality-check" })}
        latestRun={makeRun({
          step_name: "text-quality-check",
          status: "completed",
          started_at: startA,
          completed_at: endA,
        })}
        latestRuns={{
          "text-quality-check": makeRun({
            step_name: "text-quality-check",
            status: "completed",
            started_at: startA,
            completed_at: endA,
          }),
          "initial-check": makeRun({
            id: "run-sibling",
            step_name: "initial-check",
            status: "completed",
            started_at: startB,
            completed_at: endB,
          }),
        }}
        {...defaultProps}
      />
    );
    expect(screen.getByText(/parallel with/i)).toBeInTheDocument();
    expect(screen.getByText(/initial-check/)).toBeInTheDocument();
  });

  it("does NOT show parallel annotation when siblings ran sequentially", () => {
    // Linear chain: A finishes in 70ms, B starts immediately and runs
    // for 18s. Their start timestamps are within 250ms but their
    // execution intervals do not overlap — must NOT be flagged.
    const startA = "2026-04-23T19:30:00.000Z";
    const endA = "2026-04-23T19:30:00.070Z";
    const startB = "2026-04-23T19:30:00.075Z"; // 5ms after A finished
    const endB = "2026-04-23T19:30:18.275Z";
    render(
      <StepNode
        stepDef={makeStepDef({ name: "load-prompt" })}
        latestRun={makeRun({
          step_name: "load-prompt",
          status: "completed",
          started_at: startA,
          completed_at: endA,
        })}
        latestRuns={{
          "load-prompt": makeRun({
            step_name: "load-prompt",
            status: "completed",
            started_at: startA,
            completed_at: endA,
          }),
          generate: makeRun({
            id: "run-gen",
            step_name: "generate",
            status: "completed",
            started_at: startB,
            completed_at: endB,
          }),
        }}
        {...defaultProps}
      />
    );
    expect(screen.queryByText(/parallel with/i)).toBeNull();
  });

  it("does NOT show parallel annotation when siblings ran far apart", () => {
    render(
      <StepNode
        stepDef={makeStepDef({ name: "a" })}
        latestRun={makeRun({
          step_name: "a",
          status: "completed",
          started_at: "2026-04-23T19:30:00.000Z",
          completed_at: "2026-04-23T19:30:01.000Z",
        })}
        latestRuns={{
          a: makeRun({
            step_name: "a",
            status: "completed",
            started_at: "2026-04-23T19:30:00.000Z",
            completed_at: "2026-04-23T19:30:01.000Z",
          }),
          b: makeRun({
            id: "run-b",
            step_name: "b",
            status: "completed",
            started_at: "2026-04-23T19:40:00.000Z", // 10 min later
            completed_at: "2026-04-23T19:40:01.000Z",
          }),
        }}
        {...defaultProps}
      />
    );
    expect(screen.queryByText(/parallel with/i)).toBeNull();
  });
});
