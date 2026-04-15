import { useState, useEffect, useRef, useCallback, createContext, useContext } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { AgentOutputMessage, ScriptOutputMessage, TickMessage, FlowSourceChangedMessage, Job, JobStatus } from "@/lib/types";
import * as api from "@/lib/api";

export type WsStatus = "connected" | "disconnected" | "reconnecting";
export interface StepwiseWebSocketState {
  wsState: WsStatus;
}

// ── Agent output pub/sub ────────────────────────────────────────────

const agentOutputListeners = new Set<(msg: AgentOutputMessage) => void>();

export function subscribeAgentOutput(fn: (msg: AgentOutputMessage) => void) {
  agentOutputListeners.add(fn);
  return () => {
    agentOutputListeners.delete(fn);
  };
}

// ── Tick message pub/sub ────────────────────────────────────────────

const tickListeners = new Set<(msg: TickMessage) => void>();

export function subscribeTickMessages(fn: (msg: TickMessage) => void) {
  tickListeners.add(fn);
  return () => {
    tickListeners.delete(fn);
  };
}

// ── Script output pub/sub ─────────────────────────────────────────

const scriptOutputListeners = new Set<(msg: ScriptOutputMessage) => void>();

export function subscribeScriptOutput(fn: (msg: ScriptOutputMessage) => void) {
  scriptOutputListeners.add(fn);
  return () => {
    scriptOutputListeners.delete(fn);
  };
}

// ── Flow source changed pub/sub ────────────────────────────────────

const flowSourceListeners = new Set<(msg: FlowSourceChangedMessage) => void>();

export function subscribeFlowSourceChanged(fn: (msg: FlowSourceChangedMessage) => void) {
  flowSourceListeners.add(fn);
  return () => {
    flowSourceListeners.delete(fn);
  };
}

// ── WebSocket connection ────────────────────────────────────────────

// In-flight `/api/jobs/{id}?summary=true` requests, keyed by job_id.
// Multiple ticks for the same job arrive in bursts during a fast
// step transition; this dedupes them so we issue one HTTP call per
// job per burst window instead of N.
const inFlightJobFetches = new Map<string, Promise<Job | null>>();

// Safety-net broad refetch interval. The patch-in-place path covers
// updates to jobs already in the list, but new jobs created on the
// server (or status-filter membership changes) need an occasional
// list refetch to reconcile. Once every few seconds is plenty.
const BROAD_REFETCH_SAFETY_NET_MS = 5000;

/** Match the queryKey shape used by useJobs(): ["jobs", status, topLevel, includeArchived] */
type JobsQueryKey = readonly [
  "jobs",
  string | undefined,
  boolean | undefined,
  boolean | undefined,
];

type JobsQueryData = { jobs: Job[]; total: number } | Job[];

function isJobsQueryKey(key: readonly unknown[]): key is JobsQueryKey {
  return Array.isArray(key) && key[0] === "jobs";
}

function jobMatchesFilter(
  job: Job,
  statusFilter: string | undefined,
  topLevelOnly: boolean | undefined,
  includeArchived: boolean | undefined,
): boolean {
  // status filter
  if (statusFilter && (job.status as JobStatus) !== statusFilter) return false;
  // top-level filter — sub-jobs (for_each fan-out, sub_flow children)
  // have a parent_job_id and must be hidden from top-level lists.
  // Without this gate the patch-in-place path on tick prepends every
  // changed sub-job into the /jobs list cache.
  if (topLevelOnly && job.parent_job_id) return false;
  // archived filter — server hides archived from non-archived lists.
  if (!includeArchived && (job.status as JobStatus) === "archived") return false;
  return true;
}

