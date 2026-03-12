---
schema: website-spec/1.0
project: stepwise
repo: https://github.com/zackham/stepwise
url: https://stepwise.run
---

# Stepwise

Portable workflow orchestration for agents and humans. Define workflows as YAML DAGs — Stepwise executes them with LLM, script, human, and agent step types.

## Identity

- **Name:** Stepwise
- **Tagline:** "Step into your flow."
- **Description:** A workflow engine that coordinates multi-step jobs where each step can be a shell script, an LLM call, an autonomous AI agent, or a human decision. Define your workflow as a YAML file, run it from the CLI, and optionally watch it execute in a real-time web UI.
- **Audience:** Developers building AI-powered automation, agent orchestration, and multi-step pipelines. Technical users comfortable with YAML, CLI tools, and Python.
- **Tone:** Developer-focused. Direct, technical, no marketing fluff. Show code, not promises. Confident but not arrogant — let the product speak.
- **Anti-copy rules:**
  - No "revolutionary", "game-changing", "next-generation"
  - No "unleash the power of" or "supercharge your workflow"
  - No exclamation points in body copy
  - Prefer imperative verbs: "Define. Run. Watch." over "You can define..."
  - Code examples over prose whenever possible
  - Feature descriptions should be one sentence, then show usage

## Design System

### Mode
Dark mode only. Deep navy background with cyan/purple gradient accents. Premium dev tool aesthetic (Linear/Cursor neighborhood).

### Colors

Generated via kigen.design (Tailwind CSS algorithm) from logo colors:

```yaml
scales:
  cyan:    # primary accent
    50: "#d8f6ff"
    100: "#96eafe"
    200: "#22d3ee"   # source
    300: "#1ec2da"
    400: "#19aac0"
    500: "#1490a3"
    600: "#0e7484"
    700: "#085865"
    800: "#043c45"
    900: "#012127"
    950: "#011418"
  purple:  # secondary accent
    50: "#f4eefe"
    100: "#e6d8fd"
    200: "#d3b6fc"
    300: "#c297fa"
    400: "#b477f9"
    500: "#a855f7"   # source
    600: "#9e28f5"
    700: "#7c19c4"
    800: "#570e8b"
    900: "#310551"
    950: "#1d0234"
  indigo:  # gradient midpoint
    50: "#eff0fe"
    100: "#dadafc"
    200: "#b9baf9"
    300: "#9b9df7"
    400: "#7f81f4"
    500: "#6366f1"   # source
    600: "#4b4fee"
    700: "#242de5"
    800: "#171da5"
    900: "#090d64"
    950: "#040642"
  teal:    # gradient origin
    50: "#baffe6"
    100: "#31ffcc"
    200: "#06e8b8"   # source
    300: "#05d5a8"
    400: "#04bb93"
    500: "#039e7c"
    600: "#027f64"
    700: "#01604b"
    800: "#004132"
    900: "#00241a"
    950: "#00150e"
  navy:    # surfaces
    50: "#f0f0f4"
    100: "#d8dae4"
    200: "#b4b9cd"
    300: "#959cb8"
    400: "#7882a4"
    500: "#616a89"
    600: "#4c536d"
    700: "#393f53"
    800: "#272b3a"
    900: "#171a24"
    950: "#0b0d14"   # source

semantic:
  background: navy-950        # #0b0d14
  surface: "#111520"
  surface-elevated: "#1a1f2e"
  text-primary: navy-50
  text-secondary: navy-400
  text-muted: navy-600
  accent: cyan-200
  accent-secondary: purple-500
  accent-bright: cyan-100
  gradient: "linear-gradient(135deg, teal-200 0%, cyan-200 25%, indigo-500 65%, purple-500 100%)"
  code-bg: "#080a10"
  glass-bg: "rgba(11,13,20,0.85)"
```

### Typography
- **Headings:** Inter (Google Fonts), weights 400–800
- **Code:** JetBrains Mono (Google Fonts), weights 400–500
- **Body:** Inter 400, line-height 1.6
- **Scale:** 14px base, headings 2.5rem/2rem/1.5rem/1.25rem

