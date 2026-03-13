import { useEffect, useRef, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { AgentOutputMessage } from "@/lib/types";

// ── Agent output pub/sub ────────────────────────────────────────────

const agentOutputListeners = new Set<(msg: AgentOutputMessage) => void>();

export function subscribeAgentOutput(fn: (msg: AgentOutputMessage) => void) {
  agentOutputListeners.add(fn);
  return () => {
    agentOutputListeners.delete(fn);
  };
}

// ── WebSocket connection ────────────────────────────────────────────

export function useStepwiseWebSocket() {
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log("[ws] connected");
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "tick" && msg.changed_jobs?.length > 0) {
          queryClient.invalidateQueries({ queryKey: ["jobs"] });
          queryClient.invalidateQueries({ queryKey: ["status"] });
          // Invalidate all run-level queries too (stepEvents, runCost)
          queryClient.invalidateQueries({ queryKey: ["stepEvents"] });
          queryClient.invalidateQueries({ queryKey: ["runCost"] });
          for (const jobId of msg.changed_jobs) {
            queryClient.invalidateQueries({ queryKey: ["job", jobId] });
            queryClient.invalidateQueries({ queryKey: ["runs", jobId] });
            queryClient.invalidateQueries({ queryKey: ["events", jobId] });
            queryClient.invalidateQueries({ queryKey: ["jobTree", jobId] });
          }
        } else if (msg.type === "agent_output") {
          for (const fn of agentOutputListeners) fn(msg);
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      console.log("[ws] disconnected, reconnecting in 3s");
      reconnectTimeoutRef.current = setTimeout(connect, 3000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [queryClient]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      wsRef.current?.close();
    };
  }, [connect]);
}
