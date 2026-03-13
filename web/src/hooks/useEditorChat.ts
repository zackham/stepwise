import { useState, useCallback, useRef } from "react";
import { streamEditorChat } from "@/lib/api";

export type AgentMode = "claude" | "codex" | "simple";

export interface ToolActivity {
  id: string;
  name: string;
  input: Record<string, string>;
  output?: string;
  done: boolean;
  kind?: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  yamlBlocks?: Array<{ content: string; apply_id: string; applied?: boolean }>;
  toolActivities?: ToolActivity[];
  filesChanged?: string[];
}

interface UseEditorChatOptions {
  currentYaml: string;
  selectedStep: string | null;
  flowPath: string | null;
  onApplyYaml: (yaml: string) => void;
  onFilesChanged?: () => void;
}

export function useEditorChat({
  currentYaml,
  selectedStep,
  flowPath,
  onApplyYaml,
  onFilesChanged,
}: UseEditorChatOptions) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [agentMode, setAgentMode] = useState<AgentMode>("claude");
  const [sessionId, setSessionId] = useState<string | null>(null);

  // Keep latest values in refs for the streaming callback
  const currentYamlRef = useRef(currentYaml);
  currentYamlRef.current = currentYaml;
  const selectedStepRef = useRef(selectedStep);
  selectedStepRef.current = selectedStep;
  const flowPathRef = useRef(flowPath);
  flowPathRef.current = flowPath;

  const reset = useCallback(() => {
    setMessages([]);
    setSessionId(null);
  }, []);

  const send = useCallback(
    async (text: string) => {
      if (!text.trim() || isStreaming) return;

      setIsStreaming(true);

      const userMsg: ChatMessage = { role: "user", content: text };
      setMessages((prev) => [...prev, userMsg]);

      const history = messages.map((m) => ({ role: m.role, content: m.content }));

      let fullContent = "";
      const yamlBlocks: ChatMessage["yamlBlocks"] = [];
      const toolActivities: ToolActivity[] = [];
      let filesChanged: string[] = [];

      const assistantIdx = messages.length + 1;

      const updateMsg = () => {
        setMessages((prev) => {
          const updated = [...prev];
          updated[assistantIdx] = {
            role: "assistant",
            content: fullContent,
            yamlBlocks: [...yamlBlocks],
            toolActivities: [...toolActivities],
            filesChanged: filesChanged.length > 0 ? [...filesChanged] : undefined,
          };
          return updated;
        });
      };

      try {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: "", yamlBlocks: [], toolActivities: [] },
        ]);

        for await (const chunk of streamEditorChat(
          text,
          history,
          currentYamlRef.current,
          selectedStepRef.current ?? undefined,
          agentMode,
          sessionId ?? undefined,
          flowPathRef.current ?? undefined,
        )) {
          if (chunk.type === "session") {
            setSessionId(chunk.session_id ?? null);
          } else if (chunk.type === "text") {
            fullContent += chunk.content ?? "";
            updateMsg();
          } else if (chunk.type === "yaml") {
            yamlBlocks.push({
              content: chunk.content ?? "",
              apply_id: chunk.apply_id ?? "",
            });
            updateMsg();
          } else if (chunk.type === "tool_use") {
            toolActivities.push({
              id: chunk.tool_use_id ?? "",
              name: chunk.tool_name ?? "",
              input: chunk.tool_input ?? {},
              done: false,
              kind: chunk.tool_kind,
            });
            updateMsg();
          } else if (chunk.type === "tool_result") {
            const idx = toolActivities.findIndex((t) => t.id === chunk.tool_use_id);
            if (idx >= 0) {
              toolActivities[idx] = {
                ...toolActivities[idx],
                output: chunk.tool_output,
                done: true,
                kind: chunk.tool_kind || toolActivities[idx].kind,
              };
            }
            updateMsg();
          } else if (chunk.type === "files_changed") {
            filesChanged = chunk.paths ?? [];
            updateMsg();
            onFilesChanged?.();
          } else if (chunk.type === "done") {
            for (let i = 0; i < toolActivities.length; i++) {
              if (!toolActivities[i].done) {
                toolActivities[i] = { ...toolActivities[i], done: true };
              }
            }
            updateMsg();
          } else if (chunk.type === "error") {
            fullContent += `\n\n**Error:** ${chunk.content}`;
            updateMsg();
          }
        }
      } catch {
        // Stream ended or errored
      } finally {
        for (let i = 0; i < toolActivities.length; i++) {
          if (!toolActivities[i].done) {
            toolActivities[i] = { ...toolActivities[i], done: true };
          }
        }
        if (toolActivities.length > 0) {
          updateMsg();
        }
        setIsStreaming(false);
      }
    },
    [isStreaming, messages, agentMode, sessionId, onFilesChanged]
  );

  const applyYaml = useCallback(
    (msgIdx: number, blockIdx: number) => {
      const msg = messages[msgIdx];
      if (!msg?.yamlBlocks?.[blockIdx]) return;
      onApplyYaml(msg.yamlBlocks[blockIdx].content);
      setMessages((prev) => {
        const updated = [...prev];
        const m = { ...updated[msgIdx] };
        m.yamlBlocks = m.yamlBlocks?.map((b, i) =>
          i === blockIdx ? { ...b, applied: true } : b
        );
        updated[msgIdx] = m;
        return updated;
      });
    },
    [messages, onApplyYaml]
  );

  return {
    messages,
    isStreaming,
    sessionId,
    agentMode,
    setAgentMode,
    send,
    reset,
    applyYaml,
  };
}
