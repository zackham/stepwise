import { useState, type ReactNode } from "react";
import type { StepDefinition, OutputFieldSchema } from "@/lib/types";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from "@/components/ui/collapsible";
import {
  X,
  Trash2,
  ExternalLink,
  ChevronDown,
  Clock,
  RefreshCw,
  Shield,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { executorIcon } from "@/lib/executor-utils";
import { useConfig } from "@/hooks/useConfig";

interface StepDefinitionPanelProps {
  stepDef: StepDefinition;
  onClose: () => void;
  onDelete?: () => void;
  onViewFile?: (path: string) => void;
  onViewSource?: (field: string) => void;
}

const EXEC_TYPE_COLORS: Record<string, string> = {
  script: "bg-emerald-900/50 text-emerald-400 border-emerald-800",
  llm: "bg-violet-900/50 text-violet-400 border-violet-800",
  mock_llm: "bg-violet-900/50 text-violet-400 border-violet-800",
  agent: "bg-blue-900/50 text-blue-400 border-blue-800",
  external: "bg-amber-900/50 text-amber-400 border-amber-800",
  poll: "bg-cyan-900/50 text-cyan-400 border-cyan-800",
};

const EXIT_ACTION_COLORS: Record<string, string> = {
  advance: "text-emerald-400",
  loop: "text-amber-400",
  escalate: "text-red-400",
  abandon: "text-red-500 font-semibold",
};

/** Extract file paths referenced in a command string. */
function extractFilePaths(command: string): string[] {
  const matches = command.match(/\b[\w./-]+\.(py|sh|js|ts|rb|yaml|yml|json)\b/g);
  return matches ? [...new Set(matches)] : [];
}

/** Collapsible section with label + chevron. */
function Section({
  title,
  children,
  defaultOpen = true,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex items-center justify-between w-full px-4 py-2 group cursor-pointer">
        <span className="text-[11px] text-zinc-500 uppercase tracking-wider font-medium">
          {title}
        </span>
        <ChevronDown
          className={cn(
            "w-3 h-3 text-zinc-600 transition-transform",
            open && "rotate-180"
          )}
        />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="px-4 pb-3 space-y-3">{children}</div>
      </CollapsibleContent>
    </Collapsible>
  );
}

/** Read-only pre block for commands/prompts. */
function CodeBlock({
  children,
  tint,
}: {
  children: string;
  tint?: "green" | "cyan";
}) {
  const tintClass =
    tint === "green"
      ? "border-emerald-900/50 bg-emerald-950/20"
      : tint === "cyan"
        ? "border-cyan-900/50 bg-cyan-950/20"
        : "border-zinc-800 bg-zinc-900/50";
  return (
    <pre
      className={cn(
        "text-xs font-mono text-zinc-400 border rounded px-2.5 py-2 whitespace-pre-wrap break-words max-h-[200px] overflow-y-auto leading-relaxed",
        tintClass
      )}
    >
      {children}
    </pre>
  );
}

/** "Edit in source" link. */
function EditSourceLink({
  field,
  onViewSource,
}: {
  field: string;
  onViewSource?: (field: string) => void;
}) {
  if (!onViewSource) return null;
  return (
    <button
      onClick={() => onViewSource(field)}
      className="inline-flex items-center gap-1 text-[11px] text-blue-400 hover:text-blue-300 transition-colors"
    >
      <ExternalLink className="w-2.5 h-2.5" />
      Edit in source
    </button>
  );
}

/** A small key-value row. */
function KV({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline gap-2 text-xs min-w-0">
      <span className="text-zinc-500 shrink-0">{label}</span>
      <span className="text-zinc-300 font-mono min-w-0 break-all">{children}</span>
    </div>
  );
}

/** Badge chip. */
function Badge({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "text-[10px] font-medium px-1.5 py-0.5 rounded border",
        className
      )}
    >
      {children}
    </span>
  );
}

