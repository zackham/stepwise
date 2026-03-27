import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * If `value` is a string that looks like a JSON array or object, parse and return it.
 * Otherwise return the original value unchanged.
 */
export function tryParseJsonValue(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trimStart();
  if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  }
  return value;
}

/**
 * Safely convert an unknown value to a string for rendering as a React child.
 * Prevents React Error #31 when objects/arrays are passed as JSX children.
 */
export function safeRenderValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

/**
 * Format a duration between two timestamps as a human-readable string.
 * If `end` is null, uses current time (for in-progress durations).
 */
export function formatDuration(start: string | null, end: string | null): string {
  if (!start) return "-";
  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : Date.now();
  const ms = endMs - startMs;
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3600000) return `${(ms / 60000).toFixed(1)}m`;
  return `${(ms / 3600000).toFixed(1)}h`;
}
