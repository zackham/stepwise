import { useState, useMemo } from "react";
import { ContentModal } from "@/components/ui/content-modal";
import { Markdown } from "@/components/ui/markdown";
import { ArrowLeftFromLine, ChevronRight, Maximize2 } from "lucide-react";
import { cn } from "@/lib/utils";
import type { InputBinding, HandoffEnvelope } from "@/lib/types";

/* ── Shared section primitives ──────────────────────────────────── */

export function SectionHeading({ children, detail }: { children: React.ReactNode; detail?: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-semibold text-zinc-300 uppercase tracking-wide">
        {children}
      </span>
      {detail && <span className="ml-auto">{detail}</span>}
    </div>
  );
}

export function SectionDivider() {
  return <div className="border-t border-border -mx-3" />;
}

/** Collapsible sidebar section with HR, title, and optional right-side detail. Title click toggles. */
export function SidebarSection({
  title,
  detail,
  children,
  defaultOpen = true,
}: {
  title: React.ReactNode;
  detail?: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <>
      <SectionDivider />
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-2 w-full cursor-pointer group"
      >
        <ChevronRight className={cn(
          "w-3 h-3 text-zinc-600 transition-transform shrink-0",
          open && "rotate-90",
        )} />
        <span className="text-xs font-semibold text-zinc-300 uppercase tracking-wide">
          {title}
        </span>
        {detail && <span className="ml-auto">{detail}</span>}
      </button>
      {open && children}
    </>
  );
}

/* ── Input row: mapping label then value below ──────────────────── */

export function InputRow({
  field,
  mapping,
  value,
  sourceStep,
  onClickSource,
}: {
  field: string;
  mapping: string;
  value: string;
  sourceStep?: string;
  onClickSource?: (stepName: string) => void;
}) {
  const [modalOpen, setModalOpen] = useState(false);

  return (
    <>
      <div
        className="cursor-pointer hover:bg-zinc-100 dark:hover:bg-zinc-800/30 rounded px-1 py-0.5 -mx-1 transition-colors"
        onClick={() => setModalOpen(true)}
      >
        <div className="text-[10px] font-mono text-zinc-500">
          <span className="text-cyan-400">{field}</span>
          <ArrowLeftFromLine className="w-3 h-3 text-zinc-600 mx-1 inline-block align-middle" />
          {sourceStep ? (
            <button
              onClick={(ev) => { ev.stopPropagation(); onClickSource?.(sourceStep); }}
              className="text-zinc-500 hover:text-blue-400 transition-colors"
            >
              {mapping}
            </button>
          ) : (
            <span>{mapping}</span>
          )}
        </div>
        <div className="text-xs font-mono text-zinc-400 line-clamp-3 mt-0.5">
          {value}
        </div>
      </div>
      <ContentModal open={modalOpen} onOpenChange={setModalOpen} title={field} copyContent={value}>
        <pre className="whitespace-pre-wrap text-sm text-zinc-300 font-mono p-2">{value}</pre>
      </ContentModal>
    </>
  );
}

/* ── Output row: key label then value below ─────────────────────── */

export function OutputRow({ label, value }: { label: string; value: string }) {
  const [modalOpen, setModalOpen] = useState(false);

  return (
    <>
      <div
        className="cursor-pointer hover:bg-zinc-100 dark:hover:bg-zinc-800/30 rounded px-1 py-0.5 -mx-1 transition-colors"
        onClick={() => setModalOpen(true)}
      >
        <div className="text-[10px] font-mono text-emerald-400">{label}</div>
        <div className="text-xs font-mono text-zinc-700 dark:text-zinc-300 line-clamp-3 mt-0.5">
          {value}
        </div>
      </div>
      <ContentModal open={modalOpen} onOpenChange={setModalOpen} title={label} copyContent={value}>
        <pre className="whitespace-pre-wrap text-sm text-zinc-300 font-mono p-2">{value}</pre>
      </ContentModal>
    </>
  );
}

/* ── Inputs section ─────────────────────────────────────────────── */

export function InputsSection({
  inputs,
  inputBindings,
  onSelectStep,
}: {
  inputs: Record<string, unknown>;
  inputBindings?: InputBinding[];
  onSelectStep?: (stepName: string) => void;
}) {
  const bindingMap = useMemo(() => {
    const m = new Map<string, InputBinding>();
    for (const b of inputBindings ?? []) m.set(b.local_name, b);
    return m;
  }, [inputBindings]);

  const entries = useMemo(() => {
    return Object.entries(inputs).map(([field, value]) => {
      const binding = bindingMap.get(field);
      const sourceStep = binding?.source_step ?? "$job";
      const sourceField = binding?.source_field ?? field;
      const mapping = `${sourceStep}.${sourceField}`;
      const strVal = typeof value === "string" ? value
        : typeof value === "number" || typeof value === "boolean" ? String(value)
        : JSON.stringify(value, null, 2);
      return {
        field,
        mapping,
        value: strVal,
        sourceStep: sourceStep === "$job" ? undefined : sourceStep,
      };
    });
  }, [inputs, bindingMap]);

  if (entries.length === 0) return null;

  return (
    <SidebarSection title="Inputs">
      <div className="space-y-1.5">
        {entries.map((e) => (
          <InputRow
            key={e.field}
            field={e.field}
            mapping={e.mapping}
            value={e.value}
            sourceStep={e.sourceStep}
            onClickSource={onSelectStep}
          />
        ))}
      </div>
    </SidebarSection>
  );
}

