import { describe, it, expect } from "vitest";
import { JOB_STATUS_COLORS, STEP_STATUS_COLORS, STEP_PENDING_COLORS } from "./status-colors";
import type { JobStatus, StepRunStatus } from "./types";

describe("JOB_STATUS_COLORS", () => {
  const allJobStatuses: JobStatus[] = [
    "staged",
    "pending",
    "running",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "archived",
  ];

  it("has a color definition for every JobStatus", () => {
    for (const status of allJobStatuses) {
      const colors = JOB_STATUS_COLORS[status];
      expect(colors, `missing colors for '${status}'`).toBeDefined();
      expect(colors.bg).toBeTruthy();
      expect(colors.text).toBeTruthy();
      expect(colors.ring).toBeTruthy();
      expect(colors.dot).toBeTruthy();
    }
  });

  it("has no extra keys beyond known statuses", () => {
    const keys = Object.keys(JOB_STATUS_COLORS);
    expect(keys.sort()).toEqual([...allJobStatuses].sort());
  });
});

describe("STEP_STATUS_COLORS", () => {
  const allStepStatuses: StepRunStatus[] = [
    "running",
    "suspended",
    "delegated",
    "completed",
    "failed",
    "cancelled",
    "skipped",
    "throttled",
  ];

  it("has a color definition for every StepRunStatus", () => {
    for (const status of allStepStatuses) {
      const colors = STEP_STATUS_COLORS[status];
      expect(colors, `missing colors for '${status}'`).toBeDefined();
      expect(colors.bg).toBeTruthy();
      expect(colors.text).toBeTruthy();
      expect(colors.border).toBeTruthy();
      expect(colors.ring).toBeTruthy();
    }
  });

  it("has no extra keys beyond known statuses", () => {
    const keys = Object.keys(STEP_STATUS_COLORS);
    expect(keys.sort()).toEqual([...allStepStatuses].sort());
  });
});

describe("STEP_PENDING_COLORS", () => {
  it("has all required color fields", () => {
    expect(STEP_PENDING_COLORS.bg).toBeTruthy();
    expect(STEP_PENDING_COLORS.text).toBeTruthy();
    expect(STEP_PENDING_COLORS.border).toBeTruthy();
    expect(STEP_PENDING_COLORS.ring).toBeTruthy();
  });
});
