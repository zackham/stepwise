import { useIsMobile } from "@/hooks/useMediaQuery";
import { ArrowLeft } from "lucide-react";

interface MobileFullScreenProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
}

export function MobileFullScreen({ open, onClose, title, children }: MobileFullScreenProps) {
  const isMobile = useIsMobile();

  if (!isMobile || !open) return null;

  return (
    <div className="fixed inset-x-0 top-12 bottom-0 z-50 flex flex-col bg-background animate-in fade-in slide-in-from-bottom-2 duration-200">
      {title && (
        <div className="flex items-center gap-2 px-3 py-2 border-b border-border shrink-0">
          <button
            onClick={onClose}
            className="flex items-center justify-center min-w-[44px] min-h-[44px] -ml-2 text-zinc-500 hover:text-foreground"
            aria-label="Back"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <span className="text-sm font-medium truncate">{title}</span>
        </div>
      )}
      <div className="flex-1 overflow-y-auto">{children}</div>
    </div>
  );
}
