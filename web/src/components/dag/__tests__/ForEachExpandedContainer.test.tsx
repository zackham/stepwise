import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ForEachExpandedContainer } from "../ForEachExpandedContainer";
import type { HierarchicalDagNode, ForEachInstance, HierarchicalDagLayout } from "@/lib/dag-layout";
import type { JobTreeNode } from "@/lib/types";

function makeLayout(): HierarchicalDagLayout {
  return {
    nodes: [],
    edges: [],
    loopEdges: [],
    flowPorts: [],
    containerPorts: [],
    width: 200,
    height: 100,
  };
}

function makeInstance(index: number, status: string | null): ForEachInstance {
  return {
    index,
    jobId: `job-${index}`,
    status,
    layout: makeLayout(),
  };
}

function makeNode(overrides: Partial<HierarchicalDagNode> = {}): HierarchicalDagNode {
  return {
    id: "fe-step",
    x: 0,
    y: 0,
    width: 500,
    height: 400,
    isExpanded: true,
    hasSubFlow: true,
    childLayout: null,
    childJobId: null,
    childStepCount: 2,
    containerPadding: { top: 44, left: 24, right: 24, bottom: 28 },
    isForEach: true,
    forEachChildren: [],
    ...overrides,
  };
}

function makeSubTree(jobId: string, status: string): JobTreeNode {
  return {
    job: {
      id: jobId,
      name: null,
      objective: `Instance [${jobId.split("-")[1]}]`,
      status,
      workflow: { steps: {} },
      inputs: {},
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      created_by: "server",
      runner_pid: null,
      heartbeat_at: null,
      parent_job_id: null,
      parent_step_run_id: null,
    },
    runs: [],
    sub_jobs: [],
  };
}

const defaultProps = {
  expandedSteps: new Set<string>(),
  selectedStep: null,
  onSelectStep: vi.fn(),
  onToggleExpand: vi.fn(),
  onNavigateSubJob: vi.fn(),
  depth: 0,
};

describe("ForEachExpandedContainer", () => {
  it("renders step name and instance count in header", () => {
    const instances = [makeInstance(0, "completed"), makeInstance(1, "running")];
    const subTrees = [makeSubTree("job-0", "completed"), makeSubTree("job-1", "running")];
    const node = makeNode({ forEachChildren: instances });

    render(
      <ForEachExpandedContainer
        node={node}
        stepName="process-items"
        instances={instances}
        subTrees={subTrees}
        {...defaultProps}
      />
    );

    expect(screen.getByText("process-items")).toBeInTheDocument();
    expect(screen.getByText("2 instances")).toBeInTheDocument();
  });

  it("shows aggregate status badges in header", () => {
    const instances = [
      makeInstance(0, "completed"),
      makeInstance(1, "completed"),
      makeInstance(2, "running"),
    ];
    const subTrees = instances.map((i) =>
      makeSubTree(i.jobId, i.status!)
    );
    const node = makeNode({ forEachChildren: instances });

    render(
      <ForEachExpandedContainer
        node={node}
        stepName="fe-step"
        instances={instances}
        subTrees={subTrees}
        {...defaultProps}
      />
    );

    // Aggregate badges: "completed ×2" and "running"
    // "completed" appears in both aggregate header and per-instance badges
    expect(screen.getAllByText("completed").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("×2")).toBeInTheDocument();
    expect(screen.getAllByText("running").length).toBeGreaterThanOrEqual(1);
  });

  it("collapses to compact view on header click", () => {
    const instances = [
      makeInstance(0, "completed"),
      makeInstance(1, "running"),
    ];
    const subTrees = [
      makeSubTree("job-0", "completed"),
      makeSubTree("job-1", "running"),
    ];
    const node = makeNode({ forEachChildren: instances });

    render(
      <ForEachExpandedContainer
        node={node}
        stepName="fe-step"
        instances={instances}
        subTrees={subTrees}
        {...defaultProps}
      />
    );

    // Initially expanded — instance headers visible
    expect(screen.getByText("[0]")).toBeInTheDocument();
    expect(screen.getByText("[1]")).toBeInTheDocument();

    // Click header to collapse
    const header = screen.getByText("fe-step").closest("div[class*='cursor-pointer']")!;
    fireEvent.click(header);

    // Status dots still visible (compact view), item labels visible
    const dots = document.querySelectorAll("[title]");
    expect(dots.length).toBeGreaterThan(0);
  });

  it("toggles between collapsed and expanded on repeated header clicks", () => {
    const instances = [makeInstance(0, "completed")];
    const subTrees = [makeSubTree("job-0", "completed")];
    const node = makeNode({ forEachChildren: instances });

    const { container } = render(
      <ForEachExpandedContainer
        node={node}
        stepName="fe-step"
        instances={instances}
        subTrees={subTrees}
        {...defaultProps}
      />
    );

    const header = screen.getByText("fe-step").closest("div[class*='cursor-pointer']")!;

    // Initially expanded — should not have the compact status grid
    expect(container.querySelector("[title='completed']")).toBeNull();

    // Click to collapse — compact grid with status dots
    fireEvent.click(header);
    expect(container.querySelector("[title='completed']")).toBeInTheDocument();

    // Click to expand again — dots gone, back to full layout
    fireEvent.click(header);
    expect(container.querySelector("[title='completed']")).toBeNull();
  });

  it("does not call onToggleExpand when header is clicked", () => {
    const instances = [makeInstance(0, "completed")];
    const subTrees = [makeSubTree("job-0", "completed")];
    const node = makeNode({ forEachChildren: instances });
    const onToggleExpand = vi.fn();

    render(
      <ForEachExpandedContainer
        node={node}
        stepName="fe-step"
        instances={instances}
        subTrees={subTrees}
        {...defaultProps}
        onToggleExpand={onToggleExpand}
      />
    );

    const header = screen.getByText("fe-step").closest("div[class*='cursor-pointer']")!;
    fireEvent.click(header);

    // Header click toggles internal state, not the parent expand/collapse
    expect(onToggleExpand).not.toHaveBeenCalled();
  });
});
