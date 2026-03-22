import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { FlowDagView } from "../FlowDagView";
import type { FlowDefinition } from "@/lib/types";

const minimalWorkflow: FlowDefinition = {
  steps: {
    hello: {
      name: "hello",
      description: "",
      outputs: [],
      executor: { type: "shell", config: {}, decorators: [] },
      inputs: [],
      sequencing: [],
      exit_rules: [],
      idempotency: "always",
      limits: null,
    },
  },
};

function renderDag() {
  return render(
    <FlowDagView
      workflow={minimalWorkflow}
      runs={[]}
      jobTree={null}
      expandedSteps={new Set()}
      onToggleExpand={vi.fn()}
      selectedStep={null}
      onSelectStep={vi.fn()}
    />,
  );
}

describe("FlowDagView touch support", () => {
  it("container has touch-none class", () => {
    const { container } = renderDag();
    const el = container.firstElementChild as HTMLElement;
    expect(el.className).toContain("touch-none");
  });

  it("touch events do not throw", () => {
    const { container } = renderDag();
    const el = container.firstElementChild as HTMLElement;

    expect(() => {
      fireEvent.touchStart(el, { touches: [{ clientX: 100, clientY: 100 }] });
      fireEvent.touchMove(el, { touches: [{ clientX: 110, clientY: 110 }] });
      fireEvent.touchEnd(el);
    }).not.toThrow();
  });
});
