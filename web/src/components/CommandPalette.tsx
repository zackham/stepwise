import { useEffect, useState, useMemo } from "react";
import { Command } from "cmdk";
import { useNavigate } from "@tanstack/react-router";
import { useJobs } from "@/hooks/useStepwise";
import { useLocalFlows } from "@/hooks/useEditor";
import {
  LayoutGrid,
  FileCode,
  Settings2,
  Search,
  Briefcase,
  CircleDot,
  CircleCheck,
  CircleX,
  CirclePause,
  Circle,
} from "lucide-react";
import type { Job, JobStatus } from "@/lib/types";

const STATUS_ICON: Record<JobStatus, typeof Circle> = {
  running: CircleDot,
  pending: Circle,
  staged: Circle,
  paused: CirclePause,
  completed: CircleCheck,
  failed: CircleX,
  cancelled: CircleX,
  archived: Circle,
};

interface CommandPaletteProps {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

export function CommandPalette({
  open: controlledOpen,
  onOpenChange,
}: CommandPaletteProps) {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const setOpen = onOpenChange ?? setInternalOpen;
  const navigate = useNavigate();
  const { data: jobs } = useJobs();
  const { data: flows } = useLocalFlows();

  // Cmd+K / Ctrl+K to toggle
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen(!open);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, setOpen]);

  const recentJobs = useMemo(() => {
    if (!jobs) return [];
    return [...jobs]
      .sort(
        (a, b) =>
          new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      )
      .slice(0, 10);
  }, [jobs]);

  function selectJob(job: Job) {
    setOpen(false);
    navigate({ to: "/jobs/$jobId", params: { jobId: job.id } });
  }

  function selectFlow(name: string) {
    setOpen(false);
    navigate({ to: "/flows/$flowName", params: { flowName: name } });
  }

  function selectPage(path: string) {
    setOpen(false);
    navigate({ to: path });
  }

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/50"
      onClick={() => setOpen(false)}
    >
      <div
        className="fixed top-[20%] left-1/2 -translate-x-1/2 w-full max-w-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <Command
          className="rounded-xl border border-zinc-300 dark:border-zinc-700 bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-2xl overflow-hidden"
          loop
        >
          <div className="flex items-center border-b border-zinc-300 dark:border-zinc-700 px-3">
            <Search className="w-4 h-4 text-zinc-400 shrink-0 mr-2" />
            <Command.Input
              placeholder="Search jobs, flows, pages..."
              className="flex-1 h-11 bg-transparent text-sm outline-none placeholder:text-zinc-500"
              data-hotkey-search-input="true"
              autoFocus
            />
          </div>
          <Command.List className="max-h-80 overflow-y-auto p-2">
            <Command.Empty className="py-6 text-center text-sm text-zinc-500">
              No results found.
            </Command.Empty>

            {/* Pages */}
            <Command.Group
              heading="Pages"
              className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-zinc-500 dark:[&_[cmdk-group-heading]]:text-zinc-400"
            >
              <Command.Item
                value="Jobs page"
                onSelect={() => selectPage("/jobs")}
                className="flex items-center gap-2 px-2 py-1.5 text-sm rounded-md cursor-pointer aria-selected:bg-zinc-100 dark:aria-selected:bg-zinc-800 text-zinc-700 dark:text-zinc-300 aria-selected:text-zinc-900 dark:aria-selected:text-zinc-100"
              >
                <LayoutGrid className="w-4 h-4 text-zinc-500" />
                Jobs
              </Command.Item>
              <Command.Item
                value="Flows page"
                onSelect={() => selectPage("/flows")}
                className="flex items-center gap-2 px-2 py-1.5 text-sm rounded-md cursor-pointer aria-selected:bg-zinc-100 dark:aria-selected:bg-zinc-800 text-zinc-700 dark:text-zinc-300 aria-selected:text-zinc-900 dark:aria-selected:text-zinc-100"
              >
                <FileCode className="w-4 h-4 text-zinc-500" />
                Flows
              </Command.Item>
              <Command.Item
                value="Settings page"
                onSelect={() => selectPage("/settings")}
                className="flex items-center gap-2 px-2 py-1.5 text-sm rounded-md cursor-pointer aria-selected:bg-zinc-100 dark:aria-selected:bg-zinc-800 text-zinc-700 dark:text-zinc-300 aria-selected:text-zinc-900 dark:aria-selected:text-zinc-100"
              >
                <Settings2 className="w-4 h-4 text-zinc-500" />
                Settings
              </Command.Item>
            </Command.Group>

            {/* Jobs */}
            {recentJobs.length > 0 && (
              <Command.Group
                heading="Jobs"
                className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-zinc-500 dark:[&_[cmdk-group-heading]]:text-zinc-400"
              >
                {recentJobs.map((job) => {
                  const Icon = STATUS_ICON[job.status] ?? Circle;
                  return (
                    <Command.Item
                      key={job.id}
                      value={`${job.name ?? ""} ${job.objective} ${job.id}`}
                      onSelect={() => selectJob(job)}
                      className="flex items-center gap-2 px-2 py-1.5 text-sm rounded-md cursor-pointer aria-selected:bg-zinc-100 dark:aria-selected:bg-zinc-800 text-zinc-700 dark:text-zinc-300 aria-selected:text-zinc-900 dark:aria-selected:text-zinc-100"
                    >
                      <Icon className="w-4 h-4 text-zinc-500 shrink-0" />
                      <span className="truncate">
                        {job.name ?? job.objective}
                      </span>
                      <span className="ml-auto text-xs text-zinc-600 shrink-0">
                        {job.status}
                      </span>
                    </Command.Item>
                  );
                })}
              </Command.Group>
            )}

            {/* Flows */}
            {flows && flows.length > 0 && (
              <Command.Group
                heading="Flows"
                className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:text-zinc-500 dark:[&_[cmdk-group-heading]]:text-zinc-400"
              >
                {flows.map((flow) => (
                  <Command.Item
                    key={flow.path}
                    value={`${flow.name} ${flow.description}`}
                    onSelect={() => selectFlow(flow.name)}
                    className="flex items-center gap-2 px-2 py-1.5 text-sm rounded-md cursor-pointer aria-selected:bg-zinc-100 dark:aria-selected:bg-zinc-800 text-zinc-700 dark:text-zinc-300 aria-selected:text-zinc-900 dark:aria-selected:text-zinc-100"
                  >
                    <Briefcase className="w-4 h-4 text-zinc-500 shrink-0" />
                    <span className="truncate">{flow.name}</span>
                    {flow.description && (
                      <span className="ml-auto text-xs text-zinc-600 truncate max-w-[200px]">
                        {flow.description}
                      </span>
                    )}
                  </Command.Item>
                ))}
              </Command.Group>
            )}
          </Command.List>

          <div className="border-t border-zinc-300 dark:border-zinc-700 px-3 py-2 flex items-center gap-3 text-xs text-zinc-500">
            <span>
              <kbd className="px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 font-mono text-[11px]">
                ↑↓
              </kbd>{" "}
              navigate
            </span>
            <span>
              <kbd className="px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 font-mono text-[11px]">
                ↵
              </kbd>{" "}
              select
            </span>
            <span>
              <kbd className="px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 font-mono text-[11px]">
                esc
              </kbd>{" "}
              close
            </span>
          </div>
        </Command>
      </div>
    </div>
  );
}
