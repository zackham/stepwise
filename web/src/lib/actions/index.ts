import type { EntityType, ActionDefinition } from "./types";
import { JOB_ACTIONS } from "./job-actions";
import { STEP_ACTIONS } from "./step-actions";
import { FLOW_ACTIONS } from "./flow-actions";
import { CANVAS_ACTIONS } from "./canvas-actions";

export type { EntityType, ActionDefinition, ActionContext, SideEffects } from "./types";

export function getActionsForEntity<T>(type: EntityType, data: T): ActionDefinition<T>[] {
  let raw: readonly ActionDefinition<never>[];
  switch (type) {
    case "job":
      raw = JOB_ACTIONS as unknown as ActionDefinition<never>[];
      break;
    case "step":
      raw = STEP_ACTIONS as unknown as ActionDefinition<never>[];
      break;
    case "flow":
      raw = FLOW_ACTIONS as unknown as ActionDefinition<never>[];
      break;
    case "canvas":
      raw = CANVAS_ACTIONS as unknown as ActionDefinition<never>[];
      break;
    default:
      raw = [];
  }

  const actions = raw as unknown as ActionDefinition<T>[];
  return actions
    .filter((a) => a.isAvailable(data))
    .sort((a, b) => a.groupOrder - b.groupOrder);
}

export function groupActions<T>(
  actions: ActionDefinition<T>[],
): { group: string; actions: ActionDefinition<T>[] }[] {
  const groups: { group: string; actions: ActionDefinition<T>[] }[] = [];
  let currentGroup: string | null = null;

  for (const action of actions) {
    if (action.group !== currentGroup) {
      groups.push({ group: action.group, actions: [action] });
      currentGroup = action.group;
    } else {
      groups[groups.length - 1].actions.push(action);
    }
  }

  return groups;
}
