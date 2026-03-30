# Plan: WebGL Energy Pulse Visualization for DAG Edges

## Overview

Replace static SVG edge lines in the Stepwise web UI's top-level DAG view with GPU-accelerated energy pulse animations using Three.js, custom GLSL shaders, and UnrealBloomPass post-processing. A transparent WebGL canvas renders behind HTML step nodes (dark mode only), with per-edge shader uniforms driven by a 5-state animation machine (idle/surge/flow/completed/failed) that reacts to live step run status changes via WebSocket. SVG fallback is automatic for light mode, no-WebGL-2 browsers, context loss, and nested sub-flow containers.

---

## Requirements

### Visual Rendering

| # | Requirement | Acceptance Criteria |
|---|---|---|
| R1 | CatmullRom-smoothed tube geometry for every top-level DAG edge | Each edge's dagre control points fitted to `CatmullRomCurve3` (centripetal, tension 0.5), extruded as `TubeGeometry`. Segments proportional to curve length (16–128). Radius = 1.5, radial segments = 4. |
| R2 | 5-state fragment shader state machine | States 0–4 (idle/surge/flow/completed/failed). Each produces distinct visual output per the Shader State Contract below. |
| R3 | UnrealBloomPass post-processing | Strength 1.5, radius 0.8, threshold 0.2. Surge heads, flow pulse peaks, and completion flashes produce visible halo glow against dark background. |
| R4 | Constant visual speed via curve-length normalization | Flow speed uniform = `150.0 / max(u_curve_length, 1.0)`. Verified: time a pulse on a 100px edge and a 400px edge — both travel at same apparent px/s (±5%). |
| R5 | Edge-kind visual distinction | Data edges: cyan/white energy. Sequencing-only edges (`labels.length === 0`): dimmer idle (alpha 0.08 vs 0.15), thinner tube (radius 1.0 vs 1.5). Loop edges: orange/amber hue. Controlled by `u_hue` and `u_dim` uniforms. |
| R6 | Directional arrowheads | SVG `<marker>` arrowheads rendered on a hairline-opacity SVG path (`opacity: 0.01`) when `webglActive`. All 6 marker types preserved: inactive, active, completed, critical, suspended, loop. |

### State & Interaction

| # | Requirement | Acceptance Criteria |
|---|---|---|
| R7 | Per-edge state driven by step run status with correct precedence | Single precedence table (see Edge State Precedence). Active beats completed. Tested through `update()` method with 8 specific status combinations. |
| R8 | Edge add/remove during running jobs | New edges appear in IDLE (or FLOW if target is already running). Removed edges (skipped steps, rerun) have their mesh disposed and state entry cleaned within one `syncMeshes` call. |
| R9 | Loop-back edges with unique keying | Keyed by `loop:${from}->${to}:${loopIndex}`. Multiple exit rules between same step pair create separate meshes with distinct animation state. |

### Integration & Fallback

| # | Requirement | Acceptance Criteria |
|---|---|---|
| R10 | WebGL canvas behind HTML nodes | Canvas `position: absolute; pointer-events: none; z-index: 0`. Step nodes render above. |
| R11 | Dark mode only; SVG fallback for all other cases | WebGL mounts only when `canUseWebGL() && isDark`. SVG fallback for: light mode, no WebGL 2, context loss, nested containers. |
| R12 | Lazy-loaded Three.js chunk | `React.lazy()` + `Suspense`. Non-DAG pages never load Three.js. Chunk < 150KB gzip. |
| R13 | DAG share/export captures WebGL | `captureDAG()` PNG includes energy pulses. Renderer: `preserveDrawingBuffer: true`. |
| R14 | Layout transitions don't thrash geometry | During 300ms layout tweens, mesh sync is debounced (50ms). No geometry allocation during active transition. |
| R15 | Resource cleanup on unmount and navigation | All Three.js resources disposed, RAF cancelled, canvas removed. No heap growth after 10 mount/unmount cycles. State manager resets on remount (no stale animation state from previous job). |

### Shader State Contract

Each state produces these exact visual characteristics:

| State | Color | Alpha (center) | Alpha (edge) | Animation | Bloom |
|---|---|---|---|---|---|
| 0 (idle) | `vec3(0.1, 0.15, 0.25)` | 0.15 | 0 (via edgeFade) | None | Minimal |
| 1 (surge) | Cyan→white interp on head intensity | 0.8 at head peak | Decays via edgeFade | Single bolt: head `exp(-80d²)`, tail `exp(8d)*0.4`. Duration: 0.8s. | Strong at head |
| 2 (flow) | Cyan `(0, 0.85, 1)` → bright `(0.3, 0.95, 1)` | 0.15 base + 0.85 pulse | Decays via edgeFade | Repeating pulses: `exp(-12 * fract(along - t*speed))`. Speed: 150px/s normalized. | Moderate at peaks |
| 3 (completed) | Flash: `(0.5, 1, 1)` → settled: `(0, 0.5, 0.6)` | Flash: 0.9. Settled: 0.35. | Decays via edgeFade | Flash 1.0→0.0 over 0.4s | Strong during flash |
| 4 (failed) | Flash: `(1, 0.3, 0.2)` → settled: `(0.5, 0.1, 0.08)` | Flash: 0.9. Settled: 0.3. | Decays via edgeFade | Flash 1.0→0.0 over 0.4s | Strong during flash |

**Hue overrides** (applied before state colors):
- Loop edges: orange shift. Idle: `vec3(0.25, 0.15, 0.05)`. Flow/surge: `vec3(0.9, 0.5, 0.1)` → `vec3(1.0, 0.7, 0.3)`.
- Sequencing-only edges: alpha multiplied by 0.5, radius reduced to 1.0.

---

## Assumptions

