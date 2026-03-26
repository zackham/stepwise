import { describe, it, expect } from "vitest";
import { tryParseJsonValue } from "../utils";

describe("tryParseJsonValue", () => {
  it("parses a JSON array string", () => {
    expect(tryParseJsonValue('["a", "b", "c"]')).toEqual(["a", "b", "c"]);
  });

  it("parses a JSON object string", () => {
    expect(tryParseJsonValue('{"key": "value"}')).toEqual({ key: "value" });
  });

  it("returns non-string values unchanged", () => {
    expect(tryParseJsonValue(42)).toBe(42);
    expect(tryParseJsonValue(null)).toBe(null);
    expect(tryParseJsonValue(undefined)).toBe(undefined);
    expect(tryParseJsonValue(true)).toBe(true);
    const arr = [1, 2, 3];
    expect(tryParseJsonValue(arr)).toBe(arr);
    const obj = { a: 1 };
    expect(tryParseJsonValue(obj)).toBe(obj);
  });

  it("returns plain strings unchanged", () => {
    expect(tryParseJsonValue("hello")).toBe("hello");
    expect(tryParseJsonValue("")).toBe("");
    expect(tryParseJsonValue("123")).toBe("123");
  });

  it("returns invalid JSON strings unchanged", () => {
    expect(tryParseJsonValue("[invalid")).toBe("[invalid");
    expect(tryParseJsonValue("{broken:}")).toBe("{broken:}");
  });

  it("handles strings with leading whitespace", () => {
    expect(tryParseJsonValue('  ["a"]')).toEqual(["a"]);
    expect(tryParseJsonValue('  {"k": 1}')).toEqual({ k: 1 });
  });

  it("parses empty array string", () => {
    expect(tryParseJsonValue("[]")).toEqual([]);
  });

  it("parses empty object string", () => {
    expect(tryParseJsonValue("{}")).toEqual({});
  });
});
