import { CirclePause } from "lucide-react";
import type { PauseCause } from "@/lib/types";

/**
 * Banner shown when `job.status === "paused"` and the server attached a
 * `pause_cause` derived from the latest `job.paused` event.
 *
 * Purpose: the UI graph shows individual step tiles (completed / running /
 * pending) but gives no indication that the JOB is frozen. If a gate
 * step's exit rule fired with action=escalate and a sibling run was mid-
 * flight at the time, the sibling tile keeps reading "RUNNING" forever
 * and users can't tell whether the job is progressing. This banner makes
 * the pause + cause visible and links to the triggering step so the
 * causal chain is one glance, not an event-log dig.
 *
 * Also surfaces the count of STRANDED runs (see server._enrich_run_dict
 * + StepNode.tsx) to explain why something looks "still running" after
 * the pause.
 */
export function PausedJobBanner({
  cause,
  strandedCount,
  onViewStep,
}: {
  cause: PauseCause;
  strandedCount: number;
  onViewStep?: (stepName: string) => void;
}) {
  const reason = cause.reason || "paused";
  const step = cause.step;
  const rule = cause.rule;
  const target = cause.target;
  const at = cause.at
    ? new Date(cause.at).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : null;

  const renderStepLink = (name: string) => (
    <button
      type="button"
      onClick={onViewStep ? () => onViewStep(name) : undefined}
      className="underline underline-offset-2 hover:text-amber-900 dark:hover:text-amber-100 font-mono cursor-pointer"
      title="Jump to the step that caused the pause"
    >
      {name}
    </button>
  );

  return (
    <div
      data-testid="paused-job-banner"
      className="flex items-center gap-2 px-4 py-2 border-b border-amber-300/50 dark:border-amber-900/50 bg-amber-100/40 dark:bg-amber-950/30 text-xs"
    >
      <CirclePause className="w-3.5 h-3.5 text-amber-600 dark:text-amber-400 shrink-0" />
      <div className="flex-1 min-w-0 truncate">
        <span className="text-amber-800 dark:text-amber-200 font-medium uppercase tracking-wide mr-2">
          paused
        </span>
        <span className="text-amber-700 dark:text-amber-300">
          {reason === "escalated" && step && rule && (
            <>
              escalated by {renderStepLink(step)} (rule:{" "}
              <span className="font-mono">{rule}</span>)
            </>
          )}
          {reason === "max_iterations_reached" && step && (
            <>
              loop{target ? ` → ${target}` : ""} hit max iterations at{" "}
              {renderStepLink(step)}
            </>
          )}
          {!["escalated", "max_iterations_reached"].includes(reason) && (
            <>reason: {reason}{step ? ` (${step})` : ""}</>
          )}
          {at && (
            <span className="text-amber-500/70 dark:text-amber-400/60 ml-2">
              @ {at}
            </span>
          )}
        </span>
      </div>
      {strandedCount > 0 && (
        <span
          className="text-amber-700 dark:text-amber-400 font-mono whitespace-nowrap shrink-0"
          title={`${strandedCount} run${strandedCount === 1 ? " is" : "s are"} still marked RUNNING but cannot advance until the job is resumed`}
        >
          {strandedCount} stranded
        </span>
      )}
    </div>
  );
}
