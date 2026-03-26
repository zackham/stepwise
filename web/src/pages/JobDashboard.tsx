import { useNavigate, useSearch } from "@tanstack/react-router";
import { useCallback, useState } from "react";
import { JobList } from "@/components/jobs/JobList";
import { JobSummaryBar } from "@/components/jobs/JobSummaryBar";
import { CreateJobDialog, type CreateJobPrefill } from "@/components/jobs/CreateJobDialog";
import { QuickLaunch } from "@/components/jobs/QuickLaunch";
import { useJobs } from "@/hooks/useStepwise";
import { Workflow } from "lucide-react";

export function JobDashboard() {
  const navigate = useNavigate();
  const { q, status } = useSearch({ from: "/jobs" });
  const { data: jobs = [] } = useJobs();

  const [editPrefill, setEditPrefill] = useState<CreateJobPrefill | undefined>();
  const [editDialogOpen, setEditDialogOpen] = useState(false);

  const setQuery = useCallback(
    (value: string) => {
      navigate({
        to: "/jobs",
        search: (prev) => ({ ...prev, q: value || undefined }),
        replace: true,
      });
    },
    [navigate],
  );

  const setStatusFilter = useCallback(
    (value: string | null) => {
      navigate({
        to: "/jobs",
        search: (prev) => ({ ...prev, status: value || undefined }),
        replace: true,
      });
    },
    [navigate],
  );

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
        <QuickLaunch
          onLaunched={(jobId) => navigate({ to: "/jobs/$jobId", params: { jobId } })}
          onEditLaunch={(prefill) => {
            setEditPrefill(prefill);
            setEditDialogOpen(true);
          }}
        />
        <div className="flex-1 overflow-hidden">
          <JobList
            selectedJobId={null}
            onSelectJob={(jobId) =>
              navigate({ to: "/jobs/$jobId", params: { jobId } })
            }
            query={q ?? ""}
            statusFilter={status ?? null}
            onQueryChange={setQuery}
            onStatusFilterChange={setStatusFilter}
          />
        </div>
      </div>
      <div className="hidden md:flex flex-1 items-center justify-center text-zinc-600">
        <div className="text-center space-y-3">
          <Workflow className="w-12 h-12 mx-auto text-zinc-700" />
          <p className="text-lg">Select a job to view details</p>
          <p className="text-sm text-zinc-700">
            Or create a new one to get started
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
