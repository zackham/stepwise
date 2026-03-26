import type { HandoffEnvelope } from "@/lib/types";
import { JsonView } from "@/components/JsonView";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

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

  // Skip empty sections
  if (data === null || data === undefined) return null;
  if (typeof data === "object" && !Array.isArray(data) && Object.keys(data as object).length === 0)
    return null;
  if (Array.isArray(data) && data.length === 0) return null;
  if (typeof data === "string" && data === "") return null;

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex items-center gap-1.5 text-sm text-zinc-400 hover:text-foreground w-full py-1">
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
  const hasSidecar =
    envelope.sidecar.decisions_made.length > 0 ||
    envelope.sidecar.assumptions.length > 0 ||
    envelope.sidecar.open_questions.length > 0 ||
    envelope.sidecar.constraints_discovered.length > 0;

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
