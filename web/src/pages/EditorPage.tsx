import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useParams, useNavigate, Link } from "@tanstack/react-router";
import { YamlEditor } from "@/components/editor/YamlEditor";
import { EditorToolbar } from "@/components/editor/EditorToolbar";
import { FlowDagView } from "@/components/dag/FlowDagView";
import { StepDefinitionPanel } from "@/components/editor/StepDefinitionPanel";
import { ChatSidebar } from "@/components/editor/ChatSidebar";
import { FlowFileViewer } from "@/components/editor/FlowFileViewer";
import { FlowFileTree } from "@/components/editor/FlowFileTree";
import { useEditorChat } from "@/hooks/useEditorChat";
import {
  useLocalFlows,
  useLocalFlow,
  useParseYaml,
  useSaveFlow,
  usePatchStep,
  useDeleteStep,
  useFlowFiles,
} from "@/hooks/useEditor";
import { useStepwiseMutations } from "@/hooks/useStepwise";
import { Code, Workflow, FolderTree } from "lucide-react";
import { Sheet, SheetContent } from "@/components/ui/sheet";
import { RunConfigDialog } from "@/components/editor/RunConfigDialog";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { cn } from "@/lib/utils";
import type { FlowDefinition, ParseResult } from "@/lib/types";

const EMPTY_RUNS: never[] = [];

