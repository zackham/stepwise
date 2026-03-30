import { describe, it, expect } from "vitest";
import type { Job, JobStatus } from "@/lib/types";
import {
  canStart,
  canPause,
  canResume,
  canRetry,
  canCancel,
  canReset,
  canArchive,
  isStale,
  JOB_ACTIONS,
} from "../job-actions";
import { getActionsForEntity, groupActions } from "../index";

// ── Helpers ─────────────────────────────────────────────────────────────

let jobCounter = 0;

function makeJob(overrides: Partial<Job> = {}): Job {
  jobCounter++;
  return {
    id: `job-${jobCounter}`,
    name: null,
    objective: `test-job-${jobCounter}`,
    status: "completed",
    inputs: {},
    parent_job_id: null,
    parent_step_run_id: null,
    workspace_path: "/tmp",
    config: { max_sub_job_depth: 3, timeout_minutes: null, metadata: {} },
    workflow: { steps: {} },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    created_by: "server",
    runner_pid: null,
    heartbeat_at: null,
    has_suspended_steps: false,
    job_group: null,
    depends_on: [],
    ...overrides,
  };
}

function actionIds(job: Job): string[] {
  return getActionsForEntity("job", job).map((a) => a.id);
}

// ── Status predicates ──────────────────────────────────────────────────

describe("canRetry", () => {
  it("includes completed", () => {
    expect(canRetry("completed")).toBe(true);
  });
  it("includes failed", () => {
    expect(canRetry("failed")).toBe(true);
  });
  it("excludes running", () => {
    expect(canRetry("running")).toBe(false);
  });
});

describe("canCancel", () => {
  it("includes running", () => {
    expect(canCancel("running")).toBe(true);
  });
  it("excludes completed", () => {
    expect(canCancel("completed")).toBe(false);
  });
});

describe("canPause", () => {
  it("only running", () => {
    expect(canPause("running")).toBe(true);
    expect(canPause("paused")).toBe(false);
  });
});

describe("canArchive", () => {
  it("includes completed/failed/cancelled", () => {
    expect(canArchive("completed")).toBe(true);
    expect(canArchive("failed")).toBe(true);
    expect(canArchive("cancelled")).toBe(true);
  });
  it("excludes running/paused", () => {
    expect(canArchive("running")).toBe(false);
    expect(canArchive("paused")).toBe(false);
  });
});

// ── isStale ────────────────────────────────────────────────────────────

describe("isStale", () => {
  it("true for CLI-owned with old heartbeat", () => {
    expect(
      isStale({
        status: "running",
        created_by: "cli:123",
        heartbeat_at: new Date(Date.now() - 120_000).toISOString(),
      }),
    ).toBe(true);
  });
  it("false for server-owned", () => {
    expect(
      isStale({
        status: "running",
        created_by: "server",
        heartbeat_at: new Date(Date.now() - 120_000).toISOString(),
      }),
    ).toBe(false);
  });
  it("false for recent heartbeat", () => {
    expect(
      isStale({
        status: "running",
        created_by: "cli:1",
        heartbeat_at: new Date(Date.now() - 5_000).toISOString(),
      }),
    ).toBe(false);
  });
});

// ── Action availability by status ──────────────────────────────────────

describe("getActionsForEntity('job', ...)", () => {
  it("running job includes pause, cancel, inject-context", () => {
    const ids = actionIds(makeJob({ status: "running" }));
    expect(ids).toContain("job.pause");
    expect(ids).toContain("job.cancel");
    expect(ids).toContain("job.inject-context");
  });

  it("running job excludes retry, archive", () => {
    const ids = actionIds(makeJob({ status: "running" }));
    expect(ids).not.toContain("job.retry");
    expect(ids).not.toContain("job.archive");
  });

  it("failed job includes retry, archive, reset, delete", () => {
    const ids = actionIds(makeJob({ status: "failed" }));
    expect(ids).toContain("job.retry");
    expect(ids).toContain("job.archive");
    expect(ids).toContain("job.reset");
    expect(ids).toContain("job.delete");
  });

  it("completed job includes retry", () => {
    const ids = actionIds(makeJob({ status: "completed" }));
    expect(ids).toContain("job.retry");
  });

  it("stale job includes take-over", () => {
    const ids = actionIds(
      makeJob({
        status: "running",
        created_by: "cli:99",
        heartbeat_at: new Date(Date.now() - 120_000).toISOString(),
      }),
    );
    expect(ids).toContain("job.take-over");
  });

  it("archived job: no lifecycle actions, unarchive in organize", () => {
    const actions = getActionsForEntity("job", makeJob({ status: "archived" }));
    const lifecycle = actions.filter((a) => a.group === "lifecycle");
    expect(lifecycle).toHaveLength(0);
    const organize = actions.filter((a) => a.group === "organize");
    expect(organize.map((a) => a.id)).toEqual(["job.unarchive"]);
  });
});

// ── Grouping ───────────────────────────────────────────────────────────

describe("groupActions", () => {
  it("groups appear in order: lifecycle, organize, copy, navigate, danger", () => {
    const actions = getActionsForEntity("job", makeJob({ status: "completed" }));
    const groups = groupActions(actions).map((g) => g.group);
    expect(groups).toEqual(["lifecycle", "organize", "copy", "navigate", "danger"]);
  });
});

// ── Sub-menu ───────────────────────────────────────────────────────────

describe("job.copy sub-menu", () => {
  it("has 3 children", () => {
    const copyAction = JOB_ACTIONS.find((a) => a.id === "job.copy");
    expect(copyAction?.children).toHaveLength(3);
    expect(copyAction?.children?.map((c) => c.id)).toEqual([
      "job.copy.id",
      "job.copy.name",
      "job.copy.inputs",
    ]);
  });

  it("job.copy.name hidden when name is null", () => {
    const copyAction = JOB_ACTIONS.find((a) => a.id === "job.copy");
    const copyName = copyAction?.children?.find((c) => c.id === "job.copy.name");
    expect(copyName?.isAvailable(makeJob({ name: null }))).toBe(false);
  });

  it("job.copy.name visible when name is set", () => {
    const copyAction = JOB_ACTIONS.find((a) => a.id === "job.copy");
    const copyName = copyAction?.children?.find((c) => c.id === "job.copy.name");
    expect(copyName?.isAvailable(makeJob({ name: "my-job" }))).toBe(true);
  });
});
