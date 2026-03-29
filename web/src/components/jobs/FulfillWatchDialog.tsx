import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { StepRun, OutputSchema } from "@/lib/types";
import { TypedField } from "@/components/dag/TypedField";
import { validateAll } from "@/lib/validate-fields";

interface FulfillWatchDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  run: StepRun;
  onFulfill: (payload: Record<string, unknown>) => void;
  isPending: boolean;
}

export function FulfillWatchDialog({
  open,
  onOpenChange,
  run,
  onFulfill,
  isPending,
}: FulfillWatchDialogProps) {
  const outputs = run.watch?.fulfillment_outputs ?? [];
  const outputSchema = run.watch?.output_schema as OutputSchema | undefined;
  const hasSchema = outputSchema && Object.keys(outputSchema).length > 0;

  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const initial: Record<string, unknown> = {};
    if (outputSchema) {
      for (const [name, spec] of Object.entries(outputSchema)) {
        if (spec.default !== undefined) {
          initial[name] = spec.default;
        }
      }
    }
    return initial;
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState("");
  const [jsonMode, setJsonMode] = useState(false);
  const [jsonInput, setJsonInput] = useState("{}");
  const [jsonError, setJsonError] = useState<string | null>(null);

  const prompt =
    (run.watch?.config?.prompt as string) ?? "Provide the required outputs";

  const handleSubmit = () => {
    const trimmedNotes = notes.trim();
    if (jsonMode) {
      try {
        const parsed = JSON.parse(jsonInput);
        if (typeof parsed !== "object" || parsed === null) {
          setJsonError("Must be a JSON object");
          return;
        }
        setJsonError(null);
        const final = { ...parsed };
        if (trimmedNotes) final._fulfillment_notes = trimmedNotes;
        onFulfill(final);
      } catch {
        setJsonError("Invalid JSON");
      }
    } else {
      if (hasSchema) {
        const validationErrors = validateAll(values, outputs, outputSchema);
        if (Object.keys(validationErrors).length > 0) {
          setErrors(validationErrors);
          return;
        }
        setErrors({});
      }

      const payload: Record<string, unknown> = {};
      for (const key of outputs) {
        const val = values[key];
        if (val !== undefined && val !== null) {
          if (!hasSchema && typeof val === "string") {
            try {
              payload[key] = JSON.parse(val);
            } catch {
              payload[key] = val;
            }
          } else {
            payload[key] = val;
          }
        }
      }
      if (trimmedNotes) payload._fulfillment_notes = trimmedNotes;
      onFulfill(payload);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px] max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Fulfill Watch</DialogTitle>
          <DialogDescription className="whitespace-pre-wrap max-h-[40vh] overflow-y-auto">{prompt.trim()}</DialogDescription>
        </DialogHeader>

        <div className="flex justify-end mb-2">
          <button
            className="text-xs text-zinc-500 hover:text-zinc-300"
            onClick={() => setJsonMode(!jsonMode)}
          >
            {jsonMode ? "Field mode" : "JSON mode"}
          </button>
        </div>

        {jsonMode ? (
          <div className="space-y-2">
            <Textarea
              value={jsonInput}
              onChange={(e) => {
                setJsonInput(e.target.value);
                setJsonError(null);
              }}
              className="font-mono text-sm min-h-[120px]"
              placeholder='{"field": "value"}'
            />
            {jsonError && (
              <p className="text-red-400 text-xs">{jsonError}</p>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            {outputs.length === 0 ? (
              <div className="text-zinc-500 text-sm">
                No specific outputs required. Switch to JSON mode to provide
                arbitrary data.
              </div>
            ) : (
              outputs.map((field) => {
                const fieldSchema = outputSchema?.[field];
                if (fieldSchema) {
                  return (
                    <TypedField
                      key={field}
                      name={field}
                      schema={fieldSchema}
                      value={values[field]}
                      onChange={(val) =>
                        setValues((prev) => ({ ...prev, [field]: val }))
                      }
                      error={errors[field]}
                    />
                  );
                }
                return (
                  <div key={field} className="space-y-1">
                    <Label className="text-sm">{field}</Label>
                    <Input
                      value={(values[field] as string) ?? ""}
                      onChange={(e) =>
                        setValues((prev) => ({
                          ...prev,
                          [field]: e.target.value,
                        }))
                      }
                      placeholder={`Value for ${field}`}
                      className="font-mono text-sm min-h-[44px]"
                    />
                  </div>
                );
              })
            )}
          </div>
        )}

        <div className="space-y-1">
          <Label className="text-sm text-zinc-500">Notes (optional)</Label>
          <Textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="text-sm min-h-[60px]"
            placeholder="Add context or rationale for this fulfillment..."
          />
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={isPending}>
            {isPending ? "Fulfilling..." : "Fulfill"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
