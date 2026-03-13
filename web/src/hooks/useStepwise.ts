import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api";

// ── Query hooks ──────────────────────────────────────────────────────

export function useJobs(status?: string, topLevel: boolean = true) {
  return useQuery({
    queryKey: ["jobs", status, topLevel],
    queryFn: () => api.fetchJobs(status, topLevel),
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

export function useAgentOutput(runId: string | undefined) {
  return useQuery({
    queryKey: ["agentOutput", runId],
    queryFn: () => api.fetchAgentOutput(runId!),
    enabled: !!runId,
    staleTime: Infinity,
  });
}

// ── Mutation hooks ───────────────────────────────────────────────────

export function useStepwiseMutations() {
  const queryClient = useQueryClient();

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["jobs"] });
    queryClient.invalidateQueries({ queryKey: ["job"] });
    queryClient.invalidateQueries({ queryKey: ["runs"] });
    queryClient.invalidateQueries({ queryKey: ["events"] });
    queryClient.invalidateQueries({ queryKey: ["jobTree"] });
    queryClient.invalidateQueries({ queryKey: ["status"] });
  };

  const createJobMutation = useMutation({
    mutationFn: api.createJob,
    onSuccess: invalidateAll,
  });

  const startJobMutation = useMutation({
    mutationFn: api.startJob,
    onSuccess: invalidateAll,
  });

  const pauseJobMutation = useMutation({
    mutationFn: api.pauseJob,
    onSuccess: invalidateAll,
  });

  const resumeJobMutation = useMutation({
    mutationFn: api.resumeJob,
    onSuccess: invalidateAll,
  });

  const cancelJobMutation = useMutation({
    mutationFn: api.cancelJob,
    onSuccess: invalidateAll,
  });

  const rerunStepMutation = useMutation({
    mutationFn: ({ jobId, stepName }: { jobId: string; stepName: string }) =>
      api.rerunStep(jobId, stepName),
    onSuccess: invalidateAll,
  });

  const fulfillWatchMutation = useMutation({
    mutationFn: ({
      runId,
      payload,
    }: {
      runId: string;
      payload: Record<string, unknown>;
    }) => api.fulfillWatch(runId, payload),
    onSuccess: invalidateAll,
  });

  const injectContextMutation = useMutation({
    mutationFn: ({ jobId, context }: { jobId: string; context: string }) =>
      api.injectContext(jobId, context),
    onSuccess: invalidateAll,
  });

  const cancelRunMutation = useMutation({
    mutationFn: api.cancelRun,
    onSuccess: invalidateAll,
  });

  const deleteJobMutation = useMutation({
    mutationFn: api.deleteJob,
    onSuccess: invalidateAll,
  });

  const deleteAllJobsMutation = useMutation({
    mutationFn: api.deleteAllJobs,
    onSuccess: invalidateAll,
  });

  const saveTemplateMutation = useMutation({
    mutationFn: api.saveTemplate,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["templates"] });
    },
  });

  const adoptJobMutation = useMutation({
    mutationFn: api.adoptJob,
    onSuccess: invalidateAll,
  });

  const deleteTemplateMutation = useMutation({
    mutationFn: api.deleteTemplate,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["templates"] });
    },
  });

  return {
    createJob: createJobMutation,
    startJob: startJobMutation,
    pauseJob: pauseJobMutation,
    resumeJob: resumeJobMutation,
    cancelJob: cancelJobMutation,
    rerunStep: rerunStepMutation,
    fulfillWatch: fulfillWatchMutation,
    injectContext: injectContextMutation,
    cancelRun: cancelRunMutation,
    deleteJob: deleteJobMutation,
    deleteAllJobs: deleteAllJobsMutation,
    adoptJob: adoptJobMutation,
    saveTemplate: saveTemplateMutation,
    deleteTemplate: deleteTemplateMutation,
  };
}
