import { useState, useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ArrowLeft } from "lucide-react";
import { executorIcon } from "@/lib/executor-utils";

interface StepPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  existingStepNames: string[];
  onAdd: (name: string, executor: string) => void;
  isPending: boolean;
}

const STEP_TYPES = [
  { type: "script", label: "Script", description: "Run a shell command" },
  { type: "llm", label: "LLM", description: "Call a language model" },
  { type: "agent", label: "Agent", description: "Launch an AI agent" },
  { type: "external", label: "Human", description: "Wait for human input" },
  { type: "poll", label: "Poll", description: "Poll until a condition is met" },
] as const;

const TYPE_COLORS: Record<string, string> = {
  script: "border-emerald-300 dark:border-emerald-800 hover:bg-emerald-100/30 dark:hover:bg-emerald-900/30",
  llm: "border-violet-300 dark:border-violet-800 hover:bg-violet-100/30 dark:hover:bg-violet-900/30",
  agent: "border-blue-300 dark:border-blue-800 hover:bg-blue-100/30 dark:hover:bg-blue-900/30",
  external: "border-amber-300 dark:border-amber-800 hover:bg-amber-100/30 dark:hover:bg-amber-900/30",
  poll: "border-cyan-300 dark:border-cyan-800 hover:bg-cyan-100/30 dark:hover:bg-cyan-900/30",
};

export function StepPalette({
  open,
  onOpenChange,
  existingStepNames,
  onAdd,
  isPending,
}: StepPaletteProps) {
  const [selectedType, setSelectedType] = useState<string | null>(null);
  const [name, setName] = useState("");

  // Reset state when dialog closes
  useEffect(() => {
    if (!open) {
      setSelectedType(null);
      setName("");
    }
  }, [open]);

  const isDuplicate = existingStepNames.includes(name.trim());
  const canSubmit = name.trim() && !isDuplicate && !isPending;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit || !selectedType) return;
    onAdd(name.trim(), selectedType);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[420px]">
        <DialogHeader>
          <DialogTitle>
            {selectedType ? (
              <div className="flex items-center gap-2">
                <button
                  onClick={() => { setSelectedType(null); setName(""); }}
                  className="p-1 rounded hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
                  title="Back"
                >
                  <ArrowLeft className="w-4 h-4" />
                </button>
                <span className="flex items-center gap-2">
                  {executorIcon(selectedType, "w-4 h-4")}
                  {STEP_TYPES.find((t) => t.type === selectedType)?.label}
                </span>
              </div>
            ) : (
              "Add Step"
            )}
          </DialogTitle>
        </DialogHeader>

        {!selectedType ? (
          <div className="grid grid-cols-2 gap-3">
            {STEP_TYPES.map((st) => (
              <button
                key={st.type}
                onClick={() => setSelectedType(st.type)}
                className={`flex items-center gap-3 p-3 rounded-lg border bg-transparent text-left transition-colors ${
                  TYPE_COLORS[st.type] ?? "border-zinc-300 dark:border-zinc-700 hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50"
                } ${st.type === "poll" ? "col-span-2 max-w-[calc(50%-6px)]" : ""}`}
              >
                <span className="shrink-0">{executorIcon(st.type, "w-5 h-5")}</span>
                <div>
                  <div className="text-sm font-medium">{st.label}</div>
                  <div className="text-xs text-zinc-500">{st.description}</div>
                </div>
              </button>
            ))}
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Step name"
                autoFocus
              />
              {isDuplicate && (
                <p className="text-xs text-red-500 dark:text-red-400">
                  A step named &ldquo;{name.trim()}&rdquo; already exists
                </p>
              )}
            </div>
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={!canSubmit}>
                {isPending ? "Adding..." : "Add Step"}
              </Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
