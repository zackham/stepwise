# Plan: Fix Agent Chat Rendering — Interleaved Tool Calls

## Overview

The Editor Chat panel (`ChatMessages.tsx`) renders all tool activities as a collapsed summary block **above** the message text content. When an agent response contains `text → tool_call → text → tool_call → text`, the tools are stripped from their position and grouped together, destroying reading order. The conversation flow is lost.

The fix replaces the flat `content: string` + `toolActivities: ToolActivity[]` message model with an ordered sequence of **content blocks** — text, tool_use, tool_result, yaml, files_changed — preserving the exact interleaving as stream events arrive.

The Job Agent Stream (`AgentStreamView.tsx`) already renders tool cards interleaved with text via its segment model. No structural changes needed there — it serves as the reference pattern.

## Requirements

| # | Requirement | Acceptance Criteria |
|---|------------|-------------------|
| R1 | Ordered content blocks in ChatMessage | `ChatMessage.blocks` is an ordered array of typed blocks. Consecutive text chunks merge into the last text block. Legacy `content`/`toolActivities`/`yamlBlocks`/`filesChanged` fields are removed. |
| R2 | Interleaved rendering | Rendering iterates `message.blocks` in order. Given `[text, tool_use, tool_result, text]`, the DOM shows those items in that exact vertical order. |
| R3 | Collapsible tool cards | Tool calls render as compact inline cards (icon + name + status). Collapsed by default. Expanding shows tool input (key-value) and output (preformatted text). |
| R4 | tool_use and tool_result lifecycle | `tool_use` creates a card in "running" state. Matching `tool_result` updates to "completed" and attaches output. Both render at their arrival position in the block sequence. |
| R5 | Visual distinction | Tool cards use distinct background, left border colored by kind (blue=read, amber=edit, green=search, purple=command). Running = spinner, completed = checkmark. |
| R6 | YAML and files_changed blocks inline | YAML blocks and files_changed notices render at their position in the block sequence, not grouped at the end. |
| R7 | No regression in chat functionality | Apply YAML, send messages, session persistence, history all continue working. |

## Assumptions

Each verified against actual source files:

1. **Stream events arrive in correct interleaved order.** The backend (`editor_llm.py:338-421`) emits `text`, `tool_use`, `tool_result` chunks inline as they happen during ACP streaming. Order is preserved.

2. **`tool_use` always precedes its matching `tool_result`.** Linked by `tool_use_id`. Verified: `editor_llm.py:389` emits `tool_use` on `tool_call`, `editor_llm.py:414` emits `tool_result` on completed `tool_call_update`.

3. **All required data already in ChatChunk.** `tool_name`, `tool_input`, `tool_output`, `tool_kind`, `tool_use_id` are all present in the `ChatChunk` interface (`api.ts:557-577`). No backend changes needed.

4. **`ChatMessages` is only consumed by `ChatSidebar.tsx`.** Single import site — changes are contained.

5. **`useEditorChat` is the sole producer of `ChatMessage[]`.** Only file that constructs `ChatMessage` objects.

6. **`ToolActivity` type is only imported by `ChatMessages.tsx`.** Can be removed without wider impact.

## Implementation Steps

### Step 1: Define block-based message model

**File:** `web/src/hooks/useEditorChat.ts`

Replace the flat message structure with an ordered block array:

```typescript
// Remove ToolActivity interface

export type ChatBlock =
  | { type: "text"; content: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, string>; kind?: string; done: boolean; output?: string; isError?: boolean }
  | { type: "yaml"; content: string; apply_id: string; applied?: boolean }
  | { type: "files_changed"; paths: string[] };

export interface ChatMessage {
  role: "user" | "assistant";
  blocks: ChatBlock[];
}
```

### Step 2: Rewrite stream processing in useEditorChat

**File:** `web/src/hooks/useEditorChat.ts` — `send()` function (lines 56-165)

Replace the accumulator variables (`fullContent`, `yamlBlocks`, `toolActivities`, `filesChanged`) with a single `blocks: ChatBlock[]` array:

- **`text` chunk:** If last block is `{type: "text"}`, append to `content`. Otherwise push new text block.
- **`tool_use` chunk:** Push `{type: "tool_use", id, name, input, kind, done: false}`.
- **`tool_result` chunk:** Find matching `tool_use` block by `id`, set `done: true`, `output`, `isError`.
- **`yaml` chunk:** Push `{type: "yaml", content, apply_id}`.
- **`files_changed` chunk:** Push `{type: "files_changed", paths}`.
- **`done` chunk:** Mark all incomplete `tool_use` blocks as `done: true`.
- **`error` chunk:** Append as text block with error formatting.

