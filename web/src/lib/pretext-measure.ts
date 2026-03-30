import { prepare, layout, type PreparedText } from "@chenglou/pretext";

const preparedCache = new Map<string, PreparedText>();
let pretextAvailable: boolean | null = null;

/**
 * Feature detection: checks if canvas text measurement actually works.
 * Returns false in jsdom where getContext("2d") returns null.
 */
export function isPretextAvailable(): boolean {
  if (pretextAvailable !== null) return pretextAvailable;
  try {
    if (typeof OffscreenCanvas !== "undefined") {
      const ctx = new OffscreenCanvas(1, 1).getContext("2d");
      pretextAvailable = ctx !== null;
      return pretextAvailable;
    }
    if (typeof document !== "undefined") {
      const ctx = document.createElement("canvas").getContext("2d");
      pretextAvailable = ctx !== null;
      return pretextAvailable;
    }
  } catch {
    // OffscreenCanvas constructor may throw in restricted contexts
  }
  pretextAvailable = false;
  return false;
}

export function prepareText(
  text: string,
  font: string,
  options?: { whiteSpace?: "normal" | "pre-wrap" },
): PreparedText | null {
  if (!isPretextAvailable()) return null;

  const key = `${font}|${options?.whiteSpace ?? "normal"}|${text}`;
  const cached = preparedCache.get(key);
  if (cached) return cached;

  try {
    const prepared = prepare(text, font, options);
    preparedCache.set(key, prepared);
    return prepared;
  } catch {
    return null;
  }
}

export function measureTextHeight(
  text: string,
  font: string,
  maxWidth: number,
  lineHeight: number,
  options?: { whiteSpace?: "normal" | "pre-wrap" },
): number {
  if (maxWidth <= 0 || !text) return lineHeight;

  const prepared = prepareText(text, font, options);
  if (prepared) {
    const result = layout(prepared, maxWidth, lineHeight);
    return result.height;
  }

  // Fallback: character-count heuristic (monospace assumption)
  const fontSizeMatch = font.match(/(\d+(?:\.\d+)?)px/);
  const fontSize = fontSizeMatch ? parseFloat(fontSizeMatch[1]) : 14;
  const charWidth = fontSize * 0.6;
  const charsPerLine = Math.max(1, Math.floor(maxWidth / charWidth));
  const lines = Math.max(1, Math.ceil(text.length / charsPerLine));
  return lines * lineHeight;
}

export function clearPreparedCache(): void {
  preparedCache.clear();
}

// Reset feature detection (for testing)
export function _resetPretextAvailable(): void {
  pretextAvailable = null;
}
