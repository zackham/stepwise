import { useState } from "react";
import { ChevronRight, ChevronDown, Copy, Check } from "lucide-react";
import { cn } from "@/lib/utils";

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
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(JSON.stringify(data, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  if (data === null || data === undefined) {
    return (
      <span className="text-zinc-500 italic">
        {name && <span className="text-zinc-400 mr-1">{name}:</span>}
        null
      </span>
    );
  }

  if (typeof data === "string") {
    return (
      <span>
        {name && <span className="text-zinc-400 mr-1">{name}:</span>}
        <span className="text-emerald-400">&quot;{data}&quot;</span>
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
      <div>
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
        {expanded && (
          <div className="ml-4 border-l border-zinc-700/50 pl-3 mt-1 space-y-0.5">
            {data.map((item, i) => (
              <div key={i} className="text-sm">
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
      <div>
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
          )}
        </div>
        {expanded && (
          <div className="ml-4 border-l border-zinc-700/50 pl-3 mt-1 space-y-0.5">
            {entries.map(([key, value]) => (
              <div key={key} className="text-sm">
                <JsonView
                  data={value}
                  name={key}
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

  return <span className="text-zinc-400">{String(data)}</span>;
}
