import * as React from "react";
import { useCallback, useMemo } from "react";
import { useNavigate } from "@tanstack/react-router";
import { toast } from "sonner";
import { useStepwiseMutations } from "@/hooks/useStepwise";
import type { useDeleteFlow } from "@/hooks/useEditor";
import type { ActionContext, SideEffects } from "@/lib/actions/types";

const ActionCtx = React.createContext<ActionContext | null>(null);

interface ActionContextProviderProps {
  sideEffects?: Partial<SideEffects>;
  extraMutations?: { deleteFlow?: ReturnType<typeof useDeleteFlow> };
  children: React.ReactNode;
}

export function ActionContextProvider({
  sideEffects = {},
  extraMutations,
  children,
}: ActionContextProviderProps) {
  const mutations = useStepwiseMutations();
  const nav = useNavigate();

  const navigate = useCallback(
    (opts: { to: string; search?: Record<string, unknown>; replace?: boolean }) => {
      nav(opts);
    },
    [nav],
  );

  const clipboard = useCallback((text: string, label?: string) => {
    navigator.clipboard.writeText(text);
    toast.success(label ? `Copied ${label}` : "Copied to clipboard");
  }, []);

  const ctx = useMemo<ActionContext>(
    () => ({
      mutations,
      navigate,
      clipboard,
      sideEffects: sideEffects as SideEffects,
      extraMutations,
    }),
    [mutations, navigate, clipboard, sideEffects, extraMutations],
  );

  return <ActionCtx.Provider value={ctx}>{children}</ActionCtx.Provider>;
}

export function useActionContext(): ActionContext {
  const ctx = React.useContext(ActionCtx);
  if (!ctx)
    throw new Error("useActionContext must be used within ActionContextProvider");
  return ctx;
}
