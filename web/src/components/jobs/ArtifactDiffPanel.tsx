import { useState, useMemo } from "react";
import type { StepRun } from "@/lib/types";
import { DiffView } from "@/components/DiffView";

interface ArtifactDiffPanelProps {
  runs: StepRun[];
  currentRun: StepRun;
  outputs?: string[];
}

export function ArtifactDiffPanel({
  runs,
  currentRun,
  outputs,
}: ArtifactDiffPanelProps) {
  // Runs with results, sorted ascending by attempt for easy lookup
  const runsWithResults = useMemo(
    () =>
      [...runs]
        .filter((r) => r.result?.artifact != null)
        .sort((a, b) => a.attempt - b.attempt),
    [runs]
  );

  // Default: compare current vs previous attempt
  const defaultBefore = useMemo(() => {
    const prevRuns = runsWithResults.filter(
      (r) => r.attempt < currentRun.attempt
    );
    return prevRuns.length > 0 ? prevRuns[prevRuns.length - 1] : null;
  }, [runsWithResults, currentRun.attempt]);

  const [leftAttempt, setLeftAttempt] = useState<number | null>(null);
  const [rightAttempt, setRightAttempt] = useState<number | null>(null);
  const [selectedField, setSelectedField] = useState<string | null>(null);

  const effectiveLeft =
    leftAttempt != null
      ? runsWithResults.find((r) => r.attempt === leftAttempt) ?? null
      : defaultBefore;
  const effectiveRight =
    rightAttempt != null
      ? runsWithResults.find((r) => r.attempt === rightAttempt) ?? null
      : currentRun;

  const leftArtifact = effectiveLeft?.result?.artifact ?? null;
  const rightArtifact = effectiveRight?.result?.artifact ?? null;

  // Extract single field if selected
  const before =
    selectedField && leftArtifact
      ? leftArtifact[selectedField] ?? null
      : leftArtifact;
  const after =
    selectedField && rightArtifact
      ? rightArtifact[selectedField] ?? null
      : rightArtifact;

  const showSelectors = runsWithResults.length >= 3;

  return (
    <div className="space-y-2">
      {/* Attempt selectors */}
      {showSelectors ? (
        <div className="flex items-center gap-2 text-xs text-zinc-500">
          <span>Compare</span>
          <select
            value={effectiveLeft?.attempt ?? ""}
            onChange={(e) => setLeftAttempt(Number(e.target.value))}
            className="bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 rounded px-1.5 py-0.5 text-zinc-700 dark:text-zinc-300 text-xs"
          >
            {runsWithResults.map((r) => (
              <option key={r.id} value={r.attempt}>
                #{r.attempt}
              </option>
            ))}
          </select>
          <span>vs</span>
          <select
            value={effectiveRight?.attempt ?? ""}
            onChange={(e) => setRightAttempt(Number(e.target.value))}
            className="bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 rounded px-1.5 py-0.5 text-zinc-700 dark:text-zinc-300 text-xs"
          >
            {runsWithResults.map((r) => (
              <option key={r.id} value={r.attempt}>
                #{r.attempt}
              </option>
            ))}
          </select>
        </div>
      ) : (
        <div className="text-xs text-zinc-500">
          Attempt #{effectiveLeft?.attempt ?? "?"} vs #{effectiveRight?.attempt ?? "?"}
        </div>
      )}

      {/* Field tabs */}
      {outputs && outputs.length > 1 && (
        <div className="flex items-center gap-1 flex-wrap">
          <button
            onClick={() => setSelectedField(null)}
            className={`text-[10px] px-2 py-0.5 rounded transition-colors ${
              selectedField === null
                ? "bg-zinc-200 dark:bg-zinc-700 text-zinc-800 dark:text-zinc-200"
                : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 bg-zinc-100 dark:bg-zinc-800"
            }`}
          >
            All fields
          </button>
          {outputs.map((field) => (
            <button
              key={field}
              onClick={() => setSelectedField(field)}
              className={`text-[10px] px-2 py-0.5 rounded font-mono transition-colors ${
                selectedField === field
                  ? "bg-zinc-700 text-zinc-200"
                  : "text-zinc-500 hover:text-zinc-300 bg-zinc-800"
              }`}
            >
              {field}
            </button>
          ))}
        </div>
      )}

      <DiffView before={before} after={after} />
    </div>
  );
}
