import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { useStepwiseMutations, useSimilarErrors } from "@/hooks/useStepwise";
import { getGuidance } from "@/lib/error-guidance";
import type { StepRun, Job, StepDefinition } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  RefreshCw,
  Play,
  ChevronDown,
  ChevronRight,
  Lightbulb,
  History,
  MessageSquarePlus,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface ErrorRecoverySuggestionsProps {
  run: StepRun;
  job: Job;
  stepDef: StepDefinition;
}

function timeAgo(dateStr: string | null): string {
  if (!dateStr) return "";
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function ErrorRecoverySuggestions({
  run,
  job,
  stepDef,
}: ErrorRecoverySuggestionsProps) {
  const mutations = useStepwiseMutations();
  const guidance = getGuidance(run.error_category);
  const [fixesOpen, setFixesOpen] = useState(true);
  const [pastOpen, setPastOpen] = useState(false);

  const { data: similarErrors = [] } = useSimilarErrors(
    run.error_category,
    run.id,
    undefined,
  );

  const canRetry = run.status === "failed";
  const canResume = job.status === "failed" || job.status === "paused";
  const isAgentOrLlm = stepDef.executor.type === "agent" || stepDef.executor.type === "llm";

  return (
    <div className="space-y-3 mt-3">
      {/* Action buttons */}
      <div className="flex flex-wrap gap-2">
        {canRetry && (
          <Button
            variant="outline"
            size="sm"
            className={cn(
              "text-xs",
              guidance.retryable
                ? "border-amber-500/30 text-amber-400 hover:bg-amber-500/10"
                : "border-zinc-700 text-zinc-400 hover:bg-zinc-800",
            )}
            disabled={mutations.rerunStep.isPending}
            onClick={() =>
              mutations.rerunStep.mutate({
                jobId: job.id,
                stepName: run.step_name,
              })
            }
          >
            <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
            Retry Step
          </Button>
        )}
        {canResume && (
          <Button
            variant="outline"
            size="sm"
            className="text-xs border-blue-500/30 text-blue-400 hover:bg-blue-500/10"
            disabled={mutations.resumeJob.isPending}
            onClick={() => mutations.resumeJob.mutate(job.id)}
          >
            <Play className="w-3.5 h-3.5 mr-1.5" />
            Resume Job
          </Button>
        )}
        {canRetry && isAgentOrLlm && (
          <Button
            variant="outline"
            size="sm"
            className="text-xs border-purple-500/30 text-purple-400 hover:bg-purple-500/10"
            disabled={mutations.injectContext.isPending}
            onClick={() => {
              const context = prompt("Enter context to inject before retrying:");
              if (context) {
                mutations.injectContext.mutate(
                  { jobId: job.id, context },
                  {
                    onSuccess: () =>
                      mutations.rerunStep.mutate({
                        jobId: job.id,
                        stepName: run.step_name,
                      }),
                  },
                );
              }
            }}
          >
            <MessageSquarePlus className="w-3.5 h-3.5 mr-1.5" />
            Inject Context & Retry
          </Button>
        )}
      </div>

      {/* Suggested Fixes */}
      <div className="bg-amber-500/5 border border-amber-500/15 rounded">
        <button
          onClick={() => setFixesOpen(!fixesOpen)}
          className="flex items-center gap-1.5 w-full px-2.5 py-1.5 text-xs text-amber-400 hover:text-amber-300"
        >
          {fixesOpen ? (
            <ChevronDown className="w-3 h-3 shrink-0" />
          ) : (
            <ChevronRight className="w-3 h-3 shrink-0" />
          )}
          <Lightbulb className="w-3 h-3 shrink-0" />
          <span className="font-medium">Suggested Fixes</span>
          <span className="text-amber-400/60 ml-1">— {guidance.title}</span>
        </button>
        {fixesOpen && (
          <div className="px-2.5 pb-2.5 space-y-1.5">
            <p className="text-[11px] text-zinc-400">{guidance.description}</p>
            <ul className="space-y-1">
              {guidance.suggestions.map((s, i) => (
                <li
                  key={i}
                  className="text-[11px] text-zinc-300 flex items-start gap-1.5"
                >
                  <span className="text-amber-500/60 mt-0.5 shrink-0">•</span>
                  {s}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Similar Past Failures */}
      {run.error_category && (
        <div className="bg-zinc-500/5 border border-zinc-500/15 rounded">
          <button
            onClick={() => setPastOpen(!pastOpen)}
            className="flex items-center gap-1.5 w-full px-2.5 py-1.5 text-xs text-zinc-400 hover:text-zinc-300"
          >
            {pastOpen ? (
              <ChevronDown className="w-3 h-3 shrink-0" />
            ) : (
              <ChevronRight className="w-3 h-3 shrink-0" />
            )}
            <History className="w-3 h-3 shrink-0" />
            <span className="font-medium">Similar Past Failures</span>
            {similarErrors.length > 0 && (
              <span className="text-zinc-500 ml-1">
                ({similarErrors.length} found)
              </span>
            )}
          </button>
          {pastOpen && (
            <div className="px-2.5 pb-2.5">
              {similarErrors.length === 0 ? (
                <p className="text-[11px] text-zinc-500">
                  No similar failures found
                </p>
              ) : (
                <div className="space-y-1">
                  {similarErrors.map((f) => (
                    <Link
                      key={f.run_id}
                      to="/jobs/$jobId"
                      params={{ jobId: f.job_id }}
                      className="flex items-center gap-2 text-[11px] px-2 py-1 rounded hover:bg-zinc-800/50 group"
                    >
                      <span className="text-zinc-300 group-hover:text-blue-400 truncate max-w-[120px]">
                        {f.job_name || f.job_id.slice(0, 8)}
                      </span>
                      <span className="text-zinc-600">/</span>
                      <span className="text-zinc-400 font-mono truncate max-w-[100px]">
                        {f.step_name}
                      </span>
                      <span className="text-zinc-600 ml-auto shrink-0">
                        {timeAgo(f.completed_at)}
                      </span>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