export function useStepwiseWebSocket(): StepwiseWebSocketState {
  const queryClient = useQueryClient();
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shouldReconnectRef = useRef(true);
  const [wsState, setWsState] = useState<WsStatus>("disconnected");

  // Long-interval safety-net: even with patch-in-place, occasional
  // full refetches catch new jobs / filter membership changes that
  // pure splicing can't infer.
  const safetyNetRef = useRef<{
    lastFired: number;
    timer: ReturnType<typeof setTimeout> | null;
  }>({ lastFired: 0, timer: null });

  /** Fetch a single job in summary form; dedupe concurrent fetches for the same id. */
  const fetchJobDeduped = useCallback((jobId: string): Promise<Job | null> => {
    const existing = inFlightJobFetches.get(jobId);
    if (existing) return existing;
    const promise = api
      .fetchJob(jobId, true)
      .catch(() => null)
      .finally(() => {
        inFlightJobFetches.delete(jobId);
      });
    inFlightJobFetches.set(jobId, promise);
    return promise;
  }, []);

  /** Splice an updated job into every cached list query. */
  const patchJobIntoLists = useCallback(
    (updated: Job) => {
      const entries = queryClient.getQueriesData<JobsQueryData>({
        queryKey: ["jobs"],
      });
      for (const [queryKey, oldData] of entries) {
        if (!isJobsQueryKey(queryKey)) continue;
        if (!oldData) continue;
        const statusFilter = queryKey[1];
        const topLevelOnly = queryKey[2];
        const includeArchived = queryKey[3];
        const list: Job[] = Array.isArray(oldData) ? oldData : oldData.jobs;
        if (!Array.isArray(list)) continue;
        const idx = list.findIndex((j) => j.id === updated.id);
        const belongs = jobMatchesFilter(
          updated, statusFilter, topLevelOnly, includeArchived,
        );
        let newList: Job[] | null = null;
        if (idx >= 0 && belongs) {
          newList = list.slice();
          newList[idx] = updated;
        } else if (idx >= 0 && !belongs) {
          newList = list.filter((j) => j.id !== updated.id);
        } else if (idx < 0 && belongs) {
          // New job (or job that just transitioned into this filter).
          // Prepend — the list is sorted created_at DESC and a brand-
          // new job is by definition the newest.
          newList = [updated, ...list];
        }
        if (newList === null) continue;
        const newData: JobsQueryData = Array.isArray(oldData)
          ? newList
          : { jobs: newList, total: newList.length };
        queryClient.setQueryData(queryKey, newData);
      }
    },
    [queryClient],
  );

  /** Tick handler: fetch each changed job in summary form (deduped),
   *  splice into all cached job lists, no broad refetch. */
  const handleChangedJobs = useCallback(
    (jobIds: string[]) => {
      for (const jobId of jobIds) {
        fetchJobDeduped(jobId).then((job) => {
          if (job) patchJobIntoLists(job);
        });
      }
    },
    [fetchJobDeduped, patchJobIntoLists],
  );

  /** Long-interval safety net: throttled to max once per N seconds.
   *  Catches things patch-in-place can't infer (new jobs created
   *  outside the WS connection, count drifts, etc.). */
  const scheduleSafetyNetRefetch = useCallback(() => {
    const state = safetyNetRef.current;
    const now = Date.now();
    const elapsed = now - state.lastFired;
    if (elapsed >= BROAD_REFETCH_SAFETY_NET_MS) {
      queryClient.invalidateQueries({ queryKey: ["status"] });
      state.lastFired = now;
      return;
    }
    if (state.timer) return;
    state.timer = setTimeout(() => {
      queryClient.invalidateQueries({ queryKey: ["status"] });
      state.lastFired = Date.now();
      state.timer = null;
    }, BROAD_REFETCH_SAFETY_NET_MS - elapsed);
  }, [queryClient]);

  const connect = useCallback(() => {
    if (
      wsRef.current?.readyState === WebSocket.OPEN ||
      wsRef.current?.readyState === WebSocket.CONNECTING
    ) {
      return;
    }

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    setWsState("reconnecting");

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log("[ws] connected");
      setWsState("connected");
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === "tick" && msg.changed_jobs?.length > 0) {
          // Smart per-job updates: fetch ONLY the changed jobs (one
          // small request per job, deduped) and splice them into the
          // cached job-list queries via setQueryData. No more
          // /api/jobs?limit=500 refetch on every tick.
          handleChangedJobs(msg.changed_jobs);
          // Step-detail caches stay fresh via cheap scoped invalidates.
          for (const jobId of msg.changed_jobs) {
            queryClient.invalidateQueries({ queryKey: ["runs", jobId] });
            queryClient.invalidateQueries({ queryKey: ["events", jobId] });
            queryClient.invalidateQueries({ queryKey: ["jobTree", jobId] });
            queryClient.invalidateQueries({ queryKey: ["sessions", jobId] });
            queryClient.invalidateQueries({ queryKey: ["sessionTranscript", jobId] });
          }
          // Long-interval safety net for status counts, etc. — throttled
          // to once every few seconds, NOT once per tick.
          scheduleSafetyNetRefetch();
          for (const fn of tickListeners) fn(msg as TickMessage);
        } else if (msg.type === "stale_jobs") {
          // Stale jobs: patch each one into the lists rather than
          // refetching the entire list.
          if (msg.jobs?.length > 0) {
            handleChangedJobs(msg.jobs.map((s: { id: string }) => s.id));
          }
        } else if (msg.type === "flow_source_changed") {
          for (const fn of flowSourceListeners) fn(msg as FlowSourceChangedMessage);
        } else if (msg.type === "agent_output") {
          for (const fn of agentOutputListeners) fn(msg);
        } else if (msg.type === "script_output") {
          for (const fn of scriptOutputListeners) fn(msg as ScriptOutputMessage);
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      wsRef.current = null;
      setWsState("disconnected");

      if (!shouldReconnectRef.current) {
        return;
      }

      console.log("[ws] disconnected, reconnecting in 3s");
      reconnectTimeoutRef.current = setTimeout(() => {
        if (!shouldReconnectRef.current) {
          return;
        }
        connect();
      }, 3000);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [queryClient]);

  useEffect(() => {
    shouldReconnectRef.current = true;
    connect();
    return () => {
      shouldReconnectRef.current = false;
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      wsRef.current?.close();
    };
  }, [connect]);

  return { wsState };
}

const WsStatusContext = createContext<WsStatus>("disconnected");
export const WsStatusProvider = WsStatusContext.Provider;
export function useWsStatus(): WsStatus {
  return useContext(WsStatusContext);
}
