import { useState, useRef, useEffect } from "react";
import { ChevronDown, Info, AlertTriangle, RotateCcw, Sparkles } from "lucide-react";
import { Textarea } from "@/components/ui/textarea";
import type { AgentMode } from "@/hooks/useEditorChat";

const AGENT_MODES: { value: AgentMode; label: string; subtitle: string }[] = [
  { value: "claude", label: "Claude", subtitle: "Full read/write access" },
  { value: "codex", label: "Codex", subtitle: "Full read/write access" },
  { value: "simple", label: "Simple", subtitle: "Current context only" },
];

interface ChatInputProps {
  onSend: (text: string) => void;
  placeholder?: string;
  disabled?: boolean;
  agentMode: AgentMode;
  onModeChange: (mode: AgentMode) => void;
  sessionId: string | null;
  onReset: () => void;
  flowPath?: string | null;
  floating?: boolean;
}

export function ChatInput({
  onSend,
  placeholder = "Ask AI to modify this flow...",
  disabled = false,
  agentMode,
  onModeChange,
  sessionId,
  onReset,
  flowPath,
  floating = false,
}: ChatInputProps) {
  const [input, setInput] = useState("");
  const [showModeSelect, setShowModeSelect] = useState(false);
  const [showDisclosure, setShowDisclosure] = useState(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const text = input.trim();
      if (text && !disabled) {
        setInput("");
        onSend(text);
      }
    }
  };

  const currentMode = AGENT_MODES.find((m) => m.value === agentMode)!;
  const isAgentMode = agentMode !== "simple";

  return (
    <div className={floating ? "rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-lg" : "border-t border-border"}>
      {/* Permission disclosure */}
      {showDisclosure && isAgentMode && (
        <div className="px-3 py-2 border-b border-amber-300/30 dark:border-amber-900/30 bg-amber-50 dark:bg-amber-950/20 text-[11px] text-amber-800/80 dark:text-amber-200/80">
          <div className="flex items-start gap-1.5">
            <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0 text-amber-500 dark:text-amber-400" />
            <div>
              <p className="font-medium text-amber-700 dark:text-amber-300">Agent runs with full tool approval</p>
              <p className="mt-1 text-amber-700/60 dark:text-amber-200/60">
                All tool calls (file reads, writes, shell commands) are auto-approved.
                The system prompt constrains writes to the flow directory
                {flowPath && <> (<code className="px-1 bg-amber-200/30 dark:bg-amber-900/30 rounded">{flowPath}</code>)</>},
                but this is not a sandbox.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Header bar: mode selector + info */}
      <div className="flex items-center justify-between px-2 py-1 bg-zinc-50/50 dark:bg-zinc-950/50">
        <div className="flex items-center gap-1.5">
          <Sparkles className="w-3 h-3 text-violet-400" />
          <div className="relative">
          <button
            onClick={() => setShowModeSelect(!showModeSelect)}
            className="flex items-center gap-1 text-[11px] text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 transition-colors"
          >
            {currentMode.label}
            <ChevronDown className="w-2.5 h-2.5" />
          </button>
          {showModeSelect && (
            <div className="absolute bottom-full left-0 mb-1 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded shadow-lg z-50 w-44">
              {AGENT_MODES.map((mode) => (
                <button
                  key={mode.value}
                  onClick={() => { onModeChange(mode.value); setShowModeSelect(false); }}
                  className={`block w-full text-left px-3 py-1.5 text-xs hover:bg-zinc-100 dark:hover:bg-zinc-800 ${
                    mode.value === agentMode ? "text-violet-600 dark:text-violet-300" : "text-zinc-700 dark:text-zinc-300"
                  }`}
                >
                  <div className="font-medium">{mode.label}</div>
                  <div className="text-zinc-500 text-[10px]">{mode.subtitle}</div>
                </button>
              ))}
            </div>
          )}
          </div>
        </div>
        <div className="flex items-center gap-1">
          {sessionId && (
            <button
              onClick={onReset}
              className="text-zinc-400 dark:text-zinc-600 hover:text-zinc-600 dark:hover:text-zinc-400 p-0.5"
              title="New conversation"
            >
              <RotateCcw className="w-3 h-3" />
            </button>
          )}
          {isAgentMode && (
            <button
              onClick={() => setShowDisclosure((v) => !v)}
              className={`p-0.5 transition-colors ${showDisclosure ? "text-amber-400" : "text-zinc-400 dark:text-zinc-600 hover:text-zinc-600 dark:hover:text-zinc-400"}`}
              title="Agent permissions info"
            >
              <Info className="w-3 h-3" />
            </button>
          )}
        </div>
      </div>

      {/* Textarea */}
      <div className="p-2 pt-0">
        <Textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          className="text-xs bg-white dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700 min-h-[48px] max-h-[120px] resize-none w-full"
          disabled={disabled}
        />
      </div>
    </div>
  );
}
