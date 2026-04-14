import { useState } from "react";
import { ChevronRight, ChevronDown, Copy, Check, Maximize2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { ContentModal } from "@/components/ui/content-modal";
import { copyToClipboard } from "@/hooks/useCopyFeedback";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@/components/ui/tooltip";

type StringClass =
  | { type: "inline" }
  | { type: "block" }
  | { type: "json"; parsed: unknown };

function classifyString(s: string): StringClass {
  const trimmed = s.trimStart();
  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      return { type: "json", parsed: JSON.parse(s) };
    } catch {
      // not valid JSON, fall through
    }
  }
  if (s.includes("\n")) return { type: "block" };
  return { type: "inline" };
}

const COLLAPSE_THRESHOLD = 12;

function BlockString({ value, name }: { value: string; name?: string }) {
  const lineCount = value.split("\n").length;
  const isLong = lineCount > COLLAPSE_THRESHOLD;
  const [expanded, setExpanded] = useState(!isLong);
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    copyToClipboard(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div>
      {name && (
        <span className="text-zinc-500 dark:text-zinc-400 text-sm block mb-1">{name}:</span>
      )}
      <div className="relative group rounded border border-zinc-300/30 dark:border-zinc-700/30 bg-zinc-100/40 dark:bg-zinc-900/40 px-3 py-2">
        <button
          onClick={handleCopy}
          className="absolute top-2 right-2 text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 opacity-0 group-hover:opacity-100 transition-opacity"
          title="Copy text"
        >
          {copied ? (
            <Check className="w-3 h-3 text-emerald-400" />
          ) : (
            <Copy className="w-3 h-3" />
          )}
        </button>
        <div
          className={cn(
            "whitespace-pre-wrap break-words text-sm text-zinc-800 dark:text-zinc-200 font-mono leading-relaxed",
            !expanded && "max-h-[16rem] overflow-hidden"
          )}
        >
          {value}
        </div>
        {!expanded && (
          <div className="absolute bottom-8 left-0 right-0 h-12 bg-gradient-to-t from-zinc-100/90 dark:from-zinc-900/90 to-transparent pointer-events-none" />
        )}
        {isLong && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-zinc-500 dark:text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 mt-1"
          >
            {expanded
              ? "Show less"
              : `Show more (${lineCount} lines)`}
          </button>
        )}
      </div>
    </div>
  );
}

