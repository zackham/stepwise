# Named Sessions + Fork Support — Implementation Plan

> **Historical note:** This document was written when Stepwise used acpx for agent communication. The project has since migrated to native ACP stdio transport. Session and fork concepts still apply but the transport layer is different.

**Date**: 2026-04-02
**Status**: Approved design, ready for implementation
**Supersedes**: `agent-session-continuity-proposal.md` (chains + `_session_id` mechanism)

## Summary

Three concepts (`chains`, `_session_id` passing, `continue_session` boolean) collapse into one: **named sessions** with optional **fork**. Fork is implemented via `claude --fork-session`, requiring a claude-direct backend that produces ACP-compatible NDJSON so the rest of stepwise (UI, DB, reports) sees no difference.

## YAML Surface

```yaml
steps:
  plan:
    executor: agent
    agent: claude
    session: planning
    prompt: "Plan."
    outputs: [plan]

  implement:
    executor: agent
    agent: claude
    session: planning          # same name = same conversation
    prompt: "Implement."
    outputs: [code]

  review:
    executor: agent
    agent: claude
    session: critic
    fork_from: planning        # new session forked from planning's context
    prompt: "Review critically."
    after: [implement]
    outputs: [issues]

  fix:
    executor: agent
    agent: claude
    session: planning          # continues the planning session
    prompt: "Fix: $issues"
    inputs:
      issues: review.issues
```

**New step-level fields:**
- `session: <name>` — named session this step participates in. Matching names = same session.
- `fork_from: <session_name>` — fork a new session from an existing one at execution time.

**No new top-level blocks.** Session topology is inferred from step declarations.

**Removed:**
- `chains:` top-level block
- `chain:` / `chain_label:` on steps
- `continue_session: true` (implied by `session:`)
- `_session_id` as magic reserved field

**Kept:**
- `loop_prompt` — controls prompt on attempt > 1 (independent of session mechanism)
- `max_continuous_attempts` — circuit breaker for loop-back (restructured to work with sessions)

---

## Architecture

### The seamless adapter constraint

The web UI, DB, report renderer, and session viewer must see **identical data** regardless of whether acpx or claude-direct produced it. Strategy: **ClaudeDirectBackend writes ACP-compatible NDJSON**, translating claude `stream-json` events into ACP envelope format on the fly.

```
                    ┌─────────────┐
                    │  YAML Flow  │
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   Engine    │  session registry: name → {uuid, backend_type}
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │                         │
     ┌────────▼────────┐      ┌────────▼─────────┐
     │  AcpxBackend    │      │ ClaudeDirectBack  │  (fork + continue)
     │  (normal path)  │      │ end               │
     └────────┬────────┘      └────────┬──────────┘
              │                         │
              │    Both write identical  │
              │    ACP NDJSON format     │
              ▼                         ▼
     ┌──────────────────────────────────────────┐
     │  .stepwise/step-io/{step}.output.jsonl   │  ← same format
     └──────────────────────────────────────────┘
              │
     ┌────────┼──────────┬───────────┐
     │        │          │           │
    UI    DB store    Reports   Session viewer
  (same)   (same)     (same)     (same)
```

### Session UUID handling (validated)

acpx stores two UUIDs per session:
- `acpx_record_id` — acpx's internal tracking UUID (filename in `~/.acpx/sessions/`)
- `acp_session_id` — Claude's session UUID (stored in session JSON and used by claude CLI)

Claude CLI's `--resume` only accepts the `acp_session_id`. It appears as `result.sessionId` in the `session/new` response in ACP NDJSON.

**Critical**: The current `_extract_session_id()` can return the wrong UUID for continued sessions (`session/load` puts acpx_record_id in `params.sessionId`, which is matched first). For fork, we need a dedicated `_extract_claude_session_id()` that only reads `result.sessionId`.

The session registry captures the claude UUID when a session is first created and never overwrites it from subsequent continuation steps.

---

## Validation rules (parse-time)

1. **`fork_from` requires `session`**: step with `fork_from` must also declare `session`.
2. **`fork_from` references known session**: must match a `session` name on another step.
3. **Fork steps require claude**: any step with `fork_from`, and all steps on the parent session, must have `agent: claude`.
4. **Consistent `fork_from`**: if multiple steps share a session name, only one declares `fork_from` (or all agree).
5. **DAG ordering**: first step on a forked session must depend on at least one step on the parent session.
6. **`for_each` + `session` incompatible**: validation error. Defer to future release.
7. **Old syntax error**: `_session_id` in inputs or `continue_session: true` → error with migration message.

