import { useEffect, useRef, useState, useCallback } from "react";
import { subscribeAgentOutput } from "./useStepwiseWebSocket";
import { buildSegmentsFromEvents, type StreamSegment } from "./useAgentStream";
import type { AgentStreamEvent, SessionBoundary } from "@/lib/types";

export interface SessionStreamState {
  segments: StreamSegment[];
  boundaries: SessionBoundary[];
  usage: { used: number; size: number } | null;
}

export function useSessionStream(
  runIds: string[],
  backfillEvents: AgentStreamEvent[] | null | undefined,
  backfillBoundaries: SessionBoundary[] | null | undefined,
  isLive: boolean,
): { state: SessionStreamState; version: number } {
  const stateRef = useRef<SessionStreamState>({
    segments: [],
    boundaries: [],
    usage: null,
  });
  const [version, setVersion] = useState(0);
  const backfilledRef = useRef(false);
  const liveQueueRef = useRef<AgentStreamEvent[]>([]);
  const runIdSetRef = useRef(new Set(runIds));

  // Keep runId set in sync
  useEffect(() => {
    runIdSetRef.current = new Set(runIds);
  }, [runIds]);

  const processEvents = useCallback((events: AgentStreamEvent[]) => {
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
          tool: { id: ev.id, title: ev.title, kind: ev.kind, status: "running" },
        });
      } else if (ev.t === "tool_end") {
        for (const seg of state.segments) {
          if (seg.type === "tool" && seg.tool.id === ev.id) {
            seg.tool.status = ev.error ? "failed" : "completed";
            if (ev.output) seg.tool.output = ev.output;
            break;
          }
        }
      } else if (ev.t === "usage") {
        state.usage = { used: ev.used, size: ev.size };
      }
    }
    setVersion((v) => v + 1);
  }, []);

  // Subscribe to live WebSocket events for all matching run IDs
  useEffect(() => {
    if (!isLive || runIds.length === 0) return;

    const unsub = subscribeAgentOutput((msg) => {
      if (!runIdSetRef.current.has(msg.run_id)) return;
      if (!backfilledRef.current) {
        liveQueueRef.current.push(...msg.events);
      } else {
        processEvents(msg.events);
      }
    });

    return unsub;
  }, [isLive, runIds, processEvents]);

  // When backfill arrives, build segments + replay queue
  useEffect(() => {
    if (!backfillEvents || backfilledRef.current) return;

    backfilledRef.current = true;

    const built = buildSegmentsFromEvents(backfillEvents);
    stateRef.current = {
      segments: built.segments,
      boundaries: backfillBoundaries ?? [],
      usage: built.usage,
    };

    // Replay queued live events
    const queue = liveQueueRef.current;
    if (queue.length > 0) {
      processEvents(queue);
    } else {
      setVersion((v) => v + 1);
    }
    liveQueueRef.current = [];
  }, [backfillEvents, backfillBoundaries, processEvents]);

  // Reset when runIds change fundamentally
  const runIdKey = runIds.join(",");
  useEffect(() => {
    stateRef.current = { segments: [], boundaries: [], usage: null };
    setVersion(0);
    backfilledRef.current = false;
    liveQueueRef.current = [];
  }, [runIdKey]);

  // eslint-disable-next-line react-hooks/refs -- intentional ref-as-mutable-state pattern (same as useAgentStream)
  return { state: stateRef.current, version };
}
