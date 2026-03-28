import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DataFlowPanel } from "./DataFlowPanel";
import type { Job, StepDefinition, StepRun } from "@/lib/types";

function makeStep(step: Partial<StepDefinition> = {}): StepDefinition {
  return {
    name: "writer",
    description: "",
    outputs: ["result", "summary"],
    executor: { type: "agent", config: {}, decorators: [] },
    inputs: [
      {
        local_name: "person",
        source_step: "$job",
        source_field: "user",
      },
    ],
    after: [],
    exit_rules: [
      {
        name: "retry",
        type: "expression",
        config: { action: "loop", condition: "attempt < 3", target: "writer", max_iterations: 3 },
        priority: 1,
      },
    ],
    idempotency: "always",
    limits: null,
    ...step,
  };
}

function makeJob(stepDef: StepDefinition): Job {
  return {
    id: "job-1",
    objective: "Test objective",
    name: "Test job",
    workflow: { steps: { [stepDef.name]: stepDef } },
    status: "running",
    inputs: { user: "Ada" },
    parent_job_id: null,
    parent_step_run_id: null,
    workspace_path: "/tmp/job-1",
    config: { max_sub_job_depth: 1, timeout_minutes: null, metadata: {} },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    created_by: "server",
    runner_pid: null,
    heartbeat_at: null,
    job_group: null,
    depends_on: [],
  };
}

function makeRun(overrides: Partial<StepRun> = {}): StepRun {
  return {
    id: "run-1",
    job_id: "job-1",
    step_name: "writer",
    attempt: 1,
    status: "completed",
    inputs: { person: "Ada Lovelace" },
    dep_run_ids: null,
    result: {
      artifact: { result: "done", summary: "brief" },
      sidecar: {
        decisions_made: [],
        assumptions: [],
        open_questions: [],
        constraints_discovered: [],
      },
      executor_meta: {},
      workspace: "/tmp/job-1",
      timestamp: new Date().toISOString(),
    },
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

describe("DataFlowPanel", () => {
  it("renders the step inspector for agent steps with full prompt, bindings, outputs, and exit rules", () => {
    const fullPrompt = `Write a full response for $person.name.\n${"x".repeat(220)}\nEND_MARKER`;
    const stepDef = makeStep({
      executor: {
        type: "agent",
        config: { prompt: fullPrompt },
        decorators: [],
      },
    });

    render(
      <DataFlowPanel
        selection={{ kind: "step", stepName: "writer" }}
        job={makeJob(stepDef)}
        latestRuns={{ writer: makeRun() }}
        outputs={null}
        onClose={() => {}}
      />,
    );

    expect(screen.getByText("writer")).toBeInTheDocument();
    expect(screen.getAllByText("Agent").length).toBeGreaterThan(0);
    expect(screen.getByText("$person.name")).toBeInTheDocument();
    expect(screen.getByText("Agent Prompt").parentElement).toHaveTextContent("END_MARKER");
    expect(screen.getByText("local_name")).toBeInTheDocument();
    expect(screen.getByText("source_step")).toBeInTheDocument();
    expect(screen.getByText("source_field")).toBeInTheDocument();
    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("result")).toBeInTheDocument();
    expect(screen.getByText("summary")).toBeInTheDocument();
    expect(screen.getByText("retry")).toBeInTheDocument();
    expect(screen.getByText(/loop -> writer \(max 3\)/)).toBeInTheDocument();
  });

  it("truncates non-agent command previews to 200 characters", () => {
    const command = `echo ${"x".repeat(220)} END_MARKER`;
    const stepDef = makeStep({
      name: "script_step",
      executor: {
        type: "script",
        config: { command },
        decorators: [],
      },
      inputs: [],
      outputs: [],
      exit_rules: [],
    });

    render(
      <DataFlowPanel
        selection={{ kind: "step", stepName: "script_step" }}
        job={makeJob(stepDef)}
        latestRuns={{}}
        outputs={null}
        onClose={() => {}}
      />,
    );

    expect(screen.getByText("script_step")).toBeInTheDocument();
    expect(screen.queryByText("END_MARKER")).not.toBeInTheDocument();
  });
});
