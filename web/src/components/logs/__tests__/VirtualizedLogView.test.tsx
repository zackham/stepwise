import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { VirtualizedLogView } from "../VirtualizedLogView";

describe("VirtualizedLogView", () => {
  // --- Below-threshold (flat rendering, VIRTUAL_THRESHOLD = 100) ---

  it("renders nothing for empty lines array", () => {
    const { container } = render(<VirtualizedLogView lines={[]} />);
    expect(container.querySelectorAll("[data-line]").length).toBe(0);
  });

  it("renders all lines directly when below threshold", () => {
    const lines = Array.from({ length: 10 }, (_, i) => `line ${i}`);
    render(<VirtualizedLogView lines={lines} />);
    for (let i = 0; i < 10; i++) {
      expect(screen.getByText(`line ${i}`)).toBeInTheDocument();
    }
  });

  it("renders single line correctly", () => {
    render(<VirtualizedLogView lines={["hello world"]} />);
    expect(screen.getByText("hello world")).toBeInTheDocument();
  });

  it("invokes renderLine callback for each visible line", () => {
    const renderLine = vi.fn((line: string) => line.toUpperCase());
    const lines = ["alpha", "beta", "gamma"];
    render(<VirtualizedLogView lines={lines} renderLine={renderLine} />);
    expect(renderLine).toHaveBeenCalledTimes(3);
    expect(renderLine).toHaveBeenCalledWith("alpha", 0);
    expect(renderLine).toHaveBeenCalledWith("beta", 1);
    expect(renderLine).toHaveBeenCalledWith("gamma", 2);
    expect(screen.getByText("ALPHA")).toBeInTheDocument();
  });

  it("applies className prop to container", () => {
    const { container } = render(
      <VirtualizedLogView lines={["test"]} className="font-mono text-xs" />
    );
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.className).toContain("font-mono");
  });

  it("handles lines with empty strings", () => {
    const lines = ["line 1", "", "line 3"];
    render(
      <VirtualizedLogView
        lines={lines}
        renderLine={(line) => (line === "" ? "\u00A0" : line)}
      />
    );
    expect(screen.getByText("line 1")).toBeInTheDocument();
    expect(screen.getByText("line 3")).toBeInTheDocument();
  });

  // --- Above-threshold (virtualizer engaged) ---
  // jsdom doesn't provide real layout metrics, so useVirtualizer renders 0
  // visible items. We test that the virtualizer container structure is created.

  it("uses virtualizer structure for large line counts", () => {
    const lines = Array.from({ length: 500 }, (_, i) => `line ${i}`);
    const { container } = render(<VirtualizedLogView lines={lines} />);
    const allDivs = container.querySelectorAll("div");
    // Should NOT have 500+ divs — virtualizer limits DOM nodes
    expect(allDivs.length).toBeLessThan(100);
  });
});
