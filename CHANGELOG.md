# Changelog

All notable changes to Stepwise are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/). Versioning: [Semantic Versioning](https://semver.org/).

## [0.44.0] — 2026-04-20

### Added
- **Settings page overhaul** — council-reviewed (Opus 4.6, GPT-5.4, Gemini 3.1 Pro, Grok 4.20) redesign of settings IA. 6 sections reorganized, all 16 `StepwiseConfig` fields now have UI or read-only exposure:
  - **Limits** (new section) — global job cap, per-executor-type concurrency, per-agent-name concurrency, agent subprocess TTL. Reusable `NumericLimitRow` with onBlur-commit
  - **Agents** — `agent_permissions` approval policy dropdown (approve_all / prompt / deny) with not-yet-enforced warning
  - **Integrations** (renamed from API Keys) — API keys + webhook editor (`notify_url` + `notify_context` JSON textarea)
  - **Models & Labels** (merged) — Labels anchored top (high-frequency), registry below. One sidebar item removed
  - **Containment** (slimmed) — sandbox-only after concurrency moved to Limits
  - **Sidebar status footer** — version, project basename, active jobs count, reload-config button. New `useHealth()` hook (5s poll)
- **5 new config API endpoints** — `PUT /api/config/max-concurrent-jobs`, `agent-process-ttl`, `agent-permissions`, `notify-webhook`, `concurrency`. Shared `_update_local_config_field` helper
- **Prompt rendered inline for completed runs** — ACP NDJSON only carries responses; outgoing `session/prompt` is now injected into the NDJSON output file at send time so `_parse_ndjson_events` emits a `{t:"prompt"}` event. The existing `FadedText` / `PromptSegmentRow` violet panel renders for both live and completed runs across all session views
- **Runner heartbeat loop** — background runners send 10-second heartbeats so the server can distinguish alive-but-busy runners from dead ones. DB-poll fallback exits cleanly if another engine mutates job status externally

### Fixed
- **Concurrent prompt isolation** — when two jobs shared an ACP process (identical agent config), the second prompt's `session/update` handler overwrote the first's because `JsonRpcTransport._notification_handlers` was a dict keyed by method name. All streaming chunks routed to the last-registered handler, silently corrupting output files. Transport now supports multiple handlers per method (list). Each handler filters by `sessionId` and unregisters on completion
- **Multi-runner stuck-step watchdog** — engine stuck-step detector now skips jobs owned by a different runner PID. Previously, concurrent `runner_bg` processes sharing SQLite would each see the other's RUNNING steps as "stuck" (not in their `_tasks` registry) and kill them after 60s
- **Stuck-job detection skips live PIDs** — `store.stuck_jobs()` was including jobs whose runner PID was still alive; now only returns jobs with dead or missing PIDs
- **WebSocket broadcast safety** — `_broadcast()` snapshots the `_ws_clients` set before iterating to avoid mutation-during-iteration when clients connect/disconnect during an await
- **Retry button actually retries** — sidebar and right-click "Retry" were wired to `resume_job` which only flips status to RUNNING; failed step runs stayed FAILED and the job immediately re-settled. New `retry_failed_steps` endpoint creates fresh `StepRun` instances for each failed step and recursively walks delegated runs with broken sub-jobs
- **Retry idempotency for delegated fan-out** — the retry recursion only fired when the parent's delegated step run was itself FAILED. Once a previous Retry had reset it to DELEGATED, subsequent clicks became no-ops. Now walks sub-jobs regardless of parent run status
- **Follow-live FAB positioning** — "Follow live" button now actually follows and is correctly centered
- **DAG pulse animation** — data flow pulse stops on terminal (completed/failed) jobs instead of animating indefinitely
- **Tick handler efficiency** — patches changed jobs in-place instead of refetching the full list

## [0.43.5] — 2026-04-15

### Fixed
- **ACP spawn no longer leaks orphan subprocess on session/new failure** — when an upstream "Internal error" (Anthropic 5xx) failed `session/new` immediately after spawning a fresh `claude-agent-acp`, the subprocess was left running and the engine never reclaimed it. `acp_backend.spawn()` now wraps session creation; on `AcpError`, if the process was newly spawned for this step, `lifecycle.discard()` tears it down before the exception re-raises. The step still fails (correct behavior — upstream is down, retry later), but no orphan subprocess remains
- **`ResourceLifecycleManager` is now thread-safe** — `acquire()`, `discard()`, `release_all()`, `release_if_unused()`, and `find()` all hold an `RLock`. Previously two executor threads racing on the same config could each see an empty `active` list, both call `factory()`, and orphan one of the spawned resources. `acquire()` now also returns `(managed, was_newly_created)` so callers can clean up on post-acquire failure
- **Server default port is 8341** (was 8340) — aligns with vita's `./run` and avoids a port-mismatch where status checks reported "Not running" while the server was alive on a different port. CLI `stepwise server start`, `stepwise.server_bg --port`, and the `STEPWISE_PORT` env-var fallback all default to 8341
- **Script-step `STEPWISE_PROJECT_DIR` now points at the flow's source directory** — previously it resolved to the isolated per-job workspace (a copy with no project files), so scripts that read `secrets.toml`, referenced sibling files with relative paths, or published artifacts to `data/` all silently broke under `stepwise run`. `ScriptExecutor.start` now prefers `self.flow_dir` when set and falls back to `workspace` only for anonymous flows. `STEPWISE_FLOW_DIR` is still exported separately so scripts that need the workspace copy can still get it via `JOB_ENGINE_WORKSPACE`

### Changed
- **Removed dead `_DIRECT_PROVIDERS` / `_resolve_direct_provider` path from `openrouter.py`** — superseded by the `model:provider/tag` provider-suffix routing added in `62d06c9`. The file-backed direct-provider scheme (`~/.config/vita/moonshot.json`) was unused; removing it drops 30+ lines of conditional wiring and a `json.loads` + filesystem branch from the hot `chat_completion` path

## [0.43.0] — 2026-04-14

### Added
- **Containment verification staircase** — four end-to-end flows (`containment-smoke`, `containment-toolbox`, `containment-boundary`, `containment-multistep`) that each cover a tier of the containment claim: ACP handshake under the boundary, fs/bash tools through virtiofs, hostile probes (with host-side ground-truth checks for `/etc/passwd` parity, `~/.ssh/id_rsa` reachability, host-`/tmp` escape markers), and multi-step session continuity with VM reuse. All four green for all three ACP adapters (`aloop`, `claude`, `codex`) in <3 minutes wall time
- **Settings UI: Containment panel** — new section under Settings shows the project-wide containment default with a dropdown, plus a per-agent table where each agent's effective containment + source (default / project / agent) is visible and editable. Backed by two new endpoints: `PUT /api/config/containment` (writes `agent_containment` to `.stepwise/config.local.yaml`) and `PUT /api/agents/{name}/containment` (writes a per-agent override; explicitly accepts `null` to clear). Default is "no containment" — the panel makes that visible instead of invisible-but-implied
- **Per-agent concurrency caps** — `max_concurrent_by_agent: {claude: 3, codex: 5, ...}` in config, exposed as a new column in the Settings → Containment panel. Distinct from the existing per-executor-type cap (`max_concurrent_by_executor["agent"]`) which caps ALL agents together. The per-agent cap is checked in ADDITION to the type cap — a step is throttled if either is at capacity. Useful for "max 3 claude running at once even if codex+aloop slots are free" to protect a rate-limited subscription. New endpoint `PUT /api/config/agent-concurrency` + `agent_concurrency_limits` / `agent_concurrency_running` exposed in `GET /api/config`. Engine tracks per-agent running count and emits `step.throttled` with `reason: "agent_name"` when the per-agent cap fires
- **Workspace tab in job view** — new center-panel tab next to Flow/Timeline/Events that shows the job's workspace directory. Left pane is a lazy-loading file tree (dirs expand on click, workspace listing polls while the job is running), right pane shows a file preview with size + binary detection. Backed by two new endpoints: `GET /api/jobs/{job_id}/workspace?path=...` (listing) and `GET /api/jobs/{job_id}/workspace/file?path=...` (content). Both enforce the workspace root as a boundary — any `..` or absolute path traversal returns 400. Listing capped at 2000 entries, file reads at 512 KB
- **Jump-to-bottom FAB for streaming runs** — when the RunView is scrolled up from the bottom, a floating "Follow live" button appears in the lower-right of the run panel. Clicking it scrolls to bottom and pins the view so new streamed content auto-follows. Scrolling up again unpins and re-shows the FAB. Uses a `ResizeObserver` on the scroll container's child so every new streamed segment triggers a re-scroll while pinned
- **Raw LLM response on output parse failure** — when an LLM step fails parse (`artifact is None`), the run view now shows a prominent red "Parse failure" block inline (not hidden behind a "Meta" button) with the `raw_content`, `raw_tool_calls`, and a link to open the full provider API response body in a modal. The `LLMResponse` dataclass gained a `raw_response: dict | None` field and `OpenRouterClient` now persists the full parsed JSON body. Also falls back to `reasoning_content` / `reasoning` when `choices[0].message.content` is empty — reasoning models (Kimi variants) return their output in those fields

### Changed
- **Adapter-in-VM model (A1)** — the three ACP adapters (`aloop`, `claude-code-acp`, `codex-acp`) now run **inside** the guest VM, not as host processes that "delegate" tool calls. The earlier bridge design defeated containment because Read/Write/Bash tools execute in-process, so a host-spawned adapter ran with host privileges. Adapter-in-VM means every tool call lives inside the hardware boundary
- **Rootfs base image** — switched from Alpine to Debian-trixie-slim. Better Python wheel availability for `aloop`, fewer musl-vs-glibc surprises with claude/codex npm packages
- **Documentation** — `docs/containment.md` rewritten to cover adapter-in-VM model, per-agent credential mounts (virtiofs `~/.claude` / `~/.codex`, env-injected `OPENROUTER_API_KEY`), rootfs tmpfs symlink layout, and the verification staircase

### Fixed
- **Kimi / Kimi K2.5 (and any OpenRouter model) reporting `cost_usd=$0`** — the `OpenRouterClient` wasn't sending `"usage": {"include": true}` in its chat completion requests, so OpenRouter omitted `usage.cost` from the response body and every LLM step silently rolled up to $0. Added the flag; BYOK users now see real per-call cost. The fallback header (`x-openrouter-cost`) is still honoured for legacy responses
- **Session viewer: agent prompt now visible while a step is still running** — previously the run's "Prompt" panel appeared only if variable interpolation actually rewrote something (the engine gated `_interpolated_config` persistence on `interpolated != exec_ref.config`). A static prompt with no `$variable` references had nothing persisted, so the panel stayed empty until the run finished. Now the engine unconditionally writes `_interpolated_config` to `executor_state` at step-prepare time, so the UI shows the exact prompt as soon as the step dispatches
- **`agent_containment` config field never reached `load_config()` output** — the field was defined on `StepwiseConfig` and serialized correctly by `to_dict()` / `from_dict()`, but the merge code in `load_config()` and `load_config_with_sources()` didn't thread it through, so a `agent_containment: cloud-hypervisor` line in `.stepwise/config.local.yaml` had no effect at runtime. Latent bug — exposed when wiring the new Settings panel
- **aloop OPENROUTER_API_KEY threading into VMs** — `acp_backend._resolve_aloop_openrouter_key` reads host `~/.aloop/credentials.json` and injects the key into the VM spawn env when `OPENROUTER_API_KEY` isn't already in env. Without this, aloop sessions silently returned `{"stopReason":"end_turn","usage":{"inputTokens":0,...}}` because the in-VM aloop had no credentials path
- **Guest-pid liveness false positives** — host-side `os.kill(pid, 0)` checks could not see guest pids, so every 15-second health tick marked containment runs "dead" and triggered a 22-attempt retry storm. `ACPBackend` now stamps `executor_state.in_vm = True` on containment spawns, and `process_lifecycle.reap_dead_processes`, `process_lifecycle.reap_expired_processes`, `engine._run_watchdog`, `engine._adopt_stale_cli_job`, and `server._cleanup_zombie_jobs` all skip in-VM runs (they survive host events because `vmmd` is a separate daemon)
- **Probe-test heuristic on Debian rootfs** — `containment-boundary/verify` was prefix-matching the agent's reported `/etc/passwd` first line against `"root:x:0:0:root:/root"`, which Debian's default root line also matches. Verify now compares against the host's actual `/etc/passwd` first line for ground truth
- **aloop session-state writes on read-only rootfs** — `/root/.aloop` symlinked to `/tmp/.aloop` (tmpfs); guest init creates `/tmp/.aloop/sessions/` so aloop's first `mkdir` succeeds. State is per-VM ephemeral, which is correct under containment (session continuity comes from VM reuse, not file persistence)
- **Empty `ANTHROPIC_API_KEY` rejected by claude-agent-acp** — empty auth env vars (`ANTHROPIC_API_KEY=""`, `OPENAI_API_KEY=""`) are now stripped before VM spawn. claude-agent-acp treated the empty string as "external API key auth selected" and rejected the OAuth credentials file

## [0.42.0] — 2026-04-13

### Added
- **Agent management API** — REST endpoints for listing, inspecting, and configuring registered ACP agents
- **Job cost tracking** — per-job and per-step cost rollups in API + UI
- **Settings UI overhaul** — agents, schedules, and project config managed in-app
- **Flow/editor UI enhancements** — kit browsing, virtual scroll for long flows, step YAML view, in-flight job context

### Fixed
- **ACP reliability** — pipe backpressure handling, transport liveness checks, client request handler completeness
- **`after_resolved` loop-invalidation** — dependencies marked `after_resolved` now invalidate across loop iterations instead of being treated as one-shot satisfied
- **For-each sub-job orphan race** — stale-pending watchdog catches sub-jobs that lost their executor before completing
- **Web dist sync** — `_web/` rebuilt and pruned so packaged wheels ship the matching frontend

## [0.41.0] — 2026-04-11

### Added
- **Built-in scheduling** — cron schedules and poll triggers as first-class flow launchers. Cron fires on a time pattern; poll evaluates a shell condition on a cadence and only launches when the condition passes (no junk jobs from early-exit hacks). Cursor mechanism carries state between evaluations. Overlap policies (skip / queue / allow), cooldown windows, auto-pause on errors. Every tick logged. CLI: `stepwise schedule create/list/run`. UI: full schedule CRUD, last-job-status row, chat-agent template vars

### Fixed
- **ACP session continuity** — probe before load so reattach to a still-living adapter session works without spuriously re-creating one
- **Overlap detection + diagnostics** — schedule-overlap rules + session diagnostics improvements

## [0.40.0] — 2026-04-11

### Added
- **Agent containment** — hardware-isolated agent execution via [Cloud-Hypervisor](https://www.cloudhypervisor.org/) microVMs. Agent steps run inside VMs with only declared filesystem paths, credentials, and network endpoints. Non-agent steps (script, LLM, polling, external) always run on host. Opt-in via `containment: cloud-hypervisor` at step, flow, or agent settings level, or `--containment cloud-hypervisor` CLI flag
- **vmmd (VM Manager Daemon)** — privileged daemon managing VM lifecycle (virtiofsd, cloud-hypervisor, shared memory). Runs as root via one-time sudo prompt. Stepwise runs unprivileged and talks to vmmd over a Unix socket. ACP data path goes directly to guest via vsock — vmmd only handles control plane
- **virtiofs workspace mounting** — host directories mounted live into VMs via virtiofs. Near-native read/write performance for multi-GB repos. No copy-in/copy-out. Host kernel enforces subtree boundaries
- **VM grouping by agent config** — steps with the same tools, paths, and credentials share a VM. Different configs get separate hardware boundaries. Uses the same `ResourceLifecycleManager` as ACP process lifecycle
- **Guest agent** — Python agent inside VM (vsock port 9999) that spawns ACP commands with bidirectional stdio bridging. Handles concurrent connections, process lifecycle, and clean shutdown
- **`stepwise vmmd start/stop/status`** — manage the VM manager daemon
- **`stepwise doctor --containment`** — check containment prerequisites (KVM, cloud-hypervisor, virtiofsd, kernel, rootfs, vmmd)
- **`stepwise build-rootfs`** — build VM rootfs image from agent registry (Alpine + Python 3.12 + Node 22 + ACP adapters)
- **`stepwise audit <flow>`** — show containment security profile (VM groups, step containment, host steps)
- **`containment` field in YAML** — flow-level and step-level, with override chain: step > flow > agent settings > CLI
- **`docs/containment.md`** — comprehensive user-facing documentation with architecture, setup, security model, troubleshooting

### Changed
- **`ACPBackend`** — accepts optional `ContainmentBackend` for hardware-isolated process spawning. `_config_eq` includes containment in equality check
- **`ResolvedAgentConfig`** — new `containment` field, populated from agent settings or step overrides
- **`StepwiseConfig`** — new `agent_containment` field
- **CLI** — `--containment` flag on `stepwise run`
- **Documentation** — containment added to README, cli.md, concepts.md, executors.md, agent-integration.md, troubleshooting.md, flow-reference.md

## [0.39.0] — 2026-04-10

### Added
- **Native ACP client** — stepwise speaks Agent Client Protocol (ACP) JSON-RPC 2.0 directly over stdio to agent server processes. No middleware dependency. Hand-rolled transport with Future-based request/response multiplexing, notification streaming, and non-JSON line filtering
- **Agent registry** — settings-based configuration for ACP agents. Three builtins: `claude` (via `@agentclientprotocol/claude-agent-acp`), `codex` (via `@zed-industries/codex-acp`), `aloop` (native). User-defined agents via stepwise settings. Config keys support flag, env var, and ACP method delivery with defaults, overrides, and required validation
- **Resource lifecycle manager** — generic reactive lifecycle: lazy allocation on first step need, backward-looking reuse when config matches (`is_eq`), deterministic cleanup when no future steps remain. Shared foundation for ACP process management and future containment VM lifecycle
- **Shared NDJSON extraction module** (`acp_ndjson.py`) — canonical implementations of session ID, cost, text, and error extraction from ACP output. Replaces duplicated parsers across backends
- **Mock ACP server** — configurable test server with scripted responses, multi-session support, and capability toggling for comprehensive unit testing

### Changed
- **Agent executor backend** — `ACPBackend` replaces both `AcpxBackend` and `ClaudeDirectBackend` as a single unified backend. One process per config group hosts multiple sessions (validated: claude-agent-acp and aloop both support multi-session)
- **Engine session state** — `SessionState.claude_uuid` renamed to `session_id`, `backend_type` field removed. Fork logic uses ACP `session/fork` directly instead of Claude-specific snapshots
- **CLI LLM client** — rewritten to use native ACP transport instead of `acpx exec` subprocess
- **Flow editor agent** — `_acpx_agent_loop` replaced with `_acp_agent_loop` using native ACP transport with adaptive timeout streaming
- **Session cleanup** — engine session cleanup simplified to lifecycle manager teardown, replacing `acpx sessions close` subprocess calls

### Removed
- **acpx dependency** — zero references remain in source, tests, docs, or install scripts. All agent communication uses native ACP
- **`AcpxBackend`** class and all queue owner detection/cleanup/heartbeat code (~724 LOC)
- **`ClaudeDirectBackend`** and `claude_direct.py` (~596 LOC) — ACP adapters handle translation
- **acpx installation** from `install.sh` and `selfupdate` command

## [0.38.1] — 2026-04-08

### Changed
- **Agent process TTL disabled by default** — agent steps now run without a time limit. The previous 2-hour hard TTL silently killed long-running agent steps (e.g., deep research). Use per-step `limits.max_duration_minutes` in FLOW.yaml for intentional timeouts
- **`agent_process_ttl` config setting** — configurable in `config.yaml` (seconds, 0 = disabled). Overridable via `STEPWISE_AGENT_TTL` env var. Acts as a global safety net for zombie processes when enabled

## [0.38.0] — 2026-04-08

### Added
- **Coordination validator** — `stepwise validate` statically analyzes flow coordination rules. Catches session-writer collisions, fork ordering violations, unguarded loop-back bindings, and genuine cycles vs intentional loops
- **Predicate-form `when:` clauses** — `is_present:`, `is_null:`, `eq:`, `in:` predicates. The validator proves mutual exclusion between predicate-form branches, enabling safe session sharing across conditional paths (e.g., an escalation branch vs a normal branch writing to the same session)
- **Must-happen-before analysis** — the validator computes ordering over the flow DAG, including universal-prefix rules for `after.any_of` groups, so it can prove two session writers never run concurrently
- **`fork_from:` step-name semantics** — `fork_from` now references a step name (not a session name). The engine snapshots the fork source's session at completion and each fork gets an independent copy. Atomic snapshot via file lock
- **Loop-back binding runtime** — loops declared via `exits: [{action: loop}]` are now first-class. The validator distinguishes intentional loops from genuine cycles. New `is_present:` / `is_null:` predicates let steps branch on whether a loop-back input exists yet (iter-1 vs iter-N)
- **Nested loop support** — explicit loop-frame state on `Job` tracks iteration indices for nested loops. Child frames automatically reset when the outer loop iterates. Persisted to SQLite, rebuilt on crash recovery
- **Ephemeral `fork_from:`** — `fork_from` no longer requires `session:`. One-shot forks are transient — no session tracking, retries allowed. Use when you just need context from a parent step without maintaining a named session
- **`type: session` flow inputs** — new input type for passing session snapshots to sub_flows. Enables composable `for_each` + fork patterns where the sub_flow doesn't hardcode parent step names
- **`_session` virtual output** — any step with `session:` auto-exposes `_session` (resolves to the session snapshot). Combined with `fork_from: $job.<input>`, enables passing session context across scope boundaries (e.g., parent flow to for_each sub_flow)
- **`for_each` + `fork_from`** — for_each iterations can fork from a parent session. Each iteration gets an independent fork via `type: session` inputs. The canonical subagent fan-out pattern:
  ```yaml
  explore:
    for_each:
      items: build_context.angles
      item_var: angle
    inputs:
      context: build_context._session
    flow:
      steps:
        deep_dive:
          executor: agent
          fork_from: $job.context
          outputs: [result]
          prompt: "Deep-dive into: $angle"
  ```
- **Ergonomic inferences** — `agent: claude` inferred from `fork_from:` (forks are always claude), `working_dir` inherited from fork source, `flow.inputs:` inferred from parent step bindings for embedded sub_flows. Eliminates boilerplate on fork steps
- **Parse-time back-edge validation** — unguarded back-edge bindings (no `optional:`, `any_of`, or `is_present:` guard) are rejected with a clear fix suggestion

### Changed
- **Cycle detection** — now distinguishes intentional loops (closed by `exits: [{action: loop}]`) from genuine cycles. Loop-back edges are excluded from the forward-DAG cycle check, so flows with score/refine loops validate clean
- **Input resolution** — `_resolve_inputs` returns a presence side-table alongside inputs, enabling `is_present:` / `is_null:` predicates to distinguish "not yet produced" from "produced with null value"
- **Readiness check** — steps with loop-back-only dependencies become ready immediately on iter-1 (don't wait for the producer that hasn't run yet)
- **Schema migration** — `ALTER TABLE jobs ADD COLUMN loop_frames TEXT DEFAULT '{}'` (idempotent, backwards compatible)
- **README** — session example updated from `continue_session: true` to `session: main` + `fork_from`

### Documentation
- Updated `yaml-format.md`, `writing-flows.md`, `concepts.md`, `flow-reference.md`, `README.md` with fork rules, loop-back runtime semantics, `is_present`/`is_null` truth table, session inputs, and ergonomic inference documentation

## [0.37.0] — 2026-04-06

### Added
- **Kits** — group related flows into named collections with `KIT.yaml`. Organize flows by domain (e.g., `swdev` kit with plan-light, plan, plan-strong, implement, fast-implement)
- **Kit namespaced resolution** — reference kit flows as `kit/flow` (e.g., `stepwise run swdev/plan-light`). Strict resolution with helpful hints on bare names
- **Kit includes** — reference flows from other kits (`podcast/podcast-deep`), standalone flows, or registry flows (`@author:slug@^1.0`) in your kit's `include` field. Auto-fetch missing registry includes
- **Full semver support** — version constraints for kit includes: `^1.0` (caret), `~1.2` (tilde), exact (`1.2.3`), PEP 440 passthrough (`>=1.0,<2.0`)
- **Kit defaults** — `defaults` field in KIT.yaml inherited by all member flows (e.g., shared `author`, `visibility`). Explicit flow values always win
- **`stepwise catalog`** — generate a kit/flow catalog section for SKILL.md, grouped by category
- **`stepwise new kit/flow`** — create a flow inside a kit directory (e.g., `stepwise new swdev/my-flow`)
- **`stepwise agent-help <kit>`** — L2 progressive disclosure: kit usage/composition instructions plus full flow details for all member flows
- **Kit registry support** — `stepwise share` auto-detects kits (KIT.yaml present), publishes as atomic package with all bundled flows. `stepwise get @author:kit` installs kit + bundled flows + resolves includes. `stepwise search` shows TYPE column (flow/kit)
- **Kit API endpoints** — `GET/POST/PUT/DELETE /api/kits`, cross-table slug uniqueness with flows, FTS search, unified search via `GET /api/flows?include_kits=true`
- **Web UI folder navigation** — flows page shows kits as folder cards (Google Drive-style: click to drill in, breadcrumb back). Kit info sheet with usage and "View KIT.yaml" modal
- **Editor kit detail** — FlowOverview shows kit info bar for kit member flows with link to kit and KIT.yaml viewer

### Changed
- **Agent-help output** — flows grouped by kit in L0/L1, standalone flows in separate section. Kit usage/composition instructions shown inline
- **Flow discovery** — `discover_kits()` resolves includes, `discover_flows()` returns kit membership via `kit_name` field
- **`stepwise flows`** — grouped by kit with flow count

### Documentation
- Updated 8 docs for kits: flow-sharing.md (retitled to "Flow and Kit Sharing"), cli.md, README.md, concepts.md, quickstart.md, yaml-format.md (KIT.yaml format reference), writing-flows.md, FLOW_REFERENCE.md

## [0.36.0] — 2026-04-03

### Added
- **Collapsible sidebar sections** — reusable `SidebarSection` component with HR, title, chevron toggle; used for Inputs, Outputs, Session, Flow Metadata, and Prompt sections across both sidebars
- **Session step flow** — ordered step chain with token counts (e.g., `reflect 155.9k → prompt_review 5.3k → build 17.1k`) shown in session headers and sidebar session cards
- **Session modal view** — expand button on session section opens full transcript in a wide dialog
- **LLM response rendering** — single-output LLM steps render response directly with markdown auto-detection and expand button, instead of key-value output rows
- **Context window usage bar** — two-color progress bar (gray = prior steps, blue = current step) in session header
- **Timeline step details** — executor type, model, agent, session name shown in timeline row labels and hover tooltips
- **Port tooltip copy buttons** — each input/output in DAG port modals has a clipboard copy icon
- **Job overview: Flow link** — clickable flow name in job sidebar info grid navigates to flow editor

### Changed
- **Agent step Run tab redesign** — unified layout: meta/exit/cost/actions at top, inputs/outputs as collapsible sections with colored keys (cyan inputs, emerald outputs), full session transcript inline below
- **Session tab hidden for agent steps** — transcript is embedded in Run tab; Session tab only shows for non-agent steps
- **AgentStreamView simplified** — removed search bar, virtualization, and inner scroll containers; segments render flat in the sidebar
- **Input/output display** — replaced adaptive-height slots with clean key-value rows: input mappings show full YAML-style source (e.g., `prompt ← $job.prompt`), 3-line clamp, click-to-expand modals
- **Run details table** — exit, cost, workspace, meta displayed as aligned key-value grid (matches job overview style); implicit_advance shows as "implicit advance" without arrow
- **Job outputs flattened** — terminal output arrays merged into single object instead of showing array indices
- **Port tooltips portaled** — rendered via `createPortal` to document.body, fixing z-index issues where step nodes overlapped tooltips
- **Port hover** — removed scale-150 on hover (was scaling the tooltip); dots brighten on hover instead
- **Port modal reopen fix** — click-outside dismiss no longer immediately reopens the modal
- **Prompt bubble** — violet background bubble instead of blockquote left-border style; white text
- **Tool calls single-line** — truncated to one line in agent stream view
- **Font size bump** — info grid tables, session boundary headers, and token counts bumped from 10px to 12px across both sidebars
- **Tab reorder** — Timeline moved before Events; Tree view tab removed
- **Actions repositioned** — Reset/Start/Cancel buttons moved below info grid in job sidebar; Restart/Cancel below meta in step sidebar
- **Compact token display** — `155.9k / 1M` format instead of full numbers

### Fixed
- **Session boundary mapping** — `buildBoundarySegmentMap` now uses accurate `eventToSegment` array from `buildSegmentsFromEvents`, fixing last-step content not appearing in session views
- **Job cost on subscription** — agent step costs stored as $0 at write time when on subscription billing; cost endpoints no longer short-circuit to zero
- **Run cost endpoint** — uses `_run_cost()` with executor_meta fallback instead of step_events only
- **Sessions tab independence** — left sidebar Sessions tab no longer reacts to step selection changes

## [0.35.0] — 2026-04-03

### Added
- **Step-scoped session tab** — right sidebar session view filters to the selected step's runs, shows session name, shared-with pills, and link to full session in job details
- **Prompt display in sessions** — agent prompts parsed from NDJSON `session/prompt` events and shown inline with blue left-border accent, gradient fade, click-to-expand in modal
- **Tool file paths** — tool calls now show actual file paths (e.g., "read scripts/main.py") instead of generic "Read File", via `tool_title` events from intermediate `tool_call_update` messages
- **Per-run token counts** — session boundaries display incremental token usage per run
- **Trigger poll now** — `POST /runs/{run_id}/poll-now` endpoint and UI button for suspended poll steps to force an immediate check
- **Poll status display** — poll steps show check count, last/next check time, command, interval, and last error
- **Shared markdown renderer** — `react-markdown` + `remark-gfm` replaces all hand-rolled markdown renderers across content modals, changelog, chat messages, and session transcripts
- **Auto-scroll with FABs** — session views auto-scroll to bottom on load, follow streaming content, show jump-to-top/bottom floating buttons when scrolled
- **Updated column in flows list** — shows when each flow file was last modified, sortable
- **Bundled changelog** — `CHANGELOG.md` packaged with the wheel so the changelog modal works in installed deployments

### Changed
- **Session transcript redesign** — proportional font for agent prose, lightweight inline tool calls (dot + kind + path, no cards), contiguous tool runs of 3+ collapse into expandable summaries
- **Panel controls in global nav** — panel toggle buttons moved from page-level breadcrumb bars to the global top navigation via PanelContext
- **Job overview sidebar** — config/outputs flattened (no expand/collapse), split into Job Inputs vs Flow Defaults, outputs click-to-open modal
- **Flows list checkboxes** — subtle hover checkboxes, select-all header, removed selection bar
- **Jobs list checkboxes** — same pattern as flows list, row click navigates to job
- **Cursor-pointer audit** — 35+ clickable elements fixed across the UI, including base Button and TabsTrigger components
- **Chat messages** — reuse shared ToolCard and Markdown components, show tool input args (file paths, commands)
- **Output viewing** — replaced sidebar panel with ContentModal popups for job outputs and edge data flow labels
- **Notification badge** — persists last-seen timestamp in localStorage, highlights new events on open
- **DAG step subtitle** — shows agent backend name instead of output_mode; named sessions show session name in violet

### Fixed
- **Duplicate flow dispatch** — `_run_stepwise_flow` checks for already-running jobs before dispatching
- **Suspended step duration** — live-ticking amber duration shown for polling/suspended steps
- **Port tooltip zoom** — input/output port tooltips counter-scaled to stay at screen size regardless of zoom
- **Changelog modal** — scrolling fixed, about section with homepage and GitHub links added
- **$0 cost hidden** — cost display suppressed when zero (subscription billing)

## [0.34.0] — 2026-04-02

### Added
- **Named sessions** — steps with matching `session: <name>` share a single agent conversation. Replaces `continue_session` + `_session_id` input wiring with a declarative, one-field approach
- **Session forking** — `fork_from: <session>` creates an independent session branched from the parent's full conversation context. Enables parallel review, critique, and analysis patterns
- **ClaudeDirectBackend** — new agent backend that calls `claude` CLI directly for fork/resume operations. Writes ACP-compatible NDJSON so the web UI, DB, and reports see identical data regardless of backend
- **Session validation rules** — 7 parse-time checks: fork requires explicit `agent: claude`, fork_from must reference known session, for_each + session incompatible, old syntax detection, DAG ordering for forks
- **`_extract_claude_session_id()`** — reliable extraction of Claude session UUIDs from ACP NDJSON (reads only `result.sessionId`, never `params.sessionId`)

### Changed
- **Dual-backend agent executor** — `AgentExecutor` now accepts both `AcpxBackend` and `ClaudeDirectBackend`, routing automatically based on session fork state
- **Session locking** — `_SessionLockManager` now keys by session name from step definitions instead of `_session_id` from inputs
- **Circuit breaker** — `max_continuous_attempts` now fails the step instead of silently creating a new session

### Removed
- **Chains** — `chains:` top-level block, `chain:`/`chain_label:` step fields, `ChainConfig`, `context.py` (398 lines), and all chain context compilation. Zero production flows used chains
- **`_session_id` auto-emission** — steps no longer inject `_session_id` into artifacts. Engine manages session lifecycle via registry
- **`continue_session`** — deprecated in favor of `session:`. Legacy fallback kept for backward compatibility
- **Transcript capture** — removed chain-dependent transcript capture (UI uses raw NDJSON output files directly)

## [0.22.0] — 2026-03-30

### Added
- **Right-click context menus** — entity-driven action registry powering context menus, kebab menus, and keyboard shortcuts from a single source of truth. 4 entity types (Job: 17 actions, Step: 5, Flow: 9, Canvas: 4) across 10 UI surfaces
- **Canvas zone layout** — independent jobs partition to responsive CSS grid, dependent jobs stay in Dagre DAG with status-priority sorting
- **WebGL energy pulse edges** — Three.js + custom GLSL shaders with bloom post-processing, 4-state machine (idle/surge/flow/completed/failed), luma-alpha transparency, loop-aware pulsing
- **Live script output streaming** — real-time stdout/stderr tailing for script steps in the web UI with WebSocket delivery
- **Virtualized log viewers** — @tanstack/react-virtual replaces raw .map() rendering, eliminates 50-line truncation
- **Pretext integration** — canvas-based text measurement for accurate virtualized scroll heights
- **Follow-flow camera** — zoom stability, initial view centering, suspended step height awareness
- **Fulfillment panel** — wider panel, scrollable body, pinned submit, wheel event isolation
- **Running step breathing glow** — CSS keyframe pulsing blue glow ring on active steps
- **PID-file guard** — prevents duplicate server processes with stale PID detection and atexit cleanup
- **Agent executor circuit breaker** — consecutive failure tracking, permanent error halt, stuck task routing

### Changed
- **`stepwise welcome` renamed to `stepwise demo`** — clearer command name, flow directory and registry references updated
- CLI polish audit — error output, help text, formatting improvements across 7 files
- Docs and README overhaul — "packaged trust" positioning, new web-ui.md and writing-flows.md guides

### Fixed
- Registry flow resolution for derived outputs
- Usage limit resilience — error classification, reset time parsing, agent backend wait with file tailing

## [0.21.0] — 2026-03-27

**Full-Stack Orchestration Platform** — from engine primitives to production-grade job management, a comprehensive web UI, and hardened agent reliability. 297 commits spanning 15 internal versions since 0.6.0.

### Added

#### Engine
- **Job staging** — `STAGED` status, job groups, dependency edges, cross-job data wiring via `$job_ref` inputs, approval gates (`AWAITING_APPROVAL`)
- **Derived outputs** — compute output fields from executor results using expressions
- **Job metadata** — `--meta key=value` flag, metadata column, event envelopes with hook env vars
- **Step result caching** — `cache:` config on steps, `stepwise cache` CLI commands, for-each batch cache, `--rerun` flag to bypass cache
- **Multi-job wait** — `stepwise wait` with `--all` and `--any` flags for blocking on multiple jobs
- **Orphan recovery** — auto-adopt orphaned CLI jobs on server startup and periodically
- **Agent concurrency** — configurable `max_concurrent_agents` with stagger delay, semaphore-based dispatch
- **Transient retry** — auto-applied retry decorator for agent executors with error classification and exponential backoff
- **Server identity** — cross-project confusion prevention, global server registry with warnings
- **DAG validation** — `stepwise check` with cycle detection, unreachable step detection, non-zero exit
- **`stepwise validate --fix`** — auto-fix common YAML issues; `stepwise test-fixture` for test flow generation
- **Premature launch detection** — warn on steps downstream of loop bodies that could fire too early
- **`on_error: continue`** for parallel steps (not just for-each)
- **Stall detection** — validate warns on steps that can never reach completion

#### CLI
- **`stepwise job`** subcommand group — `create`, `show`, `run`, `dep`, `cancel`, `rm` for job staging
- **`stepwise tail`** — live event stream for running jobs
- **`stepwise logs`** — chronological event dump for debugging
- **`stepwise output`** — retrieve per-step outputs with positional step name
- **`stepwise docs`** — browse bundled documentation with keyword search fallback
- **`stepwise server log`** — view server log output
- **`stepwise extensions list`** — show installed executor plugins
- **`stepwise login/logout`** — Device Flow authentication for registry
- **`stepwise preflight`** — ready-to-run assessment for flows
- **`stepwise info`** — flow metadata and config init scaffolding
- **`stepwise uninstall`** — clean removal command
- **`stepwise help`** — interactive assistant
- **`stepwise version`** — alias for `--version`

#### Config System
- **Config variables** — `ConfigVar` with types, defaults, descriptions, sensitive flag, choice options
- **Flow requirements** — declare external dependencies with install hints and URLs
- **`config.local.yaml`** — per-machine overrides, auto-excluded from bundles and git
- **Project-level `notify_url`** — webhook notifications without per-run flags
- **JSON Schema generation** for flow config inputs

#### Web UI
- **Orchestrator Canvas** — mini-DAG job cards in responsive CSS grid layout
- **Virtualized job list** — handles thousands of jobs via `@tanstack/react-virtual`
- **Command palette** — `Cmd+K` / `Ctrl+K` quick navigation
- **Timeline/waterfall view** — step execution timing visualization
- **Diff viewer** — output changes across retry attempts
- **Log search** — regex filtering across all log viewers
- **Breadcrumb navigation** — hierarchical page navigation
- **Error recovery suggestions** — actionable fix hints on failure pages
- **Live duration** — real-time elapsed time on running steps
- **Browser notifications** — alerts for suspended steps
- **Light/dark theme toggle** — full light mode support across all components
- **Toast notifications** via sonner
- **WebSocket status indicator** — connection health in header
- **Status count badges** — aggregate counts on filter pills and nav links
- **Quick-launch section** — recently-run flows on dashboard (later removed)
- **Keyboard navigation** — arrow keys + Enter on job list
- **Sort controls** — name, status, date sorting on job list
- **Per-job action menu** — Cancel, Retry, Delete from job list
- **Relative time grouping** — "Today", "Yesterday", "This week" in job list
- **URL-persisted filters** — `?q=&status=` search params
- **Error summary banner** — failed job diagnostics
- **Collapsible for-each groups** — expandable step groups in DAG
- **DAG polish** — rich tooltips, executor accents, animated edges, shareable screenshot export
- **Responsive mobile layout** — full mobile support across all pages
- **React error boundary** — prevents white-screen crashes
- **404/Not Found page** — proper routing for missing resources
- **Tabbed right sidebar** — unified editor panels
- **Raw log viewer** — script step stdout/stderr
- **Flow-not-found** message in editor
- **Welcome banner** — shown when no jobs exist
- **Cost attribution** — `$0 (Max)` display for subscription billing

#### Eval Framework
- **eval-1.0** — 16-step evaluation flow with preflight, discovery, security, migration, data integrity, quality testing, scoring, and HTML report generation

### Changed
- **Rename `executor: human` → `executor: external`** — breaking change; update all `.flow.yaml` files
- **Rename `sequencing:` → `after:`** in flow YAML — breaking change; update all flow definitions
- **Rename `--var`/`--var-file` → `--input`** with `@file` prefix detection
- **Input names must be valid identifiers** (`[A-Za-z_][A-Za-z0-9_]*`) — use underscores instead of hyphens
- **`$var` placeholders auto shell-quoted** — do not pre-quote placeholders in `command`/`check_command`
- **Exit rule default: fail on no-match** when explicit `advance` rules exist but none match
- **Remove default concurrency limit** — all jobs start immediately unless configured otherwise
- **Jobs list endpoint** returns summary payload (1.8MB → 13KB) with limit parameter
- **Require auth for `stepwise share`** — Device Flow login required for registry publishing
- Install script uses `--force --reinstall` for reliable upgrades
- Server defaults to detached start; binds 0.0.0.0 for container accessibility
- `.step-io/` moved under `.stepwise/step-io/`

### Removed
- **Route system** (`RouteSpec`, `RouteDefinition`, `_launch_route`) — replaced by `when`-based branching
- **`stepwise chain`** subcommand — replaced by `--after` + `--input` job staging
- **QuickLaunch** from web dashboard

### Fixed
- Orphaned CLI jobs recovered on server restart instead of failing them
- React Error #31 from object values rendered as JSX children
- Light mode CSS variants across all web components
- Terminal step detection for sub-flow loop cycles
- Currentness invalidation cycles in circular dependency chains
- Agent session name collisions across concurrent jobs
- `acpx` session cleanup on job completion, failure, and cancellation
- Script step output recovery on server restart
- OpenRouter 400 errors from incorrect `tool_choice` on single-output LLM steps
- Double-escaped JSON strings in LLM executor output
- Expression namespace: `true`/`false`/`null` aliases for Python builtins
- For-each all-fail correct behavior
- Follow-flow mode ensures full input dialog visible for suspended steps
- Canvas page crash from `job_group` accessed from wrong path
- Harden exit rule and interpolated config rendering against unexpected types
- Zombie job cleanup with PID verification
- `stepwise update` blocked when running from editable install

### Security
- AST validation on exit rule, `when`, and derived output expressions — blocks `__class__`/`__bases__`/`__globals__` traversal
- Shell-escape user input values in `command`/`check_command` via `shlex.quote()`
- Namespace step inputs under `STEPWISE_INPUT_` prefix in environment variables
- Reject output file paths that escape working directory in AgentExecutor

### Deprecated
- Bare input environment variables (`$url`). Use `$STEPWISE_INPUT_url` instead. Bare names still exported during deprecation period.

### Development
- 1980 Python tests, 310 frontend tests (2290 total, up from ~700 in 0.6.0)

## [0.6.0] — 2026-03-17

**Optional Inputs, Session Continuity, Webhook Notifications** — smarter loops, agent memory, and async event delivery.

### Added
- **Optional inputs** — `{from: "step.field", optional: true}` weak-reference bindings that resolve to `None` when the source dep is unavailable. Enables feeding data backward across loops, first-run defaults, and graceful degradation. Cycles in the dependency graph are valid if every cycle contains at least one optional edge.
- **Session continuity** — `continue_session: true` on agent/LLM steps reuses the same agent session across loop iterations instead of starting fresh. `loop_prompt` provides an alternate prompt for attempt > 1. `max_continuous_attempts` acts as a circuit breaker, forcing a fresh session with chain context backfill after N iterations.
- **Cross-step session sharing** — Agent steps with `continue_session` auto-emit `_session_id` as a typed output. Downstream steps receive it via optional input bindings to continue the same conversation. Engine serializes concurrent access via `_SessionLockManager`.
- **Webhook notifications** — `stepwise run --async --notify <url> --notify-context '{...}'` delivers HTTP POST callbacks on job suspend, complete, and fail events. Context dict is passed through to every webhook payload.

### Changed
- **Exit rule default behavior** — When explicit `advance` rules exist but none match, the step now **fails** instead of silently advancing. This prevents unhandled output cases from progressing through the DAG. Steps with only loop/escalate/abandon rules still implicitly advance when unmatched.

### Fixed
- **Ctrl+C during human input** — presents suspend/cancel menu instead of crashing
- **External fulfill detection** — AsyncEngine polls for state changes every 5 seconds, picking up fulfills from other processes without waiting for an internal event

## [0.5.0] — 2026-03-17

**Server Management, Config Interpolation, Expression Fixes** — structured server commands and executor parameterization.

### Added
- **`stepwise server` subcommands** — `start`, `stop`, `restart`, `status` replace the old `stepwise serve`. `--detach` for background mode, status shows PID/port/uptime/log path
- **Config interpolation** — executor config string values support `$variable` interpolation from resolved inputs, parameterizing model, command, system message, etc.
- **`stepwise diagram`** — DOT source no longer pollutes flow resolution (uses `pipe()`)

### Fixed
- **Single-output LLM steps** — skip `tool_choice` for steps with one output to prevent truncation; model responds naturally with JSON/text fallback
- **Cycle detection** — now accounts for loop back-edges, preventing false positives on valid loop targets
- **Expression namespace** — `true`/`false`/`null` aliases added (Python `True`/`False`/`None`)
- **For-each all-fail** — correct behavior when all fan-out instances fail
- **Cost reporting** — accurate token cost aggregation
- **Diagram port labels** — HTML line breaks in port node labels, no more overlap

## [0.4.0] — 2026-03-16

**Pull-Based Branching, Poll Executor, Smooth DAG Camera** — conditional workflows and a polished live view.

### Added
- **`when` conditions** — steps declare their own activation condition evaluated against resolved inputs. Mutually exclusive branches, conditional gates, and skip propagation without explicit routing
- **`any_of` input bindings** — steps can depend on any one of multiple upstream steps, enabling merge points after conditional branches
- **`SKIPPED` step status** — steps that never activate are marked SKIPPED at job settlement, with a `STEP_SKIPPED` event
- **Poll executor** — `executor: poll` runs `check_command` at `interval_seconds`; JSON dict on stdout = fulfilled. For waiting on external conditions (PR reviews, deploys, etc.)
- **DAG camera** — critically damped spring with dead zone and target blending for smooth auto-follow. Zoom-to-fit active nodes (70–100%), slower pan lerp, extended active rect for human input popovers
- **Animated layout transitions** — DAG nodes interpolate position when the layout changes (expand/collapse, new steps)
- **Flows page** — dedicated page for browsing local flows, split from the editor
- **Local flow info panel** — three-column layout showing flow metadata, executor types, and description
- **Zombie job cleanup** — server fails jobs owned by dead processes on startup
- **`acpx` auto-install** — install script installs acpx when npm is available
- **`stepwise diagram`** — CLI command to render flow DAGs via graphviz

### Changed
- **Branching model rewrite** — removed the entire route system (`RouteSpec`, `RouteDefinition`, `_launch_route`). Replaced with pure-pull `when`-based branching. Exit rule `advance` with `target` replaced by `when` conditions on downstream steps
- Welcome flow rewritten using DAG branching primitives
- EditorPage simplified after FlowsPage extraction

### Fixed
- NaN/Infinity in SVG from layout transition and dagre
- Container port labels overlapping header and clipping at bottom
- Output port labels overflowing expanded container bottom
- `any_of` input bindings handled correctly in DAG layout
- Null job status handled gracefully in store

## [0.3.1] — 2026-03-14

**CLI display overhaul** — rich, readable terminal output.

### Added
- **Live block rendering** — active steps redraw in place, completed steps scroll up permanently. No more interleaving of parallel for-each items
- **Output previews** — completed steps show `→ key: value` inline
- **For-each item labels** — `[data-model]`, `[api-routes]`, etc. group sub-steps under their item
- **Loop icon** — `⟳` for retry attempts
- **`stepwise welcome`** — interactive post-install demo prompt

### Changed
- Install script uses `--force --reinstall` for reliable upgrades
- Cleaner post-install message with copy-pasteable commands

## [0.3.0] — 2026-03-14

**Async Engine, Live DAG, Agent Emit Flow** — real-time execution with dynamic workflows.

### Added
- **AsyncEngine** — event-driven engine replaces tick-based polling. Parallel step dispatch via `asyncio.Queue`, executor runs in thread pool. No more tick interval
- **Agent Emit Flow** — agent steps with `emit_flow: true` can dynamically create sub-workflows by writing `.stepwise/emit.flow.yaml`. Engine launches emitted flow as sub-job and propagates results back. Supports iterative delegation with exit rule loops
- **CLI Server Delegation** — `stepwise run` auto-delegates to a running server for lower latency. `--wait` and `--async` modes use WebSocket for live updates, falling back to REST polling
- **Job Ownership** — `created_by`, `runner_pid`, `heartbeat_at` fields track who owns each job. Stale detection for orphaned CLI jobs. Server adoption via `POST /api/jobs/{id}/adopt`
- **Typed Human Inputs** — `OutputFieldSchema` with `type: choice|number|text`, validation, auto-generated UI controls in web and CLI
- **Follow-Flow Mode** — DAG view auto-pans to track active steps at 100% zoom
- **Welcome Flow** — interactive product tour: plan, implement (for-each), test (retry loops), review (route steps), deploy. Available as `@stepwise:welcome` from registry
- **`STEPWISE_ATTEMPT`** — attempt number exposed as env var to script executors
- **Inline Human Input** — human step panels render directly below suspended DAG nodes
- **Auto-Expand Sub-Jobs** — delegated sub-flows and for-each instances expand automatically in the DAG
- **Animated DAG Edges** — intake and loopback edges animate with glow and flowing dashes for active steps
- **Data Flow Labels** — artifact values shown on DAG edges with hover tooltips
- **Settings Page** — model labels, API keys, default model configuration in the web UI
- **Billing Mode** — `billing: subscription` skips cost limit enforcement for subscription users
- **IOAdapter** — unified CLI output abstraction (PlainAdapter, QuietAdapter, TerminalAdapter)
- 500+ new tests (1144 total)

### Changed
- Server uses `AsyncEngine` instead of tick-based `Engine`
- `ThreadSafeStore` with `_LockedConnection` proxy serializes all SQLite calls
- Removed `/api/tick` endpoint (no longer needed)
- Install and README quickstart now lead with `@stepwise:welcome` demo

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
