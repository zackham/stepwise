import type { HandoffEnvelope } from "@/lib/types";
import { JsonView } from "@/components/JsonView";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { cn, tryParseJsonValue } from "@/lib/utils";

interface HandoffEnvelopeViewProps {
  envelope: HandoffEnvelope;
  isLatest?: boolean;
}

function Section({
  title,
  data,
  defaultOpen = false,
}: {
  title: string;
  data: unknown;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  // Skip empty sections — parse JSON strings before checking
  const resolved = tryParseJsonValue(data);
  if (resolved === null || resolved === undefined) return null;
  if (typeof resolved === "object" && !Array.isArray(resolved) && Object.keys(resolved as object).length === 0)
    return null;
  if (Array.isArray(resolved) && resolved.length === 0) return null;
  if (typeof resolved === "string" && resolved === "") return null;

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex items-center gap-1.5 text-sm text-zinc-500 dark:text-zinc-400 hover:text-foreground w-full py-1">
        <ChevronRight
          className={cn(
            "w-3.5 h-3.5 transition-transform",
            open && "rotate-90"
          )}
        />
        <span className="font-medium">{title}</span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="ml-5 py-1">
          <JsonView data={data} defaultExpanded={true} />
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

export function HandoffEnvelopeView({ envelope, isLatest }: HandoffEnvelopeViewProps) {
  const sidecarFields = [
    envelope.sidecar.decisions_made,
    envelope.sidecar.assumptions,
    envelope.sidecar.open_questions,
    envelope.sidecar.constraints_discovered,
  ];
  const hasSidecar = sidecarFields.some((field) => {
    const parsed = tryParseJsonValue(field);
    return Array.isArray(parsed) ? parsed.length > 0 : parsed && String(parsed).length > 0;
  });

  return (
    <div className="space-y-1">
      <Section title="Artifact" data={envelope.artifact} defaultOpen={isLatest !== false} />
      {hasSidecar && <Section title="Sidecar" data={envelope.sidecar} />}
      <Section title="Executor Meta" data={envelope.executor_meta} />
      {envelope.workspace && (
        <Section title="Workspace" data={envelope.workspace} />
      )}
    </div>
  );
}
