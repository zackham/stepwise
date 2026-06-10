import type { FlowDefinition } from "@/lib/types";

/** Extract unique $job input field names from a flow definition. */
export function extractJobInputs(flow: FlowDefinition): string[] {
  const fields = new Set<string>();
  for (const step of Object.values(flow.steps)) {
    for (const binding of step.inputs ?? []) {
      if (binding.source_step === "$job") {
        fields.add(binding.source_field);
      }
    }
  }
  return [...fields].sort();
}
