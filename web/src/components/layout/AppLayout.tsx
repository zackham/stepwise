import { useState, useEffect, useCallback, useMemo } from "react";
import { Outlet, Link, useLocation, useNavigate } from "@tanstack/react-router";
import { ErrorBoundary, type FallbackProps } from "react-error-boundary";
import { Toaster } from "sonner";
import { useStepwiseWebSocket, WsStatusProvider } from "@/hooks/useStepwiseWebSocket";
import { useNotifySuspended } from "@/hooks/useNotifySuspended";
import { useRecentEvents, type RecentEvent } from "@/hooks/useRecentEvents";
import { useHotkeys } from "@/hooks/useHotkeys";
import { useEngineStatus, useJobs, useJob, useServers } from "@/hooks/useStepwise";
import {
  LayoutGrid,
  FileCode,
  Settings2,
  FolderOpen,
  AlertTriangle,
  Sun,
  Moon,
  Bell,
  ChevronDown,
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
import { ChangelogModal } from "@/components/layout/ChangelogModal";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

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

const EVENT_DOT_COLOR: Record<RecentEvent["kind"], string> = {
  "job.completed": "bg-emerald-500",
  "job.failed": "bg-red-500",
  "job.started": "bg-blue-500",
  "step.failed": "bg-red-500",
  "step.suspended": "bg-amber-500",
};

function NotificationEventItem({
  event,
  onClick,
}: {
  event: RecentEvent;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="flex w-full items-start gap-2.5 rounded-md px-3 py-2 text-left transition-colors hover:bg-accent"
    >
      <span
        className={cn(
          "mt-1.5 h-2 w-2 shrink-0 rounded-full",
          EVENT_DOT_COLOR[event.kind] || "bg-zinc-500"
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="truncate text-xs font-medium text-foreground">{event.jobName}</div>
        <div className="flex items-center gap-2">
          <span className="truncate text-[11px] text-muted-foreground">{event.description}</span>
          <span className="shrink-0 text-[10px] text-muted-foreground/60">{timeAgo(event.timestamp)}</span>
        </div>
      </div>
    </button>
  );
}

export function AppLayout() {
  const { wsState } = useStepwiseWebSocket();
  const { enabled: notificationsEnabled, toggle: toggleNotifications } = useNotifySuspended();
  const { events: recentEvents } = useRecentEvents();
  const location = useLocation();
  const navigate = useNavigate();
  const { data: status } = useEngineStatus();
  const { data: serversData } = useServers();
  const [theme, setTheme] = useState<"dark" | "light">(getInitialTheme);
  const [commandPaletteOpen, setCommandPaletteOpen] = useState(false);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [changelogOpen, setChangelogOpen] = useState(false);
  const [seenEventCount, setSeenEventCount] = useState(0);

  const unreadCount = useMemo(
    () => Math.max(0, recentEvents.length - seenEventCount),
    [recentEvents.length, seenEventCount],
  );

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
  const { data: jobsResponse } = useJobs(undefined, true);
  const pendingCount = jobsResponse?.jobs?.filter((j) => j.has_suspended_steps).length ?? 0;
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
            <DropdownMenu>
              <DropdownMenuTrigger
                className="hidden max-w-56 cursor-pointer items-center gap-1.5 rounded-md px-2 py-1 font-mono text-xs text-zinc-600 transition-colors hover:bg-zinc-200/50 hover:text-zinc-800 md:flex dark:hover:bg-zinc-800/50 dark:hover:text-zinc-300"
                title={status.cwd}
              >
                <FolderOpen className="h-3 w-3 shrink-0" />
                <span className="truncate">{status.cwd.split("/").pop() || status.cwd}</span>
                <ChevronDown className="h-3 w-3 shrink-0 text-zinc-400" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" sideOffset={6} className="w-72">
                <div className="px-2 py-1.5 text-xs font-medium text-zinc-500">Running servers</div>
                {serversData?.servers?.length ? (
                  serversData.servers.map((server) => {
                    const folderName = server.project_path.split("/").pop() || server.project_path;
                    const isCurrent = server.url === serversData.current;
                    return (
                      <DropdownMenuItem
                        key={server.pid}
                        className={cn(
                          "flex cursor-pointer items-center gap-3 px-3 py-2.5 rounded-md",
                          isCurrent && "bg-zinc-800/50"
                        )}
                        onClick={() => {
                          if (!isCurrent) {
                            window.location.href = server.url;
                          }
                        }}
                      >
                        <span
                          className={cn(
                            "h-2 w-2 shrink-0 rounded-full",
                            isCurrent ? "bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.4)]" : "bg-zinc-600"
                          )}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-baseline gap-2">
                            <span className={cn("text-sm font-medium truncate", isCurrent ? "text-foreground" : "text-zinc-300")}>{folderName}</span>
                            <span className="ml-auto shrink-0 text-[10px] text-zinc-600 tabular-nums">:{server.port}</span>
                          </div>
                          <div className="flex items-center gap-2 mt-0.5 text-[10px] text-zinc-600">
                            <span
                              className="truncate hover:text-zinc-400 cursor-copy"
                              title="Click to copy path"
                              onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(server.project_path); }}
                            >{server.project_path}</span>
                            <span className="shrink-0">·</span>
                            <span
                              className="shrink-0 hover:text-zinc-400 cursor-copy"
                              title="Click to copy PID"
                              onClick={(e) => { e.stopPropagation(); navigator.clipboard.writeText(String(server.pid)); }}
                            >pid {server.pid}</span>
                            <span className="shrink-0">·</span>
                            <span className="shrink-0">up {timeAgo(server.started_at)}</span>
                          </div>
                        </div>
                      </DropdownMenuItem>
                    );
                  })
                ) : (
                  <div className="px-2 py-1.5 text-xs text-zinc-500">No other servers running</div>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          )}

          <nav className="ml-auto flex h-full items-stretch gap-1 md:ml-4">
            <Link to="/jobs" className={navItemClass(isJobsActive)}>
              <LayoutGrid className="h-4 w-4 md:mr-1.5 md:h-3.5 md:w-3.5" />
              <span className="hidden md:inline">Jobs</span>
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

          {/* Notification dropdown */}
          <DropdownMenu
            onOpenChange={(open) => {
              if (open) setSeenEventCount(recentEvents.length);
            }}
          >
            <DropdownMenuTrigger
              className={cn(
                "relative flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md p-1.5 transition-colors md:min-h-0 md:min-w-0",
                notificationsEnabled
                  ? "text-blue-400 hover:bg-zinc-200/50 hover:text-blue-300 dark:hover:bg-zinc-800/50"
                  : "text-zinc-500 hover:bg-zinc-200/50 hover:text-foreground dark:hover:bg-zinc-800/50"
              )}
              title="Notifications"
            >
              <Bell className="h-4 w-4" />
              {unreadCount > 0 && (
                <span className="absolute -top-0.5 -right-0.5 inline-flex h-[16px] min-w-[16px] items-center justify-center rounded-full bg-blue-500 px-1 text-[10px] leading-none font-semibold text-white">
                  {unreadCount > 9 ? "9+" : unreadCount}
                </span>
              )}
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" sideOffset={6} className="w-80">
              {/* Header */}
              <div className="flex items-center justify-between px-3 py-2">
                <span className="text-xs font-medium text-foreground">Notifications</span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    toggleNotifications();
                  }}
                  className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground transition-colors hover:text-foreground"
                >
                  <span className={cn(notificationsEnabled ? "text-foreground" : "text-muted-foreground/50")}>On</span>
                  <span className="text-muted-foreground/30">/</span>
                  <span className={cn(!notificationsEnabled ? "text-foreground" : "text-muted-foreground/50")}>Off</span>
                </button>
              </div>
              <DropdownMenuSeparator />
              {/* Event list */}
              <ScrollArea className="max-h-[400px] overflow-y-auto">
                {recentEvents.length === 0 ? (
                  <div className="px-3 py-8 text-center text-xs text-muted-foreground">
                    No recent events
                  </div>
                ) : (
                  <div className="py-1">
                    {recentEvents.map((event) => (
                      <NotificationEventItem
                        key={event.id}
                        event={event}
                        onClick={() => {
                          navigate({ to: "/jobs/$jobId", params: { jobId: event.jobId } });
                        }}
                      />
                    ))}
                  </div>
                )}
              </ScrollArea>
            </DropdownMenuContent>
          </DropdownMenu>

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
              {status.active_jobs > 0 && (
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-blue-400 opacity-75" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-blue-500" />
                </span>
              )}
              {status.version && (
                <button
                  onClick={() => setChangelogOpen(true)}
                  className="hidden rounded px-1.5 py-0.5 font-mono text-zinc-600 transition-colors hover:bg-zinc-200/60 hover:text-zinc-800 md:inline dark:hover:bg-zinc-800/60 dark:hover:text-zinc-300"
                  title="View changelog"
                >
                  v{status.version}
                </button>
              )}
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
        <ChangelogModal open={changelogOpen} onOpenChange={setChangelogOpen} currentVersion={status?.version} />
      </div>
    </WsStatusProvider>
  );
}
