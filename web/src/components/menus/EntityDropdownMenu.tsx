import { useState, useMemo } from "react";
import { MoreVertical } from "lucide-react";
import type { EntityType, ActionDefinition } from "@/lib/actions/types";
import { getActionsForEntity } from "@/lib/actions";
import { useActionContext } from "./ActionContextProvider";
import { ActionMenuItems } from "./ActionMenuItems";
import { ConfirmDialog } from "./ConfirmDialog";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

interface EntityDropdownMenuProps<T> {
  type: EntityType;
  data: T;
  triggerClassName?: string;
}

export function EntityDropdownMenu<T>({
  type,
  data,
  triggerClassName,
}: EntityDropdownMenuProps<T>) {
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
      <DropdownMenu>
        <DropdownMenuTrigger
          className={cn(
            "flex items-center justify-center rounded p-1 text-muted-foreground hover:bg-accent hover:text-accent-foreground focus-visible:outline-none",
            triggerClassName,
          )}
          onClick={(e: React.MouseEvent) => e.stopPropagation()}
        >
          <MoreVertical className="size-4" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <ActionMenuItems
            actions={actions}
            entity={data}
            context={ctx}
            menuType="dropdown"
            onRequestConfirm={setPendingAction}
          />
        </DropdownMenuContent>
      </DropdownMenu>
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
