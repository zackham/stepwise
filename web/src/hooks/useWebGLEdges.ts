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
  NoToneMapping,
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
  onReady?: () => void;
  onLost?: () => void;
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
  onReady,
  onLost,
}: UseWebGLEdgesOptions): void {
  const rendererRef = useRef<WebGLRenderer | null>(null);
  const sceneRef = useRef<Scene | null>(null);
  const cameraRef = useRef<OrthographicCamera | null>(null);
  const composerRef = useRef<BloomComposer | null>(null);
  const meshMapRef = useRef<Map<string, EdgeMeshEntry>>(new Map());
  const stateManagerRef = useRef<EdgeStateManager>(new EdgeStateManager());
  const rafRef = useRef<number>(0);
  const lastTimeRef = useRef<number>(0);
  const readyFiredRef = useRef(false);
  const layoutRef = useRef(layout);
  const latestRunsRef = useRef(latestRuns);
  const onReadyRef = useRef(onReady);
  const onLostRef = useRef(onLost);

  layoutRef.current = layout;
  latestRunsRef.current = latestRuns;
  onReadyRef.current = onReady;
  onLostRef.current = onLost;

  /** Build or update edge meshes to match the current layout. */
  const syncMeshes = useCallback((scene: Scene, edges: DagEdge[], loopEdges: LoopEdge[]) => {
    const meshMap = meshMapRef.current;
    const activeKeys = new Set<string>();

    // First pass: create/update meshes and collect curve lengths
    const curveLengths: number[] = [];

    // Data edges
    for (const edge of edges) {
      const key = `${edge.from}->${edge.to}`;
      activeKeys.add(key);

      const isSequencingOnly = edge.labels.length === 0;
      const radius = isSequencingOnly ? 2.0 : 3.0;
      const hue = 0.0; // cyan for data edges
      const dim = isSequencingOnly ? 0.5 : 1.0;

      const existing = meshMap.get(key);
      if (existing) {
        const curve = createEdgeCurve(edge.points);
        const curveLength = curve.getLength();
        existing.mesh.geometry.dispose();
        existing.mesh.geometry = createEdgeGeometry(curve, radius);
        existing.curveLength = curveLength;
        existing.material.uniforms.u_curve_length.value = curveLength;
        existing.material.uniforms.u_hue.value = hue;
        existing.material.uniforms.u_dim.value = dim;
        curveLengths.push(curveLength);
      } else {
        const curve = createEdgeCurve(edge.points);
        const curveLength = curve.getLength();
        const geometry = createEdgeGeometry(curve, radius);
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
            u_hue: { value: hue },
            u_dim: { value: dim },
            u_pulse_count: { value: 1.0 },
            u_flow_age: { value: 0.0 },
          },
        });
        const mesh = new Mesh(geometry, material);
        scene.add(mesh);
        meshMap.set(key, { key, mesh, material, curveLength });
        curveLengths.push(curveLength);
      }
    }

    // Loop edges (keyed with loopIndex for uniqueness)
    for (const le of loopEdges) {
      const key = `loop:${le.from}->${le.to}:${le.loopIndex}`;
      activeKeys.add(key);

      const curve = createLoopEdgeCurve(le.path);
      if (!curve) continue;

      const existing = meshMap.get(key);
      if (existing) {
        const curveLength = curve.getLength();
        existing.mesh.geometry.dispose();
        existing.mesh.geometry = createEdgeGeometry(curve);
        existing.curveLength = curveLength;
        existing.material.uniforms.u_curve_length.value = curveLength;
        existing.material.uniforms.u_hue.value = 1.0;
        existing.material.uniforms.u_dim.value = 1.0;
        curveLengths.push(curveLength);
      } else {
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
            u_hue: { value: 1.0 },
            u_dim: { value: 1.0 },
            u_pulse_count: { value: 1.0 },
            u_flow_age: { value: 0.0 },
          },
        });
        const mesh = new Mesh(geometry, material);
        scene.add(mesh);
        meshMap.set(key, { key, mesh, material, curveLength });
        curveLengths.push(curveLength);
      }
    }

    // Second pass: compute pulse counts relative to shortest edge
    // Use a generous unit length so pulses stay sparse — at most 1 pulse per ~300px
    const minLength = curveLengths.length > 0 ? Math.min(...curveLengths) : 1;
    const unitLength = Math.max(minLength, 300);
    for (const entry of meshMap.values()) {
      if (activeKeys.has(entry.key)) {
        const pulseCount = Math.max(1, Math.round(entry.curveLength / unitLength));
        entry.material.uniforms.u_pulse_count.value = pulseCount;
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

    let renderer: WebGLRenderer;
    try {
      renderer = new WebGLRenderer({
        alpha: true,
        antialias: true,
        premultipliedAlpha: true,
        preserveDrawingBuffer: true,
      });
    } catch {
      // WebGL context creation failed — SVG fallback stays
      return;
    }

    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.toneMapping = NoToneMapping;
    renderer.setClearColor(0x000000, 0); // Transparent — luma-alpha pass handles visibility

    const w = layoutRef.current.width;
    const h = layoutRef.current.height;
    renderer.setSize(w, h);

    const scene = new Scene();
    const camera = new OrthographicCamera(0, w, 0, h, -100, 100);
    camera.position.set(0, 0, 50);
    camera.lookAt(0, 0, 0);

    renderer.setClearColor(0x000000, 1);

    const bloom = createBloomComposer(renderer, scene, camera, w, h);

    rendererRef.current = renderer;
    sceneRef.current = scene;
    cameraRef.current = camera;
    composerRef.current = bloom;
    readyFiredRef.current = false;

    // Initial mesh setup
    syncMeshes(scene, layoutRef.current.edges, layoutRef.current.loopEdges);

    // Mount canvas
    const container = containerRef.current;
    const canvas = renderer.domElement;
    canvas.style.position = "absolute";
    canvas.style.top = "0";
    canvas.style.left = "0";
    canvas.style.pointerEvents = "none";
    canvas.style.zIndex = "0";
    if (container) {
      container.insertBefore(canvas, container.firstChild);
    }

    // Context loss handling
    const handleContextLost = (e: Event) => {
      e.preventDefault();
      cancelAnimationFrame(rafRef.current);
      onLostRef.current?.();
    };
    const handleContextRestored = () => {
      // Re-init meshes and restart animation
      syncMeshes(scene, layoutRef.current.edges, layoutRef.current.loopEdges);
      lastTimeRef.current = performance.now();
      rafRef.current = requestAnimationFrame(animate);
      onReadyRef.current?.();
    };
    canvas.addEventListener("webglcontextlost", handleContextLost);
    canvas.addEventListener("webglcontextrestored", handleContextRestored);

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
          entry.material.uniforms.u_flow_age.value = u.flowAge;
        }
        entry.material.uniforms.u_time.value = globalTime;
      }

      bloom.composer.render();

      // Fire onReady after first successful render
      if (!readyFiredRef.current) {
        readyFiredRef.current = true;
        onReadyRef.current?.();
      }

      rafRef.current = requestAnimationFrame(animate);
    };

    rafRef.current = requestAnimationFrame(animate);

    return () => {
      cancelAnimationFrame(rafRef.current);
      canvas.removeEventListener("webglcontextlost", handleContextLost);
      canvas.removeEventListener("webglcontextrestored", handleContextRestored);
      if (container && canvas.parentElement === container) {
        container.removeChild(canvas);
      }
      disposeScene(scene);
      bloom.dispose();
      renderer.dispose();
      meshMapRef.current.clear();
      stateManagerRef.current.reset();
      rendererRef.current = null;
      sceneRef.current = null;
      cameraRef.current = null;
      composerRef.current = null;
      readyFiredRef.current = false;
    };
  }, [enabled, containerRef, syncMeshes]);

  // Update meshes and camera when layout changes (debounced for transitions)
  useEffect(() => {
    if (!enabled || !sceneRef.current || !rendererRef.current || !cameraRef.current) return;

    const w = layout.width;
    const h = layout.height;

    // Camera and renderer resize are cheap — do immediately
    const camera = cameraRef.current;
    camera.right = w;
    camera.bottom = h;
    camera.updateProjectionMatrix();
    rendererRef.current.setSize(w, h);
    if (composerRef.current) composerRef.current.resize(w, h);

    // Debounce mesh sync to avoid geometry thrash during layout transitions
    const timer = setTimeout(() => {
      if (sceneRef.current) {
        syncMeshes(sceneRef.current, layout.edges, layout.loopEdges);
      }
    }, 50);
    return () => clearTimeout(timer);
  }, [enabled, layout, syncMeshes]);
}
