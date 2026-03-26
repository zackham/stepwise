import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { QuickLaunch } from "./QuickLaunch";
import type { QuickLaunchItem } from "@/lib/types";

// ── Mocks ──────────────────────────────────────────────────────────────

let mockRecentFlows: QuickLaunchItem[] = [];
const mockMutate = vi.fn();

vi.mock("@/hooks/useStepwise", () => ({
  useRecentFlows: () => ({ data: mockRecentFlows }),
  useStepwiseMutations: () => ({
    createJob: { mutate: mockMutate, isPending: false },
  }),
}));

function makeItem(overrides: Partial<QuickLaunchItem> = {}): QuickLaunchItem {
  return {
    flow_name: "test-flow",
    flow_path: "/flows/test",
    last_inputs: { url: "https://example.com" },
    last_job_id: "job-1",
    last_job_name: "Test Flow",
    last_run_at: new Date().toISOString(),
    last_status: "completed",
    workflow: { steps: {} },
    ...overrides,
  };
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  mockRecentFlows = [];
  mockMutate.mockReset();
});

// ── Tests ──────────────────────────────────────────────────────────────

describe("QuickLaunch", () => {
  it("renders nothing when no recent flows", () => {
    const { container } = render(
      <QuickLaunch onLaunched={vi.fn()} onEditLaunch={vi.fn()} />,
      { wrapper: createWrapper() }
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders flow cards with names", () => {
    mockRecentFlows = [
      makeItem({ flow_name: "my-pipeline", last_job_id: "j1" }),
      makeItem({ flow_name: "test-suite", last_job_id: "j2" }),
    ];
    render(
      <QuickLaunch onLaunched={vi.fn()} onEditLaunch={vi.fn()} />,
      { wrapper: createWrapper() }
    );
    expect(screen.getByText("my-pipeline")).toBeDefined();
    expect(screen.getByText("test-suite")).toBeDefined();
  });

  it("calls createJob.mutate on click", () => {
    mockRecentFlows = [makeItem()];
    render(
      <QuickLaunch onLaunched={vi.fn()} onEditLaunch={vi.fn()} />,
      { wrapper: createWrapper() }
    );
    fireEvent.click(screen.getByText("test-flow"));
    expect(mockMutate).toHaveBeenCalledTimes(1);
  });

  it("calls onEditLaunch with prefill data on edit button click", () => {
    mockRecentFlows = [makeItem()];
    const onEditLaunch = vi.fn();
    render(
      <QuickLaunch onLaunched={vi.fn()} onEditLaunch={onEditLaunch} />,
      { wrapper: createWrapper() }
    );
    const editBtn = screen.getByTitle("Edit & Run");
    fireEvent.click(editBtn);
    expect(onEditLaunch).toHaveBeenCalledWith({
      workflow: { steps: {} },
      inputs: { url: "https://example.com" },
      name: "Test Flow",
    });
  });

  it("limits visible items to 3 by default", () => {
    mockRecentFlows = [
      makeItem({ flow_name: "flow-1", last_job_id: "j1" }),
      makeItem({ flow_name: "flow-2", last_job_id: "j2" }),
      makeItem({ flow_name: "flow-3", last_job_id: "j3" }),
      makeItem({ flow_name: "flow-4", last_job_id: "j4" }),
      makeItem({ flow_name: "flow-5", last_job_id: "j5" }),
    ];
    render(
      <QuickLaunch onLaunched={vi.fn()} onEditLaunch={vi.fn()} />,
      { wrapper: createWrapper() }
    );
    expect(screen.getByText("flow-1")).toBeDefined();
    expect(screen.getByText("flow-2")).toBeDefined();
    expect(screen.getByText("flow-3")).toBeDefined();
    expect(screen.queryByText("flow-4")).toBeNull();
    // "2 more" expand button
    expect(screen.getByText("2 more")).toBeDefined();
  });

  it("expands to show all items on click", () => {
    mockRecentFlows = [
      makeItem({ flow_name: "flow-1", last_job_id: "j1" }),
      makeItem({ flow_name: "flow-2", last_job_id: "j2" }),
      makeItem({ flow_name: "flow-3", last_job_id: "j3" }),
      makeItem({ flow_name: "flow-4", last_job_id: "j4" }),
    ];
    render(
      <QuickLaunch onLaunched={vi.fn()} onEditLaunch={vi.fn()} />,
      { wrapper: createWrapper() }
    );
    fireEvent.click(screen.getByText("1 more"));
    expect(screen.getByText("flow-4")).toBeDefined();
    expect(screen.getByText("Show less")).toBeDefined();
  });
});
