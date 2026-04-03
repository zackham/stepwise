import { useState, useRef, useEffect } from "react";
import { ContentModal } from "@/components/ui/content-modal";

interface FadedTextProps {
  /** The full text value to display (and copy from modal). */
  value: string;
  /** Maximum visible height in pixels. Content beyond this fades out. */
  maxHeight: number;
  /** Modal title when expanded. */
  title?: string;
  /** Additional className for the text element. */
  className?: string;
  /** Height of the fade gradient in pixels. Default 24. */
  fadeHeight?: number;
  /** Custom click handler instead of default modal. */
  onClick?: () => void;
  /** Children rendered instead of default <pre>. */
  children?: React.ReactNode;
}

/**
 * Renders text with a max-height cap and a gradient fade at the bottom
 * when content overflows. Click opens a full-content modal with copy support.
 *
 * Used by AdaptiveSlotRow (RunView) and PromptSegmentRow (StreamSegments).
 */
export function FadedText({
  value,
  maxHeight,
  title = "Details",
  className = "whitespace-pre-wrap text-xs font-mono leading-relaxed text-zinc-400 break-words m-0",
  fadeHeight = 24,
  onClick,
  children,
}: FadedTextProps) {
  const [modalOpen, setModalOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const [overflows, setOverflows] = useState(false);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    setOverflows(el.scrollHeight > el.clientHeight + 2);
  }, [value, maxHeight]);

  return (
    <>
      <div
        ref={containerRef}
        className="overflow-hidden relative cursor-pointer hover:opacity-80 transition-opacity"
        style={{
          maxHeight,
          ...(overflows ? {
            maskImage: `linear-gradient(to bottom, black calc(100% - ${fadeHeight}px), transparent 100%)`,
            WebkitMaskImage: `linear-gradient(to bottom, black calc(100% - ${fadeHeight}px), transparent 100%)`,
          } : {}),
        }}
        onClick={onClick ?? (() => setModalOpen(true))}
      >
        {children ?? <pre className={className}>{value}</pre>}
        {/* no overlay div — mask fades the content itself so any background shows through */}
      </div>
      {!onClick && (
        <ContentModal
          open={modalOpen}
          onOpenChange={setModalOpen}
          title={title}
          copyContent={value}
        >
          <pre className="whitespace-pre-wrap text-sm text-zinc-300 font-mono p-3 leading-relaxed max-h-[70vh] overflow-auto">
            {value}
          </pre>
        </ContentModal>
      )}
    </>
  );
}
