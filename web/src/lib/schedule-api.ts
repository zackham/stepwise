import type { Schedule, ScheduleTick, ScheduleStats } from "./schedule-types";

const BASE_URL = "/api";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
    ...options,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

// ── Schedules ──────────────────────────────────────────────────────────

export interface SchedulesResponse {
  schedules: Schedule[];
  total: number;
}

export function fetchSchedules(status?: string, type?: string): Promise<SchedulesResponse> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (type) params.set("type", type);
  params.set("include_total", "true");
  const qs = params.toString();
  return request<SchedulesResponse>(`/schedules${qs ? `?${qs}` : ""}`);
}

export function fetchSchedule(scheduleId: string): Promise<Schedule> {
  return request<Schedule>(`/schedules/${scheduleId}`);
}

export function pauseSchedule(scheduleId: string): Promise<{ status: string }> {
  return request(`/schedules/${scheduleId}/pause`, { method: "POST" });
}

export function resumeSchedule(scheduleId: string): Promise<{ status: string }> {
  return request(`/schedules/${scheduleId}/resume`, { method: "POST" });
}

export function triggerSchedule(scheduleId: string): Promise<{ status: string; job_id?: string }> {
  return request(`/schedules/${scheduleId}/trigger`, { method: "POST" });
}

export function deleteSchedule(scheduleId: string): Promise<{ status: string }> {
  return request(`/schedules/${scheduleId}`, { method: "DELETE" });
}

export function fetchScheduleTicks(
  scheduleId: string,
  limit: number = 50,
): Promise<ScheduleTick[]> {
  return request<ScheduleTick[]>(`/schedules/${scheduleId}/ticks?limit=${limit}`);
}

export function fetchScheduleStats(scheduleId: string): Promise<ScheduleStats> {
  return request<ScheduleStats>(`/schedules/${scheduleId}/stats`);
}

export function fetchScheduleJobs(
  scheduleId: string,
  limit: number = 20,
): Promise<{ job_id: string; name: string | null; status: string; created_at: string }[]> {
  return request(`/schedules/${scheduleId}/jobs?limit=${limit}`);
}
