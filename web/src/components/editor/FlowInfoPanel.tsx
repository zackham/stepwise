import { Download, User, Tag, Hash, Loader2, CheckCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import type { RegistryFlow } from "@/lib/types";

interface FlowInfoPanelProps {
  flow: RegistryFlow;
  onInstall: () => void;
  isInstalling: boolean;
  isInstalled: boolean;
}

export function FlowInfoPanel({
  flow,
  onInstall,
  isInstalling,
  isInstalled,
}: FlowInfoPanelProps) {
  return (
    <div className="flex flex-col h-full">
      <div className="p-4 border-b border-border">
        <h3 className="font-semibold text-foreground break-words">{flow.name}</h3>
        <div className="flex items-center gap-2 mt-1 text-xs text-zinc-500">
          <span className="flex items-center gap-1">
            <User className="w-3 h-3" />
            {flow.author}
          </span>
          <span>v{flow.version}</span>
        </div>
      </div>

      <ScrollArea className="flex-1 min-h-0">
        <div className="p-4 space-y-4">
          {flow.description && (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">{flow.description}</p>
          )}

          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="flex items-center gap-1.5 text-zinc-500">
              <Download className="w-3.5 h-3.5" />
              <span>{flow.downloads} downloads</span>
            </div>
            <div className="flex items-center gap-1.5 text-zinc-500">
              <Hash className="w-3.5 h-3.5" />
              <span>{flow.steps} steps</span>
            </div>
          </div>

          {flow.tags.length > 0 && (
            <>
              <Separator />
              <div className="space-y-1.5">
                <span className="text-xs text-zinc-500 flex items-center gap-1">
                  <Tag className="w-3 h-3" /> Tags
                </span>
                <div className="flex flex-wrap gap-1">
                  {flow.tags.map((tag) => (
                    <span
                      key={tag}
                      className="text-[10px] bg-zinc-200 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 px-1.5 py-0.5 rounded"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              </div>
            </>
          )}

          <Separator />

          <div className="space-y-1.5">
            <span className="text-xs text-zinc-500">Executors</span>
            <div className="flex flex-wrap gap-1">
              {flow.executor_types.map((t) => (
                <span
                  key={t}
                  className="text-[10px] font-mono bg-zinc-200 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 px-1.5 py-0.5 rounded"
                >
                  {t}
                </span>
              ))}
            </div>
          </div>
        </div>
      </ScrollArea>

      <div className="p-4 border-t border-border">
        {isInstalled ? (
          <Button disabled className="w-full" size="sm">
            <CheckCircle className="w-3.5 h-3.5 mr-1.5" />
            Installed
          </Button>
        ) : (
          <Button
            onClick={onInstall}
            disabled={isInstalling}
            className="w-full"
            size="sm"
          >
            {isInstalling ? (
              <>
                <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                Installing...
              </>
            ) : (
              <>
                <Download className="w-3.5 h-3.5 mr-1.5" />
                Install to Project
              </>
            )}
          </Button>
        )}
      </div>
    </div>
  );
}
