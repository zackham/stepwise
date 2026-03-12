import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api";

export function useLocalFlows() {
  return useQuery({
    queryKey: ["localFlows"],
    queryFn: api.fetchLocalFlows,
    staleTime: 10000,
  });
}

export function useLocalFlow(path: string | undefined) {
  return useQuery({
    queryKey: ["localFlow", path],
    queryFn: () => api.fetchLocalFlow(path!),
    enabled: !!path,
  });
}

export function useParseYaml() {
  return useMutation({
    mutationFn: (yaml: string) => api.parseYaml(yaml),
  });
}

export function useSaveFlow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ path, yaml }: { path: string; yaml: string }) =>
      api.saveFlow(path, yaml),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["localFlows"] });
      queryClient.invalidateQueries({ queryKey: ["localFlow"] });
    },
  });
}

export function usePatchStep() {
  return useMutation({
    mutationFn: ({
      flowPath,
      stepName,
      changes,
    }: {
      flowPath: string;
      stepName: string;
      changes: Record<string, unknown>;
    }) => api.patchStep(flowPath, stepName, changes),
  });
}

export function useAddStep() {
  return useMutation({
    mutationFn: ({
      flowPath,
      name,
      executor,
    }: {
      flowPath: string;
      name: string;
      executor: string;
    }) => api.addStep(flowPath, name, executor),
  });
}

export function useDeleteStep() {
  return useMutation({
    mutationFn: ({
      flowPath,
      stepName,
    }: {
      flowPath: string;
      stepName: string;
    }) => api.deleteStep(flowPath, stepName),
  });
}

// ── Registry ──────────────────────────────────────────────────────────

export function useRegistrySearch(query: string, sort: string = "downloads") {
  return useQuery({
    queryKey: ["registrySearch", query, sort],
    queryFn: () => api.searchRegistry(query, undefined, sort),
    staleTime: 300000, // 5min cache
    enabled: true,
  });
}

export function useRegistryFlow(slug: string | undefined) {
  return useQuery({
    queryKey: ["registryFlow", slug],
    queryFn: () => api.fetchRegistryFlow(slug!),
    enabled: !!slug,
    staleTime: 600000, // 10min cache
  });
}

export function useInstallFlow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (slug: string) => api.installFlow(slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["localFlows"] });
    },
  });
}
