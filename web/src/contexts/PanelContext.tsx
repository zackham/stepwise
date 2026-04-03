import { createContext, useContext, useState, useCallback, useMemo } from "react";
import type { ReactNode } from "react";
import type { AgentMode } from "@/hooks/useEditorChat";

export interface PanelControls {
  leftPanel?: { visible: boolean; toggle: () => void; label?: string };
  rightPanel?: { visible: boolean; toggle: () => void; disabled?: boolean; label?: string };
  chat?: {
    open: boolean;
    toggle: () => void;
    isStreaming?: boolean;
    agentMode?: AgentMode;
    backgrounded?: boolean;
  };
  actions?: {
    onRun?: () => void;
    isRunning?: boolean;
    parseErrors?: string[];
  };
}

interface PanelContextValue {
  controls: PanelControls;
  register: (controls: PanelControls) => void;
  unregister: () => void;
}

const PanelContext = createContext<PanelContextValue>({
  controls: {},
  register: () => {},
  unregister: () => {},
});

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

export function usePanelControls() {
  return useContext(PanelContext);
}
