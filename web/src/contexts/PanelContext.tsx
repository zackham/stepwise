import { useState, useCallback, useMemo } from "react";
import type { ReactNode } from "react";
import { PanelContext, type PanelControls } from "./panel-context";

export function PanelProvider({ children }: { children: ReactNode }) {
  const [controls, setControls] = useState<PanelControls>({});

  const register = useCallback((c: PanelControls) => {
    setControls(c);
  }, []);

  const unregister = useCallback(() => {
    setControls({});
  }, []);

  const value = useMemo(
    () => ({ controls, register, unregister }),
    [controls, register, unregister],
  );

  return <PanelContext.Provider value={value}>{children}</PanelContext.Provider>;
}
