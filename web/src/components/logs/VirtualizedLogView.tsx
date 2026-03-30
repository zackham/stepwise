import { useRef, useEffect, useCallback, type ReactNode } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { cn } from "@/lib/utils";

const VIRTUAL_THRESHOLD = 100;

interface VirtualizedLogViewProps {
  lines: string[];
  isLive?: boolean;
  version?: number;
  className?: string;
  renderLine?: (line: string, index: number) => ReactNode;
}

export function VirtualizedLogView({
  lines,
  isLive = false,
  version = 0,
  className,
  renderLine = (line) => line,
}: VirtualizedLogViewProps) {
  const parentRef = useRef<HTMLDivElement>(null);
  const userScrolledRef = useRef(false);

  // Below threshold: render flat
  if (lines.length <= VIRTUAL_THRESHOLD) {
    return (
      <div className={cn("max-h-96 overflow-auto", className)}>
        {lines.map((line, i) => (
          <div key={i} data-line={i}>
            {renderLine(line, i)}
          </div>
        ))}
      </div>
    );
  }

  return (
    <VirtualizedLogViewInner
      lines={lines}
      isLive={isLive}
      version={version}
      className={className}
      renderLine={renderLine}
      parentRef={parentRef}
      userScrolledRef={userScrolledRef}
    />
  );
}

// Separate component to avoid conditional hook calls
function VirtualizedLogViewInner({
  lines,
  isLive,
  version,
  className,
  renderLine,
  parentRef,
  userScrolledRef,
}: {
  lines: string[];
  isLive: boolean;
  version: number;
  className?: string;
  renderLine: (line: string, index: number) => ReactNode;
  parentRef: React.RefObject<HTMLDivElement | null>;
  userScrolledRef: React.RefObject<boolean>;
}) {
  const virtualizer = useVirtualizer({
    count: lines.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 20,
    overscan: 15,
  });

  // Auto-scroll for live mode
  useEffect(() => {
    if (!isLive || userScrolledRef.current) return;
    if (lines.length > 0) {
      virtualizer.scrollToIndex(lines.length - 1, { align: "end" });
    }
  }, [version, lines.length]);

  const handleScroll = useCallback(() => {
    const el = parentRef.current;
    if (!el) return;
    const nearBottom =
      el.scrollTop + el.clientHeight >= el.scrollHeight - 50;
    userScrolledRef.current = !nearBottom;
  }, []);

  return (
    <div
      ref={parentRef}
      onScroll={handleScroll}
      className={cn("max-h-96 overflow-y-auto", className)}
    >
      <div
        style={{
          height: virtualizer.getTotalSize(),
          width: "100%",
          position: "relative",
        }}
      >
        {virtualizer.getVirtualItems().map((virtualRow) => (
          <div
            key={virtualRow.index}
            ref={virtualizer.measureElement}
            data-index={virtualRow.index}
            style={{
              position: "absolute",
              top: 0,
              left: 0,
              width: "100%",
              transform: `translateY(${virtualRow.start}px)`,
            }}
          >
            {renderLine(lines[virtualRow.index], virtualRow.index)}
          </div>
        ))}
      </div>
    </div>
  );
}
