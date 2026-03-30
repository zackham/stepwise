import { describe, it, expect } from "vitest";
import type { LocalFlow } from "@/lib/types";
import { FLOW_ACTIONS } from "../flow-actions";
import { getActionsForEntity } from "../index";

function makeFlow(overrides: Partial<LocalFlow> = {}): LocalFlow {
  return {
    path: "/flows/my-flow/FLOW.yaml",
    name: "my-flow",
    description: "A test flow",
    steps_count: 3,
    modified_at: new Date().toISOString(),
    is_directory: true,
    executor_types: ["script", "llm"],
    visibility: "interactive",
    ...overrides,
  };
}

describe("flow actions", () => {
  it("all actions available for standard flow", () => {
    const actions = getActionsForEntity("flow", makeFlow());
    const ids = actions.map((a) => a.id);
    expect(ids).toEqual([
      "flow.run",
      "flow.edit",
      "flow.view-jobs",
      "flow.copy",
      "flow.duplicate",
      "flow.export-yaml",
      "flow.delete",
    ]);
  });

  it("copy sub-menu has path and name children", () => {
    const copyAction = FLOW_ACTIONS.find((a) => a.id === "flow.copy");
    expect(copyAction?.children).toHaveLength(2);
    expect(copyAction?.children?.map((c) => c.id)).toEqual([
      "flow.copy.path",
      "flow.copy.name",
    ]);
  });

  it("delete is destructive variant", () => {
    const deleteAction = FLOW_ACTIONS.find((a) => a.id === "flow.delete");
    expect(deleteAction?.variant).toBe("destructive");
  });
});