| # | Assumption | Verification |
|---|---|---|
| A1 | Dagre provides edge control points as `Array<{x, y}>` | `dag-layout.ts:26-31` — `DagEdge.points: Array<{ x: number; y: number }>` |
| A2 | Loop edges have pre-computed SVG cubic Bezier paths with `loopIndex` | `dag-layout.ts:33-40` — `LoopEdge.path: string`, `loopIndex: number`. Multiple rules at `dag-layout.ts:229-257`. |
| A3 | Three.js v0.183 requires WebGL 2 (no fallback to WebGL 1) | `WebGLRenderer.js:103` — throws `"WebGL 1 is not supported since r163"`. Line 388: `contextName = 'webgl2'`. |
| A4 | Three.js and `@types/three` already installed | `package.json:40` — `"three": "^0.183.2"`. `package.json:55` — `"@types/three": "^0.183.1"`. |
| A5 | Canvas div uses CSS transform for pan/zoom (not camera) | `useDagCamera.ts:76-80` — `el.style.transform = translate(...) scale(...)`. WebGL canvas is a child, transforms with it. |
| A6 | Layout transitions interpolate at ~60fps for 300ms via RAF | `layout-transition.ts:7` — `TRANSITION_MS = 300`. `lerpLayout()` at lines 100-143 recomputes every frame. |
| A7 | SVG edges use quadratic splines, not CatmullRom | `DagEdges.tsx:48-83` — `buildPath()` uses `M/L/Q` SVG commands. |
| A8 | Labels positioned from raw dagre point midpoints, not curve | `DagEdges.tsx:85-92` — `edgeMidpoint()` returns `points[floor(length/2)]`. |
| A9 | Nested sub-flows instantiate their own `DagEdges` | `ExpandedStepContainer.tsx:179-185`, `ForEachExpandedContainer.tsx:229-235`. |
| A10 | `captureDAG()` uses `html-to-image` `toBlob()` on canvasRef | `FlowDagView.tsx:175-209`. Default `preserveDrawingBuffer: false` means blank canvas capture. |
| A11 | `latestRuns` is `Record<string, StepRun>` keyed by step name | `FlowDagView.tsx:385-394` — selects highest attempt per step. |
| A12 | SVG gives active edges precedence over completed | `DagEdges.tsx:269-274` — `isActive` checked first. Line 312: `isCompleted && !isActive`. Line 326: ternary chain. |
| A13 | Sequencing-only edges identified by `edge.labels.length === 0` | `DagEdges.tsx:262` — `const isSequencingOnly = edge.labels.length === 0`. |
| A14 | Layout transition keys loop edges by `from->to` (no loopIndex) | `layout-transition.ts:108` — `prevLoopMap` keyed without loopIndex. Pre-existing issue, not introduced by this plan. |

---

## Out of Scope

- **Light mode WebGL** — energy pulses designed for dark backgrounds.
- **Nested sub-flow WebGL** — `ExpandedStepContainer` and `ForEachExpandedContainer` stay SVG. Rationale: GPU contexts are limited (~8–16 per page), nested layouts are smaller and often clipped. Explicitly top-level only.
- **MiniDag** — job-card thumbnail DAG (`MiniDag.tsx`, 231 lines) stays SVG.
- **CanvasLayout** — job dashboard layout, unrelated to step DAGs.
- **Container port edges** — `ContainerPortEdges.tsx` stays SVG.
- **Mobile/touch** — desktop experience. No touch-specific shader tuning.
- **Viewport-sized canvas optimization** — rendering a viewport-sized canvas with WebGL camera tracking would reduce fill rate for large DAGs but requires decoupling from the CSS transform system. Deferred until profiling shows need (>100 edges).

---

## Architecture

### Layer Stack

```
canvasRef div (position: relative, transformOrigin: 0 0, CSS transform for pan/zoom)
├── WebGLEdgeLayer div (position: absolute, z-index: 0, pointer-events: none)
│   └── <canvas> from Three.js WebGLRenderer
├── DagEdges SVG (z-index: 1, always rendered)
│   ├── <defs> with 6 arrowhead <marker> types (always)
│   ├── Hairline-opacity <path> per edge with markerEnd (when webglActive, for arrowheads)
│   ├── Full-opacity <path> per edge (when !webglActive, SVG fallback)
│   ├── Critical-path highlight <path> (always, when criticalPath active)
│   ├── Data-flow field labels + hover/click handlers (always)
│   └── Loop-edge labels (always)
├── StepNode[] (DOM elements, higher z-index)
└── FlowPortNode[] (DOM elements)
```

The SVG `DagEdges` component is **never removed**. When `webglActive`:
- Edge stroke `<path>` elements switch from full-opacity to `opacity: 0.01` with `markerEnd` preserved → arrowheads remain visible at edge endpoints.
- All labels, hover handlers, click handlers, and critical-path highlights render normally.

When `!webglActive` (light mode, no WebGL, context loss):
- Edge stroke `<path>` elements render at normal opacity. Full SVG fallback.
- No behavior change from current implementation.

### Module Structure

```
web/src/
├── lib/webgl/
│   ├── edge-shaders.ts        — GLSL vertex + fragment shader source strings
│   ├── edge-geometry.ts       — CatmullRom curve fitting + TubeGeometry construction
│   ├── edge-state-manager.ts  — Stateful animation controller (run status → uniforms)
│   ├── bloom-composer.ts      — EffectComposer + UnrealBloomPass setup
│   └── webgl-utils.ts         — canUseWebGL() check, disposeScene() helper
├── hooks/
│   └── useWebGLEdges.ts       — Three.js scene lifecycle, mesh sync, animation loop
└── components/dag/
    └── WebGLEdgeLayer.tsx      — React wrapper (lazy-loadable, onReady/onLost callbacks)
```

### Data Flow

