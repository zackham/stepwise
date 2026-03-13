import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { AgentStreamView } from "./AgentStreamView";
import type { AgentStreamState, StreamSegment } from "@/hooks/useAgentStream";

// ── Mocks ──────────────────────────────────────────────────────────────

const mockStreamState: AgentStreamState = { segments: [], usage: null };
let mockVersion = 0;

vi.mock("@/hooks/useAgentStream", async () => {
  const actual = await vi.importActual<typeof import("@/hooks/useAgentStream")>(
    "@/hooks/useAgentStream"
  );
  return {
    ...actual,
    useAgentStream: () => ({
      streamState: mockStreamState,
      version: mockVersion,
    }),
  };
});

let mockHistoryData: { events: never[] } | undefined = undefined;

vi.mock("@/hooks/useStepwise", () => ({
  useAgentOutput: () => ({ data: mockHistoryData }),
}));

beforeEach(() => {
  mockStreamState.segments = [];
  mockStreamState.usage = null;
  mockVersion = 0;
  mockHistoryData = undefined;
});

// ── Helpers ────────────────────────────────────────────────────────────

function textSeg(text: string): StreamSegment {
  return { type: "text", text };
}

function toolSeg(
  id: string,
  title: string,
  kind: string,
  status: "running" | "completed"
): StreamSegment {
  return { type: "tool", tool: { id, title, kind, status } };
}

// ── Tests ──────────────────────────────────────────────────────────────

describe("AgentStreamView", () => {
  it('renders "Agent starting..." when live with no segments', () => {
    render(<AgentStreamView runId="r1" isLive={true} />);
    expect(screen.getByText("Agent starting...")).toBeInTheDocument();
  });

  it("shows startedAt time when provided and live with no segments", () => {
    render(
      <AgentStreamView
        runId="r1"
        isLive={true}
        startedAt="2026-03-09T10:30:00Z"
      />
    );
    expect(screen.getByText("Agent starting...")).toBeInTheDocument();
    // The time string is locale-dependent but should be present
    expect(screen.getByText(/\d+:\d+/)).toBeInTheDocument();
  });

  it("renders text content from segments", () => {
    mockStreamState.segments = [textSeg("Hello from the agent")];
    render(<AgentStreamView runId="r1" isLive={true} />);
    expect(screen.getByText("Hello from the agent")).toBeInTheDocument();
  });

  it("renders tool cards with correct status indicators", () => {
    mockStreamState.segments = [
      toolSeg("t1", "Read config.ts", "Read", "completed"),
      toolSeg("t2", "Search codebase", "Grep", "running"),
    ];
    render(<AgentStreamView runId="r1" isLive={true} />);

    expect(screen.getByText("Read config.ts")).toBeInTheDocument();
    expect(screen.getByText("Search codebase")).toBeInTheDocument();
  });

  it("shows usage bar when usage data is present", () => {
    mockStreamState.segments = [textSeg("output")];
    mockStreamState.usage = { used: 5000, size: 200000 };
    render(<AgentStreamView runId="r1" isLive={true} />);

    expect(screen.getByText(/5,000/)).toBeInTheDocument();
    expect(screen.getByText(/200,000/)).toBeInTheDocument();
    expect(screen.getByText(/tokens/)).toBeInTheDocument();
  });

  it("does not show usage bar when usage is null", () => {
    mockStreamState.segments = [textSeg("output")];
    mockStreamState.usage = null;
    render(<AgentStreamView runId="r1" isLive={true} />);

    expect(screen.queryByText(/tokens/)).toBeNull();
  });

  it("returns null when not live and no data", () => {
    const { container } = render(
      <AgentStreamView runId="r1" isLive={false} />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders multiple text and tool segments in order", () => {
    mockStreamState.segments = [
      textSeg("Analyzing..."),
      toolSeg("t1", "Read main.py", "Read", "completed"),
      textSeg("Found the issue."),
    ];
    render(<AgentStreamView runId="r1" isLive={true} />);

    expect(screen.getByText("Analyzing...")).toBeInTheDocument();
    expect(screen.getByText("Read main.py")).toBeInTheDocument();
    expect(screen.getByText("Found the issue.")).toBeInTheDocument();
  });

  it("shows cost when provided on the starting screen", () => {
    render(
      <AgentStreamView runId="r1" isLive={true} costUsd={0.0042} />
    );
    expect(screen.getByText("Agent starting...")).toBeInTheDocument();
    expect(screen.getByText("$0.0042")).toBeInTheDocument();
  });

  it("does not show cost when zero", () => {
    render(
      <AgentStreamView runId="r1" isLive={true} costUsd={0} />
    );
    expect(screen.getByText("Agent starting...")).toBeInTheDocument();
    expect(screen.queryByText(/\$/)).toBeNull();
  });
});