### Effects
- **Glassmorphism:** `backdrop-filter: blur(12px)` on `.card-glass`, background `glass-bg`
- **Glow:** `drop-shadow(0 0 20px cyan-200)` on brand icon, `box-shadow: 0 0 15px` on active nodes
- **Gradient text:** `background: var(--gradient); -webkit-background-clip: text; -webkit-text-fill-color: transparent`

### Animation
- **fadeInUp:** translateY(20px) → 0, opacity 0 → 1, 0.6s ease-out
- **pulse:** opacity 1 → 0.7 → 1, 2s infinite
- **float:** translateY(0) → -8px → 0, 3s ease-in-out infinite
- **flow:** for animated particles along DAG edges

### Layout
- **Container:** max-width 1200px, centered, padding 0 2rem
- **Section:** padding 5rem 0
- **Grid breakpoints:** 768px (2-col → 1-col), 1024px (3-col → 2-col)

### References
- Linear (linear.app) — dark UI, clean typography, developer focus
- Cursor (cursor.sh) — dark theme, code-centric, subtle gradients

## Brand Assets

Canonical location: `brand/` in the repo. Copied to `site/assets/` at build time.

```yaml
assets:
  mark: "stepwise-mark.svg"          # gradient staircase SVG mark (teal→cyan→purple) — nav + footer
  icon: "stepwise-icon.png"          # detailed wizard hat DAG icon — hero section (glow/float animation)
  favicons:
    svg: "stepwise-mark.svg"
    ico: "favicon.ico"               # ICO bundle (16+32+48+64)
    png_16: "favicon-16.png"
    png_32: "favicon-32.png"
    png_48: "favicon-48.png"
    png_64: "favicon-64.png"
    png_128: "favicon-128.png"
    png_192: "favicon-192.png"
    png_512: "favicon-512.png"
  apple_touch: "apple-touch-icon.png"  # 180px
  og_image: "og-image.png"            # 1200x630

favicon_meta: |
  <link rel="icon" href="/assets/stepwise-mark.svg" type="image/svg+xml">
  <link rel="icon" href="/assets/favicon.ico" sizes="48x48">
  <link rel="icon" href="/assets/favicon-32.png" type="image/png" sizes="32x32">
  <link rel="icon" href="/assets/favicon-192.png" type="image/png" sizes="192x192">
  <link rel="apple-touch-icon" href="/assets/apple-touch-icon.png">
  <meta property="og:image" content="https://stepwise.run/assets/og-image.png">
```

## DAG Component

A shared JavaScript module (`stepwise-dag.js`) used by both the homepage hero and flow-detail page. Same rendering core, different modes.

Include via `<script src="/static/stepwise-dag.js"></script>` after dagre.js CDN.

### Initialization

```javascript
StepwiseDAG.create({
  container: '#dag-container',  // mount target
  data: flowData.graph,         // {nodes[], edges[]} from API or injected data
  mode: 'hero' | 'detail',     // selects behavior preset
  // Hero mode options
  onLog: (entry) => {},         // callback for log panel entries
  // Detail mode options
  onNodeClick: (node) => {},    // callback for step inspector
});
```

**Data source:** Both modes consume the same graph data from `/api/flows/{slug}/graph` or `window.__FLOW_DATA__.graph`. Shape:
- **Node:** `{id, label, executor_type, has_for_each, outputs[], details: {model?, prompt?, system?, temperature?, max_tokens?, run?, inputs?, for_each?, as?, on_error?}, children?[]}`
- **Edge:** `{source, target, label?, is_loop?}`

### Layout Engine

- dagre.js with `rankdir: 'TB'`
- dagre params: `nodesep: 50, ranksep: 60, marginx: 30, marginy: 30`
- SVG viewBox auto-calculated from dagre bounds + 40px padding, `preserveAspectRatio="xMidYMid meet"`
- For_each nodes get extra height to contain children (2-column grid inside parent rect)