---

## Phase 1: ClaudeDirectBackend

**Goal**: New `AgentBackend` implementation using `claude` CLI directly, producing ACP-compatible NDJSON.

### Task 1.1: ACP NDJSON translation layer

**File**: `src/stepwise/claude_direct.py` (new)

Translates claude `--output-format stream-json` events to ACP NDJSON on the fly as claude writes to stdout. The translation runs as a wrapper around the subprocess stdout.

| Claude stream-json event | ACP NDJSON equivalent |
|---|---|
| `{"type": "system", "subtype": "init", "session_id": "X"}` | `{"jsonrpc": "2.0", "id": 2, "result": {"sessionId": "X"}}` |
| `{"type": "assistant", "message": {"content": [{"type": "text", "text": "T"}]}}` | `{"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "X", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "T"}}}}` |
| `{"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "tool_use", "id": "I", "name": "N"}}}` | `{"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "X", "update": {"sessionUpdate": "tool_call", "toolCallId": "I", "title": "N", "kind": "tool_use", "status": "pending"}}}` |
| `{"type": "stream_event", "event": {"type": "content_block_stop"}}` | `{"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "X", "update": {"sessionUpdate": "tool_call_update", "toolCallId": "I", "status": "completed", "title": "..."}}}` |
| `{"type": "result", "cost_usd": C, "session_id": "X"}` | `{"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "X", "update": {"sessionUpdate": "usage_update", "cost": {"amount": C, "currency": "USD"}}}}` |

Also synthesize `initialize` request/response and `session/new` response at the start so the output file has the same preamble structure.

**Downstream parsers that must work unchanged:**
- `_parse_ndjson_events()` in server.py (UI streaming) — reads `agent_message_chunk`, `tool_call`, `tool_call_update`, `usage_update`
- `_extract_session_id()` in agent.py — reads `result.sessionId`
- `_extract_cost()` in agent.py — reads `usage_update.cost.amount`
- `_extract_final_text()` in agent.py — reads `agent_message_chunk.content.text`

### Task 1.2: ClaudeDirectBackend class

**File**: `src/stepwise/claude_direct.py`

Implements `AgentBackend` protocol (defined in `agent.py:98-117`):

```python
class ClaudeDirectBackend:
    """Backend using claude CLI directly for fork/resume operations.

    Writes ACP-compatible NDJSON so downstream code sees no difference.
    """

    def spawn(self, prompt: str, config: dict, context: ExecutionContext) -> AgentProcess:
        fork_from_session = config.get("_fork_from_session_id")
        resume_session = config.get("_session_uuid")

        cmd = ["claude", "--output-format", "stream-json", "--bare",
               "--dangerously-skip-permissions"]  # match acpx approve-all behavior

        if fork_from_session:
            cmd.extend(["--resume", fork_from_session, "--fork-session"])
        elif resume_session:
            cmd.extend(["--resume", resume_session])

        cmd.extend(["-p", "--file", str(prompt_file)])

        # Spawn subprocess, pipe stdout through ACP translator
        # Write translated NDJSON to output_path (same path pattern as acpx)
        ...

    def wait(self, process: AgentProcess, ...) -> AgentStatus:
        # Reuse same extraction functions as AcpxBackend
        # (output file is ACP-format, so _extract_cost etc. work unchanged)
        ...
```

**Key**: Extract shared parsing functions (`_extract_cost`, `_extract_session_id`, `_extract_final_text`, `_read_last_error`) from `AcpxBackend` into module-level helpers (or a base class) so both backends use identical parsing code.

### Task 1.3: New `_extract_claude_session_id()` function

**File**: `src/stepwise/agent.py`

```python
def _extract_claude_session_id(output_path: str) -> str | None:
    """Extract Claude session UUID from ACP NDJSON output.

    Only reads result.sessionId (from session/new or session/load responses),
    never params.sessionId (which may contain acpx_record_id).
    """
    with open(output_path) as f:
        for line in f:
            data = json.loads(line.strip())
            result = data.get("result", {})
            if isinstance(result, dict) and result.get("sessionId"):
                return result["sessionId"]
    return None
```

