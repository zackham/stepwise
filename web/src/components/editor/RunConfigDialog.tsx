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
import { TypedField } from "@/components/dag/TypedField";
import { validateAll } from "@/lib/validate-fields";
import type { ConfigVar, OutputFieldSchema } from "@/lib/types";

interface RunConfigDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  configVars: ConfigVar[];
  onRun: (inputs: Record<string, unknown>) => void;
  isPending: boolean;
}

/** Convert a ConfigVar to OutputFieldSchema so TypedField can render it. */
function toFieldSchema(v: ConfigVar): OutputFieldSchema {
  return {
    type: v.type ?? "str",
    required: v.required !== false,
    default: v.default,
    description: v.description,
    options: v.options,
  };
}

export function RunConfigDialog({
  open,
  onOpenChange,
  configVars,
  onRun,
  isPending,
}: RunConfigDialogProps) {
  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const initial: Record<string, unknown> = {};
    for (const v of configVars) {
      if (v.default !== undefined && v.default !== null) {
        initial[v.name] = v.default;
      }
    }
    return initial;
  });
  const [errors, setErrors] = useState<Record<string, string>>({});

  const schema: Record<string, OutputFieldSchema> = {};
  for (const v of configVars) {
    schema[v.name] = toFieldSchema(v);
  }
  const fieldNames = configVars.map((v) => v.name);

  const handleSubmit = () => {
    const validationErrors = validateAll(values, fieldNames, schema);
    if (Object.keys(validationErrors).length > 0) {
      setErrors(validationErrors);
      return;
    }
    setErrors({});

    const inputs: Record<string, unknown> = {};
    for (const name of fieldNames) {
      const val = values[name];
      if (val !== undefined && val !== null && val !== "") {
        inputs[name] = val;
      }
    }
    onRun(inputs);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px] max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Configure inputs</DialogTitle>
          <DialogDescription>
            This flow requires configuration before running.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {configVars.map((v, i) => (
            <TypedField
              key={v.name}
              name={v.name}
              schema={schema[v.name]}
              value={values[v.name]}
              onChange={(val) =>
                setValues((prev) => ({ ...prev, [v.name]: val }))
              }
              error={errors[v.name]}
              autoFocus={i === 0}
            />
          ))}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={isPending}>
            {isPending ? "Starting..." : "Run"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
