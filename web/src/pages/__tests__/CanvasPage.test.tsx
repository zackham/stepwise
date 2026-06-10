import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CanvasPage } from "../CanvasPage";
import type { Job } from "@/lib/types";

// Mock router
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, ...props }: { children: React.ReactNode; to: string; params: Record<string, string> }) => (
    <a href={props.to} data-testid={`link-${props.params?.jobId}`}>{children}</a>
  ),
  useNavigate: () => vi.fn(),
}));

// Mock hooks
let mockJobs: Job[] = [];
let mockGroups: Array<{ group: string; max_concurrent: number; active_count: number; pending_count: number; total_count: number }> = [];

vi.mock("@/hooks/useStepwise", () => ({
  useJobs: () => ({ data: mockJobs, isLoading: false }),
  useGroups: () => ({ data: mockGroups }),
  useStepwiseMutations: () => ({
    updateGroupLimit: { mutate: vi.fn() },
    archiveJobs: { mutate: vi.fn() },
  }),
}));

vi.mock("@/lib/api", () => ({
  fetchRuns: () => Promise.resolve([]),
}));

// Mock JobCard to expose the props CanvasPage computes (status, dependency names)
vi.mock("@/components/canvas/JobCard", () => ({
  JobCard: ({ job, dependencyNames }: { job: Job; dependencyNames?: string[] }) => (
    <div
      data-testid={`job-card-${job.id}`}
      data-status={job.status}
      data-deps={dependencyNames && dependencyNames.length > 0 ? dependencyNames.join(",") : undefined}
    >
      {job.name || job.objective}
    </div>
  ),
}));

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "j-" + Math.random().toString(36).slice(2, 8),
    objective: "Test job",
    name: null,
    workflow: { steps: {} },
    status: "running",
    inputs: {},
    parent_job_id: null,
    parent_step_run_id: null,
    workspace_path: "/tmp",
    config: { max_sub_job_depth: 3, timeout_minutes: null, metadata: {} },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    created_by: "server",
    runner_pid: null,
    heartbeat_at: new Date().toISOString(),
    has_suspended_steps: false,
    job_group: null,
    depends_on: [],
    ...overrides,
  } as Job;
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
  mockJobs = [];
  mockGroups = [];
});

describe("CanvasPage partition logic", () => {
  it("renders all independent jobs in CSS grid when no deps exist", () => {
    const j1 = makeJob({ name: "Job A" });
    const j2 = makeJob({ name: "Job B" });
    mockJobs = [j1, j2];

    render(<CanvasPage jobs={mockJobs} />, { wrapper: createWrapper() });

    expect(screen.getByTestId(`job-card-${j1.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${j2.id}`)).toBeInTheDocument();
    // No dependency badges when all jobs are independent
    expect(screen.getByTestId(`job-card-${j1.id}`)).not.toHaveAttribute("data-deps");
    expect(screen.getByTestId(`job-card-${j2.id}`)).not.toHaveAttribute("data-deps");
  });

  it("surfaces depends_on edges as dependency badges on the dependent card", () => {
    const parent = makeJob({ name: "Parent" });
    const child = makeJob({ name: "Child", depends_on: [parent.id] });
    const independent = makeJob({ name: "Independent" });
    mockJobs = [parent, child, independent];

    render(<CanvasPage jobs={mockJobs} />, { wrapper: createWrapper() });

    // All three should render
    expect(screen.getByTestId(`job-card-${parent.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${child.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${independent.id}`)).toBeInTheDocument();
    // The dependent card carries its dependency's name; the others carry none
    expect(screen.getByTestId(`job-card-${child.id}`)).toHaveAttribute("data-deps", "Parent");
    expect(screen.getByTestId(`job-card-${parent.id}`)).not.toHaveAttribute("data-deps");
    expect(screen.getByTestId(`job-card-${independent.id}`)).not.toHaveAttribute("data-deps");
  });

  it("renders parent/sub jobs as plain cards (parent_job_id is not a canvas dependency)", () => {
    const parent = makeJob({ name: "Parent Job" });
    const sub = makeJob({ name: "Sub Job", parent_job_id: parent.id });
    const solo = makeJob({ name: "Solo" });
    mockJobs = [parent, sub, solo];

    render(<CanvasPage jobs={mockJobs} />, { wrapper: createWrapper() });

    expect(screen.getByTestId(`job-card-${parent.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${sub.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${solo.id}`)).toBeInTheDocument();
    // parent_job_id no longer creates a dependency edge on the canvas
    expect(screen.getByTestId(`job-card-${sub.id}`)).not.toHaveAttribute("data-deps");
  });

  it("sorts ungrouped jobs by recency regardless of status", () => {
    const completed = makeJob({ name: "Done", status: "completed", updated_at: "2026-01-03T00:00:00Z" });
    const running = makeJob({ name: "Active", status: "running", updated_at: "2026-01-01T00:00:00Z" });
    const pending = makeJob({ name: "Waiting", status: "pending", updated_at: "2026-01-02T00:00:00Z" });
    mockJobs = [running, completed, pending];

    render(<CanvasPage jobs={mockJobs} />, { wrapper: createWrapper() });

    // Interleaved sort is recency-first: most recently updated job leads even if terminal
    const cards = screen.getAllByTestId(/^job-card-/);
    expect(cards[0]).toHaveAttribute("data-status", "completed");
    expect(cards[1]).toHaveAttribute("data-status", "pending");
    expect(cards[2]).toHaveAttribute("data-status", "running");
  });

  it("breaks status ties by recency (newer first)", () => {
    const older = makeJob({ name: "Older", status: "running", updated_at: "2026-01-01T00:00:00Z" });
    const newer = makeJob({ name: "Newer", status: "running", updated_at: "2026-01-02T00:00:00Z" });
    mockJobs = [older, newer];

    render(<CanvasPage jobs={mockJobs} />, { wrapper: createWrapper() });

    const cards = screen.getAllByTestId(/^job-card-/);
    expect(cards[0]).toHaveTextContent("Newer");
    expect(cards[1]).toHaveTextContent("Older");
  });

  it("shows empty state when no jobs passed", () => {
    render(<CanvasPage jobs={[]} />, { wrapper: createWrapper() });
    expect(screen.getByText("No matching jobs")).toBeInTheDocument();
  });

  it("ignores depends_on referencing non-visible (hidden) jobs", () => {
    // Child depends on a job ID that isn't in the visible set
    const child = makeJob({ name: "Orphan Child", depends_on: ["non-existent-id"] });
    const solo = makeJob({ name: "Solo" });
    mockJobs = [child, solo];

    render(<CanvasPage jobs={mockJobs} />, { wrapper: createWrapper() });

    // Both should be in grid; the dangling reference produces no dependency badge
    expect(screen.getByTestId(`job-card-${child.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${solo.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${child.id}`)).not.toHaveAttribute("data-deps");
  });

  it("renders a labeled section with completion summary for grouped jobs", () => {
    const grouped1 = makeJob({ name: "Batch One", job_group: "batch-a", status: "completed" });
    const grouped2 = makeJob({ name: "Batch Two", job_group: "batch-a", status: "running" });
    const solo = makeJob({ name: "Solo" });
    mockJobs = [grouped1, grouped2, solo];

    render(<CanvasPage jobs={mockJobs} />, { wrapper: createWrapper() });

    // Group section header with name and rollup; active group is expanded by default
    expect(screen.getByText("batch-a")).toBeInTheDocument();
    expect(screen.getByText("1/2 done")).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${grouped1.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${grouped2.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${solo.id}`)).toBeInTheDocument();
  });
});
