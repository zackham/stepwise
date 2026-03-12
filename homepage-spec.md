# Homepage Spec

## What to build
A homepage for Stepwise, a workflow orchestration engine.
Users define workflows as YAML DAGs, Stepwise executes them with
LLM, script, human, and agent step types.

## Key sections
- Hero with animated DAG visualization showing a workflow executing
- Install/quickstart (pip install + first run with --demo flag)
- Feature showcase: three CLI modes (headless → watch → serve), YAML workflows, API-first design
- Workflow library/gallery: browsable, searchable grid of community workflows
- Read-only workflow inspector (clone of Stepwise's JobDagView, stripped of controls)
- "Built with Stepwise" meta-story section

## Tone
Developer-focused. Direct, technical, no marketing fluff. Show code, not promises.

## Tagline
"Step into your flow."

## Aesthetic
Dark mode. Deep navy background with cyan/purple gradient accents. Premium dev tool feel (Linear/Cursor neighborhood).

## Color palette
Generated via kigen.design (Tailwind CSS algorithm) from logo colors.

| Scale | 50 | 100 | 200 | 300 | 400 | 500 | 600 | 700 | 800 | 900 | 950 |
|-------|----|----|-----|-----|-----|-----|-----|-----|-----|-----|-----|
| Cyan (primary) | #d8f6ff | #96eafe | **#22d3ee** | #1ec2da | #19aac0 | #1490a3 | #0e7484 | #085865 | #043c45 | #012127 | #011418 |
| Purple (secondary) | #f4eefe | #e6d8fd | #d3b6fc | #c297fa | #b477f9 | **#a855f7** | #9e28f5 | #7c19c4 | #570e8b | #310551 | #1d0234 |
| Indigo (mid) | #eff0fe | #dadafc | #b9baf9 | #9b9df7 | #7f81f4 | **#6366f1** | #4b4fee | #242de5 | #171da5 | #090d64 | #040642 |
| Teal (origin) | #baffe6 | #31ffcc | **#06e8b8** | #05d5a8 | #04bb93 | #039e7c | #027f64 | #01604b | #004132 | #00241a | #00150e |
| Navy (surfaces) | #f0f0f4 | #d8dae4 | #b4b9cd | #959cb8 | #7882a4 | #616a89 | #4c536d | #393f53 | #272b3a | #171a24 | **#0b0d14** |

**Bold** = source color input to kigen.

Gradient: `linear-gradient(135deg, teal-200 0%, cyan-200 25%, indigo-500 65%, purple-500 100%)`
Background: navy-950. Surfaces: #111520, #1a1f2e. Text: navy-50/400/600.

## Brand assets
Canonical location: `brand/` in this repo.
- `stepwise-mark.svg` — gradient staircase SVG mark (source of truth, generates all PNGs)
- `stepwise-icon.png` — detailed raster icon for hero/feature sections
- `favicon-{16,32,48,64,128,192,512}.png` — PNG favicons at all standard sizes
- `favicon.ico` — ICO bundle (16+32+48+64)
- `apple-touch-icon.png` — 180px for iOS
- `og-image.png` — 1200x630 social sharing image
- `stepwise-icon-{32,64,128,192,512}.png` — raster icon at multiple sizes

The generate-homepage flow copies `brand/` into `site/assets/` at write time — same pattern as docs.

## Hero DAG animation

The hero shows `generate-homepage.flow.yaml` — the flow that built this page (meta-story).

**SVG dimensions and node sizing:**
- SVG viewBox: `0 0 960 460` — wider than tall, fills container (max-width: 1100px)
- Node size: 138×36px, border-radius 8px
- Node labels: 12px monospace, centered
- Layer gap: 58px vertical, column gap: 155px horizontal
- Top padding: 18px from viewBox top

**Node positions (layer, col):**
| Node | Layer | Col | Type |
|------|-------|-----|------|
| ingest-repo | 0 | 0 | script (green) |
| plan-design | 1 | 0 | llm (purple) |
| gen-shared-css | 2 | -1.2 | llm (purple) |
| gen-copy | 2 | 0 | llm (purple) |
| gen-backend | 2 | 1.2 | llm (purple) |
| generate-pages | 3.2 | -1.05 | for_each (blue) |
| gen-site-pages | 3.2 | 1.05 | for_each (blue) |
| assemble | 5.4 | 0 | llm (purple) |
| write-output | 6.4 | 0 | script (green) |

Position formula: `x = CX + col * colGap - nodeW/2`, `y = 18 + layer * layerGap`

**Fan-out children (3-column grids below for_each parents):**
- generate-pages → hero, features, quickstart, workflows, meta-story (3+2 grid)
- gen-site-pages → gallery, flow-detail, docs-index, doc-template (3+1 grid)
- Fan child size: 76×22px, gap: 6px horizontal, 4px vertical
- Fan-outs must NOT overlap — col ±1.05 with colGap 155 gives ~85px clearance between grids
- for_each nodes have 2 stacked shadow rects (no text badge — shadows convey multiplicity)

**Layout (idle):**
- 9 main nodes arranged per table above
- Node colors by type: script=green (#22c55e), llm=purple (#a855f7), for_each=blue (#3b82f6)
- Execute Flow button directly below SVG, `white-space: nowrap`
- Edges: dashed cyan animated particles flowing along bezier curves between connected nodes

**Layout (running):**
- Flexbox: log panel (flex:1) slides in from left, DAG (flex:3) stays large on right — DAG gets 75% of space
- Log panel: dark glass bg, hidden scrollbar (`scrollbar-width: none`), entries fill top-down
- Log panel height locked to DAG container height via `ResizeObserver` (`max-height` set in JS)
- Once log content overflows, auto-scrolls to bottom on each new entry (`scrollTop = scrollHeight`)
- DAG container reduces padding to fill available space
- Smooth 0.5s cubic-bezier transition on flex values
- `align-items: flex-start` (not `stretch`) — log panel height is JS-controlled, not flex-driven

**Execution sequence:**
1. Sequential: ingest (0.1s) → plan (2.1m)
2. Parallel burst: gen-css, gen-copy, gen-backend fire simultaneously (staggered 100ms)
3. Dual fan-out: generate-pages and gen-site-pages fire nearly simultaneously
   - generate-pages fans into: hero, features, quickstart, workflows, meta-story (3+2 grid)
   - gen-site-pages fans into: gallery, flow-detail, docs-index, doc-template (3+1 grid)
   - Fan children appear with spring animation (staggered 250ms each), get green checks on complete
   - Log interleaves: gen-pages[0] hero.html / gen-site[0] gallery.html / gen-pages[1] features.html / ...
4. Both fan-outs complete → assemble → write-output
5. "✓ Flow completed — 9 steps · 2 fan-outs · $4.20"
6. Button changes to "↻ Replay"

**Node animations:**
- Executing: cyan glow pulse (0.6s)
- Running: rotating dashed spinner circle
- Completed: green border stroke
- Fan children: scale(0.7→1) spring + blue glow pulse

## Special requirements
- Inspector is the real Stepwise JobDagView component in read-only mode
- CTA on inspected workflows: "Run this workflow" copies CLI command to clipboard
- Gallery fetches from /api/flows (no featured filter, limit 6)
- Download counts are placeholder for V1 (backend in V2)
