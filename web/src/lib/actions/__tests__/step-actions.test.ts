import { describe, it, expect } from "vitest";
import type { StepRun } from "@/lib/types";
import type { StepEntity } from "../step-actions";
import { getActionsForEntity } from "../index";

// ── Helpers ─────────────────────────────────────────────────────────────

function makeStepEntity(overrides: Partial<StepEntity> = {}): StepEntity {
  return {
    jobId: "job-1",
    stepDef: {
      name: "my-step",
      description: "",
      executor: { type: "script", config: {}, decorators: [] },
      inputs: [],
      outputs: ["result"],
      after: [],
      exit_rules: [],
      idempotency: "none",
      limits: null,
    },
    latestRun: null,
    ...overrides,
  };
}

function makeRun(overrides: Partial<StepRun> = {}): StepRun {
  return {
    id: "run-1",
    job_id: "job-1",
    step_name: "my-step",
    attempt: 1,
    status: "completed",
    inputs: null,
    dep_run_ids: null,
    result: null,
    error: null,
    error_category: null,
    traceback: null,
    executor_state: null,
    watch: null,
    sub_job_id: null,
    started_at: null,
    completed_at: null,
    ...overrides,
  };
}

function stepActionIds(entity: StepEntity): string[] {
  return getActionsForEntity("step", entity).map((a) => a.id);
}

// ── Tests ──────────────────────────────────────────────────────────────

describe("step actions", () => {
  it("no run → rerun available", () => {
    const ids = stepActionIds(makeStepEntity({ latestRun: null }));
    expect(ids).toContain("step.rerun");
  });

  it("completed → rerun available", () => {
    const ids = stepActionIds(
      makeStepEntity({ latestRun: makeRun({ status: "completed" }) }),
    );
    expect(ids).toContain("step.rerun");
  });

  it("running → cancel available, rerun not", () => {
    const ids = stepActionIds(
      makeStepEntity({ latestRun: makeRun({ status: "running" }) }),
    );
    expect(ids).toContain("step.cancel-run");
    expect(ids).not.toContain("step.rerun");
  });

  it("suspended → cancel available", () => {
    const ids = stepActionIds(
      makeStepEntity({ latestRun: makeRun({ status: "suspended" }) }),
    );
    expect(ids).toContain("step.cancel-run");
  });

  it("copy name always available", () => {
    const ids = stepActionIds(makeStepEntity());
    expect(ids).toContain("step.copy-name");
  });

  it("view output only with artifact", () => {
    const ids = stepActionIds(
      makeStepEntity({
        latestRun: makeRun({
          status: "completed",
          result: {
            artifact: { result: "done" },
            sidecar: {
              decisions_made: [],
              assumptions: [],
              open_questions: [],
              constraints_discovered: [],
            },
            executor_meta: {},
            workspace: "/tmp",
            timestamp: new Date().toISOString(),
          },
        }),
      }),
    );
    expect(ids).toContain("step.view-output");
  });

  it("view output hidden without run", () => {
    const ids = stepActionIds(makeStepEntity({ latestRun: null }));
    expect(ids).not.toContain("step.view-output");
  });
});
