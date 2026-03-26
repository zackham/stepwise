import { useState, useMemo, useCallback, useRef, useEffect } from "react";
import { useQueries } from "@tanstack/react-query";
import { useJobs } from "@/hooks/useStepwise";
import { JobCard } from "@/components/canvas/JobCard";
import { DependencyArrows } from "@/components/canvas/DependencyArrows";
import { computeCanvasLayout } from "@/components/canvas/CanvasLayout";
import { fetchRuns } from "@/lib/api";
import { Eye, EyeOff, Maximize } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Job, StepRun } from "@/lib/types";

export function CanvasPage() {
  const { data: jobs = [], isLoading } = useJobs(undefined, true);
  const [hideCompleted, setHideCompleted] = useState(false);

  // Pan & zoom state
  const containerRef = useRef<HTMLDivElement>(null);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [zoom, setZoom] = useState(1);
  const [isPanning, setIsPanning] = useState(false);
  const panStart = useRef({ x: 0, y: 0, panX: 0, panY: 0 });

  // Filter jobs
  const visibleJobs = useMemo(() => {
    if (hideCompleted) return jobs.filter((j) => j.status !== "completed");
    return jobs;
  }, [jobs, hideCompleted]);

  // Compute canvas layout
  const layout = useMemo(
    () => computeCanvasLayout(visibleJobs),
    [visibleJobs],
  );

  // Fetch runs for all visible jobs
  const runsQueries = useQueries({
    queries: visibleJobs.map((job) => ({
      queryKey: ["runs", job.id, undefined],
      queryFn: () => fetchRuns(job.id),
      staleTime: 5_000,
    })),
  });

  // Build jobId -> runs map
  const runsMap = useMemo(() => {
    const map = new Map<string, StepRun[]>();
    visibleJobs.forEach((job, i) => {
      map.set(job.id, runsQueries[i]?.data ?? []);
    });
    return map;
  }, [visibleJobs, runsQueries]);

  // Build jobId -> Card position map for quick lookup
  const cardMap = useMemo(() => {
    const map = new Map<string, (typeof layout.cards)[0]>();
    for (const card of layout.cards) {
      map.set(card.jobId, card);
    }
    return map;
  }, [layout.cards]);

  // Pan handlers
  const handlePointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      setIsPanning(true);
      panStart.current = { x: e.clientX, y: e.clientY, panX: pan.x, panY: pan.y };
      (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    },
    [pan],
  );

  const handlePointerMove = useCallback(
    (e: React.PointerEvent) => {
      if (!isPanning) return;
      setPan({
        x: panStart.current.panX + (e.clientX - panStart.current.x),
        y: panStart.current.panY + (e.clientY - panStart.current.y),
      });
    },
    [isPanning],
  );

  const handlePointerUp = useCallback(() => {
    setIsPanning(false);
  }, []);

  // Zoom handler
  const handleWheel = useCallback(
    (e: WheelEvent) => {
      e.preventDefault();
      const container = containerRef.current;
      if (!container) return;
      const rect = container.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;

      const factor = e.deltaY < 0 ? 1.08 : 1 / 1.08;
      const newZoom = Math.max(0.2, Math.min(3, zoom * factor));

      // Zoom toward mouse position
      setPan({
        x: mouseX - (mouseX - pan.x) * (newZoom / zoom),
        y: mouseY - (mouseY - pan.y) * (newZoom / zoom),
      });
      setZoom(newZoom);
    },
    [zoom, pan],
  );

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, [handleWheel]);

  // Fit-to-view
  const fitToView = useCallback(() => {
    const container = containerRef.current;
    if (!container || layout.cards.length === 0) return;
    const rect = container.getBoundingClientRect();
    const pad = 80;
    const scaleX = (rect.width - pad) / layout.width;
    const scaleY = (rect.height - pad) / layout.height;
    const newZoom = Math.max(0.2, Math.min(1.5, Math.min(scaleX, scaleY)));
    setPan({
      x: (rect.width - layout.width * newZoom) / 2,
      y: (rect.height - layout.height * newZoom) / 2,
    });
    setZoom(newZoom);
  }, [layout]);

  // Auto-fit on first load
  const didFit = useRef(false);
  useEffect(() => {
    if (!didFit.current && layout.cards.length > 0) {
      didFit.current = true;
      fitToView();
    }
  }, [layout.cards.length, fitToView]);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500 text-sm">
        Loading jobs...
      </div>
    );
  }

  if (jobs.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-zinc-500 text-sm">
        No jobs yet. Create one from the Jobs page.
      </div>
    );
  }

  return (
    <div className="h-full relative overflow-hidden">
      {/* Toolbar */}
      <div className="absolute top-3 right-3 z-10 flex items-center gap-1.5">
        <button
          onClick={() => setHideCompleted(!hideCompleted)}
          className={cn(
            "flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-md border transition-colors",
            hideCompleted
              ? "bg-zinc-800 border-zinc-700 text-zinc-300"
              : "bg-zinc-900/80 border-zinc-800 text-zinc-500 hover:text-zinc-300",
          )}
          title={hideCompleted ? "Show completed jobs" : "Hide completed jobs"}
        >
          {hideCompleted ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
          <span className="hidden sm:inline">{hideCompleted ? "Show done" : "Hide done"}</span>
        </button>
        <button
          onClick={fitToView}
          className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-md border bg-zinc-900/80 border-zinc-800 text-zinc-500 hover:text-zinc-300 transition-colors"
          title="Fit to view"
        >
          <Maximize className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Canvas */}
      <div
        ref={containerRef}
        className={cn("h-full w-full", isPanning ? "cursor-grabbing" : "cursor-grab")}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
      >
        <div
          style={{
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: "0 0",
            width: layout.width,
            height: layout.height,
            position: "relative",
          }}
        >
          {/* Dependency arrows (behind cards) */}
          <DependencyArrows
            edges={layout.edges}
            width={layout.width}
            height={layout.height}
          />

          {/* Group clusters */}
          {layout.groups.map((group) => (
            <div
              key={group.label}
              className="absolute rounded-xl border border-dashed border-zinc-800/60 bg-zinc-900/20"
              style={{
                left: group.x,
                top: group.y,
                width: group.width,
                height: group.height,
              }}
            >
              <div className="px-3 pt-1.5 text-[11px] text-zinc-600 font-medium">
                {group.label}:{" "}
                <span className="text-zinc-500">
                  {group.completedCount}/{group.totalCount} complete
                </span>
              </div>
            </div>
          ))}

          {/* Job cards */}
          {layout.cards.map((card) => {
            const job = visibleJobs.find((j) => j.id === card.jobId);
            if (!job) return null;
            return (
              <div
                key={card.jobId}
                className="absolute"
                style={{ left: card.x, top: card.y }}
              >
                <JobCard
                  job={job}
                  runs={runsMap.get(card.jobId) ?? []}
                  width={card.width}
                  height={card.height}
                />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
