# Plan: WebGL Energy Pulse Visualization for DAG Edges

## Overview

Replace the SVG-based edge rendering in the Stepwise DAG view with a WebGL canvas layer (Three.js) that renders energy pulse animations on edge lines — bright cyan/white surge heads with exponentially decaying tails and bloom post-processing — while keeping HTML step nodes as React components. The WebGL canvas sits behind the existing DOM node layer, reads dagre layout positions, and renders animated tube geometries with custom GLSL shaders driven by step execution state.

## Requirements

| # | Requirement | Acceptance Criteria |
|---|---|---|
| R1 | Idle edges render as faint dim wires | Edges between steps with no runs or only pending steps show a visible-but-subtle line (≈0.2 opacity) |
| R2 | Running step triggers initial surge | When a step transitions to `running`, a bright cyan/white bolt travels source→target over ~800ms |
| R3 | Continuous flow while running | After surge completes, repeating energy waves pulse along the edge at constant visual speed |
| R4 | Completed state settled glow | When the source step completes, a brief flash then settle to solid dim cyan glow |
| R5 | Failed state red pulse | When a step fails, its incoming edges pulse red then dim to faint red |
| R6 | Constant visual speed across edges | Pulse speed is normalized by `u_curve_length` — short and long edges animate at the same visual velocity |
| R7 | Bloom post-processing | Light bleeds into surrounding dark pixels, matching the reference screenshots |
| R8 | DOM↔WebGL coordinate sync | WebGL canvas stays aligned with HTML nodes during pan, zoom, resize, and layout transitions |
| R9 | Edge labels remain functional | SVG field name labels stay interactive (hover tooltips, click for data flow selection) |
| R10 | Performance: 60fps on M1 MacBook | With 20+ edges animating, requestAnimationFrame stays under 16ms |
| R11 | Graceful SVG fallback | If WebGL context creation fails, fall back to existing SVG DagEdges rendering |
| R12 | Bundle size < 170KB gzipped | Three.js loaded via dynamic import; does not block initial page load |

## Assumptions

Each assumption has been verified against the actual codebase:

1. **Edges are rendered inside `canvasRef` (a plain `<div>`)** — `FlowDagView.tsx:564-572` creates a `<div ref={canvasRef}>` with `transformOrigin: "0 0"` and CSS transform for pan/zoom. The WebGL canvas can be an absolute-positioned child of this same div.

2. **`DagEdges` is a self-contained SVG component** — `DagEdges.tsx:143-469` renders a single `<svg>` with all edge paths and labels. It receives `edges: DagEdge[]` and `loopEdges: LoopEdge[]` from the parent. We can replace the SVG path rendering while keeping the label overlays.

3. **Edge points come from dagre as `{x, y}[]`** — `dag-layout.ts:209-223` extracts `edge.points` from dagre's computed layout. These are in canvas-local coordinates (same space as node positions).

4. **Layout transitions interpolate edge points** — `layout-transition.ts:53-61` (`lerpEdge`) interpolates control points each frame during the 300ms transition. The WebGL layer must read from the interpolated layout, not the raw layout.

5. **Camera transform is applied via CSS `translate + scale`** — `useDagCamera.ts:76-89` sets `canvasRef.style.transform`. Since the WebGL canvas is inside `canvasRef`, it inherits this transform automatically — no separate coordinate conversion needed.

6. **Step run status is available as `latestRuns: Record<string, StepRun>`** — `FlowDagView.tsx:380-389` builds this map. The WebGL layer needs this to determine edge animation state.

7. **No Three.js or WebGL exists in the codebase today** — grep confirms zero references to Three.js, WebGL, or `<canvas>` for rendering. This is a fresh addition.

8. **Vite handles dynamic imports with code splitting** — `vite.config.ts:1-27` uses standard Vite config with no custom chunk strategy. `import("three")` will create a separate chunk automatically.

9. **Dark mode is the primary context** — the reference screenshots and the DAG background (`bg-zinc-950/50` in `FlowDagView.tsx:526`) are dark. Bloom and additive blending are designed for dark backgrounds.

