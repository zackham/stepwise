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
import type { ReactNode } from "react";

// ── Lightweight markdown renderer ────────────────────────────────────

function renderInlineMarkdown(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const re = /(\*\*(.+?)\*\*|`([^`]+)`)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  while ((match = re.exec(text)) !== null) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    if (match[2]) {
      parts.push(
        <strong key={key++} className="font-semibold text-foreground">
          {match[2]}
        </strong>,
      );
    } else if (match[3]) {
      parts.push(
        <code
          key={key++}
          className="rounded bg-zinc-200 px-1 py-0.5 font-mono text-[12px] dark:bg-zinc-800"
        >
          {match[3]}
        </code>,
      );
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function ChangelogMarkdown({ content }: { content: string }) {
  const lines = content.split("\n");
  const elements: ReactNode[] = [];
  let key = 0;
  let listItems: string[] = [];

  const flushList = () => {
    if (listItems.length === 0) return;
    elements.push(
      <ul key={key++} className="space-y-1 pl-4">
        {listItems.map((item, i) => (
          <li key={i} className="list-disc text-sm text-muted-foreground">
            {renderInlineMarkdown(item)}
          </li>
        ))}
      </ul>,
    );
    listItems = [];
  };

  for (const line of lines) {
    // H2: version headings
    const h2 = line.match(/^## \[?(.+?)\]?(?:\s*[—–-]\s*(.+))?$/);
    if (h2) {
      flushList();
      elements.push(
        <h2
          key={key++}
          className="mt-6 mb-1 flex items-baseline gap-2 border-b border-border pb-1 text-base font-semibold first:mt-0"
        >
          <span>{h2[1]}</span>
          {h2[2] && (
            <span className="text-xs font-normal text-muted-foreground">
              {h2[2]}
            </span>
          )}
        </h2>,
      );
      continue;
    }

    // H3: section headings (Added, Changed, Fixed, etc.)
    const h3 = line.match(/^### (.+)/);
    if (h3) {
      flushList();
      elements.push(
        <h3
          key={key++}
          className="mt-3 mb-1 text-sm font-semibold text-foreground"
        >
          {h3[1]}
        </h3>,
      );
      continue;
    }

    // H4: sub-section headings
    const h4 = line.match(/^#### (.+)/);
    if (h4) {
      flushList();
      elements.push(
        <h4
          key={key++}
          className="mt-2 mb-0.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
        >
          {h4[1]}
        </h4>,
      );
      continue;
    }

    // Skip the title line and format line
    if (line.startsWith("# ") || line.startsWith("All notable") || line.startsWith("Format:")) {
      flushList();
      continue;
    }

    // List items
    const listMatch = line.match(/^[-*]\s+(.+)/);
    if (listMatch) {
      listItems.push(listMatch[1]);
      continue;
    }

    // Paragraph text (non-empty, non-heading)
    if (line.trim()) {
      flushList();
      elements.push(
        <p key={key++} className="text-sm text-muted-foreground">
          {renderInlineMarkdown(line)}
        </p>,
      );
      continue;
    }

    // Empty line — flush list
    flushList();
  }
  flushList();

  return <div className="space-y-1">{elements}</div>;
}

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
      <DialogContent className="flex max-h-[80vh] flex-col sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            Changelog{currentVersion ? ` — v${currentVersion}` : ""}
          </DialogTitle>
          <DialogDescription>
            Recent changes and release notes.
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="min-h-0 flex-1 pr-3">
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
          {changelog && <ChangelogMarkdown content={changelog} />}
        </ScrollArea>
      </DialogContent>
    </Dialog>
  );
}
