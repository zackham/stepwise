import type { WatchSpec } from "@/lib/types";

/** Extract ExternalInputPanel props from an external watch spec. */
export function getWatchProps(watch: WatchSpec | null | undefined) {
  if (!watch || watch.mode !== "external") return null;
  return {
    prompt: (watch.config?.prompt as string) ?? "Provide the required input",
    outputs: watch.fulfillment_outputs ?? [],
    outputSchema: watch.output_schema,
  };
}