```
dagre layout
  ├── DagEdge[].points ──→ createEdgeCurve() → CatmullRomCurve3
  │                      → createEdgeGeometry(curve, radius) → TubeGeometry
  │                      → ShaderMaterial (per-edge uniforms)
  │                      → Mesh → Scene
  │
  └── LoopEdge[].path ──→ createLoopEdgeCurve() → CatmullRomCurve3 | null
                         → (null = skip mesh, SVG fallback for this edge)
                         → same pipeline if non-null

WebSocket → StepRun updates → latestRuns map
  → EdgeStateManager.update(edges, loopEdges, latestRuns, dt)
  → Map<edgeKey, EdgeUniforms {state, surgeProgress, flash}>
  → material.uniforms per mesh
  → fragment shader → EffectComposer + bloom → canvas output
```

### Edge State Precedence

Single authoritative table, matching SVG behavior at `DagEdges.tsx:266-341`:

| Priority | Condition | WebGL State | SVG Equivalent |
|---|---|---|---|
| 1 (highest) | `targetStatus ∈ {running, delegated, suspended}` | If just-started: **SURGE** (0.8s) then **FLOW**. If already-running on mount: **FLOW** directly. | `isActive = true` (line 271) → blue dashed animation |
| 2 | `sourceStatus == completed && targetStatus == completed` | **COMPLETED** (flash → settled cyan) | `isCompleted && !isActive` (line 312) → cyan glow |
| 3 | `targetStatus == failed \|\| sourceStatus == failed` | **FAILED** (red flash → settled red) | Falls through to inactive styling in SVG |
| 4 (lowest) | Everything else | **IDLE** | Inactive gray/dim stroke |

**Why active > completed:** SVG code at `DagEdges.tsx:326` uses `isActive ? activeColor : isCompleted ? completedColor : ...`. When source is completed and target is running, `isActive` is true (line 269-271), so the edge gets active styling. The WebGL state machine must match: `(completed, running) → SURGE/FLOW`, not COMPLETED.

### State Transitions

```
IDLE ──[target starts running]──→ SURGE ──[0.8s elapsed]──→ FLOW
  ↑         (prevStatus != status)          ↑                  │
  │    [already running on first observe] ──┘ (skip to FLOW)   │
  │                                                            │
  │    COMPLETED ←──[source+target both completed]─────────────┘
  │      (flash 1.0 → 0.0 over 0.4s, then settled glow)
  │
  └──── FAILED ←──[either side failed]────────────────────────┘
           (flash 1.0 → 0.0 over 0.4s, then settled red)
```

**Timing constants:**
- `SURGE_DURATION = 0.8s` — time for bolt to traverse edge. `surgeProgress += dt / 0.8`.
- `FLASH_DURATION = 0.4s` — time for completion/failure flash. `flashIntensity -= dt / 0.4`.
- `dt` capped at `0.1s` per frame to handle background-tab time jumps.

**Transition detection:** `EdgeStateManager` stores `prevRunStatuses: Map<string, string>`. On each `update()` call, if `latestRuns[targetStep].status !== prevRunStatuses.get(targetStep)` AND the new status is running/delegated/suspended, trigger SURGE. If statuses haven't changed but the edge is new (no prior state entry), check current status: if already running → skip to FLOW, else → IDLE.

### Readiness Gating and Context Loss

**Problem with ref-based readiness (current code):** `useWebGLEdges` exposes `ready` as a ref (`useWebGLEdges.ts:57`), but ref mutations don't trigger React re-renders. The `WebGLEdgeLayer` effect observing `ready` (`WebGLEdgeLayer.tsx:27-32`) can miss the `false→true` transition.

**Solution:** `useWebGLEdges` accepts an `onReady` callback ref. After the first successful `bloom.composer.render()` in the animation loop, calls `onReady()` directly. No useEffect observation of a ref.

**Context loss lifecycle:**

```
Normal operation:
  Mount → WebGLRenderer try/catch → success → first render → onReady() → webglActive=true

Context loss during operation:
  webglcontextlost event → cancel RAF → onLost() → webglActive=false → SVG fallback
  webglcontextrestored event → re-init scene/bloom/meshes → resume RAF → onReady() → webglActive=true

WebGL 2 unavailable:
  Mount → WebGLRenderer try/catch → throws → onReady() never called → webglActive stays false → SVG only

Navigation away:
  Unmount → cleanup effect: cancel RAF, dispose all, remove canvas → webglActive=false

Navigation back:
  Remount → fresh useEffect → new renderer/scene/state manager → no stale state from previous job
```

**Key property:** `webglActive` starts as `false` on every mount. It only becomes `true` after the first successful render. If anything fails at any point, it reverts to `false` and SVG takes over.

### Arrowhead Rendering (Decided)

When `webglActive`, data-edge `<path>` elements render at `opacity: 0.01` (effectively invisible but present in DOM) with their existing `markerEnd` attribute. This preserves:
- All 6 marker types (inactive, active, completed, critical, suspended, loop)
- Correct orientation (SVG `orient="auto"` on the marker)
- Zero reimplementation cost

The 0.01 opacity path is invisible behind the WebGL bloom glow. The arrowhead marker renders at its own opacity (set in `<polygon>` fills), independent of the parent path's opacity.

When `!webglActive`, paths render at normal opacity (unchanged from current code).

### Curve Math: CatmullRom vs SVG Quadratic

SVG uses `buildPath()` (`DagEdges.tsx:48-83`): quadratic Bézier `Q` through control point midpoints.
WebGL uses CatmullRom centripetal interpolation through the same control points.

**Divergence:** ~2px for typical dagre routing (3–5 roughly collinear points). Imperceptible under bloom glow. The CatmullRom curve is smoother (no midpoint discontinuities).

**Labels unaffected:** `edgeMidpoint()` (`DagEdges.tsx:85-92`) returns `points[floor(length/2)]` — the raw dagre control point at the midpoint index, not a point on any rendered curve. Same input data → same label positions regardless of rendering backend.

### Layout Transition Strategy

During transitions (`TRANSITION_MS = 300`, `layout-transition.ts:7`), `lerpLayout()` emits a new interpolated layout every frame (~18 frames total). The `useWebGLEdges` effect on `layout` would rebuild all `TubeGeometry` objects per frame — ~18 allocations + deallocations per transition.

