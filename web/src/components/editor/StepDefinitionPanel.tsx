import { useState, useCallback, useRef, useEffect } from "react";
import type { StepDefinition } from "@/lib/types";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  X,
  Terminal,
  User,
  Brain,
  Bot,
  Cog,
  Repeat,
  Plus,
  Trash2,
} from "lucide-react";

interface StepDefinitionPanelProps {
  stepDef: StepDefinition;
  onClose: () => void;
  onPatch: (changes: Record<string, unknown>) => void;
  onDelete?: () => void;
}

function executorIcon(type: string) {
  switch (type) {
    case "script":
      return <Terminal className="w-4 h-4" />;
    case "human":
      return <User className="w-4 h-4" />;
    case "mock_llm":
    case "llm":
      return <Brain className="w-4 h-4" />;
    case "agent":
      return <Bot className="w-4 h-4" />;
    case "for_each":
      return <Repeat className="w-4 h-4" />;
    default:
      return <Cog className="w-4 h-4" />;
  }
}

function DebouncedTextarea({
  value,
  onChange,
  delay = 500,
  ...props
}: {
  value: string;
  onChange: (value: string) => void;
  delay?: number;
} & Omit<React.ComponentProps<typeof Textarea>, "value" | "onChange">) {
  const [local, setLocal] = useState(value);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    setLocal(value);
  }, [value]);

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const v = e.target.value;
    setLocal(v);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => onChangeRef.current(v), delay);
  };

  useEffect(() => () => clearTimeout(timerRef.current), []);

  return <Textarea value={local} onChange={handleChange} {...props} />;
}

function DebouncedInput({
  value,
  onChange,
  delay = 500,
  ...props
}: {
  value: string;
  onChange: (value: string) => void;
  delay?: number;
} & Omit<React.ComponentProps<typeof Input>, "value" | "onChange">) {
  const [local, setLocal] = useState(value);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    setLocal(value);
  }, [value]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value;
    setLocal(v);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => onChangeRef.current(v), delay);
  };

  useEffect(() => () => clearTimeout(timerRef.current), []);

  return <Input value={local} onChange={handleChange} {...props} />;
}

