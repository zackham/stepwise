import { describe, it, expect } from "vitest";
import { getGuidance, ERROR_GUIDANCE } from "../error-guidance";

describe("getGuidance", () => {
  it("returns known guidance for each category", () => {
    for (const key of Object.keys(ERROR_GUIDANCE)) {
      const g = getGuidance(key);
      expect(g.title).toBeTruthy();
      expect(g.description).toBeTruthy();
      expect(g.suggestions.length).toBeGreaterThan(0);
      expect(typeof g.retryable).toBe("boolean");
    }
  });

  it("returns correct guidance for auth_error", () => {
    const g = getGuidance("auth_error");
    expect(g.title).toBe("Authentication Error");
    expect(g.retryable).toBe(false);
  });

  it("returns correct guidance for timeout", () => {
    const g = getGuidance("timeout");
    expect(g.title).toBe("Timeout");
    expect(g.retryable).toBe(true);
  });

  it("returns unknown guidance for unrecognized category", () => {
    const g = getGuidance("something_weird");
    expect(g.title).toBe("Unknown Error");
    expect(g.retryable).toBe(true);
  });

  it("returns fallback for null", () => {
    const g = getGuidance(null);
    expect(g.title).toBe("Error");
    expect(g.description).toContain("No error category");
  });

  it("returns fallback for undefined", () => {
    const g = getGuidance(undefined);
    expect(g.title).toBe("Error");
  });
});
