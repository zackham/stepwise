import type { OutputFieldSchema, OutputSchema } from "./types";

/**
 * Validate a single field value against its schema.
 * Returns error string or null if valid.
 */
export function validateField(
  name: string,
  value: unknown,
  schema: OutputFieldSchema,
): string | null {
  const required = schema.required !== false;

  // Blank / missing
  if (value === undefined || value === null || (typeof value === "string" && !value.trim())) {
    if (required && schema.default === undefined) {
      return `${name} is required`;
    }
    return null;
  }

  switch (schema.type) {
    case "number": {
      const num = typeof value === "number" ? value : Number(value);
      if (isNaN(num)) return `${name} must be a number`;
      if (schema.min !== undefined && num < schema.min)
        return `${name} must be at least ${schema.min}`;
      if (schema.max !== undefined && num > schema.max)
        return `${name} must be at most ${schema.max}`;
      return null;
    }
    case "bool":
      return null; // toggle always produces valid bool
    case "choice":
      if (schema.multiple) {
        if (!Array.isArray(value)) return `${name} must be a list`;
        const invalid = (value as string[]).filter(
          (v) => !schema.options?.includes(v),
        );
        if (invalid.length > 0)
          return `${name}: invalid choice(s): ${invalid.join(", ")}`;
      } else {
        if (schema.options && !schema.options.includes(value as string))
          return `${name}: must be one of ${schema.options.join(", ")}`;
      }
      return null;
    default:
      return null;
  }
}

/**
 * Validate all fields against optional schema.
 * Returns a map of field name → error string. Empty map = valid.
 */
export function validateAll(
  values: Record<string, unknown>,
  outputs: string[],
  schema?: OutputSchema,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const name of outputs) {
    const fieldSchema = schema?.[name];
    if (!fieldSchema) {
      // No schema — only check presence for required (default behavior)
      continue;
    }
    const error = validateField(name, values[name], fieldSchema);
    if (error) errors[name] = error;
  }
  return errors;
}
