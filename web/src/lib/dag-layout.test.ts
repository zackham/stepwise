import { describe, it, expect } from "vitest";
import { computeDagLayout } from "./dag-layout";
import type { WorkflowDefinition } from "./types";

function makeStep(
  name: string,
  opts: {
    inputs?: Array<{ local_name: string; source_step: string; source_field: string }>;
    sequencing?: string[];
  } = {}
) {
  return {
    name,
    outputs: ["result"],
    executor: { type: "script", config: {}, decorators: [] },
    inputs: opts.inputs ?? [],
    sequencing: opts.sequencing ?? [],
    exit_rules: [],
    idempotency: "idempotent",
    limits: null,
  };
}

describe("computeDagLayout", () => {
  it("lays out a linear workflow (A -> B -> C) in three layers", () => {
    const workflow: WorkflowDefinition = {
      steps: {
        A: makeStep("A"),
        B: makeStep("B", {
          inputs: [
            { local_name: "x", source_step: "A", source_field: "result" },
          ],
        }),
        C: makeStep("C", {
          inputs: [
            { local_name: "x", source_step: "B", source_field: "result" },
          ],
        }),
      },
    };

    const layout = computeDagLayout(workflow);

    expect(layout.nodes).toHaveLength(3);
    expect(layout.edges).toHaveLength(2);

    const nodeMap = Object.fromEntries(layout.nodes.map((n) => [n.id, n]));
    // Top-bottom layout: A should be above B, B above C
    expect(nodeMap["A"].y).toBeLessThan(nodeMap["B"].y);
    expect(nodeMap["B"].y).toBeLessThan(nodeMap["C"].y);

    // Edges should connect A->B and B->C
    const edgePairs = layout.edges.map((e) => `${e.from}->${e.to}`);
    expect(edgePairs).toContain("A->B");
    expect(edgePairs).toContain("B->C");
  });

  it("places fan-out nodes (A -> B, C) at the same depth", () => {
    const workflow: WorkflowDefinition = {
      steps: {
        A: makeStep("A"),
        B: makeStep("B", {
          inputs: [
            { local_name: "x", source_step: "A", source_field: "result" },
          ],
        }),
        C: makeStep("C", {
          inputs: [
            { local_name: "x", source_step: "A", source_field: "result" },
          ],
        }),
      },
    };

    const layout = computeDagLayout(workflow);

    expect(layout.nodes).toHaveLength(3);
    expect(layout.edges).toHaveLength(2);

    const nodeMap = Object.fromEntries(layout.nodes.map((n) => [n.id, n]));
    // B and C should be on the same layer (same y)
    expect(nodeMap["B"].y).toBe(nodeMap["C"].y);
    // A should be above both
    expect(nodeMap["A"].y).toBeLessThan(nodeMap["B"].y);
  });

  it("handles fan-in (B, C -> D) producing correct edges", () => {
    const workflow: WorkflowDefinition = {
      steps: {
        A: makeStep("A"),
        B: makeStep("B", {
          inputs: [
            { local_name: "x", source_step: "A", source_field: "result" },
          ],
        }),
        C: makeStep("C", {
          inputs: [
            { local_name: "x", source_step: "A", source_field: "result" },
          ],
        }),
        D: makeStep("D", {
          inputs: [
            { local_name: "b", source_step: "B", source_field: "result" },
            { local_name: "c", source_step: "C", source_field: "result" },
          ],
        }),
      },
    };

    const layout = computeDagLayout(workflow);

    expect(layout.nodes).toHaveLength(4);

    const edgePairs = layout.edges.map((e) => `${e.from}->${e.to}`);
    expect(edgePairs).toContain("A->B");
    expect(edgePairs).toContain("A->C");
    expect(edgePairs).toContain("B->D");
    expect(edgePairs).toContain("C->D");
    expect(layout.edges).toHaveLength(4);

    const nodeMap = Object.fromEntries(layout.nodes.map((n) => [n.id, n]));
    // D should be below B and C
    expect(nodeMap["D"].y).toBeGreaterThan(nodeMap["B"].y);
    expect(nodeMap["D"].y).toBeGreaterThan(nodeMap["C"].y);
  });

  it("handles a single-node workflow", () => {
    const workflow: WorkflowDefinition = {
      steps: {
        only: makeStep("only"),
      },
    };

    const layout = computeDagLayout(workflow);

    expect(layout.nodes).toHaveLength(1);
    expect(layout.edges).toHaveLength(0);
    expect(layout.nodes[0].id).toBe("only");
    expect(layout.width).toBeGreaterThan(0);
    expect(layout.height).toBeGreaterThan(0);
  });

  it("handles an empty workflow (no steps)", () => {
    const workflow: WorkflowDefinition = {
      steps: {},
    };

    const layout = computeDagLayout(workflow);

    expect(layout.nodes).toHaveLength(0);
    expect(layout.edges).toHaveLength(0);
  });

  it("deduplicates edges when same dependency comes from inputs and sequencing", () => {
    const workflow: WorkflowDefinition = {
      steps: {
        A: makeStep("A"),
        B: makeStep("B", {
          inputs: [
            { local_name: "x", source_step: "A", source_field: "result" },
          ],
          sequencing: ["A"],
        }),
      },
    };

    const layout = computeDagLayout(workflow);

    // Should have exactly 1 edge A->B, not 2
    const abEdges = layout.edges.filter(
      (e) => e.from === "A" && e.to === "B"
    );
    expect(abEdges).toHaveLength(1);
  });

  it("ignores $job input bindings as edges", () => {
    const workflow: WorkflowDefinition = {
      steps: {
        A: makeStep("A", {
          inputs: [
            { local_name: "input", source_step: "$job", source_field: "data" },
          ],
        }),
        B: makeStep("B", {
          inputs: [
            { local_name: "x", source_step: "A", source_field: "result" },
          ],
        }),
      },
    };

    const layout = computeDagLayout(workflow);

    // Only edge should be A->B, no $job->A
    expect(layout.edges).toHaveLength(1);
    expect(layout.edges[0].from).toBe("A");
    expect(layout.edges[0].to).toBe("B");
  });

  it("uses sequencing-only edges for ordering", () => {
    const workflow: WorkflowDefinition = {
      steps: {
        A: makeStep("A"),
        B: makeStep("B", { sequencing: ["A"] }),
      },
    };

    const layout = computeDagLayout(workflow);

    expect(layout.edges).toHaveLength(1);
    expect(layout.edges[0].from).toBe("A");
    expect(layout.edges[0].to).toBe("B");

    const nodeMap = Object.fromEntries(layout.nodes.map((n) => [n.id, n]));
    expect(nodeMap["A"].y).toBeLessThan(nodeMap["B"].y);
  });

  it("produces nodes with correct dimensions", () => {
    const workflow: WorkflowDefinition = {
      steps: {
        A: makeStep("A"),
      },
    };

    const layout = computeDagLayout(workflow);

    const node = layout.nodes[0];
    expect(node.width).toBe(240);
    expect(node.height).toBe(88);
  });

  it("edges include data flow labels from input bindings", () => {
    const workflow: WorkflowDefinition = {
      steps: {
        A: makeStep("A"),
        B: makeStep("B", {
          inputs: [
            { local_name: "x", source_step: "A", source_field: "result" },
          ],
        }),
      },
    };

    const layout = computeDagLayout(workflow);
    const edge = layout.edges.find((e) => e.from === "A" && e.to === "B");
    expect(edge).toBeDefined();
    expect(edge!.labels).toEqual(["result"]);
  });

  it("sequencing-only edges have empty labels", () => {
    const workflow: WorkflowDefinition = {
      steps: {
        A: makeStep("A"),
        B: makeStep("B", { sequencing: ["A"] }),
      },
    };

    const layout = computeDagLayout(workflow);
    const edge = layout.edges.find((e) => e.from === "A" && e.to === "B");
    expect(edge).toBeDefined();
    expect(edge!.labels).toEqual([]);
  });

  it("edges have at least 2 points each", () => {
    const workflow: WorkflowDefinition = {
      steps: {
        A: makeStep("A"),
        B: makeStep("B", { sequencing: ["A"] }),
      },
    };

    const layout = computeDagLayout(workflow);

    for (const edge of layout.edges) {
      expect(edge.points.length).toBeGreaterThanOrEqual(2);
      for (const point of edge.points) {
        expect(typeof point.x).toBe("number");
        expect(typeof point.y).toBe("number");
      }
    }
  });
});
