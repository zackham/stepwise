import { useState, useMemo, useCallback, useEffect } from "react";
import { useParams, useNavigate, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft,
  Play,
  Pause,
  Trash2,
  Zap,
  Clock,
  Terminal,
  BarChart3,
  ExternalLink,
  AlertTriangle,
  ChevronRight,
  Pencil,
  Plus,
  X,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@/components/ui/tooltip";
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
import {
  useSchedule,
  useScheduleTicks,
  useScheduleStats,
  useScheduleJobs,
  useScheduleMutations,
} from "@/hooks/useSchedules";
import type { Schedule, ScheduleTick, TickOutcome, ScheduleType } from "@/lib/schedule-types";
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

function formatTimestamp(ts: string): string {
  return new Date(ts).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
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

function formatDurationMs(ms: number | null): string {
  if (ms === null) return "-";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
}

const TICK_OUTCOME_COLORS: Record<TickOutcome, { dot: string; bg: string; text: string; label: string }> = {
  fired: {
    dot: "bg-emerald-400",
    bg: "bg-emerald-500/10",
    text: "text-emerald-400",
    label: "Fired",
  },
  skipped: {
    dot: "bg-zinc-400",
    bg: "bg-zinc-500/10",
    text: "text-zinc-400",
    label: "Skipped",
  },
  error: {
    dot: "bg-red-400",
    bg: "bg-red-500/10",
    text: "text-red-400",
    label: "Error",
  },
  overlap_skipped: {
    dot: "bg-amber-400",
    bg: "bg-amber-500/10",
    text: "text-amber-400",
    label: "Overlap",
  },
  cooldown_skipped: {
    dot: "bg-amber-400",
    bg: "bg-amber-500/10",
    text: "text-amber-400",
    label: "Cooldown",
  },
};

const STATUS_BADGE_COLORS = {
  active: {
    bg: "bg-emerald-100 dark:bg-emerald-500/10",
    text: "text-emerald-600 dark:text-emerald-400",
    ring: "ring-emerald-500/30",
  },
  paused: {
    bg: "bg-zinc-100 dark:bg-zinc-500/10",
    text: "text-zinc-500 dark:text-zinc-400",
    ring: "ring-zinc-500/30",
  },
};

const TYPE_BADGE_COLORS = {
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

// ── Tick Popover ──────────────────────────────────────────────────────

function TickDot({ tick }: { tick: ScheduleTick }) {
  const colors = TICK_OUTCOME_COLORS[tick.outcome];
  const navigate = useNavigate();

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              className={cn(
                "w-3 h-3 rounded-full shrink-0 transition-transform hover:scale-150 cursor-pointer ring-1 ring-transparent hover:ring-white/20",
                colors.dot,
              )}
            />
          }
        />
        <TooltipContent side="top" className="max-w-xs">
          <div className="space-y-1.5 text-xs">
            <div className="flex items-center gap-2">
              <span className={cn("font-medium", colors.text)}>{colors.label}</span>
              <span className="text-zinc-400">{formatTimestamp(tick.evaluated_at)}</span>
            </div>
            {tick.duration_ms !== null && (
              <div className="text-zinc-400">Duration: {formatDurationMs(tick.duration_ms)}</div>
            )}
            {tick.reason && (
              <div className="text-zinc-400">Reason: {tick.reason}</div>
            )}
            {tick.poll_output && Object.keys(tick.poll_output).length > 0 && (
              <div className="text-zinc-400">
                <pre className="mt-1 text-[10px] bg-zinc-900 rounded px-1.5 py-1 overflow-x-auto max-h-20">
                  {JSON.stringify(tick.poll_output, null, 2)}
                </pre>
              </div>
            )}
            {tick.job_id && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  navigate({ to: "/jobs/$jobId", params: { jobId: tick.job_id! } });
                }}
                className="flex items-center gap-1 text-blue-400 hover:text-blue-300 transition-colors cursor-pointer"
              >
                View job <ExternalLink className="h-2.5 w-2.5" />
              </button>
            )}
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ── Tick Timeline ─────────────────────────────────────────────────────

