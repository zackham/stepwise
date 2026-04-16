import { useState, useRef, useEffect } from "react";
import { useConfig, useConfigMutations, useOpenRouterSearch } from "@/hooks/useConfig";
import { useAgents, useAgentMutations } from "@/hooks/useAgents";
import { useIsMobile } from "@/hooks/useMediaQuery";
import type { LabelInfo, ModelInfo, AgentInfo, AgentConfigKey, AgentCapabilities } from "@/lib/api";
import {
  Tag,
  Key,
  Database,
  Settings2,
  Plus,
  Trash2,
  Check,
  X,
  Search,
  Loader2,
  Bot,
  Pencil,
  RotateCcw,
  Power,
  PowerOff,
  Shield,
  Gauge,
  Webhook,
} from "lucide-react";
import { cn } from "@/lib/utils";
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

// ── Types ──────────────────────────────────────────────────────────

type SectionId =
  | "general"
  | "agents"
  | "limits"
  | "containment"
  | "api-keys"
  | "models"
  | "labels";

interface NavItem {
  id: SectionId;
  label: string;
  icon: React.ElementType;
}

const NAV_ITEMS: NavItem[] = [
  { id: "general", label: "General", icon: Settings2 },
  { id: "agents", label: "Agents", icon: Bot },
  { id: "limits", label: "Limits", icon: Gauge },
  { id: "containment", label: "Containment", icon: Shield },
  { id: "api-keys", label: "API Keys", icon: Key },
  { id: "models", label: "Models", icon: Database },
  { id: "labels", label: "Labels", icon: Tag },
];

// ── Formatting helpers ──────────────────────────────────────────────

