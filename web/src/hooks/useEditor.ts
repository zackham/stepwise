import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api";

export function useLocalFlows() {
  return useQuery({
    queryKey: ["localFlows"],
    queryFn: api.fetchLocalFlows,
    staleTime: 10000,
  });
}

export function useCreateFlow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => api.createLocalFlow(name),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["localFlows"] });
    },
  });
}

export function useDeleteFlow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (path: string) => api.deleteFlow(path),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["localFlows"] });
    },
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

export function usePatchFlowMetadata() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      path,
      metadata,
    }: {
      path: string;
      metadata: Partial<import("@/lib/types").FlowMetadata>;
    }) => api.patchFlowMetadata(path, metadata),
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

// ── Flow Files ───────────────────────────────────────────────────────

export function useFlowFiles(flowPath: string | undefined) {
  return useQuery({
    queryKey: ["flowFiles", flowPath],
    queryFn: () => api.fetchFlowFiles(flowPath!),
    enabled: !!flowPath,
    staleTime: 5000,
  });
}

export function useFlowFile(flowPath: string | undefined, filePath: string | undefined) {
  return useQuery({
    queryKey: ["flowFile", flowPath, filePath],
    queryFn: () => api.readFlowFile(flowPath!, filePath!),
    enabled: !!flowPath && !!filePath,
  });
}

export function useWriteFlowFile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      flowPath,
      filePath,
      content,
    }: {
      flowPath: string;
      filePath: string;
      content: string;
    }) => api.writeFlowFile(flowPath, filePath, content),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["flowFiles", variables.flowPath] });
      queryClient.invalidateQueries({ queryKey: ["flowFile", variables.flowPath, variables.filePath] });
    },
  });
}

export function useDeleteFlowFile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      flowPath,
      filePath,
    }: {
      flowPath: string;
      filePath: string;
    }) => api.deleteFlowFile(flowPath, filePath),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["flowFiles", variables.flowPath] });
    },
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
