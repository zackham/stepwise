# Changelog

All notable changes to Stepwise are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] — 2026-03-12

**Editor, Visual Editing, Registry Browser, AI Chat** — full flow authoring experience.

### Added
- **Flow Editor (M10)** — CodeMirror 6 YAML editor with syntax highlighting, live DAG visualization side-by-side, flow file list with search/filter, toolbar with Save/Discard/Ctrl+S, dirty state tracking, unsaved changes warning
- **Visual Step Editing (M12b)** — click DAG nodes to open StepDefinitionPanel with editable fields (prompt, model, command, outputs). Add Step dialog with executor type picker. Delete step with confirm. Server-side AST-preserving YAML patches via ruamel.yaml round-trip
- **Registry Browser (M11)** — search/browse stepwise.run registry from the editor sidebar. Preview flow DAGs, view metadata (author, downloads, tags, executor types). One-click install to local project. Graceful offline handling ("Registry unavailable")
- **AI Chat (M13)** — LLM-assisted flow creation/modification via streaming chat panel. YAML code blocks with Apply buttons. Context-aware quick actions. OpenRouter integration with system prompt containing Stepwise YAML format reference
- **Flow directories** — flows can now be directories containing `FLOW.yaml` alongside co-located scripts, prompts, and docs. Single-file `.flow.yaml` still works everywhere
- **Name-based flow resolution** — CLI commands accept flow names: `stepwise run my-flow` resolves across project root, `flows/`, `.stepwise/flows/`
- **`stepwise new <name>`** — scaffolds a flow directory from a minimal template
- **`prompt_file:`** — load prompt content from file relative to flow directory at parse time
- **Script path resolution** — `run:` paths resolve relative to flow directory for directory flows
- **Registry bundles** — `stepwise share` bundles directory flows as structured JSON with size/count limits
- **`.origin.json`** — provenance tracking when flows are installed from the registry
- `flow_resolution.py`, `bundle.py`, `editor_llm.py` — new modules
- Editor API endpoints: `/api/local-flows`, `/api/flows/local/{path}`, `/api/flows/parse`, `/api/flows/patch-step`, `/api/flows/add-step`, `/api/flows/delete-step`, `/api/editor/chat`, `/api/registry/*`
- 10 new web components, 3 new hooks, 200+ new tests

### Changed
- **Flat CLI** — `stepwise share/get/search/info` are top-level commands (removed `stepwise flow` subgroup)
- Builder page replaced by Editor page
- `WorkflowDagView` → `FlowDagView`, `WorkflowBuilder` → `FlowBuilder` (renamed)
- Web routes: `/builder` removed, `/editor` and `/editor/$flowName` added

## [0.1.0] — 2026-03-12

**Core engine through Flow Sharing** — the complete orchestration platform.

### Added
- **Core Engine (M1)** — DAG-based workflow engine with tick loop, step readiness, parallel execution, loop management via supersession, expression-based exit rules (advance/loop/escalate/abandon), HandoffEnvelope structured output, SQLite persistence with WAL mode, decorators (timeout/retry/fallback/notification), sub-job delegation, FastAPI server with 27 REST endpoints + WebSocket
- **Web UI (M2)** — React frontend (Vite, TanStack Router + Query, Tailwind 4, shadcn/ui), DAG visualization with dagre.js, step detail panels with real-time status, YAML workflow loader, `stepwise run/serve/validate` CLI commands, `.stepwise/` project directory
- **LLM Executor (M3)** — OpenRouter integration with model registry and tier support, `stepwise config` for API keys
- **Agent Executor (M4)** — ACP protocol with async polling, StepLimits (cost/duration/iterations), step_events table, WebSocket agent output streaming, AgentStreamView component
- **For-Each (M5)** — fan-out over lists with parallel sub-jobs, `on_error: continue|fail_fast`
- **HTML Reports (M6)** — `stepwise run --report` generates self-contained HTML execution traces with SVG DAG, step timeline, cost summary
- **Context Chains (M7a)** — session continuity across agent steps, `chains:` YAML block, overflow strategies (drop_oldest/drop_middle), transcript capture
- **Flows as Tools (M7b)** — `stepwise schema/run --wait/run --async/output/fulfill/agent-help` for agent integration, structured exit codes, stdout purity
- **Route Steps (M8)** — conditional sub-flow dispatch with `routes:` block, first-match semantics, file ref cycle detection, output contract validation
- **Flow Sharing (M9)** — `stepwise share/get/search/info`, registry client with disk cache and token management, parse-time `@author:name` resolution
- `install.sh` — universal `curl | sh` installer
- `stepwise update` — upgrade to latest version
- 640+ Python tests, 77+ frontend tests

## [0.0.1] — 2026-03-08

### Added
- Initial project setup with uv
- MIT license
