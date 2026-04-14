---
title: "Implementation Plan: Fix after_resolved deps not invalidating across loop iterations"
date: "2026-04-13T12:00:00Z"
project: stepwise
tags: [implementation, plan, bugfix]
status: active
---

# Fix: `after_resolved` deps don't invalidate across loop iterations

## Overview

When a step uses `after_resolved:` deps in a looping flow, deps that change terminal state between iterations (COMPLETED→SKIPPED or SKIPPED→COMPLETED) are not detected as stale. The downstream step never re-runs because `_skip_when_blocked_steps` refuses to re-evaluate steps that already have any run, and `_resolve_inputs` records the wrong dep run for after_resolved deps that flip from COMPLETED to SKIPPED.

## Root Cause Trace

Concrete walkthrough with the podcast-panel flow (job-c3535993):

**Iteration 1 state (works correctly):**
- `candidate-adrian`: COMPLETED (run A1), `candidate-olivia`: COMPLETED (run O1)
- `candidate-ryan`: SKIPPED (run R1), `candidate-emily`: SKIPPED (run E1)
- `collect-candidates`: COMPLETED (run CC1), `dep_run_ids = {c-adrian: A1, c-olivia: O1, c-ryan: R1, c-emily: E1}`
- Loop fires from `finalize-turn` → `moderator-turn`

**Iteration 2 (fails):**
- `moderator-turn` re-runs (MT2), `parse-turn` re-runs (PT2)
- `candidate-adrian`: COMPLETED (A2), `candidate-emily`: COMPLETED (EM2)
- `candidate-olivia`: when=False, but still has O1 (COMPLETED from iter 1) — **never re-SKIPPED**
- `candidate-ryan`: when=False, still has R1 (SKIPPED from iter 1) — R1 appears valid to `_is_dep_resolved`

**Three failure points converge:**

1. **`_skip_when_blocked_steps` (engine.py:1990-1992):** `if latest is not None: continue` — skips `candidate-olivia` because it has run O1, even though O1 is stale (its dep `parse-turn` has a newer run PT2). No new SKIPPED run is created.

