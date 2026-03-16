import { useState, useCallback, useEffect, useRef } from "react";
import {
  Play,
  Pencil,
  Trash2,
  User,
  Tag,
  Hash,
  Clock,
  Plus,
  X,
  Check,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import type { LocalFlow, LocalFlowDetail, FlowMetadata } from "@/lib/types";

interface LocalFlowInfoPanelProps {
  flow: LocalFlow;
  detail: LocalFlowDetail | undefined;
  onEdit: () => void;
  onRun: () => void;
  onDelete: () => void;
  onPatchMetadata: (metadata: Partial<FlowMetadata>) => void;
}

function InlineEdit({
  value,
  onSave,
  placeholder,
  multiline,
}: {
  value: string;
  onSave: (value: string) => void;
  placeholder: string;
  multiline?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement>(null);

  useEffect(() => {
    setDraft(value);
  }, [value]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const commit = useCallback(() => {
    const trimmed = draft.trim();
    if (trimmed !== value) {
      onSave(trimmed);
    }
    setEditing(false);
  }, [draft, value, onSave]);

  if (!editing) {
    return (
      <button
        onClick={() => setEditing(true)}
        className="text-left w-full group/edit flex items-start gap-1"
      >
        <span className={value ? "text-zinc-400" : "text-zinc-600 italic"}>
          {value || placeholder}
        </span>
        <Pencil className="w-3 h-3 text-zinc-600 opacity-0 group-hover/edit:opacity-100 shrink-0 mt-0.5" />
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
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            commit();
          }
          if (e.key === "Escape") {
            setDraft(value);
            setEditing(false);
          }
        }}
        placeholder={placeholder}
        className="w-full text-sm bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-zinc-300 resize-none"
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
        if (e.key === "Escape") {
          setDraft(value);
          setEditing(false);
        }
      }}
      placeholder={placeholder}
      className="h-7 text-sm bg-zinc-900 border-zinc-700"
    />
  );
}

