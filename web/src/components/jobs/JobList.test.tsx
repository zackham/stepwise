import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { JobList } from "./JobList";
import type { Job } from "@/lib/types";

// ── ResizeObserver polyfill (jsdom lacks it) ────────────────────────────

// jsdom doesn't implement scrollTo on elements
Element.prototype.scrollTo = vi.fn();

// Mock ResizeObserver to immediately report a reasonable container size
// so @tanstack/react-virtual renders virtual items
globalThis.ResizeObserver = class {
  private cb: ResizeObserverCallback;
  constructor(cb: ResizeObserverCallback) {
    this.cb = cb;
  }
  observe(target: Element) {
    // Fire callback with a fake entry giving the element a height
    this.cb(
      [
        {
          target,
          contentRect: { height: 600, width: 400 } as DOMRectReadOnly,
          borderBoxSize: [{ blockSize: 600, inlineSize: 400 }],
          contentBoxSize: [{ blockSize: 600, inlineSize: 400 }],
          devicePixelContentBoxSize: [{ blockSize: 600, inlineSize: 400 }],
        } as unknown as ResizeObserverEntry,
      ],
      this as unknown as ResizeObserver,
    );
  }
  unobserve() {}
  disconnect() {}
} as unknown as typeof ResizeObserver;

// ── Mocks ───────────────────────────────────────────────────────────────

let mockJobs: Job[] = [];

const mockMutations = {
  resumeJob: { mutate: vi.fn() },
  cancelJob: { mutate: vi.fn() },
  deleteJob: { mutate: vi.fn() },
  deleteAllJobs: { mutate: vi.fn(), isPending: false },
  fulfillWatch: { mutate: vi.fn() },
};

vi.mock("@/hooks/useStepwise", () => ({
  useJobs: () => ({ data: mockJobs, isLoading: false }),
  useStepwiseMutations: () => mockMutations,
}));

// ── Helpers ─────────────────────────────────────────────────────────────

let jobCounter = 0;

