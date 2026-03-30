import type { EntityType, ActionDefinition } from "./types";
import { JOB_ACTIONS } from "./job-actions";
import { STEP_ACTIONS } from "./step-actions";
import { FLOW_ACTIONS } from "./flow-actions";
import { CANVAS_ACTIONS } from "./canvas-actions";

export type { EntityType, ActionDefinition, ActionContext, SideEffects } from "./types";

export function getActionsForEntity<T>(type: EntityType, data: T): ActionDefinition<T>[] {
  let actions: ActionDefinition<unknown>[];
  switch (type) {
    case "job":
      actions = JOB_ACTIONS;
      break;
    case "step":
      actions = STEP_ACTIONS;
      break;
    case "flow":
      actions = FLOW_ACTIONS;
      break;
    case "canvas":
      actions = CANVAS_ACTIONS;
      break;
    default:
      actions = [];
  }

  return (actions as ActionDefinition<T>[])
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
