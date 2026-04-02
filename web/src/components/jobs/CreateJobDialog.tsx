import { useState, useMemo, useEffect } from "react";
import { useStepwiseMutations, useTemplates } from "@/hooks/useStepwise";
import { useLocalFlows } from "@/hooks/useEditor";
import { fetchLocalFlow } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { JobInputForm, extractJobInputs } from "@/components/jobs/JobInputForm";
import { Plus } from "lucide-react";
import type { FlowDefinition } from "@/lib/types";

export interface CreateJobPrefill {
  workflow: FlowDefinition;
  inputs: Record<string, unknown>;
  name?: string;
}

interface CreateJobDialogProps {
  onCreated?: (jobId: string) => void;
  prefill?: CreateJobPrefill;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

export function CreateJobDialog({ onCreated, prefill, open: controlledOpen, onOpenChange }: CreateJobDialogProps) {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const setOpen = onOpenChange ?? setInternalOpen;
  const [selectedItem, setSelectedItem] = useState<string>("");
  const [workflow, setWorkflow] = useState<FlowDefinition | null>(null);
  const [workflowJson, setWorkflowJson] = useState("");
  const [inputValues, setInputValues] = useState<Record<string, string>>({});
  const [workspacePath, setWorkspacePath] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [mode, setMode] = useState<"flow" | "json">("flow");
  const [jobName, setJobName] = useState("");

  const { data: templates = [] } = useTemplates();
  const { data: localFlows = [] } = useLocalFlows();
  const mutations = useStepwiseMutations();

  // Apply prefill when dialog opens with prefill data
  useEffect(() => {
    if (open && prefill) {
      setWorkflow(prefill.workflow);
      setWorkflowJson(JSON.stringify(prefill.workflow, null, 2));
      const mapped: Record<string, string> = {};
      for (const [k, v] of Object.entries(prefill.inputs)) {
        mapped[k] = String(v ?? "");
      }
      setInputValues(mapped);
      setJobName(prefill.name ?? "");
      setSelectedItem("prefill");
      setMode("flow");
    }
  }, [open, prefill]);

  // Derive required inputs from the selected flow
  const jobInputFields = useMemo(
    () => (workflow ? extractJobInputs(workflow) : []),
    [workflow]
  );

  const handleSelect = async (value: string) => {
    setSelectedItem(value);
    setJsonError(null);
    setInputValues({});

    if (value.startsWith("template:")) {
      const name = value.slice("template:".length);
      const tmpl = templates.find((t) => t.name === name);
      if (tmpl) {
        setWorkflow(tmpl.workflow);
        setWorkflowJson(JSON.stringify(tmpl.workflow, null, 2));
      }
    } else if (value.startsWith("flow:")) {
      const path = value.slice("flow:".length);
      setLoading(true);
      try {
        const detail = await fetchLocalFlow(path);
        setWorkflow(detail.flow);
        setWorkflowJson(JSON.stringify(detail.flow, null, 2));
      } catch (e) {
        setJsonError(e instanceof Error ? e.message : "Failed to load flow");
      } finally {
        setLoading(false);
      }
    }
  };

  const handleInputChange = (field: string, value: string) => {
    setInputValues((prev) => ({ ...prev, [field]: value }));
  };

  const handleSubmit = () => {
    try {
      let wf: FlowDefinition;
      let inputs: Record<string, unknown>;

      if (mode === "json") {
        wf = JSON.parse(workflowJson);
        inputs = {};
      } else {
        if (!workflow) return;
        wf = workflow;
        // Build inputs from form fields
        inputs = {};
        for (const field of jobInputFields) {
          const val = inputValues[field]?.trim();
          if (val) inputs[field] = val;
        }
      }

      setJsonError(null);

      // Use flow name as objective (or first input value, or generic)
      const flowName = selectedItem.replace(/^(flow:|template:)/, "").split("/").pop() ?? "job";
      const objective = inputValues[jobInputFields[0]] || flowName;

      mutations.createJob.mutate(
        { objective, workflow: wf, inputs, workspace_path: workspacePath || undefined, name: jobName.trim() || undefined },
        {
          onSuccess: (job) => {
            setOpen(false);
            setSelectedItem("");
            setWorkflow(null);
            setWorkflowJson("");
            setInputValues({});
            setWorkspacePath("");
            setJobName("");
            onCreated?.(job.id);
          },
        }
      );
    } catch (e) {
      setJsonError(e instanceof Error ? e.message : "Invalid JSON");
    }
  };

  const canSubmit =
    mode === "json"
      ? !!workflowJson && !mutations.createJob.isPending && !loading
      : !!workflow && !mutations.createJob.isPending && !loading;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {controlledOpen === undefined && (
        <DialogTrigger
          className="inline-flex h-8 cursor-pointer items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border border-border bg-background px-2.5 text-sm font-medium transition-all hover:bg-muted hover:text-foreground dark:border-input dark:bg-input/30 dark:hover:bg-input/50"
        >
          <Plus className="w-3.5 h-3.5" />
          New Job
        </DialogTrigger>
      )}
      <DialogContent className="sm:max-w-md max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Create Job</DialogTitle>
          <DialogDescription>
            Run a flow with inputs.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Mode toggle */}
          <div className="flex gap-1 p-0.5 bg-zinc-100 dark:bg-zinc-900 rounded-md">
            <button
              onClick={() => setMode("flow")}
              className={`flex-1 py-1.5 text-xs font-medium rounded transition-colors ${
                mode === "flow"
                  ? "bg-white dark:bg-zinc-700 text-foreground shadow-sm"
                  : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
              }`}
            >
              From Flow
            </button>
            <button
              onClick={() => setMode("json")}
              className={`flex-1 py-1.5 text-xs font-medium rounded transition-colors ${
                mode === "json"
                  ? "bg-white dark:bg-zinc-700 text-foreground shadow-sm"
                  : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
              }`}
            >
              JSON
            </button>
          </div>