### Node Rendering

Merged from homepage (bold) + flow-detail (functional):

| Property | Value |
|----------|-------|
| Dimensions | 160×42px, `rx: 8` |
| Fill | `rgba(17,21,32,0.95)` — slightly more opaque than surface for contrast |
| Stroke | Type color at **full opacity** (1.0) — not dimmed |
| Stroke width | 1.5px |
| Label | 13px monospace, `fill: var(--text-primary)`, centered |
| Type dot | 4px radius filled circle, left side, type color |
| Hover | `filter: drop-shadow(0 0 8px <type-color>)`, `cursor: pointer` |

**Type colors:**

| Type | Color | CSS var |
|------|-------|---------|
| script | #22c55e | `--color-script` |
| llm | #a855f7 | `--color-llm` |
| for_each | #06b6d4 | `--color-for-each` |
| agent | #3b82f6 | `--color-agent` |
| human | #f59e0b | `--color-human` |

**For_each nodes:**
- 2 stacked shadow rects behind main rect (offsets +3px, -3px) at `stroke-opacity: 0.25`
- Children rendered as sub-nodes inside an expanded parent rect
- Child dimensions: 68×24px, `rx: 4`, `fill: var(--surface-elevated)`, stroke = child's type color at full opacity
- Child layout: 2-column grid, 8px gap
- Children have their own type dot (2.5px radius)
- Children are clickable in detail mode

### Edge Rendering

