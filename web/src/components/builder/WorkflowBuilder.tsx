import { useState, useCallback, useMemo } from "react";
import {
  useExecutors,
  useStepwiseMutations,
  useTemplates,
} from "@/hooks/useStepwise";
import { WorkflowDagView } from "@/components/dag/WorkflowDagView";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  Plus,
  Trash2,
  Save,
  Play,
  AlertTriangle,
  X,
} from "lucide-react";
import type {
  StepDefinition,
  InputBinding,
  ExitRule,
  WorkflowDefinition,
} from "@/lib/types";

interface WorkflowBuilderProps {
  initialWorkflow?: WorkflowDefinition;
  initialName?: string;
  onJobCreated?: (jobId: string) => void;
}

function emptyStep(name: string, executorType: string): StepDefinition {
  return {
    name,
    outputs: [],
    executor: { type: executorType, config: {}, decorators: [] },
    inputs: [],
    sequencing: [],
    exit_rules: [],
    idempotency: "idempotent",
    limits: null,
  };
}

export function WorkflowBuilder({
  initialWorkflow,
  initialName = "",
  onJobCreated,
}: WorkflowBuilderProps) {
  const { data: executorData } = useExecutors();
  const mutations = useStepwiseMutations();
  const executorTypes = executorData?.executors ?? ["script", "human", "mock_llm"];

  const [steps, setSteps] = useState<Record<string, StepDefinition>>(
    initialWorkflow?.steps ?? {}
  );
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [templateName, setTemplateName] = useState(initialName);
  const [templateDesc, setTemplateDesc] = useState("");
  const [objective, setObjective] = useState("");

  // New step form
  const [newStepName, setNewStepName] = useState("");
  const [newStepExecutor, setNewStepExecutor] = useState(executorTypes[0] ?? "script");

  const workflow: WorkflowDefinition = useMemo(
    () => ({ steps }),
    [steps]
  );

  const validationErrors = useMemo(() => {
    if (Object.keys(steps).length === 0) return [];
    // Simple client-side validation
    const errors: string[] = [];
    const stepNames = new Set(Object.keys(steps));

    for (const [name, step] of Object.entries(steps)) {
      for (const binding of step.inputs) {
        if (binding.source_step !== "$job" && !stepNames.has(binding.source_step)) {
          errors.push(
            `Step '${name}': input references unknown step '${binding.source_step}'`
          );
        }
      }
      for (const seq of step.sequencing) {
        if (!stepNames.has(seq)) {
          errors.push(
            `Step '${name}': sequencing references unknown step '${seq}'`
          );
        }
      }
      if (step.outputs.length === 0) {
        errors.push(`Step '${name}': no outputs declared`);
      }
    }
    return errors;
  }, [steps]);

  const addStep = useCallback(() => {
    if (!newStepName.trim() || steps[newStepName.trim()]) return;
    const name = newStepName.trim();
    setSteps((prev) => ({
      ...prev,
      [name]: emptyStep(name, newStepExecutor),
    }));
    setSelectedStep(name);
    setNewStepName("");
  }, [newStepName, newStepExecutor, steps]);

  const removeStep = useCallback(
    (name: string) => {
      setSteps((prev) => {
        const next = { ...prev };
        delete next[name];
        return next;
      });
      if (selectedStep === name) setSelectedStep(null);
    },
    [selectedStep]
  );

  const updateStep = useCallback(
    (name: string, updates: Partial<StepDefinition>) => {
      setSteps((prev) => ({
        ...prev,
        [name]: { ...prev[name], ...updates },
      }));
    },
    []
  );

  const handleSave = () => {
    if (!templateName.trim()) return;
    mutations.saveTemplate.mutate({
      name: templateName.trim(),
      description: templateDesc,
      workflow,
    });
  };

  const handleCreateJob = () => {
    if (!objective.trim()) return;
    mutations.createJob.mutate(
      { objective: objective.trim(), workflow },
      {
        onSuccess: (job) => {
          onJobCreated?.(job.id);
        },
      }
    );
  };

  const editingStep = selectedStep ? steps[selectedStep] : null;

  return (
    <div className="flex h-full">
      {/* Left: Step palette + editor */}
      <div className="w-[380px] border-r border-border flex flex-col">
        <div className="p-3 border-b border-border">
          <h2 className="text-sm font-semibold text-foreground mb-2">
            Workflow Builder
          </h2>

          {/* Add step */}
          <div className="flex gap-2">
            <Input
              value={newStepName}
              onChange={(e) => setNewStepName(e.target.value)}
              placeholder="Step name"
              className="text-sm h-8"
              onKeyDown={(e) => e.key === "Enter" && addStep()}
            />
            <Select
              value={newStepExecutor}
              onValueChange={(v) => { if (v !== null) setNewStepExecutor(v); }}
            >
              <SelectTrigger className="w-[120px] h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {executorTypes.map((t) => (
                  <SelectItem key={t} value={t}>
                    {t}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button size="sm" className="h-8 px-2" onClick={addStep}>
              <Plus className="w-3.5 h-3.5" />
            </Button>
          </div>
        </div>

        <ScrollArea className="flex-1">
          <div className="p-3 space-y-3">
            {/* Step list */}
            {Object.keys(steps).length === 0 ? (
              <div className="text-zinc-500 text-sm text-center py-4">
                Add steps to start building
              </div>
            ) : (
              Object.entries(steps).map(([name, step]) => (
                <Card
                  key={name}
                  className={`cursor-pointer transition-colors ${
                    selectedStep === name ? "ring-1 ring-zinc-500" : ""
                  }`}
                  onClick={() =>
                    setSelectedStep(selectedStep === name ? null : name)
                  }
                >
                  <CardHeader className="p-3 pb-1">
                    <div className="flex items-center justify-between">
                      <CardTitle className="text-sm">{name}</CardTitle>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          removeStep(name);
                        }}
                        className="text-zinc-500 hover:text-red-400"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </CardHeader>
                  <CardContent className="p-3 pt-0">
                    <div className="text-xs text-zinc-500">
                      {step.executor.type} &middot;{" "}
                      {step.outputs.length} output{step.outputs.length !== 1 ? "s" : ""}
                    </div>
                  </CardContent>
                </Card>
              ))
            )}

            {/* Step editor */}
            {editingStep && selectedStep && (
              <>
                <Separator />
                <StepEditor
                  step={editingStep}
                  allStepNames={Object.keys(steps)}
                  allSteps={steps}
                  executorTypes={executorTypes}
                  onUpdate={(updates) => updateStep(selectedStep, updates)}
                />
              </>
            )}

            {/* Validation */}
            {validationErrors.length > 0 && (
              <Alert variant="destructive">
                <AlertTriangle className="h-4 w-4" />
                <AlertDescription>
                  <ul className="list-disc list-inside text-xs space-y-0.5">
                    {validationErrors.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                </AlertDescription>
              </Alert>
            )}

            <Separator />

            {/* Save / Create */}
            <div className="space-y-3">
              <div className="space-y-2">
                <Label className="text-xs">Template Name</Label>
                <Input
                  value={templateName}
                  onChange={(e) => setTemplateName(e.target.value)}
                  className="h-8 text-sm"
                  placeholder="my-workflow"
                />
              </div>
              <div className="space-y-2">
                <Label className="text-xs">Description</Label>
                <Input
                  value={templateDesc}
                  onChange={(e) => setTemplateDesc(e.target.value)}
                  className="h-8 text-sm"
                  placeholder="Optional description"
                />
              </div>
              <Button
                variant="outline"
                size="sm"
                className="w-full"
                onClick={handleSave}
                disabled={
                  !templateName.trim() ||
                  Object.keys(steps).length === 0 ||
                  mutations.saveTemplate.isPending
                }
              >
                <Save className="w-3.5 h-3.5 mr-1.5" />
                Save Template
              </Button>

              <Separator />

              <div className="space-y-2">
                <Label className="text-xs">Objective (to create job)</Label>
                <Input
                  value={objective}
                  onChange={(e) => setObjective(e.target.value)}
                  className="h-8 text-sm"
                  placeholder="What should this job accomplish?"
                />
              </div>
              <Button
                size="sm"
                className="w-full"
                onClick={handleCreateJob}
                disabled={
                  !objective.trim() ||
                  Object.keys(steps).length === 0 ||
                  validationErrors.length > 0 ||
                  mutations.createJob.isPending
                }
              >
                <Play className="w-3.5 h-3.5 mr-1.5" />
                Create Job
              </Button>
            </div>
          </div>
        </ScrollArea>
      </div>

      {/* Right: DAG preview */}
      <div className="flex-1 p-4">
        <WorkflowDagView
          workflow={workflow}
          runs={[]}
          jobTree={null}
          expandedSteps={new Set()}
          onToggleExpand={() => {}}
          selectedStep={selectedStep}
          onSelectStep={setSelectedStep}
        />
      </div>
    </div>
  );
}

