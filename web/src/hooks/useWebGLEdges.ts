import { useRef, useEffect, useCallback } from "react";
import type { HierarchicalDagLayout } from "@/lib/dag-layout";
import type { DagEdge, LoopEdge } from "@/lib/dag-layout";
import type { StepRun } from "@/lib/types";
import {
  WebGLRenderer,
  Scene,
  OrthographicCamera,
  ShaderMaterial,
  Mesh,
  AdditiveBlending,
  DoubleSide,
} from "three";
import { createEdgeCurve, createEdgeGeometry, createLoopEdgeCurve } from "@/lib/webgl/edge-geometry";
import { VERTEX_SHADER, FRAGMENT_SHADER } from "@/lib/webgl/edge-shaders";
import { EdgeStateManager } from "@/lib/webgl/edge-state-manager";
import { createBloomComposer } from "@/lib/webgl/bloom-composer";
import type { BloomComposer } from "@/lib/webgl/bloom-composer";
import { disposeScene } from "@/lib/webgl/webgl-utils";

export interface UseWebGLEdgesOptions {
  containerRef: React.RefObject<HTMLDivElement | null>;
  layout: HierarchicalDagLayout;
  latestRuns: Record<string, StepRun>;
  enabled: boolean;
}

interface EdgeMeshEntry {
  key: string;
  mesh: Mesh;
  material: ShaderMaterial;
  curveLength: number;
}

/**
 * Hook that manages a Three.js scene for rendering energy pulse
 * edge animations on the DAG view.
 */
