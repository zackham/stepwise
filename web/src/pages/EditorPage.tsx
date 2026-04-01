import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate, useSearch, Link } from "@tanstack/react-router";
import { YamlEditor } from "@/components/editor/YamlEditor";
import { EditorToolbar } from "@/components/editor/EditorToolbar";
import { FlowDagView } from "@/components/dag/FlowDagView";
import { StepDefinitionPanel } from "@/components/editor/StepDefinitionPanel";
import { FlowOverview } from "@/components/editor/FlowOverview";
import { ResizablePanel } from "@/components/ui/ResizablePanel";
import { ChatSidebar } from "@/components/editor/ChatSidebar";
import { FlowFileViewer } from "@/components/editor/FlowFileViewer";
import { FlowFileTree } from "@/components/editor/FlowFileTree";
import { useEditorChat } from "@/hooks/useEditorChat";
import { JobInputForm, extractJobInputs } from "@/components/jobs/JobInputForm";
import {
  useLocalFlows,
  useLocalFlow,
  useParseYaml,
  useSaveFlow,
  usePatchStep,
  useDeleteStep,
  useFlowFiles,
  useAddStep,
  useDeleteFlow,
  usePatchFlowMetadata,
} from "@/hooks/useEditor";
import { useStepwiseMutations } from "@/hooks/useStepwise";
import { Code, Workflow, Plus, PanelLeftClose, X } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { StepPalette } from "@/components/editor/StepPalette";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { MobileFullScreen } from "@/components/layout/MobileFullScreen";
import { useIsMobile } from "@/hooks/useMediaQuery";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/utils";
import { ActionContextProvider } from "@/components/menus/ActionContextProvider";
import type { FlowDefinition, ParseResult } from "@/lib/types";

const EMPTY_RUNS: never[] = [];

const FLOW_TAB_ID = "__flow__";
const FLOW_YAML_TAB_ID = "FLOW.yaml";