/** Output schema table. */
function OutputSchemaTable({
  schema,
}: {
  schema: Record<string, OutputFieldSchema>;
}) {
  const entries = Object.entries(schema);
  if (entries.length === 0) return null;
  return (
    <div className="space-y-1.5">
      {entries.map(([name, field]) => (
        <div
          key={name}
          className="flex items-baseline gap-2 text-xs font-mono bg-zinc-900/50 rounded px-2 py-1.5 min-w-0"
        >
          <span className="text-blue-400">{name}</span>
          <Badge className="bg-zinc-800 text-zinc-400 border-zinc-700">
            {field.type}
          </Badge>
          {field.required === false && (
            <span className="text-zinc-600 text-[10px]">optional</span>
          )}
          {field.description && (
            <span className="text-zinc-500 font-sans text-[11px] truncate">
              {field.description}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

export function StepDefinitionPanel({
  stepDef,
  onClose,
  onDelete,
  onViewFile,
  onViewSource,
}: StepDefinitionPanelProps) {
  const { data: configData } = useConfig();
  const execType = stepDef.executor.type;
  const config = stepDef.executor.config as Record<string, unknown>;

  // Resolve model label
  const modelValue = config.model ? String(config.model) : "";
  const matchedLabel = configData?.labels?.find(
    (l) => l.name === modelValue
  );

  // Determine which sections to show
  const hasOutputSchema =
    stepDef.output_schema && Object.keys(stepDef.output_schema).length > 0;
  const hasDataFlow =
    stepDef.outputs.length > 0 ||
    hasOutputSchema ||
    stepDef.inputs.length > 0;
  const hasControlFlow =
    stepDef.exit_rules.length > 0 ||
    stepDef.after.length > 0 ||
    !!stepDef.for_each;
  const hasDecorators = stepDef.executor.decorators.length > 0;
  const hasSettings =
    !!stepDef.limits ||
    (stepDef.idempotency !== "idempotent" && stepDef.idempotency !== "default") ||
    !!stepDef.chain;

  // Idempotency badge
  const idempotencyBadge =
    stepDef.idempotency === "retriable_with_guard"
      ? { label: "retriable", color: "bg-amber-900/50 text-amber-400 border-amber-800" }
      : stepDef.idempotency === "non_retriable"
        ? { label: "non-retriable", color: "bg-red-900/50 text-red-400 border-red-800" }
        : null;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Header */}
      <div className="p-3 border-b border-border shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-zinc-400 shrink-0">
              {executorIcon(execType)}
            </span>
            <h3 className="font-semibold text-foreground text-sm truncate">
              {stepDef.name}
            </h3>
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
        {/* Badges row */}
        <div className="flex flex-wrap gap-1.5 mt-2">
          <Badge
            className={
              EXEC_TYPE_COLORS[execType] ??
              "bg-zinc-800 text-zinc-400 border-zinc-700"
            }
          >
            {execType}
          </Badge>
          {!!config.emit_flow && (
            <Badge className="bg-blue-900/50 text-blue-400 border-blue-800">
              emit_flow
            </Badge>
          )}
          {stepDef.for_each && (
            <Badge className="bg-purple-900/50 text-purple-400 border-purple-800">
              for_each
            </Badge>
          )}
          {idempotencyBadge && (
            <Badge className={idempotencyBadge.color}>
              {idempotencyBadge.label}
            </Badge>
          )}
        </div>
        {/* Description */}
        {stepDef.description && (
          <p className="text-xs text-zinc-400 mt-2 leading-relaxed">
            {stepDef.description}
          </p>
        )}
      </div>

      {/* Content area */}
      <ScrollArea className="flex-1 min-h-0">
          <div className="py-2 space-y-1 divide-y divide-zinc-800/50">
            {/* ── Executor Config ── */}
            <Section title="Executor">
              {execType === "script" && (() => {
                const cmd = String(config.command ?? "");
                const filePaths = extractFilePaths(cmd);
                return (
                  <div className="space-y-2">
                    <CodeBlock tint="green">{cmd}</CodeBlock>
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

              {(execType === "llm" || execType === "mock_llm") && (
                <div className="space-y-3">
                  {!!config.prompt && (
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-zinc-500">Prompt</span>
                        <EditSourceLink
                          field="prompt"
                          onViewSource={onViewSource}
                        />
                      </div>
                      <CodeBlock>{String(config.prompt)}</CodeBlock>
                    </div>
                  )}
                  {config.system != null && (
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-zinc-500">
                          System Prompt
                        </span>
                        <EditSourceLink
                          field="system"
                          onViewSource={onViewSource}
                        />
                      </div>
                      <CodeBlock>{String(config.system)}</CodeBlock>
                    </div>
                  )}
                  {modelValue && (
                    <KV label="Model">
                      <span>{modelValue}</span>
                      {matchedLabel && (
                        <span className="text-zinc-600 ml-1">
                          → {matchedLabel.model}
                        </span>
                      )}
                    </KV>
                  )}
                  <div className="flex gap-4">
                    {config.temperature != null && (
                      <KV label="Temperature">
                        {String(config.temperature)}
                      </KV>
                    )}
                    {config.max_tokens != null && (
                      <KV label="Max Tokens">
                        {String(config.max_tokens)}
                      </KV>
                    )}
                  </div>
                </div>
              )}

              {execType === "agent" && (
                <div className="space-y-3">
                  {!!config.prompt && (
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-zinc-500">Prompt</span>
                        <EditSourceLink
                          field="prompt"
                          onViewSource={onViewSource}
                        />
                      </div>
                      <CodeBlock>{String(config.prompt)}</CodeBlock>
                    </div>
                  )}
                  <div className="flex flex-wrap gap-1.5">
                    {!!config.output_mode && (
                      <Badge className="bg-zinc-800 text-zinc-400 border-zinc-700">
                        output: {String(config.output_mode)}
                      </Badge>
                    )}
                    {!!config.output_path && (
                      <Badge className="bg-zinc-800 text-zinc-400 border-zinc-700">
                        path: {String(config.output_path)}
                      </Badge>
                    )}
                  </div>
                </div>
              )}

              {execType === "external" && (
                <div className="space-y-3">
                  {!!config.prompt && (
                    <div className="space-y-1.5">
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-zinc-500">
                          Prompt / Instructions
                        </span>
                        <EditSourceLink
                          field="prompt"
                          onViewSource={onViewSource}
                        />
                      </div>
                      <CodeBlock>{String(config.prompt)}</CodeBlock>
                    </div>
                  )}
                  {hasOutputSchema && stepDef.output_schema && (
                    <div className="space-y-1.5">
                      <span className="text-xs text-zinc-500">
                        Output Schema
                      </span>
                      <OutputSchemaTable schema={stepDef.output_schema} />
                    </div>
                  )}
                </div>
              )}

              {execType === "poll" && (
                <div className="space-y-3">
                  {!!config.check_command && (
                    <div className="space-y-1.5">
                      <span className="text-xs text-zinc-500">
                        Check Command
                      </span>
                      <CodeBlock tint="cyan">
                        {String(config.check_command)}
                      </CodeBlock>
                    </div>
                  )}
                  {config.interval_seconds != null && (
                    <KV label="Interval">
                      Every {String(config.interval_seconds)}s
                    </KV>
                  )}
                  {!!config.prompt && (
                    <div className="space-y-1.5">
                      <span className="text-xs text-zinc-500">
                        Waiting Message
                      </span>
                      <CodeBlock>{String(config.prompt)}</CodeBlock>
                    </div>
                  )}
                </div>
              )}
            </Section>

            {/* ── Data Flow ── */}
            {hasDataFlow && (
              <Section title="Data Flow">
                {stepDef.outputs.length > 0 && (
                  <div className="space-y-1.5">
                    <span className="text-xs text-zinc-500">Outputs</span>
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
                  </div>
                )}
                {hasOutputSchema && stepDef.output_schema && execType !== "external" && (
                  <div className="space-y-1.5">
                    <span className="text-xs text-zinc-500">Output Schema</span>
                    <OutputSchemaTable schema={stepDef.output_schema} />
                  </div>
                )}
                {stepDef.when && (
                  <div className="space-y-1.5">
                    <span className="text-xs text-zinc-500">Activation Condition</span>
                    <CodeBlock>{stepDef.when}</CodeBlock>
                  </div>
                )}
                {stepDef.inputs.length > 0 && (
                  <div className="space-y-1.5">
                    <span className="text-xs text-zinc-500">
                      Input Bindings
                    </span>
                    <div className="space-y-1.5">
                      {stepDef.inputs.map((b) => (
                        <div
                          key={b.local_name}
                          className="text-xs font-mono bg-zinc-900/50 rounded px-2 py-1.5 flex items-center gap-1.5 flex-wrap"
                        >
                          <span className="text-blue-400">
                            {b.local_name}
                          </span>
                          <span className="text-zinc-600">&larr;</span>
                          {b.any_of_sources ? (
                            <span className="text-zinc-400">
                              any_of({b.any_of_sources.map(s => `${s.step}.${s.field}`).join(", ")})
                            </span>
                          ) : (
                            <span className="text-zinc-400">
                              {b.source_step}.{b.source_field}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </Section>
            )}

            {/* ── Control Flow ── */}
            {hasControlFlow && (
              <Section title="Control Flow">
                {stepDef.exit_rules.length > 0 && (
                  <div className="space-y-1.5">
                    <span className="text-xs text-zinc-500">Exit Rules</span>
                    <div className="space-y-1.5">
                      {stepDef.exit_rules.map((r) => {
                        const action = String(r.config.action ?? "");
                        const actionColor =
                          EXIT_ACTION_COLORS[action] ?? "text-zinc-400";
                        return (
                          <div
                            key={r.name}
                            className="text-xs font-mono bg-zinc-900/50 rounded px-2.5 py-2 space-y-0.5"
                          >
                            <div className="flex items-center gap-2">
                              <span className="text-zinc-300">{r.name}</span>
                              <span className={actionColor}>→ {action}</span>
                            </div>
                            {r.config.condition != null && (
                              <div className="text-zinc-500">
                                when: {String(r.config.condition)}
                              </div>
                            )}
                            {action === "loop" && !!r.config.target && (
                              <div className="text-zinc-500">
                                target: {String(r.config.target)}
                                {r.config.max_iterations != null &&
                                  ` (max ${String(r.config.max_iterations)})`}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
                {stepDef.after.length > 0 && (
                  <div className="space-y-1.5">
                    <span className="text-xs text-zinc-500">After</span>
                    <div className="flex flex-wrap gap-1.5">
                      {stepDef.after.map((s) => (
                        <span
                          key={s}
                          className="text-xs font-mono bg-zinc-800 text-zinc-400 px-2 py-1 rounded"
                        >
                          after {s}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
                {stepDef.for_each && (
                  <div className="space-y-1.5">
                    <span className="text-xs text-zinc-500">For-Each</span>
                    <div className="grid grid-cols-2 gap-1 text-xs font-mono">
                      <span className="text-zinc-500">Source</span>
                      <span className="text-zinc-400 break-all">
                        {stepDef.for_each.source_step}.
                        {stepDef.for_each.source_field}
                      </span>
                      <span className="text-zinc-500">Item Var</span>
                      <span className="text-zinc-400 break-all">
                        {stepDef.for_each.item_var}
                      </span>
                      <span className="text-zinc-500">On Error</span>
                      <span className="text-zinc-400 break-all">
                        {stepDef.for_each.on_error}
                      </span>
                    </div>
                  </div>
                )}
              </Section>
            )}

            {/* ── Decorators ── */}
            {hasDecorators && (
              <Section title="Decorators">
                <div className="space-y-1.5">
                  {stepDef.executor.decorators.map((d, i) => (
                    <div
                      key={i}
                      className="flex items-center gap-2 text-xs font-mono bg-zinc-900/50 rounded px-2.5 py-2"
                    >
                      {d.type === "timeout" && (
                        <>
                          <Clock className="w-3 h-3 text-zinc-500" />
                          <span className="text-zinc-300">
                            {String(d.config.timeout_minutes ?? d.config.timeout ?? "?")}m
                          </span>
                        </>
                      )}
                      {d.type === "retry" && (
                        <>
                          <RefreshCw className="w-3 h-3 text-zinc-500" />
                          <span className="text-zinc-300">
                            max {String(d.config.max_retries ?? d.config.max ?? "?")}
                          </span>
                          {d.config.backoff && (
                            <span className="text-zinc-500">
                              {String(d.config.backoff)} backoff
                            </span>
                          )}
                        </>
                      )}
                      {d.type === "fallback" && (
                        <>
                          <Shield className="w-3 h-3 text-zinc-500" />
                          <span className="text-zinc-300">
                            fallback → {String(d.config.executor_type ?? d.config.type ?? "?")}
                          </span>
                        </>
                      )}
                      {!["timeout", "retry", "fallback"].includes(d.type) && (
                        <span className="text-zinc-400">{d.type}</span>
                      )}
                    </div>
                  ))}
                </div>
              </Section>
            )}

            {/* ── Settings ── */}
            {hasSettings && (
              <Section title="Settings">
                {stepDef.limits && (
                  <div className="space-y-1.5">
                    <span className="text-xs text-zinc-500">Limits</span>
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
                )}
                {stepDef.chain && (
                  <KV label="Chain">
                    {stepDef.chain}
                    {stepDef.chain_label && (
                      <span className="text-zinc-500 ml-1">
                        ({stepDef.chain_label})
                      </span>
                    )}
                  </KV>
                )}
              </Section>
            )}
          </div>
        </ScrollArea>
    </div>
  );
}
