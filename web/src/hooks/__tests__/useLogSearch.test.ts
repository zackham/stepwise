import { describe, it, expect } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useLogSearch } from "../useLogSearch";

describe("useLogSearch", () => {
  it("has correct initial state", () => {
    const { result } = renderHook(() => useLogSearch());
    expect(result.current.query).toBe("");
    expect(result.current.caseSensitive).toBe(false);
    expect(result.current.regexMode).toBe(false);
    expect(result.current.regexError).toBe(false);
    expect(result.current.compiledRegex).toBeNull();
    expect(result.current.matchCount).toBe(0);
  });

  it("produces a compiled regex when query is set", () => {
    const { result } = renderHook(() => useLogSearch());
    act(() => result.current.setQuery("error"));
    expect(result.current.compiledRegex).not.toBeNull();
    expect(result.current.compiledRegex?.flags).toContain("i"); // case-insensitive default
    expect(result.current.compiledRegex?.flags).toContain("g");
  });

  it("toggling case sensitivity changes regex flags", () => {
    const { result } = renderHook(() => useLogSearch());
    act(() => result.current.setQuery("test"));
    expect(result.current.compiledRegex?.flags).toContain("i");

    act(() => result.current.toggleCaseSensitive());
    expect(result.current.caseSensitive).toBe(true);
    expect(result.current.compiledRegex?.flags).not.toContain("i");
  });

  it("invalid regex in regex mode sets regexError and compiledRegex to null", () => {
    const { result } = renderHook(() => useLogSearch());
    act(() => result.current.toggleRegexMode());
    act(() => result.current.setQuery("["));
    expect(result.current.regexError).toBe(true);
    expect(result.current.compiledRegex).toBeNull();
  });

  it("valid regex in regex mode works", () => {
    const { result } = renderHook(() => useLogSearch());
    act(() => result.current.toggleRegexMode());
    act(() => result.current.setQuery("step\\.\\w+"));
    expect(result.current.regexError).toBe(false);
    expect(result.current.compiledRegex).not.toBeNull();
  });

  it("clearing query resets compiled regex to null", () => {
    const { result } = renderHook(() => useLogSearch());
    act(() => result.current.setQuery("test"));
    expect(result.current.compiledRegex).not.toBeNull();

    act(() => result.current.setQuery(""));
    expect(result.current.compiledRegex).toBeNull();
  });

  it("escapes special chars in literal mode", () => {
    const { result } = renderHook(() => useLogSearch());
    // In literal mode, "step.completed" should match literal dots
    act(() => result.current.setQuery("step.completed"));
    expect(result.current.compiledRegex?.source).toContain("\\.");
  });
});
