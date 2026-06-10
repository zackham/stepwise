import { useEffect, useRef, useState, useCallback } from "react";
import { subscribeAgentOutput } from "./useStepwiseWebSocket";
import { buildSegmentsFromEvents, type StreamSegment } from "./useAgentStream";
import type { AgentStreamEvent, SessionBoundary } from "@/lib/types";

export interface SessionStreamState {
  segments: StreamSegment[];
  boundaries: SessionBoundary[];
  usage: { used: number; size: number } | null;
  eventToSegment?: number[];
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

  // Reset synchronously during render when runIds change.
  // eslint-disable react-hooks/refs — intentional mutable-accumulator pattern:
  // the refs hold a stream buffer that must be cleared before this render's
  // returned snapshot; re-render correctness is governed by the `version`
  // counter, not by these refs.
  /* eslint-disable react-hooks/refs */
  const runIdKey = runIds.join(",");
  const prevRunIdKeyRef = useRef(runIdKey);
  if (prevRunIdKeyRef.current !== runIdKey) {
    prevRunIdKeyRef.current = runIdKey;
    stateRef.current = { segments: [], boundaries: [], usage: null };
    backfilledRef.current = false;
    liveQueueRef.current = [];
  }
  /* eslint-enable react-hooks/refs */

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
      eventToSegment: built.eventToSegment,
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

  // Intentional: the ref is a mutable stream accumulator; `version` invalidates consumers on change.
  // eslint-disable-next-line react-hooks/refs
  return { state: stateRef.current, version };
}
