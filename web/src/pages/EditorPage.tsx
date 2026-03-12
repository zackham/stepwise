import { useState, useCallback, useEffect, useRef, useMemo } from "react";
import { useParams, useNavigate } from "@tanstack/react-router";
import { FlowFileList } from "@/components/editor/FlowFileList";
import { YamlEditor } from "@/components/editor/YamlEditor";
import { EditorToolbar } from "@/components/editor/EditorToolbar";
import { FlowDagView } from "@/components/dag/FlowDagView";
import { StepDefinitionPanel } from "@/components/editor/StepDefinitionPanel";
import { AddStepDialog } from "@/components/editor/AddStepDialog";
import { RegistryBrowser } from "@/components/editor/RegistryBrowser";
import { FlowInfoPanel } from "@/components/editor/FlowInfoPanel";
import { ChatPanel } from "@/components/editor/ChatPanel";
import { FlowFileTree } from "@/components/editor/FlowFileTree";
import { FlowFileViewer } from "@/components/editor/FlowFileViewer";
import {
  useLocalFlows,
  useLocalFlow,
  useCreateFlow,
  useParseYaml,
  useSaveFlow,
  usePatchStep,
  useAddStep,
  useDeleteStep,
  useRegistryFlow,
  useInstallFlow,
  useFlowFiles,
} from "@/hooks/useEditor";
import { FileCode, FolderOpen, Globe, Sparkles, Plus } from "lucide-react";
import { cn } from "@/lib/utils";
import type { FlowDefinition, LocalFlow, RegistryFlow, ParseResult } from "@/lib/types";

const EMPTY_SET = new Set<string>();
const EMPTY_RUNS: never[] = [];

type SidebarTab = "local" | "registry";

export function EditorPage() {
  const navigate = useNavigate();

  // Get flow name from URL if present
  const params = useParams({ strict: false }) as { flowName?: string };
  const flowName = params.flowName;

  // Sidebar tab state
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("local");

  // Create flow
  const createFlowMutation = useCreateFlow();
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
  const { data: flowDetail } = useLocalFlow(selectedFlow?.path);

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
  const [chatOpen, setChatOpen] = useState(false);
  const [viewingFile, setViewingFile] = useState<string | null>(null);

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
      setViewingFile(null);
    }
  }, [flowDetail]);

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
      setSelectedRegistryFlow(null); // clear registry selection
      navigate({ to: "/editor/$flowName", params: { flowName: flow.name } });
    },
    [isDirty, navigate]
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

  // Apply result from visual edit (patch/add/delete) - updates YAML + flow state
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

  // Patch step field from visual panel
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

  // Add step
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

  // Delete step
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
        // Switch to local tab and open the installed flow
        setSidebarTab("local");
        setSelectedRegistryFlow(null);
        navigate({ to: "/editor/$flowName", params: { flowName: result.name } });
      },
    });
  }, [selectedRegistryFlow, installMutation, navigate]);

  // Select registry flow for preview
  const handleSelectRegistryFlow = useCallback((flow: RegistryFlow) => {
    setSelectedRegistryFlow(flow);
  }, []);

  // Apply YAML from chat (marks as unsaved)
  const handleApplyChat = useCallback(
    (yaml: string) => {
      setYamlContent(yaml);
      // Trigger parse
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
    [parseMutation]
  );

  // Ctrl+S global handler (via ref so effect doesn't re-register)
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

  // Build workflow for DAG view
  const dagWorkflow = parsedFlow ?? { steps: {} };

  // Get the selected step definition for the panel
  const selectedStepDef = selectedStep && parsedFlow?.steps[selectedStep]
    ? parsedFlow.steps[selectedStep]
    : null;

  // Registry DAG preview workflow
  const registryDagWorkflow = registryFlowDetail?.flow ?? { steps: {} };

  // Determine if we're in registry preview mode
  const isRegistryPreview = sidebarTab === "registry" && selectedRegistryFlow != null;

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

        {/* New Flow button + inline input */}
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
              dirtyFlows={dirtyFlows}
            />
            {/* Flow file tree for directory flows */}
            {isDirectoryFlow && flowFilesData?.files && (
              <div className="border-t border-border mt-1 pt-1">
                <FlowFileTree
                  files={flowFilesData.files}
                  selectedFile={viewingFile}
                  onSelectFile={setViewingFile}
                  onRefresh={() => refetchFiles()}
                  isRefreshing={isRefetchingFiles}
                />
              </div>
            )}
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
          /* Registry preview mode */
          <div className="flex-1 flex min-h-0">
            <div className="flex-1 min-w-0">
              {registryFlowDetail?.flow ? (
                <FlowDagView
                  workflow={registryDagWorkflow}
                  runs={EMPTY_RUNS}
                  jobTree={null}
                  expandedSteps={EMPTY_SET}
                  onToggleExpand={() => {}}
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
          /* Editor mode */
          <>
            <EditorToolbar
              flowName={flowName}
              isDirty={isDirty}
              isSaving={saveMutation.isPending}
              onSave={handleSave}
              onDiscard={handleDiscard}
              onAddStep={() => setAddStepOpen(true)}
              onToggleChat={() => setChatOpen((v) => !v)}
              chatOpen={chatOpen}
              parseErrors={parseErrors}
            />
            <div className="flex-1 flex min-h-0">
              {/* YAML Editor */}
              <div className="flex-1 min-w-0 border-r border-border">
                <YamlEditor
                  value={yamlContent}
                  onChange={handleYamlChange}
                  onSave={handleSave}
                />
              </div>
              {/* DAG View or File Viewer */}
              <div className="flex-1 min-w-0">
                {viewingFile && selectedFlow?.path ? (
                  <FlowFileViewer
                    flowPath={selectedFlow.path}
                    filePath={viewingFile}
                    onClose={() => setViewingFile(null)}
                  />
                ) : (
                  <FlowDagView
                    workflow={dagWorkflow}
                    runs={EMPTY_RUNS}
                    jobTree={null}
                    expandedSteps={EMPTY_SET}
                    onToggleExpand={() => {}}
                    selectedStep={selectedStep}
                    onSelectStep={setSelectedStep}
                  />
                )}
              </div>
              {/* Right panel: Step editor or AI chat */}
              {selectedStepDef ? (
                <div className="w-80 border-l border-border shrink-0">
                  <StepDefinitionPanel
                    stepDef={selectedStepDef}
                    onClose={() => setSelectedStep(null)}
                    onPatch={handlePatchStep}
                    onDelete={handleDeleteStep}
                  />
                </div>
              ) : chatOpen ? (
                <div className="w-80 border-l border-border shrink-0">
                  <ChatPanel
                    currentYaml={yamlContent}
                    selectedStep={selectedStep}
                    flowPath={selectedFlow?.path ?? null}
                    onApplyYaml={handleApplyChat}
                    onFileApplied={() => refetchFiles()}
                    onClose={() => setChatOpen(false)}
                  />
                </div>
              ) : null}
            </div>
            <AddStepDialog
              open={addStepOpen}
              onOpenChange={setAddStepOpen}
              onAdd={handleAddStep}
              isPending={addStepMutation.isPending}
            />
          </>
        ) : (
          /* Empty state */
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
