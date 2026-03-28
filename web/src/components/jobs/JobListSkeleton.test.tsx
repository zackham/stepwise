import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";

vi.mock("@tanstack/react-router", () => ({
  useSearch: () => ({}),
  useNavigate: () => vi.fn(),
}));

// Mock the hooks to return loading state
vi.mock("@/hooks/useStepwise", () => ({
  useJobs: () => ({ data: [], isLoading: true }),
  useStepwiseMutations: () => ({
    deleteAllJobs: { mutate: vi.fn(), isPending: false },
    cancelJob: { mutate: vi.fn(), isPending: false },
    resumeJob: { mutate: vi.fn(), isPending: false },
    deleteJob: { mutate: vi.fn(), isPending: false },
  }),
}));

import { JobList } from "./JobList";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

describe("JobList loading skeleton", () => {
  it("renders skeleton placeholders when loading", () => {
    render(
      <JobList selectedJobId={null} onSelectJob={vi.fn()} />,
      { wrapper: createWrapper() },
    );
    const skeleton = screen.getByTestId("job-list-skeleton");
    expect(skeleton).toBeInTheDocument();
    // Should have multiple skeleton items
    const skeletonItems = skeleton.querySelectorAll('[data-slot="skeleton"]');
    expect(skeletonItems.length).toBeGreaterThan(5);
  });

  it("skeleton items have animate-pulse class", () => {
    render(
      <JobList selectedJobId={null} onSelectJob={vi.fn()} />,
      { wrapper: createWrapper() },
    );
    const skeleton = screen.getByTestId("job-list-skeleton");
    const items = skeleton.querySelectorAll('[data-slot="skeleton"]');
    for (const item of items) {
      expect(item.className).toContain("animate-pulse");
    }
  });
});
