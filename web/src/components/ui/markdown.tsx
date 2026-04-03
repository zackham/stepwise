import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

interface MarkdownProps {
  children: string;
  className?: string;
}

/**
 * Shared markdown renderer using react-markdown + remark-gfm.
 * Styled for the dark zinc theme. Tables disabled by default.
 */
export function Markdown({ children, className }: MarkdownProps) {
  return (
    <div className={cn("space-y-1.5 text-left overflow-hidden [overflow-wrap:anywhere]", className)}>
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      disallowedElements={["table", "thead", "tbody", "tr", "th", "td"]}
      unwrapDisallowed
      components={{
        h1: ({ children }) => (
          <div className="text-lg font-semibold text-zinc-900 dark:text-zinc-100 mt-4 mb-1">{children}</div>
        ),
        h2: ({ children }) => (
          <div className="text-base font-semibold text-zinc-900 dark:text-zinc-100 mt-3 mb-1">{children}</div>
        ),
        h3: ({ children }) => (
          <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 mt-3 mb-1">{children}</div>
        ),
        h4: ({ children }) => (
          <div className="text-sm font-semibold text-zinc-800 dark:text-zinc-200 mt-2 mb-0.5">{children}</div>
        ),
        h5: ({ children }) => (
          <div className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 mt-2">{children}</div>
        ),
        h6: ({ children }) => (
          <div className="text-xs font-semibold text-zinc-600 dark:text-zinc-400 mt-2">{children}</div>
        ),
        p: ({ children }) => (
          <p className="text-sm text-zinc-700 dark:text-zinc-300 leading-relaxed">{children}</p>
        ),
        strong: ({ children }) => (
          <strong className="font-semibold text-zinc-900 dark:text-zinc-100">{children}</strong>
        ),
        em: ({ children }) => (
          <em className="italic text-zinc-700 dark:text-zinc-300">{children}</em>
        ),
        code: ({ children, className: codeClassName }) => {
          // Block code (has language class) vs inline
          const isBlock = codeClassName?.startsWith("language-");
          if (isBlock) {
            return (
              <code className={cn("text-xs", codeClassName)}>
                {children}
              </code>
            );
          }
          return (
            <code className="px-1 py-0.5 rounded bg-zinc-200 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 text-xs font-mono break-words">
              {children}
            </code>
          );
        },
        pre: ({ children }) => (
          <pre className="bg-zinc-100 dark:bg-zinc-950 rounded p-2 text-xs font-mono text-zinc-700 dark:text-zinc-300 overflow-x-auto my-2 break-words whitespace-pre-wrap">
            {children}
          </pre>
        ),
        ul: ({ children }) => (
          <ul className="space-y-0.5 ml-1">{children}</ul>
        ),
        ol: ({ children }) => (
          <ol className="space-y-0.5 ml-1 list-decimal list-inside">{children}</ol>
        ),
        li: ({ children }) => (
          <li className="flex gap-2 text-sm text-zinc-700 dark:text-zinc-300 leading-relaxed">
            <span className="text-zinc-600 shrink-0">•</span>
            <span>{children}</span>
          </li>
        ),
        a: ({ href, children }) => (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-400 hover:text-blue-300 hover:underline"
          >
            {children}
          </a>
        ),
        blockquote: ({ children }) => (
          <blockquote className="border-l-2 border-zinc-300 dark:border-zinc-700 pl-3 text-sm text-zinc-500 dark:text-zinc-400 italic">
            {children}
          </blockquote>
        ),
        hr: () => <hr className="border-zinc-200 dark:border-zinc-800 my-3" />,
      }}
    >
      {children}
    </ReactMarkdown>
    </div>
  );
}
