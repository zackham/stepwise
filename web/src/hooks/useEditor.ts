import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import * as api from "@/lib/api";

export function useLocalFlows() {
  return useQuery({
    queryKey: ["localFlows"],
    queryFn: api.fetchLocalFlows,
    staleTime: 10000,
  });
}

export function useKits() {
  return useQuery({
    queryKey: ["kits"],
    queryFn: api.fetchKits,
    staleTime: 10000,
  });
}

export function useKitDetail(kitName: string | null) {
  return useQuery({
    queryKey: ["kitDetail", kitName],
    queryFn: () => api.fetchKitDetail(kitName!),
    enabled: !!kitName,
    staleTime: 30000,
  });
}

export function useFlowStats() {
  return useQuery({
    queryKey: ["flowStats"],
    queryFn: api.fetchFlowStats,
    staleTime: 30000,
  });
}

export function useFlowJobs(flowDir: string | undefined, limit: number = 10) {
  return useQuery({
    queryKey: ["flowJobs", flowDir, limit],
    queryFn: () => api.fetchFlowJobs(flowDir!, limit),
    enabled: !!flowDir,
    staleTime: 15000,
  });
}

export function useCreateFlow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, template }: { name: string; template?: string }) =>
      api.createLocalFlow(name, template),
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

export function useForkFlow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ sourcePath, name }: { sourcePath: string; name: string }) =>
      api.forkFlow(sourcePath, name),
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
      toast.success("Flow saved");
    },
    onError: (error) => {
      toast.error("Failed to save flow", { description: error.message });
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
  const queryClient = useQueryClient();
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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["localFlows"] });
      queryClient.invalidateQueries({ queryKey: ["localFlow"] });
    },
    onError: (error) => {
      toast.error("Failed to add step", { description: error.message });
    },
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

// ── Flow Config ──────────────────────────────────────────────────────

export function useFlowConfig(flowPath: string | undefined) {
  return useQuery({
    queryKey: ["flowConfig", flowPath],
    queryFn: () => api.fetchFlowConfig(flowPath!),
    enabled: !!flowPath,
    staleTime: 5000,
  });
}

export function useSaveFlowConfig() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      flowPath,
      data,
    }: {
      flowPath: string;
      data: { values?: Record<string, unknown>; raw_yaml?: string };
    }) => api.saveFlowConfig(flowPath, data),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: ["flowConfig", variables.flowPath] });
      queryClient.invalidateQueries({ queryKey: ["flowFiles", variables.flowPath] });
      toast.success("Config saved");
    },
    onError: (error) => {
      toast.error("Failed to save config", { description: error.message });
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
