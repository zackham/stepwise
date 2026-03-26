import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { JobList } from "../JobList";
import type { Job } from "@/lib/types";

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

const defaultProps = {
  selectedJobId: null,
  onSelectJob: vi.fn(),
  query: "",
  statusFilter: null,
  onQueryChange: vi.fn(),
  onStatusFilterChange: vi.fn(),
};

beforeEach(() => {
  vi.clearAllMocks();
  mockJobs.length = 0;
});

describe("Awaiting Input filter", () => {
  it("shows the dedicated filter button when jobs have suspended steps", () => {
    mockJobs.push(
      makeJob({ status: "running", has_suspended_steps: true }),
      makeJob({ status: "completed" }),
    );
    render(<JobList {...defaultProps} />);
    expect(screen.getByTestId("awaiting-input-filter")).toBeInTheDocument();
    expect(screen.getByTestId("awaiting-input-filter")).toHaveTextContent("Awaiting Input");
    expect(screen.getByTestId("awaiting-input-filter")).toHaveTextContent("1");
  });

  it("hides the dedicated filter button when no jobs have suspended steps", () => {
    mockJobs.push(
      makeJob({ status: "running" }),
      makeJob({ status: "completed" }),
    );
    render(<JobList {...defaultProps} />);
    expect(screen.queryByTestId("awaiting-input-filter")).not.toBeInTheDocument();
  });

  it("calls onStatusFilterChange with 'awaiting_input' when clicked", () => {
    mockJobs.push(makeJob({ status: "running", has_suspended_steps: true }));
    render(<JobList {...defaultProps} />);
    fireEvent.click(screen.getByTestId("awaiting-input-filter"));
    expect(defaultProps.onStatusFilterChange).toHaveBeenCalledWith("awaiting_input");
  });

  it("deactivates the filter when clicked while already active", () => {
    mockJobs.push(makeJob({ status: "running", has_suspended_steps: true }));
    render(<JobList {...defaultProps} statusFilter="awaiting_input" />);
    fireEvent.click(screen.getByTestId("awaiting-input-filter"));
    expect(defaultProps.onStatusFilterChange).toHaveBeenCalledWith(null);
  });

  it("shows correct count with multiple suspended jobs", () => {
    mockJobs.push(
      makeJob({ status: "running", has_suspended_steps: true }),
      makeJob({ status: "running", has_suspended_steps: true }),
      makeJob({ status: "paused", has_suspended_steps: true }),
      makeJob({ status: "completed" }),
    );
    render(<JobList {...defaultProps} />);
    expect(screen.getByTestId("awaiting-input-filter")).toHaveTextContent("3");
  });

  it("filters job list to only suspended-step jobs when active", () => {
    mockJobs.push(
      makeJob({ name: "Suspended Job", status: "running", has_suspended_steps: true }),
      makeJob({ name: "Normal Job", status: "running", has_suspended_steps: false }),
    );
    render(<JobList {...defaultProps} statusFilter="awaiting_input" />);
    expect(screen.getByText("Suspended Job")).toBeInTheDocument();
    expect(screen.queryByText("Normal Job")).not.toBeInTheDocument();
  });
});
