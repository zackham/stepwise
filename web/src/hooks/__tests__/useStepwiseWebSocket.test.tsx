import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useStepwiseWebSocket } from "../useStepwiseWebSocket";

vi.mock("@/lib/api", () => ({
  fetchJob: vi.fn(() => Promise.resolve(null)),
}));

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: MockWebSocket[] = [];

  url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }
}

function createWrapper(queryClient: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

describe("useStepwiseWebSocket tick handling", () => {
  beforeEach(() => {
    MockWebSocket.instances = [];
    vi.stubGlobal("WebSocket", MockWebSocket);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  function setup() {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const hook = renderHook(() => useStepwiseWebSocket(), {
      wrapper: createWrapper(queryClient),
    });
    const ws = MockWebSocket.instances[MockWebSocket.instances.length - 1];
    act(() => {
      ws.readyState = MockWebSocket.OPEN;
      ws.onopen?.();
    });
    return { queryClient, invalidateSpy, hook, ws };
  }

  it("invalidates the job detail query for each changed job on tick", () => {
    const { invalidateSpy, hook, ws } = setup();

    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({ type: "tick", changed_jobs: ["job-1", "job-2"] }),
      });
    });

    // The single-job detail query (used by JobDetailPage status badge,
    // banners, toasts) must be invalidated — without it the page freezes
    // at the mount-time job status.
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["job", "job-1"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["job", "job-2"] });

    hook.unmount();
  });

  it("still invalidates the scoped step-detail queries on tick", () => {
    const { invalidateSpy, hook, ws } = setup();

    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({ type: "tick", changed_jobs: ["job-1"] }),
      });
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["runs", "job-1"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["events", "job-1"] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["jobTree", "job-1"] });

    hook.unmount();
  });

  it("invalidates the job detail query for stale jobs", () => {
    const { invalidateSpy, hook, ws } = setup();

    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({ type: "stale_jobs", jobs: [{ id: "job-9" }] }),
      });
    });

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ["job", "job-9"] });

    hook.unmount();
  });

  it("does not invalidate job queries when the tick has no changed jobs", () => {
    const { invalidateSpy, hook, ws } = setup();

    act(() => {
      ws.onmessage?.({
        data: JSON.stringify({ type: "tick", changed_jobs: [] }),
      });
    });

    expect(invalidateSpy).not.toHaveBeenCalled();

    hook.unmount();
  });
});