- Path: smooth bezier curves from dagre points
- Stroke: `rgba(34,211,238,0.2)` (subtle cyan tint)
- Stroke width: 1.5px
- Arrowhead markers (white at 0.2 opacity)
- **Particle overlay:** always-on animated dashed layer — `stroke: var(--accent-bright)` (#96eafe), `stroke-width: 2`, `stroke-dasharray: 6 14`, flowing animation 1.5s infinite. Gives the DAG a living, flowing feel even at rest.
- Active state (during execution): full cyan stroke, active arrowhead marker, `filter: drop-shadow(0 0 4px var(--accent-glow))`

### Execution Simulation

Called via `dag.runDemo(schedule?)`. Drives nodes through state transitions with visual feedback.

**Node states:**
- `idle` → default rendering
- `executing` → cyan glow pulse keyframe (0.6s)
- `running` → rotating dashed spinner circle (`stroke-dasharray: 8 24`, spin animation)
- `completed` → `stroke: #22c55e`, `stroke-width: 2`, timing label appears (e.g. "1.3s")

**Fan-out behavior (for_each nodes):**
When a for_each node enters `running`:
1. Children start at `opacity: 0`, `transform: scale(0.7)`
2. Each child animates in with staggered 250ms delay: scale(0.7→1) + glow pulse
3. Children enter `running` state (pulse animation)
4. Children complete individually with staggered timing: stroke turns green, subtle green fill tint, optional checkmark
5. Parent completes when all children are done

**Execution modes:**
- **Dependency-driven** (default): `runDemo()` walks the graph topology. Node runs when all upstream deps are completed. Duration per node: `800 + random(1000)` ms. For_each children run in staggered parallel.
- **Scripted** (optional): `runDemo(schedule)` accepts a timeline array `[{t: ms, ids: [...], dur: string, hold: ms}]` for hand-tuned dramatic pacing. Used by the homepage hero for precise storytelling.

**Log entries:**
Each state transition emits a log entry via the `onLog` callback:
```javascript
{ timestamp: "00:03", node: "plan-pages", event: "started", message: "Planning page structure..." }
{ timestamp: "00:05", node: "plan-pages", event: "completed", duration: "2.1s" }
{ timestamp: "00:06", node: "generate-sections[0]", event: "started", message: "hero.html" }
```
The host page decides how to render logs (slide-in panel, floating overlay, etc.).

### Detail Mode Features

Active when `mode: 'detail'`:
- **Click-to-inspect:** Click any node (or child) → calls `onNodeClick(node)` with the full node object including `details`
- **Selected highlight:** selected node gets persistent glow
- **Panning:** mouse drag to pan SVG (`cursor: grab` / `grabbing`)
- No auto-execution — user clicks "Run Demo" button to trigger `dag.runDemo()`

### Hero Mode Features

Active when `mode: 'hero'`:
- **Auto-execution:** execution simulation starts immediately (or on button click)
- **No click-to-inspect** — nodes are non-interactive (the flow-detail page handles that)
- **No panning** — fixed viewport
- Container styling: `background: rgba(24,24,27,0.6)`, `border: 1px solid var(--border)`, `border-radius: 0.75rem`, `padding: 1.25rem 1rem`

## Pages

### Homepage

The homepage is assembled from individually generated sections. Each section is a self-contained HTML fragment with inline `<style>`.

#### Hero

Uses the shared DAG Component in hero mode, pointed at the `generate-website` flow.

- Fetches flow graph from `/api/flows/generate-website/graph` (or uses inline data)
- DAG Component configured with `mode: 'hero'`
- "Execute Flow" button below SVG triggers `dag.runDemo()`, changes to "↻ Replay" on completion
- Final message: "✓ Flow completed — N steps · 2 fan-outs · $4.20"

**Log panel (host-provided, not in the component):**
- Flexbox sibling to DAG: log panel (flex:1) slides in from left, DAG (flex:3) stays large on right
- Glass background, hidden scrollbar (`scrollbar-width: none`)
- Height locked to DAG container via `ResizeObserver`
- Auto-scrolls to bottom on each new entry
- Smooth 0.5s cubic-bezier transition on flex values
- Log entries rendered from `onLog` callback

**Brand icon:** `<img src="/assets/stepwise-icon.png" width="160" height="160">` above h1 with CSS `drop-shadow(0 0 25px var(--cyan-200))` glow and float animation.

#### Quickstart

Install command + first run example. Show the 30-second getting started:

```bash
pip install stepwise
stepwise init
stepwise run hello.flow.yaml --watch
```

Copy-to-clipboard button on install command. Terminal-style dark code block.

#### Features

Three CLI modes as primary showcase: headless (`stepwise run`), watch (`--watch` with real-time web UI), serve (`stepwise serve` for API). Also highlight: YAML workflows, 4 executor types (script/LLM/agent/human), for_each fan-outs, context chains, flows-as-tools.

Feature cards with icons, not walls of text.

#### Gallery Preview

"Community Workflows" section. Fetches from `/api/flows` (GET, limit 6). Shows flow cards linking to `/flows/{slug}`. On fetch failure, show empty state — NO mock/fallback data. "Browse all →" link to `/flows`.

#### Meta Story

"Built with Stepwise" section explaining the meta-story: this entire site was generated by a Stepwise flow. The flow that built this page is browsable at `/flows/generate-website`. Link to the flow detail page.

### Flow Gallery

- **Route:** `/flows`
- **Type:** Client-rendered page
- **Purpose:** Browse and search published Stepwise workflows
- **Data source:** `GET /api/flows` → `{flows: [...], total: N}`
- **API response fields per flow:** name, slug, description, tags (array), executor_types (array), downloads (integer), url, raw_url
- **Features:**
  - Search input (client-side filter or `?q=` param)
  - Flow cards linking to `/flows/{slug}`
  - Executor type badges: script (green #22c55e), llm (purple #a855f7), for_each (blue #3b82f6), agent (amber #f59e0b), human (amber #f59e0b)
  - On fetch failure: empty state message, NO mock data, NO fake flows

### Flow Detail

- **Route:** `/flows/{slug}`
- **Type:** Server-rendered with injected data
- **Purpose:** Interactive DAG visualization and step inspector for a single flow
- **Data injection pattern:**
  ```javascript
  window.__FLOW_DATA__ = /*INJECT_FLOW_DATA*/ {};
  ```
  The server replaces everything between `/*INJECT_FLOW_DATA*/` and `</script>` with actual JSON. This marker must be exactly `/*INJECT_FLOW_DATA*/`.
- **Data shape:** `{name, slug, description, tags[], executor_types[], downloads, yaml, graph: {nodes[], edges[]}}`
- **Uses the shared DAG Component** in detail mode:
  - `mode: 'detail'`, data from `window.__FLOW_DATA__.graph`
  - `onNodeClick` opens the step details panel
  - Container: 650px tall, dot grid background (`radial-gradient(circle, rgba(255,255,255,0.03) 1px, transparent 1px)`), pannable
  - "Run Demo" button triggers `dag.runDemo()` with dependency-driven execution
- **Page structure:**
  1. **Header** — breadcrumb, title, description, meta badges, tags
  2. **DAG section** — shared DAG Component with Run Demo button
  3. **Bottom grid** — step details panel (1fr) + full YAML viewer (2fr)
- **Step details panel (on node click):**
  - Step ID, executor badge
  - Inputs as cyan pills, outputs as purple pills
  - Model, system prompt, prompt text in scrollable code blocks
  - Temperature, max_tokens as inline properties
  - For_each config, children for sub-flows
  - Empty state: "Click on any node in the graph above to view its configuration."
- **Mini log panel** — floating overlay (bottom-left, 350×200px glass) during Run Demo, timestamped entries
- **CTA:** `stepwise flow get {slug}` command with copy button (top right)
- **YAML viewer:** raw YAML in code block with copy button

### Docs Index

- **Route:** `/docs`
- **Type:** Client-rendered page
- **Purpose:** Documentation landing page with search
- **Data source:** `GET /api/docs` → `[{slug, title, word_count, read_time_min}]`
- **Features:**
  - Search/filter input
  - Doc cards linking to `/docs/{slug}`
  - Reading time per doc
  - On fetch failure: show static doc list from build-time data

### Doc Template

- **Route:** `/docs/{slug}`
- **Type:** Client-rendered markdown page
- **Purpose:** Render individual documentation pages from markdown
- **Technical requirements:**
  - Fetches raw markdown from `/api/docs/{slug}/raw`
  - Uses marked.js for parsing — MUST pin to v9.x: `https://cdn.jsdelivr.net/npm/marked@9.1.6/marked.min.js` (v12+ has breaking renderer.heading API)
  - Uses highlight.js CDN for syntax highlighting
  - Extracts slug from URL: `window.location.pathname.split('/docs/')[1]`
  - Loading state while fetching

## Backend

- **Framework:** FastAPI (Python), port 8340, host 0.0.0.0
- **Database:** SQLite with WAL mode at `./registry.db`
- **Docs source:** Reads `.md` files from the stepwise repo's `docs/` directory

### Database Schema

```sql
CREATE TABLE flows (
    name TEXT PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    author TEXT NOT NULL DEFAULT 'anonymous',
    version TEXT NOT NULL DEFAULT '1.0',
    description TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',           -- JSON array
    yaml_content TEXT NOT NULL,
    steps INTEGER NOT NULL DEFAULT 0,
    loops INTEGER NOT NULL DEFAULT 0,
    has_for_each INTEGER NOT NULL DEFAULT 0,
    executor_types TEXT NOT NULL DEFAULT '[]', -- JSON array
    downloads INTEGER NOT NULL DEFAULT 0,
    featured INTEGER NOT NULL DEFAULT 0,
    unlisted INTEGER NOT NULL DEFAULT 0,
    update_token TEXT,
    source TEXT DEFAULT 'seed',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- FTS5 virtual table for full-text search
CREATE VIRTUAL TABLE flows_fts USING fts5(
    name, description, tags, author,
    content='flows', content_rowid='rowid'
);
```

### API Routes

```yaml
registry:
  - GET /api/flows:
      params: [q, tag, sort, limit, offset, featured]
      returns: "{flows: [...], total: N}"
  - GET /api/flows/{slug}:
      returns: "flow metadata + yaml content + graph"
  - GET /api/flows/{slug}/raw:
      returns: "raw YAML download, increments download counter"
  - POST /api/flows:
      body: "{yaml, author?, source?}"
      rejects: "_seed_meta in YAML"
  - PUT /api/flows/{slug}:
      auth: "Bearer token"
  - DELETE /api/flows/{slug}:
      auth: "Bearer token"

docs:
  - GET /api/docs:
      returns: "[{slug, title, word_count, read_time_min}]"
  - GET /api/docs/{slug}/raw:
      returns: "raw markdown content"
```

### HTML Page Routes

```yaml
pages:
  - GET /:           "serve site/index.html"
  - GET /docs:       "serve site/docs/index.html"
  - GET /docs/{slug}: "serve site/docs/doc-template.html (if markdown source exists)"
  - GET /flows:      "serve site/gallery.html"
  - GET /flows/{slug}: "serve site/flow-detail.html with flow data injected via /*INJECT_FLOW_DATA*/ marker"
```

### Data Injection

For `/flows/{slug}`: find `/*INJECT_FLOW_DATA*/` marker in HTML, replace from marker to next `</script>` tag with JSON data. Escape `</` as `<\/` in JSON to prevent premature script termination. Do NOT use simple `.replace()` on a token that appears elsewhere.

### Static Mounts

```yaml
mounts:
  - /assets → site/assets/   # brand assets (mount BEFORE /static)
  - /static → site/          # generated HTML/CSS
```

### YAML Analysis

Count steps, detect executor types (script if has `run`, else `executor` field, for_each if has `for_each`), count loops from exit rules.

### Graph Builder

Parse YAML to extract DAG nodes (with executor_type, outputs, all detail fields) and edges (from input references like `step-name.output`). For for_each steps, extract children from sub-flow definition.

### Startup

`init_db()` then `seed_if_empty()`. Seeder reads `*.flow.yaml` from `seed/` directory, rejects files with `_seed_meta`. Seeds with downloads=0, featured=0.

## Shared Infra

### Navigation

- Fixed top nav with glass background
- Brand: `<img src="/assets/stepwise-mark.svg" alt="" width="24" height="24">` next to "Stepwise" text
- Links: Home, Docs, Flows, GitHub
- Mobile: hamburger toggle at 768px breakpoint

### Footer

- Links: Docs, Flows, GitHub, PyPI
- Tagline with mark: `<img src="/assets/stepwise-mark.svg" alt="" width="16" height="16">` next to "Step into your flow." at 50% opacity

### CSS Conventions

- Reference `/static/shared.css` for base styles on all pages
- Use CSS custom properties from `:root` block
- Google Fonts: Inter (400–800) and JetBrains Mono (400–500)
- Executor badge classes: `.badge-script` (green), `.badge-llm` (purple), `.badge-agent` (blue), `.badge-human` (amber), `.badge-for_each` (cyan)
- Glass cards: `.card-glass` with `backdrop-filter: blur(12px)`
- Grid utilities: `.grid-2`, `.grid-3`, `.grid-4` with responsive breakpoints
- Scroll reveal animations on homepage sections
- Body `padding-top` for fixed nav

### Anti-Slop Checklist

- [ ] No lorem ipsum or placeholder text
- [ ] No fake data (download counts, flow names, descriptions)
- [ ] On API failure: empty state, never fallback mock data
- [ ] No CDN imports unless explicitly specified in this spec
- [ ] All HTML pages are self-contained (inline styles + shared.css reference)
- [ ] Responsive at 768px and 1024px breakpoints
- [ ] All links use correct routes from the Backend section
- [ ] Favicon meta tags present in every full page's `<head>`

## Special Requirements

- Gallery fetches from `/api/flows` with no featured filter, limit 6 for homepage preview
- Download counts are placeholder for V1 (backend in V2)
- The `generate-website` flow itself is the first seed flow in the registry — the meta-story
