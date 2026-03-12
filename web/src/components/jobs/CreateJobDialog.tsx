import { useState } from "react";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Plus } from "lucide-react";
import type { FlowDefinition } from "@/lib/types";

interface CreateJobDialogProps {
  onCreated?: (jobId: string) => void;
}

export function CreateJobDialog({ onCreated }: CreateJobDialogProps) {
  const [open, setOpen] = useState(false);
  const [objective, setObjective] = useState("");
  const [workflowJson, setWorkflowJson] = useState("");
  const [inputsJson, setInputsJson] = useState("{}");
  const [selectedItem, setSelectedItem] = useState<string>("");
  const [workspacePath, setWorkspacePath] = useState("");
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const { data: templates = [] } = useTemplates();
  const { data: localFlows = [] } = useLocalFlows();
  const mutations = useStepwiseMutations();

  const handleSelect = async (value: string) => {
    setSelectedItem(value);
    setJsonError(null);

    if (value.startsWith("template:")) {
      const name = value.slice("template:".length);
      const tmpl = templates.find((t) => t.name === name);
      if (tmpl) {
        setWorkflowJson(JSON.stringify(tmpl.workflow, null, 2));
      }
    } else if (value.startsWith("flow:")) {
      const path = value.slice("flow:".length);
      setLoading(true);
      try {
        const detail = await fetchLocalFlow(path);
        setWorkflowJson(JSON.stringify(detail.flow, null, 2));
      } catch (e) {
        setJsonError(e instanceof Error ? e.message : "Failed to load flow");
      } finally {
        setLoading(false);
      }
    }
  };

  const handleSubmit = () => {
    try {
      const workflow: FlowDefinition = JSON.parse(workflowJson);
      const inputs = JSON.parse(inputsJson);
      setJsonError(null);

      mutations.createJob.mutate(
        { objective, workflow, inputs, workspace_path: workspacePath || undefined },
        {
          onSuccess: (job) => {
            setOpen(false);
            setObjective("");
            setWorkflowJson("");
            setInputsJson("{}");
            setSelectedItem("");
            setWorkspacePath("");
            onCreated?.(job.id);
          },
        }
      );
    } catch (e) {
      setJsonError(
        e instanceof Error ? e.message : "Invalid JSON"
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        className="inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-sm font-medium transition-all disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground shadow-xs hover:bg-primary/90 h-8 px-3 cursor-pointer"
      >
        <Plus className="w-3.5 h-3.5" />
        New Job
      </DialogTrigger>
      <DialogContent className="sm:max-w-md max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Create Job</DialogTitle>
          <DialogDescription>
            Create a new job from a flow, template, or JSON definition.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label>Objective</Label>
            <Input
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              placeholder="What should this job accomplish?"
            />
          </div>

          <Tabs defaultValue="flow">
            <TabsList className="w-full">
              <TabsTrigger value="flow" className="flex-1">
                From Flow
              </TabsTrigger>
              <TabsTrigger value="json" className="flex-1">
                JSON
              </TabsTrigger>
            </TabsList>

            <TabsContent value="flow" className="space-y-2">
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
              {selectedItem && workflowJson && !loading && (
                <Textarea
                  value={workflowJson}
                  onChange={(e) => setWorkflowJson(e.target.value)}
                  className="font-mono text-xs min-h-[200px]"
                />
              )}
            </TabsContent>

            <TabsContent value="json" className="space-y-2">
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
            </TabsContent>
          </Tabs>

          <div className="space-y-2">
            <Label>Initial Inputs (JSON)</Label>
            <Textarea
              value={inputsJson}
              onChange={(e) => {
                setInputsJson(e.target.value);
                setJsonError(null);
              }}
              className="font-mono text-xs min-h-[80px]"
              placeholder="{}"
            />
          </div>

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
            <p className="text-red-400 text-xs">{jsonError}</p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={
              !objective || !workflowJson || mutations.createJob.isPending || loading
            }
          >
            {mutations.createJob.isPending
              ? "Creating..."
              : "Create Job"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
