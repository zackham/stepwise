import { useState } from "react";
import { useRecentFlows, useStepwiseMutations } from "@/hooks/useStepwise";
import { Zap, Pencil, ChevronDown, ChevronUp, Loader2 } from "lucide-react";
import type { CreateJobPrefill } from "./CreateJobDialog";
import type { QuickLaunchItem } from "@/lib/types";

function timeAgo(dateStr: string): string {
  const seconds = Math.floor(
    (Date.now() - new Date(dateStr).getTime()) / 1000
  );
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function inputSummary(inputs: Record<string, unknown>): string {
  const entries = Object.entries(inputs);
  if (entries.length === 0) return "no inputs";
  return entries
    .slice(0, 2)
    .map(([k, v]) => {
      const val = String(v ?? "");
      return `${k}=${val.length > 30 ? val.slice(0, 30) + "..." : val}`;
    })
    .join(", ");
}

interface QuickLaunchProps {
  onLaunched: (jobId: string) => void;
  onEditLaunch: (prefill: CreateJobPrefill) => void;
}

export function QuickLaunch({ onLaunched, onEditLaunch }: QuickLaunchProps) {
  const { data: recentFlows = [] } = useRecentFlows();
  const mutations = useStepwiseMutations();
  const [expanded, setExpanded] = useState(false);
  const [launchingId, setLaunchingId] = useState<string | null>(null);

  if (recentFlows.length === 0) return null;

  const visibleFlows = expanded ? recentFlows : recentFlows.slice(0, 3);

  const handleLaunch = (item: QuickLaunchItem) => {
    setLaunchingId(item.last_job_id);
    const flowName = item.flow_name;
    const firstInput = Object.values(item.last_inputs)[0];
    const objective = (firstInput as string) || flowName;

    mutations.createJob.mutate(
      {
        objective,
        workflow: item.workflow,
        inputs: item.last_inputs,
        name: item.last_job_name ?? undefined,
      },
      {
        onSuccess: (job) => {
          setLaunchingId(null);
          onLaunched(job.id);
        },
        onError: () => {
          setLaunchingId(null);
        },
      }
    );
  };

  const handleEdit = (item: QuickLaunchItem) => {
    const mapped: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(item.last_inputs)) {
      mapped[k] = v;
    }
    onEditLaunch({
      workflow: item.workflow,
      inputs: mapped,
      name: item.last_job_name ?? undefined,
    });
  };

  return (
    <div className="border-b border-border">
      <div className="flex items-center gap-1.5 px-3 pt-2 pb-1">
        <Zap className="w-3 h-3 text-yellow-500" />
        <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">Quick Launch</span>
      </div>
      <div className="px-2 pb-2 space-y-1">
        {visibleFlows.map((item) => {
          const isLaunching = launchingId === item.last_job_id;
          return (
            <div
              key={item.last_job_id}
              className="group flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50 transition-colors"
            >
              <button
                onClick={() => handleLaunch(item)}
                disabled={isLaunching}
                className="flex-1 text-left min-w-0 cursor-pointer disabled:cursor-wait"
              >
                <div className="flex items-center gap-1.5">
                  {isLaunching ? (
                    <Loader2 className="w-3 h-3 animate-spin text-zinc-500 dark:text-zinc-400 shrink-0" />
                  ) : null}
                  <span className="text-xs font-medium text-foreground truncate">
                    {item.flow_name}
                  </span>
                  <span className="text-[10px] text-zinc-500 shrink-0">
                    {timeAgo(item.last_run_at)}
                  </span>
                </div>
                <p className="text-[10px] text-zinc-500 truncate mt-0.5">
                  {inputSummary(item.last_inputs)}
                </p>
              </button>
              <button
                onClick={() => handleEdit(item)}
                className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-zinc-200 dark:hover:bg-zinc-700 transition-opacity cursor-pointer shrink-0 mt-0.5"
                title="Edit & Run"
              >
                <Pencil className="w-3 h-3 text-zinc-500 dark:text-zinc-400" />
              </button>
            </div>
          );
        })}
      </div>
      {recentFlows.length > 3 && (
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full flex items-center justify-center gap-1 py-1 text-[10px] text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 transition-colors border-t border-border cursor-pointer"
        >
          {expanded ? (
            <>
              <ChevronUp className="w-3 h-3" />
              Show less
            </>
          ) : (
            <>
              <ChevronDown className="w-3 h-3" />
              {recentFlows.length - 3} more
            </>
          )}
        </button>
      )}
    </div>
  );
}
