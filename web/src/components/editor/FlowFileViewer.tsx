import { useState, useEffect, useRef, useCallback } from "react";
import { X, Save, FileText, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useFlowFile, useWriteFlowFile } from "@/hooks/useEditor";

interface FlowFileViewerProps {
  flowPath: string;
  filePath: string;
  onClose: () => void;
}

const EXT_LANG: Record<string, string> = {
  py: "python",
  sh: "bash",
  yaml: "yaml",
  yml: "yaml",
  json: "json",
  md: "markdown",
  toml: "toml",
  j2: "jinja2",
  txt: "text",
};

export function FlowFileViewer({ flowPath, filePath, onClose }: FlowFileViewerProps) {
  const { data, isLoading } = useFlowFile(flowPath, filePath);
  const writeMutation = useWriteFlowFile();
  const [content, setContent] = useState("");
  const [savedContent, setSavedContent] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const ext = filePath.split(".").pop() ?? "";
  const lang = EXT_LANG[ext] ?? ext;
  const isDirty = content !== savedContent;

  // Load content when data arrives
  useEffect(() => {
    if (data?.content != null) {
      setContent(data.content);
      setSavedContent(data.content);
    }
  }, [data]);

  const handleSave = useCallback(() => {
    if (!isDirty) return;
    writeMutation.mutate(
      { flowPath, filePath, content },
      {
        onSuccess: () => setSavedContent(content),
      }
    );
  }, [flowPath, filePath, content, isDirty, writeMutation]);

  // Ctrl+S handler
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        handleSave();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [handleSave]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-5 h-5 animate-spin text-zinc-500" />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between h-10 px-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <FileText className="w-3.5 h-3.5 text-zinc-500 dark:text-zinc-400 shrink-0" />
          <span className="text-xs font-mono text-zinc-700 dark:text-zinc-300 truncate">{filePath}</span>
          <span className="text-[10px] text-zinc-600 shrink-0">{lang}</span>
          {isDirty && (
            <span className="w-2 h-2 rounded-full bg-amber-400 shrink-0" title="Unsaved" />
          )}
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleSave}
            disabled={!isDirty || writeMutation.isPending}
            className="h-7 text-xs"
          >
            <Save className="w-3 h-3 mr-1" />
            {writeMutation.isPending ? "..." : "Save"}
          </Button>
          <button onClick={onClose} className="text-zinc-500 hover:text-foreground p-1">
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Editor */}
      <textarea
        ref={textareaRef}
        value={content}
        onChange={(e) => setContent(e.target.value)}
        className="flex-1 w-full bg-transparent text-xs text-zinc-700 dark:text-zinc-300 font-mono p-3 leading-relaxed resize-none focus:outline-none"
        spellCheck={false}
      />
    </div>
  );
}
