import { useState, useEffect, useCallback, useRef } from "react";
import { fetchLiveSource } from "@/lib/api";
import { subscribeFlowSourceChanged } from "./useStepwiseWebSocket";
import type { StepDefinition } from "@/lib/types";

interface LiveSourceState {
  /** Current on-disk step definitions, keyed by step name */
  liveSteps: Record<string, StepDefinition> | null;
  /** Whether the source has been updated since the job was created */
  hasUpdate: boolean;
  /** Timestamp of the last detected update */
  updatedAt: number | null;
}

/**
 * Subscribe to live source updates for a job's flow file.
 * Returns the current on-disk step definitions when the file changes.
 */
export function useLiveSource(
  jobId: string | undefined,
  hasSourcePath: boolean,
): LiveSourceState {
  const [liveSteps, setLiveSteps] = useState<Record<string, StepDefinition> | null>(null);
  const [hasUpdate, setHasUpdate] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const fetchingRef = useRef(false);

  const refresh = useCallback(async () => {
    if (!jobId || !hasSourcePath || fetchingRef.current) return;
    fetchingRef.current = true;
    try {
      const data = await fetchLiveSource(jobId);
      setLiveSteps(data.steps as Record<string, StepDefinition>);
      setHasUpdate(true);
      setUpdatedAt(Date.now());
    } catch {
      // File may not exist or parse error — ignore
    } finally {
      fetchingRef.current = false;
    }
  }, [jobId, hasSourcePath]);

  useEffect(() => {
    if (!jobId || !hasSourcePath) return;

    const unsub = subscribeFlowSourceChanged((msg) => {
      if (msg.job_ids.includes(jobId)) {
        refresh();
      }
    });

    return unsub;
  }, [jobId, hasSourcePath, refresh]);

  return { liveSteps, hasUpdate, updatedAt };
}
