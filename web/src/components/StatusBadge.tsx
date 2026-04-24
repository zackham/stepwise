import { Badge } from "@/components/ui/badge";
import {
  JOB_STATUS_COLORS,
  STEP_STATUS_COLORS,
  STEP_PENDING_COLORS,
  STEP_DISPLAY_COLORS,
} from "@/lib/status-colors";
import type { JobStatus, StepDisplayStatus } from "@/lib/types";
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

function pickStepColors(status: StepDisplayStatus) {
  if (status === "pending") return STEP_PENDING_COLORS;
  if (status === "escalated" || status === "stranded") {
    return STEP_DISPLAY_COLORS[status];
  }
  return STEP_STATUS_COLORS[status];
}

export function StepStatusBadge({
  status,
}: {
  status: StepDisplayStatus;
}) {
  const colors = pickStepColors(status);
  return (
    <Badge
      variant="outline"
      className={cn(
        "text-[10px] font-mono uppercase tracking-wide",
        colors.bg,
        colors.text,
        "ring-1 border-transparent",
        colors.ring,
        // Pulse while actively running OR stranded (the running process
        // is still alive but idle — signals the "live but going nowhere"
        // state visually).
        (status === "running" || status === "stranded") && "animate-pulse"
      )}
    >
      {status}
    </Badge>
  );
}
