# Implementation Plan: Per-Job ResourceManager Lifecycle

## Overview

The ACP process pool and the cloud-hypervisor VM pool both leak their contents forever. `ACPBackend.cleanup()` and `CloudHypervisorBackend.release_all()` exist and do the right thing, but **nothing in the engine ever calls them**. Every agent spawned for every job stays alive until the stepwise server dies — and when it does, agents spawned with `start_new_session=True` reparent to `systemd --user` and keep running indefinitely. This plan introduces a shared protocol and one caller site so that agents are released when the jobs that acquired them terminate, and the entire pool is drained on server shutdown.

## Root cause

**ACP pool.** `_cleanup_job_sessions` at `engine.py:2171` had the docstring *"Process cleanup is handled by ACPBackend's lifecycle manager"* — but its body only clears an in-memory dict. `ACPBackend.cleanup()` / `self.lifecycle.release_all()` were wired up (`acp_backend.py:913-917`) but had no caller in the live code paths.

**VM pool.** `CloudHypervisorBackend` has the same shape: `release_if_unused`/`release_all` defined, never invoked from production code. vmmd's out-of-process lifecycle partially insulates VMs (vmmd has signal handlers + `_shutdown_cleanup`), but VMs leak during the server's lifetime exactly as ACP processes do.

**Diagnostic (2026-04-21).** On a single workstation: 112 `claude-agent-acp`-rooted processes, 29.8 GB RSS, 30 distinct ACP sessions of which 28 were orphans (26 jobs completed, 2 failed). Oldest leaks were 1d+4h, spanning a prior server restart.

## Design

**Unify under one protocol.** Both the ACP pool and the VM pool already implement a matching API shape. Define `ResourceManager` in `stepwise.lifecycle` as the explicit contract:

```python
class ResourceManager(Protocol):
    def release_for_job(self, job_id: str) -> None: ...
    def release_all(self) -> None: ...
```

`ResourceLifecycleManager[ACPProcess]` and `ContainmentBackend` both conform. `ACPBackend` wraps its two constituents (lifecycle + containment) and presents as a single `ResourceManager`.

**Reference-counted sharing.** A resource is shared across jobs whose configs are `is_eq` — that's the existing reuse win. Add `job_refs: set[str]` per `ManagedResource`; `release_for_job(X)` discards X from every refs set and tears down only resources whose refs set has drained to empty. Two concurrent jobs reusing the same ACP process survive first release; process dies only when both have released.

**One caller, two hooks.** The engine iterates `registry.resource_managers`:
- In `_cleanup_job_sessions(job_id)` (runs on every job terminal transition — called from cancel, complete, fail, halt paths), call `release_for_job(job_id)`.
- In `AsyncEngine.shutdown()`, call `release_all()`.

**Thread-local job_id passthrough.** `ACPBackend.spawn()` sets `self._tls.current_job_id` before calling `lifecycle.acquire`, cleared in `finally`. `_spawn_process` (invoked as the factory when we spawn a fresh process) reads TLS and passes `job_id` to `containment.get_spawn_context`. On the reuse path, `spawn()` calls `containment.get_spawn_context(cached_config, job_id=…)` directly so the VM's ref set reflects every job actually using it (not just the first).

## Requirements

1. **R1: Reference-counted ACP pool.** `ResourceLifecycleManager.acquire(config, session_name=None, job_id=None)` adds `job_id` to `managed.job_refs` and sets `job_scoped=True`. `release_for_job(job_id)` discards the ref and tears down job-scoped resources whose refs have drained empty. Acquires without `job_id` (tests, library use) remain unscoped; `release_all()` owns those.

2. **R2: Reference-counted VM pool.** `CloudHypervisorBackend.get_spawn_context(config, job_id=None)` threads `job_id` into a `{config_key: set[job_id]}` map. `release_for_job(job_id)` destroys VMs whose refs drain empty. VMs booted without `job_id` are left for `release_all()`.

