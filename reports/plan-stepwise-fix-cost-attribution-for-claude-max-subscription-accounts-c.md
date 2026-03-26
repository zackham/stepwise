# Plan: Fix Cost Attribution for Claude Max Subscription Accounts

## Overview

Claude Max subscription accounts are billed at a flat rate, not per-token. Currently the web UI shows per-token cost figures for all billing modes, which is misleading for subscription users — they see costs like "$0.0342" that don't represent real charges. The fix: when `billing_mode === "subscription"` (the default), return `0` from cost endpoints and suppress cost display in the UI entirely, rather than showing estimated costs with an "est." label.

## Current Behavior

1. **Backend**: `/api/jobs/{job_id}/cost` and `/api/runs/{run_id}/cost` always return actual accumulated token costs regardless of billing mode. The `billing_mode` config value is only used to conditionally enforce cost *limits* in `engine.py:_check_limits()` — it has no effect on cost *reporting*.

2. **Web UI**: `JobDetailPage.tsx` shows dollar costs with an "est." label when `billing_mode === "subscription"`. `StepDetailPanel.tsx` and `AgentStreamView.tsx` show costs unconditionally (no billing mode check at all). `report.py` HTML reports also show costs with no billing awareness.

3. **Config**: `billing` field in `StepwiseConfig` defaults to `"subscription"`. Exposed to frontend as `billing_mode` in `/api/config` response.

## Requirements

### R1: Cost endpoints return $0 for subscription billing
- **Acceptance**: `GET /api/jobs/{job_id}/cost` returns `{"cost_usd": 0}` and `GET /api/runs/{run_id}/cost` returns `{"cost_usd": 0}` when engine `billing_mode == "subscription"`.
- **Rationale**: Cost data is meaningless for subscription accounts. Returning 0 from the API is the cleanest fix — all consumers (web UI, `--wait` JSON output, reports) automatically get correct behavior.

### R2: Web UI hides cost display for subscription billing
- **Acceptance**: No dollar amounts shown anywhere in the job detail page, step detail panel, or agent stream view when `billing_mode === "subscription"`. No "est." label needed — the cost simply doesn't appear.
- **Rationale**: Even showing "estimated" costs for subscription users is confusing. Zero cost means nothing to display.

### R3: HTML reports respect billing mode
- **Acceptance**: `report.py` HTML reports show $0 / no cost section when billing is subscription.
- **Rationale**: Reports are generated from the same cost data, so if the engine returns 0, reports already handle this (they hide cost when 0). This should work automatically from R1.

### R4: Cost data still collected internally
- **Acceptance**: `step_events` cost records and `executor_meta.cost_usd` continue to be written regardless of billing mode. Only the *reporting* layer returns 0.
- **Rationale**: Users may switch billing modes later, and cost data is useful for debugging/analytics even on subscription plans.

### R5: `--wait` JSON output reflects billing mode
- **Acceptance**: `stepwise run --wait` output includes `"cost_usd": 0` for subscription billing.
- **Rationale**: CLI consumers should get consistent cost info.

## Assumptions

| # | Assumption | Verified Against |
|---|-----------|-----------------|
| A1 | `billing` field defaults to `"subscription"` in `StepwiseConfig` | `config.py:112` — `billing: str = "subscription"` |
| A2 | Cost endpoints are at `/api/jobs/{job_id}/cost` and `/api/runs/{run_id}/cost` | `server.py:1164-1169` (run), `server.py:1259-1268` (job) |
| A3 | `engine.billing_mode` is set from `config.billing` at engine creation | `server.py:677`, `runner.py:538,968,1538` |
| A4 | Frontend gets billing mode from `/api/config` response as `billing_mode` | `server.py:1498`, `api.ts:448` |
| A5 | `report.py` already hides cost display when `total_cost == 0` | `report.py:749-750`, `report.py:1164-1165` |
| A6 | Run cost endpoint uses `store.accumulated_cost()` only (misses `executor_meta` fallback) | `server.py:1168` — only calls `store.accumulated_cost()`, doesn't use `engine._run_cost()` |
| A7 | `resolved_flow_status()` includes per-step costs used by some UI paths | `engine.py:678` — returns full DAG with costs |