function TickTimeline({ ticks }: { ticks: ScheduleTick[] }) {
  const sorted = useMemo(
    () => [...ticks].sort((a, b) => new Date(a.evaluated_at).getTime() - new Date(b.evaluated_at).getTime()),
    [ticks],
  );

  if (sorted.length === 0) {
    return (
      <div className="text-xs text-zinc-500 py-4 text-center">
        No ticks recorded yet
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Dot timeline */}
      <div className="flex items-center gap-1 flex-wrap py-2">
        {sorted.map((tick) => (
          <TickDot key={tick.id} tick={tick} />
        ))}
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 text-[10px] text-zinc-500">
        {(["fired", "skipped", "error", "overlap_skipped", "cooldown_skipped"] as TickOutcome[]).map((outcome) => {
          const count = ticks.filter((t) => t.outcome === outcome).length;
          if (count === 0) return null;
          const colors = TICK_OUTCOME_COLORS[outcome];
          return (
            <div key={outcome} className="flex items-center gap-1">
              <span className={cn("w-2 h-2 rounded-full", colors.dot)} />
              <span>{colors.label} ({count})</span>
            </div>
          );
        })}
      </div>

      {/* Recent ticks table */}
      <div className="border border-border/50 rounded-md overflow-hidden">
        <div className="grid grid-cols-[auto_1fr_auto_auto] gap-x-4 px-3 py-1.5 text-[10px] uppercase tracking-wider text-zinc-500 font-medium bg-zinc-50/50 dark:bg-zinc-900/50 border-b border-border/50">
          <span>Outcome</span>
          <span>Time</span>
          <span>Duration</span>
          <span>Job</span>
        </div>
        <div className="max-h-64 overflow-y-auto divide-y divide-border/30">
          {[...sorted].reverse().slice(0, 25).map((tick) => {
            const colors = TICK_OUTCOME_COLORS[tick.outcome];
            return (
              <div
                key={tick.id}
                className="grid grid-cols-[auto_1fr_auto_auto] gap-x-4 px-3 py-2 text-xs items-center"
              >
                <div className="flex items-center gap-1.5">
                  <span className={cn("w-1.5 h-1.5 rounded-full", colors.dot)} />
                  <span className={cn("text-[11px]", colors.text)}>{colors.label}</span>
                </div>
                <span className="text-zinc-500 truncate">{formatTimestamp(tick.evaluated_at)}</span>
                <span className="text-zinc-500 tabular-nums">{formatDurationMs(tick.duration_ms)}</span>
                <span>
                  {tick.job_id ? (
                    <Link
                      to="/jobs/$jobId"
                      params={{ jobId: tick.job_id }}
                      className="text-blue-400 hover:text-blue-300 font-mono text-[11px] transition-colors"
                    >
                      {tick.job_id.slice(0, 8)}
                    </Link>
                  ) : (
                    <span className="text-zinc-600">-</span>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── Stat Card ─────────────────────────────────────────────────────────

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wider text-zinc-500 font-medium">{label}</span>
      <span className="text-lg font-semibold text-foreground tabular-nums">{value}</span>
      {sub && <span className="text-[11px] text-zinc-500">{sub}</span>}
    </div>
  );
}

// ── Launched Jobs ─────────────────────────────────────────────────────

function LaunchedJobs({ scheduleId }: { scheduleId: string }) {
  const { data: jobs, isLoading } = useScheduleJobs(scheduleId);
  const navigate = useNavigate();

  if (isLoading) {
    return <Skeleton className="h-32 w-full rounded-md" />;
  }

  if (!jobs || jobs.length === 0) {
    return (
      <div className="text-xs text-zinc-500 py-4 text-center">
        No jobs launched yet
      </div>
    );
  }

  const JOB_DOT_COLOR: Record<string, string> = {
    running: "bg-blue-400",
    completed: "bg-emerald-400",
    failed: "bg-red-400",
    cancelled: "bg-zinc-500",
    pending: "bg-zinc-400",
    paused: "bg-amber-400",
  };

  return (
    <div className="divide-y divide-border/30">
      {jobs.map((job) => (
        <button
          key={job.job_id}
          onClick={() => navigate({ to: "/jobs/$jobId", params: { jobId: job.job_id } })}
          className="flex items-center gap-3 w-full px-3 py-2 text-left hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40 transition-colors cursor-pointer"
        >
          <span className={cn("w-1.5 h-1.5 rounded-full shrink-0", JOB_DOT_COLOR[job.status] ?? "bg-zinc-400")} />
          <div className="flex-1 min-w-0">
            <span className="text-sm text-foreground truncate block">
              {job.name || job.job_id.slice(0, 12)}
            </span>
            <span className="text-[11px] text-zinc-500">{timeAgo(job.created_at)}</span>
          </div>
          <Badge
            variant="outline"
            className="text-[10px] font-mono uppercase tracking-wide ring-1 border-transparent shrink-0"
          >
            {job.status}
          </Badge>
          <ChevronRight className="h-3.5 w-3.5 text-zinc-500" />
        </button>
      ))}
    </div>
  );
}

// ── Edit Form Dialog ─────────────────────────────────────────────────

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
    if (form.cooldown_seconds > 0) payload.cooldown_seconds = form.cooldown_seconds;
    if (form.cron_expr) payload.cron_expr = form.cron_expr;
  }
  if (form.job_name_template) payload.job_name_template = form.job_name_template;
  const inputs: Record<string, unknown> = {};
  for (const { key, value } of form.job_inputs) {
    if (key.trim()) {
      try { inputs[key.trim()] = JSON.parse(value); } catch { inputs[key.trim()] = value; }
    }
  }
  if (Object.keys(inputs).length > 0) payload.job_inputs = inputs;
  return payload;
}

function EditScheduleDialog({
  open,
  onOpenChange,
  schedule,
  onSubmit,
  isSubmitting,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  schedule: Schedule;
  onSubmit: (form: ScheduleFormData) => void;
  isSubmitting: boolean;
}) {
  const [form, setForm] = useState<ScheduleFormData>(() => scheduleToFormData(schedule));
  const [flowSearch, setFlowSearch] = useState("");
  const [flowDropdownOpen, setFlowDropdownOpen] = useState(false);

  const { data: flows = [] } = useQuery({
    queryKey: ["localFlows"],
    queryFn: fetchLocalFlows,
  });

  useEffect(() => {
    if (open) {
      setForm(scheduleToFormData(schedule));
      setFlowSearch("");
      setFlowDropdownOpen(false);
    }
  }, [open, schedule]);

  const update = <K extends keyof ScheduleFormData>(key: K, value: ScheduleFormData[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const cronPreview = useMemo(() => {
    if (!form.cron_expr) return "";
    try { return cronstrue.toString(form.cron_expr, { use24HourTimeFormat: false }); }
    catch { return "Invalid cron expression"; }
  }, [form.cron_expr]);

  const filteredFlows = useMemo(() => {
    if (!flowSearch) return flows;
    const q = flowSearch.toLowerCase();
    return flows.filter((f: LocalFlow) =>
      f.name.toLowerCase().includes(q) || f.path.toLowerCase().includes(q),
    );
  }, [flows, flowSearch]);

  const selectedFlowName = useMemo(() => {
    if (!form.flow_path) return "";
    const found = flows.find((f: LocalFlow) => f.path === form.flow_path);
    return found?.name || flowNameFromPath(form.flow_path);
  }, [form.flow_path, flows]);

  const isValid = form.name.trim() !== "" && form.flow_path.trim() !== "" &&
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
          <DialogTitle>Edit Schedule</DialogTitle>
          <DialogDescription>Update the schedule configuration.</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="edit-name">Name</Label>
            <Input id="edit-name" value={form.name} onChange={(e) => update("name", e.target.value)} required />
          </div>
          <div className="space-y-1.5">
            <Label>Type</Label>
            <div className="flex gap-1">
              {(["cron", "poll"] as const).map((t) => (
                <button key={t} type="button" onClick={() => update("type", t)}
                  className={cn("flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-md border transition-colors cursor-pointer",
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
          <div className="space-y-1.5">
            <Label>Flow</Label>
            <div className="relative">
              <Input
                value={flowDropdownOpen ? flowSearch : (selectedFlowName || form.flow_path)}
                onChange={(e) => { setFlowSearch(e.target.value); if (!flowDropdownOpen) setFlowDropdownOpen(true); }}
                onFocus={() => setFlowDropdownOpen(true)}
                placeholder="Search flows..."
                required
              />
              {flowDropdownOpen && (
                <>
                  <div className="fixed inset-0 z-[40]" onClick={() => setFlowDropdownOpen(false)} />
                  <div className="absolute z-[50] top-full left-0 right-0 mt-1 bg-popover border border-border rounded-lg shadow-lg max-h-48 overflow-y-auto">
                    {filteredFlows.length === 0 ? (
                      <div className="px-3 py-2 text-xs text-zinc-500">No flows found</div>
                    ) : filteredFlows.map((f: LocalFlow) => (
                      <button key={f.path} type="button"
                        onClick={() => { update("flow_path", f.path); setFlowSearch(""); setFlowDropdownOpen(false); }}
                        className={cn("w-full text-left px-3 py-2 text-xs hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer",
                          f.path === form.flow_path && "bg-zinc-100 dark:bg-zinc-800")}
                      >
                        <div className="font-medium text-foreground">{f.name}</div>
                        <div className="text-zinc-600 font-mono text-[10px] mt-0.5">{f.path}</div>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
          {form.type === "cron" && (
            <div className="space-y-1.5">
              <Label htmlFor="edit-cron">Cron Expression</Label>
              <Input id="edit-cron" value={form.cron_expr} onChange={(e) => update("cron_expr", e.target.value)} className="font-mono" required />
              {cronPreview && <p className={cn("text-[11px]", cronPreview === "Invalid cron expression" ? "text-red-400" : "text-zinc-500")}>{cronPreview}</p>}
            </div>
          )}
          {form.type === "poll" && (
            <>
              <div className="space-y-1.5">
                <Label htmlFor="edit-cron-poll">Cron Expression (poll interval)</Label>
                <Input id="edit-cron-poll" value={form.cron_expr} onChange={(e) => update("cron_expr", e.target.value)} className="font-mono" />
                {cronPreview && <p className={cn("text-[11px]", cronPreview === "Invalid cron expression" ? "text-red-400" : "text-zinc-500")}>{cronPreview}</p>}
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="edit-poll-cmd">Poll Command</Label>
                <Textarea id="edit-poll-cmd" value={form.poll_command} onChange={(e) => update("poll_command", e.target.value)} rows={2} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="edit-poll-timeout">Poll Timeout (s)</Label>
                  <Input id="edit-poll-timeout" type="number" value={form.poll_timeout_seconds} onChange={(e) => update("poll_timeout_seconds", Number(e.target.value))} min={1} />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="edit-cooldown">Cooldown (s)</Label>
                  <Input id="edit-cooldown" type="number" value={form.cooldown_seconds} onChange={(e) => update("cooldown_seconds", Number(e.target.value))} min={0} />
                </div>
              </div>
            </>
          )}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <Label>Job Inputs</Label>
              <button type="button" onClick={() => update("job_inputs", [...form.job_inputs, { key: "", value: "" }])}
                className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-foreground transition-colors cursor-pointer">
                <Plus className="h-3 w-3" /> Add
              </button>
            </div>
            {form.job_inputs.map((input, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <Input value={input.key} onChange={(e) => { const next = [...form.job_inputs]; next[i] = { ...next[i], key: e.target.value }; update("job_inputs", next); }} placeholder="key" className="flex-1 font-mono text-xs" />
                <span className="text-zinc-500 text-xs">=</span>
                <Input value={input.value} onChange={(e) => { const next = [...form.job_inputs]; next[i] = { ...next[i], value: e.target.value }; update("job_inputs", next); }} placeholder="value" className="flex-1 font-mono text-xs" />
                <button type="button" onClick={() => update("job_inputs", form.job_inputs.filter((_, idx) => idx !== i))}
                  className="p-1 text-zinc-500 hover:text-red-400 transition-colors cursor-pointer shrink-0"><X className="h-3 w-3" /></button>
              </div>
            ))}
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="edit-job-name">Job Name Template</Label>
            <Input id="edit-job-name" value={form.job_name_template} onChange={(e) => update("job_name_template", e.target.value)} />
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="edit-overlap">Overlap</Label>
              <select id="edit-overlap" value={form.overlap_policy} onChange={(e) => update("overlap_policy", e.target.value)}
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm dark:bg-input/30">
                <option value="skip">skip</option>
                <option value="queue">queue</option>
                <option value="allow">allow</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="edit-recovery">Recovery</Label>
              <select id="edit-recovery" value={form.recovery_policy} onChange={(e) => update("recovery_policy", e.target.value)}
                className="h-8 w-full rounded-lg border border-input bg-transparent px-2 text-sm dark:bg-input/30">
                <option value="skip">skip</option>
                <option value="catch_up_once">catch_up_once</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="edit-tz">Timezone</Label>
              <Input id="edit-tz" value={form.timezone} onChange={(e) => update("timezone", e.target.value)} />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!isValid || isSubmitting}>
              {isSubmitting && <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5" />}
              Save Changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────

export function ScheduleDetailPage() {
  const { scheduleId } = useParams({ from: "/schedules/$scheduleId" });
  const navigate = useNavigate();
  const { data: schedule, isLoading } = useSchedule(scheduleId);
  const { data: ticks = [] } = useScheduleTicks(scheduleId);
  const { data: stats } = useScheduleStats(scheduleId);
  const mutations = useScheduleMutations();
  const [editOpen, setEditOpen] = useState(false);

  if (isLoading) {
    return (
      <div className="p-6 space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (!schedule) {
    return (
      <div className="flex flex-col items-center justify-center h-64 text-zinc-500 gap-2">
        <AlertTriangle className="h-8 w-8 text-zinc-400" />
        <p className="text-sm">Schedule not found</p>
        <button
          onClick={() => navigate({ to: "/schedules" })}
          className="text-xs text-blue-400 hover:text-blue-300 transition-colors cursor-pointer"
        >
          Back to schedules
        </button>
      </div>
    );
  }

  const statusColors = STATUS_BADGE_COLORS[schedule.status];
  const typeColors = TYPE_BADGE_COLORS[schedule.type];
  const flowName = flowNameFromPath(schedule.flow_path);

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Header */}
      <div className="border-b border-border px-4 sm:px-6 py-4">
        <div className="flex items-center gap-3 mb-3">
          <button
            onClick={() => navigate({ to: "/schedules" })}
            className="rounded-md p-1.5 text-zinc-500 hover:bg-zinc-200/50 hover:text-foreground dark:hover:bg-zinc-800/50 transition-colors cursor-pointer"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <h1 className="text-lg font-semibold text-foreground truncate">{schedule.name}</h1>
          <Badge
            variant="outline"
            className={cn(
              "text-xs font-mono uppercase tracking-wide ring-1 border-transparent",
              statusColors.bg,
              statusColors.text,
              statusColors.ring,
            )}
          >
            {schedule.status}
          </Badge>
          <Badge
            variant="outline"
            className={cn(
              "text-[10px] font-mono uppercase tracking-wide ring-1 border-transparent",
              typeColors.bg,
              typeColors.text,
              typeColors.ring,
            )}
          >
            {schedule.type}
          </Badge>
          <div className="flex-1" />

          {/* Actions */}
          <div className="flex items-center gap-1">
            <button
              onClick={() => setEditOpen(true)}
              className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-zinc-400 hover:bg-zinc-500/10 hover:text-foreground transition-colors cursor-pointer"
            >
              <Pencil className="h-3.5 w-3.5" />
              Edit
            </button>
            <button
              onClick={() => {
                if (schedule.status === "active") {
                  mutations.pauseSchedule.mutate(schedule.id);
                } else {
                  mutations.resumeSchedule.mutate(schedule.id);
                }
              }}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer",
                schedule.status === "active"
                  ? "text-amber-400 hover:bg-amber-500/10"
                  : "text-emerald-400 hover:bg-emerald-500/10",
              )}
            >
              {schedule.status === "active" ? (
                <>
                  <Pause className="h-3.5 w-3.5" />
                  Pause
                </>
              ) : (
                <>
                  <Play className="h-3.5 w-3.5" />
                  Resume
                </>
              )}
            </button>
            <button
              onClick={() => mutations.triggerSchedule.mutate(schedule.id)}
              className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-blue-400 hover:bg-blue-500/10 transition-colors cursor-pointer"
            >
              <Zap className="h-3.5 w-3.5" />
              Trigger Now
            </button>
            <AlertDialog>
              <AlertDialogTrigger
                render={
                  <button className="flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium text-red-400 hover:bg-red-500/10 transition-colors cursor-pointer" />
                }
              >
                <Trash2 className="h-3.5 w-3.5" />
                Delete
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
                    onClick={() => {
                      mutations.deleteSchedule.mutate(schedule.id, {
                        onSuccess: () => navigate({ to: "/schedules" }),
                      });
                    }}
                  >
                    Delete
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 p-4 sm:p-6">
          {/* Config Card */}
          <div className="rounded-lg border border-border p-4 space-y-3">
            <div className="flex items-center gap-2 text-sm font-medium text-foreground">
              <Clock className="h-4 w-4 text-zinc-500" />
              Configuration
            </div>
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <span className="text-zinc-500 block mb-0.5">Flow</span>
                <Link
                  to="/flows/$flowName"
                  params={{ flowName }}
                  className="text-blue-400 hover:text-blue-300 transition-colors font-medium"
                >
                  {flowName}
                </Link>
              </div>
              <div>
                <span className="text-zinc-500 block mb-0.5">Timezone</span>
                <span className="text-foreground">{schedule.timezone}</span>
              </div>
              <div className="col-span-2">
                <span className="text-zinc-500 block mb-0.5">Schedule</span>
                <span className="text-foreground">{humanCron(schedule.cron_expr, schedule.cron_description)}</span>
                <code className="ml-2 text-[11px] text-zinc-500 bg-zinc-100 dark:bg-zinc-800 px-1.5 py-0.5 rounded">
                  {schedule.cron_expr}
                </code>
              </div>
              {schedule.poll_command && (
                <div className="col-span-2">
                  <span className="text-zinc-500 block mb-0.5">Poll command</span>
                  <pre className="text-[11px] bg-zinc-100 dark:bg-zinc-800 px-2 py-1.5 rounded font-mono overflow-x-auto">
                    {schedule.poll_command}
                  </pre>
                </div>
              )}
              <div>
                <span className="text-zinc-500 block mb-0.5">Overlap policy</span>
                <span className="text-foreground">{schedule.overlap_policy}</span>
              </div>
              <div>
                <span className="text-zinc-500 block mb-0.5">Recovery policy</span>
                <span className="text-foreground">{schedule.recovery_policy}</span>
              </div>
              {schedule.cooldown_seconds !== null && (
                <div>
                  <span className="text-zinc-500 block mb-0.5">Cooldown</span>
                  <span className="text-foreground">{schedule.cooldown_seconds}s</span>
                </div>
              )}
              {schedule.paused_at && (
                <div>
                  <span className="text-zinc-500 block mb-0.5">Paused at</span>
                  <span className="text-foreground">{formatTimestamp(schedule.paused_at)}</span>
                </div>
              )}
            </div>
          </div>

          {/* Stats Card */}
          <div className="rounded-lg border border-border p-4 space-y-3">
            <div className="flex items-center gap-2 text-sm font-medium text-foreground">
              <BarChart3 className="h-4 w-4 text-zinc-500" />
              Statistics
            </div>
            {stats ? (
              <div className="grid grid-cols-3 gap-4">
                <StatCard label="Total fires" value={String(stats.total_fires)} sub={`of ${stats.total_ticks} ticks`} />
                <StatCard
                  label="Fire rate"
                  value={`${(stats.fire_rate * 100).toFixed(0)}%`}
                />
                <StatCard
                  label="Avg duration"
                  value={formatDurationMs(stats.avg_check_duration_ms)}
                />
                <StatCard
                  label="Last fired"
                  value={stats.last_fired_at ? timeAgo(stats.last_fired_at) : "never"}
                />
                <StatCard
                  label="Consec. errors"
                  value={String(stats.consecutive_errors)}
                />
                <StatCard
                  label="Consec. skips"
                  value={String(stats.consecutive_skips)}
                />
              </div>
            ) : (
              <div className="grid grid-cols-3 gap-4">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-16 rounded-md" />
                ))}
              </div>
            )}
          </div>

          {/* Tick Timeline */}
          <div className="rounded-lg border border-border p-4 space-y-3 lg:col-span-2">
            <div className="flex items-center gap-2 text-sm font-medium text-foreground">
              <Terminal className="h-4 w-4 text-zinc-500" />
              Tick Timeline
            </div>
            <TickTimeline ticks={ticks} />
          </div>

          {/* Launched Jobs */}
          <div className="rounded-lg border border-border overflow-hidden lg:col-span-2">
            <div className="flex items-center gap-2 text-sm font-medium text-foreground px-4 py-3 border-b border-border/50">
              <ExternalLink className="h-4 w-4 text-zinc-500" />
              Launched Jobs
            </div>
            <LaunchedJobs scheduleId={scheduleId} />
          </div>
        </div>
      </div>

      {/* Edit Dialog */}
      <EditScheduleDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        schedule={schedule}
        onSubmit={(form) => {
          mutations.updateSchedule.mutate(
            { id: schedule.id, payload: formDataToPayload(form) },
            { onSuccess: () => setEditOpen(false) },
          );
        }}
        isSubmitting={mutations.updateSchedule.isPending}
      />
    </div>
  );
}
