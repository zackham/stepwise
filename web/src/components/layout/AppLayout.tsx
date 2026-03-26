import { useState, useEffect, useCallback } from "react";
import { Outlet, Link, useLocation } from "@tanstack/react-router";
import { ErrorBoundary, type FallbackProps } from "react-error-boundary";
import { Toaster } from "sonner";
import { useStepwiseWebSocket } from "@/hooks/useStepwiseWebSocket";
import { useNotifySuspended } from "@/hooks/useNotifySuspended";
import { useEngineStatus, useJobs, useJob } from "@/hooks/useStepwise";
import { LayoutGrid, FileCode, Settings2, Zap, FolderOpen, AlertTriangle, Sun, Moon } from "lucide-react";
import { cn } from "@/lib/utils";
import { CommandPalette } from "@/components/CommandPalette";

function getInitialTheme(): "dark" | "light" {
  const stored = localStorage.getItem("stepwise-theme");
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme: "dark" | "light") {
  document.documentElement.classList.toggle("dark", theme === "dark");
  localStorage.setItem("stepwise-theme", theme);
}

function ErrorFallback({ error, resetErrorBoundary }: FallbackProps) {
  return (
    <div className="flex items-center justify-center h-full">
      <div className="max-w-md w-full mx-4 p-6 rounded-lg border border-red-900/50 bg-red-950/20">
        <div className="flex items-center gap-3 mb-4">
          <AlertTriangle className="w-5 h-5 text-red-400 shrink-0" />
          <h2 className="text-lg font-semibold text-red-400">Something went wrong</h2>
        </div>
        <pre className="text-sm text-zinc-400 bg-zinc-900 rounded p-3 mb-4 overflow-auto max-h-40 whitespace-pre-wrap break-words">
          {error instanceof Error ? error.message : String(error)}
        </pre>
        <button
          onClick={resetErrorBoundary}
          className="px-4 py-2 text-sm font-medium rounded-md bg-zinc-800 text-zinc-200 hover:bg-zinc-700 transition-colors"
        >
          Reload
        </button>
      </div>
    </div>
  );
}

export function AppLayout() {
  const wsStatus = useStepwiseWebSocket();
  useNotifySuspended();
  const location = useLocation();
  const { data: status } = useEngineStatus();
  const [theme, setTheme] = useState<"dark" | "light">(getInitialTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const currentPath = location.pathname;
  const isJobsActive =
    currentPath === "/jobs" || currentPath.startsWith("/jobs/");
  const isFlowsActive = currentPath.startsWith("/flows");
  const isSettingsActive = currentPath.startsWith("/settings");

  // Derive a route key for page transition animation — changes on top-level route switches
  const routeKey = isJobsActive ? "jobs" : isFlowsActive ? "flows" : isSettingsActive ? "settings" : currentPath;

  // Dynamic tab title
  const { data: suspendedJobs } = useJobs("suspended");
  const pendingCount = suspendedJobs?.length ?? 0;
  const jobIdMatch = currentPath.match(/^\/jobs\/([^/]+)/);
  const detailJobId = jobIdMatch?.[1] ?? undefined;
  const { data: detailJob } = useJob(detailJobId);

  const handleReset = useCallback(() => {
    window.location.reload();
  }, []);

  useEffect(() => {
    let title = "Stepwise";

    if (isJobsActive && detailJobId) {
      const jobName = detailJob?.name || detailJob?.objective;
      title = jobName ? `${jobName} — Stepwise` : "Stepwise";
    } else if (isJobsActive) {
      title = "Jobs — Stepwise";
    } else if (isFlowsActive) {
      title = "Flows — Stepwise";
    } else if (isSettingsActive) {
      title = "Settings — Stepwise";
    }

    if (pendingCount > 0) {
      title = `(${pendingCount}) ${title}`;
    }

    document.title = title;
  }, [currentPath, isJobsActive, isFlowsActive, isSettingsActive, detailJobId, detailJob, pendingCount]);

  return (
    <div className="h-screen flex flex-col bg-background text-foreground">
      {/* Top nav */}
      <header className="h-12 border-b border-border flex items-center px-4 gap-6 shrink-0 bg-white/80 dark:bg-zinc-950/80">
        <Link to="/jobs" className="flex items-center gap-2">
          <img src="/stepwise-icon-64.png" alt="Stepwise" className="w-5 h-5" />
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
                ? "bg-zinc-200 dark:bg-zinc-800 text-foreground"
                : "text-zinc-500 hover:text-foreground hover:bg-zinc-200/50 dark:hover:bg-zinc-800/50"
            )}
          >
            <LayoutGrid className="w-4 h-4 md:w-3.5 md:h-3.5 md:mr-1.5" />
            <span className="hidden md:inline">Jobs</span>
            {pendingCount > 0 && (
              <span className="ml-1 inline-flex items-center justify-center min-w-[18px] h-[18px] px-1 text-[11px] font-semibold leading-none rounded-full bg-amber-500 text-white">
                {pendingCount}
              </span>
            )}
          </Link>
          <Link
            to="/flows"
            className={cn(
              "flex items-center justify-center min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 px-2 md:px-3 py-1.5 text-sm rounded-md transition-colors",
              isFlowsActive
                ? "bg-zinc-200 dark:bg-zinc-800 text-foreground"
                : "text-zinc-500 hover:text-foreground hover:bg-zinc-200/50 dark:hover:bg-zinc-800/50"
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
                ? "bg-zinc-200 dark:bg-zinc-800 text-foreground"
                : "text-zinc-500 hover:text-foreground hover:bg-zinc-200/50 dark:hover:bg-zinc-800/50"
            )}
          >
            <Settings2 className="w-4 h-4 md:w-3.5 md:h-3.5 md:mr-1.5" />
            <span className="hidden md:inline">Settings</span>
          </Link>
        </nav>

        <div className="flex-1" />

        {/* WebSocket status indicator */}
        <div
          className="flex items-center gap-1.5"
          title={
            wsStatus === "connected"
              ? "WebSocket connected"
              : wsStatus === "reconnecting"
                ? "Reconnecting…"
                : "Disconnected"
          }
        >
          <span
            className={cn(
              "inline-block h-2 w-2 rounded-full",
              wsStatus === "connected" && "bg-emerald-500",
              wsStatus === "reconnecting" && "bg-amber-500 animate-pulse",
              wsStatus === "disconnected" && "bg-red-500"
            )}
          />
        </div>

        {/* Theme toggle */}
        <button
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          className="p-1.5 rounded-md text-zinc-500 hover:text-foreground hover:bg-zinc-200/50 dark:hover:bg-zinc-800/50 transition-colors min-w-[44px] min-h-[44px] md:min-w-0 md:min-h-0 flex items-center justify-center"
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        >
          {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
        </button>

        {/* Engine status */}
        {status && (
          <div className="flex items-center gap-3 text-xs text-zinc-500">
            {status.version && (
              <span className="hidden md:inline text-zinc-600 font-mono">
                v{status.version}
              </span>
            )}
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
        <ErrorBoundary FallbackComponent={ErrorFallback} onReset={handleReset}>
          <div key={routeKey} className="h-full animate-fade-in">
            <Outlet />
          </div>
        </ErrorBoundary>
      </main>
      <Toaster theme={theme} richColors position="bottom-right" />
      <CommandPalette />
    </div>
  );
}
