import { useState, useCallback, useEffect, useRef } from "react";
import {
  Play,
  Trash2,
  Pencil,
  Download,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { FlowConfigPanel } from "@/components/editor/FlowConfigPanel";
import type { LocalFlow, LocalFlowDetail, FlowMetadata } from "@/lib/types";

// --- Shared inline editing components ---

function InlineEdit({
  value,
  onSave,
  placeholder,
  multiline,
  readOnly,
}: {
  value: string;
  onSave: (value: string) => void;
  placeholder: string;
  multiline?: boolean;
  readOnly?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null);

  useEffect(() => { setDraft(value); }, [value]);
  useEffect(() => { if (editing) inputRef.current?.focus(); }, [editing]);

  const commit = useCallback(() => {
    const trimmed = draft.trim();
    if (trimmed !== value) onSave(trimmed);
    setEditing(false);
  }, [draft, value, onSave]);

  if (readOnly || !editing) {
    return (
      <button
        onClick={() => !readOnly && setEditing(true)}
        className={`text-left w-full group/edit flex items-start gap-1 ${readOnly ? "cursor-default" : ""}`}
        disabled={readOnly}
      >
        <span className={value ? "text-zinc-500 dark:text-zinc-400" : "text-zinc-400 dark:text-zinc-600 italic"}>
          {value || placeholder}
        </span>
        {!readOnly && (
          <Pencil className="w-3 h-3 text-zinc-600 opacity-0 group-hover/edit:opacity-100 shrink-0 mt-0.5" />
        )}
      </button>
    );
  }

  if (multiline) {
    return (
      <textarea
        ref={inputRef as React.RefObject<HTMLTextAreaElement>}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); commit(); }
          if (e.key === "Escape") { setDraft(value); setEditing(false); }
        }}
        placeholder={placeholder}
        className="w-full text-sm bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300 resize-none"
        rows={3}
      />
    );
  }

  return (
    <Input
      ref={inputRef as React.RefObject<HTMLInputElement>}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") { setDraft(value); setEditing(false); }
      }}
      placeholder={placeholder}
      className="h-7 text-sm bg-white dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700"
    />
  );
}

function formatRelativeTime(isoDate: string): string {
  if (!isoDate) return "";
  const diff = Date.now() - new Date(isoDate).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return `${Math.floor(days / 30)}mo ago`;
}

// --- Main component ---

interface FlowOverviewProps {
  flow: LocalFlow;
  detail: LocalFlowDetail | undefined;
  onRun: () => void;
  onDelete?: () => void;
  onInstall?: () => void;
  isInstalling?: boolean;
  isInstalled?: boolean;
  onPatchMetadata: (metadata: Partial<FlowMetadata>) => void;
  readOnly?: boolean;
}

export function FlowOverview({
  flow,
  detail,
  onRun,
  onDelete,
  onInstall,
  isInstalling,
  isInstalled,
  onPatchMetadata,
  readOnly,
}: FlowOverviewProps) {
  const metadata = detail?.flow?.metadata;
  const isRegistry = flow.source === "registry";

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="p-3 border-b border-border">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-semibold text-foreground truncate">{flow.name}</h3>
          {isRegistry && (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-violet-100 dark:bg-violet-900/40 text-violet-600 dark:text-violet-400 uppercase tracking-wider font-medium shrink-0">
              Registry
            </span>
          )}
        </div>
        <p className="text-[10px] font-mono text-zinc-500 mt-0.5 truncate">{flow.path}</p>
      </div>

      {/* Scrollable content */}
      <ScrollArea className="flex-1 min-h-0">
        <div className="p-3 space-y-4">
          {/* Description */}
          <div className="space-y-1">
            <span className="text-[10px] font-medium text-zinc-500 uppercase tracking-wider">Description</span>
            <div className="text-xs">
              <InlineEdit
                value={metadata?.description || flow.description || ""}
                onSave={(v) => onPatchMetadata({ description: v })}
                placeholder="Add description..."
                multiline
                readOnly={readOnly}
              />
            </div>
          </div>

          {/* Info grid */}
          <div className="text-xs space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="text-zinc-500 w-16">Steps</span>
              <span className="font-mono text-zinc-700 dark:text-zinc-300">{flow.steps_count}</span>
            </div>
            {flow.modified_at && (
              <div className="flex items-center gap-2">
                <span className="text-zinc-500 w-16">Modified</span>
                <span className="font-mono text-zinc-500 text-[10px]">{formatRelativeTime(flow.modified_at)}</span>
              </div>
            )}
            {metadata?.author && (
              <div className="flex items-center gap-2">
                <span className="text-zinc-500 w-16">Author</span>
                <span className="text-zinc-700 dark:text-zinc-300">{metadata.author}</span>
              </div>
            )}
            {metadata?.version && (
              <div className="flex items-center gap-2">
                <span className="text-zinc-500 w-16">Version</span>
                <span className="font-mono text-zinc-700 dark:text-zinc-300">{metadata.version}</span>
              </div>
            )}
          </div>

          {/* Editable author/version (non-registry only) */}
          {!readOnly && (
            <>
              <Separator />
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <span className="text-[10px] font-medium text-zinc-500 uppercase tracking-wider">Author</span>
                  <div className="text-xs">
                    <InlineEdit
                      value={metadata?.author || ""}
                      onSave={(v) => onPatchMetadata({ author: v })}
                      placeholder="Add author..."
                    />
                  </div>
                </div>
                <div className="space-y-1">
                  <span className="text-[10px] font-medium text-zinc-500 uppercase tracking-wider">Version</span>
                  <div className="text-xs">
                    <InlineEdit
                      value={metadata?.version || ""}
                      onSave={(v) => onPatchMetadata({ version: v })}
                      placeholder="0.1.0"
                    />
                  </div>
                </div>
              </div>
            </>
          )}

          {/* Executor types */}
          {(flow.executor_types?.length ?? 0) > 0 && (
            <>
              <Separator />
              <div className="space-y-1.5">
                <span className="text-[10px] font-medium text-zinc-500 uppercase tracking-wider">Executors</span>
                <div className="flex flex-wrap gap-1">
                  {(flow.executor_types ?? []).map((t) => (
                    <span
                      key={t}
                      className="text-[10px] font-mono bg-zinc-200 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 px-1.5 py-0.5 rounded"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* Config panel (inputs + config vars) */}
          {!isRegistry && (
            <>
              <Separator />
              <FlowConfigPanel flowPath={flow.path} />
            </>
          )}
        </div>
      </ScrollArea>

      {/* Action buttons */}
      <div className="p-3 border-t border-border flex gap-2">
        {isRegistry && onInstall ? (
          <Button
            onClick={onInstall}
            size="sm"
            className="flex-1"
            disabled={isInstalling || isInstalled}
          >
            <Download className="w-3.5 h-3.5 mr-1.5" />
            {isInstalled ? "Installed" : isInstalling ? "Installing..." : "Install"}
          </Button>
        ) : (
          <>
            <Button onClick={onRun} size="sm" className="flex-1">
              <Play className="w-3.5 h-3.5 mr-1.5" />
              Run
            </Button>
            {onDelete && (
              <Button
                onClick={onDelete}
                variant="ghost"
                size="sm"
                className="text-red-500 dark:text-red-400 hover:text-red-600 dark:hover:text-red-300"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </Button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