/* ── Outputs section ────────────────────────────────────────────── */

/** Detect if text looks like markdown (has headings, bold, lists, code blocks, links) */
function looksLikeMarkdown(text: string): boolean {
  return /^#{1,6}\s|^\*\*|^\- |\*\*.*\*\*|```|^\d+\.\s|\[.*\]\(.*\)/m.test(text);
}

export function OutputsSection({
  result,
  executorType,
}: {
  result: HandoffEnvelope;
  executorType?: string;
}) {
  const [modalOpen, setModalOpen] = useState(false);

  const entries = useMemo(() => {
    if (!result.artifact) return [];
    return Object.entries(result.artifact)
      .filter(([k]) => !k.startsWith("_"))
      .map(([key, value]) => {
        const strVal = typeof value === "string" ? value
          : typeof value === "number" || typeof value === "boolean" ? String(value)
          : JSON.stringify(value, null, 2);
        return { key, value: strVal };
      });
  }, [result]);

  if (entries.length === 0) return null;

  // LLM steps: render the response directly as content
  const isLlm = executorType === "llm";
  const singleResponse = isLlm && entries.length === 1 ? entries[0].value : null;
  const isMarkdown = singleResponse ? looksLikeMarkdown(singleResponse) : false;

  if (singleResponse) {
    return (
      <SidebarSection
        title="Response"
        detail={
          <button
            onClick={(e) => { e.stopPropagation(); setModalOpen(true); }}
            className="text-zinc-600 hover:text-zinc-300 transition-colors cursor-pointer p-0.5"
            title="Expand"
          >
            <Maximize2 className="w-3 h-3" />
          </button>
        }
      >
        <div className="text-xs">
          {isMarkdown ? (
            <Markdown>{singleResponse}</Markdown>
          ) : (
            <pre className="font-mono text-zinc-300 whitespace-pre-wrap break-words leading-relaxed">
              {singleResponse}
            </pre>
          )}
        </div>
        <ContentModal open={modalOpen} onOpenChange={setModalOpen} title="Response" copyContent={singleResponse}>
          {isMarkdown ? (
            <div className="p-3"><Markdown>{singleResponse}</Markdown></div>
          ) : (
            <pre className="whitespace-pre-wrap text-sm text-zinc-300 font-mono p-3">{singleResponse}</pre>
          )}
        </ContentModal>
      </SidebarSection>
    );
  }

  return (
    <SidebarSection title="Outputs">
      <div className="space-y-0.5">
        {entries.map((e) => (
          <OutputRow key={e.key} label={e.key} value={e.value} />
        ))}
      </div>
    </SidebarSection>
  );
}

/* ── Job-level outputs (from terminal output, not a HandoffEnvelope) */

export function JobOutputsSection({
  outputs,
}: {
  outputs: Record<string, unknown>;
}) {
  const entries = useMemo(() => {
    return Object.entries(outputs)
      .filter(([k]) => !k.startsWith("_"))
      .map(([key, value]) => {
        const strVal = typeof value === "string" ? value
          : typeof value === "number" || typeof value === "boolean" ? String(value)
          : JSON.stringify(value, null, 2);
        return { key, value: strVal };
      });
  }, [outputs]);

  if (entries.length === 0) return null;

  return (
    <SidebarSection title="Outputs">
      <div className="space-y-0.5">
        {entries.map((e) => (
          <OutputRow key={e.key} label={e.key} value={e.value} />
        ))}
      </div>
    </SidebarSection>
  );
}

/* ── Job-level inputs (no bindings, just key-value) ─────────────── */

export function JobInputsSection({
  inputs,
}: {
  inputs: Record<string, unknown>;
}) {
  const entries = useMemo(() => {
    return Object.entries(inputs).map(([field, value]) => {
      const strVal = typeof value === "string" ? value
        : typeof value === "number" || typeof value === "boolean" ? String(value)
        : JSON.stringify(value, null, 2);
      return { field, mapping: `$job.${field}`, value: strVal };
    });
  }, [inputs]);

  if (entries.length === 0) return null;

  return (
    <SidebarSection title="Inputs">
      <div className="space-y-1.5">
        {entries.map((e) => (
          <InputRow key={e.field} field={e.field} mapping={e.mapping} value={e.value} />
        ))}
      </div>
    </SidebarSection>
  );
}
