import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Job } from "@/lib/types";
import { EntityDropdownMenu } from "../EntityDropdownMenu";
import { ActionContextProvider } from "../ActionContextProvider";

// ── Mocks ───────────────────────────────────────────────────────────────

const mockDeleteMutate = vi.fn();
const mockPauseMutate = vi.fn();

vi.mock("@/hooks/useStepwise", () => ({
  useStepwiseMutations: () => ({
    startJob: { mutate: vi.fn() },
    pauseJob: { mutate: mockPauseMutate },
    resumeJob: { mutate: vi.fn() },
    cancelJob: { mutate: vi.fn() },
    deleteJob: { mutate: mockDeleteMutate },
    resetJob: { mutate: vi.fn() },
    adoptJob: { mutate: vi.fn() },
    archiveJob: { mutate: vi.fn() },
    unarchiveJob: { mutate: vi.fn() },
    rerunStep: { mutate: vi.fn() },
    cancelRun: { mutate: vi.fn() },
    injectContext: { mutate: vi.fn() },
    deleteAllJobs: { mutate: vi.fn(), isPending: false },
    fulfillWatch: { mutate: vi.fn() },
  }),
}));

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => vi.fn(),
}));

// ── Helpers ──────────────────────────────────────────────────────────────

let jobCounter = 0;
function makeJob(overrides: Partial<Job> = {}): Job {
  jobCounter++;
  return {
    id: `job-${jobCounter}`,
    name: `Test Job ${jobCounter}`,
    objective: `test-job-${jobCounter}`,
    status: "completed",
    inputs: {},
    parent_job_id: null,
    parent_step_run_id: null,
    workspace_path: "/tmp",
    config: { max_sub_job_depth: 3, timeout_minutes: null, metadata: {} },
    workflow: { steps: {} },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    created_by: "server",
    runner_pid: null,
    heartbeat_at: null,
    has_suspended_steps: false,
    job_group: null,
    depends_on: [],
    ...overrides,
  };
}

function renderWithContext(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ActionContextProvider>{ui}</ActionContextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  jobCounter = 0;
});

// ── Tests ────────────────────────────────────────────────────────────────

describe("EntityDropdownMenu", () => {
  it("renders kebab trigger", () => {
    const job = makeJob({ status: "running" });
    renderWithContext(<EntityDropdownMenu type="job" data={job} />);

    // MoreVertical icon renders as an SVG
    const trigger = screen.getByRole("button");
    expect(trigger).toBeInTheDocument();
  });

  it("shows menu items on click", () => {
    const job = makeJob({ status: "running" });
    renderWithContext(<EntityDropdownMenu type="job" data={job} />);

    fireEvent.click(screen.getByRole("button"));

    expect(screen.getByText("Pause")).toBeInTheDocument();
    expect(screen.getByText("Cancel")).toBeInTheDocument();
  });

  it("trigger click stops propagation", () => {
    const parentClick = vi.fn();
    const job = makeJob({ status: "running" });
    renderWithContext(
      <div onClick={parentClick}>
        <EntityDropdownMenu type="job" data={job} />
      </div>,
    );

    fireEvent.click(screen.getByRole("button"));

    expect(parentClick).not.toHaveBeenCalled();
  });

  it("destructive action opens confirm dialog", () => {
    const job = makeJob({ status: "completed" });
    renderWithContext(<EntityDropdownMenu type="job" data={job} />);

    fireEvent.click(screen.getByRole("button"));
    fireEvent.click(screen.getByText("Delete"));

    expect(screen.getByText("Delete job permanently?")).toBeInTheDocument();
  });

  it("confirming delete calls mutation", () => {
    const job = makeJob({ status: "completed" });
    renderWithContext(<EntityDropdownMenu type="job" data={job} />);

    fireEvent.click(screen.getByRole("button"));
    fireEvent.click(screen.getByText("Delete"));

    // Find and click the confirm button in the dialog
    const confirmButtons = screen.getAllByRole("button");
    const confirmBtn = confirmButtons.find(
      (b) => b.textContent === "Delete",
    );
    fireEvent.click(confirmBtn!);

    expect(mockDeleteMutate).toHaveBeenCalledWith(job.id);
  });
});
