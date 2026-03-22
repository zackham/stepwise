---
title: "Implementation Plan: Step-Level Result Caching"
date: "2026-03-20T00:00:00Z"
project: stepwise
tags: [implementation, plan]
status: active
---

# Implementation Plan: Step-Level Result Caching

## Overview

Add opt-in, content-addressable result caching per step. When a step declares `cache: true` (or `cache: { ttl: 24h, key_extra: v2 }`), the engine hashes its resolved inputs + interpolated executor config + engine version into a SHA-256 key, checks `.stepwise/cache/results.db` for a hit before dispatching the executor, and writes results back on success. CLI commands provide cache management; `--rerun <step>` bypasses the cache for a single step in a run.

## Requirements

### R1: YAML `cache` field on steps
- **Accepts**: `cache: true` (use type-default TTL), `cache: false` (explicit no-op, same as omitting), or `cache: { ttl: 24h, key_extra: v2 }` (explicit TTL + cache-busting suffix).
- **Parsed by**: `yaml_loader.py:_parse_step()` (line 682) → stored as `StepDefinition.cache` field.
- **AC1**: A flow with `cache: true` on a step loads without error via `load_workflow_yaml()`.
- **AC2**: `cache` round-trips through `StepDefinition.to_dict()` → `StepDefinition.from_dict()` with all fields preserved.
- **AC3**: `cache: false` and omitted `cache` both produce `cache=None` on the StepDefinition.
- **AC4**: `cache: { ttl: 30m }` produces `CacheConfig(enabled=True, ttl_seconds=1800, key_extra=None)`.

### R2: Content-addressable cache keys
- **Key components**: SHA-256 of canonical JSON containing `resolved_inputs`, `executor_type`, `executor_config` (after interpolation, minus runtime-injected keys), `engine_version`, and `key_extra`.
- **Canonical form**: `json.dumps(sort_keys=True, default=str)` for determinism.
- **AC1**: Same inputs + config + version + key_extra → identical key (deterministic).
- **AC2**: Changing any one of: an input value, executor config field, engine version, or key_extra → different key.
- **AC3**: Runtime-injected config keys (`_registry`, `_config`, `_depth_remaining`, `_project_dir`, `_prev_session_name`, `output_fields`, `flow_dir`) are excluded from the hash.

### R3: Cache store in `.stepwise/cache/results.db`
- **Separate SQLite DB** — not in `stepwise.db`. Project-scoped via `.stepwise/cache/`.
- **Schema**:
  ```sql
  CREATE TABLE cache_entries (
      key TEXT PRIMARY KEY,
      step_name TEXT NOT NULL,
      flow_name TEXT NOT NULL DEFAULT '',
      result TEXT NOT NULL,        -- JSON HandoffEnvelope.to_dict()
      created_at TEXT NOT NULL,
      expires_at TEXT,             -- NULL = never expires
      hit_count INTEGER DEFAULT 0
  );
  CREATE INDEX idx_cache_step ON cache_entries(step_name);
  CREATE INDEX idx_cache_flow ON cache_entries(flow_name);
  CREATE INDEX idx_cache_expires ON cache_entries(expires_at);
  ```
- **AC1**: Cache DB auto-created (including parent `cache/` dir) on first cache write.
- **AC2**: Reads from non-existent DB path return miss (no error, no file creation).
- **AC3**: Expired entries (now > `expires_at`) return miss and are lazily deleted on read.
- **AC4**: WAL mode enabled for safe concurrent reads from for-each sub-jobs.

### R4: Engine cache integration
- **Check point**: Inside `_prepare_step_run()` (engine.py:1044), after `_resolve_inputs()` (line 1053) and `_interpolate_config()` (line 1097), before executor creation/dispatch.
- **Write point**: Inside `_process_launch_result()` (engine.py:1160), after the success path at line 1207 (status=COMPLETED, type="data", no validation error).
- **On cache hit**: Create a StepRun with `status=COMPLETED`, `result=cached_envelope`, `executor_state={"from_cache": True, "cache_key": key}`. Emit `STEP_COMPLETED` event. Call `_process_completion()` for exit rule evaluation. Skip executor dispatch entirely.
- **Never cached**: Steps with executor type in `{"human", "poll", "for_each", "sub_flow"}`, or agent steps with `emit_flow: true`. These have side effects.
- **AC1**: Second run of a cached step with identical inputs skips executor; call count stays at 1.
- **AC2**: Cache hit still evaluates exit rules via `_process_completion()` — loops and escalations work.
- **AC3**: After TTL expiry, the same step re-executes and writes a fresh cache entry.
- **AC4**: `executor_state.from_cache == True` on cache-hit runs, visible in UI/API.

### R5: Default TTL by executor type
- `script`: 3600s (1h). `callable`: 3600s (1h). `mock_llm`: 3600s (1h).
- `llm`: 86400s (24h). `agent` (without emit_flow): 86400s (24h).
- Explicit `ttl` in YAML overrides the default.
- **AC1**: LLM step with `cache: true` and no `ttl` → `expires_at` = created_at + 24h.
- **AC2**: LLM step with `cache: { ttl: 30m }` → `expires_at` = created_at + 1800s.
- **AC3**: Script step with `cache: true` → `expires_at` = created_at + 1h.

