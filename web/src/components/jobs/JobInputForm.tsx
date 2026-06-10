import { Input } from "@/components/ui/input";

interface JobInputFormProps {
  fields: string[];
  values: Record<string, string>;
  onChange: (field: string, value: string) => void;
}

export function JobInputForm({ fields, values, onChange }: JobInputFormProps) {
  if (fields.length === 0) return null;
  return (
    <div className="space-y-3">
      {fields.map((field) => (
        <div key={field} className="space-y-1">
          <label className="text-xs font-medium text-zinc-700 dark:text-zinc-300">
            {field}
          </label>
          <Input
            value={values[field] ?? ""}
            onChange={(e) => onChange(field, e.target.value)}
            placeholder={field}
            className="text-xs bg-white dark:bg-zinc-900 border-zinc-300 dark:border-zinc-700"
          />
        </div>
      ))}
    </div>
  );
}
