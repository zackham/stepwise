> ⚠️ **Design Proposal** — This describes planned behavior that may not be fully implemented yet. Do not rely on this as current documentation.

# Agent Session Continuity & Optional Inputs

**Date:** 2026-03-17
**Status:** Implemented (Phases 1-4 landed on master, 2026-03-17)

---

## Motivation

Agent executors today are fire-and-forget: spawn a session, run to completion, extract output. Each loop iteration or downstream step starts a fresh session with no conversational memory. The chain context system (M7a) compensates by capturing transcripts and re-injecting them as XML, but this is fundamentally wasteful — you're paying full input tokens to re-serialize context the model already saw, and the injection is lossy (tool calls summarized, thinking truncated).

The core insight: agents running inside a DAG should be able to **continue conversations**, not just start new ones. This maps to how you'd actually use an agent interactively — you don't close your session and re-explain everything after a test failure.

---

## Problem 1: Feeding data backward across loops

Consider an implement → test → implement loop. After tests fail, you want to pass failure details back to the implement step. Today this is impossible — the implement step depends on test output, and test depends on implement output. The readiness checker sees a cycle and deadlocks.

The YAML validator already handles this (cycle detection excludes loop back-edges), but the runtime readiness check doesn't have a corresponding exception. The first iteration never starts.

### Proposed solution: Optional inputs

Inspired by Objective-C's weak references. An optional input is a "weak" dependency — if the data is there, use it; if not, the value is `None`. The step's own logic decides what to do with a missing value. No waiting, no cycle.

```yaml
steps:
  generate:
    run: |
      if [ -z "$score" ]; then effort=50;
      else effort=$(echo "$score" | awk '{printf "%d", (1-$1)*100}'); fi
      echo "{\"text\": \"draft effort=$effort\"}"
    inputs:
      text: $job.prompt
      score:
        from: review.score
        optional: true
    outputs: [text]

  review:
    executor: llm
    prompt: "Review: $text"
    inputs:
      text: generate.text
    outputs: [score, feedback]
    exits:
      - when: "float(outputs['score']) >= 0.8"
        action: advance
      - when: "attempt < 3"
        action: loop
        target: generate
```

**Why this over back-edge detection:** It's explicit (the flow author says "this input is optional"), general (useful beyond loops — first-run defaults, optional enrichment, graceful degradation), and simple (one bit on InputBinding, one `if` in readiness). No graph analysis heuristics.

**YAML syntax:** Required inputs stay as bare strings. Optional inputs use a dict with `from` + `optional: true`. This is consistent with the existing `any_of` dict syntax. One format — no sugar, no ambiguity. These flows are primarily authored by LLMs, so minimizing documentation and parsing burden matters more than keystroke savings.

### None handling specification

When an optional input resolves to `None`:
- **In prompt templates** (`$var` interpolation): renders as empty string `""`. The `loop_prompt` / `prompt` distinction handles the structural difference — you don't need conditional template logic.
- **In expression evaluation** (exit rules, `when` conditions): `None` is a first-class value. Expressions can test `score is None` or `score is not None`. Comparing `None` with `>`, `<`, `float()` etc. raises an eval error that fails the step — this is a flow authoring bug, not a runtime edge case.
- **In script executors** (`run:`): environment variable is unset (not set to "None" or "null"). Scripts use standard shell idioms: `if [ -z "$score" ]; then ...`.
- **`any_of` + `optional`**: Allowed. Means "try to get one of these, but if none are available, resolve to `None` and proceed." Useful for graceful degradation.

### Cycle detection update

The YAML validator's cycle detection must be updated: a cycle in the dependency graph is **valid** if every cycle contains at least one `optional: true` edge. Without this, the validator rejects flows that the engine would handle correctly.

---

## Problem 2: Session continuity on loop-back

The implement → test → implement loop is the strongest motivating case. Today each iteration starts a fresh agent session. With session continuity, the agent keeps its full conversational context — it wrote the code, it knows what files it touched, what tradeoffs it made. You just append "these tests failed: {failures} — fix them" as a new message. That's 200 tokens instead of a 50k-token context re-injection.

Prompt caching amplifies this: the entire prior conversation is cached, so the continued turn is nearly free on input tokens.

### Proposed solution: `continue_session` flag

