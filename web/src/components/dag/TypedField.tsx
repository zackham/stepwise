import type { OutputFieldSchema } from "@/lib/types";
import { tryParseJsonValue } from "@/lib/utils";

interface TypedFieldProps {
  name: string;
  schema: OutputFieldSchema;
  value: unknown;
  onChange: (value: unknown) => void;
  error?: string;
  compact?: boolean;
  autoFocus?: boolean;
}

const inputClass =
  "w-full rounded-md border border-zinc-300 dark:border-zinc-700 bg-white/80 dark:bg-zinc-800/80 px-2.5 py-1.5 text-sm text-foreground placeholder:text-zinc-400 dark:placeholder:text-zinc-600 focus:outline-none focus:border-amber-500/50 focus:ring-1 focus:ring-amber-500/20 transition-colors";

function FieldLabel({
  name,
  schema,
}: {
  name: string;
  schema: OutputFieldSchema;
}) {
  return (
    <label className="block text-[10px] font-medium text-zinc-500 uppercase tracking-wide mb-1">
      {name}
      {schema.required === false && (
        <span className="ml-1 normal-case text-zinc-600">(optional)</span>
      )}
    </label>
  );
}

function FieldDescription({ schema }: { schema: OutputFieldSchema }) {
  if (!schema.description) return null;
  return (
    <p className="text-[10px] text-zinc-600 mt-0.5">{schema.description}</p>
  );
}

function FieldError({ error }: { error?: string }) {
  if (!error) return null;
  return <p className="text-[10px] text-red-400 mt-0.5">{error}</p>;
}

function StrField({
  value,
  onChange,
  autoFocus,
}: {
  value: unknown;
  onChange: (v: string) => void;
  autoFocus?: boolean;
}) {
  return (
    <input
      type="text"
      value={(value as string) ?? ""}
      onChange={(e) => onChange(e.target.value)}
      className={inputClass + " font-mono h-9"}
      autoFocus={autoFocus}
    />
  );
}

function TextField({
  value,
  onChange,
  autoFocus,
}: {
  value: unknown;
  onChange: (v: string) => void;
  autoFocus?: boolean;
}) {
  return (
    <textarea
      rows={3}
      value={(value as string) ?? ""}
      onChange={(e) => onChange(e.target.value)}
      className={inputClass + " font-mono min-h-[60px]"}
      autoFocus={autoFocus}
    />
  );
}

function NumberField({
  value,
  onChange,
  schema,
  autoFocus,
}: {
  value: unknown;
  onChange: (v: number | string) => void;
  schema: OutputFieldSchema;
  autoFocus?: boolean;
}) {
  return (
    <input
      type="number"
      value={value === undefined || value === null ? "" : String(value)}
      onChange={(e) => {
        const raw = e.target.value;
        if (raw === "") {
          onChange("");
          return;
        }
        const num = Number(raw);
        onChange(isNaN(num) ? raw : num);
      }}
      min={schema.min}
      max={schema.max}
      step="any"
      className={inputClass + " font-mono h-9"}
      autoFocus={autoFocus}
    />
  );
}

function BoolField({
  value,
  onChange,
  schema,
}: {
  value: unknown;
  onChange: (v: boolean) => void;
  schema: OutputFieldSchema;
}) {
  const checked = value === true || value === "true";
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`
        inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium
        border transition-colors
        ${
          checked
            ? "bg-amber-600/80 border-amber-500/50 text-white"
            : "bg-zinc-100/80 dark:bg-zinc-800/80 border-zinc-300 dark:border-zinc-700 text-zinc-500 dark:text-zinc-400 hover:border-zinc-400 dark:hover:border-zinc-600"
        }
      `}
    >
      <span
        className={`w-3 h-3 rounded-sm border ${
          checked
            ? "bg-amber-400 border-amber-400"
            : "bg-zinc-300 dark:bg-zinc-700 border-zinc-400 dark:border-zinc-600"
        }`}
      >
        {checked && (
          <svg viewBox="0 0 12 12" className="w-3 h-3 text-zinc-900">
            <path
              d="M3 6l2 2 4-4"
              stroke="currentColor"
              strokeWidth="2"
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        )}
      </span>
      {checked ? "Yes" : "No"}
    </button>
  );
}

function ChoiceSingleField({
  value,
  onChange,
  schema,
}: {
  value: unknown;
  onChange: (v: string) => void;
  schema: OutputFieldSchema;
}) {
  const options = schema.options ?? [];
  return (
    <div className="space-y-1">
      {options.map((opt) => (
        <label
          key={opt}
          className="flex items-center gap-2 text-sm text-zinc-700 dark:text-zinc-300 cursor-pointer hover:text-foreground"
        >
          <input
            type="radio"
            name={`choice-${options.join("-")}`}
            checked={value === opt}
            onChange={() => onChange(opt)}
            className="accent-amber-500"
          />
          {opt}
        </label>
      ))}
    </div>
  );
}

function ChoiceMultipleField({
  value,
  onChange,
  schema,
}: {
  value: unknown;
  onChange: (v: string[]) => void;
  schema: OutputFieldSchema;
}) {
  const options = schema.options ?? [];
  const parsed = tryParseJsonValue(value);
  const selected = Array.isArray(parsed) ? (parsed as string[]) : [];

  const toggle = (opt: string) => {
    if (selected.includes(opt)) {
      onChange(selected.filter((v) => v !== opt));
    } else {
      onChange([...selected, opt]);
    }
  };

  return (
    <div className="space-y-1">
      {options.map((opt) => (
        <label
          key={opt}
          className="flex items-center gap-2 text-sm text-zinc-700 dark:text-zinc-300 cursor-pointer hover:text-foreground"
        >
          <input
            type="checkbox"
            checked={selected.includes(opt)}
            onChange={() => toggle(opt)}
            className="accent-amber-500"
          />
          {opt}
        </label>
      ))}
    </div>
  );
}

export function TypedField({
  name,
  schema,
  value,
  onChange,
  error,
  autoFocus,
}: TypedFieldProps) {
  const fieldType = schema.type || "str";
  return (
    <div>
      <FieldLabel name={name} schema={schema} />
      {fieldType === "str" && (
        <StrField value={value} onChange={onChange} autoFocus={autoFocus} />
      )}
      {fieldType === "text" && (
        <TextField value={value} onChange={onChange} autoFocus={autoFocus} />
      )}
      {fieldType === "number" && (
        <NumberField
          value={value}
          onChange={onChange}
          schema={schema}
          autoFocus={autoFocus}
        />
      )}
      {fieldType === "bool" && (
        <BoolField value={value} onChange={onChange} schema={schema} />
      )}
      {fieldType === "choice" && !schema.multiple && (
        <ChoiceSingleField
          value={value}
          onChange={onChange as (v: string) => void}
          schema={schema}
        />
      )}
      {fieldType === "choice" && schema.multiple && (
        <ChoiceMultipleField
          value={value}
          onChange={onChange as (v: string[]) => void}
          schema={schema}
        />
      )}
      <FieldDescription schema={schema} />
      <FieldError error={error} />
    </div>
  );
}