10. **Loop edges use pre-computed SVG paths** — `dag-layout.ts:117-131` (`computeLoopEdgePath`) returns a cubic Bézier SVG path string. We need to parse or recompute these as Three.js curves.

## Out of Scope

- **Node card redesign** — Step nodes remain as HTML React components with current styling.
- **Light mode bloom** — Bloom is optimized for dark mode only. Light mode gets the SVG fallback or muted WebGL.
- **3D perspective or depth** — All rendering stays 2D (z=0 plane). Camera is orthographic.
- **WebGL rendering of node cards** — Only edges and their energy effects are WebGL.
- **Particle systems** — The spec uses tube geometry + fragment shader, not GPU particles.
- **Mobile-specific optimizations** — Performance target is desktop/laptop. Mobile falls back to SVG if needed.
- **Container port edges** — `ContainerPortEdges.tsx` (child-layout input/output lines) stay SVG for now.
- **MiniDag.tsx** — The compact overview DAG on the canvas page is unaffected.
- **DAG capture (Share/Download)** — `html-to-image` cannot capture WebGL. A follow-up would use `renderer.domElement.toDataURL()`. For now, capture falls back to SVG-only rendering.

## Architecture

### Component Hierarchy

```
FlowDagView (containerRef + canvasRef)
├── WebGLEdgeLayer          ← NEW: absolute-positioned <canvas>, z-index below nodes
│   ├── Three.js Scene
│   │   ├── OrthographicCamera
│   │   ├── TubeGeometry meshes (one per edge)
│   │   └── EffectComposer (UnrealBloomPass)
│   └── EdgeStateManager    ← NEW: maps step runs to per-edge shader uniforms
├── DagEdges (SVG)          ← MODIFIED: only renders labels + interaction areas (paths removed)
├── FlowPortNode[]
├── StepNode[]
├── ExpandedStepContainer[]
└── ExternalInputPanel
```

### Data Flow

```
dagre layout → useLayoutTransition → interpolated layout
                                           ↓
                              ┌─── WebGLEdgeLayer (reads edges[], loopEdges[])
                              │         ↓
                              │    Three.js scene rebuild on layout change
                              │    Shader uniforms update on rAF tick
                              │         ↓
                              │    EffectComposer renders to <canvas>
                              │
                              └─── DagEdges (labels only, no paths)
                                        ↓
                                   SVG text/rect elements for field names
```

### Key Design Decisions

1. **WebGL canvas is a child of `canvasRef`**, not a separate layer. This means it inherits the CSS `translate/scale` transform from the camera system — no coordinate sync code needed. The canvas dimensions match `layout.width × layout.height` and are positioned at `(0, 0)` absolute within the canvas div.

2. **One `ShaderMaterial` per edge** (not instanced). With typical DAG sizes (5-30 edges), the draw call count is manageable. Each edge needs independent uniform state (`u_state`, `u_surge_progress`, `u_time`). Instancing would add complexity for minimal gain at this scale.

3. **Edge state is derived from step runs, not WebSocket events directly.** The `latestRuns` map (already computed in FlowDagView) drives edge state. When `latestRuns` changes (via React Query invalidation from WebSocket ticks), the `EdgeStateManager` updates uniforms. This keeps the WebGL layer a pure function of React state.

4. **SVG labels stay in DagEdges.** The existing label rendering (field names, value previews, hover tooltips, click handlers) is complex and well-tested. The WebGL layer only replaces the `<path>` elements. `DagEdges` is modified to skip path rendering but keep label groups.

5. **Three.js is loaded via `React.lazy` + dynamic `import()`** to keep the initial bundle small. The WebGL component itself is lazy-loaded. Until Three.js loads, the SVG fallback renders.

### File Structure