3. **R3: ResourceManager protocol.** Defined in `stepwise.lifecycle`. `ResourceLifecycleManager`, `ACPBackend`, `CloudHypervisorBackend`, and `NoContainmentBackend` all conform. `ACPBackend.release_for_job` and `.release_all` drive both the lifecycle manager and the containment backend.

4. **R4: ExecutorRegistry exposes managers.** `ExecutorRegistry.register_resource_manager(mgr)` + `.resource_managers` property (read-only view). `registry_factory.py` registers the `ACPBackend` at registry-creation time.

5. **R5: Engine drives release on terminal status.** `_cleanup_job_sessions(job_id, job)` iterates `self.registry.resource_managers` and calls `release_for_job(job_id)`, swallowing + logging per-manager exceptions.

6. **R6: Engine drives drain on shutdown.** `AsyncEngine.shutdown()` iterates the same list and calls `release_all()`. Runs in the FastAPI `lifespan` shutdown branch which already awaits `_engine.shutdown()`.

7. **R7: Tests.**
   - `test_lifecycle.py`: unit tests for `job_refs`, `job_scoped`, `release_for_job` (single job drains; shared-across-jobs survives first release; unscoped untouched; unknown job no-op; teardown exception does not corrupt pool).
   - `test_resource_manager_lifecycle.py`: integration tests proving the engine calls `release_for_job` on job completion and `release_all` on shutdown, and that failing managers do not break the engine.

## Orthogonality with H18

H18 (`reports/plan-stepwise-h18-server-restart-resilience-orphan-reattach-proble.md`) addresses *live-and-wanted* agent orphans — agents whose jobs are still active after a server restart. This plan addresses *completed-and-still-running* agents — the resource never gets released on normal job completion or graceful shutdown. Both bugs produce orphans; neither patch covers the other's case.

| agent state | job active | job terminal |
|---|---|---|
| agent alive | **H18**: reattach monitor | **This plan**: release_for_job |
| agent dead | **H18**: fail run as zombie | no-op |

When H18 lands, its reattach path should call `lifecycle.acquire(config, session_name, job_id)` so reattached agents participate in the same ref-counted cleanup on subsequent job completion. Without the `job_id` arg, a reattached agent would complete its job cleanly but then leak forever — replacing one bug with another.

## Out of scope (follow-ups)

- ~~**Startup orphan sweep.**~~ Shipped in 0.45.1. `process_lifecycle.reap_orphaned_agent_processes` scans `/proc` at server startup, intersects with `executor_state.pgid` of active jobs, SIGTERMs the rest. Runs after H18's `reattach_surviving_runs` so owned-set is accurate. Covers the "previous server SIGKILLed / upgraded" class.
- **`PR_SET_PDEATHSIG`.** Linux kernel primitive that sends SIGTERM to the child when its parent dies. Eliminates the "prior stepwise SIGKILLed → agents reparented to systemd" class. `preexec_fn` at `acp_backend.py:448`.
- **Idle-TTL reaper for the pool itself.** Generalize `reap_expired_processes` beyond `store.active_jobs()` so regressions in ref-counting are caught before they accumulate.
- **Observability.** `/api/v1/health` + `stepwise doctor` should surface per-manager pool sizes, oldest age, and orphan counts detected by the startup sweep.
- **Promote ACP supervision to a daemon** (acpx-daemon mirroring vmmd). Only worth building if server-SIGKILL resilience becomes a pain point the startup sweep can't cover. Protocol stays stable; only the backend swaps.

## Risk notes

- **Reuse-path VM registration.** If `ACPBackend.spawn()` re-registers the VM ref every time a job reuses an existing ACP process (via `_register_containment_ref`), the call is idempotent — `get_spawn_context` for an already-booted VM just adds `job_id` to the set and returns the same `VMSpawnContext`. No redundant VM boots.
- **Failed release_for_job doesn't poison the engine.** Engine catches and logs; the job still transitions correctly. Worst case is that one manager leaks while others drain cleanly.
- **Agent reuse across jobs still works.** Two jobs with matching `ResolvedAgentConfig` share one ACP process exactly as before. Ref-counting only changes *when* the process is torn down, not *whether* it's reused.
