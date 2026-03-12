import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { StepDefinitionPanel } from "../StepDefinitionPanel";
import type { StepDefinition } from "@/lib/types";

function makeStepDef(overrides: Partial<StepDefinition> = {}): StepDefinition {
  return {
    name: "analyze",
    outputs: ["summary"],
    executor: { type: "llm", config: { prompt: "Analyze data", model: "gpt-4o" }, decorators: [] },
    inputs: [{ local_name: "raw", source_step: "fetch", source_field: "data" }],
    sequencing: [],
    exit_rules: [],
    idempotency: "default",
    limits: null,
    ...overrides,
  };
}

describe("StepDefinitionPanel", () => {
  it("renders step name and executor type", () => {
    render(
      <StepDefinitionPanel
        stepDef={makeStepDef()}
        onClose={vi.fn()}
        onPatch={vi.fn()}
      />
    );
    expect(screen.getByText("analyze")).toBeInTheDocument();
    expect(screen.getByText("llm")).toBeInTheDocument();
  });

  it("shows prompt field for LLM executor", () => {
    render(
      <StepDefinitionPanel
        stepDef={makeStepDef()}
        onClose={vi.fn()}
        onPatch={vi.fn()}
      />
    );
    expect(screen.getByText("Prompt")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Analyze data")).toBeInTheDocument();
  });

  it("shows model field for LLM executor", () => {
    render(
      <StepDefinitionPanel
        stepDef={makeStepDef()}
        onClose={vi.fn()}
        onPatch={vi.fn()}
      />
    );
    expect(screen.getByText("Model")).toBeInTheDocument();
    expect(screen.getByDisplayValue("gpt-4o")).toBeInTheDocument();
  });

  it("shows run command for script executor", () => {
    render(
      <StepDefinitionPanel
        stepDef={makeStepDef({
          name: "fetch",
          executor: { type: "script", config: { command: "curl http://example.com" }, decorators: [] },
        })}
        onClose={vi.fn()}
        onPatch={vi.fn()}
      />
    );
    expect(screen.getByText("Run Command")).toBeInTheDocument();
    expect(screen.getByDisplayValue("curl http://example.com")).toBeInTheDocument();
  });

  it("shows prompt for human executor", () => {
    render(
      <StepDefinitionPanel
        stepDef={makeStepDef({
          name: "review",
          executor: { type: "human", config: { prompt: "Review the output" }, decorators: [] },
        })}
        onClose={vi.fn()}
        onPatch={vi.fn()}
      />
    );
    expect(screen.getByText("Prompt / Instructions")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Review the output")).toBeInTheDocument();
  });

  it("displays outputs as tags", () => {
    render(
      <StepDefinitionPanel
        stepDef={makeStepDef({ outputs: ["summary", "details"] })}
        onClose={vi.fn()}
        onPatch={vi.fn()}
      />
    );
    expect(screen.getByText("summary")).toBeInTheDocument();
    expect(screen.getByText("details")).toBeInTheDocument();
  });

  it("displays input bindings", () => {
    render(
      <StepDefinitionPanel
        stepDef={makeStepDef()}
        onClose={vi.fn()}
        onPatch={vi.fn()}
      />
    );
    expect(screen.getByText("raw")).toBeInTheDocument();
    expect(screen.getByText("fetch.data")).toBeInTheDocument();
  });

  it("calls onClose when close button clicked", () => {
    const onClose = vi.fn();
    render(
      <StepDefinitionPanel
        stepDef={makeStepDef()}
        onClose={onClose}
        onPatch={vi.fn()}
      />
    );
    // Find the X button (close)
    const buttons = screen.getAllByRole("button");
    const closeBtn = buttons.find(
      (b) => b.querySelector("svg.lucide-x") !== null
    );
    expect(closeBtn).toBeTruthy();
    fireEvent.click(closeBtn!);
    expect(onClose).toHaveBeenCalled();
  });

  it("shows delete button when onDelete provided", () => {
    const onDelete = vi.fn();
    render(
      <StepDefinitionPanel
        stepDef={makeStepDef()}
        onClose={vi.fn()}
        onPatch={vi.fn()}
        onDelete={onDelete}
      />
    );
    const deleteBtn = screen.getByTitle("Delete step");
    expect(deleteBtn).toBeInTheDocument();
    fireEvent.click(deleteBtn);
    expect(onDelete).toHaveBeenCalled();
  });

  it("hides delete button when onDelete not provided", () => {
    render(
      <StepDefinitionPanel
        stepDef={makeStepDef()}
        onClose={vi.fn()}
        onPatch={vi.fn()}
      />
    );
    expect(screen.queryByTitle("Delete step")).not.toBeInTheDocument();
  });

  it("calls onPatch with debounced prompt change", async () => {
    vi.useFakeTimers();
    const onPatch = vi.fn();
    render(
      <StepDefinitionPanel
        stepDef={makeStepDef()}
        onClose={vi.fn()}
        onPatch={onPatch}
      />
    );
    const promptField = screen.getByDisplayValue("Analyze data");
    fireEvent.change(promptField, { target: { value: "New prompt" } });
    // Should not have called yet (debounced)
    expect(onPatch).not.toHaveBeenCalled();
    // Advance past debounce
    vi.advanceTimersByTime(600);
    expect(onPatch).toHaveBeenCalledWith({ prompt: "New prompt" });
    vi.useRealTimers();
  });

  it("shows limits when present", () => {
    render(
      <StepDefinitionPanel
        stepDef={makeStepDef({
          limits: { max_cost_usd: 0.5, max_duration_minutes: 10, max_iterations: 3 },
        })}
        onClose={vi.fn()}
        onPatch={vi.fn()}
      />
    );
    expect(screen.getByText("Limits")).toBeInTheDocument();
    expect(screen.getByText("$0.5")).toBeInTheDocument();
    expect(screen.getByText("10m")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });

  it("shows exit rules when present", () => {
    render(
      <StepDefinitionPanel
        stepDef={makeStepDef({
          exit_rules: [
            {
              name: "check_done",
              type: "expression",
              config: { condition: "attempt >= 3", action: "escalate" },
              priority: 0,
            },
          ],
        })}
        onClose={vi.fn()}
        onPatch={vi.fn()}
      />
    );
    expect(screen.getByText("Exit Rules")).toBeInTheDocument();
    expect(screen.getByText("check_done")).toBeInTheDocument();
  });
});
