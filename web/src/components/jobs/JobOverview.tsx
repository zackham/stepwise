import { useJobOutput, useJobCost } from "@/hooks/useStepwise";
import { useConfig } from "@/hooks/useConfig";
import { JsonView } from "@/components/JsonView";
import { ContentModal } from "@/components/ui/content-modal";
import { JobStatusBadge } from "@/components/StatusBadge";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import type { Job } from "@/lib/types";
import { cn, formatDuration, formatCost } from "@/lib/utils";
import { Link } from "@tanstack/react-router";
import { Package, Terminal, Monitor, DollarSign } from "lucide-react";
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
  const { data: configData } = useConfig();
  const [outputsModalOpen, setOutputsModalOpen] = useState(false);

  const normalizedOutputs = useMemo(
    () => (outputs ? normalizeOutputValue(outputs) as Record<string, unknown> : null),
    [outputs],
  );

  const stepCount = Object.keys(job.workflow.steps).length;
  const hasInputs = job.inputs && Object.keys(job.inputs).length > 0;
  const hasOutputs = normalizedOutputs && Object.keys(normalizedOutputs).length > 0;
  const hasConfigVars = job.config && Object.keys(job.config).length > 0;
  const meta = job.workflow.metadata;

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
        {costData && (costData.cost_usd > 0 || costData.billing_mode === "subscription") && (
          <div className="flex items-center gap-2">
            <span className="text-zinc-500 w-16">Cost</span>
            <span className="font-mono text-zinc-400">
              {costData.billing_mode === "subscription"
                ? "$0 (Max)"
                : formatCost(costData.cost_usd)}
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

      {/* 3. Config vars */}
      {hasConfigVars && (
        <div className="space-y-1.5">
          <SectionHeading>Config</SectionHeading>
          <div className="bg-zinc-50/50 dark:bg-zinc-900/50 rounded border border-zinc-200 dark:border-zinc-800 p-2">
            <JsonView data={job.config} defaultExpanded={false} />
          </div>
        </div>
      )}

      {/* 4. Job inputs */}
      {hasInputs && (
        <div className="space-y-1.5">
          <SectionHeading>Inputs</SectionHeading>
          <div className="max-h-40 overflow-y-auto bg-zinc-50/50 dark:bg-zinc-900/50 rounded border border-zinc-200 dark:border-zinc-800 p-2">
            <JsonView data={job.inputs} defaultExpanded={false} />
          </div>
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
                className="max-h-40 overflow-y-auto bg-zinc-50/50 dark:bg-zinc-900/50 rounded border border-zinc-200 dark:border-zinc-800 p-2 cursor-pointer hover:border-zinc-400 dark:hover:border-zinc-600 transition-colors"
                title="Click to view all outputs"
              >
                <JsonView data={normalizedOutputs} defaultExpanded={false} />
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
