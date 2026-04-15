import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";
import { toast } from "sonner";
import * as api from "@/lib/api";
import type { SessionInfo } from "@/lib/types";

// ── Query hooks ──────────────────────────────────────────────────────

export function useJobs(status?: string, topLevel: boolean = true, includeArchived: boolean = false) {
  return useQuery({
    queryKey: ["jobs", status, topLevel, includeArchived],
    queryFn: () => api.fetchJobs(status, topLevel, includeArchived),
    select: (data) => data,
  });
}

export function useJob(jobId: string | undefined) {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.fetchJob(jobId!),
    enabled: !!jobId,
  });
}

export function useJobTree(jobId: string | undefined) {
  return useQuery({
    queryKey: ["jobTree", jobId],
    queryFn: () => api.fetchJobTree(jobId!),
    enabled: !!jobId,
  });
}

export function useRuns(jobId: string | undefined, stepName?: string) {
  return useQuery({
    queryKey: ["runs", jobId, stepName],
    queryFn: () => api.fetchRuns(jobId!, stepName),
    enabled: !!jobId,
  });
}

export function useEvents(jobId: string | undefined) {
  return useQuery({
    queryKey: ["events", jobId],
    queryFn: () => api.fetchEvents(jobId!),
    enabled: !!jobId,
  });
}

export function useExecutors() {
  return useQuery({
    queryKey: ["executors"],
    queryFn: api.fetchExecutors,
    staleTime: 60000,
  });
}

export function useTemplates() {
  return useQuery({
    queryKey: ["templates"],
    queryFn: api.fetchTemplates,
    staleTime: 10000,
  });
}

export function useStepEvents(runId: string | undefined) {
  return useQuery({
    queryKey: ["stepEvents", runId],
    queryFn: () => api.fetchStepEvents(runId!),
    enabled: !!runId,
  });
}

export function useRunCost(runId: string | undefined) {
  return useQuery({
    queryKey: ["runCost", runId],
    queryFn: () => api.fetchRunCost(runId!),
    enabled: !!runId,
  });
}

export function useJobCost(jobId: string | undefined) {
  return useQuery({
    queryKey: ["jobCost", jobId],
    queryFn: () => api.fetchJobCost(jobId!),
    enabled: !!jobId,
  });
}

export function useRecentFlows(limit: number = 5) {
  return useQuery({
    queryKey: ["recentFlows", limit],
    queryFn: () => api.fetchRecentFlows(limit),
    staleTime: 10_000,
  });
}

export function useEngineStatus() {
  return useQuery({
    queryKey: ["status"],
    queryFn: api.fetchStatus,
  });
}

export function useJobOutput(jobId: string | undefined, enabled: boolean = true) {
  return useQuery({
    queryKey: ["jobOutput", jobId],
    queryFn: () => api.fetchJobOutput(jobId!),
    enabled: !!jobId && enabled,
    staleTime: Infinity,
  });
}

export function useAgentOutput(runId: string | undefined, options?: { staleTime?: number }) {
  return useQuery({
    queryKey: ["agentOutput", runId],
    queryFn: () => api.fetchAgentOutput(runId!),
    enabled: !!runId,
    staleTime: options?.staleTime ?? Infinity,
  });
}

export function useJobSessions(jobId: string | undefined) {
  return useQuery({
    queryKey: ["sessions", jobId],
    queryFn: () => api.fetchJobSessions(jobId!),
    enabled: !!jobId,
  });
}

export function useSessionTranscript(
  jobId: string | undefined,
  sessionName: string | undefined,
) {
  return useQuery({
    queryKey: ["sessionTranscript", jobId, sessionName],
    queryFn: () => api.fetchSessionTranscript(jobId!, sessionName!),
    enabled: !!jobId && !!sessionName,
    staleTime: Infinity,
  });
}

export interface SessionStepEntry {
  name: string;
  runs: number;
  tokens: number;
}

/** Ordered step entries with run counts and token usage for a session. Backed by cached transcript query. */
export function useSessionStepEntries(
  jobId: string | undefined,
  session: SessionInfo | null | undefined,
): SessionStepEntry[] {
  const { data: transcript } = useSessionTranscript(jobId, session?.session_name);
  return useMemo(() => {
    if (!session) return [];
    const boundaries = transcript?.boundaries ?? [];
    const runCounts = new Map<string, number>();
    const tokenCounts = new Map<string, number>();
    for (const b of boundaries) {
      runCounts.set(b.step_name, (runCounts.get(b.step_name) ?? 0) + 1);
      tokenCounts.set(b.step_name, (tokenCounts.get(b.step_name) ?? 0) + (b.tokens_used ?? 0));
    }
    return session.step_names.map(name => ({
      name,
      runs: runCounts.get(name) ?? 1,
      tokens: tokenCounts.get(name) ?? 0,
    }));
  }, [session, transcript]);
}

