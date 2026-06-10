import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

// ── Mocks ──────────────────────────────────────────────────────────

// Capture the tick subscriber so we can call it in tests
let tickSubscriber: ((msg: { type: "tick"; changed_jobs: string[]; timestamp: string }) => void) | null = null;

vi.mock("../useStepwiseWebSocket", () => ({
  subscribeTickMessages: vi.fn((fn: typeof tickSubscriber) => {
    tickSubscriber = fn;
    return () => { tickSubscriber = null; };
  }),
}));

const mockNavigate = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  useRouter: () => ({ navigate: mockNavigate }),
}));

vi.mock("@/lib/api", () => ({
  fetchRuns: vi.fn(),
  fetchJob: vi.fn(),
}));

import { useNotifySuspended } from "../useNotifySuspended";
import { fetchRuns, fetchJob } from "@/lib/api";
import type { StepRun, Job } from "@/lib/types";

const mockedFetchRuns = vi.mocked(fetchRuns);
const mockedFetchJob = vi.mocked(fetchJob);

const STORAGE_KEY = "stepwise-notifications-enabled";

// ── Helpers ──────────────────────────────────────────────────────────

function makeRun(overrides: Partial<StepRun> = {}): StepRun {
  return {
    id: "run-1",
    job_id: "job-1",
    step_name: "step-a",
    attempt: 1,
    status: "running",
    inputs: null,
    dep_run_ids: null,
    result: null,
    error: null,
    error_category: null,
    traceback: null,
    executor_state: null,
    watch: null,
    sub_job_id: null,
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    id: "job-1",
    objective: "test job",
    name: "My Job",
    workflow: { steps: {} },
    status: "running",
    inputs: {},
    parent_job_id: null,
    parent_step_run_id: null,
    workspace_path: "/tmp",
    config: { max_sub_job_depth: 3, timeout_minutes: null, metadata: {} },
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    created_by: "server",
    runner_pid: null,
    heartbeat_at: null,
    job_group: null,
    depends_on: [],
    ...overrides,
  };
}

class MockNotification {
  static permission: NotificationPermission = "granted";
  static requestPermission = vi.fn(() => Promise.resolve("granted" as NotificationPermission));

  title: string;
  options: NotificationOptions;
  onclick: ((ev: Event) => void) | null = null;
  close = vi.fn();

  constructor(title: string, options: NotificationOptions = {}) {
    this.title = title;
    this.options = options;
    MockNotification.instances.push(this);
  }

  static instances: MockNotification[] = [];
  static reset() {
    MockNotification.instances = [];
    MockNotification.permission = "granted";
    MockNotification.requestPermission.mockReset().mockResolvedValue("granted");
  }
}

/** Render the hook with notifications already opted in (localStorage flag set). */
function renderEnabledHook() {
  localStorage.setItem(STORAGE_KEY, "true");
  return renderHook(() => useNotifySuspended());
}

// ── Setup ────────────────────────────────────────────────────────────