```yaml
steps:
  implement:
    executor: agent
    prompt: "Implement this feature: $spec"
    loop_prompt: "These tests failed:\n$failures\nPlease fix the issues."
    continue_session: true
    inputs:
      spec: $job.spec
      failures:
        from: run-tests.failures
        optional: true
    outputs: [result]

  run-tests:
    run: |
      npm test 2>&1 | tail -50
      # outputs {passed: bool, failures: string}
    inputs:
      result: implement.result
    outputs: [passed, failures]
    exits:
      - when: "outputs['passed'] == true"
        action: advance
      - when: "attempt < 3"
        action: loop
        target: implement
```

**Behavior:**
- First run (attempt 1): creates session `step-implement`, sends `prompt`
- Loop-back (attempt 2+): continues session `step-implement`, sends `loop_prompt` (falls back to `prompt` if `loop_prompt` not defined)
- Session name drops the `-{attempt}` suffix so it persists across iterations

**`loop_prompt`:** On first run you want "Implement: $spec". On loop-back you want "Fix these failures: $failures". These are structurally different prompts. The `loop_prompt` field handles this cleanly — used when `continue_session` is true and attempt > 1. Simple, explicit, no conditional logic in templates.

**Implementation:** `AcpxBackend.spawn()` checks if the session already exists. If so, it sends a new prompt to the existing session instead of creating one. The session name is stored in executor_state and carried across attempts.

### Chain context interaction

When `continue_session` is true, **disable M7a chain context injection** for that step. The agent already has its full conversation history in the live session — prepending `<prior_context>` XML would duplicate the context and waste tokens. The engine should skip `_compile_chain_context()` for continued sessions.

Chain context remains useful for steps that don't use session continuity (different agents, different working directories, cost isolation).

### Failure modes and mitigations

**Context window exhaustion.** Continued sessions grow monotonically. Unlike chain context (which has drop_oldest/drop_middle overflow), the live session's context is managed by the agent provider, not the engine.

Mitigation: `max_continuous_attempts` parameter on steps with `continue_session: true`. After N iterations, force a fresh session with M7a chain context as backfill. This is the circuit breaker — it prevents runaway loops from accumulating unbounded context. Default: no limit (author sets it explicitly when loops could be long-running).

Additionally, expose `_session_tokens` as a readable value in exit rule expressions so flows can make context-aware decisions: `when: "session_tokens > 100000"` → escalate.

**Agent crash between iterations.** If the agent process dies, the session may be unrecoverable. Before continuing a session, `AcpxBackend` must health-check the session (does it still exist? is it responsive?). If the session is gone:
1. Fall back to fresh session + M7a chain context backfill
2. Log the fallback for observability
3. The step still works — just less efficiently

This means `continue_session` steps must work in both modes (continued and fresh-with-context). The `loop_prompt` with `$var` interpolation handles this naturally — the prompt is the same regardless of whether it's continuing or starting fresh.

**Hallucination reinforcement.** Long debugging loops can reinforce stuck patterns — the agent keeps trying the same broken approach because the full failed history is in context. The `max_continuous_attempts` circuit breaker addresses this: after N failed iterations, start fresh so the agent gets a clean slate (with summarized prior context via M7a, not the full conversation).

**Output extraction.** Currently outputs are extracted from per-attempt NDJSON files. With continued sessions, the NDJSON file grows across attempts. Track the byte offset at the start of each attempt so output extraction reads only the current attempt's output, not stale data from prior iterations.

---

## Problem 3: Session sharing across steps

Sometimes multiple steps are really phases of one logical conversation: plan → implement → validate → simplify → commit. You want the agent to accumulate context across all phases — it planned the work, so it has full context when implementing; it wrote the code, so it has full context when validating.

The step boundaries exist for validation gates and data flow control, not for agent isolation. Each step can have its own exit rules, retry logic, and downstream dependencies, but the conversation is continuous.

### Proposed solution: Session ID as typed data (replaces named sessions)

~~Original approach: `session: <name>` field creating a global session namespace.~~

**Revised approach (post-council review):** Session IDs flow through the existing InputBinding system as typed data. No global namespace, no side-channel state.

Every agent step with `continue_session: true` automatically emits a `_session_id` output field containing the acpx session identifier. Downstream steps can reference this session to continue the conversation:

```yaml
steps:
  plan:
    executor: agent
    prompt: "Plan the implementation for: $spec"
    continue_session: true
    inputs:
      spec: $job.spec
    outputs: [plan]
    # automatically emits _session_id

  implement:
    executor: agent
    prompt: "Now implement the plan above."
    continue_session: true
    inputs:
      plan: plan.plan
      _session_id: plan._session_id
    outputs: [result]
    # continues plan's session, emits same _session_id

  validate:
    executor: agent
    prompt: "Review what you implemented for correctness."
    continue_session: true
    inputs:
      result: implement.result
      _session_id: implement._session_id
    outputs: [approved]
    # continues the same session through all three steps
```

