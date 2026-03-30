import { useRef, useEffect } from "react";
import type { HierarchicalDagLayout } from "@/lib/dag-layout";
import type { StepRun } from "@/lib/types";
import { useWebGLEdges } from "@/hooks/useWebGLEdges";

interface WebGLEdgeLayerProps {
  layout: HierarchicalDagLayout;
  latestRuns: Record<string, StepRun>;
  onReady: () => void;
}

export default function WebGLEdgeLayer({
  layout,
  latestRuns,
  onReady,
}: WebGLEdgeLayerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const notifiedRef = useRef(false);

  const { ready } = useWebGLEdges({
    containerRef,
    layout,
    latestRuns,
    enabled: true,
  });

  useEffect(() => {
    if (ready && !notifiedRef.current) {
      notifiedRef.current = true;
      onReady();
    }
  }, [ready, onReady]);

  return (
    <div
      ref={containerRef}
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: layout.width,
        height: layout.height,
        pointerEvents: "none",
        zIndex: 0,
      }}
    />
  );
}
