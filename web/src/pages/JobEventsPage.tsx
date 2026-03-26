import { useParams } from "@tanstack/react-router";
import { useJob } from "@/hooks/useStepwise";
import { EventLog } from "@/components/events/EventLog";
import { Breadcrumb } from "@/components/layout/Breadcrumb";

export function JobEventsPage() {
  const { jobId } = useParams({ from: "/jobs/$jobId/events" });
  const { data: job } = useJob(jobId);
  const jobName = job?.name || job?.objective || "...";

  return (
    <div className="flex flex-col h-full">
      <Breadcrumb
        segments={[
          { label: "Jobs", to: "/jobs" },
          { label: jobName, to: "/jobs/$jobId", params: { jobId } },
          { label: "Events" },
        ]}
      />

      <div className="flex-1 overflow-hidden">
        <EventLog jobId={jobId} />
      </div>
    </div>
  );
}
