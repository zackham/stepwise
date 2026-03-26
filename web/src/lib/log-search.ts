import { createElement } from "react";
import type { ReactNode } from "react";

/**
 * Split text by regex matches and return React nodes with highlight marks.
 * Returns the original string when regex is null (zero allocation).
 */
export function highlightMatches(
  text: string,
  regex: RegExp | null
): ReactNode {
  if (!regex || !text) return text;

  // Use a capturing-group split so matched parts are interleaved
  const parts = text.split(new RegExp(`(${regex.source})`, regex.flags));
  if (parts.length === 1) return text; // no match

  const nodes: ReactNode[] = [];
  const testRe = new RegExp(regex.source, regex.flags);
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    if (!part) continue;
    // Reset lastIndex for test
    testRe.lastIndex = 0;
    if (testRe.test(part) && part.length > 0) {
      nodes.push(
        createElement(
          "mark",
          {
            key: i,
            className:
              "bg-yellow-500/30 text-yellow-200 rounded-sm px-0.5",
          },
          part
        )
      );
    } else {
      nodes.push(part);
    }
  }
  return nodes;
}

/**
 * Count total regex matches in a string.
 */
export function countMatches(text: string, regex: RegExp | null): number {
  if (!regex || !text) return 0;
  const matches = text.match(new RegExp(regex.source, regex.flags));
  return matches ? matches.length : 0;
}

/**
 * Escape special regex characters for literal matching.
 */
export function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
