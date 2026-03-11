import { describe, it, expect } from "vitest";
import { buildSegmentsFromEvents } from "./useAgentStream";
import type { AgentStreamEvent } from "@/lib/types";

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
});
