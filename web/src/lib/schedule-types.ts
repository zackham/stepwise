// ── Schedule Types ──────────────────────────────────────────────────────

export type ScheduleStatus = "active" | "paused";
export type ScheduleType = "cron" | "poll";
export type TickOutcome = "fired" | "skipped" | "error" | "overlap_skipped" | "cooldown_skipped";
export type OverlapPolicy = "skip" | "queue" | "allow";
export type RecoveryPolicy = "skip" | "catch_up_once";

export interface Schedule {
  id: string;
  name: string;
  type: ScheduleType;
  flow_path: string;
  cron_expr: string;
  cron_description: string;
  poll_command: string | null;
  poll_timeout_seconds: number;
  cooldown_seconds: number | null;
  job_inputs: Record<string, unknown>;
  job_name_template: string | null;
  overlap_policy: OverlapPolicy;
  recovery_policy: RecoveryPolicy;
  status: ScheduleStatus;
  timezone: string;
  max_consecutive_errors: number;
  created_at: string;
  updated_at: string;
  paused_at: string | null;
  last_fired_at: string | null;
  last_job_status: string | null;
  metadata: Record<string, unknown>;
}

export interface ScheduleTick {
  id: string;
  schedule_id: string;
  scheduled_for: string;
  evaluated_at: string;
  outcome: TickOutcome;
  reason: string | null;
  poll_output: Record<string, unknown> | null;
  job_id: string | null;
  duration_ms: number | null;
}

export interface ScheduleStats {
  total_ticks: number;
  total_fires: number;
  fire_rate: number;
  avg_check_duration_ms: number | null;
  last_fired_at: string | null;
  consecutive_errors: number;
  consecutive_skips: number;
}
