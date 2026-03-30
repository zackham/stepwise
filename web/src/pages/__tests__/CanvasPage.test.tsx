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
  }),
}));

vi.mock("@/lib/api", () => ({
  fetchRuns: () => Promise.resolve([]),
}));

// Mock canvas components to avoid dagre/svg complexity in jsdom
vi.mock("@/components/canvas/DependencyArrows", () => ({
  DependencyArrows: () => <div data-testid="dependency-arrows" />,
}));

vi.mock("@/components/canvas/CanvasLayout", () => ({
  computeCanvasLayout: (jobs: Job[]) => ({
    cards: jobs.map((j, i) => ({ jobId: j.id, x: i * 300, y: 0, width: 280, height: 180 })),
    edges: [],
    groups: [],
    width: jobs.length * 300,
    height: 200,
  }),
}));

vi.mock("@/components/canvas/JobCard", () => ({
  JobCard: ({ job }: { job: Job }) => (
    <div data-testid={`job-card-${job.id}`} data-status={job.status}>
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

    render(<CanvasPage />, { wrapper: createWrapper() });

    expect(screen.getByTestId(`job-card-${j1.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${j2.id}`)).toBeInTheDocument();
    // No dependency arrows when all jobs are independent
    expect(screen.queryByTestId("dependency-arrows")).not.toBeInTheDocument();
  });

  it("places both ends of a depends_on edge in the DAG zone", () => {
    const parent = makeJob({ name: "Parent" });
    const child = makeJob({ name: "Child", depends_on: [parent.id] });
    const independent = makeJob({ name: "Independent" });
    mockJobs = [parent, child, independent];

    render(<CanvasPage />, { wrapper: createWrapper() });

    // All three should render
    expect(screen.getByTestId(`job-card-${parent.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${child.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${independent.id}`)).toBeInTheDocument();
    // DAG zone present (dependency arrows rendered for dependent jobs)
    expect(screen.getByTestId("dependency-arrows")).toBeInTheDocument();
  });

  it("places parent_job_id relationships in the DAG zone", () => {
    const parent = makeJob({ name: "Parent Job" });
    const sub = makeJob({ name: "Sub Job", parent_job_id: parent.id });
    const solo = makeJob({ name: "Solo" });
    mockJobs = [parent, sub, solo];

    render(<CanvasPage />, { wrapper: createWrapper() });

    expect(screen.getByTestId(`job-card-${parent.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${sub.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${solo.id}`)).toBeInTheDocument();
    expect(screen.getByTestId("dependency-arrows")).toBeInTheDocument();
  });

  it("sorts independent jobs by status priority: running > pending > completed", () => {
    const completed = makeJob({ name: "Done", status: "completed", updated_at: "2026-01-01T00:00:00Z" });
    const running = makeJob({ name: "Active", status: "running", updated_at: "2026-01-01T00:00:00Z" });
    const pending = makeJob({ name: "Waiting", status: "pending", updated_at: "2026-01-01T00:00:00Z" });
    mockJobs = [completed, running, pending];

    render(<CanvasPage />, { wrapper: createWrapper() });

    const cards = screen.getAllByTestId(/^job-card-/);
    expect(cards[0]).toHaveAttribute("data-status", "running");
    expect(cards[1]).toHaveAttribute("data-status", "pending");
    expect(cards[2]).toHaveAttribute("data-status", "completed");
  });

  it("breaks status ties by recency (newer first)", () => {
    const older = makeJob({ name: "Older", status: "running", updated_at: "2026-01-01T00:00:00Z" });
    const newer = makeJob({ name: "Newer", status: "running", updated_at: "2026-01-02T00:00:00Z" });
    mockJobs = [older, newer];

    render(<CanvasPage />, { wrapper: createWrapper() });

    const cards = screen.getAllByTestId(/^job-card-/);
    expect(cards[0]).toHaveTextContent("Newer");
    expect(cards[1]).toHaveTextContent("Older");
  });

  it("shows empty state when no jobs exist", () => {
    mockJobs = [];
    render(<CanvasPage />, { wrapper: createWrapper() });
    expect(screen.getByText("No jobs yet. Create one from the Jobs page.")).toBeInTheDocument();
  });

  it("ignores depends_on referencing non-visible (hidden) jobs", () => {
    // Child depends on a job ID that isn't in the visible set
    const child = makeJob({ name: "Orphan Child", depends_on: ["non-existent-id"] });
    const solo = makeJob({ name: "Solo" });
    mockJobs = [child, solo];

    render(<CanvasPage />, { wrapper: createWrapper() });

    // Both should be in grid (no valid edge = both independent)
    expect(screen.queryByTestId("dependency-arrows")).not.toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${child.id}`)).toBeInTheDocument();
    expect(screen.getByTestId(`job-card-${solo.id}`)).toBeInTheDocument();
  });

  it("labels the independent section when DAG zone is present", () => {
    const parent = makeJob({ name: "Parent" });
    const child = makeJob({ name: "Child", depends_on: [parent.id] });
    const solo = makeJob({ name: "Solo" });
    mockJobs = [parent, child, solo];

    render(<CanvasPage />, { wrapper: createWrapper() });

    expect(screen.getByText("Independent jobs")).toBeInTheDocument();
  });
});
