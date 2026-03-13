import { useState, useCallback, useRef, useEffect, type ReactNode } from "react";
import type { StepDefinition } from "@/lib/types";
import { ScrollArea } from "@/components/ui/scroll-area";
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
  Trash2,
  ExternalLink,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useConfig } from "@/hooks/useConfig";

type StepPanelTab = "details" | "chat";

interface StepDefinitionPanelProps {
  stepDef: StepDefinition;
  onClose: () => void;
  onPatch: (changes: Record<string, unknown>) => void;
  onDelete?: () => void;
  onViewFile?: (path: string) => void;
  onViewSource?: (field: string) => void;
  /** When set, show tabs (details/chat). Undefined = details only, no tabs. */
  mode?: StepPanelTab;
  onTabChange?: (tab: StepPanelTab) => void;
  onCloseChat?: () => void;
  /** Chat content to render when mode="chat" */
  chatContent?: ReactNode;
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

/** Extract file paths referenced in a command string. */
function extractFilePaths(command: string): string[] {
  const matches = command.match(/\b[\w./-]+\.(py|sh|js|ts|rb|yaml|yml|json)\b/g);
  return matches ? [...new Set(matches)] : [];
}

/** Read-only truncated prompt with "Edit in Source" link. */
function PromptPreview({ label, value, field, onViewSource }: { label: string; value: string; field: string; onViewSource?: (field: string) => void }) {
  if (!value) return null;
  const MAX_LINES = 8;
  const lines = value.split("\n");
  const truncated = lines.length > MAX_LINES;
  const display = truncated ? lines.slice(0, MAX_LINES).join("\n") + "\n…" : value;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between">
        <Label className="text-xs text-zinc-500">{label}</Label>
        {onViewSource && (
          <button
            onClick={() => onViewSource(field)}
            className="inline-flex items-center gap-1 text-[11px] text-blue-400 hover:text-blue-300 transition-colors"
          >
            <ExternalLink className="w-2.5 h-2.5" />
            Edit in source
          </button>
        )}
      </div>
      <pre className="text-xs font-mono text-zinc-400 bg-zinc-900/50 border border-zinc-800 rounded px-2.5 py-2 whitespace-pre-wrap break-words max-h-[160px] overflow-y-auto leading-relaxed">
        {display}
      </pre>
    </div>
  );
}

function ModelSelect({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { data: config } = useConfig();
  const [customInput, setCustomInput] = useState(false);

  const labels = config?.labels ?? [];
  const models = config?.model_registry ?? [];

  // Check if current value is a known label or model
  const isLabel = labels.some((l) => l.name === value);
  const isKnownModel = models.some((m) => m.id === value);
  const matchedLabel = labels.find((l) => l.name === value);

  if (customInput) {
    return (
      <div className="space-y-2">
        <Label className="text-xs text-zinc-500">Model</Label>
        <div className="flex gap-1">
          <Input
            value={value}
            onChange={(e) => onChange(e.target.value)}
            className="text-xs bg-zinc-900 border-zinc-700 flex-1 font-mono"
            placeholder="provider/model-id"
          />
          <button
            onClick={() => setCustomInput(false)}
            className="text-[10px] text-zinc-500 hover:text-zinc-300 px-2"
          >
            List
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <Label className="text-xs text-zinc-500">Model</Label>
        {matchedLabel && (
          <span className="text-[10px] text-zinc-600 font-mono">
            → {matchedLabel.model}
          </span>
        )}
      </div>
      <div className="flex gap-1">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="flex-1 text-xs bg-zinc-900 border border-zinc-700 rounded px-2 py-1.5 text-zinc-300"
        >
          {!value && <option value="">Select model...</option>}
          {/* Show current value if not in any group */}
          {value && !isLabel && !isKnownModel && (
            <option value={value}>{value}</option>
          )}
          <optgroup label="Labels">
            {labels.map((l) => (
              <option key={l.name} value={l.name}>
                {l.name} → {l.model}
              </option>
            ))}
          </optgroup>
          {models.length > 0 && (
            <optgroup label="Models">
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.id}
                </option>
              ))}
            </optgroup>
          )}
        </select>
        <button
          onClick={() => setCustomInput(true)}
          className="text-[10px] text-zinc-500 hover:text-zinc-300 px-2 shrink-0"
          title="Type a custom model ID"
        >
          Custom
        </button>
      </div>
    </div>
  );
}

