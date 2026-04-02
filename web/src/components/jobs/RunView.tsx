import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { useRuns, useEvents, useRunCost, useStepwiseMutations } from "@/hooks/useStepwise";
import { useConfig } from "@/hooks/useConfig";
import type { StepDefinition, StepRun, HandoffEnvelope, InputBinding } from "@/lib/types";
import { StepStatusBadge } from "@/components/StatusBadge";
import { AgentStreamView } from "./AgentStreamView";
import { JsonView } from "@/components/JsonView";
import { Button } from "@/components/ui/button";
import {
  ChevronLeft,
  ChevronRight,
  RefreshCw,
  AlertTriangle,
  StopCircle,
  Copy,
  Check,
  Terminal,
} from "lucide-react";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { ContentModal } from "@/components/ui/content-modal";
import { useAgentOutput } from "@/hooks/useStepwise";
import { useScriptStream } from "@/hooks/useScriptStream";
import { toast } from "sonner";
import { cn, formatCost, formatDuration } from "@/lib/utils";
import { VirtualizedLogView } from "@/components/logs/VirtualizedLogView";
import { LiveDuration } from "@/components/LiveDuration";
import { measureTextHeight, truncateToLines } from "@/lib/pretext-measure";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";

interface RunViewProps {
  jobId: string;
  stepDef: StepDefinition;
  hasLiveSource?: boolean;
  onSelectStep?: (stepName: string) => void;
}

/* ── Helpers ───────────────────────────────────────────────────────── */

function formatCostDisplay(cost: number | null | undefined): string {
  if (cost == null || cost === 0) return "-";
  return formatCost(cost);
}

/** Pretty timestamp: "6:58 PM" */
function formatTimePretty(ts: string | null): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] font-medium text-zinc-500 uppercase tracking-wider">
      {children}
    </div>
  );
}

/* ── Redesigned completed-run sections ────────────────────────────── */

/** Compact timing line: "Started 6:58 PM · 2.2s" */
function TimingLine({ run }: { run: StepRun }) {
  const { copy: copyRunId, justCopied: runIdCopied } = useCopyFeedback();

  return (
    <div className="flex items-center gap-1.5 text-[11px] flex-wrap mt-3">
      <span className="text-zinc-300">Started {formatTimePretty(run.started_at)}</span>
      <span className="text-zinc-600">&middot;</span>
      <span className="text-zinc-200 font-medium">{formatDuration(run.started_at, run.completed_at)}</span>
      <span
        onClick={() => copyRunId(run.id)}
        className={cn(
          "cursor-pointer hover:text-blue-400 transition-colors font-mono ml-auto text-[10px]",
          runIdCopied ? "text-green-400" : "text-zinc-600"
        )}
        title="Click to copy run ID"
      >
        {run.id.slice(0, 8)}
      </span>
    </div>
  );
}

/* ── Adaptive Run Content ────────────────────────────────────────── */

interface AdaptiveSlot {
  type: "input" | "result";
  label: string; // e.g. "step_name.field_name" or result key
  stepName?: string; // for input slots: the source step (clickable)
  fieldName?: string; // for input slots: the local field name
  sourceField?: string; // for input slots: the source step's field name
  isJobInput?: boolean; // source is $job
  value: string; // stringified value
}

function buildSlots(
  inputs: Record<string, unknown> | null,
  inputBindings: InputBinding[],
  result: HandoffEnvelope | null,
): AdaptiveSlot[] {
  const slots: AdaptiveSlot[] = [];

  // Build input slots
  if (inputs) {
    const bindingMap = new Map<string, InputBinding>();
    for (const b of inputBindings) bindingMap.set(b.local_name, b);

    for (const [field, value] of Object.entries(inputs)) {
      const binding = bindingMap.get(field);
      const isJob = binding?.source_step === "$job";
      const stepName = binding?.source_step ?? "";
      const strVal = typeof value === "string" ? value
        : typeof value === "number" || typeof value === "boolean" ? String(value)
        : JSON.stringify(value, null, 2);

      slots.push({
        type: "input",
        label: isJob ? field : `${stepName}.${field}`,
        stepName: isJob ? undefined : stepName,
        fieldName: field,
        sourceField: binding?.source_field ?? field,
        isJobInput: isJob,
        value: strVal,
      });
    }
  }

  // Build result slots
  if (result?.artifact) {
    const userKeys = Object.entries(result.artifact).filter(([k]) => !k.startsWith("_"));
    for (const [key, value] of userKeys) {
      const strVal = typeof value === "string" ? value
        : typeof value === "number" || typeof value === "boolean" ? String(value)
        : JSON.stringify(value, null, 2);
      slots.push({ type: "result", label: key, value: strVal });
    }
  }

  return slots;
}

/**
 * Two-pass adaptive allocation with caching:
 *
 * Pass 1 (hidden): render with 1 line per value, visibility:hidden.
 *   Measure chrome height = scrollHeight - (slots.length * lineHeight).
 *   Cache it.
 *
 * Pass 2: compute allocation from cached chrome height + viewport.
 *   No re-render needed for resize — just recalculate from cache.
 */