This reliably returns the claude UUID for both fresh sessions (`session/new` response) and loaded sessions (`session/load` response), because the response always has `result.sessionId = claude_uuid`.

### Task 1.4: Testing

- Unit: ACP translation for all 5 event types (exhaustive input/output pairs)
- Unit: `_extract_cost()`, `_extract_session_id()`, `_extract_final_text()` on translated output
- Unit: `_parse_ndjson_events()` produces identical UI events from translated output vs real acpx output
- Integration: mock claude subprocess → verify `HandoffEnvelope` and `executor_state` match acpx-produced equivalents

---

## Phase 2: Named Sessions in YAML/Models

**Goal**: Add `session` and `fork_from` as step-level fields with validation.

### Task 2.1: Model changes

**File**: `src/stepwise/models.py`

Add to `StepDefinition`:
```python
session: str | None = None
fork_from: str | None = None
```

Update `to_dict()` / `from_dict()` serialization.

### Task 2.2: YAML parsing

**File**: `src/stepwise/yaml_loader.py`

Add `session` and `fork_from` to step-level field parsing (alongside `outputs`, `inputs`, `after` — NOT in executor config).

### Task 2.3: Validation

**File**: `src/stepwise/yaml_loader.py` (or validation module)

Implement all 7 validation rules from the "Validation rules" section above.

### Task 2.4: Testing

- Parse valid flows with session/fork_from → model round-trips
- Parse invalid flows → all 7 validation rules produce clear errors
- Backward compat: flows without session still parse

---

## Phase 3: Engine — Named Session Lifecycle

**Goal**: Engine manages session creation, continuation, and forking based on step-level fields.

### Task 3.1: Session registry

**File**: `src/stepwise/engine.py`

Per-job session state tracking:

```python
@dataclass
class SessionState:
    name: str
    claude_uuid: str | None = None      # Claude session ID (for fork --resume)
    backend_type: str = "acpx"          # "acpx" or "claude_direct"
    fork_from: str | None = None
    agent: str = "claude"
    created: bool = False               # True after first step completes
```

Built on job start by scanning workflow steps. Updated after each step completes (capture claude_uuid from first step via `_extract_claude_session_id()`).

