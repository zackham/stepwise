import { render, screen, fireEvent, act } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import type { ReactNode } from "react";
import { ErrorRecoverySuggestions } from "../ErrorRecoverySuggestions";
import type { StepRun, Job, StepDefinition } from "@/lib/types";

// ── Mock router (Link) ──────────────────────────────────────────────

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, ...props }: { children: ReactNode; to: string }) =>
    createElement("a", { href: props.to }, children),
}));

// ── Mock hooks ──────────────────────────────────────────────────────

let mockSimilarErrors: unknown[] = [];

vi.mock("@/hooks/useStepwise", () => ({
  useStepwiseMutations: () => ({
    rerunStep: { mutate: vi.fn(), isPending: false },
    resumeJob: { mutate: vi.fn(), isPending: false },
    injectContext: { mutate: vi.fn(), isPending: false },
  }),
  useSimilarErrors: () => ({
    data: mockSimilarErrors,
  }),
}));

// ── Helpers ─────────────────────────────────────────────────────────

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function renderWith(ui: React.ReactElement) {
  return render(ui, { wrapper: createWrapper() });
}

function makeRun(overrides: Partial<StepRun> = {}): StepRun {
  return {
    id: "run-1",
    job_id: "job-1",
    step_name: "fetch-data",
    attempt: 1,
    status: "failed",
    inputs: null,
    dep_run_ids: null,
    result: null,
    error: "Connection timed out",
    error_category: "timeout",
    executor_state: null,
    watch: null,
    sub_job_id: null,
    started_at: "2025-01-01T00:00:00Z",
    completed_at: "2025-01-01T00:01:00Z",
    ...overrides,
  };
}

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-1",
    objective: "test",
    name: "test-job",
    workflow: { steps: {} },
    status: "failed",
    inputs: {},
    parent_job_id: null,
    parent_step_run_id: null,
    workspace_path: "",
    config: { max_sub_job_depth: 3, timeout_minutes: null, metadata: {} },
    created_at: "2025-01-01T00:00:00Z",
    updated_at: "2025-01-01T00:01:00Z",
    created_by: "server",
    runner_pid: null,
    heartbeat_at: null,
    ...overrides,
  };
}

function makeStepDef(overrides: Partial<StepDefinition> = {}): StepDefinition {
  return {
    name: "fetch-data",
    description: "",
    outputs: ["data"],
    executor: { type: "script", config: { command: "curl ..." }, decorators: [] },
    inputs: [],
    after: [],
    exit_rules: [],
    idempotency: "none",
    limits: null,
    ...overrides,
  };
}

// ── Tests ───────────────────────────────────────────────────────────

describe("ErrorRecoverySuggestions", () => {
  it("shows retry button for failed run", () => {
    renderWith(
      <ErrorRecoverySuggestions run={makeRun()} job={makeJob()} stepDef={makeStepDef()} />,
    );
    expect(screen.getByText("Retry Step")).toBeTruthy();
  });

  it("shows resume button when job is failed", () => {
    renderWith(
      <ErrorRecoverySuggestions
        run={makeRun()}
        job={makeJob({ status: "failed" })}
        stepDef={makeStepDef()}
      />,
    );
    expect(screen.getByText("Resume Job")).toBeTruthy();
  });

  it("hides resume button when job is running", () => {
    renderWith(
      <ErrorRecoverySuggestions
        run={makeRun()}
        job={makeJob({ status: "running" })}
        stepDef={makeStepDef()}
      />,
    );
    expect(screen.queryByText("Resume Job")).toBeNull();
  });

  it("displays correct guidance for timeout category", () => {
    renderWith(
      <ErrorRecoverySuggestions
        run={makeRun({ error_category: "timeout" })}
        job={makeJob()}
        stepDef={makeStepDef()}
      />,
    );
    expect(screen.getByText(/Timeout/)).toBeTruthy();
    expect(screen.getByText(/timeouts are often transient/)).toBeTruthy();
  });

  it("displays correct guidance for auth_error category", () => {
    renderWith(
      <ErrorRecoverySuggestions
        run={makeRun({ error_category: "auth_error" })}
        job={makeJob()}
        stepDef={makeStepDef()}
      />,
    );
    expect(screen.getByText(/Authentication Error/)).toBeTruthy();
    expect(screen.getByText(/Check API key/)).toBeTruthy();
  });

  it("shows fallback guidance for null category", () => {
    renderWith(
      <ErrorRecoverySuggestions
        run={makeRun({ error_category: null })}
        job={makeJob()}
        stepDef={makeStepDef()}
      />,
    );
    expect(screen.getByText(/No error category/)).toBeTruthy();
  });

  it("shows inject context button for agent steps", () => {
    renderWith(
      <ErrorRecoverySuggestions
        run={makeRun()}
        job={makeJob()}
        stepDef={makeStepDef({
          executor: { type: "agent", config: { prompt: "do stuff" }, decorators: [] },
        })}
      />,
    );
    expect(screen.getByText("Inject Context & Retry")).toBeTruthy();
  });

  it("hides inject context button for script steps", () => {
    renderWith(
      <ErrorRecoverySuggestions
        run={makeRun()}
        job={makeJob()}
        stepDef={makeStepDef()}
      />,
    );
    expect(screen.queryByText("Inject Context & Retry")).toBeNull();
  });

  it("renders similar failures list", () => {
    mockSimilarErrors = [
      {
        run_id: "r-1",
        job_id: "j-1",
        step_name: "call-api",
        error: "timeout",
        error_category: "timeout",
        completed_at: "2025-01-01T00:00:00Z",
        job_name: "daily-pipeline",
      },
    ];

    renderWith(
      <ErrorRecoverySuggestions run={makeRun()} job={makeJob()} stepDef={makeStepDef()} />,
    );
    // Click to open the past failures section
    const pastButton = screen.getByText("Similar Past Failures");
    act(() => {
      fireEvent.click(pastButton);
    });
    expect(screen.getByText("daily-pipeline")).toBeTruthy();
    expect(screen.getByText("call-api")).toBeTruthy();

    mockSimilarErrors = [];
  });

  it("shows empty state when no similar failures", () => {
    mockSimilarErrors = [];
    renderWith(
      <ErrorRecoverySuggestions run={makeRun()} job={makeJob()} stepDef={makeStepDef()} />,
    );
    const pastButton = screen.getByText("Similar Past Failures");
    act(() => {
      fireEvent.click(pastButton);
    });
    expect(screen.getByText("No similar failures found")).toBeTruthy();
  });

  it("hides similar failures section when no error_category", () => {
    renderWith(
      <ErrorRecoverySuggestions
        run={makeRun({ error_category: null })}
        job={makeJob()}
        stepDef={makeStepDef()}
      />,
    );
    expect(screen.queryByText("Similar Past Failures")).toBeNull();
  });
});