          {/* Optional job name */}
          <div className="space-y-2">
            <Label>Name <span className="text-zinc-500 font-normal">(optional)</span></Label>
            <Input
              value={jobName}
              onChange={(e) => setJobName(e.target.value)}
              placeholder="Human-friendly job name"
              className="text-xs"
            />
          </div>

          {mode === "flow" ? (
            <>
              {/* Flow selector */}
              <div className="space-y-2">
                <Label>Flow</Label>
                <Select
                  value={selectedItem}
                  onValueChange={(v) => { if (v !== null) handleSelect(v); }}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select a flow" />
                  </SelectTrigger>
                  <SelectContent>
                    {localFlows.length > 0 && (
                      <SelectGroup>
                        <SelectLabel>Local Flows</SelectLabel>
                        {localFlows.map((f) => (
                          <SelectItem key={`flow:${f.path}`} value={`flow:${f.path}`}>
                            {f.name}
                            <span className="text-zinc-500 ml-2">
                              {f.steps_count} step{f.steps_count !== 1 ? "s" : ""}
                            </span>
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    )}
                    {templates.length > 0 && (
                      <SelectGroup>
                        <SelectLabel>Templates</SelectLabel>
                        {templates.map((t) => (
                          <SelectItem key={`template:${t.name}`} value={`template:${t.name}`}>
                            {t.name}
                            {t.description && (
                              <span className="text-zinc-500 ml-2">
                                — {t.description}
                              </span>
                            )}
                          </SelectItem>
                        ))}
                      </SelectGroup>
                    )}
                  </SelectContent>
                </Select>
                {loading && (
                  <p className="text-xs text-zinc-500">Loading flow...</p>
                )}
              </div>

              {/* Dynamic input fields from $job references */}
              {jobInputFields.length > 0 && (
                <div className="space-y-3">
                  <Label className="text-zinc-500 dark:text-zinc-400">Inputs</Label>
                  <JobInputForm
                    fields={jobInputFields}
                    values={inputValues}
                    onChange={handleInputChange}
                  />
                </div>
              )}

              {workflow && jobInputFields.length === 0 && (
                <p className="text-xs text-zinc-500">
                  This flow has no job-level inputs.
                </p>
              )}
            </>
          ) : (
            /* JSON mode */
            <div className="space-y-2">
              <Label>Flow JSON</Label>
              <Textarea
                value={workflowJson}
                onChange={(e) => {
                  setWorkflowJson(e.target.value);
                  setJsonError(null);
                }}
                className="font-mono text-xs min-h-[200px]"
                placeholder='{"steps": {...}}'
              />
            </div>
          )}

          <div className="space-y-2">
            <Label>Workspace Path <span className="text-zinc-500 font-normal">(optional)</span></Label>
            <Input
              value={workspacePath}
              onChange={(e) => setWorkspacePath(e.target.value)}
              placeholder="/home/zack/work/stepwise"
              className="font-mono text-xs"
            />
          </div>

          {jsonError && (
            <p className="text-red-500 dark:text-red-400 text-xs">{jsonError}</p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!canSubmit}
          >
            {mutations.createJob.isPending ? "Creating..." : "Run"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