**Why this over named sessions:**
- **Preserves DAG invariants.** Session state transfer is explicit in the dependency graph, not a hidden side-channel. You can look at a step's inputs and know exactly what session it continues.
- **No global namespace.** No risk of collision between workflows or between unrelated steps that accidentally use the same session name.
- **Engine readiness works naturally.** Step B depends on step A's `_session_id` output → B can't start until A completes. No special-case ordering logic needed for the sequential case.
- **Composable.** A step's `_session_id` is just data. It can flow through `any_of`, be passed into sub-flows as an input, or be ignored.

**Behavior:**
- If a step has `continue_session: true` and receives a `_session_id` input, it continues that session
- If a step has `continue_session: true` and does NOT receive a `_session_id` input, it creates a new session and emits the ID
- `_session_id` is a reserved output field name, automatically populated by the agent executor — flow authors don't declare it in `outputs:`

### Session as a lockable resource

The fan-out pattern motivates treating sessions as lockable resources:

```yaml
steps:
  enumerate:
    run: "echo '{\"features\": [\"auth\", \"search\", \"export\"]}'"
    outputs: [features]

  plan:
    executor: agent
    for_each: enumerate.features
    prompt: "Plan implementation for: $item"
    outputs: [plan]

  implement:
    executor: agent
    for_each: plan
    prompt: "Implement this plan: $plan"
    continue_session: true
    inputs:
      plan: plan.plan
      _session_id:
        from: coding-session._session_id
        optional: true
    outputs: [result]

  coding-session:
    executor: agent
    prompt: "You'll be implementing several features. Starting workspace setup."
    continue_session: true
    outputs: [ready]
```

In this pattern, multiple `implement` iterations fan out from `plan` and all feed into a single agent session. We genuinely don't care what order they execute — each plan is independent — but they can't interleave because agent sessions are single-threaded conversations.

**The engine treats the session as a lockable resource:**
- Before sending a prompt to a continued session, the engine acquires a lock on that session ID
- The lock is released when the step completes (output extracted)
- If the lock is held, the step is not ready (waits in the readiness check alongside dep checks)
- Lock acquisition order is **deterministic** (alphabetical by step name, then by for_each index) so runs are reproducible even though the author doesn't care about order

This is not special-case logic — it's the same category as "don't exceed max concurrent steps." The session lock is just another readiness constraint.