function JsonStringWrapper({
  raw,
  parsed,
  name,
  defaultExpanded,
  depth,
}: {
  raw: string;
  parsed: unknown;
  name?: string;
  defaultExpanded: boolean;
  depth: number;
}) {
  const [showRaw, setShowRaw] = useState(false);

  const badge = (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger
          onClick={() => setShowRaw(!showRaw)}
          className={cn(
            "inline-flex items-center gap-0.5 text-[10px] rounded px-1 py-0 transition-colors shrink-0 cursor-pointer",
            showRaw
              ? "text-zinc-500 dark:text-zinc-400 bg-zinc-500/10 border border-zinc-500/20 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-500/20"
              : "text-amber-400 bg-amber-500/10 border border-amber-500/20 hover:text-amber-300 hover:bg-amber-500/20"
          )}
        >
          {showRaw ? "raw" : "JSON string"}
        </TooltipTrigger>
        <TooltipContent>
          {showRaw
            ? "Value is a string containing valid JSON — click to view parsed"
            : "Value is a string containing valid JSON — click to view raw"}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );

  if (showRaw) {
    const hasNewlines = raw.includes("\n");
    return (
      <div>
        <div className="flex items-center gap-1.5 mb-0.5">
          {name && <span className="text-zinc-500 dark:text-zinc-400 text-sm mr-1">{name}:</span>}
          {badge}
        </div>
        {hasNewlines ? (
          <BlockString value={raw} />
        ) : (
          <span className="text-zinc-800 dark:text-zinc-200 text-sm break-all">{raw}</span>
        )}
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      <div className="min-w-0">
        <JsonView
          data={parsed}
          name={name}
          defaultExpanded={defaultExpanded}
          depth={depth}
        />
      </div>
      {badge}
    </div>
  );
}

function JsonObjectView({
  data,
  entries,
  name,
  expanded,
  setExpanded,
  copied,
  handleCopy,
  isSingleKeyObject,
  depth,
}: {
  data: Record<string, unknown>;
  entries: [string, unknown][];
  name?: string;
  expanded: boolean;
  setExpanded: (v: boolean) => void;
  copied: boolean;
  handleCopy: () => void;
  isSingleKeyObject: boolean;
  depth: number;
}) {
  const [modalOpen, setModalOpen] = useState(false);
  const showExpandButton = depth === 0 && entries.length > 3;

  return (
    <div className={cn(depth === 0 ? "overflow-x-auto relative" : undefined)}>
      <div className="flex items-center gap-1">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 hover:text-foreground text-zinc-500 dark:text-zinc-400 text-sm"
        >
          {expanded ? (
            <ChevronDown className="w-3 h-3" />
          ) : (
            <ChevronRight className="w-3 h-3" />
          )}
          {name && <span className="mr-1">{name}:</span>}
          <span className="text-zinc-500">
            {"{"}
            {entries.length} key{entries.length !== 1 ? "s" : ""}
            {"}"}
          </span>
        </button>
        {depth === 0 && (
          <button
            onClick={handleCopy}
            className="text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 ml-2"
            title="Copy JSON"
          >
            {copied ? (
              <Check className="w-3 h-3 text-emerald-400" />
            ) : (
              <Copy className="w-3 h-3" />
            )}
          </button>
        )}
        {showExpandButton && (
          <button
            onClick={() => setModalOpen(true)}
            className="text-zinc-600 hover:text-zinc-400 ml-1 transition-colors"
            title="Expand in modal"
          >
            <Maximize2 className="w-3 h-3" />
          </button>
        )}
      </div>
      {expanded && (
        <div className="ml-4 border-l border-zinc-300/50 dark:border-zinc-700/50 pl-3 mt-1 space-y-0.5 min-w-0">
          {entries.map(([key, value]) => (
            <div key={key} className="text-sm min-w-0">
              <JsonView
                data={value}
                name={key}
                defaultExpanded={isSingleKeyObject || depth < 1}
                depth={depth + 1}
              />
            </div>
          ))}
        </div>
      )}
      {showExpandButton && (
        <ContentModal open={modalOpen} onOpenChange={setModalOpen} title={name ?? "JSON"}>
          <pre className="text-sm text-zinc-300 font-mono p-2 whitespace-pre-wrap break-words">
            {JSON.stringify(data, null, 2)}
          </pre>
        </ContentModal>
      )}
    </div>
  );
}

interface JsonViewProps {
  data: unknown;
  name?: string;
  defaultExpanded?: boolean;
  depth?: number;
}

export function JsonView({
  data,
  name,
  defaultExpanded = true,
  depth = 0,
}: JsonViewProps) {
  const isSingleKeyObject =
    typeof data === "object" &&
    data !== null &&
    !Array.isArray(data) &&
    Object.keys(data as Record<string, unknown>).length === 1;
  const [expanded, setExpanded] = useState(defaultExpanded || isSingleKeyObject);
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    copyToClipboard(JSON.stringify(data, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  if (data === null || data === undefined) {
    return (
      <span className="text-zinc-500 italic">
        {name && <span className="text-zinc-500 dark:text-zinc-400 mr-1">{name}:</span>}
        null
      </span>
    );
  }

  if (typeof data === "string") {
    const cls = classifyString(data);

    if (cls.type === "json") {
      return (
        <JsonStringWrapper
          raw={data}
          parsed={cls.parsed}
          name={name}
          defaultExpanded={depth < 1}
          depth={depth + 1}
        />
      );
    }

    if (cls.type === "block") {
      return <BlockString value={data} name={name} />;
    }

    // inline
    return (
      <span>
        {name && <span className="text-zinc-500 dark:text-zinc-400 mr-1">{name}:</span>}
        <span className="text-zinc-800 dark:text-zinc-200">{data}</span>
      </span>
    );
  }

  if (typeof data === "number" || typeof data === "boolean") {
    return (
      <span>
        {name && <span className="text-zinc-500 dark:text-zinc-400 mr-1">{name}:</span>}
        <span className="text-blue-600 dark:text-blue-400">{String(data)}</span>
      </span>
    );
  }

  if (Array.isArray(data)) {
    if (data.length === 0) {
      return (
        <span>
          {name && <span className="text-zinc-500 dark:text-zinc-400 mr-1">{name}:</span>}
          <span className="text-zinc-500">[]</span>
        </span>
      );
    }

    return (
      <div className={depth === 0 ? "overflow-x-auto" : undefined}>
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 hover:text-foreground text-zinc-500 dark:text-zinc-400 text-sm"
        >
          {expanded ? (
            <ChevronDown className="w-3 h-3" />
          ) : (
            <ChevronRight className="w-3 h-3" />
          )}
          {name && <span className="mr-1">{name}:</span>}
          <span className="text-zinc-500">
            [{data.length} item{data.length !== 1 ? "s" : ""}]
          </span>
        </button>
        {expanded && (
          <div className="ml-4 border-l border-zinc-300/50 dark:border-zinc-700/50 pl-3 mt-1 space-y-0.5 min-w-0">
            {data.map((item, i) => (
              <div key={i} className="text-sm min-w-0">
                <JsonView
                  data={item}
                  name={String(i)}
                  defaultExpanded={depth < 1}
                  depth={depth + 1}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  if (typeof data === "object") {
    const entries = Object.entries(data as Record<string, unknown>);
    if (entries.length === 0) {
      return (
        <span>
          {name && <span className="text-zinc-500 dark:text-zinc-400 mr-1">{name}:</span>}
          <span className="text-zinc-500">{"{}"}</span>
        </span>
      );
    }

    return (
      <JsonObjectView
        data={data as Record<string, unknown>}
        entries={entries}
        name={name}
        expanded={expanded}
        setExpanded={setExpanded}
        copied={copied}
        handleCopy={handleCopy}
        isSingleKeyObject={isSingleKeyObject}
        depth={depth}
      />
    );
  }

  return <span className="text-zinc-500 dark:text-zinc-400">{String(data)}</span>;
}
