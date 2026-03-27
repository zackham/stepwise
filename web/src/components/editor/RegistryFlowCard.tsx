import { Download, Star, Terminal, Brain, User, Bot } from "lucide-react";
import { cn } from "@/lib/utils";
import type { RegistryFlow } from "@/lib/types";

function ExecutorBadge({ type }: { type: string }) {
  const icon = {
    script: <Terminal className="w-3 h-3" />,
    llm: <Brain className="w-3 h-3" />,
    external: <User className="w-3 h-3" />,
    agent: <Bot className="w-3 h-3" />,
  }[type] ?? <Terminal className="w-3 h-3" />;

  return (
    <span className="inline-flex items-center gap-0.5 text-[10px] text-zinc-500 bg-zinc-200 dark:bg-zinc-800 px-1 py-0.5 rounded">
      {icon}
      {type}
    </span>
  );
}

interface RegistryFlowCardProps {
  flow: RegistryFlow;
  isSelected: boolean;
  onClick: () => void;
}

export function RegistryFlowCard({ flow, isSelected, onClick }: RegistryFlowCardProps) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full text-left p-2 rounded-md transition-colors",
        isSelected
          ? "bg-zinc-200 dark:bg-zinc-800 border border-zinc-400 dark:border-zinc-600"
          : "hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50 border border-transparent"
      )}
    >
      <div className="flex items-start justify-between gap-1">
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            {flow.featured && <Star className="w-3 h-3 text-amber-400 shrink-0" />}
            <span className="text-xs font-medium text-foreground truncate">
              {flow.name}
            </span>
          </div>
          <p className="text-[10px] text-zinc-500 mt-0.5 truncate">
            by {flow.author}
          </p>
        </div>
        <div className="flex items-center gap-1 text-[10px] text-zinc-600 shrink-0">
          <Download className="w-3 h-3" />
          {flow.downloads}
        </div>
      </div>

      {flow.description && (
        <p className="text-[10px] text-zinc-500 mt-1 line-clamp-2">
          {flow.description}
        </p>
      )}

      <div className="flex items-center gap-1 mt-1.5 flex-wrap">
        {flow.executor_types.slice(0, 3).map((t) => (
          <ExecutorBadge key={t} type={t} />
        ))}
        <span className="text-[10px] text-zinc-600 ml-auto">
          {flow.steps} step{flow.steps !== 1 ? "s" : ""}
        </span>
      </div>
    </button>
  );
}