```
web/src/
├── components/dag/
│   ├── DagEdges.tsx                 # MODIFIED: labels-only mode when WebGL active
│   ├── WebGLEdgeLayer.tsx           # NEW: React component wrapping Three.js scene
│   ├── FlowDagView.tsx              # MODIFIED: mounts WebGLEdgeLayer
│   └── ...
├── lib/
│   ├── webgl/
│   │   ├── edge-geometry.ts         # NEW: dagre points → CatmullRom → TubeGeometry
│   │   ├── edge-shaders.ts          # NEW: GLSL vertex + fragment shader source
│   │   ├── edge-state-manager.ts    # NEW: step run status → per-edge uniforms
│   │   ├── bloom-composer.ts        # NEW: EffectComposer + UnrealBloomPass setup
│   │   └── webgl-utils.ts           # NEW: WebGL capability detection, cleanup helpers
│   └── dag-layout.ts               # UNCHANGED
└── hooks/
    └── useWebGLEdges.ts             # NEW: hook orchestrating scene lifecycle + rAF loop
```

## Implementation Steps

### Step 1: WebGL capability detection + fallback infrastructure (~30min)

**Files:** `web/src/lib/webgl/webgl-utils.ts`, `web/src/components/dag/FlowDagView.tsx`

Create `webgl-utils.ts`:
- `canUseWebGL(): boolean` — tries `document.createElement("canvas").getContext("webgl2")` (or `"webgl"` fallback), returns boolean. Caches result.
- `disposeScene(scene: THREE.Scene): void` — recursive geometry/material disposal helper.

Modify `FlowDagView.tsx`:
- Import `canUseWebGL` and store result in a ref (checked once).
- Add a `webglActive` boolean state (starts `false`, set to `true` after lazy load succeeds).
- Pass `webglActive` to `DagEdges` as a new prop (used in Step 7 to hide paths).

### Step 2: Edge geometry generation from dagre points (~1hr)

**Files:** `web/src/lib/webgl/edge-geometry.ts`

Functions:
- `createEdgeCurve(points: {x: number, y: number}[]): THREE.CatmullRomCurve3` — converts dagre edge points to a smooth centripetal Catmull-Rom spline in the z=0 plane. For 2-point edges, inserts a midpoint to give the spline enough control points.
- `createEdgeGeometry(curve: THREE.CatmullRomCurve3, radius?: number): THREE.TubeGeometry` — generates tube geometry with automatic tubular segment count based on curve length. Default radius: 1.5 (thin lines).
- `createLoopEdgeCurve(fromNode, toNode, loopIndex): THREE.CatmullRomCurve3` — converts loop edge geometry (currently an SVG cubic Bézier) to a Three.js curve. Samples the Bézier at 10-20 points and fits a CatmullRom.

All functions are pure (no scene mutation) and independently testable.

### Step 3: GLSL shaders — pulse state machine (~1hr)

**Files:** `web/src/lib/webgl/edge-shaders.ts`

Export `VERTEX_SHADER` and `FRAGMENT_SHADER` as template literal strings.

**Vertex shader:** Pass through UV coordinates. Standard MVP transform.

**Fragment shader uniforms:**
- `u_time: float` — global clock (seconds)
- `u_state: int` — 0=Idle, 1=Surge, 2=Flow, 3=Completed, 4=Failed
- `u_surge_progress: float` — 0→1.5 during surge animation
- `u_curve_length: float` — total curve arc length for speed normalization
- `u_flash: float` — 0→1 flash intensity for completion/failure flash

**Fragment shader logic:**
```glsl
// State 0 (Idle): faint wire
vec3 colorIdle = vec3(0.1, 0.15, 0.25);
float alpha = 0.2;

// State 1 (Surge): single bright bolt
// distance from surge front, exponential decay tail

// State 2 (Flow): repeating pulses, speed = BASE_SPEED / u_curve_length
// fract() for repetition, exp() for decay

// State 3 (Completed): brief flash → settle to dim cyan glow
// mix(flashColor, settledColor, 1.0 - u_flash)

// State 4 (Failed): red pulse → dim red
// Same structure as completed but with red palette
```

Additive blending + transparent + depthWrite=false on the material.

### Step 4: Edge state manager — step runs to shader uniforms (~1hr)

