/* eslint-disable react-refresh/only-export-components */
import { useState } from "react";
import type { ToolCallState, StreamSegment } from "@/hooks/useAgentStream";
import {
  Search,
  FileText,
  Pencil,
  Terminal,
  Cog,
  Loader2,
  ChevronRight,
  X,
} from "lucide-react";
import { highlightMatches, countMatches } from "@/lib/log-search";
import { cn } from "@/lib/utils";
import { ContentModal } from "@/components/ui/content-modal";
import { Markdown } from "@/components/ui/markdown";

export function toolIcon(kind: string) {
  switch (kind) {
    case "search":
    case "Grep":
    case "Glob":
      return <Search className="w-3 h-3" />;
    case "read":
    case "Read":
      return <FileText className="w-3 h-3" />;
    case "write":
    case "Write":
    case "Edit":
      return <Pencil className="w-3 h-3" />;
    case "execute":
    case "Bash":
      return <Terminal className="w-3 h-3" />;
    default:
      return <Cog className="w-3 h-3" />;
  }
}

function extractFilePath(title: string | undefined, kind: string): string {
  return title?.replace(/^(Read|Write|Edit|Glob|Grep|Bash)\s+/i, "").replace(/\s+\(.*\)$/, "") || title || kind;
}

export function ToolCard({ tool }: { tool: ToolCallState }) {
  const [expanded, setExpanded] = useState(false);
  const isRunning = tool.status === "running";
  const isFailed = tool.status === "failed";
  const isCompleted = tool.status === "completed";
  const hasOutput = !!tool.output;
  const canExpand = hasOutput && !isRunning;
  const filePath = extractFilePath(tool.title, tool.kind);

  return (
    <div className="my-0.5">
      <button
        type="button"
        onClick={() => canExpand && setExpanded((v) => !v)}
        className={cn(
          "text-xs py-0.5 text-left w-full [overflow-wrap:anywhere]",
          canExpand && "cursor-pointer hover:text-zinc-200",
          !canExpand && "cursor-default",
          isRunning ? "text-blue-400" : isFailed ? "text-red-400" : "text-zinc-500",
        )}
      >
        <span className={cn(
          "inline-block w-1.5 h-1.5 rounded-full align-middle mr-1.5",
          isRunning ? "bg-blue-400 animate-pulse" : isFailed ? "bg-red-400" : "bg-emerald-500",
        )} />
        <span className={cn("", isRunning ? "text-blue-400" : isFailed ? "text-red-400" : "text-zinc-600")}>
          {tool.kind}
        </span>
        {" "}
        <span className={cn("", isCompleted && "text-zinc-500")}>
          {filePath}
        </span>
        {isRunning && <Loader2 className="w-2.5 h-2.5 animate-spin inline ml-1 align-middle" />}
        {isFailed && <X className="w-2.5 h-2.5 inline ml-1 align-middle" />}
      </button>
      {expanded && hasOutput && (
        <div className="ml-4 mt-0.5 mb-1">
          <pre className="text-[11px] text-zinc-600 dark:text-zinc-400 whitespace-pre-wrap break-words max-h-40 overflow-y-auto font-mono">
            {tool.output}
          </pre>
        </div>
      )}
    </div>
  );
}

/** Collapsed group of contiguous tool calls */
function ToolGroup({ tools }: { tools: ToolCallState[] }) {
  const [expanded, setExpanded] = useState(false);
  const failed = tools.filter((t) => t.status === "failed").length;
  const kinds = new Map<string, number>();
  for (const t of tools) {
    const k = t.kind || "tool";
    kinds.set(k, (kinds.get(k) || 0) + 1);
  }
  const summary = Array.from(kinds.entries())
    .map(([k, n]) => `${n} ${k}${n > 1 ? "s" : ""}`)
    .join(", ");

  return (
    <div className="my-1">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-1.5 text-xs text-zinc-500 hover:text-zinc-300 cursor-pointer py-0.5 transition-colors"
      >
        <ChevronRight className={cn("w-3 h-3 transition-transform shrink-0", expanded && "rotate-90")} />
        <span className="text-zinc-600">{summary}</span>
        {failed > 0 && <span className="text-red-400 text-[10px]">({failed} failed)</span>}
      </button>
      {expanded && (
        <div className="ml-1">
          {tools.map((tool, i) => (
            <ToolCard key={tool.id || i} tool={tool} />
          ))}
        </div>
      )}
    </div>
  );
}

