import { type ReactNode } from "react";
import { useResizableWidth } from "@/hooks/useResizableWidth";
import { DragHandle } from "@/components/ui/DragHandle";
import { PanelLeftOpen, PanelRightOpen } from "lucide-react";

interface ResizablePanelProps {
  storageKey: string;
  defaultWidth?: number;
  min?: number;
  max?: number;
  children: ReactNode;
  className?: string;
  /** Which side of the viewport. Affects drag handle position and direction. Default: "right" */
  side?: "left" | "right";
  /** Controlled collapse state */
  collapsed?: boolean;
  /** Called to expand from collapsed state */
  onExpand?: () => void;
  /** Called when user drags past min width or clicks collapse */
  onCollapse?: () => void;
}

export function ResizablePanel({
  storageKey,
  defaultWidth = 384,
  min,
  max,
  children,
  className = "",
  side = "right",
  collapsed = false,
  onExpand,
  onCollapse,
}: ResizablePanelProps) {
  const { width, onMouseDown } = useResizableWidth({
    storageKey, defaultWidth, min, max, side,
    onCollapse,
  });

  if (collapsed && onExpand) {
    const borderClass = side === "left" ? "border-r" : "border-l";
    const Icon = side === "left" ? PanelLeftOpen : PanelRightOpen;
    return (
      <button
        onClick={onExpand}
        className={`w-8 ${borderClass} border-border flex items-center justify-center text-zinc-500 hover:text-foreground hover:bg-zinc-200/50 dark:hover:bg-zinc-800/50 shrink-0`}
      >
        <Icon className="w-4 h-4" />
      </button>
    );
  }

  return (
    <>
      {side === "right" && <DragHandle onMouseDown={onMouseDown} />}
      <div
        className={`shrink-0 flex flex-col ${className}`}
        style={{ width, maxHeight: "calc(100vh - 3rem)" }}
      >
        {children}
      </div>
      {side === "left" && <DragHandle onMouseDown={onMouseDown} />}
    </>
  );
}
