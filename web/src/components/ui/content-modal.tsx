import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Copy, Check } from "lucide-react"
import { useCopyFeedback } from "@/hooks/useCopyFeedback"

interface ContentModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  children: React.ReactNode
  copyContent?: string
}

export function ContentModal({
  open,
  onOpenChange,
  title,
  children,
  copyContent,
}: ContentModalProps) {
  const { copy, justCopied } = useCopyFeedback()

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false} className="max-h-[80vh] flex flex-col bg-zinc-900 border-zinc-700 text-zinc-100 sm:max-w-[900px] max-w-[90vw]">
        <DialogHeader>
          <div className="flex items-center justify-between">
            <DialogTitle className="text-zinc-100">{title}</DialogTitle>
            {copyContent !== undefined && (
              <button
                onClick={() => copy(copyContent)}
                className="text-zinc-500 hover:text-zinc-300 transition-colors p-1 rounded hover:bg-zinc-800"
                title="Copy to clipboard"
              >
                {justCopied ? (
                  <Check className="w-3.5 h-3.5 text-green-400" />
                ) : (
                  <Copy className="w-3.5 h-3.5" />
                )}
              </button>
            )}
          </div>
        </DialogHeader>
        <div className="flex-1 overflow-y-auto min-h-0">
          {children}
        </div>
      </DialogContent>
    </Dialog>
  )
}
