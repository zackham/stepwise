import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Job } from "@/lib/types";
import { EntityContextMenu } from "../EntityContextMenu";
import { ActionContextProvider } from "../ActionContextProvider";

// ── Mocks ───────────────────────────────────────────────────────────────

const mockDeleteMutate = vi.fn();
const mockPauseMutate = vi.fn();
const mockCancelMutate = vi.fn();
const mockResumeMutate = vi.fn();

vi.mock("@/hooks/useStepwise", () => ({
  useStepwiseMutations: () => ({
    startJob: { mutate: vi.fn() },
    pauseJob: { mutate: mockPauseMutate },
    resumeJob: { mutate: mockResumeMutate },
    cancelJob: { mutate: mockCancelMutate },
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

describe("EntityContextMenu", () => {
  it("renders children as trigger", () => {
    const job = makeJob({ status: "running" });
    renderWithContext(
      <EntityContextMenu type="job" data={job}>
        <div data-testid="trigger">Job Row</div>
      </EntityContextMenu>,
    );
    expect(screen.getByTestId("trigger")).toBeInTheDocument();
  });

  it("shows correct actions for running job on right-click", () => {
    const job = makeJob({ status: "running" });
    renderWithContext(
      <EntityContextMenu type="job" data={job}>
        <div data-testid="trigger">Job Row</div>
      </EntityContextMenu>,
    );

    fireEvent.contextMenu(screen.getByTestId("trigger"));

    expect(screen.getByText("Pause")).toBeInTheDocument();
    expect(screen.getByText("Cancel")).toBeInTheDocument();
    expect(screen.getByText("Inject Context")).toBeInTheDocument();
    // Should NOT show Retry for running job
    expect(screen.queryByText("Retry")).not.toBeInTheDocument();
    // Should NOT show Archive for running job
    expect(screen.queryByText("Archive")).not.toBeInTheDocument();
  });

  it("shows correct actions for failed job", () => {
    const job = makeJob({ status: "failed" });
    renderWithContext(
      <EntityContextMenu type="job" data={job}>
        <div data-testid="trigger">Job Row</div>
      </EntityContextMenu>,
    );

    fireEvent.contextMenu(screen.getByTestId("trigger"));

    expect(screen.getByText("Retry")).toBeInTheDocument();
    expect(screen.getByText("Archive")).toBeInTheDocument();
    expect(screen.getByText("Reset")).toBeInTheDocument();
    // Should NOT show Pause for failed job
    expect(screen.queryByText("Pause")).not.toBeInTheDocument();
  });

  it("executes non-destructive action immediately", () => {
    const job = makeJob({ status: "running" });
    renderWithContext(
      <EntityContextMenu type="job" data={job}>
        <div data-testid="trigger">Job Row</div>
      </EntityContextMenu>,
    );

    fireEvent.contextMenu(screen.getByTestId("trigger"));
    fireEvent.click(screen.getByText("Pause"));

    expect(mockPauseMutate).toHaveBeenCalledWith(job.id);
  });

  it("opens confirm dialog for destructive action", () => {
    const job = makeJob({ status: "completed" });
    renderWithContext(
      <EntityContextMenu type="job" data={job}>
        <div data-testid="trigger">Job Row</div>
      </EntityContextMenu>,
    );

    fireEvent.contextMenu(screen.getByTestId("trigger"));
    fireEvent.click(screen.getByText("Delete"));

    // Confirm dialog should appear (sibling of menu, survives menu close)
    expect(screen.getByText("Delete job permanently?")).toBeInTheDocument();
  });

  it("confirm dialog executes action then closes", () => {
    const job = makeJob({ status: "completed" });
    renderWithContext(
      <EntityContextMenu type="job" data={job}>
        <div data-testid="trigger">Job Row</div>
      </EntityContextMenu>,
    );

    fireEvent.contextMenu(screen.getByTestId("trigger"));
    fireEvent.click(screen.getByText("Delete"));

    // Click confirm in dialog
    const confirmButtons = screen.getAllByRole("button");
    const confirmBtn = confirmButtons.find(
      (b) => b.textContent === "Delete",
    );
    expect(confirmBtn).toBeDefined();
    fireEvent.click(confirmBtn!);

    expect(mockDeleteMutate).toHaveBeenCalledWith(job.id);
    // Dialog should close
    expect(screen.queryByText("Delete job permanently?")).not.toBeInTheDocument();
  });

  it("cancel dismisses dialog without mutation", () => {
    const job = makeJob({ status: "completed" });
    renderWithContext(
      <EntityContextMenu type="job" data={job}>
        <div data-testid="trigger">Job Row</div>
      </EntityContextMenu>,
    );

    fireEvent.contextMenu(screen.getByTestId("trigger"));
    fireEvent.click(screen.getByText("Delete"));

    // Click cancel
    fireEvent.click(screen.getByText("Cancel"));

    expect(mockDeleteMutate).not.toHaveBeenCalled();
    expect(screen.queryByText("Delete job permanently?")).not.toBeInTheDocument();
  });
});
