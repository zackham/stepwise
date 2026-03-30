import { useState, useMemo } from "react";
import type { EntityType, ActionDefinition, ActionContext } from "@/lib/actions/types";
import { getActionsForEntity } from "@/lib/actions";
import { useActionContext } from "./ActionContextProvider";
import { useHotkeys, type HotkeyBinding } from "@/hooks/useHotkeys";

export function useEntityShortcuts<T>(
  type: EntityType,
  entity: T | null,
  ctx?: ActionContext,
): {
  pendingAction: ActionDefinition<T> | null;
  clearPending: () => void;
  confirmPending: () => void;
} {
  const [pendingAction, setPendingAction] = useState<ActionDefinition<T> | null>(null);
  const fallbackCtx = useActionContext();
  const actionCtx = ctx ?? fallbackCtx;

  const actions = useMemo(
    () =>
      entity
        ? getActionsForEntity<T>(type, entity).filter((a) => a.shortcutKeys)
        : [],
    [type, entity],
  );

  const bindings = useMemo<HotkeyBinding[]>(
    () =>
      actions.map((action) => ({
        keys: action.shortcutKeys!,
        onTrigger: () => {
          if (action.confirm) {
            setPendingAction(action);
          } else {
            action.execute(entity!, actionCtx);
          }
        },
      })),
    [actions, entity, actionCtx],
  );

  useHotkeys(bindings, { enabled: !!entity });

  return {
    pendingAction,
    clearPending: () => setPendingAction(null),
    confirmPending: () => {
      if (pendingAction && entity) {
        pendingAction.execute(entity, actionCtx);
      }
      setPendingAction(null);
    },
  };
}
