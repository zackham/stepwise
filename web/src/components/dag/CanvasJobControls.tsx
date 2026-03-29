import { Pause, Play, RotateCcw, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import type { JobStatus } from "@/lib/types";

interface CanvasJobControlsProps {
  jobStatus: JobStatus;
  onPause: () => void;
  onResume: () => void;
  onCancel: () => void;
  onRetry: () => void;
  isPausePending?: boolean;
  isResumePending?: boolean;
  isCancelPending?: boolean;
  isRetryPending?: boolean;
}

interface ActionDef {
  key: string;
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  isPending: boolean;
  className: string;
}

export function CanvasJobControls({
  jobStatus,
  onPause,
  onResume,
  onCancel,
  onRetry,
  isPausePending,
  isResumePending,
  isCancelPending,
  isRetryPending,
}: CanvasJobControlsProps) {
  const actions: ActionDef[] = [];

  if (jobStatus === "running") {
    actions.push({
      key: "pause",
      icon: <Pause className="w-3.5 h-3.5" />,
      label: "Pause",
      onClick: onPause,
      isPending: !!isPausePending,
      className: "text-amber-400 hover:bg-amber-500/15",
    });
    actions.push({
      key: "cancel",
      icon: <XCircle className="w-3.5 h-3.5" />,
      label: "Cancel",
      onClick: onCancel,
      isPending: !!isCancelPending,
      className: "text-red-400 hover:bg-red-500/15",
    });
  }

  if (jobStatus === "paused") {
    actions.push({
      key: "resume",
      icon: <Play className="w-3.5 h-3.5" />,
      label: "Resume",
      onClick: onResume,
      isPending: !!isResumePending,
      className: "text-blue-400 hover:bg-blue-500/15",
    });
    actions.push({
      key: "cancel",
      icon: <XCircle className="w-3.5 h-3.5" />,
      label: "Cancel",
      onClick: onCancel,
      isPending: !!isCancelPending,
      className: "text-red-400 hover:bg-red-500/15",
    });
  }

  if (jobStatus === "pending" || jobStatus === "staged") {
    actions.push({
      key: "start",
      icon: <Play className="w-3.5 h-3.5" />,
      label: "Start",
      onClick: onResume,
      isPending: !!isResumePending,
      className: "text-blue-400 hover:bg-blue-500/15",
    });
    actions.push({
      key: "cancel",
      icon: <XCircle className="w-3.5 h-3.5" />,
      label: "Cancel",
      onClick: onCancel,
      isPending: !!isCancelPending,
      className: "text-red-400 hover:bg-red-500/15",
    });
  }

  if (jobStatus === "completed" || jobStatus === "failed") {
    actions.push({
      key: "retry",
      icon: <RotateCcw className="w-3.5 h-3.5" />,
      label: "Retry",
      onClick: onRetry,
      isPending: !!isRetryPending,
      className: "text-blue-400 hover:bg-blue-500/15",
    });
  }

  if (jobStatus === "failed") {
    actions.push({
      key: "cancel",
      icon: <XCircle className="w-3.5 h-3.5" />,
      label: "Cancel",
      onClick: onCancel,
      isPending: !!isCancelPending,
      className: "text-red-400 hover:bg-red-500/15",
    });
  }

  if (actions.length === 0) return null;

  return (
    <div
      className="absolute top-3 right-3 z-10 flex items-center gap-0.5 bg-white/80 dark:bg-zinc-900/80 backdrop-blur-sm rounded-lg border border-zinc-300/50 dark:border-zinc-700/50 px-1 py-1 shadow-lg"
      data-capture-hide
    >
      {actions.map((action, i) => (
        <span key={action.key} className="contents">
          {i > 0 && (
            <div className="w-px h-4 bg-zinc-300/50 dark:bg-zinc-700/50" />
          )}
          <button
            onClick={(e) => {
              e.stopPropagation();
              action.onClick();
            }}
            disabled={action.isPending}
            className={cn(
              "flex items-center gap-1.5 text-xs px-2 py-1 rounded-md transition-colors disabled:opacity-50",
              action.className,
            )}
            title={action.label}
          >
            {action.icon}
            <span>{action.label}</span>
          </button>
        </span>
      ))}
    </div>
  );
}
