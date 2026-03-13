import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate } from "@tanstack/react-router";
import { FlowFileList } from "@/components/editor/FlowFileList";
import { YamlEditor } from "@/components/editor/YamlEditor";
import { EditorToolbar } from "@/components/editor/EditorToolbar";
import { FlowDagView } from "@/components/dag/FlowDagView";
import { StepDefinitionPanel } from "@/components/editor/StepDefinitionPanel";
import { AddStepDialog } from "@/components/editor/AddStepDialog";
import { RegistryBrowser } from "@/components/editor/RegistryBrowser";
import { FlowInfoPanel } from "@/components/editor/FlowInfoPanel";
import { ChatMessages } from "@/components/editor/ChatMessages";
import { ChatInput } from "@/components/editor/ChatInput";
import { FlowFileTree } from "@/components/editor/FlowFileTree";
import { FlowFileViewer } from "@/components/editor/FlowFileViewer";
import { useEditorChat } from "@/hooks/useEditorChat";
import {
  useLocalFlows,
  useLocalFlow,
  useCreateFlow,
  useDeleteFlow,
  useParseYaml,
  useSaveFlow,
  usePatchStep,
  useAddStep,
  useDeleteStep,
  useRegistryFlow,
  useInstallFlow,
  useFlowFiles,
} from "@/hooks/useEditor";
import { FileCode, FolderOpen, Globe, Plus, Code, Workflow, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { FlowDefinition, LocalFlow, RegistryFlow, ParseResult } from "@/lib/types";

const EMPTY_RUNS: never[] = [];

type SidebarTab = "local" | "registry";
type CenterTab = "flow" | "source";
type StepPanelTab = "details" | "chat";

/** Dedicated prompt editor with local state to avoid cursor jumps from re-renders. */
function PromptEditor({
  stepName,
  fieldName,
  initialValue,
  onPatch,
  onClose,
}: {
  stepName: string;
  fieldName: string;
  initialValue: string;
  onPatch: (changes: Record<string, unknown>) => void;
  onClose: () => void;
}) {
  const [local, setLocal] = useState(initialValue);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onPatchRef = useRef(onPatch);
  onPatchRef.current = onPatch;

  useEffect(() => () => clearTimeout(timerRef.current), []);

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const v = e.target.value;
    setLocal(v);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => onPatchRef.current({ [fieldName]: v }), 500);
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-zinc-950/50">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-zinc-500">{stepName}</span>
          <span className="text-zinc-600">→</span>
          <span className="text-foreground font-medium">{fieldName}</span>
        </div>
        <button
          onClick={onClose}
          className="text-xs text-zinc-500 hover:text-foreground px-2 py-1 rounded hover:bg-zinc-800"
        >
          Done
        </button>
      </div>
      <textarea
        className="flex-1 w-full p-4 font-mono text-sm bg-transparent text-zinc-300 resize-none outline-none leading-relaxed"
        value={local}
        onChange={handleChange}
        spellCheck={false}
        autoFocus
      />
    </div>
  );
}

