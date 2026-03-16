import { Play, MessageSquare, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { AgentMode } from "@/hooks/useEditorChat";

const AGENT_LABELS: Record<AgentMode, string> = {
  claude: "Claude",
  codex: "Codex",
  simple: "AI",
};

interface EditorToolbarProps {
  flowName: string;
  onBack?: () => void;
  onRun?: () => void;
  isRunning?: boolean;
  parseErrors: string[];
  chatOpen?: boolean;
  onToggleChat?: () => void;
  isChatStreaming?: boolean;
  agentMode?: AgentMode;
}

export function EditorToolbar({
  flowName,
  onBack,
  onRun,
  isRunning,
  parseErrors,
  chatOpen,
  onToggleChat,
  isChatStreaming,
  agentMode = "claude",
}: EditorToolbarProps) {
  return (
    <div className="flex items-center gap-3 h-10 px-3 border-b border-border shrink-0">
      {onBack && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onBack}
          className="h-7 w-7 p-0 text-zinc-500 hover:text-foreground"
          title="Back to flows"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
        </Button>
      )}
      <span className="text-sm font-medium text-foreground">
        {flowName}
      </span>

      {onRun && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onRun}
          disabled={isRunning || parseErrors.length > 0}
          className="h-7 text-xs text-emerald-400 hover:text-emerald-300"
        >
          <Play className="w-3 h-3 mr-1" />
          {isRunning ? "Starting..." : "Run"}
        </Button>
      )}

      <div className="flex-1" />

      {parseErrors.length > 0 && (
        <span
          className="text-xs text-red-400 truncate max-w-xs"
          title={parseErrors.join("\n")}
        >
          {parseErrors[0]}
        </span>
      )}

      {onToggleChat && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggleChat}
          className={cn(
            "h-7 text-xs relative",
            chatOpen ? "text-violet-400 hover:text-violet-300" : "text-zinc-500 hover:text-zinc-300"
          )}
          title={chatOpen ? "Close chat" : "Open chat"}
        >
          <MessageSquare className="w-3.5 h-3.5 mr-1" />
          {AGENT_LABELS[agentMode]}
          {isChatStreaming && (
            <span className="absolute -top-0.5 -right-0.5 flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-violet-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-violet-500" />
            </span>
          )}
        </Button>
      )}
    </div>
  );
}
