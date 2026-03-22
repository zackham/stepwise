import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useMediaQuery, useIsMobile } from "../useMediaQuery";

let listeners: Array<(e: { matches: boolean }) => void>;
let mockMql: {
  matches: boolean;
  media: string;
  addEventListener: ReturnType<typeof vi.fn>;
  removeEventListener: ReturnType<typeof vi.fn>;
};

beforeEach(() => {
  listeners = [];
  mockMql = {
    matches: false,
    media: "",
    addEventListener: vi.fn((_, cb) => listeners.push(cb)),
    removeEventListener: vi.fn((_, cb) => {
      listeners = listeners.filter((l) => l !== cb);
    }),
  };
  vi.mocked(window.matchMedia).mockImplementation(
    (query: string) =>
      ({
        ...mockMql,
        media: query,
        onchange: null,
        dispatchEvent: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
});

describe("useMediaQuery", () => {
  it("returns false when matchMedia does not match", () => {
    const { result } = renderHook(() => useMediaQuery("(max-width: 767px)"));
    expect(result.current).toBe(false);
  });

  it("returns true when matchMedia matches", () => {
    mockMql.matches = true;
    const { result } = renderHook(() => useMediaQuery("(max-width: 767px)"));
    expect(result.current).toBe(true);
  });

  it("updates when matchMedia fires a change event", () => {
    const { result } = renderHook(() => useMediaQuery("(max-width: 767px)"));
    expect(result.current).toBe(false);

    act(() => {
      listeners.forEach((cb) => cb({ matches: true }));
    });
    expect(result.current).toBe(true);
  });

  it("calls removeEventListener on unmount", () => {
    const { unmount } = renderHook(() => useMediaQuery("(max-width: 767px)"));
    expect(mockMql.addEventListener).toHaveBeenCalledWith(
      "change",
      expect.any(Function),
    );
    unmount();
    expect(mockMql.removeEventListener).toHaveBeenCalledWith(
      "change",
      expect.any(Function),
    );
  });
});

describe("useIsMobile", () => {
  it("queries max-width: 767px", () => {
    renderHook(() => useIsMobile());
    expect(window.matchMedia).toHaveBeenCalledWith("(max-width: 767px)");
  });
});
