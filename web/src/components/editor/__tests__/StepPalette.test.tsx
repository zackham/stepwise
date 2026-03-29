import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement } from "react";
import type { ReactNode } from "react";
import { StepPalette } from "../StepPalette";

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

function renderWithQuery(ui: React.ReactElement) {
  return render(ui, { wrapper: createWrapper() });
}

describe("StepPalette", () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    existingStepNames: ["fetch", "analyze"],
    onAdd: vi.fn(),
    isPending: false,
  };

  it("renders all 5 executor type cards in phase 1", () => {
    renderWithQuery(<StepPalette {...defaultProps} />);
    expect(screen.getByText("Script")).toBeInTheDocument();
    expect(screen.getByText("LLM")).toBeInTheDocument();
    expect(screen.getByText("Agent")).toBeInTheDocument();
    expect(screen.getByText("Human")).toBeInTheDocument();
    expect(screen.getByText("Poll")).toBeInTheDocument();
  });

  it("transitions to name input after selecting a type", () => {
    renderWithQuery(<StepPalette {...defaultProps} />);
    fireEvent.click(screen.getByText("Script"));
    expect(screen.getByPlaceholderText(/step name/i)).toBeInTheDocument();
  });

  it("back button returns to type selection", () => {
    renderWithQuery(<StepPalette {...defaultProps} />);
    fireEvent.click(screen.getByText("Script"));
    const backButton = screen.getByTitle(/back/i);
    fireEvent.click(backButton);
    expect(screen.getByText("LLM")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/step name/i)).not.toBeInTheDocument();
  });

  it("shows error for duplicate step name", () => {
    renderWithQuery(<StepPalette {...defaultProps} />);
    fireEvent.click(screen.getByText("Script"));
    const input = screen.getByPlaceholderText(/step name/i);
    fireEvent.change(input, { target: { value: "fetch" } });
    expect(screen.getByText(/already exists/i)).toBeInTheDocument();
  });

  it("disables submit when name is empty", () => {
    renderWithQuery(<StepPalette {...defaultProps} />);
    fireEvent.click(screen.getByText("Script"));
    const submitButton = screen.getByRole("button", { name: /add step/i });
    expect(submitButton).toBeDisabled();
  });

  it("calls onAdd with correct name and executor on submit", () => {
    const onAdd = vi.fn();
    renderWithQuery(<StepPalette {...defaultProps} onAdd={onAdd} />);
    fireEvent.click(screen.getByText("Agent"));
    const input = screen.getByPlaceholderText(/step name/i);
    fireEvent.change(input, { target: { value: "plan-impl" } });
    const submitButton = screen.getByRole("button", { name: /add step/i });
    fireEvent.click(submitButton);
    expect(onAdd).toHaveBeenCalledWith("plan-impl", "agent");
  });

  it("resets state when dialog closes and reopens", () => {
    const { rerender } = renderWithQuery(<StepPalette {...defaultProps} />);
    fireEvent.click(screen.getByText("Script"));
    rerender(<StepPalette {...defaultProps} open={false} />);
    rerender(<StepPalette {...defaultProps} open={true} />);
    expect(screen.getByText("Script")).toBeInTheDocument();
    expect(screen.getByText("LLM")).toBeInTheDocument();
  });

  it("does not render when open is false", () => {
    renderWithQuery(<StepPalette {...defaultProps} open={false} />);
    expect(screen.queryByText("Script")).not.toBeInTheDocument();
  });
});
