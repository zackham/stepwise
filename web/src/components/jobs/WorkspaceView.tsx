import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchJobWorkspace,
  fetchJobWorkspaceFile,
  type WorkspaceEntry,
} from "@/lib/api";
import { ChevronRight, Folder, FileText, FileJson, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";

interface WorkspaceViewProps {
  jobId: string;
}

function formatSize(bytes: number | null): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function iconFor(entry: WorkspaceEntry) {
  if (entry.is_dir) return <Folder className="w-3.5 h-3.5 text-amber-500" />;
  if (entry.name.endsWith(".json") || entry.name.endsWith(".jsonl")) {
    return <FileJson className="w-3.5 h-3.5 text-blue-400" />;
  }
  return <FileText className="w-3.5 h-3.5 text-zinc-500" />;
}

/** Tree node — displays one directory row and, when expanded, its contents.
 * Simple recursive fetch: each dir loads on expand. Keeps one level-at-a-time
 * load so large job workspaces don't blow up. */
function DirNode({
  jobId,
  entry,
  depth,
  onSelectFile,
  selectedPath,
}: {
  jobId: string;
  entry: WorkspaceEntry;
  depth: number;
  onSelectFile: (path: string) => void;
  selectedPath: string | null;
}) {
  const [open, setOpen] = useState(depth === 0);
  const { data: listing } = useQuery({
    queryKey: ["workspace", jobId, entry.path],
    queryFn: () => fetchJobWorkspace(jobId, entry.path),
    enabled: open,
    staleTime: 10_000,
  });

  return (
    <>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 w-full px-2 py-1 text-xs hover:bg-zinc-100 dark:hover:bg-zinc-900/50 text-left"
        style={{ paddingLeft: `${depth * 12 + 8}px` }}
      >
        <ChevronRight
          className={cn(
            "w-3 h-3 text-zinc-500 transition-transform shrink-0",
            open && "rotate-90",
          )}
        />
        {iconFor(entry)}
        <span className="font-mono text-zinc-300 truncate flex-1">{entry.name}</span>
      </button>
      {open && listing?.entries.map((child) =>
        child.is_dir ? (
          <DirNode
            key={child.path}
            jobId={jobId}
            entry={child}
            depth={depth + 1}
            onSelectFile={onSelectFile}
            selectedPath={selectedPath}
          />
        ) : (
          <FileRow
            key={child.path}
            entry={child}
            depth={depth + 1}
            selected={selectedPath === child.path}
            onClick={() => onSelectFile(child.path)}
          />
        ),
      )}
    </>
  );
}

function FileRow({
  entry,
  depth,
  selected,
  onClick,
}: {
  entry: WorkspaceEntry;
  depth: number;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1.5 w-full px-2 py-1 text-xs text-left transition-colors",
        selected
          ? "bg-blue-900/30 border-l-2 border-blue-500"
          : "hover:bg-zinc-100 dark:hover:bg-zinc-900/50",
      )}
      style={{ paddingLeft: `${depth * 12 + 8 + 12}px` }}
    >
      {iconFor(entry)}
      <span className="font-mono text-zinc-300 truncate flex-1">{entry.name}</span>
      <span className="text-[10px] text-zinc-600 font-mono shrink-0">
        {formatSize(entry.size)}
      </span>
    </button>
  );
}

function FilePreview({ jobId, path }: { jobId: string; path: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["workspace", jobId, "file", path],
    queryFn: () => fetchJobWorkspaceFile(jobId, path),
    staleTime: 5_000,
  });

  if (isLoading) {
    return (
      <div className="p-4 text-xs text-zinc-500">Loading {path}…</div>
    );
  }
  if (error) {
    return (
      <div className="p-4 text-xs text-red-400 flex items-center gap-2">
        <AlertCircle className="w-3.5 h-3.5" />
        {(error as Error).message}
      </div>
    );
  }
  if (!data) return null;

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="shrink-0 flex items-baseline gap-3 px-3 py-2 border-b border-border bg-zinc-50/50 dark:bg-zinc-950/50">
        <span className="text-xs font-mono text-zinc-300 truncate">{data.path}</span>
        <span className="text-[10px] text-zinc-500 font-mono">{formatSize(data.size)}</span>
        {data.truncated && (
          <span className="text-[10px] text-amber-400">truncated to 512 KB</span>
        )}
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        {data.is_binary ? (
          <div className="p-4 text-xs text-zinc-500">
            Binary file ({formatSize(data.size)}) — preview unavailable.
          </div>
        ) : (
          <pre className="font-mono text-[11px] text-zinc-200 whitespace-pre-wrap p-3">
            {data.content ?? ""}
          </pre>
        )}
      </div>
    </div>
  );
}

export function WorkspaceView({ jobId }: WorkspaceViewProps) {
  const [selectedPath, setSelectedPath] = useState<string | null>(null);

  const { data: root, isLoading, error } = useQuery({
    queryKey: ["workspace", jobId, ""],
    queryFn: () => fetchJobWorkspace(jobId, ""),
    staleTime: 5_000,
    refetchInterval: 3_000,  // poll while running; cheap if dir is empty
  });

  const rootEntries = useMemo(() => root?.entries ?? [], [root]);

  if (isLoading) {
    return <div className="p-4 text-xs text-zinc-500">Loading workspace…</div>;
  }
  if (error) {
    return (
      <div className="p-4 text-xs text-red-400 flex items-center gap-2">
        <AlertCircle className="w-3.5 h-3.5" />
        {(error as Error).message}
      </div>
    );
  }
  if (!root?.exists) {
    return (
      <div className="p-4 text-xs text-zinc-500">
        No workspace directory. The job hasn't written anything to disk yet.
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0">
      {/* Left: file tree */}
      <div className="w-72 shrink-0 border-r border-border overflow-y-auto">
        <div className="px-3 py-2 text-[10px] uppercase tracking-wide font-medium text-zinc-500 border-b border-border bg-zinc-50/50 dark:bg-zinc-950/50 sticky top-0 z-10">
          Workspace
        </div>
        {rootEntries.length === 0 ? (
          <div className="p-4 text-xs text-zinc-500">(empty)</div>
        ) : (
          <div className="py-1">
            {rootEntries.map((entry) =>
              entry.is_dir ? (
                <DirNode
                  key={entry.path}
                  jobId={jobId}
                  entry={entry}
                  depth={0}
                  onSelectFile={setSelectedPath}
                  selectedPath={selectedPath}
                />
              ) : (
                <FileRow
                  key={entry.path}
                  entry={entry}
                  depth={0}
                  selected={selectedPath === entry.path}
                  onClick={() => setSelectedPath(entry.path)}
                />
              ),
            )}
          </div>
        )}
        {root?.truncated && (
          <div className="p-2 text-[10px] text-amber-400 border-t border-border">
            Listing truncated — too many files
          </div>
        )}
      </div>

      {/* Right: file preview */}
      <div className="flex-1 min-h-0 min-w-0">
        {selectedPath ? (
          <FilePreview jobId={jobId} path={selectedPath} />
        ) : (
          <div className="p-4 text-xs text-zinc-500">
            Select a file to preview.
          </div>
        )}
      </div>
    </div>
  );
}
