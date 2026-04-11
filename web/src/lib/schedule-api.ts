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

export function fetchSchedules(status?: string, type?: string): Promise<Schedule[]> {
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (type) params.set("type", type);
  const qs = params.toString();
  return request<Schedule[]>(`/schedules${qs ? `?${qs}` : ""}`);
}

export function fetchSchedule(scheduleId: string): Promise<Schedule> {
  return request<Schedule>(`/schedules/${scheduleId}`);
}

export interface CreateSchedulePayload {
  name: string;
  type: "cron" | "poll";
  flow_path: string;
  cron_expr?: string;
  poll_command?: string;
  poll_timeout_seconds?: number;
  cooldown_seconds?: number;
  job_inputs?: Record<string, unknown>;
  job_name_template?: string;
  overlap_policy?: string;
  recovery_policy?: string;
  timezone?: string;
  max_consecutive_errors?: number;
  metadata?: Record<string, unknown>;
}

export function createSchedule(payload: CreateSchedulePayload): Promise<Schedule> {
  return request<Schedule>("/schedules", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateSchedule(
  scheduleId: string,
  payload: Partial<CreateSchedulePayload>,
): Promise<Schedule> {
  return request<Schedule>(`/schedules/${scheduleId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
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
): Promise<{ id: string; name: string | null; status: string; created_at: string }[]> {
  return request(`/schedules/${scheduleId}/jobs?limit=${limit}`);
}