function TagEditor({
  tags,
  onSave,
}: {
  tags: string[];
  onSave: (tags: string[]) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [newTag, setNewTag] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (adding) inputRef.current?.focus();
  }, [adding]);

  const addTag = useCallback(() => {
    const tag = newTag.trim().toLowerCase();
    if (tag && !tags.includes(tag)) {
      onSave([...tags, tag]);
    }
    setNewTag("");
    setAdding(false);
  }, [newTag, tags, onSave]);

  const removeTag = useCallback(
    (tag: string) => {
      onSave(tags.filter((t) => t !== tag));
    },
    [tags, onSave]
  );

  return (
    <div className="flex flex-wrap gap-1">
      {tags.map((tag) => (
        <span
          key={tag}
          className="text-[10px] bg-zinc-800 text-zinc-400 px-1.5 py-0.5 rounded flex items-center gap-1 group/tag"
        >
          {tag}
          <button
            onClick={() => removeTag(tag)}
            className="opacity-0 group-hover/tag:opacity-100"
          >
            <X className="w-2.5 h-2.5" />
          </button>
        </span>
      ))}
      {adding ? (
        <span className="flex items-center gap-0.5">
          <Input
            ref={inputRef}
            value={newTag}
            onChange={(e) => setNewTag(e.target.value)}
            onBlur={addTag}
            onKeyDown={(e) => {
              if (e.key === "Enter") addTag();
              if (e.key === "Escape") {
                setNewTag("");
                setAdding(false);
              }
            }}
            placeholder="tag"
            className="h-5 w-16 text-[10px] bg-zinc-900 border-zinc-700 px-1"
          />
          <button onClick={addTag}>
            <Check className="w-3 h-3 text-zinc-500" />
          </button>
        </span>
      ) : (
        <button
          onClick={() => setAdding(true)}
          className="text-[10px] bg-zinc-800/50 text-zinc-600 px-1.5 py-0.5 rounded hover:text-zinc-400"
        >
          <Plus className="w-2.5 h-2.5 inline" />
        </button>
      )}
    </div>
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

export function LocalFlowInfoPanel({
  flow,
  detail,
  onEdit,
  onRun,
  onDelete,
  onPatchMetadata,
}: LocalFlowInfoPanelProps) {
  const metadata = detail?.flow?.metadata;

  return (
    <div className="flex flex-col h-full">
      <div className="p-4 border-b border-border">
        <h3 className="font-semibold text-foreground">{flow.name}</h3>
        <div className="flex items-center gap-2 mt-1 text-xs text-zinc-500">
          {(metadata?.author || flow.description) && (
            <>
              {metadata?.author && (
                <span className="flex items-center gap-1">
                  <User className="w-3 h-3" />
                  {metadata.author}
                </span>
              )}
              {metadata?.version && <span>v{metadata.version}</span>}
            </>
          )}
        </div>
      </div>

      <ScrollArea className="flex-1 min-h-0">
        <div className="p-4 space-y-4">
          {/* Description */}
          <div className="space-y-1">
            <span className="text-xs text-zinc-500">Description</span>
            <div className="text-sm">
              <InlineEdit
                value={metadata?.description || flow.description || ""}
                onSave={(v) => onPatchMetadata({ description: v })}
                placeholder="Add description..."
                multiline
              />
            </div>
          </div>

          {/* Stats */}
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="flex items-center gap-1.5 text-zinc-500">
              <Hash className="w-3.5 h-3.5" />
              <span>
                {flow.steps_count} step{flow.steps_count !== 1 && "s"}
              </span>
            </div>
            {flow.modified_at && (
              <div className="flex items-center gap-1.5 text-zinc-500">
                <Clock className="w-3.5 h-3.5" />
                <span>{formatRelativeTime(flow.modified_at)}</span>
              </div>
            )}
          </div>

          <Separator />

          {/* Author + Version */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <span className="text-xs text-zinc-500">Author</span>
              <div className="text-sm">
                <InlineEdit
                  value={metadata?.author || ""}
                  onSave={(v) => onPatchMetadata({ author: v })}
                  placeholder="Add author..."
                />
              </div>
            </div>
            <div className="space-y-1">
              <span className="text-xs text-zinc-500">Version</span>
              <div className="text-sm">
                <InlineEdit
                  value={metadata?.version || ""}
                  onSave={(v) => onPatchMetadata({ version: v })}
                  placeholder="0.1.0"
                />
              </div>
            </div>
          </div>

          {/* Executor types */}
          {(flow.executor_types?.length ?? 0) > 0 && (
            <>
              <Separator />
              <div className="space-y-1.5">
                <span className="text-xs text-zinc-500">Executors</span>
                <div className="flex flex-wrap gap-1">
                  {(flow.executor_types ?? []).map((t) => (
                    <span
                      key={t}
                      className="text-[10px] font-mono bg-zinc-800 text-zinc-400 px-1.5 py-0.5 rounded"
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* Tags */}
          <Separator />
          <div className="space-y-1.5">
            <span className="text-xs text-zinc-500 flex items-center gap-1">
              <Tag className="w-3 h-3" /> Tags
            </span>
            <TagEditor
              tags={metadata?.tags || []}
              onSave={(tags) => onPatchMetadata({ tags })}
            />
          </div>
        </div>
      </ScrollArea>

      <div className="p-4 border-t border-border flex gap-2">
        <Button
          onClick={onRun}
          size="sm"
          className="flex-1"
        >
          <Play className="w-3.5 h-3.5 mr-1.5" />
          Run
        </Button>
        <Button
          onClick={onEdit}
          variant="outline"
          size="sm"
          className="flex-1"
        >
          <Pencil className="w-3.5 h-3.5 mr-1.5" />
          Edit
        </Button>
        <Button
          onClick={onDelete}
          variant="ghost"
          size="sm"
          className="text-red-400 hover:text-red-300"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </Button>
      </div>
    </div>
  );
}
