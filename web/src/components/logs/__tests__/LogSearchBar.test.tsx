import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LogSearchBar } from "../LogSearchBar";
import type { LogSearchState } from "@/hooks/useLogSearch";
import { createRef } from "react";

function makeSearch(overrides: Partial<LogSearchState> = {}): LogSearchState {
  return {
    query: "",
    setQuery: vi.fn(),
    caseSensitive: false,
    toggleCaseSensitive: vi.fn(),
    regexMode: false,
    toggleRegexMode: vi.fn(),
    regexError: false,
    compiledRegex: null,
    matchCount: 0,
    setMatchCount: vi.fn(),
    searchInputRef: createRef<HTMLInputElement>(),
    ...overrides,
  };
}

describe("LogSearchBar", () => {
  it("renders search input and toggle buttons", () => {
    render(<LogSearchBar search={makeSearch()} />);
    expect(screen.getByPlaceholderText("Search logs...")).toBeInTheDocument();
    expect(screen.getByText("Aa")).toBeInTheDocument();
    expect(screen.getByText(".*")).toBeInTheDocument();
  });

  it("calls setQuery on input change", () => {
    const search = makeSearch();
    render(<LogSearchBar search={search} />);
    fireEvent.change(screen.getByPlaceholderText("Search logs..."), {
      target: { value: "test" },
    });
    expect(search.setQuery).toHaveBeenCalledWith("test");
  });

  it("calls toggleCaseSensitive on Aa click", () => {
    const search = makeSearch();
    render(<LogSearchBar search={search} />);
    fireEvent.click(screen.getByText("Aa"));
    expect(search.toggleCaseSensitive).toHaveBeenCalled();
  });

  it("calls toggleRegexMode on .* click", () => {
    const search = makeSearch();
    render(<LogSearchBar search={search} />);
    fireEvent.click(screen.getByText(".*"));
    expect(search.toggleRegexMode).toHaveBeenCalled();
  });

  it("shows match count when query is present", () => {
    render(<LogSearchBar search={makeSearch({ query: "test", matchCount: 42 })} />);
    expect(screen.getByText("42 matches")).toBeInTheDocument();
  });

  it("shows singular match for count of 1", () => {
    render(<LogSearchBar search={makeSearch({ query: "test", matchCount: 1 })} />);
    expect(screen.getByText("1 match")).toBeInTheDocument();
  });

  it("hides match count when query is empty", () => {
    render(<LogSearchBar search={makeSearch({ matchCount: 5 })} />);
    expect(screen.queryByText(/match/)).toBeNull();
  });

  it("shows error indicator when regexError is true", () => {
    render(<LogSearchBar search={makeSearch({ query: "[", regexError: true })} />);
    expect(screen.getByText("invalid")).toBeInTheDocument();
  });

  it("highlights active case-sensitive toggle", () => {
    render(<LogSearchBar search={makeSearch({ caseSensitive: true })} />);
    const aaButton = screen.getByText("Aa");
    expect(aaButton.className).toContain("text-blue-400");
  });

  it("highlights active regex toggle", () => {
    render(<LogSearchBar search={makeSearch({ regexMode: true })} />);
    const regexButton = screen.getByText(".*");
    expect(regexButton.className).toContain("text-blue-400");
  });
});
