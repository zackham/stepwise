import { X } from "lucide-react";
import { ChatMessages } from "./ChatMessages";
import { ChatInput } from "./ChatInput";
import type { ChatMessage, AgentMode } from "@/hooks/useEditorChat";

interface ChatSidebarProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  onSend: (text: string) => void;
  onReset: () => void;
  onApplyYaml: (msgIdx: number, blockIdx: number) => void;
  agentMode: AgentMode;
  onModeChange: (mode: AgentMode) => void;
  sessionId: string | null;
  flowPath: string | null;
  stepContext: string | null;
  onRemoveStepContext: () => void;
}

export function ChatSidebar({
  messages,
  isStreaming,
  onSend,
  onReset,
  onApplyYaml,
  agentMode,
  onModeChange,
  sessionId,
  flowPath,
  stepContext,
  onRemoveStepContext,
}: ChatSidebarProps) {
  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Messages area */}
      <ChatMessages
        messages={messages}
        isStreaming={isStreaming}
        onApplyYaml={onApplyYaml}
      />

      {/* Step context chip */}
      {stepContext && (
        <div className="px-3 py-1.5 border-t border-border">
          <div className="inline-flex items-center gap-1.5 px-2 py-1 rounded-full bg-violet-950/40 border border-violet-800/50 text-[11px] text-violet-300">
            <span className="font-mono">{stepContext}</span>
            <button
              onClick={onRemoveStepContext}
              className="text-violet-500 hover:text-violet-300"
            >
              <X className="w-2.5 h-2.5" />
            </button>
          </div>
        </div>
      )}

      {/* Chat input */}
      <ChatInput
        onSend={onSend}
        placeholder={stepContext ? `Modify ${stepContext}...` : "Ask AI to modify this flow..."}
        disabled={isStreaming}
        agentMode={agentMode}
        onModeChange={onModeChange}
        sessionId={sessionId}
        onReset={onReset}
        flowPath={flowPath}
      />
    </div>
  );
}
