import { useState, useRef, useEffect, useCallback } from "react";
import { Send, Loader2 } from "lucide-react";
import type { WatchSpec, OutputSchema } from "@/lib/types";
import { TypedField } from "./TypedField";
import { validateAll } from "@/lib/validate-fields";

interface ExternalInputPanelProps {
  prompt: string;
  outputs: string[];
  outputSchema?: OutputSchema;
  onSubmit: (payload: Record<string, unknown>) => void;
  isPending: boolean;
  submitError?: string;
}

function AutoTextarea({
  value,
  onChange,
  onKeyDown,
  placeholder,
  className,
  inputRef,
}: {
  value: string;
  onChange: (e: React.ChangeEvent<HTMLTextAreaElement>) => void;
  onKeyDown?: (e: React.KeyboardEvent<HTMLTextAreaElement>) => void;
  placeholder?: string;
  className?: string;
  inputRef?: React.Ref<HTMLTextAreaElement>;
}) {
  const internalRef = useRef<HTMLTextAreaElement>(null);
  const ref = (inputRef as React.RefObject<HTMLTextAreaElement>) ?? internalRef;

  const resize = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0";
    el.style.height = el.scrollHeight + "px";
  }, [ref]);

  useEffect(() => resize(), [value, resize]);

  return (
    <textarea
      ref={ref}
      rows={1}
      value={value}
      onChange={onChange}
      onKeyDown={onKeyDown}
      placeholder={placeholder}
      className={className}
      style={{ resize: "none", overflow: "hidden" }}
    />
  );
}

export function ExternalInputPanel({
  prompt,
  outputs,
  outputSchema,
  onSubmit,
  isPending,
  submitError,
}: ExternalInputPanelProps) {
  const [values, setValues] = useState<Record<string, unknown>>(() => {
    // Initialize defaults from schema
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
  const firstInputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    firstInputRef.current?.focus();
  }, []);

  const hasSchema = outputSchema && Object.keys(outputSchema).length > 0;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

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
        // For untyped fields that are strings, try JSON parse
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
    onSubmit(payload);
  };

  const hasValues =
    outputs.length === 0 ||
    outputs.some((k) => {
      const v = values[k];
      if (v === undefined || v === null) return false;
      if (typeof v === "string") return v.trim().length > 0;
      return true;
    });

  const textareaClass =
    "w-full min-h-[44px] rounded-md border border-zinc-300 dark:border-zinc-700 bg-zinc-50/80 dark:bg-zinc-800/80 px-2.5 py-2 text-sm text-foreground placeholder:text-zinc-500 dark:placeholder:text-zinc-600 focus:outline-none focus:border-amber-500/50 focus:ring-1 focus:ring-amber-500/20 transition-colors";

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && hasValues) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  return (
    <div
      className="w-[26rem] max-w-[calc(100vw-2rem)] rounded-lg border border-amber-500/30 bg-white/95 dark:bg-zinc-900/95 backdrop-blur-sm shadow-xl shadow-amber-500/5 flex flex-col"
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
      onWheel={(e) => e.stopPropagation()}
    >
      {/* Connector arrow */}
      <div className="flex justify-center -mt-2">
        <div className="w-3 h-3 rotate-45 bg-white dark:bg-zinc-900 border-l border-t border-amber-500/30" />
      </div>

      <form onSubmit={handleSubmit} className="flex flex-col flex-1 min-h-0">
        {/* Scrollable body — fixed max height, submit always visible below */}
        <div className="p-3 pt-1 space-y-2.5 overflow-y-auto max-h-[300px]">
        {/* Prompt */}
        <pre className="text-xs font-sans text-amber-800 dark:text-amber-200/80 leading-relaxed whitespace-pre-wrap">{prompt.trim()}</pre>

        {/* Fields */}
        {outputs.length > 0 ? (
          <div className="space-y-2">
            {outputs.map((field, i) => {
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
                    autoFocus={i === 0}
                  />
                );
              }
              // Fallback: untyped textarea
              return (
                <div key={field}>
                  <label className="block text-[10px] font-medium text-zinc-500 uppercase tracking-wide mb-1">
                    {field}
                  </label>
                  <AutoTextarea
                    inputRef={i === 0 ? firstInputRef : undefined}
                    value={(values[field] as string) ?? ""}
                    onChange={(e) =>
                      setValues((prev) => ({
                        ...prev,
                        [field]: e.target.value,
                      }))
                    }
                    onKeyDown={handleKeyDown}
                    placeholder={field}
                    className={textareaClass + " font-mono"}
                  />
                </div>
              );
            })}
          </div>
        ) : (
          <AutoTextarea
            inputRef={firstInputRef}
            value={(values["_response"] as string) ?? ""}
            onChange={(e) =>
              setValues((prev) => ({ ...prev, _response: e.target.value }))
            }
            onKeyDown={handleKeyDown}
            placeholder="Enter response..."
            className={textareaClass}
          />
        )}

        {/* Server-side error */}
        {submitError && (
          <p className="text-[10px] text-red-500 dark:text-red-400">{submitError}</p>
        )}
        </div>

        {/* Submit — fixed at bottom, never scrolls away */}
        <div className="p-3 pt-0 shrink-0">
          <button
            type="submit"
            disabled={isPending || !hasValues}
            className="w-full min-h-[44px] h-8 rounded-md bg-amber-600/90 hover:bg-amber-500/90 disabled:opacity-40 disabled:cursor-not-allowed text-xs font-medium text-white flex items-center justify-center gap-1.5 transition-colors"
          >
            {isPending ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                Submitting...
              </>
            ) : (
              <>
                <Send className="w-3 h-3" />
                Submit
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  );
}

export function getWatchProps(watch: WatchSpec | null | undefined) {
  if (!watch || watch.mode !== "external") return null;
  return {
    prompt: (watch.config?.prompt as string) ?? "Provide the required input",
    outputs: watch.fulfillment_outputs ?? [],
    outputSchema: watch.output_schema,
  };
}
