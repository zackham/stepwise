# Agent Session Continuity

How agent steps maintain conversational context across loop iterations and across steps — reducing token waste and improving agent performance on multi-turn workflows.

*Originally a design proposal (2026-03-17). Phases 1-4 are implemented on master.*

---

## Overview

Agent steps can continue conversations instead of starting fresh. This means an agent that wrote code retains full context when fixing test failures, and multiple steps can share a single agent session for phased work (plan, implement, validate).

Three features work together:

| Feature | What | YAML |
|---|---|---|
| **Optional inputs** | Weak dependencies that resolve to `None` if unavailable | `optional: true` on input binding |
| **Session continuity** | Reuse agent session across loop iterations | `continue_session: true` on step |
| **Cross-step sessions** | Share a session across multiple steps via `_session_id` | `_session_id` input binding |

Each is independently useful. Optional inputs enable loops with feedback. Session continuity makes those loops token-efficient. Cross-step sessions extend that efficiency across step boundaries.

---

## Optional Inputs

An optional input is a "weak" dependency — if the data exists, use it; if not, the value is `None`. The step proceeds either way.

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

On the first iteration, `score` is `None` (review hasn't run yet). On subsequent iterations, `score` contains the review output. The cycle is valid because the optional edge breaks the hard dependency.

### None handling

| Context | Behavior |
|---|---|
| Prompt templates (`$var`) | Renders as empty string `""` |
| Exit rule expressions | `None` is first-class; test with `score is None` |
| Script executors (`run:`) | Environment variable is unset (not "None") |

Comparing `None` with `>`, `<`, `float()`, etc. raises an eval error — this is a flow authoring bug.

`any_of` + `optional` is allowed: "try to get one of these, but if none are available, resolve to `None`."

### Cycle detection

A cycle in the dependency graph is valid if every cycle contains at least one `optional: true` edge. The validator enforces this.

---

## Session Continuity on Loop-Back

When `continue_session: true`, the agent keeps its full conversational context across loop iterations. Instead of re-explaining everything after a test failure, you append "these tests failed — fix them" as a new message.

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
- First run (attempt 1): creates a new session, sends `prompt`
- Loop-back (attempt 2+): continues the existing session, sends `loop_prompt` (falls back to `prompt` if `loop_prompt` is not defined)

**`loop_prompt`:** On first run you want "Implement: $spec". On loop-back you want "Fix these failures: $failures". The `loop_prompt` field handles structurally different prompts for each case.

### Chain context interaction

When `continue_session` is true, chain context injection (M7a) is disabled for that step. The agent already has its full conversation history — prepending prior context XML would duplicate it and waste tokens.

### Failure modes

**Context window exhaustion.** Continued sessions grow monotonically. Use exit rules with attempt limits to prevent unbounded loops.

**Agent crash between iterations.** If the session dies, the engine falls back to a fresh session with chain context backfill. The step still works, just less efficiently.

**Hallucination reinforcement.** Long debugging loops can reinforce stuck patterns. Attempt limits provide a circuit breaker — after N failed iterations, the flow can escalate or take a different path.

---

## Cross-Step Sessions via `_session_id`

Multiple steps can share a single agent session by wiring `_session_id` through the input system. Every agent step with `continue_session: true` automatically emits a `_session_id` output containing the session identifier.

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
    # continues plan's session

  validate:
    executor: agent
    prompt: "Review what you implemented for correctness."
    continue_session: true
    inputs:
      result: implement.result
      _session_id: implement._session_id
    outputs: [approved]
    # same session through all three steps
```

The agent accumulates context across all phases — it planned the work, so it has full context when implementing; it wrote the code, so it has full context when validating.

**Key properties:**
- Session state is explicit in the dependency graph (no hidden side-channels)
- `_session_id` is a reserved output field, automatically populated — don't declare it in `outputs:`
- If a step has `continue_session: true` and receives `_session_id`, it continues that session
- If a step has `continue_session: true` without `_session_id` input, it creates a new session

### Session locking

When multiple steps reference the same session (e.g., fan-out steps feeding a single session), the engine treats the session as a lockable resource. One step at a time can use a session. Lock acquisition order is deterministic (alphabetical by step name, then by for-each index).

### Constraints

Steps sharing a session (via `_session_id` wiring) must use the same agent type and the same working directory. These are validated at YAML load time.

### When to use chain context instead

Session sharing via `_session_id` doesn't fit every case. Use chain context (M7a) when:
- Steps use **different agents** (can't share a session)
- Steps need **different working directories**
- You want **cost isolation** between steps
- You want **independent retry** without conversation coupling

---

## Session Cleanup

Sessions are tied to job lifecycle. When a job completes (success or failure), all sessions created during that run are closed. No TTL, no reference counting — explicit cleanup on job termination.

---

See [Writing Flows](writing-flows.md) for YAML syntax. See [Executors](executors.md) for the full executor reference.