export function StepDefinitionPanel({
  stepDef,
  onClose,
  onPatch,
  onDelete,
}: StepDefinitionPanelProps) {
  const execType = stepDef.executor.type;

  const handlePatch = useCallback(
    (key: string, value: unknown) => {
      onPatch({ [key]: value });
    },
    [onPatch]
  );

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-border">
        <div className="flex items-center gap-2">
          <span className="text-zinc-400">{executorIcon(execType)}</span>
          <h3 className="font-semibold text-foreground">{stepDef.name}</h3>
          <span className="text-xs font-mono text-zinc-500 bg-zinc-800 px-1.5 py-0.5 rounded">
            {execType}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {onDelete && (
            <button
              onClick={onDelete}
              className="text-zinc-500 hover:text-red-400"
              title="Delete step"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          )}
          <button
            onClick={onClose}
            className="text-zinc-500 hover:text-foreground"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      <ScrollArea className="flex-1 min-h-0">
        <div className="p-4 space-y-5">
          {/* Executor-specific config */}
          {execType === "script" && (
            <div className="space-y-2">
              <Label className="text-xs text-zinc-500">Run Command</Label>
              <DebouncedInput
                value={String(stepDef.executor.config.command ?? "")}
                onChange={(v) => handlePatch("run", v)}
                className="font-mono text-xs bg-zinc-900 border-zinc-700"
              />
            </div>
          )}

          {(execType === "llm" || execType === "agent") && (
            <>
              <div className="space-y-2">
                <Label className="text-xs text-zinc-500">Prompt</Label>
                <DebouncedTextarea
                  value={String(stepDef.executor.config.prompt ?? "")}
                  onChange={(v) => handlePatch("prompt", v)}
                  className="font-mono text-xs bg-zinc-900 border-zinc-700 min-h-[120px]"
                />
              </div>
              {execType === "llm" && (
                <>
                  {stepDef.executor.config.system != null && (
                    <div className="space-y-2">
                      <Label className="text-xs text-zinc-500">
                        System Prompt
                      </Label>
                      <DebouncedTextarea
                        value={String(stepDef.executor.config.system ?? "")}
                        onChange={(v) => handlePatch("system", v)}
                        className="font-mono text-xs bg-zinc-900 border-zinc-700 min-h-[80px]"
                      />
                    </div>
                  )}
                  <div className="space-y-2">
                    <Label className="text-xs text-zinc-500">Model</Label>
                    <DebouncedInput
                      value={String(stepDef.executor.config.model ?? "")}
                      onChange={(v) => handlePatch("model", v)}
                      className="text-xs bg-zinc-900 border-zinc-700"
                      placeholder="e.g. gpt-4o"
                    />
                  </div>
                </>
              )}
            </>
          )}

          {execType === "human" && (
            <div className="space-y-2">
              <Label className="text-xs text-zinc-500">
                Prompt / Instructions
              </Label>
              <DebouncedTextarea
                value={String(stepDef.executor.config.prompt ?? "")}
                onChange={(v) => handlePatch("prompt", v)}
                className="text-xs bg-zinc-900 border-zinc-700 min-h-[100px]"
              />
            </div>
          )}

          <Separator />

          {/* Outputs */}
          <div className="space-y-2">
            <Label className="text-xs text-zinc-500">Outputs</Label>
            <div className="flex flex-wrap gap-1.5">
              {stepDef.outputs.map((out) => (
                <span
                  key={out}
                  className="text-xs font-mono bg-zinc-800 text-zinc-300 px-2 py-1 rounded"
                >
                  {out}
                </span>
              ))}
            </div>
            <DebouncedInput
              value={stepDef.outputs.join(", ")}
              onChange={(v) =>
                handlePatch(
                  "outputs",
                  v
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean)
                )
              }
              className="text-xs bg-zinc-900 border-zinc-700"
              placeholder="comma-separated output names"
            />
          </div>

          {/* Input Bindings */}
          {stepDef.inputs.length > 0 && (
            <>
              <Separator />
              <div className="space-y-2">
                <Label className="text-xs text-zinc-500">Input Bindings</Label>
                <div className="space-y-1.5">
                  {stepDef.inputs.map((b) => (
                    <div
                      key={b.local_name}
                      className="text-xs font-mono bg-zinc-900/50 rounded px-2 py-1.5 flex items-center gap-1.5"
                    >
                      <span className="text-blue-400">{b.local_name}</span>
                      <span className="text-zinc-600">&larr;</span>
                      <span className="text-zinc-400">
                        {b.source_step}.{b.source_field}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* Exit Rules */}
          {stepDef.exit_rules.length > 0 && (
            <>
              <Separator />
              <div className="space-y-2">
                <Label className="text-xs text-zinc-500">Exit Rules</Label>
                <div className="space-y-1.5">
                  {stepDef.exit_rules.map((r) => (
                    <div
                      key={r.name}
                      className="text-xs font-mono bg-zinc-900/50 rounded px-2 py-1.5"
                    >
                      <span className="text-amber-400">{r.name}</span>
                      <span className="text-zinc-600"> ({r.type})</span>
                      {r.config.condition != null && (
                        <span className="text-zinc-500">
                          {" "}
                          when: {String(r.config.condition)}
                        </span>
                      )}
                      {r.config.action != null && (
                        <span className="text-zinc-400">
                          {" "}
                          &rarr; {String(r.config.action)}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* Sequencing */}
          {stepDef.sequencing.length > 0 && (
            <>
              <Separator />
              <div className="space-y-2">
                <Label className="text-xs text-zinc-500">Sequencing</Label>
                <div className="flex flex-wrap gap-1.5">
                  {stepDef.sequencing.map((s) => (
                    <span
                      key={s}
                      className="text-xs font-mono bg-zinc-800 text-zinc-400 px-2 py-1 rounded"
                    >
                      after {s}
                    </span>
                  ))}
                </div>
              </div>
            </>
          )}

          {/* Limits */}
          {stepDef.limits && (
            <>
              <Separator />
              <div className="space-y-2">
                <Label className="text-xs text-zinc-500">Limits</Label>
                <div className="grid grid-cols-2 gap-1 text-xs font-mono">
                  {stepDef.limits.max_cost_usd != null && (
                    <>
                      <span className="text-zinc-500">Max Cost</span>
                      <span className="text-zinc-400">
                        ${stepDef.limits.max_cost_usd}
                      </span>
                    </>
                  )}
                  {stepDef.limits.max_duration_minutes != null && (
                    <>
                      <span className="text-zinc-500">Max Duration</span>
                      <span className="text-zinc-400">
                        {stepDef.limits.max_duration_minutes}m
                      </span>
                    </>
                  )}
                  {stepDef.limits.max_iterations != null && (
                    <>
                      <span className="text-zinc-500">Max Iterations</span>
                      <span className="text-zinc-400">
                        {stepDef.limits.max_iterations}
                      </span>
                    </>
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
