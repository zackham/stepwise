import { describe, it, expect, vi, beforeEach } from "vitest";
import { render as rtlRender, screen, act } from "@testing-library/react";
import type { ReactElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AgentStreamView } from "./AgentStreamView";
import type { AgentStreamState, StreamSegment } from "@/hooks/useAgentStream";
import type { AgentStreamEvent } from "@/lib/types";

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

let mockHistoryData: { events: AgentStreamEvent[] } | undefined = undefined;
const mockRefetch = vi.fn(() => Promise.resolve());

vi.mock("@/hooks/useStepwise", () => ({
  useAgentOutput: () => ({ data: mockHistoryData, refetch: mockRefetch }),
}));

beforeEach(() => {
  mockStreamState.segments = [];
  mockStreamState.usage = null;
  mockVersion = 0;
  mockHistoryData = undefined;
  mockRefetch.mockReset();
  mockRefetch.mockImplementation(() => Promise.resolve());
});

// AgentStreamView uses useQueryClient (cache invalidation on unmount of a
// live view) — wrap renders in a provider.
function render(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrap = (el: ReactElement) => (
    <QueryClientProvider client={queryClient}>{el}</QueryClientProvider>
  );
  const result = rtlRender(wrap(ui));
  return {
    ...result,
    rerender: (next: ReactElement) => result.rerender(wrap(next)),
  };
}

// ── Helpers ────────────────────────────────────────────────────────────

function textSeg(text: string): StreamSegment {
  return { type: "text", text };
}

function toolSeg(
  id: string,
  title: string,
  kind: string,
  status: "running" | "completed" | "failed",
  output?: string
): StreamSegment {
  return { type: "tool", tool: { id, title, kind, status, output } };
}

// ── Tests ──────────────────────────────────────────────────────────────