**Files:** `web/src/lib/webgl/edge-state-manager.ts`

Class `EdgeStateManager`:
- Constructor takes nothing; state is set per-frame.
- `updateEdgeStates(edges: DagEdge[], loopEdges: LoopEdge[], latestRuns: Record<string, StepRun>, deltaTime: number): EdgeState[]` — for each edge, determines target state from source/target step run status, manages surge→flow transition timing, updates `u_surge_progress` and `u_flash` animations.

Internal per-edge state tracking:
```typescript
interface EdgeAnimState {
  edgeKey: string;            // "from->to"
  currentState: 0 | 1 | 2 | 3 | 4;
  surgeProgress: number;      // animated 0→1.5
  flashIntensity: number;     // animated 1→0
  transitionTime: number;     // time since last state change
}
```

State transition rules:
- **No run → Idle (0)**: Default.
- **Target starts running → Surge (1)**: Detect `latestRuns[edge.to].status === "running"` when previous was not running. Animate `surgeProgress` from 0 to 1.5 over 800ms.
- **Surge complete → Flow (2)**: When `surgeProgress >= 1.0`.
- **Source completed + target completed/running → Completed (3)**: Flash then decay over 400ms.
- **Source or target failed → Failed (4)**: Red flash then decay.

### Step 5: Bloom post-processing setup (~45min)

**Files:** `web/src/lib/webgl/bloom-composer.ts`

Function `createBloomComposer(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: THREE.Camera, width: number, height: number)`:
- Creates `EffectComposer` with `RenderPass` + `UnrealBloomPass`.
- Bloom parameters tuned for the reference aesthetic:
  - `strength: 1.5` (high — lots of glow)
  - `radius: 0.8` (wide bloom spread)
  - `threshold: 0.2` (low — most of the bright edges emit bloom)
- Returns `{ composer: EffectComposer, resize(w, h): void, dispose(): void }`.

Import strategy: `UnrealBloomPass` and `EffectComposer` from `three/addons/postprocessing/`. These are part of the Three.js package but tree-shaken by Vite.

### Step 6: React hook — scene lifecycle + animation loop (~1.5hr)

**Files:** `web/src/hooks/useWebGLEdges.ts`

Hook `useWebGLEdges(options)`:

**Inputs:**
```typescript
interface UseWebGLEdgesOptions {
  canvasRef: RefObject<HTMLDivElement | null>;
  layout: HierarchicalDagLayout;          // interpolated layout (post-transition)
  latestRuns: Record<string, StepRun>;
  enabled: boolean;                        // false = skip all WebGL work
}
```

**Returns:** `{ canvasElement: HTMLCanvasElement | null, ready: boolean }`

**Lifecycle:**
1. **Mount:** Create `WebGLRenderer` with `{ alpha: true, antialias: true }`. Create `OrthographicCamera`. Create `Scene`. Create `EffectComposer`.
2. **Layout change:** Diff edges — add/remove/update tube meshes. Reuse geometries when points haven't changed. Update camera frustum to match `layout.width × layout.height`.
3. **Every frame (rAF):** Call `EdgeStateManager.updateEdgeStates()`. Apply uniforms to each mesh's `ShaderMaterial`. Increment `u_time`. Call `composer.render()`.
4. **Unmount:** Dispose all geometries, materials, textures, renderer.

**Canvas sizing:** Match `layout.width × layout.height` (same as the canvasRef div). Device pixel ratio applied via `renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))`.

**Optimization:** Skip `composer.render()` when all edges are in Idle state and no animations are in progress (battery saver).

### Step 7: Modify DagEdges for labels-only mode (~30min)

**Files:** `web/src/components/dag/DagEdges.tsx`

Add prop `webglActive?: boolean`. When `true`:
- Skip rendering `<path>` elements for data edges and loop edges (the `<g>` groups with `<path d={pathD}>` at lines 282-337 and 444-454).
- Keep rendering all `<marker>` definitions (arrowheads) — remove these too since WebGL handles the visual, but keep the SVG container for labels.
- Keep rendering edge label groups (`<text>`, `<rect>` at lines 338-403) — these remain interactive SVG elements.
- Keep the `<style>` block and `@keyframes` — harmless when unused but can be removed.
- SVG overlay stays `pointer-events: none` on the container, `pointer-events: auto` on label groups (existing pattern, lines 156, 355).