### R6: CLI `stepwise cache` commands
- `stepwise cache clear [--flow X] [--step Y]` — delete matching entries, print count. No args = clear all.
- `stepwise cache stats` — print total entries, DB file size, hit count sum, per-flow breakdown.
- `stepwise cache debug <flow> <step> [--var KEY=VALUE]` — load flow, resolve step inputs from `--var` flags, compute and display the cache key + all key components (for debugging cache misses).
- **AC1**: `stepwise cache clear` on empty cache prints "0 entries cleared" (not an error).
- **AC2**: `stepwise cache stats` shows "No cache database found" when `.stepwise/cache/results.db` doesn't exist.
- **AC3**: `stepwise cache debug flow.yaml my-step --var x=5` prints the SHA-256 key and the canonical JSON used to compute it.

### R7: `--rerun <step>` flag on `stepwise run`
- Bypasses cache lookup for the named step(s), but still writes results to cache.
- Repeatable: `--rerun step-a --rerun step-b`.
- Stored in `job.config.metadata["rerun_steps"]` so the engine can read it.
- **AC1**: `stepwise run f.yaml --rerun analyze` re-executes `analyze` even with valid cache, caches the new result.
- **AC2**: Other cached steps in the same flow still hit cache normally.
- **AC3**: `--rerun nonexistent-step` does not error at parse time (step names validated lazily by engine).

### R8: For-each batch cache lookups
- Before creating N sub-jobs in `_launch_for_each()` (engine.py:1348), batch-query cache for all items.
- Cached items: store results directly in the for-each tracking, skip sub-job creation.
- Uncached items: create sub-jobs as normal.
- `_check_for_each_completion()` (engine.py:1391) merges cached results with live sub-job results, preserving original item order.
- **AC1**: For-each over 100 items, 80 cached → only 20 sub-jobs created.
- **AC2**: Final `results` list has 100 entries in correct order (cached and live interleaved by original index).
- **AC3**: If all items are cached, the for-each step completes immediately (same as empty-list fast path at line 1329).

## Assumptions

### A1: Cache belongs in a separate SQLite DB, not in `stepwise.db`
Cache entries are ephemeral, potentially large (LLM responses), and have different lifecycle than jobs/runs. Mixing them would bloat the main DB and complicate backup/migration.
- **Verified**: `store.py:46-108` — `_create_tables()` creates only `jobs`, `step_runs`, `events`, `step_events`. Cache is a distinct concern.
- **Verified**: `project.py:106-108` — `.stepwise/.gitignore` ignores `*` (everything), so `cache/results.db` is auto-ignored.
- **Verified**: `project.py:29-37` — `StepwiseProject` has explicit paths for `db_path`, `jobs_dir`, `templates_dir`. Adding `cache_dir` follows this pattern.

### A2: `_prepare_step_run()` is the single correct interception point for cache check
This method (engine.py:1044-1138) is called by both `Engine._launch()` (legacy, line 253) and `AsyncEngine._launch()` (line 2399). It resolves inputs, interpolates config, and creates the run — all necessary for computing the cache key. The cache check goes after interpolation (line 1102) but before any runtime config injection (lines 1104-1136).
- **Verified**: `engine.py:2399` — `AsyncEngine._launch()` calls `self._prepare_step_run(job, step_name)` then immediately dispatches to thread pool. Inserting a cache check between these is clean.
- **Verified**: `engine.py:1094-1102` — `_interpolate_config()` resolves `$variable` references, making the config ready for hashing. Lines 1104-1136 inject runtime-only keys (`output_fields`, `flow_dir`, `_registry`, etc.) that must NOT be part of the cache key.

### A3: Cache key should be computed from the interpolated config BEFORE runtime injection
The executor config goes through three phases: (1) raw from YAML, (2) interpolated with `$variable` refs resolved (line 1097), (3) augmented with runtime keys (lines 1104-1136). Phase 2 is the right hash input — it includes user-meaningful config (model, prompt text) but not ephemeral runtime context.
- **Verified**: engine.py:1096-1102 — interpolation happens first. Lines 1104+ add `output_fields` (line 1105), `flow_dir` (line 1107), `continue_session`/`_prev_session_name` (lines 1113-1117), `_registry`/`_config`/`_depth_remaining`/`_project_dir` (lines 1128-1136). All of these are runtime-only.

### A4: Human, poll, sub_flow, and emit_flow agent steps must never be cached
- **Verified**: `executors.py` — `HumanExecutor.start()` returns `ExecutorResult(type="watch")`, creating a suspension for human input. Caching would skip the human.
- **Verified**: `executors.py` — `PollExecutor.start()` returns `ExecutorResult(type="watch")` with poll config. Caching would skip external condition checking.
- **Verified**: `engine.py:2395-2396` — sub_flow steps dispatch via `_launch_sub_flow()`, not through `_prepare_step_run()`. They'll naturally bypass the cache check.
- **Verified**: `engine.py:1126` — `emit_flow` agents get `_registry` injected and return `type="delegate"`. Their side effect (writing `.stepwise/emit.flow.yaml`) makes caching unsafe.

