import { useParams, useNavigate } from "@tanstack/react-router";
import { useJob } from "@/hooks/useStepwise";
import { JobTreeView } from "@/components/jobs/JobTreeView";
import { Breadcrumb } from "@/components/layout/Breadcrumb";

export function JobTreePage() {
  const { jobId } = useParams({ from: "/jobs/$jobId/tree" });
  const navigate = useNavigate();
  const { data: job } = useJob(jobId);
  const jobName = job?.name || job?.objective || "...";

  return (
    <div className="flex flex-col h-full">
      <Breadcrumb
        segments={[
          { label: "Jobs", to: "/jobs" },
          { label: jobName, to: "/jobs/$jobId", params: { jobId } },
          { label: "Tree" },
        ]}
      />

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
