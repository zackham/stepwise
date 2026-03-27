import { useState } from "react";
import { useCreateFlow } from "@/hooks/useEditor";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { Bot, Brain, Plus, UserCheck } from "lucide-react";

const TEMPLATES = [
  {
    id: "blank",
    label: "Blank",
    description: "Empty flow with a single hello step",
    icon: Plus,
  },
  {
    id: "simple-llm",
    label: "Simple LLM",
    description: "Single LLM step with a prompt input",
    icon: Brain,
  },
  {
    id: "agent-task",
    label: "Agent Task",
    description: "Agent step with a validation loop",
    icon: Bot,
  },
  {
    id: "external-approval",
    label: "External Approval",
    description: "Agent draft with external approval loop",
    icon: UserCheck,
  },
] as const;

interface CreateFlowDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (result: { path: string; name: string }) => void;
}

export function CreateFlowDialog({
  open,
  onOpenChange,
  onCreated,
}: CreateFlowDialogProps) {
  const [name, setName] = useState("");
  const [template, setTemplate] = useState("blank");
  const createFlowMutation = useCreateFlow();

  const handleCreate = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    createFlowMutation.mutate(
      { name: trimmed, template },
      {
        onSuccess: (result) => {
          setName("");
          setTemplate("blank");
          onOpenChange(false);
          onCreated(result);
        },
      }
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New Flow</DialogTitle>
          <DialogDescription>
            Choose a template to get started.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Name</Label>
            <Input
              autoFocus
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-flow"
              className="text-sm bg-white dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700"
              onKeyDown={(e) => {
                if (e.key === "Enter") handleCreate();
              }}
            />
          </div>

          <div className="space-y-2">
            <Label>Template</Label>
            <div className="grid grid-cols-2 gap-2">
              {TEMPLATES.map((t) => {
                const Icon = t.icon;
                return (
                  <button
                    key={t.id}
                    onClick={() => setTemplate(t.id)}
                    className={cn(
                      "flex flex-col items-start gap-1 rounded-lg border p-3 text-left text-sm transition-colors",
                      template === t.id
                        ? "border-blue-500 bg-blue-500/10 text-foreground"
                        : "border-zinc-300 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-900 text-zinc-500 dark:text-zinc-400 hover:border-zinc-400 dark:hover:border-zinc-500 hover:text-foreground"
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Icon className="w-3.5 h-3.5" />
                      <span className="font-medium">{t.label}</span>
                    </div>
                    <span className="text-[11px] leading-tight text-zinc-500">
                      {t.description}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>

          {createFlowMutation.isError && (
            <p className="text-xs text-red-400">
              {(createFlowMutation.error as Error).message?.includes("409")
                ? "A flow with this name already exists"
                : "Failed to create flow"}
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleCreate}
            disabled={!name.trim() || createFlowMutation.isPending}
          >
            {createFlowMutation.isPending ? "Creating..." : "Create"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
