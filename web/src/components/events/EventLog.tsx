import { useEffect, useRef, useState, useMemo } from "react";
import { useEvents } from "@/hooks/useStepwise";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Badge } from "@/components/ui/badge";
import {
  Play,
  CheckCircle,
  XCircle,
  Pause,
  RotateCcw,
  ArrowRight,
  Eye,
  MessageSquare,
  Repeat,
  AlertTriangle,
  Zap,
  ArrowDownToLine,
  GitBranch,
  ChevronDown,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { StepwiseEvent } from "@/lib/types";

interface EventLogProps {
  jobId: string;
}

const EVENT_CATEGORIES = {
  job: { label: "Job", color: "text-blue-400 bg-blue-500/10" },
  step: { label: "Step", color: "text-emerald-400 bg-emerald-500/10" },
  external: { label: "External", color: "text-amber-400 bg-amber-500/10" },
  engine: { label: "Engine", color: "text-purple-400 bg-purple-500/10" },
  effector: { label: "Effector", color: "text-pink-400 bg-pink-500/10" },
};

function categorize(evt: StepwiseEvent): keyof typeof EVENT_CATEGORIES {
  if (evt.is_effector) return "effector";
  if (evt.type.startsWith("job.")) return "job";
  if (evt.type.startsWith("step.")) return "step";
  if (evt.type.startsWith("external.") || evt.type.startsWith("watch.") || evt.type.startsWith("context."))
    return "external";
  return "engine";
}

function eventIcon(type: string, isEffector: boolean) {
  if (isEffector) return <Zap className="w-3.5 h-3.5" />;
  switch (type) {
    case "job.started":
      return <Play className="w-3.5 h-3.5" />;
    case "job.completed":
      return <CheckCircle className="w-3.5 h-3.5" />;
    case "job.failed":
      return <XCircle className="w-3.5 h-3.5" />;
    case "job.paused":
      return <Pause className="w-3.5 h-3.5" />;
    case "job.resumed":
      return <RotateCcw className="w-3.5 h-3.5" />;
    case "step.started":
      return <Play className="w-3.5 h-3.5" />;
    case "step.completed":
      return <CheckCircle className="w-3.5 h-3.5" />;
    case "step.failed":
      return <XCircle className="w-3.5 h-3.5" />;
    case "step.suspended":
      return <Eye className="w-3.5 h-3.5" />;
    case "step.delegated":
      return <GitBranch className="w-3.5 h-3.5" />;
    case "exit.resolved":
      return <ArrowRight className="w-3.5 h-3.5" />;
    case "watch.fulfilled":
      return <ArrowDownToLine className="w-3.5 h-3.5" />;
    case "external.rerun":
      return <RotateCcw className="w-3.5 h-3.5" />;
    case "loop.iteration":
      return <Repeat className="w-3.5 h-3.5" />;
    case "loop.max_reached":
      return <AlertTriangle className="w-3.5 h-3.5" />;
    case "context.injected":
      return <MessageSquare className="w-3.5 h-3.5" />;
    default:
      return <Zap className="w-3.5 h-3.5" />;
  }
}

function formatTime(ts: string): string {
  return new Date(ts).toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
  });
}

function dataPreview(data: Record<string, unknown>): string {
  const entries = Object.entries(data);
  if (entries.length === 0) return "";
  return entries
    .map(([k, v]) => {
      const val = typeof v === "string" ? v : JSON.stringify(v);
      return `${k}=${val}`;
    })
    .join(" ")
    .slice(0, 80);
}

export function EventLog({ jobId }: EventLogProps) {
  const { data: events = [] } = useEvents(jobId);
  const [autoScroll, setAutoScroll] = useState(true);
  const [activeFilters, setActiveFilters] = useState<
    Set<keyof typeof EVENT_CATEGORIES>
  >(new Set(Object.keys(EVENT_CATEGORIES) as Array<keyof typeof EVENT_CATEGORIES>));
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (autoScroll && bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [events, autoScroll]);

  const filteredEvents = useMemo(
    () => events.filter((evt) => activeFilters.has(categorize(evt))),
    [events, activeFilters]
  );

  const toggleFilter = (cat: keyof typeof EVENT_CATEGORIES) => {
    setActiveFilters((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) {
        next.delete(cat);
      } else {
        next.add(cat);
      }
      return next;
    });
  };

  return (
    <div className="flex flex-col h-full">
      {/* Filters */}
      <div className="flex items-center gap-2 p-3 border-b border-border">
        {(
          Object.entries(EVENT_CATEGORIES) as Array<
            [keyof typeof EVENT_CATEGORIES, { label: string; color: string }]
          >
        ).map(([key, { label, color }]) => (
          <button
            key={key}
            onClick={() => toggleFilter(key)}
            className={cn(
              "text-xs px-2 py-0.5 rounded-full border transition-colors",
              activeFilters.has(key)
                ? `${color} border-current/30`
                : "text-zinc-600 border-zinc-700/50 bg-transparent"
            )}
          >
            {label}
          </button>
        ))}
        <div className="flex-1" />
        <button
          onClick={() => setAutoScroll(!autoScroll)}
          className={cn(
            "text-xs flex items-center gap-1",
            autoScroll ? "text-blue-400" : "text-zinc-500"
          )}
        >
          <ChevronDown className="w-3 h-3" />
          Auto-scroll
        </button>
      </div>

      {/* Events */}
      <ScrollArea className="flex-1">
        <div className="p-2 space-y-0.5">
          {filteredEvents.map((evt) => {
            const cat = categorize(evt);
            const catStyle = EVENT_CATEGORIES[cat];
            return (
              <div
                key={evt.id}
                className={cn(
                  "flex items-start gap-2 px-2 py-1.5 rounded text-sm",
                  "hover:bg-zinc-800/50 group"
                )}
              >
                <span
                  className={cn(
                    "shrink-0 mt-0.5",
                    cat === "step" &&
                      (evt.type.includes("failed")
                        ? "text-red-400"
                        : evt.type.includes("completed")
                        ? "text-emerald-400"
                        : "text-emerald-400/70"),
                    cat === "job" && "text-blue-400",
                    cat === "external" && "text-amber-400",
                    cat === "engine" && "text-purple-400",
                    cat === "effector" && "text-pink-400"
                  )}
                >
                  {eventIcon(evt.type, evt.is_effector)}
                </span>

                <span className="text-zinc-600 text-xs font-mono shrink-0 mt-0.5">
                  {formatTime(evt.timestamp)}
                </span>

                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <Badge
                      variant="outline"
                      className={cn(
                        "text-[9px] font-mono border-transparent",
                        catStyle.color
                      )}
                    >
                      {evt.type}
                    </Badge>
                    {evt.is_effector && (
                      <Badge
                        variant="outline"
                        className="text-[9px] font-mono bg-pink-500/10 text-pink-400 border-pink-500/30"
                      >
                        effector
                      </Badge>
                    )}
                  </div>
                  {Object.keys(evt.data).length > 0 && (
                    <div className="text-xs text-zinc-500 mt-0.5 font-mono truncate">
                      {dataPreview(evt.data)}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
          <div ref={bottomRef} />
        </div>
      </ScrollArea>
    </div>
  );
}
