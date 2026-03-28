import { useState, useRef, useEffect } from "react";
import { useConfig, useConfigMutations, useOpenRouterSearch } from "@/hooks/useConfig";
import { useIsMobile } from "@/hooks/useMediaQuery";
import type { LabelInfo, ModelInfo } from "@/lib/api";
import {
  Tag,
  Key,
  Database,
  Settings2,
  Plus,
  Trash2,
  Check,
  X,
  ChevronDown,
  Search,
  Loader2,
} from "lucide-react";
import { cn } from "@/lib/utils";

// ── Formatting helpers ──────────────────────────────────────────────

function formatTokenCount(n: number | undefined | null): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n % 1_000 === 0 ? 0 : 1)}K`;
  return String(n);
}

function formatCostPerMToken(costPerToken: number | undefined | null): string {
  if (costPerToken == null) return "—";
  const perMillion = costPerToken * 1_000_000;
  if (perMillion < 0.01) return "<$0.01";
  if (perMillion >= 100) return `$${perMillion.toFixed(0)}`;
  if (perMillion >= 10) return `$${perMillion.toFixed(1)}`;
  return `$${perMillion.toFixed(2)}`;
}

// ── Source badge ─────────────────────────────────────────────────────

function SourceBadge({ source }: { source: string }) {
  const colors: Record<string, string> = {
    default: "bg-zinc-200 dark:bg-zinc-800 text-zinc-500",
    user: "bg-blue-100 dark:bg-blue-950 text-blue-600 dark:text-blue-400",
    project: "bg-green-100 dark:bg-green-950 text-green-600 dark:text-green-400",
    local: "bg-amber-100 dark:bg-amber-950 text-amber-600 dark:text-amber-400",
  };
  return (
    <span className={cn("text-[10px] px-1.5 py-0.5 rounded", colors[source] ?? colors.default)}>
      {source}
    </span>
  );
}

// ── Label row ───────────────────────────────────────────────────────

function LabelRow({
  label,
  models,
  onUpdate,
  onDelete,
}: {
  label: LabelInfo;
  models: ModelInfo[];
  onUpdate: (model: string) => void;
  onDelete?: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [modelValue, setModelValue] = useState(label.model);

  const handleSave = () => {
    if (modelValue && modelValue !== label.model) {
      onUpdate(modelValue);
    }
    setEditing(false);
  };

  return (
    <div className="flex items-center gap-3 py-2 px-3 hover:bg-zinc-50/50 dark:hover:bg-zinc-900/50 rounded group">
      <span className="text-sm font-mono text-zinc-700 dark:text-zinc-300 w-24 shrink-0">{label.name}</span>
      {editing ? (
        <div className="flex-1 flex items-center gap-2">
          <select
            value={modelValue}
            onChange={(e) => setModelValue(e.target.value)}
            className="flex-1 text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300"
          >
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name} ({m.id})
              </option>
            ))}
            {!models.some((m) => m.id === modelValue) && (
              <option value={modelValue}>{modelValue}</option>
            )}
          </select>
          <button onClick={handleSave} className="text-green-400 hover:text-green-300 p-1">
            <Check className="w-3.5 h-3.5" />
          </button>
          <button onClick={() => setEditing(false)} className="text-zinc-500 hover:text-zinc-700 dark:text-zinc-300 p-1">
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      ) : (
        <>
          <span className="flex-1 text-xs text-zinc-400 font-mono truncate">{label.model}</span>
          <button
            onClick={() => {
              setModelValue(label.model);
              setEditing(true);
            }}
            className="text-[11px] text-zinc-600 hover:text-zinc-700 dark:text-zinc-300 hover-capable:opacity-0 hover-capable:group-hover:opacity-100 transition-opacity"
          >
            Change
          </button>
        </>
      )}
      <SourceBadge source={label.source} />
      {onDelete && (
        <button
          onClick={onDelete}
          className="text-zinc-700 hover:text-red-400 hover-capable:opacity-0 hover-capable:group-hover:opacity-100 transition-opacity p-1"
        >
          <Trash2 className="w-3 h-3" />
        </button>
      )}
    </div>
  );
}

// ── Add label form ──────────────────────────────────────────────────

function AddLabelForm({
  models,
  onAdd,
}: {
  models: ModelInfo[];
  onAdd: (name: string, model: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [model, setModel] = useState(models[0]?.id ?? "");
  const [error, setError] = useState("");
  const isMobile = useIsMobile();

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 text-xs text-zinc-500 hover:text-zinc-700 dark:text-zinc-300 px-3 py-2"
      >
        <Plus className="w-3 h-3" />
        Add Label
      </button>
    );
  }

  const handleSubmit = () => {
    if (!/^[a-z][a-z0-9_-]{0,62}$/.test(name)) {
      setError("Lowercase letters, digits, hyphens, underscores only");
      return;
    }
    onAdd(name, model);
    setName("");
    setModel(models[0]?.id ?? "");
    setOpen(false);
    setError("");
  };

  return (
    <div className={cn("px-3 py-2", isMobile ? "space-y-2" : "flex items-center gap-2")}>
      <div className={cn(isMobile ? "flex items-center gap-2" : "contents")}>
        <input
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setError("");
          }}
          placeholder="label-name"
          className="text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300 w-28 font-mono"
          autoFocus
        />
        <select
          value={model}
          onChange={(e) => setModel(e.target.value)}
          className="flex-1 text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300"
        >
          {models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name} ({m.id})
            </option>
          ))}
        </select>
      </div>
      <div className="flex items-center gap-2">
        <button onClick={handleSubmit} className="text-green-400 hover:text-green-300 p-1">
          <Check className="w-3.5 h-3.5" />
        </button>
        <button onClick={() => setOpen(false)} className="text-zinc-500 hover:text-zinc-700 dark:text-zinc-300 p-1">
          <X className="w-3.5 h-3.5" />
        </button>
        {error && <span className="text-[10px] text-red-400">{error}</span>}
      </div>
    </div>
  );
}

// ── API key row ─────────────────────────────────────────────────────

function ApiKeyRow({
  name,
  hasKey,
  source,
  onSet,
}: {
  name: string;
  hasKey: boolean;
  source: string | null;
  onSet: (value: string, scope: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [scope, setScope] = useState("user");
  const isMobile = useIsMobile();

  return (
    <div className="flex items-center gap-3 py-2 px-3 hover:bg-zinc-50/50 dark:hover:bg-zinc-900/50 rounded group">
      <span className="text-sm text-zinc-700 dark:text-zinc-300 w-28 shrink-0 capitalize">{name}</span>
      {editing ? (
        <div className={cn("flex-1", isMobile ? "space-y-2" : "flex items-center gap-2")}>
          <input
            type="password"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="sk-..."
            className={cn("text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300 font-mono", isMobile ? "w-full" : "flex-1")}
            autoFocus
          />
          <div className="flex items-center gap-2">
            <select
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              className="text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-400"
            >
              <option value="user">User</option>
              <option value="project">Project</option>
            </select>
            <button
              onClick={() => {
                if (value) onSet(value, scope);
                setEditing(false);
                setValue("");
              }}
              className="text-green-400 hover:text-green-300 p-1"
            >
              <Check className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => {
                setEditing(false);
                setValue("");
              }}
              className="text-zinc-500 hover:text-zinc-700 dark:text-zinc-300 p-1"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      ) : (
        <>
          <span className="flex-1 text-xs text-zinc-500 font-mono">
            {hasKey ? "••••••••" : "(not set)"}
          </span>
          <button
            onClick={() => setEditing(true)}
            className="text-[11px] text-zinc-600 hover:text-zinc-700 dark:text-zinc-300 hover-capable:opacity-0 hover-capable:group-hover:opacity-100 transition-opacity"
          >
            {hasKey ? "Update" : "Set"}
          </button>
          {source && <SourceBadge source={source} />}
        </>
      )}
    </div>
  );
}

// ── Model registry row ──────────────────────────────────────────────

function ModelRow({
  model,
  labelRefs,
  onDelete,
}: {
  model: ModelInfo;
  labelRefs: string[];
  onDelete: () => void;
}) {
  const isMobile = useIsMobile();

  if (isMobile) {
    return (
      <div className="py-1.5 px-3 hover:bg-zinc-50/50 dark:hover:bg-zinc-900/50 rounded group space-y-1">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-zinc-700 dark:text-zinc-300 truncate flex-1 min-w-0">{model.id}</span>
          {labelRefs.map((l) => (
            <span key={l} className="text-[10px] px-1 py-0.5 bg-violet-950 text-violet-400 rounded shrink-0">
              {l}
            </span>
          ))}
          <button
            onClick={onDelete}
            className="text-zinc-700 hover:text-red-400 hover-capable:opacity-0 hover-capable:group-hover:opacity-100 transition-opacity p-1 shrink-0"
          >
            <Trash2 className="w-3 h-3" />
          </button>
        </div>
        <div className="grid grid-cols-2 gap-x-3 text-[10px] text-zinc-500 font-mono">
          <span title="Context window">{formatTokenCount(model.context_length)} ctx</span>
          <span title="Max output">{formatTokenCount(model.max_output_tokens)} out</span>
          <span title="Input cost per 1M tokens">{formatCostPerMToken(model.prompt_cost)}/Mi</span>
          <span title="Output cost per 1M tokens">{formatCostPerMToken(model.completion_cost)}/Mo</span>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2 py-1.5 px-3 hover:bg-zinc-50/50 dark:hover:bg-zinc-900/50 rounded group">
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono text-zinc-700 dark:text-zinc-300 truncate">{model.id}</span>
          {labelRefs.map((l) => (
            <span key={l} className="text-[10px] px-1 py-0.5 bg-violet-950 text-violet-400 rounded shrink-0">
              {l}
            </span>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-3 shrink-0 text-[10px] text-zinc-500 font-mono">
        <span title="Context window">{formatTokenCount(model.context_length)} ctx</span>
        <span title="Max output">{formatTokenCount(model.max_output_tokens)} out</span>
        <span title="Input cost per 1M tokens">{formatCostPerMToken(model.prompt_cost)}/Mi</span>
        <span title="Output cost per 1M tokens">{formatCostPerMToken(model.completion_cost)}/Mo</span>
      </div>
      <button
        onClick={onDelete}
        className="text-zinc-700 hover:text-red-400 hover-capable:opacity-0 hover-capable:group-hover:opacity-100 transition-opacity p-1 shrink-0"
      >
        <Trash2 className="w-3 h-3" />
      </button>
    </div>
  );
}

// ── OpenRouter model search ─────────────────────────────────────────

function ModelSearch({
  existingIds,
  onAdd,
}: {
  existingIds: Set<string>;
  onAdd: (model: ModelInfo) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const { data: results, isLoading, isFetching } = useOpenRouterSearch(query);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 text-xs text-zinc-500 hover:text-zinc-700 dark:text-zinc-300 px-3 py-2"
      >
        <Search className="w-3 h-3" />
        Search OpenRouter Models
      </button>
    );
  }

  const filtered = results?.filter((m) => !existingIds.has(m.id)) ?? [];

  return (
    <div className="px-3 py-2 space-y-2">
      <div className="flex items-center gap-2">
        <div className="flex-1 relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-zinc-600" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search models (e.g. claude, gpt, gemini)..."
            className="w-full text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded pl-7 pr-2 py-1.5 text-zinc-700 dark:text-zinc-300 placeholder:text-zinc-600 focus:outline-none focus:border-blue-500"
          />
          {isFetching && (
            <Loader2 className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-zinc-600 animate-spin" />
          )}
        </div>
        <button
          onClick={() => {
            setOpen(false);
            setQuery("");
          }}
          className="text-zinc-500 hover:text-zinc-700 dark:text-zinc-300 p-1"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>

      {query.length >= 2 && (
        <div className="max-h-64 overflow-y-auto border border-zinc-200 dark:border-zinc-800 rounded bg-zinc-50/80 dark:bg-zinc-950/80">
          {isLoading ? (
            <div className="flex items-center justify-center py-4 text-xs text-zinc-600">
              <Loader2 className="w-3 h-3 animate-spin mr-1.5" />
              Searching...
            </div>
          ) : filtered.length === 0 ? (
            <div className="py-3 text-center text-xs text-zinc-600">
              {results?.length === 0 ? "No models found" : "All matches already in registry"}
            </div>
          ) : (
            filtered.map((m) => (
              <button
                key={m.id}
                onClick={() => onAdd(m)}
                className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-zinc-100/50 dark:hover:bg-zinc-800/50 border-b border-zinc-200/50 dark:border-zinc-800/50 last:border-b-0"
              >
                <div className="flex-1 min-w-0">
                  <div className="text-xs text-zinc-700 dark:text-zinc-300 truncate">{m.name}</div>
                  <div className="text-[10px] text-zinc-600 font-mono truncate">{m.id}</div>
                </div>
                <div className="flex items-center gap-2 shrink-0 text-[10px] text-zinc-500 font-mono">
                  <span>{formatTokenCount(m.context_length)}</span>
                  <span>{formatCostPerMToken(m.prompt_cost)}/Mi</span>
                  <span>{formatCostPerMToken(m.completion_cost)}/Mo</span>
                </div>
                <Plus className="w-3 h-3 text-zinc-600 shrink-0" />
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ── Section wrapper ─────────────────────────────────────────────────

function Section({
  title,
  icon: Icon,
  children,
  defaultOpen = true,
}: {
  title: string;
  icon: React.ElementType;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-zinc-200 dark:border-zinc-800 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 px-4 py-3 bg-zinc-50/50 dark:bg-zinc-950/50 hover:bg-zinc-100/50 dark:hover:bg-zinc-900/50 transition-colors"
      >
        <Icon className="w-4 h-4 text-zinc-500" />
        <span className="text-sm font-medium text-zinc-700 dark:text-zinc-300">{title}</span>
        <div className="flex-1" />
        <ChevronDown className={cn("w-4 h-4 text-zinc-600 transition-transform", !open && "-rotate-90")} />
      </button>
      {open && <div className="p-2">{children}</div>}
    </div>
  );
}

// ── Main page ───────────────────────────────────────────────────────

export function SettingsPage() {
  const { data: config, isLoading } = useConfig();
  const mutations = useConfigMutations();

  if (isLoading || !config) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500">
        Loading...
      </div>
    );
  }

  const defaultLabels = config.labels.filter((l) => l.is_default);
  const customLabels = config.labels.filter((l) => !l.is_default);

  // Map model IDs to which labels reference them
  const modelLabelMap: Record<string, string[]> = {};
  for (const label of config.labels) {
    if (!modelLabelMap[label.model]) modelLabelMap[label.model] = [];
    modelLabelMap[label.model].push(label.name);
  }

  const existingModelIds = new Set(config.model_registry.map((m) => m.id));

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-3xl mx-auto py-8 px-6 space-y-6">
        <h1 className="text-lg font-semibold text-foreground">Settings</h1>

        {/* Model Labels */}
        <Section title="Model Labels" icon={Tag}>
          <div className="space-y-1">
            <div className="px-3 py-1 text-[10px] font-medium text-zinc-600 uppercase tracking-wide">
              Default Labels
            </div>
            {defaultLabels.map((label) => (
              <LabelRow
                key={label.name}
                label={label}
                models={config.model_registry}
                onUpdate={(model) =>
                  mutations.updateLabel.mutate({ name: label.name, model })
                }
              />
            ))}
            <div className="px-3 py-1 mt-3 text-[10px] font-medium text-zinc-600 uppercase tracking-wide">
              Custom Labels
            </div>
            {customLabels.length === 0 && (
              <div className="px-3 py-1 text-xs text-zinc-600">No custom labels</div>
            )}
            {customLabels.map((label) => (
              <LabelRow
                key={label.name}
                label={label}
                models={config.model_registry}
                onUpdate={(model) =>
                  mutations.updateLabel.mutate({ name: label.name, model })
                }
                onDelete={() => mutations.deleteLabel.mutate(label.name)}
              />
            ))}
            <AddLabelForm
              models={config.model_registry}
              onAdd={(name, model) => mutations.createLabel.mutate({ name, model })}
            />
          </div>
        </Section>

        {/* API Keys */}
        <Section title="API Keys" icon={Key}>
          <div className="space-y-1">
            <ApiKeyRow
              name="OpenRouter"
              hasKey={config.has_api_key}
              source={config.api_key_source}
              onSet={(value, scope) =>
                mutations.setApiKey.mutate({ key: "openrouter", value, scope })
              }
            />
            <ApiKeyRow
              name="Anthropic"
              hasKey={config.has_anthropic_key}
              source={null}
              onSet={(value, scope) =>
                mutations.setApiKey.mutate({ key: "anthropic", value, scope })
              }
            />
          </div>
          <p className="text-[10px] text-zinc-600 px-3 mt-2">
            User = ~/.config/stepwise/ &nbsp;|&nbsp; Project = .stepwise/config.local.yaml (gitignored)
          </p>
        </Section>

        {/* Model Registry */}
        <Section title="Available Models" icon={Database}>
          <div className="space-y-0.5">
            {/* Column headers */}
            {config.model_registry.length > 0 && (
              <div className="hidden md:flex items-center gap-2 py-1 px-3 text-[10px] font-medium text-zinc-600 uppercase tracking-wide">
                <div className="flex-1">Model</div>
                <div className="flex items-center gap-3 shrink-0 font-mono">
                  <span className="w-12 text-right">Context</span>
                  <span className="w-12 text-right">Output</span>
                  <span className="w-14 text-right">In/1M</span>
                  <span className="w-14 text-right">Out/1M</span>
                </div>
                <div className="w-7" />
              </div>
            )}
            {config.model_registry.map((model) => (
              <ModelRow
                key={model.id}
                model={model}
                labelRefs={modelLabelMap[model.id] ?? []}
                onDelete={() => mutations.removeModel.mutate(model.id)}
              />
            ))}
            {config.model_registry.length === 0 && (
              <div className="px-3 py-2 text-xs text-zinc-600">
                No models in registry. Search OpenRouter to add models.
              </div>
            )}
            <ModelSearch
              existingIds={existingModelIds}
              onAdd={(model) => mutations.addModel.mutate(model)}
            />
          </div>
        </Section>

        {/* General */}
        <Section title="General" icon={Settings2}>
          <div className="space-y-2 px-3 py-2">
            <div className="flex items-center gap-3">
              <span className="text-xs text-zinc-500 w-28">Default Model</span>
              <select
                value={config.default_model}
                onChange={(e) => mutations.setDefaultModel.mutate(e.target.value)}
                className="text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300"
              >
                {config.labels.map((l) => (
                  <option key={l.name} value={l.name}>
                    {l.name} ({l.model})
                  </option>
                ))}
              </select>
              <span className="text-[10px] text-zinc-600">Used when step omits model:</span>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs text-zinc-500 w-28">Default Agent</span>
              <select
                value={config.default_agent}
                onChange={(e) => mutations.setDefaultAgent.mutate(e.target.value)}
                className="text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300"
              >
                <option value="claude">claude</option>
                <option value="codex">codex</option>
              </select>
              <span className="text-[10px] text-zinc-600">Used when agent step omits backend:</span>
            </div>
          </div>
        </Section>
      </div>
    </div>
  );
}
