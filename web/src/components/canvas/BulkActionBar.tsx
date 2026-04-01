import { useState, useCallback } from "react";
import { Archive, Ban, Trash2, RotateCcw, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { ConfirmDialog } from "@/components/menus/ConfirmDialog";
import { useStepwiseMutations } from "@/hooks/useStepwise";
import { toast } from "sonner";
import type { Job } from "@/lib/types";

interface BulkActionBarProps {
  selectedIds: Set<string>;
  jobs: Job[];
  onClearSelection: () => void;
}

type ConfirmAction = "cancel" | "delete" | null;

export function BulkActionBar({ selectedIds, jobs, onClearSelection }: BulkActionBarProps) {
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const { archiveJobs, bulkDeleteJobs, cancelJob, resetJob } = useStepwiseMutations();

  const count = selectedIds.size;
  const selectedJobs = jobs.filter((j) => selectedIds.has(j.id));
  const cancellableCount = selectedJobs.filter((j) => j.status === "running" || j.status === "pending" || j.status === "paused" || j.status === "staged").length;
  const retryableCount = selectedJobs.filter((j) => j.status === "failed" || j.status === "cancelled").length;

  const handleArchive = useCallback(async () => {
    const ids = Array.from(selectedIds);
    archiveJobs.mutate(ids, {
      onSuccess: () => onClearSelection(),
    });
  }, [selectedIds, archiveJobs, onClearSelection]);

  const handleCancel = useCallback(async () => {
    setIsProcessing(true);
    const toCancel = selectedJobs.filter(
      (j) => j.status === "running" || j.status === "pending" || j.status === "paused" || j.status === "staged"
    );
    try {
      await Promise.all(toCancel.map((j) => cancelJob.mutateAsync(j.id)));
      toast.success(`Cancelled ${toCancel.length} job(s)`);
      onClearSelection();
    } catch (error) {
      toast.error("Some cancellations failed", { description: (error as Error).message });
    } finally {
      setIsProcessing(false);
      setConfirmAction(null);
    }
  }, [selectedJobs, cancelJob, onClearSelection]);

  const handleDelete = useCallback(async () => {
    const ids = Array.from(selectedIds);
    bulkDeleteJobs.mutate(ids, {
      onSuccess: () => {
        onClearSelection();
        setConfirmAction(null);
      },
      onError: () => setConfirmAction(null),
    });
  }, [selectedIds, bulkDeleteJobs, onClearSelection]);

  const handleRetry = useCallback(async () => {
    setIsProcessing(true);
    const toRetry = selectedJobs.filter(
      (j) => j.status === "failed" || j.status === "cancelled"
    );
    try {
      await Promise.all(toRetry.map((j) => resetJob.mutateAsync(j.id)));
      toast.success(`Reset ${toRetry.length} job(s) for retry`);
      onClearSelection();
    } catch (error) {
      toast.error("Some retries failed", { description: (error as Error).message });
    } finally {
      setIsProcessing(false);
    }
  }, [selectedJobs, resetJob, onClearSelection]);

  if (count === 0) return null;

  return (
    <>
      <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50">
        <div className="flex items-center gap-3 px-4 py-2.5 rounded-xl bg-zinc-900/95 backdrop-blur border border-zinc-700 shadow-2xl">
          <span className="text-sm font-medium text-zinc-200 whitespace-nowrap">
            {count} selected
          </span>

          <div className="w-px h-5 bg-zinc-700" />

          <button
            onClick={handleArchive}
            disabled={isProcessing}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-md transition-colors",
              "text-zinc-300 hover:text-white hover:bg-zinc-800",
              isProcessing && "opacity-50 pointer-events-none",
            )}
          >
            <Archive className="w-3.5 h-3.5" />
            Archive
          </button>

          <button
            onClick={() => setConfirmAction("cancel")}
            disabled={isProcessing || cancellableCount === 0}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-md transition-colors",
              "text-zinc-300 hover:text-white hover:bg-zinc-800",
              (isProcessing || cancellableCount === 0) && "opacity-50 pointer-events-none",
            )}
          >
            <Ban className="w-3.5 h-3.5" />
            Cancel{cancellableCount > 0 && ` (${cancellableCount})`}
          </button>

          <button
            onClick={() => setConfirmAction("delete")}
            disabled={isProcessing}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-md transition-colors",
              "text-red-400 hover:text-red-300 hover:bg-red-950/50",
              isProcessing && "opacity-50 pointer-events-none",
            )}
          >
            <Trash2 className="w-3.5 h-3.5" />
            Delete
          </button>

          <button
            onClick={handleRetry}
            disabled={isProcessing || retryableCount === 0}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-md transition-colors",
              "text-zinc-300 hover:text-white hover:bg-zinc-800",
              (isProcessing || retryableCount === 0) && "opacity-50 pointer-events-none",
            )}
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Retry{retryableCount > 0 && ` (${retryableCount})`}
          </button>

          <div className="w-px h-5 bg-zinc-700" />

          <button
            onClick={onClearSelection}
            className="p-1 rounded hover:bg-zinc-800 text-zinc-400 hover:text-zinc-200 transition-colors"
            title="Deselect all"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      <ConfirmDialog
        open={confirmAction === "cancel"}
        title="Cancel jobs?"
        description={`This will cancel ${cancellableCount} active job(s). They can be retried later.`}
        confirmLabel="Cancel jobs"
        variant="destructive"
        onConfirm={handleCancel}
        onCancel={() => setConfirmAction(null)}
      />

      <ConfirmDialog
        open={confirmAction === "delete"}
        title="Delete jobs?"
        description={`This will permanently delete ${count} job(s) and all their runs. This cannot be undone.`}
        confirmLabel="Delete"
        variant="destructive"
        onConfirm={handleDelete}
        onCancel={() => setConfirmAction(null)}
      />
    </>
  );
}