Update `updateMsg()`:
```typescript
const blocks: ChatBlock[] = [];

const updateMsg = () => {
  setMessages((prev) => {
    const updated = [...prev];
    updated[assistantIdx] = { role: "assistant", blocks: [...blocks] };
    return updated;
  });
};
```

Update `history` extraction (line 65) to pull text from blocks:
```typescript
const history = messages.map((m) => ({
  role: m.role,
  content: m.blocks
    .filter((b): b is Extract<ChatBlock, { type: "text" }> => b.type === "text")
    .map((b) => b.content)
    .join("\n"),
}));
```

Update `applyYaml` to find yaml blocks by `apply_id` within `message.blocks`.

User messages: create with `blocks: [{ type: "text", content: text }]`.

### Step 3: Create ToolCallCard component

**File:** `web/src/components/editor/ToolCallCard.tsx` (new)

A collapsible inline tool call card:

```typescript
interface ToolCallCardProps {
  name: string;
  kind?: string;
  input: Record<string, string>;
  output?: string;
  done: boolean;
  isError?: boolean;
}
```

**Collapsed state (default):** Single row — icon (by kind) + tool name (truncated) + status indicator (spinner/checkmark). Chevron if expandable.

**Expanded state:** Below the header:
- **Input section:** Key-value pairs from `input` dict. Keys in `text-zinc-500`, values in `text-zinc-300 font-mono`. Skip if input is empty.
- **Output section:** Preformatted text in `text-[11px] font-mono`. Max height 200px with scroll. Red tint if `isError`. Show only when `done` and output exists.

Kind-to-style mapping:
| kind | Border | Icon |
|------|--------|------|
| read, Read | `border-l-blue-500` | FileText |
| edit, write, Write, Edit | `border-l-amber-500` | Pencil |
| search, Grep, Glob | `border-l-green-500` | Search |
| command, Bash | `border-l-purple-500` | Terminal |
| default | `border-l-zinc-500` | Cog |

Outer wrapper: `bg-zinc-100/50 dark:bg-zinc-900/50 border border-zinc-300/20 dark:border-zinc-700/20 border-l-2 rounded text-[11px] my-1`.

### Step 4: Rewrite ChatMessages block rendering

**File:** `web/src/components/editor/ChatMessages.tsx`

Remove `ToolActivitiesBlock` component entirely. Replace the per-message rendering with a block iterator:

```tsx
{msg.role === "user" ? (
  <div className="text-xs text-blue-700 dark:text-blue-300 bg-blue-100/50 dark:bg-blue-950/30 rounded px-2.5 py-1.5">
    <span className="whitespace-pre-wrap break-words">
      {msg.blocks.filter(b => b.type === "text").map(b => b.content).join("")}
    </span>
  </div>
) : (
  <div className="space-y-1.5">
    {msg.blocks.map((block, blockIdx) => {
      switch (block.type) {
        case "text":
          return block.content ? (
            <div key={blockIdx} className="text-xs text-zinc-700 dark:text-zinc-300 leading-relaxed">
              <MarkdownContent content={block.content} />
            </div>
          ) : null;
        case "tool_use":
          return (
            <ToolCallCard
              key={block.id}
              name={block.name}
              kind={block.kind}
              input={block.input}
              output={block.output}
              done={block.done}
              isError={block.isError}
            />
          );
        case "yaml":
          return <YamlBlock key={block.apply_id} block={block} onApply={() => onApplyYaml(msgIdx, block.apply_id)} />;
        case "files_changed":
          return <FilesChangedBlock key={blockIdx} paths={block.paths} />;
      }
    })}
  </div>
)}
```

Extract the existing YAML rendering (lines 202-228) into a `YamlBlock` sub-component. Extract the files_changed rendering (lines 188-200) into a `FilesChangedBlock` sub-component. Both stay in the same file.

### Step 5: Update applyYaml callback signature

**Files:** `web/src/components/editor/ChatMessages.tsx`, `web/src/components/editor/ChatSidebar.tsx`, `web/src/hooks/useEditorChat.ts`

Change from `(msgIdx: number, blockIdx: number)` to `(msgIdx: number, applyId: string)`:

