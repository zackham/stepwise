import { Save, RotateCcw, Plus, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface EditorToolbarProps {
  flowName: string;
  isDirty: boolean;
  isSaving: boolean;
  onSave: () => void;
  onDiscard: () => void;
  onAddStep?: () => void;
  onToggleChat?: () => void;
  chatOpen?: boolean;
  parseErrors: string[];
}

export function EditorToolbar({
  flowName,
  isDirty,
  isSaving,
  onSave,
  onDiscard,
  onAddStep,
  onToggleChat,
  chatOpen,
  parseErrors,
}: EditorToolbarProps) {
  return (
    <div className="flex items-center gap-3 h-10 px-3 border-b border-border shrink-0">
      <span className="text-sm font-medium text-foreground flex items-center gap-2">
        {flowName}
        {isDirty && (
          <span
            className="w-2 h-2 rounded-full bg-amber-400"
            title="Unsaved changes"
          />
        )}
      </span>

      {onAddStep && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onAddStep}
          className="h-7 text-xs"
        >
          <Plus className="w-3 h-3 mr-1" />
          Add Step
        </Button>
      )}

      <div className="flex-1" />

      {parseErrors.length > 0 && (
        <span
          className="text-xs text-red-400 truncate max-w-xs"
          title={parseErrors.join("\n")}
        >
          {parseErrors[0]}
        </span>
      )}

      {onToggleChat && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onToggleChat}
          className={cn("h-7 text-xs", chatOpen && "bg-violet-500/20 text-violet-300")}
        >
          <Sparkles className="w-3 h-3 mr-1" />
          AI
        </Button>
      )}

      <Button
        variant="ghost"
        size="sm"
        onClick={onDiscard}
        disabled={!isDirty}
        className="h-7 text-xs"
      >
        <RotateCcw className="w-3 h-3 mr-1" />
        Discard
      </Button>
      <Button
        variant="default"
        size="sm"
        onClick={onSave}
        disabled={!isDirty || isSaving}
        className="h-7 text-xs"
      >
        <Save className="w-3 h-3 mr-1" />
        {isSaving ? "Saving..." : "Save"}
      </Button>
    </div>
  );
}
