import { useState, useRef, useEffect, useCallback } from "react";
import { X, Loader2, Bot, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Markdown } from "@/components/ui/markdown";

interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

interface ScheduleChatProps {
  onClose: () => void;
  onScheduleChanged: () => void;
}

const SYSTEM_INTRO = "I can help you create, modify, or manage schedules. What would you like to do?";

async function* streamScheduleChat(
  message: string,
  history: Array<{ role: string; content: string }>,
): AsyncGenerator<{ type: string; content?: string; action?: string; schedule?: Record<string, unknown> }> {
  const res = await fetch("/api/schedules/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history }),
  });

  if (!res.ok) {
    yield { type: "error", content: `${res.status}: ${await res.text()}` };
    return;
  }

  const reader = res.body?.getReader();
  if (!reader) return;

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        yield JSON.parse(line);
      } catch {
        // skip malformed lines
      }
    }
  }

  if (buffer.trim()) {
    try {
      yield JSON.parse(buffer);
    } catch {
      // skip
    }
  }
}

export function ScheduleChat({ onClose, onScheduleChanged }: ScheduleChatProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([
    { role: "system", content: SYSTEM_INTRO },
  ]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const send = useCallback(
    async (text: string) => {
      if (!text.trim() || isStreaming) return;

      setIsStreaming(true);
      const userMsg: ChatMessage = { role: "user", content: text };
      setMessages((prev) => [...prev, userMsg]);

      // Build history without system intro
      const history = messages
        .filter((m) => m.role !== "system")
        .map((m) => ({ role: m.role, content: m.content }));

      let fullContent = "";
      const assistantIdx = messages.length + 1; // +1 for the user msg we just added

      try {
        setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

        for await (const chunk of streamScheduleChat(text, history)) {
          if (chunk.type === "text") {
            fullContent += chunk.content ?? "";
            setMessages((prev) => {
              const updated = [...prev];
              updated[assistantIdx] = { role: "assistant", content: fullContent };
              return updated;
            });
          } else if (chunk.type === "action") {
            // Schedule was created/modified/deleted — refresh the list
            onScheduleChanged();
          } else if (chunk.type === "error") {
            fullContent += `\n\n**Error:** ${chunk.content}`;
            setMessages((prev) => {
              const updated = [...prev];
              updated[assistantIdx] = { role: "assistant", content: fullContent };
              return updated;
            });
          }
          // "done" type — no action needed
        }
      } catch {
        if (!fullContent) {
          fullContent = "Connection error. Please try again.";
          setMessages((prev) => {
            const updated = [...prev];
            updated[assistantIdx] = { role: "assistant", content: fullContent };
            return updated;
          });
        }
      } finally {
        setIsStreaming(false);
      }
    },
    [isStreaming, messages, onScheduleChanged],
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const text = input.trim();
      if (text && !isStreaming) {
        setInput("");
        send(text);
      }
    }
  };

  return (
    <div className="w-80 border-l border-border flex flex-col h-full shrink-0 bg-background">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <div className="flex items-center gap-1.5">
          <Bot className="h-3.5 w-3.5 text-violet-400" />
          <span className="text-xs font-medium">Schedule Assistant</span>
        </div>
        <button
          onClick={onClose}
          className="p-1 rounded text-zinc-500 hover:text-foreground hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.map((msg, i) => (
          <div key={i}>
            {msg.role === "system" ? (
              <div className="text-xs text-zinc-500 bg-zinc-100/50 dark:bg-zinc-800/50 rounded-md px-3 py-2">
                {msg.content}
              </div>
            ) : msg.role === "user" ? (
              <div className="border-l-2 border-blue-500/30 pl-3">
                <div className="text-xs text-blue-800/60 dark:text-blue-300/50 whitespace-pre-wrap break-words">
                  {msg.content}
                </div>
              </div>
            ) : (
              <div className="text-xs [overflow-wrap:anywhere]">
                <Markdown>{msg.content}</Markdown>
              </div>
            )}
          </div>
        ))}

        {isStreaming && messages[messages.length - 1]?.content === "" && (
          <div className="flex items-center gap-1.5 text-xs text-zinc-500">
            <Loader2 className="w-3 h-3 animate-spin" />
            Thinking...
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-border p-2">
        <div className="relative">
          <Textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="e.g. Create a schedule that runs my-flow every hour..."
            className="text-xs bg-white dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700 min-h-[48px] max-h-[100px] resize-none w-full pr-9"
            disabled={isStreaming}
          />
          <Button
            size="sm"
            variant="ghost"
            className="absolute right-1 bottom-1 h-6 w-6 p-0"
            disabled={!input.trim() || isStreaming}
            onClick={() => {
              const text = input.trim();
              if (text) {
                setInput("");
                send(text);
              }
            }}
          >
            <Send className="h-3 w-3" />
          </Button>
        </div>
      </div>
    </div>
  );
}
