import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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

const TEXTAREA_HINTS = ["prompt", "question", "description", "context", "spec"];

function isTextArea(field: string): boolean {
  return TEXTAREA_HINTS.some((h) => field.includes(h));
}

interface JobInputFormProps {
  fields: string[];
  values: Record<string, string>;
  onChange: (field: string, value: string) => void;
}

export function JobInputForm({ fields, values, onChange }: JobInputFormProps) {
  if (fields.length === 0) return null;
  return (
    <div className="space-y-3">
      {fields.map((field) => (
        <div key={field} className="space-y-1">
          <label className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
            {field}
          </label>
          {isTextArea(field) ? (
            <Textarea
              value={values[field] ?? ""}
              onChange={(e) => onChange(field, e.target.value)}
              placeholder={field}
              className="text-xs min-h-[60px] bg-white dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700"
            />
          ) : (
            <Input
              value={values[field] ?? ""}
              onChange={(e) => onChange(field, e.target.value)}
              placeholder={field}
              className="text-xs bg-white dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700"
            />
          )}
        </div>
      ))}
    </div>
  );
}
