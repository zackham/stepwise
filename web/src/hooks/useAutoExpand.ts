import { useState, useEffect, useCallback, useRef } from "react";
import type { Job, StepRun, JobTreeNode } from "@/lib/types";

/**
 * Manages expanded-step state for the DAG view.
 *
 * Auto-expands steps that acquire sub-jobs (runtime delegation, for-each fan-out,
 * or design-time sub-flows). Resets when jobId changes. Returns the expanded set
 * and a toggle callback.
 */
export function useAutoExpand(
  jobId: string,
  runs: StepRun[],
  job: Job | undefined,
  jobTree: JobTreeNode | null,
) {
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());
  const prevSubJobKeysRef = useRef<Set<string>>(new Set());

  // Reset when switching jobs
  useEffect(() => {
    setExpandedSteps(new Set());
    prevSubJobKeysRef.current = new Set();
  }, [jobId]);

  // Auto-expand steps that have sub-jobs (runtime or design-time)
  // Walks the full job tree recursively so sub-sub-jobs also auto-expand
  useEffect(() => {
    const stepsToExpand: string[] = [];
    const currentKeys = new Set<string>();

    function scanTree(treeNode: JobTreeNode | null) {
      if (!treeNode) return;
      const nodeRuns = treeNode.runs;
      const workflow = treeNode.job.workflow;

      // Runtime sub-jobs: runs with sub_job_id or for_each sub_job_ids
      for (const run of nodeRuns) {
        if (run.sub_job_id) {
          const key = `run:${run.id}`;
          currentKeys.add(key);
          if (!prevSubJobKeysRef.current.has(key)) {
            stepsToExpand.push(run.step_name);
          }
        }
        if (run.executor_state?.for_each === true) {
          const key = `fe:${run.id}`;
          currentKeys.add(key);
          if (!prevSubJobKeysRef.current.has(key)) {
            stepsToExpand.push(run.step_name);
          }
        }
      }

      // Design-time sub-flows: steps with sub_flow that have started running
      for (const [name, step] of Object.entries(workflow.steps)) {
        if (step.sub_flow) {
          const hasRun = nodeRuns.some((r) => r.step_name === name);
          if (hasRun) {
            const key = `def:${treeNode.job.id}:${name}`;
            currentKeys.add(key);
            if (!prevSubJobKeysRef.current.has(key)) {
              stepsToExpand.push(name);
            }
          }
        }
      }

      // Recurse into sub-jobs
      for (const child of treeNode.sub_jobs) {
        scanTree(child);
      }
    }

    scanTree(jobTree ?? null);
    // Also scan top-level runs/job for the case where jobTree hasn't loaded yet
    if (!jobTree && job) {
      for (const run of runs) {
        if (run.sub_job_id) {
          const key = `run:${run.id}`;
          currentKeys.add(key);
          if (!prevSubJobKeysRef.current.has(key)) {
            stepsToExpand.push(run.step_name);
          }
        }
      }
    }

    prevSubJobKeysRef.current = currentKeys;
    if (stepsToExpand.length > 0) {
      setExpandedSteps((prev) => {
        const next = new Set(prev);
        for (const name of stepsToExpand) next.add(name);
        return next;
      });
    }
  }, [runs, job, jobTree]);

  const toggleExpand = useCallback((stepName: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepName)) next.delete(stepName);
      else next.add(stepName);
      return next;
    });
  }, []);

  return { expandedSteps, toggleExpand };
}
