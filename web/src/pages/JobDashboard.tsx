import { useNavigate, useSearch } from "@tanstack/react-router";
import { useCallback } from "react";
import { JobList } from "@/components/jobs/JobList";
import { CreateJobDialog } from "@/components/jobs/CreateJobDialog";
import { Workflow } from "lucide-react";

export function JobDashboard() {
  const navigate = useNavigate();
  const { q, status } = useSearch({ from: "/jobs" });

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

  return (
    <div className="flex h-full">
      <div className="w-full md:w-72 md:border-r border-border flex flex-col md:shrink-0">
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
    </div>
  );
}
