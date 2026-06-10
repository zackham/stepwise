import { createContext, useContext } from "react";
import type { ActionContext } from "@/lib/actions/types";

export const ActionCtx = createContext<ActionContext | null>(null);

export function useActionContext(): ActionContext {
  const ctx = useContext(ActionCtx);
  if (!ctx)
    throw new Error("useActionContext must be used within ActionContextProvider");
  return ctx;
}
