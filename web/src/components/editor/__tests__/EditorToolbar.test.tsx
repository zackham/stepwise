import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { EditorToolbar } from "../EditorToolbar";

// Mock TanStack Router Link as a simple anchor
vi.mock("@tanstack/react-router", () => ({
  Link: ({ children, to, ...props }: { children: React.ReactNode; to: string; [k: string]: unknown }) => (
    <a href={to} {...props}>{children}</a>
  ),
}));

describe("EditorToolbar", () => {
  it("displays flow name", () => {
    render(
      <EditorToolbar
        flowName="research"
        parseErrors={[]}
      />
    );
    expect(screen.getByText("research")).toBeDefined();
  });

  it("displays parse errors", () => {
    render(
      <EditorToolbar
        flowName="research"
        parseErrors={["Invalid YAML: unexpected indent"]}
      />
    );
    expect(
      screen.getByText("Invalid YAML: unexpected indent")
    ).toBeDefined();
  });

  it("shows Run button when onRun provided", () => {
    render(
      <EditorToolbar
        flowName="research"
        parseErrors={[]}
        onRun={() => {}}
      />
    );
    expect(screen.getByText("Run")).toBeDefined();
  });

  it("disables Run when parse errors exist", () => {
    render(
      <EditorToolbar
        flowName="research"
        parseErrors={["error"]}
        onRun={() => {}}
      />
    );
    const runBtn = screen.getByText("Run").closest("button")!;
    expect(runBtn.disabled).toBe(true);
  });

  it("shows chat toggle when onToggleChat provided", () => {
    render(
      <EditorToolbar
        flowName="research"
        parseErrors={[]}
        onToggleChat={() => {}}
      />
    );
    expect(screen.getByTitle("Open chat")).toBeDefined();
  });

  it("calls onToggleChat when chat button clicked", () => {
    const onToggleChat = vi.fn();
    render(
      <EditorToolbar
        flowName="research"
        parseErrors={[]}
        onToggleChat={onToggleChat}
        chatOpen={false}
      />
    );
    fireEvent.click(screen.getByTitle("Open chat"));
    expect(onToggleChat).toHaveBeenCalled();
  });
});
