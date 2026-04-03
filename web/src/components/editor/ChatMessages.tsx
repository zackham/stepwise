import { useEffect, useRef } from "react";
import { Loader2, Check, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Markdown } from "@/components/ui/markdown";
import { ToolCard } from "@/components/jobs/StreamSegments";
import type { ChatMessage } from "@/hooks/useEditorChat";
import type { ToolCallState } from "@/hooks/useAgentStream";

interface ChatMessagesProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  onApplyYaml: (msgIdx: number, blockIdx: number) => void;
  emptyMessage?: string;
}

export function ChatMessages({ messages, isStreaming, onApplyYaml, emptyMessage }: ChatMessagesProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  return (
    <ScrollArea className="flex-1 min-h-0">
      <div ref={scrollRef} className="p-3 space-y-3">
        {messages.length === 0 && emptyMessage && (
          <p className="text-xs text-zinc-500">{emptyMessage}</p>
        )}

        {messages.map((msg, msgIdx) => (
          <div key={msgIdx} className="space-y-1.5">
            {/* Tool activities */}
            {msg.role === "assistant" && msg.toolActivities && msg.toolActivities.length > 0 && (
              <div className="space-y-0">
                {msg.toolActivities.map((tool) => {
                  // Build a descriptive title from tool name + input args
                  const arg = tool.input.file_path || tool.input.path || tool.input.command || tool.input.pattern || "";
                  const title = arg ? `${tool.name} ${arg}` : tool.name;
                  return (
                    <ToolCard
                      key={tool.id}
                      tool={{
                        id: tool.id,
                        title,
                        kind: tool.kind ?? tool.name,
                        status: tool.done ? "completed" : "running",
                      } satisfies ToolCallState}
                    />
                  );
                })}
              </div>
            )}

            {/* Message content */}
            {msg.content && (
              msg.role === "user" ? (
                <div className="border-l-2 border-blue-500/30 pl-3 mt-3 mb-2">
                  <div className="text-xs text-blue-800/60 dark:text-blue-300/50 whitespace-pre-wrap break-words">
                    {msg.content}
                  </div>
                </div>
              ) : (
                <div className="text-xs [overflow-wrap:anywhere]">
                  <Markdown>{msg.content}</Markdown>
                </div>
              )
            )}

            {/* Files changed */}
            {msg.filesChanged && msg.filesChanged.length > 0 && (
              <div className="my-1.5 px-2.5 py-1.5 bg-green-100/30 dark:bg-green-950/30 border border-green-300/30 dark:border-green-900/30 rounded text-[11px] text-green-700/80 dark:text-green-300/80">
                <div className="flex items-center gap-1.5 font-medium text-green-700 dark:text-green-300">
                  <Pencil className="w-3 h-3" />
                  {msg.filesChanged.length === 1 ? "1 file written" : `${msg.filesChanged.length} files written`}
                </div>
                <div className="mt-1 space-y-0.5 text-green-600/60 dark:text-green-400/60">
                  {msg.filesChanged.map((path) => (
                    <div key={path} className="font-mono truncate">{path}</div>
                  ))}
                </div>
              </div>
            )}

            {/* YAML blocks */}
            {msg.yamlBlocks?.map((block, blockIdx) => (
              <div
                key={block.apply_id}
                className="bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded overflow-hidden min-w-0"
              >
                <pre className="text-[11px] text-zinc-700 dark:text-zinc-300 p-2 overflow-x-auto max-h-48">
                  <code>{block.content}</code>
                </pre>
                <div className="flex items-center gap-1.5 p-1.5 bg-zinc-100/50 dark:bg-zinc-800/50 border-t border-zinc-300 dark:border-zinc-700">
                  {block.applied ? (
                    <Button size="sm" variant="ghost" disabled className="h-6 text-xs">
                      <Check className="w-3 h-3 mr-1 text-green-400" />
                      Applied
                    </Button>
                  ) : (
                    <Button
                      size="sm" variant="ghost"
                      onClick={() => onApplyYaml(msgIdx, blockIdx)}
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
  );
}