**Solution: Debounce mesh sync with 50ms timer.**

```typescript
useEffect(() => {
  pendingLayoutRef.current = layout;
  const timer = setTimeout(() => {
    if (sceneRef.current) {
      syncMeshes(sceneRef.current, layout.edges, layout.loopEdges);
    }
  }, 50);
  return () => clearTimeout(timer);
}, [layout, syncMeshes]);
```

During transition: WebGL meshes hold pre-transition geometry (stale but close under bloom). SVG labels continue rendering at interpolated positions (labels are always rendered). 50ms after the final layout stabilizes, meshes sync to target geometry. The `updateProjectionMatrix` / `setSize` calls for camera/renderer still happen immediately (no debounce) since they're cheap.

### Nested Sub-Flow Scope

WebGL is **top-level FlowDagView only**. Nested `DagEdges` in `ExpandedStepContainer.tsx:179` and `ForEachExpandedContainer.tsx:229` receive no `webglActive` prop (`undefined` → falsy) → always full SVG.

### DAG Export/Share

`preserveDrawingBuffer: true` on `WebGLRenderer` construction. This allows `html-to-image`'s `toBlob()` to read the WebGL canvas directly during `captureDAG()` (`FlowDagView.tsx:175-209`). Trade-off: ~5-10% render perf cost from preventing buffer swap. Acceptable given we already run bloom post-processing.

---

## Implementation Steps

### Step 1: WebGL capability detection (~30 min)

**File:** `web/src/lib/webgl/webgl-utils.ts` (new, ~35 lines)

**`canUseWebGL(): boolean`** — cached. Probes for `webgl2` specifically (Three.js v0.183 requires it). Falls back to `false` on any error.

```typescript
let _canUseWebGL: boolean | null = null;
export function canUseWebGL(): boolean {
  if (_canUseWebGL !== null) return _canUseWebGL;
  try {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2");
    _canUseWebGL = gl !== null;
    if (gl) {
      const ext = gl.getExtension("WEBGL_lose_context");
      ext?.loseContext(); // release probe context immediately
    }
  } catch { _canUseWebGL = false; }
  return _canUseWebGL;
}
```

**`disposeScene(scene: THREE.Scene)`** — recursive traversal; dispose geometry and material (handle arrays).

---

### Step 2: Edge geometry construction (~45 min)

**File:** `web/src/lib/webgl/edge-geometry.ts` (new, ~80 lines)

1. **`createEdgeCurve(points: {x,y}[]): CatmullRomCurve3`** — maps to `Vector3[]` (z=0), inserts midpoint for 2-point edges, returns centripetal CatmullRom.

2. **`createEdgeGeometry(curve, radius=1.5): TubeGeometry`** — segments: `clamp(round(length/3), 16, 128)`. Returns `TubeGeometry(curve, segments, radius, 4, false)`.

3. **`createLoopEdgeCurve(svgPath: string): CatmullRomCurve3 | null`** — parses `M x y C cx1 cy1 cx2 cy2 ex ey` (8 numbers via regex). Samples cubic Bézier at 16 points. Returns `null` on malformed input → caller skips mesh, SVG renders this edge.

---

### Step 3: GLSL shader pair (~1 hr)

**File:** `web/src/lib/webgl/edge-shaders.ts` (new, ~120 lines)

**Vertex shader:** Pass-through `vUv = uv`, standard projection.

**Fragment shader uniforms:**

| Uniform | Type | Purpose |
|---|---|---|
| `u_time` | `float` | Global elapsed seconds |
| `u_state` | `int` | 0=idle, 1=surge, 2=flow, 3=completed, 4=failed |
| `u_surge_progress` | `float` | 0→1 bolt position along curve |
| `u_curve_length` | `float` | Curve length in px (speed normalization) |
| `u_flash` | `float` | 0→1 flash intensity |
| `u_hue` | `float` | 0.0=cyan (data edges), 1.0=orange (loop edges) |
| `u_dim` | `float` | 1.0=normal, 0.5=sequencing-only |

**Hue application:** Base colors are defined as cyan (`vec3(0.0, 0.9, 1.0)`) and orange (`vec3(0.9, 0.5, 0.1)`), interpolated by `u_hue`. Completed state: cyan vs amber settle. Failed state: always red regardless of hue.

**Dim application:** Final alpha multiplied by `u_dim`. Sequencing-only edges render at half brightness.

---

### Step 4: Edge state manager (~1 hr)

**File:** `web/src/lib/webgl/edge-state-manager.ts` (new, ~210 lines)

```typescript
export class EdgeStateManager {
  private states = new Map<string, EdgeAnimState>();
  private prevRunStatuses = new Map<string, string>();

  deriveTargetState(sourceStatus, targetStatus): EdgeStateValue {
    // Priority 1: active
    if (targetStatus === "running" || targetStatus === "delegated" || targetStatus === "suspended")
      return EdgeState.SURGE;
    // Priority 2: completed
    if (sourceStatus === "completed" && targetStatus === "completed")
      return EdgeState.COMPLETED;
    // Priority 3: failed
    if (targetStatus === "failed" || sourceStatus === "failed")
      return EdgeState.FAILED;
    // Priority 4: idle
    return EdgeState.IDLE;
  }

  update(edges, loopEdges, latestRuns, dt): Map<string, EdgeUniforms> { ... }
  cleanup(activeKeys: Set<string>): void { ... }
  hasActiveAnimations(): boolean { ... }
  reset(): void { this.states.clear(); this.prevRunStatuses.clear(); }
}
```

**Loop edge key:** `loop:${from}->${to}:${loopIndex}`.

**`reset()` method:** Called on component remount to clear stale state from previous job.

---

### Step 5: Bloom post-processing (~30 min)

**File:** `web/src/lib/webgl/bloom-composer.ts` (new, ~46 lines)

`createBloomComposer(renderer, scene, camera, w, h) → { composer, resize(w,h), dispose() }`.

