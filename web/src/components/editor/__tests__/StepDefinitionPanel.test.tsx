import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import type { ReactNode } from "react";
import { StepDefinitionPanel } from "../StepDefinitionPanel";
import type { StepDefinition } from "@/lib/types";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function renderWithQuery(ui: React.ReactElement) {
  return render(ui, { wrapper: createWrapper() });
}

function makeStepDef(overrides: Partial<StepDefinition> = {}): StepDefinition {
  return {
    name: "analyze",
    description: "",
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
    renderWithQuery(
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
    renderWithQuery(
      <StepDefinitionPanel
        stepDef={makeStepDef()}
        onClose={vi.fn()}
        onPatch={vi.fn()}
      />
    );
    expect(screen.getByText("Prompt")).toBeInTheDocument();
    // Prompt is rendered read-only via PromptPreview
    expect(screen.getByText("Analyze data")).toBeInTheDocument();
  });

  it("shows model field for LLM executor", () => {
    renderWithQuery(
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
    renderWithQuery(
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
    renderWithQuery(
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
    // Prompt is rendered read-only via PromptPreview
    expect(screen.getByText("Review the output")).toBeInTheDocument();
  });

  it("displays outputs as tags", () => {
    renderWithQuery(
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
    renderWithQuery(
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
    renderWithQuery(
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
    renderWithQuery(
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
    renderWithQuery(
      <StepDefinitionPanel
        stepDef={makeStepDef()}
        onClose={vi.fn()}
        onPatch={vi.fn()}
      />
    );
    expect(screen.queryByTitle("Delete step")).not.toBeInTheDocument();
  });

  it("calls onPatch when model changes via custom input", async () => {
    vi.useFakeTimers();
    const onPatch = vi.fn();
    renderWithQuery(
      <StepDefinitionPanel
        stepDef={makeStepDef()}
        onClose={vi.fn()}
        onPatch={onPatch}
      />
    );
    // Switch to custom input mode
    fireEvent.click(screen.getByTitle("Type a custom model ID"));
    const modelInput = screen.getByPlaceholderText("provider/model-id");
    fireEvent.change(modelInput, { target: { value: "claude-sonnet-4" } });
    expect(onPatch).toHaveBeenCalledWith({ model: "claude-sonnet-4" });
    vi.useRealTimers();
  });

  it("shows limits when present", () => {
    renderWithQuery(
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
    renderWithQuery(
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