type CenterTab = "flow" | "source";

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
  const isCompact = useMediaQuery("(max-width: 1023px)");

  const params = useParams({ strict: false }) as { flowName?: string };
  const flowName = params.flowName;

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

  // Load flow files for directory flows
  const isDirectoryFlow = selectedFlow?.is_directory ?? false;
  const { data: flowFilesData, refetch: refetchFiles, isFetching: isRefetchingFiles } =
    useFlowFiles(isDirectoryFlow ? selectedFlow?.path : undefined);

  // Editor state
  const [yamlContent, setYamlContent] = useState("");
  const [parsedFlow, setParsedFlow] = useState<FlowDefinition | null>(null);
  const [parseErrors, setParseErrors] = useState<string[]>([]);
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [stepContext, setStepContext] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(false);
  const [viewingFile, setViewingFile] = useState<string | null>(null);
  const [centerTab, setCenterTab] = useState<CenterTab>("flow");
  const [editingPrompt, setEditingPrompt] = useState<{ step: string; field: string } | null>(null);
  const [showFileTree, setShowFileTree] = useState(false);

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
      setViewingFile("FLOW.yaml");
      setEditingPrompt(null);
      setCenterTab("flow");
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
            if (path) {
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

  // Run flow directly from editor
  const mutations = useStepwiseMutations();
  const [showRunConfig, setShowRunConfig] = useState(false);

  const launchJob = useCallback((inputs: Record<string, unknown>) => {
    if (!parsedFlow || !flowName) return;
    mutations.createJob.mutate(
      { objective: flowName, workflow: parsedFlow, inputs, workspace_path: undefined },
      {
        onSuccess: (job) => {
          setShowRunConfig(false);
          navigate({ to: "/jobs/$jobId", params: { jobId: job.id } });
        },
      }
    );
  }, [parsedFlow, flowName, mutations, navigate]);

  const handleRun = useCallback(() => {
    if (!parsedFlow || !flowName) return;
    const vars = parsedFlow.config_vars ?? [];
    const needsInput = vars.some(
      (v) => v.required !== false && (v.default === undefined || v.default === null)
    );
    if (needsInput) {
      setShowRunConfig(true);
    } else {
      // Pre-fill defaults and run immediately
      const inputs: Record<string, unknown> = {};
      for (const v of vars) {
        if (v.default !== undefined && v.default !== null) {
          inputs[v.name] = v.default;
        }
      }
      launchJob(inputs);
    }
  }, [parsedFlow, flowName, launchJob]);

  // Click file in tree → open source tab with that file
  const handleSelectFile = useCallback((filePath: string | null) => {
    setViewingFile(filePath);
    setEditingPrompt(null);
    if (filePath === "FLOW.yaml") {
      setCenterTab("flow");
    } else if (filePath) {
      setCenterTab("source");
    }
  }, []);

  // When selecting a step, also set it as chat context
  const handleSelectStep = useCallback((stepName: string | null) => {
    setSelectedStep(stepName);
    if (stepName) setStepContext(stepName);
  }, []);

  // Cleanup parse timer
  useEffect(() => {
    return () => clearTimeout(parseTimerRef.current);
  }, []);

  const dagWorkflow = parsedFlow ?? { steps: {} };
  const selectedStepDef = selectedStep && parsedFlow?.steps[selectedStep]
    ? parsedFlow.steps[selectedStep]
    : null;

  if (!flowName) {
    navigate({ to: "/flows" });
    return null;
  }

  if (!flowsLoading && flowName && !selectedFlow) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-center space-y-3">
          <p className="text-zinc-400 text-lg">
            Flow <span className="text-foreground font-medium">"{flowName}"</span> not found
          </p>
          <Link to="/flows" className="text-sm text-blue-400 hover:text-blue-300">
            Back to flows
          </Link>
        </div>
      </div>
    );
  }

  return (
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
      />
      <div className="flex-1 flex min-h-0">
        {/* File tree for directory flows — toggled */}
        {isCompact ? (
          <Sheet open={showFileTree && isDirectoryFlow && !!flowFilesData?.files} onOpenChange={setShowFileTree}>
            <SheetContent side="left" showCloseButton={false} className="w-[70vw] sm:max-w-xs p-0 overflow-y-auto">
              {flowFilesData?.files && (
                <FlowFileTree
                  files={flowFilesData.files}
                  selectedFile={viewingFile}
                  onSelectFile={(f) => { handleSelectFile(f); setShowFileTree(false); }}
                  onRefresh={() => refetchFiles()}
                  isRefreshing={isRefetchingFiles}
                />
              )}
            </SheetContent>
          </Sheet>
        ) : (
          showFileTree && isDirectoryFlow && flowFilesData?.files && (
            <div className="w-48 border-r border-border shrink-0 overflow-y-auto">
              <FlowFileTree
                files={flowFilesData.files}
                selectedFile={viewingFile}
                onSelectFile={handleSelectFile}
                onRefresh={() => refetchFiles()}
                isRefreshing={isRefetchingFiles}
              />
            </div>
          )
        )}

        {/* Center panel */}
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
            </button>
            {isDirectoryFlow && (
              <button
                onClick={() => setShowFileTree((s) => !s)}
                className={cn(
                  "ml-2 px-2 py-2 text-xs transition-colors flex items-center gap-1",
                  showFileTree ? "text-foreground" : "text-zinc-600 hover:text-zinc-400"
                )}
                title="Toggle file tree"
              >
                <FolderTree className="w-3 h-3" />
              </button>
            )}
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
            />
          )}
          </div>
        </div>

        {/* Step inspector */}
        {isCompact ? (
          <Sheet
            open={!!selectedStepDef}
            onOpenChange={(open) => {
              if (!open) {
                setSelectedStep(null);
                setEditingPrompt(null);
                setViewingFile("FLOW.yaml");
              }
            }}
          >
            <SheetContent side="right" showCloseButton={false} className="w-[85vw] sm:max-w-sm p-0 overflow-y-auto">
              {selectedStepDef && (
                <StepDefinitionPanel
                  stepDef={selectedStepDef}
                  onClose={() => {
                    setSelectedStep(null);
                    setEditingPrompt(null);
                    setViewingFile("FLOW.yaml");
                  }}
                  onDelete={handleDeleteStep}
                  onViewFile={(path) => {
                    setViewingFile(path);
                    setCenterTab("source");
                    setSelectedStep(null);
                  }}
                  onViewSource={(field) => {
                    setEditingPrompt({ step: selectedStep!, field });
                    setViewingFile(null);
                    setCenterTab("source");
                    setSelectedStep(null);
                  }}
                />
              )}
            </SheetContent>
          </Sheet>
        ) : (
          selectedStepDef && (
            <div className="w-80 border-l border-border shrink-0 flex flex-col">
              <StepDefinitionPanel
                stepDef={selectedStepDef}
                onClose={() => {
                  setSelectedStep(null);
                  setEditingPrompt(null);
                  setViewingFile("FLOW.yaml");
                }}
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
              />
            </div>
          )
        )}

        {/* Chat sidebar */}
        {isCompact ? (
          <Sheet open={chatOpen} onOpenChange={setChatOpen}>
            <SheetContent side="right" showCloseButton={false} className="w-[85vw] sm:max-w-md p-0 overflow-y-auto">
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
          )
        )}
      </div>

      {/* Run config dialog for flows with required inputs */}
      {parsedFlow?.config_vars && parsedFlow.config_vars.length > 0 && (
        <RunConfigDialog
          open={showRunConfig}
          onOpenChange={setShowRunConfig}
          configVars={parsedFlow.config_vars}
          onRun={launchJob}
          isPending={mutations.createJob.isPending}
        />
      )}
    </div>
  );
}
