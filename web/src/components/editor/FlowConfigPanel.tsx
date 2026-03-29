import { useState, useCallback, useEffect } from "react";
import { Settings, Code, Save, Eye, EyeOff } from "lucide-react";
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
import { useFlowConfig, useSaveFlowConfig } from "@/hooks/useEditor";
import type { ConfigVar } from "@/lib/types";

type ViewMode = "form" | "raw";

interface FlowConfigPanelProps {
  flowPath: string;
}

function ConfigField({
  configVar,
  value,
  onChange,
}: {
  configVar: ConfigVar;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const [showSensitive, setShowSensitive] = useState(false);
  const fieldType = configVar.type || "str";
  const strValue = value != null ? String(value) : "";

  if (fieldType === "bool") {
    return (
      <div className="space-y-1">
        <Label className="text-xs text-zinc-400">{configVar.name}</Label>
        {configVar.description && (
          <p className="text-[10px] text-zinc-600">{configVar.description}</p>
        )}
        <Select
          value={value != null ? String(value) : ""}
          onValueChange={(v) => onChange(v === "true")}
        >
          <SelectTrigger className="h-8 text-sm bg-zinc-900 border-zinc-700">
            <SelectValue placeholder={configVar.example || "Select..."} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="true">true</SelectItem>
            <SelectItem value="false">false</SelectItem>
          </SelectContent>
        </Select>
      </div>
    );
  }

  if (fieldType === "choice" && configVar.options) {
    return (
      <div className="space-y-1">
        <Label className="text-xs text-zinc-400">{configVar.name}</Label>
        {configVar.description && (
          <p className="text-[10px] text-zinc-600">{configVar.description}</p>
        )}
        <Select
          value={strValue}
          onValueChange={(v) => onChange(v)}
        >
          <SelectTrigger className="h-8 text-sm bg-zinc-900 border-zinc-700">
            <SelectValue placeholder={configVar.example || "Select..."} />
          </SelectTrigger>
          <SelectContent>
            {configVar.options.map((opt) => (
              <SelectItem key={opt} value={opt}>
                {opt}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    );
  }

  if (fieldType === "text") {
    return (
      <div className="space-y-1">
        <Label className="text-xs text-zinc-400">{configVar.name}</Label>
        {configVar.description && (
          <p className="text-[10px] text-zinc-600">{configVar.description}</p>
        )}
        <Textarea
          value={strValue}
          onChange={(e) => onChange(e.target.value)}
          placeholder={configVar.example || configVar.default != null ? String(configVar.default) : ""}
          className="text-sm bg-zinc-900 border-zinc-700 min-h-[60px]"
          rows={3}
        />
      </div>
    );
  }

  if (fieldType === "number") {
    return (
      <div className="space-y-1">
        <Label className="text-xs text-zinc-400">{configVar.name}</Label>
        {configVar.description && (
          <p className="text-[10px] text-zinc-600">{configVar.description}</p>
        )}
        <Input
          type="number"
          value={strValue}
          onChange={(e) => {
            const v = e.target.value;
            onChange(v === "" ? null : Number(v));
          }}
          placeholder={configVar.example || (configVar.default != null ? String(configVar.default) : "")}
          className="h-8 text-sm bg-zinc-900 border-zinc-700"
        />
      </div>
    );
  }

  // Default: str
  return (
    <div className="space-y-1">
      <Label className="text-xs text-zinc-400">
        {configVar.name}
        {configVar.sensitive && (
          <button
            onClick={() => setShowSensitive(!showSensitive)}
            className="ml-1.5 text-zinc-600 hover:text-zinc-400"
          >
            {showSensitive ? <EyeOff className="w-3 h-3 inline" /> : <Eye className="w-3 h-3 inline" />}
          </button>
        )}
      </Label>
      {configVar.description && (
        <p className="text-[10px] text-zinc-600">{configVar.description}</p>
      )}
      <Input
        type={configVar.sensitive && !showSensitive ? "password" : "text"}
        value={strValue}
        onChange={(e) => onChange(e.target.value || null)}
        placeholder={configVar.example || (configVar.default != null ? String(configVar.default) : "")}
        className="h-8 text-sm bg-zinc-900 border-zinc-700"
      />
    </div>
  );
}

export function FlowConfigPanel({ flowPath }: FlowConfigPanelProps) {
  const { data: config } = useFlowConfig(flowPath);
  const saveMutation = useSaveFlowConfig();
  const [mode, setMode] = useState<ViewMode>("form");
  const [formValues, setFormValues] = useState<Record<string, unknown>>({});
  const [rawYaml, setRawYaml] = useState("");
  const [dirty, setDirty] = useState(false);

  // Sync from server data
  useEffect(() => {
    if (config) {
      setFormValues(config.values);
      setRawYaml(config.raw_yaml);
      setDirty(false);
    }
  }, [config]);

  const handleFieldChange = useCallback(
    (name: string, value: unknown) => {
      setFormValues((prev) => ({ ...prev, [name]: value }));
      setDirty(true);
    },
    []
  );

  const handleRawChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      setRawYaml(e.target.value);
      setDirty(true);
    },
    []
  );

  const handleSave = useCallback(() => {
    if (mode === "form") {
      saveMutation.mutate(
        { flowPath, data: { values: formValues } },
        { onSuccess: () => setDirty(false) }
      );
    } else {
      saveMutation.mutate(
        { flowPath, data: { raw_yaml: rawYaml } },
        { onSuccess: () => setDirty(false) }
      );
    }
  }, [mode, flowPath, formValues, rawYaml, saveMutation]);

  if (!config) return null;

  const configVars: ConfigVar[] = config.config_vars;
  const hasConfigVars = configVars.length > 0;
  const hasValues = Object.keys(config.values).length > 0;

  // Nothing to show if no config vars declared and no config file exists
  if (!hasConfigVars && !hasValues) return null;

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50">
      <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-800">
        <div className="flex items-center gap-1.5 text-xs text-zinc-400">
          <Settings className="w-3.5 h-3.5" />
          <span className="font-medium">Config</span>
          <span className="text-zinc-600">({config.config_path})</span>
        </div>
        <div className="flex items-center gap-1">
          {hasConfigVars && (
            <button
              onClick={() => setMode(mode === "form" ? "raw" : "form")}
              className="text-[10px] text-zinc-500 hover:text-zinc-300 px-1.5 py-0.5 rounded hover:bg-zinc-800"
            >
              <Code className="w-3 h-3 inline mr-1" />
              {mode === "form" ? "YAML" : "Form"}
            </button>
          )}
          {dirty && (
            <Button
              onClick={handleSave}
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-xs"
              disabled={saveMutation.isPending}
            >
              <Save className="w-3 h-3 mr-1" />
              {saveMutation.isPending ? "Saving..." : "Save"}
            </Button>
          )}
        </div>
      </div>

      <div className="p-3">
        {mode === "form" && hasConfigVars ? (
          <div className="space-y-3">
            {configVars.map((cv) => (
              <ConfigField
                key={cv.name}
                configVar={cv}
                value={formValues[cv.name] ?? null}
                onChange={(v) => handleFieldChange(cv.name, v)}
              />
            ))}
          </div>
        ) : (
          <Textarea
            value={rawYaml}
            onChange={handleRawChange}
            placeholder="# key: value"
            className="font-mono text-xs bg-zinc-950 border-zinc-700 min-h-[80px]"
            rows={Math.max(4, rawYaml.split("\n").length + 1)}
            onKeyDown={(e) => {
              if (e.key === "s" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                if (dirty) handleSave();
              }
            }}
          />
        )}
      </div>
    </div>
  );
}
