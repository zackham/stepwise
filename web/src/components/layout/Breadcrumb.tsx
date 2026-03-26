import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { useIsMobile } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/utils";

export interface BreadcrumbSegment {
  label: string;
  to?: string;
  params?: Record<string, string>;
}

interface BreadcrumbProps {
  segments: BreadcrumbSegment[];
  className?: string;
}

export function Breadcrumb({ segments, className }: BreadcrumbProps) {
  const isMobile = useIsMobile();

  // On mobile, collapse intermediate segments for 3+ segments
  const visible =
    isMobile && segments.length >= 3
      ? [segments[0], { label: "…" }, segments[segments.length - 1]]
      : segments;

  return (
    <nav
      aria-label="Breadcrumb"
      className={cn("flex items-center gap-1 px-4 py-1.5 border-b border-border text-xs", className)}
    >
      {visible.map((seg, i) => (
        <span key={i} className="flex items-center gap-1 min-w-0">
          {i > 0 && (
            <ChevronRight className="w-3 h-3 text-zinc-600 shrink-0" />
          )}
          {seg.to && i < visible.length - 1 ? (
            <Link
              to={seg.to}
              params={seg.params}
              className={cn(
                "truncate max-w-[200px] text-zinc-400 hover:text-foreground transition-colors min-h-[44px] md:min-h-0 flex items-center",
              )}
              title={seg.label}
            >
              {seg.label}
            </Link>
          ) : (
            <span
              className={cn(
                "truncate max-w-[200px]",
                i === visible.length - 1 ? "text-zinc-500" : "text-zinc-400",
              )}
              title={seg.label}
            >
              {seg.label}
            </span>
          )}
        </span>
      ))}
    </nav>
  );
}
