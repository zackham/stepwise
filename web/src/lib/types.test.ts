import { describe, it, expect } from "vitest";
import { EVENT_TYPES } from "./types";

describe("EVENT_TYPES", () => {
  it("contains all step lifecycle events", () => {
    expect(EVENT_TYPES.STEP_STARTED).toBe("step.started");
    expect(EVENT_TYPES.STEP_COMPLETED).toBe("step.completed");
    expect(EVENT_TYPES.STEP_FAILED).toBe("step.failed");
    expect(EVENT_TYPES.STEP_SUSPENDED).toBe("step.suspended");
    expect(EVENT_TYPES.STEP_DELEGATED).toBe("step.delegated");
  });

  it("contains all job lifecycle events", () => {
    expect(EVENT_TYPES.JOB_STARTED).toBe("job.started");
    expect(EVENT_TYPES.JOB_COMPLETED).toBe("job.completed");
    expect(EVENT_TYPES.JOB_FAILED).toBe("job.failed");
    expect(EVENT_TYPES.JOB_PAUSED).toBe("job.paused");
    expect(EVENT_TYPES.JOB_RESUMED).toBe("job.resumed");
  });

  it("contains all engine action events", () => {
    expect(EVENT_TYPES.EXIT_RESOLVED).toBe("exit.resolved");
    expect(EVENT_TYPES.WATCH_FULFILLED).toBe("watch.fulfilled");
    expect(EVENT_TYPES.HUMAN_RERUN).toBe("human.rerun");
    expect(EVENT_TYPES.LOOP_ITERATION).toBe("loop.iteration");
    expect(EVENT_TYPES.LOOP_MAX_REACHED).toBe("loop.max_reached");
    expect(EVENT_TYPES.CONTEXT_INJECTED).toBe("context.injected");
  });

  it("all values follow dot-notation pattern", () => {
    for (const value of Object.values(EVENT_TYPES)) {
      expect(value).toMatch(/^[a-z]+\.[a-z_]+$/);
    }
  });

  it("all values are unique", () => {
    const values = Object.values(EVENT_TYPES);
    expect(new Set(values).size).toBe(values.length);
  });
});