// ── Step Editor ───────────────────────────────────────────────────────

function StepEditor({
  step,
  allStepNames,
  allSteps,
  executorTypes,
  onUpdate,
}: {
  step: StepDefinition;
  allStepNames: string[];
  allSteps: Record<string, StepDefinition>;
  executorTypes: string[];
  onUpdate: (updates: Partial<StepDefinition>) => void;
}) {
  const [newOutput, setNewOutput] = useState("");
  const [newSeq, setNewSeq] = useState("");

  const otherSteps = allStepNames.filter((n) => n !== step.name);

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-foreground">
        Edit: {step.name}
      </h3>

      {/* Executor type */}
      <div className="space-y-1">
        <Label className="text-xs">Executor</Label>
        <Select
          value={step.executor.type}
          onValueChange={(type) => {
            if (type !== null) onUpdate({
              executor: { ...step.executor, type },
            }); }
          }
        >
          <SelectTrigger className="h-8 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {executorTypes.map((t) => (
              <SelectItem key={t} value={t}>
                {t}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Executor config */}
      <div className="space-y-1">
        <Label className="text-xs">Executor Config (JSON)</Label>
        <Textarea
          value={JSON.stringify(step.executor.config, null, 2)}
          onChange={(e) => {
            try {
              const config = JSON.parse(e.target.value);
              onUpdate({
                executor: { ...step.executor, config },
              });
            } catch {
              // let them type
            }
          }}
          className="font-mono text-xs min-h-[60px]"
        />
      </div>

      {/* Outputs */}
      <div className="space-y-1">
        <Label className="text-xs">Outputs</Label>
        <div className="space-y-1">
          {step.outputs.map((out) => (
            <div
              key={out}
              className="flex items-center gap-1 text-xs font-mono bg-zinc-900/50 rounded px-2 py-1"
            >
              <span className="flex-1">{out}</span>
              <button
                onClick={() =>
                  onUpdate({
                    outputs: step.outputs.filter((o) => o !== out),
                  })
                }
                className="text-zinc-500 hover:text-red-400"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
        <div className="flex gap-1">
          <Input
            value={newOutput}
            onChange={(e) => setNewOutput(e.target.value)}
            className="h-7 text-xs"
            placeholder="output_name"
            onKeyDown={(e) => {
              if (e.key === "Enter" && newOutput.trim()) {
                onUpdate({
                  outputs: [...step.outputs, newOutput.trim()],
                });
                setNewOutput("");
              }
            }}
          />
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2"
            onClick={() => {
              if (newOutput.trim()) {
                onUpdate({
                  outputs: [...step.outputs, newOutput.trim()],
                });
                setNewOutput("");
              }
            }}
          >
            <Plus className="w-3 h-3" />
          </Button>
        </div>
      </div>

      {/* Input bindings */}
      <div className="space-y-1">
        <Label className="text-xs">Input Bindings</Label>
        {step.inputs.map((binding, i) => (
          <div
            key={i}
            className="flex items-center gap-1 text-xs font-mono bg-zinc-900/50 rounded px-2 py-1"
          >
            <span className="text-blue-400">{binding.local_name}</span>
            <span className="text-zinc-600">&larr;</span>
            <span className="text-zinc-400">
              {binding.source_step}.{binding.source_field}
            </span>
            <button
              onClick={() =>
                onUpdate({
                  inputs: step.inputs.filter((_, idx) => idx !== i),
                })
              }
              className="ml-auto text-zinc-500 hover:text-red-400"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        ))}
        <InputBindingForm
          otherSteps={otherSteps}
          allSteps={allSteps}
          onAdd={(binding) =>
            onUpdate({ inputs: [...step.inputs, binding] })
          }
        />
      </div>

      {/* Sequencing */}
      <div className="space-y-1">
        <Label className="text-xs">Sequencing (wait-for)</Label>
        <div className="flex flex-wrap gap-1">
          {step.sequencing.map((s) => (
            <span
              key={s}
              className="text-xs bg-zinc-800 rounded px-2 py-0.5 flex items-center gap-1"
            >
              {s}
              <button
                onClick={() =>
                  onUpdate({
                    sequencing: step.sequencing.filter((x) => x !== s),
                  })
                }
                className="text-zinc-500 hover:text-red-400"
              >
                <X className="w-2.5 h-2.5" />
              </button>
            </span>
          ))}
        </div>
        <div className="flex gap-1">
          <Select value={newSeq} onValueChange={(v) => { if (v !== null) setNewSeq(v); }}>
            <SelectTrigger className="h-7 text-xs">
              <SelectValue placeholder="Add dependency" />
            </SelectTrigger>
            <SelectContent>
              {otherSteps
                .filter((n) => !step.sequencing.includes(n))
                .map((n) => (
                  <SelectItem key={n} value={n}>
                    {n}
                  </SelectItem>
                ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            className="h-7 px-2"
            onClick={() => {
              if (newSeq && !step.sequencing.includes(newSeq)) {
                onUpdate({
                  sequencing: [...step.sequencing, newSeq],
                });
                setNewSeq("");
              }
            }}
          >
            <Plus className="w-3 h-3" />
          </Button>
        </div>
      </div>

      {/* Idempotency */}
      <div className="space-y-1">
        <Label className="text-xs">Idempotency</Label>
        <Select
          value={step.idempotency}
          onValueChange={(v) => { if (v !== null) onUpdate({ idempotency: v }); }}
        >
          <SelectTrigger className="h-8 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="idempotent">Idempotent</SelectItem>
            <SelectItem value="retriable_with_guard">
              Retriable with guard
            </SelectItem>
            <SelectItem value="non_retriable">Non-retriable</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {/* Exit Rules */}
      <div className="space-y-1">
        <Label className="text-xs">Exit Rules</Label>
        {step.exit_rules.map((rule, i) => (
          <div
            key={i}
            className="text-xs font-mono bg-zinc-900/50 rounded px-2 py-1 flex items-center gap-1"
          >
            <span className="text-amber-400">{rule.name}</span>
            <span className="text-zinc-600">({rule.type})</span>
            <button
              onClick={() =>
                onUpdate({
                  exit_rules: step.exit_rules.filter((_, idx) => idx !== i),
                })
              }
              className="ml-auto text-zinc-500 hover:text-red-400"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        ))}
        <ExitRuleForm
          allStepNames={allStepNames}
          onAdd={(rule) =>
            onUpdate({ exit_rules: [...step.exit_rules, rule] })
          }
        />
      </div>
    </div>
  );
}

// ── Input Binding Form ────────────────────────────────────────────────

function InputBindingForm({
  otherSteps,
  allSteps,
  onAdd,
}: {
  otherSteps: string[];
  allSteps: Record<string, StepDefinition>;
  onAdd: (binding: InputBinding) => void;
}) {
  const [localName, setLocalName] = useState("");
  const [sourceStep, setSourceStep] = useState("");
  const [sourceField, setSourceField] = useState("");

  const sourceOutputs =
    sourceStep === "$job"
      ? ["(manual)"]
      : allSteps[sourceStep]?.outputs ?? [];

  const handleAdd = () => {
    if (!localName.trim() || !sourceStep || !sourceField) return;
    onAdd({
      local_name: localName.trim(),
      source_step: sourceStep,
      source_field: sourceField,
    });
    setLocalName("");
    setSourceStep("");
    setSourceField("");
  };

  return (
    <div className="flex gap-1 flex-wrap">
      <Input
        value={localName}
        onChange={(e) => setLocalName(e.target.value)}
        className="h-7 text-xs w-24"
        placeholder="local_name"
      />
      <Select value={sourceStep} onValueChange={(v) => { if (v !== null) setSourceStep(v); }}>
        <SelectTrigger className="h-7 text-xs w-24">
          <SelectValue placeholder="source" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="$job">$job</SelectItem>
          {otherSteps.map((n) => (
            <SelectItem key={n} value={n}>
              {n}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Input
        value={sourceField}
        onChange={(e) => setSourceField(e.target.value)}
        className="h-7 text-xs w-24"
        placeholder="field"
        list="source-fields"
      />
      {sourceOutputs.length > 0 && (
        <datalist id="source-fields">
          {sourceOutputs.map((o) => (
            <option key={o} value={o} />
          ))}
        </datalist>
      )}
      <Button
        variant="outline"
        size="sm"
        className="h-7 px-2"
        onClick={handleAdd}
      >
        <Plus className="w-3 h-3" />
      </Button>
    </div>
  );
}

// ── Exit Rule Form ────────────────────────────────────────────────────

function ExitRuleForm({
  allStepNames,
  onAdd,
}: {
  allStepNames: string[];
  onAdd: (rule: ExitRule) => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState("always");
  const [action, setAction] = useState("advance");

  const handleAdd = () => {
    if (!name.trim()) return;
    onAdd({
      name: name.trim(),
      type,
      config: { action },
      priority: 0,
    });
    setName("");
  };

  return (
    <div className="flex gap-1 flex-wrap">
      <Input
        value={name}
        onChange={(e) => setName(e.target.value)}
        className="h-7 text-xs w-24"
        placeholder="rule_name"
      />
      <Select value={type} onValueChange={(v) => { if (v !== null) setType(v); }}>
        <SelectTrigger className="h-7 text-xs w-24">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="always">always</SelectItem>
          <SelectItem value="field_match">field_match</SelectItem>
        </SelectContent>
      </Select>
      <Select value={action} onValueChange={(v) => { if (v !== null) setAction(v); }}>
        <SelectTrigger className="h-7 text-xs w-24">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="advance">advance</SelectItem>
          <SelectItem value="loop">loop</SelectItem>
          <SelectItem value="escalate">escalate</SelectItem>
          <SelectItem value="abandon">abandon</SelectItem>
        </SelectContent>
      </Select>
      <Button
        variant="outline"
        size="sm"
        className="h-7 px-2"
        onClick={handleAdd}
      >
        <Plus className="w-3 h-3" />
      </Button>
    </div>
  );
}