export function PromptSegmentRow({ text }: { text: string }) {
  const [modalOpen, setModalOpen] = useState(false);

  return (
    <>
      <div
        className="relative mt-4 mb-3 cursor-pointer hover:opacity-80 transition-opacity border-l-2 border-blue-500/30 pl-3"
        onClick={() => setModalOpen(true)}
      >
        <div className="max-h-[7.5rem] overflow-hidden relative">
          <pre className="whitespace-pre-wrap text-xs text-blue-800/60 dark:text-blue-300/50 leading-relaxed">
            {text}
          </pre>
          <div className="absolute bottom-0 left-0 right-0 h-8 bg-gradient-to-t from-white dark:from-zinc-950 to-transparent pointer-events-none" />
        </div>
      </div>
      <ContentModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        title="Prompt"
        copyContent={text}
      >
        <pre className="whitespace-pre-wrap text-sm text-zinc-300 font-mono p-3 leading-relaxed max-h-[70vh] overflow-auto">
          {text}
        </pre>
      </ContentModal>
    </>
  );
}

export function SegmentRow({
  segment,
  searchRegex,
  hasActiveSearch,
}: {
  segment: StreamSegment;
  searchRegex?: RegExp | null;
  hasActiveSearch?: boolean;
}) {
  if (segment.type === "prompt") {
    return <PromptSegmentRow text={segment.text} />;
  }
  if (segment.type === "text") {
    const matches = searchRegex ? countMatches(segment.text, searchRegex) > 0 : true;
    if (searchRegex) {
      // When searching, fall back to raw text with highlights
      return (
        <div className={cn("my-1.5", hasActiveSearch && !matches && "opacity-40")}>
          <span className="whitespace-pre-wrap text-sm text-zinc-300 leading-relaxed">
            {highlightMatches(segment.text, searchRegex)}
          </span>
        </div>
      );
    }
    return (
      <div className={cn("my-1.5", hasActiveSearch && !matches && "opacity-40")}>
        <Markdown>{segment.text}</Markdown>
      </div>
    );
  }
  return <ToolCard tool={segment.tool} />;
}

/**
 * Render a list of segments, collapsing contiguous runs of 3+ tool calls
 * into an expandable summary.
 */
export function SegmentList({
  segments,
  searchRegex,
  hasActiveSearch,
}: {
  segments: StreamSegment[];
  searchRegex?: RegExp | null;
  hasActiveSearch?: boolean;
}) {
  const groups: Array<{ type: "segment"; index: number } | { type: "tool_group"; tools: ToolCallState[] }> = [];
  let i = 0;
  while (i < segments.length) {
    if (segments[i].type === "tool") {
      // Collect contiguous tool segments
      const toolStart = i;
      const tools: ToolCallState[] = [];
      while (i < segments.length && segments[i].type === "tool") {
        tools.push((segments[i] as { type: "tool"; tool: ToolCallState }).tool);
        i++;
      }
      if (tools.length >= 3) {
        groups.push({ type: "tool_group", tools });
      } else {
        // Render individually
        for (let j = toolStart; j < i; j++) {
          groups.push({ type: "segment", index: j });
        }
      }
    } else {
      groups.push({ type: "segment", index: i });
      i++;
    }
  }

  return (
    <>
      {groups.map((g, gi) => {
        if (g.type === "tool_group") {
          return <ToolGroup key={`tg-${gi}`} tools={g.tools} />;
        }
        return (
          <SegmentRow
            key={g.index}
            segment={segments[g.index]}
            searchRegex={searchRegex}
            hasActiveSearch={hasActiveSearch}
          />
        );
      })}
    </>
  );
}
