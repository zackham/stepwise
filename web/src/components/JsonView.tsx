import { useState } from "react";
import { ChevronRight, ChevronDown, Copy, Check, Braces, List } from "lucide-react";
import { cn } from "@/lib/utils";

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
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div>
      {name && (
        <span className="text-zinc-400 text-sm block mb-1">{name}:</span>
      )}
      <div className="relative group rounded border border-zinc-700/30 bg-zinc-900/40 px-3 py-2">
        <button
          onClick={handleCopy}
          className="absolute top-2 right-2 text-zinc-500 hover:text-zinc-300 opacity-0 group-hover:opacity-100 transition-opacity"
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
            "whitespace-pre-wrap break-words text-sm text-zinc-200 font-mono leading-relaxed",
            !expanded && "max-h-[16rem] overflow-hidden"
          )}
        >
          {value}
        </div>
        {!expanded && (
          <div className="absolute bottom-8 left-0 right-0 h-12 bg-gradient-to-t from-zinc-900/90 to-transparent pointer-events-none" />
        )}
        {isLong && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-zinc-400 hover:text-zinc-200 mt-1"
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

function RawJsonView({ data }: { data: unknown }) {
  const [copied, setCopied] = useState(false);
  const json = JSON.stringify(data, null, 2);

  const handleCopy = () => {
    navigator.clipboard.writeText(json);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="relative group rounded border border-zinc-700/30 bg-zinc-900/40 px-3 py-2">
      <button
        onClick={handleCopy}
        className="absolute top-2 right-2 text-zinc-500 hover:text-zinc-300 opacity-0 group-hover:opacity-100 transition-opacity"
        title="Copy JSON"
      >
        {copied ? (
          <Check className="w-3 h-3 text-emerald-400" />
        ) : (
          <Copy className="w-3 h-3" />
        )}
      </button>
      <pre className="whitespace-pre-wrap break-words text-sm text-zinc-200 font-mono leading-relaxed">
        {json}
      </pre>
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
  const [rawJson, setRawJson] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  // At depth 0, show raw JSON toggle for objects and arrays
  const isStructured =
    depth === 0 &&
    data !== null &&
    data !== undefined &&
    typeof data === "object";

  if (isStructured && rawJson) {
    return (
      <div>
        <div className="flex justify-end mb-1">
          <button
            onClick={() => setRawJson(false)}
            className="flex items-center gap-1 text-[10px] text-zinc-500 hover:text-zinc-300"
            title="Switch to tree view"
          >
            <List className="w-3 h-3" />
            Tree
          </button>
        </div>
        <RawJsonView data={data} />
      </div>
    );
  }

  if (data === null || data === undefined) {
    return (
      <span className="text-zinc-500 italic">
        {name && <span className="text-zinc-400 mr-1">{name}:</span>}
        null
      </span>
    );
  }

  if (typeof data === "string") {
    const cls = classifyString(data);

    if (cls.type === "json") {
      return (
        <JsonView
          data={cls.parsed}
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
        {name && <span className="text-zinc-400 mr-1">{name}:</span>}
        <span className="text-zinc-200">{data}</span>
      </span>
    );
  }

  if (typeof data === "number" || typeof data === "boolean") {
    return (
      <span>
        {name && <span className="text-zinc-400 mr-1">{name}:</span>}
        <span className="text-blue-400">{String(data)}</span>
      </span>
    );
  }

  if (Array.isArray(data)) {
    if (data.length === 0) {
      return (
        <span>
          {name && <span className="text-zinc-400 mr-1">{name}:</span>}
          <span className="text-zinc-500">[]</span>
        </span>
      );
    }

    return (
      <div className={depth === 0 ? "overflow-x-auto" : undefined}>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1 hover:text-foreground text-zinc-400 text-sm"
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
          {depth === 0 && (
            <button
              onClick={() => setRawJson(true)}
              className="flex items-center gap-1 text-[10px] text-zinc-500 hover:text-zinc-300 ml-1"
              title="Switch to raw JSON"
            >
              <Braces className="w-3 h-3" />
              JSON
            </button>
          )}
        </div>
        {expanded && (
          <div className="ml-4 border-l border-zinc-700/50 pl-3 mt-1 space-y-0.5 min-w-0">
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
          {name && <span className="text-zinc-400 mr-1">{name}:</span>}
          <span className="text-zinc-500">{"{}"}</span>
        </span>
      );
    }

    return (
      <div className={depth === 0 ? "overflow-x-auto" : undefined}>
        <div className="flex items-center gap-1">
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1 hover:text-foreground text-zinc-400 text-sm"
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
            <>
              <button
                onClick={handleCopy}
                className="text-zinc-500 hover:text-zinc-300 ml-2"
                title="Copy JSON"
              >
                {copied ? (
                  <Check className="w-3 h-3 text-emerald-400" />
                ) : (
                  <Copy className="w-3 h-3" />
                )}
              </button>
              <button
                onClick={() => setRawJson(true)}
                className="flex items-center gap-1 text-[10px] text-zinc-500 hover:text-zinc-300 ml-1"
                title="Switch to raw JSON"
              >
                <Braces className="w-3 h-3" />
                JSON
              </button>
            </>
          )}
        </div>
        {expanded && (
          <div className="ml-4 border-l border-zinc-700/50 pl-3 mt-1 space-y-0.5 min-w-0">
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
      </div>
    );
  }

  return <span className="text-zinc-400">{String(data)}</span>;
}