When `webglActive` is `false` or `undefined`, render everything as before (full backward compatibility).

### Step 8: Mount WebGL canvas in FlowDagView (~45min)

**Files:** `web/src/components/dag/FlowDagView.tsx`

Integration changes:
1. Lazy-load `WebGLEdgeLayer`:
   ```typescript
   const WebGLEdgeLayer = React.lazy(() => import("./WebGLEdgeLayer"));
   ```
2. Inside the `<div ref={canvasRef}>`, render before `<DagEdges>`:
   ```tsx
   {webglSupported && (
     <Suspense fallback={null}>
       <WebGLEdgeLayer
         layout={layout}
         loopEdges={layout.loopEdges}
         latestRuns={latestRuns}
         width={layout.width}
         height={layout.height}
         onReady={() => setWebglActive(true)}
       />
     </Suspense>
   )}
   <DagEdges
     {...existingProps}
     webglActive={webglActive}
   />
   ```
3. The WebGL `<canvas>` is positioned `absolute inset-0` within the canvasRef div, with `pointer-events: none` and `z-index: 0` (below SVG labels and HTML nodes).

### Step 9: WebGLEdgeLayer React component (~1hr)

**Files:** `web/src/components/dag/WebGLEdgeLayer.tsx`

This is the React component wrapper:
- Uses `useWebGLEdges` hook internally.
- Mounts a `<canvas>` element into the DOM via a ref.
- Calls `onReady()` callback once the first frame renders successfully.
- Handles cleanup on unmount.
- Passes `layout.width` and `layout.height` to size the canvas.
- Uses `ResizeObserver` as a safety net (the primary sizing is from layout dimensions).

### Step 10: Performance tuning + device capability tiers (~1hr)

**Files:** `web/src/lib/webgl/webgl-utils.ts`, `web/src/hooks/useWebGLEdges.ts`

Performance tiers:
- **High (default):** Full bloom, all animations, device pixel ratio up to 2.
- **Medium:** Bloom disabled (skip EffectComposer, render directly), animations on. Triggered if frame time consistently > 12ms.
- **Low / Fallback:** WebGL disabled entirely, SVG DagEdges renders fully. Triggered if WebGL context lost or initial frame > 50ms.

Add to `webgl-utils.ts`:
- `getPerformanceTier(): "high" | "medium" | "low"` — checks `navigator.hardwareConcurrency`, `renderer.capabilities.maxTextureSize`, and a quick test render.

Add frame time monitoring to the rAF loop in `useWebGLEdges.ts` — if 5 consecutive frames exceed the budget, downgrade tier.

### Step 11: Install Three.js dependency (~5min)

**Command:** `cd web && npm install three && npm install -D @types/three`

Verify in `package.json`. No other config changes needed — Vite handles Three.js tree-shaking and chunk splitting natively.

### Step 12: Integration testing + visual QA (~1.5hr)

- Run existing tests: `cd web && npm run test` — verify DagEdges label tests still pass with `webglActive={false}`.
- Write new tests for pure functions in `edge-geometry.ts`, `edge-state-manager.ts`.
- Manual visual QA against reference screenshots with a running workflow.
- Verify fallback: force `canUseWebGL()` to return false, confirm SVG renders.
- Verify lazy loading: check network tab that Three.js chunk loads only when DAG view mounts.

## Testing Strategy

### Unit Tests (Vitest)

