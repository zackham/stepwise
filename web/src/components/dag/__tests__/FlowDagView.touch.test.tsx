import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FlowDagView } from "../FlowDagView";
import type { FlowDefinition } from "@/lib/types";

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const minimalWorkflow: FlowDefinition = {
  steps: {
    hello: {
      name: "hello",
      description: "",
      outputs: [],
      executor: { type: "shell", config: {}, decorators: [] },
      inputs: [],
      after: [],
      exit_rules: [],
      idempotency: "always",
      limits: null,
    },
  },
};

function renderDag() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <FlowDagView
        workflow={minimalWorkflow}
        runs={[]}
        jobTree={null}
        expandedSteps={new Set()}
        onToggleExpand={vi.fn()}
        selectedStep={null}
        onSelectStep={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

describe("FlowDagView touch support", () => {
  it("container has touch-none class", () => {
    const { container } = renderDag();
    const el = container.querySelector(".touch-none") as HTMLElement;
    expect(el).toBeTruthy();
  });

  it("touch events do not throw", () => {
    const { container } = renderDag();
    const el = container.querySelector(".touch-none") as HTMLElement ?? container.firstElementChild as HTMLElement;

    expect(() => {
      fireEvent.touchStart(el, { touches: [{ clientX: 100, clientY: 100 }] });
      fireEvent.touchMove(el, { touches: [{ clientX: 110, clientY: 110 }] });
      fireEvent.touchEnd(el);
    }).not.toThrow();
  });
});
