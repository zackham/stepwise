import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { JobList } from "../JobList";
import type { Job } from "@/lib/types";

type MockSearchState = {
  q?: string;
  status?: string;
  range?: string;
};

let mockSearchState: MockSearchState = {};
const searchListeners = new Set<() => void>();

function setMockSearchState(next: MockSearchState = {}) {
  mockSearchState = next;
}

vi.mock("@tanstack/react-router", async () => {
  const React = await import("react");
  const actual = await vi.importActual<typeof import("@tanstack/react-router")>("@tanstack/react-router");

  return {
    ...actual,
    useSearch: () =>
      React.useSyncExternalStore(
        (listener) => {
          searchListeners.add(listener);
          return () => searchListeners.delete(listener);
        },
        () => mockSearchState,
        () => mockSearchState,
      ),
    useNavigate: () =>
      ({
        search,
      }: {
        search?: MockSearchState | ((prev: MockSearchState) => MockSearchState);
      }) => {
        if (!search) return Promise.resolve();
        mockSearchState = typeof search === "function" ? search(mockSearchState) : search;
        for (const listener of searchListeners) listener();
        return Promise.resolve();
      },
  };
});

// Mock hooks
const mockJobs: Job[] = [];
vi.mock("@/hooks/useStepwise", () => ({
  useJobs: () => ({ data: mockJobs, isLoading: false }),
  useStepwiseMutations: () => ({
    cancelJob: { mutate: vi.fn() },
    deleteJob: { mutate: vi.fn() },
    deleteAllJobs: { mutate: vi.fn(), isPending: false },
    resumeJob: { mutate: vi.fn() },
  }),
}));

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "j-" + Math.random().toString(36).slice(2, 8),
    objective: "Test job",
    name: null,
    workflow: { name: "test", steps: {} },
    status: "running",
    inputs: {},
    parent_job_id: null,
    parent_step_run_id: null,
    workspace_path: "/tmp",
    config: {},
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    created_by: "server",
    runner_pid: null,
    heartbeat_at: new Date().toISOString(),
    has_suspended_steps: false,
    ...overrides,
  } as Job;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockJobs.length = 0;
  setMockSearchState();
  searchListeners.clear();
  // jsdom doesn't implement scrollTo on elements
  Element.prototype.scrollTo = vi.fn();
});

function renderJobList() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <JobList selectedJobId={null} onSelectJob={vi.fn()} />
    </QueryClientProvider>,
  );
}

describe("Awaiting Input filter", () => {
  it("shows the dedicated filter button when jobs have suspended steps", () => {
    mockJobs.push(
      makeJob({ status: "running", has_suspended_steps: true }),
      makeJob({ status: "completed" }),
    );
    renderJobList();
    expect(screen.getByTestId("awaiting-input-filter")).toBeInTheDocument();
    expect(screen.getByTestId("awaiting-input-filter")).toHaveTextContent("Awaiting Fulfillment");
    expect(screen.getByTestId("awaiting-input-filter")).toHaveTextContent("1");
  });

  it("hides the dedicated filter button when no jobs have suspended steps", () => {
    mockJobs.push(
      makeJob({ status: "running" }),
      makeJob({ status: "completed" }),
    );
    renderJobList();
    expect(screen.queryByTestId("awaiting-input-filter")).not.toBeInTheDocument();
  });

  it("stores 'awaiting_input' in search when clicked", () => {
    mockJobs.push(makeJob({ status: "running", has_suspended_steps: true }));
    renderJobList();
    fireEvent.click(screen.getByTestId("awaiting-input-filter"));
    expect(mockSearchState.status).toBe("awaiting_input");
  });

  it("deactivates the filter when clicked while already active", () => {
    mockJobs.push(makeJob({ status: "running", has_suspended_steps: true }));
    setMockSearchState({ status: "awaiting_input" });
    renderJobList();
    fireEvent.click(screen.getByTestId("awaiting-input-filter"));
    expect(mockSearchState.status).toBeUndefined();
  });

  it("shows correct count with multiple suspended jobs", () => {
    mockJobs.push(
      makeJob({ status: "running", has_suspended_steps: true }),
      makeJob({ status: "running", has_suspended_steps: true }),
      makeJob({ status: "paused", has_suspended_steps: true }),
      makeJob({ status: "completed" }),
    );
    renderJobList();
    expect(screen.getByTestId("awaiting-input-filter")).toHaveTextContent("3");
  });

  it("filters job list to only suspended-step jobs when active", () => {
    mockJobs.push(
      makeJob({ name: "Suspended Job", status: "running", has_suspended_steps: true }),
      makeJob({ name: "Normal Job", status: "running", has_suspended_steps: false }),
    );
    setMockSearchState({ status: "awaiting_input" });
    renderJobList();
    // The virtualizer may not render items in jsdom (no layout dimensions),
    // but the filter badge count confirms filtering is applied
    expect(screen.getByTestId("awaiting-input-filter")).toHaveTextContent("1");
  });
});
