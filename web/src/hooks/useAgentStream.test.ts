import { describe, it, expect, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  buildSegmentsFromEvents,
  trimBackfillOverlap,
  useAgentStream,
} from "./useAgentStream";
import type { AgentStreamEvent } from "@/lib/types";

// Capture WS subscribers so tests can push live agent_output messages.
const { wsListeners } = vi.hoisted(() => ({
  wsListeners: new Set<(msg: { run_id: string; events: AgentStreamEvent[] }) => void>(),
}));

vi.mock("./useStepwiseWebSocket", () => ({
  subscribeAgentOutput: (
    fn: (msg: { run_id: string; events: AgentStreamEvent[] }) => void,
  ) => {
    wsListeners.add(fn);
    return () => {
      wsListeners.delete(fn);
    };
  },
}));

function emitAgentOutput(run_id: string, events: AgentStreamEvent[]) {
  for (const fn of [...wsListeners]) fn({ run_id, events });
}

describe("buildSegmentsFromEvents", () => {
  it("returns empty segments and null usage for empty events", () => {
    const result = buildSegmentsFromEvents([]);
    expect(result.segments).toEqual([]);
    expect(result.usage).toBeNull();
  });

  it("creates a text segment from a single text event", () => {
    const events: AgentStreamEvent[] = [{ t: "text", text: "hello" }];
    const result = buildSegmentsFromEvents(events);

    expect(result.segments).toHaveLength(1);
    expect(result.segments[0]).toEqual({ type: "text", text: "hello" });
  });

  it("merges consecutive text events into one segment", () => {
    const events: AgentStreamEvent[] = [
      { t: "text", text: "hello " },
      { t: "text", text: "world" },
      { t: "text", text: "!" },
    ];
    const result = buildSegmentsFromEvents(events);

    expect(result.segments).toHaveLength(1);
    expect(result.segments[0]).toEqual({ type: "text", text: "hello world!" });
  });

  it("creates a tool segment with running status from tool_start", () => {
    const events: AgentStreamEvent[] = [
      { t: "tool_start", id: "t1", title: "Read file", kind: "Read" },
    ];
    const result = buildSegmentsFromEvents(events);

    expect(result.segments).toHaveLength(1);
    expect(result.segments[0]).toEqual({
      type: "tool",
      tool: { id: "t1", title: "Read file", kind: "Read", status: "running" },
    });
  });

  it("updates matching tool to completed on tool_end", () => {
    const events: AgentStreamEvent[] = [
      { t: "tool_start", id: "t1", title: "Read file", kind: "Read" },
      { t: "tool_end", id: "t1" },
    ];
    const result = buildSegmentsFromEvents(events);

    expect(result.segments).toHaveLength(1);
    const seg = result.segments[0];
    expect(seg.type).toBe("tool");
    if (seg.type === "tool") {
      expect(seg.tool.status).toBe("completed");
    }
  });

  it("does not affect other tools when completing one", () => {
    const events: AgentStreamEvent[] = [
      { t: "tool_start", id: "t1", title: "Read file", kind: "Read" },
      { t: "tool_start", id: "t2", title: "Search", kind: "Grep" },
      { t: "tool_end", id: "t1" },
    ];
    const result = buildSegmentsFromEvents(events);

    expect(result.segments).toHaveLength(2);
    const [first, second] = result.segments;
    expect(first.type === "tool" && first.tool.status).toBe("completed");
    expect(second.type === "tool" && second.tool.status).toBe("running");
  });

  it("sets usage from a usage event", () => {
    const events: AgentStreamEvent[] = [
      { t: "usage", used: 5000, size: 200000 },
    ];
    const result = buildSegmentsFromEvents(events);

    expect(result.segments).toEqual([]);
    expect(result.usage).toEqual({ used: 5000, size: 200000 });
  });

  it("uses the last usage event when multiple are present", () => {
    const events: AgentStreamEvent[] = [
      { t: "usage", used: 1000, size: 200000 },
      { t: "text", text: "thinking..." },
      { t: "usage", used: 5000, size: 200000 },
    ];
    const result = buildSegmentsFromEvents(events);

    expect(result.usage).toEqual({ used: 5000, size: 200000 });
  });

  it("handles a mixed event sequence with correct segment ordering", () => {
    const events: AgentStreamEvent[] = [
      { t: "text", text: "Let me read that file.\n" },
      { t: "tool_start", id: "t1", title: "Read config.ts", kind: "Read" },
      { t: "tool_end", id: "t1" },
      { t: "text", text: "Now searching..." },
      { t: "text", text: "\n" },
      { t: "tool_start", id: "t2", title: "Grep for imports", kind: "Grep" },
      { t: "usage", used: 12000, size: 200000 },
    ];
    const result = buildSegmentsFromEvents(events);

    expect(result.segments).toHaveLength(4);

    // First: text segment
    expect(result.segments[0]).toEqual({
      type: "text",
      text: "Let me read that file.\n",
    });

    // Second: completed tool
    expect(result.segments[1]).toEqual({
      type: "tool",
      tool: { id: "t1", title: "Read config.ts", kind: "Read", status: "completed" },
    });

    // Third: merged text
    expect(result.segments[2]).toEqual({
      type: "text",
      text: "Now searching...\n",
    });

    // Fourth: still-running tool
    expect(result.segments[3]).toEqual({
      type: "tool",
      tool: { id: "t2", title: "Grep for imports", kind: "Grep", status: "running" },
    });

    expect(result.usage).toEqual({ used: 12000, size: 200000 });
  });

  it("does not merge text segments separated by a tool", () => {
    const events: AgentStreamEvent[] = [
      { t: "text", text: "before" },
      { t: "tool_start", id: "t1", title: "Bash", kind: "Bash" },
      { t: "tool_end", id: "t1" },
      { t: "text", text: "after" },
    ];
    const result = buildSegmentsFromEvents(events);

    expect(result.segments).toHaveLength(3);
    expect(result.segments[0]).toEqual({ type: "text", text: "before" });
    expect(result.segments[1].type).toBe("tool");
    expect(result.segments[2]).toEqual({ type: "text", text: "after" });
  });

  it("ignores tool_end for unknown tool id", () => {
    const events: AgentStreamEvent[] = [
      { t: "tool_start", id: "t1", title: "Read", kind: "Read" },
      { t: "tool_end", id: "nonexistent" },
    ];
    const result = buildSegmentsFromEvents(events);

    expect(result.segments).toHaveLength(1);
    const seg = result.segments[0];
    if (seg.type === "tool") {
      expect(seg.tool.status).toBe("running");
    }
  });

  it("captures output from tool_end event", () => {
    const events: AgentStreamEvent[] = [
      { t: "tool_start", id: "t1", title: "Read config.ts", kind: "Read" },
      { t: "tool_end", id: "t1", output: "file contents here" },
    ];
    const result = buildSegmentsFromEvents(events);

    expect(result.segments).toHaveLength(1);
    const seg = result.segments[0];
    expect(seg.type).toBe("tool");
    if (seg.type === "tool") {
      expect(seg.tool.status).toBe("completed");
      expect(seg.tool.output).toBe("file contents here");
    }
  });

  it("marks tool as failed when tool_end has error flag", () => {
    const events: AgentStreamEvent[] = [
      { t: "tool_start", id: "t1", title: "Run script", kind: "Bash" },
      { t: "tool_end", id: "t1", error: true, output: "exit code 1" },
    ];
    const result = buildSegmentsFromEvents(events);

    expect(result.segments).toHaveLength(1);
    const seg = result.segments[0];
    expect(seg.type).toBe("tool");
    if (seg.type === "tool") {
      expect(seg.tool.status).toBe("failed");
      expect(seg.tool.output).toBe("exit code 1");
    }
  });

  it("tool without output on tool_end has no output field", () => {
    const events: AgentStreamEvent[] = [
      { t: "tool_start", id: "t1", title: "Read", kind: "Read" },
      { t: "tool_end", id: "t1" },
    ];
    const result = buildSegmentsFromEvents(events);

    const seg = result.segments[0];
    if (seg.type === "tool") {
      expect(seg.tool.output).toBeUndefined();
    }
  });
});