**Future: fork-and-reconcile.** The serial lock is a pragmatic answer for current agent protocols. If agents gain fork-and-reconcile capabilities (like we've built for Telegram messages in vita), the lock could become a semaphore or disappear entirely. The abstraction supports this evolution — the session ID is just data, and the locking policy is an engine concern, not a flow concern.

### Design tension — composability vs coupling

Steps sharing a session via `_session_id` are coupled at the conversation level. If you rerun step 2 in isolation, it's continuing a session that has step 1's context. This is fine for the "phases of one conversation" pattern, but it means these steps aren't independently composable. This is a deliberate tradeoff — the flow author opts into it by wiring `_session_id` through their inputs.

### Relationship to chain context (M7a)

Session sharing via `_session_id` subsumes M7a chain context for the "phases of one agent's work" pattern. Chain context remains valuable when:
- Steps use **different agents** (can't share a session)
- Steps need **different working directories** (session is directory-scoped)
- You want **cost isolation** between steps (separate sessions = separate billing)
- You want **independent retry** without conversation coupling

When a step receives `_session_id` and `continue_session` is true, skip M7a injection for that step (same rule as loop-back continuity — don't double-inject context).

---

## Summary of changes

Layered, each independently useful:

| Layer | What | Enables |
|---|---|---|
| **1. Optional inputs** | `InputBinding.optional` flag. Readiness skips optional deps. Resolve returns `None` for missing. Cycle detection allows cycles with optional edges. | Feeding data backward across loops. First-run defaults. Graceful degradation. |
| **2. `continue_session`** | AcpxBackend reuses session name across attempts (drops `-{attempt}` suffix). AgentExecutor checks for existing session before spawning. Health check + M7a fallback on crash. | Token-efficient loop-back. Agent retains full context across iterations. |
| **3. `loop_prompt`** | AgentExecutor picks prompt based on attempt + continue_session. | Structurally different prompts for first run vs loop-back without template conditionals. |
| **4. `_session_id` as typed output** | Agent steps emit session ID. Downstream steps continue sessions via InputBinding. Engine acquires session lock before sending prompts. | Multi-phase agent workflows as one conversation. Fan-out-to-single-session patterns. |

Each layer builds on the prior. Optional inputs is the foundation.

---

## Implementation order

Based on council review (Claude Opus 4.6, Gemini 3.1 Pro, Grok 4.1):

### Phase 1: Optional inputs (Layer 1)

Smallest change, highest standalone value, unblocks everything else.

Changes:
- Add `optional: bool = False` to `InputBinding` model
- Update `_is_step_ready()` to skip optional deps
- Update `_resolve_inputs()` to return `None` for missing optional deps
- Update cycle detection in YAML validator to allow cycles with optional edges
- Update expression evaluator to handle `None` (comparisons, truthiness)
- Update prompt template interpolation: `None` → empty string
- Update script executor: `None` → unset env var

### Phase 2: Session continuity (Layers 2 + 3)

Build together — `loop_prompt` is trivial once `continue_session` exists.

Changes:
- Add `continue_session: bool = False` and `loop_prompt: str | None` to step definition model
- Modify `AcpxBackend.spawn()`: when `continue_session` is true, use `step-{step_name}` (no attempt suffix). Check if session exists → continue vs create.
- Add session health check before continuing (is session alive?)
- Add M7a fallback path: if session is gone, start fresh with chain context
- Skip chain context compilation for continued sessions
- Add `max_continuous_attempts` parameter (optional circuit breaker)
- Track NDJSON byte offsets per attempt for output extraction
- Prompt selection: `loop_prompt` when `continue_session and attempt > 1`, else `prompt`

### Phase 3: Session ID as data (Layer 4)

Changes:
- Agent executor automatically emits `_session_id` output field when `continue_session` is true
- When step receives `_session_id` input + `continue_session`, continue that session instead of creating new
- Session lock manager in engine: acquire before prompt send, release on step completion
- Deterministic lock acquisition order (alphabetical step name, then for_each index)
- Skip M7a for steps continuing a received session
- Validate at YAML load: steps sharing `_session_id` must use the same agent type
- Validate at YAML load: steps sharing `_session_id` must use the same working directory

---

## Resolved questions (from council review)

1. **Session cleanup:** Tied to job lifecycle. When a workflow completes (success or failure), close all sessions created during that run via acpx. No TTL, no reference counting — explicit cleanup on job termination.

2. **Session + different agents:** Hard error at YAML validation time. All steps that could share a session (via `_session_id` wiring) must use the same agent. A session is a conversation with one agent.

3. **Session + different working directories:** Hard error at YAML validation time. Agent sessions are directory-scoped. Steps sharing a session must share a working directory.

4. **`loop_prompt` vs script-generated prompts:** `loop_prompt` is sufficient for the common case. Complex prompt construction should use a separate script step upstream that outputs the formatted prompt, fed to the agent step via inputs. Defer `prompt_command:` — it's a separate feature.

5. **Session output as data:** Yes — `_session_id` is automatically emitted. This is the foundation of cross-step session sharing (replaces the original named sessions approach).

6. **Interaction with `emit_flow`:** Sub-flows do NOT inherit parent sessions. Session scope is explicit — pass `_session_id` as a typed input to the sub-flow if you want continuity across the boundary.

7. **Interaction with decorators:** If a continued session times out, start a fresh session on retry. Timeouts mean the agent is stuck — continuing the stuck context doesn't help. Fresh session + M7a chain context backfill gives a clean slate.

---

## Council review notes

Reviewed 2026-03-17 by Claude Opus 4.6, Gemini 3.1 Pro, Grok 4.1.

**Key revision:** Replaced Layer 4 (named sessions via `session: <name>` field) with session-ID-as-typed-data approach. Original named sessions created a global mutable namespace that broke DAG invariants — data dependencies tracked in InputBinding, state dependencies hidden in session names. The revised approach keeps everything in the binding system.

**Gemini 3.1 Pro** provided the strongest argument for the revision: named sessions are "an architectural anti-pattern for a DAG" because they introduce a second, implicit dependency graph alongside the explicit one. Session ID as data preserves the single-graph invariant.

**Session locking** was added post-council to handle the fan-out pattern (multiple independent steps feeding into one session). The engine treats the session as a lockable resource with deterministic acquisition order, keeping runs reproducible without requiring the author to specify an arbitrary ordering.
