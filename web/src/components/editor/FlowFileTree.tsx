import { useState, useMemo } from "react";
import {
  FileText, FolderOpen, Folder, ChevronRight, ChevronDown,
  RefreshCw,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { FlowFile } from "@/lib/api";

interface TreeNode {
  name: string;
  path: string;
  isDir: boolean;
  children: TreeNode[];
  file?: FlowFile;
}

function buildTree(files: FlowFile[]): TreeNode[] {
  const root: TreeNode[] = [];
  const dirMap = new Map<string, TreeNode>();

  const getOrCreateDir = (parts: string[]): TreeNode[] => {
    if (parts.length === 0) return root;
    const dirPath = parts.join("/");
    const existing = dirMap.get(dirPath);
    if (existing) return existing.children;

    const parent = getOrCreateDir(parts.slice(0, -1));
    const node: TreeNode = {
      name: parts[parts.length - 1],
      path: dirPath,
      isDir: true,
      children: [],
    };
    parent.push(node);
    dirMap.set(dirPath, node);
    return node.children;
  };

  for (const file of files) {
    const parts = file.path.split("/");
    const fileName = parts.pop()!;
    const parent = getOrCreateDir(parts);
    parent.push({
      name: fileName,
      path: file.path,
      isDir: false,
      children: [],
      file,
    });
  }

  // Sort: dirs first, then alphabetically
  const sortNodes = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    for (const n of nodes) {
      if (n.isDir) sortNodes(n.children);
    }
  };
  sortNodes(root);
  return root;
}

interface FlowFileTreeProps {
  files: FlowFile[];
  selectedFile: string | null;
  onSelectFile: (path: string) => void;
  onRefresh?: () => void;
  isRefreshing?: boolean;
}

export function FlowFileTree({
  files,
  selectedFile,
  onSelectFile,
  onRefresh,
  isRefreshing,
}: FlowFileTreeProps) {
  const tree = useMemo(() => buildTree(files), [files]);

  return (
    <div className="flex flex-col">
      <div className="px-1">
        {tree.map((node) => (
          <TreeNodeRow
            key={node.path}
            node={node}
            depth={0}
            selectedFile={selectedFile}
            onSelectFile={onSelectFile}
          />
        ))}
      </div>
    </div>
  );
}

function TreeNodeRow({
  node,
  depth,
  selectedFile,
  onSelectFile,
}: {
  node: TreeNode;
  depth: number;
  selectedFile: string | null;
  onSelectFile: (path: string) => void;
}) {
  const [expanded, setExpanded] = useState(true);
  const isSelected = node.path === selectedFile;
  const isYaml = node.name.endsWith(".yaml") || node.name.endsWith(".yml");

  if (node.isDir) {
    return (
      <>
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 w-full text-left text-sm text-zinc-500 dark:text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 py-2 rounded hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50"
          style={{ paddingLeft: `${depth * 12 + 4}px` }}
        >
          {expanded ? (
            <ChevronDown className="w-3.5 h-3.5 shrink-0" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 shrink-0" />
          )}
          {expanded ? (
            <FolderOpen className="w-3.5 h-3.5 shrink-0 text-zinc-500" />
          ) : (
            <Folder className="w-3.5 h-3.5 shrink-0 text-zinc-500" />
          )}
          <span className="truncate">{node.name}</span>
        </button>
        {expanded &&
          node.children.map((child) => (
            <TreeNodeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              selectedFile={selectedFile}
              onSelectFile={onSelectFile}
            />
          ))}
      </>
    );
  }

  return (
    <button
      onClick={() => onSelectFile(node.path)}
      className={cn(
        "flex items-center gap-1.5 w-full text-left text-sm py-2 rounded",
        isSelected
          ? "bg-blue-500/20 text-blue-300"
          : "text-zinc-500 dark:text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50"
      )}
      style={{ paddingLeft: `${depth * 12 + 4 + 16}px` }}
    >
      <FileText className={cn(
        "w-3.5 h-3.5 shrink-0",
        isYaml ? "text-amber-500" : "text-zinc-500"
      )} />
      <span className="truncate">{node.name}</span>
    </button>
  );
}