function useAdaptiveAllocation(
  containerRef: React.RefObject<HTMLDivElement | null>,
  slots: AdaptiveSlot[],
  font: string,
  width: number,
) {
  const [allocation, setAllocation] = useState<number[]>(() =>
    new Array(slots.length).fill(1)
  );
  const [needsScroll, setNeedsScroll] = useState(false);
  const [ready, setReady] = useState(false);
  const chromeHeightRef = useRef(0);
  const naturalLinesRef = useRef<number[]>([]);

  // Compute natural line counts (only when slots or width change)
  useEffect(() => {
    if (width <= 0 || slots.length === 0) return;
    const lineHeight = 18;
    naturalLinesRef.current = slots.map((slot) => {
      if (!slot.value) return 1;
      const h = measureTextHeight(slot.value, font, width, lineHeight, { whiteSpace: "pre-wrap" });
      return Math.max(1, Math.ceil(h / lineHeight));
    });
  }, [slots, font, width]);

  const distribute = useCallback(() => {
    const el = containerRef.current;
    if (!el || slots.length === 0) return;

    const lineHeight = 18;
    const containerTop = el.getBoundingClientRect().top;
    const viewportBottom = window.innerHeight;
    const totalAvailable = viewportBottom - containerTop;
    const pixelsForValues = totalAvailable - chromeHeightRef.current;
    const availableLines = Math.floor(pixelsForValues / lineHeight);

    if (availableLines < slots.length) {
      setNeedsScroll(true);
      setAllocation(new Array(slots.length).fill(1));
      return;
    }
    setNeedsScroll(false);

    const naturalLines = naturalLinesRef.current;
    const alloc = new Array(slots.length).fill(1);
    let remaining = availableLines - slots.length;

    let changed = true;
    while (remaining > 0 && changed) {
      changed = false;
      for (let i = 0; i < slots.length; i++) {
        if (remaining <= 0) break;
        if (alloc[i] < (naturalLines[i] ?? 1)) {
          alloc[i]++;
          remaining--;
          changed = true;
        }
      }
    }

    setAllocation(alloc);
  }, [containerRef, slots]);

  // Pass 1: measure chrome height (runs once after first render with 1-line values)
  useEffect(() => {
    const el = containerRef.current;
    if (!el || slots.length === 0 || ready) return;

    const measure = () => {
      const lineHeight = 18;
      // Content is rendered with 1 line per slot — chrome = total - value lines
      const cs = getComputedStyle(el);
      const bottomPad = parseFloat(cs.paddingBottom) || 0;
      chromeHeightRef.current = el.scrollHeight - (slots.length * lineHeight) + bottomPad;
      setReady(true);
      distribute();
    };

    const raf = requestAnimationFrame(measure);
    return () => cancelAnimationFrame(raf);
  }, [containerRef, slots.length, ready, distribute]);

  // On resize, just redistribute from cached chrome height
  useEffect(() => {
    if (!ready) return;
    window.addEventListener("resize", distribute);
    return () => window.removeEventListener("resize", distribute);
  }, [ready, distribute]);

  // Re-distribute when ready or font/width changes
  useEffect(() => {
    if (ready) distribute();
  }, [ready, distribute, font, width]);

  // Reset when slots change (new step selected)
  useEffect(() => {
    setReady(false);
    setAllocation(new Array(slots.length).fill(1));
  }, [slots]);

  // Sidebar resize: debounce, then full re-measure
  const prevWidthRef = useRef(0);
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    let debounceTimer: ReturnType<typeof setTimeout>;
    const ro = new ResizeObserver(() => {
      const newWidth = el.clientWidth;
      if (prevWidthRef.current && newWidth !== prevWidthRef.current) {
        // Width changed (sidebar drag) — debounce and re-measure everything
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
          setReady(false);
          setAllocation(new Array(slots.length).fill(1));
        }, 150);
      }
      prevWidthRef.current = newWidth;
    });
    ro.observe(el);
    return () => { ro.disconnect(); clearTimeout(debounceTimer); };
  }, [containerRef, slots.length]);

  return { allocation, needsScroll, ready };
}

