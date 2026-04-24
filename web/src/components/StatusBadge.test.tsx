import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { JobStatusBadge, StepStatusBadge } from "./StatusBadge";
import type { JobStatus, StepDisplayStatus } from "@/lib/types";

describe("JobStatusBadge", () => {
  const allStatuses: JobStatus[] = [
    "pending",
    "running",
    "paused",
    "completed",
    "failed",
    "cancelled",
  ];

  it.each(allStatuses)("renders '%s' status text", (status) => {
    render(<JobStatusBadge status={status} />);
    expect(screen.getByText(status)).toBeInTheDocument();
  });

  it("applies animate-pulse class for running status", () => {
    const { container } = render(<JobStatusBadge status="running" />);
    const badge = container.querySelector("[class*='animate-pulse']");
    expect(badge).not.toBeNull();
  });

  it("does not apply animate-pulse for non-running statuses", () => {
    const { container } = render(<JobStatusBadge status="completed" />);
    const badge = container.querySelector("[class*='animate-pulse']");
    expect(badge).toBeNull();
  });
});

describe("StepStatusBadge", () => {
  const allStatuses: StepDisplayStatus[] = [
    "running",
    "suspended",
    "delegated",
    "completed",
    "failed",
    "pending",
    "escalated",
    "stranded",
  ];

  it.each(allStatuses)("renders '%s' status text", (status) => {
    render(<StepStatusBadge status={status} />);
    expect(screen.getByText(status)).toBeInTheDocument();
  });

  it("applies animate-pulse for running status", () => {
    const { container } = render(<StepStatusBadge status="running" />);
    expect(container.querySelector("[class*='animate-pulse']")).not.toBeNull();
  });

  it("applies animate-pulse for stranded (process is idle but alive)", () => {
    const { container } = render(<StepStatusBadge status="stranded" />);
    expect(container.querySelector("[class*='animate-pulse']")).not.toBeNull();
  });

  it("does NOT pulse for escalated (run itself is terminal)", () => {
    const { container } = render(<StepStatusBadge status="escalated" />);
    expect(container.querySelector("[class*='animate-pulse']")).toBeNull();
  });

  it("does not apply animate-pulse for pending", () => {
    const { container } = render(<StepStatusBadge status="pending" />);
    expect(container.querySelector("[class*='animate-pulse']")).toBeNull();
  });

  it("uses red theme for escalated", () => {
    const { container } = render(<StepStatusBadge status="escalated" />);
    const html = container.innerHTML;
    // red-700 for light-mode text; verifies we picked the escalated palette
    expect(html).toContain("text-red-700");
  });

  it("uses amber theme for stranded", () => {
    const { container } = render(<StepStatusBadge status="stranded" />);
    const html = container.innerHTML;
    expect(html).toContain("text-amber-700");
  });
});