export function StepDefinitionPanel({
  stepDef,
  onClose,
  onPatch,
  onDelete,
  onViewFile,
  onViewSource,
  mode,
  onTabChange,
  onCloseChat,
  chatContent,
}: StepDefinitionPanelProps) {
  const execType = stepDef.executor.type;
  const showTabs = mode !== undefined;
  const activeTab = mode ?? "details";

  const handlePatch = useCallback(
    (key: string, value: unknown) => {
      onPatch({ [key]: value });
    },
    [onPatch]
  );

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-border shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-zinc-400 shrink-0">{executorIcon(execType)}</span>
          <h3 className="font-semibold text-foreground text-sm truncate">{stepDef.name}</h3>
          <span className="text-[10px] font-mono text-zinc-500 bg-zinc-800 px-1.5 py-0.5 rounded shrink-0">
            {execType}
          </span>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {onDelete && (
            <button
              onClick={onDelete}
              className="text-zinc-500 hover:text-red-400"
              title="Delete step"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
          <button
            onClick={onClose}
            className="text-zinc-500 hover:text-foreground"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Tab bar (only when chat is active) */}
      {showTabs && (
        <div className="flex items-center border-b border-border bg-zinc-950/50 px-2 shrink-0">
          <button
            onClick={() => onTabChange?.("details")}
            className={cn(
              "px-3 py-1.5 text-xs font-medium border-b-2 transition-colors",
              activeTab === "details"
                ? "border-blue-500 text-foreground"
                : "border-transparent text-zinc-500 hover:text-zinc-300"
            )}
          >
            Details
          </button>
          <button
            onClick={() => onTabChange?.("chat")}
            className={cn(
              "px-3 py-1.5 text-xs font-medium border-b-2 transition-colors flex items-center gap-1.5",
              activeTab === "chat"
                ? "border-violet-500 text-foreground"
                : "border-transparent text-zinc-500 hover:text-zinc-300"
            )}
          >
            Chat
            <button
              onClick={(e) => { e.stopPropagation(); onCloseChat?.(); }}
              className="text-zinc-500 hover:text-foreground rounded-sm"
              title="Close chat"
            >
              <X className="w-3 h-3" />
            </button>
          </button>
        </div>
      )}

      {/* Content area */}
      {activeTab === "chat" && chatContent ? (
        chatContent
      ) : (
        <ScrollArea className="flex-1 min-h-0">
          <div className="p-4 space-y-5">
            {/* Executor-specific config */}
            {execType === "script" && (() => {
              const cmd = String(stepDef.executor.config.command ?? "");
              const filePaths = extractFilePaths(cmd);
              return (
                <div className="space-y-2">
                  <Label className="text-xs text-zinc-500">Run Command</Label>
                  <DebouncedInput
                    value={cmd}
                    onChange={(v) => handlePatch("run", v)}
                    className="font-mono text-xs bg-zinc-900 border-zinc-700"
                  />
                  {onViewFile && filePaths.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {filePaths.map((fp) => (
                        <button
                          key={fp}
                          onClick={() => onViewFile(fp)}
                          className="inline-flex items-center gap-1 text-[11px] font-mono text-blue-400 hover:text-blue-300 bg-blue-950/30 hover:bg-blue-950/50 rounded px-1.5 py-0.5 transition-colors"
                        >
                          <ExternalLink className="w-2.5 h-2.5" />
                          {fp}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            })()}

            {(execType === "llm" || execType === "agent") && (
              <>
                <PromptPreview
                  label="Prompt"
                  field="prompt"
                  value={String(stepDef.executor.config.prompt ?? "")}
                  onViewSource={onViewSource}
                />
                {execType === "llm" && (
                  <>
                    {stepDef.executor.config.system != null && (
                      <PromptPreview
                        label="System Prompt"
                        field="system"
                        value={String(stepDef.executor.config.system ?? "")}
                        onViewSource={onViewSource}
                      />
                    )}
                    <ModelSelect
                      value={String(stepDef.executor.config.model ?? "")}
                      onChange={(v) => handlePatch("model", v)}
                    />
                  </>
                )}
              </>
            )}

            {execType === "human" && (
              <PromptPreview
                label="Prompt / Instructions"
                field="prompt"
                value={String(stepDef.executor.config.prompt ?? "")}
                onViewSource={onViewSource}
              />
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
      )}
    </div>
  );
}
