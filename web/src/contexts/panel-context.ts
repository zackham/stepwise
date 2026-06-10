import { createContext, useContext } from "react";
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

export interface PanelContextValue {
  controls: PanelControls;
  register: (controls: PanelControls) => void;
  unregister: () => void;
}

export const PanelContext = createContext<PanelContextValue>({
  controls: {},
  register: () => {},
  unregister: () => {},
});

export function usePanelControls() {
  return useContext(PanelContext);
}