function formatTokenCount(n: number | undefined | null): string {
  if (n == null) return "\u2014";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n % 1_000 === 0 ? 0 : 1)}K`;
  return String(n);
}

function formatCostPerMToken(costPerToken: number | undefined | null): string {
  if (costPerToken == null) return "\u2014";
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

// ── Section header ──────────────────────────────────────────────────

function SectionHeader({ title, description }: { title: string; description?: string }) {
  return (
    <div className="mb-6">
      <h2 className="text-lg font-semibold text-zinc-100">{title}</h2>
      {description && (
        <p className="text-sm text-zinc-500 mt-1">{description}</p>
      )}
    </div>
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
          <span className="flex-1 text-xs text-zinc-500 dark:text-zinc-400 font-mono truncate">{label.model}</span>
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
        {error && <span className="text-[10px] text-red-500 dark:text-red-400">{error}</span>}
      </div>
    </div>
  );
}

// ── API key row ─────────────────────────────────────────────────────

function ApiKeyRow({
  name,
  envVar,
  hasKey,
  currentValue,
  source,
  onSet,
}: {
  name: string;
  envVar: string;
  hasKey: boolean;
  currentValue?: string;
  source: string | null;
  onSet: (value: string, scope: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [scope, setScope] = useState("user");
  const isMobile = useIsMobile();

  return (
    <div className="flex items-center gap-3 py-2 px-3 hover:bg-zinc-50/50 dark:hover:bg-zinc-900/50 rounded group">
      <div className="w-36 shrink-0">
        <span className="text-sm text-zinc-700 dark:text-zinc-300 capitalize">{name}</span>
        <span className="block text-[10px] font-mono text-zinc-500">{envVar}</span>
      </div>
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
              className="text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-500 dark:text-zinc-400"
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
          <span
            onClick={() => { setValue(currentValue || ""); setEditing(true); }}
            className="flex-1 text-xs text-zinc-500 font-mono truncate cursor-pointer hover:text-zinc-300 transition-colors"
            title={hasKey ? "Click to edit" : "Click to set"}
          >
            {currentValue || (hasKey ? "••••••••" : "(not set)")}
          </span>
          <button
            onClick={() => { setValue(currentValue || ""); setEditing(true); }}
            className="text-[11px] text-zinc-600 hover:text-zinc-700 dark:text-zinc-300 hover-capable:opacity-0 hover-capable:group-hover:opacity-100 transition-opacity shrink-0"
          >
            {hasKey ? "Edit" : "Set"}
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
            <span key={l} className="text-[10px] px-1 py-0.5 bg-violet-100 dark:bg-violet-950 text-violet-600 dark:text-violet-400 rounded shrink-0">
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
            <span key={l} className="text-[10px] px-1 py-0.5 bg-violet-100 dark:bg-violet-950 text-violet-600 dark:text-violet-400 rounded shrink-0">
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
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-zinc-500 dark:text-zinc-600" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search models (e.g. claude, gpt, gemini)..."
            className="w-full text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded pl-7 pr-2 py-1.5 text-zinc-700 dark:text-zinc-300 placeholder:text-zinc-400 dark:placeholder:text-zinc-600 focus:outline-none focus:border-blue-500"
          />
          {isFetching && (
            <Loader2 className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-zinc-500 dark:text-zinc-600 animate-spin" />
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
            <div className="flex items-center justify-center py-4 text-xs text-zinc-500 dark:text-zinc-600">
              <Loader2 className="w-3 h-3 animate-spin mr-1.5" />
              Searching...
            </div>
          ) : filtered.length === 0 ? (
            <div className="py-3 text-center text-xs text-zinc-500 dark:text-zinc-600">
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
                  <div className="text-[10px] text-zinc-500 dark:text-zinc-600 font-mono truncate">{m.id}</div>
                </div>
                <div className="flex items-center gap-2 shrink-0 text-[10px] text-zinc-500 font-mono">
                  <span>{formatTokenCount(m.context_length)}</span>
                  <span>{formatCostPerMToken(m.prompt_cost)}/Mi</span>
                  <span>{formatCostPerMToken(m.completion_cost)}/Mo</span>
                </div>
                <Plus className="w-3 h-3 text-zinc-500 dark:text-zinc-600 shrink-0" />
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// ── Agent config key helpers ────────────────────────────────────────

function getConfigValue(key: AgentConfigKey): string {
  if (key.default != null) return String(key.default);
  if (key.builtin_default != null) return String(key.builtin_default);
  return "";
}

function getDeliveryMechanism(key: AgentConfigKey): string {
  if (key.flag) return `--flag: ${key.flag}`;
  if (key.env) return `env: ${key.env}`;
  if (key.acp) return `acp: ${key.acp}`;
  return "none";
}

function getModelValue(config: Record<string, AgentConfigKey>): string | null {
  const modelKey = config.model;
  if (!modelKey) return null;
  return getConfigValue(modelKey) || null;
}

function getToolsList(config: Record<string, AgentConfigKey>): string[] {
  const toolsKey = config.tools;
  if (!toolsKey) return [];
  const val = toolsKey.default ?? toolsKey.builtin_default;
  if (Array.isArray(val)) return val.map(String);
  if (typeof val === "string" && val) return val.split(",").map((s) => s.trim());
  return [];
}

function getEnvKeyStatus(config: Record<string, AgentConfigKey>): Array<{ name: string; env: string }> {
  return Object.entries(config)
    .filter(([, v]) => v.env)
    .map(([name, v]) => ({ name, env: v.env! }));
}

// ── Agent config key editor ────────────────────────────────────────

interface ConfigKeyEditorRowProps {
  keyName: string;
  keyConfig: AgentConfigKey;
  isBuiltin: boolean;
  onChange: (name: string, config: AgentConfigKey) => void;
  onRemove: (name: string) => void;
}

function ConfigKeyEditorRow({ keyName, keyConfig, isBuiltin, onChange, onRemove }: ConfigKeyEditorRowProps) {
  const isMobile = useIsMobile();
  return (
    <div className="py-2 px-2 rounded hover:bg-zinc-50/50 dark:hover:bg-zinc-900/50">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-mono text-zinc-700 dark:text-zinc-300 w-28 shrink-0">{keyName}</span>
        <select
          value={keyConfig.flag ? "flag" : keyConfig.env ? "env" : keyConfig.acp ? "acp" : "flag"}
          onChange={(e) => {
            const mechanism = e.target.value;
            const newKey: AgentConfigKey = { ...keyConfig };
            delete newKey.flag;
            delete newKey.env;
            delete newKey.acp;
            if (mechanism === "flag") newKey.flag = keyConfig.flag || keyConfig.env || keyConfig.acp || `--${keyName}`;
            else if (mechanism === "env") newKey.env = keyConfig.env || keyConfig.flag || `STEPWISE_${keyName.toUpperCase()}`;
            else if (mechanism === "acp") newKey.acp = keyConfig.acp || keyName;
            onChange(keyName, newKey);
          }}
          className="text-[11px] bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-1.5 py-0.5 text-zinc-600 dark:text-zinc-400 w-16"
        >
          <option value="flag">flag</option>
          <option value="env">env</option>
          <option value="acp">acp</option>
        </select>
        <input
          value={keyConfig.flag || keyConfig.env || keyConfig.acp || ""}
          onChange={(e) => {
            const newKey: AgentConfigKey = { ...keyConfig };
            if (newKey.flag != null) newKey.flag = e.target.value;
            else if (newKey.env != null) newKey.env = e.target.value;
            else if (newKey.acp != null) newKey.acp = e.target.value;
            onChange(keyName, newKey);
          }}
          placeholder="delivery target"
          className="flex-1 min-w-0 text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300 font-mono"
        />
        <input
          value={keyConfig.default != null ? String(keyConfig.default) : ""}
          onChange={(e) => {
            onChange(keyName, { ...keyConfig, default: e.target.value || undefined });
          }}
          placeholder="default"
          className="w-32 text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300 font-mono"
        />
        {isBuiltin && keyConfig.builtin_default != null && (
          <span className="text-[10px] text-zinc-500 dark:text-zinc-600 shrink-0" title="Builtin default">
            ({String(keyConfig.builtin_default)})
          </span>
        )}
        <label className="flex items-center gap-1 text-[10px] text-zinc-500 dark:text-zinc-600 shrink-0">
          <input
            type="checkbox"
            checked={keyConfig.required ?? false}
            onChange={(e) => onChange(keyName, { ...keyConfig, required: e.target.checked })}
            className="w-3 h-3"
          />
          req
        </label>
        {!isBuiltin && (
          <button
            onClick={() => onRemove(keyName)}
            className="text-zinc-500 hover:text-red-400 p-0.5 shrink-0"
          >
            <Trash2 className="w-3 h-3" />
          </button>
        )}
      </div>
    </div>
  );
}

// ── Agent edit dialog ──────────────────────────────────────────────

interface AgentEditDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agent: AgentInfo;
  onSave: (agent: Partial<AgentInfo>) => void;
  isPending: boolean;
}

function AgentEditDialog({ open, onOpenChange, agent, onSave, isPending }: AgentEditDialogProps) {
  const [config, setConfig] = useState<Record<string, AgentConfigKey>>({});
  const [command, setCommand] = useState("");
  const [newKeyName, setNewKeyName] = useState("");

  useEffect(() => {
    if (open) {
      setConfig({ ...agent.config });
      setCommand(agent.command.join(" "));
      setNewKeyName("");
    }
  }, [open, agent]);

  const handleConfigChange = (name: string, keyConfig: AgentConfigKey) => {
    setConfig((prev) => ({ ...prev, [name]: keyConfig }));
  };

  const handleConfigRemove = (name: string) => {
    setConfig((prev) => {
      const next = { ...prev };
      delete next[name];
      return next;
    });
  };

  const handleAddKey = () => {
    const trimmed = newKeyName.trim().toLowerCase().replace(/[^a-z0-9_-]/g, "_");
    if (!trimmed || config[trimmed]) return;
    setConfig((prev) => ({
      ...prev,
      [trimmed]: { flag: `--${trimmed}` },
    }));
    setNewKeyName("");
  };

  const handleSave = () => {
    const payload: Partial<AgentInfo> = { config };
    if (!agent.is_builtin) {
      payload.command = command.split(/\s+/).filter(Boolean);
    }
    onSave(payload);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            Edit Agent: {agent.name}
            {agent.is_builtin && (
              <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-950 text-blue-600 dark:text-blue-400 font-normal">
                builtin
              </span>
            )}
          </DialogTitle>
          <DialogDescription>
            {agent.is_builtin
              ? "Override configuration for this builtin agent. Only config keys can be modified."
              : "Edit command and configuration for this custom agent."}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Command (custom agents only) */}
          {!agent.is_builtin && (
            <div className="space-y-1.5">
              <Label className="text-xs">Command</Label>
              <Input
                value={command}
                onChange={(e) => setCommand(e.target.value)}
                placeholder="e.g. python -m my_agent"
                className="text-xs font-mono bg-zinc-50 dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700"
              />
              <p className="text-[10px] text-zinc-500 dark:text-zinc-600">
                Space-separated command and arguments
              </p>
            </div>
          )}

          {/* Config keys */}
          <div className="space-y-1.5">
            <Label className="text-xs">Configuration Keys</Label>
            <div className="border border-zinc-200 dark:border-zinc-800 rounded-lg divide-y divide-zinc-200/50 dark:divide-zinc-800/50">
              {Object.entries(config).length === 0 && (
                <div className="px-3 py-2 text-xs text-zinc-500 dark:text-zinc-600">
                  No configuration keys
                </div>
              )}
              {Object.entries(config).map(([name, keyConfig]) => (
                <ConfigKeyEditorRow
                  key={name}
                  keyName={name}
                  keyConfig={keyConfig}
                  isBuiltin={agent.is_builtin}
                  onChange={handleConfigChange}
                  onRemove={handleConfigRemove}
                />
              ))}
            </div>

            {/* Add key row */}
            {!agent.is_builtin && (
              <div className="flex items-center gap-2 px-2">
                <input
                  value={newKeyName}
                  onChange={(e) => setNewKeyName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleAddKey();
                  }}
                  placeholder="new_key_name"
                  className="text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300 font-mono w-36"
                />
                <button
                  onClick={handleAddKey}
                  disabled={!newKeyName.trim()}
                  className="flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200 disabled:opacity-40"
                >
                  <Plus className="w-3 h-3" />
                  Add Key
                </button>
              </div>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={isPending}>
            {isPending ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Add agent dialog ───────────────────────────────────────────────

interface AddAgentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (agent: Partial<AgentInfo>) => void;
  isPending: boolean;
  existingNames: Set<string>;
}

function AddAgentDialog({ open, onOpenChange, onCreate, isPending, existingNames }: AddAgentDialogProps) {
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [config, setConfig] = useState<Record<string, AgentConfigKey>>({
    model: { flag: "--model", default: "default" },
  });
  const [capabilities, setCapabilities] = useState<AgentCapabilities>({
    fork: false,
    resume: false,
    sessions: false,
    modes: false,
    multi_session: false,
  });
  const [newKeyName, setNewKeyName] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setName("");
      setCommand("");
      setConfig({ model: { flag: "--model", default: "default" } });
      setCapabilities({ fork: false, resume: false, sessions: false, modes: false, multi_session: false });
      setNewKeyName("");
      setError("");
    }
  }, [open]);

  const handleConfigChange = (keyName: string, keyConfig: AgentConfigKey) => {
    setConfig((prev) => ({ ...prev, [keyName]: keyConfig }));
  };

  const handleConfigRemove = (keyName: string) => {
    setConfig((prev) => {
      const next = { ...prev };
      delete next[keyName];
      return next;
    });
  };

  const handleAddKey = () => {
    const trimmed = newKeyName.trim().toLowerCase().replace(/[^a-z0-9_-]/g, "_");
    if (!trimmed || config[trimmed]) return;
    setConfig((prev) => ({
      ...prev,
      [trimmed]: { flag: `--${trimmed}` },
    }));
    setNewKeyName("");
  };

  const handleCreate = () => {
    const trimmed = name.trim().toLowerCase();
    if (!/^[a-z][a-z0-9-]*$/.test(trimmed)) {
      setError("Name must be lowercase, start with a letter, and contain only letters, digits, and hyphens");
      return;
    }
    if (existingNames.has(trimmed)) {
      setError("An agent with this name already exists");
      return;
    }
    if (!command.trim()) {
      setError("Command is required");
      return;
    }

    onCreate({
      name: trimmed,
      command: command.split(/\s+/).filter(Boolean),
      config,
      capabilities,
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg max-h-[80vh] overflow-hidden flex flex-col">
        <DialogHeader className="px-6 pt-6 pb-2 shrink-0">
          <DialogTitle>Add Custom Agent</DialogTitle>
          <DialogDescription>
            Register a new agent backend for use in flows.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 px-6 pb-6 overflow-y-auto flex-1 min-h-0">
          {/* Name */}
          <div className="space-y-1.5">
            <Label className="text-xs">Name</Label>
            <Input
              autoFocus
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setError("");
              }}
              placeholder="my-agent"
              className="text-xs font-mono bg-zinc-50 dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700"
            />
          </div>

          {/* Command */}
          <div className="space-y-1.5">
            <Label className="text-xs">Command</Label>
            <Input
              value={command}
              onChange={(e) => {
                setCommand(e.target.value);
                setError("");
              }}
              placeholder="python -m my_agent"
              className="text-xs font-mono bg-zinc-50 dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700"
            />
            <p className="text-[10px] text-zinc-500 dark:text-zinc-600">
              Space-separated command and arguments
            </p>
          </div>

          {/* Config keys */}
          <div className="space-y-1.5">
            <Label className="text-xs">Configuration Keys</Label>
            <div className="border border-zinc-200 dark:border-zinc-800 rounded-lg divide-y divide-zinc-200/50 dark:divide-zinc-800/50">
              {Object.entries(config).length === 0 && (
                <div className="px-3 py-2 text-xs text-zinc-500 dark:text-zinc-600">
                  No configuration keys
                </div>
              )}
              {Object.entries(config).map(([keyName, keyConfig]) => (
                <ConfigKeyEditorRow
                  key={keyName}
                  keyName={keyName}
                  keyConfig={keyConfig}
                  isBuiltin={false}
                  onChange={handleConfigChange}
                  onRemove={handleConfigRemove}
                />
              ))}
            </div>
            <div className="flex items-center gap-2 px-2">
              <input
                value={newKeyName}
                onChange={(e) => setNewKeyName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleAddKey();
                }}
                placeholder="new_key_name"
                className="text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300 font-mono w-36"
              />
              <button
                onClick={handleAddKey}
                disabled={!newKeyName.trim()}
                className="flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200 disabled:opacity-40"
              >
                <Plus className="w-3 h-3" />
                Add Key
              </button>
            </div>
          </div>

          {/* Capabilities */}
          <div className="space-y-1.5">
            <Label className="text-xs">Capabilities</Label>
            <div className="grid grid-cols-2 gap-2 px-2">
              {(["fork", "resume", "sessions", "multi_session"] as const).map((cap) => (
                <label key={cap} className="flex items-center gap-2 text-xs text-zinc-700 dark:text-zinc-300">
                  <input
                    type="checkbox"
                    checked={capabilities[cap]}
                    onChange={(e) =>
                      setCapabilities((prev) => ({ ...prev, [cap]: e.target.checked }))
                    }
                    className="w-3.5 h-3.5"
                  />
                  {cap.replace(/_/g, " ")}
                </label>
              ))}
            </div>
          </div>

          {error && (
            <p className="text-xs text-red-500 dark:text-red-400">{error}</p>
          )}
        </div>

        <DialogFooter className="px-6 pb-6 pt-2 shrink-0">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleCreate}
            disabled={!name.trim() || !command.trim() || isPending}
          >
            {isPending ? "Creating..." : "Create Agent"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Agent card ─────────────────────────────────────────────────────

function AgentCard({
  agent,
  onEdit,
  onToggle,
  onDelete,
  onReset,
}: {
  agent: AgentInfo;
  onEdit: () => void;
  onToggle: () => void;
  onDelete?: () => void;
  onReset?: () => void;
}) {
  const isMobile = useIsMobile();
  const model = getModelValue(agent.config);
  const tools = getToolsList(agent.config);
  const envKeys = getEnvKeyStatus(agent.config);

  return (
    <div
      className={cn(
        "px-3 py-2.5 rounded-lg hover:bg-zinc-50/50 dark:hover:bg-zinc-900/50 group transition-colors",
        agent.is_disabled && "opacity-60"
      )}
    >
      {/* Header row */}
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
          {agent.name}
        </span>
        {agent.is_builtin && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-100 dark:bg-blue-950 text-blue-600 dark:text-blue-400">
            builtin
          </span>
        )}
        {agent.is_disabled && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-950 text-red-600 dark:text-red-400">
            disabled
          </span>
        )}
        {agent.has_overrides && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-950 text-amber-600 dark:text-amber-400">
            overridden
          </span>
        )}
        {agent.containment && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-950 text-green-600 dark:text-green-400 flex items-center gap-0.5">
            <Shield className="w-2.5 h-2.5" />
            {agent.containment}
          </span>
        )}
        <div className="flex-1" />

        {/* Action buttons */}
        <div className={cn(
          "flex items-center gap-1",
          !isMobile && "hover-capable:opacity-0 hover-capable:group-hover:opacity-100 transition-opacity"
        )}>
          <button
            onClick={onEdit}
            className="text-zinc-500 hover:text-zinc-700 dark:text-zinc-400 dark:hover:text-zinc-200 p-1"
            title="Edit"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={onToggle}
            className={cn(
              "p-1",
              agent.is_disabled
                ? "text-green-500 hover:text-green-400"
                : "text-zinc-500 hover:text-amber-500"
            )}
            title={agent.is_disabled ? "Enable" : "Disable"}
          >
            {agent.is_disabled ? (
              <Power className="w-3.5 h-3.5" />
            ) : (
              <PowerOff className="w-3.5 h-3.5" />
            )}
          </button>
          {onReset && (
            <button
              onClick={onReset}
              className="text-zinc-500 hover:text-blue-500 p-1"
              title="Reset to defaults"
            >
              <RotateCcw className="w-3.5 h-3.5" />
            </button>
          )}
          {onDelete && (
            <button
              onClick={onDelete}
              className="text-zinc-500 hover:text-red-400 p-1"
              title="Delete"
            >
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Command */}
      <div className="text-[11px] font-mono text-zinc-500 dark:text-zinc-600 truncate mb-1">
        {agent.command.join(" ")}
      </div>

      {/* Config summary */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-zinc-500 dark:text-zinc-500">
        {model && (
          <span>
            model: <span className="font-mono text-zinc-600 dark:text-zinc-400">{model}</span>
          </span>
        )}
        {tools.length > 0 && (
          <span>
            tools: <span className="font-mono text-zinc-600 dark:text-zinc-400">{tools.length > 3 ? `${tools.slice(0, 3).join(", ")}...` : tools.join(", ")}</span>
          </span>
        )}
        {envKeys.length > 0 && envKeys.map((ek) => (
          <span key={ek.name} className="flex items-center gap-1">
            <Key className="w-2.5 h-2.5" />
            <span className="font-mono">{ek.env}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

// ── Agents section ──────────────────────────────────────────────────

const PERMISSIONS_OPTIONS: Array<{
  value: string;
  label: string;
  description: string;
}> = [
  {
    value: "approve_all",
    label: "Approve all",
    description:
      "Every tool call runs without prompting. Recommended for local dev on a trusted project. This is the engine's current behavior today.",
  },
  {
    value: "prompt",
    label: "Prompt on write",
    description:
      "Intended to pause on write/exec tool calls and ask for approval. Defined in config but not yet enforced by the engine — setting this persists the policy so the future enforcer can pick it up.",
  },
  {
    value: "deny",
    label: "Deny side effects",
    description:
      "Intended to reject any tool call with a side effect. Defined in config but not yet enforced by the engine.",
  },
];

function AgentsSection({
  config,
  mutations,
}: {
  config: NonNullable<ReturnType<typeof useConfig>["data"]>;
  mutations: ReturnType<typeof useConfigMutations>;
}) {
  const { data: agents, isLoading, isError } = useAgents();
  const agentMutations = useAgentMutations();
  const [editAgent, setEditAgent] = useState<AgentInfo | null>(null);
  const [showAddDialog, setShowAddDialog] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null);

  const currentPerms = config.agent_permissions ?? "approve_all";
  const currentPermDesc =
    PERMISSIONS_OPTIONS.find((p) => p.value === currentPerms)?.description ?? "";
  const permsNotEnforced = currentPerms !== "approve_all";

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-6 text-xs text-zinc-500">
        <Loader2 className="w-3 h-3 animate-spin mr-1.5" />
        Loading agents...
      </div>
    );
  }

  if (isError || !agents) {
    return (
      <div className="px-3 py-4 text-xs text-zinc-500 dark:text-zinc-600">
        Failed to load agents. The backend may not support this endpoint yet.
      </div>
    );
  }

  const builtinAgents = agents.filter((a) => a.is_builtin);
  const customAgents = agents.filter((a) => !a.is_builtin);
  const existingNames = new Set(agents.map((a) => a.name));

  return (
    <div className="space-y-1">
      {/* ── Approval policy ─────────────────────────────────────────── */}
      <div className="border border-zinc-200 dark:border-zinc-800 rounded-lg p-3 mb-4">
        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0">
            <div className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Approval policy
            </div>
            <p className="text-[11px] text-zinc-500 dark:text-zinc-500 mt-0.5">
              {currentPermDesc}
            </p>
            {permsNotEnforced && (
              <p className="text-[10px] text-amber-600 dark:text-amber-400 mt-1.5 flex items-start gap-1">
                <span className="font-mono">⚠</span>
                <span>
                  Not yet enforced by the engine. The policy is persisted to
                  <code className="text-[10px] bg-zinc-100 dark:bg-zinc-900 px-1 rounded mx-1">.stepwise/config.local.yaml</code>
                  and will take effect when the approval gate ships.
                </span>
              </p>
            )}
          </div>
          <select
            value={currentPerms}
            onChange={(e) => mutations.setAgentPermissions.mutate(e.target.value)}
            disabled={mutations.setAgentPermissions.isPending}
            className="text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1.5 text-zinc-700 dark:text-zinc-300 min-w-[10rem] shrink-0"
          >
            {PERMISSIONS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Builtin agents */}
      <div className="px-3 py-1 text-[10px] font-medium text-zinc-500 dark:text-zinc-600 uppercase tracking-wide">
        Builtin Agents
      </div>
      {builtinAgents.length === 0 && (
        <div className="px-3 py-1 text-xs text-zinc-500 dark:text-zinc-600">No builtin agents</div>
      )}
      {builtinAgents.map((agent) => (
        <AgentCard
          key={agent.name}
          agent={agent}
          onEdit={() => setEditAgent(agent)}
          onToggle={() =>
            agent.is_disabled
              ? agentMutations.enableAgent.mutate(agent.name)
              : agentMutations.disableAgent.mutate(agent.name)
          }
          onReset={agent.has_overrides ? () => agentMutations.resetAgent.mutate(agent.name) : undefined}
        />
      ))}

      {/* Custom agents */}
      <div className="px-3 py-1 mt-3 text-[10px] font-medium text-zinc-500 dark:text-zinc-600 uppercase tracking-wide">
        Custom Agents
      </div>
      {customAgents.length === 0 && (
        <div className="px-3 py-1 text-xs text-zinc-500 dark:text-zinc-600">No custom agents</div>
      )}
      {customAgents.map((agent) => (
        <AgentCard
          key={agent.name}
          agent={agent}
          onEdit={() => setEditAgent(agent)}
          onToggle={() =>
            agent.is_disabled
              ? agentMutations.enableAgent.mutate(agent.name)
              : agentMutations.disableAgent.mutate(agent.name)
          }
          onDelete={() => setDeleteConfirm(agent.name)}
        />
      ))}

      {/* Add agent button */}
      <button
        onClick={() => setShowAddDialog(true)}
        className="flex items-center gap-1.5 text-xs text-zinc-500 hover:text-zinc-700 dark:text-zinc-300 px-3 py-2"
      >
        <Plus className="w-3 h-3" />
        Add Agent
      </button>

      {/* Edit dialog */}
      {editAgent && (
        <AgentEditDialog
          open={!!editAgent}
          onOpenChange={(open) => {
            if (!open) setEditAgent(null);
          }}
          agent={editAgent}
          onSave={(payload) => {
            agentMutations.updateAgent.mutate(
              { name: editAgent.name, agent: payload },
              { onSuccess: () => setEditAgent(null) }
            );
          }}
          isPending={agentMutations.updateAgent.isPending}
        />
      )}

      {/* Add dialog */}
      <AddAgentDialog
        open={showAddDialog}
        onOpenChange={setShowAddDialog}
        existingNames={existingNames}
        onCreate={(agent) => {
          agentMutations.createAgent.mutate(agent, {
            onSuccess: () => setShowAddDialog(false),
          });
        }}
        isPending={agentMutations.createAgent.isPending}
      />

      {/* Delete confirmation */}
      {deleteConfirm && (
        <Dialog
          open={!!deleteConfirm}
          onOpenChange={(open) => {
            if (!open) setDeleteConfirm(null);
          }}
        >
          <DialogContent className="sm:max-w-xs">
            <DialogHeader>
              <DialogTitle>Delete Agent</DialogTitle>
              <DialogDescription>
                Are you sure you want to delete the agent "{deleteConfirm}"? This cannot be undone.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="outline" onClick={() => setDeleteConfirm(null)}>
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={() => {
                  agentMutations.deleteAgent.mutate(deleteConfirm, {
                    onSuccess: () => setDeleteConfirm(null),
                  });
                }}
                disabled={agentMutations.deleteAgent.isPending}
              >
                {agentMutations.deleteAgent.isPending ? "Deleting..." : "Delete"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}

// ── Containment section ─────────────────────────────────────────────

function ContainmentSection({
  config,
  agents,
  mutations,
}: {
  config: NonNullable<ReturnType<typeof useConfig>["data"]>;
  agents: AgentInfo[] | undefined;
  mutations: ReturnType<typeof useConfigMutations>;
}) {
  const agentMutations = useAgentMutations();
  const projectDefault = config.agent_containment ?? null;

  // Effective containment for an agent = explicit per-agent override
  // when set, otherwise the project-wide default.
  function effectiveFor(agent: AgentInfo): { value: string | null; source: string } {
    if (agent.containment) return { value: agent.containment, source: "agent" };
    if (projectDefault) return { value: projectDefault, source: "project" };
    return { value: null, source: "default" };
  }

  return (
    <div>
      <SectionHeader
        title="Containment"
        description={
          "Hardware-isolated agent execution via Cloud-Hypervisor microVMs. " +
          "Set a project-wide default below, then pin specific agents if needed. " +
          "Override chain at run time: CLI flag > flow YAML > step YAML > agent override > project default. " +
          "Concurrency caps used to live here — they've moved to the Limits section."
        }
      />

      <div className="space-y-6">
        {/* ── Project-wide default ───────────────────────────────────── */}
        <div className="border border-zinc-200 dark:border-zinc-800 rounded-lg p-4">
          <div className="flex items-start gap-3">
            <div className="flex-1">
              <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                Project default
              </div>
              <p className="text-xs text-zinc-500 dark:text-zinc-500 mt-0.5">
                Applied to every agent step in this project unless overridden.
                Stored in <code className="text-[10px] bg-zinc-100 dark:bg-zinc-900 px-1 rounded">.stepwise/config.local.yaml</code>.
              </p>
            </div>
            <select
              value={projectDefault ?? ""}
              onChange={(e) => {
                const v = e.target.value === "" ? null : e.target.value;
                mutations.setAgentContainmentDefault.mutate(v);
              }}
              disabled={mutations.setAgentContainmentDefault.isPending}
              className="text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1.5 text-zinc-700 dark:text-zinc-300 min-w-[12rem]"
            >
              <option value="">None (no containment)</option>
              <option value="cloud-hypervisor">cloud-hypervisor</option>
            </select>
          </div>
          <div className="mt-3 text-[11px] text-zinc-500 dark:text-zinc-500 leading-relaxed">
            <Shield className="w-3 h-3 inline-block mr-1 -mt-0.5" />
            <span>
              Containment requires a built rootfs and the vmmd daemon.
              Run <code className="text-[10px] bg-zinc-100 dark:bg-zinc-900 px-1 rounded">stepwise doctor --containment</code> to check
              prerequisites, then <code className="text-[10px] bg-zinc-100 dark:bg-zinc-900 px-1 rounded">stepwise build-rootfs</code> and{" "}
              <code className="text-[10px] bg-zinc-100 dark:bg-zinc-900 px-1 rounded">sudo stepwise vmmd start --detach</code>.
            </span>
          </div>
        </div>

        {/* ── Per-agent table: containment only ───────────────────────── */}
        <div className="border border-zinc-200 dark:border-zinc-800 rounded-lg overflow-hidden">
          <div className="px-3 py-2 bg-zinc-50 dark:bg-zinc-900 border-b border-zinc-200 dark:border-zinc-800">
            <div className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Per-agent overrides
            </div>
            <p className="text-[11px] text-zinc-500 dark:text-zinc-500 mt-0.5">
              Pin a specific agent to a containment mode regardless of the
              project default. Use <code className="text-[10px] bg-zinc-100 dark:bg-zinc-900 px-1 rounded">(use project default)</code> to
              clear an override.
            </p>
          </div>

          {!agents || agents.length === 0 ? (
            <div className="px-3 py-4 text-xs text-zinc-500 dark:text-zinc-600">
              No agents registered.
            </div>
          ) : (
            <div>
              <div className="px-3 py-1.5 border-b border-zinc-200/50 dark:border-zinc-800/50 grid grid-cols-[1fr_auto_auto] items-center gap-3 text-[10px] uppercase tracking-wide font-medium text-zinc-500 dark:text-zinc-600">
                <div>Agent</div>
                <div>Effective</div>
                <div className="min-w-[10rem] text-left">Override</div>
              </div>
              <div className="divide-y divide-zinc-200/50 dark:divide-zinc-800/50">
                {agents.map((agent) => {
                  const effective = effectiveFor(agent);
                  const overrideValue = agent.containment ?? "";
                  return (
                    <div
                      key={agent.name}
                      className="grid grid-cols-[1fr_auto_auto] items-center gap-3 px-3 py-2"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <Bot className="w-3.5 h-3.5 text-zinc-500 dark:text-zinc-500 shrink-0" />
                        <span className="text-xs font-mono text-zinc-700 dark:text-zinc-300 truncate">
                          {agent.name}
                        </span>
                        {agent.is_disabled && (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-red-100 dark:bg-red-950 text-red-600 dark:text-red-400">
                            disabled
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        {effective.value ? (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-100 dark:bg-green-950 text-green-600 dark:text-green-400 flex items-center gap-0.5">
                            <Shield className="w-2.5 h-2.5" />
                            {effective.value}
                          </span>
                        ) : (
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-900 text-zinc-500 dark:text-zinc-500">
                            none
                          </span>
                        )}
                        <SourceBadge source={effective.source} />
                      </div>
                      <select
                        value={overrideValue}
                        onChange={(e) => {
                          const v = e.target.value === "" ? null : e.target.value;
                          agentMutations.setAgentContainment.mutate({
                            name: agent.name,
                            containment: v,
                          });
                        }}
                        disabled={agentMutations.setAgentContainment.isPending}
                        className="text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300 min-w-[10rem]"
                      >
                        <option value="">(use project default)</option>
                        <option value="cloud-hypervisor">cloud-hypervisor</option>
                      </select>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Limits section ──────────────────────────────────────────────────
//
// Concurrency + throttling controls, pulled out of Containment (where
// they were conceptually mis-grouped) into one "don't melt my laptop"
// tab. Order of presentation matches the order the engine evaluates:
//   1. max_concurrent_jobs   — overall job-queue cap
//   2. per-executor-type     — e.g. "at most 3 agent steps across the whole engine"
//   3. per-agent-name        — e.g. "at most 2 claude steps"
//   4. agent_process_ttl     — safety net for zombie subprocesses
// A step is throttled if ANY applicable cap is at capacity.

const KNOWN_EXECUTOR_TYPES = ["agent", "llm", "script", "external", "poll"];

function NumericLimitRow({
  label,
  description,
  value,
  placeholder,
  running,
  onChange,
  disabled,
  min = 0,
  max = 9999,
}: {
  label: React.ReactNode;
  description?: React.ReactNode;
  value: number;
  placeholder?: string;
  running?: number;
  onChange: (n: number) => void;
  disabled?: boolean;
  min?: number;
  max?: number;
}) {
  const [local, setLocal] = useState(String(value || ""));
  useEffect(() => {
    setLocal(String(value || ""));
  }, [value]);

  return (
    <div className="flex items-start gap-3 px-3 py-2">
      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
          {label}
        </div>
        {description && (
          <p className="text-[11px] text-zinc-500 dark:text-zinc-500 mt-0.5">
            {description}
          </p>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {running !== undefined && value > 0 && (
          <span className="text-[10px] text-zinc-500 dark:text-zinc-600 font-mono">
            {running}/{value}
          </span>
        )}
        <input
          type="number"
          min={min}
          max={max}
          value={local}
          placeholder={placeholder ?? "∞"}
          onChange={(e) => setLocal(e.target.value)}
          onBlur={() => {
            const n = parseInt(local || "0", 10);
            if (Number.isNaN(n) || n < 0) {
              setLocal(String(value || ""));
              return;
            }
            if (n !== value) onChange(n);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          }}
          disabled={disabled}
          className="text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1 text-zinc-700 dark:text-zinc-300 w-20 text-right font-mono"
        />
      </div>
    </div>
  );
}

function LimitsSection({
  config,
  agents,
  mutations,
}: {
  config: NonNullable<ReturnType<typeof useConfig>["data"]>;
  agents: AgentInfo[] | undefined;
  mutations: ReturnType<typeof useConfigMutations>;
}) {
  const executorLimits = config.concurrency_limits ?? {};
  const executorRunning = config.concurrency_running ?? {};
  const agentLimits = config.agent_concurrency_limits ?? {};
  const agentRunning = config.agent_concurrency_running ?? {};

  return (
    <div>
      <SectionHeader
        title="Limits"
        description={
          "Concurrency caps and process safety nets. A step is throttled if " +
          "any applicable cap is at capacity. Caps compose: the per-agent cap " +
          "is AND'd with the per-executor-type cap, which is AND'd with the " +
          "global job cap. Retries and per-step timeouts live in your FLOW.yaml, " +
          "not here."
        }
      />

      <div className="space-y-6">
        {/* ── Global caps ────────────────────────────────────────────── */}
        <div className="border border-zinc-200 dark:border-zinc-800 rounded-lg overflow-hidden">
          <div className="px-3 py-2 bg-zinc-50 dark:bg-zinc-900 border-b border-zinc-200 dark:border-zinc-800">
            <div className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Global
            </div>
          </div>
          <div className="divide-y divide-zinc-200/50 dark:divide-zinc-800/50">
            <NumericLimitRow
              label="Max concurrent jobs"
              description="Jobs that can be RUNNING at once across the whole engine. 0 = default (10)."
              value={config.max_concurrent_jobs ?? 10}
              onChange={(n) => mutations.setMaxConcurrentJobs.mutate(n)}
              disabled={mutations.setMaxConcurrentJobs.isPending}
              placeholder="10"
            />
            <NumericLimitRow
              label="Agent subprocess TTL (seconds)"
              description="Zombie-reaper safety net. Agent subprocesses older than this are killed during the reap sweep. 0 = disabled."
              value={config.agent_process_ttl ?? 0}
              onChange={(n) => mutations.setAgentProcessTtl.mutate(n)}
              disabled={mutations.setAgentProcessTtl.isPending}
              placeholder="off"
            />
          </div>
        </div>

        {/* ── Per-executor-type caps ─────────────────────────────────── */}
        <div className="border border-zinc-200 dark:border-zinc-800 rounded-lg overflow-hidden">
          <div className="px-3 py-2 bg-zinc-50 dark:bg-zinc-900 border-b border-zinc-200 dark:border-zinc-800">
            <div className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Per-executor-type
            </div>
            <p className="text-[11px] text-zinc-500 dark:text-zinc-500 mt-0.5">
              Cap simultaneous steps of each executor type across all jobs.
              Useful for protecting a rate-limited subscription model
              (<code className="text-[10px] bg-zinc-100 dark:bg-zinc-900 px-1 rounded">agent</code>)
              or a flaky local process pool. 0 / empty = no cap.
            </p>
          </div>
          <div className="divide-y divide-zinc-200/50 dark:divide-zinc-800/50">
            {KNOWN_EXECUTOR_TYPES.map((type) => (
              <NumericLimitRow
                key={type}
                label={<span className="font-mono">{type}</span>}
                value={executorLimits[type] ?? 0}
                running={executorRunning[type] ?? 0}
                onChange={(n) =>
                  mutations.setExecutorConcurrencyLimit.mutate({
                    executor_type: type,
                    limit: n,
                  })
                }
                disabled={mutations.setExecutorConcurrencyLimit.isPending}
              />
            ))}
          </div>
        </div>

        {/* ── Per-agent-name caps ────────────────────────────────────── */}
        <div className="border border-zinc-200 dark:border-zinc-800 rounded-lg overflow-hidden">
          <div className="px-3 py-2 bg-zinc-50 dark:bg-zinc-900 border-b border-zinc-200 dark:border-zinc-800">
            <div className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
              Per-agent-name
            </div>
            <p className="text-[11px] text-zinc-500 dark:text-zinc-500 mt-0.5">
              Cap simultaneous steps for a specific agent backend by name.
              Tighter than the per-executor-type cap — useful for
              <code className="text-[10px] bg-zinc-100 dark:bg-zinc-900 px-1 mx-1 rounded">max 2 claude</code> alongside
              <code className="text-[10px] bg-zinc-100 dark:bg-zinc-900 px-1 mx-1 rounded">max 5 codex</code>.
              0 = no per-agent cap.
            </p>
          </div>
          {!agents || agents.length === 0 ? (
            <div className="px-3 py-4 text-xs text-zinc-500 dark:text-zinc-600">
              No agents registered.
            </div>
          ) : (
            <div className="divide-y divide-zinc-200/50 dark:divide-zinc-800/50">
              {agents.map((agent) => (
                <NumericLimitRow
                  key={agent.name}
                  label={
                    <span className="flex items-center gap-1.5">
                      <Bot className="w-3 h-3 text-zinc-500" />
                      <span className="font-mono">{agent.name}</span>
                      {agent.is_disabled && (
                        <span className="text-[10px] px-1 py-0.5 rounded bg-red-100 dark:bg-red-950 text-red-600 dark:text-red-400">
                          disabled
                        </span>
                      )}
                    </span>
                  }
                  value={agentLimits[agent.name] ?? 0}
                  running={agentRunning[agent.name] ?? 0}
                  onChange={(n) =>
                    mutations.setAgentConcurrencyLimit.mutate({
                      agent: agent.name,
                      limit: n,
                    })
                  }
                  disabled={mutations.setAgentConcurrencyLimit.isPending}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── General section ─────────────────────────────────────────────────

function GeneralSection({
  config,
  agents,
  mutations,
}: {
  config: NonNullable<ReturnType<typeof useConfig>["data"]>;
  agents: AgentInfo[] | undefined;
  mutations: ReturnType<typeof useConfigMutations>;
}) {
  return (
    <div>
      <SectionHeader
        title="General"
        description="Default settings for steps and agents."
      />

      <div className="space-y-6">
        <div className="space-y-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-zinc-400">Default Model</label>
            <div className="flex items-center gap-3">
              <select
                value={config.default_model}
                onChange={(e) => mutations.setDefaultModel.mutate(e.target.value)}
                className="text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1.5 text-zinc-700 dark:text-zinc-300"
              >
                {config.labels.map((l) => (
                  <option key={l.name} value={l.name}>
                    {l.name} ({l.model})
                  </option>
                ))}
              </select>
              <span className="text-[10px] text-zinc-500 dark:text-zinc-600">Used when step omits model:</span>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-zinc-400">Default Agent</label>
            <div className="flex items-center gap-3">
              <select
                value={config.default_agent}
                onChange={(e) => mutations.setDefaultAgent.mutate(e.target.value)}
                className="text-xs bg-zinc-50 dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 rounded px-2 py-1.5 text-zinc-700 dark:text-zinc-300"
              >
                {agents && agents.length > 0 ? (
                  agents.filter((a) => !a.is_disabled).map((a) => (
                    <option key={a.name} value={a.name}>{a.name}</option>
                  ))
                ) : (
                  <>
                    <option value="claude">claude</option>
                    <option value="codex">codex</option>
                  </>
                )}
                {agents && agents.length > 0 && !agents.some((a) => a.name === config.default_agent) && (
                  <option value={config.default_agent}>{config.default_agent}</option>
                )}
              </select>
              <span className="text-[10px] text-zinc-500 dark:text-zinc-600">Used when agent step omits backend:</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── API Keys section ────────────────────────────────────────────────

function ApiKeysSection({
  config,
  mutations,
}: {
  config: NonNullable<ReturnType<typeof useConfig>["data"]>;
  mutations: ReturnType<typeof useConfigMutations>;
}) {
  return (
    <div>
      <SectionHeader
        title="API Keys"
        description="Provider credentials for model access."
      />

      <div className="border border-zinc-200 dark:border-zinc-800 rounded-lg overflow-hidden">
        <ApiKeyRow
          name="OpenRouter"
          envVar="OPENROUTER_API_KEY"
          hasKey={config.has_api_key}
          currentValue={config.openrouter_api_key}
          source={config.api_key_source}
          onSet={(value, scope) =>
            mutations.setApiKey.mutate({ key: "openrouter", value, scope })
          }
        />
        <div className="border-t border-zinc-200/50 dark:border-zinc-800/50" />
        <ApiKeyRow
          name="Anthropic"
          envVar="ANTHROPIC_API_KEY"
          hasKey={config.has_anthropic_key}
          currentValue={config.anthropic_api_key}
          source={null}
          onSet={(value, scope) =>
            mutations.setApiKey.mutate({ key: "anthropic", value, scope })
          }
        />
      </div>

      <p className="text-[10px] text-zinc-500 dark:text-zinc-600 mt-3">
        User = ~/.config/stepwise/ &nbsp;|&nbsp; Project = .stepwise/config.local.yaml (gitignored)
      </p>
    </div>
  );
}

// ── Models section ──────────────────────────────────────────────────

function ModelsSection({
  config,
  modelLabelMap,
  existingModelIds,
  mutations,
}: {
  config: NonNullable<ReturnType<typeof useConfig>["data"]>;
  modelLabelMap: Record<string, string[]>;
  existingModelIds: Set<string>;
  mutations: ReturnType<typeof useConfigMutations>;
}) {
  return (
    <div>
      <SectionHeader
        title="Models"
        description="Available models in the registry."
      />

      <div className="border border-zinc-200 dark:border-zinc-800 rounded-lg overflow-hidden">
        <div className="space-y-0.5 p-1">
          {/* Column headers */}
          {config.model_registry.length > 0 && (
            <div className="hidden md:flex items-center gap-2 py-1 px-3 text-[10px] font-medium text-zinc-500 dark:text-zinc-600 uppercase tracking-wide">
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
            <div className="px-3 py-2 text-xs text-zinc-500 dark:text-zinc-600">
              No models in registry. Search OpenRouter to add models.
            </div>
          )}
        </div>
      </div>

      <div className="mt-2">
        <ModelSearch
          existingIds={existingModelIds}
          onAdd={(model) => mutations.addModel.mutate(model)}
        />
      </div>
    </div>
  );
}

// ── Labels section ──────────────────────────────────────────────────

function LabelsSection({
  config,
  mutations,
}: {
  config: NonNullable<ReturnType<typeof useConfig>["data"]>;
  mutations: ReturnType<typeof useConfigMutations>;
}) {
  const defaultLabels = config.labels.filter((l) => l.is_default);
  const customLabels = config.labels.filter((l) => !l.is_default);

  return (
    <div>
      <SectionHeader
        title="Labels"
        description="Model label aliases for use in flows."
      />

      <div className="border border-zinc-200 dark:border-zinc-800 rounded-lg overflow-hidden">
        <div className="space-y-1 p-1">
          <div className="px-3 py-1 text-[10px] font-medium text-zinc-500 dark:text-zinc-600 uppercase tracking-wide">
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

          <div className="px-3 py-1 mt-3 text-[10px] font-medium text-zinc-500 dark:text-zinc-600 uppercase tracking-wide">
            Custom Labels
          </div>
          {customLabels.length === 0 && (
            <div className="px-3 py-1 text-xs text-zinc-500 dark:text-zinc-600">No custom labels</div>
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
      </div>
    </div>
  );
}

// ── Sidebar navigation ──────────────────────────────────────────────

function Sidebar({
  activeSection,
  onSelect,
}: {
  activeSection: SectionId;
  onSelect: (id: SectionId) => void;
}) {
  return (
    <nav className="w-52 shrink-0 py-6 pr-2">
      <div className="space-y-0.5">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          const isActive = activeSection === item.id;
          return (
            <button
              key={item.id}
              onClick={() => onSelect(item.id)}
              className={cn(
                "w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors text-left",
                isActive
                  ? "bg-zinc-100 dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 font-medium"
                  : "text-zinc-500 dark:text-zinc-400 hover:bg-zinc-50 dark:hover:bg-zinc-800/50 hover:text-zinc-700 dark:hover:text-zinc-300"
              )}
            >
              <Icon className={cn("w-4 h-4 shrink-0", isActive ? "text-zinc-700 dark:text-zinc-300" : "text-zinc-400 dark:text-zinc-600")} />
              {item.label}
            </button>
          );
        })}
      </div>
    </nav>
  );
}

// ── Mobile tab bar ──────────────────────────────────────────────────

function MobileTabBar({
  activeSection,
  onSelect,
}: {
  activeSection: SectionId;
  onSelect: (id: SectionId) => void;
}) {
  return (
    <div className="flex overflow-x-auto border-b border-zinc-200 dark:border-zinc-800 px-2 gap-1 shrink-0">
      {NAV_ITEMS.map((item) => {
        const Icon = item.icon;
        const isActive = activeSection === item.id;
        return (
          <button
            key={item.id}
            onClick={() => onSelect(item.id)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-2.5 text-xs whitespace-nowrap transition-colors shrink-0",
              isActive
                ? "text-zinc-900 dark:text-zinc-100 border-b-2 border-zinc-900 dark:border-zinc-100 font-medium"
                : "text-zinc-500 dark:text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-300"
            )}
          >
            <Icon className="w-3.5 h-3.5" />
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

// ── Main page ───────────────────────────────────────────────────────

export function SettingsPage() {
  const { data: config, isLoading } = useConfig();
  const { data: agents } = useAgents();
  const mutations = useConfigMutations();
  const isMobile = useIsMobile();
  const [activeSection, setActiveSection] = useState<SectionId>("general");

  if (isLoading || !config) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500">
        Loading...
      </div>
    );
  }

  // Precompute label-to-model mapping
  const modelLabelMap: Record<string, string[]> = {};
  for (const label of config.labels) {
    if (!modelLabelMap[label.model]) modelLabelMap[label.model] = [];
    modelLabelMap[label.model].push(label.name);
  }
  const existingModelIds = new Set(config.model_registry.map((m) => m.id));

  const renderContent = () => {
    switch (activeSection) {
      case "general":
        return <GeneralSection config={config} agents={agents} mutations={mutations} />;
      case "agents":
        return (
          <div>
            <SectionHeader
              title="Agents"
              description="Agent backends available for use in flows, plus the project-wide approval policy."
            />
            <AgentsSection config={config} mutations={mutations} />
          </div>
        );
      case "limits":
        return <LimitsSection config={config} agents={agents} mutations={mutations} />;
      case "containment":
        return <ContainmentSection config={config} agents={agents} mutations={mutations} />;
      case "api-keys":
        return <ApiKeysSection config={config} mutations={mutations} />;
      case "models":
        return (
          <ModelsSection
            config={config}
            modelLabelMap={modelLabelMap}
            existingModelIds={existingModelIds}
            mutations={mutations}
          />
        );
      case "labels":
        return <LabelsSection config={config} mutations={mutations} />;
    }
  };

  if (isMobile) {
    return (
      <div className="h-full flex flex-col">
        <MobileTabBar activeSection={activeSection} onSelect={setActiveSection} />
        <div className="flex-1 overflow-y-auto p-4">
          {renderContent()}
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex">
      <Sidebar activeSection={activeSection} onSelect={setActiveSection} />
      <div className="flex-1 border-l border-zinc-200 dark:border-zinc-800 overflow-y-auto">
        <div className="max-w-2xl py-8 px-8">
          {renderContent()}
        </div>
      </div>
    </div>
  );
}
