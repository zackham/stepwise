import { useEffect, useRef, useState, useCallback } from "react";
import { subscribeAgentOutput } from "./useStepwiseWebSocket";
import type { AgentStreamEvent } from "@/lib/types";

// ── Types ───────────────────────────────────────────────────────────

export interface ToolCallState {
  id: string;
  title: string;
  kind: string;
  status: "running" | "completed";
}

export type StreamSegment =
  | { type: "text"; text: string }
  | { type: "tool"; tool: ToolCallState };

export interface AgentStreamState {
  segments: StreamSegment[];
  usage: { used: number; size: number } | null;
}

// ── Build segments from events ──────────────────────────────────────

export function buildSegmentsFromEvents(events: AgentStreamEvent[]): AgentStreamState {
  const segments: StreamSegment[] = [];
  let usage: { used: number; size: number } | null = null;

  for (const ev of events) {
    if (ev.t === "text") {
      const last = segments[segments.length - 1];
      if (last && last.type === "text") {
        last.text += ev.text;
      } else {
        segments.push({ type: "text", text: ev.text });
      }
    } else if (ev.t === "tool_start") {
      segments.push({
        type: "tool",
        tool: { id: ev.id, title: ev.title, kind: ev.kind, status: "running" },
      });
    } else if (ev.t === "tool_end") {
      for (const seg of segments) {
        if (seg.type === "tool" && seg.tool.id === ev.id) {
          seg.tool.status = "completed";
          break;
        }
      }
    } else if (ev.t === "usage") {
      usage = { used: ev.used, size: ev.size };
    }
  }

  return { segments, usage };
}

// ── Live stream hook ────────────────────────────────────────────────

export function useAgentStream(runId: string | undefined) {
  const stateRef = useRef<AgentStreamState>({ segments: [], usage: null });
  const [version, setVersion] = useState(0);

  const processEvents = useCallback(
    (events: AgentStreamEvent[]) => {
      const state = stateRef.current;
      for (const ev of events) {
        if (ev.t === "text") {
          const last = state.segments[state.segments.length - 1];
          if (last && last.type === "text") {
            last.text += ev.text;
          } else {
            state.segments.push({ type: "text", text: ev.text });
          }
        } else if (ev.t === "tool_start") {
          state.segments.push({
            type: "tool",
            tool: {
              id: ev.id,
              title: ev.title,
              kind: ev.kind,
              status: "running",
            },
          });
        } else if (ev.t === "tool_end") {
          for (const seg of state.segments) {
            if (seg.type === "tool" && seg.tool.id === ev.id) {
              seg.tool.status = "completed";
              break;
            }
          }
        } else if (ev.t === "usage") {
          state.usage = { used: ev.used, size: ev.size };
        }
      }
      setVersion((v) => v + 1);
    },
    []
  );

  useEffect(() => {
    if (!runId) return;
    // Reset on runId change
    stateRef.current = { segments: [], usage: null };
    setVersion(0);

    const unsub = subscribeAgentOutput((msg) => {
      if (msg.run_id === runId) {
        processEvents(msg.events);
      }
    });

    return unsub;
  }, [runId, processEvents]);

  return {
    streamState: stateRef.current,
    version,
  };
}