**Critical rule**: `claude_uuid` is set ONCE when the session is created (first step's output), never overwritten by subsequent steps. This avoids the acpx_record_id contamination on `session/load` continuations.

### Task 3.2: Session context injection

**File**: `src/stepwise/engine.py` (replaces lines 1788-1802 and duplicate at 3514-3522)

```python
if step_def.session:
    session_state = self._session_registry[step_def.session]
    session_ctx["_session_name"] = session_state.name
    session_ctx["_backend_type"] = session_state.backend_type
    session_ctx["_agent"] = session_state.agent

    if session_state.claude_uuid and not session_state.fork_from:
        # Continue existing session
        session_ctx["_session_uuid"] = session_state.claude_uuid
    elif session_state.fork_from and not session_state.created:
        # First step on forked session — pass parent UUID
        parent = self._session_registry[session_state.fork_from]
        session_ctx["_fork_from_session_id"] = parent.claude_uuid
        session_ctx["_backend_type"] = "claude_direct"
    elif session_state.created:
        # Subsequent step on forked session — continue via claude_direct
        session_ctx["_session_uuid"] = session_state.claude_uuid
        session_ctx["_backend_type"] = "claude_direct"

    # loop_prompt and circuit breaker (independent of session mechanism)
    if step_def.loop_prompt:
        session_ctx["loop_prompt"] = step_def.loop_prompt
    if step_def.max_continuous_attempts:
        session_ctx["max_continuous_attempts"] = step_def.max_continuous_attempts
```

### Task 3.3: Backend routing in AgentExecutor

**File**: `src/stepwise/agent.py`

`AgentExecutor` receives both backends:

```python
class AgentExecutor(Executor):
    def __init__(self, backend: AgentBackend, claude_direct_backend: AgentBackend | None = None, ...):
        self.backend = backend
        self.claude_direct = claude_direct_backend

    def _select_backend(self, config: dict) -> AgentBackend:
        if config.get("_backend_type") == "claude_direct":
            return self.claude_direct
        return self.backend
```

### Task 3.4: Session UUID capture after step completes

**File**: `src/stepwise/engine.py` (result processing)

```python
if step_def.session:
    state = self._session_registry[step_def.session]
    if not state.created:
        # First step on this session — capture claude UUID
        output_path = run.executor_state.get("output_path")
        if output_path:
            state.claude_uuid = _extract_claude_session_id(output_path)
        state.created = True
        # If this is a forked session, all future steps use claude_direct
        if state.fork_from:
            state.backend_type = "claude_direct"
```

### Task 3.5: Update SessionLockManager

**File**: `src/stepwise/engine.py` (lines 3167-3185, 3927-3937)

Key by session name instead of `_session_id` input:

```python
# In executor dispatch:
session_name = step_def.session  # from step definition
if session_name:
    lock = self._session_locks.get_lock(session_name)
    async with lock:
        result = await ...
```

### Task 3.6: Session cleanup

**File**: `src/stepwise/engine.py`

Update `_cleanup_job_sessions()`:
- acpx sessions: close via `acpx sessions close --name`
- claude_direct sessions: no active cleanup needed (no queue owner process)

### Task 3.7: Restart resilience

**File**: `src/stepwise/engine.py`

`_get_exec_ref_for_run()` must replay session context injection. Rebuild session registry from completed runs' executor_state on restart.

### Task 3.8: Testing

- Unit: session registry builds from workflow
- Unit: context injection for fresh/continued/forked sessions
- Unit: backend routing
- Unit: UUID capture on first step, no overwrite on subsequent
- Unit: lock manager keys by name
- Integration: multi-step named session flow
- Integration: fork flow creates independent session
- Integration: restart correctly reattaches

---

## Phase 4: Remove old session mechanism

### Task 4.1: Remove `_session_id` auto-emission

**File**: `src/stepwise/agent.py` (lines 1357-1362)

Delete the artifact injection of `_session_id`.

### Task 4.2: Remove `continue_session` from models

**File**: `src/stepwise/models.py`

Remove `continue_session: bool = False` from `StepDefinition`.

### Task 4.3: Restructure circuit breaker

**File**: `src/stepwise/agent.py`

Move `max_continuous_attempts` check outside the deleted `if self.continue_session:` block. With named sessions, the check is: "does this step have a session AND attempt > max_continuous_attempts?" When triggered, **fail the step** (not create a fresh anonymous session — that's a footgun nobody wants). The exit rule / engine escalation handles what happens next.

### Task 4.4: Remove from YAML parser

**File**: `src/stepwise/yaml_loader.py`

Remove `continue_session` from parsed fields. Emit error if encountered: "continue_session is removed, use session: <name>".

### Task 4.5: Clean up input resolution

**File**: `src/stepwise/engine.py`

Remove special handling of `_session_id` as reserved input.

### Task 4.6: Clean up AgentExecutor session logic

**File**: `src/stepwise/agent.py` (lines 1195-1215)

Remove `use_existing` / `_prev_session_name` / fallback session name generation / `_session_id` input extraction. Backend selection now handled by `_select_backend()`.

---

## Phase 5: Chain removal

### Task 5.1: Delete files

- `src/stepwise/context.py` (398 lines — entire module)
- `tests/test_context_chains.py` (1,157 lines — 68 tests)

### Task 5.2: Remove from models

**File**: `src/stepwise/models.py`

Delete: `ChainConfig`, `chain`/`chain_label` from `StepDefinition`, `chains` from `WorkflowDefinition`.

### Task 5.3: Remove from engine

**File**: `src/stepwise/engine.py`

Delete: `_compile_chain_context()`, chain skip logic (lines 1730-1737), chain context variable.

### Task 5.4: Remove from YAML loader

Delete `_parse_chains()`, chain/chain_label from step field parsing.

### Task 5.5: Remove from executors

**File**: `src/stepwise/executors.py`

Remove `chain_context` and `chain` from `ExecutionContext`.

### Task 5.6: Remove from agent executor

**File**: `src/stepwise/agent.py`

Remove chain context prompt prepending. Remove `capture_transcript = bool(context.chain)` (UI uses raw NDJSON, not transcripts — validated).

### Task 5.7: Remove from report/events

**File**: `src/stepwise/report.py` — remove chain badge rendering (~80 lines)
**File**: `src/stepwise/events.py` — remove `CHAIN_CONTEXT_COMPILED` constant

---

## Phase 6: Registry factory update

**File**: `src/stepwise/registry_factory.py`

Inject `ClaudeDirectBackend` into agent executor factory:

```python
acpx_backend = AcpxBackend(...)
claude_direct_backend = ClaudeDirectBackend(...)
registry.register("agent", lambda cfg: AgentExecutor(
    backend=acpx_backend,
    claude_direct_backend=claude_direct_backend,
    ...
))
```

---

## Phase 7: Flow migration + docs

### Task 7.1: Migrate all 11 vita flows

| Flow | Session mapping |
|------|----------------|
| `sleep` | 4 steps → `session: sleep` |
| `meeting-ingest` | 2 session groups → `session: speakers`, `session: analysis` |
| `plan` | plan→refine → `session: planning` |
| `plan-strong` | plan→revise→refine → `session: planning` |
| `plan-and-build` | 2 groups → `session: planning`, `session: implementation` |
| `fast-plan-implement` | plan→implement → `session: main` |
| `implement` | build→validate/fix → `session: building` |
| `test-fix` | fix→run-tests → `session: fixing` |
| `code-session` | work→instruct → `session: pair` |
| `report` | work→hub → `session: research` |
| `cabin-hackathon` | similar pattern |

For each: remove `continue_session: true`, remove `_session_id` input bindings, add `session: <name>`, add `agent: claude` where needed. Keep `loop_prompt` and `after` unchanged.

### Task 7.2: Update stepwise docs

| Doc | Changes |
|-----|---------|
| `writing-flows.md` | Replace session continuity section with named sessions + fork |
| `concepts.md` | Remove chain concept, add session/fork |
| `yaml-format.md` | Add `session`, `fork_from`; remove `chain`, `chain_label`, `continue_session`, `chains` |
| `executors.md` | Document `agent: claude` requirement for fork |
| `patterns.md` | Replace chain patterns with session/fork patterns |
| `flow-reference.md` | Update schema |
| `agent-session-continuity-proposal.md` | Archive or update |

### Task 7.3: Add fork example flow

`flows/examples/fork-review.flow.yaml` demonstrating the fork pattern.

---

## Implementation order

```
Phase 1 (ClaudeDirectBackend)     ← start immediately
    │
Phase 2 (YAML/Models)            ← parallel with Phase 1
    │
Phase 3 (Engine session lifecycle) ← depends on 1 + 2
    │
Phase 4 (Remove old mechanisms)   ← depends on 3
    │
Phase 5 (Chain removal)           ← parallel with 4
    │
Phase 6 (Registry factory)        ← depends on 1 + 3
    │
Phase 7 (Migration + docs)        ← depends on all above
```

---

## Risk assessment

| Risk | Severity | Mitigation |
|---|---|---|
| ACP translation edge cases (tool nesting, thinking blocks) | **High** | Exhaustive unit tests comparing against real acpx output |
| Claude UUID extraction returns wrong ID | **High** | Dedicated `_extract_claude_session_id()` that only reads `result.sessionId` |
| `claude --fork-session` behavior changes | **Low** | Adapter is thin; pin version in CI |
| Session UUID unavailable for fork (race) | **None** | DAG guarantees parent completes before fork launches |
| Server restart loses session registry | **Medium** | Rebuild from executor_state in DB |
| `for_each` + `session` needed | **Low** | Clear validation error; workaround is sub-flows |
| acpx adds fork support | **Low** | One-line swap in registry factory; translation layer removed |

---

## Validated assumptions

- ✅ `agent` field already exists on steps (stored in `ExecutorRef.config`, defaults to "claude")
- ✅ Chains unused in production (0/35 flows). 68 tests isolated in one file. Safe removal.
- ✅ UI session viewer reads raw NDJSON only (not transcript files). Transcript capture can be removed with chains.
- ✅ `loop_prompt` is independent of `continue_session` (works on `attempt > 1` unconditionally)
- ✅ `max_continuous_attempts` depends on `continue_session` block — needs restructuring
- ✅ Claude CLI `--fork-session` works. Vita already uses it in `scripts/telegram_parallel/`
- ✅ Claude stores sessions with `acp_session_id` as filename in `~/.claude/projects/`
- ✅ `acp_session_id` appears as `result.sessionId` in ACP NDJSON `session/new` response
- ✅ Real ACP NDJSON format validated against actual output files
- ✅ `_parse_ndjson_events()` handles 4 of 6 event types (2 silently dropped: `agent_thought_chunk`, `available_commands_update`)