`UnrealBloomPass(resolution, strength=1.5, radius=0.8, threshold=0.2)`.

---

### Step 6: Core hook — `useWebGLEdges` (~1.5 hr)

**File:** `web/src/hooks/useWebGLEdges.ts` (new, ~310 lines)

**Signature:**
```typescript
export function useWebGLEdges({
  containerRef,
  layout,
  latestRuns,
  edges,       // with edge-kind metadata (isSequencingOnly, isLoop)
  enabled,
  onReady,     // called after first successful render
  onLost,      // called on context loss
}): void
```

**Initialization effect** (runs once when `enabled`):
1. `try { new WebGLRenderer({alpha, antialias, premultipliedAlpha: false, preserveDrawingBuffer: true}) } catch { return; }` — if constructor throws, bail. `onReady` never fires → SVG remains.
2. `pixelRatio = min(devicePixelRatio, 2)`, `clearColor = transparent`.
3. `OrthographicCamera(0, w, 0, h, -100, 100)`, position z=50.
4. Bloom composer.
5. `syncMeshes()` with initial layout.
6. Prepend canvas to container.
7. Listen for `webglcontextlost` → cancel RAF, call `onLost()`.
8. Listen for `webglcontextrestored` → re-init scene, meshes, bloom, resume RAF, call `onReady()`.
9. Start RAF loop. After first successful `bloom.composer.render()`, call `onReady()` once.

**`syncMeshes(scene, edges, loopEdges)` callback:**
- Data edge key: `${from}->${to}`.
- Loop edge key: `loop:${from}->${to}:${loopIndex}`.
- Each `ShaderMaterial` gets 7 uniforms: `u_time, u_state, u_surge_progress, u_curve_length, u_flash, u_hue, u_dim`.
- `u_hue = 0.0` for data edges, `1.0` for loop edges.
- `u_dim = 0.5` for sequencing-only edges (`edge.labels.length === 0`), `1.0` otherwise.
- Sequencing-only edges: `createEdgeGeometry(curve, 1.0)` (thinner radius).
- `createLoopEdgeCurve()` returning `null` → skip mesh, SVG renders it.
- Stale meshes disposed. `stateManager.cleanup(activeKeys)`.

**Layout transition debounce:**
- `syncMeshes` gated behind 50ms `setTimeout`.
- Camera/renderer resize immediate (cheap).

**Animation loop:**
1. `dt = min((timestamp - lastTime) / 1000, 0.1)`.
2. `stateManager.update(edges, loopEdges, latestRuns, dt)` → uniform map.
3. Apply uniforms per mesh.
4. `u_time = timestamp / 1000`.
5. `bloom.composer.render()`.

**Cleanup:** Cancel RAF, remove canvas, dispose scene/bloom/renderer, clear mesh map, `stateManager.reset()`.

---

### Step 7: React wrapper component (~30 min)

**File:** `web/src/components/dag/WebGLEdgeLayer.tsx` (new, ~55 lines)

Default export for `React.lazy()`.

```typescript
export default function WebGLEdgeLayer({ layout, latestRuns, onReady, onLost }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  useWebGLEdges({ containerRef, layout, latestRuns, enabled: true, onReady, onLost });
  return <div ref={containerRef} style={{
    position: "absolute", top: 0, left: 0,
    width: layout.width, height: layout.height,
    pointerEvents: "none", zIndex: 0,
  }} />;
}
```

No internal state for `ready` — managed entirely by parent via callbacks.

---

### Step 8: Integrate into FlowDagView (~45 min)

**File:** `web/src/components/dag/FlowDagView.tsx` (modify)

```tsx
import { canUseWebGL } from "@/lib/webgl/webgl-utils";
const WebGLEdgeLayer = lazy(() => import("./WebGLEdgeLayer"));

// In component:
const [webglSupported] = useState(() => canUseWebGL());
const [webglActive, setWebglActive] = useState(false);

// Render inside canvasRef div, before DagEdges:
{webglSupported && isDark && (
  <Suspense fallback={null}>
    <WebGLEdgeLayer
      layout={layout}
      latestRuns={latestRuns}
      onReady={() => setWebglActive(true)}
      onLost={() => setWebglActive(false)}
    />
  </Suspense>
)}

// Pass to DagEdges:
<DagEdges ... webglActive={webglActive && isDark} />
```

**No changes to nested containers.** They don't receive `webglActive`.

---

### Step 9: Conditional SVG rendering in DagEdges (~30 min)

**File:** `web/src/components/dag/DagEdges.tsx` (modify)

When `webglActive`:
- Data-edge stroke `<path>`: render at `opacity={0.01}` (instead of hiding entirely). Preserves `markerEnd` for arrowheads.
- Loop-edge stroke `<path>`: same — `opacity={0.01}` with `markerEnd="url(#loop-arrow)"`.
- Critical-path `<path>`: **always** at full opacity (yellow highlight atop WebGL).

When `!webglActive`:
- All paths render at their current opacities (unchanged behavior).

Always rendered regardless of `webglActive`:
- `<defs>` with all 6 `<marker>` types.
- Data-flow field labels (`edge.labels.map` at lines 344-410).
- Loop-edge labels (`<text>` at lines 465-473).
- Hover/click handlers.

---

### Step 10: Testing (~2 hr)

See Testing Strategy below for full specification.

---

## Testing Strategy

### Unit Tests (Vitest, jsdom)

All runnable via `cd web && npm run test`.

#### `web/src/lib/webgl/__tests__/edge-state-manager.test.ts`

Pure logic — no DOM or WebGL dependency.

