import { useState, useMemo, useCallback, useEffect } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
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
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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

          {/* Cron expression (always shown for cron, optionally for poll) */}
          {form.type === "cron" && (
            <div className="space-y-1.5">
              <Label htmlFor="schedule-cron">Cron Expression</Label>
              <Input
                id="schedule-cron"
                value={form.cron_expr}
                onChange={(e) => update("cron_expr", e.target.value)}
                placeholder="*/5 * * * *"
                className="font-mono"
                required
              />
              {cronPreview && (
                <p className={cn(
                  "text-[11px]",
                  cronPreview === "Invalid cron expression" ? "text-red-400" : "text-zinc-500",
                )}>
                  {cronPreview}
                </p>
              )}
            </div>
          )}

          {/* Poll-specific fields */}
          {form.type === "poll" && (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="schedule-cron-poll">Cron Expression (poll check interval)</Label>
                <Input
                  id="schedule-cron-poll"
                  value={form.cron_expr}
                  onChange={(e) => update("cron_expr", e.target.value)}
                  placeholder="*/5 * * * *"
                  className="font-mono"
                />
                {cronPreview && (
                  <p className={cn(
                    "text-[11px]",
                    cronPreview === "Invalid cron expression" ? "text-red-400" : "text-zinc-500",
                  )}>
                    {cronPreview}
                  </p>
                )}
              </div>
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
          </div>

          {/* Policies + Timezone */}
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="schedule-overlap">Overlap</Label>
              <select
                id="schedule-overlap"
                value={form.overlap_policy}
                onChange={(e) => update("overlap_policy", e.target.value)}
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm dark:bg-input/30"
              >
                <option value="skip">skip</option>
                <option value="queue">queue</option>
                <option value="allow">allow</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="schedule-recovery">Recovery</Label>
              <select
                id="schedule-recovery"
                value={form.recovery_policy}
                onChange={(e) => update("recovery_policy", e.target.value)}
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm dark:bg-input/30"
              >
                <option value="skip">skip</option>
                <option value="catch_up_once">catch_up_once</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="schedule-tz">Timezone</Label>
              <Input
                id="schedule-tz"
                value={form.timezone}
                onChange={(e) => update("timezone", e.target.value)}
                placeholder="America/Los_Angeles"
              />
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

      {/* Last fired + status */}
      <div className="hidden md:flex flex-col items-end w-28 text-xs shrink-0">
        <span className="text-zinc-500">
          {schedule.last_fired_at ? timeAgo(schedule.last_fired_at) : "never"}
        </span>
        {schedule.last_job_status && (
          <span className={cn(
            "text-[10px] mt-0.5",
            schedule.last_job_status === "completed" && "text-emerald-500",
            schedule.last_job_status === "running" && "text-blue-400",
            schedule.last_job_status === "failed" && "text-red-400",
            schedule.last_job_status === "cancelled" && "text-zinc-400",
          )}>
            {schedule.last_job_status}
          </span>
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
    <div className="flex h-full flex-col">
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
        <span className="hidden md:block w-24 text-right">Last fired</span>
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
  );
}