describe("trimBackfillOverlap", () => {
  const text = (t: string): AgentStreamEvent => ({ t: "text", text: t });

  it("returns the queue unchanged when there is no overlap", () => {
    const backfill = [text("a"), text("b")];
    const queue = [text("c"), text("d")];
    expect(trimBackfillOverlap(backfill, queue)).toEqual(queue);
  });

  it("drops a queue fully contained in the backfill tail", () => {
    const backfill = [text("a"), text("b"), text("c")];
    const queue = [text("b"), text("c")];
    expect(trimBackfillOverlap(backfill, queue)).toEqual([]);
  });

  it("drops only the overlapping prefix of the queue", () => {
    const backfill = [text("a"), text("b")];
    const queue = [text("a"), text("b"), text("c")];
    expect(trimBackfillOverlap(backfill, queue)).toEqual([text("c")]);
  });

  it("compares full event shape, not just type", () => {
    const backfill: AgentStreamEvent[] = [
      { t: "tool_start", id: "t1", title: "Read", kind: "Read" },
    ];
    const queue: AgentStreamEvent[] = [
      { t: "tool_start", id: "t2", title: "Grep", kind: "Grep" },
    ];
    expect(trimBackfillOverlap(backfill, queue)).toEqual(queue);
  });

  it("prefers the largest overlap for ambiguous repeated events", () => {
    const backfill = [text("."), text(".")];
    const queue = [text("."), text("."), text("!")];
    expect(trimBackfillOverlap(backfill, queue)).toEqual([text("!")]);
  });

  it("handles empty backfill and empty queue", () => {
    expect(trimBackfillOverlap([], [text("a")])).toEqual([text("a")]);
    expect(trimBackfillOverlap([text("a")], [])).toEqual([]);
  });
});

