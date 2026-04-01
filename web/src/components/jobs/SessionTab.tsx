import { useState } from "react";
import { useJobSessions } from "@/hooks/useStepwise";
import { SessionTranscriptView } from "./SessionTranscriptView";
import { cn } from "@/lib/utils";
import type { SessionInfo } from "@/lib/types";

interface SessionTabProps {
  jobId: string;
  highlightStep?: string | null;
  onNavigateToStep: (stepName: string) => void;
}

export function SessionTab({ jobId, highlightStep, onNavigateToStep }: SessionTabProps) {
  const { data: sessionData, isLoading } = useJobSessions(jobId);
  const sessions = sessionData?.sessions ?? [];
  const [selectedName, setSelectedName] = useState<string | null>(null);

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

  // Single session → render directly
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
      />
    );
  }

  // Multiple sessions → picker
  const activeSession = sessions.find((s) => s.session_name === selectedName)
    ?? sessions.find((s) => s.is_active)
    ?? sessions[0];

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-1.5 px-3 py-2 border-b border-border shrink-0 overflow-x-auto">
        {sessions.map((s: SessionInfo) => (
          <button
            key={s.session_name}
            onClick={() => setSelectedName(s.session_name)}
            className={cn(
              "text-xs px-2.5 py-1 rounded-md transition-colors whitespace-nowrap",
              s.session_name === activeSession.session_name
                ? "bg-zinc-200 dark:bg-zinc-800 text-foreground"
                : "text-zinc-500 hover:text-foreground hover:bg-zinc-100 dark:hover:bg-zinc-900",
            )}
          >
            {s.session_name}
            {s.is_active && (
              <span className="ml-1.5 inline-block w-1.5 h-1.5 rounded-full bg-blue-400" />
            )}
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        <SessionTranscriptView
          jobId={jobId}
          sessionName={activeSession.session_name}
          runIds={activeSession.run_ids}
          isLive={activeSession.is_active}
          highlightStep={highlightStep}
          onNavigateToStep={onNavigateToStep}
        />
      </div>
    </div>
  );
}