2. **`_is_dep_resolved` (engine.py:1742-1757):** For `candidate-olivia`, `_is_dep_settled` returns False (O1's dep PT1 is superseded by PT2 → O1 not current). Then `latest_run.status == SKIPPED` check fails (O1 is COMPLETED). Returns **False** — `collect-candidates` can never become ready.

3. **`_resolve_inputs` (engine.py:3265-3273):** If `collect-candidates` somehow did re-run, `latest_completed_run(candidate-olivia)` returns O1 (the stale COMPLETED run), not the new SKIPPED run. Provenance would be wrong, causing `_is_current` to fail on the next iteration.

**Fix strategy:** Fix the source (#1) so #2 and #3 resolve naturally. Create new SKIPPED runs for stale steps, and record correct provenance.

## Requirements

### R1: Re-skip stale conditional steps across loop iterations
**Acceptance criteria:**
- When a loop fires and upstream deps produce new runs, conditional steps whose latest run is stale (COMPLETED but not current, or SKIPPED with deps changed) have their `when` condition re-evaluated.
- If `when` is False, a new SKIPPED run is created with the correct `dep_run_ids` and `attempt` number.
- If `when` is True, no SKIPPED run is created — the step is left for `_dispatch_ready_steps` to pick up and re-execute.
- Verified by: new test `test_after_resolved_loop_invalidation` where `merge` runs on both iterations.

### R2: Correct dep provenance for after_resolved deps
**Acceptance criteria:**
- `_resolve_inputs` records the latest terminal run (COMPLETED or SKIPPED) for `after_resolved` deps, using `store.latest_run()` (ordered by attempt DESC, store.py:775) rather than `store.latest_completed_run()`.
- When a dep was COMPLETED in iter 1 and SKIPPED in iter 2, the SKIPPED run (higher attempt) is recorded as the dep_run_id.
- Verified by: `_is_current` on the downstream step's new run correctly returns True (SKIPPED dep's latest_run matches dep_run_ids).

### R3: Provenance tracking on SKIPPED runs
**Acceptance criteria:**
- Eagerly-created SKIPPED runs (from `_skip_when_blocked_steps`) include `dep_run_ids` from the `_resolve_inputs` call.
- The `attempt` field uses `store.next_attempt()` (store.py:789) instead of hardcoded `1`, so re-skipped runs get correct attempt numbers.
- Verified by: inspecting `dep_run_ids` on SKIPPED runs in the new test.

### R4: No regression in existing behavior
**Acceptance criteria:**
- All 6 existing tests in `tests/test_after_resolved.py` pass unchanged.
- All tests in `tests/test_branching.py` pass (loop behavior).
- All tests in `tests/test_runner.py` pass (general engine behavior).
- Full test suite passes: `uv run pytest tests/ -x -q --ignore=tests/test_delegation.py --ignore=tests/test_editor_api.py`

## Assumptions

1. **`_skip_when_blocked_steps` is the only place that creates eager SKIPPED runs mid-execution.**
   - Verified: `_settle_unstarted_steps` (engine.py:2126-2138) creates SKIPPED runs at settlement time only, gated by `latest is None` — end-of-job, never mid-loop.
   - `_skip_when_blocked_steps` (engine.py:1962-2086) is the exclusive eager path. Called from `_dispatch_ready` (engine.py:4689) and legacy tick loop (engine.py:1484).

2. **SKIPPED runs currently don't record `dep_run_ids`.**
   - Verified: engine.py:2066-2071 — `StepRun` constructor omits `dep_run_ids`. The `_resolve_inputs` call at line 2056 returns them as the second element (`inputs, _, presence`), but the `_` discards them.

3. **`latest_run` returns runs ordered by attempt DESC.**
   - Verified: store.py:775 — `ORDER BY attempt DESC LIMIT 1`. A new SKIPPED run (via `next_attempt`, store.py:789 = `MAX(attempt) + 1`) will be returned as `latest_run`.

4. **`_resolve_inputs` for `after_resolved` prefers `latest_completed_run` over SKIPPED.**
   - Verified: engine.py:3265 — `latest = self.store.latest_completed_run(...)` is checked first. Only if None does it fall back to SKIPPED (line 3270-3273). When a dep has COMPLETED (O1, attempt 1) + SKIPPED (S_O2, attempt 2), `latest_completed_run` returns O1 (the stale run).

5. **`_dispatch_ready` calls `_skip_when_blocked_steps` only when nothing is ready, then re-checks.**
   - Verified: engine.py:4688-4694 (AsyncEngine): `if not ready: while self._skip_when_blocked_steps(job): ...` then `ready = self._find_ready(job)`.
   - Verified: engine.py:1481-1486 (legacy Engine): same pattern within tick loop.
   - This ordering means: `_is_dep_resolved` returns False on the first pass (stale dep), `_skip_when_blocked_steps` creates the SKIPPED run, second `_find_ready` sees the dep resolved. No changes needed to `_is_dep_resolved`.

6. **Exit rule `attempt` and `max_iterations` are unaffected by SKIPPED runs.**
   - Verified: engine.py:3299 — `_evaluate_rule(rule, artifact, attempt=run.attempt)` uses the completing step's own attempt, not dep attempts.
   - Verified: engine.py:3316 — `max_iterations` uses `completed_run_count` (store.py:798), which filters `status = COMPLETED`. SKIPPED runs don't count.

7. **`_is_current` for after_resolved SKIPPED deps checks `latest_run.id == source_run.id`.**
   - Verified: engine.py:1838-1843 — `if source_run.status == StepRunStatus.SKIPPED: latest_dep = self.store.latest_run(...); if latest_dep.id == source_run.id: continue`. After the fix, when a new SKIPPED run supersedes the old one, the old dep_run_id no longer matches `latest_run`, so `_is_current` correctly returns False.

## Out of Scope

- **`_is_dep_resolved` (engine.py:1742-1757):** No changes needed. After `_skip_when_blocked_steps` creates fresh SKIPPED runs, `latest_run.status == SKIPPED` correctly returns True. The ordering in `_dispatch_ready` (assumption 5) guarantees this.

- **`_is_current` (engine.py:1762-1873):** No changes needed. The after_resolved SKIPPED provenance check (line 1838-1843) works correctly once (a) dep_run_ids are recorded on SKIPPED runs and (b) `_resolve_inputs` records the latest terminal run. Verified by assumption 7.

- **`_settle_unstarted_steps` (engine.py:2126-2138):** Settlement is end-of-job. The bug is mid-execution; settlement is unaffected.

- **Validator changes:** This is a runtime engine bug, not a static validation gap.

- **`_is_step_ready` (engine.py:1536-1694):** No changes needed. The settled terminal guard (line 1582-1590) uses `latest_completed_run`, which would detect newer COMPLETED deps (triggering re-evaluation). For deps that flip to SKIPPED, the fix to `_skip_when_blocked_steps` handles re-skipping before `_is_step_ready` is called.

## Architecture

The fix makes 3 localized changes to methods in `src/stepwise/engine.py`, all within the shared `Engine` base class (inherited by both `AsyncEngine` and the legacy `Engine`):

| Change | Method | Lines | What changes |
|--------|--------|-------|-------------|
| 1a. Capture dep_run_ids | `_skip_when_blocked_steps` | 2056 | `_, _` → `_, dep_run_ids` |
| 1b. Record on SKIPPED | `_skip_when_blocked_steps` | 2066-2068 | Add `dep_run_ids=dep_run_ids`, fix `attempt` |
| 2. Stale run gate | `_skip_when_blocked_steps` | 1989-1992 | Replace blanket `continue` with staleness check |
| 3. Dep recording | `_resolve_inputs` | 3264-3273 | Use `latest_run` for after_resolved deps |

**No new methods, classes, or files.** The fix follows existing engine patterns:
- `dep_run_ids` tracking: same pattern as `_prepare_step_run` (engine.py:2306-2315)
- `next_attempt`: same call as `_prepare_step_run` (engine.py:2305)
- `_is_current` for staleness: same pattern as `_is_step_ready` (engine.py:1571-1573)

**Data flow after fix:**
```
Loop fires → target step re-runs → deps get new runs →
_dispatch_ready() →
  _find_ready() → nothing ready (candidate-olivia stale, blocks after_resolved) →
  _skip_when_blocked_steps() →
    candidate-olivia: COMPLETED O1, _is_current(O1)=False → stale → re-evaluate when →
      when=False → create SKIPPED S_O2 with dep_run_ids={parse-turn: PT2} →
    candidate-ryan: SKIPPED R1, dep_run_ids={parse-turn: PT1} vs latest PT2 → stale → re-evaluate →
      when=False → create SKIPPED R2 with dep_run_ids={parse-turn: PT2} →
  _find_ready() → collect-candidates:
    _is_dep_resolved(candidate-olivia) → latest_run=S_O2, status=SKIPPED → True ✓
    _is_dep_resolved(candidate-ryan) → latest_run=R2, status=SKIPPED → True ✓
    → ready → _launch()
```

## Implementation Steps

### Step 1: Capture `dep_run_ids` from `_resolve_inputs` in `_skip_when_blocked_steps`
**File:** `src/stepwise/engine.py`, line 2056
**Change:** Rename the discarded second return value from `_` to `dep_run_ids`.
**Exact edit:**
```
- inputs, _, presence = self._resolve_inputs(job, step_def)
+ inputs, dep_run_ids, presence = self._resolve_inputs(job, step_def)
```
**Why:** `dep_run_ids` is needed for both Step 2 (recording on SKIPPED runs) and Step 3 (staleness detection). The value is already computed — we just need to stop discarding it.
**Depends on:** Nothing. No behavioral change.
**Verify:** `uv run pytest tests/test_after_resolved.py -x -q` (passes unchanged)

### Step 2: Record `dep_run_ids` and correct `attempt` on eager SKIPPED runs
**File:** `src/stepwise/engine.py`, lines 2066-2068
**Change:** Add `dep_run_ids=dep_run_ids` to the `StepRun` constructor. Replace hardcoded `attempt=1` with `attempt=self.store.next_attempt(job.id, step_name)`.
**Exact edit:**
```
- run = StepRun(
-     id=_gen_id("run"), job_id=job.id, step_name=step_name,
-     attempt=1, status=StepRunStatus.SKIPPED,
-     error="when condition false",
-     started_at=_now(), completed_at=_now(),
- )
+ run = StepRun(
+     id=_gen_id("run"), job_id=job.id, step_name=step_name,
+     attempt=self.store.next_attempt(job.id, step_name),
+     status=StepRunStatus.SKIPPED,
+     dep_run_ids=dep_run_ids,
+     error="when condition false",
+     started_at=_now(), completed_at=_now(),
+ )
```
**Why:** `dep_run_ids` enables staleness detection on subsequent iterations. `next_attempt` (store.py:789 — `MAX(attempt) + 1`) ensures the new SKIPPED run supersedes prior runs in `latest_run` ordering (store.py:775 — `ORDER BY attempt DESC`).
**Depends on:** Step 1 (dep_run_ids must be in scope).
**Verify:** `uv run pytest tests/test_after_resolved.py -x -q` (passes — first-pass behavior unchanged since `next_attempt` returns 1 when no prior runs exist).

### Step 3: Replace blanket "has any run" gate with staleness-aware check
**File:** `src/stepwise/engine.py`, lines 1989-1992
**Change:** Replace the 3-line block with a staleness-aware check that allows stale COMPLETED and SKIPPED runs to fall through for when-condition re-evaluation.
**Exact replacement of lines 1989-1992:**
```python
# Skip if already has a run — UNLESS the run is stale (deps changed
# since it was created). Stale runs need when-condition re-evaluation
# so that after_resolved downstream steps see fresh SKIPPED/COMPLETED state.
latest = self.store.latest_run(job.id, step_name)
if latest is not None:
    # Active or failed: don't touch
    if latest.status in (StepRunStatus.RUNNING, StepRunStatus.SUSPENDED,
                         StepRunStatus.DELEGATED, StepRunStatus.FAILED):
        continue
    # Current COMPLETED: step ran successfully in this iteration
    if latest.status == StepRunStatus.COMPLETED:
        if self._is_current(job, latest):
            continue
        # Stale COMPLETED: fall through to re-evaluate when condition
    elif latest.status == StepRunStatus.SKIPPED:
        # Check if deps have changed since the skip decision
        if latest.dep_run_ids:
            stale = False
            for dep_step, used_run_id in latest.dep_run_ids.items():
                if dep_step == "$job":
                    continue
                dep_latest = self.store.latest_run(job.id, dep_step)
                if dep_latest and dep_latest.id != used_run_id:
                    stale = True
                    break
            if not stale:
                continue
        # No dep_run_ids (pre-fix SKIPPED) or deps changed: re-evaluate
```
**Why each branch:**
- `RUNNING/SUSPENDED/DELEGATED/FAILED`: These are active or terminal-with-error — re-skipping would corrupt state.
- `COMPLETED + _is_current()`: The step's output is still valid for this iteration. `_is_current` (engine.py:1762) recursively checks dep provenance — if any dep was superseded, it returns False.
- `COMPLETED + not current`: A dep was superseded by a loop iteration. The when condition may have changed. Fall through to re-evaluate.
- `SKIPPED + dep_run_ids match`: The skip decision was made with the same dep runs still in place. Still valid.
- `SKIPPED + no dep_run_ids`: Pre-fix SKIPPED runs lack provenance. Conservatively re-evaluate (self-healing — creates a new SKIPPED with dep_run_ids).
- `SKIPPED + dep changed`: A dep has a newer run. The when condition may have flipped. Fall through.

**Depends on:** Step 2 (SKIPPED runs must have dep_run_ids for the staleness check to stabilize).
**Verify:** `uv run pytest tests/test_after_resolved.py -x -q` (all pass — no stale runs in first-pass tests, so the new code paths aren't triggered)

### Step 4: Fix `_resolve_inputs` to record latest terminal run for after_resolved deps
**File:** `src/stepwise/engine.py`, lines 3264-3273
**Change:** Replace the `latest_completed_run`-first logic with a single `latest_run` check.
**Exact replacement:**
```python
# Record after_resolved deps — use latest terminal run (COMPLETED or
# SKIPPED), not just latest COMPLETED. In loop iterations, a dep that
# was COMPLETED in iter N may be SKIPPED in iter N+1; the SKIPPED run
# (higher attempt) is the authoritative state.
for seq_step in step_def.after_resolved:
    latest_any = self.store.latest_run(job.id, seq_step)
    if latest_any and latest_any.status in (
        StepRunStatus.COMPLETED, StepRunStatus.SKIPPED,
    ):
        dep_run_ids[seq_step] = latest_any.id
```
**Why:** `latest_completed_run` (store.py:781) filters `status = 'completed'`. When a dep has both a COMPLETED run (iter 1, attempt 1) and a SKIPPED run (iter 2, attempt 2), it returns the stale COMPLETED run. `latest_run` (store.py:773) returns the highest-attempt run regardless of status, which is the SKIPPED run — the correct current state.
**Backwards compatibility:** On first-pass flows (no loops), `latest_run` returns the same run as `latest_completed_run` (there's only one terminal run). On SKIPPED-only deps, `latest_run` returns the SKIPPED run — same as the old fallback path.
**Depends on:** Steps 2-3 (new SKIPPED runs must exist with correct attempt numbers for `latest_run` to return them).
**Verify:** `uv run pytest tests/test_after_resolved.py -x -q` (passes — first-pass tests have no stale deps)

### Step 5: Write regression test for after_resolved + loop interaction
**File:** `tests/test_after_resolved.py`
**Change:** Add `test_after_resolved_loop_invalidation` to `TestAfterResolvedEngine` class.
**Test flow:**
```
dispatcher(attempt→turn) → [branch_a(when turn==1), branch_b(when turn==2)]
                         → merge(after_resolved: [branch_a, branch_b])
                         → controller(loop→dispatcher when attempt<2, else advance)
```
**Step-by-step expected behavior:**
1. `dispatcher` (attempt 1) → `{"turn": 1}`
2. `branch_a` when `turn == 1` → True → runs → `{"result": "a"}`
3. `branch_b` when `turn == 2` → False → eagerly SKIPPED
4. `merge` after_resolved `[branch_a, branch_b]` → both resolved → runs
5. `controller` (attempt 1) → `{"done": False}`, exit rule: `attempt < 2` → loop to `dispatcher`
6. `dispatcher` (attempt 2) → `{"turn": 2}`
7. `branch_a` when `turn == 1` → False → stale COMPLETED O1 detected → re-SKIPPED
8. `branch_b` when `turn == 2` → True → stale SKIPPED detected → re-runs → `{"result": "b"}`
9. `merge` after_resolved → both resolved (branch_a SKIPPED, branch_b COMPLETED) → runs again
10. `controller` (attempt 2) → `{"done": True}`, exit rule: advance → job completes

**Assertions:**
- `result.status == JobStatus.COMPLETED`
- `merge` has exactly 2 COMPLETED runs (one per iteration)
- `branch_a` has 1 COMPLETED run + at least 1 SKIPPED run
- `branch_b` has at least 1 SKIPPED run + 1 COMPLETED run
- SKIPPED runs for `branch_a` (iter 2) and `branch_b` (iter 1) have non-None `dep_run_ids`

**Implementation notes:**
- Use `register_step_fn` with a counter (list-based closure) for `dispatcher` to return different turn values per attempt
- `controller` needs an `ExitRule` with `type="expression"`, `condition="attempt < 2"`, `action="loop"`, `target="dispatcher"`
- `merge` needs only `after_resolved=["branch_a", "branch_b"]`, no input bindings (it doesn't consume their data, just waits for resolution)
- `branch_a` and `branch_b` need `inputs=[InputBinding("turn", "dispatcher", "turn")]` and `when="turn == 1"` / `when="turn == 2"`

**Depends on:** Steps 1-4 (all engine changes must be in place).
**Verify:** `uv run pytest tests/test_after_resolved.py::TestAfterResolvedEngine::test_after_resolved_loop_invalidation -xvs`

### Step 6: Run full regression suite
**Command:** `uv run pytest tests/ -x -q --ignore=tests/test_delegation.py --ignore=tests/test_editor_api.py`
**Depends on:** Steps 1-5.
**Key test files to watch for failures:**
- `tests/test_after_resolved.py` — direct feature tests (6 existing + 1 new)
- `tests/test_branching.py` — conditional branching with when conditions
- `tests/test_runner.py` — general engine execution, including loops
- `tests/test_validate_loop_back.py` — loop-back edge validation
- `tests/test_when_predicate_parse.py` — when condition parsing/evaluation

## Testing Strategy

### Test 1: New after_resolved + loop test (Step 5)
```bash
uv run pytest tests/test_after_resolved.py::TestAfterResolvedEngine::test_after_resolved_loop_invalidation -xvs
```
**Validates:** R1 (re-skipping), R2 (provenance), R3 (dep_run_ids on SKIPPED).

### Test 2: Existing after_resolved tests unchanged
```bash
uv run pytest tests/test_after_resolved.py -x -q
```
**Validates:** R4 (first-pass after_resolved not broken). All 6 existing tests must pass with zero modifications.

### Test 3: Loop and branching tests
```bash
uv run pytest tests/test_branching.py tests/test_runner.py tests/test_validate_loop_back.py -x -q
```
**Validates:** R4 (existing loop behavior preserved). These tests exercise loop exit rules, `_is_current`, `_is_step_ready`, and `_skip_when_blocked_steps`.

### Test 4: Full suite
```bash
uv run pytest tests/ -x -q --ignore=tests/test_delegation.py --ignore=tests/test_editor_api.py
```
**Validates:** R4 globally. The `--ignore` flags exclude tests with external service dependencies unrelated to this fix.

### Manual verification (optional)
Re-run the podcast-panel flow against the production server after deploying:
```bash
stepwise run ~/work/vita/flows/podcast-panel/FLOW.yaml --name "test: after_resolved loop fix"
```
Verify that `collect-candidates` runs on iteration 2.

## Risks & Mitigations

### Risk 1: Infinite re-skipping loop
If the staleness check always triggers in the `while self._skip_when_blocked_steps(job)` loop (engine.py:4689), it could create SKIPPED runs endlessly.
**Mitigation:** New SKIPPED runs include `dep_run_ids` (Step 2). On the next iteration of the while loop, the staleness check (Step 3) compares those dep_run_ids against current `latest_run` — which haven't changed since we just created the SKIPPED run. `stale` evaluates to False → `continue` → step is skipped. The while loop terminates.
**Verification:** The new test (Step 5) exercises 2 iterations. If infinite looping occurs, `run_job_sync`'s timeout (10s default) catches it.

### Risk 2: `_is_current` cost in `_skip_when_blocked_steps`
Calling `_is_current` (engine.py:1762 — recursive tree walk) for stale COMPLETED runs adds computational cost.
**Mitigation:** `_skip_when_blocked_steps` only runs when (a) nothing is ready (engine.py:4688 — `if not ready:`) and (b) nothing is in flight (engine.py:1980-1983 — safety guard checks running/suspended/delegated). This is a quiescent state. The `_is_current` call is the same one used in `_is_step_ready` (line 1572) — it's not a new pattern, just invoked from a new call site.

### Risk 3: Attempt numbering affects exit rules
Creating new SKIPPED runs increases the `attempt` number on those steps. If an exit rule references `attempt` for a step that also gets re-skipped, the count might be unexpected.
**Mitigation verified:** Exit rules use `run.attempt` of the step that just completed (engine.py:3299), not its deps' attempts. `max_iterations` uses `completed_run_count` (engine.py:3316, store.py:798 — `status = 'completed'`), which excludes SKIPPED runs. Re-skipping a *dep* has no effect on the *consuming* step's exit rule evaluation.

### Risk 4: Old SKIPPED runs without dep_run_ids (backward compat)
Pre-fix SKIPPED runs (created by current code at engine.py:2066-2071) have no `dep_run_ids`. The staleness check in Step 3 treats them conservatively as stale.
**Mitigation:** On re-evaluation, a new SKIPPED run is created WITH `dep_run_ids` (Step 2). The self-healing occurs once per old SKIPPED run. The cost is one extra `_resolve_inputs` call + `StepRun` write — negligible. Only affects in-flight jobs that were started before the fix is deployed.