describe("useAgentStream backfill/live overlap", () => {
  it("does not duplicate text or spawn phantom running tools when queued WS events overlap the backfill", () => {
    const events: AgentStreamEvent[] = [
      { t: "text", text: "hello " },
      { t: "tool_start", id: "t1", title: "Read file", kind: "Read" },
      { t: "tool_end", id: "t1" },
    ];

    const { result, rerender } = renderHook(
      ({ backfill }: { backfill: AgentStreamEvent[] | null }) =>
        useAgentStream("r1", backfill),
      { initialProps: { backfill: null as AgentStreamEvent[] | null } },
    );

    // Live WS events arrive while the REST backfill is in flight — queued.
    act(() => emitAgentOutput("r1", events));
    expect(result.current.streamState.segments).toHaveLength(0);

    // Backfill resolves and (the server read the file AFTER those events
    // were written) contains the exact same events.
    rerender({ backfill: [...events] });

    const segments = result.current.streamState.segments;
    expect(segments).toHaveLength(2);
    expect(segments[0]).toEqual({ type: "text", text: "hello " });
    const toolSeg = segments[1];
    expect(toolSeg.type).toBe("tool");
    if (toolSeg.type === "tool") {
      // Pre-fix the replayed tool_start created a SECOND t1 card stuck
      // forever in "running" (its tool_end was already consumed).
      expect(toolSeg.tool.status).toBe("completed");
    }
  });

  it("replays only the non-overlapping tail of the queue", () => {
    const a: AgentStreamEvent = { t: "text", text: "a" };
    const b: AgentStreamEvent = { t: "text", text: "b" };
    const c: AgentStreamEvent = { t: "text", text: "c" };

    const { result, rerender } = renderHook(
      ({ backfill }: { backfill: AgentStreamEvent[] | null }) =>
        useAgentStream("r1", backfill),
      { initialProps: { backfill: null as AgentStreamEvent[] | null } },
    );

    // Queue holds a, b, c; backfill only covers a, b.
    act(() => emitAgentOutput("r1", [a, b, c]));
    rerender({ backfill: [a, b] });

    expect(result.current.streamState.segments).toEqual([
      { type: "text", text: "abc" },
    ]);
  });

  it("dedupes tool_start by id even when the overlap is not byte-identical", () => {
    const { result, rerender } = renderHook(
      ({ backfill }: { backfill: AgentStreamEvent[] | null }) =>
        useAgentStream("r1", backfill),
      { initialProps: { backfill: null as AgentStreamEvent[] | null } },
    );

    // Queued copy carries an updated title, so exact-overlap trimming
    // can't catch it — the id-based dedupe must.
    act(() =>
      emitAgentOutput("r1", [
        { t: "tool_start", id: "t1", title: "Read file (updated)", kind: "Read" },
        { t: "text", text: " done" },
      ]),
    );
    rerender({
      backfill: [
        { t: "tool_start", id: "t1", title: "Read file", kind: "Read" },
        { t: "tool_end", id: "t1" },
      ],
    });

    const segments = result.current.streamState.segments;
    expect(segments).toHaveLength(2);
    const toolSeg = segments[0];
    expect(toolSeg.type).toBe("tool");
    if (toolSeg.type === "tool") {
      expect(toolSeg.tool.status).toBe("completed");
      expect(toolSeg.tool.title).toBe("Read file (updated)");
    }
    expect(segments[1]).toEqual({ type: "text", text: " done" });
  });

  it("processes live events directly after backfill has been applied", () => {
    const { result, rerender } = renderHook(
      ({ backfill }: { backfill: AgentStreamEvent[] | null }) =>
        useAgentStream("r1", backfill),
      { initialProps: { backfill: null as AgentStreamEvent[] | null } },
    );

    rerender({ backfill: [{ t: "text", text: "start" }] });
    act(() => emitAgentOutput("r1", [{ t: "text", text: " more" }]));

    expect(result.current.streamState.segments).toEqual([
      { type: "text", text: "start more" },
    ]);
  });
});
