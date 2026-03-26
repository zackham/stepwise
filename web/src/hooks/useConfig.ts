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

  return {
    createLabel,
    updateLabel,
    deleteLabel,
    addModel,
    removeModel,
    setApiKey,
    setDefaultModel,
  };
}
