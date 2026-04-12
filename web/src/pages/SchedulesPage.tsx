import { useState, useMemo, useCallback, useEffect } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Play,
  Pause,
  Trash2,
  Zap,
  Clock,
  Terminal,
  Timer,
  Plus,
  Pencil,
  X,
  Loader2,
  Bot,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectTrigger,
  SelectContent,
  SelectItem,
  SelectValue,
  SelectGroup,
  SelectLabel,
} from "@/components/ui/select";
import { useSchedules, useScheduleMutations } from "@/hooks/useSchedules";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import type { Schedule, ScheduleStatus, ScheduleType } from "@/lib/schedule-types";
import type { CreateSchedulePayload } from "@/lib/schedule-api";
import type { LocalFlow } from "@/lib/types";
import { fetchLocalFlows } from "@/lib/api";
import cronstrue from "cronstrue";
import { ScheduleChat } from "@/components/schedules/ScheduleChat";
import { JobStatusBadge } from "@/components/StatusBadge";
import { usePanelRegister } from "@/hooks/usePanelRegister";

// ── Helpers ────────────────────────────────────────────────────────────

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime();
  if (diff < 60000) return "just now";
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`;
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`;
  return `${Math.floor(diff / 86400000)}d ago`;
}

function humanCron(expr: string, fallback?: string): string {
  if (!expr) return fallback || "-";
  try {
    return cronstrue.toString(expr, { use24HourTimeFormat: false });
  } catch {
    return fallback || expr;
  }
}

function flowNameFromPath(path: string): string {
  if (!path) return "-";
  const parts = path.split("/");
  return parts[parts.length - 1]?.replace(/\.ya?ml$/, "") || path;
}

const STATUS_COLORS: Record<ScheduleStatus, { dot: string; bg: string; text: string; ring: string }> = {
  active: {
    dot: "bg-emerald-400",
    bg: "bg-emerald-100 dark:bg-emerald-500/10",
    text: "text-emerald-600 dark:text-emerald-400",
    ring: "ring-emerald-500/30",
  },
  paused: {
    dot: "bg-zinc-400",
    bg: "bg-zinc-100 dark:bg-zinc-500/10",
    text: "text-zinc-500 dark:text-zinc-400",
    ring: "ring-zinc-500/30",
  },
};

const TYPE_COLORS: Record<ScheduleType, { bg: string; text: string; ring: string }> = {
  cron: {
    bg: "bg-blue-100 dark:bg-blue-500/10",
    text: "text-blue-600 dark:text-blue-400",
    ring: "ring-blue-500/30",
  },
  poll: {
    bg: "bg-violet-100 dark:bg-violet-500/10",
    text: "text-violet-600 dark:text-violet-400",
    ring: "ring-violet-500/30",
  },
};

// ── Filter Pills ──────────────────────────────────────────────────────

function FilterPill({
  label,
  active,
  dotColor,
  onClick,
}: {
  label: string;
  active: boolean;
  dotColor?: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 h-8 px-2.5 text-xs rounded-md border transition-colors cursor-pointer",
        active
          ? "bg-zinc-100 dark:bg-zinc-800 border-zinc-300 dark:border-zinc-600 text-foreground"
          : "bg-transparent border-transparent text-foreground/70 hover:text-foreground hover:bg-zinc-800/30",
      )}
    >
      {dotColor && <span className={cn("w-1.5 h-1.5 rounded-full", dotColor)} />}
      {label}
    </button>
  );
}

// ── Delete Confirmation ───────────────────────────────────────────────

