import { describe, it, expect, vi, afterEach } from "vitest";
import { render } from "@testing-library/react";
import type { DagEdge, LoopEdge } from "@/lib/dag-layout";

const edges: DagEdge[] = [
  {
    from: "a",
    to: "b",
    points: [
      { x: 10, y: 10 },
      { x: 50, y: 50 },
    ],
    labels: [],
  },
];

const loopEdges: LoopEdge[] = [];

afterEach(() => {
  vi.restoreAllMocks();
  vi.resetModules();
  Reflect.deleteProperty(globalThis, "OffscreenCanvas");
});

describe("DagEdges", () => {
  it("uses canvas measureText for label width when available", async () => {
    const measureText = vi.fn(() => ({ width: 101.2 }));

    class MockOffscreenCanvas {
      getContext() {
        return {
          font: "",
          measureText,
        };
      }
    }

    Object.defineProperty(globalThis, "OffscreenCanvas", {
      configurable: true,
      value: MockOffscreenCanvas,
    });

    const { DagEdges } = await import("./DagEdges");
    const { container } = render(
      <DagEdges
        edges={[{ ...edges[0], labels: ["measured_label"] }]}
        loopEdges={loopEdges}
        width={100}
        height={100}
      />,
    );

    const rect = container.querySelector("rect");
    expect(measureText).toHaveBeenCalledWith("measured_label");
    expect(rect).toHaveAttribute("width", "114");
  });

  it("falls back to the char-width estimate when canvas context is unavailable", async () => {
    const originalCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation(((tagName: string, options?: ElementCreationOptions) => {
      if (tagName === "canvas") {
        return {
          getContext: () => null,
        } as unknown as HTMLCanvasElement;
      }
      return originalCreateElement(tagName, options);
    }) as typeof document.createElement);

    const { DagEdges } = await import("./DagEdges");
    const label = "fallback";
    const expectedWidth = String(label.length * 6.5 + 12);
    const { container } = render(
      <DagEdges
        edges={[{ ...edges[0], labels: [label] }]}
        loopEdges={loopEdges}
        width={100}
        height={100}
      />,
    );

    const rect = container.querySelector("rect");
    expect(rect).toHaveAttribute("width", expectedWidth);
  });
});
