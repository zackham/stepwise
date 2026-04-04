import { useState, useEffect } from "react";
import { useJobSessions, useSessionStepEntries } from "@/hooks/useStepwise";
import { SessionTranscriptView } from "./SessionTranscriptView";
import { SessionStepFlow } from "./SessionStepFlow";
import { cn } from "@/lib/utils";
import type { SessionInfo } from "@/lib/types";
import { ArrowLeft } from "lucide-react";

interface SessionTabProps {
  jobId: string;
  highlightStep?: string | null;
  onNavigateToStep: (stepName: string) => void;
  /** When true, uses collapsible boundaries + focusStep for right-panel usage */
  focusStep?: string;
  /** Pre-select a session by name (opens directly into transcript view) */
  initialSession?: string | null;
}

/** Format an ISO timestamp as a relative time string */
function relativeTime(iso: string | null): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 0) return "just now";
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return `${Math.floor(diff / 86_400_000)}d ago`;
}

/** Format a duration in milliseconds to a compact string */
function formatDurationMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 3_600_000) return `${(ms / 60_000).toFixed(1)}m`;
  return `${(ms / 3_600_000).toFixed(1)}h`;
}

/** Extract a human-readable session name.
 *  Named sessions (from session: field) are already clean.
 *  Auto-generated pattern: "step-{8char_hash}-{step_name}-{attempt}" → extract step name. */
function formatSessionName(name: string, _stepNames: string[]): string {
  const autoMatch = name.match(/^step-[a-f0-9]{8}-(.+?)(?:-\d+)?$/);
  if (autoMatch) {
    return autoMatch[1];
  }
  return name;
}

function SessionCard({
  session,
  jobId,
  onClick,
  onNavigateToStep,
}: {
  session: SessionInfo;
  jobId: string;
  onClick: () => void;
  onNavigateToStep?: (stepName: string) => void;
}) {
  const displayName = formatSessionName(session.session_name, session.step_names);
  const stepEntries = useSessionStepEntries(jobId, session);

  // Calculate duration
  const startMs = session.started_at ? new Date(session.started_at).getTime() : null;
  const endMs = session.latest_at ? new Date(session.latest_at).getTime() : null;
  const durationMs = startMs && endMs ? endMs - startMs : null;
  const startedAgo = session.started_at ? relativeTime(session.started_at) : null;
  const duration = durationMs !== null && durationMs > 0 ? formatDurationMs(durationMs) : null;

  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left px-3 py-2.5 rounded-md transition-colors cursor-pointer",
        "hover:bg-zinc-100 dark:hover:bg-zinc-800/60",
        "border border-transparent hover:border-zinc-200 dark:hover:border-zinc-700/50"
      )}
    >
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-foreground truncate">
          {displayName}
        </span>
        {session.is_active && (
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500" />
          </span>
        )}
      </div>
      <SessionStepFlow entries={stepEntries} onSelectStep={onNavigateToStep} />
      <div className="flex items-center gap-2 mt-1 text-[10px] text-zinc-500">
        <span>{session.run_ids.length} run{session.run_ids.length !== 1 ? "s" : ""}</span>
        {startedAgo && (
          <>
            <span className="text-zinc-600">·</span>
            <span>{startedAgo}</span>
            {duration && (
              <>
                <span className="text-zinc-600">·</span>
                <span>{duration}</span>
              </>
            )}
          </>
        )}
        {session.total_tokens > 0 && (
          <>
            <span className="text-zinc-600">·</span>
            <span>{session.total_tokens >= 1000 ? `${(session.total_tokens / 1000).toFixed(1)}k` : session.total_tokens} tokens</span>
          </>
        )}
      </div>
    </button>
  );
}

export function SessionTab({ jobId, highlightStep, onNavigateToStep, focusStep, initialSession }: SessionTabProps) {
  const { data: sessionData, isLoading } = useJobSessions(jobId);
  const sessions = sessionData?.sessions ?? [];
  const [selectedSession, setSelectedSession] = useState<string | null>(initialSession ?? null);
  const firstSession = sessions.length === 1 ? sessions[0] : null;
  const singleSessionEntries = useSessionStepEntries(jobId, firstSession);

  // React to external initialSession changes (e.g., "View full session" from right panel)
  useEffect(() => {
    if (initialSession) setSelectedSession(initialSession);
  }, [initialSession]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <p className="text-sm text-zinc-500">Loading sessions...</p>
      </div>
    );
  }

  if (sessions.length === 0) {
    return (
      <div className="flex items-center justify-center py-12">
        <p className="text-sm text-zinc-500">No agent sessions for this job</p>
      </div>
    );
  }

  // Single session → render transcript directly
  if (sessions.length === 1) {
    const s = sessions[0];
    return (
      <div className="flex flex-col h-full">
        {singleSessionEntries.length > 1 && (
          <div className="px-3 py-2 border-b border-border shrink-0">
            <SessionStepFlow
              entries={singleSessionEntries}
              currentStep={highlightStep ?? undefined}
              onSelectStep={onNavigateToStep}
            />
          </div>
        )}
        <div className="flex-1 min-h-0">
          <SessionTranscriptView
            jobId={jobId}
            sessionName={s.session_name}
            runIds={s.run_ids}
            isLive={s.is_active}
            highlightStep={highlightStep}
            onNavigateToStep={onNavigateToStep}
            collapsibleBoundaries={true}
            defaultExpanded={true}
            focusStep={focusStep}
            onSelectStep={onNavigateToStep}
          />
        </div>
      </div>
    );
  }

  // Level 2: selected session → transcript view with back button
  const activeSessionInfo = sessions.find((s) => s.session_name === selectedSession);
  if (activeSessionInfo) {
    return (
      <div className="flex flex-col h-full">
        {/* Back header */}
        <div className="flex items-center gap-2 px-3 py-2 border-b border-border shrink-0">
          <button
            onClick={() => setSelectedSession(null)}
            className="flex items-center gap-1 text-xs text-zinc-400 hover:text-foreground transition-colors cursor-pointer"
          >
            <ArrowLeft className="w-3 h-3" />
            Sessions
          </button>
          <span className="text-xs font-medium text-foreground truncate">
            {formatSessionName(activeSessionInfo.session_name, activeSessionInfo.step_names)}
          </span>
          {activeSessionInfo.is_active && (
            <span className="relative flex h-2 w-2 shrink-0">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500" />
            </span>
          )}
        </div>
        {/* Transcript */}
        <div className="flex-1 min-h-0 overflow-y-auto">
          <SessionTranscriptView
            key={activeSessionInfo.session_name}
            jobId={jobId}
            sessionName={activeSessionInfo.session_name}
            runIds={activeSessionInfo.run_ids}
            isLive={activeSessionInfo.is_active}
            highlightStep={highlightStep}
            onNavigateToStep={onNavigateToStep}
            collapsibleBoundaries={true}
            defaultExpanded={true}
            onSelectStep={onNavigateToStep}
          />
        </div>
      </div>
    );
  }

  // Level 1: session list
  return (
    <div className="p-2 space-y-1">
      {sessions.map((s: SessionInfo) => (
        <SessionCard
          key={s.session_name}
          session={s}
          jobId={jobId}
          onClick={() => setSelectedSession(s.session_name)}
          onNavigateToStep={onNavigateToStep}
        />
      ))}
    </div>
  );
}
