import { useState, useMemo } from "react";
import { FolderOpen, FileText, Search, ChevronRight, ChevronDown, Trash2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import {
  ContextMenu,
  ContextMenuTrigger,
  ContextMenuContent,
  ContextMenuItem,
} from "@/components/ui/context-menu";
import { FlowFileTree } from "./FlowFileTree";
import { cn } from "@/lib/utils";
import type { LocalFlow } from "@/lib/types";
import type { FlowFile } from "@/lib/api";

interface FlowFileListProps {
  flows: LocalFlow[];
  selectedName: string | undefined;
  onSelect: (flow: LocalFlow) => void;
  onDelete?: (flow: LocalFlow) => void;
  dirtyFlows: Set<string>;
  flowFiles?: FlowFile[];
  selectedFile?: string | null;
  onSelectFile?: (path: string) => void;
  onRefreshFiles?: () => void;
  isRefreshingFiles?: boolean;
}

export function FlowFileList({
  flows,
  selectedName,
  onSelect,
  onDelete,
  dirtyFlows,
  flowFiles,
  selectedFile,
  onSelectFile,
  onRefreshFiles,
  isRefreshingFiles,
}: FlowFileListProps) {
  const [filter, setFilter] = useState("");

  const filtered = useMemo(
    () =>
      filter
        ? flows.filter((f) =>
            f.name.toLowerCase().includes(filter.toLowerCase())
          )
        : flows,
    [flows, filter]
  );

  return (
    <div className="flex flex-col h-full">
      <div className="p-3 border-b border-border">
        <div className="relative">
          <Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-zinc-500" />
          <Input
            placeholder="Filter flows..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="pl-8 h-8 text-sm bg-zinc-900 border-zinc-700"
          />
        </div>
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {filtered.length === 0 && (
          <div className="px-3 py-8 text-center text-sm text-zinc-500">
            {flows.length === 0 ? "No flows found" : "No matching flows"}
          </div>
        )}
        {filtered.map((flow) => {
          const isSelected = flow.name === selectedName;
          const isExpandedDir = isSelected && flow.is_directory && flowFiles && flowFiles.length > 0;

          const rowContent = (
            <button
              onClick={() => onSelect(flow)}
              className={cn(
                "w-full text-left px-3 py-2 text-sm flex items-center gap-2 transition-colors",
                isSelected
                  ? "bg-zinc-800 text-foreground"
                  : "text-zinc-400 hover:text-foreground hover:bg-zinc-800/50"
              )}
            >
              {flow.is_directory ? (
                <>
                  {isSelected ? (
                    <ChevronDown className="w-3 h-3 shrink-0 text-zinc-500" />
                  ) : (
                    <ChevronRight className="w-3 h-3 shrink-0 text-zinc-500" />
                  )}
                  <FolderOpen className="w-3.5 h-3.5 shrink-0 text-blue-400" />
                </>
              ) : (
                <FileText className="w-3.5 h-3.5 shrink-0 text-zinc-500" />
              )}
              <div className="flex flex-col min-w-0 flex-1">
                <span className="truncate">{flow.name}</span>
                {flow.description && (
                  <span className="text-[10px] text-zinc-600 truncate leading-tight">
                    {flow.description}
                  </span>
                )}
              </div>
              <span className="ml-auto text-xs text-zinc-600 shrink-0">
                {flow.steps_count}
              </span>
              {dirtyFlows.has(flow.name) && (
                <span className="w-1.5 h-1.5 rounded-full bg-amber-400 shrink-0" />
              )}
            </button>
          );

          return (
            <div key={flow.path}>
              {onDelete ? (
                <ContextMenu>
                  <ContextMenuTrigger>
                    {rowContent}
                  </ContextMenuTrigger>
                  <ContextMenuContent>
                    <ContextMenuItem
                      variant="destructive"
                      onClick={() => onDelete(flow)}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      Delete flow
                    </ContextMenuItem>
                  </ContextMenuContent>
                </ContextMenu>
              ) : (
                rowContent
              )}
              {isExpandedDir && onSelectFile && (
                <div className="ml-3 border-l border-zinc-800 pl-1">
                  <FlowFileTree
                    files={flowFiles}
                    selectedFile={selectedFile ?? null}
                    onSelectFile={onSelectFile}
                    onRefresh={onRefreshFiles}
                    isRefreshing={isRefreshingFiles}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
