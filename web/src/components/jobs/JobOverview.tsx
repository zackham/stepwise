import { useJobOutput, useJobCost, useStepwiseMutations } from "@/hooks/useStepwise";
import { JobStatusBadge } from "@/components/StatusBadge";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { Button } from "@/components/ui/button";
import type { Job } from "@/lib/types";
import { cn, formatDuration, formatCost } from "@/lib/utils";
import { Link } from "@tanstack/react-router";
import { Terminal, Monitor, Play, Pause, RotateCcw, XCircle, RefreshCw, AlertTriangle } from "lucide-react";
import { useMemo, useState } from "react";
import { SidebarSection, JobInputsSection, JobOutputsSection } from "./RunSections";

interface JobOverviewProps {
  job: Job;
}


function parseJsonString(value: string): unknown {
  const trimmed = value.trimStart();
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      return JSON.parse(value);
    } catch {
      // Leave invalid JSON-like strings unchanged.
    }
  }
  return value;
}

function normalizeOutputValue(value: unknown): unknown {
  if (typeof value === "string") {
    const parsed = parseJsonString(value);
    return parsed === value ? value : normalizeOutputValue(parsed);
  }
  if (Array.isArray(value)) {
    return value.map((item) => normalizeOutputValue(item));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([key, item]) => [
        key,
        normalizeOutputValue(item),
      ])
    );
  }
  return value;
}

