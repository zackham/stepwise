import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DiffView } from "./DiffView";

describe("DiffView", () => {
  it("renders added lines in green when before is null", () => {
    const { container } = render(
      <DiffView before={null} after={{ key: "value" }} />
    );
    const addedLines = container.querySelectorAll(".bg-emerald-500\\/10");
    expect(addedLines.length).toBeGreaterThan(0);
    // All lines should have "+" gutter
    const gutters = container.querySelectorAll(".text-emerald-500");
    expect(gutters.length).toBeGreaterThan(0);
  });

  it("renders removed lines in red when after is null", () => {
    const { container } = render(
      <DiffView before={{ key: "value" }} after={null} />
    );
    const removedLines = container.querySelectorAll(".bg-red-500\\/10");
    expect(removedLines.length).toBeGreaterThan(0);
    const gutters = container.querySelectorAll(".text-red-500");
    expect(gutters.length).toBeGreaterThan(0);
  });

  it("shows identical message when objects match", () => {
    render(<DiffView before={{ a: 1 }} after={{ a: 1 }} />);
    expect(screen.getByText("Outputs are identical")).toBeInTheDocument();
  });

  it("shows empty message when both are null", () => {
    render(<DiffView before={null} after={null} />);
    expect(
      screen.getByText("No output in either attempt")
    ).toBeInTheDocument();
  });

  it("renders added and removed lines for changed values", () => {
    const { container } = render(
      <DiffView before={{ score: 0.5 }} after={{ score: 0.9 }} />
    );
    const addedLines = container.querySelectorAll(".bg-emerald-500\\/10");
    const removedLines = container.querySelectorAll(".bg-red-500\\/10");
    expect(addedLines.length).toBeGreaterThan(0);
    expect(removedLines.length).toBeGreaterThan(0);
  });

  it("collapses large unchanged blocks with an expander", async () => {
    // Create objects with many identical fields and one change
    const base: Record<string, number> = {};
    for (let i = 0; i < 20; i++) base[`field_${String(i).padStart(2, "0")}`] = i;
    const before = { ...base, changed: "old" };
    const after = { ...base, changed: "new" };

    render(<DiffView before={before} after={after} contextLines={3} />);
    const expander = screen.getByText(/Show \d+ hidden lines/);
    expect(expander).toBeInTheDocument();

    // Click to expand
    await userEvent.click(expander);
    expect(screen.queryByText(/Show \d+ hidden lines/)).not.toBeInTheDocument();
  });
});
