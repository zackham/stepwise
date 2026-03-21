import { render, screen, fireEvent } from "@testing-library/react";
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
  it("renders step name and executor type badge", () => {
    renderWithQuery(
      <StepDefinitionPanel
        stepDef={makeStepDef()}
        onClose={vi.fn()}
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
      />
    );
    expect(screen.getByText("Prompt")).toBeInTheDocument();
    expect(screen.getByText("Analyze data")).toBeInTheDocument();
  });

  it("shows model as read-only text for LLM executor", () => {
    renderWithQuery(
      <StepDefinitionPanel
        stepDef={makeStepDef()}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("Model")).toBeInTheDocument();
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
  });

  it("shows run command as read-only pre block for script executor", () => {
    renderWithQuery(
      <StepDefinitionPanel
        stepDef={makeStepDef({
          name: "fetch",
          executor: { type: "script", config: { command: "curl http://example.com" }, decorators: [] },
        })}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("curl http://example.com")).toBeInTheDocument();
  });

  it("shows prompt for external executor", () => {
    renderWithQuery(
      <StepDefinitionPanel
        stepDef={makeStepDef({
          name: "review",
          executor: { type: "external", config: { prompt: "Review the output" }, decorators: [] },
        })}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("Prompt / Instructions")).toBeInTheDocument();
    expect(screen.getByText("Review the output")).toBeInTheDocument();
  });

  it("displays outputs as read-only tags", () => {
    renderWithQuery(
      <StepDefinitionPanel
        stepDef={makeStepDef({ outputs: ["summary", "details"] })}
        onClose={vi.fn()}
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
      />
    );
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
      />
    );
    expect(screen.queryByTitle("Delete step")).not.toBeInTheDocument();
  });

  it("shows limits when present", () => {
    renderWithQuery(
      <StepDefinitionPanel
        stepDef={makeStepDef({
          limits: { max_cost_usd: 0.5, max_duration_minutes: 10, max_iterations: 3 },
        })}
        onClose={vi.fn()}
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
      />
    );
    expect(screen.getByText("check_done")).toBeInTheDocument();
    expect(screen.getByText("→ escalate")).toBeInTheDocument();
  });

  it("shows description when present", () => {
    renderWithQuery(
      <StepDefinitionPanel
        stepDef={makeStepDef({ description: "Analyze raw data for insights" })}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("Analyze raw data for insights")).toBeInTheDocument();
  });

  it("shows emit_flow badge when set", () => {
    renderWithQuery(
      <StepDefinitionPanel
        stepDef={makeStepDef({
          executor: { type: "agent", config: { prompt: "Do it", emit_flow: true }, decorators: [] },
        })}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("emit_flow")).toBeInTheDocument();
  });

  it("shows decorators section when decorators present", () => {
    renderWithQuery(
      <StepDefinitionPanel
        stepDef={makeStepDef({
          executor: {
            type: "llm",
            config: { prompt: "Test", model: "gpt-4o" },
            decorators: [
              { type: "timeout", config: { timeout_minutes: 30 } },
              { type: "retry", config: { max_retries: 3, backoff: "exponential" } },
            ],
          },
        })}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("Decorators")).toBeInTheDocument();
    expect(screen.getByText("30m")).toBeInTheDocument();
    expect(screen.getByText("max 3")).toBeInTheDocument();
    expect(screen.getByText("exponential backoff")).toBeInTheDocument();
  });

  it("shows for_each badge and details when present", () => {
    renderWithQuery(
      <StepDefinitionPanel
        stepDef={makeStepDef({
          for_each: { source_step: "fetch", source_field: "items", item_var: "item", on_error: "fail_fast" },
        })}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("for_each")).toBeInTheDocument();
    expect(screen.getByText("fetch.items")).toBeInTheDocument();
    expect(screen.getByText("item")).toBeInTheDocument();
    expect(screen.getByText("fail_fast")).toBeInTheDocument();
  });

  it("shows poll executor config", () => {
    renderWithQuery(
      <StepDefinitionPanel
        stepDef={makeStepDef({
          name: "wait",
          executor: {
            type: "poll",
            config: { check_command: "gh pr view --json status", interval_seconds: 30, prompt: "Waiting for review" },
            decorators: [],
          },
        })}
        onClose={vi.fn()}
      />
    );
    expect(screen.getByText("poll")).toBeInTheDocument();
    expect(screen.getByText("gh pr view --json status")).toBeInTheDocument();
    expect(screen.getByText(/Every 30s/)).toBeInTheDocument();
    expect(screen.getByText("Waiting for review")).toBeInTheDocument();
  });
});
