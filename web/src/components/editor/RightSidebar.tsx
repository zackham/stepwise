import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { StepDefinitionPanel } from "./StepDefinitionPanel";
import { ChatSidebar } from "./ChatSidebar";
import { Inspect, MessageSquare } from "lucide-react";
import { cn } from "@/lib/utils";
import type { StepDefinition } from "@/lib/types";
import type { ChatMessage, AgentMode } from "@/hooks/useEditorChat";

export type SidebarTab = "inspector" | "chat";

interface RightSidebarProps {
  activeTab: SidebarTab;
  onTabChange: (tab: SidebarTab) => void;
  // Inspector props
  selectedStepDef: StepDefinition | null;
  onCloseInspector: () => void;
  onDeleteStep?: () => void;
  onViewFile?: (path: string) => void;
  onViewSource?: (field: string) => void;
  // Chat props
  chatMessages: ChatMessage[];
  isChatStreaming: boolean;
  onChatSend: (text: string) => void;
  onChatReset: () => void;
  onApplyYaml: (msgIdx: number, blockIdx: number) => void;
  agentMode: AgentMode;
  onModeChange: (mode: AgentMode) => void;
  sessionId: string | null;
  flowPath: string | null;
  stepContext: string | null;
  onRemoveStepContext: () => void;
}

export function RightSidebar({
  activeTab,
  onTabChange,
  selectedStepDef,
  onCloseInspector,
  onDeleteStep,
  onViewFile,
  onViewSource,
  chatMessages,
  isChatStreaming,
  onChatSend,
  onChatReset,
  onApplyYaml,
  agentMode,
  onModeChange,
  sessionId,
  flowPath,
  stepContext,
  onRemoveStepContext,
}: RightSidebarProps) {
  return (
    <div className="w-80 border-l border-border shrink-0 flex flex-col">
      <Tabs
        value={activeTab}
        onValueChange={(v) => onTabChange(v as SidebarTab)}
        className="flex flex-col flex-1 min-h-0 gap-0"
      >
        <TabsList variant="line" className="w-full px-1 border-b border-border shrink-0">
          <TabsTrigger
            value="inspector"
            disabled={!selectedStepDef}
            className="text-xs gap-1 px-2.5"
          >
            <Inspect className="w-3 h-3" />
            Inspector
            {selectedStepDef && activeTab !== "inspector" && (
              <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
            )}
          </TabsTrigger>
          <TabsTrigger value="chat" className="text-xs gap-1 px-2.5">
            <MessageSquare className="w-3 h-3" />
            Chat
            {isChatStreaming && (
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-violet-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-violet-500" />
              </span>
            )}
          </TabsTrigger>
        </TabsList>

        <TabsContent
          value="inspector"
          className={cn(
            "flex-1 min-h-0 overflow-hidden",
            activeTab !== "inspector" && "hidden"
          )}
        >
          {selectedStepDef ? (
            <StepDefinitionPanel
              stepDef={selectedStepDef}
              onClose={onCloseInspector}
              onDelete={onDeleteStep}
              onViewFile={onViewFile}
              onViewSource={onViewSource}
            />
          ) : (
            <div className="flex items-center justify-center h-full text-xs text-zinc-600">
              Select a step to inspect
            </div>
          )}
        </TabsContent>

        <TabsContent
          value="chat"
          className={cn(
            "flex-1 min-h-0 overflow-hidden flex flex-col",
            activeTab !== "chat" && "hidden"
          )}
        >
          <ChatSidebar
            messages={chatMessages}
            isStreaming={isChatStreaming}
            onSend={onChatSend}
            onReset={onChatReset}
            onApplyYaml={onApplyYaml}
            agentMode={agentMode}
            onModeChange={onModeChange}
            sessionId={sessionId}
            flowPath={flowPath}
            stepContext={stepContext}
            onRemoveStepContext={onRemoveStepContext}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
