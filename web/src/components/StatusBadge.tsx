import { Badge } from "@/components/ui/badge";
import { JOB_STATUS_COLORS, STEP_STATUS_COLORS, STEP_PENDING_COLORS } from "@/lib/status-colors";
import type { JobStatus, StepRunStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const colors = JOB_STATUS_COLORS[status];
  return (
    <Badge
      variant="outline"
      className={cn(
        "text-xs font-mono uppercase tracking-wide",
        colors.bg,
        colors.text,
        colors.ring,
        "ring-1 border-transparent",
        status === "running" && "animate-pulse"
      )}
    >
      {status}
    </Badge>
  );
}

export function StepStatusBadge({
  status,
}: {
  status: StepRunStatus | "pending";
}) {
  const colors =
    status === "pending"
      ? STEP_PENDING_COLORS
      : STEP_STATUS_COLORS[status];
  return (
    <Badge
      variant="outline"
      className={cn(
        "text-[10px] font-mono uppercase tracking-wide",
        colors.bg,
        colors.text,
        "ring-1 border-transparent",
        colors.ring,
        status === "running" && "animate-pulse"
      )}
    >
      {status}
    </Badge>
  );
}
