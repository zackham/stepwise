import * as React from "react";
import type { ActionDefinition, ActionContext } from "@/lib/actions/types";
import { groupActions } from "@/lib/actions";
import {
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubTrigger,
  ContextMenuSubContent,
  ContextMenuShortcut,
} from "@/components/ui/context-menu";
import {
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubTrigger,
  DropdownMenuSubContent,
  DropdownMenuShortcut,
} from "@/components/ui/dropdown-menu";

interface ActionMenuItemsProps<T> {
  actions: ActionDefinition<T>[];
  entity: T;
  context: ActionContext;
  menuType: "context" | "dropdown";
  onRequestConfirm: (action: ActionDefinition<T>) => void;
}

export function ActionMenuItems<T>({
  actions,
  entity,
  context,
  menuType,
  onRequestConfirm,
}: ActionMenuItemsProps<T>) {
  const groups = groupActions(actions);
  const Item = menuType === "context" ? ContextMenuItem : DropdownMenuItem;
  const Separator = menuType === "context" ? ContextMenuSeparator : DropdownMenuSeparator;
  const Sub = menuType === "context" ? ContextMenuSub : DropdownMenuSub;
  const SubTrigger = menuType === "context" ? ContextMenuSubTrigger : DropdownMenuSubTrigger;
  const SubContent = menuType === "context" ? ContextMenuSubContent : DropdownMenuSubContent;
  const Shortcut = menuType === "context" ? ContextMenuShortcut : DropdownMenuShortcut;

  return (
    <>
      {groups.map((group, gi) => (
        <React.Fragment key={group.group}>
          {gi > 0 && <Separator />}
          {group.actions.map((action) => {
            const Icon = action.icon;
            const disabled = action.isEnabled ? !action.isEnabled(entity) : false;

            if (action.children && action.children.length > 0) {
              const availableChildren = action.children.filter((c) =>
                c.isAvailable(entity),
              );
              if (availableChildren.length === 0) return null;

              return (
                <Sub key={action.id}>
                  <SubTrigger>
                    {Icon && <Icon className="size-3.5" />}
                    {action.label}
                  </SubTrigger>
                  <SubContent>
                    {availableChildren.map((child) => {
                      const ChildIcon = child.icon;
                      return (
                        <Item
                          key={child.id}
                          disabled={child.isEnabled ? !child.isEnabled(entity) : false}
                          onClick={(e: React.MouseEvent) => {
                            e.stopPropagation();
                            child.execute(entity, context);
                          }}
                        >
                          {ChildIcon && <ChildIcon className="size-3.5" />}
                          {child.label}
                        </Item>
                      );
                    })}
                  </SubContent>
                </Sub>
              );
            }

            return (
              <Item
                key={action.id}
                variant={action.variant}
                disabled={disabled}
                onClick={(e: React.MouseEvent) => {
                  e.stopPropagation();
                  if (action.confirm) {
                    onRequestConfirm(action);
                  } else {
                    action.execute(entity, context);
                  }
                }}
              >
                {Icon && <Icon className="size-3.5" />}
                {action.label}
                {action.shortcut && <Shortcut>{action.shortcut}</Shortcut>}
              </Item>
            );
          })}
        </React.Fragment>
      ))}
    </>
  );
}
