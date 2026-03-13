import { useState, useRef, useEffect } from "react";
import { Hand, Send, Loader2 } from "lucide-react";
import type { WatchSpec } from "@/lib/types";

interface HumanInputPanelProps {
  prompt: string;
  outputs: string[];
  onSubmit: (payload: Record<string, unknown>) => void;
  isPending: boolean;
}

export function HumanInputPanel({
  prompt,
  outputs,
  onSubmit,
  isPending,
}: HumanInputPanelProps) {
  const [values, setValues] = useState<Record<string, string>>({});
  const firstInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    firstInputRef.current?.focus();
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const payload: Record<string, unknown> = {};
    for (const key of outputs) {
      const val = values[key] ?? "";
      try {
        payload[key] = JSON.parse(val);
      } catch {
        payload[key] = val;
      }
    }
    onSubmit(payload);
  };

  const hasValues = outputs.length === 0 || outputs.some((k) => (values[k] ?? "").trim());

  return (
    <div
      className="rounded-lg border border-amber-500/30 bg-zinc-900/95 backdrop-blur-sm shadow-xl shadow-amber-500/5"
      style={{ width: 320 }}
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      {/* Connector arrow */}
      <div className="flex justify-center -mt-2">
        <div className="w-3 h-3 rotate-45 bg-zinc-900 border-l border-t border-amber-500/30" />
      </div>

      <form onSubmit={handleSubmit} className="p-3 pt-1 space-y-2.5">
        {/* Prompt */}
        <div className="flex items-start gap-2">
          <Hand className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />
          <p className="text-xs text-amber-200/80 leading-relaxed">{prompt}</p>
        </div>

        {/* Fields */}
        {outputs.length > 0 ? (
          <div className="space-y-2">
            {outputs.map((field, i) => (
              <div key={field}>
                <label className="block text-[10px] font-medium text-zinc-500 uppercase tracking-wide mb-1">
                  {field}
                </label>
                <input
                  ref={i === 0 ? firstInputRef : undefined}
                  type="text"
                  value={values[field] ?? ""}
                  onChange={(e) =>
                    setValues((prev) => ({ ...prev, [field]: e.target.value }))
                  }
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && hasValues) {
                      e.preventDefault();
                      handleSubmit(e);
                    }
                  }}
                  placeholder={field}
                  className="w-full h-8 rounded-md border border-zinc-700 bg-zinc-800/80 px-2.5 text-sm text-foreground placeholder:text-zinc-600 focus:outline-none focus:border-amber-500/50 focus:ring-1 focus:ring-amber-500/20 transition-colors font-mono"
                />
              </div>
            ))}
          </div>
        ) : (
          <div className="space-y-2">
            <label className="block text-[10px] font-medium text-zinc-500 uppercase tracking-wide mb-1">
              Response
            </label>
            <input
              ref={firstInputRef}
              type="text"
              value={values["_response"] ?? ""}
              onChange={(e) =>
                setValues((prev) => ({ ...prev, _response: e.target.value }))
              }
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleSubmit(e);
                }
              }}
              placeholder="Enter response..."
              className="w-full h-8 rounded-md border border-zinc-700 bg-zinc-800/80 px-2.5 text-sm text-foreground placeholder:text-zinc-600 focus:outline-none focus:border-amber-500/50 focus:ring-1 focus:ring-amber-500/20 transition-colors"
            />
          </div>
        )}

        {/* Submit */}
        <button
          type="submit"
          disabled={isPending || !hasValues}
          className="w-full h-8 rounded-md bg-amber-600/90 hover:bg-amber-500/90 disabled:opacity-40 disabled:cursor-not-allowed text-xs font-medium text-white flex items-center justify-center gap-1.5 transition-colors"
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
      </form>
    </div>
  );
}

export function getWatchProps(watch: WatchSpec | null | undefined) {
  if (!watch || watch.mode !== "human") return null;
  return {
    prompt: (watch.config?.prompt as string) ?? "Provide the required input",
    outputs: watch.fulfillment_outputs ?? [],
  };
}
