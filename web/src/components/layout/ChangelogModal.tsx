import { useQuery } from "@tanstack/react-query";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { fetchChangelog } from "@/lib/api";
import { Markdown } from "@/components/ui/markdown";

// ── Modal ────────────────────────────────────────────────────────────

interface ChangelogModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  currentVersion?: string;
}

export function ChangelogModal({
  open,
  onOpenChange,
  currentVersion,
}: ChangelogModalProps) {
  const { data: changelog, isLoading, error } = useQuery({
    queryKey: ["changelog"],
    queryFn: fetchChangelog,
    enabled: open,
    staleTime: 5 * 60 * 1000,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[80vh] flex-col sm:max-w-2xl overflow-hidden">
        <DialogHeader>
          <DialogTitle>
            Stepwise{currentVersion ? ` v${currentVersion}` : ""}
          </DialogTitle>
          <DialogDescription className="sr-only">
            About Stepwise and release notes.
          </DialogDescription>
        </DialogHeader>

        {/* About preamble */}
        <div className="flex flex-col gap-2 border-b border-border pb-3">
          <p className="text-sm text-muted-foreground">
            Portable workflow orchestration for agents and humans.
          </p>
          <div className="flex items-center gap-3 text-xs">
            <a
              href="https://stepwise.run"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-500 hover:text-blue-400 hover:underline transition-colors"
            >
              stepwise.run
            </a>
            <span className="text-zinc-600">·</span>
            <a
              href="https://github.com/zackham/stepwise"
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-500 hover:text-blue-400 hover:underline transition-colors"
            >
              GitHub
            </a>
          </div>
        </div>

        <ScrollArea className="min-h-0 flex-1 overflow-y-auto pr-3">
          {isLoading && (
            <p className="py-8 text-center text-sm text-muted-foreground">
              Loading changelog...
            </p>
          )}
          {error && (
            <p className="py-8 text-center text-sm text-red-500">
              Failed to load changelog.
            </p>
          )}
          {changelog && <div className="px-3"><Markdown>{changelog}</Markdown></div>}
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}