## Implementation Steps

### Step 1: Add billing-aware cost methods to engine
**File**: `src/stepwise/engine.py`

Modify `job_cost()` and `_run_cost()` to check `self.billing_mode`:

```python
def job_cost(self, job_id: str) -> float:
    """Total accumulated cost across all runs for a job, including sub-jobs."""
    if self.billing_mode == "subscription":
        return 0.0
    # ... existing logic unchanged
```

This is the single chokepoint — all cost reporting flows through `job_cost()` and `_run_cost()`. By returning 0 here, all downstream consumers (API endpoints, reports, `--wait` output) automatically get $0.

Do **not** modify `_run_cost()` separately — `job_cost()` calls it, and `_run_cost()` is also used by `_check_limits()`. Since limits are already gated on `billing_mode == "api_key"`, `_run_cost()` should continue returning real values for internal use. Instead, add a public `reported_job_cost()` or gate at the `job_cost()` level.

**Revised approach** — keep `_run_cost()` and `job_cost()` returning real values (needed by `_check_limits()` and `resolved_flow_status()`). Instead, gate at the API layer:

### Step 1 (revised): Gate cost at API endpoints
**File**: `src/stepwise/server.py`

Modify both cost endpoints to return 0 when billing mode is subscription:

```python
@app.get("/api/runs/{run_id}/cost")
def get_run_cost(run_id: str):
    engine = _get_engine()
    if engine.billing_mode == "subscription":
        return {"run_id": run_id, "cost_usd": 0}
    cost = engine.store.accumulated_cost(run_id)
    return {"run_id": run_id, "cost_usd": cost}

@app.get("/api/jobs/{job_id}/cost")
def get_job_cost(job_id: str):
    engine = _get_engine()
    try:
        engine.get_job(job_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if engine.billing_mode == "subscription":
        return {"job_id": job_id, "cost_usd": 0}
    cost = engine.job_cost(job_id)
    return {"job_id": job_id, "cost_usd": round(cost, 4) if cost else 0}
```

### Step 2: Gate cost in `resolved_flow_status()`
**File**: `src/stepwise/engine.py`

The `resolved_flow_status()` method returns per-step cost data used by the `/api/jobs/{job_id}/status` endpoint. Find where it includes `cost` in the returned dict and zero it out when `billing_mode == "subscription"`.

Read `resolved_flow_status()` to identify the exact line, then add:
```python
cost = 0.0 if self.billing_mode == "subscription" else self._run_cost(run)
```

### Step 3: Gate cost in `--wait` JSON output
**File**: `src/stepwise/runner.py`

Find where `cost_usd` is included in `--wait` mode JSON output. Gate it on billing mode:
```python
cost = 0.0 if engine.billing_mode == "subscription" else engine.job_cost(job.id)
```

### Step 4: Remove "est." label from web UI, hide cost entirely for subscription
**File**: `web/src/pages/JobDetailPage.tsx`

The current code shows costs with "est." when subscription. Change to hide costs entirely:

**Lines 341-351** (header badge): Change condition from `costData.cost_usd > 0` to also require `configData?.billing_mode !== "subscription"`:
```tsx
{costData && costData.cost_usd > 0 && configData?.billing_mode !== "subscription" && (
```

**Lines 485-497** (sidebar): Same pattern — add billing mode check and remove the "est." span:
```tsx
{costData && costData.cost_usd > 0 && configData?.billing_mode !== "subscription" && (
  <div className="flex items-center gap-2">
    <span className="text-zinc-500 w-16">Cost</span>
    <span className="font-mono text-zinc-400">
      ${costData.cost_usd.toFixed(4)}
    </span>
  </div>
)}
```

### Step 5: Hide cost in StepDetailPanel for subscription
**File**: `web/src/components/jobs/StepDetailPanel.tsx`

**Line 268** (AgentStreamView costUsd prop): Pass `undefined` when subscription:
```tsx
costUsd={configData?.billing_mode !== "subscription" ? costData?.cost_usd : undefined}
```

This requires adding `useConfig()` to `StepDetailPanel` (currently not imported there).

