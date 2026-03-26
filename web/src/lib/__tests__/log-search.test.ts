import { describe, it, expect } from "vitest";
import { highlightMatches, countMatches, escapeRegex } from "../log-search";
import { isValidElement } from "react";

describe("highlightMatches", () => {
  it("returns plain text when regex is null", () => {
    expect(highlightMatches("hello world", null)).toBe("hello world");
  });

  it("returns plain text when no match", () => {
    const regex = /xyz/gi;
    expect(highlightMatches("hello world", regex)).toBe("hello world");
  });

  it("wraps matched substrings in mark elements", () => {
    const regex = /error/gi;
    const result = highlightMatches("An error occurred", regex);
    expect(Array.isArray(result)).toBe(true);
    const nodes = result as React.ReactNode[];
    // Should contain "An ", <mark>error</mark>, " occurred"
    const marks = nodes.filter(
      (n) => typeof n === "object" && n !== null && isValidElement(n)
    );
    expect(marks.length).toBe(1);
  });

  it("handles multiple matches per line", () => {
    const regex = /data/gi;
    const result = highlightMatches("data processing data", regex);
    expect(Array.isArray(result)).toBe(true);
    const nodes = result as React.ReactNode[];
    const marks = nodes.filter(
      (n) => typeof n === "object" && n !== null && isValidElement(n)
    );
    expect(marks.length).toBe(2);
  });

  it("returns original text for empty string", () => {
    expect(highlightMatches("", /test/g)).toBe("");
  });
});

describe("countMatches", () => {
  it("returns 0 for null regex", () => {
    expect(countMatches("hello world", null)).toBe(0);
  });

  it("returns correct count for multiple matches", () => {
    expect(countMatches("data processing data output", /data/gi)).toBe(2);
  });

  it("handles case-insensitive matching", () => {
    expect(countMatches("Error error ERROR", /error/gi)).toBe(3);
  });

  it("returns 0 for no match", () => {
    expect(countMatches("hello world", /xyz/g)).toBe(0);
  });

  it("returns 0 for empty text", () => {
    expect(countMatches("", /test/g)).toBe(0);
  });
});

describe("escapeRegex", () => {
  it("escapes special regex characters", () => {
    expect(escapeRegex("step.completed")).toBe("step\\.completed");
    expect(escapeRegex("[test]")).toBe("\\[test\\]");
    expect(escapeRegex("a*b+c?")).toBe("a\\*b\\+c\\?");
  });

  it("leaves normal text unchanged", () => {
    expect(escapeRegex("hello")).toBe("hello");
  });
});
