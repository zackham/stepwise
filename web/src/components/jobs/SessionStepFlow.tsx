import { ChevronsRight } from "lucide-react";
import type { SessionStepEntry } from "@/hooks/useStepwise";

interface SessionStepFlowProps {
  entries: SessionStepEntry[];
  currentStep?: string;
  onSelectStep?: (stepName: string) => void;
}

function formatTok(tokens: number): string {
  if (tokens >= 1000) return `${(tokens / 1000).toFixed(1)}k`;
  return String(tokens);
}

/** Renders an ordered step flow with arrows: plan (23.1k) ›› implement (3, 45.2k) ›› validate (12.0k) */
export function SessionStepFlow({ entries, currentStep, onSelectStep }: SessionStepFlowProps) {
  if (entries.length <= 1) return null;

  return (
    <div className="flex items-center gap-1 flex-wrap text-[10px]">
      {entries.map((entry, i) => {
        const isCurrent = entry.name === currentStep;
        const detail = [
          entry.runs > 1 ? String(entry.runs) : null,
          entry.tokens > 0 ? formatTok(entry.tokens) : null,
        ].filter(Boolean).join(", ");

        return (
          <span key={entry.name} className="flex items-center gap-1">
            {i > 0 && (
              <ChevronsRight className="w-3 h-3 text-zinc-500/50 shrink-0" />
            )}
            {isCurrent ? (
              <span className="font-semibold text-zinc-200">
                {entry.name}{detail ? ` (${detail})` : ""}
              </span>
            ) : (
              <button
                onClick={() => onSelectStep?.(entry.name)}
                className="text-blue-600 dark:text-blue-400 hover:text-blue-500 dark:hover:text-blue-300 transition-colors cursor-pointer"
              >
                {entry.name}{detail ? ` (${detail})` : ""}
              </button>
            )}
          </span>
        );
      })}
    </div>
  );
}