interface EditorTab {
  id: string;
  label: string;
  pinned: boolean;
}

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
      <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-zinc-50/50 dark:bg-zinc-950/50">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-zinc-500">{stepName}</span>
          <span className="text-zinc-400 dark:text-zinc-600">→</span>
          <span className="text-foreground font-medium">{fieldName}</span>
        </div>
        <button
          onClick={onClose}
          className="text-xs text-zinc-500 hover:text-foreground px-2 py-1 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800"
        >
          Done
        </button>
      </div>
      <textarea
        className="flex-1 w-full p-4 font-mono text-sm bg-transparent text-zinc-700 dark:text-zinc-300 resize-none outline-none leading-relaxed"
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
  const isCompact = useMediaQuery("(max-width: 1023px)");
  const isMobile = useIsMobile();

  const params = useParams({ strict: false }) as { flowName?: string };
  const flowName = params.flowName;
  const searchParams = useSearch({ strict: false }) as { step?: string };

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

  // Fetch local flows to resolve path from name
  const { data: flows = [], isLoading: flowsLoading } = useLocalFlows();
  const selectedFlow = useMemo(
    () => (flowName ? flows.find((f) => f.name === flowName) : undefined),
    [flowName, flows]
  );

  // Load selected flow detail
  const { data: flowDetail, refetch: refetchFlow } = useLocalFlow(selectedFlow?.path);

  // Registry flows are read-only in the editor
  const isReadOnly = selectedFlow?.source === "registry";

  // Load flow files for directory flows
  const isDirectoryFlow = selectedFlow?.is_directory ?? false;
  const { data: flowFilesData, refetch: refetchFiles, isFetching: isRefetchingFiles } =
    useFlowFiles(isDirectoryFlow ? selectedFlow?.path : undefined);

  // Editor state
  const [yamlContent, setYamlContent] = useState("");
  const [parsedFlow, setParsedFlow] = useState<FlowDefinition | null>(null);
  const [parseErrors, setParseErrors] = useState<string[]>([]);
  const [leftPanelCollapsed, setLeftPanelCollapsed] = useState(false);
  const [rightPanelCollapsed, setRightPanelCollapsed] = useState(false);
  const selectedStep = searchParams.step ?? null;
  const setSelectedStep = useCallback((step: string | null) => {
    navigate({
      search: (prev: Record<string, unknown>) => ({
        ...prev,
        step: step || undefined,
      }),
      replace: false,
    });
  }, [navigate]);
  const [stepContext, setStepContext] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [openTabs, setOpenTabs] = useState<EditorTab[]>([
    { id: FLOW_TAB_ID, label: "Flow", pinned: true },
    { id: FLOW_YAML_TAB_ID, label: "FLOW.yaml", pinned: true },
  ]);
  const [activeTabId, setActiveTabId] = useState<string>(FLOW_TAB_ID);
  const [previewTabId, setPreviewTabId] = useState<string | null>(null);
  const [editingPrompt, setEditingPrompt] = useState<{ step: string; field: string } | null>(null);

  // Derive viewingFile from activeTabId for backward compat
  const viewingFile = activeTabId === FLOW_TAB_ID ? "FLOW.yaml" : activeTabId;

  // Apply YAML from chat (parse + save immediately)
  const saveMutation = useSaveFlow();
  const saveMutationRef = useRef(saveMutation);
  saveMutationRef.current = saveMutation;

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
      setParsedFlow(data.flow);
      setParseErrors([]);
    }
  }, [refetchFiles, queryClient, refetchFlow]);

  // Chat hook — flow-scoped, step context passed separately
  const chat = useEditorChat({
    currentYaml: yamlContent,
    selectedStep: stepContext,
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
      setParsedFlow(flowDetail.flow);
      setParseErrors([]);
      setSelectedStep(null);
      setStepContext(null);
      setExpandedSteps(new Set());
      setOpenTabs([
        { id: FLOW_TAB_ID, label: "Flow", pinned: true },
        { id: FLOW_YAML_TAB_ID, label: "FLOW.yaml", pinned: true },
      ]);
      setActiveTabId(FLOW_TAB_ID);
      setPreviewTabId(null);
      setEditingPrompt(null);
      chat.reset();
    }
  }, [flowDetail]); // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced parse + autosave on YAML change
  const parseMutation = useParseYaml();
  const parseTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const selectedFlowRef = useRef(selectedFlow);
  selectedFlowRef.current = selectedFlow;

  const handleYamlChange = (value: string) => {
    setYamlContent(value);
    clearTimeout(parseTimerRef.current);
    parseTimerRef.current = setTimeout(() => {
      parseMutation.mutate(value, {
        onSuccess: (result) => {
          if (result.flow) {
            setParsedFlow(result.flow);
            setParseErrors([]);
            const path = selectedFlowRef.current?.path;
            const source = selectedFlowRef.current?.source;
            if (path && source !== "registry") {
              saveMutationRef.current.mutate({ path, yaml: value });
            }
          } else {
            setParseErrors(result.errors);
          }
        },
      });
    }, 500);
  };

  // Visual editing mutations
  const patchStepMutation = usePatchStep();
  const deleteStepMutation = useDeleteStep();
  const addStepMutation = useAddStep();
  const [showStepPalette, setShowStepPalette] = useState(false);

  const applyVisualResult = useCallback(
    (result: ParseResult) => {
      if (result.raw_yaml) {
        setYamlContent(result.raw_yaml);
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

  const handleDeleteStep = useCallback(() => {
    if (!selectedFlow?.path || !selectedStep) return;
    if (!confirm(`Delete step "${selectedStep}"?`)) return;
    deleteStepMutation.mutate(
      { flowPath: selectedFlow.path, stepName: selectedStep },
      {
        onSuccess: (result) => {
          applyVisualResult(result);
          setSelectedStep(null);
          if (stepContext === selectedStep) setStepContext(null);
        },
      }
    );
  }, [selectedFlow?.path, selectedStep, stepContext, deleteStepMutation, applyVisualResult]);

  // When selecting a step, also set it as chat context
  const handleSelectStep = useCallback((stepName: string | null) => {
    setSelectedStep(stepName);
    if (stepName) {
      setStepContext(stepName);
      setRightPanelCollapsed(false);
    }
  }, [setSelectedStep]);

  const handleAddStep = useCallback(
    (name: string, executor: string) => {
      if (!selectedFlow?.path) return;
      clearTimeout(parseTimerRef.current);
      addStepMutation.mutate(
        { flowPath: selectedFlow.path, name, executor },
        {
          onSuccess: (result) => {
            applyVisualResult(result);
            handleSelectStep(name);
            setShowStepPalette(false);
          },
        }
      );
    },
    [selectedFlow?.path, addStepMutation, applyVisualResult, handleSelectStep]
  );

  // Flow metadata + actions
  const mutations = useStepwiseMutations();
  const deleteFlowMutation = useDeleteFlow();
  const patchMetadataMutation = usePatchFlowMetadata();

  const handleDeleteFlow = useCallback(() => {
    if (!selectedFlow) return;
    deleteFlowMutation.mutate(selectedFlow.path, {
      onSuccess: () => navigate({ to: "/flows" }),
    });
  }, [selectedFlow, deleteFlowMutation, navigate]);

  const handlePatchMetadata = useCallback(
    (metadata: Partial<import("@/lib/types").FlowMetadata>) => {
      if (!selectedFlow) return;
      patchMetadataMutation.mutate({ path: selectedFlow.path, metadata });
    },
    [selectedFlow, patchMetadataMutation],
  );
  const [showRunConfig, setShowRunConfig] = useState(false);
  const [runJobName, setRunJobName] = useState("");
  const [runInputValues, setRunInputValues] = useState<Record<string, string>>({});
  const [runWorkspacePath, setRunWorkspacePath] = useState("");
  const jobInputFields = useMemo(
    () => (parsedFlow ? extractJobInputs(parsedFlow) : []),
    [parsedFlow]
  );
  const hasWorkingDirInput = jobInputFields.includes("working_dir");
  const runFormFields = useMemo(
    () => jobInputFields.filter((field) => field !== "working_dir"),
    [jobInputFields]
  );

  const launchJob = useCallback(
    (
      inputs: Record<string, unknown>,
      options?: { workspacePath?: string; name?: string }
    ) => {
      if (!parsedFlow || !flowName) return;
      mutations.createJob.mutate(
        {
          objective: flowName,
          workflow: parsedFlow,
          inputs,
          workspace_path: options?.workspacePath,
          name: options?.name?.trim() || undefined,
        },
        {
          onSuccess: (job) => {
            setShowRunConfig(false);
            navigate({ to: "/jobs/$jobId", params: { jobId: job.id } });
          },
        }
      );
    },
    [parsedFlow, flowName, mutations, navigate]
  );

  const handleRun = useCallback(() => {
    if (!parsedFlow || !flowName) return;
    if (jobInputFields.length === 0) {
      launchJob({});
      return;
    }

    const initialValues: Record<string, string> = {};
    for (const field of runFormFields) {
      initialValues[field] = "";
    }

    setRunInputValues(initialValues);
    setRunWorkspacePath("");
    setRunJobName(flowName);
    setShowRunConfig(true);
  }, [parsedFlow, flowName, jobInputFields.length, launchJob, runFormFields]);

  const handleRunInputChange = useCallback((field: string, value: string) => {
    setRunInputValues((prev) => ({ ...prev, [field]: value }));
  }, []);

  const handleRunDialogChange = useCallback(
    (open: boolean) => {
      if (!open && !mutations.createJob.isPending) {
        setRunJobName(flowName ?? "");
        setRunInputValues({});
        setRunWorkspacePath("");
      }
      setShowRunConfig(open);
    },
    [flowName, mutations.createJob.isPending]
  );

  const handleRunSubmit = useCallback(() => {
    const inputs: Record<string, unknown> = {};
    for (const field of runFormFields) {
      const value = runInputValues[field]?.trim();
      if (value) {
        inputs[field] = value;
      }
    }

    const workspacePath = runWorkspacePath.trim();
    if (hasWorkingDirInput && workspacePath) {
      inputs.working_dir = workspacePath;
    }

    launchJob(inputs, {
      workspacePath: hasWorkingDirInput && workspacePath ? workspacePath : undefined,
      name: runJobName,
    });
  }, [
    hasWorkingDirInput,
    launchJob,
    runFormFields,
    runInputValues,
    runJobName,
    runWorkspacePath,
  ]);

  // Click file in tree → open as preview tab (or activate if already open)
  const handleSelectFile = useCallback((filePath: string | null) => {
    setEditingPrompt(null);
    if (!filePath) return;
    if (filePath === "FLOW.yaml") {
      setActiveTabId(FLOW_YAML_TAB_ID);
      return;
    }
    setOpenTabs((prev) => {
      const existing = prev.find((t) => t.id === filePath);
      if (existing) {
        // Already open — just activate it, don't touch preview state
        setActiveTabId(filePath);
        return prev;
      }
      // Replace existing preview tab or add new preview tab
      const newTab: EditorTab = { id: filePath, label: filePath.split("/").pop() ?? filePath, pinned: false };
      const withoutPreview = previewTabId ? prev.filter((t) => t.id !== previewTabId) : prev;
      setPreviewTabId(filePath);
      setActiveTabId(filePath);
      return [...withoutPreview, newTab];
    });
  }, [previewTabId]);

  // Close a tab — Flow tab cannot be closed
  const handleCloseTab = useCallback((tabId: string) => {
    if (tabId === FLOW_TAB_ID || tabId === FLOW_YAML_TAB_ID) return;
    setOpenTabs((prev) => {
      const idx = prev.findIndex((t) => t.id === tabId);
      if (idx === -1) return prev;
      const next = prev.filter((t) => t.id !== tabId);
      // If closing the active tab, switch to the previous tab (or next, or Flow)
      if (tabId === activeTabId) {
        const newActive = next[Math.min(idx, next.length - 1)] ?? next[0];
        setActiveTabId(newActive?.id ?? FLOW_TAB_ID);
      }
      return next;
    });
    if (tabId === previewTabId) setPreviewTabId(null);
  }, [activeTabId, previewTabId]);

  // Double-click a tab to pin it
  const handlePinTab = useCallback((tabId: string) => {
    setOpenTabs((prev) => prev.map((t) => t.id === tabId ? { ...t, pinned: true } : t));
    if (tabId === previewTabId) setPreviewTabId(null);
  }, [previewTabId]);

  // Cleanup parse timer
  useEffect(() => {
    return () => clearTimeout(parseTimerRef.current);
  }, []);

  const dagWorkflow = parsedFlow ?? { steps: {} };
  const selectedStepDef = useMemo(() => {
    if (!selectedStep || !parsedFlow) return null;
    // Check top-level steps first
    if (parsedFlow.steps[selectedStep]) return parsedFlow.steps[selectedStep];
    // Search sub_flow steps
    for (const step of Object.values(parsedFlow.steps)) {
      if (step.sub_flow?.steps[selectedStep]) return step.sub_flow.steps[selectedStep];
    }
    return null;
  }, [selectedStep, parsedFlow]);

  if (!flowName) {
    navigate({ to: "/flows" });
    return null;
  }

  if (!flowsLoading && flowName && !selectedFlow) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center space-y-3">
          <p className="text-zinc-500 dark:text-zinc-400 text-lg">
            Flow <span className="text-foreground font-medium">"{flowName}"</span> not found
          </p>
          <Link to="/flows" className="text-sm text-blue-600 dark:text-blue-400 hover:text-blue-500 dark:hover:text-blue-300">
            Back to flows
          </Link>
        </div>
      </div>
    );
  }

  return (
    <ActionContextProvider
      sideEffects={{ onRunFlow: handleRun }}
      extraMutations={{ deleteFlow: deleteFlowMutation }}
    >
    <div className="h-full flex flex-col">
      <EditorToolbar
        flowName={flowName}
        onRun={parsedFlow ? handleRun : undefined}
        isRunning={mutations.createJob.isPending}
        parseErrors={parseErrors}
        chatOpen={chatOpen}
        onToggleChat={() => setChatOpen((o) => !o)}
        isChatStreaming={chat.isStreaming}
        agentMode={chat.agentMode}
        leftPanelVisible={!leftPanelCollapsed}
        onToggleLeftPanel={() => setLeftPanelCollapsed((c) => !c)}
        rightPanelVisible={!rightPanelCollapsed}
        onToggleRightPanel={() => setRightPanelCollapsed((c) => !c)}
      />
      <div className="flex-1 flex min-h-0">
        {/* Left sidebar: Flow Details + Files */}
        {!isMobile && !isCompact && selectedFlow && !leftPanelCollapsed && (
          <ResizablePanel storageKey="stepwise-editor-left-panel-width" side="left" onCollapse={() => setLeftPanelCollapsed(true)}>
            <Tabs defaultValue="details" className="flex flex-col h-full gap-0">
              <div className="flex items-center justify-between border-b border-border bg-zinc-50/50 dark:bg-zinc-950/50 shrink-0">
                <TabsList variant="line" className="px-1">
                  <TabsTrigger value="details" className="text-xs gap-1 px-2.5">
                    Details
                  </TabsTrigger>
                  {isDirectoryFlow && flowFilesData?.files && (
                    <TabsTrigger value="files" className="text-xs gap-1 px-2.5">
                      Files
                    </TabsTrigger>
                  )}
                </TabsList>
                <button
                  onClick={() => setLeftPanelCollapsed(true)}
                  className="text-zinc-500 dark:text-zinc-600 hover:text-zinc-700 dark:hover:text-zinc-300 p-0.5 mr-2"
                  title="Collapse sidebar"
                >
                  <PanelLeftClose className="w-3.5 h-3.5" />
                </button>
              </div>
              <TabsContent value="details" className="flex-1 min-h-0 overflow-y-auto mt-0">
                <FlowOverview
                  flow={selectedFlow}
                  detail={flowDetail}
                  onRun={handleRun}
                  onDelete={isReadOnly ? undefined : handleDeleteFlow}
                  onPatchMetadata={handlePatchMetadata}
                  readOnly={isReadOnly}
                />
              </TabsContent>
              {isDirectoryFlow && flowFilesData?.files && (
                <TabsContent value="files" className="flex-1 min-h-0 overflow-y-auto mt-0">
                  <FlowFileTree
                    files={flowFilesData.files}
                    selectedFile={viewingFile}
                    onSelectFile={handleSelectFile}
                    onRefresh={() => refetchFiles()}
                    isRefreshing={isRefetchingFiles}
                  />
                </TabsContent>
              )}
            </Tabs>
          </ResizablePanel>
        )}

        {/* Center panel */}
        <div className="flex-1 min-w-0 flex flex-col">
          {/* VS Code-style tab bar */}
          <div className="flex items-center border-b border-border bg-zinc-50/50 dark:bg-zinc-950/50 overflow-x-auto">
            {openTabs.map((tab) => {
              const isActive = tab.id === activeTabId;
              const isPreview = tab.id === previewTabId;
              const isFlowTab = tab.id === FLOW_TAB_ID;
              return (
                <button
                  key={tab.id}
                  onClick={() => { setActiveTabId(tab.id); if (isFlowTab) setEditingPrompt(null); }}
                  onDoubleClick={() => { if (!tab.pinned) handlePinTab(tab.id); }}
                  className={cn(
                    "group relative px-3 py-2 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5 shrink-0",
                    isActive
                      ? "border-blue-500 text-foreground"
                      : "border-transparent text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
                  )}
                >
                  {isFlowTab ? <Workflow className="w-3 h-3" /> : <Code className="w-3 h-3" />}
                  <span className={cn(isPreview && "italic")}>{tab.label}</span>
                  {!isFlowTab && tab.id !== FLOW_YAML_TAB_ID && (
                    <span
                      onClick={(e) => { e.stopPropagation(); handleCloseTab(tab.id); }}
                      className="ml-1 p-0.5 rounded opacity-0 group-hover:opacity-100 hover:bg-zinc-200 dark:hover:bg-zinc-700 transition-opacity"
                    >
                      <X className="w-3 h-3" />
                    </span>
                  )}
                </button>
              );
            })}
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
                onClose={() => { setEditingPrompt(null); setActiveTabId(FLOW_YAML_TAB_ID); }}
              />
            );
          })() : activeTabId === FLOW_TAB_ID ? (
            <FlowDagView
              workflow={dagWorkflow}
              runs={EMPTY_RUNS}
              jobTree={null}
              expandedSteps={expandedSteps}
              onToggleExpand={toggleExpand}
              selectedStep={selectedStep}
              onSelectStep={handleSelectStep}
            />
          ) : activeTabId !== FLOW_YAML_TAB_ID && selectedFlow?.path ? (
            <FlowFileViewer
              flowPath={selectedFlow.path}
              filePath={activeTabId}
              onClose={() => {
                handleCloseTab(activeTabId);
              }}
            />
          ) : (
            <YamlEditor
              value={yamlContent}
              onChange={handleYamlChange}
            />
          )}
          {activeTabId === FLOW_TAB_ID && !editingPrompt && !isReadOnly && (
            <button
              onClick={() => setShowStepPalette(true)}
              className="absolute bottom-14 right-3 z-20 flex items-center gap-1.5 bg-white/80 dark:bg-zinc-900/80 border border-zinc-300/50 dark:border-zinc-700/50 rounded-md px-2.5 py-1.5 text-zinc-400 hover:text-foreground text-xs shadow-sm hover:bg-white dark:hover:bg-zinc-800 transition-colors min-h-[44px] md:min-h-0"
              title="Add step"
            >
              <Plus className="w-3.5 h-3.5" />
              Add step
            </button>
          )}
          </div>
        </div>

        {/* Step inspector */}
        {isMobile ? (
          <MobileFullScreen
            open={!!selectedStepDef}
            onClose={() => {
              setSelectedStep(null);
              setEditingPrompt(null);
            }}
            title={selectedStepDef?.name ?? "Step"}
          >
            {selectedStepDef && (
              <StepDefinitionPanel
                stepDef={selectedStepDef}
                onSelectStep={handleSelectStep}
                onClose={() => {
                  setSelectedStep(null);
                  setEditingPrompt(null);
                }}
                onDelete={handleDeleteStep}
                onViewFile={(path) => {
                  handleSelectFile(path);
                  setSelectedStep(null);
                }}
                onViewSource={(field) => {
                  setEditingPrompt({ step: selectedStep!, field });
                  setActiveTabId(FLOW_YAML_TAB_ID);
                  setSelectedStep(null);
                }}
              />
            )}
          </MobileFullScreen>
        ) : isCompact ? (
          <Sheet
            open={!!selectedStepDef}
            onOpenChange={(open) => {
              if (!open) {
                setSelectedStep(null);
                setEditingPrompt(null);
              }
            }}
          >
            <SheetContent side="right" showCloseButton={false} className="w-[90vw] sm:max-w-sm p-0 overflow-y-auto">
              {selectedStepDef && (
                <StepDefinitionPanel
                  stepDef={selectedStepDef}
                  onClose={() => {
                    setSelectedStep(null);
                    setEditingPrompt(null);
                  }}
                  onDelete={handleDeleteStep}
                  onViewFile={(path) => {
                    handleSelectFile(path);
                    setSelectedStep(null);
                  }}
                  onViewSource={(field) => {
                    setEditingPrompt({ step: selectedStep!, field });
                    setActiveTabId(FLOW_YAML_TAB_ID);
                    setSelectedStep(null);
                  }}
                />
              )}
            </SheetContent>
          </Sheet>
        ) : selectedStepDef && !rightPanelCollapsed ? (
          <ResizablePanel storageKey="stepwise-editor-right-panel-width" onCollapse={() => setRightPanelCollapsed(true)}>
              <StepDefinitionPanel
                stepDef={selectedStepDef}
                onSelectStep={handleSelectStep}
                onClose={() => {
                  setSelectedStep(null);
                  setEditingPrompt(null);
                }}
                onDelete={handleDeleteStep}
                onViewFile={(path) => {
                  handleSelectFile(path);
                }}
                onViewSource={(field) => {
                  setEditingPrompt({ step: selectedStep!, field });
                  setActiveTabId(FLOW_YAML_TAB_ID);
                }}
              />
          </ResizablePanel>
        ) : null}

        {/* Chat sidebar */}
        {isMobile ? (
          <MobileFullScreen
            open={chatOpen}
            onClose={() => setChatOpen(false)}
            title="Chat"
          >
            <ChatSidebar
              messages={chat.messages}
              isStreaming={chat.isStreaming}
              onSend={chat.send}
              onReset={chat.reset}
              onApplyYaml={chat.applyYaml}
              agentMode={chat.agentMode}
              onModeChange={chat.setAgentMode}
              sessionId={chat.sessionId}
              flowPath={selectedFlow?.path ?? null}
              stepContext={stepContext}
              onRemoveStepContext={() => setStepContext(null)}
            />
          </MobileFullScreen>
        ) : isCompact ? (
          <Sheet open={chatOpen} onOpenChange={setChatOpen}>
            <SheetContent side="right" showCloseButton={false} className="w-[90vw] sm:max-w-md p-0 overflow-y-auto">
              <ChatSidebar
                messages={chat.messages}
                isStreaming={chat.isStreaming}
                onSend={chat.send}
                onReset={chat.reset}
                onApplyYaml={chat.applyYaml}
                agentMode={chat.agentMode}
                onModeChange={chat.setAgentMode}
                sessionId={chat.sessionId}
                flowPath={selectedFlow?.path ?? null}
                stepContext={stepContext}
                onRemoveStepContext={() => setStepContext(null)}
              />
            </SheetContent>
          </Sheet>
        ) : (
          chatOpen && (
            <ResizablePanel storageKey="stepwise-editor-chat-width">
              <ChatSidebar
                messages={chat.messages}
                isStreaming={chat.isStreaming}
                onSend={chat.send}
                onReset={chat.reset}
                onApplyYaml={chat.applyYaml}
                agentMode={chat.agentMode}
                onModeChange={chat.setAgentMode}
                sessionId={chat.sessionId}
                flowPath={selectedFlow?.path ?? null}
                stepContext={stepContext}
                onRemoveStepContext={() => setStepContext(null)}
              />
            </ResizablePanel>
          )
        )}
      </div>

      <Dialog open={showRunConfig} onOpenChange={handleRunDialogChange}>
        <DialogContent className="sm:max-w-md max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Run flow</DialogTitle>
            <DialogDescription>
              Provide job inputs before starting this flow.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-2">
              <Label>Name</Label>
              <Input
                value={runJobName}
                onChange={(e) => setRunJobName(e.target.value)}
                placeholder="Human-friendly job name"
                className="text-xs"
              />
            </div>

            {runFormFields.length > 0 && (
              <div className="space-y-3">
                <Label className="text-zinc-500 dark:text-zinc-400">Inputs</Label>
                <JobInputForm
                  fields={runFormFields}
                  values={runInputValues}
                  onChange={handleRunInputChange}
                />
              </div>
            )}

            {hasWorkingDirInput && (
              <div className="space-y-2">
                <Label>Workspace Path</Label>
                <Input
                  value={runWorkspacePath}
                  onChange={(e) => setRunWorkspacePath(e.target.value)}
                  placeholder="/home/zack/work/stepwise"
                  className="font-mono text-xs"
                />
              </div>
            )}
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => handleRunDialogChange(false)}
              disabled={mutations.createJob.isPending}
            >
              Cancel
            </Button>
            <Button onClick={handleRunSubmit} disabled={mutations.createJob.isPending}>
              {mutations.createJob.isPending ? "Starting..." : "Run"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <StepPalette
        open={showStepPalette}
        onOpenChange={setShowStepPalette}
        existingStepNames={Object.keys(parsedFlow?.steps ?? {})}
        onAdd={handleAddStep}
        isPending={addStepMutation.isPending}
      />
    </div>
    </ActionContextProvider>
  );
}
