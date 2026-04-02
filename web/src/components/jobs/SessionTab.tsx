import { useState } from "react";
import { useJobSessions } from "@/hooks/useStepwise";
import { SessionTranscriptView } from "./SessionTranscriptView";
import { cn } from "@/lib/utils";
import type { SessionInfo } from "@/lib/types";
import { ArrowLeft } from "lucide-react";

interface SessionTabProps {
  jobId: string;
  highlightStep?: string | null;
  onNavigateToStep: (stepName: string) => void;
  /** When true, uses collapsible boundaries + focusStep for right-panel usage */
  focusStep?: string;
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

function SessionCard({
  session,
  onClick,
}: {
  session: SessionInfo;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left px-3 py-2.5 rounded-md transition-colors",
        "hover:bg-zinc-100 dark:hover:bg-zinc-800/60",
        "border border-transparent hover:border-zinc-200 dark:hover:border-zinc-700/50"
      )}
    >
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-foreground truncate">
          {session.session_name}
        </span>
        {session.is_active && (
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-2 w-2 bg-blue-500" />
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 mt-1 text-[10px] text-zinc-500">
        <span>{session.step_names.length} step{session.step_names.length !== 1 ? "s" : ""}</span>
        <span className="text-zinc-600">·</span>
        <span>{session.run_ids.length} run{session.run_ids.length !== 1 ? "s" : ""}</span>
        {session.started_at && (
          <>
            <span className="text-zinc-600">·</span>
            <span>{relativeTime(session.started_at)}</span>
            {session.latest_at && session.latest_at !== session.started_at && (
              <>
                <span className="text-zinc-700">→</span>
                <span>{relativeTime(session.latest_at)}</span>
              </>
            )}
          </>
        )}
      </div>
    </button>
  );
}

export function SessionTab({ jobId, highlightStep, onNavigateToStep, focusStep }: SessionTabProps) {
  const { data: sessionData, isLoading } = useJobSessions(jobId);
  const sessions = sessionData?.sessions ?? [];
  const [selectedSession, setSelectedSession] = useState<string | null>(null);

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
            className="flex items-center gap-1 text-xs text-zinc-400 hover:text-foreground transition-colors"
          >
            <ArrowLeft className="w-3 h-3" />
            Sessions
          </button>
          <span className="text-xs font-medium text-foreground truncate">
            {activeSessionInfo.session_name}
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
            jobId={jobId}
            sessionName={activeSessionInfo.session_name}
            runIds={activeSessionInfo.run_ids}
            isLive={activeSessionInfo.is_active}
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

  // Level 1: session list
  return (
    <div className="p-2 space-y-1">
      {sessions.map((s: SessionInfo) => (
        <SessionCard
          key={s.session_name}
          session={s}
          onClick={() => setSelectedSession(s.session_name)}
        />
      ))}
    </div>
  );
}