| Test file | What it covers | Commands |
|---|---|---|
| `web/src/lib/webgl/edge-geometry.test.ts` | `createEdgeCurve` produces valid curves from 2, 3, N points; `createEdgeGeometry` returns geometry with UV range [0,1] on U axis; loop edge conversion | `cd web && npm run test -- edge-geometry` |
| `web/src/lib/webgl/edge-state-manager.test.ts` | State transitions: idle→surge→flow→completed, idle→surge→failed, surge timing (800ms), flash decay, constant speed normalization | `cd web && npm run test -- edge-state-manager` |
| `web/src/lib/webgl/webgl-utils.test.ts` | `canUseWebGL` returns false in jsdom (no WebGL), returns cached result | `cd web && npm run test -- webgl-utils` |
| `web/src/components/dag/DagEdges.test.tsx` | Existing tests pass; new test: with `webglActive={true}`, no `<path>` elements rendered but labels still present | `cd web && npm run test -- DagEdges` |

### Integration Tests

| Scenario | Verification |
|---|---|
| WebGL context creation failure | `canUseWebGL` returns false → `webglActive` stays false → DagEdges renders full SVG paths |
| Layout transition | Edge geometries update smoothly during 300ms layout transition (manual visual check) |
| Pan/zoom | WebGL canvas stays aligned with HTML nodes (manual — verify with browser dev tools overlay) |
| DAG capture (Share) | With WebGL active, capture still works (SVG labels render; WebGL canvas may need `preserveDrawingBuffer: true` or graceful fallback) |

### Performance Tests (Manual)

| Test | Target | How to measure |
|---|---|---|
| Initial load (no WebGL) | < 200ms FCP delta | Lighthouse before/after |
| Three.js chunk size | < 170KB gzipped | `npm run build && ls -la dist/assets/*.js` |
| 20-edge animation at 60fps | < 16ms per frame | Chrome DevTools Performance tab, record 5s of animation |
| Idle power consumption | < 1% CPU when all edges settled | Activity Monitor / `top` with settled DAG |

### Run all tests
```bash
cd web && npm run test        # vitest
cd web && npm run lint         # eslint
cd web && npm run build        # verify production build succeeds
```

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **Three.js bundle bloat** | Medium | High — 150KB gzipped could slow initial load | Dynamic `import()` with `React.lazy`. Three.js loads only when DAG view is visited. Verify chunk splitting with `npm run build`. |
| **WebGL context limits** | Low | High — browsers limit WebGL contexts per page | Single renderer instance shared across all edges. Dispose on unmount. Monitor `webglcontextlost` event and fall back to SVG. |
| **DOM↔WebGL coordinate drift** | Medium | Medium — misaligned edges look broken | WebGL canvas is a child of `canvasRef` and inherits CSS transform. No manual coordinate conversion. Canvas size matches `layout.width × layout.height`. |
| **Layout transition jank** | Medium | Medium — jarring if geometry rebuilds lag | Don't rebuild geometry during transition — update vertex positions of existing tubes. Use `geometry.attributes.position.needsUpdate = true`. If edge count changes, rebuild only new/removed edges. |
| **DAG capture regression** | High | Low — Share button won't capture WebGL | For v1, accept that capture shows labels but not energy effects. Document as known limitation. Follow-up: composite WebGL `toDataURL()` with html-to-image output. |
| **Bloom on light mode** | Medium | Low — additive blending looks wrong on white | Disable bloom and use reduced-opacity tubes in light mode. Fall back to SVG entirely if `theme === "light"`. |
| **TubeGeometry for 2D lines is overkill** | Low | Low — slightly more triangles than needed | TubeGeometry with `radialSegments: 4` (square cross-section) is cheap. Could optimize later with custom BufferGeometry ribbon if profiling shows GPU bottleneck, but 20 tubes × ~100 triangles each = 2000 triangles total — trivial. |
| **EffectComposer import size** | Medium | Medium — postprocessing adds to chunk | Import only `EffectComposer`, `RenderPass`, `UnrealBloomPass` from `three/addons/`. Vite tree-shakes unused postprocessing passes. Verify with bundle analyzer. |
| **Test environment (jsdom) has no WebGL** | Certain | Low | All WebGL code is behind `canUseWebGL()` guard. Unit tests cover pure geometry/state logic. WebGL integration is tested manually. The `webgl-utils.test.ts` verifies graceful degradation. |
