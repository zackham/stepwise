import { useState, useRef, useEffect, useCallback } from "react";
import {
  Send, X, Loader2, Check, Sparkles, FileText, Search, FolderOpen,
  ChevronDown, ChevronRight, RotateCcw, CheckCheck,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { streamEditorChat, writeFlowFile } from "@/lib/api";
import type { ChatChunk } from "@/lib/api";

type AgentMode = "claude" | "codex" | "simple";

const AGENT_MODES: { value: AgentMode; label: string; subtitle: string }[] = [
  { value: "claude", label: "Claude", subtitle: "Can read project files" },
  { value: "codex", label: "Codex", subtitle: "Can read project files" },
  { value: "simple", label: "Simple", subtitle: "Current context only" },
];

interface ToolActivity {
  id: string;
  name: string;
  input: Record<string, string>;
  output?: string;
  done: boolean;
}

interface FileBlock {
  path: string;
  content: string;
  apply_id: string;
  applied?: boolean;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  yamlBlocks?: Array<{ content: string; apply_id: string; applied?: boolean }>;
  fileBlocks?: FileBlock[];
  toolActivities?: ToolActivity[];
}

interface ChatPanelProps {
  currentYaml: string;
  selectedStep: string | null;
  flowPath: string | null;
  onApplyYaml: (yaml: string) => void;
  onFileApplied?: () => void;
  onClose: () => void;
}

export function ChatPanel({
  currentYaml,
  selectedStep,
  flowPath,
  onApplyYaml,
  onFileApplied,
  onClose,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [agentMode, setAgentMode] = useState<AgentMode>("claude");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [showModeSelect, setShowModeSelect] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll on new messages
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleNewConversation = useCallback(() => {
    setMessages([]);
    setSessionId(null);
  }, []);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || isStreaming) return;

    setInput("");
    setIsStreaming(true);

    const userMsg: ChatMessage = { role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);

    const history = messages.map((m) => ({ role: m.role, content: m.content }));

    let fullContent = "";
    const yamlBlocks: ChatMessage["yamlBlocks"] = [];
    const fileBlocks: FileBlock[] = [];
    const toolActivities: ToolActivity[] = [];

    const updateMsg = (assistantIdx: number) => {
      setMessages((prev) => {
        const updated = [...prev];
        updated[assistantIdx] = {
          role: "assistant",
          content: fullContent,
          yamlBlocks: [...yamlBlocks],
          fileBlocks: [...fileBlocks],
          toolActivities: [...toolActivities],
        };
        return updated;
      });
    };

    const assistantIdx = messages.length + 1;

    try {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "", yamlBlocks: [], fileBlocks: [], toolActivities: [] },
      ]);

      for await (const chunk of streamEditorChat(
        text, history, currentYaml, selectedStep ?? undefined,
        agentMode, sessionId ?? undefined, flowPath ?? undefined,
      )) {
        if (chunk.type === "session") {
          setSessionId(chunk.session_id ?? null);
        } else if (chunk.type === "text") {
          fullContent += chunk.content ?? "";
          updateMsg(assistantIdx);
        } else if (chunk.type === "yaml") {
          yamlBlocks.push({
            content: chunk.content ?? "",
            apply_id: chunk.apply_id ?? "",
          });
          updateMsg(assistantIdx);
        } else if (chunk.type === "file_block") {
          fileBlocks.push({
            path: chunk.path ?? "",
            content: chunk.content ?? "",
            apply_id: chunk.apply_id ?? "",
          });
          updateMsg(assistantIdx);
        } else if (chunk.type === "tool_use") {
          toolActivities.push({
            id: chunk.tool_use_id ?? "",
            name: chunk.tool_name ?? "",
            input: chunk.tool_input ?? {},
            done: false,
          });
          updateMsg(assistantIdx);
        } else if (chunk.type === "tool_result") {
          const idx = toolActivities.findIndex((t) => t.id === chunk.tool_use_id);
          if (idx >= 0) {
            toolActivities[idx] = { ...toolActivities[idx], output: chunk.tool_output, done: true };
          }
          updateMsg(assistantIdx);
        } else if (chunk.type === "done") {
          // Mark all pending tools as done when stream completes
          for (let i = 0; i < toolActivities.length; i++) {
            if (!toolActivities[i].done) {
              toolActivities[i] = { ...toolActivities[i], done: true };
            }
          }
          updateMsg(assistantIdx);
        } else if (chunk.type === "error") {
          fullContent += `\n\n**Error:** ${chunk.content}`;
          updateMsg(assistantIdx);
        }
      }
    } catch {
      // Stream ended or errored
    } finally {
      // Ensure all tool activities are marked done on stream end
      for (let i = 0; i < toolActivities.length; i++) {
        if (!toolActivities[i].done) {
          toolActivities[i] = { ...toolActivities[i], done: true };
        }
      }
      if (toolActivities.length > 0) {
        updateMsg(assistantIdx);
      }
      setIsStreaming(false);
    }
  }, [input, isStreaming, messages, currentYaml, selectedStep, agentMode, sessionId, flowPath]);

  const handleApplyYaml = useCallback(
    (msgIdx: number, blockIdx: number) => {
      const msg = messages[msgIdx];
      if (!msg?.yamlBlocks?.[blockIdx]) return;
      onApplyYaml(msg.yamlBlocks[blockIdx].content);
      setMessages((prev) => {
        const updated = [...prev];
        const m = { ...updated[msgIdx] };
        m.yamlBlocks = m.yamlBlocks?.map((b, i) =>
          i === blockIdx ? { ...b, applied: true } : b
        );
        updated[msgIdx] = m;
        return updated;
      });
    },
    [messages, onApplyYaml]
  );

  const handleApplyFile = useCallback(
    async (msgIdx: number, blockIdx: number) => {
      const msg = messages[msgIdx];
      if (!msg?.fileBlocks?.[blockIdx] || !flowPath) return;
      const block = msg.fileBlocks[blockIdx];
      try {
        await writeFlowFile(flowPath, block.path, block.content);
        setMessages((prev) => {
          const updated = [...prev];
          const m = { ...updated[msgIdx] };
          m.fileBlocks = m.fileBlocks?.map((b, i) =>
            i === blockIdx ? { ...b, applied: true } : b
          );
          updated[msgIdx] = m;
          return updated;
        });
        onFileApplied?.();
      } catch {
        // TODO: show error
      }
    },
    [messages, flowPath, onFileApplied]
  );

  const handleApplyAllFiles = useCallback(
    async (msgIdx: number) => {
      const msg = messages[msgIdx];
      if (!msg?.fileBlocks || !flowPath) return;
      for (let i = 0; i < msg.fileBlocks.length; i++) {
        if (!msg.fileBlocks[i].applied) {
          await handleApplyFile(msgIdx, i);
        }
      }
    },
    [messages, flowPath, handleApplyFile]
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const currentMode = AGENT_MODES.find((m) => m.value === agentMode)!;

  const quickActions = selectedStep
    ? [
        { label: "Improve prompt", msg: `Improve the prompt for the "${selectedStep}" step to be clearer and more effective.` },
        { label: "Add retry logic", msg: `Add retry/loop exit rules to the "${selectedStep}" step.` },
        { label: "Explain step", msg: `Explain what the "${selectedStep}" step does and how it fits in the flow.` },
      ]
    : [
        { label: "Create a research flow", msg: "Create a flow that researches a topic, synthesizes findings, and produces a report." },
        { label: "Convert a skill into a flow", msg: "Convert the  skill into a Stepwise flow. Read the SKILL.md and all referenced scripts first." },
      ];

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-border">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-violet-400" />
          {/* Mode selector */}
          <div className="relative">
            <button
              onClick={() => setShowModeSelect(!showModeSelect)}
              className="flex items-center gap-1 text-sm font-medium text-foreground hover:text-violet-300 transition-colors"
            >
              {currentMode.label}
              <ChevronDown className="w-3 h-3" />
            </button>
            {showModeSelect && (
              <div className="absolute top-full left-0 mt-1 bg-zinc-900 border border-zinc-700 rounded shadow-lg z-50 w-48">
                {AGENT_MODES.map((mode) => (
                  <button
                    key={mode.value}
                    onClick={() => { setAgentMode(mode.value); setShowModeSelect(false); }}
                    className={`block w-full text-left px-3 py-2 text-xs hover:bg-zinc-800 ${
                      mode.value === agentMode ? "text-violet-300" : "text-zinc-300"
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
              onClick={handleNewConversation}
              className="text-zinc-500 hover:text-foreground p-1"
              title="New conversation"
            >
              <RotateCcw className="w-3.5 h-3.5" />
            </button>
          )}
          <button onClick={onClose} className="text-zinc-500 hover:text-foreground p-1">
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Messages */}
      <ScrollArea className="flex-1 min-h-0">
        <div ref={scrollRef} className="p-3 space-y-3">
          {messages.length === 0 && (
            <div className="space-y-3">
              <p className="text-xs text-zinc-500">
                {currentYaml
                  ? "Ask me to modify this flow, explain steps, or suggest improvements."
                  : "Describe a workflow and I'll generate the YAML and scripts for you."}
              </p>
              <div className="space-y-1.5">
                {quickActions.map((action) => (
                  <button
                    key={action.label}
                    onClick={() => { setInput(action.msg); inputRef.current?.focus(); }}
                    className="block w-full text-left text-xs text-zinc-400 hover:text-foreground bg-zinc-900/50 hover:bg-zinc-800 rounded px-2.5 py-1.5 transition-colors"
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, msgIdx) => (
            <div key={msgIdx} className="space-y-1.5">
              {/* Message text */}
              <div className={`text-xs ${
                msg.role === "user"
                  ? "text-blue-300 bg-blue-950/30 rounded px-2.5 py-1.5"
                  : "text-zinc-300"
              }`}>
                <span className="whitespace-pre-wrap">{msg.content}</span>
              </div>

              {/* Tool activities */}
              {msg.toolActivities && msg.toolActivities.length > 0 && (
                <div className="space-y-1 my-1.5">
                  {msg.toolActivities.map((tool) => (
                    <div key={tool.id} className="flex items-center gap-1.5 text-[11px] text-zinc-500">
                      {tool.done ? (
                        <Check className="w-3 h-3 text-green-500 shrink-0" />
                      ) : (
                        <Loader2 className="w-3 h-3 animate-spin shrink-0" />
                      )}
                      <FileText className="w-3 h-3 shrink-0" />
                      <span className="truncate">{tool.name}</span>
                    </div>
                  ))}
                </div>
              )}

              {/* File blocks */}
              {msg.fileBlocks && msg.fileBlocks.length > 0 && (
                <div className="space-y-2 my-1.5">
                  {/* Apply All button when multiple files */}
                  {msg.fileBlocks.length > 1 && msg.fileBlocks.some((b) => !b.applied) && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => handleApplyAllFiles(msgIdx)}
                      className="h-6 text-xs w-full"
                    >
                      <CheckCheck className="w-3 h-3 mr-1" />
                      Apply All ({msg.fileBlocks.filter((b) => !b.applied).length} files)
                    </Button>
                  )}
                  {msg.fileBlocks.map((block, blockIdx) => (
                    <FileBlockCard
                      key={block.apply_id}
                      block={block}
                      onApply={() => handleApplyFile(msgIdx, blockIdx)}
                      canApply={!!flowPath}
                    />
                  ))}
                </div>
              )}

              {/* YAML blocks (legacy) */}
              {msg.yamlBlocks?.map((block, blockIdx) => (
                <div
                  key={block.apply_id}
                  className="bg-zinc-900 border border-zinc-700 rounded overflow-hidden"
                >
                  <pre className="text-[11px] text-zinc-300 p-2 overflow-x-auto max-h-48">
                    <code>{block.content}</code>
                  </pre>
                  <div className="flex items-center gap-1.5 p-1.5 bg-zinc-800/50 border-t border-zinc-700">
                    {block.applied ? (
                      <Button size="sm" variant="ghost" disabled className="h-6 text-xs">
                        <Check className="w-3 h-3 mr-1 text-green-400" />
                        Applied
                      </Button>
                    ) : (
                      <Button
                        size="sm" variant="ghost"
                        onClick={() => handleApplyYaml(msgIdx, blockIdx)}
                        className="h-6 text-xs"
                      >
                        <Check className="w-3 h-3 mr-1" />
                        Apply
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ))}

          {isStreaming && (
            <div className="flex items-center gap-1.5 text-xs text-zinc-500">
              <Loader2 className="w-3 h-3 animate-spin" />
              Thinking...
            </div>
          )}
        </div>
      </ScrollArea>

      {/* Input */}
      <div className="p-2 border-t border-border">
        <div className="flex gap-1.5">
          <Textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={currentYaml ? "Modify this flow..." : "Describe a workflow..."}
            className="text-xs bg-zinc-900 border-zinc-700 min-h-[60px] max-h-[120px] resize-none"
            disabled={isStreaming}
          />
          <Button
            size="sm"
            onClick={handleSend}
            disabled={!input.trim() || isStreaming}
            className="h-auto self-end"
          >
            <Send className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>
    </div>
  );
}


// ── File Block Card ─────────────────────────────────────────────────

function FileBlockCard({
  block,
  onApply,
  canApply,
}: {
  block: FileBlock;
  onApply: () => void;
  canApply: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const lines = block.content.split("\n");
  const isLong = lines.length > 30;
  const displayContent = expanded ? block.content : lines.slice(0, 30).join("\n");

  // Determine language from extension
  const ext = block.path.split(".").pop() ?? "";
  const langMap: Record<string, string> = {
    py: "python", sh: "bash", yaml: "yaml", yml: "yaml",
    json: "json", md: "markdown", toml: "toml", j2: "jinja2",
  };

  return (
    <div className={`bg-zinc-900 border rounded overflow-hidden ${
      block.applied ? "border-green-800/50" : "border-zinc-700"
    }`}>
      {/* File path header */}
      <div className="flex items-center justify-between px-2.5 py-1.5 bg-zinc-800/80 border-b border-zinc-700">
        <div className="flex items-center gap-1.5 text-[11px]">
          <FileText className="w-3 h-3 text-zinc-400" />
          <span className="font-mono text-zinc-300">{block.path}</span>
          <span className="text-zinc-600">{langMap[ext] ?? ext}</span>
        </div>
        {isLong && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-zinc-500 hover:text-zinc-300"
          >
            {expanded
              ? <ChevronDown className="w-3 h-3" />
              : <ChevronRight className="w-3 h-3" />
            }
          </button>
        )}
      </div>

      {/* Content preview */}
      <pre className="text-[11px] text-zinc-300 p-2 overflow-x-auto max-h-64">
        <code>{displayContent}</code>
        {isLong && !expanded && (
          <span className="text-zinc-600">{`\n... ${lines.length - 30} more lines`}</span>
        )}
      </pre>

      {/* Apply button */}
      <div className="flex items-center gap-1.5 p-1.5 bg-zinc-800/50 border-t border-zinc-700">
        {block.applied ? (
          <Button size="sm" variant="ghost" disabled className="h-6 text-xs">
            <Check className="w-3 h-3 mr-1 text-green-400" />
            Applied
          </Button>
        ) : (
          <Button
            size="sm" variant="ghost"
            onClick={onApply}
            disabled={!canApply}
            className="h-6 text-xs"
            title={canApply ? `Create ${block.path}` : "Load a directory flow first"}
          >
            <Check className="w-3 h-3 mr-1" />
            Apply
          </Button>
        )}
      </div>
    </div>
  );
}
