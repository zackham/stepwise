import { useRef } from "react";
import type { HierarchicalDagLayout } from "@/lib/dag-layout";
import type { StepRun } from "@/lib/types";
import { useWebGLEdges } from "@/hooks/useWebGLEdges";

interface WebGLEdgeLayerProps {
  layout: HierarchicalDagLayout;
  latestRuns: Record<string, StepRun>;
  onReady: () => void;
  onLost?: () => void;
}

export default function WebGLEdgeLayer({
  layout,
  latestRuns,
  onReady,
  onLost,
}: WebGLEdgeLayerProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useWebGLEdges({
    containerRef,
    layout,
    latestRuns,
    enabled: true,
    onReady,
    onLost,
  });

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