describe("AgentStreamView", () => {
  it('renders "Loading output..." when live and backfill not yet loaded', () => {
    render(<AgentStreamView runId="r1" isLive={true} />);
    expect(screen.getByText("Loading output...")).toBeInTheDocument();
  });

  it('renders "Agent starting..." when live and backfill returned empty', () => {
    mockHistoryData = { events: [] };
    render(<AgentStreamView runId="r1" isLive={true} />);
    expect(screen.getByText("Agent starting...")).toBeInTheDocument();
  });

  it("shows startedAt time when provided and live with no segments", () => {
    mockHistoryData = { events: [] };
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

    // Tool cards render kind + title in separate spans; the button's
    // accessible name joins them.
    expect(
      screen.getByRole("button", { name: "Read config.ts" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Grep Search codebase" })
    ).toBeInTheDocument();
  });

  it("reports usage to parent via onUsage when usage data is present", () => {
    mockStreamState.segments = [textSeg("output")];
    mockStreamState.usage = { used: 5000, size: 200000 };
    const onUsage = vi.fn();
    render(<AgentStreamView runId="r1" isLive={true} onUsage={onUsage} />);

    // The usage bar moved to the parent (RunView) — the stream view
    // reports usage upward instead of rendering tokens inline.
    expect(onUsage).toHaveBeenCalledWith({ used: 5000, size: 200000 });
    expect(screen.queryByText(/tokens/)).toBeNull();
  });

  it("reports null usage via onUsage when usage is null", () => {
    mockStreamState.segments = [textSeg("output")];
    mockStreamState.usage = null;
    const onUsage = vi.fn();
    render(<AgentStreamView runId="r1" isLive={true} onUsage={onUsage} />);

    expect(onUsage).toHaveBeenCalledWith(null);
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
    expect(
      screen.getByRole("button", { name: "Read main.py" })
    ).toBeInTheDocument();
    expect(screen.getByText("Found the issue.")).toBeInTheDocument();
  });

  it("shows cost when provided on the starting screen", () => {
    mockHistoryData = { events: [] };
    render(
      <AgentStreamView runId="r1" isLive={true} costUsd={0.0042} />
    );
    expect(screen.getByText("Agent starting...")).toBeInTheDocument();
    expect(screen.getByText("$0.0042")).toBeInTheDocument();
  });

  it("does not show cost when zero", () => {
    mockHistoryData = { events: [] };
    render(
      <AgentStreamView runId="r1" isLive={true} costUsd={0} />
    );
    expect(screen.getByText("Agent starting...")).toBeInTheDocument();
    expect(screen.queryByText(/\$/)).toBeNull();
  });

  it("renders completed tool card as collapsible when output is present", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    mockStreamState.segments = [
      toolSeg("t1", "Read config.ts", "Read", "completed", "file contents here"),
    ];
    render(<AgentStreamView runId="r1" isLive={true} />);

    // Tool title visible (kind + path joined in the button's accessible name)
    const toolButton = screen.getByRole("button", { name: "Read config.ts" });
    expect(toolButton).toBeInTheDocument();
    // Output hidden by default
    expect(screen.queryByText("file contents here")).toBeNull();

    // Click to expand
    await user.click(toolButton);
    expect(screen.getByText("file contents here")).toBeInTheDocument();

    // Click again to collapse
    await user.click(toolButton);
    expect(screen.queryByText("file contents here")).toBeNull();
  });

  it("renders failed tool card with error styling", () => {
    mockStreamState.segments = [
      toolSeg("t1", "Command failed", "Bash", "failed", "exit code 1"),
    ];
    render(<AgentStreamView runId="r1" isLive={true} />);
    expect(screen.getByText("Command failed")).toBeInTheDocument();
  });

  it("does not make running tool cards collapsible", () => {
    mockStreamState.segments = [
      toolSeg("t1", "Reading file", "Read", "running", "partial"),
    ];
    render(<AgentStreamView runId="r1" isLive={true} />);
    expect(screen.getByText("Reading file")).toBeInTheDocument();
    // Output should not be visible even though it exists — tool is still running
    expect(screen.queryByText("partial")).toBeNull();
  });

  // ── Live → done transition (transcript must not truncate) ───────────

  it("refetches history on the live→done transition and keeps the accumulated stream until it arrives", async () => {
    mockStreamState.segments = [textSeg("accumulated live transcript")];
    mockHistoryData = { events: [{ t: "text", text: "stale mount-time snapshot" }] };

    let resolveRefetch!: () => void;
    mockRefetch.mockImplementation(
      () => new Promise<void>((resolve) => { resolveRefetch = resolve; }),
    );

    const { rerender } = render(<AgentStreamView runId="r1" isLive={true} />);
    expect(screen.getByText("accumulated live transcript")).toBeInTheDocument();

    // Run completes — isLive flips false
    rerender(<AgentStreamView runId="r1" isLive={false} />);

    // The stale history must be refetched...
    expect(mockRefetch).toHaveBeenCalledTimes(1);
    // ...and until it resolves, the accumulated stream stays on screen
    // instead of truncating back to the mount-time snapshot.
    expect(screen.getByText("accumulated live transcript")).toBeInTheDocument();
    expect(screen.queryByText("stale mount-time snapshot")).toBeNull();

    // Fresh full transcript arrives
    mockHistoryData = { events: [{ t: "text", text: "full final transcript" }] };
    await act(async () => {
      resolveRefetch();
    });
    rerender(<AgentStreamView runId="r1" isLive={false} />);

    expect(screen.getByText("full final transcript")).toBeInTheDocument();
    expect(screen.queryByText("accumulated live transcript")).toBeNull();
  });

  it("does not refetch when mounted on an already-finished run", () => {
    mockHistoryData = { events: [{ t: "text", text: "historical transcript" }] };
    render(<AgentStreamView runId="r1" isLive={false} />);

    expect(screen.getByText("historical transcript")).toBeInTheDocument();
    expect(mockRefetch).not.toHaveBeenCalled();
  });
});