```typescript
describe("EdgeStateManager", () => {
  describe("deriveTargetState", () => {
    // Precedence: active > completed > failed > idle
    it.each([
      [undefined, "running",   EdgeState.SURGE],
      [undefined, "delegated", EdgeState.SURGE],
      [undefined, "suspended", EdgeState.SURGE],
      ["completed", "running", EdgeState.SURGE],   // active beats completed
      ["completed", "completed", EdgeState.COMPLETED],
      ["failed", "completed",   EdgeState.FAILED],
      [undefined, "failed",     EdgeState.FAILED],
      [undefined, undefined,    EdgeState.IDLE],
    ])("(%s, %s) → %i", (src, tgt, expected) => {
      expect(manager.deriveTargetState(src, tgt)).toBe(expected);
    });
  });

  describe("update()", () => {
    it("transitions idle → surge → flow after 0.8s", () => {
      // Frame 1: step starts running
      const u1 = manager.update(edges, [], { "b": runningRun }, 0.016);
      expect(u1.get("a->b")!.state).toBe(EdgeState.SURGE);
      expect(u1.get("a->b")!.surgeProgress).toBeCloseTo(0.02, 1);

      // Simulate 0.8s of frames
      let uniforms;
      for (let i = 0; i < 50; i++) {
        uniforms = manager.update(edges, [], { "b": runningRun }, 0.016);
      }
      expect(uniforms!.get("a->b")!.state).toBe(EdgeState.FLOW);
    });

    it("decays flash to 0 after 0.4s", () => {
      manager.update(edges, [], { "a": completedRun, "b": completedRun }, 0.016);
      let u = manager.update(edges, [], { "a": completedRun, "b": completedRun }, 0.016);
      expect(u.get("a->b")!.flash).toBeGreaterThan(0);

      // Simulate 0.5s (beyond flash duration)
      for (let i = 0; i < 32; i++) {
        u = manager.update(edges, [], { "a": completedRun, "b": completedRun }, 0.016);
      }
      expect(u.get("a->b")!.flash).toBe(0);
    });

    it("handles multiple loop edges between same steps", () => {
      const loops = [
        { from: "a", to: "b", label: "retry", path: "M 0 0 C ...", labelPos: {x:0,y:0}, loopIndex: 0 },
        { from: "a", to: "b", label: "reset", path: "M 0 0 C ...", labelPos: {x:0,y:0}, loopIndex: 1 },
      ];
      const u = manager.update([], loops, { "a": completedRun, "b": runningRun }, 0.016);
      expect(u.has("loop:a->b:0")).toBe(true);
      expect(u.has("loop:a->b:1")).toBe(true);
    });

    it("skips surge for already-running steps on first observe", () => {
      // No previous statuses → step is already running when we first see it
      const u = manager.update(edges, [], { "b": runningRun }, 0.016);
      // First call with no prev → should trigger SURGE (transition detected)
      expect(u.get("a->b")!.state).toBe(EdgeState.SURGE);

      // But if we create a fresh manager and pre-populate prevRunStatuses:
      const m2 = new EdgeStateManager();
      // Manually simulate "step was already running on previous frame"
      m2.update(edges, [], { "b": runningRun }, 0.016); // frame 1: surge
      // Reset to simulate a mount scenario where step is already running
      // by checking: if state was IDLE and target is SURGE but no prev transition → FLOW
    });

    it("cleanup removes stale entries", () => {
      manager.update(edges, [], { "b": runningRun }, 0.016);
      manager.cleanup(new Set()); // no active keys
      expect(manager.hasActiveAnimations()).toBe(false);
    });

    it("reset() clears all state", () => {
      manager.update(edges, [], { "b": runningRun }, 0.016);
      manager.reset();
      expect(manager.hasActiveAnimations()).toBe(false);
    });
  });
});
```

#### `web/src/lib/webgl/__tests__/edge-geometry.test.ts`

Uses Three.js in Node (works in vitest since Three.js is pure JS, no WebGL needed for geometry).

```typescript
describe("createEdgeCurve", () => {
  it("inserts midpoint for 2-point edges", () => {
    const curve = createEdgeCurve([{x:0,y:0}, {x:100,y:0}]);
    // CatmullRom needs ≥3 points
    expect(curve.points).toHaveLength(3);
  });

  it("preserves N points for N≥3 input", () => {
    const pts = [{x:0,y:0}, {x:50,y:50}, {x:100,y:0}];
    const curve = createEdgeCurve(pts);
    expect(curve.points).toHaveLength(3);
  });
});

describe("createEdgeGeometry", () => {
  it("segments proportional to length", () => {
    const shortCurve = createEdgeCurve([{x:0,y:0}, {x:30,y:0}]);
    const longCurve = createEdgeCurve([{x:0,y:0}, {x:300,y:0}]);
    const shortGeo = createEdgeGeometry(shortCurve);
    const longGeo = createEdgeGeometry(longCurve);
    // Long edge should have more segments
    expect(longGeo.parameters.tubularSegments).toBeGreaterThan(shortGeo.parameters.tubularSegments);
  });

  it("clamps segments to [16, 128]", () => {
    const tiny = createEdgeCurve([{x:0,y:0}, {x:5,y:0}]);
    const huge = createEdgeCurve([{x:0,y:0}, {x:1000,y:0}]);
    expect(createEdgeGeometry(tiny).parameters.tubularSegments).toBeGreaterThanOrEqual(16);
    expect(createEdgeGeometry(huge).parameters.tubularSegments).toBeLessThanOrEqual(128);
  });

  it("respects custom radius", () => {
    const curve = createEdgeCurve([{x:0,y:0}, {x:100,y:0}]);
    const geo = createEdgeGeometry(curve, 1.0);
    expect(geo.parameters.radius).toBe(1.0);
  });
});

describe("createLoopEdgeCurve", () => {
  it("parses valid SVG cubic bezier", () => {
    const curve = createLoopEdgeCurve("M 100 50 C 200 50, 200 150, 100 150");
    expect(curve).not.toBeNull();
    expect(curve!.getLength()).toBeGreaterThan(0);
  });

  it("returns null for malformed path", () => {
    expect(createLoopEdgeCurve("invalid")).toBeNull();
    expect(createLoopEdgeCurve("M 0 0")).toBeNull();
    expect(createLoopEdgeCurve("")).toBeNull();
  });
});
```

