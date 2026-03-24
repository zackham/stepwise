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

  it("shows 'Awaiting input' indicator for external-suspended steps", () => {
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
    expect(screen.getByText("Awaiting input")).toBeInTheDocument();
  });

  it("does not show 'Awaiting input' for non-external suspended steps", () => {
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
    expect(screen.queryByText("Awaiting input")).toBeNull();
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
});
