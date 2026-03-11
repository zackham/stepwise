import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { JobStatusBadge, StepStatusBadge } from "./StatusBadge";
import type { JobStatus, StepRunStatus } from "@/lib/types";

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
  const allStatuses: Array<StepRunStatus | "pending"> = [
    "running",
    "suspended",
    "delegated",
    "completed",
    "failed",
    "pending",
  ];

  it.each(allStatuses)("renders '%s' status text", (status) => {
    render(<StepStatusBadge status={status} />);
    expect(screen.getByText(status)).toBeInTheDocument();
  });

  it("applies animate-pulse only for running status", () => {
    const { container: running } = render(
      <StepStatusBadge status="running" />
    );
    expect(running.querySelector("[class*='animate-pulse']")).not.toBeNull();

    const { container: pending } = render(
      <StepStatusBadge status="pending" />
    );
    expect(pending.querySelector("[class*='animate-pulse']")).toBeNull();
  });
});
