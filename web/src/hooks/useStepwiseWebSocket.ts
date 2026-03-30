import { useState, useEffect, useRef, useCallback, createContext, useContext } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { AgentOutputMessage, ScriptOutputMessage, TickMessage, FlowSourceChangedMessage } from "@/lib/types";

export type WsStatus = "connected" | "disconnected" | "reconnecting";
export interface StepwiseWebSocketState {
  wsState: WsStatus;
}

// ── Agent output pub/sub ────────────────────────────────────────────

const agentOutputListeners = new Set<(msg: AgentOutputMessage) => void>();

export function subscribeAgentOutput(fn: (msg: AgentOutputMessage) => void) {
  agentOutputListeners.add(fn);
  return () => {
    agentOutputListeners.delete(fn);
  };
}

// ── Tick message pub/sub ────────────────────────────────────────────

const tickListeners = new Set<(msg: TickMessage) => void>();

export function subscribeTickMessages(fn: (msg: TickMessage) => void) {
  tickListeners.add(fn);
  return () => {
    tickListeners.delete(fn);
  };
}

// ── Script output pub/sub ─────────────────────────────────────────

const scriptOutputListeners = new Set<(msg: ScriptOutputMessage) => void>();

export function subscribeScriptOutput(fn: (msg: ScriptOutputMessage) => void) {
  scriptOutputListeners.add(fn);
  return () => {
    scriptOutputListeners.delete(fn);
  };
}

// ── Flow source changed pub/sub ────────────────────────────────────

const flowSourceListeners = new Set<(msg: FlowSourceChangedMessage) => void>();

export function subscribeFlowSourceChanged(fn: (msg: FlowSourceChangedMessage) => void) {
  flowSourceListeners.add(fn);
  return () => {
    flowSourceListeners.delete(fn);
  };
}

// ── WebSocket connection ────────────────────────────────────────────

export function useStepwiseWebSocket(): StepwiseWebSocketState {
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shouldReconnectRef = useRef(true);
  const [wsState, setWsState] = useState<WsStatus>("disconnected");

  const connect = useCallback(() => {
    if (
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return;
    }

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    setWsState("reconnecting");

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log("[ws] connected");
      setWsState("connected");
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "tick" && msg.changed_jobs?.length > 0) {
          queryClient.invalidateQueries({ queryKey: ["jobs"] });
          queryClient.invalidateQueries({ queryKey: ["status"] });
          queryClient.invalidateQueries({ queryKey: ["recentFlows"] });
          // Invalidate all run-level queries too (stepEvents, runCost)
          queryClient.invalidateQueries({ queryKey: ["stepEvents"] });
          queryClient.invalidateQueries({ queryKey: ["runCost"] });
          for (const jobId of msg.changed_jobs) {
            queryClient.invalidateQueries({ queryKey: ["job", jobId] });
            queryClient.invalidateQueries({ queryKey: ["runs", jobId] });
            queryClient.invalidateQueries({ queryKey: ["events", jobId] });
            queryClient.invalidateQueries({ queryKey: ["jobTree", jobId] });
          }
          for (const fn of tickListeners) fn(msg as TickMessage);
        } else if (msg.type === "stale_jobs") {
          // Stale job detection — refresh job list so UI shows stale indicators
          queryClient.invalidateQueries({ queryKey: ["jobs"] });
          if (msg.jobs?.length > 0) {
            for (const stale of msg.jobs) {
              queryClient.invalidateQueries({ queryKey: ["job", stale.id] });
            }
          }
        } else if (msg.type === "flow_source_changed") {
          for (const fn of flowSourceListeners) fn(msg as FlowSourceChangedMessage);
        } else if (msg.type === "agent_output") {
          for (const fn of agentOutputListeners) fn(msg);
        } else if (msg.type === "script_output") {
          for (const fn of scriptOutputListeners) fn(msg as ScriptOutputMessage);
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      wsRef.current = null;
      setWsState("disconnected");

      if (!shouldReconnectRef.current) {
        return;
      }

      console.log("[ws] disconnected, reconnecting in 3s");
      reconnectTimeoutRef.current = setTimeout(() => {
        if (!shouldReconnectRef.current) {
          return;
        }
        connect();
      }, 3000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [queryClient]);

  useEffect(() => {
    shouldReconnectRef.current = true;
    connect();
    return () => {
      shouldReconnectRef.current = false;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      wsRef.current?.close();
    };
  }, [connect]);

  return { wsState };
}

const WsStatusContext = createContext<WsStatus>("disconnected");
export const WsStatusProvider = WsStatusContext.Provider;
export function useWsStatus(): WsStatus {
  return useContext(WsStatusContext);
}
