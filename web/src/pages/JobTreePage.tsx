import { useParams, useNavigate, Link } from "@tanstack/react-router";
import { useJob } from "@/hooks/useStepwise";
import { JobTreeView } from "@/components/jobs/JobTreeView";
import { JobStatusBadge } from "@/components/StatusBadge";
import { ArrowLeft } from "lucide-react";

export function JobTreePage() {
  const { jobId } = useParams({ from: "/jobs/$jobId/tree" });
  const navigate = useNavigate();
  const { data: job } = useJob(jobId);

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-3 px-4 py-2 border-b border-border bg-zinc-950/30">
        <Link
          to="/jobs/$jobId"
          params={{ jobId }}
          className="text-zinc-500 hover:text-foreground"
        >
          <ArrowLeft className="w-4 h-4" />
        </Link>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold truncate text-foreground">
              {job?.objective || "..."} — Job Tree
            </h2>
            {job && <JobStatusBadge status={job.status} />}
          </div>
          <div className="text-[10px] font-mono text-zinc-600 mt-0.5">
            {jobId}
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        <JobTreeView
          jobId={jobId}
          onNavigateToJob={(id) =>
            navigate({ to: "/jobs/$jobId", params: { jobId: id } })
          }
        />
      </div>
    </div>
  );
}
