import { Outlet, Link, useLocation } from "@tanstack/react-router";
import { useStepwiseWebSocket } from "@/hooks/useStepwiseWebSocket";
import { useEngineStatus } from "@/hooks/useStepwise";
import { LayoutGrid, FileCode, Settings2, Zap, FolderOpen } from "lucide-react";
import { cn } from "@/lib/utils";

export function AppLayout() {
  useStepwiseWebSocket();
  const location = useLocation();
  const { data: status } = useEngineStatus();

  const currentPath = location.pathname;
  const isJobsActive =
    currentPath === "/jobs" || currentPath.startsWith("/jobs/");
  const isFlowsActive = currentPath.startsWith("/flows");
  const isSettingsActive = currentPath.startsWith("/settings");

  return (
    <div className="h-screen flex flex-col bg-background text-foreground dark">
      {/* Top nav */}
      <header className="h-12 border-b border-border flex items-center px-4 gap-6 shrink-0 bg-zinc-950/80">
        <Link to="/jobs" className="flex items-center gap-2">
          <img src="/logo.png" alt="Stepwise" className="w-5 h-5" />
          <span className="font-semibold text-sm tracking-tight">
            Stepwise
          </span>
        </Link>
        {status?.cwd && (
          <div
            className="hidden md:flex items-center gap-1.5 text-xs text-zinc-600 font-mono truncate max-w-48"
            title={status.cwd}
          >
            <FolderOpen className="w-3 h-3 shrink-0" />
            <span className="truncate">{status.cwd.split("/").pop() || status.cwd}</span>
          </div>
        )}

        <nav className="flex items-center gap-1 ml-auto md:ml-4">
          <Link
            to="/jobs"
            className={cn(
              "flex items-center justify-center min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 px-2 md:px-3 py-1.5 text-sm rounded-md transition-colors",
              isJobsActive
                ? "bg-zinc-800 text-foreground"
                : "text-zinc-500 hover:text-foreground hover:bg-zinc-800/50"
            )}
          >
            <LayoutGrid className="w-4 h-4 md:w-3.5 md:h-3.5 md:mr-1.5" />
            <span className="hidden md:inline">Jobs</span>
          </Link>
          <Link
            to="/flows"
            className={cn(
              "flex items-center justify-center min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 px-2 md:px-3 py-1.5 text-sm rounded-md transition-colors",
              isFlowsActive
                ? "bg-zinc-800 text-foreground"
                : "text-zinc-500 hover:text-foreground hover:bg-zinc-800/50"
            )}
          >
            <FileCode className="w-4 h-4 md:w-3.5 md:h-3.5 md:mr-1.5" />
            <span className="hidden md:inline">Flows</span>
          </Link>
          <Link
            to="/settings"
            className={cn(
              "flex items-center justify-center min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 px-2 md:px-3 py-1.5 text-sm rounded-md transition-colors",
              isSettingsActive
                ? "bg-zinc-800 text-foreground"
                : "text-zinc-500 hover:text-foreground hover:bg-zinc-800/50"
            )}
          >
            <Settings2 className="w-4 h-4 md:w-3.5 md:h-3.5 md:mr-1.5" />
            <span className="hidden md:inline">Settings</span>
          </Link>
        </nav>

        <div className="flex-1" />

        {/* Engine status */}
        {status && (
          <div className="flex items-center gap-3 text-xs text-zinc-500">
            <div className="hidden md:flex items-center gap-1.5">
              <Zap className="w-3 h-3" />
              <span>
                {status.active_jobs} active / {status.total_jobs} total
              </span>
            </div>
            <div className="flex items-center gap-1">
              {status.active_jobs > 0 && (
                <span className="relative flex h-2 w-2">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
                  <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500" />
                </span>
              )}
            </div>
          </div>
        )}
      </header>

      {/* Main content */}
      <main className="flex-1 overflow-hidden">
        <Outlet />
      </main>
    </div>
  );
}