function AdaptiveRunContent({
  inputs,
  inputBindings,
  result,
  onSelectStep,
  containerRef,
  onNeedsScroll,
}: {
  inputs: Record<string, unknown> | null;
  inputBindings: InputBinding[];
  result: HandoffEnvelope | null;
  onSelectStep?: (stepName: string) => void;
  containerRef: React.RefObject<HTMLDivElement | null>;
  onNeedsScroll?: (needs: boolean) => void;
}) {
  const slots = useMemo(
    () => buildSlots(inputs, inputBindings, result),
    [inputs, inputBindings, result],
  );

  // Measure font + width ONCE while overflow:hidden (no scrollbar) — cached, not re-measured on scroll toggle
  const [textMetrics, setTextMetrics] = useState({ font: "12px monospace", width: 300 });
  const textMetricsMeasured = useRef(false);
  useEffect(() => {
    // Reset on slot change (new step selected)
    textMetricsMeasured.current = false;
  }, [slots]);
  useEffect(() => {
    if (textMetricsMeasured.current) return;
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      if (textMetricsMeasured.current) return;
      const probe = document.createElement("span");
      probe.className = "text-xs font-mono";
      probe.style.visibility = "hidden";
      probe.style.position = "absolute";
      probe.textContent = "X";
      el.appendChild(probe);
      const cs = getComputedStyle(probe);
      const font = `${cs.fontSize} ${cs.fontFamily}`;
      el.removeChild(probe);
      const containerCs = getComputedStyle(el);
      const padLeft = parseFloat(containerCs.paddingLeft) || 0;
      const padRight = parseFloat(containerCs.paddingRight) || 0;
      const contentWidth = el.clientWidth - padLeft - padRight;
      if (contentWidth > 0) {
        setTextMetrics({ font, width: contentWidth });
        textMetricsMeasured.current = true;
      }
    };
    if (document.fonts?.ready) {
      document.fonts.ready.then(measure);
    } else {
      measure();
    }
  }, [containerRef, slots]);

  const { allocation, needsScroll, ready } = useAdaptiveAllocation(containerRef, slots, textMetrics.font, textMetrics.width);

  useEffect(() => {
    onNeedsScroll?.(needsScroll);
  }, [needsScroll, onNeedsScroll]);

  const inputSlots = useMemo(() => slots.filter((s) => s.type === "input"), [slots]);
  const resultSlots = useMemo(() => slots.filter((s) => s.type === "result"), [slots]);

  if (slots.length === 0) return null;

  // allocation is ordered: all input slots first, then result slots
  const inputAllocStart = 0;
  const resultAllocStart = inputSlots.length;

  return (
    <div className="space-y-4" style={ready ? undefined : { visibility: "hidden" }}>
      {/* Inputs section */}
      {inputSlots.length > 0 && (
        <div>
          <SectionHeading>Inputs</SectionHeading>
          <div className="mt-1.5">
            {inputSlots.map((slot, i) => (
              <AdaptiveSlotRow
                key={i}
                slot={slot}
                lines={allocation[inputAllocStart + i] ?? 3}
                font={textMetrics.font}
                width={textMetrics.width}
                onSelectStep={onSelectStep}
              />
            ))}
          </div>
        </div>
      )}

      {/* Result section — card treatment */}
      {resultSlots.length > 0 && (
        <div className="bg-emerald-50 dark:bg-emerald-900/40 -mx-3 px-3 py-2.5">
          <SectionHeading>Result</SectionHeading>
          <div className="mt-1.5">
            {resultSlots.map((slot, i) => (
              <AdaptiveSlotRow
                key={i}
                slot={slot}
                lines={allocation[resultAllocStart + i] ?? 3}
                font={textMetrics.font}
                width={textMetrics.width}
                isResult
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function AdaptiveSlotRow({
  slot,
  lines,
  font,
  width,
  onSelectStep,
  isResult,
}: {
  slot: AdaptiveSlot;
  lines: number;
  font: string;
  width: number;
  onSelectStep?: (stepName: string) => void;
  isResult?: boolean;
}) {
  const [modalOpen, setModalOpen] = useState(false);

  const { text: displayText, truncated } = useMemo(
    () => truncateToLines(slot.value, font, width, 18, lines, { whiteSpace: "pre-wrap" }),
    [slot.value, font, width, lines],
  );

  return (
    <div className="py-1.5">
      {/* Label pill — field ← source */}
      <div className="mb-1">
        <span className="flex items-center rounded bg-zinc-100 dark:bg-zinc-800/60 px-2 py-1 text-xs font-mono">
          <span className="text-cyan-600 dark:text-cyan-400">{slot.fieldName ?? slot.label}</span>
          {slot.type === "input" && (
            <>
              <span className="text-zinc-400 dark:text-zinc-600 mx-1.5">←</span>
              {slot.isJobInput ? (
                <span className="text-zinc-500">$job.{slot.fieldName}</span>
              ) : (
                <span>
                  <a
                    onClick={() => slot.stepName && onSelectStep?.(slot.stepName)}
                    className="text-zinc-500 dark:text-zinc-400 hover:text-blue-500 dark:hover:text-blue-400 cursor-pointer transition-colors"
                  >
                    {slot.stepName}
                  </a>
                  <span className="text-zinc-400 dark:text-zinc-600">.{slot.sourceField}</span>
                </span>
              )}
            </>
          )}
        </span>
      </div>

      {/* Value — pretext-truncated */}
      <div
        className={cn(
          "text-xs font-mono cursor-pointer hover:text-zinc-200 transition-colors whitespace-pre-wrap break-words leading-relaxed text-zinc-400",
        )}
        onClick={() => setModalOpen(true)}
      >
        {truncated && displayText.endsWith("…")
          ? <>{displayText.slice(0, -1)}<span className="text-zinc-600">…</span></>
          : displayText}
      </div>

      <ContentModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        title={slot.label}
        copyContent={slot.value}
      >
        <pre className="whitespace-pre-wrap text-sm text-zinc-300 font-mono p-3 leading-relaxed max-h-[70vh] overflow-auto">
          {slot.value}
        </pre>
      </ContentModal>
    </div>
  );
}

/** Compact metadata footer: exit, cost, executor meta, workspace */
function MetadataFooter({
  exitRes,
  costUsd,
  isSubscription,
  showCost,
  executorMeta,
  workspace,
  sidecar,
}: {
  exitRes?: { rule: string; action: string };
  costUsd?: number;
  isSubscription: boolean;
  showCost: boolean;
  executorMeta?: Record<string, unknown>;
  workspace?: string;
  sidecar?: HandoffEnvelope["sidecar"];
}) {
  const hasExit = !!exitRes;
  const hasCost = showCost && (isSubscription || (costUsd != null && costUsd > 0));
  // Filter out keys already shown elsewhere (stdout, stderr, return_code, cost)
  const filteredMeta = useMemo(() => {
    if (!executorMeta) return null;
    const skip = new Set(["stdout", "stderr", "return_code", "cost_usd"]);
    const entries = Object.entries(executorMeta).filter(([k]) => !skip.has(k));
    return entries.length > 0 ? Object.fromEntries(entries) : null;
  }, [executorMeta]);
  const hasSidecar = sidecar && (
    sidecar.decisions_made?.length > 0 ||
    sidecar.assumptions?.length > 0 ||
    sidecar.open_questions?.length > 0 ||
    sidecar.constraints_discovered?.length > 0
  );
  const hasWorkspace = !!workspace;

  if (!hasExit && !hasCost && !filteredMeta && !hasWorkspace && !hasSidecar) return null;

  return (
    <div className="space-y-1.5 pt-2">
      {/* Exit + Cost on one line */}
      <div className="flex items-center gap-4 flex-wrap text-xs">
        {hasExit && exitRes && (
          <div className="flex items-center gap-1.5">
            <span className="text-zinc-600">Exit:</span>
            <span
              className={cn(
                "font-medium",
                exitRes.action === "advance" && "text-emerald-400",
                exitRes.action === "loop" && "text-purple-400",
                exitRes.action === "escalate" && "text-red-400",
                exitRes.action === "abandon" && "text-red-500"
              )}
            >
              {exitRes.rule}
            </span>
            <span className="text-zinc-600">&rarr;</span>
            <span className="text-zinc-400">{exitRes.action}</span>
          </div>
        )}
        {hasCost && (
          <span className="font-mono text-emerald-600 dark:text-emerald-400 text-[11px]">
            {costUsd != null && costUsd > 0
              ? formatCostDisplay(costUsd)
              : isSubscription
                ? "$0 (Max)"
                : formatCostDisplay(costUsd)}
          </span>
        )}
      </div>

      {/* Sidecar collapsible */}
      {hasSidecar && sidecar && (
        <CollapsibleSection title="Sidecar">
          <div className="space-y-1.5">
            {sidecar.decisions_made?.length > 0 && (
              <SidecarList label="Decisions" items={sidecar.decisions_made} />
            )}
            {sidecar.assumptions?.length > 0 && (
              <SidecarList label="Assumptions" items={sidecar.assumptions} />
            )}
            {sidecar.open_questions?.length > 0 && (
              <SidecarList label="Open questions" items={sidecar.open_questions} />
            )}
            {sidecar.constraints_discovered?.length > 0 && (
              <SidecarList label="Constraints" items={sidecar.constraints_discovered} />
            )}
          </div>
        </CollapsibleSection>
      )}

      {/* Executor meta + workspace on one compact line */}
      {(filteredMeta || (hasWorkspace && workspace)) && (
        <div className="flex items-center gap-3 flex-wrap">
          {filteredMeta && <ExecutorMetaLink meta={filteredMeta} />}
          {hasWorkspace && workspace && <WorkspaceInline workspace={workspace} />}
        </div>
      )}
    </div>
  );
}

/** Executor meta as a clickable link that opens a modal */
function ExecutorMetaLink({ meta }: { meta: Record<string, unknown> }) {
  const [modalOpen, setModalOpen] = useState(false);
  const metaStr = JSON.stringify(meta, null, 2);

  return (
    <>
      <button
        onClick={() => setModalOpen(true)}
        className="text-[10px] text-zinc-500 hover:text-zinc-300 transition-colors"
      >
        Executor meta &rarr;
      </button>
      <ContentModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        title="Executor Meta"
        copyContent={metaStr}
      >
        <JsonView data={meta} defaultExpanded={true} />
      </ContentModal>
    </>
  );
}

/** Workspace displayed inline with click-to-copy */
function WorkspaceInline({ workspace }: { workspace: string }) {
  const { copy, justCopied } = useCopyFeedback();

  // Trim to relative path from jobs/ directory
  const jobsIdx = workspace.indexOf("/jobs/");
  const displayPath = jobsIdx !== -1 ? workspace.slice(jobsIdx + 1) : workspace;

  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[10px] text-zinc-600">Workspace:</span>
      <button
        onClick={() => copy(workspace)}
        className="text-[10px] font-mono text-zinc-500 hover:text-zinc-300 transition-colors truncate"
        title="Click to copy full path"
      >
        {displayPath}
      </button>
      {justCopied && (
        <span className="text-[9px] text-green-400 animate-in fade-in duration-150">Copied</span>
      )}
    </div>
  );
}

function SidecarList({ label, items }: { label: string; items: string[] }) {
  return (
    <div>
      <span className="text-[10px] text-zinc-500 uppercase tracking-wider">{label}</span>
      <ul className="mt-0.5 space-y-0.5">
        {items.map((item, i) => (
          <li key={i} className="text-xs text-zinc-400 pl-2">
            &bull; {typeof item === "string" ? item : JSON.stringify(item)}
          </li>
        ))}
      </ul>
    </div>
  );
}

function CollapsibleSection({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex items-center gap-1 text-[10px] text-zinc-500 hover:text-zinc-300 transition-colors w-full">
        <ChevronRight
          className={cn("w-2.5 h-2.5 transition-transform", open && "rotate-90")}
        />
        <span>{title}</span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="ml-3.5 mt-1">{children}</div>
      </CollapsibleContent>
    </Collapsible>
  );
}

/* ── Log highlighting ──────────────────────────────────────────────── */

function highlightLogLine(line: string): React.ReactNode {
  const timestampRe = /^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*Z?|\[\d{2}:\d{2}:\d{2}\]|\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})/;
  const errorRe = /\b(ERROR|FATAL|CRITICAL|PANIC)\b/i;
  const warnRe = /\b(WARN|WARNING)\b/i;
  const infoRe = /\b(INFO)\b/i;
  const debugRe = /\b(DEBUG|TRACE)\b/i;

  let className = "text-zinc-700 dark:text-zinc-300";
  if (errorRe.test(line)) className = "text-red-400";
  else if (warnRe.test(line)) className = "text-amber-400";
  else if (infoRe.test(line)) className = "text-blue-400";
  else if (debugRe.test(line)) className = "text-zinc-500 dark:text-zinc-400";

  const tsMatch = line.match(timestampRe);
  if (tsMatch) {
    return (
      <span className={className}>
        <span className="text-zinc-600">{tsMatch[0]}</span>
        {line.slice(tsMatch[0].length)}
      </span>
    );
  }
  return <span className={className}>{line}</span>;
}

/* ── Script log viewer (completed runs) ───────────────────────────── */

function ScriptLogView({ run }: { run: StepRun }) {
  const [copied, setCopied] = useState(false);
  const stdout = (run.result?.executor_meta?.stdout as string) ?? "";
  const stderr = (run.result?.executor_meta?.stderr as string) ?? "";
  const returnCode = run.result?.executor_meta?.return_code as number | undefined;
  if (!stdout && !stderr) return null;

  const fullText = [stdout, stderr ? `--- stderr ---\n${stderr}` : ""].filter(Boolean).join("\n");
  const lines = fullText.split("\n");

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-1.5 text-xs text-zinc-500">
          <Terminal className="w-3 h-3" />
          <span>Logs</span>
          {returnCode != null && (
            <span className={cn(
              "font-mono text-[10px] px-1 py-0.5 rounded",
              returnCode === 0 ? "text-emerald-400 bg-emerald-500/10" : "text-red-400 bg-red-500/10"
            )}>
              exit {returnCode}
            </span>
          )}
        </div>
        <button
          onClick={() => {
            navigator.clipboard.writeText(fullText);
            setCopied(true);
            toast.success("Copied to clipboard");
            setTimeout(() => setCopied(false), 2000);
          }}
          className="flex items-center gap-1 text-[10px] text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
        >
          {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <div className="bg-zinc-50 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 rounded overflow-hidden">
        <VirtualizedLogView
          lines={lines}
          className="text-[11px] font-mono p-2 text-zinc-700 dark:text-zinc-300 whitespace-pre-wrap break-all leading-relaxed"
          renderLine={(line) => highlightLogLine(line)}
        />
      </div>
    </div>
  );
}

/* ── Live script log viewer (running) ─────────────────────────────── */

function LiveScriptLogView({ runId }: { runId: string }) {
  const { stdout, stderr, truncated, version } = useScriptStream(runId);
  if (!stdout && !stderr) {
    return (
      <div className="text-xs text-zinc-500 italic py-4 text-center">
        <div className="flex items-center justify-center gap-2">
          <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
          Script running...
        </div>
      </div>
    );
  }

  const rawLines = stdout.split("\n");
  if (rawLines.length > 0 && rawLines[rawLines.length - 1] === "") rawLines.pop();

  return (
    <div>
      <div className="text-xs text-zinc-500 dark:text-zinc-500 mb-1 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
          Live Output
        </div>
        <button
          onClick={() => { navigator.clipboard.writeText(stdout); toast.success("Copied to clipboard"); }}
          className="flex items-center gap-1 text-[10px] text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 transition-colors"
        >
          <Copy className="w-3 h-3" /> Copy
        </button>
      </div>
      {truncated && (
        <div className="text-[10px] text-amber-400/70 mb-1 font-mono">[earlier output truncated]</div>
      )}
      <div className="bg-zinc-50 dark:bg-zinc-950 rounded border border-zinc-200 dark:border-zinc-800">
        <VirtualizedLogView
          lines={rawLines}
          isLive={true}
          version={version}
          className="p-2 font-mono text-xs"
          renderLine={(line) => (
            <span className="whitespace-pre-wrap break-words leading-relaxed">
              {line === "" ? "\u00A0" : highlightLogLine(line)}
            </span>
          )}
        />
      </div>
      {stderr && (
        <div className="mt-2">
          <div className="text-xs text-red-400/70 dark:text-red-400/70 mb-1">stderr</div>
          <pre className="bg-zinc-50 dark:bg-zinc-950 rounded border border-red-300/20 dark:border-red-500/20 p-2 font-mono text-xs text-red-600 dark:text-red-300/80 max-h-48 overflow-auto whitespace-pre-wrap break-words">
            {stderr}
          </pre>
        </div>
      )}
    </div>
  );
}

/* ── Agent raw event viewer ───────────────────────────────────────── */

function AgentRawView({ runId }: { runId: string }) {
  const { data } = useAgentOutput(runId);
  const [copied, setCopied] = useState(false);
  const text = (data?.events ?? []).map((e: unknown) => JSON.stringify(e)).join("\n");

  if (!text) return <div className="text-xs text-zinc-600">No output</div>;

  return (
    <div>
      <div className="flex justify-end mb-1">
        <button
          onClick={() => {
            navigator.clipboard.writeText(text);
            setCopied(true);
            toast.success("Copied to clipboard");
            setTimeout(() => setCopied(false), 2000);
          }}
          className="flex items-center gap-1 text-[10px] text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
        >
          {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
          {copied ? "Copied" : "Copy All"}
        </button>
      </div>
      <pre className="text-[10px] font-mono bg-zinc-50 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 rounded p-2 text-zinc-600 dark:text-zinc-400 whitespace-pre-wrap break-all max-h-96 overflow-auto">
        {text}
      </pre>
    </div>
  );
}

/* ── Truncated prompt with click-to-expand modal ──────────────────── */

function PromptBlock({ prompt }: { prompt: string }) {
  const [modalOpen, setModalOpen] = useState(false);
  const lines = prompt.trim().split("\n");
  const isTruncated = lines.length > 4;

  return (
    <div>
      <div
        onClick={() => setModalOpen(true)}
        className={cn(
          "text-xs font-mono bg-zinc-50 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded p-2 whitespace-pre-wrap break-all cursor-pointer hover:bg-zinc-50/80 dark:hover:bg-zinc-900/70 transition-colors",
          "text-emerald-700 dark:text-emerald-300",
          isTruncated && "line-clamp-4"
        )}
      >
        {prompt.trim()}
      </div>
      <ContentModal
        open={modalOpen}
        onOpenChange={setModalOpen}
        title="Prompt"
        copyContent={prompt.trim()}
      >
        <pre className="whitespace-pre-wrap text-sm text-zinc-300 font-mono p-2">
          {prompt.trim()}
        </pre>
      </ContentModal>
    </div>
  );
}

/* ── Run navigator header ─────────────────────────────────────────── */

function RunNavigator({
  runs,
  currentIndex,
  onSelect,
  stepName,
}: {
  runs: StepRun[];
  currentIndex: number;
  onSelect: (idx: number) => void;
  stepName: string;
}) {
  const run = runs[currentIndex];
  if (!run) return null;

  const total = runs.length;
  // runs are sorted newest-first, so attempt number = total - currentIndex
  const canPrev = currentIndex < total - 1;
  const canNext = currentIndex > 0;

  return (
    <div className="flex items-center gap-2 text-xs">
      <div className="flex items-center gap-0.5">
        <button
          onClick={() => canPrev && onSelect(currentIndex + 1)}
          disabled={!canPrev}
          className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 disabled:opacity-30 disabled:cursor-default"
          aria-label="Previous attempt"
        >
          <ChevronLeft className="w-3.5 h-3.5" />
        </button>
        <span className="text-zinc-500 font-mono text-[10px] min-w-[2rem] text-center">
          {run.attempt} of {total}
        </span>
        <button
          onClick={() => canNext && onSelect(currentIndex - 1)}
          disabled={!canNext}
          className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 disabled:opacity-30 disabled:cursor-default"
          aria-label="Next attempt"
        >
          <ChevronRight className="w-3.5 h-3.5" />
        </button>
      </div>
      <span className="text-zinc-500 truncate">{stepName}</span>
      <span className="ml-auto"><StepStatusBadge status={run.status} /></span>
    </div>
  );
}

/* ── Main RunView ─────────────────────────────────────────────────── */

export function RunView({ jobId, stepDef, hasLiveSource, onSelectStep }: RunViewProps) {
  const { data: runs = [] } = useRuns(jobId, stepDef.name);
  const { data: events = [] } = useEvents(jobId);
  const mutations = useStepwiseMutations();
  const [agentViewMode, setAgentViewMode] = useState<"stream" | "raw">("stream");
  const [runIndex, setRunIndex] = useState(0);

  const { data: configData } = useConfig();
  const isSubscription = configData?.billing_mode === "subscription";

  const containerRef = useRef<HTMLDivElement>(null);
  const [scrollEnabled, setScrollEnabled] = useState(false);

  // Reset scroll state on step change
  useEffect(() => {
    setScrollEnabled(false);
  }, [stepDef.name]);

  // Constrain container to viewport height + check overflow once after content settles
  const [maxHeight, setMaxHeight] = useState<string>("100%");
  const overflowChecked = useRef(false);
  useEffect(() => { overflowChecked.current = false; }, [stepDef.name]);
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      const mh = window.innerHeight - rect.top;
      setMaxHeight(`${mh}px`);

      // One-time overflow check after content renders
      if (!overflowChecked.current) {
        overflowChecked.current = true;
        // Wait for content to render, then check if it overflows
        setTimeout(() => {
          if (!el) return;
          // Temporarily measure with overflow visible
          const saved = el.style.overflow;
          el.style.overflow = "hidden";
          const overflows = el.scrollHeight > el.clientHeight + 2;
          el.style.overflow = saved;
          if (overflows) setScrollEnabled(true);
        }, 300);
      }
    };
    const timer = setTimeout(measure, 50);
    window.addEventListener("resize", measure);
    return () => { clearTimeout(timer); window.removeEventListener("resize", measure); };
  }, [stepDef.name, runs]);

  const isAgent = stepDef.executor.type === "agent";
  const isExternal = stepDef.executor.type === "external";
  const isScript = stepDef.executor.type === "script";

  const sortedRuns = useMemo(
    () => [...runs].sort((a, b) => b.attempt - a.attempt),
    [runs],
  );

  // Reset index when step changes or new runs come in
  useEffect(() => { setRunIndex(0); }, [stepDef.name]);
  useEffect(() => {
    if (runIndex >= sortedRuns.length && sortedRuns.length > 0) setRunIndex(0);
  }, [sortedRuns.length, runIndex]);

  const run = sortedRuns[runIndex] ?? null;
  const isRunning = run?.status === "running";
  const isFailed = run?.status === "failed";
  const isSuspended = run?.status === "suspended";

  // Live cost for running agent steps
  const { data: costData } = useRunCost(isRunning ? run?.id : undefined);

  // Exit resolutions
  const exitResolutions = useMemo(() => {
    const map: Record<number, { rule: string; action: string }> = {};
    let attemptCounter = 0;
    for (const e of events) {
      if (e.type === "exit.resolved" && e.data.step === stepDef.name) {
        attemptCounter++;
        map[attemptCounter] = {
          rule: e.data.rule as string,
          action: e.data.action as string,
        };
      }
    }
    return map;
  }, [events, stepDef.name]);

  // Get interpolated prompt for a run
  const getRunPrompt = (r: StepRun): string | undefined => {
    if (isExternal) {
      const wp = r.watch?.config?.prompt;
      return typeof wp === "string" ? wp : undefined;
    }
    const ic = r.executor_state?._interpolated_config as Record<string, unknown> | undefined;
    return typeof ic?.prompt === "string" ? ic.prompt : undefined;
  };


  const canRerun =
    !run ||
    run.status === "completed" ||
    run.status === "failed" ||
    run.status === "cancelled" ||
    run.status === "skipped";

  // Should we show cost for this step?
  const showCost = isAgent || stepDef.executor.type === "llm";

  if (sortedRuns.length === 0) {
    return (
      <div className="p-4 space-y-3 animate-step-fade">
        <div className="text-xs text-zinc-500 italic text-center py-8">
          No runs yet
        </div>
        {canRerun && (
          <Button
            variant="outline"
            size="sm"
            disabled={mutations.rerunStep.isPending}
            onClick={() => mutations.rerunStep.mutate({ jobId, stepName: stepDef.name })}
          >
            <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
            Run Step
          </Button>
        )}
      </div>
    );
  }

  const runPrompt = run ? getRunPrompt(run) : undefined;
  const exitRes = run ? exitResolutions[run.attempt] : undefined;

  return (
    <div ref={containerRef} className={cn("px-3 pb-4 space-y-3 animate-step-fade", scrollEnabled ? "overflow-y-auto" : "overflow-hidden")} style={{ maxHeight }}>
      {/* 1. Header bar — run navigator */}
      <div className="sticky top-0 z-10 -mx-3 px-3 pt-3 pb-2 border-b border-border bg-zinc-50/80 dark:bg-zinc-950/80 backdrop-blur-sm">
        <RunNavigator
          runs={sortedRuns}
          currentIndex={runIndex}
          onSelect={setRunIndex}
          stepName={stepDef.name}
        />
      </div>

      {/* 2. Compact timing line */}
      {run && !isRunning && <TimingLine run={run} />}

      {/* Running: show live timing instead */}
      {run && isRunning && (
        <div className="flex items-center gap-1.5 text-[11px] flex-wrap mt-3">
          <span className="text-zinc-300">Started {formatTimePretty(run.started_at)}</span>
          <span className="text-zinc-600">&middot;</span>
          <span className="text-blue-400 font-medium">
            <LiveDuration startTime={run.started_at} endTime={run.completed_at} />
          </span>
        </div>
      )}

      {/* 3. Adaptive inputs + result */}
      {run && (run?.inputs || run?.result) && (
        <AdaptiveRunContent
          inputs={run.inputs}
          inputBindings={stepDef.inputs}
          result={run.result}
          onSelectStep={onSelectStep}
          containerRef={containerRef}
          onNeedsScroll={setScrollEnabled}
        />
      )}

      {/* 4. Prompt (interpolated) */}
      {runPrompt && (
        <div className="space-y-1.5">
          <SectionHeading>Prompt</SectionHeading>
          <PromptBlock prompt={runPrompt} />
        </div>
      )}

      {/* 5. Output area */}
      {/* Live agent stream */}
      {run && isRunning && isAgent && (
        <div className="max-h-[60vh] overflow-y-auto rounded-lg border border-zinc-800/50">
          <AgentStreamView
            runId={run.id}
            isLive={true}
            startedAt={run.started_at}
            costUsd={costData?.cost_usd}
            billingMode={costData?.billing_mode}
          />
        </div>
      )}

      {/* Live script output */}
      {run && isRunning && isScript && (
        <div className="max-h-[60vh] overflow-y-auto rounded-lg border border-zinc-800/50">
          <LiveScriptLogView runId={run.id} />
        </div>
      )}

      {/* Usage limit waiting */}
      {run?.status === "running" && !!(run?.executor_state as Record<string, unknown> | undefined)?.usage_limit_waiting && (() => {
        const es = run.executor_state as Record<string, unknown>;
        const resetAt = es.reset_at ? String(es.reset_at) : null;
        const limitMsg = es.usage_limit_message ? String(es.usage_limit_message) : null;
        return (
          <div className="rounded-md bg-amber-100 dark:bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-300 border border-amber-500/20">
            <div className="font-medium">Usage limit reached</div>
            {resetAt && <div>Resuming at {new Date(resetAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>}
            {limitMsg && <div className="mt-1 text-xs opacity-75">{limitMsg}</div>}
          </div>
        );
      })()}

      {/* Suspended external — fulfillment status */}
      {run && isSuspended && isExternal && (
        <div className="rounded-md bg-blue-100 dark:bg-blue-500/10 p-3 text-sm text-blue-700 dark:text-blue-300 border border-blue-500/20">
          <div className="font-medium">Waiting for input</div>
          <div className="text-xs mt-1 opacity-75">
            This step is suspended and waiting for external fulfillment.
          </div>
        </div>
      )}

      {/* Completed agent replay */}
      {run?.result && isAgent && (
        <div className="space-y-1.5">
          <div className="flex items-center justify-between">
            <SectionHeading>Agent Output</SectionHeading>
            <div className="flex items-center gap-0.5 bg-zinc-200 dark:bg-zinc-800 rounded p-0.5">
              <button
                onClick={() => setAgentViewMode("stream")}
                className={cn(
                  "text-[10px] px-2 py-0.5 rounded transition-colors",
                  agentViewMode === "stream"
                    ? "bg-white dark:bg-zinc-700 text-zinc-800 dark:text-zinc-200"
                    : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
                )}
              >
                Stream
              </button>
              <button
                onClick={() => setAgentViewMode("raw")}
                className={cn(
                  "text-[10px] px-2 py-0.5 rounded transition-colors",
                  agentViewMode === "raw"
                    ? "bg-white dark:bg-zinc-700 text-zinc-800 dark:text-zinc-200"
                    : "text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300"
                )}
              >
                Raw
              </button>
            </div>
          </div>
          <div className="max-h-[60vh] overflow-y-auto">
            {agentViewMode === "stream" ? (
              <AgentStreamView runId={run.id} isLive={false} />
            ) : (
              <AgentRawView runId={run.id} />
            )}
          </div>
        </div>
      )}

      {/* Completed script logs */}
      {run?.result && isScript && (
        <div className="max-h-[60vh] overflow-y-auto rounded-lg border border-zinc-800/50">
          <ScriptLogView run={run} />
        </div>
      )}

      {/* Fulfillment notes */}
      {run?.result?.artifact?._fulfillment_notes != null && (
        <div className="text-xs text-zinc-400 bg-zinc-900/30 rounded-lg border border-zinc-800 px-3 py-2 whitespace-pre-wrap">
          {String(run.result.artifact._fulfillment_notes)}
        </div>
      )}

      {/* 7. Error */}
      {run && isFailed && run.error && (
        <div className="space-y-1.5">
          <div className="bg-red-500/10 border border-red-500/20 rounded-lg p-2.5 text-sm">
            <div className="flex items-center gap-1.5 text-red-400 mb-1">
              <AlertTriangle className="w-3.5 h-3.5" />
              <span className="font-medium text-xs">Error</span>
              {run.error_category && (
                <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-red-500/20 text-red-300">
                  {run.error_category}
                </span>
              )}
            </div>
            <div className="text-red-300/80 text-xs font-mono whitespace-pre-wrap break-words">
              {run.error}
            </div>
          </div>
          {run.traceback && (
            <details className="text-xs">
              <summary className="text-zinc-500 cursor-pointer hover:text-zinc-400">Traceback</summary>
              <pre className="mt-1 text-[10px] font-mono text-red-300/60 whitespace-pre-wrap break-all bg-zinc-950 rounded p-2 max-h-48 overflow-auto">
                {run.traceback}
              </pre>
            </details>
          )}
          <Button
            variant="outline"
            size="sm"
            className="border-amber-500/30 text-amber-400 hover:bg-amber-500/10"
            disabled={mutations.rerunStep.isPending}
            onClick={() => mutations.rerunStep.mutate({ jobId, stepName: stepDef.name })}
          >
            <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
            Retry
          </Button>
        </div>
      )}

      {/* 8. Metadata footer */}
      {run?.result && (
        <MetadataFooter
          exitRes={exitRes}
          costUsd={run.result.executor_meta?.cost_usd as number | undefined}
          isSubscription={isSubscription}
          showCost={showCost}
          executorMeta={run.result.executor_meta}
          workspace={run.result.workspace}
          sidecar={run.result.sidecar}
        />
      )}

      {/* Exit resolution for runs without result (e.g. failed) */}
      {!run?.result && exitRes && (
        <div className="flex items-center gap-1.5 text-xs">
          <span className="text-zinc-600">Exit:</span>
          <span
            className={cn(
              "font-medium",
              exitRes.action === "advance" && "text-emerald-400",
              exitRes.action === "loop" && "text-purple-400",
              exitRes.action === "escalate" && "text-red-400",
              exitRes.action === "abandon" && "text-red-500"
            )}
          >
            {exitRes.rule}
          </span>
          <span className="text-zinc-600">&rarr;</span>
          <span className="text-zinc-400">{exitRes.action}</span>
        </div>
      )}

      {/* Actions for non-failed runs */}
      {run && !isFailed && (
        <div className="flex gap-2 pb-1">
          {canRerun && (
            <Button
              variant="outline"
              size="sm"
              disabled={mutations.rerunStep.isPending}
              onClick={() => mutations.rerunStep.mutate({ jobId, stepName: stepDef.name })}
            >
              <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
              Restart
            </Button>
          )}
          {isRunning && (
            <Button
              variant="outline"
              size="sm"
              className="border-red-500/30 text-red-400 hover:bg-red-500/10"
              disabled={mutations.cancelRun.isPending}
              onClick={() => mutations.cancelRun.mutate(run.id)}
            >
              <StopCircle className="w-3.5 h-3.5 mr-1.5" />
              Cancel
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