export function useWebGLEdges({
  containerRef,
  layout,
  latestRuns,
  enabled,
}: UseWebGLEdgesOptions): {
  canvasElement: HTMLCanvasElement | null;
  ready: boolean;
} {
  const rendererRef = useRef<WebGLRenderer | null>(null);
  const sceneRef = useRef<Scene | null>(null);
  const cameraRef = useRef<OrthographicCamera | null>(null);
  const composerRef = useRef<BloomComposer | null>(null);
  const meshMapRef = useRef<Map<string, EdgeMeshEntry>>(new Map());
  const stateManagerRef = useRef<EdgeStateManager>(new EdgeStateManager());
  const rafRef = useRef<number>(0);
  const lastTimeRef = useRef<number>(0);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const readyRef = useRef(false);
  const layoutRef = useRef(layout);
  const latestRunsRef = useRef(latestRuns);

  layoutRef.current = layout;
  latestRunsRef.current = latestRuns;

  /** Build or update edge meshes to match the current layout. */
  const syncMeshes = useCallback((scene: Scene, edges: DagEdge[], loopEdges: LoopEdge[]) => {
    const meshMap = meshMapRef.current;
    const activeKeys = new Set<string>();

    // Data edges
    for (const edge of edges) {
      const key = `${edge.from}->${edge.to}`;
      activeKeys.add(key);

      const existing = meshMap.get(key);
      if (existing) {
        // Update geometry if points changed
        const curve = createEdgeCurve(edge.points);
        const curveLength = curve.getLength();
        existing.mesh.geometry.dispose();
        existing.mesh.geometry = createEdgeGeometry(curve);
        existing.curveLength = curveLength;
        existing.material.uniforms.u_curve_length.value = curveLength;
      } else {
        // Create new mesh
        const curve = createEdgeCurve(edge.points);
        const curveLength = curve.getLength();
        const geometry = createEdgeGeometry(curve);
        const material = new ShaderMaterial({
          vertexShader: VERTEX_SHADER,
          fragmentShader: FRAGMENT_SHADER,
          transparent: true,
          depthWrite: false,
          blending: AdditiveBlending,
          side: DoubleSide,
          uniforms: {
            u_time: { value: 0.0 },
            u_state: { value: 0 },
            u_surge_progress: { value: 0.0 },
            u_curve_length: { value: curveLength },
            u_flash: { value: 0.0 },
          },
        });
        const mesh = new Mesh(geometry, material);
        scene.add(mesh);
        meshMap.set(key, { key, mesh, material, curveLength });
      }
    }

    // Loop edges
    for (const le of loopEdges) {
      const key = `loop:${le.from}->${le.to}`;
      activeKeys.add(key);

      const existing = meshMap.get(key);
      if (existing) {
        const curve = createLoopEdgeCurve(le.path);
        const curveLength = curve.getLength();
        existing.mesh.geometry.dispose();
        existing.mesh.geometry = createEdgeGeometry(curve);
        existing.curveLength = curveLength;
        existing.material.uniforms.u_curve_length.value = curveLength;
      } else {
        const curve = createLoopEdgeCurve(le.path);
        const curveLength = curve.getLength();
        const geometry = createEdgeGeometry(curve);
        const material = new ShaderMaterial({
          vertexShader: VERTEX_SHADER,
          fragmentShader: FRAGMENT_SHADER,
          transparent: true,
          depthWrite: false,
          blending: AdditiveBlending,
          side: DoubleSide,
          uniforms: {
            u_time: { value: 0.0 },
            u_state: { value: 0 },
            u_surge_progress: { value: 0.0 },
            u_curve_length: { value: curveLength },
            u_flash: { value: 0.0 },
          },
        });
        const mesh = new Mesh(geometry, material);
        scene.add(mesh);
        meshMap.set(key, { key, mesh, material, curveLength });
      }
    }

    // Remove stale meshes
    for (const [key, entry] of meshMap) {
      if (!activeKeys.has(key)) {
        scene.remove(entry.mesh);
        entry.mesh.geometry.dispose();
        entry.material.dispose();
        meshMap.delete(key);
      }
    }

    stateManagerRef.current.cleanup(activeKeys);
  }, []);

  // Initialize Three.js scene
  useEffect(() => {
    if (!enabled) return;

    const renderer = new WebGLRenderer({
      alpha: true,
      antialias: true,
      premultipliedAlpha: false,
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);

    const w = layoutRef.current.width;
    const h = layoutRef.current.height;
    renderer.setSize(w, h);

    const scene = new Scene();
    const camera = new OrthographicCamera(0, w, 0, h, -100, 100);
    camera.position.set(0, 0, 50);
    camera.lookAt(0, 0, 0);

    const bloom = createBloomComposer(renderer, scene, camera, w, h);

    rendererRef.current = renderer;
    sceneRef.current = scene;
    cameraRef.current = camera;
    composerRef.current = bloom;
    canvasRef.current = renderer.domElement;
    readyRef.current = true;

    // Initial mesh setup
    syncMeshes(scene, layoutRef.current.edges, layoutRef.current.loopEdges);

    // Mount canvas
    const container = containerRef.current;
    if (container) {
      const canvas = renderer.domElement;
      canvas.style.position = "absolute";
      canvas.style.top = "0";
      canvas.style.left = "0";
      canvas.style.pointerEvents = "none";
      canvas.style.zIndex = "0";
      container.insertBefore(canvas, container.firstChild);
    }

    // Animation loop
    lastTimeRef.current = performance.now();
    const animate = (timestamp: number) => {
      const dt = Math.min((timestamp - lastTimeRef.current) / 1000, 0.1); // cap at 100ms
      lastTimeRef.current = timestamp;

      const currentLayout = layoutRef.current;
      const currentRuns = latestRunsRef.current;

      // Update edge state uniforms
      const uniforms = stateManagerRef.current.update(
        currentLayout.edges,
        currentLayout.loopEdges,
        currentRuns,
        dt,
      );

      const globalTime = timestamp / 1000;
      const meshMap = meshMapRef.current;

      for (const [key, entry] of meshMap) {
        const u = uniforms.get(key);
        if (u) {
          entry.material.uniforms.u_state.value = u.state;
          entry.material.uniforms.u_surge_progress.value = u.surgeProgress;
          entry.material.uniforms.u_flash.value = u.flash;
        }
        entry.material.uniforms.u_time.value = globalTime;
      }

      bloom.composer.render();
      rafRef.current = requestAnimationFrame(animate);
    };

    rafRef.current = requestAnimationFrame(animate);

    return () => {
      cancelAnimationFrame(rafRef.current);
      if (container && renderer.domElement.parentElement === container) {
        container.removeChild(renderer.domElement);
      }
      disposeScene(scene);
      bloom.dispose();
      renderer.dispose();
      meshMapRef.current.clear();
      rendererRef.current = null;
      sceneRef.current = null;
      cameraRef.current = null;
      composerRef.current = null;
      canvasRef.current = null;
      readyRef.current = false;
    };
  }, [enabled, containerRef, syncMeshes]);

  // Update meshes and camera when layout changes
  useEffect(() => {
    if (!enabled || !sceneRef.current || !rendererRef.current || !cameraRef.current || !composerRef.current) return;

    const w = layout.width;
    const h = layout.height;

    // Update camera
    const camera = cameraRef.current;
    camera.right = w;
    camera.bottom = h;
    camera.updateProjectionMatrix();

    // Update renderer size
    rendererRef.current.setSize(w, h);
    composerRef.current.resize(w, h);

    // Sync meshes
    syncMeshes(sceneRef.current, layout.edges, layout.loopEdges);
  }, [enabled, layout, syncMeshes]);

  return {
    canvasElement: canvasRef.current,
    ready: readyRef.current,
  };
}
