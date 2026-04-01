import { useState, useEffect, useCallback } from "react";
import { Outlet, Link, useLocation, useNavigate } from "@tanstack/react-router";
import { ErrorBoundary, type FallbackProps } from "react-error-boundary";
import { Toaster } from "sonner";
import { useStepwiseWebSocket, WsStatusProvider } from "@/hooks/useStepwiseWebSocket";
import { useNotifySuspended } from "@/hooks/useNotifySuspended";
import { useHotkeys } from "@/hooks/useHotkeys";
import { useEngineStatus, useJobs, useJob } from "@/hooks/useStepwise";
import {
  LayoutGrid,
  FileCode,
  Settings2,
  Zap,
  FolderOpen,
  AlertTriangle,
  Sun,
  Moon,
  Bell,
  BellOff,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { CommandPalette } from "@/components/CommandPalette";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

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
    <div className="flex h-full items-center justify-center">
      <div className="mx-4 w-full max-w-md rounded-lg border border-red-900/50 bg-red-950/20 p-6">
        <div className="mb-4 flex items-center gap-3">
          <AlertTriangle className="h-5 w-5 shrink-0 text-red-400" />
          <h2 className="text-lg font-semibold text-red-400">Something went wrong</h2>
        </div>
        <pre className="mb-4 max-h-40 overflow-auto rounded bg-zinc-100 p-3 text-sm whitespace-pre-wrap break-words text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400">
          {error instanceof Error ? error.message : String(error)}
        </pre>
        <button
          onClick={resetErrorBoundary}
          className="rounded-md bg-zinc-200 px-4 py-2 text-sm font-medium text-zinc-800 transition-colors hover:bg-zinc-300 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700"
        >
          Reload
        </button>
      </div>
    </div>
  );
}

const SHORTCUTS = [
  { keys: ["g", "j"], description: "Go to Jobs" },
  { keys: ["g", "f"], description: "Go to Flows" },
  { keys: ["g", "s"], description: "Go to Settings" },
  { keys: ["/"], description: "Focus search" },
  { keys: ["?"], description: "Show keyboard shortcuts" },
  { keys: ["Ctrl/Cmd", "K"], description: "Open command palette" },
  { keys: ["j / \u2193"], description: "Next step (Job Detail)" },
  { keys: ["k / \u2191"], description: "Previous step (Job Detail)" },
  { keys: ["Enter"], description: "Open step detail" },
  { keys: ["Escape"], description: "Clear selection" },
];

const ENTITY_SHORTCUTS = [
  { keys: ["D"], description: "Delete selected entity" },
  { keys: ["R"], description: "Retry/Rerun selected entity" },
  { keys: ["Enter"], description: "Open selected entity" },
];

function StepwiseMark({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      viewBox="0 0 100 100"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        <linearGradient id="stepwise-mark-gradient" x1="0" y1="100" x2="100" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stopColor="#06e8b8" />
          <stop offset="50%" stopColor="#5080e0" />
          <stop offset="100%" stopColor="#c060d8" />
        </linearGradient>
      </defs>
      <path
        d="M 10 80 L 50 80 L 50 20 L 90 20"
        stroke="url(#stepwise-mark-gradient)"
        strokeWidth="14"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  );
}

function ShortcutsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>
            Global navigation and search shortcuts.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            {SHORTCUTS.map((shortcut) => (
              <div
                key={`${shortcut.keys.join("-")}-${shortcut.description}`}
                className="flex items-center justify-between gap-4 rounded-lg border border-border/70 bg-muted/30 px-3 py-2"
              >
                <div className="flex items-center gap-1.5">
                  {shortcut.keys.map((key) => (
                    <kbd
                      key={`${shortcut.description}-${key}`}
                      className="inline-flex min-w-6 items-center justify-center rounded-md border border-border bg-background px-2 py-1 text-[11px] font-medium text-zinc-600 dark:text-zinc-300"
                    >
                      {key}
                    </kbd>
                  ))}
                </div>
                <span className="text-sm text-muted-foreground">{shortcut.description}</span>
              </div>
            ))}
          </div>
          <div>
            <p className="mb-2 text-xs font-medium text-muted-foreground uppercase tracking-wider">Entity Actions</p>
            <div className="space-y-2">
              {ENTITY_SHORTCUTS.map((shortcut) => (
                <div
                  key={`entity-${shortcut.keys.join("-")}-${shortcut.description}`}
                  className="flex items-center justify-between gap-4 rounded-lg border border-border/70 bg-muted/30 px-3 py-2"
                >
                  <div className="flex items-center gap-1.5">
                    {shortcut.keys.map((key) => (
                      <kbd
                        key={`${shortcut.description}-${key}`}
                        className="inline-flex min-w-6 items-center justify-center rounded-md border border-border bg-background px-2 py-1 text-[11px] font-medium text-zinc-600 dark:text-zinc-300"
                      >
                        {key}
                      </kbd>
                    ))}
                  </div>
                  <span className="text-sm text-muted-foreground">{shortcut.description}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function AppLayout() {
  const { wsState } = useStepwiseWebSocket();
  const { enabled: notificationsEnabled, toggle: toggleNotifications } = useNotifySuspended();
  const location = useLocation();
  const navigate = useNavigate();
  const { data: status } = useEngineStatus();
  const [theme, setTheme] = useState<"dark" | "light">(getInitialTheme);
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const currentPath = location.pathname;
  const isJobsActive = currentPath === "/jobs" || currentPath.startsWith("/jobs/");
  const isFlowsActive = currentPath.startsWith("/flows");
  const isSettingsActive = currentPath.startsWith("/settings");

  // Derive a route key for page transition animation - changes on top-level route switches
  const routeKey = isJobsActive
    ? "jobs"
    : isFlowsActive
      ? "flows"
      : isSettingsActive
        ? "settings"
        : currentPath;

  // Dynamic tab title
  const { data: allJobs } = useJobs(undefined, true);
  const pendingCount = allJobs?.filter((j) => j.has_suspended_steps).length ?? 0;
  const jobIdMatch = currentPath.match(/^\/jobs\/([^/]+)/);
  const detailJobId = jobIdMatch?.[1] ?? undefined;
  const { data: detailJob } = useJob(detailJobId);

  const handleReset = useCallback(() => {
    window.location.reload();
  }, []);

  const focusSearch = useCallback(() => {
    const searchInput = Array.from(
      document.querySelectorAll<HTMLInputElement>('[data-hotkey-search-input="true"]')
    )
      .reverse()
      .find((element) => !element.disabled && element.getClientRects().length > 0);

    if (searchInput) {
      searchInput.focus();
      searchInput.select?.();
      return;
    }

    setCommandPaletteOpen(true);
  }, []);

  useHotkeys([
    {
      keys: ["g", "j"],
      onTrigger: () => navigate({ to: "/jobs" }),
    },
    {
      keys: ["g", "f"],
      onTrigger: () => navigate({ to: "/flows" }),
    },
    {
      keys: ["g", "s"],
      onTrigger: () => navigate({ to: "/settings" }),
    },
    {
      keys: ["/"],
      onTrigger: focusSearch,
    },
    {
      keys: ["?"],
      onTrigger: () => setShortcutsOpen(true),
    },
  ]);

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
  }, [
    currentPath,
    isJobsActive,
    isFlowsActive,
    isSettingsActive,
    detailJobId,
    detailJob,
    pendingCount,
  ]);

  const websocketTitle =
    wsState === "connected"
      ? "WebSocket connected"
      : wsState === "reconnecting"
        ? "WebSocket reconnecting"
        : "WebSocket disconnected";

  const navItemClass = (isActive: boolean) =>
    cn(
      "relative flex min-h-[44px] min-w-[44px] items-center justify-center self-stretch px-2 text-sm transition-colors md:min-h-0 md:min-w-0 md:px-3",
      isActive
        ? "text-foreground after:absolute after:bottom-0 after:left-2 after:right-2 after:h-0.5 after:rounded-full after:bg-[linear-gradient(90deg,#06e8b8_0%,#5080e0_50%,#c060d8_100%)]"
        : "text-zinc-500 hover:bg-zinc-200/40 hover:text-foreground dark:hover:bg-zinc-900/60"
    );

  return (
    <WsStatusProvider value={wsState}>
      <div className="flex h-screen flex-col bg-background text-foreground">
        {/* Top nav */}
        <header className="flex h-12 shrink-0 items-center gap-6 border-b border-border bg-white/80 px-4 dark:bg-zinc-950/80">
          <Link to="/jobs" className="flex items-center gap-2">
            <StepwiseMark className="h-6 w-6 shrink-0" />
            <span className="text-sm font-semibold tracking-tight">Stepwise</span>
          </Link>
          {status?.cwd && (
            <div
              className="hidden max-w-48 items-center gap-1.5 truncate font-mono text-xs text-zinc-600 md:flex"
              title={status.cwd}
            >
              <FolderOpen className="h-3 w-3 shrink-0" />
              <span className="truncate">{status.cwd.split("/").pop() || status.cwd}</span>
            </div>
          )}

          <nav className="ml-auto flex h-full items-stretch gap-1 md:ml-4">
            <Link to="/jobs" className={navItemClass(isJobsActive)}>
              <LayoutGrid className="h-4 w-4 md:mr-1.5 md:h-3.5 md:w-3.5" />
              <span className="hidden md:inline">Jobs</span>
              {pendingCount > 0 && (
                <span className="ml-1 inline-flex h-[18px] min-w-[18px] items-center justify-center rounded-full bg-amber-500 px-1 text-[11px] leading-none font-semibold text-white">
                  {pendingCount}
                </span>
              )}
            </Link>
            <Link to="/flows" className={navItemClass(isFlowsActive)}>
              <FileCode className="h-4 w-4 md:mr-1.5 md:h-3.5 md:w-3.5" />
              <span className="hidden md:inline">Flows</span>
            </Link>
            <Link to="/settings" className={navItemClass(isSettingsActive)}>
              <Settings2 className="h-4 w-4 md:mr-1.5 md:h-3.5 md:w-3.5" />
              <span className="hidden md:inline">Settings</span>
            </Link>
          </nav>

          <div className="flex-1" />

          {/* WebSocket status indicator */}
          <div className="flex items-center gap-2" title={websocketTitle}>
            <span
              className={cn(
                "inline-block h-2 w-2 rounded-full",
                wsState === "connected" && "bg-emerald-500",
                wsState === "reconnecting" && "animate-pulse bg-amber-500",
                wsState === "disconnected" && "bg-red-500"
              )}
            />
            {wsState === "disconnected" && (
              <span className="hidden text-[11px] font-medium text-amber-600/90 md:inline dark:text-amber-300/90">
                Real-time updates paused
              </span>
            )}
          </div>

          {/* Notification toggle */}
          <button
            onClick={toggleNotifications}
            className={cn(
              "flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md p-1.5 transition-colors md:min-h-0 md:min-w-0",
              notificationsEnabled
                ? "text-blue-400 hover:bg-zinc-200/50 hover:text-blue-300 dark:hover:bg-zinc-800/50"
                : "text-zinc-500 hover:bg-zinc-200/50 hover:text-foreground dark:hover:bg-zinc-800/50"
            )}
            title={notificationsEnabled ? "Notifications on — click to disable" : "Notifications off — click to enable"}
          >
            {notificationsEnabled ? <Bell className="h-4 w-4" /> : <BellOff className="h-4 w-4" />}
          </button>

          {/* Theme toggle */}
          <button
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className="flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md p-1.5 text-zinc-500 transition-colors hover:bg-zinc-200/50 hover:text-foreground md:min-h-0 md:min-w-0 dark:hover:bg-zinc-800/50"
            title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          >
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>

          {/* Engine status */}
          {status && (
            <div className="flex items-center gap-3 text-xs text-zinc-500">
              {status.version && (
                <span className="hidden font-mono text-zinc-600 md:inline">v{status.version}</span>
              )}
              <div className="hidden items-center gap-1.5 md:flex">
                <Zap className="h-3 w-3" />
                <span>
                  {status.active_jobs} active / {status.total_jobs} total
                </span>
              </div>
              <div className="flex items-center gap-1">
                {status.active_jobs > 0 && (
                  <span className="relative flex h-2 w-2">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-blue-500" />
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
        <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
        <CommandPalette open={commandPaletteOpen} onOpenChange={setCommandPaletteOpen} />
      </div>
    </WsStatusProvider>
  );
}