beforeEach(() => {
  MockNotification.reset();
  vi.stubGlobal("Notification", MockNotification);
  localStorage.clear();
  // Default: tab not focused
  vi.spyOn(document, "hasFocus").mockReturnValue(false);
  mockNavigate.mockReset();
  mockedFetchRuns.mockReset();
  mockedFetchJob.mockReset();
  tickSubscriber = null;
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ── Tests ────────────────────────────────────────────────────────────

describe("useNotifySuspended", () => {
  it("does not request permission on mount (opt-in only)", () => {
    MockNotification.permission = "default";
    const { result } = renderHook(() => useNotifySuspended());
    expect(MockNotification.requestPermission).not.toHaveBeenCalled();
    expect(result.current.enabled).toBe(false);
  });

  it("starts enabled when localStorage flag is set", () => {
    localStorage.setItem(STORAGE_KEY, "true");
    const { result } = renderHook(() => useNotifySuspended());
    expect(result.current.enabled).toBe(true);
  });

  it("toggle requests permission when default, then enables on grant", async () => {
    MockNotification.permission = "default";
    const { result } = renderHook(() => useNotifySuspended());

    await act(async () => {
      result.current.toggle();
    });

    expect(MockNotification.requestPermission).toHaveBeenCalled();
    expect(result.current.enabled).toBe(true);
    expect(result.current.permission).toBe("granted");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("true");
  });

  it("toggle stays disabled when permission request is denied", async () => {
    MockNotification.permission = "default";
    MockNotification.requestPermission.mockResolvedValue("denied");
    const { result } = renderHook(() => useNotifySuspended());

    await act(async () => {
      result.current.toggle();
    });

    expect(result.current.enabled).toBe(false);
    expect(result.current.permission).toBe("denied");
  });

  it("toggle enables directly without prompting when permission already granted", async () => {
    MockNotification.permission = "granted";
    const { result } = renderHook(() => useNotifySuspended());

    await act(async () => {
      result.current.toggle();
    });

    expect(MockNotification.requestPermission).not.toHaveBeenCalled();
    expect(result.current.enabled).toBe(true);
  });

  it("toggle turns notifications off and persists the setting", async () => {
    const { result } = renderEnabledHook();
    expect(result.current.enabled).toBe(true);

    await act(async () => {
      result.current.toggle();
    });

    expect(result.current.enabled).toBe(false);
    expect(localStorage.getItem(STORAGE_KEY)).toBe("false");
  });

  it("does not subscribe to ticks when disabled", () => {
    renderHook(() => useNotifySuspended());
    expect(tickSubscriber).toBeNull();
  });

  it("fires notification for newly suspended step when tab is not focused", async () => {
    mockedFetchRuns.mockResolvedValue([
      makeRun({ id: "run-s1", step_name: "review", status: "suspended" }),
    ]);
    mockedFetchJob.mockResolvedValue(makeJob({ id: "job-1", name: "Deploy" }));

    renderEnabledHook();

    await act(async () => {
      await tickSubscriber?.({
        type: "tick",
        changed_jobs: ["job-1"],
        timestamp: new Date().toISOString(),
      });
    });

    expect(MockNotification.instances).toHaveLength(1);
    expect(MockNotification.instances[0].title).toBe("Step suspended: review");
    expect(MockNotification.instances[0].options.body).toBe("Job: Deploy");
  });

  it("does not fire notification when tab is focused", async () => {
    vi.spyOn(document, "hasFocus").mockReturnValue(true);
    mockedFetchRuns.mockResolvedValue([
      makeRun({ id: "run-s1", step_name: "review", status: "suspended" }),
    ]);

    renderEnabledHook();

    await act(async () => {
      await tickSubscriber?.({
        type: "tick",
        changed_jobs: ["job-1"],
        timestamp: new Date().toISOString(),
      });
    });

    expect(MockNotification.instances).toHaveLength(0);
    expect(mockedFetchRuns).not.toHaveBeenCalled();
  });

  it("does not re-notify for already-seen suspended runs", async () => {
    mockedFetchRuns.mockResolvedValue([
      makeRun({ id: "run-s1", step_name: "review", status: "suspended" }),
    ]);
    mockedFetchJob.mockResolvedValue(makeJob({ id: "job-1", name: "Deploy" }));

    renderEnabledHook();

    // First tick
    await act(async () => {
      await tickSubscriber?.({
        type: "tick",
        changed_jobs: ["job-1"],
        timestamp: new Date().toISOString(),
      });
    });

    // Second tick with same suspended run
    await act(async () => {
      await tickSubscriber?.({
        type: "tick",
        changed_jobs: ["job-1"],
        timestamp: new Date().toISOString(),
      });
    });

    expect(MockNotification.instances).toHaveLength(1);
  });

  it("uses job objective as fallback when name is null", async () => {
    mockedFetchRuns.mockResolvedValue([
      makeRun({ id: "run-s1", step_name: "review", status: "suspended" }),
    ]);
    mockedFetchJob.mockResolvedValue(makeJob({ id: "job-1", name: null, objective: "Run tests" }));

    renderEnabledHook();

    await act(async () => {
      await tickSubscriber?.({
        type: "tick",
        changed_jobs: ["job-1"],
        timestamp: new Date().toISOString(),
      });
    });

    expect(MockNotification.instances[0].options.body).toBe("Job: Run tests");
  });

  it("clicking notification focuses window and navigates to job", async () => {
    mockedFetchRuns.mockResolvedValue([
      makeRun({ id: "run-s1", step_name: "review", status: "suspended" }),
    ]);
    mockedFetchJob.mockResolvedValue(makeJob({ id: "job-1" }));

    const focusSpy = vi.spyOn(window, "focus").mockImplementation(() => {});

    renderEnabledHook();

    await act(async () => {
      await tickSubscriber?.({
        type: "tick",
        changed_jobs: ["job-1"],
        timestamp: new Date().toISOString(),
      });
    });

    const notification = MockNotification.instances[0];
    notification.onclick?.(new Event("click"));

    expect(focusSpy).toHaveBeenCalled();
    expect(mockNavigate).toHaveBeenCalledWith({
      to: "/jobs/$jobId",
      params: { jobId: "job-1" },
    });
    expect(notification.close).toHaveBeenCalled();
  });

  it("handles multiple suspended steps in one tick", async () => {
    mockedFetchRuns.mockResolvedValue([
      makeRun({ id: "run-s1", step_name: "review", status: "suspended" }),
      makeRun({ id: "run-s2", step_name: "approve", status: "suspended" }),
    ]);
    mockedFetchJob.mockResolvedValue(makeJob({ id: "job-1", name: "Deploy" }));

    renderEnabledHook();

    await act(async () => {
      await tickSubscriber?.({
        type: "tick",
        changed_jobs: ["job-1"],
        timestamp: new Date().toISOString(),
      });
    });

    expect(MockNotification.instances).toHaveLength(2);
    expect(MockNotification.instances[0].title).toBe("Step suspended: review");
    expect(MockNotification.instances[1].title).toBe("Step suspended: approve");
  });
});