In `useEditorChat.ts:applyYaml`:
```typescript
const applyYaml = useCallback(
  (msgIdx: number, applyId: string) => {
    const msg = messages[msgIdx];
    const yamlBlock = msg?.blocks.find(
      (b): b is Extract<ChatBlock, { type: "yaml" }> =>
        b.type === "yaml" && b.apply_id === applyId
    );
    if (!yamlBlock) return;
    onApplyYaml(yamlBlock.content);
    setMessages((prev) => {
      const updated = [...prev];
      updated[msgIdx] = {
        ...updated[msgIdx],
        blocks: updated[msgIdx].blocks.map((b) =>
          b.type === "yaml" && b.apply_id === applyId
            ? { ...b, applied: true }
            : b
        ),
      };
      return updated;
    });
  },
  [messages, onApplyYaml]
);
```

### Step 6: Update ChatMessagesProps interface

**File:** `web/src/components/editor/ChatMessages.tsx`

```typescript
interface ChatMessagesProps {
  messages: ChatMessage[];
  isStreaming: boolean;
  onApplyYaml: (msgIdx: number, applyId: string) => void;
  emptyMessage?: string;
}
```

### Step 7: Add tests

**File:** `web/src/components/editor/ChatMessages.test.tsx` (new or update if exists)

Tests:

1. **Block ordering:** Message with `[text("Before"), tool_use(running), text("After")]` blocks renders text, then tool card, then text in DOM order.
2. **Tool completion:** `tool_use` block with `done: true` and `output` renders checkmark status and expandable output.
3. **Collapsible behavior:** Tool card renders collapsed by default. After click, input/output become visible.
4. **YAML inline:** YAML block renders at its position between text blocks with Apply button.
5. **Files changed inline:** files_changed block renders at its position.
6. **User message rendering:** User messages render as simple text bubble regardless of block types.
7. **Empty message:** Shows empty state message when no messages.

**File:** `web/src/hooks/useEditorChat.test.ts` (new or update if exists)

Tests:

1. **Text merging:** Two consecutive `text` chunks produce a single text block.
2. **Tool interleaving:** Sequence of `text → tool_use → text → tool_result → text` produces 4 blocks in order (text, tool_use, text, text) with the tool_use block updated with output.
3. **YAML block ordering:** `text → yaml → text` produces 3 blocks in order.
4. **Done marks tools complete:** `done` chunk marks all incomplete tool_use blocks as done.
5. **Error appends text block:** `error` chunk appends a text block with error content.

## File Change Summary

| File | Change |
|------|--------|
| `web/src/hooks/useEditorChat.ts` | Replace flat message model with `ChatBlock[]`; rewrite stream processing; update `applyYaml` |
| `web/src/components/editor/ChatMessages.tsx` | Remove `ToolActivitiesBlock`; render blocks in order; extract `YamlBlock`/`FilesChangedBlock` sub-components |
| `web/src/components/editor/ToolCallCard.tsx` | **New** — collapsible tool call card with input/output display |
| `web/src/components/editor/ChatSidebar.tsx` | Update `onApplyYaml` prop type (`blockIdx: number` → `applyId: string`) |
| `web/src/components/editor/ChatMessages.test.tsx` | **New** — tests for block-based rendering |

No backend changes required — all data is already present in the stream.

## Testing Strategy

```bash
# Run all frontend tests
cd web && npm run test -- --run

# Run specific test files
cd web && npx vitest run src/components/editor/ChatMessages.test.tsx
cd web && npx vitest run src/hooks/useEditorChat.test.ts

# Lint
cd web && npm run lint

# Type check
cd web && npx tsc --noEmit

# Manual verification
cd web && npm run dev
# 1. Open editor page, start a chat with an agent mode (Claude/Codex)
# 2. Send: "read the flow file and suggest improvements"
# 3. Verify: tool cards appear BETWEEN text blocks, not grouped above
# 4. Verify: cards collapsed by default, click expands to show input/output
# 5. Verify: running tools show spinner, completed show checkmark with output
# 6. Verify: Apply YAML still works on inline yaml blocks
# 7. Verify: files_changed block appears at correct position
```

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Breaking chat history serialization | `history` array only needs `role + content` (text). Extract via `blocks.filter(b => b.type === "text")`. No backend change. |
| `applyYaml` index shift | Switching from positional `blockIdx` to `apply_id` lookup is more robust. |
| Performance with many tool calls | Each `ToolCallCard` is lightweight with local expand state. Typical agent response has 5-20 tools. |
| Large tool output in memory | Tool output is already bounded by the backend's NDJSON chunk size. No additional truncation needed. |
| Regression in existing chat features | Comprehensive test suite covers rendering, apply, streaming state. Manual smoke test covers end-to-end. |
