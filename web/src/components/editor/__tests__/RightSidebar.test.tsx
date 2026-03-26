import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import type { ReactNode } from "react";
import { RightSidebar } from "../RightSidebar";
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
    after: [],
    exit_rules: [],
    idempotency: "default",
    limits: null,
    ...overrides,
  };
}

const defaultChatProps = {
  chatMessages: [],
  isChatStreaming: false,
  onChatSend: vi.fn(),
  onChatReset: vi.fn(),
  onApplyYaml: vi.fn(),
  agentMode: "claude" as const,
  onModeChange: vi.fn(),
  sessionId: null,
  flowPath: null,
  stepContext: null,
  onRemoveStepContext: vi.fn(),
};

describe("RightSidebar", () => {
  it("renders Inspector tab content when activeTab=inspector and step selected", () => {
    renderWithQuery(
      <RightSidebar
        activeTab="inspector"
        onTabChange={vi.fn()}
        selectedStepDef={makeStepDef()}
        onCloseInspector={vi.fn()}
        {...defaultChatProps}
      />
    );
    expect(screen.getByText("analyze")).toBeDefined();
  });

  it("renders Chat tab content when activeTab=chat", () => {
    renderWithQuery(
      <RightSidebar
        activeTab="chat"
        onTabChange={vi.fn()}
        selectedStepDef={null}
        onCloseInspector={vi.fn()}
        {...defaultChatProps}
      />
    );
    expect(screen.getByText("Chat")).toBeDefined();
    expect(screen.getByPlaceholderText("Ask AI to modify this flow...")).toBeDefined();
  });

  it("shows placeholder when inspector tab active but no step selected", () => {
    renderWithQuery(
      <RightSidebar
        activeTab="inspector"
        onTabChange={vi.fn()}
        selectedStepDef={null}
        onCloseInspector={vi.fn()}
        {...defaultChatProps}
      />
    );
    expect(screen.getByText("Select a step to inspect")).toBeDefined();
  });

  it("calls onTabChange when clicking tab", () => {
    const onTabChange = vi.fn();
    renderWithQuery(
      <RightSidebar
        activeTab="chat"
        onTabChange={onTabChange}
        selectedStepDef={makeStepDef()}
        onCloseInspector={vi.fn()}
        {...defaultChatProps}
      />
    );
    fireEvent.click(screen.getByText("Inspector"));
    expect(onTabChange).toHaveBeenCalledWith("inspector");
  });

  it("shows dot indicator on Inspector tab when step selected but chat active", () => {
    const { container } = renderWithQuery(
      <RightSidebar
        activeTab="chat"
        onTabChange={vi.fn()}
        selectedStepDef={makeStepDef()}
        onCloseInspector={vi.fn()}
        {...defaultChatProps}
      />
    );
    // The blue dot indicator next to "Inspector" text
    const dot = container.querySelector(".bg-blue-500");
    expect(dot).not.toBeNull();
  });

  it("shows streaming indicator on Chat tab when streaming", () => {
    const { container } = renderWithQuery(
      <RightSidebar
        activeTab="inspector"
        onTabChange={vi.fn()}
        selectedStepDef={makeStepDef()}
        onCloseInspector={vi.fn()}
        {...defaultChatProps}
        isChatStreaming={true}
      />
    );
    const dot = container.querySelector(".bg-violet-500");
    expect(dot).not.toBeNull();
  });
});
