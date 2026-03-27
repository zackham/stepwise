import { diffLines } from "diff";
import { useMemo, useState } from "react";

interface DiffViewProps {
  before: unknown;
  after: unknown;
  contextLines?: number;
}

function serialize(value: unknown): string | null {
  if (value == null) return null;
  return JSON.stringify(value, null, 2);
}

interface DiffBlock {
  type: "added" | "removed" | "context" | "expander";
  lines: string[];
  /** For expander blocks, the total hidden line count */
  hiddenCount?: number;
}

function buildBlocks(
  parts: ReturnType<typeof diffLines>,
  contextLines: number
): DiffBlock[] {
  // First, flatten into tagged lines
  const taggedLines: { type: "added" | "removed" | "context"; text: string }[] =
    [];
  for (const part of parts) {
    const lines = part.value.replace(/\n$/, "").split("\n");
    const type = part.added ? "added" : part.removed ? "removed" : "context";
    for (const line of lines) {
      taggedLines.push({ type, text: line });
    }
  }

  // Build blocks: collapse large context sections
  const blocks: DiffBlock[] = [];
  let contextBuffer: string[] = [];

  const flushContext = (isFirst: boolean, isLast: boolean) => {
    if (contextBuffer.length === 0) return;
    const threshold = contextLines * 2 + 1;
    if (contextBuffer.length <= threshold) {
      blocks.push({ type: "context", lines: [...contextBuffer] });
    } else {
      const showBefore = isFirst ? 0 : contextLines;
      const showAfter = isLast ? 0 : contextLines;
      if (showBefore > 0) {
        blocks.push({
          type: "context",
          lines: contextBuffer.slice(0, showBefore),
        });
      }
      blocks.push({
        type: "expander",
        lines: contextBuffer.slice(showBefore, contextBuffer.length - showAfter),
        hiddenCount: contextBuffer.length - showBefore - showAfter,
      });
      if (showAfter > 0) {
        blocks.push({
          type: "context",
          lines: contextBuffer.slice(contextBuffer.length - showAfter),
        });
      }
    }
    contextBuffer = [];
  };

  // Determine change positions to know first/last context
  const changeIndices: number[] = [];
  taggedLines.forEach((l, i) => {
    if (l.type !== "context") changeIndices.push(i);
  });

  let i = 0;
  while (i < taggedLines.length) {
    const line = taggedLines[i];
    if (line.type === "context") {
      contextBuffer.push(line.text);
      i++;
    } else {
      const isFirstChange = changeIndices[0] === i;
      flushContext(blocks.length === 0 && isFirstChange, false);

      // Collect consecutive same-type lines
      const changeType = line.type;
      const changeLines: string[] = [];
      while (i < taggedLines.length && taggedLines[i].type === changeType) {
        changeLines.push(taggedLines[i].text);
        i++;
      }
      blocks.push({ type: changeType, lines: changeLines });
    }
  }
  // Flush trailing context
  flushContext(blocks.length === 0, true);

  return blocks;
}

export function DiffView({
  before,
  after,
  contextLines = 3,
}: DiffViewProps) {
  const beforeStr = serialize(before);
  const afterStr = serialize(after);

  const result = useMemo(() => {
    if (beforeStr == null && afterStr == null) {
      return { status: "empty" as const };
    }
    if (beforeStr == null) {
      return {
        status: "diff" as const,
        blocks: [
          { type: "added" as const, lines: afterStr!.split("\n") },
        ],
      };
    }
    if (afterStr == null) {
      return {
        status: "diff" as const,
        blocks: [
          { type: "removed" as const, lines: beforeStr.split("\n") },
        ],
      };
    }
    if (beforeStr === afterStr) {
      return { status: "identical" as const };
    }
    const parts = diffLines(beforeStr, afterStr);
    return {
      status: "diff" as const,
      blocks: buildBlocks(parts, contextLines),
    };
  }, [beforeStr, afterStr, contextLines]);

  if (result.status === "empty") {
    return (
      <div className="text-xs text-zinc-600 italic py-2">
        No output in either attempt
      </div>
    );
  }

  if (result.status === "identical") {
    return (
      <div className="text-xs text-zinc-500 italic py-2">
        Outputs are identical
      </div>
    );
  }

  return (
    <div className="bg-zinc-50 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 rounded overflow-hidden font-mono text-xs">
      {result.blocks.map((block, bi) => (
        <DiffBlock key={bi} block={block} />
      ))}
    </div>
  );
}

function DiffBlock({ block }: { block: DiffBlock }) {
  const [expanded, setExpanded] = useState(false);

  if (block.type === "expander") {
    if (expanded) {
      return (
        <>
          {block.lines.map((line, i) => (
            <div key={i} className="flex text-zinc-500">
              <span className="w-5 shrink-0 text-center select-none text-zinc-700">
                {" "}
              </span>
              <span className="flex-1 px-2 whitespace-pre-wrap break-all">
                {line}
              </span>
            </div>
          ))}
        </>
      );
    }
    return (
      <button
        onClick={() => setExpanded(true)}
        className="w-full text-[10px] text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 bg-zinc-100 dark:bg-zinc-900 border-y border-zinc-200 dark:border-zinc-800 py-1 px-2 text-center cursor-pointer"
      >
        Show {block.hiddenCount} hidden lines
      </button>
    );
  }

  const colorClass =
    block.type === "added"
      ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
      : block.type === "removed"
        ? "bg-red-500/10 text-red-700 dark:text-red-300"
        : "text-zinc-500";

  const gutter =
    block.type === "added" ? "+" : block.type === "removed" ? "-" : " ";

  const gutterColor =
    block.type === "added"
      ? "text-emerald-500"
      : block.type === "removed"
        ? "text-red-500"
        : "text-zinc-700";

  return (
    <>
      {block.lines.map((line, i) => (
        <div key={i} className={`flex ${colorClass}`}>
          <span
            className={`w-5 shrink-0 text-center select-none ${gutterColor}`}
          >
            {gutter}
          </span>
          <span className="flex-1 px-2 whitespace-pre-wrap break-all">
            {line}
          </span>
        </div>
      ))}
    </>
  );
}
