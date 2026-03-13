import { useEffect, useRef } from "react";
import {
  Loader2, Check, FileText, Search, Pencil, Eye,
  ChevronDown, ChevronRight, Terminal,
} from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { ChatMessage, ToolActivity } from "@/hooks/useEditorChat";

/** Collapsible tool activity summary. */
function ToolActivitiesBlock({ tools, isStreaming }: { tools: ToolActivity[]; isStreaming: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const allDone = tools.every((t) => t.done);
  const showExpanded = expanded || isStreaming || !allDone;

  const TOOL_ICONS: Record<string, typeof FileText> = {
    edit: Pencil,
    read: Eye,
    search: Search,
    command: Terminal,
  };

  if (!showExpanded) {
    return (
      <button
        onClick={() => setExpanded(true)}
        className="flex items-center gap-1.5 text-[11px] text-zinc-600 hover:text-zinc-400 transition-colors"
      >
        <ChevronRight className="w-3 h-3" />
        <Check className="w-3 h-3 text-green-600" />
        <span>{tools.length} tool {tools.length === 1 ? "call" : "calls"}</span>
      </button>
    );
  }

  return (
    <div className="space-y-1 my-1">
      {allDone && (
        <button
          onClick={() => setExpanded(false)}
          className="flex items-center gap-1.5 text-[11px] text-zinc-600 hover:text-zinc-400 transition-colors"
        >
          <ChevronDown className="w-3 h-3" />
          <span>{tools.filter(t => t.done).length} tool {tools.filter(t => t.done).length === 1 ? "call" : "calls"}</span>
        </button>
      )}
      {tools.map((tool) => {
        const IconComponent = TOOL_ICONS[tool.kind ?? ""] ?? FileText;
        return (
          <div key={tool.id} className="flex items-center gap-1.5 text-[11px] text-zinc-500">
            {tool.done ? (
              <Check className="w-3 h-3 text-green-500 shrink-0" />
            ) : (
              <Loader2 className="w-3 h-3 animate-spin shrink-0" />
            )}
            <IconComponent className="w-3 h-3 shrink-0" />
            <span className="truncate">{tool.name}</span>
          </div>
        );
      })}
    </div>
  );
}

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
            {msg.role === "assistant" && msg.toolActivities && msg.toolActivities.length > 0 && (
              <ToolActivitiesBlock
                tools={msg.toolActivities}
                isStreaming={isStreaming && msgIdx === messages.length - 1}
              />
            )}

            <div className={`text-xs ${
              msg.role === "user"
                ? "text-blue-300 bg-blue-950/30 rounded px-2.5 py-1.5"
                : "text-zinc-300"
            }`}>
              <span className="whitespace-pre-wrap">{msg.content}</span>
            </div>

            {msg.filesChanged && msg.filesChanged.length > 0 && (
              <div className="my-1.5 px-2.5 py-1.5 bg-green-950/30 border border-green-900/30 rounded text-[11px] text-green-300/80">
                <div className="flex items-center gap-1.5 font-medium text-green-300">
                  <Pencil className="w-3 h-3" />
                  {msg.filesChanged.length === 1 ? "1 file written" : `${msg.filesChanged.length} files written`}
                </div>
                <div className="mt-1 space-y-0.5 text-green-400/60">
                  {msg.filesChanged.map((path) => (
                    <div key={path} className="font-mono truncate">{path}</div>
                  ))}
                </div>
              </div>
            )}

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
