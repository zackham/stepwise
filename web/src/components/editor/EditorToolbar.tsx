import { Play, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Breadcrumb } from "@/components/layout/Breadcrumb";
import type { AgentMode } from "@/hooks/useEditorChat";

const AGENT_LABELS: Record<AgentMode, string> = {
  claude: "Claude",
  codex: "Codex",
  simple: "AI",
};

interface EditorToolbarProps {
  flowName: string;
  onRun?: () => void;
  isRunning?: boolean;
  parseErrors: string[];
  chatOpen?: boolean;
  onToggleChat?: () => void;
  isChatStreaming?: boolean;
  agentMode?: AgentMode;
  chatBackgrounded?: boolean;
}

export function EditorToolbar({
  flowName,
  onRun,
  isRunning,
  parseErrors,
  chatOpen,
  onToggleChat,
  isChatStreaming,
  agentMode = "claude",
  chatBackgrounded,
}: EditorToolbarProps) {
  return (
    <div className="flex items-center gap-3 h-10 px-3 border-b border-border shrink-0">
      <Breadcrumb
        segments={[
          { label: "Flows", to: "/flows" },
          { label: flowName },
        ]}
        className="border-0 px-0 py-0"
      />

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
          {chatBackgrounded && !isChatStreaming && (
            <span className="w-1.5 h-1.5 rounded-full bg-violet-500 ml-0.5" />
          )}
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