export function EditorPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  // Get flow name from URL if present
  const params = useParams({ strict: false }) as { flowName?: string };
  const flowName = params.flowName;

  // Sidebar tab state
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("local");

  // Sub-flow expand/collapse
  const [expandedSteps, setExpandedSteps] = useState<Set<string>>(new Set());
  const toggleExpand = useCallback((stepName: string) => {
    setExpandedSteps((prev) => {
      const next = new Set(prev);
      if (next.has(stepName)) next.delete(stepName);
      else next.add(stepName);
      return next;
    });
  }, []);

  // Create flow
  const createFlowMutation = useCreateFlow();
  const deleteFlowMutation = useDeleteFlow();
  const [showNewFlowInput, setShowNewFlowInput] = useState(false);
  const [newFlowName, setNewFlowName] = useState("");

  // Fetch local flows list
  const { data: flows = [] } = useLocalFlows();

  // Find the selected flow's path from the list
  const selectedFlow = useMemo(
    () => (flowName ? flows.find((f) => f.name === flowName) : undefined),
    [flowName, flows]
  );

  // Load selected flow detail
  const { data: flowDetail, refetch: refetchFlow } = useLocalFlow(selectedFlow?.path);

  // Load flow files for directory flows
  const isDirectoryFlow = selectedFlow?.is_directory ?? false;
  const { data: flowFilesData, refetch: refetchFiles, isFetching: isRefetchingFiles } =
    useFlowFiles(isDirectoryFlow ? selectedFlow?.path : undefined);

  // Editor state
  const [yamlContent, setYamlContent] = useState("");
  const [savedYaml, setSavedYaml] = useState("");
  const [parsedFlow, setParsedFlow] = useState<FlowDefinition | null>(null);
  const [parseErrors, setParseErrors] = useState<string[]>([]);
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [addStepOpen, setAddStepOpen] = useState(false);
  const [viewingFile, setViewingFile] = useState<string | null>(null);
  const [centerTab, setCenterTab] = useState<CenterTab>("flow");
  const [editingPrompt, setEditingPrompt] = useState<{ step: string; field: string } | null>(null);
  const [stepPanelTab, setStepPanelTab] = useState<StepPanelTab>("details");

  // Registry state
  const [selectedRegistryFlow, setSelectedRegistryFlow] = useState<RegistryFlow | null>(null);
  const { data: registryFlowDetail } = useRegistryFlow(selectedRegistryFlow?.slug);
  const installMutation = useInstallFlow();
  const [installedSlugs, setInstalledSlugs] = useState<Set<string>>(new Set());

  // Track dirty state
  const isDirty = yamlContent !== savedYaml && savedYaml !== "";
  const dirtyFlows = useMemo(() => {
    const set = new Set<string>();
    if (isDirty && flowName) set.add(flowName);
    return set;
  }, [isDirty, flowName]);

  // Apply YAML from chat
  const handleApplyChat = useCallback(
    (yaml: string) => {
      setYamlContent(yaml);
      clearTimeout(parseTimerRef.current);
      parseMutation.mutate(yaml, {
        onSuccess: (result) => {
          if (result.flow) {
            setParsedFlow(result.flow);
            setParseErrors([]);
          } else {
            setParseErrors(result.errors);
          }
        },
      });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  );

  const handleFilesChanged = useCallback(async () => {
    refetchFiles();
    queryClient.invalidateQueries({ queryKey: ["localFlows"] });
    const { data } = await refetchFlow();
    if (data) {
      loadedPathRef.current = data.path;
      setYamlContent(data.raw_yaml);
      setSavedYaml(data.raw_yaml);
      setParsedFlow(data.flow);
      setParseErrors([]);
    }
  }, [refetchFiles, queryClient, refetchFlow]);

  // Chat hook — shared across flow and step contexts
  const chat = useEditorChat({
    currentYaml: yamlContent,
    selectedStep,
    flowPath: selectedFlow?.path ?? null,
    onApplyYaml: handleApplyChat,
    onFilesChanged: handleFilesChanged,
  });

  // When flow detail loads, initialize editor
  const loadedPathRef = useRef<string | undefined>(undefined);
  useEffect(() => {
    if (flowDetail && flowDetail.path !== loadedPathRef.current) {
      loadedPathRef.current = flowDetail.path;
      setYamlContent(flowDetail.raw_yaml);
      setSavedYaml(flowDetail.raw_yaml);
      setParsedFlow(flowDetail.flow);
      setParseErrors([]);
      setSelectedStep(null);
      setExpandedSteps(new Set());
      setViewingFile("FLOW.yaml");
      setEditingPrompt(null);
      setCenterTab("flow");
      chat.reset();
    }
  }, [flowDetail]); // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced parse on YAML change
  const parseMutation = useParseYaml();
  const parseTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const handleYamlChange = (value: string) => {
    setYamlContent(value);
    clearTimeout(parseTimerRef.current);
    parseTimerRef.current = setTimeout(() => {
      parseMutation.mutate(value, {
        onSuccess: (result) => {
          if (result.flow) {
            setParsedFlow(result.flow);
            setParseErrors([]);
          } else {
            setParseErrors(result.errors);
          }
        },
      });
    }, 500);
  };

  // Save
  const saveMutation = useSaveFlow();

  const handleSave = useCallback(() => {
    if (!selectedFlow?.path || yamlContent === savedYaml) return;
    saveMutation.mutate(
      { path: selectedFlow.path, yaml: yamlContent },
      {
        onSuccess: (result) => {
          setSavedYaml(result.raw_yaml);
          setParsedFlow(result.flow);
          setParseErrors([]);
        },
      }
    );
  }, [selectedFlow?.path, yamlContent, savedYaml, saveMutation]);

  // Discard changes
  const handleDiscard = useCallback(() => {
    if (savedYaml) {
      setYamlContent(savedYaml);
      parseMutation.mutate(savedYaml, {
        onSuccess: (result) => {
          if (result.flow) {
            setParsedFlow(result.flow);
            setParseErrors([]);
          }
        },
      });
    }
  }, [savedYaml, parseMutation]);

  // Select flow from list
  const handleSelectFlow = useCallback(
    (flow: LocalFlow) => {
      if (isDirty && !confirm("Discard unsaved changes?")) return;
      setSelectedRegistryFlow(null);
      navigate({ to: "/editor/$flowName", params: { flowName: flow.name } });
    },
    [isDirty, navigate]
  );

  // Delete flow
  const handleDeleteFlow = useCallback(
    (flow: LocalFlow) => {
      if (!confirm(`Delete flow "${flow.name}"? This cannot be undone.`)) return;
      deleteFlowMutation.mutate(flow.path, {
        onSuccess: () => {
          if (flowName === flow.name) {
            navigate({ to: "/editor" });
          }
        },
      });
    },
    [deleteFlowMutation, flowName, navigate]
  );

  // Create new flow
  const handleCreateFlow = useCallback(() => {
    const name = newFlowName.trim();
    if (!name) return;
    createFlowMutation.mutate(name, {
      onSuccess: (result) => {
        setShowNewFlowInput(false);
        setNewFlowName("");
        setSidebarTab("local");
        navigate({ to: "/editor/$flowName", params: { flowName: result.name } });
      },
    });
  }, [newFlowName, createFlowMutation, navigate]);

  // Visual editing mutations
  const patchStepMutation = usePatchStep();
  const addStepMutation = useAddStep();
  const deleteStepMutation = useDeleteStep();

  const applyVisualResult = useCallback(
    (result: ParseResult) => {
      if (result.raw_yaml) {
        setYamlContent(result.raw_yaml);
        setSavedYaml(result.raw_yaml);
      }
      if (result.flow) {
        setParsedFlow(result.flow);
        setParseErrors([]);
      } else {
        setParseErrors(result.errors);
      }
    },
    []
  );

  const handlePatchStep = useCallback(
    (changes: Record<string, unknown>) => {
      if (!selectedFlow?.path || !selectedStep) return;
      patchStepMutation.mutate(
        { flowPath: selectedFlow.path, stepName: selectedStep, changes },
        { onSuccess: applyVisualResult }
      );
    },
    [selectedFlow?.path, selectedStep, patchStepMutation, applyVisualResult]
  );

  const handleAddStep = useCallback(
    (name: string, executor: string) => {
      if (!selectedFlow?.path) return;
      addStepMutation.mutate(
        { flowPath: selectedFlow.path, name, executor },
        {
          onSuccess: (result) => {
            applyVisualResult(result);
            setAddStepOpen(false);
            setSelectedStep(name);
          },
        }
      );
    },
    [selectedFlow?.path, addStepMutation, applyVisualResult]
  );

  const handleDeleteStep = useCallback(() => {
    if (!selectedFlow?.path || !selectedStep) return;
    if (!confirm(`Delete step "${selectedStep}"?`)) return;
    deleteStepMutation.mutate(
      { flowPath: selectedFlow.path, stepName: selectedStep },
      {
        onSuccess: (result) => {
          applyVisualResult(result);
          setSelectedStep(null);
        },
      }
    );
  }, [selectedFlow?.path, selectedStep, deleteStepMutation, applyVisualResult]);

  // Registry install
  const handleInstall = useCallback(() => {
    if (!selectedRegistryFlow) return;
    installMutation.mutate(selectedRegistryFlow.slug, {
      onSuccess: (result) => {
        setInstalledSlugs((prev) => new Set([...prev, selectedRegistryFlow.slug]));
        setSidebarTab("local");
        setSelectedRegistryFlow(null);
        navigate({ to: "/editor/$flowName", params: { flowName: result.name } });
      },
    });
  }, [selectedRegistryFlow, installMutation, navigate]);

  const handleSelectRegistryFlow = useCallback((flow: RegistryFlow) => {
    setSelectedRegistryFlow(flow);
  }, []);

  // Click file in tree → open source tab with that file (FLOW.yaml → flow tab)
  const handleSelectFile = useCallback((filePath: string | null) => {
    setViewingFile(filePath);
    setEditingPrompt(null);
    if (filePath === "FLOW.yaml") {
      setCenterTab("flow");
    } else if (filePath) {
      setCenterTab("source");
    }
  }, []);

  // When selecting a step, reset chat tab to details and clear chat
  const handleSelectStep = useCallback((stepName: string | null) => {
    if (stepName !== selectedStep) {
      setStepPanelTab("details");
      chat.reset();
    }
    setSelectedStep(stepName);
  }, [selectedStep, chat]);

  // Send chat message — auto-switch to chat tab when step is selected
  const handleChatSend = useCallback((text: string) => {
    if (selectedStep) {
      setStepPanelTab("chat");
    }
    chat.send(text);
  }, [selectedStep, chat]);

  // Close step chat tab
  const handleCloseStepChat = useCallback(() => {
    setStepPanelTab("details");
    chat.reset();
  }, [chat]);

  // Ctrl+S global handler
  const handleSaveRef = useRef(handleSave);
  handleSaveRef.current = handleSave;

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        handleSaveRef.current();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // Unsaved changes warning
  useEffect(() => {
    if (!isDirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  // Cleanup parse timer
  useEffect(() => {
    return () => clearTimeout(parseTimerRef.current);
  }, []);

  const dagWorkflow = parsedFlow ?? { steps: {} };
  const selectedStepDef = selectedStep && parsedFlow?.steps[selectedStep]
    ? parsedFlow.steps[selectedStep]
    : null;
  const registryDagWorkflow = registryFlowDetail?.flow ?? { steps: {} };
  const isRegistryPreview = sidebarTab === "registry" && selectedRegistryFlow != null;
  const hasStepChat = chat.messages.length > 0 && selectedStep != null;
  const showRightPanel = !!selectedStepDef || chat.messages.length > 0;

  return (
    <div className="h-full flex">
      {/* Left sidebar */}
      <div className="w-64 border-r border-border shrink-0 flex flex-col bg-zinc-950/50">
        {/* Tab bar */}
        <div className="h-10 flex items-center border-b border-border">
          <button
            onClick={() => setSidebarTab("local")}
            className={cn(
              "flex-1 h-full flex items-center justify-center gap-1.5 text-xs font-medium transition-colors",
              sidebarTab === "local"
                ? "text-foreground border-b-2 border-blue-500"
                : "text-zinc-500 hover:text-zinc-300"
            )}
          >
            <FolderOpen className="w-3.5 h-3.5" />
            Local
          </button>
          <button
            onClick={() => setSidebarTab("registry")}
            className={cn(
              "flex-1 h-full flex items-center justify-center gap-1.5 text-xs font-medium transition-colors",
              sidebarTab === "registry"
                ? "text-foreground border-b-2 border-blue-500"
                : "text-zinc-500 hover:text-zinc-300"
            )}
          >
            <Globe className="w-3.5 h-3.5" />
            Registry
          </button>
        </div>

        {/* New Flow button */}
        <div className="px-2 py-2 border-b border-border">
          {showNewFlowInput ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleCreateFlow();
              }}
              className="flex gap-1"
            >
              <input
                autoFocus
                value={newFlowName}
                onChange={(e) => setNewFlowName(e.target.value)}
                placeholder="flow-name"
                className="flex-1 px-2 py-1 text-xs rounded bg-zinc-800 border border-zinc-700 text-foreground placeholder:text-zinc-600 focus:outline-none focus:border-blue-500"
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setShowNewFlowInput(false);
                    setNewFlowName("");
                  }
                }}
              />
              <button
                type="submit"
                disabled={!newFlowName.trim() || createFlowMutation.isPending}
                className="px-2 py-1 text-xs rounded bg-blue-600 text-white hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {createFlowMutation.isPending ? "..." : "Create"}
              </button>
            </form>
          ) : (
            <button
              onClick={() => setShowNewFlowInput(true)}
              className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 text-xs rounded border border-dashed border-zinc-700 text-zinc-400 hover:text-foreground hover:border-zinc-500 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              New Flow
            </button>
          )}
          {createFlowMutation.isError && (
            <p className="mt-1 text-xs text-red-400">
              {(createFlowMutation.error as Error).message?.includes("409")
                ? "Flow already exists"
                : "Failed to create flow"}
            </p>
          )}
        </div>

        {/* Tab content */}
        {sidebarTab === "local" ? (
          <div className="flex-1 flex flex-col min-h-0 overflow-y-auto">
            <FlowFileList
              flows={flows}
              selectedName={flowName}
              onSelect={handleSelectFlow}
              onDelete={handleDeleteFlow}
              dirtyFlows={dirtyFlows}
              flowFiles={isDirectoryFlow ? flowFilesData?.files : undefined}
              selectedFile={viewingFile}
              onSelectFile={handleSelectFile}
              onRefreshFiles={() => refetchFiles()}
              isRefreshingFiles={isRefetchingFiles}
            />
          </div>
        ) : (
          <RegistryBrowser
            selectedSlug={selectedRegistryFlow?.slug}
            onSelect={handleSelectRegistryFlow}
          />
        )}
      </div>

      {/* Center + right panels */}
      <div className="flex-1 flex flex-col min-w-0">
        {isRegistryPreview ? (
          <div className="flex-1 flex min-h-0">
            <div className="flex-1 min-w-0">
              {registryFlowDetail?.flow ? (
                <FlowDagView
                  workflow={registryDagWorkflow}
                  runs={EMPTY_RUNS}
                  jobTree={null}
                  expandedSteps={expandedSteps}
                  onToggleExpand={toggleExpand}
                  selectedStep={null}
                  onSelectStep={() => {}}
                />
              ) : (
                <div className="flex items-center justify-center h-full text-zinc-600 text-sm">
                  Loading preview...
                </div>
              )}
            </div>
            <div className="w-80 border-l border-border shrink-0">
              <FlowInfoPanel
                flow={selectedRegistryFlow}
                onInstall={handleInstall}
                isInstalling={installMutation.isPending}
                isInstalled={installedSlugs.has(selectedRegistryFlow.slug)}
              />
            </div>
          </div>
        ) : flowName && selectedFlow ? (
          <>
            <EditorToolbar
              flowName={flowName}
              isDirty={isDirty}
              isSaving={saveMutation.isPending}
              onSave={handleSave}
              onDiscard={handleDiscard}
              onAddStep={() => setAddStepOpen(true)}
              parseErrors={parseErrors}
            />
            <div className="flex-1 flex min-h-0">
              {/* Center panel: tabs + Flow (DAG) or Source (editor/viewer) */}
              <div className="flex-1 min-w-0 flex flex-col">
                {/* Center tab bar */}
                <div className="flex items-center border-b border-border bg-zinc-950/50 px-4">
                  <button
                    onClick={() => { setCenterTab("flow"); setEditingPrompt(null); }}
                    className={cn(
                      "px-3 py-2 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5",
                      centerTab === "flow"
                        ? "border-blue-500 text-foreground"
                        : "border-transparent text-zinc-500 hover:text-zinc-300"
                    )}
                  >
                    <Workflow className="w-3 h-3" />
                    Flow
                  </button>
                  <button
                    onClick={() => setCenterTab("source")}
                    className={cn(
                      "px-3 py-2 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5",
                      centerTab === "source"
                        ? "border-blue-500 text-foreground"
                        : "border-transparent text-zinc-500 hover:text-zinc-300"
                    )}
                  >
                    <Code className="w-3 h-3" />
                    Source
                    {isDirty && <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />}
                  </button>
                </div>
                <div className="flex-1 min-w-0 min-h-0 relative">
                {editingPrompt && parsedFlow ? (() => {
                  const stepDef = parsedFlow.steps[editingPrompt.step];
                  const promptValue = stepDef
                    ? String((stepDef.executor.config as Record<string, unknown>)[editingPrompt.field] ?? "")
                    : "";
                  return (
                    <PromptEditor
                      key={`${editingPrompt.step}:${editingPrompt.field}`}
                      stepName={editingPrompt.step}
                      fieldName={editingPrompt.field}
                      initialValue={promptValue}
                      onPatch={handlePatchStep}
                      onClose={() => { setEditingPrompt(null); setViewingFile("FLOW.yaml"); }}
                    />
                  );
                })() : centerTab === "flow" ? (
                  <FlowDagView
                    workflow={dagWorkflow}
                    runs={EMPTY_RUNS}
                    jobTree={null}
                    expandedSteps={expandedSteps}
                    onToggleExpand={toggleExpand}
                    selectedStep={selectedStep}
                    onSelectStep={handleSelectStep}
                  />
                ) : viewingFile && viewingFile !== "FLOW.yaml" && selectedFlow?.path ? (
                  <FlowFileViewer
                    flowPath={selectedFlow.path}
                    filePath={viewingFile}
                    onClose={() => {
                      setViewingFile("FLOW.yaml");
                    }}
                  />
                ) : (
                  <YamlEditor
                    value={yamlContent}
                    onChange={handleYamlChange}
                    onSave={handleSave}
                  />
                )}

                {/* Floating chat input — visible only when no sidebar is shown */}
                {!showRightPanel && (
                  <div className="absolute bottom-3 right-3 w-72 z-20">
                    <ChatInput
                      onSend={handleChatSend}
                      placeholder="Modify this flow..."
                      disabled={chat.isStreaming}
                      agentMode={chat.agentMode}
                      onModeChange={chat.setAgentMode}
                      sessionId={chat.sessionId}
                      onReset={chat.reset}
                      flowPath={selectedFlow?.path ?? null}
                      floating
                    />
                  </div>
                )}
                </div>
              </div>

              {/* Right panel — only when step selected or chat active */}
              {showRightPanel && (
                <div className="w-80 border-l border-border shrink-0 flex flex-col">
                  {selectedStepDef ? (
                    <>
                      <StepDefinitionPanel
                        stepDef={selectedStepDef}
                        onClose={() => {
                          setSelectedStep(null);
                          setEditingPrompt(null);
                          setViewingFile("FLOW.yaml");
                        }}
                        onPatch={handlePatchStep}
                        onDelete={handleDeleteStep}
                        onViewFile={(path) => {
                          setViewingFile(path);
                          setCenterTab("source");
                        }}
                        onViewSource={(field) => {
                          setEditingPrompt({ step: selectedStep!, field });
                          setViewingFile(null);
                          setCenterTab("source");
                        }}
                        mode={hasStepChat ? stepPanelTab : undefined}
                        onTabChange={setStepPanelTab}
                        onCloseChat={handleCloseStepChat}
                        chatContent={
                          <ChatMessages
                            messages={chat.messages}
                            isStreaming={chat.isStreaming}
                            onApplyYaml={chat.applyYaml}
                          />
                        }
                      />
                      <ChatInput
                        onSend={handleChatSend}
                        placeholder={`Modify ${selectedStep}...`}
                        disabled={chat.isStreaming}
                        agentMode={chat.agentMode}
                        onModeChange={chat.setAgentMode}
                        sessionId={chat.sessionId}
                        onReset={chat.reset}
                        flowPath={selectedFlow?.path ?? null}
                      />
                    </>
                  ) : (
                    <>
                      <ChatMessages
                        messages={chat.messages}
                        isStreaming={chat.isStreaming}
                        onApplyYaml={chat.applyYaml}
                      />
                      <ChatInput
                        onSend={handleChatSend}
                        placeholder="Modify this flow..."
                        disabled={chat.isStreaming}
                        agentMode={chat.agentMode}
                        onModeChange={chat.setAgentMode}
                        sessionId={chat.sessionId}
                        onReset={chat.reset}
                        flowPath={selectedFlow?.path ?? null}
                      />
                    </>
                  )}
                </div>
              )}
            </div>
            <AddStepDialog
              open={addStepOpen}
              onOpenChange={setAddStepOpen}
              onAdd={handleAddStep}
              isPending={addStepMutation.isPending}
            />
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center text-zinc-600">
              <FileCode className="w-12 h-12 mx-auto mb-3 opacity-50" />
              <p className="text-sm">Select a flow to edit</p>
              <div className="flex items-center justify-center gap-3 mt-3">
                <button
                  onClick={() => setShowNewFlowInput(true)}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded bg-blue-600 text-white hover:bg-blue-500 transition-colors"
                >
                  <Plus className="w-3.5 h-3.5" />
                  New Flow
                </button>
                <button
                  onClick={() => setSidebarTab("registry")}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded border border-zinc-700 text-zinc-400 hover:text-foreground hover:border-zinc-500 transition-colors"
                >
                  <Globe className="w-3.5 h-3.5" />
                  Browse Registry
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
