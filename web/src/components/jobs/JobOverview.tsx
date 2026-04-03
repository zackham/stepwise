import { useJobOutput, useJobCost } from "@/hooks/useStepwise";
import { ContentModal } from "@/components/ui/content-modal";
import { JobStatusBadge } from "@/components/StatusBadge";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import type { Job } from "@/lib/types";
import { cn, formatDuration, formatCost } from "@/lib/utils";
import { Link } from "@tanstack/react-router";
import { Package, Terminal, Monitor } from "lucide-react";
import { useMemo, useState } from "react";

interface JobOverviewProps {
  job: Job;
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-medium text-zinc-500 uppercase tracking-wider">
      {children}
    </div>
  );
}

function truncateValue(value: unknown, maxLen = 80): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "string") return value.length > maxLen ? value.slice(0, maxLen) + "..." : value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  const json = JSON.stringify(value);
  return json.length > maxLen ? json.slice(0, maxLen) + "..." : json;
}

function KeyValueList({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="space-y-1">
      {Object.entries(data).map(([key, value]) => (
        <div key={key} className="flex gap-2 text-xs leading-snug">
          <span className="text-zinc-500 shrink-0 font-mono">{key}:</span>
          <span className="text-zinc-400 font-mono truncate">
            {typeof value === "string" ? `"${truncateValue(value)}"` : truncateValue(value)}
          </span>
        </div>
      ))}
    </div>
  );
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
  const isTerminal =
    job.status === "completed" || job.status === "failed" || job.status === "cancelled";
  const { data: outputs, isLoading: outputsLoading } = useJobOutput(job.id, isTerminal);
  const { data: costData } = useJobCost(job.id);
  const [outputsModalOpen, setOutputsModalOpen] = useState(false);

  const normalizedOutputs = useMemo(
    () => (outputs ? normalizeOutputValue(outputs) as Record<string, unknown> : null),
    [outputs],
  );

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
        {job.name && job.objective && (
          <p className="text-xs text-muted-foreground">{job.objective}</p>
        )}
      </div>

      {/* 2. Info grid */}
      <div className="text-xs space-y-1.5">
        <div className="flex items-center gap-2">
          <span className="text-zinc-500 w-16">Job ID</span>
          <span
            onClick={() => copyId(job.id)}
            className={cn(
              "font-mono text-[10px] cursor-pointer hover:text-blue-400 transition-colors",
              idCopied ? "text-green-400" : "text-zinc-600"
            )}
            title="Click to copy"
          >
            {job.id}
          </span>
        </div>
        {meta?.name && (
          <div className="flex items-center gap-2">
            <span className="text-zinc-500 w-16">Flow</span>
            <Link
              to="/flows/$flowName"
              params={{ flowName: meta.name }}
              className="text-blue-600 dark:text-blue-400 hover:text-blue-500 dark:hover:text-blue-300 underline underline-offset-2 text-[10px] font-mono"
            >
              {meta.name}
            </Link>
          </div>
        )}
        <div className="flex items-center gap-2">
          <span className="text-zinc-500 w-16">Steps</span>
          <span className="font-mono text-zinc-700 dark:text-zinc-300">{stepCount}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-zinc-500 w-16">Created</span>
          <span className="font-mono text-zinc-500 text-[10px]">
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
            <span className="font-mono text-zinc-500 text-[10px]">
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
          <span className="flex items-center gap-1 text-zinc-400 text-[10px] font-mono">
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
            <span className="font-mono text-zinc-500 text-[10px]">
              {new Date(job.heartbeat_at).toLocaleString()}
            </span>
          </div>
        )}
      </div>

      {/* 3. Job Inputs */}
      {hasJobInputs && (
        <div className="space-y-1.5">
          <SectionHeading>Job Inputs</SectionHeading>
          <KeyValueList data={jobInputs} />
        </div>
      )}

      {/* 4. Flow Defaults */}
      {hasFlowDefaults && (
        <div className="space-y-1.5">
          <SectionHeading>Flow Defaults</SectionHeading>
          <KeyValueList data={flowDefaults} />
        </div>
      )}

      {/* 5. Job outputs */}
      {isTerminal && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1">
            <Package className="w-3 h-3 text-zinc-500" />
            <SectionHeading>Outputs</SectionHeading>
          </div>
          {outputsLoading ? (
            <div className="text-zinc-500 text-xs py-2">Loading outputs...</div>
          ) : hasOutputs ? (
            <>
              <div
                onClick={() => setOutputsModalOpen(true)}
                className="cursor-pointer rounded px-2 py-1.5 -mx-2 hover:bg-zinc-100 dark:hover:bg-zinc-800/60 transition-colors"
                title="Click to view full outputs"
              >
                <KeyValueList data={normalizedOutputs!} />
              </div>
              <ContentModal
                open={outputsModalOpen}
                onOpenChange={setOutputsModalOpen}
                title="Job Outputs"
                copyContent={JSON.stringify(normalizedOutputs, null, 2)}
              >
                <pre className="whitespace-pre-wrap text-sm text-zinc-300 font-mono p-3 leading-relaxed">
                  {JSON.stringify(normalizedOutputs, null, 2)}
                </pre>
              </ContentModal>
            </>
          ) : (
            <div className="text-zinc-600 text-xs py-2">No outputs</div>
          )}
        </div>
      )}

      {/* 6. Flow metadata */}
      {meta && (meta.author || meta.description) && (
        <div className="space-y-1.5">
          <SectionHeading>Flow Metadata</SectionHeading>
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
        </div>
      )}
    </div>
  );
}
