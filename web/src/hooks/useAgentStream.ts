import { useEffect, useRef, useState, useCallback } from "react";
import { subscribeAgentOutput } from "./useStepwiseWebSocket";
import type { AgentStreamEvent } from "@/lib/types";

// ── Types ───────────────────────────────────────────────────────────

export interface ToolCallState {
  id: string;
  title: string;
  kind: string;
  status: "running" | "completed" | "failed";
  output?: string;
}

export type StreamSegment =
  | { type: "text"; text: string }
  | { type: "prompt"; text: string }
  | { type: "tool"; tool: ToolCallState };

export interface AgentStreamState {
  segments: StreamSegment[];
  usage: { used: number; size: number } | null;
  eventToSegment?: number[];
}

// ── Build segments from events ──────────────────────────────────────

export function buildSegmentsFromEvents(events: AgentStreamEvent[]): AgentStreamState {
  const segments: StreamSegment[] = [];
  // Maps each raw event index to the segment index it belongs to (or -1 if no segment)
  const eventToSegment: number[] = [];
  let usage: { used: number; size: number } | null = null;

  for (const ev of events) {
    if (ev.t === "text") {
      const last = segments[segments.length - 1];
      if (last && last.type === "text") {
        last.text += ev.text;
        eventToSegment.push(segments.length - 1);
      } else {
        segments.push({ type: "text", text: ev.text });
        eventToSegment.push(segments.length - 1);
      }
    } else if (ev.t === "tool_start") {
      segments.push({
        type: "tool",
        tool: { id: ev.id, title: ev.title, kind: ev.kind, status: "running" },
      });
      eventToSegment.push(segments.length - 1);
    } else if (ev.t === "tool_title") {
      for (const seg of segments) {
        if (seg.type === "tool" && seg.tool.id === ev.id) {
          seg.tool.title = ev.title;
          break;
        }
      }
      eventToSegment.push(-1);
    } else if (ev.t === "tool_end") {
      for (const seg of segments) {
        if (seg.type === "tool" && seg.tool.id === ev.id) {
          seg.tool.status = ev.error ? "failed" : "completed";
          if (ev.output) seg.tool.output = ev.output;
          break;
        }
      }
      eventToSegment.push(-1);
    } else if (ev.t === "prompt") {
      segments.push({ type: "prompt", text: ev.text });
      eventToSegment.push(segments.length - 1);
    } else if (ev.t === "usage") {
      usage = { used: ev.used, size: ev.size };
      eventToSegment.push(-1);
    } else {
      eventToSegment.push(-1);
    }
  }

  return { segments, usage, eventToSegment };
}

// ── Live stream hook (with backfill support) ────────────────────────

export function useAgentStream(
  runId: string | undefined,
  backfillEvents?: AgentStreamEvent[] | null,
) {
  const stateRef = useRef<AgentStreamState>({ segments: [], usage: null });
  const [version, setVersion] = useState(0);
  const backfilledRef = useRef(false);
  const liveQueueRef = useRef<AgentStreamEvent[]>([]);

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
        } else if (ev.t === "tool_title") {
          for (const seg of state.segments) {
            if (seg.type === "tool" && seg.tool.id === ev.id) {
              seg.tool.title = ev.title;
              break;
            }
          }
        } else if (ev.t === "tool_end") {
          for (const seg of state.segments) {
            if (seg.type === "tool" && seg.tool.id === ev.id) {
              seg.tool.status = ev.error ? "failed" : "completed";
              if (ev.output) seg.tool.output = ev.output;
              break;
            }
          }
        } else if (ev.t === "prompt") {
          state.segments.push({ type: "prompt", text: ev.text });
        } else if (ev.t === "usage") {
          state.usage = { used: ev.used, size: ev.size };
        }
      }
      setVersion((v) => v + 1);
    },
    [],
  );

  // Subscribe to live WebSocket events; queue them until backfill arrives
  useEffect(() => {
    if (!runId) return;
    // Reset on runId change
    stateRef.current = { segments: [], usage: null };
    setVersion(0);
    backfilledRef.current = false;
    liveQueueRef.current = [];

    const unsub = subscribeAgentOutput((msg) => {
      if (msg.run_id !== runId) return;
      if (!backfilledRef.current) {
        // Queue live events until backfill arrives
        liveQueueRef.current.push(...msg.events);
      } else {
        processEvents(msg.events);
      }
    });

    return unsub;
  }, [runId, processEvents]);

  // When backfill arrives, process it then replay queued live events
  useEffect(() => {
    if (!runId || !backfillEvents || backfilledRef.current) return;

    backfilledRef.current = true;

    // Process all backfill events
    if (backfillEvents.length > 0) {
      processEvents(backfillEvents);
    }

    // Replay queued live events. The backfill covers everything in the file
    // at REST-read time. Live events may partially overlap, but text
    // concatenation is idempotent and tool updates are keyed by ID,
    // so a small overlap at the boundary is harmless.
    const queue = liveQueueRef.current;
    if (queue.length > 0) {
      processEvents(queue);
    }
    liveQueueRef.current = [];
  }, [runId, backfillEvents, processEvents]);

  return {
    streamState: stateRef.current,
    version,
  };
}
