import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { FlowFileList } from "../FlowFileList";
import type { LocalFlow } from "@/lib/types";

const mockFlows: LocalFlow[] = [
  {
    path: "flows/research/FLOW.yaml",
    name: "research",
    description: "",
    steps_count: 3,
    modified_at: "2026-03-11T10:00:00",
    is_directory: true,
    executor_types: ["script", "llm"],
  },
  {
    path: "flows/deploy.flow.yaml",
    name: "deploy",
    description: "",
    steps_count: 2,
    modified_at: "2026-03-10T08:00:00",
    is_directory: false,
    executor_types: ["script"],
  },
  {
    path: "flows/review/FLOW.yaml",
    name: "review",
    description: "",
    steps_count: 5,
    modified_at: "2026-03-09T14:00:00",
    is_directory: true,
    executor_types: ["external", "llm"],
  },
];

describe("FlowFileList", () => {
  it("renders all flows", () => {
    render(
      <FlowFileList
        flows={mockFlows}
        selectedName={undefined}
        onSelect={() => {}}
      />
    );
    expect(screen.getByText("research")).toBeDefined();
    expect(screen.getByText("deploy")).toBeDefined();
    expect(screen.getByText("review")).toBeDefined();
  });

  it("shows step counts", () => {
    render(
      <FlowFileList
        flows={mockFlows}
        selectedName={undefined}
        onSelect={() => {}}
      />
    );
    expect(screen.getByText("3")).toBeDefined();
    expect(screen.getByText("2")).toBeDefined();
    expect(screen.getByText("5")).toBeDefined();
  });

  it("highlights selected flow", () => {
    render(
      <FlowFileList
        flows={mockFlows}
        selectedName="research"
        onSelect={() => {}}
      />
    );
    const button = screen.getByText("research").closest("button")!;
    expect(button.className).toContain("dark:bg-zinc-800");
  });

  it("calls onSelect when flow clicked", () => {
    const onSelect = vi.fn();
    render(
      <FlowFileList
        flows={mockFlows}
        selectedName={undefined}
        onSelect={onSelect}
      />
    );
    fireEvent.click(screen.getByText("deploy"));
    expect(onSelect).toHaveBeenCalledWith(mockFlows[1]);
  });

  it("filters flows by name", () => {
    render(
      <FlowFileList
        flows={mockFlows}
        selectedName={undefined}
        onSelect={() => {}}
      />
    );
    const input = screen.getByPlaceholderText("Filter flows...");
    fireEvent.change(input, { target: { value: "res" } });
    expect(screen.getByText("research")).toBeDefined();
    expect(screen.queryByText("deploy")).toBeNull();
    expect(screen.queryByText("review")).toBeNull();
  });

  it("shows empty state when no flows", () => {
    render(
      <FlowFileList
        flows={[]}
        selectedName={undefined}
        onSelect={() => {}}
      />
    );
    expect(screen.getByText("No flows found")).toBeDefined();
  });

  it("shows no match state when filter has no results", () => {
    render(
      <FlowFileList
        flows={mockFlows}
        selectedName={undefined}
        onSelect={() => {}}
      />
    );
    const input = screen.getByPlaceholderText("Filter flows...");
    fireEvent.change(input, { target: { value: "zzzzz" } });
    expect(screen.getByText("No matching flows")).toBeDefined();
  });

});
