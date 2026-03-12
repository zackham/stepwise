import { useState, useRef, useEffect, useCallback } from "react";
import { Send, X, Loader2, Check, Undo2, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { streamEditorChat } from "@/lib/api";
import type { ChatChunk } from "@/lib/api";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  yamlBlocks?: Array<{ content: string; apply_id: string; applied?: boolean }>;
}

interface ChatPanelProps {
  currentYaml: string;
  selectedStep: string | null;
  onApplyYaml: (yaml: string) => void;
  onClose: () => void;
}

export function ChatPanel({
  currentYaml,
  selectedStep,
  onApplyYaml,
  onClose,
}: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Auto-scroll on new messages
  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || isStreaming) return;

    setInput("");
    setIsStreaming(true);

    const userMsg: ChatMessage = { role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);

    // Build history for the API
    const history = messages.map((m) => ({
      role: m.role,
      content: m.content,
    }));

    // Stream response
    let fullContent = "";
    const yamlBlocks: ChatMessage["yamlBlocks"] = [];

    try {
      const assistantIdx = messages.length + 1; // +1 for user message we just added

      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: "", yamlBlocks: [] },
      ]);

      for await (const chunk of streamEditorChat(
        text,
        history,
        currentYaml,
        selectedStep ?? undefined,
      )) {
        if (chunk.type === "text") {
          fullContent += chunk.content ?? "";
          setMessages((prev) => {
            const updated = [...prev];
            updated[assistantIdx] = {
              role: "assistant",
              content: fullContent,
              yamlBlocks: [...yamlBlocks],
            };
            return updated;
          });
        } else if (chunk.type === "yaml") {
          yamlBlocks.push({
            content: chunk.content ?? "",
            apply_id: chunk.apply_id ?? "",
          });
          setMessages((prev) => {
            const updated = [...prev];
            updated[assistantIdx] = {
              role: "assistant",
              content: fullContent,
              yamlBlocks: [...yamlBlocks],
            };
            return updated;
          });
        } else if (chunk.type === "error") {
          fullContent += `\n\n**Error:** ${chunk.content}`;
          setMessages((prev) => {
            const updated = [...prev];
            updated[assistantIdx] = {
              role: "assistant",
              content: fullContent,
              yamlBlocks: [...yamlBlocks],
            };
            return updated;
          });
        }
      }
    } catch {
      // Stream ended or errored
    } finally {
      setIsStreaming(false);
    }
  }, [input, isStreaming, messages, currentYaml, selectedStep]);

  const handleApply = useCallback(
    (msgIdx: number, blockIdx: number) => {
      const msg = messages[msgIdx];
      if (!msg?.yamlBlocks?.[blockIdx]) return;
      const block = msg.yamlBlocks[blockIdx];
      onApplyYaml(block.content);
      // Mark as applied
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

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Quick actions for selected step
  const quickActions = selectedStep
    ? [
        { label: "Improve prompt", msg: `Improve the prompt for the "${selectedStep}" step to be clearer and more effective.` },
        { label: "Add retry logic", msg: `Add retry/loop exit rules to the "${selectedStep}" step.` },
        { label: "Explain step", msg: `Explain what the "${selectedStep}" step does and how it fits in the flow.` },
      ]
    : [
        { label: "Create a research flow", msg: "Create a flow that researches a topic, synthesizes findings, and produces a report." },
        { label: "Create a code review flow", msg: "Create a flow for automated code review with human approval." },
      ];

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-border">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-violet-400" />
          <span className="text-sm font-medium text-foreground">AI Assistant</span>
        </div>
        <button onClick={onClose} className="text-zinc-500 hover:text-foreground">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Messages */}
      <ScrollArea className="flex-1 min-h-0">
        <div ref={scrollRef} className="p-3 space-y-3">
          {messages.length === 0 && (
            <div className="space-y-3">
              <p className="text-xs text-zinc-500">
                {currentYaml
                  ? "Ask me to modify this flow, explain steps, or suggest improvements."
                  : "Describe a workflow and I'll generate the YAML for you."}
              </p>
              <div className="space-y-1.5">
                {quickActions.map((action) => (
                  <button
                    key={action.label}
                    onClick={() => {
                      setInput(action.msg);
                      inputRef.current?.focus();
                    }}
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
              <div
                className={`text-xs ${
                  msg.role === "user"
                    ? "text-blue-300 bg-blue-950/30 rounded px-2.5 py-1.5"
                    : "text-zinc-300"
                }`}
              >
                <span className="whitespace-pre-wrap">{msg.content}</span>
              </div>

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
                        size="sm"
                        variant="ghost"
                        onClick={() => handleApply(msgIdx, blockIdx)}
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
            placeholder={
              currentYaml ? "Modify this flow..." : "Describe a workflow..."
            }
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
