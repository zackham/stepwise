import { describe, it, expect } from "vitest";
import { validateField, validateAll } from "../validate-fields";

describe("validateField", () => {
  it("returns null for valid str", () => {
    expect(validateField("name", "Alice", { type: "str" })).toBeNull();
  });

  it("returns error for required blank str", () => {
    expect(validateField("name", "", { type: "str" })).toBe("name is required");
  });

  it("allows blank for optional str", () => {
    expect(
      validateField("name", "", { type: "str", required: false }),
    ).toBeNull();
  });

  it("allows blank when default exists", () => {
    expect(
      validateField("name", "", { type: "str", default: "world" }),
    ).toBeNull();
  });

  it("validates number type", () => {
    expect(validateField("score", "7.5", { type: "number" })).toBeNull();
    expect(validateField("score", "abc", { type: "number" })).toBe(
      "score must be a number",
    );
  });

  it("validates number min/max", () => {
    expect(
      validateField("score", 15, { type: "number", min: 0, max: 10 }),
    ).toBe("score must be at most 10");
    expect(
      validateField("score", -1, { type: "number", min: 0, max: 10 }),
    ).toBe("score must be at least 0");
    expect(
      validateField("score", 5, { type: "number", min: 0, max: 10 }),
    ).toBeNull();
  });

  it("validates choice single", () => {
    expect(
      validateField("pick", "a", {
        type: "choice",
        options: ["a", "b", "c"],
      }),
    ).toBeNull();
    expect(
      validateField("pick", "x", {
        type: "choice",
        options: ["a", "b", "c"],
      }),
    ).toBe("pick: must be one of a, b, c");
  });

  it("validates choice multiple", () => {
    expect(
      validateField("picks", ["a", "c"], {
        type: "choice",
        options: ["a", "b", "c"],
        multiple: true,
      }),
    ).toBeNull();
    expect(
      validateField("picks", ["a", "x"], {
        type: "choice",
        options: ["a", "b"],
        multiple: true,
      }),
    ).toBe("picks: invalid choice(s): x");
    expect(
      validateField("picks", "a", {
        type: "choice",
        options: ["a"],
        multiple: true,
      }),
    ).toBe("picks must be a list");
  });

  it("bool is always valid", () => {
    expect(validateField("ok", true, { type: "bool" })).toBeNull();
    expect(validateField("ok", false, { type: "bool" })).toBeNull();
  });
});

describe("validateAll", () => {
  it("returns empty for valid payload", () => {
    const errors = validateAll(
      { score: 5, decision: "yes" },
      ["score", "decision"],
      {
        score: { type: "number", min: 0, max: 10 },
        decision: { type: "choice", options: ["yes", "no"] },
      },
    );
    expect(errors).toEqual({});
  });

  it("returns errors for invalid fields", () => {
    const errors = validateAll(
      { score: 15, decision: "maybe" },
      ["score", "decision"],
      {
        score: { type: "number", min: 0, max: 10 },
        decision: { type: "choice", options: ["yes", "no"] },
      },
    );
    expect(Object.keys(errors)).toHaveLength(2);
    expect(errors.score).toContain("at most");
    expect(errors.decision).toContain("must be one of");
  });

  it("skips fields without schema", () => {
    const errors = validateAll({ answer: "hello" }, ["answer"]);
    expect(errors).toEqual({});
  });
});
