import { useState, useMemo } from "react";
import type { EntityType, ActionDefinition } from "@/lib/actions/types";
import { getActionsForEntity } from "@/lib/actions";
import { useActionContext } from "./ActionContextProvider";
import { ActionMenuItems } from "./ActionMenuItems";
import { ConfirmDialog } from "./ConfirmDialog";
import {
  ContextMenu,
  ContextMenuTrigger,
  ContextMenuContent,
} from "@/components/ui/context-menu";

interface EntityContextMenuProps<T> {
  type: EntityType;
  data: T;
  children: React.ReactNode;
  className?: string;
}

export function EntityContextMenu<T>({
  type,
  data,
  children,
  className,
}: EntityContextMenuProps<T>) {
  const ctx = useActionContext();
  const actions = useMemo(() => getActionsForEntity(type, data), [type, data]);
  const [pendingAction, setPendingAction] = useState<ActionDefinition<T> | null>(null);

  const confirmDescription = pendingAction?.confirm
    ? typeof pendingAction.confirm.description === "function"
      ? pendingAction.confirm.description(data)
      : pendingAction.confirm.description
    : "";

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger className={className}>{children}</ContextMenuTrigger>
        <ContextMenuContent>
          <ActionMenuItems
            actions={actions}
            entity={data}
            context={ctx}
            menuType="context"
            onRequestConfirm={setPendingAction}
          />
        </ContextMenuContent>
      </ContextMenu>
      <ConfirmDialog
        open={!!pendingAction}
        title={pendingAction?.confirm?.title ?? ""}
        description={confirmDescription}
        confirmLabel={pendingAction?.confirm?.confirmLabel ?? pendingAction?.label ?? "Confirm"}
        variant={pendingAction?.variant === "destructive" ? "destructive" : "default"}
        onConfirm={() => {
          pendingAction?.execute(data, ctx);
          setPendingAction(null);
        }}
        onCancel={() => setPendingAction(null)}
      />
    </>
  );
}
