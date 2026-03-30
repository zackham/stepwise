import { describe, it, expect, afterEach } from "vitest";
import {
  measureTextHeight,
  clearPreparedCache,
  isPretextAvailable,
  prepareText,
  _resetPretextAvailable,
} from "../pretext-measure";

afterEach(() => {
  clearPreparedCache();
  _resetPretextAvailable();
  // Clean up any OffscreenCanvas mock
  Reflect.deleteProperty(globalThis, "OffscreenCanvas");
});

describe("isPretextAvailable", () => {
  it("returns false in jsdom (canvas getContext returns null)", () => {
    expect(isPretextAvailable()).toBe(false);
  });

  it("returns true when OffscreenCanvas is available with working context", () => {
    Object.defineProperty(globalThis, "OffscreenCanvas", {
      configurable: true,
      value: class {
        getContext() {
          return { font: "", measureText: () => ({ width: 10 }) };
        }
      },
    });
    _resetPretextAvailable();
    expect(isPretextAvailable()).toBe(true);
  });

  it("caches result after first check", () => {
    const result1 = isPretextAvailable();
    const result2 = isPretextAvailable();
    expect(result1).toBe(result2);
  });
});

describe("prepareText", () => {
  it("returns null in jsdom (pretext unavailable)", () => {
    expect(prepareText("hello", "14px monospace")).toBeNull();
  });

  it("caches prepare results for identical inputs", () => {
    const r1 = prepareText("hello", "14px monospace");
    const r2 = prepareText("hello", "14px monospace");
    expect(r1).toBe(r2);
  });
});

describe("measureTextHeight", () => {
  it("returns single lineHeight for short text", () => {
    const h = measureTextHeight("hello", "14px monospace", 500, 20);
    expect(h).toBe(20);
  });

  it("returns multi-line height for long text that wraps", () => {
    // 200 chars at 14px monospace: charWidth=8.4, charsPerLine=floor(100/8.4)=11
    // 200/11 = ceil(18.18) = 19 lines -> 19 * 20 = 380
    const h = measureTextHeight("a".repeat(200), "14px monospace", 100, 20);
    expect(h).toBe(380);
  });

  it("returns lineHeight for empty string", () => {
    expect(measureTextHeight("", "14px monospace", 500, 20)).toBe(20);
  });

  it("returns lineHeight for zero-width container", () => {
    expect(measureTextHeight("hello", "14px monospace", 0, 20)).toBe(20);
  });

  it("handles negative maxWidth", () => {
    expect(measureTextHeight("hello", "14px monospace", -100, 20)).toBe(20);
  });

  it("extracts font size from font string for fallback calculation", () => {
    // "10px monospace" -> charWidth = 6, charsPerLine = floor(60/6) = 10
    // 30 chars / 10 = 3 lines -> 3 * 16 = 48
    expect(measureTextHeight("a".repeat(30), "10px monospace", 60, 16)).toBe(48);
  });

  it("defaults to 14px font size when font string has no px value", () => {
    expect(measureTextHeight("hello", "monospace", 500, 20)).toBe(20);
  });

  it("handles very narrow container (1 char per line)", () => {
    // 10px font -> charWidth=6, charsPerLine=floor(6/6)=1
    // 5 chars / 1 = 5 lines -> 5 * 20 = 100
    expect(measureTextHeight("hello", "10px monospace", 6, 20)).toBe(100);
  });
});

describe("clearPreparedCache", () => {
  it("resets cache so next call re-prepares", () => {
    prepareText("hello", "14px monospace");
    clearPreparedCache();
    expect(prepareText("hello", "14px monospace")).toBeNull();
  });
});
