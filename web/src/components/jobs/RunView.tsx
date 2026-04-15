import { useState, useMemo, useEffect, useRef, useCallback } from "react";
import { useRuns, useEvents, useRunCost, useStepwiseMutations, useJobSessions, useSessionStepEntries } from "@/hooks/useStepwise";
import { useConfig } from "@/hooks/useConfig";
import type { StepDefinition, StepRun, HandoffEnvelope, InputBinding } from "@/lib/types";
import { StepStatusBadge } from "@/components/StatusBadge";
import { AgentStreamView } from "./AgentStreamView";
import { SessionStepFlow } from "./SessionStepFlow";
import { SectionHeading, SidebarSection, InputsSection, OutputsSection } from "./RunSections";
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
  Maximize2,
  ArrowDown,
} from "lucide-react";
import { useCopyFeedback } from "@/hooks/useCopyFeedback";
import { ContentModal } from "@/components/ui/content-modal";
import { useScriptStream } from "@/hooks/useScriptStream";
import { toast } from "sonner";
import { cn, formatCost, formatDuration } from "@/lib/utils";
import { VirtualizedLogView } from "@/components/logs/VirtualizedLogView";
import { LiveDuration } from "@/components/LiveDuration";

interface RunViewProps {
  jobId: string;
  stepDef: StepDefinition;
  hasLiveSource?: boolean;
  onSelectStep?: (stepName: string) => void;
  onViewFullSession?: (sessionName: string) => void;
}

/* ── Helpers ───────────────────────────────────────────────────────── */

function formatCostDisplay(cost: number | null | undefined): string {
  if (cost == null || cost === 0) return "-";
  return formatCost(cost);
}

/** Pretty timestamp: "6:58 PM" */
function formatTokensCompact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatTimePretty(ts: string | null): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}


/* ── Redesigned completed-run sections ────────────────────────────── */