function DeleteScheduleButton({
  schedule,
  onDelete,
}: {
  schedule: Schedule;
  onDelete: (id: string) => void;
}) {
  return (
    <AlertDialog>
      <AlertDialogTrigger
        render={
          <button
            className="rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-red-500/10 hover:text-red-400 cursor-pointer"
            title="Delete schedule"
          />
        }
      >
        <Trash2 className="h-3.5 w-3.5" />
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete schedule</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete <strong>{schedule.name}</strong>? This action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={() => onDelete(schedule.id)}
          >
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// ── Schedule Form (create + edit) ────────────────────────────────────

interface ScheduleFormData {
  name: string;
  type: ScheduleType;
  flow_path: string;
  cron_expr: string;
  poll_command: string;
  poll_timeout_seconds: number;
  cooldown_seconds: number;
  job_inputs: { key: string; value: string }[];
  job_name_template: string;
  overlap_policy: string;
  recovery_policy: string;
  timezone: string;
}

// ── Cron Interval Picker ─────────────────────────────────────────────

interface CronPreset {
  label: string;
  cron: string;
  mode: "minute" | "hourly" | "daily" | "weekly";
}

const CRON_PRESETS: CronPreset[] = [
  { label: "Every 5 min", cron: "*/5 * * * *", mode: "minute" },
  { label: "Every 15 min", cron: "*/15 * * * *", mode: "minute" },
  { label: "Every 30 min", cron: "*/30 * * * *", mode: "minute" },
  { label: "Hourly", cron: "0 * * * *", mode: "hourly" },
  { label: "Daily", cron: "0 9 * * *", mode: "daily" },
  { label: "Weekly", cron: "0 9 * * MON", mode: "weekly" },
];

const DAY_NAMES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"];
const DAY_LABELS = ["M", "T", "W", "T", "F", "S", "S"];

function parseCronMode(expr: string): "minute" | "hourly" | "daily" | "weekly" | "custom" {
  if (!expr) return "minute";
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return "custom";
  const [min, hour, dom, mon, dow] = parts;
  // Check presets first
  if (CRON_PRESETS.some((p) => p.cron === expr)) {
    return CRON_PRESETS.find((p) => p.cron === expr)!.mode;
  }
  // Minute intervals: */N * * * *
  if (min.startsWith("*/") && hour === "*" && dom === "*" && mon === "*" && dow === "*") return "minute";
  // Hourly: N * * * *
  if (!min.includes("/") && !min.includes(",") && hour === "*" && dom === "*" && mon === "*" && dow === "*") return "hourly";
  // Weekly: has dow != *
  if (dow !== "*") return "weekly";
  // Daily: specific hour, dom/mon/dow are *
  if (dom === "*" && mon === "*" && dow === "*") return "daily";
  return "custom";
}

function CronIntervalPicker({
  value,
  onChange,
  cronPreview,
  label,
}: {
  value: string;
  onChange: (expr: string) => void;
  cronPreview: string;
  label?: string;
}) {
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [customMode, setCustomMode] = useState<"minute" | "hourly" | "daily" | "weekly" | "custom" | null>(null);

  const mode = customMode ?? parseCronMode(value);

  // Parse current values from cron for the custom controls
  const parts = value.trim().split(/\s+/);
  const cronMin = parts[0] || "0";
  const cronHour = parts[1] || "9";
  const cronDow = parts[4] || "*";

  const minuteInterval = cronMin.startsWith("*/") ? parseInt(cronMin.slice(2)) || 5 : 5;
  const atMinute = !cronMin.includes("/") && !cronMin.includes(",") ? parseInt(cronMin) || 0 : 0;
  const atHour = cronHour !== "*" ? parseInt(cronHour) || 9 : 9;

  const selectedDays = cronDow === "*" ? [] : cronDow.split(",");

  const handlePreset = (preset: CronPreset) => {
    onChange(preset.cron);
    setCustomMode(null);
  };

  const handleMinuteChange = (mins: number) => {
    const clamped = Math.max(1, Math.min(59, mins));
    onChange(`*/${clamped} * * * *`);
  };

  const handleHourlyChange = (min: number) => {
    onChange(`${Math.max(0, Math.min(59, min))} * * * *`);
  };

  const handleDailyChange = (hour: number, min: number) => {
    const safeDow = mode === "weekly" && selectedDays.length > 0 ? selectedDays.join(",") : "*";
    onChange(`${Math.max(0, Math.min(59, min))} ${Math.max(0, Math.min(23, hour))} * * ${safeDow}`);
  };

  const handleDayToggle = (day: string) => {
    let days = [...selectedDays];
    if (days.includes(day)) {
      days = days.filter((d) => d !== day);
    } else {
      days.push(day);
    }
    // Sort by day order
    days.sort((a, b) => DAY_NAMES.indexOf(a) - DAY_NAMES.indexOf(b));
    if (days.length === 0) {
      onChange(`${atMinute} ${atHour} * * *`);
      setCustomMode("daily");
    } else {
      onChange(`${atMinute} ${atHour} * * ${days.join(",")}`);
    }
  };

  const activePreset = CRON_PRESETS.find((p) => p.cron === value);

  return (
    <div className="space-y-2">
      <Label>{label || "Schedule"}</Label>

      {/* Preset buttons */}
      <div className="flex flex-wrap gap-1">
        {CRON_PRESETS.map((preset) => (
          <button
            key={preset.cron}
            type="button"
            onClick={() => handlePreset(preset)}
            className={cn(
              "px-2 py-1 text-[11px] rounded-md border transition-colors cursor-pointer",
              activePreset?.cron === preset.cron
                ? "bg-zinc-100 dark:bg-zinc-800 border-zinc-300 dark:border-zinc-600 text-foreground font-medium"
                : "border-zinc-200 dark:border-zinc-700 text-zinc-500 hover:text-foreground hover:border-zinc-400",
            )}
          >
            {preset.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => { setCustomMode("custom"); setShowAdvanced(true); }}
          className={cn(
            "px-2 py-1 text-[11px] rounded-md border transition-colors cursor-pointer",
            mode === "custom" && !activePreset
              ? "bg-zinc-100 dark:bg-zinc-800 border-zinc-300 dark:border-zinc-600 text-foreground font-medium"
              : "border-zinc-200 dark:border-zinc-700 text-zinc-500 hover:text-foreground hover:border-zinc-400",
          )}
        >
          Custom
        </button>
      </div>

      {/* Custom controls */}
      {mode === "minute" && !activePreset && (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-zinc-500">Every</span>
          <Input
            type="number"
            value={minuteInterval}
            onChange={(e) => handleMinuteChange(parseInt(e.target.value) || 5)}
            min={1}
            max={59}
            className="w-16 h-7 text-xs font-mono"
          />
          <span className="text-zinc-500">minutes</span>
        </div>
      )}

      {mode === "hourly" && !activePreset && (
        <div className="flex items-center gap-2 text-xs">
          <span className="text-zinc-500">At minute</span>
          <Input
            type="number"
            value={atMinute}
            onChange={(e) => handleHourlyChange(parseInt(e.target.value) || 0)}
            min={0}
            max={59}
            className="w-16 h-7 text-xs font-mono"
          />
        </div>
      )}

      {(mode === "daily" || mode === "weekly") && !activePreset && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-xs">
            <span className="text-zinc-500">At</span>
            <Input
              type="number"
              value={atHour}
              onChange={(e) => handleDailyChange(parseInt(e.target.value) || 0, atMinute)}
              min={0}
              max={23}
              className="w-14 h-7 text-xs font-mono"
            />
            <span className="text-zinc-500">:</span>
            <Input
              type="number"
              value={atMinute}
              onChange={(e) => handleDailyChange(atHour, parseInt(e.target.value) || 0)}
              min={0}
              max={59}
              className="w-14 h-7 text-xs font-mono"
            />
          </div>
          <div className="flex items-center gap-1">
            {DAY_NAMES.map((day, i) => (
              <button
                key={day}
                type="button"
                onClick={() => handleDayToggle(day)}
                className={cn(
                  "w-7 h-7 text-[10px] rounded-md border transition-colors cursor-pointer",
                  selectedDays.includes(day)
                    ? "bg-blue-100 dark:bg-blue-500/20 border-blue-300 dark:border-blue-500/40 text-blue-700 dark:text-blue-300 font-medium"
                    : "border-zinc-200 dark:border-zinc-700 text-zinc-500 hover:border-zinc-400",
                )}
              >
                {DAY_LABELS[i]}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Live preview */}
      {cronPreview && (
        <p className={cn(
          "text-[11px]",
          cronPreview === "Invalid cron expression" ? "text-red-400" : "text-zinc-500",
        )}>
          {cronPreview}
        </p>
      )}

      {/* Always show raw cron expression */}
      <Input
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setCustomMode("custom");
        }}
        placeholder="*/5 * * * *"
        className="font-mono text-xs"
      />
    </div>
  );
}

const DEFAULT_FORM: ScheduleFormData = {
  name: "",
  type: "cron",
  flow_path: "",
  cron_expr: "",
  poll_command: "",
  poll_timeout_seconds: 30,
  cooldown_seconds: 0,
  job_inputs: [],
  job_name_template: "",
  overlap_policy: "skip",
  recovery_policy: "skip",
  timezone: "America/Los_Angeles",
};

function scheduleToFormData(schedule: Schedule): ScheduleFormData {
  const inputs = Object.entries(schedule.job_inputs || {}).map(([key, value]) => ({
    key,
    value: typeof value === "string" ? value : JSON.stringify(value),
  }));
  return {
    name: schedule.name,
    type: schedule.type,
    flow_path: schedule.flow_path,
    cron_expr: schedule.cron_expr || "",
    poll_command: schedule.poll_command || "",
    poll_timeout_seconds: schedule.poll_timeout_seconds || 30,
    cooldown_seconds: schedule.cooldown_seconds || 0,
    job_inputs: inputs.length > 0 ? inputs : [],
    job_name_template: schedule.job_name_template || "",
    overlap_policy: schedule.overlap_policy,
    recovery_policy: schedule.recovery_policy,
    timezone: schedule.timezone,
  };
}

function formDataToPayload(form: ScheduleFormData): CreateSchedulePayload {
  const payload: CreateSchedulePayload = {
    name: form.name,
    type: form.type,
    flow_path: form.flow_path,
    overlap_policy: form.overlap_policy,
    recovery_policy: form.recovery_policy,
    timezone: form.timezone,
  };

  if (form.type === "cron") {
    payload.cron_expr = form.cron_expr;
  } else {
    payload.poll_command = form.poll_command;
    payload.poll_timeout_seconds = form.poll_timeout_seconds;
    if (form.cooldown_seconds > 0) {
      payload.cooldown_seconds = form.cooldown_seconds;
    }
    if (form.cron_expr) {
      payload.cron_expr = form.cron_expr;
    }
  }

  if (form.job_name_template) {
    payload.job_name_template = form.job_name_template;
  }

  const inputs: Record<string, unknown> = {};
  for (const { key, value } of form.job_inputs) {
    if (key.trim()) {
      try {
        inputs[key.trim()] = JSON.parse(value);
      } catch {
        inputs[key.trim()] = value;
      }
    }
  }
  if (Object.keys(inputs).length > 0) {
    payload.job_inputs = inputs;
  }

  return payload;
}

function ScheduleFormDialog({
  open,
  onOpenChange,
  editingSchedule,
  onSubmit,
  isSubmitting,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editingSchedule: Schedule | null;
  onSubmit: (form: ScheduleFormData) => void;
  isSubmitting: boolean;
}) {
  const [form, setForm] = useState<ScheduleFormData>(DEFAULT_FORM);
  const [flowSearch, setFlowSearch] = useState("");
  const [flowDropdownOpen, setFlowDropdownOpen] = useState(false);

  const { data: flows = [] } = useQuery({
    queryKey: ["localFlows"],
    queryFn: fetchLocalFlows,
  });

  useEffect(() => {
    if (open) {
      if (editingSchedule) {
        setForm(scheduleToFormData(editingSchedule));
      } else {
        setForm(DEFAULT_FORM);
      }
      setFlowSearch("");
      setFlowDropdownOpen(false);
    }
  }, [open, editingSchedule]);

  const update = <K extends keyof ScheduleFormData>(key: K, value: ScheduleFormData[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const cronPreview = useMemo(() => {
    if (!form.cron_expr) return "";
    try {
      return cronstrue.toString(form.cron_expr, { use24HourTimeFormat: false });
    } catch {
      return "Invalid cron expression";
    }
  }, [form.cron_expr]);

  const filteredFlows = useMemo(() => {
    if (!flowSearch) return flows;
    const q = flowSearch.toLowerCase();
    return flows.filter(
      (f: LocalFlow) =>
        f.name.toLowerCase().includes(q) ||
        f.path.toLowerCase().includes(q) ||
        (f.description && f.description.toLowerCase().includes(q)),
    );
  }, [flows, flowSearch]);

  const selectedFlowName = useMemo(() => {
    if (!form.flow_path) return "";
    const found = flows.find((f: LocalFlow) => f.path === form.flow_path);
    return found?.name || flowNameFromPath(form.flow_path);
  }, [form.flow_path, flows]);

  const isValid =
    form.name.trim() !== "" &&
    form.flow_path.trim() !== "" &&
    (form.type === "poll" || form.cron_expr.trim() !== "");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!isValid) return;
    onSubmit(form);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{editingSchedule ? "Edit Schedule" : "New Schedule"}</DialogTitle>
          <DialogDescription>
            {editingSchedule
              ? "Update the schedule configuration."
              : "Create a new schedule to run a flow on a recurring basis."}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name */}
          <div className="space-y-1.5">
            <Label htmlFor="schedule-name">Name</Label>
            <Input
              id="schedule-name"
              value={form.name}
              onChange={(e) => update("name", e.target.value)}
              placeholder="my-schedule"
              required
            />
          </div>

          {/* Type toggle */}
          <div className="space-y-1.5">
            <Label>Type</Label>
            <div className="flex gap-1">
              {(["cron", "poll"] as const).map((t) => (
                <button
                  key={t}
                  type="button"
                  onClick={() => update("type", t)}
                  className={cn(
                    "flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border transition-colors cursor-pointer",
                    form.type === t
                      ? "bg-zinc-100 dark:bg-zinc-800 border-zinc-300 dark:border-zinc-600 text-foreground"
                      : "bg-transparent border-zinc-200 dark:border-zinc-700 text-zinc-500 hover:text-foreground hover:border-zinc-400",
                  )}
                >
                  {t === "cron" ? <Clock className="h-3 w-3" /> : <Terminal className="h-3 w-3" />}
                  {t}
                </button>
              ))}
            </div>
          </div>

          {/* Flow selector */}
          <div className="space-y-1.5">
            <Label>Flow</Label>
            <div className="relative">
              <Input
                value={flowDropdownOpen ? flowSearch : (selectedFlowName || form.flow_path)}
                onChange={(e) => {
                  setFlowSearch(e.target.value);
                  if (!flowDropdownOpen) setFlowDropdownOpen(true);
                }}
                onFocus={() => setFlowDropdownOpen(true)}
                placeholder="Search flows..."
                required
              />
              {flowDropdownOpen && (
                <>
                  <div
                    className="fixed inset-0 z-[40]"
                    onClick={() => setFlowDropdownOpen(false)}
                  />
                  <div className="absolute z-[50] top-full left-0 right-0 mt-1 bg-popover border border-border rounded-lg shadow-lg max-h-48 overflow-y-auto">
                    {filteredFlows.length === 0 ? (
                      <div className="px-3 py-2 text-xs text-zinc-500">No flows found</div>
                    ) : (
                      filteredFlows.map((f: LocalFlow) => (
                        <button
                          key={f.path}
                          type="button"
                          onClick={() => {
                            update("flow_path", f.path);
                            setFlowSearch("");
                            setFlowDropdownOpen(false);
                          }}
                          className={cn(
                            "w-full text-left px-3 py-2 text-xs hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer",
                            f.path === form.flow_path && "bg-zinc-100 dark:bg-zinc-800",
                          )}
                        >
                          <div className="font-medium text-foreground">{f.name}</div>
                          {f.description && (
                            <div className="text-zinc-500 truncate mt-0.5">{f.description}</div>
                          )}
                          <div className="text-zinc-600 font-mono text-[10px] mt-0.5">{f.path}</div>
                        </button>
                      ))
                    )}
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Cron interval picker (shown for cron type) */}
          {form.type === "cron" && (
            <CronIntervalPicker
              value={form.cron_expr}
              onChange={(expr) => update("cron_expr", expr)}
              cronPreview={cronPreview}
            />
          )}

          {/* Poll-specific fields */}
          {form.type === "poll" && (
            <>
              <CronIntervalPicker
                value={form.cron_expr}
                onChange={(expr) => update("cron_expr", expr)}
                cronPreview={cronPreview}
                label="Poll Check Interval"
              />
              <div className="space-y-1.5">
                <Label htmlFor="schedule-poll-cmd">Poll Command</Label>
                <Textarea
                  id="schedule-poll-cmd"
                  value={form.poll_command}
                  onChange={(e) => update("poll_command", e.target.value)}
                  placeholder="python check_condition.py"
                  rows={2}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="schedule-poll-timeout">Poll Timeout (s)</Label>
                  <Input
                    id="schedule-poll-timeout"
                    type="number"
                    value={form.poll_timeout_seconds}
                    onChange={(e) => update("poll_timeout_seconds", Number(e.target.value))}
                    min={1}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="schedule-cooldown">Cooldown (s)</Label>
                  <Input
                    id="schedule-cooldown"
                    type="number"
                    value={form.cooldown_seconds}
                    onChange={(e) => update("cooldown_seconds", Number(e.target.value))}
                    min={0}
                  />
                </div>
              </div>
            </>
          )}

          {/* Job Inputs */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label>Job Inputs</Label>
              <button
                type="button"
                onClick={() =>
                  update("job_inputs", [...form.job_inputs, { key: "", value: "" }])
                }
                className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-foreground transition-colors cursor-pointer"
              >
                <Plus className="h-3 w-3" />
                Add
              </button>
            </div>
            {form.job_inputs.length > 0 && (
              <div className="space-y-1.5">
                {form.job_inputs.map((input, i) => (
                  <div key={i} className="flex items-center gap-1.5">
                    <Input
                      value={input.key}
                      onChange={(e) => {
                        const next = [...form.job_inputs];
                        next[i] = { ...next[i], key: e.target.value };
                        update("job_inputs", next);
                      }}
                      placeholder="key"
                      className="flex-1 font-mono text-xs"
                    />
                    <span className="text-zinc-500 text-xs">=</span>
                    <Input
                      value={input.value}
                      onChange={(e) => {
                        const next = [...form.job_inputs];
                        next[i] = { ...next[i], value: e.target.value };
                        update("job_inputs", next);
                      }}
                      placeholder="value"
                      className="flex-1 font-mono text-xs"
                    />
                    <button
                      type="button"
                      onClick={() => {
                        const next = form.job_inputs.filter((_, idx) => idx !== i);
                        update("job_inputs", next);
                      }}
                      className="p-1 text-zinc-500 hover:text-red-400 transition-colors cursor-pointer shrink-0"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Job Name Template */}
          <div className="space-y-1.5">
            <Label htmlFor="schedule-job-name">Job Name Template</Label>
            <Input
              id="schedule-job-name"
              value={form.job_name_template}
              onChange={(e) => update("job_name_template", e.target.value)}
              placeholder="my-flow-{date}"
            />
            <p className="text-[11px] text-zinc-500">
              Built-in: <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 font-mono">{"{date}"}</code> <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 font-mono">{"{time}"}</code>
              {form.type === "poll" && (
                <> · Poll output: <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 font-mono">{"{key_name}"}</code></>
              )}
            </p>
          </div>

          {/* Policies + Timezone */}
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1.5">
              <Label>Overlap</Label>
              <Select
                value={form.overlap_policy}
                onValueChange={(val) => { if (val !== null) update("overlap_policy", val as string); }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="skip">skip</SelectItem>
                  <SelectItem value="queue">queue</SelectItem>
                  <SelectItem value="allow">allow</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Recovery</Label>
              <Select
                value={form.recovery_policy}
                onValueChange={(val) => { if (val !== null) update("recovery_policy", val as string); }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="skip">skip</SelectItem>
                  <SelectItem value="catch_up_once">catch_up_once</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Timezone</Label>
              <Select
                value={form.timezone}
                onValueChange={(val) => { if (val !== null) update("timezone", val as string); }}
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    <SelectLabel>Common</SelectLabel>
                    <SelectItem value="America/Los_Angeles">America/Los_Angeles</SelectItem>
                    <SelectItem value="America/Denver">America/Denver</SelectItem>
                    <SelectItem value="America/Chicago">America/Chicago</SelectItem>
                    <SelectItem value="America/New_York">America/New_York</SelectItem>
                    <SelectItem value="UTC">UTC</SelectItem>
                    <SelectItem value="Europe/London">Europe/London</SelectItem>
                    <SelectItem value="Asia/Tokyo">Asia/Tokyo</SelectItem>
                  </SelectGroup>
                </SelectContent>
              </Select>
            </div>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={!isValid || isSubmitting}>
              {isSubmitting && <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />}
              {editingSchedule ? "Save Changes" : "Create Schedule"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ── Schedule Row ──────────────────────────────────────────────────────

function ScheduleRow({
  schedule,
  onPauseResume,
  onTrigger,
  onDelete,
  onEdit,
}: {
  schedule: Schedule;
  onPauseResume: (schedule: Schedule) => void;
  onTrigger: (id: string) => void;
  onDelete: (id: string) => void;
  onEdit: (schedule: Schedule) => void;
}) {
  const navigate = useNavigate();
  const statusColors = STATUS_COLORS[schedule.status];
  const typeColors = TYPE_COLORS[schedule.type];

  return (
    <div
      onClick={() => navigate({ to: "/schedules/$scheduleId", params: { scheduleId: schedule.id } })}
      className="w-full text-left px-4 sm:px-6 py-3 flex items-center gap-3 transition-none hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40 cursor-pointer"
    >
      {/* Status dot */}
      <span
        className={cn("w-2 h-2 rounded-full shrink-0", statusColors.dot)}
        title={schedule.status}
      />

      {/* Name + flow */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-foreground truncate">
            {schedule.name}
          </span>
          <Badge
            variant="outline"
            className={cn(
              "text-[10px] font-mono uppercase tracking-wide ring-1 border-transparent shrink-0",
              typeColors.bg,
              typeColors.text,
              typeColors.ring,
            )}
          >
            {schedule.type}
          </Badge>
        </div>
        <div className="text-xs text-zinc-500 truncate mt-0.5">
          <button
            onClick={(e) => {
              e.stopPropagation();
              const flowName = flowNameFromPath(schedule.flow_path);
              navigate({ to: "/flows/$flowName", params: { flowName } });
            }}
            className="text-zinc-600 hover:text-blue-400 transition-colors cursor-pointer"
          >
            {flowNameFromPath(schedule.flow_path)}
          </button>
          <span className="text-zinc-700"> · </span>
          <span>{humanCron(schedule.cron_expr, schedule.cron_description)}</span>
        </div>
      </div>

      {/* Last fired */}
      <div className="hidden md:block w-20 text-right text-xs text-zinc-500 shrink-0">
        {schedule.last_fired_at ? timeAgo(schedule.last_fired_at) : "never"}
      </div>

      {/* Last job status */}
      <div className="hidden md:flex w-24 justify-end shrink-0">
        {schedule.last_job_status ? (
          <JobStatusBadge status={schedule.last_job_status as any} />
        ) : (
          <span className="text-xs text-zinc-600">—</span>
        )}
      </div>

      {/* Actions */}
      <div
        className="flex items-center gap-0.5 shrink-0"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          onClick={() => onEdit(schedule)}
          className="rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-zinc-500/10 hover:text-foreground cursor-pointer"
          title="Edit schedule"
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
        <button
          onClick={() => onPauseResume(schedule)}
          className={cn(
            "rounded-md p-1.5 transition-colors cursor-pointer",
            schedule.status === "active"
              ? "text-zinc-500 hover:bg-amber-500/10 hover:text-amber-400"
              : "text-zinc-500 hover:bg-emerald-500/10 hover:text-emerald-400",
          )}
          title={schedule.status === "active" ? "Pause" : "Resume"}
        >
          {schedule.status === "active" ? (
            <Pause className="h-3.5 w-3.5" />
          ) : (
            <Play className="h-3.5 w-3.5" />
          )}
        </button>
        <button
          onClick={() => onTrigger(schedule.id)}
          className="rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-blue-500/10 hover:text-blue-400 cursor-pointer"
          title="Trigger now"
        >
          <Zap className="h-3.5 w-3.5" />
        </button>
        <DeleteScheduleButton schedule={schedule} onDelete={onDelete} />
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────

export function SchedulesPage() {
  const [statusFilter, setStatusFilter] = useState<ScheduleStatus | null>(null);
  const [typeFilter, setTypeFilter] = useState<ScheduleType | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<Schedule | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const queryClient = useQueryClient();

  // Register chat toggle in global nav header (same pattern as editor)
  usePanelRegister({
    chat: {
      open: chatOpen,
      toggle: () => setChatOpen((v) => !v),
    },
  });

  const { data: schedules = [], isLoading } = useSchedules(
    statusFilter ?? undefined,
    typeFilter ?? undefined,
  );
  const mutations = useScheduleMutations();

  const counts = useMemo(() => {
    const byStatus: Record<string, number> = { active: 0, paused: 0 };
    const byType: Record<string, number> = { cron: 0, poll: 0 };
    for (const s of schedules) {
      byStatus[s.status] = (byStatus[s.status] ?? 0) + 1;
      byType[s.type] = (byType[s.type] ?? 0) + 1;
    }
    return { byStatus, byType };
  }, [schedules]);

  const handlePauseResume = useCallback(
    (schedule: Schedule) => {
      if (schedule.status === "active") {
        mutations.pauseSchedule.mutate(schedule.id);
      } else {
        mutations.resumeSchedule.mutate(schedule.id);
      }
    },
    [mutations],
  );

  const handleTrigger = useCallback(
    (id: string) => {
      mutations.triggerSchedule.mutate(id);
    },
    [mutations],
  );

  const handleDelete = useCallback(
    (id: string) => {
      mutations.deleteSchedule.mutate(id);
    },
    [mutations],
  );

  const handleEdit = useCallback((schedule: Schedule) => {
    setEditingSchedule(schedule);
    setDialogOpen(true);
  }, []);

  const handleCreate = useCallback(() => {
    setEditingSchedule(null);
    setDialogOpen(true);
  }, []);

  const handleFormSubmit = useCallback(
    (form: ScheduleFormData) => {
      const payload = formDataToPayload(form);
      if (editingSchedule) {
        mutations.updateSchedule.mutate(
          { id: editingSchedule.id, payload },
          {
            onSuccess: () => setDialogOpen(false),
          },
        );
      } else {
        mutations.createSchedule.mutate(payload, {
          onSuccess: () => setDialogOpen(false),
        });
      }
    },
    [editingSchedule, mutations],
  );

  const isSubmitting = mutations.createSchedule.isPending || mutations.updateSchedule.isPending;

  return (
    <div className="flex h-full">
      {/* Main content */}
      <div className="flex h-full flex-col flex-1 min-w-0">
      {/* Header */}
      <div className="flex items-center gap-4 border-b border-border px-4 sm:px-6 py-3">
        <div className="flex items-center gap-2">
          <Clock className="h-4 w-4 text-zinc-500" />
          <h1 className="text-sm font-semibold">Schedules</h1>
          {schedules.length > 0 && (
            <span className="text-xs text-zinc-500">{schedules.length}</span>
          )}
        </div>

        <div className="flex-1" />

        {/* Status filters */}
        <div className="flex items-center gap-1">
          <FilterPill
            label={`${counts.byStatus.active ?? 0} active`}
            active={statusFilter === "active"}
            dotColor="bg-emerald-400"
            onClick={() => setStatusFilter(statusFilter === "active" ? null : "active")}
          />
          <FilterPill
            label={`${counts.byStatus.paused ?? 0} paused`}
            active={statusFilter === "paused"}
            dotColor="bg-zinc-400"
            onClick={() => setStatusFilter(statusFilter === "paused" ? null : "paused")}
          />
        </div>

        <div className="h-4 w-px bg-border" />

        {/* Type filters */}
        <div className="flex items-center gap-1">
          <FilterPill
            label="cron"
            active={typeFilter === "cron"}
            onClick={() => setTypeFilter(typeFilter === "cron" ? null : "cron")}
          />
          <FilterPill
            label="poll"
            active={typeFilter === "poll"}
            onClick={() => setTypeFilter(typeFilter === "poll" ? null : "poll")}
          />
        </div>

        <div className="h-4 w-px bg-border" />

        {/* New Schedule button */}
        <Button size="sm" onClick={handleCreate}>
          <Plus className="h-3.5 w-3.5" data-icon="inline-start" />
          New Schedule
        </Button>
      </div>

      {/* Table header */}
      <div className="flex items-center gap-3 px-4 sm:px-6 py-2 border-b border-border/50 text-[11px] uppercase tracking-wider text-zinc-500 font-medium">
        <span className="w-2 shrink-0" />
        <span className="flex-1">Name</span>
        <span className="hidden md:block w-20 text-right">Last fired</span>
        <span className="hidden md:block w-24 text-right">Last status</span>
        <span className="w-[152px] shrink-0" />
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="space-y-1 p-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full rounded-md" />
            ))}
          </div>
        ) : schedules.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 text-zinc-500 gap-2">
            <Timer className="h-8 w-8 text-zinc-400" />
            <p className="text-sm">No schedules found</p>
            <p className="text-xs text-zinc-600">
              Create one with the button above or via the CLI
            </p>
            <Button variant="outline" size="sm" className="mt-2" onClick={handleCreate}>
              <Plus className="h-3.5 w-3.5" data-icon="inline-start" />
              Create Schedule
            </Button>
          </div>
        ) : (
          <div className="divide-y divide-border/50">
            {schedules.map((schedule) => (
              <ScheduleRow
                key={schedule.id}
                schedule={schedule}
                onPauseResume={handlePauseResume}
                onTrigger={handleTrigger}
                onDelete={handleDelete}
                onEdit={handleEdit}
              />
            ))}
          </div>
        )}
      </div>

      {/* Create/Edit Dialog */}
      <ScheduleFormDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        editingSchedule={editingSchedule}
        onSubmit={handleFormSubmit}
        isSubmitting={isSubmitting}
      />
      </div>

      {/* Chat Panel */}
      {chatOpen && (
        <ScheduleChat
          onClose={() => setChatOpen(false)}
          onScheduleChanged={() => {
            queryClient.invalidateQueries({ queryKey: ["schedules"] });
          }}
        />
      )}
    </div>
  );
}
