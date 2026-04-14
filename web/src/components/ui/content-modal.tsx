import { useState, useMemo } from "react"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Copy, Check } from "lucide-react"
import { useCopyFeedback } from "@/hooks/useCopyFeedback"
import { cn } from "@/lib/utils"

type ContentType = "markdown" | "json" | "raw"

function detectContentType(text: string): ContentType[] {
  const types: ContentType[] = ["raw"]

  // JSON detection
  const trimmed = text.trim()
  if (
    (trimmed.startsWith("{") && trimmed.endsWith("}")) ||
    (trimmed.startsWith("[") && trimmed.endsWith("]"))
  ) {
    try {
      JSON.parse(trimmed)
      types.unshift("json")
    } catch {
      // not valid JSON
    }
  }

  // Markdown detection — look for common patterns
  if (
    /^#{1,6}\s/m.test(text) ||
    /^\*\*[^*]+\*\*/m.test(text) ||
    /^[-*]\s/m.test(text) ||
    /^\d+\.\s/m.test(text) ||
    /```[\s\S]*```/m.test(text) ||
    /\[.+\]\(.+\)/m.test(text)
  ) {
    types.unshift("markdown")
  }

  return types
}

import { Markdown } from "@/components/ui/markdown";
export { Markdown as MarkdownView };

function JsonView({ text }: { text: string }) {
  const formatted = useMemo(() => {
    try {
      return JSON.stringify(JSON.parse(text.trim()), null, 2)
    } catch {
      return text
    }
  }, [text])

  return (
    <pre className="whitespace-pre-wrap text-sm text-zinc-300 font-mono p-3 leading-relaxed">
      {formatted}
    </pre>
  )
}

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
  const contentTypes = useMemo(
    () => (copyContent ? detectContentType(copyContent) : []),
    [copyContent],
  )
  const hasTabs = contentTypes.length > 1
  const defaultTab = contentTypes[0] ?? "raw"
  const [activeTab, setActiveTab] = useState<ContentType>(defaultTab)
  const [prevDefault, setPrevDefault] = useState(defaultTab)
  if (defaultTab !== prevDefault) {
    setPrevDefault(defaultTab)
    setActiveTab(defaultTab)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false} className="max-h-[80vh] flex flex-col bg-zinc-900 border-zinc-700 text-zinc-100 sm:max-w-[900px] max-w-[90vw]">
        <DialogHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <DialogTitle className="text-zinc-100">{title}</DialogTitle>
              {hasTabs && (
                <div className="flex items-center gap-0.5 rounded-md border border-zinc-700 p-0.5">
                  {contentTypes.map((type) => (
                    <button
                      key={type}
                      onClick={() => setActiveTab(type)}
                      className={cn(
                        "px-2 py-0.5 text-[11px] rounded transition-colors capitalize",
                        (activeTab === type || (!contentTypes.includes(activeTab) && type === defaultTab))
                          ? "bg-zinc-700 text-zinc-100"
                          : "text-zinc-500 hover:text-zinc-300",
                      )}
                    >
                      {type}
                    </button>
                  ))}
                </div>
              )}
            </div>
            {copyContent !== undefined && (
              <button
                onClick={(e) => { e.stopPropagation(); copy(copyContent); }}
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
          {hasTabs && copyContent ? (
            (() => {
              const tab = contentTypes.includes(activeTab) ? activeTab : defaultTab
              if (tab === "markdown") return <div className="p-3"><Markdown>{copyContent}</Markdown></div>
              if (tab === "json") return <JsonView text={copyContent} />
              return (
                <pre className="whitespace-pre-wrap text-sm text-zinc-300 font-mono p-3 leading-relaxed">
                  {copyContent}
                </pre>
              )
            })()
          ) : (
            children
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
