import { useState, useEffect, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api";
import type { ModelInfo } from "@/lib/api";

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: api.fetchConfig,
    staleTime: 10000,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: api.fetchHealth,
    // Settings footer refreshes this — 5s is fine for a read-mostly
    // status strip. Don't poll more aggressively than needed.
    refetchInterval: 5000,
    staleTime: 4000,
  });
}

export function useOpenRouterSearch(query: string) {
  const [debouncedQuery, setDebouncedQuery] = useState(query);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => {
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setDebouncedQuery(query), 300);
    return () => clearTimeout(timerRef.current);
  }, [query]);

  return useQuery({
    queryKey: ["openrouter-models", debouncedQuery],
    queryFn: () => api.searchOpenRouterModels(debouncedQuery, 30),
    enabled: debouncedQuery.length >= 2,
    staleTime: 60000,
    select: (data) => data.models,
  });
}

export function useConfigMutations() {
  const queryClient = useQueryClient();

  const invalidateConfig = () => {
    queryClient.invalidateQueries({ queryKey: ["config"] });
  };

  const createLabel = useMutation({
    mutationFn: ({ name, model }: { name: string; model: string }) =>
      api.createLabel(name, model),
    onSuccess: invalidateConfig,
  });

  const updateLabel = useMutation({
    mutationFn: ({ name, model }: { name: string; model: string }) =>
      api.updateLabel(name, model),
    onSuccess: invalidateConfig,
  });

  const deleteLabel = useMutation({
    mutationFn: (name: string) => api.deleteLabel(name),
    onSuccess: invalidateConfig,
  });

  const addModel = useMutation({
    mutationFn: (model: ModelInfo) => api.addModel(model),
    onSuccess: invalidateConfig,
  });

  const removeModel = useMutation({
    mutationFn: (modelId: string) => api.removeModel(modelId),
    onSuccess: invalidateConfig,
  });

  const setApiKey = useMutation({
    mutationFn: ({ key, value, scope }: { key: string; value: string; scope?: string }) =>
      api.setApiKey(key, value, scope),
    onSuccess: invalidateConfig,
  });

  const setDefaultModel = useMutation({
    mutationFn: (model: string) => api.setDefaultModel(model),
    onSuccess: invalidateConfig,
  });

  const setDefaultAgent = useMutation({
    mutationFn: (agent: string) => api.setDefaultAgent(agent),
    onSuccess: invalidateConfig,
  });

  const setAgentContainmentDefault = useMutation({
    mutationFn: (containment: string | null) =>
      api.setAgentContainmentDefault(containment),
    onSuccess: invalidateConfig,
  });

  const setAgentConcurrencyLimit = useMutation({
    mutationFn: ({ agent, limit }: { agent: string; limit: number }) =>
      api.setAgentConcurrencyLimit(agent, limit),
    onSuccess: invalidateConfig,
  });

  const setExecutorConcurrencyLimit = useMutation({
    mutationFn: ({ executor_type, limit }: { executor_type: string; limit: number }) =>
      api.setExecutorConcurrencyLimit(executor_type, limit),
    onSuccess: invalidateConfig,
  });

  const setMaxConcurrentJobs = useMutation({
    mutationFn: (limit: number) => api.setMaxConcurrentJobs(limit),
    onSuccess: invalidateConfig,
  });

  const setAgentProcessTtl = useMutation({
    mutationFn: (ttl_seconds: number) => api.setAgentProcessTtl(ttl_seconds),
    onSuccess: invalidateConfig,
  });

  const setAgentPermissions = useMutation({
    mutationFn: (permissions: string) => api.setAgentPermissions(permissions),
    onSuccess: invalidateConfig,
  });

  const setNotifyWebhook = useMutation({
    mutationFn: ({
      url,
      context,
    }: {
      url: string | null;
      context: Record<string, unknown> | null;
    }) => api.setNotifyWebhook(url, context),
    onSuccess: invalidateConfig,
  });

  return {
    createLabel,
    updateLabel,
    deleteLabel,
    addModel,
    removeModel,
    setApiKey,
    setDefaultModel,
    setDefaultAgent,
    setAgentContainmentDefault,
    setAgentConcurrencyLimit,
    setExecutorConcurrencyLimit,
    setMaxConcurrentJobs,
    setAgentProcessTtl,
    setAgentPermissions,
    setNotifyWebhook,
  };
}
