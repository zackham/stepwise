import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import * as scheduleApi from "@/lib/schedule-api";
import type { CreateSchedulePayload } from "@/lib/schedule-api";

// ── Query hooks ──────────────────────────────────────────────────────

export function useSchedules(status?: string, type?: string) {
  return useQuery({
    queryKey: ["schedules", status, type],
    queryFn: () => scheduleApi.fetchSchedules(status, type),
  });
}

export function useSchedule(scheduleId: string | undefined) {
  return useQuery({
    queryKey: ["schedule", scheduleId],
    queryFn: () => scheduleApi.fetchSchedule(scheduleId!),
    enabled: !!scheduleId,
  });
}

export function useScheduleTicks(scheduleId: string | undefined, limit: number = 50) {
  return useQuery({
    queryKey: ["scheduleTicks", scheduleId, limit],
    queryFn: () => scheduleApi.fetchScheduleTicks(scheduleId!, limit),
    enabled: !!scheduleId,
  });
}

export function useScheduleStats(scheduleId: string | undefined) {
  return useQuery({
    queryKey: ["scheduleStats", scheduleId],
    queryFn: () => scheduleApi.fetchScheduleStats(scheduleId!),
    enabled: !!scheduleId,
  });
}

export function useScheduleJobs(scheduleId: string | undefined, limit: number = 20) {
  return useQuery({
    queryKey: ["scheduleJobs", scheduleId, limit],
    queryFn: () => scheduleApi.fetchScheduleJobs(scheduleId!, limit),
    enabled: !!scheduleId,
  });
}

// ── Mutation hooks ───────────────────────────────────────────────────

export function useScheduleMutations() {
  const queryClient = useQueryClient();

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["schedules"] });
    queryClient.invalidateQueries({ queryKey: ["schedule"] });
    queryClient.invalidateQueries({ queryKey: ["scheduleTicks"] });
    queryClient.invalidateQueries({ queryKey: ["scheduleStats"] });
    queryClient.invalidateQueries({ queryKey: ["scheduleJobs"] });
  };

  const createScheduleMutation = useMutation({
    mutationFn: (payload: CreateSchedulePayload) => scheduleApi.createSchedule(payload),
    onSuccess: () => {
      invalidateAll();
      toast.success("Schedule created");
    },
    onError: (error) => {
      toast.error("Failed to create schedule", { description: error.message });
    },
  });

  const updateScheduleMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Partial<CreateSchedulePayload> }) =>
      scheduleApi.updateSchedule(id, payload),
    onSuccess: () => {
      invalidateAll();
      toast.success("Schedule updated");
    },
    onError: (error) => {
      toast.error("Failed to update schedule", { description: error.message });
    },
  });

  const pauseScheduleMutation = useMutation({
    mutationFn: scheduleApi.pauseSchedule,
    onSuccess: () => {
      invalidateAll();
      toast.success("Schedule paused");
    },
    onError: (error) => {
      toast.error("Failed to pause schedule", { description: error.message });
    },
  });

  const resumeScheduleMutation = useMutation({
    mutationFn: scheduleApi.resumeSchedule,
    onSuccess: () => {
      invalidateAll();
      toast.success("Schedule resumed");
    },
    onError: (error) => {
      toast.error("Failed to resume schedule", { description: error.message });
    },
  });

  const triggerScheduleMutation = useMutation({
    mutationFn: scheduleApi.triggerSchedule,
    onSuccess: () => {
      invalidateAll();
      toast.success("Schedule triggered");
    },
    onError: (error) => {
      toast.error("Failed to trigger schedule", { description: error.message });
    },
  });

  const deleteScheduleMutation = useMutation({
    mutationFn: scheduleApi.deleteSchedule,
    onSuccess: () => {
      invalidateAll();
      toast.success("Schedule deleted");
    },
    onError: (error) => {
      toast.error("Failed to delete schedule", { description: error.message });
    },
  });

  return {
    createSchedule: createScheduleMutation,
    updateSchedule: updateScheduleMutation,
    pauseSchedule: pauseScheduleMutation,
    resumeSchedule: resumeScheduleMutation,
    triggerSchedule: triggerScheduleMutation,
    deleteSchedule: deleteScheduleMutation,
  };
}