### A5: `_parse_duration()` exists but needs extraction
- **Verified**: `cli.py:2195-2209` — parses `24h`, `7d`, `30m` to seconds. Currently only used within `cli.py`. Needs to be importable from `yaml_loader.py` without creating a circular import.
- **Decision**: Copy the ~15-line function into `yaml_loader.py` (it's tiny, self-contained, and avoids a cross-module import from the CLI layer).

### A6: For-each cache requires computing sub-flow cache keys without creating sub-jobs
- **Verified**: `engine.py:1350-1367` — each sub-job gets `sub_inputs = {**inputs, fe.item_var: item}`. The cache key for each item uses these inputs plus the terminal step's interpolated executor config.
- **Verified**: `engine.py:1391-1497` — `_check_for_each_completion()` assembles results as `completed_results[i]` by sub-job index. Cached results must slot into the same index-based scheme.

### A7: Engine version accessible at runtime
- **Verified**: `cli.py:58-64` — `_get_version()` uses `importlib.metadata.version("stepwise-run")`. This can be imported by `cache.py` without circular dependency (it reads package metadata, not CLI code).

## Out of Scope

- **Cross-project or remote cache sharing** — cache is local to `.stepwise/cache/`. No S3/Redis/shared filesystem.
- **Cache warming or pre-computation** — no proactive cache population mechanism.
- **Web UI cache management** — CLI-only cache commands. No cache visibility in the React frontend.
- **Max-size eviction policy** — no LRU or disk-size cap. TTL expiry and `stepwise cache clear` are sufficient for v1.
- **Cache for sub_flow steps** — sub_flow steps (engine.py:2395) don't go through `_prepare_step_run()`. Caching them would require separate interception; not worth it for v1.
- **Invalidation on flow file changes** — prompt/config changes naturally invalidate because they're part of the cache key (prompts are in executor config after interpolation). No file-mtime-based invalidation needed.

## Architecture

### New module: `src/stepwise/cache.py`

A standalone module containing `StepResultCache` (SQLite wrapper) and `compute_cache_key()` (hashing logic). Follows `store.py` patterns: raw SQL, WAL mode, `json.dumps()`/`json.loads()` serialization.

**Module DAG position**: `models → cache → engine`. `cache.py` imports only `HandoffEnvelope` and `Sidecar` from `models.py` for deserialization. No imports from `engine`, `executors`, or `server`.

### Cache key computation

The cache key is a SHA-256 of a canonical JSON object:

```python
key_material = {
    "inputs": resolved_inputs,                    # from _resolve_inputs()
    "executor_type": exec_ref.type,               # e.g., "script", "llm"
    "executor_config": sanitized_config,           # after interpolation, minus RUNTIME_KEYS
    "engine_version": "0.6.0",                    # from importlib.metadata
    "key_extra": cache_config.key_extra or "",    # user-provided cache buster
}
```

**RUNTIME_KEYS** (stripped before hashing): `{"output_fields", "flow_dir", "_registry", "_config", "_depth_remaining", "_project_dir", "_prev_session_name", "continue_session", "loop_prompt", "max_continuous_attempts"}`. These are injected by `_prepare_step_run()` at lines 1104-1136 and are not user-meaningful.

### Engine integration — detailed flow

#### Cache check (in `_prepare_step_run`, engine.py:1044)

The check is inserted after line 1102 (interpolation complete) and before line 1104 (runtime injection begins):

```
1053: inputs, dep_run_ids = self._resolve_inputs(job, step_def)
1097: interpolated = _interpolate_config(exec_ref.config, inputs)
1102: exec_ref = ExecutorRef(type=..., config=interpolated, ...)
  ──► NEW: cache_key = compute_cache_key(inputs, exec_ref, version, key_extra)
  ──► NEW: cached = self.cache.get(cache_key)
  ──► NEW: if cached: create synthetic COMPLETED run, return early
1104: if step_def.outputs and "output_fields" not in exec_ref.config: ...
```

On cache hit, `_prepare_step_run()` returns a 4-tuple sentinel `(run, None, None, None)` where `exec_ref=None` signals "already handled". `AsyncEngine._launch()` (line 2399) checks this sentinel and skips the `_run_executor()` coroutine dispatch.

#### Cache write (in `_process_launch_result`, engine.py:1160)

After the success completion path at line 1207-1213:

```
1205: run.status = StepRunStatus.COMPLETED
1207: self.store.save_run(run)
1213: self._process_completion(job, run)
  ──► NEW: if run.executor_state and run.executor_state.get("_cache_key"):
  ──►   self.cache.put(key, step_name, flow_name, envelope, ttl)
```

The cache key is threaded from `_prepare_step_run()` to `_process_launch_result()` via `run.executor_state["_cache_key"]`. This avoids recomputing the key.

#### For-each batch path (in `_launch_for_each`, engine.py:1275)

After resolving the source list (line 1314) and before the item loop (line 1348):

```
1314: if not isinstance(source_list, list): raise ...
  ──► NEW: Compute cache keys for all items; batch_get() from cache
  ──► NEW: Partition into cached_items[index] and uncached_indices
1348: # Create sub-jobs only for uncached items
```

The `executor_state` on the for-each run stores both `sub_job_ids` (for uncached items) and `cached_results` (dict of index→artifact for cached items). `_check_for_each_completion()` merges these in order.

### How cache connects to the engine lifecycle

```
Engine.__init__(project_dir=...)
  └─ self.cache = StepResultCache(project_dir / "cache" / "results.db")
       └─ Lazy init: DB file created on first put(), not on __init__

_dispatch_ready(job_id)
  └─ _launch(job, step_name)
       └─ _prepare_step_run(job, step_name)
            ├─ _resolve_inputs()              → inputs dict
            ├─ _interpolate_config()          → interpolated exec_ref
            ├─ ►CACHE CHECK◄                  → hit? synthetic run, return
            ├─ runtime injection (lines 1104+)
            └─ return (run, exec_ref, inputs, ctx)
       └─ _run_executor()                    (skipped on cache hit)
            └─ executor.start()
       └─ queue: ("step_result", ...)

_handle_queue_event("step_result")
  └─ _process_launch_result(job, run, result)
       ├─ case "data" + success:
       │    ├─ run.status = COMPLETED
       │    ├─ ►CACHE WRITE◄
       │    └─ _process_completion()
       └─ (other cases: watch, delegate — not cached)
```

## Implementation Steps

### Step 1: Add `CacheConfig` dataclass to models.py (~20 min)

**File**: `src/stepwise/models.py`

1. Add `CacheConfig` dataclass after line 403 (before `to_dict`):
   - Fields: `enabled: bool = True`, `ttl_seconds: int | None = None`, `key_extra: str | None = None`
   - `to_dict()`: returns `{"enabled": True, ...}` with only non-default fields
   - `from_dict()`: constructs from dict with defaults
2. Add `cache: CacheConfig | None = None` field to `StepDefinition` at line 403.
3. Update `StepDefinition.to_dict()` at line 414: add `if self.cache: d["cache"] = self.cache.to_dict()`.
4. Update `StepDefinition.from_dict()` at line 437: add `cache=CacheConfig.from_dict(d["cache"]) if d.get("cache") else None`.

**Depends on**: Nothing. This is a pure model change.

### Step 2: Parse `cache` in YAML loader (~20 min)

**File**: `src/stepwise/yaml_loader.py`

1. Add `_parse_duration()` function (copy from `cli.py:2195-2209` — 15 lines, self-contained, no import needed).
2. Add `_parse_cache_config(step_data, step_name)` helper:
   - `cache: true` → `CacheConfig()`
   - `cache: false` → `None`
   - `cache: { ttl: "24h", key_extra: "v2" }` → parse ttl via `_parse_duration()`, construct `CacheConfig(ttl_seconds=..., key_extra=...)`
   - Validate: error if `ttl` string doesn't parse.
3. Call `_parse_cache_config()` in `_parse_step()` around line 815 (before the `return StepDefinition(...)` at line 817).
4. Pass `cache=cache_config` to the `StepDefinition()` constructor at line 817.
5. Also handle cache in the flow-step (line 729) and for-each (line 759) return paths — pass `cache=None` explicitly for clarity.

**Depends on**: Step 1 (CacheConfig class exists).

### Step 3: Create `StepResultCache` class (~40 min)

**File**: `src/stepwise/cache.py` (new)

1. Constructor: `__init__(self, db_path: str)`. Store path; do NOT open DB yet (lazy).
2. `_ensure_db()`: Create parent dirs, open SQLite connection, WAL mode, create table + indices (schema from R3). Called on first `get()` or `put()`.
3. `get(key: str) -> HandoffEnvelope | None`:
   - If no DB file exists on disk, return `None` (no-op read for non-existent cache).
   - Query: `SELECT result, expires_at FROM cache_entries WHERE key = ?`
   - Check `expires_at`: if expired, `DELETE` the row and return `None`.
   - Otherwise: `UPDATE hit_count = hit_count + 1`, deserialize via `HandoffEnvelope.from_dict(json.loads(result))`, return.
4. `put(key, step_name, flow_name, envelope, ttl_seconds)`:
   - `_ensure_db()`, compute `expires_at` from `ttl_seconds` if not None.
   - `INSERT OR REPLACE INTO cache_entries (key, step_name, flow_name, result, created_at, expires_at)`.
5. `batch_get(keys: list[str]) -> dict[str, HandoffEnvelope]`:
   - If no DB file exists, return `{}`.
   - `SELECT key, result, expires_at FROM cache_entries WHERE key IN (?, ?, ...)`.
   - Filter expired, increment hit counts for valid entries, return dict.
6. `clear(flow_name=None, step_name=None) -> int`: filtered `DELETE`, return `cursor.rowcount`.
7. `stats() -> dict`: `SELECT count(*), sum(hit_count) FROM cache_entries` + `os.path.getsize(db_path)` + per-flow breakdown.
8. `close()`: close connection if open.

Follow `store.py` patterns: `_dumps()` via `json.dumps(default=str)`, `sqlite3.Row` row factory, `busy_timeout=5000`.

**Depends on**: Step 1 (imports `HandoffEnvelope`, `Sidecar` from models).

### Step 4: Implement `compute_cache_key()` (~20 min)

**File**: `src/stepwise/cache.py`

1. Define `RUNTIME_CONFIG_KEYS` constant:
   ```python
   RUNTIME_CONFIG_KEYS = {
       "output_fields", "flow_dir",
       "_registry", "_config", "_depth_remaining", "_project_dir",
       "_prev_session_name", "continue_session", "loop_prompt",
       "max_continuous_attempts",
   }
   ```
   These are injected by `_prepare_step_run()` at engine.py lines 1104-1136.

2. Define `UNCACHEABLE_EXECUTOR_TYPES`:
   ```python
   UNCACHEABLE_EXECUTOR_TYPES = {"human", "poll", "for_each", "sub_flow"}
   ```
   Verified against executor types in `registry_factory.py` and `_launch()` special-case checks at engine.py:2373, 2395.

3. Define `DEFAULT_TTL`:
   ```python
   DEFAULT_TTL = {"script": 3600, "callable": 3600, "mock_llm": 3600, "llm": 86400, "agent": 86400}
   ```

4. Implement `compute_cache_key(inputs, exec_type, exec_config, engine_version, key_extra) -> str`:
   - Strip `RUNTIME_CONFIG_KEYS` from `exec_config`.
   - Build canonical dict, `json.dumps(sort_keys=True, default=str)`, `hashlib.sha256().hexdigest()`.

5. Implement `get_engine_version() -> str`: wraps `importlib.metadata.version("stepwise-run")` with fallback to `"0.0.0"` (same pattern as `cli.py:58-64`).

**Depends on**: Nothing (pure functions + constants).

### Step 5: Wire cache into `Engine.__init__()` (~15 min)

**File**: `src/stepwise/engine.py`

1. Import: `from stepwise.cache import StepResultCache, compute_cache_key, UNCACHEABLE_EXECUTOR_TYPES, DEFAULT_TTL, get_engine_version`.
2. In `Engine.__init__()` (line 94-109), add:
   ```python
   self.cache: StepResultCache | None = None
   if project_dir:
       self.cache = StepResultCache(str(project_dir / "cache" / "results.db"))
   ```
   This is lazy — the `StepResultCache` won't create the DB file until the first `put()`.
3. In `AsyncEngine.shutdown()` (line 2243), add: `if self.cache: self.cache.close()`.

**Depends on**: Step 3 (StepResultCache exists).

### Step 6: Add cache check to `_prepare_step_run()` (~30 min)

**File**: `src/stepwise/engine.py`

1. After line 1102 (interpolated `exec_ref` ready), before line 1104 (runtime injection), insert cache check block:
   - Guard conditions (skip cache if ANY is true):
     - `step_def.cache is None`
     - `self.cache is None`
     - `exec_ref.type in UNCACHEABLE_EXECUTOR_TYPES`
     - `exec_ref.config.get("emit_flow")` — agent with dynamic flow emission
     - `step_name in self._get_rerun_steps(job)` — `--rerun` bypass
   - Compute key: `cache_key = compute_cache_key(inputs, exec_ref.type, interpolated, get_engine_version(), step_def.cache.key_extra)`
   - Note: `interpolated` is the config dict captured at line 1097 BEFORE runtime injection. This is the correct input for hashing.
   - Lookup: `cached = self.cache.get(cache_key)`
   - On hit:
     - `run.result = cached`, `run.status = COMPLETED`, `run.completed_at = _now()`, `run.executor_state = {"from_cache": True, "cache_key": cache_key}`
     - `self.store.save_run(run)`, emit `STEP_COMPLETED` with `from_cache=True`
     - `self._process_completion(job, run)` — exit rules still evaluate
     - Return `(run, None, None, None)` — sentinel tuple

2. On cache miss: store the key for later write-back: `run.executor_state = {"_cache_key": cache_key}`, `self.store.save_run(run)`.

3. Add helper `_get_rerun_steps(self, job) -> set[str]`: reads `job.config.metadata.get("rerun_steps", [])`, returns as set.

**Depends on**: Steps 4, 5.

### Step 7: Handle cache sentinel in `AsyncEngine._launch()` (~15 min)

**File**: `src/stepwise/engine.py`

1. Modify `AsyncEngine._launch()` at line 2399:
   ```python
   run, exec_ref, inputs, ctx = self._prepare_step_run(job, step_name)
   if exec_ref is None:
       # Cache hit — run already completed in _prepare_step_run
       self._after_step_change(job.id)
       return run
   ```
2. This follows the existing pattern: `_launch_for_each()` (line 2375) and `_launch_sub_flow()` (line 2396) are similar early-return paths in `_launch()`.

**Depends on**: Step 6.

### Step 8: Add cache write to `_process_launch_result()` (~20 min)

**File**: `src/stepwise/engine.py`

1. After line 1213 (`self._process_completion(job, run)`) in the success path, add:
   ```python
   # Write to cache if applicable
   cache_key = (run.executor_state or {}).get("_cache_key")
   if cache_key and self.cache and step_def.cache:
       ttl = step_def.cache.ttl_seconds or DEFAULT_TTL.get(step_def.executor.type, 3600)
       try:
           self.cache.put(cache_key, step_name, job.workflow.metadata.name, run.result, ttl)
       except Exception:
           pass  # cache write failure is not fatal
   ```
2. The try/except ensures cache write failures never break job execution. This is a fire-and-forget pattern.
3. The flow name comes from `job.workflow.metadata.name` (models.py:466 — `FlowMetadata.name`).

**Depends on**: Steps 5, 6.

### Step 9: Add `--rerun` flag to CLI (~25 min)

**Files**: `src/stepwise/cli.py`, `src/stepwise/runner.py`

1. In `build_parser()` (cli.py:3288), add after `--notify-context`:
   ```python
   p_run.add_argument("--rerun", action="append", dest="rerun_steps",
                       metavar="STEP", help="Bypass cache for named step (repeatable)")
   ```

2. In `cmd_run()` (cli.py:1506), extract rerun steps and pass through all three modes:
   - `rerun_steps = getattr(args, "rerun_steps", None) or []`
   - Pass to `run_flow()` (line 1647), `run_wait()` (line 1625), `run_async()` (line 1601) as a new kwarg.

3. In `runner.py:run_flow()` (line 380), add `rerun_steps: list[str] | None = None` parameter. After job creation (line 490), before `store.save_job()` (line 493):
   ```python
   if rerun_steps:
       job.config.metadata["rerun_steps"] = rerun_steps
   ```

4. Do the same for `run_wait()` and `run_async()` in runner.py.

**Depends on**: Step 6 (`_get_rerun_steps()` helper reads the metadata).

### Step 10: For-each batch cache — compute keys (~30 min)

**File**: `src/stepwise/engine.py`

1. In `_launch_for_each()`, after the source list is resolved (line 1314) and before creating sub-jobs (line 1348), add a batch cache check:
   - Guard: skip if no `step_def.cache` or no `self.cache`.
   - For each item in `source_list`, compute the cache key using `sub_inputs = {**inputs, fe.item_var: item}` and the sub-flow's terminal step's executor config.
   - Call `self.cache.batch_get(all_keys)` — single DB query.

2. Determine cacheable config: inspect `step_def.sub_flow.steps` for terminal steps. If the sub-flow has a single terminal step with `cache` config, use its executor config for the key. If multiple terminals or no cache config, skip batch caching (fall through to normal per-item sub-job creation).

**Depends on**: Steps 3, 4.

### Step 11: For-each batch cache — split and merge results (~45 min)

**File**: `src/stepwise/engine.py`

1. After batch lookup, partition items:
   - `cached_results: dict[int, dict]` — index → artifact for cache hits.
   - `uncached_items: list[tuple[int, Any]]` — (original_index, item) for cache misses.

2. Create sub-jobs only for `uncached_items`. Track both the sub-job-to-index mapping and cached results in `executor_state`:
   ```python
   run.executor_state = {
       "for_each": True,
       "sub_job_ids": [...],  # only for uncached items
       "sub_job_index_map": {sub_job_id: original_index, ...},
       "cached_results": {str(index): artifact, ...},
       "item_count": len(source_list),
       "on_error": fe.on_error,
   }
   ```

3. Modify `_check_for_each_completion()` (line 1391) to merge:
   - Initialize `completed_results` from `cached_results` (pre-filled at known indices).
   - Fill in live sub-job results at their mapped indices.
   - Final `results` list is assembled in order.

4. Handle edge case: all items cached → complete immediately (same pattern as empty list at line 1329, but with populated results).

**Depends on**: Step 10.

### Step 12: Add `stepwise cache` CLI commands (~40 min)

**File**: `src/stepwise/cli.py`

1. In `build_parser()` (line 3244), add cache subcommand group:
   ```python
   p_cache = sub.add_parser("cache", help="Manage step result cache")
   cache_sub = p_cache.add_subparsers(dest="cache_action")
   p_cc = cache_sub.add_parser("clear", help="Clear cached results")
   p_cc.add_argument("--flow", help="Clear only entries for this flow")
   p_cc.add_argument("--step", help="Clear only entries for this step")
   p_cs = cache_sub.add_parser("stats", help="Show cache statistics")
   p_cd = cache_sub.add_parser("debug", help="Show cache key for a step")
   p_cd.add_argument("flow", help="Flow file path")
   p_cd.add_argument("step", help="Step name")
   p_cd.add_argument("--var", action="append", dest="vars", metavar="KEY=VALUE")
   ```

2. Add `cmd_cache(args)` handler:
   - Resolve project via `_find_project_or_exit(args)`.
   - Construct cache path: `project.dot_dir / "cache" / "results.db"`.
   - `clear`: open `StepResultCache`, call `clear(flow, step)`, print count.
   - `stats`: open cache (handle missing DB gracefully), call `stats()`, format as aligned columns.
   - `debug`: load flow via `load_workflow_yaml()`, find step, resolve inputs from `--var` flags via `parse_vars()`, compute cache key via `compute_cache_key()`, print key + canonical JSON.

3. Add `"cache": cmd_cache` to `handlers` dict at line 3484.

**Depends on**: Steps 3, 4.

### Step 13: Add validation warnings for cache on uncacheable steps (~15 min)

**File**: `src/stepwise/models.py`

1. In `WorkflowDefinition.validate()` (line 511), add after the exit rule checks (~line 595):
   ```python
   if step.cache is not None:
       if step.executor.type in ("human", "poll"):
           errors.append(
               f"Step '{name}': cache has no effect on {step.executor.type} steps"
           )
       if step.executor.type == "agent" and step.executor.config.get("emit_flow"):
           errors.append(
               f"Step '{name}': cache has no effect on agent steps with emit_flow"
           )
   ```
2. These are errors (not warnings) to catch misconfigurations early. `stepwise validate` surfaces them.

**Depends on**: Step 1.

### Step 14: Tests — cache store and key computation (~30 min)

**File**: `tests/test_cache.py` (new)

Tests for the `cache.py` module in isolation:

1. **`test_put_and_get`**: put an entry, get it back, verify envelope matches.
2. **`test_get_nonexistent_key`**: get returns None.
3. **`test_get_nonexistent_db`**: get with non-existent file path returns None (no error).
4. **`test_ttl_expiry`**: put with `ttl_seconds=1`, sleep 2s or manipulate `expires_at`, verify miss.
5. **`test_hit_count_incremented`**: get twice, verify `hit_count=2` in DB.
6. **`test_batch_get`**: put 5 entries, batch_get 3 of them, verify 3 returned.
7. **`test_batch_get_filters_expired`**: put 3 entries, expire 1, batch_get returns 2.
8. **`test_clear_all`**: put 5, clear(), verify 0 remain, verify returned count=5.
9. **`test_clear_by_flow`**: put entries for 2 flows, clear flow "a", verify flow "b" untouched.
10. **`test_clear_by_step`**: put entries for 2 steps, clear step "x", verify step "y" untouched.
11. **`test_stats`**: put 3 entries, verify total_entries=3, total_hits=0.
12. **`test_cache_key_deterministic`**: same inputs → same key across calls.
13. **`test_cache_key_differs_on_input_change`**: different inputs → different key.
14. **`test_cache_key_differs_on_config_change`**: different executor config → different key.
15. **`test_cache_key_strips_runtime_keys`**: config with `_registry` and `output_fields` → same key as without them.
16. **`test_cache_key_differs_on_version`**: different engine version → different key.
17. **`test_cache_key_differs_on_key_extra`**: `v1` vs `v2` → different key.

Use `:memory:` for in-memory SQLite or `tmp_path` fixture for file-based tests.

**Depends on**: Steps 3, 4.

### Step 15: Tests — engine cache integration (~45 min)

**File**: `tests/test_cache.py` (continued) or `tests/test_cache_engine.py`

Engine-level tests using `async_engine` fixture + `register_step_fn`:

1. **`test_cache_hit_skips_executor`**: Register callable with call counter. Run workflow twice with same inputs. Assert counter=1 on second run. Assert second run's `executor_state.from_cache == True`.
2. **`test_cache_miss_different_inputs`**: Run with `x=5`, then `x=10`. Counter=2 (both execute).
3. **`test_cache_miss_different_config`**: Two workflows with same step name but different executor config. Both execute.
4. **`test_cache_respects_ttl`**: Insert a cache entry with past `expires_at` directly into DB. Verify step re-executes.
5. **`test_default_ttl_script_vs_llm`**: Step with `executor: script` + `cache: true` → verify `expires_at` is ~1h from now. Step with `executor: mock_llm` → verify ~24h.
6. **`test_rerun_bypasses_cache`**: Pre-populate cache, set `rerun_steps` in metadata. Step executes despite cache hit.
7. **`test_rerun_writes_to_cache`**: After rerun, run again without `--rerun`. Cache hit (counter=2, not 3).
8. **`test_human_step_not_cached`**: Human step with `cache: true` → still suspends.
9. **`test_emit_flow_agent_not_cached`**: Agent with `emit_flow: true` + `cache: true` → still delegates.
10. **`test_cache_hit_evaluates_exit_rules`**: Step with exit rules + cache hit → exit rules fire correctly.
11. **`test_no_cache_when_no_project_dir`**: Engine without `project_dir` → `self.cache` is None → no cache behavior.

For the `async_engine` fixture: it needs a `project_dir` for cache to activate. Either modify the fixture or create a separate `cached_engine` fixture that provides a `tmp_path`-based project dir.

**Depends on**: Steps 6, 7, 8, 9.

### Step 16: Tests — YAML roundtrip and for-each batch cache (~30 min)

**File**: `tests/test_cache.py` (continued)

1. **`test_cache_config_yaml_true`**: Parse `cache: true` → `CacheConfig(enabled=True, ttl_seconds=None, key_extra=None)`.
2. **`test_cache_config_yaml_dict`**: Parse `cache: { ttl: "24h", key_extra: "v2" }` → correct fields.
3. **`test_cache_config_yaml_false`**: Parse `cache: false` → `None`.
4. **`test_cache_config_roundtrip`**: `StepDefinition` with cache → `to_dict()` → `from_dict()` → matches.
5. **`test_validate_cache_on_human_step`**: Workflow with human step + cache → validation error.
6. **`test_for_each_batch_partial_cache`**: 5-item for-each, pre-populate cache for items 0, 2, 4. Run flow. Assert 2 sub-jobs created (items 1, 3). Assert final results list has 5 entries in correct order.
7. **`test_for_each_all_cached`**: All items cached → no sub-jobs, immediate completion.
8. **`test_for_each_none_cached`**: No cache entries → all sub-jobs created (baseline behavior).

**Depends on**: Steps 2, 10, 11.

### Step 17: Update CLAUDE.md documentation (~15 min)

**File**: `CLAUDE.md`

1. Add `cache.py` to the "Key files" Engine section with description.
2. Add `cache` field docs to StepDefinition description.
3. Add cache CLI commands to the CLI mode table.
4. Add YAML cache syntax example to the "YAML workflow format" section.
5. Add `--rerun` flag to the run command documentation.

**Depends on**: All implementation steps complete.

## Testing Strategy

### Commands

```bash
# Unit tests — cache module
uv run pytest tests/test_cache.py -v -k "test_put_and_get or test_get_nonexistent"

# Engine integration tests
uv run pytest tests/test_cache.py -v -k "test_cache_hit_skips"

# YAML roundtrip tests
uv run pytest tests/test_cache.py -v -k "test_cache_config_yaml"

# For-each batch tests
uv run pytest tests/test_cache.py -v -k "test_for_each_batch"

# Full cache test suite
uv run pytest tests/test_cache.py -v

# Full regression (must pass — CLAUDE.md guardrail #6)
uv run pytest tests/

# Frontend unaffected
cd web && npm run test
```

### Manual integration verification

```bash
# Create a test flow with cache
cat > /tmp/cache-test.flow.yaml << 'EOF'
name: cache-test
steps:
  greet:
    run: echo '{"message": "hello"}'
    outputs: [message]
    cache: true
EOF

# First run — executor runs
stepwise run /tmp/cache-test.flow.yaml

# Second run — cache hit (should be near-instant)
stepwise run /tmp/cache-test.flow.yaml

# Inspect cache
stepwise cache stats

# Force re-execution
stepwise run /tmp/cache-test.flow.yaml --rerun greet

# Clear cache
stepwise cache clear --step greet
stepwise cache stats  # should show 0 entries
```

## Risks & Mitigations

### Risk 1: Cache DB contention under concurrent for-each sub-jobs
**Impact**: Multiple sub-jobs completing simultaneously try to write cache entries → SQLite lock contention.
**Mitigation**: WAL mode + `busy_timeout=5000` (same strategy as `store.py:43`). Cache writes wrapped in try/except — a failed write is a miss on next run, not a job failure. Sub-jobs share the same cache DB file but SQLite WAL handles concurrent readers well.
**Likelihood**: Low — sub-jobs complete on different threads, writes are fast (single INSERT).

### Risk 2: Stale cache entries for non-deterministic scripts
**Impact**: Script executors that read external state (APIs, files, timestamps) return stale cached results.
**Mitigation**: Default 1h TTL for scripts limits staleness. Users control via explicit `ttl` or `cache: false`. `--rerun <step>` provides immediate bypass. Cache key includes interpolated config (so different `$url` params → different cache entries).
**Likelihood**: Medium — scripts are the most variable executor type. Documentation should advise against caching side-effectful scripts.

### Risk 3: Cache key includes engine version — upgrades clear cache
**Impact**: Every `stepwise update` that bumps version invalidates all cache entries.
**Mitigation**: Intentional for safety — new engine version may change executor behavior or output format. Documented as expected. Patch-version bumps (0.6.0 → 0.6.1) also invalidate; this is conservative but safe.
**Likelihood**: Certain, by design.

### Risk 4: For-each batch merge complexity
**Impact**: Merging cached and live results in correct order requires careful index tracking. Bug → wrong order or missing items.
**Mitigation**: Index-based tracking (`sub_job_index_map` in executor_state). Thorough tests: partial cache, all cached, none cached, single item. The existing `_check_for_each_completion()` (engine.py:1401) already uses index-based `completed_results[i]` — the merge extends this pattern.
**Likelihood**: Low with thorough testing.

### Risk 5: `_prepare_step_run()` sentinel return changes method contract
**Impact**: Any code calling `_prepare_step_run()` expecting a non-None 4-tuple could break on cache hit.
**Mitigation**: Only two callers exist: `Engine._launch()` (line 253, legacy tick-based — not commonly used) and `AsyncEngine._launch()` (line 2399). Both are updated in Step 7. No other code calls `_prepare_step_run()`. Verified via grep.
**Likelihood**: Very low — contained change, both callers updated.
