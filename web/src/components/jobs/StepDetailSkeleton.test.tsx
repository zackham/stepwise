import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { StepDetailSkeleton } from "./StepDetailPanel";

describe("StepDetailSkeleton", () => {
  it("renders the skeleton container with test id", () => {
    render(<StepDetailSkeleton />);
    expect(screen.getByTestId("step-detail-skeleton")).toBeInTheDocument();
  });

  it("contains pulse-animated skeleton elements", () => {
    const { container } = render(<StepDetailSkeleton />);
    const skeletons = container.querySelectorAll('[data-slot="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
    for (const el of skeletons) {
      expect(el.className).toContain("animate-pulse");
    }
  });

  it("has the fade-in animation class", () => {
    render(<StepDetailSkeleton />);
    const root = screen.getByTestId("step-detail-skeleton");
    expect(root.className).toContain("animate-fade-in");
  });
});
