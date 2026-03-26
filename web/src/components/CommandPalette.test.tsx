import { describe, it, expect, vi, beforeEach, beforeAll } from "vitest";

// cmdk uses ResizeObserver and scrollIntoView internally
beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  Element.prototype.scrollIntoView = vi.fn();
});
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import type { ReactNode } from "react";
import { CommandPalette } from "./CommandPalette";

// Mock navigation
const mockNavigate = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => mockNavigate,
}));

// Mock API
vi.mock("@/lib/api", () => ({
  fetchJobs: vi.fn().mockResolvedValue([
    {
      id: "j1",
      objective: "Deploy app",
      name: "deploy-prod",
      status: "running",
      workflow: { steps: {} },
      inputs: {},
      workspace_path: "/tmp",
      config: { max_sub_job_depth: 3, timeout_minutes: null, metadata: {} },
      created_at: "2025-01-01T00:00:00Z",
      updated_at: "2025-01-02T00:00:00Z",
      created_by: "server",
      parent_job_id: null,
      parent_step_run_id: null,
      runner_pid: null,
      heartbeat_at: null,
    },
    {
      id: "j2",
      objective: "Run tests",
      name: null,
      status: "completed",
      workflow: { steps: {} },
      inputs: {},
      workspace_path: "/tmp",
      config: { max_sub_job_depth: 3, timeout_minutes: null, metadata: {} },
      created_at: "2025-01-01T00:00:00Z",
      updated_at: "2025-01-01T12:00:00Z",
      created_by: "server",
      parent_job_id: null,
      parent_step_run_id: null,
      runner_pid: null,
      heartbeat_at: null,
    },
  ]),
  fetchLocalFlows: vi.fn().mockResolvedValue([
    {
      path: "/flows/my-flow.flow.yaml",
      name: "my-flow",
      description: "A test flow",
      steps_count: 3,
      modified_at: "2025-01-01T00:00:00Z",
      is_directory: false,
      executor_types: ["script"],
    },
  ]),
  fetchFlowStats: vi.fn().mockResolvedValue({ total: 1 }),
}));

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("CommandPalette", () => {
  it("does not render when closed", () => {
    render(<CommandPalette />, { wrapper: createWrapper() });
    expect(screen.queryByPlaceholderText(/search/i)).not.toBeInTheDocument();
  });

  it("opens on Cmd+K", async () => {
    render(<CommandPalette />, { wrapper: createWrapper() });
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument();
  });

  it("opens on Ctrl+K", () => {
    render(<CommandPalette />, { wrapper: createWrapper() });
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument();
  });

  it("closes on second Cmd+K", () => {
    render(<CommandPalette />, { wrapper: createWrapper() });
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    expect(screen.getByPlaceholderText(/search/i)).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    expect(screen.queryByPlaceholderText(/search/i)).not.toBeInTheDocument();
  });

  it("shows page navigation items when open", () => {
    render(<CommandPalette />, { wrapper: createWrapper() });
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    expect(screen.getByText("Jobs")).toBeInTheDocument();
    expect(screen.getByText("Flows")).toBeInTheDocument();
    expect(screen.getByText("Settings")).toBeInTheDocument();
  });

  it("shows jobs after data loads", async () => {
    render(<CommandPalette />, { wrapper: createWrapper() });
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    await waitFor(() => {
      expect(screen.getByText("deploy-prod")).toBeInTheDocument();
    });
    expect(screen.getByText("Run tests")).toBeInTheDocument();
  });

  it("shows flows after data loads", async () => {
    render(<CommandPalette />, { wrapper: createWrapper() });
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    await waitFor(() => {
      expect(screen.getByText("my-flow")).toBeInTheDocument();
    });
  });

  it("navigates to job on select", async () => {
    render(<CommandPalette />, { wrapper: createWrapper() });
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    await waitFor(() => {
      expect(screen.getByText("deploy-prod")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("deploy-prod"));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: "/jobs/$jobId",
      params: { jobId: "j1" },
    });
  });

  it("navigates to flow on select", async () => {
    render(<CommandPalette />, { wrapper: createWrapper() });
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    await waitFor(() => {
      expect(screen.getByText("my-flow")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText("my-flow"));
    expect(mockNavigate).toHaveBeenCalledWith({
      to: "/flows/$flowName",
      params: { flowName: "my-flow" },
    });
  });

  it("navigates to pages on select", () => {
    render(<CommandPalette />, { wrapper: createWrapper() });
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    fireEvent.click(screen.getByText("Settings"));
    expect(mockNavigate).toHaveBeenCalledWith({ to: "/settings" });
  });
});