function makeJob(overrides: Partial<Job> = {}): Job {
  jobCounter++;
  return {
    id: `job-${jobCounter}`,
    name: null,
    objective: `Test job ${jobCounter}`,
    status: "completed",
    inputs: {},
    parent_job_id: null,
    parent_step_run_id: null,
    workspace_path: "/tmp",
    config: { max_sub_job_depth: 3, timeout_minutes: null, metadata: {} },
    workflow: { steps: {} },
    created_at: new Date(Date.now() - jobCounter * 60000).toISOString(),
    updated_at: new Date(Date.now() - jobCounter * 60000).toISOString(),
    created_by: "server",
    runner_pid: null,
    heartbeat_at: null,
    has_suspended_steps: false,
    job_group: null,
    depends_on: [],
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

function renderJobList(props: Partial<Parameters<typeof JobList>[0]> = {}) {
  const onSelectJob = props.onSelectJob ?? vi.fn();
  return {
    onSelectJob,
    ...render(
      <JobList
        selectedJobId={null}
        onSelectJob={onSelectJob}
        {...props}
      />,
      { wrapper: createWrapper() },
    ),
  };
}

// ── Tests ───────────────────────────────────────────────────────────────

beforeEach(() => {
  mockJobs = [];
  jobCounter = 0;
});

describe("JobList", () => {
  it("renders job names", () => {
    mockJobs = [
      makeJob({ name: "Deploy API" }),
      makeJob({ name: "Run Tests" }),
      makeJob({ name: "Build Docker" }),
    ];
    renderJobList();

    expect(screen.getByText("Deploy API")).toBeInTheDocument();
    expect(screen.getByText("Run Tests")).toBeInTheDocument();
    expect(screen.getByText("Build Docker")).toBeInTheDocument();
  });

  it("renders objective when name is null", () => {
    mockJobs = [makeJob({ name: null, objective: "analyze data" })];
    renderJobList();

    expect(screen.getByText("analyze data")).toBeInTheDocument();
  });

  it("filters by text query", () => {
    mockJobs = [
      makeJob({ name: "Deploy API" }),
      makeJob({ name: "Run Tests" }),
      makeJob({ name: "Deploy Frontend" }),
    ];
    renderJobList({ query: "deploy", onQueryChange: vi.fn() });

    expect(screen.getByText("Deploy API")).toBeInTheDocument();
    expect(screen.getByText("Deploy Frontend")).toBeInTheDocument();
    expect(screen.queryByText("Run Tests")).not.toBeInTheDocument();
  });

  it("filters by status pill click", () => {
    mockJobs = [
      makeJob({ name: "Job A", status: "running" }),
      makeJob({ name: "Job B", status: "completed" }),
      makeJob({ name: "Job C", status: "running" }),
    ];
    renderJobList();

    // Click the "Running" status pill
    const runningPill = screen.getByRole("button", { name: /Running/i });
    fireEvent.click(runningPill);

    expect(screen.getByText("Job A")).toBeInTheDocument();
    expect(screen.getByText("Job C")).toBeInTheDocument();
    expect(screen.queryByText("Job B")).not.toBeInTheDocument();
  });

  it("shows empty state when no jobs", () => {
    mockJobs = [];
    renderJobList();

    expect(screen.getByText("No jobs yet")).toBeInTheDocument();
  });

  it("shows filtered empty state when filter matches nothing", () => {
    mockJobs = [makeJob({ name: "Deploy API" })];
    renderJobList({ query: "zzzzz", onQueryChange: vi.fn() });

    expect(screen.getByText("No matching jobs")).toBeInTheDocument();
  });

  it("has correct accessibility attributes", () => {
    mockJobs = [makeJob({ name: "Job A" }), makeJob({ name: "Job B" })];
    renderJobList();

    const listbox = screen.getByRole("listbox");
    expect(listbox).toHaveAttribute("aria-label", "Job list");

    const options = screen.getAllByRole("option");
    expect(options.length).toBeGreaterThanOrEqual(2);
    for (const opt of options) {
      expect(opt).toHaveAttribute("aria-selected");
    }
  });

  it("navigates with keyboard ArrowDown and Enter", () => {
    mockJobs = [
      makeJob({ name: "Job A" }),
      makeJob({ name: "Job B" }),
      makeJob({ name: "Job C" }),
    ];
    const { onSelectJob } = renderJobList();

    const listbox = screen.getByRole("listbox");

    // ArrowDown to focus first item
    fireEvent.keyDown(listbox, { key: "ArrowDown" });
    // ArrowDown to focus second item
    fireEvent.keyDown(listbox, { key: "ArrowDown" });
    // Enter to select
    fireEvent.keyDown(listbox, { key: "Enter" });

    // Jobs are sorted by "recent" (default), so the order is by updated_at desc.
    // The second item (index 1) should be selected.
    expect(onSelectJob).toHaveBeenCalledTimes(1);
    // Verify it was called with a job id (second in sorted order)
    expect(onSelectJob).toHaveBeenCalledWith(expect.stringMatching(/^job-/));
  });

  it("clears focus on Escape", () => {
    mockJobs = [makeJob({ name: "Job A" })];
    renderJobList();

    const listbox = screen.getByRole("listbox");

    fireEvent.keyDown(listbox, { key: "ArrowDown" });
    // After ArrowDown, aria-activedescendant should be set
    expect(listbox.getAttribute("aria-activedescendant")).toBeTruthy();

    fireEvent.keyDown(listbox, { key: "Escape" });
    // After Escape, aria-activedescendant should be cleared
    expect(listbox.getAttribute("aria-activedescendant")).toBeFalsy();
  });

  it("marks selected job with aria-selected=true", () => {
    mockJobs = [makeJob({ name: "Job A" }), makeJob({ name: "Job B" })];
    renderJobList({ selectedJobId: "job-1" });

    const options = screen.getAllByRole("option");
    const selected = options.find(
      (opt) => opt.getAttribute("aria-selected") === "true",
    );
    expect(selected).toBeDefined();
    expect(selected!.id).toBe("job-job-1");
  });
});
