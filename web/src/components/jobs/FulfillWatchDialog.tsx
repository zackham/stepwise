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
import type { StepRun } from "@/lib/types";

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
  const [values, setValues] = useState<Record<string, string>>({});
  const [jsonMode, setJsonMode] = useState(false);
  const [jsonInput, setJsonInput] = useState("{}");
  const [jsonError, setJsonError] = useState<string | null>(null);

  const prompt =
    (run.watch?.config?.prompt as string) ?? "Provide the required outputs";

  const handleSubmit = () => {
    if (jsonMode) {
      try {
        const parsed = JSON.parse(jsonInput);
        if (typeof parsed !== "object" || parsed === null) {
          setJsonError("Must be a JSON object");
          return;
        }
        setJsonError(null);
        onFulfill(parsed);
      } catch {
        setJsonError("Invalid JSON");
      }
    } else {
      const payload: Record<string, unknown> = {};
      for (const key of outputs) {
        const val = values[key] ?? "";
        // Try to parse as JSON first
        try {
          payload[key] = JSON.parse(val);
        } catch {
          payload[key] = val;
        }
      }
      onFulfill(payload);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>Fulfill Watch</DialogTitle>
          <DialogDescription>{prompt}</DialogDescription>
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
              outputs.map((field) => (
                <div key={field} className="space-y-1">
                  <Label className="text-sm">{field}</Label>
                  <Input
                    value={values[field] ?? ""}
                    onChange={(e) =>
                      setValues((prev) => ({
                        ...prev,
                        [field]: e.target.value,
                      }))
                    }
                    placeholder={`Value for ${field}`}
                    className="font-mono text-sm"
                  />
                </div>
              ))
            )}
          </div>
        )}

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