export function JobOverview({ job }: JobOverviewProps) {
  const { copy: copyId, justCopied: idCopied } = useCopyFeedback();
  const mutations = useStepwiseMutations();
  const isTerminal =
    job.status === "completed" || job.status === "failed" || job.status === "cancelled";
  const { data: outputs, isLoading: outputsLoading } = useJobOutput(job.id, isTerminal);
  const { data: costData } = useJobCost(job.id);
  const stale = job.status === "running" && job.created_by !== "server" &&
    (!job.heartbeat_at || Date.now() - new Date(job.heartbeat_at).getTime() > 60_000);

  const normalizedOutputs = useMemo(() => {
    if (!outputs) return null;
    const normalized = normalizeOutputValue(outputs);
    // Terminal outputs come as an array (one per terminal step) — merge into a single object
    if (Array.isArray(normalized)) {
      const merged: Record<string, unknown> = {};
      for (const item of normalized) {
        if (item && typeof item === "object") Object.assign(merged, item);
      }
      return merged;
    }
    return normalized as Record<string, unknown>;
  }, [outputs]);

  const stepCount = Object.keys(job.workflow.steps).length;
  const hasOutputs = normalizedOutputs && Object.keys(normalizedOutputs).length > 0;
  const meta = job.workflow.metadata;

  // Split inputs into "Job Inputs" (explicitly passed) vs "Flow Defaults" (config_var defaults not overridden)
  const configVarNames = new Set(
    (job.workflow.config_vars ?? []).map((cv) => cv.name),
  );
  const configVarDefaults: Record<string, unknown> = {};
  for (const cv of job.workflow.config_vars ?? []) {
    if (cv.default !== undefined) {
      configVarDefaults[cv.name] = cv.default;
    }
  }

  const jobInputs: Record<string, unknown> = {};
  const flowDefaults: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(job.inputs ?? {})) {
    if (!configVarNames.has(key)) {
      // Not a config_var — it's a direct job input
      jobInputs[key] = value;
    } else if (
      key in configVarDefaults &&
      JSON.stringify(value) === JSON.stringify(configVarDefaults[key])
    ) {
      // Value matches config_var default — show as flow default
      flowDefaults[key] = value;
    } else {
      // Config var but overridden — show as job input
      jobInputs[key] = value;
    }
  }

  const hasJobInputs = Object.keys(jobInputs).length > 0;
  const hasFlowDefaults = Object.keys(flowDefaults).length > 0;

  return (
    <div className="p-3 space-y-4 animate-step-fade">
      {/* 1. Header */}
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-foreground truncate">
            {job.name || job.objective || "Untitled Job"}
          </h3>
          <JobStatusBadge status={job.status} />
        </div>
        {job.name && job.objective && job.name !== job.objective && (
          <p className="text-xs text-muted-foreground">{job.objective}</p>
        )}
      </div>

      {/* 2. Info grid */}
      <div className="text-xs space-y-1.5">
        {meta?.name && (
          <div className="flex items-center gap-2">
            <span className="text-zinc-500 w-16">Flow</span>
            <Link
              to="/flows/$flowName"
              params={{ flowName: meta.name }}
              className="font-mono text-blue-400 hover:text-blue-300 transition-colors"
            >
              {meta.name}
            </Link>
          </div>
        )}
        <div className="flex items-center gap-2">
          <span className="text-zinc-500 w-16">Job ID</span>
          <span
            onClick={() => copyId(job.id)}
            className={cn(
              "font-mono cursor-pointer hover:text-blue-400 transition-colors",
              idCopied ? "text-green-400" : "text-zinc-600"
            )}
            title="Click to copy"
          >
            {job.id}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-zinc-500 w-16">Steps</span>
          <span className="font-mono text-zinc-700 dark:text-zinc-300">{stepCount}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-zinc-500 w-16">Created</span>
          <span className="font-mono text-zinc-500">
            {new Date(job.created_at).toLocaleString()}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-zinc-500 w-16">Duration</span>
          <span className="font-mono text-zinc-400">
            {job.status === "staged" || job.status === "pending"
              ? "-"
              : formatDuration(job.created_at, job.updated_at)}
          </span>
        </div>
        {isTerminal && (
          <div className="flex items-center gap-2">
            <span className="text-zinc-500 w-16">Finished</span>
            <span className="font-mono text-zinc-500">
              {new Date(job.updated_at).toLocaleString()}
            </span>
          </div>
        )}
        {costData && costData.cost_usd > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-zinc-500 w-16">Cost</span>
            <span className="font-mono text-zinc-400">
              {formatCost(costData.cost_usd)}
            </span>
          </div>
        )}
        <div className="flex items-center gap-2">
          <span className="text-zinc-500 w-16">Source</span>
          <span className="flex items-center gap-1 text-zinc-400 font-mono">
            {job.created_by.startsWith("cli:") ? (
              <>
                <Terminal className="w-3 h-3" />
                CLI (PID {job.runner_pid ?? job.created_by.slice(4)})
              </>
            ) : (
              <>
                <Monitor className="w-3 h-3" />
                Server
              </>
            )}
          </span>
        </div>
        {job.heartbeat_at && (
          <div className="flex items-center gap-2">
            <span className="text-zinc-500 w-16">Heartbeat</span>
            <span className="font-mono text-zinc-500">
              {new Date(job.heartbeat_at).toLocaleString()}
            </span>
          </div>
        )}
      </div>

      {/* 3. Actions */}
      {(() => {
        const s = job.status;
        const buttons: React.ReactNode[] = [];

        if (s === "staged" || s === "pending") {
          buttons.push(
            <Button key="start" variant="outline" size="sm" className="border-blue-500/30 text-blue-400 hover:bg-blue-500/10" onClick={() => mutations.startJob.mutate(job.id)}>
              <Play className="w-3.5 h-3.5 mr-1.5" />Start
            </Button>
          );
        }
        if (s === "running") {
          buttons.push(
            <Button key="pause" variant="outline" size="sm" onClick={() => mutations.pauseJob.mutate(job.id)} disabled={mutations.pauseJob.isPending}>
              <Pause className="w-3.5 h-3.5 mr-1.5" />Pause
            </Button>
          );
          buttons.push(
            <Button key="cancel" variant="outline" size="sm" className="border-red-500/30 text-red-400 hover:bg-red-500/10" onClick={() => mutations.cancelJob.mutate(job.id)} disabled={mutations.cancelJob.isPending}>
              <XCircle className="w-3.5 h-3.5 mr-1.5" />Cancel
            </Button>
          );
        }
        if (s === "paused") {
          buttons.push(
            <Button key="resume" variant="outline" size="sm" className="border-blue-500/30 text-blue-400 hover:bg-blue-500/10" onClick={() => mutations.resumeJob.mutate(job.id)} disabled={mutations.resumeJob.isPending}>
              <Play className="w-3.5 h-3.5 mr-1.5" />Resume
            </Button>
          );
          buttons.push(
            <Button key="cancel" variant="outline" size="sm" className="border-red-500/30 text-red-400 hover:bg-red-500/10" onClick={() => mutations.cancelJob.mutate(job.id)} disabled={mutations.cancelJob.isPending}>
              <XCircle className="w-3.5 h-3.5 mr-1.5" />Cancel
            </Button>
          );
        }
        if (s === "failed") {
          buttons.push(
            <Button key="retry" variant="outline" size="sm" className="border-amber-500/30 text-amber-400 hover:bg-amber-500/10" onClick={() => mutations.resumeJob.mutate(job.id)} disabled={mutations.resumeJob.isPending}>
              <RefreshCw className="w-3.5 h-3.5 mr-1.5" />Retry
            </Button>
          );
        }
        if (s === "completed" || s === "failed" || s === "cancelled") {
          buttons.push(
            <Button key="reset" variant="outline" size="sm" onClick={() => mutations.resetJob.mutate(job.id)} disabled={mutations.resetJob.isPending}>
              <RotateCcw className="w-3.5 h-3.5 mr-1.5" />Reset
            </Button>
          );
        }

        if (buttons.length === 0 && !stale) return null;
        return (
          <div className="space-y-2">
            {stale && (
              <div className="flex items-center gap-1.5 text-amber-500 text-xs">
                <AlertTriangle className="w-3.5 h-3.5" />
                Owner not responding
              </div>
            )}
            {buttons.length > 0 && (
              <div className="flex flex-wrap gap-2">{buttons}</div>
            )}
          </div>
        );
      })()}

      {/* 4. Job Inputs */}
      {hasJobInputs && <JobInputsSection inputs={jobInputs} />}

      {/* 4. Flow Defaults */}
      {hasFlowDefaults && <JobInputsSection inputs={flowDefaults} />}

      {/* 5. Job outputs */}
      {isTerminal && (
        outputsLoading ? (
          <div className="text-zinc-500 text-xs py-2">Loading outputs...</div>
        ) : hasOutputs ? (
          <JobOutputsSection outputs={normalizedOutputs!} />
        ) : (
          <div className="text-zinc-600 text-xs py-2">No outputs</div>
        )
      )}

      {/* 6. Flow metadata */}
      {meta && (meta.author || meta.description) && (
        <SidebarSection title="Flow Metadata">
          <div className="text-xs space-y-1">
            {meta.author && (
              <div>
                <span className="text-zinc-500">Author: </span>
                <span className="text-zinc-400">{meta.author}</span>
              </div>
            )}
            {meta.description && (
              <div>
                <span className="text-zinc-500">Description: </span>
                <span className="text-zinc-400">{meta.description}</span>
              </div>
            )}
          </div>
        </SidebarSection>
      )}

    </div>
  );
}