/** Compact timing line: "Started 6:58 PM · 2.2s" */
function TimingLine({ run }: { run: StepRun }) {
  const { copy: copyRunId, justCopied: runIdCopied } = useCopyFeedback();

  return (
    <div className="flex items-center gap-3 text-[11px] flex-wrap mt-3">
      <span className="text-zinc-300">Started {formatTimePretty(run.started_at)}</span>
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

/* ── Run Content (inputs + outputs) ──────────────────────────────── */

function RunContentTable({
  inputs,
  inputBindings,
  result,
  onSelectStep,
  executorType,
}: {
  inputs: Record<string, unknown> | null;
  inputBindings: InputBinding[];
  result: HandoffEnvelope | null;
  onSelectStep?: (stepName: string) => void;
  executorType?: string;
}) {
  const hasInputs = inputs && Object.keys(inputs).length > 0;
  const hasResult = result?.artifact && Object.keys(result.artifact).filter(k => !k.startsWith("_")).length > 0;
  if (!hasInputs && !hasResult) return null;

  return (
    <>
      {hasInputs && (
        <InputsSection inputs={inputs!} inputBindings={inputBindings} onSelectStep={onSelectStep} />
      )}
      {hasResult && (
        <OutputsSection result={result!} executorType={executorType} />
      )}
    </>
  );
}

/** Run details table — exit, cost, workspace, meta in a clean grid */
function RunDetailsTable({
  exitRes,
  costUsd,
  isSubscription,
  showCost,
  executorMeta,
  workspace,
}: {
  exitRes?: { rule: string; action: string };
  costUsd?: number;
  isSubscription: boolean;
  showCost: boolean;
  executorMeta?: Record<string, unknown>;
  workspace?: string;
}) {
  const { copy: copyWs, justCopied: wsCopied } = useCopyFeedback();
  const [metaModalOpen, setMetaModalOpen] = useState(false);
  const [rawResponseModalOpen, setRawResponseModalOpen] = useState(false);

  const hasCost = showCost && (isSubscription || (costUsd != null && costUsd > 0));
  const filteredMeta = useMemo(() => {
    if (!executorMeta) return null;
    const skip = new Set([
      "stdout",
      "stderr",
      "return_code",
      "cost_usd",
      // These are shown inline in the parse-failure block below.
      "raw_content",
      "raw_tool_calls",
      "raw_response",
    ]);
    const entries = Object.entries(executorMeta).filter(([k]) => !skip.has(k));
    return entries.length > 0 ? Object.fromEntries(entries) : null;
  }, [executorMeta]);

  // LLM parse failure: surface raw_content / raw_tool_calls / raw_response
  // inline so diagnosing "Output parse failure" doesn't require clicking
  // through the Meta modal. One click to expand in a modal; click the
  // preview chip to open.
  const parseFailureDetails = useMemo(() => {
    if (!executorMeta) return null;
    if (!executorMeta.failed) return null;
    const rawContent = executorMeta.raw_content as string | null | undefined;
    const rawToolCalls = executorMeta.raw_tool_calls as unknown;
    const rawResponse = executorMeta.raw_response as Record<string, unknown> | null | undefined;
    const errorText = (executorMeta.error as string | undefined) ?? "failed";
    if (!rawContent && !rawToolCalls && !rawResponse) {
      return { errorText, hasBody: false } as const;
    }
    return {
      errorText,
      hasBody: true,
      rawContent,
      rawToolCalls,
      rawResponse,
    } as const;
  }, [executorMeta]);

  const hasAnything =
    !!exitRes || hasCost || !!filteredMeta || !!workspace || !!parseFailureDetails;
  if (!hasAnything) return null;

  // Shorten workspace for display
  const displayWs = workspace
    ? (() => { const i = workspace.indexOf("/jobs/"); return i !== -1 ? workspace.slice(i + 1) : workspace; })()
    : null;

  return (
    <div className="text-xs space-y-1.5">
      {exitRes && (
        <div className="flex items-baseline gap-2">
          <span className="text-zinc-500 w-16 shrink-0">Exit</span>
          {exitRes.rule === "implicit_advance" ? (
            <span className="font-medium text-emerald-400">implicit advance</span>
          ) : (
            <span className="flex items-baseline gap-1.5">
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
            </span>
          )}
        </div>
      )}
      {hasCost && (
        <div className="flex items-baseline gap-2">
          <span className="text-zinc-500 w-16 shrink-0">Cost</span>
          <span className="font-mono text-emerald-400">
            {costUsd != null && costUsd > 0
              ? formatCostDisplay(costUsd)
              : isSubscription ? "$0 (Max)" : "-"}
          </span>
        </div>
      )}
      {workspace && displayWs && (
        <div className="flex items-baseline gap-2">
          <span className="text-zinc-500 w-16 shrink-0">Workspace</span>
          <span
            onClick={() => copyWs(workspace)}
            className={cn(
              "font-mono truncate cursor-pointer hover:text-blue-400 transition-colors",
              wsCopied ? "text-green-400" : "text-zinc-500"
            )}
            title="Click to copy full path"
          >
            {displayWs}
          </span>
        </div>
      )}
      {parseFailureDetails && (
        <div className="mt-1 rounded border border-red-900/40 bg-red-950/20 p-2 space-y-1.5">
          <div className="flex items-baseline gap-2">
            <span className="text-red-400 font-medium text-[11px] uppercase tracking-wide">
              Parse failure
            </span>
            <span className="text-zinc-400 truncate">{parseFailureDetails.errorText}</span>
          </div>
          {parseFailureDetails.hasBody ? (
            <>
              {typeof parseFailureDetails.rawContent === "string" && parseFailureDetails.rawContent.length > 0 && (
                <div>
                  <div className="text-[10px] text-zinc-500 uppercase tracking-wide mb-0.5">Raw content</div>
                  <pre className="font-mono text-[11px] text-zinc-300 whitespace-pre-wrap max-h-40 overflow-auto bg-zinc-950/60 rounded p-1.5">
                    {parseFailureDetails.rawContent}
                  </pre>
                </div>
              )}
              {parseFailureDetails.rawToolCalls != null && (
                <div>
                  <div className="text-[10px] text-zinc-500 uppercase tracking-wide mb-0.5">Raw tool_calls</div>
                  <pre className="font-mono text-[11px] text-zinc-300 whitespace-pre-wrap max-h-40 overflow-auto bg-zinc-950/60 rounded p-1.5">
                    {JSON.stringify(parseFailureDetails.rawToolCalls, null, 2)}
                  </pre>
                </div>
              )}
              {parseFailureDetails.rawResponse && (
                <div>
                  <button
                    onClick={() => setRawResponseModalOpen(true)}
                    className="text-[11px] text-blue-400 hover:text-blue-300 underline"
                  >
                    Open full API response →
                  </button>
                  <ContentModal
                    open={rawResponseModalOpen}
                    onOpenChange={setRawResponseModalOpen}
                    title="Raw API response"
                    copyContent={JSON.stringify(parseFailureDetails.rawResponse, null, 2)}
                  >
                    <JsonView data={parseFailureDetails.rawResponse} defaultExpanded={true} />
                  </ContentModal>
                </div>
              )}
            </>
          ) : (
            <div className="text-[11px] text-zinc-500 italic">
              No raw content, tool calls, or API response captured. Re-run to capture.
            </div>
          )}
        </div>
      )}
      {filteredMeta && (
        <div className="flex items-baseline gap-2">
          <span className="text-zinc-500 w-16 shrink-0">Meta</span>
          <button
            onClick={() => setMetaModalOpen(true)}
            className="text-zinc-500 hover:text-blue-400 transition-colors cursor-pointer font-mono"
          >
            {Object.keys(filteredMeta).join(", ")}
          </button>
          <ContentModal
            open={metaModalOpen}
            onOpenChange={setMetaModalOpen}
            title="Executor Meta"
            copyContent={JSON.stringify(filteredMeta, null, 2)}
          >
            <JsonView data={filteredMeta} defaultExpanded={true} />
          </ContentModal>
        </div>
      )}
    </div>
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

/* ── Script log viewer (completed runs) — renders inside SidebarSection ── */

function ScriptLogView({ run }: { run: StepRun }) {
  const stdout = (run.result?.executor_meta?.stdout as string) ?? "";
  const stderr = (run.result?.executor_meta?.stderr as string) ?? "";
  if (!stdout && !stderr) return null;

  const fullText = [stdout, stderr ? `--- stderr ---\n${stderr}` : ""].filter(Boolean).join("\n");
  const lines = fullText.split("\n");

  return (
    <div className="-mx-3 -mb-4">
      <VirtualizedLogView
        lines={lines}
        inline
        className="text-[11px] font-mono px-3 py-2 text-zinc-700 dark:text-zinc-300 whitespace-pre-wrap break-all leading-relaxed"
        renderLine={(line) => highlightLogLine(line)}
      />
    </div>
  );
}

/* ── Live script log viewer (running) — renders inside SidebarSection ── */

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
    <div className="-mx-3 -mb-4">
      {truncated && (
        <div className="text-[10px] text-amber-400/70 mb-1 px-3 font-mono">[earlier output truncated]</div>
      )}
      <VirtualizedLogView
        lines={rawLines}
        isLive={true}
        version={version}
        inline
        className="px-3 py-2 font-mono text-xs"
        renderLine={(line) => (
          <span className="whitespace-pre-wrap break-words leading-relaxed">
            {line === "" ? "\u00A0" : highlightLogLine(line)}
          </span>
        )}
      />
      {stderr && (
        <div className="px-3 pb-2">
          <div className="text-xs text-red-400/70 dark:text-red-400/70 mb-1">stderr</div>
          <pre className="rounded border border-red-300/20 dark:border-red-500/20 p-2 font-mono text-xs text-red-600 dark:text-red-300/80 max-h-48 overflow-auto whitespace-pre-wrap break-words">
            {stderr}
          </pre>
        </div>
      )}
    </div>
  );
}

/* ── Script output sidebar section wrapper ────────────────────────── */

function ScriptOutputSection({
  returnCode,
  fullText,
  isLive,
  children,
}: {
  returnCode?: number;
  fullText?: string;
  isLive?: boolean;
  children: React.ReactNode;
}) {
  const { copy, justCopied } = useCopyFeedback();
  const [modalOpen, setModalOpen] = useState(false);

  return (
    <>
      <SidebarSection
        title="Output"
        detail={
          <span className="flex items-center gap-1.5">
            {isLive && (
              <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse inline-block" />
            )}
            {returnCode != null && (
              <span className={cn(
                "font-mono text-[10px] px-1 py-0.5 rounded",
                returnCode === 0 ? "text-emerald-400 bg-emerald-500/10" : "text-red-400 bg-red-500/10"
              )}>
                exit {returnCode}
              </span>
            )}
            {fullText && (
              <button
                onClick={(e) => { e.stopPropagation(); copy(fullText); }}
                className="text-zinc-600 hover:text-zinc-300 transition-colors cursor-pointer p-0.5"
                title="Copy output"
              >
                {justCopied ? <Check className="w-3 h-3 text-green-400" /> : <Copy className="w-3 h-3" />}
              </button>
            )}
            {fullText && (
              <button
                onClick={(e) => { e.stopPropagation(); setModalOpen(true); }}
                className="text-zinc-600 hover:text-zinc-300 transition-colors cursor-pointer p-0.5"
                title="Expand output"
              >
                <Maximize2 className="w-3 h-3" />
              </button>
            )}
          </span>
        }
      >
        {children}
      </SidebarSection>

      {fullText && (
        <ContentModal open={modalOpen} onOpenChange={setModalOpen} title="Script Output" copyContent={fullText}>
          <pre className="whitespace-pre-wrap text-sm text-zinc-300 font-mono p-3 leading-relaxed">
            {fullText}
          </pre>
        </ContentModal>
      )}
    </>
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
          className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 disabled:opacity-30 disabled:cursor-default cursor-pointer"
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
          className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 disabled:opacity-30 disabled:cursor-default cursor-pointer"
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

export function RunView({ jobId, stepDef, hasLiveSource, onSelectStep, onViewFullSession }: RunViewProps) {
  const { data: runs = [] } = useRuns(jobId, stepDef.name);
  const { data: events = [] } = useEvents(jobId);
  const mutations = useStepwiseMutations();

  const [runIndex, setRunIndex] = useState(0);

  const { data: configData } = useConfig();
  const isSubscription = configData?.billing_mode === "subscription";

  const containerRef = useRef<HTMLDivElement>(null);

  // ── Jump-to-bottom FAB for streaming runs ─────────────────────────
  // Track whether the user is scrolled to the bottom of the run view.
  // When true + the stream is live, we auto-scroll to follow new
  // content ("pinned"). Scrolling up unpins and reveals a FAB that
  // re-pins (and scrolls to bottom) when clicked.
  const BOTTOM_EPSILON_PX = 40;
  const [isAtBottom, setIsAtBottom] = useState(true);
  const [pinnedToBottom, setPinnedToBottom] = useState(true);

  const measureBottom = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const distance = el.scrollHeight - (el.scrollTop + el.clientHeight);
    setIsAtBottom(distance <= BOTTOM_EPSILON_PX);
  }, []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onScroll = () => {
      const distance = el.scrollHeight - (el.scrollTop + el.clientHeight);
      const atBottom = distance <= BOTTOM_EPSILON_PX;
      setIsAtBottom(atBottom);
      // If the user scrolls away from the bottom while pinned, unpin.
      if (!atBottom && pinnedToBottom) setPinnedToBottom(false);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [pinnedToBottom]);

  const scrollToBottom = useCallback((smooth = true) => {
    const el = containerRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
    setIsAtBottom(true);
    setPinnedToBottom(true);
  }, []);

  // Auto-follow: when pinned and content grows, scroll to bottom.
  // We observe the scroll container's height changes via ResizeObserver
  // so every new streamed segment triggers a follow.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      if (pinnedToBottom) {
        el.scrollTo({ top: el.scrollHeight, behavior: "auto" });
      }
      measureBottom();
    });
    ro.observe(el);
    // Also observe the first child (the content wrapper) — the container
    // itself doesn't change size, but its scrollHeight does when children
    // grow. Watching a child directly fires on every append.
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  }, [pinnedToBottom, measureBottom]);

  // Constrain container to viewport height
  const [maxHeight, setMaxHeight] = useState<string>("100%");
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      const mh = window.innerHeight - rect.top;
      setMaxHeight(`${mh}px`);
    };
    const timer = setTimeout(measure, 50);
    window.addEventListener("resize", measure);
    return () => { clearTimeout(timer); window.removeEventListener("resize", measure); };
  }, [stepDef.name, runs]);

  const isAgent = stepDef.executor.type === "agent";
  const isExternal = stepDef.executor.type === "external";
  const isScript = stepDef.executor.type === "script";
  const isPoll = stepDef.executor.type === "poll";

  // Session info for agent steps
  const { data: sessionData } = useJobSessions(isAgent ? jobId : undefined);
  const stepSession = useMemo(() => {
    if (!isAgent || !sessionData?.sessions) return null;
    return sessionData.sessions.find(s => s.step_names.includes(stepDef.name)) ?? null;
  }, [isAgent, sessionData, stepDef.name]);
  const sessionStepEntries = useSessionStepEntries(isAgent ? jobId : undefined, stepSession);
  const [agentUsage, setAgentUsage] = useState<{ used: number; size: number } | null>(null);
  const [sessionModalOpen, setSessionModalOpen] = useState(false);
  const handleUsage = useCallback((u: { used: number; size: number } | null) => setAgentUsage(u), []);
  // Tokens used by steps before the current one in this session
  const priorTokens = useMemo(() => {
    let sum = 0;
    for (const e of sessionStepEntries) {
      if (e.name === stepDef.name) break;
      sum += e.tokens;
    }
    return sum;
  }, [sessionStepEntries, stepDef.name]);
  const currentStepTokens = useMemo(() => {
    return sessionStepEntries.find(e => e.name === stepDef.name)?.tokens ?? 0;
  }, [sessionStepEntries, stepDef.name]);

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

  const showJumpFAB = !isAtBottom;

  return (
    <div className="relative h-full">
    <div ref={containerRef} className="px-3 pb-4 space-y-3 animate-step-fade overflow-y-auto" style={{ maxHeight }}>
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
      {run && !isRunning && !isSuspended && <TimingLine run={run} />}

      {/* Running or suspended: show live timing */}
      {run && (isRunning || isSuspended) && (
        <div className="flex items-center gap-1.5 text-[11px] flex-wrap mt-3">
          <span className="text-zinc-300">Started {formatTimePretty(run.started_at)}</span>
              <span className={cn("font-medium", isRunning ? "text-blue-400" : "text-amber-400")}>
            <LiveDuration startTime={run.started_at} endTime={run.completed_at} />
          </span>
        </div>
      )}

      {/* ── Agent step layout: meta/actions first, then stream ── */}
      {isAgent && (
        <>
          {/* Metadata footer (exit, cost, workspace) */}
          {run?.result && (
            <RunDetailsTable
              exitRes={exitRes}
              costUsd={run.result.executor_meta?.cost_usd as number | undefined}
              isSubscription={isSubscription}
              showCost={showCost}
              executorMeta={run.result.executor_meta}
              workspace={run.result.workspace}
            />
          )}

          {/* Exit resolution for runs without result (e.g. failed) */}
          {!run?.result && exitRes && (
            <div className="flex items-baseline gap-2 text-xs">
              <span className="text-zinc-500 w-16 shrink-0">Exit</span>
              {exitRes.rule === "implicit_advance" ? (
                <span className="font-medium text-emerald-400">implicit advance</span>
              ) : (
                <span className="flex items-baseline gap-1.5">
                  <span className={cn("font-medium",
                    exitRes.action === "advance" && "text-emerald-400",
                    exitRes.action === "loop" && "text-purple-400",
                    exitRes.action === "escalate" && "text-red-400",
                    exitRes.action === "abandon" && "text-red-500"
                  )}>{exitRes.rule}</span>
                  <span className="text-zinc-600">&rarr;</span>
                  <span className="text-zinc-400">{exitRes.action}</span>
                </span>
              )}
            </div>
          )}

          {/* Actions */}
          {run && (
            <div className="flex gap-2">
              {isFailed && (
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
              )}
              {!isFailed && canRerun && (
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

          {/* Inputs + result */}
          {run && (run?.inputs || run?.result) && (
            <RunContentTable
              inputs={run.inputs}
              inputBindings={stepDef.inputs}
              result={run.result}
              onSelectStep={onSelectStep}
              executorType={stepDef.executor.type}
            />
          )}

          {/* Prompt (interpolated) — agent branch. The non-agent branch
              below has the same block; the agent branch was previously
              missing it, so running agent steps showed no Prompt panel.
              _interpolated_config is persisted at step-prepare time so
              this works the moment a run dispatches. */}
          {runPrompt && (
            <SidebarSection title="Prompt">
              <PromptBlock prompt={runPrompt} />
            </SidebarSection>
          )}

          {/* Error */}
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

          {/* Session + agent stream */}
          {run && (
            <>
            <SidebarSection
              title={/^step-[a-f0-9]{8}-/.test(stepSession?.session_name ?? "")
                ? "Session"
                : stepSession ? `Session: ${stepSession.session_name}` : "Session"}
              detail={
                <span className="flex items-center gap-1.5">
                  {stepSession?.is_active && (
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse inline-block" />
                  )}
                  {agentUsage ? (
                    <span className="text-[10px] text-zinc-500 font-mono flex items-center gap-1.5">
                      {formatTokensCompact(agentUsage.used)} / {formatTokensCompact(agentUsage.size)}
                      <span className="w-16 h-1.5 bg-zinc-800 rounded-full overflow-hidden flex">
                        <span
                          className="h-full bg-zinc-600 transition-all shrink-0"
                          style={{ width: `${Math.min((priorTokens / agentUsage.size) * 100, 100)}%` }}
                        />
                        <span
                          className="h-full bg-blue-500 transition-all shrink-0"
                          style={{ width: `${Math.min((currentStepTokens / agentUsage.size) * 100, 100)}%` }}
                        />
                      </span>
                    </span>
                  ) : stepSession && stepSession.total_tokens > 0 ? (
                    <span className="text-[10px] text-zinc-500 font-mono">
                      {formatTokensCompact(stepSession.total_tokens)}
                    </span>
                  ) : null}
                  <button
                    onClick={(e) => { e.stopPropagation(); setSessionModalOpen(true); }}
                    className="text-zinc-600 hover:text-zinc-300 transition-colors cursor-pointer p-0.5"
                    title="Expand session"
                  >
                    <Maximize2 className="w-3 h-3" />
                  </button>
                </span>
              }
            >
              {stepSession && sessionStepEntries.length > 1 && (
                <div className="text-xs space-y-1.5">
                  {(() => {
                    const isNamed = !/^step-[a-f0-9]{8}-/.test(stepSession.session_name);
                    return isNamed && onViewFullSession ? (
                      <div className="flex items-baseline gap-2">
                        <span className="text-zinc-500 w-16 shrink-0">Name</span>
                        <button
                          onClick={() => onViewFullSession(stepSession.session_name)}
                          className="font-mono text-blue-400 hover:text-blue-300 transition-colors cursor-pointer"
                        >
                          {stepSession.session_name}
                        </button>
                      </div>
                    ) : null;
                  })()}
                  <div className="flex items-baseline gap-2">
                    <span className="text-zinc-500 w-16 shrink-0">Runs</span>
                    <div className="font-mono space-y-0.5">
                      {sessionStepEntries.map((entry) => {
                        const isCurrent = entry.name === stepDef.name;
                        return (
                          <div key={entry.name}>
                            {isCurrent ? (
                              <span className="font-semibold text-zinc-200">
                                {entry.name}
                                {entry.tokens > 0 && <span className="text-zinc-500 font-normal"> {formatTokensCompact(entry.tokens)}</span>}
                              </span>
                            ) : (
                              <span>
                                <button
                                  onClick={() => onSelectStep?.(entry.name)}
                                  className="text-blue-400 hover:text-blue-300 transition-colors cursor-pointer"
                                >
                                  {entry.name}
                                </button>
                                {entry.tokens > 0 && <span className="text-zinc-500"> {formatTokensCompact(entry.tokens)}</span>}
                              </span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>
              )}

              {/* Agent stream — inline, no scroll wrapper */}
              <div className="-mx-3 -mb-4">
                <AgentStreamView
                  runId={run.id}
                  isLive={isRunning}
                  startedAt={run.started_at}
                  costUsd={isRunning ? costData?.cost_usd : (run.result?.executor_meta?.cost_usd as number | undefined)}
                  billingMode={isRunning ? costData?.billing_mode : undefined}
                  onUsage={handleUsage}
                  compact
                />
              </div>
            </SidebarSection>

            {/* Session expanded modal */}
            <ContentModal open={sessionModalOpen} onOpenChange={setSessionModalOpen} title={`${stepDef.name} — Session Transcript`}>
              <div className="p-3">
                <AgentStreamView
                  runId={run.id}
                  isLive={isRunning}
                  startedAt={run.started_at}
                />
              </div>
            </ContentModal>
            </>
          )}
        </>
      )}

      {/* ── Non-agent step layout ── */}
      {!isAgent && (
        <>
          {/* Metadata (exit, cost, workspace) */}
          {run?.result && (
            <RunDetailsTable
              exitRes={exitRes}
              costUsd={run.result.executor_meta?.cost_usd as number | undefined}
              isSubscription={isSubscription}
              showCost={showCost}
              executorMeta={run.result.executor_meta}
              workspace={run.result.workspace}
            />
          )}

          {/* Exit resolution for runs without result (e.g. failed) */}
          {!run?.result && exitRes && (
            <div className="flex items-baseline gap-2 text-xs">
              <span className="text-zinc-500 w-16 shrink-0">Exit</span>
              {exitRes.rule === "implicit_advance" ? (
                <span className="font-medium text-emerald-400">implicit advance</span>
              ) : (
                <span className="flex items-baseline gap-1.5">
                  <span className={cn("font-medium",
                    exitRes.action === "advance" && "text-emerald-400",
                    exitRes.action === "loop" && "text-purple-400",
                    exitRes.action === "escalate" && "text-red-400",
                    exitRes.action === "abandon" && "text-red-500"
                  )}>{exitRes.rule}</span>
                  <span className="text-zinc-600">&rarr;</span>
                  <span className="text-zinc-400">{exitRes.action}</span>
                </span>
              )}
            </div>
          )}

          {/* Error */}
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
            </div>
          )}

          {/* Actions */}
          {run && (
            <div className="flex gap-2">
              {isFailed && (
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
              )}
              {!isFailed && canRerun && (
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

          {/* Inputs + outputs */}
          {run && (run?.inputs || run?.result) && (
            <RunContentTable
              inputs={run.inputs}
              inputBindings={stepDef.inputs}
              result={run.result}
              onSelectStep={onSelectStep}
              executorType={stepDef.executor.type}
            />
          )}

          {/* Prompt (interpolated) */}
          {runPrompt && (
            <SidebarSection title="Prompt">
              <PromptBlock prompt={runPrompt} />
            </SidebarSection>
          )}

          {/* Live script output */}
          {run && isRunning && isScript && (
            <ScriptOutputSection isLive>
              <LiveScriptLogView runId={run.id} />
            </ScriptOutputSection>
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

          {/* Suspended poll — trigger now button + status */}
          {run && isSuspended && isPoll && (() => {
            const watchState = (run.executor_state as Record<string, unknown> | undefined)?._watch as {
              last_checked_at?: string;
              check_count?: number;
              last_error?: string | null;
              next_check_at?: string;
            } | undefined;
            const watchConfig = run.watch?.config as {
              check_command?: string;
              interval_seconds?: number;
              prompt?: string;
            } | undefined;

            const formatRelativeTime = (iso: string | undefined, direction: "past" | "future"): string => {
              if (!iso) return "-";
              const diff = (new Date(iso).getTime() - Date.now()) / 1000;
              const absDiff = Math.abs(diff);
              if (absDiff < 60) return direction === "past" ? "just now" : "in <1m";
              const mins = Math.round(absDiff / 60);
              if (mins < 60) return direction === "past" ? `${mins}m ago` : `in ${mins}m`;
              const hrs = Math.round(mins / 60);
              return direction === "past" ? `${hrs}h ago` : `in ${hrs}h`;
            };

            const formatInterval = (seconds: number | undefined): string => {
              if (!seconds) return "-";
              if (seconds < 60) return `${seconds}s`;
              if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
              return `${(seconds / 3600).toFixed(1)}h`;
            };

            return (
              <>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-amber-500">Polling...</span>
                  <button
                    onClick={() => mutations.triggerPollNow.mutate(run.id)}
                    disabled={mutations.triggerPollNow.isPending}
                    className="text-xs px-2 py-1 rounded bg-zinc-800 border border-zinc-700 text-zinc-300 hover:text-white hover:bg-zinc-700 transition-colors cursor-pointer disabled:opacity-50"
                  >
                    {mutations.triggerPollNow.isPending ? "Triggering..." : "Poll Now"}
                  </button>
                </div>

                {(watchState || watchConfig) && (
                  <div className="space-y-1.5">
                    <SectionHeading>Poll Status</SectionHeading>
                    <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
                      {watchConfig?.check_command && (
                        <>
                          <span className="text-zinc-500">Command</span>
                          <span className="text-zinc-300 font-mono truncate" title={watchConfig.check_command}>{watchConfig.check_command}</span>
                        </>
                      )}
                      {watchConfig?.interval_seconds != null && (
                        <>
                          <span className="text-zinc-500">Interval</span>
                          <span className="text-zinc-300">{formatInterval(watchConfig.interval_seconds)}</span>
                        </>
                      )}
                      {watchState?.check_count != null && (
                        <>
                          <span className="text-zinc-500">Checks</span>
                          <span className="text-zinc-300">{watchState.check_count}</span>
                        </>
                      )}
                      {watchState?.last_checked_at && (
                        <>
                          <span className="text-zinc-500">Last check</span>
                          <span className="text-zinc-300">{formatRelativeTime(watchState.last_checked_at, "past")}</span>
                        </>
                      )}
                      {watchState?.next_check_at && (
                        <>
                          <span className="text-zinc-500">Next check</span>
                          <span className="text-zinc-300">{formatRelativeTime(watchState.next_check_at, "future")}</span>
                        </>
                      )}
                      {watchState?.last_error != null && (
                        <>
                          <span className="text-zinc-500">Last error</span>
                          <span className={watchState.last_error ? "text-red-400" : "text-zinc-500 italic"}>
                            {watchState.last_error || "(none)"}
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                )}
              </>
            );
          })()}

          {/* Completed script logs */}
          {run?.result && isScript && (() => {
            const stdout = (run.result?.executor_meta?.stdout as string) ?? "";
            const stderr = (run.result?.executor_meta?.stderr as string) ?? "";
            const returnCode = run.result?.executor_meta?.return_code as number | undefined;
            const fullText = [stdout, stderr ? `--- stderr ---\n${stderr}` : ""].filter(Boolean).join("\n");
            if (!stdout && !stderr) return null;
            return (
              <ScriptOutputSection
                returnCode={returnCode}
                fullText={fullText}
              >
                <ScriptLogView run={run} />
              </ScriptOutputSection>
            );
          })()}

          {/* Fulfillment notes */}
          {run?.result?.artifact?._fulfillment_notes != null && (
            <div className="text-xs text-zinc-400 bg-zinc-900/30 rounded-lg border border-zinc-800 px-3 py-2 whitespace-pre-wrap">
              {String(run.result.artifact._fulfillment_notes)}
            </div>
          )}
        </>
      )}
    </div>
    {showJumpFAB && (
      <button
        onClick={() => scrollToBottom(true)}
        className={cn(
          "absolute bottom-4 right-4 z-20",
          "flex items-center gap-1.5 px-3 py-1.5 rounded-full",
          "bg-blue-600 hover:bg-blue-500 text-white shadow-lg shadow-blue-900/40",
          "text-xs font-medium transition-all",
          "animate-in fade-in slide-in-from-bottom-2 duration-200",
        )}
        title={isRunning ? "Jump to bottom and follow live" : "Jump to bottom"}
      >
        <ArrowDown className="w-3.5 h-3.5" />
        {isRunning ? "Follow live" : "Jump to bottom"}
      </button>
    )}
    </div>
  );
}
