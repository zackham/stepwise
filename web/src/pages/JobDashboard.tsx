import { useNavigate } from "@tanstack/react-router";
import { JobList } from "@/components/jobs/JobList";
import { CreateJobDialog } from "@/components/jobs/CreateJobDialog";
import { Workflow } from "lucide-react";

export function JobDashboard() {
  const navigate = useNavigate();

  return (
    <div className="flex h-full">
      <div className="w-72 border-r border-border flex flex-col shrink-0">
        <div className="flex items-center justify-between p-3 border-b border-border">
          <h2 className="text-sm font-semibold text-foreground">Jobs</h2>
          <CreateJobDialog
            onCreated={(jobId) => navigate({ to: "/jobs/$jobId", params: { jobId } })}
          />
        </div>
        <div className="flex-1 overflow-hidden">
          <JobList
            selectedJobId={null}
            onSelectJob={(jobId) =>
              navigate({ to: "/jobs/$jobId", params: { jobId } })
            }
          />
        </div>
      </div>
      <div className="flex-1 flex items-center justify-center text-zinc-600">
        <div className="text-center space-y-3">
          <Workflow className="w-12 h-12 mx-auto text-zinc-700" />
          <p className="text-lg">Select a job to view details</p>
          <p className="text-sm text-zinc-700">
            Or create a new one to get started
          </p>
        </div>
      </div>
    </div>
  );
}