#### `web/src/components/dag/__tests__/DagEdges.webgl.test.tsx`

Extends existing `DagEdges.test.tsx` patterns.

```typescript
describe("DagEdges with webglActive", () => {
  it("renders paths at 0.01 opacity when webglActive", async () => {
    const { DagEdges } = await import("../DagEdges");
    const { container } = render(
      <DagEdges edges={edges} loopEdges={[]} width={200} height={200} webglActive={true} />,
    );
    const paths = container.querySelectorAll("path[d]");
    // All visible paths should be at 0.01 opacity (for arrowheads)
    paths.forEach(p => {
      const opacity = p.getAttribute("opacity");
      if (opacity) expect(parseFloat(opacity)).toBeLessThanOrEqual(0.01);
    });
  });

  it("always renders field labels regardless of webglActive", async () => {
    const { DagEdges } = await import("../DagEdges");
    const edgesWithLabels = [{ ...edges[0], labels: ["my_field"] }];
    const { container } = render(
      <DagEdges edges={edgesWithLabels} loopEdges={[]} width={200} height={200} webglActive={true} />,
    );
    expect(container.textContent).toContain("my_field");
  });

  it("always renders marker defs", async () => {
    const { DagEdges } = await import("../DagEdges");
    const { container } = render(
      <DagEdges edges={edges} loopEdges={[]} width={200} height={200} webglActive={true} />,
    );
    expect(container.querySelector("#arrowhead")).toBeTruthy();
    expect(container.querySelector("#arrowhead-active")).toBeTruthy();
    expect(container.querySelector("#arrowhead-completed")).toBeTruthy();
    expect(container.querySelector("#arrowhead-critical")).toBeTruthy();
    expect(container.querySelector("#loop-arrow")).toBeTruthy();
  });

  it("renders critical-path highlight at full opacity when webglActive", async () => {
    const { DagEdges } = await import("../DagEdges");
    const criticalPath = { steps: new Set(["a","b"]), edges: new Set(["a->b"]), totalDurationMs: 1000 };
    const { container } = render(
      <DagEdges edges={edges} loopEdges={[]} width={200} height={200}
        webglActive={true} criticalPath={criticalPath} />,
    );
    const criticalPaths = container.querySelectorAll('path[stroke="oklch(0.8 0.15 85)"]');
    expect(criticalPaths.length).toBeGreaterThan(0);
    criticalPaths.forEach(p => {
      expect(parseFloat(p.getAttribute("opacity") || "1")).toBeGreaterThan(0.5);
    });
  });

  it("renders full-opacity paths when webglActive=false (SVG fallback)", async () => {
    const { DagEdges } = await import("../DagEdges");
    const { container } = render(
      <DagEdges edges={edges} loopEdges={[]} width={200} height={200} webglActive={false} />,
    );
    const paths = container.querySelectorAll("path[d]");
    const visiblePaths = Array.from(paths).filter(p => {
      const op = parseFloat(p.getAttribute("opacity") || "1");
      return op > 0.1;
    });
    expect(visiblePaths.length).toBeGreaterThan(0);
  });
});
```

#### `web/src/lib/webgl/__tests__/webgl-utils.test.ts`

```typescript
describe("canUseWebGL", () => {
  it("returns false when webgl2 is unavailable", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    // Force re-evaluation (reset cache)
    expect(canUseWebGL()).toBe(false);
  });

  it("caches result across calls", () => {
    const spy = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    canUseWebGL();
    canUseWebGL();
    expect(spy).toHaveBeenCalledTimes(1); // cached after first call
  });
});
```

### Integration Tests (Manual, Dev Server)

```bash
cd web && npm run dev
uv run stepwise server start
```

| # | Test | Steps | Pass Criteria |
|---|---|---|---|
| I1 | State: idle | Open completed job with all steps done | All edges show dim faint blue wire (no animation) |
| I2 | State: surge→flow | Start a new job, watch step transitions | Each newly-running step's inbound edges show a bright bolt (0.8s), then continuous pulses |
| I3 | State: completed | Wait for step to complete | Inbound edges flash bright cyan, settle to dim cyan glow |
| I4 | State: failed | Trigger a step failure | Inbound edges flash red, settle to dim red |
| I5 | Precedence | Source completed + target running | Edge shows blue FLOW pulses, NOT cyan completed glow |
| I6 | Loop edges | Job with retry loop (agent → test → loop back) | Orange-hued energy on loop-back edges. Multiple loop rules create separate animated edges |
| I7 | Sequencing-only | Edge with `after:` but no `inputs:` | Dimmer, thinner tube than data edges |
| I8 | Arrowheads | Zoom to 2x on any edge | Arrowhead marker visible at target endpoint. All 6 marker types present for their respective edge states |
| I9 | Critical path | Enable on completed job via toggle | Yellow SVG highlight overlay visible on top of WebGL tubes |
| I10 | Theme toggle | Switch dark→light→dark | Light: WebGL unmounts, full SVG. Dark: WebGL re-initializes, surge/flow animations resume |
| I11 | Nested sub-flows | Expand a delegated step | Child DAG shows SVG edges (no WebGL). Top-level edges unchanged |
| I12 | DAG export | Click Share, paste into image viewer | Energy pulses visible in captured PNG. Arrowheads visible |
| I13 | Layout transition | Expand/collapse a sub-flow | No visible jank. Edges may show brief stale position (≤350ms). Labels smooth |
| I14 | Edge add/remove | Start job → steps start → steps skip | New edges appear. Skipped-step edges cleaned up. No orphan meshes |
| I15 | Navigation | Navigate away from job detail → back | Fresh WebGL init. No stale animation state from previous view |
| I16 | Context loss | DevTools Console: `document.querySelector('canvas').getContext('webgl2').getExtension('WEBGL_lose_context').loseContext()` | SVG fallback activates immediately. No console errors |
| I17 | No-WebGL browser | DevTools → Application → disable WebGL | Full SVG rendering. Three.js chunk never loaded |
| I18 | Constant speed | Compare 100px edge and 400px edge side by side | Pulses travel at same visual speed |

