import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ActionContextProvider } from "@/components/menus/ActionContextProvider";
import { FlowFileList } from "../FlowFileList";
import type { LocalFlow } from "@/lib/types";

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/hooks/useStepwise", () => ({
  useStepwiseMutations: () => ({}),
}));

const mockFlows: LocalFlow[] = [
  {
    path: "flows/research/FLOW.yaml",
    name: "research",
    description: "",
    steps_count: 3,
    modified_at: "2026-03-11T10:00:00",
    is_directory: true,
    executor_types: ["script", "llm"],
    visibility: "interactive",
  },
  {
    path: "flows/deploy.flow.yaml",
    name: "deploy",
    description: "",
    steps_count: 2,
    modified_at: "2026-03-10T08:00:00",
    is_directory: false,
    executor_types: ["script"],
    visibility: "interactive",
  },
  {
    path: "flows/review/FLOW.yaml",
    name: "review",
    description: "",
    steps_count: 5,
    modified_at: "2026-03-09T14:00:00",
    is_directory: true,
    executor_types: ["external", "llm"],
    visibility: "interactive",
  },
];

function createWrapper() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <ActionContextProvider>{children}</ActionContextProvider>
    </QueryClientProvider>
  );
}

describe("FlowFileList", () => {
  it("renders all flows", () => {
    render(
      <FlowFileList
        flows={mockFlows}
        selectedName={undefined}
        onSelect={() => {}}
      />,
      { wrapper: createWrapper() },
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
      />,
      { wrapper: createWrapper() },
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
      />,
      { wrapper: createWrapper() },
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
      />,
      { wrapper: createWrapper() },
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
      />,
      { wrapper: createWrapper() },
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
      />,
      { wrapper: createWrapper() },
    );
    expect(screen.getByText("No flows found")).toBeDefined();
  });

  it("shows no match state when filter has no results", () => {
    render(
      <FlowFileList
        flows={mockFlows}
        selectedName={undefined}
        onSelect={() => {}}
      />,
      { wrapper: createWrapper() },
    );
    const input = screen.getByPlaceholderText("Filter flows...");
    fireEvent.change(input, { target: { value: "zzzzz" } });
    expect(screen.getByText("No matching flows")).toBeDefined();
  });
});
