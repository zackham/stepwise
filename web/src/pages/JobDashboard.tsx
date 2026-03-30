import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { JobList } from "@/components/jobs/JobList";
import { JobSummaryBar } from "@/components/jobs/JobSummaryBar";
import { CreateJobDialog, type CreateJobPrefill } from "@/components/jobs/CreateJobDialog";
import { ActionContextProvider } from "@/components/menus/ActionContextProvider";
// QuickLaunch removed — post-1.0
import { useJobs } from "@/hooks/useStepwise";

export function JobDashboard() {
  const navigate = useNavigate();
  const { data: jobs = [] } = useJobs();

  const [editPrefill, setEditPrefill] = useState<CreateJobPrefill | undefined>();
  const [editDialogOpen, setEditDialogOpen] = useState(false);

  const handleEditDialogChange = (open: boolean) => {
    setEditDialogOpen(open);
    if (!open) setEditPrefill(undefined);
  };

  return (
    <div className="flex h-full">
      <div className="w-full md:w-72 md:border-r border-border flex flex-col md:shrink-0">
        <div className="flex items-center justify-between p-3 border-b border-border">
          <h2 className="text-sm font-semibold text-foreground">Jobs</h2>
          <CreateJobDialog
            onCreated={(jobId) => navigate({ to: "/jobs/$jobId", params: { jobId } })}
          />
        </div>
        <JobSummaryBar jobs={jobs} />
        <div className="flex-1 overflow-hidden">
          <ActionContextProvider>
            <JobList
              selectedJobId={null}
              onSelectJob={(jobId) =>
                navigate({ to: "/jobs/$jobId", params: { jobId }, search: true })
              }
            />
          </ActionContextProvider>
        </div>
      </div>
      <div className="hidden md:flex flex-1 items-center justify-center text-zinc-600">
        <div className="text-center max-w-sm space-y-3">
          <img src="/stepwise-icon-64.png" alt="Stepwise" className="w-12 h-12 mx-auto opacity-40" />
          <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">Select a job to view its DAG</p>
          <p className="text-xs text-zinc-600">
            The detail view shows the workflow graph, step statuses, and live agent output for the selected job.
          </p>
        </div>
      </div>

      {/* Edit & Run dialog (controlled, triggered from QuickLaunch) */}
      <CreateJobDialog
        open={editDialogOpen}
        onOpenChange={handleEditDialogChange}
        prefill={editPrefill}
        onCreated={(jobId) => {
          setEditDialogOpen(false);
          setEditPrefill(undefined);
          navigate({ to: "/jobs/$jobId", params: { jobId } });
        }}
      />
    </div>
  );
}
