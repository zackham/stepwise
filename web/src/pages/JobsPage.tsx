import { useSearch, useNavigate } from "@tanstack/react-router";
import { JobDashboard } from "./JobDashboard";
import { CanvasPage } from "./CanvasPage";
import { List, LayoutGrid } from "lucide-react";
import { cn } from "@/lib/utils";

const STORAGE_KEY = "stepwise-job-view";

function getStoredView(): "list" | "grid" {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "grid" ? "grid" : "list";
}

export function JobsPage() {
  const searchParams = useSearch({ from: "/jobs" });
  const navigate = useNavigate();
  const viewMode = searchParams.view_mode ?? getStoredView();

  const setViewMode = (mode: "list" | "grid") => {
    localStorage.setItem(STORAGE_KEY, mode);
    navigate({
      search: (prev: Record<string, unknown>) => ({
        ...prev,
        view_mode: mode === "list" ? undefined : mode,
      }),
      replace: true,
    });
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center px-4 py-2 border-b border-border shrink-0">
        <div className="flex items-center gap-1 rounded-lg border border-border p-0.5 bg-zinc-100/50 dark:bg-zinc-900/50">
          <button
            onClick={() => setViewMode("list")}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors",
              viewMode === "list"
                ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                : "text-zinc-500 hover:text-foreground"
            )}
          >
            <List className="w-3.5 h-3.5" />
            List
          </button>
          <button
            onClick={() => setViewMode("grid")}
            className={cn(
              "flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors",
              viewMode === "grid"
                ? "bg-white dark:bg-zinc-800 text-foreground shadow-sm"
                : "text-zinc-500 hover:text-foreground"
            )}
          >
            <LayoutGrid className="w-3.5 h-3.5" />
            Grid
          </button>
        </div>
      </div>
      <div className="flex-1 min-h-0">
        {viewMode === "grid" ? <CanvasPage /> : <JobDashboard />}
      </div>
    </div>
  );
}
