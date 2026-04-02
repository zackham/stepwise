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

function MarkdownView({ text }: { text: string }) {
  // Simple markdown renderer — handles headings, bold, code blocks, lists, links
  const lines = text.split("\n")
  const elements: React.ReactNode[] = []
  let inCodeBlock = false
  let codeLines: string[] = []
  let codeKey = 0

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]

    if (line.startsWith("```")) {
      if (inCodeBlock) {
        elements.push(
          <pre key={`code-${codeKey++}`} className="bg-zinc-950 rounded p-2 text-xs font-mono text-zinc-300 overflow-x-auto my-2">
            {codeLines.join("\n")}
          </pre>
        )
        codeLines = []
        inCodeBlock = false
      } else {
        inCodeBlock = true
      }
      continue
    }

    if (inCodeBlock) {
      codeLines.push(line)
      continue
    }

    // Headings
    const headingMatch = line.match(/^(#{1,6})\s+(.+)/)
    if (headingMatch) {
      const level = headingMatch[1].length
      const sizes = ["text-lg", "text-base", "text-sm", "text-sm", "text-xs", "text-xs"]
      elements.push(
        <div key={i} className={cn("font-semibold text-zinc-100 mt-3 mb-1", sizes[level - 1])}>
          {renderInline(headingMatch[2])}
        </div>
      )
      continue
    }

    // Empty line
    if (!line.trim()) {
      elements.push(<div key={i} className="h-2" />)
      continue
    }

    // List items
    if (/^[-*]\s/.test(line)) {
      elements.push(
        <div key={i} className="flex gap-2 text-sm text-zinc-300 leading-relaxed">
          <span className="text-zinc-600 shrink-0">•</span>
          <span>{renderInline(line.replace(/^[-*]\s/, ""))}</span>
        </div>
      )
      continue
    }

    // Numbered list
    const numMatch = line.match(/^(\d+)\.\s(.+)/)
    if (numMatch) {
      elements.push(
        <div key={i} className="flex gap-2 text-sm text-zinc-300 leading-relaxed">
          <span className="text-zinc-500 shrink-0 tabular-nums">{numMatch[1]}.</span>
          <span>{renderInline(numMatch[2])}</span>
        </div>
      )
      continue
    }

    // Regular paragraph
    elements.push(
      <p key={i} className="text-sm text-zinc-300 leading-relaxed">
        {renderInline(line)}
      </p>
    )
  }

  return <div className="space-y-0.5 p-3">{elements}</div>
}

function renderInline(text: string): React.ReactNode {
  // Handle bold, code, links
  const parts: React.ReactNode[] = []
  let remaining = text
  let key = 0

  while (remaining) {
    // Bold
    const boldMatch = remaining.match(/\*\*([^*]+)\*\*/)
    // Inline code
    const codeMatch = remaining.match(/`([^`]+)`/)
    // Link
    const linkMatch = remaining.match(/\[([^\]]+)\]\(([^)]+)\)/)

    // Find earliest match
    const matches = [
      boldMatch && { idx: remaining.indexOf(boldMatch[0]), match: boldMatch, type: "bold" },
      codeMatch && { idx: remaining.indexOf(codeMatch[0]), match: codeMatch, type: "code" },
      linkMatch && { idx: remaining.indexOf(linkMatch[0]), match: linkMatch, type: "link" },
    ].filter(Boolean).sort((a, b) => a!.idx - b!.idx)

    if (matches.length === 0) {
      parts.push(remaining)
      break
    }

    const first = matches[0]!
    if (first.idx > 0) {
      parts.push(remaining.slice(0, first.idx))
    }

    if (first.type === "bold") {
      parts.push(<strong key={key++} className="font-semibold text-zinc-100">{first.match![1]}</strong>)
    } else if (first.type === "code") {
      parts.push(<code key={key++} className="px-1 py-0.5 rounded bg-zinc-800 text-zinc-300 text-xs font-mono">{first.match![1]}</code>)
    } else if (first.type === "link") {
      parts.push(<span key={key++} className="text-blue-400">{first.match![1]}</span>)
    }

    remaining = remaining.slice(first.idx + first.match![0].length)
  }

  return parts.length === 1 ? parts[0] : <>{parts}</>
}

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
          {hasTabs && copyContent ? (
            (() => {
              const tab = contentTypes.includes(activeTab) ? activeTab : defaultTab
              if (tab === "markdown") return <MarkdownView text={copyContent} />
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
