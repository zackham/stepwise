import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PausedJobBanner } from "./PausedJobBanner";
import type { PauseCause } from "@/lib/types";

function renderBanner(cause: PauseCause, strandedCount = 0, onViewStep = vi.fn()) {
  return render(
    <PausedJobBanner
      cause={cause}
      strandedCount={strandedCount}
      onViewStep={onViewStep}
    />
  );
}

describe("PausedJobBanner", () => {
  it("renders the PAUSED label", () => {
    renderBanner({ reason: "escalated", step: "gate", rule: "still_broken" });
    expect(screen.getByText("paused")).toBeInTheDocument();
  });

  it("names the escalating step as a clickable link", () => {
    const onViewStep = vi.fn();
    renderBanner(
      { reason: "escalated", step: "text-quality-check", rule: "still_broken" },
      0,
      onViewStep,
    );
    const stepButton = screen.getByText("text-quality-check");
    expect(stepButton.tagName).toBe("BUTTON");
    fireEvent.click(stepButton);
    expect(onViewStep).toHaveBeenCalledWith("text-quality-check");
  });

  it("includes the rule name", () => {
    renderBanner({ reason: "escalated", step: "gate", rule: "still_broken" });
    expect(screen.getByText("still_broken")).toBeInTheDocument();
  });

  it("renders max_iterations_reached with target", () => {
    renderBanner({
      reason: "max_iterations_reached",
      step: "check",
      target: "refine",
    });
    expect(
      screen.getByText(/loop → refine hit max iterations/),
    ).toBeInTheDocument();
    expect(screen.getByText("check")).toBeInTheDocument();
  });

  it("falls back to a reason-only message for unknown reasons", () => {
    renderBanner({ reason: "some_future_reason", step: "x" });
    expect(screen.getByText(/reason: some_future_reason/)).toBeInTheDocument();
  });

  it("shows stranded count when > 0", () => {
    renderBanner({ reason: "escalated", step: "s", rule: "r" }, 3);
    expect(screen.getByText("3 stranded")).toBeInTheDocument();
  });

  it("hides stranded indicator when count is 0", () => {
    renderBanner({ reason: "escalated", step: "s", rule: "r" }, 0);
    expect(screen.queryByText(/stranded/)).toBeNull();
  });

  it("has a stable testid for integration/e2e", () => {
    renderBanner({ reason: "escalated", step: "s", rule: "r" });
    expect(screen.getByTestId("paused-job-banner")).toBeInTheDocument();
  });

  it("formats the pause timestamp when provided", () => {
    renderBanner({
      reason: "escalated",
      step: "s",
      rule: "r",
      at: "2026-04-23T19:35:04.008Z",
    });
    // Time format is locale-dependent but must contain a colon
    expect(screen.getByText(/@ /)).toBeInTheDocument();
  });
});