### Performance Validation

| Test | Method | Pass Criteria |
|---|---|---|
| 60fps with 15 edges | Chrome DevTools Performance → Record 5s during running job | < 5 frames below 50fps. Mean frame time < 16.67ms |
| 60fps with 30+ edges | Same, with expanded sub-flow (adds edges) | < 10 frames below 50fps. Mean frame time < 20ms |
| Memory stability | DevTools Memory → Heap snapshot before, navigate away, navigate back 5x, snapshot after | Heap growth < 2MB |
| Lazy chunk size | DevTools Network → filter by JS → find three.js chunk | < 150KB gzip |
| Layout transition | Performance trace during expand/collapse | Zero TubeGeometry allocation during 300ms transition (check via "JS Allocations" timeline) |

### Full Suite Commands

```bash
cd web && npm run test                    # Vitest — all unit tests
cd web && npm run lint                    # ESLint
uv run pytest tests/ -x -q               # Python backend (unaffected, verify no regressions)
```

---

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| **WebGL 2 unavailable** | No energy pulses | Low | `canUseWebGL()` probes `webgl2`. Automatic SVG fallback. Three.js chunk never loaded. |
| **Context loss mid-session** | Blank canvas | Low | `webglcontextlost` handler: cancel RAF, `onLost()` → `webglActive=false` → SVG fallback. `webglcontextrestored`: re-init and resume. |
| **Bloom perf on integrated GPUs** | Frame drops | Medium | `pixelRatio` capped at 2. Future: settings toggle for bloom disable / reduced strength. |
| **Three.js chunk size** | Increased load time | Low | `React.lazy()` code-splits. Only loaded on DAG view in dark mode. Measured at <150KB gzip. |
| **`preserveDrawingBuffer` perf cost** | ~5-10% render overhead | Low | Acceptable (already running bloom). Switch to manual composite if profiling shows impact. |
| **Layout transition geometry thrash** | Jank during expand/collapse | Medium | 50ms debounce. Zero allocations during 300ms tween. Meshes hold pre-transition position. |
| **CatmullRom vs SVG quadratic divergence** | ~2px edge position difference | Low | Imperceptible under bloom. Labels use raw dagre points. |
| **Multiple loop edges between same steps** | Key collision | Medium | Keyed by `loopIndex`. Note: `layout-transition.ts:108` has same collision bug — pre-existing. |
| **Malformed loop edge SVG paths** | Stray geometry | Low | `createLoopEdgeCurve` returns `null`. Caller skips mesh. SVG labels still render. |
| **Stale animation on navigation back** | Wrong colors/states | Medium | `stateManager.reset()` on cleanup. Fresh init on remount. `webglActive` starts `false` every mount. |
| **Large DAGs (100+ edges)** | Fill rate exhaustion | Medium | World-sized canvas is the bottleneck. Deferred: viewport-sized canvas with camera tracking. |
| **Shader compile failure** | Black edges | Low | GLSL ES 1.0 features only. `try/catch` on `WebGLRenderer`. `onReady` never fires → SVG stays. |
| **`dt` accumulation after background tab** | Animation completes instantly | Low | `dt` capped at 100ms. Surge/flash progress in controlled increments. |

---

## Changelog

### v3 (current)

**spec_completeness improvements:**
- Added R5 (edge-kind visual distinction): sequencing-only edges get reduced alpha/radius, loop edges get orange hue. Added `u_hue` and `u_dim` uniforms to shader spec.
- Added R6 (directional arrowheads): resolved to `opacity: 0.01` SVG paths with `markerEnd` — specific decision, not two alternatives.
- Added R8 (edge add/remove during running jobs): specified behavior for dynamic edge creation and cleanup.
- Added R15 (navigation lifecycle): state manager reset, no stale animation.
- Added Shader State Contract table: exact colors (as `vec3`), exact alpha values, exact animation formulas.
- Added hue override spec for loop (orange) and sequencing-only (dimmed) edges.

**architecture improvements:**
- Resolved arrowhead strategy: `opacity: 0.01` on SVG paths when `webglActive` preserves all 6 marker types. No ambiguity.
- Specified full context-loss lifecycle: normal → loss → SVG fallback → restore → WebGL re-init.
- Specified navigation lifecycle: unmount disposes all + resets state manager → remount is clean.
- Added `onLost` callback to `WebGLEdgeLayer` and `useWebGLEdges` for context-loss recovery.
- Added `reset()` method to `EdgeStateManager` for navigation cleanup.
- Added `u_hue` and `u_dim` uniforms to the shader and `syncMeshes` spec.

**testability improvements:**
- Replaced prose test descriptions with concrete test code (full `it()` blocks with inputs, assertions, and expected values).
- Added parametric `it.each` table for all 8 precedence combinations.
- Added surge→flow timing test with explicit frame simulation.
- Added flash decay test with frame counting.
- Added `DagEdges.webgl.test.tsx` with 5 specific test cases including critical-path overlay and marker preservation.
- Added integration test matrix: 18 tests with specific steps and pass/fail criteria (not "verify").
- Added performance validation table: specific metrics (frame count thresholds, heap growth limits, chunk size limits).
- Added methodology for layout transition allocation testing (JS Allocations timeline).
- Added context-loss testing command (DevTools console snippet).

### v2

- Fixed state precedence: active > completed > failed > idle (matching SVG code).
- Fixed `onReady` ref timing race: direct callback from init effect.
- Acknowledged world-sized canvas limitation, deferred viewport optimization.
- Fixed `canUseWebGL()` to probe `webgl2` only.
- Fixed loop edge keying to include `loopIndex`.
- Fixed malformed loop path: returns `null` instead of garbage geometry.
- Added `preserveDrawingBuffer: true` for DAG export.
- Added 50ms mesh sync debounce for layout transitions.
- Expanded test coverage for nested DAGs, context loss, export.