**Lines 406-414** (run history cost): Add billing mode check:
```tsx
{configData?.billing_mode !== "subscription" &&
  run.result?.executor_meta?.cost_usd != null &&
  (run.result.executor_meta.cost_usd as number) > 0 && (
  ...
)}
```

### Step 6: Report cost gating (automatic)
**File**: `src/stepwise/report.py`

`report.py` uses `store.accumulated_cost()` directly (line 60), bypassing the engine's billing mode awareness. Two options:

**Option A** (simple): Pass billing mode to `generate_report()` and zero costs when subscription. This requires threading the billing mode from the caller.

**Option B** (minimal): Since reports are generated from the engine context (via `server.py` or `runner.py`), modify `generate_report()` to accept a `billing_mode` parameter:

```python
def generate_report(store, job, billing_mode="subscription") -> str:
    # In step cost calculation:
    cost = 0.0 if billing_mode == "subscription" else sum(store.accumulated_cost(r.id) for r in runs)
```

Update callers to pass `engine.billing_mode`.

### Step 7: Add/update tests

**File**: `tests/test_server.py` (or new `tests/test_cost_billing.py`)

```python
def test_job_cost_zero_for_subscription(async_engine):
    """Subscription billing returns 0 cost from API."""
    assert async_engine.billing_mode == "subscription"
    # Create job with a step that generates cost data
    # Verify job_cost() returns real value (internal)
    # Verify API endpoint returns 0

def test_job_cost_real_for_api_key(async_engine):
    """API key billing returns actual cost from API."""
    async_engine.billing_mode = "api_key"
    # Create job with cost data
    # Verify API returns real cost

def test_run_cost_zero_for_subscription(async_engine):
    """Run cost endpoint returns 0 for subscription billing."""
    # Similar pattern
```

**File**: `web/src/components/jobs/__tests__/` or existing test files

Add/update vitest tests for:
- JobDetailPage: cost hidden when billing_mode is subscription
- StepDetailPanel: cost hidden when billing_mode is subscription
- AgentStreamView: costUsd not passed when subscription

## Testing Strategy

### Python tests
```bash
# Run all tests to verify no regressions
uv run pytest tests/

# Run specific cost/billing tests
uv run pytest tests/test_server.py -k "cost"
uv run pytest tests/test_m4_async.py -k "cost"

# If new test file created:
uv run pytest tests/test_cost_billing.py -v
```

### Web tests
```bash
cd web && npm run test
cd web && npm run lint
```

### Manual verification
1. Start server with default config (subscription): `stepwise server start`
2. Run a flow with an LLM/agent step
3. Verify: Job detail page shows no cost, step detail shows no cost, agent stream shows no cost
4. Change config to `billing: api_key` in `.stepwise/config.yaml`
5. Restart server, run same flow
6. Verify: Costs now appear correctly

## File Change Summary

| File | Change |
|------|--------|
| `src/stepwise/server.py:1164-1169, 1259-1268` | Gate run/job cost endpoints on billing mode |
| `src/stepwise/engine.py` (in `resolved_flow_status()`) | Zero out per-step costs for subscription |
| `src/stepwise/runner.py` | Zero out cost in `--wait` JSON for subscription |
| `src/stepwise/report.py:58-67` | Accept billing_mode param, zero costs for subscription |
| `web/src/pages/JobDetailPage.tsx:341-351, 485-497` | Hide cost display + remove "est." for subscription |
| `web/src/components/jobs/StepDetailPanel.tsx:268, 406-414` | Add billing mode check, hide cost for subscription |
| `tests/test_server.py` or `tests/test_cost_billing.py` | New billing-aware cost tests |

## Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| Users on subscription who want to see estimated costs lose visibility | Low — the "est." values were misleading anyway | Could add an opt-in "show estimated costs" toggle in settings if requested |
| `resolved_flow_status()` cost zeroing affects agent-facing `/api/jobs/{id}/status` | Low — agents don't make cost decisions | Verify no agent logic depends on cost values |
| Report generation callers need update to pass billing_mode | Low — few callers | Grep for `generate_report` calls and update all |
