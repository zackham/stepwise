import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import * as api from "@/lib/api";
import type { AgentInfo } from "@/lib/api";

// ── Query hook ──────────────────────────────────────────────────────

export function useAgents() {
  return useQuery({
    queryKey: ["agents"],
    queryFn: api.fetchAgents,
    staleTime: 10000,
    select: (data) => data.agents,
  });
}

// ── Mutation hooks ──────────────────────────────────────────────────

export function useAgentMutations() {
  const queryClient = useQueryClient();

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["agents"] });
    queryClient.invalidateQueries({ queryKey: ["config"] });
  };

  const updateAgent = useMutation({
    mutationFn: ({ name, agent }: { name: string; agent: Partial<AgentInfo> }) =>
      api.updateAgent(name, agent),
    onSuccess: () => {
      invalidateAll();
      toast.success("Agent updated");
    },
    onError: (error) => {
      toast.error("Failed to update agent", { description: error.message });
    },
  });

  const createAgent = useMutation({
    mutationFn: (agent: Partial<AgentInfo>) => api.createAgent(agent),
    onSuccess: () => {
      invalidateAll();
      toast.success("Agent created");
    },
    onError: (error) => {
      toast.error("Failed to create agent", { description: error.message });
    },
  });

  const deleteAgent = useMutation({
    mutationFn: (name: string) => api.deleteAgent(name),
    onSuccess: () => {
      invalidateAll();
      toast.success("Agent deleted");
    },
    onError: (error) => {
      toast.error("Failed to delete agent", { description: error.message });
    },
  });

  const disableAgent = useMutation({
    mutationFn: (name: string) => api.disableAgent(name),
    onSuccess: () => {
      invalidateAll();
      toast.success("Agent disabled");
    },
    onError: (error) => {
      toast.error("Failed to disable agent", { description: error.message });
    },
  });

  const enableAgent = useMutation({
    mutationFn: (name: string) => api.enableAgent(name),
    onSuccess: () => {
      invalidateAll();
      toast.success("Agent enabled");
    },
    onError: (error) => {
      toast.error("Failed to enable agent", { description: error.message });
    },
  });

  const resetAgent = useMutation({
    mutationFn: (name: string) => api.resetAgent(name),
    onSuccess: () => {
      invalidateAll();
      toast.success("Agent reset to defaults");
    },
    onError: (error) => {
      toast.error("Failed to reset agent", { description: error.message });
    },
  });

  const setAgentContainment = useMutation({
    mutationFn: ({ name, containment }: { name: string; containment: string | null }) =>
      api.setAgentContainment(name, containment),
    onSuccess: invalidateAll,
    onError: (error) => {
      toast.error("Failed to update agent containment", {
        description: error.message,
      });
    },
  });

  return {
    updateAgent,
    createAgent,
    deleteAgent,
    disableAgent,
    enableAgent,
    resetAgent,
    setAgentContainment,
  };
}
