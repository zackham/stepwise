import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { Breadcrumb } from "../Breadcrumb";

// Mock TanStack Router Link as a simple anchor
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [k: string]: unknown }) => (
    <a href={to} {...props}>{children}</a>
  ),
}));

// Mock useIsMobile — default to desktop
const mockUseIsMobile = vi.fn(() => false);
vi.mock("@/hooks/useMediaQuery", () => ({
  useIsMobile: () => mockUseIsMobile(),
}));

describe("Breadcrumb", () => {
  it("renders all segments", () => {
    render(
      <Breadcrumb
        segments={[
          { label: "Jobs", to: "/jobs" },
          { label: "my-job", to: "/jobs/123" },
          { label: "Events" },
        ]}
      />
    );
    expect(screen.getByText("Jobs")).toBeDefined();
    expect(screen.getByText("my-job")).toBeDefined();
    expect(screen.getByText("Events")).toBeDefined();
  });

  it("renders clickable segments as links", () => {
    render(
      <Breadcrumb
        segments={[
          { label: "Jobs", to: "/jobs" },
          { label: "Events" },
        ]}
      />
    );
    const link = screen.getByText("Jobs");
    expect(link.tagName).toBe("A");
  });

  it("renders final segment as plain text, not a link", () => {
    render(
      <Breadcrumb
        segments={[
          { label: "Jobs", to: "/jobs" },
          { label: "Events" },
        ]}
      />
    );
    const final = screen.getByText("Events");
    expect(final.tagName).toBe("SPAN");
  });

  it("renders separators between segments", () => {
    const { container } = render(
      <Breadcrumb
        segments={[
          { label: "Jobs", to: "/jobs" },
          { label: "my-job", to: "/jobs/123" },
          { label: "Events" },
        ]}
      />
    );
    // ChevronRight renders as SVG elements — there should be 2 for 3 segments
    const svgs = container.querySelectorAll("svg");
    expect(svgs.length).toBe(2);
  });

  it("truncates long labels", () => {
    render(
      <Breadcrumb
        segments={[
          { label: "A very long job name that should be truncated" },
        ]}
      />
    );
    const el = screen.getByText("A very long job name that should be truncated");
    expect(el.className).toContain("truncate");
    expect(el.getAttribute("title")).toBe("A very long job name that should be truncated");
  });

  it("collapses intermediate segments on mobile", () => {
    mockUseIsMobile.mockReturnValue(true);
    render(
      <Breadcrumb
        segments={[
          { label: "Jobs", to: "/jobs" },
          { label: "my-job", to: "/jobs/123" },
          { label: "Events" },
        ]}
      />
    );
    expect(screen.getByText("Jobs")).toBeDefined();
    expect(screen.getByText("Events")).toBeDefined();
    expect(screen.getByText("…")).toBeDefined();
    expect(screen.queryByText("my-job")).toBeNull();
    mockUseIsMobile.mockReturnValue(false);
  });

  it("shows all segments on desktop even with 3+ segments", () => {
    mockUseIsMobile.mockReturnValue(false);
    render(
      <Breadcrumb
        segments={[
          { label: "Jobs", to: "/jobs" },
          { label: "my-job", to: "/jobs/123" },
          { label: "Events" },
        ]}
      />
    );
    expect(screen.getByText("Jobs")).toBeDefined();
    expect(screen.getByText("my-job")).toBeDefined();
    expect(screen.getByText("Events")).toBeDefined();
  });
});