export function useSimilarErrors(
  errorCategory: string | null | undefined,
  excludeRunId?: string,
  stepName?: string,
) {
  return useQuery({
    queryKey: ["similarErrors", errorCategory, excludeRunId, stepName],
    queryFn: () => api.fetchSimilarErrors(errorCategory!, excludeRunId, stepName),
    enabled: !!errorCategory,
    staleTime: 30_000,
  });
}

export function useServers() {
  return useQuery({
    queryKey: ["servers"],
    queryFn: api.fetchServers,
    staleTime: 30_000,
  });
}

// ── Mutation hooks ───────────────────────────────────────────────────

export function useGroups() {
  return useQuery({
    queryKey: ["groups"],
    queryFn: api.fetchGroups,
    staleTime: 5_000,
  });
}

export function useStepwiseMutations() {
  const queryClient = useQueryClient();

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["jobs"] });
    queryClient.invalidateQueries({ queryKey: ["job"] });
    queryClient.invalidateQueries({ queryKey: ["runs"] });
    queryClient.invalidateQueries({ queryKey: ["events"] });
    queryClient.invalidateQueries({ queryKey: ["jobTree"] });
    queryClient.invalidateQueries({ queryKey: ["status"] });
    queryClient.invalidateQueries({ queryKey: ["flowStats"] });
    queryClient.invalidateQueries({ queryKey: ["recentFlows"] });
  };

  const createJobMutation = useMutation({
    mutationFn: api.createJob,
    onSuccess: () => {
      invalidateAll();
      toast.success("Job created");
    },
    onError: (error) => {
      toast.error("Failed to create job", { description: error.message });
    },
  });

  const startJobMutation = useMutation({
    mutationFn: api.startJob,
    onSuccess: invalidateAll,
  });

  const pauseJobMutation = useMutation({
    mutationFn: api.pauseJob,
    onSuccess: () => {
      invalidateAll();
      toast.success("Job paused");
    },
    onError: (error) => {
      toast.error("Failed to pause job", { description: error.message });
    },
  });

  const resumeJobMutation = useMutation({
    mutationFn: api.resumeJob,
    onSuccess: () => {
      invalidateAll();
      toast.success("Job resumed");
    },
    onError: (error) => {
      toast.error("Failed to resume job", { description: error.message });
    },
  });

  const cancelJobMutation = useMutation({
    mutationFn: api.cancelJob,
    onSuccess: () => {
      invalidateAll();
      toast.success("Job cancelled");
    },
    onError: (error) => {
      toast.error("Failed to cancel job", { description: error.message });
    },
  });

  const resetJobMutation = useMutation({
    mutationFn: api.resetJob,
    onSuccess: () => {
      invalidateAll();
      toast.success("Job reset");
    },
    onError: (error) => {
      toast.error("Failed to reset job", { description: error.message });
    },
  });

  const retryFailedStepsMutation = useMutation({
    mutationFn: api.retryFailedSteps,
    onSuccess: (result) => {
      invalidateAll();
      const parts: string[] = [];
      if (result.steps_rerun > 0) {
        parts.push(`${result.steps_rerun} step${result.steps_rerun === 1 ? "" : "s"} rerun`);
      }
      if (result.delegated_reset > 0) {
        parts.push(`${result.delegated_reset} delegated`);
      }
      if (result.jobs_resumed > 1) {
        parts.push(`${result.jobs_resumed} jobs`);
      }
      toast.success(
        parts.length > 0 ? `Retrying: ${parts.join(", ")}` : "Retrying job",
      );
    },
    onError: (error) => {
      toast.error("Failed to retry job", { description: error.message });
    },
  });

  const approveJobMutation = useMutation({
    mutationFn: api.approveJob,
    onSuccess: () => {
      invalidateAll();
      toast.success("Job approved");
    },
    onError: (error) => {
      toast.error("Failed to approve job", { description: error.message });
    },
  });

  const rerunStepMutation = useMutation({
    mutationFn: ({ jobId, stepName }: { jobId: string; stepName: string }) =>
      api.rerunStep(jobId, stepName),
    onSuccess: () => {
      invalidateAll();
      toast.success("Step restart started");
    },
    onError: (error) => {
      toast.error("Failed to restart step", { description: error.message });
    },
  });

  const fulfillWatchMutation = useMutation({
    mutationFn: ({
      runId,
      payload,
    }: {
      runId: string;
      payload: Record<string, unknown>;
    }) => api.fulfillWatch(runId, payload),
    onSuccess: () => {
      invalidateAll();
      toast.success("Watch fulfilled");
    },
    onError: (error) => {
      toast.error("Failed to fulfill watch", { description: error.message });
    },
  });

  const injectContextMutation = useMutation({
    mutationFn: ({ jobId, context }: { jobId: string; context: string }) =>
      api.injectContext(jobId, context),
    onSuccess: invalidateAll,
  });

  const triggerPollNowMutation = useMutation({
    mutationFn: api.triggerPollNow,
    onSuccess: () => {
      invalidateAll();
      toast.success("Poll triggered");
    },
    onError: (error) => {
      toast.error("Failed to trigger poll", { description: error.message });
    },
  });

  const cancelRunMutation = useMutation({
    mutationFn: api.cancelRun,
    onSuccess: () => {
      invalidateAll();
      toast.success("Run cancelled");
    },
    onError: (error) => {
      toast.error("Failed to cancel run", { description: error.message });
    },
  });

  const deleteJobMutation = useMutation({
    mutationFn: api.deleteJob,
    onSuccess: () => {
      invalidateAll();
      toast.success("Job deleted");
    },
    onError: (error) => {
      toast.error("Failed to delete job", { description: error.message });
    },
  });

  const deleteAllJobsMutation = useMutation({
    mutationFn: api.deleteAllJobs,
    onSuccess: () => {
      invalidateAll();
      toast.success("All jobs deleted");
    },
    onError: (error) => {
      toast.error("Failed to delete jobs", { description: error.message });
    },
  });

  const archiveJobMutation = useMutation({
    mutationFn: api.archiveJob,
    onSuccess: () => {
      invalidateAll();
      toast.success("Job archived");
    },
    onError: (error) => {
      toast.error("Failed to archive job", { description: error.message });
    },
  });

  const unarchiveJobMutation = useMutation({
    mutationFn: api.unarchiveJob,
    onSuccess: () => {
      invalidateAll();
      toast.success("Job unarchived");
    },
    onError: (error) => {
      toast.error("Failed to unarchive job", { description: error.message });
    },
  });

  const archiveJobsMutation = useMutation({
    mutationFn: api.archiveJobs,
    onSuccess: (data) => {
      invalidateAll();
      toast.success(`Archived ${data.count} job(s)`);
    },
    onError: (error) => {
      toast.error("Failed to archive jobs", { description: error.message });
    },
  });

  const bulkDeleteJobsMutation = useMutation({
    mutationFn: api.bulkDeleteJobs,
    onSuccess: (data) => {
      invalidateAll();
      toast.success(`Deleted ${data.count} job(s)`);
    },
    onError: (error) => {
      toast.error("Failed to delete jobs", { description: error.message });
    },
  });

  const saveTemplateMutation = useMutation({
    mutationFn: api.saveTemplate,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["templates"] });
      toast.success("Template saved");
    },
    onError: (error) => {
      toast.error("Failed to save template", { description: error.message });
    },
  });

  const adoptJobMutation = useMutation({
    mutationFn: api.adoptJob,
    onSuccess: () => {
      invalidateAll();
      toast.success("Job adopted");
    },
    onError: (error) => {
      toast.error("Failed to adopt job", { description: error.message });
    },
  });

  const updateGroupLimitMutation = useMutation({
    mutationFn: ({ group, maxConcurrent }: { group: string; maxConcurrent: number }) =>
      api.updateGroup(group, maxConcurrent),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["groups"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
  });

  const deleteTemplateMutation = useMutation({
    mutationFn: api.deleteTemplate,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["templates"] });
      toast.success("Template deleted");
    },
    onError: (error) => {
      toast.error("Failed to delete template", { description: error.message });
    },
  });

  return {
    createJob: createJobMutation,
    startJob: startJobMutation,
    pauseJob: pauseJobMutation,
    resumeJob: resumeJobMutation,
    cancelJob: cancelJobMutation,
    resetJob: resetJobMutation,
    retryFailedSteps: retryFailedStepsMutation,
    approveJob: approveJobMutation,
    rerunStep: rerunStepMutation,
    fulfillWatch: fulfillWatchMutation,
    injectContext: injectContextMutation,
    triggerPollNow: triggerPollNowMutation,
    cancelRun: cancelRunMutation,
    deleteJob: deleteJobMutation,
    deleteAllJobs: deleteAllJobsMutation,
    archiveJob: archiveJobMutation,
    unarchiveJob: unarchiveJobMutation,
    archiveJobs: archiveJobsMutation,
    bulkDeleteJobs: bulkDeleteJobsMutation,
    adoptJob: adoptJobMutation,
    saveTemplate: saveTemplateMutation,
    deleteTemplate: deleteTemplateMutation,
    updateGroupLimit: updateGroupLimitMutation,
  };
}
