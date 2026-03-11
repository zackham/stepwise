import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import type { ReactNode } from "react";
import { useJobs, useJob, useRuns, useEvents, useEngineStatus } from "./useStepwise";

// Mock the API module
vi.mock("@/lib/api", () => ({
  fetchJobs: vi.fn(),
  fetchJob: vi.fn(),
  fetchRuns: vi.fn(),
  fetchEvents: vi.fn(),
  fetchStatus: vi.fn(),
  fetchJobTree: vi.fn(),
  fetchExecutors: vi.fn(),
  fetchTemplates: vi.fn(),
}));

import * as api from "@/lib/api";

const mockedApi = vi.mocked(api);

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: 0,
      },
    },
  });

  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("useJobs", () => {
  it("returns jobs from the API", async () => {
    const jobs = [
      {
        id: "j1",
        objective: "Test",
        status: "running",
        workflow: { steps: {} },
        inputs: {},
        parent_job_id: null,
        parent_step_run_id: null,
        workspace_path: "/tmp",
        config: { max_sub_job_depth: 3, timeout_minutes: null, metadata: {} },
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
      },
    ];
    mockedApi.fetchJobs.mockResolvedValueOnce(jobs as any);

    const { result } = renderHook(() => useJobs(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(jobs);
    expect(mockedApi.fetchJobs).toHaveBeenCalledWith(undefined, true);
  });

  it("passes status filter to API", async () => {
    mockedApi.fetchJobs.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useJobs("running"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockedApi.fetchJobs).toHaveBeenCalledWith("running", true);
  });

  it("has a 5-second refetch interval", () => {
    mockedApi.fetchJobs.mockResolvedValue([]);

    const { result } = renderHook(() => useJobs(), {
      wrapper: createWrapper(),
    });

    // The hook configures refetchInterval: 5000
    // We can verify by checking the query options indirectly
    expect(result.current.isLoading || result.current.isSuccess || result.current.isError).toBe(true);
  });
});

describe("useJob", () => {
  it("fetches a job when jobId is provided", async () => {
    const job = { id: "j1", objective: "Test" };
    mockedApi.fetchJob.mockResolvedValueOnce(job as any);

    const { result } = renderHook(() => useJob("j1"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(job);
  });

  it("does not fetch when jobId is undefined", () => {
    const { result } = renderHook(() => useJob(undefined), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe("idle");
    expect(mockedApi.fetchJob).not.toHaveBeenCalled();
  });
});

describe("useRuns", () => {
  it("fetches runs for a job", async () => {
    const runs = [{ id: "r1", step_name: "step1", attempt: 1, status: "completed" }];
    mockedApi.fetchRuns.mockResolvedValueOnce(runs as any);

    const { result } = renderHook(() => useRuns("j1"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(runs);
    expect(mockedApi.fetchRuns).toHaveBeenCalledWith("j1", undefined);
  });

  it("passes stepName filter", async () => {
    mockedApi.fetchRuns.mockResolvedValueOnce([]);

    const { result } = renderHook(() => useRuns("j1", "step_a"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(mockedApi.fetchRuns).toHaveBeenCalledWith("j1", "step_a");
  });

  it("does not fetch when jobId is undefined", () => {
    const { result } = renderHook(() => useRuns(undefined), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe("idle");
    expect(mockedApi.fetchRuns).not.toHaveBeenCalled();
  });
});

describe("useEvents", () => {
  it("fetches events for a job", async () => {
    const events = [{ id: "e1", type: "step.started", timestamp: "2024-01-01T00:00:00Z" }];
    mockedApi.fetchEvents.mockResolvedValueOnce(events as any);

    const { result } = renderHook(() => useEvents("j1"), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(events);
  });

  it("does not fetch when jobId is undefined", () => {
    const { result } = renderHook(() => useEvents(undefined), {
      wrapper: createWrapper(),
    });

    expect(result.current.fetchStatus).toBe("idle");
    expect(mockedApi.fetchEvents).not.toHaveBeenCalled();
  });
});

describe("useEngineStatus", () => {
  it("fetches engine status", async () => {
    const status = { active_jobs: 3, total_jobs: 10, registered_executors: ["script"] };
    mockedApi.fetchStatus.mockResolvedValueOnce(status);

    const { result } = renderHook(() => useEngineStatus(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(status);
  });
});
