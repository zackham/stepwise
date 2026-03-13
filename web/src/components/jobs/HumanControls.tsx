import { useState } from "react";
import { useStepwiseMutations } from "@/hooks/useStepwise";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import type { Job } from "@/lib/types";
import {
  Play,
  Pause,
  RotateCcw,
  XCircle,
  MessageSquare,
  Zap,
  AlertTriangle,
  ShieldCheck,
} from "lucide-react";

interface HumanControlsProps {
  job: Job;
}

function isStale(job: { status: string; created_by: string; heartbeat_at: string | null }): boolean {
  if (job.status !== "running" || job.created_by === "server") return false;
  if (!job.heartbeat_at) return true;
  const age = Date.now() - new Date(job.heartbeat_at).getTime();
  return age > 60_000;
}

export function HumanControls({ job }: HumanControlsProps) {
  const mutations = useStepwiseMutations();
  const [contextDialogOpen, setContextDialogOpen] = useState(false);
  const [contextText, setContextText] = useState("");
  const stale = isStale(job);

  const handleInjectContext = () => {
    if (!contextText.trim()) return;
    mutations.injectContext.mutate(
      { jobId: job.id, context: contextText.trim() },
      {
        onSuccess: () => {
          setContextDialogOpen(false);
          setContextText("");
        },
      }
    );
  };

  return (
    <>
      <div className="flex items-center gap-2 p-3 border-b border-border bg-zinc-900/50">
        {/* Job lifecycle actions */}
        {job.status === "pending" && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => mutations.startJob.mutate(job.id)}
            disabled={mutations.startJob.isPending}
            className="border-blue-500/30 text-blue-400 hover:bg-blue-500/10"
          >
            <Play className="w-3.5 h-3.5 mr-1.5" />
            Start
          </Button>
        )}

        {job.status === "running" && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => mutations.pauseJob.mutate(job.id)}
            disabled={mutations.pauseJob.isPending}
          >
            <Pause className="w-3.5 h-3.5 mr-1.5" />
            Pause
          </Button>
        )}

        {job.status === "paused" && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => mutations.resumeJob.mutate(job.id)}
            disabled={mutations.resumeJob.isPending}
            className="border-blue-500/30 text-blue-400 hover:bg-blue-500/10"
          >
            <RotateCcw className="w-3.5 h-3.5 mr-1.5" />
            Resume
          </Button>
        )}

        {(job.status === "running" || job.status === "paused") && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => mutations.cancelJob.mutate(job.id)}
            disabled={mutations.cancelJob.isPending}
            className="border-red-500/30 text-red-400 hover:bg-red-500/10"
          >
            <XCircle className="w-3.5 h-3.5 mr-1.5" />
            Cancel
          </Button>
        )}

        {/* Stale job warning + adopt */}
        {stale && (
          <>
            <div className="flex items-center gap-1.5 text-amber-500 text-xs">
              <AlertTriangle className="w-3.5 h-3.5" />
              Owner not responding
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => mutations.adoptJob.mutate(job.id)}
              disabled={mutations.adoptJob.isPending}
              className="border-amber-500/30 text-amber-400 hover:bg-amber-500/10"
            >
              <ShieldCheck className="w-3.5 h-3.5 mr-1.5" />
              Take Over
            </Button>
          </>
        )}

        <div className="flex-1" />

        {/* Utility actions */}
        <Button
          variant="outline"
          size="sm"
          onClick={() => setContextDialogOpen(true)}
          className="text-zinc-400"
        >
          <MessageSquare className="w-3.5 h-3.5 mr-1.5" />
          Inject Context
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={() => mutations.triggerTick.mutate()}
          disabled={mutations.triggerTick.isPending}
          className="text-zinc-400"
        >
          <Zap className="w-3.5 h-3.5 mr-1.5" />
          Tick
        </Button>
      </div>

      {/* Inject Context Dialog */}
      <Dialog open={contextDialogOpen} onOpenChange={setContextDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Inject Context</DialogTitle>
            <DialogDescription>
              Add context information that will be available to future step
              executions.
            </DialogDescription>
          </DialogHeader>
          <Textarea
            value={contextText}
            onChange={(e) => setContextText(e.target.value)}
            placeholder="Enter context information..."
            className="min-h-[100px]"
          />
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setContextDialogOpen(false)}
            >
              Cancel
            </Button>
            <Button
              onClick={handleInjectContext}
              disabled={
                !contextText.trim() || mutations.injectContext.isPending
              }
            >
              Inject
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
